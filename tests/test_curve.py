"""Courbe : bornes du plancher (M12) et bascule vers une courbe de plancher."""

import pytest

from custom_components.de_dietrich_smart.curve import (
    curve_flow,
    floor_excess,
    floor_violation,
    switch_proposal,
)

FLOOR = {"emitter": "floor", "connection": "direct", "max_floor_flow": 45, "base_temp": -15}
CLOUD = {"slope_step": 0.1, "base_step": 0.5, "max_flow": 80.0}


def test_safe_curve_has_no_violation():
    # 22 + 0,5 × 35 = 39,5 °C à −15 °C.
    assert floor_violation(0.5, 22.0, 80.0, FLOOR) is None
    assert floor_excess(0.5, 22.0, 80.0, FLOOR) == 0


@pytest.mark.parametrize(
    ("slope", "base", "match"),
    [
        (1.2, 15.0, "pente 1,2 hors des bornes du plancher"),
        (0.1, 30.0, "pente 0,1 hors des bornes du plancher"),
        (0.2, 36.0, "pied 36 °C hors des bornes du plancher"),
        (0.8, 24.0, "départ de 52 °C à −15 °C, au-delà de 45 °C"),
    ],
)
def test_violations(slope, base, match):
    assert match in floor_violation(slope, base, 80.0, FLOOR)
    assert floor_excess(slope, base, 80.0, FLOOR) > 0


def test_guard_only_for_direct_floor():
    for opts in ({**FLOOR, "emitter": "radiators"}, {**FLOOR, "connection": "mixing"}):
        assert floor_violation(1.5, 40.0, 80.0, opts) is None
        assert floor_excess(1.5, 40.0, 80.0, opts) == 0


def test_cloud_max_flow_caps_the_check():
    # Départ plafonné à 40 °C par la chaudière : pas de dépassement, mais pente hors bornes.
    assert "départ" not in floor_violation(1.5, 15.0, 40.0, FLOOR)


def test_excess_decreases_toward_safety():
    assert floor_excess(0.8, 15.0, 80.0, FLOOR) < floor_excess(1.5, 15.0, 80.0, FLOOR)


def test_switch_keeps_flow_at_5_degrees():
    # Courbe d'origine de l'application : 15 + 1,5 × 15 = 37,5 °C à 5 °C.
    curve = {**CLOUD, "slope": 1.5, "base": 15.0}
    slope, base = switch_proposal(curve, FLOOR)
    assert floor_violation(slope, base, 80.0, FLOOR) is None
    assert curve_flow({**curve, "slope": slope, "base": base}, 5.0) == pytest.approx(37.5, abs=0.5)


def test_switch_when_flow_at_5_cannot_be_kept():
    # 40 + 1,5 × 15 = 62,5 °C à 5 °C : impossible à garder sous 45 °C à −15 °C.
    curve = {**CLOUD, "slope": 1.5, "base": 40.0}
    slope, base = switch_proposal(curve, FLOOR)
    assert slope == 0.2 and floor_violation(slope, base, 80.0, FLOOR) is None
    assert base == 35.0
