"""Intégration De Dietrich Smart : chaudière et thermostat SMART TC° par le cloud BDR Thermea."""

from __future__ import annotations

from dataclasses import dataclass
from zoneinfo import ZoneInfo

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from . import faults, panel
from .balancing.store import Balancing
from .bdr.cloud import BdrClient
from .commands import CommandRunner
from .const import DOMAIN
from .coordinator import DeDietrichCoordinator
from .curve.coordinator import DiagnosticCoordinator
from .curve.outdoor import OutdoorCoordinator
from .devices import appliance_device_info
from .energy import ConsumptionCoordinator
from .http import client_factory, user_agent
from .journal import Journal
from .repairs import async_delete_issues, async_update_issues
from .services import async_setup_services
from .websocket import async_setup_websocket

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
    diagnostic: DiagnosticCoordinator
    outdoor: OutdoorCoordinator
    balancing: Balancing


type DeDietrichConfigEntry = ConfigEntry[RuntimeData]


CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    async_setup_services(hass)
    async_setup_websocket(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: DeDietrichConfigEntry) -> bool:
    client = BdrClient(
        entry.data[CONF_USERNAME],
        entry.data[CONF_PASSWORD],
        ZoneInfo(hass.config.time_zone),
        client_factory=client_factory(hass),
        user_agent=await user_agent(hass),
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
    diagnostic = DiagnosticCoordinator(hass, entry, coordinator)
    outdoor = OutdoorCoordinator(hass, entry, coordinator, consumption)
    balancing = Balancing(hass, entry.entry_id)
    await balancing.async_load()
    entry.runtime_data = RuntimeData(
        client=client,
        coordinator=coordinator,
        journal=journal,
        commands=commands,
        device_id=device.id,
        consumption=consumption,
        diagnostic=diagnostic,
        outdoor=outdoor,
        balancing=balancing,
    )
    await consumption.async_load()
    # La consommation ne bloque pas le chargement : elle sera relue à l'heure suivante.
    await consumption.async_refresh()
    # Relève horaire même sans capteur abonné (« gaz du jour » désactivé) : l'import en dépend.
    entry.async_on_unload(consumption.async_add_listener(lambda: None))
    entry.async_on_unload(consumption.async_save)
    # Diagnostic de la courbe : lu dans l'enregistreur, un échec ne bloque pas le chargement.
    await diagnostic.async_refresh()
    entry.async_on_unload(diagnostic.async_add_listener(lambda: None))
    # Températures journalières (DJU) : l'archive Open-Meteo est lue une fois, sans bloquer le chargement.
    await outdoor.async_load()
    await outdoor.async_refresh()
    entry.async_on_unload(outdoor.async_add_listener(lambda: None))
    entry.async_on_unload(outdoor.async_save)

    @callback
    def _on_data() -> None:
        """Nouvel état publié (relève réussie ou après une commande)."""
        async_update_issues(hass, entry)
        diagnostic.async_on_state()
        entry.async_create_background_task(hass, commands.async_run_pending(), "de_dietrich_smart_pending")

    @callback
    def _after_poll() -> None:
        # Relève en échec : les abonnés ne sont pas prévenus, l'alerte « cloud injoignable » si.
        async_update_issues(hass, entry)

    entry.async_on_unload(coordinator.async_add_listener(_on_data))
    coordinator.after_poll.append(_after_poll)
    entry.async_on_unload(lambda: coordinator.after_poll.remove(_after_poll))
    async_update_issues(hass, entry)
    entry.async_on_unload(client.close)
    entry.async_on_unload(journal.async_save)
    entry.async_on_unload(balancing.async_save)
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    await panel.async_register(hass)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: DeDietrichConfigEntry) -> bool:
    if ok := await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        panel.async_unregister(hass, entry.entry_id)
    return ok


async def async_remove_entry(hass: HomeAssistant, entry: DeDietrichConfigEntry) -> None:
    """Intégration supprimée : alertes et fichiers .storage (journal, conso, températures, nourrice) avec elle."""
    async_delete_issues(hass, entry.entry_id)
    for name in ("journal", "consumption", "outdoor", "balancing"):
        await Store(hass, 1, f"{DOMAIN}.{entry.entry_id}.{name}").async_remove()
