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
