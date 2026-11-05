"""Sondes, climatiseurs et zones lus dans Home Assistant pour l'assistant de la nourrice."""

from __future__ import annotations

from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import ATTR_DEVICE_CLASS, ATTR_FRIENDLY_NAME, STATE_UNAVAILABLE, STATE_UNKNOWN
from homeassistant.core import HomeAssistant
from homeassistant.helpers import area_registry as ar
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er

from ..const import DOMAIN


def _area_id(hass: HomeAssistant, entity_id: str) -> str | None:
    entry = er.async_get(hass).async_get(entity_id)
    if entry is None:
        return None
    if entry.area_id:
        return entry.area_id
    device = dr.async_get(hass).async_get(entry.device_id) if entry.device_id else None
    return device.area_id if device else None


def sensors(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Sondes utilisables : température, avec statistiques horaires (`measurement`), hors entités de l'intégration."""
    registry = er.async_get(hass)
    out = []
    for state in hass.states.async_all("sensor"):
        attrs = state.attributes
        if attrs.get(ATTR_DEVICE_CLASS) != SensorDeviceClass.TEMPERATURE:
            continue
        if attrs.get("state_class") != SensorStateClass.MEASUREMENT:
            continue
        if state.state in (STATE_UNAVAILABLE, STATE_UNKNOWN):
            continue
        entry = registry.async_get(state.entity_id)
        if entry is not None and entry.platform == DOMAIN:
            continue
        out.append(
            {
                "entity_id": state.entity_id,
                "name": attrs.get(ATTR_FRIENDLY_NAME, state.entity_id),
                "state": state.state,
                "area_id": _area_id(hass, state.entity_id),
            }
        )
    return sorted(out, key=lambda s: s["name"])


def climates(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Climatiseurs ou thermostats disponibles, hors entités de l'intégration."""
    registry = er.async_get(hass)
    out = []
    for state in hass.states.async_all("climate"):
        entry = registry.async_get(state.entity_id)
        if entry is not None and entry.platform == DOMAIN:
            continue
        out.append(
            {
                "entity_id": state.entity_id,
                "name": state.attributes.get(ATTR_FRIENDLY_NAME, state.entity_id),
                "area_id": _area_id(hass, state.entity_id),
            }
        )
    return sorted(out, key=lambda c: c["name"])


def areas(hass: HomeAssistant) -> list[dict[str, str]]:
    return [{"id": a.id, "name": a.name} for a in ar.async_get(hass).async_list_areas()]
