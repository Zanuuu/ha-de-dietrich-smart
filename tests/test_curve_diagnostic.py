"""Diagnostic de la courbe sur des heures synthétiques : une règle par test."""

from datetime import UTC, datetime, timedelta

import pytest

from custom_components.de_dietrich_smart.curve.diagnostic import Hour, count_short_cycles, diagnose

FLOOR = {"emitter": "floor", "connection": "direct", "max_floor_flow": 45, "base_temp": -15}
SAFE = {
    "slope": 0.5, "slope_min": 0.0, "slope_max": 4.0, "slope_step": 0.1,
    "base": 22.0, "base_min": 15.0, "base_max": 80.0, "base_step": 0.5, "max_flow": 80.0,
}  # fmt: skip
T0 = datetime(2026, 11, 1, tzinfo=UTC)


def hours(days: float, outdoor: float, gap: float, start: datetime = T0) -> list[Hour]:
    """Confort 19 °C de 6 h à 22 h, réduit 17 °C la nuit ; ambiance = consigne + écart le jour."""
    out = []
    for i in range(int(days * 24)):
        t = start + timedelta(hours=i)
        comfort = 6 <= t.hour < 22
        sp = 19.0 if comfort else 17.0
        out.append(Hour(t, sp + gap if comfort else sp, sp, outdoor, 40.0))
    return out


def test_insufficient_data():
    d = diagnose(hours(2, 5.0, -2.0), None, SAFE, FLOOR)
    assert d.action == "insufficient_data" and d.slope is None
    assert "2 jours" in d.reasons[0]


def test_switch_to_floor_first_even_without_data():
    unsafe = {**SAFE, "slope": 1.5, "base": 15.0}
    d = diagnose([], None, unsafe, FLOOR)
    assert d.action == "switch_to_floor" and (d.slope, d.base) == (0.3, 33.0)
    assert d.confidence == "high"


def test_comfortable_keeps():
    d = diagnose(hours(8, 5.0, 0.1), None, SAFE, FLOOR)
    assert d.action == "keep" and (d.slope, d.base) == (0.5, 22.0)
    assert d.bands["fresh"]["occupied"] == 8 * 16 and d.bands["fresh"]["gap"] == pytest.approx(0.1)


def test_deficit_cold_raises_slope():
    d = diagnose(hours(8, -3.0, -1.0), None, SAFE, FLOOR)
    assert d.action == "raise_slope" and (d.slope, d.base) == (0.6, 22.0)


def test_deficit_mild_raises_base_by_two():
    d = diagnose(hours(8, 10.0, -1.0), None, SAFE, FLOOR)
    assert d.action == "raise_base" and (d.slope, d.base) == (0.5, 24.0)


def test_deficit_fresh_raises_base_by_one():
    d = diagnose(hours(8, 4.0, -1.0), None, SAFE, FLOOR)
    assert d.action == "raise_base" and d.base == 23.0


def test_cold_before_mild():
    mixed = hours(4, 10.0, -1.0) + hours(4, -3.0, -1.0, T0 + timedelta(days=4))
    assert diagnose(mixed, None, SAFE, FLOOR).action == "raise_slope"


def test_excess_cold_lowers_slope():
    d = diagnose(hours(8, -3.0, 1.5), None, SAFE, FLOOR)
    assert d.action == "lower_slope" and d.slope == 0.4


def test_excess_mild_lowers_base():
    d = diagnose(hours(8, 10.0, 1.5), None, SAFE, FLOOR)
    assert d.action == "lower_base" and d.base == 21.0


def test_short_cycles_lower_base():
    d = diagnose(hours(8, 10.0, 0.0), 9.0, SAFE, FLOOR)
    assert d.action == "lower_base" and any("cycles courts" in r for r in d.reasons)


def test_band_without_enough_hours_is_ignored():
    # 1 jour en bande froide (16 h occupées < 24) : pas de pente relevée ; le reste est confortable.
    mixed = hours(7, 5.0, 0.0) + hours(1, -3.0, -2.0, T0 + timedelta(days=7))
    d = diagnose(mixed, None, SAFE, FLOOR)
    assert d.action == "keep" and d.bands["cold"]["counted"] is False


def test_proposal_clamped_to_max_floor_flow():
    # 30 + 0,4 × 35 = 44 : +2 au pied dépasserait 45 °C à −15 °C ; ramené à 31.
    near = {**SAFE, "slope": 0.4, "base": 30.0}
    d = diagnose(hours(8, 10.0, -1.0), None, near, FLOOR)
    assert d.action == "raise_base" and d.base == 31.0


def test_proposal_at_limit_keeps():
    at = {**SAFE, "slope": 0.4, "base": 31.0}
    d = diagnose(hours(8, 10.0, -1.0), None, at, FLOOR)
    assert d.action == "keep" and any("départ maxi" in r for r in d.reasons)


@pytest.mark.parametrize(("days", "confidence"), [(8, "high"), (4, "medium")])
def test_confidence(days, confidence):
    assert diagnose(hours(days, 10.0, -1.0), None, SAFE, FLOOR).confidence == confidence


def test_low_confidence_when_band_is_thin():
    # 7 jours doux confortables + 2 jours froids en déficit (32 h occupées, < 48).
    mixed = hours(7, 10.0, 0.0) + hours(2, -3.0, -1.0, T0 + timedelta(days=7))
    d = diagnose(mixed, None, SAFE, FLOOR)
    assert d.action == "raise_slope" and d.confidence == "low"


def test_recovery_time():
    h = hours(8, 5.0, 0.0)
    # Le matin, l'ambiance atteint la consigne une heure après le passage en confort.
    h = [Hour(x.start, 17.5, x.setpoint, x.outdoor, x.ratio) if x.start.hour == 6 else x for x in h]
    assert diagnose(h, None, SAFE, FLOOR).bands["fresh"]["recovery_h"] == 1


def test_count_short_cycles():
    start = T0
    changes = [
        (start + timedelta(minutes=0), True),
        (start + timedelta(minutes=5), False),  # 5 min : court
        (start + timedelta(minutes=60), True),
        (start + timedelta(minutes=90), False),  # 30 min
        (start + timedelta(hours=24), True),
        (start + timedelta(hours=24, minutes=8), False),  # 8 min : court
    ]
    assert count_short_cycles(changes, start, start + timedelta(days=2)) == 1.0
    assert count_short_cycles([], start, start + timedelta(days=2)) is None


def test_one_override_does_not_hide_comfort_hours():
    # Une dérogation d'une heure à 23 °C ne doit pas faire passer les heures de confort pour inoccupées.
    h = hours(8, 10.0, -1.0)
    h[30] = Hour(h[30].start, 22.0, 23.0, 10.0, 80.0)
    d = diagnose(h, None, SAFE, FLOOR)
    assert d.action == "raise_base" and d.bands["mild"]["occupied"] >= 8 * 16 - 1
