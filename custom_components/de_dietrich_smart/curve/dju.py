"""Degrés-jours unifiés, jours locaux de consommation, comparaisons et CSV (port de consumption.py).

DJU « méthode météo » : max(0, base − (Tmin + Tmax) / 2), base 18 °C. Un jour local prend la somme
de ses heures s'il les a toutes (23 à 25 selon le changement d'heure) ; sinon la valeur du jour du
cloud, qui est un jour UTC (une heure de décalage, comme dans l'application).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..bdr.models import DayConsumption, HourConsumption

DJU_BASE = 18.0
# Saison de chauffe : du 1er septembre au 31 août.
SEASON_START_MONTH = 9


def dju(tmin: float, tmax: float, base: float = DJU_BASE) -> float:
    return round(max(0.0, base - (tmin + tmax) / 2), 1)


def _r(v: float | None, n: int = 2) -> float | None:
    return round(v, n) if v is not None else None


def _sum(rows: list[dict[str, Any]], key: str) -> float | None:
    vals = [r[key] for r in rows if r.get(key) is not None]
    return round(sum(vals), 1) if vals else None


def _shift_year(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # 29 février
        return d.replace(year=d.year + years, day=28)


def local_days(
    days: list[DayConsumption],
    hours: list[HourConsumption],
    temps: dict[date, tuple[float, float, str]],
    time_zone: str,
    start: date,
    end: date,
    price: float | None,
) -> list[dict[str, Any]]:
    """Une ligne par jour local de start à end : chauffage, eau chaude, total, coût, T ext, DJU."""
    tz = ZoneInfo(time_zone)
    by_day: dict[date, list[HourConsumption]] = {}
    for h in hours:
        by_day.setdefault(h.ts.astimezone(tz).date(), []).append(h)
    cloud = {d.day: d for d in days}
    out = []
    d = start
    while d <= end:
        t0 = datetime.combine(d, datetime.min.time(), tz).astimezone(UTC)
        t1 = datetime.combine(d + timedelta(days=1), datetime.min.time(), tz).astimezone(UTC)
        hs = by_day.get(d, [])
        heating: float | None
        dhw: float | None
        if hs and len(hs) >= (t1 - t0) // timedelta(hours=1):
            heating = sum(h.heating_kwh or 0.0 for h in hs)
            dhw = sum(h.dhw_kwh or 0.0 for h in hs)
        else:
            c = cloud.get(d)
            heating, dhw = (c.heating_kwh, c.dhw_kwh) if c else (None, None)
        total = None if heating is None and dhw is None else (heating or 0.0) + (dhw or 0.0)
        t = temps.get(d)
        out.append(
            {
                "day": d.isoformat(),
                "heating": _r(heating),
                "dhw": _r(dhw),
                "total": _r(total),
                "cost": _r(total * price) if total is not None and price else None,
                "tmin": t[0] if t else None,
                "tmax": t[1] if t else None,
                "outdoor": _r((t[0] + t[1]) / 2, 1) if t else None,
                "dju": dju(t[0], t[1]) if t else None,
                "source": t[2] if t else None,
            }
        )
        d += timedelta(days=1)
    return out


def rows_provider(
    days: list[DayConsumption],
    hours: list[HourConsumption],
    temps: dict[date, tuple[float, float, str]],
    time_zone: str,
    price: float | None,
) -> Callable[[date, date], list[dict[str, Any]]]:
    """Lignes journalières à la demande, pour summary() et les exports."""
    return lambda start, end: local_days(days, hours, temps, time_zone, start, end, price)


def compare(cur: list[dict[str, Any]], prev: list[dict[str, Any]], price: float | None) -> dict[str, Any]:
    """Totaux de la période et de la même période un an plus tôt, avec et sans correction
    climatique (le chauffage de l'an dernier ramené aux DJU de cette année)."""
    c_total, p_total = _sum(cur, "total"), _sum(prev, "total")
    c_dju, p_dju = _sum(cur, "dju"), _sum(prev, "dju")
    p_heat, p_dhw = _sum(prev, "heating"), _sum(prev, "dhw")
    corrected = None
    if p_heat is not None and c_dju and p_dju:
        corrected = round(p_heat * c_dju / p_dju + (p_dhw or 0), 1)

    def delta(a: float | None, b: float | None) -> int | None:
        return round((a - b) / b * 100) if a is not None and b else None

    return {
        "total": c_total,
        "cost": round(c_total * price, 2) if c_total is not None and price else None,
        "dju": c_dju,
        "last_year": p_total,
        "last_year_dju": p_dju,
        "last_year_corrected": corrected,
        "delta_pct": delta(c_total, p_total),
        "delta_corrected_pct": delta(c_total, corrected),
        "days": sum(1 for r in cur if r["total"] is not None),
        "days_last_year": sum(1 for r in prev if r["total"] is not None),
    }


def kwh_per_dju(rows: list[dict[str, Any]]) -> tuple[float | None, float | None, float | None]:
    """Chauffage par DJU sur les jours donnés, et les deux sommes (jours sans DJU exclus du rapport)."""
    heating = _sum(rows, "heating")
    with_dju = [r for r in rows if r.get("dju") is not None and r.get("heating") is not None]
    h, d = _sum(with_dju, "heating"), _sum(with_dju, "dju")
    return (round(h / d, 2) if h and d else None), heating, d


def summary(rows: Callable[[date, date], list[dict[str, Any]]], today: date, price: float | None) -> dict[str, Any]:
    """Veille, mois en cours et saison jusqu'à hier contre l'an dernier, kWh/DJU sur 30 jours."""
    yesterday = today - timedelta(days=1)
    y = rows(yesterday, yesterday)
    m_start = today.replace(day=1)
    cur = rows(m_start, yesterday) if yesterday >= m_start else []
    prev = rows(_shift_year(m_start, -1), _shift_year(yesterday, -1)) if cur else []
    s_year = today.year if today.month >= SEASON_START_MONTH else today.year - 1
    s_start = date(s_year, SEASON_START_MONTH, 1)
    s_cur = rows(s_start, yesterday) if yesterday >= s_start else []
    s_prev = rows(_shift_year(s_start, -1), _shift_year(yesterday, -1)) if s_cur else []
    ratio, heating_30, dju_30 = kwh_per_dju(rows(today - timedelta(days=30), yesterday))
    return {
        "yesterday": {k: y[0].get(k) for k in ("total", "heating", "dhw", "cost", "dju", "outdoor")} if y else None,
        "month": compare(cur, prev, price),
        "season": {**compare(s_cur, s_prev, price), "label": f"{s_year}-{s_year + 1}"},
        "kwh_per_dju_30d": ratio,
        "heating_30d": heating_30,
        "dju_30d": dju_30,
        "dju_base": int(DJU_BASE),
    }


def monthly(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Totaux par mois ; un mois sans aucun jour de consommation connu n'apparaît pas."""
    months: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        if r["total"] is not None:
            months.setdefault(r["day"][:7], []).append(r)
    return [
        {
            "month": m,
            "heating": _sum(rs, "heating"),
            "dhw": _sum(rs, "dhw"),
            "total": _sum(rs, "total"),
            "cost": _sum(rs, "cost"),
            "dju": _sum(rs, "dju"),
            "days": sum(1 for r in rs if r["total"] is not None),
        }
        for m, rs in sorted(months.items())
    ]


def _num(v: float | None) -> str:
    """Nombre au format français pour un tableur : virgule décimale, vide si absent."""
    return "" if v is None else f"{round(v, 3):g}".replace(".", ",")


def csv_days(rows: list[dict[str, Any]]) -> str:
    head = "Jour;Chauffage (kWh);Eau chaude (kWh);Total (kWh);Coût (€);T ext min (°C);T ext max (°C);DJU"
    keys = ("heating", "dhw", "total", "cost", "tmin", "tmax", "dju")
    return "\n".join([head, *(";".join([r["day"], *(_num(r.get(k)) for k in keys)]) for r in rows)]) + "\n"


def csv_hours(hours: list[HourConsumption], time_zone: str, price: float | None) -> str:
    tz = ZoneInfo(time_zone)
    lines = ["Heure (locale);Chauffage (kWh);Eau chaude (kWh);Total (kWh);Coût (€)"]
    for h in sorted(hours, key=lambda x: x.ts):
        total = None if h.heating_kwh is None and h.dhw_kwh is None else (h.heating_kwh or 0.0) + (h.dhw_kwh or 0.0)
        cost = total * price if total is not None and price else None
        when = h.ts.astimezone(tz).strftime("%Y-%m-%d %H:%M")
        lines.append(";".join([when, _num(h.heating_kwh), _num(h.dhw_kwh), _num(total), _num(cost)]))
    return "\n".join(lines) + "\n"
