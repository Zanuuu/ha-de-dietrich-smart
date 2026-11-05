"""Diagnostics : état normalisé et réponses brutes du cloud, anonymisés."""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant

from . import DeDietrichConfigEntry
from .bdr.cloud import anonymize
from .bdr.models import SourceError

TO_REDACT = {CONF_USERNAME, CONF_PASSWORD}


async def async_get_config_entry_diagnostics(hass: HomeAssistant, entry: DeDietrichConfigEntry) -> dict[str, Any]:
    rt = entry.runtime_data
    try:
        raw: Any = await rt.client.raw()
    except SourceError as exc:
        raw = {"error": str(exc)}
    state = rt.coordinator.data
    return {
        "entry": {
            "data": async_redact_data(dict(entry.data), TO_REDACT),
            "options": dict(entry.options),
            "unique_id": anonymize(entry.unique_id, "id"),
        },
        "coordinator": {
            "last_update_success": rt.coordinator.last_update_success,
            "errors_in_row": rt.coordinator.errors_in_row,
            "last_error": rt.coordinator.last_error,
            "update_interval": str(rt.coordinator.update_interval),
        },
        "state": anonymize(state.to_dict()) if state else None,
        "raw": anonymize(raw),
        "recent_events": rt.journal.events(20),
        "curve_diagnosis": rt.diagnostic.data.as_dict() if rt.diagnostic.data else None,
        "balancing": rt.balancing.diagnostics(),
    }
