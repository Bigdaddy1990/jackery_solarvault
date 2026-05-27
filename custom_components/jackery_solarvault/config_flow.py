"""Config flow for Jackery SolarVault."""

from collections.abc import Mapping
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .client import JackeryApi, JackeryAuthError, JackeryError
from .const import (
    CONF_CREATE_CALCULATED_POWER_SENSORS,
    CONF_CREATE_SAVINGS_DETAIL_SENSORS,
    CONF_CREATE_SMART_METER_DERIVED_SENSORS,
    CONF_ENABLE_BLE_TRANSPORT,
    CONF_ENABLE_BLE_WRITES,
    CONF_ENABLE_UNREDACTED_DIAGNOSTICS,
    CONF_MQTT_MAC_ID,
    CONF_REGION_CODE,
    CONF_THIRD_PARTY_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_PORT,
    CONF_THIRD_PARTY_MQTT_TOKEN,
    CONF_THIRD_PARTY_MQTT_USERNAME,
    DEFAULT_CREATE_CALCULATED_POWER_SENSORS,
    DEFAULT_CREATE_SAVINGS_DETAIL_SENSORS,
    DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
    DEFAULT_ENABLE_BLE_TRANSPORT,
    DEFAULT_ENABLE_BLE_WRITES,
    DEFAULT_ENABLE_UNREDACTED_DIAGNOSTICS,
    DEFAULT_THIRD_PARTY_MQTT_ENABLE,
    DEFAULT_THIRD_PARTY_MQTT_IP,
    DEFAULT_THIRD_PARTY_MQTT_PASSWORD,
    DEFAULT_THIRD_PARTY_MQTT_PORT,
    DEFAULT_THIRD_PARTY_MQTT_TOKEN,
    DEFAULT_THIRD_PARTY_MQTT_USERNAME,
    DOMAIN,
    FLOW_ABORT_REAUTH_ENTRY_MISSING,
    FLOW_ABORT_REAUTH_SUCCESSFUL,
    FLOW_ABORT_RECONFIGURE_ACCOUNT_MISMATCH,
    FLOW_ABORT_RECONFIGURE_ENTRY_MISSING,
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
from .util import (
    config_entry_bool_option,
    config_entry_int_option,
    config_entry_str_option,
)

_LOGGER = logging.getLogger(__name__)

_BOOL_OPTION_DEFAULTS: dict[str, bool] = {
    CONF_CREATE_SMART_METER_DERIVED_SENSORS: DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
    CONF_CREATE_CALCULATED_POWER_SENSORS: DEFAULT_CREATE_CALCULATED_POWER_SENSORS,
    CONF_CREATE_SAVINGS_DETAIL_SENSORS: DEFAULT_CREATE_SAVINGS_DETAIL_SENSORS,
    CONF_ENABLE_BLE_TRANSPORT: DEFAULT_ENABLE_BLE_TRANSPORT,
    CONF_ENABLE_BLE_WRITES: DEFAULT_ENABLE_BLE_WRITES,
    CONF_ENABLE_UNREDACTED_DIAGNOSTICS: DEFAULT_ENABLE_UNREDACTED_DIAGNOSTICS,
    CONF_THIRD_PARTY_MQTT_ENABLE: DEFAULT_THIRD_PARTY_MQTT_ENABLE,
}

_STR_OPTION_DEFAULTS: dict[str, str] = {
    CONF_THIRD_PARTY_MQTT_IP: DEFAULT_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_USERNAME: DEFAULT_THIRD_PARTY_MQTT_USERNAME,
    CONF_THIRD_PARTY_MQTT_PASSWORD: DEFAULT_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_TOKEN: DEFAULT_THIRD_PARTY_MQTT_TOKEN,
}

_INT_OPTION_DEFAULTS: dict[str, int] = {
    CONF_THIRD_PARTY_MQTT_PORT: DEFAULT_THIRD_PARTY_MQTT_PORT,
}


def _normalize_account(value: str) -> str:
    """
    Normalize an account identifier by stripping leading and trailing whitespace.
    
    Returns:
        The account identifier with leading and trailing whitespace removed.
    """
    return value.strip()


def _current_option_values(entry: ConfigEntry) -> dict[str, Any]:
    """
    Resolve the current option values for a configuration entry.
    
    For each known option key (grouped by boolean, string, and integer types), the value is taken from the entry's stored options, falling back to any legacy setup-data value for that key and then to the type-specific default.
    
    Parameters:
        entry (ConfigEntry): Configuration entry to read option and legacy setup-data values from.
    
    Returns:
        dict[str, Any]: Mapping of option keys to their resolved current values.
    """
    values: dict[str, Any] = {}
    for key, bool_default in _BOOL_OPTION_DEFAULTS.items():
        values[key] = config_entry_bool_option(entry, key, bool_default)
    for key, str_default in _STR_OPTION_DEFAULTS.items():
        values[key] = config_entry_str_option(entry, key, str_default)
    for key, int_default in _INT_OPTION_DEFAULTS.items():
        values[key] = config_entry_int_option(entry, key, int_default)
    return values


def _flow_options(
    user_input: dict[str, Any],
    current_options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Produce a complete options dictionary by taking values from `user_input` when present, otherwise preserving `current_options`, and finally falling back to the integration's typed defaults.
    
    Parameters:
        user_input (dict[str, Any]): Option values provided by the user; may omit keys.
        current_options (dict[str, Any] | None): Existing stored option values to preserve when `user_input` omits a key.
    
    Returns:
        dict[str, Any]: A merged options dictionary containing every known option key with its resolved value.
    """
    current = current_options or {}
    merged: dict[str, Any] = {}
    for defaults in (
        _BOOL_OPTION_DEFAULTS,
        _STR_OPTION_DEFAULTS,
        _INT_OPTION_DEFAULTS,
    ):
        for key, default in defaults.items():
            merged[key] = user_input.get(key, current.get(key, default))
    return merged


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
    vol.Optional(
        CONF_ENABLE_BLE_TRANSPORT,
        default=DEFAULT_ENABLE_BLE_TRANSPORT,
    ): bool,
})


class JackeryOptionsFlow(OptionsFlow):
    """Handle the Jackery SolarVault options flow."""

    # No __init__: HA injects self.config_entry automatically since 2024.11.

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """
        Present the options form for the integration or create an options entry from submitted values.
        
        When `user_input` is provided, merge the submitted values with the current stored options and create an options entry. When `user_input` is None, show the options form populated with defaults from the current entry options (BLE, sensor-creation, diagnostics, and third-party MQTT settings).
        
        Parameters:
            user_input (dict[str, Any] | None): Submitted form values, or None to render the form.
        
        Returns:
            ConfigFlowResult: The created options entry result, or a form result to display to the user.
        """
        current_options = _current_option_values(self.config_entry)
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
        current_enable_ble_transport = current_options[CONF_ENABLE_BLE_TRANSPORT]
        current_enable_ble_writes = current_options[CONF_ENABLE_BLE_WRITES]
        current_enable_unredacted_diagnostics = current_options[
            CONF_ENABLE_UNREDACTED_DIAGNOSTICS
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
            vol.Optional(
                CONF_ENABLE_BLE_TRANSPORT,
                default=current_enable_ble_transport,
            ): bool,
            vol.Optional(
                CONF_ENABLE_BLE_WRITES,
                default=current_enable_ble_writes,
            ): bool,
            vol.Optional(
                CONF_ENABLE_UNREDACTED_DIAGNOSTICS,
                default=current_enable_unredacted_diagnostics,
            ): bool,
            vol.Optional(
                CONF_THIRD_PARTY_MQTT_ENABLE,
                default=current_options[CONF_THIRD_PARTY_MQTT_ENABLE],
            ): bool,
            vol.Optional(
                CONF_THIRD_PARTY_MQTT_IP,
                default=current_options[CONF_THIRD_PARTY_MQTT_IP],
            ): str,
            vol.Optional(
                CONF_THIRD_PARTY_MQTT_PORT,
                default=current_options[CONF_THIRD_PARTY_MQTT_PORT],
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Optional(
                CONF_THIRD_PARTY_MQTT_USERNAME,
                default=current_options[CONF_THIRD_PARTY_MQTT_USERNAME],
            ): str,
            vol.Optional(
                CONF_THIRD_PARTY_MQTT_PASSWORD,
                default=current_options[CONF_THIRD_PARTY_MQTT_PASSWORD],
            ): str,
            vol.Optional(
                CONF_THIRD_PARTY_MQTT_TOKEN,
                default=current_options[CONF_THIRD_PARTY_MQTT_TOKEN],
            ): str,
        })
        return self.async_show_form(step_id=FLOW_STEP_INIT, data_schema=schema)


class JackeryConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Jackery SolarVault config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """
        Handle the initial user-driven configuration step and authenticate the provided Jackery account.
        
        Validates and normalizes the submitted username, prevents creating a duplicate entry for the same account, attempts to authenticate with the Jackery service using the provided credentials, and on success creates the configuration entry with the supplied credentials and merged initial options. On validation or authentication failure, returns the form populated with appropriate error messages.
        
        Parameters:
            user_input (dict[str, Any] | None): Form input submitted by the user. Expected keys include `CONF_USERNAME` and `CONF_PASSWORD`, and may include optional integration option fields.
        
        Returns:
            ConfigFlowResult: A flow result that either shows the user form with errors or creates the new configuration entry on successful authentication.
        """
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
                _LOGGER.debug("Cannot connect to Jackery during setup: %s", err)
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
    ) -> ConfigFlowResult:
        """
        Reconfigure an existing config entry by validating provided account credentials and updating stored username, password, and options.
        
        Validates that the submitted username matches the entry being reconfigured, verifies credentials with the Jackery service, and on success updates and reloads the entry. If input is missing or invalid, presents the reconfigure form prefilled with current option defaults. Aborts if the reconfigure target is missing or the provided account does not match the entry.
        
        Returns:
            A ConfigFlowResult that shows the reconfigure form with any errors, aborts with a specific reason, or updates and reloads the entry on successful reconfiguration.
        """
        try:
            entry = self._get_reconfigure_entry()
        except (KeyError, RuntimeError):
            return self.async_abort(reason=FLOW_ABORT_RECONFIGURE_ENTRY_MISSING)

        errors: dict[str, str] = {}

        if user_input is not None:
            account = _normalize_account(user_input[CONF_USERNAME])
            if not account:
                errors[CONF_USERNAME] = FLOW_ERROR_ACCOUNT_REQUIRED
            elif account.lower() != str(entry.unique_id or "").lower():
                return self.async_abort(reason=FLOW_ABORT_RECONFIGURE_ACCOUNT_MISMATCH)
            else:
                await self.async_set_unique_id(account.lower())
                self._abort_if_unique_id_mismatch()
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
                    _LOGGER.debug(
                        "Cannot connect to Jackery during reconfigure: %s", err
                    )
                    errors[FLOW_ERROR_BASE] = FLOW_ERROR_CANNOT_CONNECT
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data_updates={
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
            vol.Optional(
                CONF_ENABLE_BLE_TRANSPORT,
                default=current_options[CONF_ENABLE_BLE_TRANSPORT],
            ): bool,
            vol.Optional(
                CONF_ENABLE_BLE_WRITES,
                default=current_options[CONF_ENABLE_BLE_WRITES],
            ): bool,
            vol.Optional(
                CONF_ENABLE_UNREDACTED_DIAGNOSTICS,
                default=current_options[CONF_ENABLE_UNREDACTED_DIAGNOSTICS],
            ): bool,
            vol.Optional(
                CONF_THIRD_PARTY_MQTT_ENABLE,
                default=current_options[CONF_THIRD_PARTY_MQTT_ENABLE],
            ): bool,
            vol.Optional(
                CONF_THIRD_PARTY_MQTT_IP,
                default=current_options[CONF_THIRD_PARTY_MQTT_IP],
            ): str,
            vol.Optional(
                CONF_THIRD_PARTY_MQTT_PORT,
                default=current_options[CONF_THIRD_PARTY_MQTT_PORT],
            ): vol.All(vol.Coerce(int), vol.Range(min=1, max=65535)),
            vol.Optional(
                CONF_THIRD_PARTY_MQTT_USERNAME,
                default=current_options[CONF_THIRD_PARTY_MQTT_USERNAME],
            ): str,
            vol.Optional(
                CONF_THIRD_PARTY_MQTT_PASSWORD,
                default=current_options[CONF_THIRD_PARTY_MQTT_PASSWORD],
            ): str,
            vol.Optional(
                CONF_THIRD_PARTY_MQTT_TOKEN,
                default=current_options[CONF_THIRD_PARTY_MQTT_TOKEN],
            ): str,
        })
        return self.async_show_form(
            step_id=FLOW_STEP_RECONFIGURE,
            data_schema=schema,
            description_placeholders={
                "username": str(entry.data.get(CONF_USERNAME, "")),
            },
            errors=errors,
        )

    async def async_step_reauth(
        self, entry_data: Mapping[str, Any]
    ) -> ConfigFlowResult:
        """Handle reauth started by ConfigEntryAuthFailed."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """
        Prompt for the account's current password and validate it against Jackery to complete reauthentication.
        
        Parameters:
            user_input (dict[str, Any] | None): Form data containing `CONF_PASSWORD` when submitted.
        
        Returns:
            ConfigFlowResult: The next flow result (shows the password form on error or missing input, aborts and updates the entry on successful reauthentication).
        """
        try:
            entry = self._get_reauth_entry()
        except (KeyError, RuntimeError):
            return self.async_abort(reason=FLOW_ABORT_REAUTH_ENTRY_MISSING)
        errors: dict[str, str] = {}

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            api = JackeryApi(
                session=session,
                account=entry.data[CONF_USERNAME],
                password=user_input[CONF_PASSWORD],
                mqtt_mac_id=entry.data.get(CONF_MQTT_MAC_ID),
                region_code=entry.data.get(CONF_REGION_CODE),
            )
            try:
                await api.async_login()
            except JackeryAuthError:
                errors[FLOW_ERROR_BASE] = FLOW_ERROR_INVALID_AUTH
            except JackeryError as err:
                _LOGGER.debug("Cannot connect to Jackery during reauth: %s", err)
                errors[FLOW_ERROR_BASE] = FLOW_ERROR_CANNOT_CONNECT
            else:
                return self.async_update_reload_and_abort(
                    entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
                    reason=FLOW_ABORT_REAUTH_SUCCESSFUL,
                )

        return self.async_show_form(
            step_id=FLOW_STEP_REAUTH_CONFIRM,
            data_schema=vol.Schema({
                vol.Required(CONF_PASSWORD): vol.All(str, vol.Length(min=1))
            }),
            description_placeholders={
                "username": entry.data[CONF_USERNAME],
            },
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> JackeryOptionsFlow:
        """Return the options flow handler for this entry."""
        return JackeryOptionsFlow()
