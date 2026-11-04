"""Courbe de chauffe : départ estimé selon la température extérieure.

Approximation : la notice De Dietrich ne donne qu'un graphique, et l'influence de la sonde
d'ambiance (CP240) n'est pas connue.
"""

from __future__ import annotations

from typing import Any


def flow_temperature(slope: float, base: float, outdoor: float, max_flow: float | None = None) -> float:
    """Départ ≈ pied + pente × (20 − T ext), plafonné au départ maxi."""
    flow = base + slope * (20.0 - outdoor)
    return min(flow, max_flow) if max_flow is not None else flow


def curve_flow(curve: dict[str, Any] | None, outdoor: float) -> float | None:
    """Départ estimé pour la courbe lue sur le cloud ; None si la pente ou le pied manque."""
    if not curve or curve.get("slope") is None or curve.get("base") is None:
        return None
    return flow_temperature(curve["slope"], curve["base"], outdoor, curve.get("max_flow"))
