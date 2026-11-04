"""DJU, jours locaux de consommation, comparaisons corrigées du climat et CSV (port de consumption.py)."""

from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from custom_components.de_dietrich_smart.bdr.models import DayConsumption, HourConsumption
from custom_components.de_dietrich_smart.curve.dju import (
    compare,
    csv_days,
    csv_hours,
    dju,
    kwh_per_dju,
    local_days,
    monthly,
    summary,
)

TZ = "Europe/Paris"


def local_hours(d: date, values: list[float], dhw: float = 0.0) -> list[HourConsumption]:
    """Heures du jour local d, à partir de minuit (heure de Paris)."""
    t0 = datetime.combine(d, datetime.min.time(), ZoneInfo(TZ)).astimezone(UTC)
    return [HourConsumption(t0 + timedelta(hours=i), v, dhw) for i, v in enumerate(values)]


def test_dju_weather_method():
    assert dju(2.0, 10.0) == 12.0
    assert dju(15.0, 25.0) == 0.0
    assert dju(2.0, 10.0, base=16) == 10.0


def test_full_local_day_of_hours_wins_over_cloud_day():
    d = date(2026, 11, 2)
    days = [DayConsumption(d, 9.0, 2.0), DayConsumption(d - timedelta(days=1), 12.0, 2.0)]
    hours = local_hours(d, [2.0] * 5 + [0.0] * 4 + [1.0] + [0.0] * 14, dhw=0.0)
    # Heures incomplètes la veille : le jour du cloud reste la référence.
    hours += local_hours(d - timedelta(days=1), [1.0] * 20)
    temps = {d: (2.0, 10.0, "open-meteo")}
    rows = local_days(days, hours, temps, TZ, d - timedelta(days=1), d, price=0.1)
    assert [r["day"] for r in rows] == ["2026-11-01", "2026-11-02"]
    assert (rows[1]["heating"], rows[1]["dhw"], rows[1]["total"]) == (11.0, 0.0, 11.0)
    assert rows[1]["cost"] == pytest.approx(1.1) and rows[1]["dju"] == 12.0 and rows[1]["outdoor"] == 6.0
    assert rows[0]["total"] == 14.0 and rows[0]["dju"] is None


def test_dst_days_have_23_or_25_hours():
    for d, n in ((date(2026, 10, 25), 25), (date(2026, 3, 29), 23)):
        (r,) = local_days([], local_hours(d, [1.0] * n), {}, TZ, d, d, None)
        assert r["heating"] == n and r["cost"] is None


def test_day_without_anything_is_empty_not_zero():
    (r,) = local_days([], [], {}, TZ, date(2026, 11, 2), date(2026, 11, 2), None)
    assert r["total"] is None and r["heating"] is None


def row(day: str, heating: float, dhw: float, dju_: float | None) -> dict:
    return {"day": day, "heating": heating, "dhw": dhw, "total": heating + dhw, "cost": None, "dju": dju_}


def test_compare_corrects_last_year_to_this_year_climate():
    cur = [row("2026-11-01", 100.0, 10.0, 200.0)]
    prev = [row("2025-11-01", 120.0, 10.0, 300.0)]
    c = compare(cur, prev, price=0.1)
    # Chauffage de l'an dernier ramené aux DJU de cette année : 120 × 200 / 300 + 10 = 90.
    assert c["last_year_corrected"] == 90.0
    assert (c["total"], c["last_year"], c["delta_pct"], c["delta_corrected_pct"]) == (110.0, 130.0, -15, 22)
    assert c["cost"] == 11.0 and c["days"] == 1 and c["days_last_year"] == 1


def test_compare_without_last_year():
    c = compare([row("2026-11-01", 100.0, 10.0, 200.0)], [], price=None)
    assert c["last_year"] is None and c["last_year_corrected"] is None and c["delta_pct"] is None


def test_compare_only_on_days_known_both_years():
    """Cas réel de la saison 2026-2027 : l'historique de l'an dernier ne couvre pas tout septembre."""
    cur = [row("2026-09-01", 0.0, 2.0, 50.0), row("2026-10-01", 100.0, 10.0, 200.0)]
    prev = [row("2025-10-01", 120.0, 10.0, 300.0)]
    c = compare(cur, prev, price=0.1)
    # Le total et le coût restent ceux de la période ; l'écart ne compte que le 1er octobre.
    assert (c["total"], c["cost"], c["dju"]) == (112.0, 11.2, 250.0)
    assert (c["compared_total"], c["compared_dju"], c["last_year"], c["last_year_dju"]) == (110.0, 200.0, 130.0, 300.0)
    assert (c["delta_pct"], c["last_year_corrected"], c["delta_corrected_pct"]) == (-15, 90.0, 22)
    assert (c["days"], c["days_last_year"], c["days_compared"]) == (2, 1, 1)


