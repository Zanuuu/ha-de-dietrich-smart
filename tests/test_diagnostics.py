"""Diagnostics : aucune donnée personnelle ni identifiant réel."""

import json
import re

from custom_components.de_dietrich_smart.diagnostics import async_get_config_entry_diagnostics


async def test_diagnostics_redacted(hass, config_entry, mock_client, dashboard):
    mock_client.raw.return_value = {"dashboard": dashboard}
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    diag = await async_get_config_entry_diagnostics(hass, config_entry)
    text = json.dumps(diag, default=str)
    assert "test@example.org" not in text and "mot-de-passe" not in text
    assert diag["entry"]["data"] == {"username": "**REDACTED**", "password": "**REDACTED**"}
    # Identifiants d'appareil remplacés par des pseudonymes.
    assert "a1b2c3" not in text and not re.search(r'"(z1|h1)"', text)
    assert diag["state"]["appliances"][0]["zones"][0]["setpoint"] == 21.0
    # Diagnostic de la courbe joint (pas d'identifiant dedans).
    assert diag["curve_diagnosis"]["action"] == "switch_to_floor"
