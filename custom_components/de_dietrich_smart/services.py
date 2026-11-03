"""Services : dérogation, vacances, créneaux des programmes, annulation."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import TYPE_CHECKING

import probatio
from homeassistant.const import ATTR_CONFIG_ENTRY_ID, ATTR_TEMPERATURE
from homeassistant.core import HomeAssistant, ServiceCall, callback
from homeassistant.exceptions import ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import service

from .commands import CommandError, CommandResult
from .const import CONF_DEFAULT_DURATION, DEFAULT_OPTIONS, DOMAIN, DURATION_CHOICES

if TYPE_CHECKING:
    from . import DeDietrichConfigEntry

ENTRY = {probatio.Required(ATTR_CONFIG_ENTRY_ID): cv.string}
SWITCH_POINT = probatio.Schema(
    {
        probatio.Required("day"): probatio.All(probatio.Coerce(int), probatio.Range(min=0, max=6)),
        probatio.Required("time"): cv.string,
        probatio.Optional("activity"): probatio.Coerce(int),
        probatio.Optional("setpoint"): probatio.Any(None, probatio.Coerce(float)),
        probatio.Optional("comfort"): cv.boolean,
    }
)
SCHEMAS = {
    "set_override": probatio.Schema(
        {
            **ENTRY,
            probatio.Required(ATTR_TEMPERATURE): probatio.Coerce(float),
            probatio.Optional("duration"): probatio.In(DURATION_CHOICES),
        }
    ),
    "set_holiday": probatio.Schema({**ENTRY, probatio.Required("start"): cv.date, probatio.Required("end"): cv.date}),
    "cancel_holiday": probatio.Schema(ENTRY),
    "set_week_program": probatio.Schema(
        {
            **ENTRY,
            probatio.Required("program"): probatio.In(["1", "2", "3", "dhw"]),
            probatio.Required("switch_points"): [SWITCH_POINT],
        }
    ),
    "undo_last_command": probatio.Schema(ENTRY),
}


async def _run(coro: Awaitable[CommandResult]) -> None:
    try:
        await coro
    except CommandError as exc:
        raise ServiceValidationError(
            translation_domain=DOMAIN, translation_key="command_failed", translation_placeholders={"message": str(exc)}
        ) from exc


async def _handle(call: ServiceCall) -> None:
    entry: DeDietrichConfigEntry = service.async_get_config_entry(call.hass, DOMAIN, call.data[ATTR_CONFIG_ENTRY_ID])
    commands = entry.runtime_data.commands
    match call.service:
        case "set_override":
            duration = call.data.get("duration") or entry.options.get(
                CONF_DEFAULT_DURATION, DEFAULT_OPTIONS[CONF_DEFAULT_DURATION]
            )
            await _run(commands.set_setpoint(call.data[ATTR_TEMPERATURE], duration))
        case "set_holiday":
            await _run(commands.set_holiday(call.data["start"], call.data["end"]))
        case "cancel_holiday":
            await _run(commands.set_holiday(None, None))
        case "set_week_program":
            week = [dict(sp) for sp in call.data["switch_points"]]
            if call.data["program"] == "dhw":
                await _run(commands.set_dhw_week(week))
            else:
                await _run(commands.set_week(int(call.data["program"]), week))
        case "undo_last_command":
            if (cid := commands.last_undoable()) is None:
                raise ServiceValidationError(translation_domain=DOMAIN, translation_key="nothing_to_undo")
            await _run(commands.undo(cid))


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    for name, schema in SCHEMAS.items():
        hass.services.async_register(DOMAIN, name, _handle, schema=schema)
