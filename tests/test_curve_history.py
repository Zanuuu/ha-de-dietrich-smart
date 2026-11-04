"""Lecture de l'enregistreur pour le diagnostic : moyennes horaires et changements de « chauffe »."""

from datetime import UTC, datetime, timedelta

import pytest
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import async_import_statistics
from pytest_homeassistant_custom_component.components.recorder.common import async_wait_recording_done

from custom_components.de_dietrich_smart.curve.history import fetch_changes, fetch_hours, resolve_entity_ids

T0 = datetime(2026, 11, 1, tzinfo=UTC)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(recorder_mock, enable_custom_integrations):
    """Le recorder doit être prêt avant hass : remplace le fixture de conftest."""


def _import(hass, entity_id: str, unit: str | None, values: list[float]) -> None:
    async_import_statistics(
        hass,
        {
            "source": "recorder",
            "statistic_id": entity_id,
            "name": None,
            "unit_of_measurement": unit,
            "unit_class": "temperature" if unit == "°C" else None,
            "has_sum": False,
            "mean_type": StatisticMeanType.ARITHMETIC,
        },
        [{"start": T0 + timedelta(hours=i), "mean": v, "min": v, "max": v} for i, v in enumerate(values)],
    )


async def test_fetch_hours(hass):
    ids = {"room": "sensor.r", "setpoint": "sensor.s", "outdoor": "sensor.o", "ratio": "sensor.p"}
    _import(hass, "sensor.r", "°C", [18.5, 19.0, 19.2])
    _import(hass, "sensor.s", "°C", [19.0, 19.0, 19.0])
    _import(hass, "sensor.o", "°C", [4.0, 5.0])
    _import(hass, "sensor.p", "%", [50.0, 30.0, 10.0])
    await async_wait_recording_done(hass)
    hours = await fetch_hours(hass, ids, T0, T0 + timedelta(hours=3))
    assert [h.start for h in hours] == [T0 + timedelta(hours=i) for i in range(3)]
    assert (hours[0].room, hours[0].setpoint, hours[0].outdoor, hours[0].ratio) == (18.5, 19.0, 4.0, 50.0)
    # Heure sans extérieur : gardée, valeur absente.
    assert hours[2].outdoor is None and hours[2].ratio == 10.0


async def test_fetch_hours_without_ids(hass):
    assert await fetch_hours(hass, {"room": None, "setpoint": None, "outdoor": None, "ratio": None}, T0, T0) == []


async def test_fetch_changes(hass, freezer):
    freezer.move_to(T0)
    hass.states.async_set("binary_sensor.chauffe", "off")
    freezer.tick(timedelta(minutes=10))
    hass.states.async_set("binary_sensor.chauffe", "on")
    freezer.tick(timedelta(minutes=5))
    hass.states.async_set("binary_sensor.chauffe", "off")
    freezer.tick(timedelta(minutes=5))
    hass.states.async_set("binary_sensor.chauffe", "unavailable")
    await async_wait_recording_done(hass)
    changes = await fetch_changes(hass, "binary_sensor.chauffe", T0 - timedelta(hours=1), T0 + timedelta(hours=1))
    assert changes == [
        (T0, False),
        (T0 + timedelta(minutes=10), True),
        (T0 + timedelta(minutes=15), False),
    ]


async def test_resolve_entity_ids(hass, config_entry, mock_client):
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    ids = resolve_entity_ids(hass, config_entry.runtime_data.coordinator.data)
    assert ids == {
        "room": "sensor.salon_room_temperature",
        "setpoint": "sensor.salon_setpoint",
        "outdoor": "sensor.maison_exemple_outdoor_temperature",
        "ratio": "sensor.salon_heating_ratio",
        "heating": "binary_sensor.salon_heating",
    }
