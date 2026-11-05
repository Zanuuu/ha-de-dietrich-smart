"""Entité eau chaude."""

import copy

from homeassistant.components.water_heater import DOMAIN as WATER_HEATER
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE

from .conftest import push

ENTITY = "water_heater.eau_chaude"


async def test_state(hass, loaded):
    s = hass.states.get(ENTITY)
    assert s.state == "schedule"
    assert s.attributes["operation_list"] == ["schedule", "comfort", "eco"]
    assert s.attributes[ATTR_TEMPERATURE] == 55.0
    assert s.attributes["current_temperature"] == 49.5
    assert s.attributes["min_temp"] == 40.0 and s.attributes["max_temp"] == 65.0


async def test_instant_boiler_has_no_water_temperature(hass, loaded, state):
    b = copy.deepcopy(state)
    b.dhw.temp = None
    b.dhw.target = 45.0
    await push(hass, loaded, b)
    s = hass.states.get(ENTITY)
    assert s.attributes["current_temperature"] is None
    assert s.attributes[ATTR_TEMPERATURE] == 55.0 and s.attributes["active_setpoint"] == 45.0


async def test_dhw_operation_eco(hass, loaded, mock_client):
    await hass.services.async_call(
        WATER_HEATER, "set_operation_mode", {ATTR_ENTITY_ID: ENTITY, "operation_mode": "eco"}, blocking=True
    )
    mock_client.dhw_mode.assert_awaited_once_with("h1", "eco")


async def test_set_comfort_setpoint(hass, loaded, mock_client):
    await hass.services.async_call(
        WATER_HEATER, "set_temperature", {ATTR_ENTITY_ID: ENTITY, ATTR_TEMPERATURE: 52}, blocking=True
    )
    mock_client.dhw_comfort_setpoint.assert_awaited_once_with("h1", 52.0)


async def test_boost_reads_as_schedule(hass, loaded, state):
    b = copy.deepcopy(state)
    b.dhw.mode = "boost"
    await push(hass, loaded, b)
    assert hass.states.get(ENTITY).state == "schedule"
