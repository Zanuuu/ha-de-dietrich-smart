"""Consommation de gaz : statistiques externes pour le tableau Énergie."""

from datetime import UTC, date, datetime, timedelta

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.de_dietrich_smart.bdr.models import DayConsumption, HourConsumption
from custom_components.de_dietrich_smart.energy import build_statistics, today_kwh

NOW = datetime(2026, 11, 4, 12, 30, tzinfo=UTC)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """Le recorder doit être prêt avant hass : remplace le fixture de conftest."""


def hours_of(day: date, heating: float, dhw: float = 0.0, count: int = 24) -> list[HourConsumption]:
    t0 = datetime.combine(day, datetime.min.time(), UTC)
    return [HourConsumption(t0 + timedelta(hours=h), heating, dhw) for h in range(count)]


def test_hours_override_utc_day() -> None:
    days = [DayConsumption(date(2026, 11, 1), 30.0, 5.0), DayConsumption(date(2026, 11, 2), 99.0, 9.0)]
    hours = hours_of(date(2026, 11, 2), 1.0)
    stats = build_statistics(days, hours, "heating", NOW)
    # Le 01/11 : valeur du jour à minuit UTC ; le 02/11 : 24 heures (24 kWh, pas 99).
    assert stats[0]["start"] == datetime(2026, 11, 1, tzinfo=UTC) and stats[0]["state"] == 30.0
    assert len(stats) == 1 + 24
    assert stats[-1]["sum"] == 30.0 + 24.0


def test_partial_first_hour_day_keeps_daily_value() -> None:
    # Les heures commencent à 23:00 UTC (minuit local) : le jour UTC n'a qu'une heure.
    days = [DayConsumption(date(2026, 11, 1), 30.0, 0.0)]
    hours = [HourConsumption(datetime(2026, 11, 1, 23, tzinfo=UTC), 2.0, 0.0)]
    stats = build_statistics(days, hours, "heating", NOW)
    assert [s["state"] for s in stats] == [30.0]


def test_future_hours_ignored() -> None:
    hours = hours_of(date(2026, 11, 4), 1.0)
    stats = build_statistics([], hours, "heating", NOW)
    assert stats[-1]["start"] == datetime(2026, 11, 4, 12, tzinfo=UTC) and stats[-1]["sum"] == 13.0


def test_today_kwh() -> None:
    # Jour local Europe/Paris (UTC+1 en novembre) : de 23:00 UTC la veille à maintenant.
    hours = hours_of(date(2026, 11, 3), 1.0, 0.5) + hours_of(date(2026, 11, 4), 1.0, 0.5)
    assert today_kwh(hours, NOW, "Europe/Paris") == pytest.approx((1 + 13) * 1.5)


async def _sums(hass, statistic_id: str) -> list[float]:
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period, hass, datetime(2026, 1, 1, tzinfo=UTC), None, {statistic_id}, "hour", None, {"sum"}
    )
    return [r["sum"] for r in rows.get(statistic_id, [])]


async def test_reimport_is_idempotent(hass, config_entry, mock_client, freezer) -> None:
    freezer.move_to(NOW)
    days = [DayConsumption(date(2026, 11, 1), 30.0, 5.0)]
    hours = hours_of(date(2026, 11, 2), 1.0, 0.25)
    mock_client.fetch_consumption_days.return_value = days
    mock_client.fetch_consumption_hours.return_value = hours
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    consumption = config_entry.runtime_data.consumption
    await consumption.async_refresh()
    await async_wait_recording_done(hass)
    sid = "de_dietrich_smart:a1b2c3_gas_heating"
    first = await _sums(hass, sid)
    assert first[-1] == 54.0
    # Nouvel import identique, puis une heure de plus : jamais de double compte.
    mock_client.fetch_consumption_days.return_value = []
    mock_client.fetch_consumption_hours.return_value = [
        *hours,
        HourConsumption(datetime(2026, 11, 3, tzinfo=UTC), 2.0, 0),
    ]
    await consumption.async_refresh()
    await async_wait_recording_done(hass)
    assert (await _sums(hass, sid))[-1] == 56.0
    assert (await _sums(hass, "de_dietrich_smart:a1b2c3_gas_dhw"))[-1] == 5.0 + 6.0


async def test_gas_today_sensor(hass, config_entry, mock_client, freezer) -> None:
    await hass.config.async_set_time_zone("Europe/Paris")
    freezer.move_to(NOW)
    mock_client.fetch_consumption_hours.return_value = hours_of(date(2026, 11, 3), 1.0) + hours_of(
        date(2026, 11, 4), 1.0
    )
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    s = hass.states.get("sensor.maison_exemple_gas_today")
    assert s is not None and float(s.state) == 14.0
    assert s.attributes["unit_of_measurement"] == "kWh"
