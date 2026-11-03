"""Fixtures communes."""

import json
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

# Import au chargement : le fixture hass remplace ensuite le paquet custom_components dans sys.modules.
from custom_components.de_dietrich_smart.bdr.cloud import BdrClient, parse_dashboard
from custom_components.de_dietrich_smart.bdr.models import State


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
    s = State(ts=datetime(2026, 11, 2, 20, 0, tzinfo=UTC), source="bdr", appliances=parse_dashboard(dashboard))
    # Lectures complémentaires relevées sur l'installation réelle le 03/11/2026.
    assert s.zone and s.dhw
    s.zone.heating_curve = {
        "slope": 1.5, "slope_min": 0.0, "slope_max": 4.0, "slope_step": 0.1,
        "base": 15.0, "base_min": 15.0, "base_max": 80.0, "base_step": 0.5, "max_flow": 80.0,
    }  # fmt: skip
    s.zone.activities = [
        {"n": n, "name": name, "setpoint": sp, "min": 5.0, "max": 30.0, "step": 0.5}
        for n, name, sp in (
            (1, "Nuit", 18.0),
            (2, "Maison", 19.0),
            (3, "Absent", 16.0),
            (4, "Matin", 19.0),
            (5, "Soir", 19.0),
        )
    ]
    s.dhw.boost_capable = True
    return s


@pytest.fixture
def mock_client(state):
    """BdrClient remplacé : fetch_state rend `state` (modifiable par le test), commandes enregistrées."""
    # spec : les méthodes async du vrai client deviennent des AsyncMock.
    client = AsyncMock(spec=BdrClient)
    client.fetch_state.return_value = state
    client.fetch_consumption_days.return_value = []
    client.fetch_consumption_hours.return_value = []
    with patch("custom_components.de_dietrich_smart.BdrClient", return_value=client):
        yield client


@pytest.fixture
def config_entry(hass):
    entry = MockConfigEntry(
        domain="de_dietrich_smart",
        title="EMC-S",
        unique_id="a1b2c3",
        data={"username": "julie@example.org", "password": "mot-de-passe"},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def loaded(hass, config_entry, mock_client):
    """Entrée chargée avec le client simulé ; rend les données d'exécution."""
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry.runtime_data


async def push(hass, rt, new_state) -> None:
    """Publie un nouvel état comme après une relève."""
    rt.coordinator.async_set_updated_data(new_state)
    await hass.async_block_till_done()
