"""Reconfiguration et réauthentification d'une entrée chargée : un seul client à la fois sur le cloud."""

import copy
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.data_entry_flow import FlowResultType

from custom_components.de_dietrich_smart.bdr.cloud import BdrClient
from custom_components.de_dietrich_smart.bdr.models import SourceError


@pytest.fixture
def seen_states(state, config_entry):
    """Client de validation : note l'état de l'entrée au moment où il se connecte."""
    seen: list[ConfigEntryState] = []
    client = AsyncMock(spec=BdrClient)

    async def fetch_state():
        seen.append(config_entry.state)
        return state

    client.fetch_state.side_effect = fetch_state
    with patch("custom_components.de_dietrich_smart.config_flow.BdrClient", return_value=client):
        yield seen, client


async def test_reconfigure_unloads_during_validation(hass, config_entry, mock_client, seen_states):
    seen, _ = seen_states
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "test@example.org", "password": "neuf"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    assert seen == [ConfigEntryState.NOT_LOADED]
    assert config_entry.state is ConfigEntryState.LOADED


async def test_failed_validation_reloads_entry(hass, config_entry, mock_client, seen_states):
    seen, client = seen_states

    async def refused():
        seen.append(config_entry.state)
        raise SourceError("injoignable")

    client.fetch_state.side_effect = refused
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "test@example.org", "password": "neuf"}
    )
    await hass.async_block_till_done()
    assert result["errors"] == {"base": "cannot_connect"}
    assert seen == [ConfigEntryState.NOT_LOADED]
    assert config_entry.state is ConfigEntryState.LOADED
    assert config_entry.data["password"] == "mot-de-passe"


async def test_other_device_reloads_entry(hass, config_entry, mock_client, seen_states, state):
    other = copy.deepcopy(state)
    other.appliance.id = "autre"
    _, client = seen_states
    client.fetch_state.side_effect = None
    client.fetch_state.return_value = other
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "autre@example.org", "password": "x"}
    )
    await hass.async_block_till_done()
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "unique_id_mismatch"
    assert config_entry.state is ConfigEntryState.LOADED