def test_compare_ignores_a_day_missing_this_year():
    empty = {**row("2026-10-02", 0.0, 0.0, 5.0), "heating": None, "dhw": None, "total": None}
    prev = [row("2025-10-01", 20.0, 1.0, 5.0), row("2025-10-02", 90.0, 1.0, 5.0)]
    c = compare([row("2026-10-01", 10.0, 1.0, 5.0), empty], prev, None)
    assert (c["compared_total"], c["last_year"], c["delta_pct"]) == (11.0, 21.0, -48)
    assert (c["days"], c["days_last_year"], c["days_compared"]) == (1, 2, 1)


def test_compare_correction_skips_days_without_dju_on_either_side():
    cur = [row("2026-10-01", 10.0, 0.0, 5.0), row("2026-10-02", 50.0, 0.0, None)]
    prev = [row("2025-10-01", 20.0, 0.0, 5.0), row("2025-10-02", 60.0, 0.0, 5.0)]
    c = compare(cur, prev, None)
    assert (c["compared_total"], c["last_year"], c["delta_pct"]) == (60.0, 80.0, -25)
    # À climat égal : seul le 1er octobre a ses DJU des deux côtés, 10 contre 20.
    assert (c["compared_dju"], c["last_year_dju"]) == (5.0, 5.0)
    assert (c["last_year_corrected"], c["delta_corrected_pct"]) == (20.0, -50)


def test_compare_29_february_has_no_match():
    cur = [row("2028-02-28", 10.0, 0.0, 5.0), row("2028-02-29", 99.0, 0.0, 5.0)]
    c = compare(cur, [row("2027-02-28", 20.0, 0.0, 5.0)], None)
    assert (c["compared_total"], c["last_year"], c["days_compared"], c["days"]) == (10.0, 20.0, 1, 2)


def test_kwh_per_dju():
    rows = [row(f"2026-10-{d:02d}", 30.0, 2.0, 10.0) for d in range(1, 31)]
    assert kwh_per_dju(rows) == (3.0, 900.0, 300.0)
    assert kwh_per_dju([row("2026-10-01", 30.0, 2.0, None)]) == (None, 30.0, None)


def test_summary_month_and_season():
    today = date(2026, 11, 4)
    rows = {}
    for back in range(0, 430):
        d = today - timedelta(days=back)
        rows[d] = row(d.isoformat(), 10.0, 1.0, 5.0)
    s = summary(lambda a, b: [rows[d] for d in sorted(rows) if a <= d <= b], today, price=None)
    assert s["yesterday"]["total"] == 11.0
    # Mois en cours jusqu'à hier (3 jours) contre les mêmes jours de l'an dernier.
    assert s["month"]["days"] == 3 and s["month"]["last_year"] == 33.0
    assert s["season"]["label"] == "2026-2027" and s["season"]["days"] == 64
    assert s["kwh_per_dju_30d"] == 2.0 and s["dju_base"] == 18


def test_monthly_series():
    today = date(2026, 11, 4)
    rows = [row((today - timedelta(days=b)).isoformat(), 10.0, 1.0, 5.0) for b in range(400, -1, -1)]
    months = monthly(rows)
    # Novembre : du 1er au 4 (aujourd'hui compris).
    assert months[-1]["month"] == "2026-11" and months[-1]["total"] == 44.0 and months[-1]["days"] == 4
    assert months[-1]["dju"] == 20.0 and months[0]["month"] == "2025-09" and len(months) == 15


def test_csv_days_french_format():
    rows = [{**row("2026-11-02", 11.0, 0.5, 12.0), "cost": 1.83, "tmin": 2.0, "tmax": 10.0}]
    text = csv_days(rows)
    lines = text.splitlines()
    assert lines[0] == "Jour;Chauffage (kWh);Eau chaude (kWh);Total (kWh);Coût (€);T ext min (°C);T ext max (°C);DJU"
    assert lines[1] == "2026-11-02;11;0,5;11,5;1,83;2;10;12"


def test_csv_hours_local_time():
    hours = local_hours(date(2026, 11, 2), [2.0, 1.5], dhw=0.5)
    lines = csv_hours(hours, TZ, price=0.1).splitlines()
    assert lines[0] == "Heure (locale);Chauffage (kWh);Eau chaude (kWh);Total (kWh);Coût (€)"
    assert lines[1] == "2026-11-02 00:00;2;0,5;2,5;0,25"
    assert lines[2] == "2026-11-02 01:00;1,5;0,5;2;0,2"
