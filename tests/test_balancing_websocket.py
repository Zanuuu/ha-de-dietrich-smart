"""Nourrice : WebSocket, droits, persistance, diagnostics et suppression du fichier."""

import json

import pytest
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import entity_registry as er

from custom_components.de_dietrich_smart.balancing.store import Balancing
from custom_components.de_dietrich_smart.diagnostics import async_get_config_entry_diagnostics

T = "de_dietrich_smart/balancing"
ROOMS = [
    {
        "id": "a",
        "name": "Pièce A",
        "target": 19,
        "climate": None,
        "zones": [{"name": "Z", "sensors": [{"entity_id": "sensor.a", "correction": 0.5}]}],
        "loops": [{"label": "A1", "length": 100}, {"label": "A2", "length": 50}],
    },
    {
        "id": "b",
        "name": "Pièce B",
        "target": 18,
        "zones": [{"name": "Z", "sensors": [{"entity_id": "sensor.b"}]}],
        "loops": [{"label": "B1", "length": 50}],
    },
]


@pytest.fixture
async def client(hass, loaded, hass_ws_client):
    return await hass_ws_client(hass)


async def call(client, msg_id, config_entry, action, **msg):
    await client.send_json({"id": msg_id, "type": f"{T}/{action}", "entry_id": config_entry.entry_id, **msg})
    return await client.receive_json()


async def test_whole_flow(hass, client, config_entry, loaded):
    res = await call(client, 1, config_entry, "status")
    assert res["success"] and res["result"]["step"] == "setup" and res["result"]["activity"] == "heating"
    res = await call(client, 2, config_entry, "save_setup", rooms=ROOMS, graduation=0.25)
    assert res["result"]["step"] == "thermostat"
    assert res["result"]["rooms"][1]["climate"] is None
    assert res["result"]["rooms"][1]["zones"][0]["sensors"][0]["correction"] == 0.0
    await call(client, 3, config_entry, "set_thermostat_room", room="a")
    res = await call(client, 4, config_entry, "set_objective", objective="lower_others")
    assert res["result"]["step"] == "start_point" and res["result"]["start_point"] is None
    res = await call(client, 5, config_entry, "save_readings", readings={"A1": 2, "A2": 1, "B1": 1})
    assert {p["label"]: p["fixed"] for p in res["result"]["start_point"]} == {"A1": True, "A2": True, "B1": False}
    res = await call(client, 6, config_entry, "validate_start_point")
    assert res["result"]["step"] == "measuring" and res["result"]["validated_at"]
    assert [e["message"] for e in loaded.journal.events(kind="nourrice")] == [
        "Nourrice : point de départ validé",
        "Nourrice : objectif baisser les autres pièces",
    ]
    # Changer d'objectif annule la validation : le point de départ n'est plus le même.
    res = await call(client, 7, config_entry, "set_objective", objective="raise_others")
    assert res["result"]["step"] == "start_point"
    await call(client, 8, config_entry, "validate_start_point")
    # Corriger une sonde ne change pas le point de départ ; changer une boucle, si.
    other = json.loads(json.dumps(ROOMS))
    other[0]["zones"][0]["sensors"][0]["correction"] = 1.0
    res = await call(client, 9, config_entry, "save_setup", rooms=other, graduation=0.25)
    assert res["result"]["step"] == "measuring"
    other[0]["loops"].pop()
    res = await call(client, 10, config_entry, "save_setup", rooms=other, graduation=0.25)
    assert res["result"]["step"] == "start_point" and "A2" not in res["result"]["readings"]


async def test_removing_thermostat_room_goes_back(hass, client, config_entry):
    await call(client, 1, config_entry, "save_setup", rooms=ROOMS, graduation=0.25)
    await call(client, 2, config_entry, "set_thermostat_room", room="a")
    res = await call(client, 3, config_entry, "save_setup", rooms=ROOMS[1:], graduation=0.25)
    assert res["result"]["thermostat_room"] is None and res["result"]["step"] == "thermostat"


