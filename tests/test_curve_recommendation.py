"""Coordinateur du diagnostic et capteur de recommandation."""

import copy

from .conftest import push

ENTITY = "sensor.salon_curve_recommendation"


async def test_recommends_switch_for_unsafe_curve(hass, loaded):
    # Courbe du fixture (1,5 / 15) dangereuse pour le plancher : bascule, sans attendre de données.
    s = hass.states.get(ENTITY)
    assert s is not None and s.state == "switch_to_floor"
    assert (s.attributes["slope"], s.attributes["base"], s.attributes["confidence"]) == (0.3, 33.0, "high")
    assert s.attributes["reasons"][0].startswith("Courbe hors des bornes du plancher")
    assert "switch_to_floor" in s.attributes["options"]


async def test_recomputed_when_curve_changes(hass, loaded, state):
    safe = copy.deepcopy(state)
    safe.zone.heating_curve = {**safe.zone.heating_curve, "slope": 0.5, "base": 22.0}
    await push(hass, loaded, safe)
    await hass.async_block_till_done()
    # Sans enregistreur dans ce test : pas de mesures.
    assert hass.states.get(ENTITY).state == "insufficient_data"
    assert loaded.diagnostic.data.action == "insufficient_data"


async def test_no_switch_for_radiators(hass, config_entry, mock_client):
    hass.config_entries.async_update_entry(config_entry, options={"emitter": "radiators"})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert hass.states.get(ENTITY).state == "insufficient_data"
