"""Boost d'eau chaude (consigne confort pendant 30 min environ)."""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DeDietrichConfigEntry
from .entity import DeDietrichEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: DeDietrichConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    d = entry.runtime_data.coordinator.data.dhw
    if d and d.boost_capable:
        async_add_entities([BoostButton(entry)])


class BoostButton(DeDietrichEntity, ButtonEntity):
    _attr_translation_key = "boost"

    def __init__(self, entry: DeDietrichConfigEntry) -> None:
        super().__init__(entry, "dhw", "boost")

    async def async_press(self) -> None:
        await self._command(self.entry.runtime_data.commands.dhw_boost())
