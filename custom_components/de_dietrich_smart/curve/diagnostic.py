"""Diagnostic de la courbe de chauffe sur les moyennes horaires de l'enregistreur (fonctions pures).

Les heures sont classées par température extérieure (douce, fraîche, froide). Dans chaque bande, on
mesure l'écart ambiance − consigne pendant les heures occupées, c'est-à-dire en consigne de confort :
aucun autre signal d'occupation n'est fiable. Si la courbe sort des bornes du plancher, une bascule
est proposée ; sinon, un petit pas au plus : la pente répond au froid, le pied au temps doux.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from ..const import CONF_BASE_TEMP, CONF_MAX_FLOOR_FLOW, DEFAULT_OPTIONS, FLOOR_BASE, FLOOR_SLOPE
from . import floor_violation, guard_active, switch_proposal

Action = Literal[
    "keep", "raise_base", "lower_base", "raise_slope", "lower_slope", "switch_to_floor", "insufficient_data"
]
ACTIONS: tuple[str, ...] = (
    "keep", "raise_base", "lower_base", "raise_slope", "lower_slope", "switch_to_floor", "insufficient_data"
)  # fmt: skip
Confidence = Literal["high", "medium", "low"]

WINDOW_DAYS = 14
MIN_DAYS = 3.0
HIGH_DAYS = 7.0
# Bandes de température extérieure (moyenne de l'heure).
MILD_ABOVE = 8.0
COLD_BELOW = 0.0
# Heure occupée : consigne à moins de 0,5 °C de la plus haute consigne de la fenêtre.
OCCUPIED_MARGIN = 0.5
# Une bande ne compte qu'avec 24 heures occupées ; la confiance est haute à partir de 48.
MIN_BAND_HOURS = 24
HIGH_BAND_HOURS = 48
DEFICIT = -0.5
EXCESS = 1.0
# Remontée : ambiance à 0,2 °C de la consigne de confort, suivie 12 h au plus.
RECOVERED_MARGIN = 0.2
RECOVERY_MAX_H = 12
# Cycles courts : chauffes de moins de 10 min, trop nombreuses au-delà de 6 par jour.
SHORT_RUN = timedelta(minutes=10)
SHORT_CYCLES_MAX = 6.0
STEP_SLOPE = 0.1
STEP_BASE_MILD = 2.0
STEP_BASE_FRESH = 1.0
STEP_BASE_DOWN = 1.0

BANDS = {"mild": "douce (> 8 °C)", "fresh": "fraîche (0 à 8 °C)", "cold": "froide (< 0 °C)"}


@dataclass(frozen=True)
class Hour:
    """Moyennes d'une heure : ambiance, consigne, extérieur (°C), part de chauffe (%)."""

    start: datetime
    room: float | None
    setpoint: float | None
    outdoor: float | None
    ratio: float | None


@dataclass
class Diagnosis:
    action: Action
    slope: float | None
    base: float | None
    reasons: list[str]
    confidence: Confidence
    days: float
    bands: dict[str, dict[str, Any]] = field(default_factory=dict)
    short_cycles_per_day: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _fmt(v: float, n: int = 1) -> str:
    return f"{round(v, n):g}".replace(".", ",").replace("-", "−")


def band_of(outdoor: float) -> str:
    if outdoor > MILD_ABOVE:
        return "mild"
    return "cold" if outdoor < COLD_BELOW else "fresh"


def count_short_cycles(changes: list[tuple[datetime, bool]], start: datetime, end: datetime) -> float | None:
    """Chauffes de moins de 10 min par jour, d'après les changements d'état du capteur « chauffe »."""
    if not changes:
        return None
    short, on_since = 0, None
    for ts, on in sorted(changes):
        if on and on_since is None:
            on_since = ts
        elif not on and on_since is not None:
            short += ts - on_since < SHORT_RUN
            on_since = None
    days = (end - start).total_seconds() / 86400
    return round(short / days, 1) if days > 0 else None


