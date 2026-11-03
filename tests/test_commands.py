"""Commandes : contrôles, annulation et retours au programme (port de controller.py)."""

import copy
from datetime import date, timedelta

import pytest

from custom_components.de_dietrich_smart.commands import CommandError, CommandRunner
from custom_components.de_dietrich_smart.journal import Journal


@pytest.fixture
async def rt(hass, config_entry, mock_client):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry.runtime_data


async def test_setpoint_rounds_and_bounds(rt, mock_client):
    res = await rt.commands.set_setpoint(19.3, "next")
    mock_client.zone_override.assert_awaited_once_with("z1", 19.5)
    assert res.message == "19,5 °C jusqu'au prochain créneau" and res.undo_until is not None
    with pytest.raises(CommandError, match="hors limites"):
        await rt.commands.set_setpoint(31, "next")
    with pytest.raises(CommandError, match="durée inconnue"):
        await rt.commands.set_setpoint(20, "3h")


async def test_override_1h_adds_pending(rt, mock_client, freezer):
    freezer.move_to("2026-11-04 10:00:00+00:00")
    await rt.commands.set_setpoint(20, "1h")
    mock_client.zone_manual.assert_awaited_once_with("z1", 20.0)
    (p,) = rt.journal.pendings()
    assert p["action"] == "zone_schedule" and p["due"] == pytest.approx(1793786400 + 3600)
    assert p["params"] == {"zone_id": "z1", "program": 2}


async def test_pending_return_survives_restart(hass, rt, config_entry, mock_client, state, freezer):
    freezer.move_to("2026-11-04 10:00:00+00:00")
    await rt.commands.set_setpoint(20, "1h")
    await rt.journal.async_save()
    # Redémarrage : nouveau journal relu du disque, nouvel exécuteur.
    journal = Journal(hass, config_entry.entry_id)
    await journal.async_load()
    runner = CommandRunner(hass, mock_client, rt.coordinator, journal)
    manual = copy.deepcopy(state)
    manual.zone.mode, manual.zone.setpoint = "manual", 20.0
    rt.coordinator.async_set_updated_data(manual)
    freezer.tick(timedelta(seconds=3601))
    await runner.async_run_pending()
    mock_client.zone_schedule.assert_awaited_once_with("z1", 2)
    assert journal.pendings() == []


async def test_pending_return_dropped_if_changed(hass, rt, mock_client, state, freezer):
    freezer.move_to("2026-11-04 10:00:00+00:00")
    await rt.commands.set_setpoint(20, "2h")
    changed = copy.deepcopy(state)
    changed.zone.mode, changed.zone.setpoint = "manual", 22.0
    rt.coordinator.async_set_updated_data(changed)
    freezer.tick(timedelta(seconds=7201))
    await rt.commands.async_run_pending()
    mock_client.zone_schedule.assert_not_awaited()
    assert rt.journal.events(1)[0]["message"] == "Retour au programme annulé : le réglage a changé entre-temps"


async def test_undo_window(rt, mock_client, freezer):
    freezer.move_to("2026-11-04 10:00:00+00:00")
    res = await rt.commands.set_mode("antifrost")
    freezer.tick(timedelta(seconds=119))
    await rt.commands.undo(res.command_id)
    # Avant : dérogation à 21 °C.
    mock_client.zone_override.assert_awaited_once_with("z1", 21.0)
    with pytest.raises(CommandError, match="ne peut plus être annulée"):
        await rt.commands.undo(res.command_id)
    res = await rt.commands.set_program(3)
    freezer.tick(timedelta(seconds=121))
    with pytest.raises(CommandError, match="délai d'annulation dépassé"):
        await rt.commands.undo(res.command_id)


async def test_undo_only_last_command(rt):
    first = await rt.commands.set_dhw_mode("eco")
    await rt.commands.set_dhw_mode("comfort")
    with pytest.raises(CommandError, match="une autre commande a suivi"):
        await rt.commands.undo(first.command_id)


@pytest.mark.parametrize(
    ("do", "restore_call", "restore_args"),
    [
        (lambda c: c.set_dhw_mode("eco"), "dhw_mode", ("h1", "schedule")),
        (lambda c: c.set_dhw_setpoint("comfort", 50), "dhw_comfort_setpoint", ("h1", 55.0)),
        (lambda c: c.set_fireplace(True), "zone_fireplace", ("z1", False)),
    ],
)
async def test_undo_restores_before(rt, mock_client, do, restore_call, restore_args):
    res = await do(rt.commands)
    getattr(mock_client, restore_call).reset_mock()
    await rt.commands.undo(res.command_id)
    getattr(mock_client, restore_call).assert_awaited_once_with(*restore_args)


async def test_boost_requires_schedule(rt, state):
    eco = copy.deepcopy(state)
    eco.dhw.mode, eco.dhw.boost_capable = "eco", True
    rt.coordinator.async_set_updated_data(eco)
    with pytest.raises(CommandError, match="qu'en mode programme"):
        await rt.commands.dhw_boost()


async def test_holiday_max_365_days(rt, freezer):
    freezer.move_to("2026-11-04 10:00:00+00:00")
    with pytest.raises(CommandError, match="limitée à un an"):
        await rt.commands.set_holiday(date(2026, 12, 1), date(2027, 12, 2))
    with pytest.raises(CommandError, match="au moins un jour"):
        await rt.commands.set_holiday(date(2026, 12, 1), date(2026, 12, 1))


async def test_week_validation_errors(rt, state):
    with_week = copy.deepcopy(state)
    with_week.zone.week = {1: [{"day": 1, "time": "06:00", "activity": 2, "setpoint": None}]}
    rt.coordinator.async_set_updated_data(with_week)
    seven = [{"day": 2, "time": f"0{h}:00", "activity": 1} for h in range(7)]
    with pytest.raises(CommandError, match="6 créneaux au plus par jour"):
        await rt.commands.set_week(1, seven)
    with pytest.raises(CommandError, match="au moins un créneau"):
        await rt.commands.set_week(1, [])


async def test_failed_command_is_journaled(rt, mock_client):
    from custom_components.de_dietrich_smart.bdr.models import SourceError

    mock_client.zone_program.side_effect = SourceError("HTTP 500")
    with pytest.raises(CommandError, match="n'a pas abouti"):
        await rt.commands.set_program(3)
    (c,) = rt.journal.commands(1)
    assert c["ok"] is False and c["error"] == "HTTP 500"


async def test_pending_runs_after_next_poll(hass, rt, mock_client, state, freezer):
    """Branchement : l'échéance est traitée quand une nouvelle relève est publiée."""
    freezer.move_to("2026-11-04 10:00:00+00:00")
    await rt.commands.set_setpoint(20, "1h")
    manual = copy.deepcopy(state)
    manual.zone.mode, manual.zone.setpoint = "manual", 20.0
    freezer.tick(timedelta(seconds=3601))
    rt.coordinator.async_set_updated_data(manual)
    await hass.async_block_till_done()
    mock_client.zone_schedule.assert_awaited_once_with("z1", 2)
