"""API WebSocket du panneau et enregistrement du panneau latéral."""

import copy
from unittest.mock import AsyncMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

T = "de_dietrich_smart"


async def ws(client, msg_id, **msg):
    await client.send_json({"id": msg_id, **msg})
    return await client.receive_json()


async def test_ws_overview(hass, loaded, config_entry, hass_ws_client):
    client = await hass_ws_client(hass)
    res = await ws(client, 1, type=f"{T}/overview")
    assert res["success"]
    (e,) = res["result"]["entries"]
    assert e["entry_id"] == config_entry.entry_id and e["title"] == "EMC-S"
    z = e["state"]["appliances"][0]["zones"][0]
    assert z["setpoint"] == 21.0 and [a["name"] for a in z["activities"]][:2] == ["Nuit", "Maison"]
    assert e["undo"] is None


async def test_ws_set_week_validates(hass, loaded, config_entry, state, hass_ws_client, mock_client):
    s = copy.deepcopy(state)
    s.zone.week = {2: [{"day": 1, "time": "06:00", "activity": 2, "setpoint": None}]}
    loaded.coordinator.async_set_updated_data(s)
    client = await hass_ws_client(hass)
    seven = [{"day": 2, "time": f"0{h}:00", "activity": 1} for h in range(7)]
    res = await ws(client, 1, type=f"{T}/set_week", entry_id=config_entry.entry_id, program=2, switch_points=seven)
    assert not res["success"] and res["error"]["code"] == "command_failed"
    assert "6 créneaux au plus par jour" in res["error"]["message"]
    ok = [{"day": 1, "time": "06:30", "activity": 4}]
    res = await ws(client, 2, type=f"{T}/set_week", entry_id=config_entry.entry_id, program=2, switch_points=ok)
    assert res["success"] and res["result"]["undo_until"] is not None
    # Délai restant donné par le serveur : le panneau ne dépend pas de l'horloge du navigateur.
    assert 119 <= res["result"]["undo_remaining"] <= 120
    mock_client.zone_week.assert_awaited_once()


async def test_ws_write_requires_admin(hass, loaded, config_entry, hass_ws_client, hass_read_only_access_token):
    client = await hass_ws_client(hass, hass_read_only_access_token)
    res = await ws(client, 1, type=f"{T}/set_activity", entry_id=config_entry.entry_id, activity=1, setpoint=17)
    assert not res["success"] and res["error"]["code"] == "unauthorized"
    res = await ws(client, 2, type=f"{T}/overview")
    assert res["success"]


async def test_ws_undo_and_journal(hass, loaded, config_entry, hass_ws_client, mock_client):
    client = await hass_ws_client(hass)
    res = await ws(client, 1, type=f"{T}/set_activity", entry_id=config_entry.entry_id, activity=1, setpoint=17)
    cid = res["result"]["command_id"]
    overview = await ws(client, 2, type=f"{T}/overview")
    assert overview["result"]["entries"][0]["undo"]["command_id"] == cid
    assert 0 < overview["result"]["entries"][0]["undo"]["undo_remaining"] <= 120
    res = await ws(client, 3, type=f"{T}/undo", entry_id=config_entry.entry_id, command_id=cid)
    assert res["success"]
    mock_client.zone_activities.assert_awaited_with("z1", {1: 18.0})
    res = await ws(client, 4, type=f"{T}/journal", entry_id=config_entry.entry_id, limit=10)
    assert [c["action"] for c in res["result"]["commands"]] == ["annulation", "activite"]


async def test_ws_unknown_entry(hass, loaded, hass_ws_client):
    client = await hass_ws_client(hass)
    res = await ws(client, 1, type=f"{T}/journal", entry_id="nope")
    assert not res["success"] and res["error"]["code"] == "not_found"


async def test_panel_registered_once_for_two_entries(hass, config_entry, mock_client):
    # Même chaudière simulée pour les deux entrées : les doublons d'entités sont ignorés par HA.
    other = MockConfigEntry(domain=T, unique_id="autre", data=dict(config_entry.data))
    other.add_to_hass(hass)
    hass.config.components.add("frontend")
    with (
        patch("homeassistant.components.http.HomeAssistantHTTP.async_register_static_paths", AsyncMock()) as static,
        patch("homeassistant.components.panel_custom.async_register_panel", AsyncMock()) as register,
        patch("homeassistant.components.frontend.async_remove_panel") as remove,
    ):
        # Le chargement de l'intégration charge les deux entrées.
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        assert len(hass.config_entries.async_loaded_entries(T)) == 2
        assert register.await_count == 1
        assert register.await_args.kwargs["frontend_url_path"] == "de-dietrich-smart"
        static.assert_awaited_once()
        assert await hass.config_entries.async_unload(config_entry.entry_id)
        remove.assert_not_called()
        assert await hass.config_entries.async_unload(other.entry_id)
        remove.assert_called_once_with(hass, "de-dietrich-smart")