def _bands(hours: list[Hour]) -> dict[str, dict[str, Any]]:
    complete = [h for h in hours if h.room is not None and h.setpoint is not None and h.outdoor is not None]
    top = max((h.setpoint for h in complete if h.setpoint is not None), default=None)
    out: dict[str, dict[str, Any]] = {}
    for key, label in BANDS.items():
        out[key] = {"label": label, "hours": 0, "occupied": 0, "gap": None, "ratio": None, "recovery_h": None}
    if top is None:
        return {k: {**v, "counted": False} for k, v in out.items()}
    threshold = top - OCCUPIED_MARGIN
    gaps: dict[str, list[float]] = {k: [] for k in BANDS}
    ratios: dict[str, list[float]] = {k: [] for k in BANDS}
    recoveries: dict[str, list[int]] = {k: [] for k in BANDS}
    for h in complete:
        assert h.outdoor is not None and h.room is not None and h.setpoint is not None
        b = band_of(h.outdoor)
        out[b]["hours"] += 1
        if h.ratio is not None:
            ratios[b].append(h.ratio)
        if h.setpoint >= threshold:
            out[b]["occupied"] += 1
            gaps[b].append(h.room - h.setpoint)
    # Remontée : heures entre le passage en confort et l'ambiance atteinte.
    by_start = {h.start: h for h in complete}
    for h in complete:
        prev = by_start.get(h.start - timedelta(hours=1))
        if not (prev and prev.setpoint is not None and h.setpoint is not None and h.outdoor is not None):
            continue
        if not (prev.setpoint < threshold <= h.setpoint):
            continue
        for k in range(RECOVERY_MAX_H + 1):
            x = by_start.get(h.start + timedelta(hours=k))
            if not x or x.setpoint is None or x.setpoint < threshold or x.room is None:
                break
            if x.room >= x.setpoint - RECOVERED_MARGIN:
                recoveries[band_of(h.outdoor)].append(k)
                break
    for b in BANDS:
        out[b]["gap"] = round(statistics.fmean(gaps[b]), 2) if gaps[b] else None
        out[b]["ratio"] = round(statistics.fmean(ratios[b]), 1) if ratios[b] else None
        out[b]["recovery_h"] = statistics.median(recoveries[b]) if recoveries[b] else None
        out[b]["counted"] = out[b]["occupied"] >= MIN_BAND_HOURS
    return out


def _down(v: float, step: float) -> float:
    return round(math.floor(v / step + 1e-9) * step, 2)


def _limits(curve: dict[str, Any], opts: Mapping[str, Any]) -> tuple[float, float, float, float]:
    """Bornes de la pente et du pied : celles du cloud, resserrées par le plancher si le garde-fou est actif."""
    s_lo, s_hi = curve.get("slope_min") or 0.0, curve.get("slope_max") or 4.0
    b_lo, b_hi = curve.get("base_min") or 15.0, curve.get("base_max") or 80.0
    if guard_active(opts):
        s_lo, s_hi = max(s_lo, FLOOR_SLOPE[0]), min(s_hi, FLOOR_SLOPE[1])
        b_lo, b_hi = max(b_lo, FLOOR_BASE[0]), min(b_hi, FLOOR_BASE[1])
    return s_lo, s_hi, b_lo, b_hi


def _step(
    curve: dict[str, Any], opts: Mapping[str, Any], d_slope: float, d_base: float
) -> tuple[float, float, str | None]:
    """Applique un pas, borné ; rend la nouvelle courbe et, si elle a été ramenée, pourquoi."""
    slope, base = curve["slope"], curve["base"]
    s_lo, s_hi, b_lo, b_hi = _limits(curve, opts)
    new_s = round(min(max(slope + d_slope, s_lo), s_hi), 2)
    new_b = round(min(max(base + d_base, b_lo), b_hi), 1)
    note = None
    if (new_s, new_b) != (round(slope + d_slope, 2), round(base + d_base, 1)):
        note = "bornes de la courbe"
    if guard_active(opts):
        o = {**DEFAULT_OPTIONS, **opts}
        span = 20.0 - float(o[CONF_BASE_TEMP])
        limit = float(o[CONF_MAX_FLOOR_FLOW])
        if new_b + new_s * span > limit:
            note = "départ maxi du plancher"
            if d_base:
                new_b = max(min(new_b, _down(limit - new_s * span, curve.get("base_step") or 0.5)), base)
            else:
                new_s = max(min(new_s, _down((limit - new_b) / span, curve.get("slope_step") or 0.1)), slope)
    return new_s, new_b, note


