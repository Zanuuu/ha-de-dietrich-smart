"""Intégration De Dietrich Smart : chaudière et thermostat SMART TC° par le cloud BDR Thermea."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from typing import Any
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.httpx_client import create_async_httpx_client

from . import faults
from .bdr.cloud import BdrClient
from .commands import CommandRunner
from .coordinator import DeDietrichCoordinator
from .devices import appliance_device_info
from .journal import Journal

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.CLIMATE,
    Platform.NUMBER,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
    Platform.WATER_HEATER,
]


@dataclass
class RuntimeData:
    client: BdrClient
    coordinator: DeDietrichCoordinator
    journal: Journal
    commands: CommandRunner
    device_id: str
    consumption: Any = None


type DeDietrichConfigEntry = ConfigEntry[RuntimeData]


async def async_setup_entry(hass: HomeAssistant, entry: DeDietrichConfigEntry) -> bool:
    client = BdrClient(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        ZoneInfo(hass.config.time_zone),
        # Clients fermés par le client BDR lui-même (celui de connexion à chaque usage).
        client_factory=partial(create_async_httpx_client, hass, auto_cleanup=False),
    )
    await client.start()
    # Table des codes défaut lue hors de la boucle (lecture de fichier).
    await hass.async_add_executor_job(faults.load)
    journal = Journal(hass, entry.entry_id)
    await journal.async_load()
    coordinator = DeDietrichCoordinator(hass, entry, client, journal)
    try:
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await client.close()
        raise
    commands = CommandRunner(hass, client, coordinator, journal)
    appliance = coordinator.data.appliance
    assert appliance is not None
    # Fiche de la chaudière enregistrée d'emblée : la zone et l'eau chaude s'y rattachent.
    device = dr.async_get(hass).async_get_or_create(config_entry_id=entry.entry_id, **appliance_device_info(appliance))
    entry.runtime_data = RuntimeData(
        client=client, coordinator=coordinator, journal=journal, commands=commands, device_id=device.id
    )

    @callback
    def _after_poll() -> None:
        entry.async_create_background_task(hass, commands.async_run_pending(), "de_dietrich_smart_pending")

    entry.async_on_unload(coordinator.async_add_listener(_after_poll))
    entry.async_on_unload(client.close)
    entry.async_on_unload(journal.async_save)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DeDietrichConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
