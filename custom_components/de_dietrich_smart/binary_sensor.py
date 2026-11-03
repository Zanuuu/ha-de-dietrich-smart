"""Capteurs binaires : connexion, défaut, pression basse, chauffe, eau chaude, vacances."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DeDietrichConfigEntry
from .bdr.models import State
from .entity import DeDietrichEntity, Kind
from .events import low_pressure

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class DDBinaryDescription(BinarySensorEntityDescription):
    kind: Kind
    value_fn: Callable[[State], bool | None]


BINARY_SENSORS: tuple[DDBinaryDescription, ...] = (
    DDBinaryDescription(
        key="online",
        translation_key="online",
        kind="appliance",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.appliance.online if s.appliance else None,
    ),
    DDBinaryDescription(
        key="problem",
        translation_key="problem",
        kind="appliance",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda s: s.appliance.in_error if s.appliance else None,
    ),
    DDBinaryDescription(
        key="low_pressure",
        translation_key="low_pressure",
        kind="appliance",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda s: low_pressure(s.appliance.pressure, s.appliance.pressure_ok) if s.appliance else None,
    ),
    DDBinaryDescription(
        key="holiday",
        translation_key="holiday",
        kind="appliance",
        value_fn=lambda s: s.appliance.holiday_active if s.appliance else None,
    ),
    DDBinaryDescription(
        key="heating",
        translation_key="heating",
        kind="zone",
        device_class=BinarySensorDeviceClass.HEAT,
        value_fn=lambda s: (s.zone.demand == "heating") if s.zone and s.zone.demand else None,
    ),
    DDBinaryDescription(
        key="preheat",
        translation_key="preheat",
        kind="zone",
        entity_registry_enabled_default=False,
        value_fn=lambda s: s.zone.preheat_active if s.zone else None,
    ),
    DDBinaryDescription(
        key="dhw_heating",
        translation_key="dhw_heating",
        kind="dhw",
        device_class=BinarySensorDeviceClass.HEAT,
        value_fn=lambda s: (s.dhw.status == "heating") if s.dhw and s.dhw.status else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: DeDietrichConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    data = entry.runtime_data.coordinator.data
    present = {"appliance": data.appliance, "zone": data.zone, "dhw": data.dhw}
    async_add_entities(DDBinarySensor(entry, d) for d in BINARY_SENSORS if present[d.kind] is not None)


class DDBinarySensor(DeDietrichEntity, BinarySensorEntity):
    entity_description: DDBinaryDescription

    def __init__(self, entry: DeDietrichConfigEntry, description: DDBinaryDescription) -> None:
        super().__init__(entry, description.kind, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        return self.entity_description.value_fn(self.coordinator.data) if self.coordinator.data else None
