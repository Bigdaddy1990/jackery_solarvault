"""Config flow for Jackery SolarVault."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_SCAN_INTERVAL, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import JackeryApi, JackeryAuthError, JackeryError
from .const import (
    CONF_DEVICE_ID,
    CONF_SYSTEM_ID,
    DEFAULT_SCAN_INTERVAL_SEC,
    DOMAIN,
    MIN_SCAN_INTERVAL_SEC,
)

_LOGGER = logging.getLogger(__name__)

USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_USERNAME): str,
        vol.Required(CONF_PASSWORD): str,
        vol.Optional(CONF_DEVICE_ID): str,
        vol.Optional(CONF_SYSTEM_ID): str,
    }
)


class JackeryConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            await self.async_set_unique_id(user_input[CONF_USERNAME].lower())
            self._abort_if_unique_id_configured()

            session = async_get_clientsession(self.hass)
            api = JackeryApi(
                session=session,
                account=user_input[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
            )
            try:
                await api.async_login()
            except JackeryAuthError:
                errors["base"] = "invalid_auth"
            except JackeryError as err:
                _LOGGER.error("Cannot connect to Jackery: %s", err)
                errors["base"] = "cannot_connect"
            else:
                # Strip empty optional values
                clean = {k: v for k, v in user_input.items() if v not in (None, "")}
                return self.async_create_entry(
                    title=f"Jackery ({user_input[CONF_USERNAME]})",
                    data=clean,
                )

        return self.async_show_form(
            step_id="user", data_schema=USER_SCHEMA, errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> JackeryOptionsFlow:
        return JackeryOptionsFlow(entry)


class JackeryOptionsFlow(OptionsFlow):
    def __init__(self, entry: ConfigEntry) -> None:
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        current = self._entry.options.get(
            CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL_SEC
        )
        schema = vol.Schema(
            {
                vol.Required(CONF_SCAN_INTERVAL, default=current): vol.All(
                    int, vol.Range(min=MIN_SCAN_INTERVAL_SEC, max=3600)
                ),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
