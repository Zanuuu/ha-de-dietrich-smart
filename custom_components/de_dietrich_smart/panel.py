"""Panneau latéral « Chaudière » : un seul pour toutes les entrées, retiré avec la dernière."""

from __future__ import annotations

import json
from pathlib import Path

from homeassistant.core import HomeAssistant

from .const import DOMAIN

URL_PATH = "de-dietrich-smart"
STATIC_URL = f"/{DOMAIN}_static"
FRONTEND_DIR = Path(__file__).parent / "frontend"
DATA_PANEL = f"{DOMAIN}_panel"


def _version() -> str:
    return str(json.loads((Path(__file__).parent / "manifest.json").read_text())["version"])


async def async_register(hass: HomeAssistant) -> None:
    """Enregistre le panneau à la première entrée chargée (si l'interface est chargée)."""
    registered = hass.data.get(DATA_PANEL)
    if registered is True or "frontend" not in hass.config.components or hass.http is None:
        return
    from homeassistant.components import panel_custom
    from homeassistant.components.http import StaticPathConfig

    hass.data[DATA_PANEL] = True
    version = await hass.async_add_executor_job(_version)
    # Après un déchargement, les fichiers restent servis : seul le panneau est remis.
    if registered != "static":
        await hass.http.async_register_static_paths([StaticPathConfig(STATIC_URL, str(FRONTEND_DIR), True)])
    await panel_custom.async_register_panel(
        hass,
        frontend_url_path=URL_PATH,
        webcomponent_name="dd-smart-panel",
        module_url=f"{STATIC_URL}/dd-smart-panel.js?v={version}",
        sidebar_title="Chaudière",
        sidebar_icon="mdi:water-boiler",
        require_admin=False,
    )


def async_unregister(hass: HomeAssistant, unloading_entry_id: str) -> None:
    """Retire le panneau quand plus aucune autre entrée n'est chargée."""
    others = [e for e in hass.config_entries.async_loaded_entries(DOMAIN) if e.entry_id != unloading_entry_id]
    if hass.data.get(DATA_PANEL) is not True or others:
        return
    from homeassistant.components import frontend

    frontend.async_remove_panel(hass, URL_PATH)
    # Les fichiers statiques restent servis (une route aiohttp ne se retire pas) : sans effet.
    hass.data[DATA_PANEL] = "static"
