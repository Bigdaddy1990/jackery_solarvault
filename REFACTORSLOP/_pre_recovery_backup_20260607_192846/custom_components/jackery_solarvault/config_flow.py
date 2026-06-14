"""Config flow for Jackery SolarVault."""

from collections.abc import Mapping  # noqa: I001, TC003
import logging
from typing import Any

from homeassistant.config_entries import (
    ConfigEntry,  # noqa: TC001
    ConfigFlow,
    ConfigFlowResult,  # noqa: TC001
    OptionsFlow,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

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
from .util import config_entry_bool_option

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
    CONF_ENABLE_DERIVED_HOME_ENERGY_FALLBACK: DEFAULT_ENABLE_DERIVED_HOME_ENERGY_FALLBACK,
}


def _normalize_account(value: Any) -> str:  # noqa: ANN401
    """Normalize user-facing account identifiers before auth and unique IDs."""
    return value.strip() if isinstance(value, str) else ""


def _entry_text(entry: ConfigEntry, key: str) -> str:
    """Return stored config-entry text without stringifying missing values."""
    value = entry.data.get(key)
    return value if isinstance(value, str) else ""


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


def _current_local_mqtt_options(entry: ConfigEntry) -> dict[str, Any]:
    """Return current local-MQTT options with safe defaults."""
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
    }


def _merge_local_mqtt_options(
    user_input: dict[str, Any], current: dict[str, Any]
) -> dict[str, Any]:
    """Merge user input with current local-MQTT options, preserving missing keys."""
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
        """Step init."""
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
        })
        return self.async_show_form(step_id=FLOW_STEP_INIT, data_schema=schema)


class JackeryConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Jackery SolarVault config flow."""

    VERSION = 1

    async def async_step_bluetooth(self, discovery_info: Any) -> ConfigFlowResult:  # noqa: ANN401
        """Start account setup from a local BLE discovery signal."""
        self.context["title_placeholders"] = {"name": "Jackery SolarVault"}
        return await self.async_step_user()

    async def async_step_dhcp(self, discovery_info: Any) -> ConfigFlowResult:  # noqa: ANN401
        """Start account setup from a DHCP discovery signal."""
        self.context["title_placeholders"] = {"name": "Jackery SolarVault"}
        return await self.async_step_user()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Handle the initial user-driven config flow step."""
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
        """Handle a user-initiated reconfigure of an existing entry.

        HA's reconfigure flow lets the user change credentials and toggle
        the calculated-sensor options without removing the entry. The
        normalized account from user input must match the entry that
        triggered the flow; otherwise we abort to keep unique-id semantics
        stable across the reconfigure round-trip.
        """
        try:
            entry = self._get_reconfigure_entry()
        except KeyError, RuntimeError:
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
        """Prompt the user for a fresh password and re-test against Jackery."""
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
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
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
