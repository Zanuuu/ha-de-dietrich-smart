"""Fiche d'appareil de la chaudière (enregistrée dès le chargement : zone et eau chaude s'y rattachent)."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo

from .bdr.models import Appliance
from .const import DOMAIN


def appliance_device_info(a: Appliance) -> DeviceInfo:
    gw = a.gateway or {}
    return DeviceInfo(
        identifiers={(DOMAIN, a.id)},
        name=a.name,
        manufacturer="De Dietrich",
        model=a.name,
        sw_version=gw.get("softwareVersion"),
        hw_version=gw.get("hardwareVersion"),
    )
