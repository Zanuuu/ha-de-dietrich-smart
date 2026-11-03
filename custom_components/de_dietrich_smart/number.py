"""Réglages numériques : courbe de chauffe, consignes des activités, consignes d'eau chaude."""

from __future__ import annotations

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DeDietrichConfigEntry
from .entity import DeDietrichEntity

PARALLEL_UPDATES = 1


async def async_setup_entry(
    hass: HomeAssistant, entry: DeDietrichConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    data = entry.runtime_data.coordinator.data
    entities: list[NumberEntity] = []
    if data.zone:
        if data.zone.heating_curve:
            entities += [CurveNumber(entry, "slope"), CurveNumber(entry, "base")]
        entities += [ActivityNumber(entry, a["n"], a.get("name") or str(a["n"])) for a in data.zone.activities]
    if data.dhw:
        entities += [DhwNumber(entry, "comfort"), DhwNumber(entry, "reduced")]
    async_add_entities(entities)


class CurveNumber(DeDietrichEntity, NumberEntity):
    """Pente ou pied de la courbe de chauffe ; bornes et pas donnés par le cloud."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_mode = NumberMode.BOX

    def __init__(self, entry: DeDietrichConfigEntry, part: str) -> None:
        super().__init__(entry, "zone", f"heating_curve_{part}")
        self.part = part
        self._attr_translation_key = f"heating_curve_{part}"
        if part == "base":
            self._attr_device_class = NumberDeviceClass.TEMPERATURE
            self._attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS

    def _curve(self, key: str, default: float) -> float:
        c = (self.zone.heating_curve if self.zone else None) or {}
        v = c.get(f"{self.part}{key}")
        return float(v) if v is not None else default

    @property
    def available(self) -> bool:
        return super().available and bool(self.zone and self.zone.heating_curve)

    @property
    def native_value(self) -> float | None:
        c = self.zone.heating_curve if self.zone else None
        return c.get(self.part) if c else None

    @property
    def native_min_value(self) -> float:
        return self._curve("_min", 0.0 if self.part == "slope" else 15.0)

    @property
    def native_max_value(self) -> float:
        return self._curve("_max", 4.0 if self.part == "slope" else 80.0)

    @property
    def native_step(self) -> float:
        return self._curve("_step", 0.1 if self.part == "slope" else 0.5)

    async def async_set_native_value(self, value: float) -> None:
        commands = self.entry.runtime_data.commands
        if self.part == "slope":
            await self._command(commands.set_heating_curve(value, None))
        else:
            await self._command(commands.set_heating_curve(None, value))


class ActivityNumber(DeDietrichEntity, NumberEntity):
    """Consigne d'une activité du thermostat (Nuit, Maison, Absent, Matin, Soir)."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "activity_setpoint"
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_mode = NumberMode.BOX

    def __init__(self, entry: DeDietrichConfigEntry, n: int, name: str) -> None:
        super().__init__(entry, "zone", f"activity_{n}_setpoint")
        self.n = n
        self._attr_translation_placeholders = {"activity": name}

    def _act(self) -> dict | None:
        return next((a for a in (self.zone.activities if self.zone else []) if a["n"] == self.n), None)

    @property
    def available(self) -> bool:
        return super().available and self._act() is not None

    @property
    def native_value(self) -> float | None:
        a = self._act()
        return a.get("setpoint") if a else None

    @property
    def native_min_value(self) -> float:
        return float((self._act() or {}).get("min") or 5.0)

    @property
    def native_max_value(self) -> float:
        return float((self._act() or {}).get("max") or 30.0)

    @property
    def native_step(self) -> float:
        return float((self._act() or {}).get("step") or 0.5)

    async def async_set_native_value(self, value: float) -> None:
        await self._command(self.entry.runtime_data.commands.set_activity(self.n, value))


class DhwNumber(DeDietrichEntity, NumberEntity):
    """Consigne d'eau chaude confort ou réduite."""

    _attr_entity_category = EntityCategory.CONFIG
    _attr_device_class = NumberDeviceClass.TEMPERATURE
    _attr_native_unit_of_measurement = UnitOfTemperature.CELSIUS
    _attr_native_step = 0.5
    _attr_mode = NumberMode.BOX

    def __init__(self, entry: DeDietrichConfigEntry, kind: str) -> None:
        super().__init__(entry, "dhw", f"{kind}_setpoint")
        self.setpoint_kind = kind
        self._attr_translation_key = f"dhw_{kind}_setpoint"
        self._attr_entity_registry_enabled_default = kind == "comfort"

    @property
    def native_value(self) -> float | None:
        d = self.dhw
        if not d:
            return None
        return d.comfort_setpoint if self.setpoint_kind == "comfort" else d.reduced_setpoint

    @property
    def native_min_value(self) -> float:
        d = self.dhw
        v = (d.comfort_min if self.setpoint_kind == "comfort" else d.reduced_min) if d else None
        return v if v is not None else 10.0

    @property
    def native_max_value(self) -> float:
        d = self.dhw
        v = (d.comfort_max if self.setpoint_kind == "comfort" else d.reduced_max) if d else None
        return v if v is not None else 65.0

    async def async_set_native_value(self, value: float) -> None:
        await self._command(self.entry.runtime_data.commands.set_dhw_setpoint(self.setpoint_kind, value))
