"""Zone de chauffage (thermostat SMART TC°)."""

from __future__ import annotations

from typing import Any

from homeassistant.components.climate import ClimateEntity, ClimateEntityFeature, HVACAction, HVACMode
from homeassistant.const import ATTR_TEMPERATURE, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from . import DeDietrichConfigEntry
from .const import CONF_DEFAULT_DURATION, DEFAULT_OPTIONS
from .entity import DeDietrichEntity

PARALLEL_UPDATES = 1
PRESETS = ["schedule", "manual", "antifrost", "fireplace"]


async def async_setup_entry(
    hass: HomeAssistant, entry: DeDietrichConfigEntry, async_add_entities: AddConfigEntryEntitiesCallback
) -> None:
    if entry.runtime_data.coordinator.data.zone:
        async_add_entities([ZoneClimate(entry)])


class ZoneClimate(DeDietrichEntity, ClimateEntity):
    _attr_name = None
    _attr_translation_key = "zone"
    _attr_temperature_unit = UnitOfTemperature.CELSIUS
    _attr_target_temperature_step = 0.5
    _attr_hvac_modes = [HVACMode.HEAT, HVACMode.OFF]
    _attr_preset_modes = PRESETS
    _attr_supported_features = (
        ClimateEntityFeature.TARGET_TEMPERATURE
        | ClimateEntityFeature.PRESET_MODE
        | ClimateEntityFeature.TURN_ON
        | ClimateEntityFeature.TURN_OFF
    )

    def __init__(self, entry: DeDietrichConfigEntry) -> None:
        super().__init__(entry, "zone", "climate")

    @property
    def current_temperature(self) -> float | None:
        return self.zone.room_temp if self.zone else None

    @property
    def target_temperature(self) -> float | None:
        return self.zone.setpoint if self.zone else None

    @property
    def min_temp(self) -> float:
        return self.zone.setpoint_min if self.zone else 5.0

    @property
    def max_temp(self) -> float:
        return self.zone.setpoint_max if self.zone else 30.0

    @property
    def hvac_mode(self) -> HVACMode:
        return HVACMode.OFF if self.zone and self.zone.mode == "antifrost" else HVACMode.HEAT

    @property
    def hvac_action(self) -> HVACAction | None:
        if not self.zone:
            return None
        if self.zone.mode == "antifrost" and self.zone.demand != "heating":
            return HVACAction.OFF
        return HVACAction.HEATING if self.zone.demand == "heating" else HVACAction.IDLE

    @property
    def preset_mode(self) -> str | None:
        z = self.zone
        if not z:
            return None
        if z.fireplace:
            return "fireplace"
        # Une dérogation « jusqu'au prochain créneau » reste dans le programme.
        return {"schedule": "schedule", "override": "schedule", "manual": "manual", "antifrost": "antifrost"}.get(
            z.mode or ""
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        z = self.zone
        if not z:
            return {}
        return {
            "override_end": z.override_end,
            "next_switch": z.next_switch,
            "next_setpoint": z.next_setpoint,
            "program": z.program,
        }

    async def async_set_temperature(self, **kwargs: Any) -> None:
        duration = self.entry.options.get(CONF_DEFAULT_DURATION, DEFAULT_OPTIONS[CONF_DEFAULT_DURATION])
        await self._command(self.entry.runtime_data.commands.set_setpoint(kwargs[ATTR_TEMPERATURE], duration))

    async def async_set_hvac_mode(self, hvac_mode: HVACMode) -> None:
        mode = "antifrost" if hvac_mode == HVACMode.OFF else "schedule"
        await self._command(self.entry.runtime_data.commands.set_mode(mode))

    async def async_set_preset_mode(self, preset_mode: str) -> None:
        commands = self.entry.runtime_data.commands
        z = self.zone
        if preset_mode == "fireplace":
            await self._command(commands.set_fireplace(True))
            return
        if z and z.fireplace:
            await self._command(commands.set_fireplace(False))
        if preset_mode == "manual":
            setpoint = z.setpoint if z and z.setpoint is not None else 19.0
            await self._command(commands.set_setpoint(setpoint, "permanent"))
        else:
            await self._command(commands.set_mode(preset_mode))
