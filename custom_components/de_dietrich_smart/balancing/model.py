"""Pièces, zones, sondes et boucles de la nourrice ; point de départ des débits. Fonctions pures, sans HA.

Une pièce a une ou plusieurs boucles, une ou plusieurs zones (chacune avec ses sondes), un climatiseur
facultatif et une cible. Les longueurs sont en mètres linéaires, les débits en L/min.
"""

from __future__ import annotations

from typing import Any, Final

OBJECTIVE_LOWER: Final = "lower_others"
OBJECTIVE_RAISE: Final = "raise_others"
OBJECTIVES: Final = (OBJECTIVE_LOWER, OBJECTIVE_RAISE)

STEP_SETUP: Final = "setup"
STEP_THERMOSTAT: Final = "thermostat"
STEP_START_POINT: Final = "start_point"
STEP_MEASURING: Final = "measuring"

# Hypothèses de départ, à revoir après les premiers bilans.
MIN_FLOW: Final = 0.5
DEFAULT_GRADUATION: Final = 0.25
DEFAULT_TARGET: Final = 19.0
TARGET_RANGE: Final = (5.0, 30.0)
CORRECTION_RANGE: Final = (-5.0, 5.0)


class BalancingError(ValueError):
    """Saisie refusée ; le message s'affiche tel quel dans le panneau."""


def validate_rooms(rooms: list[dict[str, Any]]) -> None:
    """Refuse un inventaire incohérent : un même identifiant, une même boucle ou sonde ne servent qu'une fois."""
    if not rooms:
        raise BalancingError("Ajoutez au moins une pièce.")
    ids: set[str] = set()
    labels: set[str] = set()
    sensors: set[str] = set()
    for room in rooms:
        name = room["name"].strip()
        if not name:
            raise BalancingError("Chaque pièce doit avoir un nom.")
        if room["id"] in ids:
            raise BalancingError(f"Pièce en double : {name}.")
        ids.add(room["id"])
        if not TARGET_RANGE[0] <= room["target"] <= TARGET_RANGE[1]:
            raise BalancingError(f"{name} : cible hors de {TARGET_RANGE[0]:g} à {TARGET_RANGE[1]:g} °C.")
        if not room["loops"]:
            raise BalancingError(f"{name} : ajoutez au moins une boucle.")
        for loop in room["loops"]:
            label = loop["label"].strip()
            if not label:
                raise BalancingError(f"{name} : chaque boucle doit avoir un nom.")
            if label in labels:
                raise BalancingError(f"Boucle en double : {label}.")
            labels.add(label)
            if loop["length"] <= 0:
                raise BalancingError(f"Boucle {label} : la longueur doit être positive.")
        for zone in room["zones"]:
            if not zone["name"].strip():
                raise BalancingError(f"{name} : chaque zone doit avoir un nom.")
            for sensor in zone["sensors"]:
                if sensor["entity_id"] in sensors:
                    raise BalancingError(f"Sonde utilisée deux fois : {sensor['entity_id']}.")
                sensors.add(sensor["entity_id"])
                if not CORRECTION_RANGE[0] <= sensor["correction"] <= CORRECTION_RANGE[1]:
                    raise BalancingError(
                        f"Sonde {sensor['entity_id']} : correction hors de ±{CORRECTION_RANGE[1]:g} °C."
                    )


def loops_of(rooms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Toutes les boucles, avec l'identifiant de leur pièce."""
    return [{**loop, "room": room["id"]} for room in rooms for loop in room["loops"]]


def inventory(rooms: list[dict[str, Any]]) -> list[tuple[str, str, float]]:
    """Ce dont dépend le point de départ : (pièce, boucle, longueur)."""
    return sorted((loop["room"], loop["label"], loop["length"]) for loop in loops_of(rooms))


def _round_to(value: float, graduation: float) -> float:
    return round(round(value / graduation) * graduation, 3)


def starting_point(
    rooms: list[dict[str, Any]],
    readings: dict[str, float],
    objective: str,
    thermostat_room: str,
    graduation: float = DEFAULT_GRADUATION,
) -> list[dict[str, Any]]:
    """Débit conseillé par boucle : le total relevé réparti au prorata des longueurs.

    Objectif « baisser » : les boucles de la pièce du thermostat restent grandes ouvertes et sortent du calcul.
    Objectif « monter » : toutes les boucles se règlent.
    """
    loops = loops_of(rooms)
    missing = [loop["label"] for loop in loops if loop["label"] not in readings]
    if missing:
        raise BalancingError(f"Débit à relever : {', '.join(missing)}.")
    fixed = {loop["label"] for loop in loops if objective == OBJECTIVE_LOWER and loop["room"] == thermostat_room}
    free = [loop for loop in loops if loop["label"] not in fixed]
    total = sum(readings[loop["label"]] for loop in free)
    if free and total <= 0:
        raise BalancingError("Le total des débits à répartir doit être positif : relevez-les pendant la chauffe.")
    length = sum(loop["length"] for loop in free)
    out = []
    for loop in loops:
        current = readings[loop["label"]]
        if loop["label"] in fixed:
            target = current
        else:
            target = max(MIN_FLOW, _round_to(total * loop["length"] / length, graduation))
        out.append(
            {
                "label": loop["label"],
                "room": loop["room"],
                "current": current,
                "target": target,
                "delta": round(target - current, 3),
                "fixed": loop["label"] in fixed,
            }
        )
    return out


def step_of(data: dict[str, Any]) -> str:
    """Étape atteinte, déduite de ce qui est saisi."""
    if not data["rooms"]:
        return STEP_SETUP
    if data["thermostat_room"] is None or data["objective"] is None:
        return STEP_THERMOSTAT
    if data["validated_at"] is None:
        return STEP_START_POINT
    return STEP_MEASURING
