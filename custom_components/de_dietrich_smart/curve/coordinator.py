"""Diagnostic de la courbe recalculé chaque heure (et quand la courbe change), sans appel au cloud."""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from ..const import DOMAIN
from .diagnostic import WINDOW_DAYS, Diagnosis, count_short_cycles, diagnose
from .history import fetch_changes, fetch_hours, resolve_entity_ids

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from ..coordinator import DeDietrichCoordinator

_LOGGER = logging.getLogger(__name__)
# Cycles courts : l'historique d'états est purgé au bout de 10 jours par défaut.
CYCLES_DAYS = 7


class DiagnosticCoordinator(DataUpdateCoordinator[Diagnosis]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, main: DeDietrichCoordinator) -> None:
        super().__init__(
            hass, _LOGGER, config_entry=entry, name=f"{DOMAIN}_diagnostic", update_interval=timedelta(hours=1)
        )
        self.main = main
        self._curve: tuple[Any, Any] | None = None

    def _current_curve(self) -> dict[str, Any] | None:
        s = self.main.data
        return s.zone.heating_curve if s and s.zone else None

    @callback
    def async_on_state(self) -> None:
        """Courbe changée (commande, réglage sur le thermostat) : diagnostic refait."""
        c = self._current_curve()
        key = (c.get("slope"), c.get("base")) if c else None
        if key != self._curve:
            self.hass.async_create_task(self.async_request_refresh())

    async def _async_update_data(self) -> Diagnosis:
        curve = self._current_curve()
        self._curve = (curve.get("slope"), curve.get("base")) if curve else None
        opts = self.config_entry.options if self.config_entry else {}
        hours, cycles = [], None
        if "recorder" in self.hass.config.components:
            now = dt_util.utcnow()
            ids = resolve_entity_ids(self.hass, self.main.data)
            try:
                hours = await fetch_hours(self.hass, ids, now - timedelta(days=WINDOW_DAYS), now)
                since = now - timedelta(days=CYCLES_DAYS)
                changes = await fetch_changes(self.hass, ids["heating"], since, now)
            except Exception as exc:
                raise UpdateFailed(f"historique illisible : {exc}") from exc
            if changes:
                cycles = count_short_cycles(changes, max(since, changes[0][0]), now)
        return diagnose(hours, cycles, curve, opts)
