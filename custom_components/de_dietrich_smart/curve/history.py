"""Lecture de l'enregistreur pour le diagnostic : moyennes horaires et plages de chauffe.

Les capteurs sont retrouvés par leur unique_id : le diagnostic suit les renommages d'entités.
"""

from __future__ import annotations

from datetime import datetime

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from homeassistant.util import dt as dt_util

from ..bdr.models import State
from ..const import DOMAIN
from .diagnostic import Hour

# Rôle → (plateforme, objet porteur, clé de l'unique_id).
SOURCES: dict[str, tuple[str, str, str]] = {
    "room": ("sensor", "zone", "room_temperature"),
    "setpoint": ("sensor", "zone", "target_temperature"),
    "outdoor": ("sensor", "appliance", "outdoor_temperature"),
    "ratio": ("sensor", "zone", "heating_ratio"),
    "heating": ("binary_sensor", "zone", "heating"),
}


def resolve_entity_ids(hass: HomeAssistant, state: State | None) -> dict[str, str | None]:
    reg = er.async_get(hass)
    out: dict[str, str | None] = {}
    for role, (platform, kind, key) in SOURCES.items():
        obj = (state.zone if kind == "zone" else state.appliance) if state else None
        out[role] = reg.async_get_entity_id(platform, DOMAIN, f"{obj.id}_{key}") if obj else None
    return out


async def fetch_hours(hass: HomeAssistant, ids: dict[str, str | None], start: datetime, end: datetime) -> list[Hour]:
    """Moyennes horaires des statistiques ; une heure est gardée dès qu'une mesure existe."""
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period

    wanted = {role: eid for role in ("room", "setpoint", "outdoor", "ratio") if (eid := ids.get(role))}
    if not wanted:
        return []
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, start, end, set(wanted.values()), "hour", None, {"mean"}
    )
    by_start: dict[float, dict[str, float | None]] = {}
    for role, eid in wanted.items():
        for r in rows.get(eid, []):
            by_start.setdefault(r["start"], {})[role] = r.get("mean")
    return [
        Hour(
            dt_util.utc_from_timestamp(ts),
            v.get("room"),
            v.get("setpoint"),
            v.get("outdoor"),
            v.get("ratio"),
        )
        for ts, v in sorted(by_start.items())
    ]


async def fetch_changes(
    hass: HomeAssistant, entity_id: str | None, start: datetime, end: datetime
) -> list[tuple[datetime, bool]]:
    """Changements marche/arrêt d'un capteur binaire (états inconnus ignorés)."""
    if not entity_id:
        return []
    from homeassistant.components.recorder import get_instance, history

    states = await get_instance(hass).async_add_executor_job(
        history.state_changes_during_period, hass, start, end, entity_id
    )
    return [
        (s.last_changed, s.state == STATE_ON) for s in states.get(entity_id, []) if s.state in (STATE_ON, STATE_OFF)
    ]


async def fetch_min_max(
    hass: HomeAssistant, entity_id: str | None, start: datetime, end: datetime
) -> list[tuple[datetime, float, float]]:
    """Mini et maxi horaires d'un capteur (statistiques de l'enregistreur)."""
    if not entity_id:
        return []
    from homeassistant.components.recorder import get_instance
    from homeassistant.components.recorder.statistics import statistics_during_period

    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, start, end, {entity_id}, "hour", None, {"min", "max"}
    )
    out = []
    for r in rows.get(entity_id, []):
        lo, hi = r.get("min"), r.get("max")
        if lo is not None and hi is not None:
            out.append((dt_util.utc_from_timestamp(r["start"]), lo, hi))
    return out
