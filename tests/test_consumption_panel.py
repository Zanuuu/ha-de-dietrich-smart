"""Consommation dans le panneau et kWh/DJU : résumé, mois, CSV, capteur."""

from datetime import date, timedelta

import pytest

from custom_components.de_dietrich_smart.bdr.models import DayConsumption

T = "de_dietrich_smart"
TODAY = date(2026, 11, 4)


@pytest.fixture
async def with_history(hass, config_entry, mock_client, freezer, monkeypatch):
    """30 jours de consommation et de températures (sans appel à Open-Meteo)."""
    await hass.config.async_set_time_zone("UTC")
    freezer.move_to("2026-11-04 10:00:00+00:00")
    days = [DayConsumption(TODAY - timedelta(days=n), 30.0, 2.0) for n in range(1, 31)]
    mock_client.fetch_consumption_days.return_value = days
    hass.config_entries.async_update_entry(config_entry, options={"gas_price": 0.1})
    from custom_components.de_dietrich_smart.curve import outdoor

    async def no_fetch(self):
        self.days = {d.day: (2.0, 10.0, "open-meteo") for d in days}
        return dict(self.days)

    monkeypatch.setattr(outdoor.OutdoorCoordinator, "_async_update_data", no_fetch)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry


async def test_kwh_per_dju_sensor(hass, with_history):
    s = hass.states.get("sensor.maison_exemple_heating_per_degree_day")
    assert s is not None
    # 30 × 30 kWh pour 30 × 12 DJU.
    assert s.state == "2.5" and s.attributes["heating_kwh_30d"] == 900.0 and s.attributes["dju_30d"] == 360.0
    assert s.attributes["unit_of_measurement"] == "kWh/DJU"


async def test_ws_consumption(hass, with_history, hass_ws_client):
    client = await hass_ws_client(hass)
    await client.send_json({"id": 1, "type": f"{T}/consumption", "entry_id": with_history.entry_id})
    res = await client.receive_json()
    assert res["success"], res
    r = res["result"]
    assert r["summary"]["yesterday"]["total"] == 32.0 and r["summary"]["yesterday"]["dju"] == 12.0
    assert r["summary"]["kwh_per_dju_30d"] == 2.5 and r["summary"]["month"]["days"] == 3
    assert [m["month"] for m in r["months"]] == ["2026-10", "2026-11"]
    assert r["sources"] == {"open-meteo": 30} and r["price"] == 0.1


async def test_ws_consumption_csv(hass, with_history, hass_ws_client):
    client = await hass_ws_client(hass)
    await client.send_json(
        {"id": 1, "type": f"{T}/consumption_csv", "entry_id": with_history.entry_id, "period": "days"}
    )
    res = await client.receive_json()
    assert res["success"] and res["result"]["filename"] == "de-dietrich-conso-jours-2026-11-04.csv"
    lines = res["result"]["csv"].splitlines()
    assert lines[0].startswith("Jour;Chauffage (kWh)") and lines[1] == "2026-10-05;30;2;32;3,2;2;10;12"
    assert len(lines) == 1 + 31  # du premier jour connu à aujourd'hui
    await client.send_json(
        {"id": 2, "type": f"{T}/consumption_csv", "entry_id": with_history.entry_id, "period": "hours"}
    )
    res = await client.receive_json()
    assert res["result"]["filename"] == "de-dietrich-conso-heures-2026-11-04.csv"
    assert res["result"]["csv"].splitlines() == ["Heure (locale);Chauffage (kWh);Eau chaude (kWh);Total (kWh);Coût (€)"]
