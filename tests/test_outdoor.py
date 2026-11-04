"""Températures extérieures journalières pour les DJU : enregistreur HA d'abord, Open-Meteo sinon."""

from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
import respx

from custom_components.de_dietrich_smart.bdr.models import DayConsumption
from custom_components.de_dietrich_smart.curve.outdoor import (
    ARCHIVE_URL,
    FORECAST_URL,
    daily_from_hours,
    open_meteo_requests,
    parse_open_meteo,
)

TODAY = date(2026, 11, 4)


def test_daily_from_hours_needs_20_hours():
    t0 = datetime(2026, 11, 1, 23, tzinfo=UTC)  # minuit à Paris
    rows = [(t0 + timedelta(hours=i), 5.0 - i * 0.1, 5.0 + i * 0.1) for i in range(24)]
    rows += [(t0 + timedelta(hours=24 + i), 1.0, 2.0) for i in range(19)]
    assert daily_from_hours(rows, "Europe/Paris") == {date(2026, 11, 2): (2.7, 7.3)}


def test_parse_open_meteo_skips_nulls():
    payload = {
        "daily": {
            "time": ["2026-11-01", "2026-11-02"],
            "temperature_2m_min": [1.5, None],
            "temperature_2m_max": [9.0, 8.0],
        }
    }
    assert parse_open_meteo(payload) == {date(2026, 11, 1): (1.5, 9.0)}


def test_requests_archive_then_forecast():
    missing = [date(2025, 10, 1), date(2025, 10, 2), date(2026, 11, 2), date(2026, 11, 3)]
    reqs = open_meteo_requests(missing, TODAY, 48.58, 7.75, "Europe/Paris")
    (u1, p1), (u2, p2) = reqs
    assert u1 == ARCHIVE_URL and (p1["start_date"], p1["end_date"]) == ("2025-10-01", "2025-10-02")
    assert u2 == FORECAST_URL and p2["past_days"] == 7 and p2["forecast_days"] == 1
    assert p1["daily"] == "temperature_2m_min,temperature_2m_max" and p1["timezone"] == "Europe/Paris"
    assert (p1["latitude"], p1["longitude"]) == (48.58, 7.75)


def daily(days: list[date], tmin: float = 2.0, tmax: float = 10.0) -> dict:
    return {"daily": {"time": [d.isoformat() for d in days], "temperature_2m_min": [tmin] * len(days),
                      "temperature_2m_max": [tmax] * len(days)}}  # fmt: skip


@pytest.fixture
async def located(hass):
    hass.config.latitude, hass.config.longitude = 48.5839, 7.7455
    await hass.config.async_set_time_zone("Europe/Paris")


@respx.mock
async def test_coordinator_fetches_once_a_day(hass, located, config_entry, mock_client, freezer):
    freezer.move_to("2026-11-04 10:00:00+01:00")
    mock_client.fetch_consumption_days.return_value = [DayConsumption(TODAY - timedelta(days=20), 10.0, 1.0)]
    past = [TODAY - timedelta(days=n) for n in range(20, 6, -1)]
    recent = [TODAY - timedelta(days=n) for n in range(7, 0, -1)]
    archive = respx.get(ARCHIVE_URL).mock(return_value=httpx.Response(200, json=daily(past)))
    forecast = respx.get(FORECAST_URL).mock(return_value=httpx.Response(200, json=daily(recent, 4.0, 12.0)))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    outdoor = config_entry.runtime_data.outdoor
    assert outdoor.data[TODAY - timedelta(days=1)] == (4.0, 12.0, "open-meteo")
    assert outdoor.data[TODAY - timedelta(days=20)] == (2.0, 10.0, "open-meteo")
    # Coordonnées arrondies : la position exacte de la maison ne sort pas.
    assert archive.calls.last.request.url.params["latitude"] == "48.58"
    await outdoor.async_refresh()
    assert archive.call_count == 1 and forecast.call_count == 1


@respx.mock
async def test_coordinator_survives_open_meteo_failure(hass, located, config_entry, mock_client, freezer, caplog):
    freezer.move_to("2026-11-04 10:00:00+01:00")
    respx.get(ARCHIVE_URL).mock(return_value=httpx.Response(500))
    respx.get(FORECAST_URL).mock(side_effect=httpx.ConnectError("hors ligne"))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    rt = config_entry.runtime_data
    assert rt.outdoor.data == {} and rt.outdoor.last_update_success
    await rt.outdoor.async_refresh()
    assert caplog.text.count("Open-Meteo indisponible") == 1


@respx.mock
async def test_days_kept_in_storage(hass, located, config_entry, mock_client, freezer, hass_storage):
    freezer.move_to("2026-11-04 10:00:00+01:00")
    respx.get(FORECAST_URL).mock(return_value=httpx.Response(200, json=daily([TODAY - timedelta(days=1)])))
    respx.get(ARCHIVE_URL).mock(return_value=httpx.Response(200, json=daily([])))
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert await hass.config_entries.async_unload(config_entry.entry_id)
    stored = hass_storage[f"de_dietrich_smart.{config_entry.entry_id}.outdoor"]["data"]
    assert stored["days"]["2026-11-03"] == [2.0, 10.0, "open-meteo"]
