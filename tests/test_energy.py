"""Consommation de gaz : statistiques externes pour le tableau Énergie."""

from datetime import UTC, date, datetime, timedelta

import pytest
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import statistics_during_period
from pytest_homeassistant_custom_component.common import MockConfigEntry, async_fire_time_changed
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.de_dietrich_smart.bdr.models import DayConsumption, HourConsumption
from custom_components.de_dietrich_smart.energy import build_cost_statistics, build_statistics, today_kwh

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
    # Le 01/11 : valeur du jour à minuit UTC (24 heures écrites) ; le 02/11 : 24 heures (24 kWh, pas 99).
    assert stats[0]["start"] == datetime(2026, 11, 1, tzinfo=UTC) and stats[0]["state"] == 30.0
    assert len(stats) == 24 + 24
    assert stats[-1]["sum"] == 30.0 + 24.0


def test_partial_first_hour_day_keeps_daily_value() -> None:
    # Les heures commencent à 23:00 UTC (minuit local) : le jour UTC n'a qu'une heure.
    days = [DayConsumption(date(2026, 11, 1), 30.0, 0.0)]
    hours = [HourConsumption(datetime(2026, 11, 1, 23, tzinfo=UTC), 2.0, 0.0)]
    stats = build_statistics(days, hours, "heating", NOW)
    assert [s["state"] for s in stats] == [30.0] + [0.0] * 23


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


def test_daily_value_rewrites_whole_day() -> None:
    """Jour UTC passé des heures à la valeur du jour : ses 24 heures sont réécrites (sommes monotones)."""
    day = date(2026, 11, 2)
    days = [DayConsumption(day, 30.0, 0.0)]
    hours = hours_of(day, 1.0, count=23)
    stats = build_statistics(days, hours, "heating", NOW)
    assert len(stats) == 24
    assert stats[0]["state"] == 30.0 and all(s["state"] == 0.0 for s in stats[1:])
    sums = [s["sum"] for s in stats]
    assert sums == sorted(sums) and sums[-1] == 30.0


async def test_stored_hours_survive_restart(hass, hass_storage, config_entry, mock_client, freezer) -> None:
    freezer.move_to(NOW)
    old = datetime(2026, 10, 10, 8, tzinfo=UTC)
    hass_storage[f"de_dietrich_smart.{config_entry.entry_id}.consumption"] = {
        "version": 1,
        "minor_version": 1,
        "key": f"de_dietrich_smart.{config_entry.entry_id}.consumption",
        "data": {"days": {"2026-10-10": [40.0, 4.0]}, "hours": {old.isoformat(): [3.0, 0.5]}},
    }
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    data = config_entry.runtime_data.consumption.data
    assert [h.ts for h in data.hours] == [old]
    assert [d.day for d in data.days] == [date(2026, 10, 10)]


