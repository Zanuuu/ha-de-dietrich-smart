"""Relève de l'état de la chaudière sur le cloud BDR Thermea."""

from __future__ import annotations

import logging
import time
from collections import deque
from typing import TYPE_CHECKING, Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from . import events
from .bdr.cloud import BdrClient
from .bdr.models import AuthError, SourceError, State
from .const import DOMAIN, POLL_INTERVAL, POLL_MAX, REFRESH_SOON
from .journal import Journal

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

_LOGGER = logging.getLogger(__name__)
# Changement attendu après une commande : oublié au bout de 10 min.
EXPECT_TTL = 600
# Événement « connexion perdue » au 3e échec de suite.
LOST_AFTER = 3


class DeDietrichCoordinator(DataUpdateCoordinator[State]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry, client: BdrClient, journal: Journal) -> None:
        super().__init__(hass, _LOGGER, config_entry=entry, name=DOMAIN, update_interval=POLL_INTERVAL)
        self.client = client
        self.journal = journal
        self.previous: State | None = None
        self.errors_in_row = 0
        self.last_error: str | None = None
        self._expect: list[tuple[float, str, Any]] = []
        self._soon: Any = None
        # Demande de chauffe relevée à chaque relève, sur la dernière heure : (instant, en chauffe).
        self._demand: deque[tuple[float, bool]] = deque()

    async def _async_update_data(self) -> State:
        try:
            state = await self.client.fetch_state()
        except AuthError as exc:
            raise ConfigEntryAuthFailed(str(exc)) from exc
        except SourceError as exc:
            self.errors_in_row += 1
            self.last_error = str(exc)
            self.update_interval = min(POLL_INTERVAL * 2**self.errors_in_row, POLL_MAX)
            if self.errors_in_row == LOST_AFTER:
                self.journal.add_event(events.event("connexion", f"Connexion perdue : {exc}", "warning"))
            raise UpdateFailed(str(exc)) from exc
        if self.errors_in_row >= LOST_AFTER:
            self.journal.add_event(events.event("connexion", "Connexion rétablie", "good"))
        self.errors_in_row, self.last_error = 0, None
        self.update_interval = POLL_INTERVAL
        prev = self.data
        new_events = events.initial(state) if prev is None else events.diff(prev, state, self._origin)
        for ev in new_events:
            self.journal.add_event(ev)
        self.previous = prev
        self._sample_demand(state)
        return state

    def _sample_demand(self, state: State) -> None:
        now = time.time()
        if state.zone and state.zone.demand is not None:
            self._demand.append((now, state.zone.demand == "heating"))
        while self._demand and now - self._demand[0][0] > 3600:
            self._demand.popleft()

    @callback
    def async_set_updated_data(self, data: State) -> None:
        self._sample_demand(data)
        super().async_set_updated_data(data)

    @property
    def heating_ratio(self) -> float | None:
        """Part des relèves de la dernière heure en chauffe (0 à 1)."""
        now = time.time()
        recent = [h for t, h in self._demand if now - t <= 3600]
        return sum(recent) / len(recent) if recent else None

    def expect(self, key: str, value: Any) -> None:
        """Changement attendu suite à une commande : son événement portera l'origine Home Assistant."""
        now = time.time()
        self._expect = [(t, k, v) for t, k, v in self._expect if now - t < EXPECT_TTL] + [(now, key, value)]

    def _origin(self, key: str, value: Any) -> str:
        for i, (_, k, v) in enumerate(self._expect):
            close = isinstance(v, float) and isinstance(value, float) and abs(v - value) < 0.05
            if k == key and (v == value or close):
                del self._expect[i]
                return events.APP_ORIGIN
        return events.EXT_ORIGIN

    async def async_shutdown(self) -> None:
        if self._soon:
            self._soon()
            self._soon = None
        await super().async_shutdown()

    @callback
    def async_refresh_soon(self) -> None:
        """Relève anticipée après une commande, le temps que le cloud la prenne en compte."""
        if self._soon:
            self._soon()

        async def _run(_now: Any) -> None:
            self._soon = None
            await self.async_request_refresh()

        self._soon = async_call_later(self.hass, REFRESH_SOON, _run)
