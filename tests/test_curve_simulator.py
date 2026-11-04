"""Simulateur : départ prévu pour la courbe actuelle et la proposée, par heure et par jour."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.de_dietrich_smart.curve.simulator import daily_summary, forecast_points, simulate

CUR = {"slope": 0.5, "base": 22.0, "max_flow": 80.0}
T0 = datetime(2026, 11, 5, tzinfo=UTC)


def test_simulate_current_and_proposed():
    rows = simulate([(T0, 5.0), (T0 + timedelta(hours=1), -15.0)], CUR, {"slope": 0.6, "base": 22.0})
    assert rows[0] == {"ts": T0.isoformat(), "outdoor": 5.0, "current": 29.5, "proposed": 31.0}
    assert rows[1]["current"] == 39.5 and rows[1]["proposed"] == 43.0


def test_simulate_capped_by_boiler_max_flow():
    rows = simulate([(T0, -20.0)], {**CUR, "slope": 1.5, "base": 30.0, "max_flow": 50.0}, None)
    assert rows[0]["current"] == 50.0 and rows[0]["proposed"] is None


def test_daily_summary_local_days():
    # Europe/Paris (UTC+1) : 23:00 UTC appartient au jour suivant.
    pts = [(T0 + timedelta(hours=h), t) for h, t in ((10, 8.0), (14, 12.0), (23, 2.0))]
    days = daily_summary(simulate(pts, CUR, None), "Europe/Paris")
    assert [d["day"] for d in days] == ["2026-11-05", "2026-11-06"]
    assert days[0]["outdoor_min"] == 8.0 and days[0]["outdoor_max"] == 12.0
    assert days[0]["current_max"] == 28.0 and days[0]["current_mean"] == pytest.approx(27.0)
    assert days[1]["proposed_max"] is None


def test_forecast_points_hourly_and_daily():
    hourly = [{"datetime": "2026-11-05T10:00:00+00:00", "temperature": 7.5}, {"datetime": "2026-11-05T11:00:00+00:00"}]
    assert forecast_points(hourly) == [(datetime(2026, 11, 5, 10, tzinfo=UTC), 7.5)]
    # Prévision journalière : moyenne du maxi et du mini, à midi.
    daily = [{"datetime": "2026-11-06T11:00:00+00:00", "temperature": 10.0, "templow": 2.0}]
    assert forecast_points(daily, daily=True) == [(datetime(2026, 11, 6, 11, tzinfo=UTC), 6.0)]
