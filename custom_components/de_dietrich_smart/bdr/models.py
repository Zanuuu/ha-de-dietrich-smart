"""Modèle de données de la chaudière (porté de l'application ancienne application).

Un champ que le cloud ne fournit pas vaut None.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from typing import Any

# Modes normalisés d'une zone de chauffage.
ZONE_MODES = ("schedule", "override", "manual", "antifrost")
# Modes normalisés de l'eau chaude.
DHW_MODES = ("schedule", "comfort", "eco", "boost", "off")


class SourceError(Exception):
    """Erreur de la source, message lisible par l'utilisateur."""


class AuthError(SourceError):
    """Identifiants refusés ou jeton impossible à renouveler."""


@dataclass
class Zone:
    id: str
    name: str
    room_temp: float | None = None
    setpoint: float | None = None
    setpoint_min: float = 5.0
    setpoint_max: float = 30.0
    mode: str | None = None  # ZONE_MODES
    override_end: datetime | None = None
    schedule_setpoint: float | None = None
    next_setpoint: float | None = None
    next_switch: datetime | None = None
    program: int | None = None
    programs: list[int] = field(default_factory=lambda: [1, 2, 3])
    demand: str | None = None  # "heating" | "idle"
    fireplace: bool | None = None
    fireplace_capable: bool = False
    # Programmes horaires lus sur la source : numéro → changements (voir chaudiere/programs.py).
    week: dict[int, list[dict[str, Any]]] = field(default_factory=dict)
    # Activités du thermostat, si la source les donne : {n, name, setpoint, min, max, step}.
    activities: list[dict[str, Any]] = field(default_factory=list)
    # Courbe de chauffe, si la source la donne : {slope, base, max_flow, *_min, *_max, *_step}.
    heating_curve: dict[str, Any] | None = None
    preheat_enabled: bool | None = None
    preheat_active: bool | None = None


@dataclass
class Dhw:
    id: str
    name: str
    temp: float | None = None
    target: float | None = None
    comfort_setpoint: float | None = None
    reduced_setpoint: float | None = None
    comfort_min: float | None = None
    comfort_max: float | None = None
    reduced_min: float | None = None
    reduced_max: float | None = None
    mode: str | None = None  # DHW_MODES
    status: str | None = None  # "heating" | "idle"
    next_switch: datetime | None = None
    dhw_type: str | None = None
    program: int | None = None
    week: list[dict[str, Any]] = field(default_factory=list)
    boost_capable: bool = False
    boost_end: datetime | None = None


@dataclass
class Appliance:
    id: str
    name: str
    online: bool | None = None
    error_status: str | None = None
    in_error: bool = False
    pressure: float | None = None
    pressure_ok: bool | None = None
    outdoor_temp: float | None = None
    outdoor_source: str | None = None
    activity: str | None = None  # "heating" | "dhw" | "idle"
    operating_mode: str | None = None
    # Mesures que seule une source locale fournira.
    flow_temp: float | None = None
    return_temp: float | None = None
    flame: bool | None = None
    modulation: float | None = None
    gateway: dict[str, Any] = field(default_factory=dict)
    holiday_active: bool | None = None
    # Vacances : jours entiers, dates locales « AAAA-MM-JJ » (None = aucune période).
    holiday_start: str | None = None
    holiday_end: str | None = None
    seasonal_mode: str | None = None
    # Erreurs actives détaillées, si la source les donne : {code, text, raw}.
    active_errors: list[dict[str, Any]] | None = None
    zones: list[Zone] = field(default_factory=list)
    dhw: list[Dhw] = field(default_factory=list)


@dataclass
class State:
    ts: datetime
    source: str
    appliances: list[Appliance]

    @property
    def appliance(self) -> Appliance | None:
        return self.appliances[0] if self.appliances else None

    @property
    def zone(self) -> Zone | None:
        a = self.appliance
        return a.zones[0] if a and a.zones else None

    @property
    def dhw(self) -> Dhw | None:
        a = self.appliance
        return a.dhw[0] if a and a.dhw else None

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


@dataclass
class DayConsumption:
    day: date
    heating_kwh: float | None = None
    dhw_kwh: float | None = None
    heating_delivered_kwh: float | None = None
    dhw_delivered_kwh: float | None = None


@dataclass
class HourConsumption:
    """Énergie consommée pendant l'heure qui commence à ts (UTC)."""

    ts: datetime
    heating_kwh: float | None = None
    dhw_kwh: float | None = None


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_jsonable(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value
