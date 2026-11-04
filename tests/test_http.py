"""Clients HTTP du client BDR : détenus et fermés par lui, jamais ceux gérés par Home Assistant."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from custom_components.de_dietrich_smart.http import client_factory


async def test_clients_really_close_without_warning(hass, caplog: pytest.LogCaptureFixture) -> None:
    make = client_factory(hass)
    client = make(timeout=httpx.Timeout(5.0))
    await client.aclose()
    assert client.is_closed
    async with make(follow_redirects=True) as login:
        pass
    assert login.is_closed
    assert "closes the Home Assistant httpx client" not in caplog.text


async def test_setup_uses_owned_clients(hass, config_entry, state) -> None:
    client = AsyncMock()
    client.fetch_state.return_value = state
    client.fetch_consumption_days.return_value = []
    client.fetch_consumption_hours.return_value = []
    with patch("custom_components.de_dietrich_smart.BdrClient", return_value=client) as cls:
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
    manifest = json.loads((Path(__file__).parents[1] / "custom_components/de_dietrich_smart/manifest.json").read_text())
    assert cls.call_args.kwargs["user_agent"] == f"ha-de-dietrich-smart/{manifest['version']}"
    made = cls.call_args.kwargs["client_factory"]()
    assert type(made) is httpx.AsyncClient
    await made.aclose()
