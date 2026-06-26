"""Config flow for Jackery SolarVault."""

from collections.abc import Mapping
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import JackeryApi, JackeryAuthError, JackeryError
from .client.mqtt.mqtt_session_cache import async_save_mqtt_session
from .const import (
    CONF_CREATE_CALCULATED_POWER_SENSORS,
    CONF_CREATE_SAVINGS_DETAIL_SENSORS,
    CONF_CREATE_SMART_METER_DERIVED_SENSORS,
    CONF_ENABLE_BLE_TRANSPORT,
    CONF_ENABLE_BLE_WRITES,
    CONF_ENABLE_DERIVED_HOME_ENERGY_FALLBACK,
    CONF_ENABLE_MONTH_STATISTICS,
    CONF_ENABLE_UNREDACTED_DIAGNOSTICS,
    CONF_ENABLE_WEEK_STATISTICS,
    CONF_ENABLE_YEAR_STATISTICS,
    CONF_LOCAL_MQTT_ENABLE,
    CONF_LOCAL_MQTT_HOST,
    CONF_LOCAL_MQTT_PASSWORD,
    CONF_LOCAL_MQTT_PORT,
    CONF_LOCAL_MQTT_TOPIC,
    CONF_LOCAL_MQTT_USERNAME,
    CONF_MQTT_MAC_ID,
    CONF_REGION_CODE,
    CONF_THIRD_PARTY_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_PORT,
    CONF_THIRD_PARTY_MQTT_TOKEN,
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
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
    DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER,
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
    _OPTION_DEFAULTS,  # noqa: RUF100, W191
    _RECONFIGURE_IN_PLACE_OPTION_KEYS,
    config_entry_bool_option,
    config_entry_int_option,
    config_entry_str_option,
)


)
from .util import (

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.components.bluetooth import BluetoothServiceInfoBleak
    from homeassistant.config_entries import ConfigEntry, ConfigFlowResult
    from homeassistant.helpers.service_info.dhcp import DhcpServiceInfo
    from homeassistant.helpers.service_info.mqtt import MqttServiceInfo
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo


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
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER,
}

_INT_OPTION_DEFAULTS: dict[str, int] = {
    CONF_THIRD_PARTY_MQTT_PORT: DEFAULT_THIRD_PARTY_MQTT_PORT,
}


def _normalize_account(value: str) -> str:
    """Normalize an account identifier by stripping leading and trailing whitespace.

    Returns:
        The account identifier with leading and trailing whitespace removed.
    """
    return value.strip()


def _current_option_values(entry: ConfigEntry) -> dict[str, Any]:
    """Resolve the current option values for a configuration entry.

    For each known option key (grouped by boolean, string, and integer types), the value is taken from the entry's stored options, falling back to any legacy setup-data value for that key and then to the type-specific default.

    Parameters:
        entry (ConfigEntry): Configuration entry to read option and legacy setup-data values from.

    Returns:
        dict[str, Any]: Mapping of option keys to their resolved current values.
    """  # noqa: E501
    return value.strip() if isinstance(value, str) else ""


    values: dict[str, Any] = {}  # noqa: E303
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
    """Produce a complete options dictionary by taking values from `user_input` when present, otherwise preserving `current_options`, and finally falling back to the integration's typed defaults.

    Parameters:
        user_input (dict[str, Any]): Option values provided by the user; may omit keys.
        current_options (dict[str, Any] | None): Existing stored option values to preserve when `user_input` omits a key.

    Returns:
        dict[str, Any]: A merged options dictionary containing every known option key with its resolved value.
        value (`True` or `False`).
    """  # noqa: E501
    current = current_options or {}
    keys = option_keys or frozenset(_OPTION_DEFAULTS)
    return {
        key: user_input.get(key, current.get(key, default))
        for key, default in _OPTION_DEFAULTS.items()
        if key in keys
    }


