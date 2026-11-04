"""Courbe de chauffe : départ estimé selon la température extérieure, bornes du plancher.

Approximation : la notice De Dietrich ne donne qu'un graphique, et l'influence de la sonde
d'ambiance (CP240) n'est pas connue.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from ..const import (
    CONF_BASE_TEMP,
    CONF_CONNECTION,
    CONF_EMITTER,
    CONF_MAX_FLOOR_FLOW,
    DEFAULT_OPTIONS,
    FLOOR_BASE,
    FLOOR_SLOPE,
)

# Point de la bascule : la nouvelle courbe donne le même départ qu'aujourd'hui à 5 °C dehors.
SWITCH_PIVOT = 5.0


def flow_temperature(slope: float, base: float, outdoor: float, max_flow: float | None = None) -> float:
    """Départ ≈ pied + pente × (20 − T ext), plafonné au départ maxi."""
    flow = base + slope * (20.0 - outdoor)
    return min(flow, max_flow) if max_flow is not None else flow


def curve_flow(curve: dict[str, Any] | None, outdoor: float) -> float | None:
    """Départ estimé pour la courbe lue sur le cloud ; None si la pente ou le pied manque."""
    if not curve or curve.get("slope") is None or curve.get("base") is None:
        return None
    return flow_temperature(curve["slope"], curve["base"], outdoor, curve.get("max_flow"))


def guard_active(opts: Mapping[str, Any]) -> bool:
    """Garde-fou du plancher : plancher chauffant raccordé en direct (sans vanne mélangeuse)."""
    o = {**DEFAULT_OPTIONS, **opts}
    return o[CONF_EMITTER] == "floor" and o[CONF_CONNECTION] == "direct"


def _num(v: float) -> str:
    return f"{round(v, 2):g}".replace(".", ",").replace("-", "−")


def _excesses(slope: float, base: float, max_flow: float | None, opts: Mapping[str, Any]) -> list[tuple[float, str]]:
    """Dépassements (en °C de départ à la température de base) et leur raison."""
    if not guard_active(opts):
        return []
    o = {**DEFAULT_OPTIONS, **opts}
    base_temp, limit = float(o[CONF_BASE_TEMP]), float(o[CONF_MAX_FLOOR_FLOW])
    out: list[tuple[float, str]] = []
    lo, hi = FLOOR_SLOPE
    if not lo <= slope <= hi:
        gap = (lo - slope if slope < lo else slope - hi) * (20.0 - base_temp)
        out.append((gap, f"pente {_num(slope)} hors des bornes du plancher ({_num(lo)} à {_num(hi)})"))
    lo, hi = FLOOR_BASE
    if not lo <= base <= hi:
        gap = lo - base if base < lo else base - hi
        out.append((gap, f"pied {_num(base)} °C hors des bornes du plancher ({_num(lo)} à {_num(hi)} °C)"))
    flow = flow_temperature(slope, base, base_temp, max_flow)
    if flow > limit:
        out.append((flow - limit, f"départ de {_num(flow)} °C à {_num(base_temp)} °C, au-delà de {_num(limit)} °C"))
    return out


def floor_violation(slope: float, base: float, max_flow: float | None, opts: Mapping[str, Any]) -> str | None:
    """Raison lisible si la courbe est dangereuse pour le plancher, sinon None."""
    reasons = [r for _, r in _excesses(slope, base, max_flow, opts)]
    return " ; ".join(reasons) if reasons else None


def floor_excess(slope: float, base: float, max_flow: float | None, opts: Mapping[str, Any]) -> float:
    """Dépassement total (0 si sûre) : un réglage qui le réduit rapproche de la sécurité."""
    return sum(g for g, _ in _excesses(slope, base, max_flow, opts))


def _down(v: float, step: float) -> float:
    return round(math.floor(v / step + 1e-9) * step, 2)


def switch_proposal(curve: dict[str, Any], opts: Mapping[str, Any]) -> tuple[float, float]:
    """Bascule vers une courbe de plancher, au même départ qu'aujourd'hui à 5 °C si possible."""
    o = {**DEFAULT_OPTIONS, **opts}
    base_temp, limit = float(o[CONF_BASE_TEMP]), float(o[CONF_MAX_FLOOR_FLOW])
    slope_step, base_step = curve.get("slope_step") or 0.1, curve.get("base_step") or 0.5
    pivot = curve_flow(curve, SWITCH_PIVOT)
    assert pivot is not None
    # Même départ au pivot et départ maxi à la température de base : pente au plus (limite − pivot) / (5 − T base).
    room = (limit - pivot) / (SWITCH_PIVOT - base_temp)
    slope = _down(min(max(min(curve["slope"], room), FLOOR_SLOPE[0]), FLOOR_SLOPE[1]), slope_step)
    slope = max(slope, FLOOR_SLOPE[0])
    base = round(min(max(pivot - slope * (20.0 - SWITCH_PIVOT), FLOOR_BASE[0]), FLOOR_BASE[1]) / base_step) * base_step
    if flow_temperature(slope, base, base_temp) > limit:
        base = _down(limit - slope * (20.0 - base_temp), base_step)
    return slope, round(base, 1)
