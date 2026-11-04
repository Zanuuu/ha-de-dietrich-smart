"""Températures extérieures journalières (mini, maxi) pour les DJU.

L'enregistreur de Home Assistant ne remonte qu'à l'installation : le passé vient de l'archive
Open-Meteo (gratuite, sans compte), lue une fois puis gardée en `.storage`. Seules les coordonnées
de la maison, arrondies à 0,01° (environ 1 km), sont envoyées. Dès que l'enregistreur couvre au
moins 20 heures d'un jour, les mini et maxi du capteur extérieur de la chaudière l'emportent.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from homeassistant.core import HomeAssistant
from homeassistant.helpers.httpx_client import get_async_client
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from .history import fetch_min_max, resolve_entity_ids

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from ..coordinator import DeDietrichCoordinator
    from ..energy import ConsumptionCoordinator

_LOGGER = logging.getLogger(__name__)
ARCHIVE_URL = "https://archive-api.open-meteo.com/v1/archive"
FORECAST_URL = "https://api.open-meteo.com/v1/forecast"
# L'archive a quelques jours de retard : les 6 derniers jours viennent de l'API de prévision.
RECENT_DAYS = 6
HISTORY_DAYS = 400
HA_DAYS = 30
MIN_HOURS = 20

Temps = dict[date, tuple[float, float, str]]


def daily_from_hours(rows: list[tuple[datetime, float, float]], time_zone: str) -> dict[date, tuple[float, float]]:
    """Mini et maxi par jour local, pour les jours couverts par au moins 20 heures."""
    tz = ZoneInfo(time_zone)
    by_day: dict[date, list[tuple[float, float]]] = {}
    for ts, lo, hi in rows:
        by_day.setdefault(ts.astimezone(tz).date(), []).append((lo, hi))
    return {
        d: (round(min(lo for lo, _ in v), 1), round(max(hi for _, hi in v), 1))
        for d, v in by_day.items()
        if len(v) >= MIN_HOURS
    }


def parse_open_meteo(payload: dict[str, Any]) -> dict[date, tuple[float, float]]:
    d = payload.get("daily") or {}
    return {
        date.fromisoformat(day): (float(lo), float(hi))
        for day, lo, hi in zip(
            d.get("time", []), d.get("temperature_2m_min", []), d.get("temperature_2m_max", []), strict=False
        )
        if lo is not None and hi is not None
    }


def open_meteo_requests(
    missing: list[date], today: date, latitude: float, longitude: float, time_zone: str
) -> list[tuple[str, dict[str, Any]]]:
    base = {
        "latitude": round(latitude, 2),
        "longitude": round(longitude, 2),
        "daily": "temperature_2m_min,temperature_2m_max",
        "timezone": time_zone,
    }
    old = [d for d in missing if d < today - timedelta(days=RECENT_DAYS)]
    recent = [d for d in missing if d >= today - timedelta(days=RECENT_DAYS)]
    out: list[tuple[str, dict[str, Any]]] = []
    if old:
        out.append((ARCHIVE_URL, {**base, "start_date": min(old).isoformat(), "end_date": max(old).isoformat()}))
    if recent:
        out.append((FORECAST_URL, {**base, "past_days": 7, "forecast_days": 1}))
    return out


class OutdoorCoordinator(DataUpdateCoordinator[Temps]):
    """Complète les températures journalières une fois par jour (relu toutes les 6 h si un appel a échoué)."""

    def __init__(
        self, hass: HomeAssistant, entry: ConfigEntry, main: DeDietrichCoordinator, consumption: ConsumptionCoordinator
    ) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=f"{DOMAIN}_outdoor", update_interval=timedelta(hours=6)
        )
        self.main = main
        self.consumption = consumption
        self._store: Store[dict] = Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.outdoor")
        self.days: Temps = {}
        self._done: date | None = None
        self._warned = False

    async def async_load(self) -> None:
        raw = await self._store.async_load() or {}
        for k, (lo, hi, src) in (raw.get("days") or {}).items():
            self.days[date.fromisoformat(k)] = (lo, hi, src)

    def _dump(self) -> dict:
        return {"days": {k.isoformat(): list(v) for k, v in sorted(self.days.items())}}

    async def async_save(self) -> None:
        await self._store.async_save(self._dump())

    def _first_day(self, today: date) -> date:
        cons = self.consumption.data.days if self.consumption.data else []
        return min((d.day for d in cons), default=today - timedelta(days=HISTORY_DAYS))

    async def _async_update_data(self) -> Temps:
        today = dt_util.now().date()
        if self._done == today:
            return dict(self.days)
        tz = self.hass.config.time_zone
        if "recorder" in self.hass.config.components:
            now = dt_util.utcnow()
            eid = resolve_entity_ids(self.hass, self.main.data)["outdoor"]
            rows = await fetch_min_max(self.hass, eid, now - timedelta(days=HA_DAYS), now)
            for d, (lo, hi) in daily_from_hours(rows, tz).items():
                if d < today:
                    self.days[d] = (lo, hi, "ha")
        first = self._first_day(today)
        missing = [
            first + timedelta(days=n) for n in range((today - first).days) if first + timedelta(days=n) not in self.days
        ]
        ok = True
        client = get_async_client(self.hass)
        for url, params in open_meteo_requests(
            missing, today, self.hass.config.latitude, self.hass.config.longitude, tz
        ):
            try:
                r = await client.get(url, params=params, timeout=30)
                r.raise_for_status()
                got = parse_open_meteo(r.json())
            except Exception as exc:
                ok = False
                if not self._warned:
                    _LOGGER.warning("Open-Meteo indisponible, DJU incomplets : %s", exc)
                    self._warned = True
                continue
            wanted = set(missing)
            self.days.update({d: (lo, hi, "open-meteo") for d, (lo, hi) in got.items() if d in wanted})
        if ok:
            self._done = today
        self._store.async_delay_save(self._dump, 10)
        return dict(self.days)
