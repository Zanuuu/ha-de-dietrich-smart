"""Intégration De Dietrich Smart : chaudière et thermostat SMART TC° par le cloud BDR Thermea."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.httpx_client import create_async_httpx_client
from homeassistant.helpers.typing import ConfigType

from . import faults
from .bdr.cloud import BdrClient
from .commands import CommandRunner
from .const import DOMAIN
from .coordinator import DeDietrichCoordinator
from .devices import appliance_device_info
from .energy import ConsumptionCoordinator
from .journal import Journal
from .repairs import async_update_issues
from .services import async_setup_services

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
    consumption: ConsumptionCoordinator


type DeDietrichConfigEntry = ConfigEntry[RuntimeData]


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_setup_services(hass)
    return True


async def _options_updated(hass: HomeAssistant, entry: DeDietrichConfigEntry) -> None:
    async_update_issues(hass, entry)


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
    consumption = ConsumptionCoordinator(hass, entry, client, coordinator)
    entry.runtime_data = RuntimeData(
        client=client,
        coordinator=coordinator,
        journal=journal,
        commands=commands,
        device_id=device.id,
        consumption=consumption,
    )
    # La consommation ne bloque pas le chargement : elle sera relue à l'heure suivante.
    await consumption.async_refresh()

    @callback
    def _on_data() -> None:
        """Nouvel état publié (relève réussie ou après une commande)."""
        async_update_issues(hass, entry)
        entry.async_create_background_task(hass, commands.async_run_pending(), "de_dietrich_smart_pending")

    @callback
    def _after_poll() -> None:
        # Relève en échec : les abonnés ne sont pas prévenus, l'alerte « cloud injoignable » si.
        async_update_issues(hass, entry)

    entry.async_on_unload(coordinator.async_add_listener(_on_data))
    coordinator.after_poll.append(_after_poll)
    entry.async_on_unload(lambda: coordinator.after_poll.remove(_after_poll))
    entry.async_on_unload(entry.add_update_listener(_options_updated))
    async_update_issues(hass, entry)
    entry.async_on_unload(client.close)
    entry.async_on_unload(journal.async_save)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DeDietrichConfigEntry) -> bool:
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