async def test_consumption_polls_without_sensor(hass, config_entry, mock_client, freezer) -> None:
    """L'import horaire continue même si le capteur « gaz du jour » est désactivé."""
    from homeassistant.helpers import entity_registry as er

    er.async_get(hass).async_get_or_create(
        "sensor",
        "de_dietrich_smart",
        "a1b2c3_gas_today",
        config_entry=config_entry,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    freezer.move_to(NOW)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    calls = mock_client.fetch_consumption_days.await_count
    freezer.tick(timedelta(hours=1, minutes=1))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert mock_client.fetch_consumption_days.await_count == calls + 1


async def test_consumption_auth_error_starts_reauth(hass, config_entry, mock_client, freezer) -> None:
    from custom_components.de_dietrich_smart.bdr.models import AuthError

    freezer.move_to(NOW)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    mock_client.fetch_consumption_days.side_effect = AuthError("refusé")
    for _ in range(3):
        freezer.tick(timedelta(hours=1, minutes=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert [f["context"]["source"] for f in hass.config_entries.flow.async_progress()] == ["reauth"]
    assert mock_client.fetch_consumption_days.await_count == 2


async def test_gas_today_resets_at_midnight(hass, config_entry, mock_client, freezer) -> None:
    await hass.config.async_set_time_zone("Europe/Paris")
    freezer.move_to(NOW)
    # Heures du 04/11 local seulement (la 24e heure UTC, 23:00 Z, est déjà le 05/11 à Paris).
    mock_client.fetch_consumption_hours.return_value = hours_of(date(2026, 11, 4), 1.0, count=23)
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    eid = "sensor.maison_exemple_gas_today"
    assert hass.states.get(eid).attributes["last_reset"] == "2026-11-04T00:00:00+01:00"
    # 00:30 le lendemain, avant la relève horaire : plus le total de la veille.
    freezer.move_to(datetime(2026, 11, 4, 23, 30, tzinfo=UTC))
    config_entry.runtime_data.consumption.async_update_listeners()
    await hass.async_block_till_done()
    s = hass.states.get(eid)
    assert s.state == "unknown" and s.attributes["last_reset"] == "2026-11-05T00:00:00+01:00"


async def test_statistic_id_is_a_slug(hass, config_entry, mock_client, state, freezer) -> None:
    # Identifiant réel du cloud : majuscules et tirets, refusés par le recorder dans un statistic_id.
    freezer.move_to(NOW)
    state.appliances[0].id = "A1B2-C3d4"
    mock_client.fetch_consumption_days.return_value = [DayConsumption(date(2026, 11, 1), 30.0, 5.0)]
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)
    assert (await _sums(hass, "de_dietrich_smart:a1b2_c3d4_gas_heating"))[-1] == 30.0


def test_cost_statistics_follow_energy() -> None:
    stats = build_statistics(
        [DayConsumption(date(2026, 11, 1), 30.0, 5.0)], hours_of(date(2026, 11, 2), 1.0), "heating", NOW
    )
    cost = build_cost_statistics(stats, 0.15)
    assert [c["start"] for c in cost] == [s["start"] for s in stats]
    assert cost[0]["state"] == pytest.approx(30.0 * 0.15)
    assert cost[-1]["sum"] == pytest.approx(54.0 * 0.15)


async def test_cost_statistics_imported_with_price(hass, mock_client, freezer) -> None:
    freezer.move_to(NOW)
    entry = MockConfigEntry(
        domain="de_dietrich_smart",
        unique_id="a1b2c3",
        data={"username": "julie@example.org", "password": "mot-de-passe"},
        options={"gas_price": 0.15},
    )
    entry.add_to_hass(hass)
    mock_client.fetch_consumption_days.return_value = [DayConsumption(date(2026, 11, 1), 30.0, 5.0)]
    mock_client.fetch_consumption_hours.return_value = hours_of(date(2026, 11, 2), 1.0, 0.25)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)
    assert (await _sums(hass, "de_dietrich_smart:a1b2c3_gas_heating_cost"))[-1] == pytest.approx(54.0 * 0.15)
    assert (await _sums(hass, "de_dietrich_smart:a1b2c3_gas_dhw_cost"))[-1] == pytest.approx(11.0 * 0.15)


async def test_no_cost_statistics_without_price(hass, config_entry, mock_client, freezer) -> None:
    freezer.move_to(NOW)
    mock_client.fetch_consumption_days.return_value = [DayConsumption(date(2026, 11, 1), 30.0, 5.0)]
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    await async_wait_recording_done(hass)
    assert await _sums(hass, "de_dietrich_smart:a1b2c3_gas_heating_cost") == []


async def test_hourly_import_sends_recent_window_only(hass, config_entry, mock_client, freezer) -> None:
    """Après le premier import complet, seuls les derniers jours sont réécrits, sommes comprises."""
    from unittest.mock import patch

    from homeassistant.components.recorder import statistics as rs

    freezer.move_to(NOW)
    mock_client.fetch_consumption_days.return_value = [
        DayConsumption(date(2026, 10, 1) + timedelta(days=n), 20.0, 4.0) for n in range(31)
    ]
    mock_client.fetch_consumption_hours.return_value = hours_of(date(2026, 11, 2), 1.0, 0.25)
    sent: dict[str, list[list]] = {}

    def spy(hass_, metadata, stats):
        sent.setdefault(metadata["statistic_id"], []).append(list(stats))
        return original(hass_, metadata, stats)

    original = rs.async_add_external_statistics
    with patch.object(rs, "async_add_external_statistics", spy):
        assert await hass.config_entries.async_setup(config_entry.entry_id)
        await hass.async_block_till_done()
        await config_entry.runtime_data.consumption.async_refresh()
        await async_wait_recording_done(hass)
    sid = "de_dietrich_smart:a1b2c3_gas_heating"
    full, recent = sent[sid]
    assert full[0]["start"] == datetime(2026, 10, 1, tzinfo=UTC)
    cutoff = datetime(2026, 11, 1, tzinfo=UTC)
    assert recent and all(s["start"] >= cutoff for s in recent)
    assert [s for s in full if s["start"] >= cutoff] == recent
    assert (await _sums(hass, sid))[-1] == full[-1]["sum"]
