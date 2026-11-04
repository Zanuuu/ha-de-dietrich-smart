"""Eau chaude sanitaire."""

from __future__ import annotations

from typing import Any

from homeassistant.components.water_heater import WaterHeaterEntity, WaterHeaterEntityFeature
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DeDietrichConfigEntry
from .entity import DeDietrichEntity

PARALLEL_UPDATES = 1
OPERATIONS = ["schedule", "comfort", "eco"]
# Le boost se déroule dans le programme ; l'arrêt complet n'est pas proposé (hors-gel = éco).
READ_AS = {"boost": "schedule", "off": "eco"}


async def async_setup_entry(
    hass: HomeAssistant, entry: DeDietrichConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    if entry.runtime_data.coordinator.data.dhw:
        async_add_entities([DhwWaterHeater(entry)])


class DhwWaterHeater(DeDietrichEntity, WaterHeaterEntity):
    _attr_name = None
    _attr_translation_key = "dhw"
    _attr_native_temperature_unit = UnitOfTemperature.CELSIUS
    # Anciens noms pour HA 2026.10 : à retirer quand la version minimale passera à 2026.11.
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_operation_list = OPERATIONS
    _attr_supported_features = WaterHeaterEntityFeature.TARGET_TEMPERATURE | WaterHeaterEntityFeature.OPERATION_MODE

    def __init__(self, entry: DeDietrichConfigEntry) -> None:
        super().__init__(entry, "dhw", "water_heater")

    @property
    def current_operation(self) -> str | None:
        mode = self.dhw.mode if self.dhw else None
        return READ_AS.get(mode or "", mode)

    @property
    def native_current_temperature(self) -> float | None:
        return self.dhw.temp if self.dhw else None

    @property
    def native_target_temperature(self) -> float | None:
        return self.dhw.comfort_setpoint if self.dhw else None

    current_temperature = native_current_temperature
    target_temperature = native_target_temperature

    @property
    def min_temp(self) -> float:
        return (self.dhw.comfort_min if self.dhw else None) or 40.0

    @property
    def max_temp(self) -> float:
        return (self.dhw.comfort_max if self.dhw else None) or 65.0

    async def async_set_temperature(self, **kwargs: Any) -> None:
        await self._command(self.entry.runtime_data.commands.set_dhw_setpoint("comfort", kwargs[ATTR_TEMPERATURE]))

    async def async_set_operation_mode(self, operation_mode: str) -> None:
        await self._command(self.entry.runtime_data.commands.set_dhw_mode(operation_mode))
