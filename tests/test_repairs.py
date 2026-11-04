"""Réparations : pression, défaut, courbe dangereuse pour le plancher, cloud injoignable."""

import copy

import pytest
from homeassistant.helpers import issue_registry as ir
from pytest_homeassistant_custom_component.common import async_fire_time_changed

from custom_components.de_dietrich_smart.bdr.models import SourceError

from .conftest import push

DOMAIN = "de_dietrich_smart"


def issue(hass, entry, key):
    return ir.async_get(hass).async_get_issue(DOMAIN, f"{key}_{entry.entry_id}")


async def with_curve(hass, rt, state, slope, base):
    s = copy.deepcopy(state)
    s.zone.heating_curve = {**s.zone.heating_curve, "slope": slope, "base": base}
    await push(hass, rt, s)


async def test_unsafe_curve_today(hass, loaded, config_entry, state):
    # Courbe relevée : 15 + 1,5 × 35 = 67,5 °C à −15 °C, au-delà de 45 °C.
    i = issue(hass, config_entry, "unsafe_floor_curve")
    assert i is not None and i.severity is ir.IssueSeverity.ERROR
    assert i.translation_placeholders["flow"] == "67,5"
    # 24 + 0,8 × 35 = 52 > 45 : toujours dangereuse.
    await with_curve(hass, loaded, state, 0.8, 24.0)
    assert issue(hass, config_entry, "unsafe_floor_curve") is not None
    # 22 + 0,6 × 35 = 43 : plus d'alerte.
    await with_curve(hass, loaded, state, 0.6, 22.0)
    assert issue(hass, config_entry, "unsafe_floor_curve") is None


async def test_mixing_connection_no_issue(hass, config_entry, mock_client):
    hass.config_entries.async_update_entry(config_entry, options={"connection": "mixing"})
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert issue(hass, config_entry, "unsafe_floor_curve") is None


@pytest.mark.parametrize(("pressure", "expected"), [(0.8, True), (1.6, False)])
async def test_low_pressure_issue(hass, loaded, config_entry, state, pressure, expected):
    s = copy.deepcopy(state)
    s.appliance.pressure = pressure
    await push(hass, loaded, s)
    assert (issue(hass, config_entry, "low_pressure") is not None) is expected


async def test_boiler_fault_issue(hass, loaded, config_entry, state):
    s = copy.deepcopy(state)
    s.appliance.in_error, s.appliance.error_status = True, "Locking"
    s.appliance.active_errors = [{"code": "H01.09", "text": None, "raw": {}}]
    await push(hass, loaded, s)
    i = issue(hass, config_entry, "boiler_fault")
    assert i is not None and i.translation_placeholders["code"] == "H01.09"
    assert "gaz" in i.translation_placeholders["label"].lower()
    await push(hass, loaded, state)
    assert issue(hass, config_entry, "boiler_fault") is None


async def test_cloud_unreachable_after_15_min(hass, loaded, config_entry, mock_client, freezer):
    loaded.coordinator.async_add_listener(lambda: None)
    mock_client.fetch_state.side_effect = SourceError("cloud BDR injoignable")
    for _ in range(6):
        freezer.tick(loaded.coordinator.update_interval)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert issue(hass, config_entry, "cloud_unreachable") is not None
    mock_client.fetch_state.side_effect = None
    freezer.tick(loaded.coordinator.update_interval)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert issue(hass, config_entry, "cloud_unreachable") is None


async def test_cloud_unreachable_at_15_min_despite_backoff(hass, loaded, config_entry, mock_client, freezer):
    """Relèves espacées (2, 6, 14, 29 min) : l'alerte tombe quand même à 15 min, pas à la relève suivante."""
    loaded.coordinator.async_add_listener(lambda: None)
    mock_client.fetch_state.side_effect = SourceError("cloud BDR injoignable")
    freezer.tick(loaded.coordinator.update_interval)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    elapsed = 0.0
    while elapsed + loaded.coordinator.update_interval.total_seconds() <= 900:
        elapsed += loaded.coordinator.update_interval.total_seconds()
        freezer.tick(loaded.coordinator.update_interval)
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert issue(hass, config_entry, "cloud_unreachable") is None
    freezer.tick(901 - elapsed)
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert issue(hass, config_entry, "cloud_unreachable") is not None


async def test_curve_without_base(hass, loaded, config_entry, state):
    """Courbe sans pied (baseSetpoint absent) : ni exception, ni alerte, départ calculé inconnu."""
    from custom_components.de_dietrich_smart.sensor import _curve_flow

    await with_curve(hass, loaded, state, 1.5, None)
    assert issue(hass, config_entry, "unsafe_floor_curve") is None
    assert _curve_flow(loaded.coordinator.data) is None


async def test_entry_removal_cleans_up(hass, hass_storage, loaded, config_entry):
    """Suppression de l'intégration : plus d'alerte orpheline ni de fichier .storage."""
    await loaded.consumption.async_save()
    await loaded.journal.async_save()
    assert issue(hass, config_entry, "unsafe_floor_curve") is not None
    assert await hass.config_entries.async_remove(config_entry.entry_id)
    await hass.async_block_till_done()
    assert not [i for i in ir.async_get(hass).issues.values() if i.domain == DOMAIN]
    assert not [k for k in hass_storage if config_entry.entry_id in k]
