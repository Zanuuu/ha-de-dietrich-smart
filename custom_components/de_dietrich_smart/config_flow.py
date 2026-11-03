"""Configuration de l'intégration (complétée en tâche 5)."""

from collections.abc import Mapping
from typing import Any

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult

from .const import DOMAIN


class DeDietrichConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        return self.async_show_form(step_id="reauth_confirm")
