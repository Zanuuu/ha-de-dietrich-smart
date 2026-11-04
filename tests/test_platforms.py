"""Capteurs, capteurs binaires, réglages (number, select, switch, button)."""

import copy
from datetime import timedelta

import pytest
from homeassistant.const import ATTR_ENTITY_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import entity_registry as er

from .conftest import push


def st(hass, entity_id):
    s = hass.states.get(entity_id)
    assert s is not None, entity_id
    return s


async def test_sensors(hass, loaded):
    assert st(hass, "sensor.salon_room_temperature").state == "19.4"
    out = st(hass, "sensor.maison_exemple_outdoor_temperature")
    assert out.state == "6.0" and out.attributes["source"] == "météo"
    assert st(hass, "sensor.maison_exemple_water_pressure").state == "1.6"
    assert st(hass, "sensor.maison_exemple_activity").state == "heating"
    assert st(hass, "sensor.maison_exemple_error_status").state == "Running"
    assert st(hass, "sensor.maison_exemple_fault_code").state == "unknown"
    assert st(hass, "sensor.salon_override_end").state == "2026-11-02T21:30:00+00:00"


async def test_room_temp_unavailable_when_cloud_sends_null(hass, loaded, state):
    lost = copy.deepcopy(state)
    lost.zone.room_temp = None
    await push(hass, loaded, lost)
    assert st(hass, "sensor.salon_room_temperature").state == STATE_UNAVAILABLE


async def test_fault_code_described(hass, loaded, state):
    f = copy.deepcopy(state)
    f.appliance.in_error, f.appliance.active_errors = True, [{"code": "H02.31", "text": None, "raw": {}}]
    await push(hass, loaded, f)
    s = st(hass, "sensor.maison_exemple_fault_code")
    assert s.state == "H02.31" and s.attributes["label"] == "Pression faible : remplissage automatique nécessaire"
    assert st(hass, "binary_sensor.maison_exemple_problem").state == STATE_ON


@pytest.mark.parametrize(
    ("pressure", "ok", "expected"), [(0.9, True, STATE_ON), (1.0, True, STATE_OFF), (1.0, False, STATE_ON)]
)
async def test_low_pressure_threshold(hass, loaded, state, pressure, ok, expected):
    s = copy.deepcopy(state)
    s.appliance.pressure, s.appliance.pressure_ok = pressure, ok
    await push(hass, loaded, s)
    assert st(hass, "binary_sensor.maison_exemple_low_pressure").state == expected


async def test_binary_sensors(hass, loaded):
    assert st(hass, "binary_sensor.maison_exemple_online").state == STATE_ON
    assert st(hass, "binary_sensor.salon_heating").state == STATE_ON
    assert st(hass, "binary_sensor.eau_chaude_heating").state == STATE_OFF
    assert st(hass, "binary_sensor.maison_exemple_holiday").state == STATE_OFF


async def test_heating_ratio(hass, loaded, state, freezer):
    assert st(hass, "sensor.salon_heating_ratio").state == "100"
    idle = copy.deepcopy(state)
    idle.zone.demand = "idle"
    freezer.tick(timedelta(minutes=30))
    await push(hass, loaded, idle)
    assert st(hass, "sensor.salon_heating_ratio").state == "50"
    freezer.tick(timedelta(minutes=61))
    await push(hass, loaded, idle)
    assert st(hass, "sensor.salon_heating_ratio").state == "0"


async def test_curve_numbers_write(hass, config_entry, mock_client):
    # Radiateurs : bornes du cloud, sans garde-fou du plancher.
    hass.config_entries.async_update_entry(config_entry, options={"emitter": "radiators"})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    slope = st(hass, "number.salon_heating_curve_slope")
    assert slope.state == "1.5" and slope.attributes["min"] == 0.0 and slope.attributes["max"] == 4.0
    assert slope.attributes["step"] == 0.1
    await hass.services.async_call(
        "number", "set_value", {ATTR_ENTITY_ID: slope.entity_id, "value": 0.8}, blocking=True
    )
    mock_client.zone_heating_curve.assert_awaited_once_with("z1", 0.8, 15.0)
    await hass.services.async_call(
        "number", "set_value", {ATTR_ENTITY_ID: "number.salon_heating_curve_base", "value": 22}, blocking=True
    )
    mock_client.zone_heating_curve.assert_awaited_with("z1", 1.5, 22.0)


