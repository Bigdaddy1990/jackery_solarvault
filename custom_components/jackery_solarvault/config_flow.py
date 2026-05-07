"""Config flow for Jackery SolarVault."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigFlow, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .api import JackeryApi, JackeryAuthError, JackeryError
from .const import (
    CONF_CREATE_CALCULATED_POWER_SENSORS,
    CONF_CREATE_SAVINGS_DETAIL_SENSORS,
    CONF_CREATE_SMART_METER_DERIVED_SENSORS,
    DEFAULT_CREATE_CALCULATED_POWER_SENSORS,
    DEFAULT_CREATE_SAVINGS_DETAIL_SENSORS,
    DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
    DOMAIN,
    FLOW_ABORT_REAUTH_ENTRY_MISSING,
    FLOW_ABORT_REAUTH_SUCCESSFUL,
    FLOW_ERROR_ACCOUNT_REQUIRED,
    FLOW_ERROR_BASE,
    FLOW_ERROR_CANNOT_CONNECT,
    FLOW_ERROR_INVALID_AUTH,
    FLOW_STEP_INIT,
    FLOW_STEP_REAUTH_CONFIRM,
    FLOW_STEP_USER,
)

_LOGGER = logging.getLogger(__name__)


def _normalize_account(value: str) -> str:
    """Normalize user-facing account identifiers before auth and unique IDs."""
    return value.strip()


def _entry_bool_option(entry: ConfigEntry, key: str, default: bool) -> bool:
    """Return a boolean option, falling back to setup data then defaults."""
    return bool(entry.options.get(key, entry.data.get(key, default)))


USER_SCHEMA = vol.Schema({
    vol.Required(CONF_USERNAME): vol.All(str, vol.Length(min=1)),
    vol.Required(CONF_PASSWORD): vol.All(str, vol.Length(min=1)),
    vol.Optional(
        CONF_CREATE_SMART_METER_DERIVED_SENSORS,
        default=DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
    ): bool,
    vol.Optional(
        CONF_CREATE_CALCULATED_POWER_SENSORS,
        default=DEFAULT_CREATE_CALCULATED_POWER_SENSORS,
    ): bool,
    vol.Optional(
        CONF_CREATE_SAVINGS_DETAIL_SENSORS,
        default=DEFAULT_CREATE_SAVINGS_DETAIL_SENSORS,
    ): bool,
})


class JackeryOptionsFlow(OptionsFlow):
    """Handle the Jackery SolarVault jackery options flow."""

    def __init__(self, entry: ConfigEntry) -> None:
        """Initialise the entity from the coordinator and description."""
        self._entry = entry

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Step init."""
        if user_input is not None:
            clean = {k: v for k, v in user_input.items() if v not in (None, "")}
            return self.async_create_entry(title="", data=clean)

        current_create_derived = _entry_bool_option(
            self._entry,
            CONF_CREATE_SMART_METER_DERIVED_SENSORS,
            DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
        )
        current_create_calculated_power = _entry_bool_option(
            self._entry,
            CONF_CREATE_CALCULATED_POWER_SENSORS,
            DEFAULT_CREATE_CALCULATED_POWER_SENSORS,
        )
        current_create_savings_details = _entry_bool_option(
            self._entry,
            CONF_CREATE_SAVINGS_DETAIL_SENSORS,
            DEFAULT_CREATE_SAVINGS_DETAIL_SENSORS,
        )
        schema = vol.Schema({
            vol.Optional(
                CONF_CREATE_SMART_METER_DERIVED_SENSORS,
                default=current_create_derived,
            ): bool,
            vol.Optional(
                CONF_CREATE_CALCULATED_POWER_SENSORS,
                default=current_create_calculated_power,
            ): bool,
            vol.Optional(
                CONF_CREATE_SAVINGS_DETAIL_SENSORS,
                default=current_create_savings_details,
            ): bool,
        })
        return self.async_show_form(step_id=FLOW_STEP_INIT, data_schema=schema)


class JackeryConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Jackery SolarVault jackery config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialise the entity from the coordinator and description."""
        self._reauth_entry: ConfigEntry | None = None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle the initial user-driven config flow step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            account = _normalize_account(user_input[CONF_USERNAME])
            if not account:
                errors[CONF_USERNAME] = FLOW_ERROR_ACCOUNT_REQUIRED
                return self.async_show_form(
                    step_id=FLOW_STEP_USER,
                    data_schema=USER_SCHEMA,
                    errors=errors,
                )
            await self.async_set_unique_id(account.lower())
            self._abort_if_unique_id_configured()

            session = async_get_clientsession(self.hass)
            api = JackeryApi(
                session=session,
                account=account,
                password=user_input[CONF_PASSWORD],
            )
            try:
                await api.async_login()
            except JackeryAuthError:
                errors[FLOW_ERROR_BASE] = FLOW_ERROR_INVALID_AUTH
            except JackeryError as err:
                _LOGGER.error("Cannot connect to Jackery: %s", err)
                errors[FLOW_ERROR_BASE] = FLOW_ERROR_CANNOT_CONNECT
            else:
                return self.async_create_entry(
                    title=account,
                    data={
                        CONF_USERNAME: account,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                    options={
                        CONF_CREATE_SMART_METER_DERIVED_SENSORS: user_input.get(
                            CONF_CREATE_SMART_METER_DERIVED_SENSORS,
                            DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
                        ),
                        CONF_CREATE_CALCULATED_POWER_SENSORS: user_input.get(
                            CONF_CREATE_CALCULATED_POWER_SENSORS,
                            DEFAULT_CREATE_CALCULATED_POWER_SENSORS,
                        ),
                        CONF_CREATE_SAVINGS_DETAIL_SENSORS: user_input.get(
                            CONF_CREATE_SAVINGS_DETAIL_SENSORS,
                            DEFAULT_CREATE_SAVINGS_DETAIL_SENSORS,
                        ),
                    },
                )

        return self.async_show_form(
            step_id=FLOW_STEP_USER,
            data_schema=USER_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Handle reauth started by ConfigEntryAuthFailed."""
        entry_id = self.context.get("entry_id")
        if not entry_id:
            return self.async_abort(reason=FLOW_ABORT_REAUTH_ENTRY_MISSING)
        self._reauth_entry = self.hass.config_entries.async_get_entry(entry_id)
        if self._reauth_entry is None:
            return self.async_abort(reason=FLOW_ABORT_REAUTH_ENTRY_MISSING)
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Prompt the user for a fresh password and re-test against Jackery."""
        if self._reauth_entry is None:
            return self.async_abort(reason=FLOW_ABORT_REAUTH_ENTRY_MISSING)
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            api = JackeryApi(
                session=session,
                account=self._reauth_entry.data[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
            )
            try:
                await api.async_login()
            except JackeryAuthError:
                errors[FLOW_ERROR_BASE] = FLOW_ERROR_INVALID_AUTH
            except JackeryError as err:
                _LOGGER.error("Cannot connect to Jackery during reauth: %s", err)
                errors[FLOW_ERROR_BASE] = FLOW_ERROR_CANNOT_CONNECT
            else:
                self.hass.config_entries.async_update_entry(
                    self._reauth_entry,
                    data={
                        **self._reauth_entry.data,
                        CONF_PASSWORD: user_input[CONF_PASSWORD],
                    },
                )
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason=FLOW_ABORT_REAUTH_SUCCESSFUL)

        return self.async_show_form(
            step_id=FLOW_STEP_REAUTH_CONFIRM,
            data_schema=vol.Schema({
                vol.Required(CONF_PASSWORD): vol.All(str, vol.Length(min=1))
            }),
            description_placeholders={
                "username": self._reauth_entry.data[CONF_USERNAME],
            },
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> JackeryOptionsFlow:
        """Return the options flow handler for this entry."""
        return JackeryOptionsFlow(entry)