@pytest.mark.parametrize(
    ("action", "payload", "message"),
    [
        ("save_setup", {"rooms": [], "graduation": 0.25}, "au moins une pièce"),
        ("save_setup", {"rooms": ROOMS, "graduation": 0}, "graduation"),
        ("save_readings", {"readings": {"X": 1}}, "Boucle inconnue"),
        ("set_thermostat_room", {"room": "x"}, "Pièce inconnue"),
        ("validate_start_point", {}, "thermostat"),
    ],
)
async def test_refusals_are_readable(hass, client, config_entry, action, payload, message):
    res = await call(client, 1, config_entry, action, **payload)
    assert not res["success"] and res["error"]["code"] == "invalid_input" and message in res["error"]["message"]


async def test_negative_reading_refused(hass, client, config_entry):
    await call(client, 1, config_entry, "save_setup", rooms=ROOMS, graduation=0.25)
    res = await call(client, 2, config_entry, "save_readings", readings={"A1": -1})
    assert not res["success"] and "négatif" in res["error"]["message"]


async def test_writes_require_admin(hass, loaded, config_entry, hass_ws_client, hass_read_only_access_token):
    client = await hass_ws_client(hass, hass_read_only_access_token)
    res = await call(client, 1, config_entry, "save_setup", rooms=ROOMS, graduation=0.25)
    assert not res["success"] and res["error"]["code"] == "unauthorized"
    res = await call(client, 2, config_entry, "status")
    assert res["success"]


async def test_proposal_uses_areas_and_skips_own_entities(hass, client, config_entry):
    area = ar.async_get(hass).async_create("Pièce A")
    registry = er.async_get(hass)
    measured = {"device_class": "temperature", "state_class": "measurement", "friendly_name": "Sonde"}
    entry = registry.async_get_or_create("sensor", "test", "sonde", suggested_object_id="sonde")
    registry.async_update_entity(entry.entity_id, area_id=area.id)
    hass.states.async_set(entry.entity_id, "19.5", measured)
    hass.states.async_set("sensor.sans_statistique", "19", {"device_class": "temperature"})
    hass.states.async_set("sensor.hors_ligne", "unavailable", measured)
    hass.states.async_set("sensor.libre", "12", measured)
    res = await call(client, 1, config_entry, "propose")
    assert [r["name"] for r in res["result"]["rooms"]] == ["Pièce A"]
    assert res["result"]["unclassified"] == ["sensor.libre"]
    # Une proposition n'enregistre rien.
    status = await call(client, 2, config_entry, "status")
    assert status["result"]["rooms"] == [] and status["result"]["step"] == "setup"
    assert {s["entity_id"] for s in status["result"]["sensors"]} == {entry.entity_id, "sensor.libre"}


async def test_state_survives_reload(hass, client, config_entry):
    await call(client, 1, config_entry, "save_setup", rooms=ROOMS, graduation=0.25)
    await call(client, 2, config_entry, "set_thermostat_room", room="a")
    assert await hass.config_entries.async_reload(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.runtime_data.balancing.status()["thermostat_room"] == "a"


async def test_load_keeps_defaults_for_older_files(hass, hass_storage):
    hass_storage["de_dietrich_smart.x.balancing"] = {"version": 1, "key": "k", "data": {"rooms": []}}
    b = Balancing(hass, "x")
    await b.async_load()
    assert b.status()["graduation"] == 0.25 and b.step == "setup"


async def test_diagnostics_hide_names(hass, client, config_entry, mock_client):
    mock_client.raw.return_value = {}
    await call(client, 1, config_entry, "save_setup", rooms=ROOMS, graduation=0.25)
    diag = await async_get_config_entry_diagnostics(hass, config_entry)
    text = json.dumps(diag["balancing"])
    assert "Pièce A" not in text and "A1" not in text and "sensor.a" not in text
    assert diag["balancing"]["rooms"][0] == {"target": 19.0, "has_climate": False, "loops": 2, "zones": [1]}


async def test_remove_entry_deletes_file(hass, client, config_entry, hass_storage):
    await call(client, 1, config_entry, "save_setup", rooms=ROOMS, graduation=0.25)
    await config_entry.runtime_data.balancing.async_save()
    key = f"de_dietrich_smart.{config_entry.entry_id}.balancing"
    assert key in hass_storage
    assert await hass.config_entries.async_remove(config_entry.entry_id)
    assert key not in hass_storage