def diagnose(
    hours: list[Hour], short_cycles_per_day: float | None, curve: dict[str, Any] | None, opts: Mapping[str, Any]
) -> Diagnosis:
    complete = [h for h in hours if h.room is not None and h.setpoint is not None and h.outdoor is not None]
    days = round(len(complete) / 24, 1)
    bands = _bands(hours)
    if not curve or curve.get("slope") is None or curve.get("base") is None:
        return Diagnosis("insufficient_data", None, None, ["Courbe de chauffe pas encore lue."], "low", days, bands)
    slope, base = curve["slope"], curve["base"]
    if reason := floor_violation(slope, base, curve.get("max_flow"), opts):
        s, b = switch_proposal(curve, opts)
        reasons = [
            f"Courbe hors des bornes du plancher : {reason}.",
            "Bascule proposée une seule fois : même départ qu'aujourd'hui vers 5 °C dehors, puis de petits pas.",
        ]
        return Diagnosis("switch_to_floor", s, b, reasons, "high", days, bands, short_cycles_per_day)
    if days < MIN_DAYS:
        reasons = [f"Seulement {_fmt(days)} jours de mesures : il en faut au moins {_fmt(MIN_DAYS)}."]
        return Diagnosis("insufficient_data", None, None, reasons, "low", days, bands, short_cycles_per_day)

    def gap(b: str) -> float | None:
        return bands[b]["gap"] if bands[b]["counted"] else None

    def says(b: str, what: str) -> str:
        g = bands[b]
        mean = _fmt(abs(g["gap"]))
        return f"Bande {g['label']} : {mean} °C {what} la consigne en moyenne sur {g['occupied']} h occupées."

    decision: tuple[Action, float, float, str, str | None] | None = None
    for band, check, action, d_slope, d_base, what in (
        ("cold", lambda g: g < DEFICIT, "raise_slope", STEP_SLOPE, 0.0, "sous"),
        ("mild", lambda g: g < DEFICIT, "raise_base", 0.0, STEP_BASE_MILD, "sous"),
        ("fresh", lambda g: g < DEFICIT, "raise_base", 0.0, STEP_BASE_FRESH, "sous"),
        ("cold", lambda g: g > EXCESS, "lower_slope", -STEP_SLOPE, 0.0, "au-dessus de"),
        ("mild", lambda g: g > EXCESS, "lower_base", 0.0, -STEP_BASE_DOWN, "au-dessus de"),
        ("fresh", lambda g: g > EXCESS, "lower_base", 0.0, -STEP_BASE_DOWN, "au-dessus de"),
    ):
        g = gap(band)
        if g is not None and check(g):
            decision = (action, d_slope, d_base, says(band, what), band)  # type: ignore[assignment]
            break
    if decision is None and short_cycles_per_day is not None and short_cycles_per_day > SHORT_CYCLES_MAX:
        reason = f"{_fmt(short_cycles_per_day)} cycles courts (moins de 10 min) par jour : départ trop chaud."
        decision = ("lower_base", 0.0, -STEP_BASE_DOWN, reason, None)
    if decision is None:
        reasons = ["Écarts à la consigne dans les tolérances pour chaque bande mesurée."]
        empty = [BANDS[b] for b in BANDS if not bands[b]["counted"]]
        if empty:
            reasons.append(f"Pas assez de données en bande {', '.join(empty)} : rien à en conclure.")
        conf: Confidence = "high" if days >= HIGH_DAYS else "medium"
        return Diagnosis("keep", slope, base, reasons, conf, days, bands, short_cycles_per_day)
    action, d_slope, d_base, reason, decisive = decision
    new_s, new_b, note = _step(curve, opts, d_slope, d_base)
    occupied = bands[decisive]["occupied"] if decisive else HIGH_BAND_HOURS
    if days >= HIGH_DAYS and occupied >= HIGH_BAND_HOURS:
        confidence: Confidence = "high"
    elif occupied >= HIGH_BAND_HOURS:
        confidence = "medium"
    else:
        confidence = "low"
    if (new_s, new_b) == (slope, base):
        reasons = [reason, f"Déjà en butée ({note}) : réglage conservé."]
        return Diagnosis("keep", slope, base, reasons, confidence, days, bands, short_cycles_per_day)
    reasons = [reason] + ([f"Proposition ramenée par le {note}."] if note else [])
    return Diagnosis(action, new_s, new_b, reasons, confidence, days, bands, short_cycles_per_day)