async def test_panel_reregistered_after_reload(hass, config_entry, mock_client):
    hass.config.components.add("frontend")
    with (
        patch("homeassistant.components.http.HomeAssistantHTTP.async_register_static_paths", AsyncMock()) as static,
        patch("homeassistant.components.panel_custom.async_register_panel", AsyncMock()) as register,
        patch("homeassistant.components.frontend.async_remove_panel"),
    ):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        assert await hass.config_entries.async_reload(config_entry.entry_id)
        await hass.async_block_till_done()
        assert register.await_count == 2 and static.await_count == 1


async def test_ws_curve(hass, loaded, config_entry, hass_ws_client):
    client = await hass_ws_client(hass)
    res = await ws(client, 1, type=f"{T}/curve", entry_id=config_entry.entry_id)
    assert res["success"]
    r = res["result"]
    assert r["curve"]["slope"] == 1.5 and r["curve"]["base"] == 15.0
    assert r["guard"] == {
        "active": True, "slope": [0.2, 1.0], "base": [15.0, 35.0], "max_floor_flow": 45.0, "base_temp": -15.0,
        "violation": r["guard"]["violation"],
    }  # fmt: skip
    assert "pente 1,5" in r["guard"]["violation"]
    # Point du moment : 6 °C dehors, 15 + 1,5 × 14 = 36 °C.
    assert r["now"] == {"outdoor": 6.0, "flow": 36.0}
    assert r["diagnosis"]["action"] == "switch_to_floor" and r["diagnosis"]["slope"] == 0.3


async def test_ws_set_heating_curve(hass, loaded, config_entry, hass_ws_client, mock_client):
    client = await hass_ws_client(hass)
    res = await ws(client, 1, type=f"{T}/set_heating_curve", entry_id=config_entry.entry_id, slope=1.5, base=22)
    assert not res["success"] and res["error"]["code"] == "command_failed"
    assert "courbe refusée pour le plancher" in res["error"]["message"]
    res = await ws(client, 2, type=f"{T}/set_heating_curve", entry_id=config_entry.entry_id, slope=0.3, base=33)
    assert res["success"] and 119 <= res["result"]["undo_remaining"] <= 120
    mock_client.zone_heating_curve.assert_awaited_once_with("z1", 0.3, 33.0)


async def test_ws_set_heating_curve_requires_admin(
    hass, loaded, config_entry, hass_ws_client, hass_read_only_access_token
):
    client = await hass_ws_client(hass, hass_read_only_access_token)
    res = await ws(client, 1, type=f"{T}/set_heating_curve", entry_id=config_entry.entry_id, slope=0.3, base=33)
    assert not res["success"] and res["error"]["code"] == "unauthorized"
    res = await ws(client, 2, type=f"{T}/curve", entry_id=config_entry.entry_id)
    assert res["success"]


async def test_ws_simulate_forecast(hass, config_entry, mock_client, hass_ws_client):
    from homeassistant.core import SupportsResponse

    calls = []

    async def forecasts(call):
        calls.append(call.data)
        fc = [{"datetime": f"2026-11-05T{9 + h:02d}:00:00+00:00", "temperature": 10.0 - h} for h in range(3)]
        return {"weather.maison": {"forecast": fc}}

    hass.services.async_register("weather", "get_forecasts", forecasts, supports_response=SupportsResponse.ONLY)
    hass.config_entries.async_update_entry(config_entry, options={"weather_entity": "weather.maison"})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    client = await hass_ws_client(hass)
    res = await ws(client, 1, type=f"{T}/simulate", entry_id=config_entry.entry_id, source="forecast")
    assert res["success"], res
    r = res["result"]
    assert calls[0]["type"] == "hourly" and r["error"] is None
    # Courbe actuelle 1,5 / 15 ; proposée : la bascule du diagnostic (0,3 / 33).
    assert r["rows"][0] == {"ts": "2026-11-05T09:00:00+00:00", "outdoor": 10.0, "current": 30.0, "proposed": 36.0}
    assert r["proposed"] == {"slope": 0.3, "base": 33.0} and r["max_floor_flow"] == 45.0
    assert len(r["days"]) == 1
    # Courbe proposée donnée par le panneau (réglage en cours).
    res = await ws(
        client, 2, type=f"{T}/simulate", entry_id=config_entry.entry_id, source="forecast", slope=0.5, base=22
    )
    assert res["result"]["rows"][0]["proposed"] == 27.0


async def test_ws_simulate_without_weather_entity(hass, loaded, config_entry, hass_ws_client):
    client = await hass_ws_client(hass)
    res = await ws(client, 1, type=f"{T}/simulate", entry_id=config_entry.entry_id, source="forecast")
    assert res["success"] and res["result"]["rows"] == []
    assert "entité météo" in res["result"]["error"]


async def test_ws_simulate_history_without_recorder(hass, loaded, config_entry, hass_ws_client):
    client = await hass_ws_client(hass)
    res = await ws(client, 1, type=f"{T}/simulate", entry_id=config_entry.entry_id, source="history")
    assert res["success"] and res["result"]["rows"] == []
    assert "enregistreur" in res["result"]["error"]
