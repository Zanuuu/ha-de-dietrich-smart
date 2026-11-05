"""Nourrice : inventaire, point de départ et proposition (fonctions pures, noms génériques)."""

import pytest

from custom_components.de_dietrich_smart.balancing.model import (
    MIN_FLOW,
    OBJECTIVE_LOWER,
    OBJECTIVE_RAISE,
    BalancingError,
    starting_point,
    step_of,
    validate_rooms,
)
from custom_components.de_dietrich_smart.balancing.proposal import propose


def room(rid, loops, sensors=("sensor.a",), target=19.0):
    return {
        "id": rid,
        "name": rid.upper(),
        "target": target,
        "climate": None,
        "zones": [{"name": "Z", "sensors": [{"entity_id": s, "correction": 0.0} for s in sensors]}],
        "loops": [{"label": label, "length": length} for label, length in loops],
    }


ROOMS = [
    room("a", [("A1", 100.0), ("A2", 100.0)], ["sensor.a"]),
    room("b", [("B1", 50.0)], ["sensor.b"]),
    room("c", [("C1", 150.0)], ["sensor.c"]),
]
READINGS = {"A1": 2.0, "A2": 2.0, "B1": 1.0, "C1": 3.0}


def test_valid_rooms():
    validate_rooms(ROOMS)


@pytest.mark.parametrize(
    ("rooms", "message"),
    [
        ([], "au moins une pièce"),
        ([room("a", [])], "au moins une boucle"),
        ([{**room("a", [("A", 1.0)]), "name": " "}], "un nom"),
        ([room("a", [("A", 1.0)]), room("a", [("B", 1.0)], ["sensor.b"])], "Pièce en double"),
        ([room("a", [("A", 1.0)]), room("b", [("A", 1.0)], ["sensor.b"])], "Boucle en double"),
        ([room("a", [("A", 0.0)])], "longueur"),
        ([room("a", [("A", 1.0)]), room("b", [("B", 1.0)])], "Sonde utilisée deux fois"),
        ([room("a", [("A", 1.0)], target=40.0)], "cible"),
    ],
)
def test_invalid_rooms(rooms, message):
    with pytest.raises(BalancingError, match=message):
        validate_rooms(rooms)


def test_correction_bounds():
    r = room("a", [("A", 1.0)])
    r["zones"][0]["sensors"][0]["correction"] = 9.0
    with pytest.raises(BalancingError, match="correction"):
        validate_rooms([r])


def test_start_lower_keeps_every_thermostat_loop_open():
    points = {p["label"]: p for p in starting_point(ROOMS, READINGS, OBJECTIVE_LOWER, "a")}
    # Les deux boucles de la pièce du thermostat sortent du calcul : 4 L/min à répartir sur 50 et 150 m.
    assert points["A1"]["fixed"] and points["A2"]["fixed"]
    assert points["A1"]["target"] == 2.0 and points["A1"]["delta"] == 0
    assert points["B1"]["target"] == 1.0 and points["C1"]["target"] == 3.0


def test_start_raise_adjusts_all_loops():
    points = {p["label"]: p for p in starting_point(ROOMS, READINGS, OBJECTIVE_RAISE, "a")}
    assert not any(p["fixed"] for p in points.values())
    # 8 L/min sur 400 m : 2 L/min pour 100 m.
    assert points["A1"]["target"] == 2.0 and points["B1"]["target"] == 1.0 and points["C1"]["target"] == 3.0


def test_start_rounds_to_graduation_and_keeps_floor():
    rooms = [room("a", [("A", 10.0)]), room("b", [("B", 990.0)], ["sensor.b"])]
    points = {p["label"]: p for p in starting_point(rooms, {"A": 1.0, "B": 2.1}, OBJECTIVE_RAISE, "a", 0.25)}
    assert points["A"]["target"] == MIN_FLOW
    assert points["B"]["target"] == 3.0 and points["B"]["delta"] == pytest.approx(0.9)


def test_start_needs_all_readings():
    with pytest.raises(BalancingError, match="B1"):
        starting_point(ROOMS, {"A1": 1.0, "A2": 1.0, "C1": 1.0}, OBJECTIVE_RAISE, "a")


def test_start_refuses_zero_total():
    with pytest.raises(BalancingError, match="positif"):
        starting_point(ROOMS, dict.fromkeys(READINGS, 0.0), OBJECTIVE_RAISE, "a")


def test_start_with_only_thermostat_loops():
    points = starting_point([ROOMS[0]], {"A1": 2.0, "A2": 2.0}, OBJECTIVE_LOWER, "a")
    assert all(p["fixed"] for p in points)


@pytest.mark.parametrize(
    ("rooms", "thermostat", "objective", "validated", "step"),
    [
        ([], None, None, None, "setup"),
        (ROOMS, None, OBJECTIVE_LOWER, None, "thermostat"),
        (ROOMS, "a", None, None, "thermostat"),
        (ROOMS, "a", OBJECTIVE_LOWER, None, "start_point"),
        (ROOMS, "a", OBJECTIVE_LOWER, 1.0, "measuring"),
    ],
)
def test_steps(rooms, thermostat, objective, validated, step):
    data = {"rooms": rooms, "thermostat_room": thermostat, "objective": objective, "validated_at": validated}
    assert step_of(data) == step


def test_proposal_from_areas():
    areas = [{"id": "piece_a", "name": "Pièce A"}, {"id": "piece_b", "name": "Pièce B"}, {"id": "vide", "name": "Vide"}]
    sensors = [
        {"entity_id": "sensor.capteur_1", "area_id": "piece_a"},
        {"entity_id": "sensor.piece_b_temperature", "area_id": None},
        {"entity_id": "sensor.garage", "area_id": None},
        # Mot entier seulement : « piece_ab » n'appartient pas à « piece_a ».
        {"entity_id": "sensor.piece_ab_temperature", "area_id": None},
    ]
    climates = [{"entity_id": "climate.piece_a", "area_id": None}, {"entity_id": "climate.autre", "area_id": None}]
    out = propose(sensors, climates, areas)
    assert [r["name"] for r in out["rooms"]] == ["Pièce A", "Pièce B"]
    a, b = out["rooms"]
    assert a["climate"] == "climate.piece_a" and b["climate"] is None
    assert a["zones"][0]["sensors"] == [{"entity_id": "sensor.capteur_1", "correction": 0.0}]
    assert b["zones"][0]["sensors"][0]["entity_id"] == "sensor.piece_b_temperature" and a["loops"] == []
    assert out["unclassified"] == ["sensor.garage", "sensor.piece_ab_temperature"]
