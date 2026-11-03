"""Réparations : cloud injoignable, pression basse, défaut chaudière, courbe dangereuse pour le plancher."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import issue_registry as ir

from .const import (
    CONF_BASE_TEMP,
    CONF_CONNECTION,
    CONF_EMITTER,
    CONF_MAX_FLOOR_FLOW,
    DEFAULT_OPTIONS,
    DOMAIN,
)
from .curve import flow_temperature
from .events import low_pressure
from .faults import describe

if TYPE_CHECKING:
    from . import DeDietrichConfigEntry

# Cloud injoignable : signalé après 15 min sans relève réussie.
UNREACHABLE_AFTER = 900


def _fmt(v: float) -> str:
    return f"{v:.1f}".replace(".", ",").removesuffix(",0")


@callback
def async_update_issues(hass: HomeAssistant, entry: DeDietrichConfigEntry) -> None:
    rt = entry.runtime_data
    state = rt.coordinator.data
    opts = {**DEFAULT_OPTIONS, **entry.options}

    def set_issue(key: str, active: bool, severity: ir.IssueSeverity, **placeholders: str) -> None:
        issue_id = f"{key}_{entry.entry_id}"
        if active:
            ir.async_create_issue(
                hass,
                DOMAIN,
                issue_id,
                is_fixable=False,
                severity=severity,
                translation_key=key,
                translation_placeholders={"name": entry.title, **placeholders},
            )
        else:
            ir.async_delete_issue(hass, DOMAIN, issue_id)

    since = rt.coordinator.failing_since
    set_issue(
        "cloud_unreachable",
        since is not None and time.time() - since >= UNREACHABLE_AFTER,
        ir.IssueSeverity.WARNING,
        error=rt.coordinator.last_error or "",
    )
    a = state.appliance if state else None
    if a is None:
        return
    set_issue(
        "low_pressure",
        low_pressure(a.pressure, a.pressure_ok),
        ir.IssueSeverity.WARNING,
        pressure=_fmt(a.pressure) if a.pressure is not None else "?",
    )
    code = next((e["code"] for e in (a.active_errors or []) if e.get("code")), None)
    fault = describe(code)
    set_issue(
        "boiler_fault",
        a.in_error,
        ir.IssueSeverity.ERROR,
        code=code or a.error_status or "?",
        label=fault.label if fault else "code non transmis par le cloud : lisez-le sur l'écran de la chaudière",
        advice=(fault.advice or "") if fault else "",
    )
    c = state.zone.heating_curve if state and state.zone else None
    unsafe = False
    flow = 0.0
    if c and opts[CONF_EMITTER] == "floor" and opts[CONF_CONNECTION] == "direct":
        flow = flow_temperature(c["slope"], c["base"], float(opts[CONF_BASE_TEMP]), c.get("max_flow"))
        unsafe = flow > float(opts[CONF_MAX_FLOOR_FLOW])
    set_issue(
        "unsafe_floor_curve",
        unsafe,
        ir.IssueSeverity.ERROR,
        flow=_fmt(flow),
        base_temp=_fmt(float(opts[CONF_BASE_TEMP])),
        max_floor_flow=_fmt(float(opts[CONF_MAX_FLOOR_FLOW])),
        slope=_fmt(c["slope"]) if c else "",
        base=_fmt(c["base"]) if c else "",
        max_flow=_fmt(c["max_flow"]) if c and c.get("max_flow") is not None else "?",
    )
