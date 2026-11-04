"""Configuration : identifiants De Dietrich, options, réauthentification, reconfiguration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from zoneinfo import ZoneInfo

import probatio
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers import selector

from .bdr.cloud import BdrClient
from .bdr.models import AuthError, SourceError, State
from .const import (
    CONF_BASE_TEMP,
    CONF_CONNECTION,
    CONF_DEFAULT_DURATION,
    CONF_EMITTER,
    CONF_GAS_PRICE,
    CONF_MAX_FLOOR_FLOW,
    CONF_WEATHER_ENTITY,
    DEFAULT_OPTIONS,
    DOMAIN,
    DURATION_CHOICES,
)
from .http import client_factory, user_agent

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_USERNAME): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.EMAIL, autocomplete="username")
        ),
        probatio.Required(CONF_PASSWORD): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD, autocomplete="current-password")
        ),
    }
)


class DeDietrichConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def _validate(self, username: str, password: str) -> tuple[State | None, dict[str, str]]:
        """Une connexion de validation au cloud ; rend l'état lu ou l'erreur du formulaire."""
        client = BdrClient(
            username,
            password,
            ZoneInfo(self.hass.config.time_zone),
            client_factory=client_factory(self.hass),
            user_agent=await user_agent(self.hass),
        )
        try:
            await client.start()
            state = await client.fetch_state()
        except AuthError:
            return None, {"base": "invalid_auth"}
        except SourceError:
            return None, {"base": "cannot_connect"}
        except Exception:
            _LOGGER.exception("erreur inattendue pendant la connexion au cloud BDR")
            return None, {"base": "unknown"}
        finally:
            await client.close()
        if not state.appliance:
            return None, {"base": "no_appliance"}
        return state, {}

    async def _validate_for(
        self, entry: ConfigEntry, username: str, password: str
    ) -> tuple[State | None, dict[str, str]]:
        """Validation pour une entrée existante : déchargée le temps de la connexion, le cloud
        n'acceptant qu'un client. Rechargée en cas d'échec ; en cas de succès, par la mise à jour."""
        was_loaded = entry.state is ConfigEntryState.LOADED
        if was_loaded:
            await self.hass.config_entries.async_unload(entry.entry_id)
        state, errors = await self._validate(username, password)
        if was_loaded and not (state and state.appliance and state.appliance.id == entry.unique_id):
            await self.hass.config_entries.async_setup(entry.entry_id)
        return state, errors

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            state, errors = await self._validate(user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            if state and state.appliance:
                await self.async_set_unique_id(state.appliance.id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=state.appliance.name, data=user_input)
        return self.async_show_form(
            step_id="user", data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, user_input), errors=errors
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reauth_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            state, errors = await self._validate_for(entry, entry.data[CONF_USERNAME], user_input[CONF_PASSWORD])
            if state and state.appliance:
                await self.async_set_unique_id(state.appliance.id)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=probatio.Schema({probatio.Required(CONF_PASSWORD): USER_SCHEMA.schema[CONF_PASSWORD]}),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            state, errors = await self._validate_for(entry, user_input[CONF_USERNAME], user_input[CONF_PASSWORD])
            if state and state.appliance:
                await self.async_set_unique_id(state.appliance.id)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(entry, data_updates=user_input)
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self.add_suggested_values_to_schema(USER_SCHEMA, {CONF_USERNAME: entry.data[CONF_USERNAME]}),
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> DeDietrichOptionsFlow:
        return DeDietrichOptionsFlow()


OPTIONS_SCHEMA = probatio.Schema(
    {
        probatio.Required(CONF_DEFAULT_DURATION): selector.SelectSelector(
            selector.SelectSelectorConfig(options=list(DURATION_CHOICES), translation_key="duration")
        ),
        probatio.Required(CONF_EMITTER): selector.SelectSelector(
            selector.SelectSelectorConfig(options=["floor", "radiators"], translation_key="emitter")
        ),
        probatio.Required(CONF_CONNECTION): selector.SelectSelector(
            selector.SelectSelectorConfig(options=["direct", "mixing"], translation_key="connection")
        ),
        probatio.Required(CONF_MAX_FLOOR_FLOW): selector.NumberSelector(
            selector.NumberSelectorConfig(min=30, max=60, step=1, unit_of_measurement="°C")
        ),
        probatio.Required(CONF_BASE_TEMP): selector.NumberSelector(
            selector.NumberSelectorConfig(min=-25, max=0, step=1, unit_of_measurement="°C")
        ),
        probatio.Optional(CONF_WEATHER_ENTITY): selector.EntitySelector(
            selector.EntitySelectorConfig(domain="weather")
        ),
        probatio.Optional(CONF_GAS_PRICE): selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=2, step="any", unit_of_measurement="€/kWh", mode=selector.NumberSelectorMode.BOX
            )
        ),
    }
)


class DeDietrichOptionsFlow(OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        return self.async_show_form(
            step_id="init",
            data_schema=self.add_suggested_values_to_schema(
                OPTIONS_SCHEMA, {**DEFAULT_OPTIONS, **self.config_entry.options}
            ),
        )
