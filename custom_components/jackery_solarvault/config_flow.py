"""Config flow for Jackery SolarVault."""

import logging
from typing import TYPE_CHECKING, Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, OptionsFlow
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client.api import JackeryApi, JackeryAuthError, JackeryError
from .const import (
    CONF_CREATE_CALCULATED_POWER_SENSORS,
    CONF_CREATE_SAVINGS_DETAIL_SENSORS,
    CONF_CREATE_SMART_METER_DERIVED_SENSORS,
    CONF_ENABLE_BLE_TRANSPORT,
    CONF_ENABLE_DERIVED_HOME_ENERGY_FALLBACK,
    CONF_ENABLE_MONTH_STATISTICS,
    CONF_ENABLE_WEEK_STATISTICS,
    CONF_ENABLE_YEAR_STATISTICS,
    CONF_LOCAL_MQTT_ENABLE,
    CONF_LOCAL_MQTT_HOST,
    CONF_LOCAL_MQTT_PASSWORD,
    CONF_LOCAL_MQTT_PORT,
    CONF_LOCAL_MQTT_USERNAME,
    CONF_MQTT_MAC_ID,
    CONF_REGION_CODE,
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
    DEFAULT_CREATE_CALCULATED_POWER_SENSORS,
    DEFAULT_CREATE_SAVINGS_DETAIL_SENSORS,
    DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
    DEFAULT_ENABLE_BLE_TRANSPORT,
    DEFAULT_ENABLE_DERIVED_HOME_ENERGY_FALLBACK,
    DEFAULT_ENABLE_MONTH_STATISTICS,
    DEFAULT_ENABLE_WEEK_STATISTICS,
    DEFAULT_ENABLE_YEAR_STATISTICS,
    DEFAULT_LOCAL_MQTT_ENABLE,
    DEFAULT_LOCAL_MQTT_PORT,
    DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER,
    DOMAIN,
    ENTRY_BOOTSTRAP_MQTT_SESSION,
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
from .util import config_entry_bool_option

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.config_entries import ConfigEntry, ConfigFlowResult

_LOGGER = logging.getLogger(__name__)

# Options surface in the UI flow. Debug-only toggles
# (``CONF_ENABLE_UNREDACTED_DIAGNOSTICS`` and ``CONF_ENABLE_PAYLOAD_DEBUG_LOG``)
# are deliberately NOT listed here — sensitive logging is gated by the
# ``JACKERY_DEV_MODE=1`` environment variable so it cannot be toggled
# accidentally by users sharing diagnostics screenshots. See
# ``util.dev_mode_redactions_disabled``.
_OPTION_DEFAULTS: dict[str, bool] = {
    CONF_CREATE_SMART_METER_DERIVED_SENSORS: DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
    CONF_CREATE_CALCULATED_POWER_SENSORS: DEFAULT_CREATE_CALCULATED_POWER_SENSORS,
    CONF_CREATE_SAVINGS_DETAIL_SENSORS: DEFAULT_CREATE_SAVINGS_DETAIL_SENSORS,
    CONF_ENABLE_BLE_TRANSPORT: DEFAULT_ENABLE_BLE_TRANSPORT,
    CONF_ENABLE_WEEK_STATISTICS: DEFAULT_ENABLE_WEEK_STATISTICS,
    CONF_ENABLE_MONTH_STATISTICS: DEFAULT_ENABLE_MONTH_STATISTICS,
    CONF_ENABLE_YEAR_STATISTICS: DEFAULT_ENABLE_YEAR_STATISTICS,
    CONF_ENABLE_DERIVED_HOME_ENERGY_FALLBACK: DEFAULT_ENABLE_DERIVED_HOME_ENERGY_FALLBACK,  # noqa: E501
}


def _normalize_account(value: Any) -> str:  # noqa: ANN401
    """
    Normalize user-facing account identifiers before authentication and unique-id generation.
    
    Parameters:
        value (Any): The raw account identifier input.
    
    Returns:
        str: The trimmed account string if `value` is a `str`, otherwise an empty string.
    """
    return value.strip() if isinstance(value, str) else ""


def _entry_text(entry: ConfigEntry, key: str) -> str:
    """
    Get the string value for `key` from a config entry.
    
    Parameters:
        entry (ConfigEntry): The configuration entry to read from.
        key (str): The data key to retrieve.
    
    Returns:
        str: The stored string value for `key`, or an empty string if the key is missing or its value is not a string.
    """
    value = entry.data.get(key)
    return value if isinstance(value, str) else ""


def _current_option_values(entry: ConfigEntry) -> dict[str, bool]:
    """
    Retrieve current option values for the given config entry, using legacy defaults when an option is not set.
    
    Parameters:
        entry (ConfigEntry): The configuration entry to read options from.
    
    Returns:
        dict[str, bool]: Mapping of option keys to their boolean values; stored option values are used when present, otherwise legacy defaults are applied.
    """
    return {
        key: config_entry_bool_option(entry, key, default)
        for key, default in _OPTION_DEFAULTS.items()
    }


def _flow_options(
    user_input: dict[str, Any],
    current_options: dict[str, bool] | None = None,
) -> dict[str, bool]:
    """
    Produce a complete mapping of all boolean option keys to their selected values.
    
    For each known option key, use the value from `user_input` when present; otherwise use the value from `current_options` if provided; if neither is present, use the configured default.
    
    Parameters:
        user_input (dict[str, Any]): Form-submitted option values keyed by option name.
        current_options (dict[str, bool] | None): Existing option values to preserve when a key is omitted.
    
    Returns:
        dict[str, bool]: A dictionary containing every option key with its resolved boolean value.
    """
    current = current_options or {}
    return {
        key: user_input.get(key, current.get(key, default))
        for key, default in _OPTION_DEFAULTS.items()
    }


def _entry_data_from_api_login(
    account: str,
    password: str,
    api: JackeryApi,
    existing_entry: ConfigEntry | None = None,
) -> dict[str, Any]:
    """
    Create config entry data populated from credentials and values retrieved from a successful JackeryApi login.
    
    Parameters:
        account (str): Account username to store under `CONF_USERNAME`.
        password (str): Account password to store under `CONF_PASSWORD`.
        api (JackeryApi): Authenticated API client providing optional bootstrap values (`mqtt_mac_id`, `region_code`, and an MQTT session snapshot).
        existing_entry (ConfigEntry | None): Optional existing config entry whose string fields are used as fallbacks when the API does not provide them.
    
    Returns:
        dict[str, Any]: A dictionary containing at minimum `CONF_USERNAME` and `CONF_PASSWORD`. May also include:
          - `CONF_MQTT_MAC_ID` (str): MQTT MAC ID from the API or the existing entry.
          - `CONF_REGION_CODE` (str): Region code from the API or the existing entry.
          - `ENTRY_BOOTSTRAP_MQTT_SESSION` (dict): A copy of the MQTT session snapshot returned by the API, if present.
    """
    data: dict[str, Any] = {
        CONF_USERNAME: account,
        CONF_PASSWORD: password,
    }
    mqtt_mac_id = api.mqtt_mac_id
    if mqtt_mac_id:
        data[CONF_MQTT_MAC_ID] = mqtt_mac_id
    elif existing_entry is not None and isinstance(
        existing_entry.data.get(CONF_MQTT_MAC_ID), str
    ):
        data[CONF_MQTT_MAC_ID] = existing_entry.data[CONF_MQTT_MAC_ID]

    region_code = api.region_code
    if region_code:
        data[CONF_REGION_CODE] = region_code
    elif existing_entry is not None and isinstance(
        existing_entry.data.get(CONF_REGION_CODE), str
    ):
        data[CONF_REGION_CODE] = existing_entry.data[CONF_REGION_CODE]

    snapshot = api.mqtt_session_snapshot()
    if snapshot is not None:
        data[ENTRY_BOOTSTRAP_MQTT_SESSION] = dict(snapshot)
    return data


def _current_local_mqtt_options(entry: ConfigEntry) -> dict[str, Any]:
    """
    Return a dictionary of local MQTT option values normalized with safe defaults.
    
    The returned mapping contains the following keys with normalized types and fallbacks:
    - CONF_LOCAL_MQTT_ENABLE: bool — whether local MQTT is enabled (defaults to DEFAULT_LOCAL_MQTT_ENABLE).
    - CONF_LOCAL_MQTT_HOST: str — MQTT host (empty string when not set).
    - CONF_LOCAL_MQTT_PORT: int — MQTT port (defaults to DEFAULT_LOCAL_MQTT_PORT).
    - CONF_LOCAL_MQTT_USERNAME: str — MQTT username (empty string when not set).
    - CONF_LOCAL_MQTT_PASSWORD: str — MQTT password (empty string when not set).
    - CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: str — topic filter string trimmed of surrounding whitespace (empty string when not set).
    
    Returns:
        dict[str, Any]: Normalized local-MQTT option values suitable for storing in entry options or using in configuration logic.
    """
    options: Mapping[str, Any] = entry.options
    return {
        CONF_LOCAL_MQTT_ENABLE: bool(
            options.get(CONF_LOCAL_MQTT_ENABLE, DEFAULT_LOCAL_MQTT_ENABLE)
        ),
        CONF_LOCAL_MQTT_HOST: str(options.get(CONF_LOCAL_MQTT_HOST, "") or ""),
        CONF_LOCAL_MQTT_PORT: int(
            options.get(CONF_LOCAL_MQTT_PORT, DEFAULT_LOCAL_MQTT_PORT)
        ),
        CONF_LOCAL_MQTT_USERNAME: str(options.get(CONF_LOCAL_MQTT_USERNAME, "") or ""),
        CONF_LOCAL_MQTT_PASSWORD: str(options.get(CONF_LOCAL_MQTT_PASSWORD, "") or ""),
        CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: str(
            options.get(
                CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
                DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER,
            )
            or ""
        ).strip(),
    }


def _merge_local_mqtt_options(
    user_input: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    """
    Builds a merged local-MQTT options dictionary using user-provided values with fallbacks to the current settings.
    
    For each expected local-MQTT field, the value from `user_input` is used when present; otherwise the value from `current` is used. Returned values are coerced: enable is converted to `bool`; host, username, password, and topic filter are converted to `str` (host and topic are trimmed and empty defaults are `""`); port is converted to `int`.
    
    Parameters:
        user_input (dict[str, Any]): Partial form input containing any local-MQTT fields to update.
        current (dict[str, Any]): Current stored local-MQTT settings used as defaults for omitted fields.
    
    Returns:
        dict[str, Any]: Merged local-MQTT options with keys
            - CONF_LOCAL_MQTT_ENABLE (bool)
            - CONF_LOCAL_MQTT_HOST (str)
            - CONF_LOCAL_MQTT_PORT (int)
            - CONF_LOCAL_MQTT_USERNAME (str)
            - CONF_LOCAL_MQTT_PASSWORD (str)
            - CONF_THIRD_PARTY_MQTT_TOPIC_FILTER (str)
    """
    return {
        CONF_LOCAL_MQTT_ENABLE: bool(
            user_input.get(CONF_LOCAL_MQTT_ENABLE, current[CONF_LOCAL_MQTT_ENABLE])
        ),
        CONF_LOCAL_MQTT_HOST: str(
            user_input.get(CONF_LOCAL_MQTT_HOST, current[CONF_LOCAL_MQTT_HOST]) or ""
        ).strip(),
        CONF_LOCAL_MQTT_PORT: int(
            user_input.get(CONF_LOCAL_MQTT_PORT, current[CONF_LOCAL_MQTT_PORT])
        ),
        CONF_LOCAL_MQTT_USERNAME: str(
            user_input.get(CONF_LOCAL_MQTT_USERNAME, current[CONF_LOCAL_MQTT_USERNAME])
            or ""
        ),
        CONF_LOCAL_MQTT_PASSWORD: str(
            user_input.get(CONF_LOCAL_MQTT_PASSWORD, current[CONF_LOCAL_MQTT_PASSWORD])
            or ""
        ),
        CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: str(
            user_input.get(
                CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
                current[CONF_THIRD_PARTY_MQTT_TOPIC_FILTER],
            )
            or ""
        ).strip(),
    }


def _reconfigure_options(
    entry: ConfigEntry,
    user_input: dict[str, Any],
) -> dict[str, Any]:
    """
    Build the options payload for a reconfigure operation, preserving non-form options and existing local-MQTT settings.
    
    Merges the current entry's options with boolean option values supplied in `user_input`, preserving any keys not present in the form, and then ensures local-MQTT fields are taken from the existing entry rather than from the form.
    
    Parameters:
        entry (ConfigEntry): The config entry being reconfigured; its existing options are used as the base.
        user_input (dict[str, Any]): Submitted form values for option toggles and local-MQTT fields.
    
    Returns:
        dict[str, Any]: The merged options dictionary ready to be saved on the config entry.
    """
    merged = dict(entry.options)
    merged.update(_flow_options(user_input, _current_option_values(entry)))
    merged.update(_current_local_mqtt_options(entry))
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
        Handle the initial options step for the integration.
        
        If form input is provided, merge the submitted boolean option toggles and local MQTT fields with the current entry values and create the options entry. If no input is provided, present the options form populated with the current boolean option defaults and current local MQTT settings.
        
        Parameters:
            user_input (dict[str, Any] | None): Submitted form values for option toggles and local MQTT fields; may be None when rendering the form.
        
        Returns:
            ConfigFlowResult: A config flow result that either creates the updated options entry or displays the options form.
        """
        current_options = _current_option_values(self.config_entry)
        current_local_mqtt = _current_local_mqtt_options(self.config_entry)
        if user_input is not None:
            merged = _flow_options(user_input, current_options)
            merged.update(_merge_local_mqtt_options(user_input, current_local_mqtt))
            return self.async_create_entry(title="", data=merged)

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
        current_enable_week_statistics = current_options[CONF_ENABLE_WEEK_STATISTICS]
        current_enable_month_statistics = current_options[CONF_ENABLE_MONTH_STATISTICS]
        current_enable_year_statistics = current_options[CONF_ENABLE_YEAR_STATISTICS]
        current_enable_derived_home_fallback = current_options[
            CONF_ENABLE_DERIVED_HOME_ENERGY_FALLBACK
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
                CONF_ENABLE_WEEK_STATISTICS,
                default=current_enable_week_statistics,
            ): bool,
            vol.Optional(
                CONF_ENABLE_MONTH_STATISTICS,
                default=current_enable_month_statistics,
            ): bool,
            vol.Optional(
                CONF_ENABLE_YEAR_STATISTICS,
                default=current_enable_year_statistics,
            ): bool,
            vol.Optional(
                CONF_ENABLE_DERIVED_HOME_ENERGY_FALLBACK,
                default=current_enable_derived_home_fallback,
            ): bool,
            vol.Optional(
                CONF_LOCAL_MQTT_ENABLE,
                default=current_local_mqtt[CONF_LOCAL_MQTT_ENABLE],
            ): bool,
            vol.Optional(
                CONF_LOCAL_MQTT_HOST,
                default=current_local_mqtt[CONF_LOCAL_MQTT_HOST],
            ): str,
            vol.Optional(
                CONF_LOCAL_MQTT_PORT,
                default=current_local_mqtt[CONF_LOCAL_MQTT_PORT],
            ): vol.All(int, vol.Range(min=1, max=65535)),
            vol.Optional(
                CONF_LOCAL_MQTT_USERNAME,
                default=current_local_mqtt[CONF_LOCAL_MQTT_USERNAME],
            ): str,
            vol.Optional(
                CONF_LOCAL_MQTT_PASSWORD,
                default=current_local_mqtt[CONF_LOCAL_MQTT_PASSWORD],
            ): str,
            vol.Optional(
                CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
                default=current_local_mqtt[CONF_THIRD_PARTY_MQTT_TOPIC_FILTER],
            ): str,
        })
        return self.async_show_form(step_id=FLOW_STEP_INIT, data_schema=schema)


class JackeryConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Jackery SolarVault config flow."""

    VERSION = 1

    @callback
    def _async_abort_duplicate_discovery(self) -> ConfigFlowResult | None:
        """
        Abort redundant discovery/re-discovery flows before prompting.
        
        Checks for an existing configured entry and for other in-progress flows. If a configured entry exists, aborts with reason "already_configured". If another flow is in progress, aborts with reason "already_in_progress". Returns the abort result when a duplicate is found, otherwise None.
        
        Returns:
            ConfigFlowResult | None: An abort result when a duplicate discovery or in-progress flow is detected, `None` otherwise.
        """
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        if any(
            progress.get("flow_id") != self.flow_id
            for progress in self._async_in_progress()
        ):
            return self.async_abort(reason="already_in_progress")
        return None

    async def async_step_bluetooth(self, discovery_info: Any) -> ConfigFlowResult:  # noqa: ANN401
        """
        Route a Bluetooth discovery into the user configuration flow, aborting if the integration is already configured or another discovery flow is in progress.
        
        Returns:
            ConfigFlowResult: An abort result when the discovery is ignored, otherwise the result from the user step.
        """
        if (abort_result := self._async_abort_duplicate_discovery()) is not None:
            return abort_result
        return await self.async_step_user()

    async def async_step_dhcp(self, discovery_info: Any) -> ConfigFlowResult:  # noqa: ANN401
        """
        Handle DHCP discovery by delegating to the user step or aborting duplicate discovery.
        
        Parameters:
            discovery_info (Any): DHCP discovery information provided by Home Assistant.
        
        Returns:
            ConfigFlowResult: Either an abort result when the discovery is duplicate or the result of proceeding to the user step.
        """
        if (abort_result := self._async_abort_duplicate_discovery()) is not None:
            return abort_result
        return await self.async_step_user()

    async def async_step_mqtt(self, discovery_info: Any) -> ConfigFlowResult:  # noqa: ANN401
        """
        Handle a discovered MQTT device by aborting duplicate discovery flows or continuing to the user step.
        
        Aborts if there is an existing configured entry or another in-progress flow for this integration; otherwise delegates to the user configuration step.
        
        Returns:
            ConfigFlowResult: An abort result when the discovery is a duplicate, or the result produced by the user step.
        """
        if (abort_result := self._async_abort_duplicate_discovery()) is not None:
            return abort_result
        return await self.async_step_user()

    async def async_step_zeroconf(self, discovery_info: Any) -> ConfigFlowResult:  # noqa: ANN401
        """
        Handle a Zeroconf discovery event by aborting redundant flows or continuing to the user setup step.
        
        Parameters:
            discovery_info (Any): Discovery payload provided by Zeroconf; unused by this step except for protocol compatibility.
        
        Returns:
            ConfigFlowResult: An abort result if the discovery is duplicate or already in progress, otherwise the result of the user setup step.
        """
        if (abort_result := self._async_abort_duplicate_discovery()) is not None:
            return abort_result
        return await self.async_step_user()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """
        Handle the initial user-driven configuration step for the integration.
        
        Validates and normalizes the provided username, prevents duplicate configuration, attempts to authenticate against the Jackery API, and on success creates a new config entry populated with credentials and API-derived bootstrap data and default options. If validation or authentication fails, returns the user form populated with appropriate error messages.
        
        Parameters:
        	user_input (dict[str, Any] | None): Form data submitted by the user; expected keys include `CONF_USERNAME` and `CONF_PASSWORD`.
        
        Returns:
        	ConfigFlowResult: A form to display (possibly with errors) or a created config entry on successful authentication.
        """
        errors: dict[str, str] = {}

        if user_input is not None:
            account = _normalize_account(user_input.get(CONF_USERNAME))
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
                    data=_entry_data_from_api_login(
                        account,
                        user_input[CONF_PASSWORD],
                        api,
                    ),
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
        Handle reconfiguration of an existing config entry by validating credentials and updating stored entry data and options.
        
        Validates that the normalized account in `user_input` matches the entry that triggered the reconfigure flow, attempts to authenticate with the Jackery API using the provided password (preserving existing MQTT/bootstrap metadata from the entry when constructing the API client), and on successful authentication updates the entry data and options and aborts the flow with a success reason. If authentication or connection fails, the form is re-displayed with appropriate errors; if the account does not match the entry, the flow is aborted.
        
        Parameters:
            user_input (dict[str, Any] | None): Form data submitted by the user; when `None` the reconfigure form is shown.
        
        Returns:
            ConfigFlowResult: The next result for the config flow (form, abort, or update-and-abort on success).
        """
        try:
            entry = self._get_reconfigure_entry()
        except (KeyError, RuntimeError):
            return self.async_abort(reason=FLOW_ABORT_RECONFIGURE_ENTRY_MISSING)

        errors: dict[str, str] = {}

        if user_input is not None:
            account = _normalize_account(user_input.get(CONF_USERNAME))
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
                        data_updates=_entry_data_from_api_login(
                            account,
                            user_input[CONF_PASSWORD],
                            api,
                            entry,
                        ),
                        options=_reconfigure_options(entry, user_input),
                        reason=FLOW_ABORT_RECONFIGURE_SUCCESSFUL,
                    )

        current_options = _current_option_values(entry)
        stored_username = _entry_text(entry, CONF_USERNAME)
        schema = vol.Schema({
            vol.Required(CONF_USERNAME, default=stored_username): vol.All(
                str, vol.Length(min=1)
            ),
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
                CONF_ENABLE_WEEK_STATISTICS,
                default=current_options[CONF_ENABLE_WEEK_STATISTICS],
            ): bool,
            vol.Optional(
                CONF_ENABLE_MONTH_STATISTICS,
                default=current_options[CONF_ENABLE_MONTH_STATISTICS],
            ): bool,
            vol.Optional(
                CONF_ENABLE_YEAR_STATISTICS,
                default=current_options[CONF_ENABLE_YEAR_STATISTICS],
            ): bool,
            vol.Optional(
                CONF_ENABLE_DERIVED_HOME_ENERGY_FALLBACK,
                default=current_options[CONF_ENABLE_DERIVED_HOME_ENERGY_FALLBACK],
            ): bool,
        })
        return self.async_show_form(
            step_id=FLOW_STEP_RECONFIGURE,
            data_schema=schema,
            description_placeholders={
                "username": stored_username,
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
        Prompt for the account's current password and validate it with Jackery's API.
        
        Aborts with FLOW_ABORT_REAUTH_ENTRY_MISSING if the reauthentication target entry or its stored username cannot be retrieved. On successful authentication, updates the entry data with API-derived bootstrap fields and aborts with FLOW_ABORT_REAUTH_SUCCESSFUL. If authentication fails, re-displays the password form with appropriate error messages.
        
        Returns:
            ConfigFlowResult: a form to collect a password, an abort result, or an update-and-abort result after successful reauthentication.
        """
        try:
            entry = self._get_reauth_entry()
        except (KeyError, RuntimeError):
            return self.async_abort(reason=FLOW_ABORT_REAUTH_ENTRY_MISSING)
        errors: dict[str, str] = {}
        stored_username = _entry_text(entry, CONF_USERNAME)
        if not stored_username:
            return self.async_abort(reason=FLOW_ABORT_REAUTH_ENTRY_MISSING)

        if user_input is not None:
            session = async_get_clientsession(self.hass)
            api = JackeryApi(
                session=session,
                account=stored_username,
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
                    data_updates=_entry_data_from_api_login(
                        stored_username,
                        user_input[CONF_PASSWORD],
                        api,
                        entry,
                    ),
                    reason=FLOW_ABORT_REAUTH_SUCCESSFUL,
                )

        return self.async_show_form(
            step_id=FLOW_STEP_REAUTH_CONFIRM,
            data_schema=vol.Schema({
                vol.Required(CONF_PASSWORD): vol.All(str, vol.Length(min=1))
            }),
            description_placeholders={
                "username": stored_username,
            },
            errors=errors,
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> JackeryOptionsFlow:
        """Return the options flow handler for this entry."""
        return JackeryOptionsFlow()