async def test_curve_numbers_floor_bounds(hass, loaded, mock_client):
    # Plancher direct : bornes resserrées à celles du plancher, dans la limite de celles du cloud.
    slope = st(hass, "number.salon_heating_curve_slope")
    assert slope.attributes["min"] == 0.2 and slope.attributes["max"] == 1.0
    base = st(hass, "number.salon_heating_curve_base")
    assert base.attributes["min"] == 15.0 and base.attributes["max"] == 35.0
    with pytest.raises(ServiceValidationError, match="courbe refusée pour le plancher"):
        await hass.services.async_call(
            "number", "set_value", {ATTR_ENTITY_ID: base.entity_id, "value": 22}, blocking=True
        )
    mock_client.zone_heating_curve.assert_not_awaited()


async def test_activity_numbers(hass, loaded, mock_client):
    s = st(hass, "number.salon_activity_nuit_setpoint")
    assert s.state == "18.0"
    await hass.services.async_call("number", "set_value", {ATTR_ENTITY_ID: s.entity_id, "value": 17.5}, blocking=True)
    mock_client.zone_activities.assert_awaited_once_with("z1", {1: 17.5})


async def test_program_select(hass, loaded, mock_client):
    s = st(hass, "select.salon_program")
    assert s.state == "2" and s.attributes["options"] == ["1", "2", "3"]
    await hass.services.async_call(
        "select", "select_option", {ATTR_ENTITY_ID: s.entity_id, "option": "3"}, blocking=True
    )
    mock_client.zone_program.assert_awaited_once_with("z1", 3)


async def test_fireplace_switch_and_boost(hass, loaded, mock_client):
    await hass.services.async_call("switch", "turn_on", {ATTR_ENTITY_ID: "switch.salon_fireplace"}, blocking=True)
    mock_client.zone_fireplace.assert_awaited_once_with("z1", True)
    await hass.services.async_call("button", "press", {ATTR_ENTITY_ID: "button.eau_chaude_boost"}, blocking=True)
    mock_client.dhw_boost.assert_awaited_once_with("h1")


async def test_fireplace_absent_when_incapable(hass, config_entry, mock_client, state):
    state.zone.fireplace_capable = False
    state.dhw.boost_capable = False
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("switch.salon_fireplace") is None
    assert hass.states.get("button.eau_chaude_boost") is None


async def test_secondary_entities_disabled(hass, loaded):
    reg = er.async_get(hass)
    for eid in ("sensor.salon_heating_curve_flow", "binary_sensor.salon_preheat", "number.eau_chaude_reduced_setpoint"):
        ent = reg.async_get(eid)
        assert ent is not None and ent.disabled_by is er.RegistryEntryDisabler.INTEGRATION, eid


async def test_curve_and_activities_added_when_read_later(hass, config_entry, mock_client, state):
    """Lectures complémentaires absentes au démarrage : les réglages apparaissent à la relève qui les apporte."""
    late = copy.deepcopy(state)
    state.zone.heating_curve = None
    state.zone.activities = []
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get("number.salon_heating_curve_slope") is None
    assert hass.states.get("number.salon_activity_nuit_setpoint") is None
    await push(hass, config_entry.runtime_data, late)
    assert hass.states.get("number.salon_heating_curve_slope") is not None
    assert hass.states.get("number.salon_activity_nuit_setpoint") is not None
    # Une relève de plus n'ajoute rien en double.
    count = len(hass.states.async_entity_ids("number"))
    await push(hass, config_entry.runtime_data, copy.deepcopy(late))
    assert len(hass.states.async_entity_ids("number")) == count
