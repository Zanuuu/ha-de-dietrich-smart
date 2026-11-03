"""Mode cheminée du thermostat."""

from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DeDietrichConfigEntry
from .entity import DeDietrichEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: DeDietrichConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    z = entry.runtime_data.coordinator.data.zone
    if z and z.fireplace_capable:
        async_add_entities([FireplaceSwitch(entry)])


class FireplaceSwitch(DeDietrichEntity, SwitchEntity):
    _attr_translation_key = "fireplace"

    def __init__(self, entry: DeDietrichConfigEntry) -> None:
        super().__init__(entry, "zone", "fireplace")

    @property
    def is_on(self) -> bool | None:
        return self.zone.fireplace if self.zone else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._command(self.entry.runtime_data.commands.set_fireplace(True))

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._command(self.entry.runtime_data.commands.set_fireplace(False))