def _entry_data_from_api_login(
    account: str,
    password: str,
    api: JackeryApi,
    existing_entry: ConfigEntry | None = None,
    merged: dict[str, Any] = {}
    for defaults in (  # noqa: E113
        _BOOL_OPTION_DEFAULTS,
        _STR_OPTION_DEFAULTS,
        _INT_OPTION_DEFAULTS,
    ):
        for key, default in defaults.items():
            merged[key] = user_input.get(key, current.get(key, default))
    region_code = api.region_code
    if region_code:
        data[CONF_REGION_CODE] = region_code
    elif existing_entry is not None and isinstance(
        existing_entry.data.get(CONF_REGION_CODE),
        str,
    ):
        data[CONF_REGION_CODE] = existing_entry.data[CONF_REGION_CODE]

    snapshot = api.mqtt_session_snapshot()
    if snapshot is not None:
        data[ENTRY_BOOTSTRAP_MQTT_SESSION] = dict(snapshot)
    return data


def _coerce_local_mqtt_port(value: object) -> int:
    """Return a safe local MQTT port value from stored options or form input."""
    if value in {None, ""}:
        return DEFAULT_LOCAL_MQTT_PORT
    try:
        return int(cast("Any", value))
    except TypeError as err:
        _LOGGER.debug(
            "Local MQTT port %r has an unusable type; using default %d: %s",
            value,
            DEFAULT_LOCAL_MQTT_PORT,
            err,
        )
        return DEFAULT_LOCAL_MQTT_PORT
    except ValueError as err:
        _LOGGER.debug(
            "Local MQTT port %r is not a valid integer; using default %d: %s",
            value,
            DEFAULT_LOCAL_MQTT_PORT,
            err,
        )
        return DEFAULT_LOCAL_MQTT_PORT


def _current_local_mqtt_options(entry: ConfigEntry) -> dict[str, Any]:
    """Normalize and return local MQTT option values from a ConfigEntry.

    The returned mapping contains the following keys with normalized types and safe
    defaults:
    - CONF_LOCAL_MQTT_ENABLE: bool — whether local MQTT is enabled (defaults to
    DEFAULT_LOCAL_MQTT_ENABLE)
    - CONF_LOCAL_MQTT_HOST: str — MQTT host (empty string when not set)
    - CONF_LOCAL_MQTT_PORT: int — MQTT port (defaults to DEFAULT_LOCAL_MQTT_PORT)
    - CONF_LOCAL_MQTT_USERNAME: str — MQTT username (empty string when not set)
    - CONF_LOCAL_MQTT_PASSWORD: str — MQTT password (empty string when not set)
    - CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: str — topic filter trimmed of surrounding
    whitespace (empty string when not set)

    Returns:
        dict[str, Any]: Normalized local MQTT option values suitable for storing in
        entry options or using in configuration logic.
    """
    options: Mapping[str, Any] = entry.options
    return {
        CONF_LOCAL_MQTT_ENABLE: bool(
            # Explicit user toggle wins; the legacy app-synced flag is only a
            # fallback when the local toggle was never set. Mirrors the
            # coordinator so a disable actually sticks and the form reflects it.
            options.get(CONF_LOCAL_MQTT_ENABLE)
            if CONF_LOCAL_MQTT_ENABLE in options
            else options.get(CONF_THIRD_PARTY_MQTT_ENABLE) or DEFAULT_LOCAL_MQTT_ENABLE,
        ),
        CONF_LOCAL_MQTT_HOST: str(
            options.get(CONF_LOCAL_MQTT_HOST)
            or options.get(CONF_THIRD_PARTY_MQTT_IP)
            or "",
        ).strip(),
        CONF_LOCAL_MQTT_PORT: _coerce_local_mqtt_port(
            options.get(CONF_LOCAL_MQTT_PORT)
            or options.get(CONF_THIRD_PARTY_MQTT_PORT),
        ),
        CONF_LOCAL_MQTT_USERNAME: str(
            options.get(CONF_LOCAL_MQTT_USERNAME)
            or options.get(CONF_THIRD_PARTY_MQTT_USERNAME)
            or "",
        ).strip(),
        CONF_LOCAL_MQTT_PASSWORD: str(
            options.get(CONF_LOCAL_MQTT_PASSWORD)
            or options.get(CONF_THIRD_PARTY_MQTT_PASSWORD)
            or "",
        ),
        CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: str(
            options.get(CONF_THIRD_PARTY_MQTT_TOPIC_FILTER)
            or options.get(CONF_LOCAL_MQTT_TOPIC)
            or DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER,
        ).strip(),
    }


def _merge_local_mqtt_options(
    user_input: dict[str, Any],
    current: dict[str, Any],
) -> dict[str, Any]:
    """Builds a merged local-MQTT options dictionary using user-provided values with.

    fallbacks to the current settings.

    For each expected local-MQTT field, the value from `user_input` is used when
    present; otherwise the value from `current` is used. Returned values are coerced:
    enable is converted to `bool`; host, username, password, and topic filter are
    converted to `str` (host and topic are trimmed and empty defaults are `""`); port
    is converted to `int`.

    Parameters:
        user_input (dict[str, Any]): Partial form input containing any local-MQTT
        fields to update.
        current (dict[str, Any]): Current stored local-MQTT settings used as defaults
        for omitted fields.

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
            user_input.get(CONF_LOCAL_MQTT_ENABLE, current[CONF_LOCAL_MQTT_ENABLE]),
        ),
        CONF_LOCAL_MQTT_HOST: str(
            user_input.get(CONF_LOCAL_MQTT_HOST, current[CONF_LOCAL_MQTT_HOST]) or "",
        ).strip(),
        CONF_LOCAL_MQTT_PORT: _coerce_local_mqtt_port(
            user_input.get(CONF_LOCAL_MQTT_PORT, current[CONF_LOCAL_MQTT_PORT]),
        ),
        CONF_LOCAL_MQTT_USERNAME: str(
            user_input.get(CONF_LOCAL_MQTT_USERNAME, current[CONF_LOCAL_MQTT_USERNAME])
            or "",
        ),
        CONF_LOCAL_MQTT_PASSWORD: str(
            user_input.get(CONF_LOCAL_MQTT_PASSWORD, current[CONF_LOCAL_MQTT_PASSWORD])
            or "",
        ),
        CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: str(
            user_input.get(
                CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
                current[CONF_THIRD_PARTY_MQTT_TOPIC_FILTER],
            )
            or "",
        ).strip(),
    }


def _local_mqtt_option_schema(
    current: dict[str, Any],
) -> dict[vol.Optional, object]:
    """Build voluptuous Optional schema entries for the six local MQTT option fields.

    The returned mapping contains vol.Optional descriptors for:
    - CONF_LOCAL_MQTT_ENABLE (bool)
    - CONF_LOCAL_MQTT_HOST (str)
    - CONF_LOCAL_MQTT_PORT (int, 1-65535)
    - CONF_LOCAL_MQTT_USERNAME (str)
    - CONF_LOCAL_MQTT_PASSWORD (str)
    - CONF_THIRD_PARTY_MQTT_TOPIC_FILTER (str)

    Parameters:
        current: Normalized local-MQTT options (e.g. from
        `_current_local_mqtt_options`) used as form defaults.

    Returns:
        dict[vol.Optional, object]: Mapping of vol.Optional keys to their voluptuous
        validators suitable for inclusion in a vol.Schema.
    """
    return {
        vol.Optional(
            CONF_LOCAL_MQTT_ENABLE,
            default=current[CONF_LOCAL_MQTT_ENABLE],
        ): bool,
        vol.Optional(
            CONF_LOCAL_MQTT_HOST,
            default=current[CONF_LOCAL_MQTT_HOST],
        ): str,
        vol.Optional(
            CONF_LOCAL_MQTT_PORT,
            default=current[CONF_LOCAL_MQTT_PORT],
        ): vol.All(int, vol.Range(min=1, max=65535)),
        vol.Optional(
            CONF_LOCAL_MQTT_USERNAME,
            default=current[CONF_LOCAL_MQTT_USERNAME],
        ): str,
        vol.Optional(
            CONF_LOCAL_MQTT_PASSWORD,
            default=current[CONF_LOCAL_MQTT_PASSWORD],
        ): str,
        vol.Optional(
            CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
            default=current[CONF_THIRD_PARTY_MQTT_TOPIC_FILTER],
        ): str,
    }


def _reconfigure_options(
    entry: ConfigEntry,
    user_input: dict[str, Any],
) -> dict[str, Any]:
    """Compose the options mapping to save when reconfiguring an existing config entry.

    Preserves any option keys not exposed by the reconfigure form, and applies
    submitted in-place option toggles and local-MQTT-related fields from `user_input`.
    Entity-creating options are intentionally excluded so credential maintenance never
    triggers a live-data-pausing reload.

    Returns:
        dict[str, Any]: The merged options dictionary ready to be stored on the config
        entry.
    """
    current_local_mqtt = _current_local_mqtt_options(entry)
    merged = dict(entry.options)
    current_options = _current_option_values(entry)
    merged.update(
        _flow_options(
            user_input,
            current_options,
            _RECONFIGURE_IN_PLACE_OPTION_KEYS,
        ),
    )
    merged.update(_merge_local_mqtt_options(user_input, current_local_mqtt))
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
        """Present the options form for the integration or create an options entry from submitted values.

        When `user_input` is provided, merge the submitted values with the current stored options and create an options entry. When `user_input` is None, show the options form populated with defaults from the current entry options (BLE, sensor-creation, diagnostics, and third-party MQTT settings).

        Parameters:
            user_input (dict[str, Any] | None): Submitted form values, or None to render the form.

        Returns:
            ConfigFlowResult: The created options entry result, or a form result to display to the user.
        """  # noqa: E501
        current_options = _current_option_values(self.config_entry)
        current_local_mqtt = _current_local_mqtt_options(self.config_entry)
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
        current_enable_week_statistics = current_options[CONF_ENABLE_WEEK_STATISTICS]
        current_enable_month_statistics = current_options[CONF_ENABLE_MONTH_STATISTICS]
        current_enable_year_statistics = current_options[CONF_ENABLE_YEAR_STATISTICS]
        current_enable_derived_home_fallback = current_options[
            CONF_ENABLE_DERIVED_HOME_ENERGY_FALLBACK
        ]
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
                default=current_options[CONF_ENABLE_BLE_WRITES],
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
            vol.Optional(
                CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
                default=current_options[CONF_THIRD_PARTY_MQTT_TOPIC_FILTER],
            ): str,
        })
        return self.async_show_form(step_id=FLOW_STEP_INIT, data_schema=schema)


class JackeryConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Jackery SolarVault config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial user-driven configuration step and authenticate the provided Jackery account.

        Validates and normalizes the submitted username, prevents creating a duplicate entry for the same account, attempts to authenticate with the Jackery service using the provided credentials, and on success creates the configuration entry with the supplied credentials and merged initial options. On validation or authentication failure, returns the form populated with appropriate error messages.

        Parameters:
            user_input (dict[str, Any] | None): Form input submitted by the user. Expected keys include `CONF_USERNAME` and `CONF_PASSWORD`, and may include optional integration option fields.

        Returns:
            ConfigFlowResult: A flow result that either shows the user form with errors or creates the new configuration entry on successful authentication.
            duplicate discovery or in-progress flow is detected, `None` otherwise.
        """  # noqa: E501
        if self._async_current_entries():
            return self.async_abort(reason="already_configured")
        if any(
            progress.get("flow_id") != self.flow_id
            for progress in self._async_in_progress()
        ):
            return self.async_abort(reason="already_in_progress")
        return None

    async def async_step_bluetooth(
        self,
        discovery_info: BluetoothServiceInfoBleak,
    ) -> ConfigFlowResult:
        """Route a Bluetooth discovery into the user configuration flow, aborting if.

        the integration is already configured or another discovery flow is in progress.

        Returns:
            ConfigFlowResult: An abort result when the discovery is ignored, otherwise
            the result from the user step.
        """
        self.context["title_placeholders"] = {"name": "Jackery SolarVault"}
        if (abort_result := self._async_abort_duplicate_discovery()) is not None:
            return abort_result
        return await self.async_step_user()

    async def async_step_dhcp(
        self,
        discovery_info: DhcpServiceInfo,
    ) -> ConfigFlowResult:
        """Start account setup from a DHCP discovery signal.

        Parameters:
            discovery_info (DhcpServiceInfo): DHCP discovery information provided by
            Home Assistant.

        Returns:
            ConfigFlowResult: An abort result when the discovery is duplicate or the
            result of proceeding to the user step.
        """
        self.context["title_placeholders"] = {"name": "Jackery SolarVault"}
        if (abort_result := self._async_abort_duplicate_discovery()) is not None:
            return abort_result
        return await self.async_step_user()

    async def async_step_mqtt(
        self,
        discovery_info: MqttServiceInfo,
    ) -> ConfigFlowResult:
        """Handle a discovered MQTT device and route to the user configuration step if.

        not a duplicate.

        Aborts when a configured entry already exists or another in-progress flow for
        this integration is present; otherwise delegates to `async_step_user()`.

        Returns:
            ConfigFlowResult: An abort result when the discovery is a duplicate, or the
            result returned by `async_step_user()`.
        """
        if (abort_result := self._async_abort_duplicate_discovery()) is not None:
            return abort_result
        return await self.async_step_user()

    async def async_step_zeroconf(
        self,
        discovery_info: ZeroconfServiceInfo,
    ) -> ConfigFlowResult:
        """Handle a Zeroconf discovery event by aborting duplicate or.

        already-in-progress flows, otherwise continue to the user setup step.

        Parameters:
            discovery_info (ZeroconfServiceInfo): Discovery payload from Zeroconf;
            unused by this step.

        Returns:
            ConfigFlowResult: An abort result if the discovery is a duplicate or
            another flow is in progress, otherwise the result returned by the user
            setup step.
        """
        if (abort_result := self._async_abort_duplicate_discovery()) is not None:
            return abort_result
        return await self.async_step_user()

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle the initial user-driven config flow step.

        Validate and normalize the submitted username, prevent duplicate configuration,
        authenticate against the Jackery API, and on success create a config entry that
        contains credentials, API-derived bootstrap data, and resolved option values.
        If validation or authentication fails, present the user form populated with
        appropriate error messages.

        Parameters:
            user_input (dict[str, Any] | None): Form input containing at least
                CONF_USERNAME and CONF_PASSWORD; may include option toggles.

        Returns:
            ConfigFlowResult: Either the user form (possibly with errors) or a created
            config entry.
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
            if self._async_current_entries():
                return self.async_abort(reason="already_configured")
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
        """Reconfigure an existing config entry by validating provided account credentials and updating stored username, password, and options.

        Validates that the submitted username matches the entry being reconfigured, verifies credentials with the Jackery service, and on success updates and reloads the entry. If input is missing or invalid, presents the reconfigure form prefilled with current option defaults. Aborts if the reconfigure target is missing or the provided account does not match the entry.
        reconfigured, attempts to authenticate with the Jackery API (preserving
        existing MQTT/bootstrap metadata from the entry), and on successful
        authentication updates the entry's stored data and options and ends the flow
        with a successful abort reason. If authentication fails or a connection error
        occurs, the reconfigure form is re-displayed with error messages; if the
        provided account does not match the entry, the flow is aborted.

        Parameters:
            user_input (dict[str, Any] | None): Form data submitted by the user. When
            `None`, the reconfigure form is shown.

        Returns:
            A ConfigFlowResult that shows the reconfigure form with any errors, aborts with a specific reason, or updates and reloads the entry on successful reconfiguration.
            validation errors, aborts the flow for account mismatches or missing entry,
            or updates the entry and aborts with a success reason.
        """  # noqa: E501
        try:
            entry = self._get_reconfigure_entry()
        except KeyError, RuntimeError:
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
                    snapshot = api.mqtt_session_snapshot()
                    if snapshot is not None:
                        await async_save_mqtt_session(
                            self.hass,
                            entry.entry_id,
                            **snapshot,
                        )
                    self.hass.config_entries.async_update_entry(
                        entry,
                        data={**entry.data, **data_updates},
                        options=_reconfigure_options(entry, user_input),
                    )
                    return self.async_abort(reason=FLOW_ABORT_RECONFIGURE_SUCCESSFUL)

        current_options = _current_option_values(entry)
        current_local_mqtt = _current_local_mqtt_options(entry)
        stored_username = _entry_text(entry, CONF_USERNAME)
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
            ): bool,
            vol.Optional(
                CONF_ENABLE_DERIVED_HOME_ENERGY_FALLBACK,
                default=current_options[CONF_ENABLE_DERIVED_HOME_ENERGY_FALLBACK],
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
            vol.Optional(
                CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
                default=current_options[CONF_THIRD_PARTY_MQTT_TOPIC_FILTER],
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
        """Prompt for the account's current password and validate it against Jackery to complete reauthentication.

        Parameters:
            user_input (dict[str, Any] | None): Form data containing `CONF_PASSWORD` when submitted.

        Returns:
            ConfigFlowResult: The next flow result (shows the password form on error or missing input, aborts and updates the entry on successful reauthentication).
            update-and-abort result after successful reauthentication.
        """  # noqa: E501
        try:
            entry = self._get_reauth_entry()
        except KeyError, RuntimeError:
            return self.async_abort(reason=FLOW_ABORT_REAUTH_ENTRY_MISSING)
        errors: dict[str, str] = {}
        stored_username = _entry_text(entry, CONF_USERNAME)
        if not stored_username:
            return self.async_abort(reason=FLOW_ABORT_REAUTH_ENTRY_MISSING)

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
                snapshot = api.mqtt_session_snapshot()
                if snapshot is not None:
                    await async_save_mqtt_session(
                        self.hass,
                        entry.entry_id,
                        **snapshot,
                    )
                self.hass.config_entries.async_update_entry(
                    entry,
                    data={**entry.data, **data_updates},
                )
                # Reauth intentionally avoids immediate reloads because boot-time
                # reconnect runs in the background.
                return self.async_abort(reason=FLOW_ABORT_REAUTH_SUCCESSFUL)

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
