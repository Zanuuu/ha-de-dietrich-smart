"""Capteurs : températures, pression, état, défaut, courbe de chauffe, part de chauffe."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfEnergy, UnitOfPressure, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.util import dt as dt_util

from . import DeDietrichConfigEntry
from .bdr.models import State
from .coordinator import DeDietrichCoordinator
from .curve import curve_flow
from .curve.diagnostic import ACTIONS
from .energy import today_kwh
from .entity import DeDietrichEntity, Kind
from .faults import describe

PARALLEL_UPDATES = 0


def _fault_code(s: State) -> str | None:
    a = s.appliance
    errors = (a.active_errors or []) if a else []
    return next((e["code"] for e in errors if e.get("code")), None)


def _curve_flow(s: State) -> float | None:
    a, z = s.appliance, s.zone
    if not (a and z and a.outdoor_temp is not None):
        return None
    flow = curve_flow(z.heating_curve, a.outdoor_temp)
    return round(flow, 1) if flow is not None else None


@dataclass(frozen=True, kw_only=True)
class DDSensorDescription(SensorEntityDescription):
    kind: Kind
    value_fn: Callable[[State], Any]
    # Valeur absente = entité indisponible (le cloud n'a pas transmis la mesure).
    unavailable_when_none: bool = False
    attrs_fn: Callable[[State], dict[str, Any]] | None = None
    coordinator_fn: Callable[[DeDietrichCoordinator], Any] | None = None


SENSORS: tuple[DDSensorDescription, ...] = (
    DDSensorDescription(
        key="room_temperature",
        translation_key="room_temperature",
        kind="zone",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda s: s.zone.room_temp if s.zone else None,
        unavailable_when_none=True,
    ),
    # Consigne en vigueur : capteur pour que l'enregistreur en garde les moyennes horaires (diagnostic).
    DDSensorDescription(
        key="target_temperature",
        translation_key="target_temperature",
        kind="zone",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda s: s.zone.setpoint if s.zone else None,
    ),
    DDSensorDescription(
        key="outdoor_temperature",
        translation_key="outdoor_temperature",
        kind="appliance",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        value_fn=lambda s: s.appliance.outdoor_temp if s.appliance else None,
        attrs_fn=lambda s: {"source": s.appliance.outdoor_source if s.appliance else None},
    ),
    DDSensorDescription(
        key="water_pressure",
        translation_key="water_pressure",
        kind="appliance",
        device_class=SensorDeviceClass.PRESSURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfPressure.BAR,
        suggested_display_precision=1,
        value_fn=lambda s: s.appliance.pressure if s.appliance else None,
        unavailable_when_none=True,
    ),
    DDSensorDescription(
        key="activity",
        translation_key="activity",
        kind="appliance",
        device_class=SensorDeviceClass.ENUM,
        options=["heating", "dhw", "idle"],
        value_fn=lambda s: s.appliance.activity if s.appliance else None,
    ),
    DDSensorDescription(
        key="error_status",
        translation_key="error_status",
        kind="appliance",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda s: s.appliance.error_status if s.appliance else None,
    ),
    DDSensorDescription(
        key="fault_code",
        translation_key="fault_code",
        kind="appliance",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_fault_code,
        attrs_fn=lambda s: (
            {"label": f.label, "advice": f.advice, "causes": list(f.causes)} if (f := describe(_fault_code(s))) else {}
        ),
    ),
    DDSensorDescription(
        key="override_end",
        translation_key="override_end",
        kind="zone",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s: s.zone.override_end if s.zone else None,
    ),
    DDSensorDescription(
        key="next_switch",
        translation_key="next_switch",
        kind="zone",
        device_class=SensorDeviceClass.TIMESTAMP,
        value_fn=lambda s: s.zone.next_switch if s.zone else None,
    ),
    DDSensorDescription(
        key="heating_curve_flow",
        translation_key="heating_curve_flow",
        kind="zone",
        device_class=SensorDeviceClass.TEMPERATURE,
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        entity_registry_enabled_default=False,
        value_fn=_curve_flow,
    ),
    DDSensorDescription(
        key="heating_ratio",
        translation_key="heating_ratio",
        kind="zone",
        state_class=SensorStateClass.MEASUREMENT,
        native_unit_of_measurement=PERCENTAGE,
        suggested_display_precision=0,
        value_fn=lambda s: None,
        coordinator_fn=lambda c: round(r * 100) if (r := c.heating_ratio) is not None else None,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: DeDietrichConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    data = entry.runtime_data.coordinator.data
    present = {"appliance": data.appliance, "zone": data.zone, "dhw": data.dhw}
    entities: list[SensorEntity] = [DDSensor(entry, d) for d in SENSORS if present[d.kind] is not None]
    if data.appliance:
        entities.append(GasTodaySensor(entry))
    if data.zone:
        entities.append(CurveRecommendationSensor(entry))
    async_add_entities(entities)


class DDSensor(DeDietrichEntity, SensorEntity):
    entity_description: DDSensorDescription

    def __init__(self, entry: DeDietrichConfigEntry, description: DDSensorDescription) -> None:
        super().__init__(entry, description.kind, description.key)
        self.entity_description = description

    def _value(self) -> Any:
        d = self.entity_description
        if d.coordinator_fn:
            return d.coordinator_fn(self.coordinator)
        return d.value_fn(self.coordinator.data) if self.coordinator.data else None

    @property
    def available(self) -> bool:
        if not super().available:
            return False
        return not (self.entity_description.unavailable_when_none and self._value() is None)

    @property
    def native_value(self) -> str | float | datetime | None:
        value: str | float | datetime | None = self._value()
        return value

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        fn = self.entity_description.attrs_fn
        return fn(self.coordinator.data) if fn and self.coordinator.data else None


class GasTodaySensor(DeDietrichEntity, SensorEntity):
    """Gaz consommé depuis minuit (chauffage + eau chaude), relu chaque heure.

    Pour le tableau Énergie, utiliser les statistiques externes « Gaz chauffage » et
    « Gaz eau chaude » : le cloud transmet les heures avec retard.
    """

    _attr_translation_key = "gas_today"
    _attr_device_class = SensorDeviceClass.ENERGY
    _attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
    _attr_state_class = SensorStateClass.TOTAL
    _attr_suggested_display_precision = 0

    def __init__(self, entry: DeDietrichConfigEntry) -> None:
        super().__init__(entry, "appliance", "gas_today")
        self.consumption = entry.runtime_data.consumption

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.consumption.async_add_listener(self.async_write_ha_state))

    @property
    def native_value(self) -> float | None:
        # Calculé à la lecture : après minuit, plus le total de la veille.
        data = self.consumption.data
        return today_kwh(data.hours, dt_util.utcnow(), self.hass.config.time_zone) if data else None

    @property
    def last_reset(self) -> datetime:
        return dt_util.start_of_local_day()


class CurveRecommendationSensor(DeDietrichEntity, SensorEntity):
    """Recommandation du diagnostic de la courbe, avec la courbe proposée, les raisons et la confiance."""

    _attr_translation_key = "curve_recommendation"
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = list(ACTIONS)

    def __init__(self, entry: DeDietrichConfigEntry) -> None:
        super().__init__(entry, "zone", "curve_recommendation")
        self.diagnostic = entry.runtime_data.diagnostic

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        self.async_on_remove(self.diagnostic.async_add_listener(self.async_write_ha_state))

    @property
    def available(self) -> bool:
        return super().available and self.diagnostic.last_update_success and self.diagnostic.data is not None

    @property
    def native_value(self) -> str | None:
        d = self.diagnostic.data
        return d.action if d else None

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        d = self.diagnostic.data
        if not d:
            return None
        return {"slope": d.slope, "base": d.base, "confidence": d.confidence, "reasons": d.reasons, "days": d.days}
