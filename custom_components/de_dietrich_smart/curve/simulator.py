"""Simulateur : départ prévu par la courbe actuelle et par la proposée (fonctions pures).

Il ne prédit pas la température des pièces : seulement ce que la chaudière demanderait au départ
pour les températures extérieures prévues (entité weather) ou relevées (rejeu des jours passés).
"""

from __future__ import annotations

import statistics
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.util import dt as dt_util

from . import flow_temperature


def _flow(curve: dict[str, Any] | None, outdoor: float, max_flow: float | None) -> float | None:
    if not curve or curve.get("slope") is None or curve.get("base") is None:
        return None
    return round(flow_temperature(curve["slope"], curve["base"], outdoor, max_flow), 1)


def simulate(
    points: list[tuple[datetime, float]], current: dict[str, Any], proposed: dict[str, Any] | None
) -> list[dict[str, Any]]:
    """Une ligne par point : extérieur, départ de la courbe actuelle et de la proposée (même plafond)."""
    max_flow = current.get("max_flow")
    return [
        {
            "ts": ts.isoformat(),
            "outdoor": t,
            "current": _flow(current, t, max_flow),
            "proposed": _flow(proposed, t, max_flow),
        }
        for ts, t in points
    ]


def daily_summary(rows: list[dict[str, Any]], time_zone: str) -> list[dict[str, Any]]:
    """Par jour local : extérieur mini et maxi, départ mini, moyen et maxi de chaque courbe."""
    tz = ZoneInfo(time_zone)
    by_day: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by_day.setdefault(datetime.fromisoformat(r["ts"]).astimezone(tz).date().isoformat(), []).append(r)
    out = []
    for day, rs in sorted(by_day.items()):
        d: dict[str, Any] = {
            "day": day,
            "outdoor_min": min(r["outdoor"] for r in rs),
            "outdoor_max": max(r["outdoor"] for r in rs),
        }
        for key in ("current", "proposed"):
            vals = [r[key] for r in rs if r[key] is not None]
            d[f"{key}_min"] = min(vals) if vals else None
            d[f"{key}_max"] = max(vals) if vals else None
            d[f"{key}_mean"] = round(statistics.fmean(vals), 1) if vals else None
        out.append(d)
    return out


def forecast_points(forecast: list[dict[str, Any]], daily: bool = False) -> list[tuple[datetime, float]]:
    """Points (instant, extérieur) d'une réponse de weather.get_forecasts ; en journalier, moyenne maxi/mini."""
    out = []
    for f in forecast:
        t, low = f.get("temperature"), f.get("templow")
        ts = dt_util.parse_datetime(str(f.get("datetime") or ""))
        if t is None or ts is None:
            continue
        out.append((dt_util.as_utc(ts), (t + low) / 2 if daily and low is not None else float(t)))
    return out
