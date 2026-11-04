"""Services de l'intégration."""

import copy
from datetime import date

import pytest
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError

from custom_components.de_dietrich_smart.bdr.models import SourceError

DOMAIN = "de_dietrich_smart"


async def call(hass, entry, service, **data):
    await hass.services.async_call(DOMAIN, service, {"config_entry_id": entry.entry_id, **data}, blocking=True)


async def test_set_override(hass, loaded, config_entry, mock_client):
    await call(hass, config_entry, "set_override", temperature=20.5, duration="2h")
    mock_client.zone_manual.assert_awaited_once_with("z1", 20.5)
    await call(hass, config_entry, "set_override", temperature=19)
    mock_client.zone_override.assert_awaited_once_with("z1", 19.0)


async def test_holiday(hass, loaded, config_entry, mock_client, state, freezer):
    freezer.move_to("2026-11-04 10:00:00+00:00")
    await call(hass, config_entry, "set_holiday", start="2026-12-20", end="2027-01-03")
    mock_client.holiday.assert_awaited_once_with("a1b2c3", date(2026, 12, 20), date(2027, 1, 3))
    on = copy.deepcopy(state)
    on.appliance.holiday_start, on.appliance.holiday_end = "2026-12-20", "2027-01-03"
    loaded.coordinator.async_set_updated_data(on)
    await call(hass, config_entry, "cancel_holiday")
    mock_client.holiday.assert_awaited_with("a1b2c3", None, None)


async def test_set_week_program(hass, loaded, config_entry, mock_client, state):
    s = copy.deepcopy(state)
    s.zone.week = {2: [{"day": 1, "time": "06:00", "activity": 2, "setpoint": None}]}
    s.dhw.week = [{"day": 1, "time": "06:00", "comfort": True}]
    loaded.coordinator.async_set_updated_data(s)
    week = [{"day": 1, "time": "06:30", "activity": 4}, {"day": 1, "time": "22:00", "activity": 1}]
    await call(hass, config_entry, "set_week_program", program="2", switch_points=week)
    sent = mock_client.zone_week.await_args.args
    assert sent[0:2] == ("z1", 2) and [p["time"] for p in sent[2]] == ["06:30", "22:00"]
    await call(
        hass,
        config_entry,
        "set_week_program",
        program="dhw",
        switch_points=[{"day": 1, "time": "07:00", "comfort": True}],
    )
    mock_client.dhw_week.assert_awaited_once()


async def test_week_program_error_is_validation_error(hass, loaded, config_entry, state):
    s = copy.deepcopy(state)
    s.zone.week = {2: [{"day": 1, "time": "06:00", "activity": 2, "setpoint": None}]}
    loaded.coordinator.async_set_updated_data(s)
    with pytest.raises(ServiceValidationError) as err:
        await call(hass, config_entry, "set_week_program", program="2", switch_points=[])
    assert "au moins un créneau" in err.value.translation_placeholders["message"]


async def test_undo_last_command(hass, loaded, config_entry, mock_client):
    with pytest.raises(ServiceValidationError):
        await call(hass, config_entry, "undo_last_command")
    await call(hass, config_entry, "set_override", temperature=20, duration="permanent")
    await call(hass, config_entry, "undo_last_command")
    mock_client.zone_override.assert_awaited_once_with("z1", 21.0)


async def test_unknown_entry(hass, loaded):
    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(DOMAIN, "cancel_holiday", {"config_entry_id": "nope"}, blocking=True)


async def test_cloud_refusal_is_not_a_validation_error(hass, loaded, config_entry, mock_client):
    """Refus du cloud : erreur d'exécution, pas une saisie invalide de l'utilisateur."""
    mock_client.zone_override.side_effect = SourceError("HTTP 500")
    with pytest.raises(HomeAssistantError) as err:
        await call(hass, config_entry, "set_override", temperature=19)
    assert not isinstance(err.value, ServiceValidationError)
    assert "HTTP 500" in err.value.translation_placeholders["message"]
