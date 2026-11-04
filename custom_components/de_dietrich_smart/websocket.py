"""API WebSocket du panneau latéral : état, journal, créneaux, activités, courbe, simulation, annulation."""

from __future__ import annotations

import logging
import time
from collections.abc import Awaitable
from datetime import timedelta
from typing import TYPE_CHECKING, Any

import probatio
from homeassistant.components import websocket_api
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.util import dt as dt_util

from .commands import CommandError, CommandResult
from .const import (
    CONF_BASE_TEMP,
    CONF_MAX_FLOOR_FLOW,
    CONF_WEATHER_ENTITY,
    DEFAULT_OPTIONS,
    DOMAIN,
    FLOOR_BASE,
    FLOOR_SLOPE,
    UNDO_WINDOW,
)
from .curve import curve_flow, floor_violation, guard_active
from .curve.history import fetch_hours, resolve_entity_ids
from .curve.simulator import daily_summary, forecast_points, simulate

if TYPE_CHECKING:
    from . import DeDietrichConfigEntry

T = DOMAIN
_LOGGER = logging.getLogger(__name__)
# Rejeu : 7 derniers jours de statistiques du capteur extérieur.
REPLAY_DAYS = 7


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


@websocket_api.websocket_command({probatio.Required("type"): f"{T}/curve", probatio.Required("entry_id"): str})
@callback
def ws_curve(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Courbe, bornes du plancher, point du moment et diagnostic."""
    if not (entry := _entry(hass, connection, msg)):
        return
    rt = entry.runtime_data
    state = rt.coordinator.data
    curve = state.zone.heating_curve if state and state.zone else None
    opts = {**DEFAULT_OPTIONS, **entry.options}
    outdoor = state.appliance.outdoor_temp if state and state.appliance else None
    flow = curve_flow(curve, outdoor) if outdoor is not None else None
    violation = (
        floor_violation(curve["slope"], curve["base"], curve.get("max_flow"), opts)
        if curve and curve.get("slope") is not None and curve.get("base") is not None
        else None
    )
    connection.send_result(
        msg["id"],
        {
            "curve": curve,
            "guard": {
                "active": guard_active(opts),
                "slope": list(FLOOR_SLOPE),
                "base": list(FLOOR_BASE),
                "max_floor_flow": float(opts[CONF_MAX_FLOOR_FLOW]),
                "base_temp": float(opts[CONF_BASE_TEMP]),
                "violation": violation,
            },
            "now": {"outdoor": outdoor, "flow": round(flow, 1) if flow is not None else None},
            "diagnosis": rt.diagnostic.data.as_dict() if rt.diagnostic.data else None,
        },
    )


@websocket_api.websocket_command(
    {
        probatio.Required("type"): f"{T}/set_heating_curve",
        probatio.Required("entry_id"): str,
        probatio.Required("slope"): probatio.Coerce(float),
        probatio.Required("base"): probatio.Coerce(float),
    }
)
@websocket_api.require_admin
@websocket_api.async_response
async def ws_set_heating_curve(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    if entry := _entry(hass, connection, msg):
        await _send(connection, msg, entry.runtime_data.commands.set_heating_curve(msg["slope"], msg["base"]))


async def _forecast(hass: HomeAssistant, entity_id: str) -> list[tuple[Any, float]]:
    """Prévisions horaires de l'entité météo, à défaut journalières."""
    for kind in ("hourly", "daily"):
        try:
            res = await hass.services.async_call(
                "weather", "get_forecasts", {"entity_id": entity_id, "type": kind}, blocking=True, return_response=True
            )
        except HomeAssistantError as exc:
            _LOGGER.debug("prévisions %s indisponibles pour %s : %s", kind, entity_id, exc)
            continue
        by_entity: Any = (res or {}).get(entity_id) or {}
        if points := forecast_points(by_entity.get("forecast") or [], daily=kind == "daily"):
            return points
    return []


@websocket_api.websocket_command(
    {
        probatio.Required("type"): f"{T}/simulate",
        probatio.Required("entry_id"): str,
        probatio.Required("source"): probatio.In(["forecast", "history"]),
        probatio.Optional("slope"): probatio.Coerce(float),
        probatio.Optional("base"): probatio.Coerce(float),
    }
)
@websocket_api.async_response
async def ws_simulate(hass: HomeAssistant, connection: websocket_api.ActiveConnection, msg: dict) -> None:
    """Départ prévu (prévisions) ou rejoué (7 derniers jours) pour la courbe actuelle et la proposée."""
    if not (entry := _entry(hass, connection, msg)):
        return
    rt = entry.runtime_data
    state = rt.coordinator.data
    curve = state.zone.heating_curve if state and state.zone else None
    opts = {**DEFAULT_OPTIONS, **entry.options}
    dg = rt.diagnostic.data
    proposed: dict[str, float] | None = None
    if "slope" in msg and "base" in msg:
        proposed = {"slope": msg["slope"], "base": msg["base"]}
    elif dg and dg.action not in ("keep", "insufficient_data") and dg.slope is not None and dg.base is not None:
        proposed = {"slope": dg.slope, "base": dg.base}
    error, points = None, []
    if not curve:
        error = "Courbe de chauffe pas encore lue sur le cloud."
    elif msg["source"] == "forecast":
        if not (weather := opts.get(CONF_WEATHER_ENTITY)):
            error = "Choisissez une entité météo dans les options de l'intégration pour simuler les prochains jours."
        elif not (points := await _forecast(hass, weather)):
            error = f"Pas de prévisions de température pour {weather}."
    elif "recorder" not in hass.config.components:
        error = "Le rejeu des derniers jours demande l'enregistreur de Home Assistant."
    else:
        now = dt_util.utcnow()
        ids = resolve_entity_ids(hass, state)
        hours = await fetch_hours(hass, {"outdoor": ids["outdoor"]}, now - timedelta(days=REPLAY_DAYS), now)
        points = [(h.start, h.outdoor) for h in hours if h.outdoor is not None]
        if not points:
            error = "Pas encore de températures extérieures enregistrées."
    rows = simulate(points, curve, proposed) if curve else []
    connection.send_result(
        msg["id"],
        {
            "source": msg["source"],
            "error": error,
            "proposed": proposed,
            "max_floor_flow": float(opts[CONF_MAX_FLOOR_FLOW]),
            "guard_active": guard_active(opts),
            "rows": rows,
            "days": daily_summary(rows, hass.config.time_zone),
        },
    )


@callback
def async_setup_websocket(hass: HomeAssistant) -> None:
    for handler in (
        ws_overview,
        ws_journal,
        ws_set_week,
        ws_set_dhw_week,
        ws_set_activity,
        ws_curve,
        ws_set_heating_curve,
        ws_simulate,
        ws_undo,
    ):
        websocket_api.async_register_command(hass, handler)
