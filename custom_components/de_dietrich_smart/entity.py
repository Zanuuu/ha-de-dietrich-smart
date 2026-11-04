"""Entité de base : appareil (chaudière, zone, eau chaude) et exécution des commandes."""

from __future__ import annotations

from collections.abc import Awaitable
from typing import Literal

from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from . import DeDietrichConfigEntry
from .bdr.models import Appliance, Dhw, Zone
from .commands import CloudRefused, CommandError, CommandResult
from .const import DOMAIN
from .coordinator import DeDietrichCoordinator
from .devices import appliance_device_info

Kind = Literal["appliance", "zone", "dhw"]


class DeDietrichEntity(CoordinatorEntity[DeDietrichCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, entry: DeDietrichConfigEntry, kind: Kind, key: str) -> None:
        super().__init__(entry.runtime_data.coordinator)
        self.entry = entry
        self.kind = kind
        a = self.appliance
        assert a is not None
        obj = {"appliance": a, "zone": self.zone, "dhw": self.dhw}[kind]
        assert obj is not None
        self._attr_unique_id = f"{obj.id}_{key}"
        if kind == "appliance":
            self._attr_device_info = appliance_device_info(a)
        else:
            self._attr_device_info = DeviceInfo(
                identifiers={(DOMAIN, obj.id)},
                name=obj.name,
                manufacturer="De Dietrich",
                model="SMART TC°" if kind == "zone" else "Eau chaude sanitaire",
                via_device_id=entry.runtime_data.device_id,
            )

    @property
    def appliance(self) -> Appliance | None:
        return self.coordinator.data.appliance if self.coordinator.data else None

    @property
    def zone(self) -> Zone | None:
        return self.coordinator.data.zone if self.coordinator.data else None

    @property
    def dhw(self) -> Dhw | None:
        return self.coordinator.data.dhw if self.coordinator.data else None

    @property
    def available(self) -> bool:
        obj = {"appliance": self.appliance, "zone": self.zone, "dhw": self.dhw}[self.kind]
        return super().available and obj is not None

    async def _command(self, coro: Awaitable[CommandResult]) -> CommandResult:
        """Exécute une commande ; son échec devient une erreur traduite de Home Assistant."""
        try:
            return await coro
        except CloudRefused as exc:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"message": str(exc)},
            ) from exc
        except CommandError as exc:
            # Saisie refusée (bornes, garde-fou du plancher) : erreur de validation, comme les services.
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="command_failed",
                translation_placeholders={"message": str(exc)},
            ) from exc
