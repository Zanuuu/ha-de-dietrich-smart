"""État de l'assistant de la nourrice : un fichier `.storage` par entrée, sur le modèle du journal."""

from __future__ import annotations

import time
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from ..const import DOMAIN
from .model import (
    DEFAULT_GRADUATION,
    OBJECTIVES,
    BalancingError,
    inventory,
    starting_point,
    step_of,
    validate_rooms,
)

SAVE_DELAY = 10


def _empty() -> dict[str, Any]:
    return {
        "rooms": [],
        "thermostat_room": None,
        "objective": None,
        "graduation": DEFAULT_GRADUATION,
        "readings": {},
        "validated_at": None,
    }


class Balancing:
    def __init__(self, hass: HomeAssistant, entry_id: str) -> None:
        self._store: Store[dict[str, Any]] = Store(hass, 1, f"{DOMAIN}.{entry_id}.balancing")
        self._data = _empty()

    async def async_load(self) -> None:
        if (data := await self._store.async_load()) is not None:
            self._data = {**_empty(), **data}

    async def async_save(self) -> None:
        await self._store.async_save(self._data)

    def _save(self) -> None:
        self._store.async_delay_save(lambda: self._data, SAVE_DELAY)

    @property
    def step(self) -> str:
        return step_of(self._data)

    @property
    def rooms(self) -> list[dict[str, Any]]:
        return self._data["rooms"]

    def _room_name(self, room_id: str) -> str:
        return next((r["name"] for r in self.rooms if r["id"] == room_id), room_id)

    def save_setup(self, rooms: list[dict[str, Any]], graduation: float) -> None:
        """Enregistre l'inventaire. Un changement de pièces ou de boucles annule le point de départ validé."""
        validate_rooms(rooms)
        if graduation <= 0:
            raise BalancingError("La graduation des débitmètres doit être positive.")
        d = self._data
        if inventory(rooms) != inventory(d["rooms"]) or graduation != d["graduation"]:
            d["validated_at"] = None
        labels = {loop["label"] for room in rooms for loop in room["loops"]}
        d["readings"] = {k: v for k, v in d["readings"].items() if k in labels}
        if d["thermostat_room"] not in {room["id"] for room in rooms}:
            d["thermostat_room"] = None
            d["validated_at"] = None
        d["rooms"] = rooms
        d["graduation"] = graduation
        self._save()

    def save_readings(self, readings: dict[str, float]) -> None:
        labels = {loop["label"] for room in self.rooms for loop in room["loops"]}
        if unknown := sorted(set(readings) - labels):
            raise BalancingError(f"Boucle inconnue : {', '.join(unknown)}.")
        if any(v < 0 for v in readings.values()):
            raise BalancingError("Un débit ne peut pas être négatif.")
        self._data["readings"] = dict(readings)
        self._data["validated_at"] = None
        self._save()

    def set_thermostat_room(self, room_id: str) -> None:
        if room_id not in {room["id"] for room in self.rooms}:
            raise BalancingError("Pièce inconnue.")
        if room_id != self._data["thermostat_room"]:
            self._data["validated_at"] = None
        self._data["thermostat_room"] = room_id
        self._save()

    def set_objective(self, objective: str) -> bool:
        """Vrai si l'objectif a changé : le point de départ est alors à revalider."""
        if objective not in OBJECTIVES:
            raise BalancingError("Objectif inconnu.")
        changed = objective != self._data["objective"]
        if changed:
            self._data["validated_at"] = None
        self._data["objective"] = objective
        self._save()
        return changed

    def start_point(self) -> list[dict[str, Any]] | None:
        """Débits conseillés, dès que le thermostat, l'objectif et tous les relevés sont connus."""
        d = self._data
        if d["thermostat_room"] is None or d["objective"] is None or not d["readings"]:
            return None
        try:
            return starting_point(d["rooms"], d["readings"], d["objective"], d["thermostat_room"], d["graduation"])
        except BalancingError:
            return None

    def validate_start_point(self) -> None:
        d = self._data
        if d["thermostat_room"] is None or d["objective"] is None:
            raise BalancingError("Choisissez d'abord la pièce du thermostat et l'objectif.")
        starting_point(d["rooms"], d["readings"], d["objective"], d["thermostat_room"], d["graduation"])
        d["validated_at"] = time.time()
        self._save()

    def status(self) -> dict[str, Any]:
        return {**self._data, "step": self.step, "start_point": self.start_point()}

    def diagnostics(self) -> dict[str, Any]:
        """Sans noms de pièces, de boucles ni de sondes : les diagnostics se partagent."""
        d = self._data
        return {
            "step": self.step,
            "objective": d["objective"],
            "thermostat_room_set": d["thermostat_room"] is not None,
            "start_point_validated": d["validated_at"] is not None,
            "graduation": d["graduation"],
            "rooms": [
                {
                    "target": room["target"],
                    "has_climate": room.get("climate") is not None,
                    "loops": len(room["loops"]),
                    "zones": [len(zone["sensors"]) for zone in room["zones"]],
                }
                for room in d["rooms"]
            ],
        }
