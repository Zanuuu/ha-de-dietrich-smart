"""API WebSocket du panneau latéral : état, journal, créneaux, activités, annulation."""

from __future__ import annotations

import time
from collections.abc import Awaitable
from typing import TYPE_CHECKING, Any

import probatio
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback

from .commands import CommandError, CommandResult
from .const import DOMAIN, UNDO_WINDOW

if TYPE_CHECKING:
    from . import DeDietrichConfigEntry

T = DOMAIN


def _entry(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> DeDietrichConfigEntry | None:
    entry = hass.config_entries.async_get_entry(msg["entry_id"])
    if entry is None or entry.domain != DOMAIN or entry.state is not ConfigEntryState.LOADED:
        connection.send_error(msg["id"], websocket_api.ERR_NOT_FOUND, "chaudière introuvable")
        return None
    return entry


def _undo(entry: DeDietrichConfigEntry) -> dict[str, Any] | None:
    cid = entry.runtime_data.commands.last_undoable()
    if cid is None:
        return None
    c = entry.runtime_data.journal.command(cid)
    assert c is not None
    until = c["ts"] + UNDO_WINDOW
    return {"command_id": cid, "undo_until": until, "undo_remaining": _remaining(until), "action": c["action"]}


def _remaining(until: float | None) -> float | None:
    """Secondes restantes pour annuler, selon l'horloge du serveur (celle du navigateur peut dériver)."""
    return max(0.0, round(until - time.time(), 1)) if until is not None else None


async def _send(connection: websocket_api.ActiveConnection, msg: dict, coro: Awaitable[CommandResult]) -> None:
    try:
        res = await coro
    except CommandError as exc:
        connection.send_error(msg["id"], "command_failed", str(exc))
        return
    connection.send_result(
        msg["id"],
        {
            "command_id": res.command_id,
            "message": res.message,
            "undo_until": res.undo_until,
            "undo_remaining": _remaining(res.undo_until),
        },
    )


@websocket_api.websocket_command({probatio.Required("type"): f"{T}/overview"})
@callback
def ws_overview(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    entries = []
    for entry in hass.config_entries.async_loaded_entries(DOMAIN):
        state = entry.runtime_data.coordinator.data
        entries.append(
            {
                "entry_id": entry.entry_id,
                "title": entry.title,
                "state": state.to_dict() if state else None,
                "undo": _undo(entry),
            }
        )
    connection.send_result(msg["id"], {"entries": entries})


@websocket_api.websocket_command(
    {
        probatio.Required("type"): f"{T}/journal",
        probatio.Required("entry_id"): str,
        probatio.Optional("limit", default=100): probatio.All(int, probatio.Range(min=1, max=500)),
        probatio.Optional("kind"): str,
        probatio.Optional("before"): int,
    }
)
@callback
def ws_journal(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    if not (entry := _entry(hass, connection, msg)):
        return
    j = entry.runtime_data.journal
    connection.send_result(
        msg["id"],
        {"events": j.events(msg["limit"], msg.get("kind"), msg.get("before")), "commands": j.commands(msg["limit"])},
    )


SWITCH_POINT = {
    probatio.Required("day"): int,
    probatio.Required("time"): str,
    probatio.Optional("activity"): int,
    probatio.Optional("setpoint"): probatio.Any(None, float, int),
    probatio.Optional("comfort"): bool,
}


@websocket_api.websocket_command(
    {
        probatio.Required("type"): f"{T}/set_week",
        probatio.Required("entry_id"): str,
        probatio.Required("program"): int,
        probatio.Required("switch_points"): [SWITCH_POINT],
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set_week(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    if entry := _entry(hass, connection, msg):
        week = [dict(sp) for sp in msg["switch_points"]]
        await _send(connection, msg, entry.runtime_data.commands.set_week(msg["program"], week))


@websocket_api.websocket_command(
    {
        probatio.Required("type"): f"{T}/set_dhw_week",
        probatio.Required("entry_id"): str,
        probatio.Required("switch_points"): [SWITCH_POINT],
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set_dhw_week(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    if entry := _entry(hass, connection, msg):
        week = [dict(sp) for sp in msg["switch_points"]]
        await _send(connection, msg, entry.runtime_data.commands.set_dhw_week(week))


@websocket_api.websocket_command(
    {
        probatio.Required("type"): f"{T}/set_activity",
        probatio.Required("entry_id"): str,
        probatio.Required("activity"): int,
        probatio.Required("setpoint"): probatio.Coerce(float),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set_activity(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    if entry := _entry(hass, connection, msg):
        await _send(connection, msg, entry.runtime_data.commands.set_activity(msg["activity"], msg["setpoint"]))


@websocket_api.websocket_command(
    {
        probatio.Required("type"): f"{T}/undo",
        probatio.Required("entry_id"): str,
        probatio.Required("command_id"): int,
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_undo(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    if entry := _entry(hass, connection, msg):
        await _send(connection, msg, entry.runtime_data.commands.undo(msg["command_id"]))


@callback
def async_setup_websocket(hass: HomeAssistant) -> None:
    for handler in (ws_overview, ws_journal, ws_set_week, ws_set_dhw_week, ws_set_activity, ws_undo):
        websocket_api.async_register_command(hass, handler)
