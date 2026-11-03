"""Entité climat de la zone de chauffage."""

import copy

import pytest
from homeassistant.components.climate import (
    ATTR_HVAC_ACTION,
    ATTR_PRESET_MODE,
    HVACAction,
    HVACMode,
)
from homeassistant.components.climate import (
    DOMAIN as CLIMATE,
)
from homeassistant.const import ATTR_ENTITY_ID, ATTR_TEMPERATURE
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from custom_components.de_dietrich_smart.bdr.models import SourceError

from .conftest import push

ENTITY = "climate.salon"


async def test_state_from_cloud(hass, loaded):
    s = hass.states.get(ENTITY)
    assert s.state == HVACMode.HEAT
    assert s.attributes["current_temperature"] == 19.4
    assert s.attributes[ATTR_TEMPERATURE] == 21.0
    assert s.attributes[ATTR_HVAC_ACTION] == HVACAction.HEATING
    assert s.attributes[ATTR_PRESET_MODE] == "schedule"
    assert s.attributes["min_temp"] == 5.0 and s.attributes["max_temp"] == 30.0
    assert s.attributes["target_temp_step"] == 0.5
    assert s.attributes["override_end"] is not None and s.attributes["program"] == 2
    ent = er.async_get(hass).async_get(ENTITY)
    assert ent.unique_id == "z1_climate"
    dev = dr.async_get(hass).async_get(ent.device_id)
    assert dev.name == "Salon" and dev.via_device_id is not None


async def test_set_temperature_uses_default_duration(hass, loaded, mock_client, config_entry):
    await hass.services.async_call(
        CLIMATE, "set_temperature", {ATTR_ENTITY_ID: ENTITY, ATTR_TEMPERATURE: 19.5}, blocking=True
    )
    mock_client.zone_override.assert_awaited_once_with("z1", 19.5)
    hass.config_entries.async_update_entry(
        config_entry, options={**config_entry.options, "default_duration": "permanent"}
    )
    await hass.async_block_till_done()
    await hass.services.async_call(
        CLIMATE, "set_temperature", {ATTR_ENTITY_ID: ENTITY, ATTR_TEMPERATURE: 20}, blocking=True
    )
    mock_client.zone_manual.assert_awaited_with("z1", 20.0)


async def test_preset_antifrost(hass, loaded, mock_client):
    await hass.services.async_call(
        CLIMATE, "set_preset_mode", {ATTR_ENTITY_ID: ENTITY, ATTR_PRESET_MODE: "antifrost"}, blocking=True
    )
    mock_client.zone_antifrost.assert_awaited_once_with("z1")


async def test_preset_fireplace_and_back(hass, loaded, mock_client, state):
    await hass.services.async_call(
        CLIMATE, "set_preset_mode", {ATTR_ENTITY_ID: ENTITY, ATTR_PRESET_MODE: "fireplace"}, blocking=True
    )
    mock_client.zone_fireplace.assert_awaited_once_with("z1", True)
    fire = copy.deepcopy(state)
    fire.zone.fireplace = True
    await push(hass, loaded, fire)
    assert hass.states.get(ENTITY).attributes[ATTR_PRESET_MODE] == "fireplace"
    await hass.services.async_call(
        CLIMATE, "set_preset_mode", {ATTR_ENTITY_ID: ENTITY, ATTR_PRESET_MODE: "schedule"}, blocking=True
    )
    mock_client.zone_fireplace.assert_awaited_with("z1", False)


async def test_hvac_off_is_antifrost(hass, loaded, mock_client, state):
    await hass.services.async_call(
        CLIMATE, "set_hvac_mode", {ATTR_ENTITY_ID: ENTITY, "hvac_mode": HVACMode.OFF}, blocking=True
    )
    mock_client.zone_antifrost.assert_awaited_once_with("z1")
    frost = copy.deepcopy(state)
    frost.zone.mode = "antifrost"
    await push(hass, loaded, frost)
    assert hass.states.get(ENTITY).state == HVACMode.OFF
    await hass.services.async_call(
        CLIMATE, "set_hvac_mode", {ATTR_ENTITY_ID: ENTITY, "hvac_mode": HVACMode.HEAT}, blocking=True
    )
    mock_client.zone_schedule.assert_awaited_once_with("z1", 2)


async def test_unavailable_when_room_temp_none(hass, loaded, state):
    lost = copy.deepcopy(state)
    lost.zone.room_temp = None
    await push(hass, loaded, lost)
    s = hass.states.get(ENTITY)
    assert s.state == HVACMode.HEAT and s.attributes["current_temperature"] is None
    assert s.attributes[ATTR_TEMPERATURE] == 21.0


async def test_command_error_is_translated(hass, loaded, mock_client):
    mock_client.zone_override.side_effect = SourceError("HTTP 500")
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(
            CLIMATE, "set_temperature", {ATTR_ENTITY_ID: ENTITY, ATTR_TEMPERATURE: 20}, blocking=True
        )
    assert err.value.translation_key == "command_failed"
    assert "HTTP 500" in err.value.translation_placeholders["message"]
