"""Config flow for Jackery SolarVault."""

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
    CONF_MQTT_MAC_ID,
    CONF_REGION_CODE,
    DEFAULT_CREATE_CALCULATED_POWER_SENSORS,
    DEFAULT_CREATE_SAVINGS_DETAIL_SENSORS,
    DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
    DOMAIN,
    FLOW_ABORT_REAUTH_ENTRY_MISSING,
    FLOW_ABORT_REAUTH_SUCCESSFUL,
    FLOW_ABORT_RECONFIGURE_ACCOUNT_MISMATCH,
    FLOW_ABORT_RECONFIGURE_SUCCESSFUL,
    FLOW_ERROR_ACCOUNT_REQUIRED,
    FLOW_ERROR_BASE,
    FLOW_ERROR_CANNOT_CONNECT,
    FLOW_ERROR_INVALID_AUTH,
    FLOW_STEP_INIT,
    FLOW_STEP_REAUTH_CONFIRM,
    FLOW_STEP_RECONFIGURE,
    FLOW_STEP_USER,
)
from .util import config_entry_bool_option

_LOGGER = logging.getLogger(__name__)

_OPTION_DEFAULTS: dict[str, bool] = {
    CONF_CREATE_SMART_METER_DERIVED_SENSORS: DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
    CONF_CREATE_CALCULATED_POWER_SENSORS: DEFAULT_CREATE_CALCULATED_POWER_SENSORS,
    CONF_CREATE_SAVINGS_DETAIL_SENSORS: DEFAULT_CREATE_SAVINGS_DETAIL_SENSORS,
}


def _normalize_account(value: str) -> str:
    """Normalize user-facing account identifiers before auth and unique IDs."""
    return value.strip()


def _current_option_values(entry: ConfigEntry) -> dict[str, bool]:
    """Return current option values with legacy setup-data fallback."""
    return {
        key: config_entry_bool_option(entry, key, default)
        for key, default in _OPTION_DEFAULTS.items()
    }


def _flow_options(
    user_input: dict[str, Any],
    current_options: dict[str, bool] | None = None,
) -> dict[str, bool]:
    """Build complete options, preserving current values when fields are omitted."""
    current = current_options or {}
    return {
        key: user_input.get(key, current.get(key, default))
        for key, default in _OPTION_DEFAULTS.items()
    }


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
        current_options = _current_option_values(self._entry)
        if user_input is not None:
            return self.async_create_entry(
                title="",
                data=_flow_options(user_input, current_options),
            )

        current_create_derived = current_options[
            CONF_CREATE_SMART_METER_DERIVED_SENSORS
        ]
        current_create_calculated_power = current_options[
            CONF_CREATE_CALCULATED_POWER_SENSORS
        ]
        current_create_savings_details = current_options[
            CONF_CREATE_SAVINGS_DETAIL_SENSORS
        ]
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
                    options=_flow_options(user_input),
                )

        return self.async_show_form(
            step_id=FLOW_STEP_USER,
            data_schema=USER_SCHEMA,
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        """Handle a user-initiated reconfigure of an existing entry.

        HA's reconfigure flow lets the user change credentials and toggle
        the calculated-sensor options without removing the entry. The
        normalized account from user input must match the entry that
        triggered the flow; otherwise we abort to keep unique-id semantics
        stable across the reconfigure round-trip.
        """
        entry_id = self.context.get("entry_id")
        entry = self.hass.config_entries.async_get_entry(entry_id) if entry_id else None
        if entry is None:
            return self.async_abort(reason=FLOW_ABORT_REAUTH_ENTRY_MISSING)

        errors: dict[str, str] = {}

        if user_input is not None:
            account = _normalize_account(user_input[CONF_USERNAME])
            if not account:
                errors[CONF_USERNAME] = FLOW_ERROR_ACCOUNT_REQUIRED
            elif account.lower() != str(entry.unique_id or "").lower():
                return self.async_abort(reason=FLOW_ABORT_RECONFIGURE_ACCOUNT_MISMATCH)
            else:
                session = async_get_clientsession(self.hass)
                api = JackeryApi(
                    session=session,
                    account=account,
                    password=user_input[CONF_PASSWORD],
                    mqtt_mac_id=entry.data.get(CONF_MQTT_MAC_ID),
                    region_code=entry.data.get(CONF_REGION_CODE),
                )
                try:
                    await api.async_login()
                except JackeryAuthError:
                    errors[FLOW_ERROR_BASE] = FLOW_ERROR_INVALID_AUTH
                except JackeryError as err:
                    _LOGGER.error(
                        "Cannot connect to Jackery during reconfigure: %s", err
                    )
                    errors[FLOW_ERROR_BASE] = FLOW_ERROR_CANNOT_CONNECT
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data={
                            **entry.data,
                            CONF_USERNAME: account,
                            CONF_PASSWORD: user_input[CONF_PASSWORD],
                        },
                        options=_flow_options(
                            user_input, _current_option_values(entry)
                        ),
                        reason=FLOW_ABORT_RECONFIGURE_SUCCESSFUL,
                    )

        current_options = _current_option_values(entry)
        schema = vol.Schema({
            vol.Required(
                CONF_USERNAME, default=entry.data.get(CONF_USERNAME, "")
            ): vol.All(str, vol.Length(min=1)),
            vol.Required(CONF_PASSWORD): vol.All(str, vol.Length(min=1)),
            vol.Optional(
                CONF_CREATE_SMART_METER_DERIVED_SENSORS,
                default=current_options[CONF_CREATE_SMART_METER_DERIVED_SENSORS],
            ): bool,
            vol.Optional(
                CONF_CREATE_CALCULATED_POWER_SENSORS,
                default=current_options[CONF_CREATE_CALCULATED_POWER_SENSORS],
            ): bool,
            vol.Optional(
                CONF_CREATE_SAVINGS_DETAIL_SENSORS,
                default=current_options[CONF_CREATE_SAVINGS_DETAIL_SENSORS],
            ): bool,
        })
        return self.async_show_form(
            step_id=FLOW_STEP_RECONFIGURE,
            data_schema=schema,
            description_placeholders={
                "username": str(entry.data.get(CONF_USERNAME, "")),
            },
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
                mqtt_mac_id=self._reauth_entry.data.get(CONF_MQTT_MAC_ID),
                region_code=self._reauth_entry.data.get(CONF_REGION_CODE),
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
