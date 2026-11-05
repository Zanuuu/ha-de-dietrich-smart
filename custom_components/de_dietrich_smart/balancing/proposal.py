"""Pièces et sondes proposées à partir des zones de Home Assistant. Fonctions pures, sans HA.

Une proposition n'est qu'un brouillon : rien n'est enregistré avant que l'utilisateur ne l'ait revu.
"""

from __future__ import annotations

from typing import Any

from .model import DEFAULT_TARGET


def _match_area(entity: dict[str, Any], areas: list[dict[str, str]]) -> str | None:
    """Zone de l'entité, sinon celle dont l'identifiant figure, mot pour mot, dans son entity_id."""
    if entity.get("area_id"):
        return entity["area_id"]
    words = f"_{entity['entity_id'].split('.', 1)[1]}_"
    found = [area["id"] for area in areas if f"_{area['id']}_" in words]
    return max(found, key=len) if found else None


def propose(
    sensors: list[dict[str, Any]], climates: list[dict[str, Any]], areas: list[dict[str, str]]
) -> dict[str, Any]:
    """Une pièce par zone HA contenant au moins une sonde ; les autres sondes restent « à classer »."""
    by_area: dict[str, list[str]] = {}
    unclassified = []
    for sensor in sensors:
        if area := _match_area(sensor, areas):
            by_area.setdefault(area, []).append(sensor["entity_id"])
        else:
            unclassified.append(sensor["entity_id"])
    climate_of: dict[str, str] = {}
    for climate in climates:
        if area := _match_area(climate, areas):
            climate_of.setdefault(area, climate["entity_id"])
    rooms = [
        {
            "id": area["id"],
            "name": area["name"],
            "target": DEFAULT_TARGET,
            "climate": climate_of.get(area["id"]),
            "zones": [
                {
                    "name": area["name"],
                    "sensors": [{"entity_id": e, "correction": 0.0} for e in sorted(by_area[area["id"]])],
                }
            ],
            "loops": [],
        }
        for area in areas
        if area["id"] in by_area
    ]
    return {"rooms": rooms, "unclassified": sorted(unclassified)}
