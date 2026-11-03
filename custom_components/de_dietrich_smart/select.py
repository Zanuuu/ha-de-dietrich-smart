"""Programme horaire actif (1 à 3)."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DeDietrichConfigEntry
from .entity import DeDietrichEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: DeDietrichConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    if entry.runtime_data.coordinator.data.zone:
        async_add_entities([ProgramSelect(entry)])


class ProgramSelect(DeDietrichEntity, SelectEntity):
    _attr_translation_key = "program"

    def __init__(self, entry: DeDietrichConfigEntry) -> None:
        super().__init__(entry, "zone", "program")

    @property
    def options(self) -> list[str]:
        return [str(n) for n in (self.zone.programs if self.zone else [1, 2, 3])]

    @property
    def current_option(self) -> str | None:
        return str(self.zone.program) if self.zone and self.zone.program else None

    async def async_select_option(self, option: str) -> None:
        await self._command(self.entry.runtime_data.commands.set_program(int(option)))
