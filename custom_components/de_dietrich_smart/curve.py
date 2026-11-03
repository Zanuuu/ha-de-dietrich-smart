"""Courbe de chauffe : départ estimé selon la température extérieure.

Approximation : la notice De Dietrich ne donne qu'un graphique, et l'influence de la sonde
d'ambiance (CP240) n'est pas connue.
"""

from __future__ import annotations


def flow_temperature(slope: float, base: float, outdoor: float, max_flow: float | None = None) -> float:
    """Départ ≈ pied + pente × (20 − T ext), plafonné au départ maxi."""
    flow = base + slope * (20.0 - outdoor)
    return min(flow, max_flow) if max_flow is not None else flow
