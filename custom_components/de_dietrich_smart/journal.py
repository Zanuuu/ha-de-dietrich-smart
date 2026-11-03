"""Journal persistant : événements, commandes et retours au programme en attente.

Remplace la base SQLite de l'application : un fichier `.storage` par entrée, borné.
"""

from __future__ import annotations

import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from .const import DOMAIN
from .events import Event

MAX_EVENTS = 500
MAX_COMMANDS = 500
SAVE_DELAY = 10


class Journal:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.journal")
        self._data: dict[str, Any] = {"next_id": 1, "events": [], "commands": [], "pendings": []}

    async def async_load(self) -> None:
        if (data := await self._store.async_load()) is not None:
            self._data = data

    async def async_save(self) -> None:
        await self._store.async_save(self._data)

    def _save(self) -> None:
        self._store.async_delay_save(lambda: self._data, SAVE_DELAY)

    def _next_id(self) -> int:
        n: int = self._data["next_id"]
        self._data["next_id"] = n + 1
        return n

    # --- Événements -------------------------------------------------------

    def add_event(self, ev: Event) -> None:
        self._data["events"].append({"id": self._next_id(), **ev})
        del self._data["events"][:-MAX_EVENTS]
        self._save()

    def events(self, limit: int = 100, kind: str | None = None, before: int | None = None) -> list[dict[str, Any]]:
        """Événements du plus récent au plus ancien."""
        out = []
        for e in reversed(self._data["events"]):
            if (kind is None or e["kind"] == kind) and (before is None or e["id"] < before):
                out.append(e)
                if len(out) >= limit:
                    break
        return out

    # --- Commandes --------------------------------------------------------

    def add_command(
        self, action: str, params: dict[str, Any], before: dict[str, Any], origin: str, undo_of: int | None = None
    ) -> int:
        cid = self._next_id()
        self._data["commands"].append(
            {
                "id": cid,
                "ts": time.time(),
                "action": action,
                "params": params,
                "before": before,
                "ok": None,
                "error": None,
                "origin": origin,
                "undo_of": undo_of,
                "undone": False,
            }
        )
        del self._data["commands"][:-MAX_COMMANDS]
        self._save()
        return cid

    def command(self, cid: int) -> dict[str, Any] | None:
        return next((c for c in self._data["commands"] if c["id"] == cid), None)

    def finish_command(self, cid: int, ok: bool, error: str | None = None) -> None:
        if c := self.command(cid):
            c["ok"], c["error"] = ok, error
            self._save()

    def mark_undone(self, cid: int) -> None:
        if c := self.command(cid):
            c["undone"] = True
            self._save()

    def commands(self, limit: int = 50) -> list[dict[str, Any]]:
        """Commandes de la plus récente à la plus ancienne."""
        return list(reversed(self._data["commands"][-limit:]))

    # --- Retours au programme en attente ----------------------------------

    def add_pending(
        self, due: float, action: str, params: dict[str, Any], command_id: int, expect: dict[str, Any]
    ) -> int:
        pid = self._next_id()
        self._data["pendings"].append(
            {"id": pid, "due": due, "action": action, "params": params, "command_id": command_id, "expect": expect}
        )
        self._save()
        return pid

    def pendings(self) -> list[dict[str, Any]]:
        return list(self._data["pendings"])

    def drop_pending(self, pid: int | None = None, zone_id: str | None = None) -> None:
        self._data["pendings"] = [
            p
            for p in self._data["pendings"]
            if not (
                (pid is not None and p["id"] == pid) or (zone_id is not None and p["params"].get("zone_id") == zone_id)
            )
        ]
        self._save()
