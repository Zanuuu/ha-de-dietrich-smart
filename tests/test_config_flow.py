"""Configuration : ajout, erreurs, réauthentification, reconfiguration, options."""

import copy
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType

from custom_components.de_dietrich_smart.bdr.cloud import BdrClient
from custom_components.de_dietrich_smart.bdr.models import AuthError, SourceError
from custom_components.de_dietrich_smart.const import DOMAIN

CREDS = {"username": "test@example.org", "password": "mot-de-passe"}


@pytest.fixture
def flow_client(state):
    client = AsyncMock(spec=BdrClient)
    client.fetch_state.return_value = state
    with patch("custom_components.de_dietrich_smart.config_flow.BdrClient", return_value=client):
        yield client


@pytest.fixture(autouse=True)
def no_setup():
    with patch("custom_components.de_dietrich_smart.async_setup_entry", return_value=True):
        yield


async def test_user_flow_ok(hass, flow_client, state):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM and result["errors"] == {}
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == state.appliance.name
    assert result["data"] == CREDS
    assert result["result"].unique_id == "a1b2c3"
    flow_client.close.assert_awaited()


@pytest.mark.parametrize(
    ("exc", "error"),
    [(AuthError("refusé"), "invalid_auth"), (SourceError("injoignable"), "cannot_connect"), (RuntimeError, "unknown")],
)
async def test_user_flow_errors(hass, flow_client, exc, error):
    flow_client.fetch_state.side_effect = exc
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.FORM and result["errors"] == {"base": error}
    flow_client.close.assert_awaited()
    # Une fois corrigé, l'ajout aboutit.
    flow_client.fetch_state.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_duplicate_aborts(hass, flow_client, config_entry):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], CREDS)
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "already_configured"


async def test_reauth(hass, flow_client, config_entry):
    result = await config_entry.start_reauth_flow(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reauth_confirm"
    flow_client.fetch_state.side_effect = AuthError("refusé")
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"password": "faux"})
    assert result["errors"] == {"base": "invalid_auth"}
    flow_client.fetch_state.side_effect = None
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"password": "nouveau"})
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reauth_successful"
    assert config_entry.data == {"username": "test@example.org", "password": "nouveau"}


async def test_reconfigure_other_device_aborts(hass, flow_client, config_entry, state):
    other = copy.deepcopy(state)
    other.appliance.id = "autre"
    flow_client.fetch_state.return_value = other
    result = await config_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM and result["step_id"] == "reconfigure"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "autre@example.org", "password": "x"}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "unique_id_mismatch"


async def test_reconfigure_same_device(hass, flow_client, config_entry):
    result = await config_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"username": "test@example.org", "password": "neuf"}
    )
    assert result["type"] is FlowResultType.ABORT and result["reason"] == "reconfigure_successful"
    assert config_entry.data["password"] == "neuf"


async def test_options_flow(hass, config_entry):
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "default_duration": "2h",
            "emitter": "floor",
            "connection": "direct",
            "max_floor_flow": 45,
            "base_temp": -15,
            "gas_price": 0.15,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert config_entry.options["gas_price"] == 0.15
    assert config_entry.options["default_duration"] == "2h"
    assert config_entry.options["max_floor_flow"] == 45
