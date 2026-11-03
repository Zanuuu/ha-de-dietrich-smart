"""Fixtures communes."""

import json
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Charge custom_components/ dans chaque test."""


FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def dashboard() -> dict:
    """Réponse anonymisée de /homes/dashboard (cloud BDR)."""
    return json.loads((FIXTURES / "dashboard.json").read_text())


@pytest.fixture
def state(dashboard: dict):
    """État de la chaudière tiré de dashboard.json (sans programmes ni extras)."""
    from datetime import UTC, datetime

    from custom_components.de_dietrich_smart.bdr.cloud import parse_dashboard
    from custom_components.de_dietrich_smart.bdr.models import State

    return State(ts=datetime(2026, 11, 2, 20, 0, tzinfo=UTC), source="bdr", appliances=parse_dashboard(dashboard))


@pytest.fixture
def mock_client(state):
    """BdrClient remplacé : fetch_state rend `state` (modifiable par le test), commandes enregistrées."""
    from unittest.mock import AsyncMock, MagicMock, patch

    client = MagicMock()
    client.start = AsyncMock()
    client.close = AsyncMock()
    client.fetch_state = AsyncMock(return_value=state)
    client.fetch_consumption_days = AsyncMock(return_value=[])
    client.fetch_consumption_hours = AsyncMock(return_value=[])
    with patch("custom_components.de_dietrich_smart.BdrClient", return_value=client):
        yield client


@pytest.fixture
def config_entry(hass):
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain="de_dietrich_smart",
        title="EMC-S",
        unique_id="a1b2c3",
        data={"username": "julie@example.org", "password": "mot-de-passe"},
    )
    entry.add_to_hass(hass)
    return entry
