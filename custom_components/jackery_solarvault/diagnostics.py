"""Diagnostics support for Jackery SolarVault."""

import logging
from typing import TYPE_CHECKING, Any, cast

from homeassistant.components.diagnostics import async_redact_data

# _local_mqtt_client moved to coordinator property
from .const import (
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_PORT,
    CONF_THIRD_PARTY_MQTT_USERNAME,
    DEFAULT_THIRD_PARTY_MQTT_PORT,
    DIAGNOSTICS_SCHEMA_VERSION,
    DOMAIN,
    LOCAL_MQTT_RUNTIME_KEY,
    REDACTED_VALUE,
)
from .coordinator import JackerySolarVaultCoordinator
from .util import (
    active_redact_keys,
    config_entry_int_option,
    config_entry_str_option,
    local_mqtt_opt_in,
    redacted_json_safe_payload,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

    from . import JackeryConfigEntry
    from .client.local_mqtt import JackeryLocalMqttClient

_LOGGER = logging.getLogger(__name__)


def _redacted_payload_map(
    payloads: Mapping[Any, Any],
    prefix: str,
    redact_keys: frozenset[str],
) -> dict[str, Any]:
    """Build a deterministic labeled mapping of redacted payloads.

    Replace original mapping keys with stable generated labels.

    Payloads are processed in a stable order (sorted by the string form of the original keys). Each value is redacted using the provided `redact_keys`; values that are not mappings are wrapped as `{"value": payload}` before redaction.

    Parameters:
        payloads (Mapping[Any, Any]): Mapping whose keys will be replaced by generated
        labels; values are payloads to redact.
        prefix (str): Prefix for generated labels; labels are formatted as "<prefix>_<index>" with index starting at 1.
        redact_keys (frozenset[str]): Field names to redact from each payload.

    Returns:
        dict[str, Any]: Mapping of generated labels to redacted payloads.
    """  # ruff: ignore[line-too-long]
    redacted: dict[str, Any] = {}
    for index, key in enumerate(sorted(payloads, key=str), start=1):
        payload = payloads[key]
        label = f"{prefix}_{index}"
        if isinstance(payload, dict):
            redacted[label] = async_redact_data(payload, redact_keys)
        else:
            redacted[label] = async_redact_data({"value": payload}, redact_keys)
    return redacted


async def async_get_config_entry_diagnostics(  # ruff: ignore[unused-async]  # HA requires async.
    hass: HomeAssistant, entry: JackeryConfigEntry
) -> dict[str, Any]:
    """Build a diagnostics export for the given config entry.

    The returned payload contains redacted copies of the entry's stored data and
    options, a stable mapping of labeled device payloads, and diagnostics from the
    coordinator, API responses, and transports. Redaction is mandatory and a final
    recursive boundary pass protects nested and differently-cased sensitive keys.

    Returns:
        dict[str, Any]: Diagnostics export with keys:
            - `entry_data`: redacted copy of the config entry's stored data.
            - `options`: redacted copy of the config entry's options.
            - `devices`: mapping of stable local device labels to redacted device payloads.
            - `raw_api`: redacted diagnostics including coordinator metadata, API response snapshots, MQTT/local MQTT/BLE diagnostics, and statistics backfill.
    """  # ruff: ignore[line-too-long]
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    redact_keys = active_redact_keys()
    sensitive_sources = (
        dict(entry.data),
        dict(entry.options),
        coordinator.data or {},
        coordinator.api.last_login_response or {},
        coordinator.api.last_system_list_response or {},
        coordinator.api.last_property_responses,
        coordinator.api.last_alarm_response or {},
        coordinator.api.last_statistic_response or {},
        coordinator.api.last_price_response or {},
        coordinator.api.last_price_sources_response or {},
        coordinator.api.last_price_history_config_response or {},
        coordinator.api.last_device_statistic_responses,
        coordinator.api.last_device_period_stat_responses,
        coordinator.api.last_battery_pack_responses,
        coordinator.api.last_ota_responses,
        coordinator.api.last_location_responses,
    )

    devices = _redacted_payload_map(coordinator.data or {}, "device", redact_keys)

    raw = {
        "coordinator": {
            "update_interval_seconds": (
                int(coordinator.configured_update_interval.total_seconds())
            ),
            "coordinator_polling": True,
            "redactions_enforced": True,
        },
        "login_response": async_redact_data(
            coordinator.api.last_login_response or {}, redact_keys
        ),
        "system_list_response": async_redact_data(
            coordinator.api.last_system_list_response or {}, redact_keys
        ),
        "property_responses": _redacted_payload_map(
            coordinator.api.last_property_responses, "property_response", redact_keys
        ),
        "alarm_response": async_redact_data(
            coordinator.api.last_alarm_response or {}, redact_keys
        ),
        "statistic_response": async_redact_data(
            coordinator.api.last_statistic_response or {}, redact_keys
        ),
        "price_response": async_redact_data(
            coordinator.api.last_price_response or {}, redact_keys
        ),
        "price_sources_response": async_redact_data(
            coordinator.api.last_price_sources_response or {}, redact_keys
        ),
        "price_history_config_response": async_redact_data(
            coordinator.api.last_price_history_config_response or {}, redact_keys
        ),
        "device_statistic_responses": _redacted_payload_map(
            coordinator.api.last_device_statistic_responses,
            "device_statistic_response",
            redact_keys,
        ),
        "device_period_stat_responses": _redacted_payload_map(
            coordinator.api.last_device_period_stat_responses,
            "device_period_stat_response",
            redact_keys,
        ),
        "battery_pack_responses": _redacted_payload_map(
            coordinator.api.last_battery_pack_responses,
            "battery_pack_response",
            redact_keys,
        ),
        "ota_responses": _redacted_payload_map(
            coordinator.api.last_ota_responses, "ota_response", redact_keys
        ),
        "location_responses": _redacted_payload_map(
            coordinator.api.last_location_responses, "location_response", redact_keys
        ),
        "mqtt": async_redact_data(
            coordinator.mqtt_diagnostics_snapshot(),
            redact_keys,
        ),
        "polling": async_redact_data(
            coordinator.polling_diagnostics,
            redact_keys,
        ),
        "local_mqtt": async_redact_data(
            _local_mqtt_diagnostics(hass, entry),
            redact_keys,
        ),
        "local_mqtt_device_config": async_redact_data(
            coordinator.local_mqtt_config_diagnostics,
            redact_keys,
        ),
        "ble_transport": _redacted_payload_map(
            coordinator.ble_observations(), "ble_device", redact_keys
        ),
        "statistics_backfill": async_redact_data(
            coordinator.statistics_backfill_diagnostics,
            redact_keys,
        ),
        "endpoint_backoff": async_redact_data(
            coordinator.endpoint_backoff_diagnostics(),
            redact_keys,
        ),
        "app_chart_import": async_redact_data(
            coordinator.app_chart_import_diagnostics(),
            redact_keys,
        ),
    }

    export = {
        "entry_data": async_redact_data(dict(entry.data), redact_keys),
        "options": async_redact_data(dict(entry.options), redact_keys),
        "devices": devices,
        "schema_version": DIAGNOSTICS_SCHEMA_VERSION,
        "rejection_metrics": coordinator.rejection_metrics.as_dict(),
        "raw_api": raw,
    }
    return cast(
        "dict[str, Any]",
        _diagnostic_json_null_free(
            redacted_json_safe_payload(
                export,
                sensitive_sources=sensitive_sources,
            )
        ),
    )


def _diagnostic_json_null_free(value: object) -> object:
    """Return diagnostics JSON without raw null leaves."""
    if value is None:
        return ""
    if isinstance(value, dict):
        return {
            str(key): _diagnostic_json_null_free(item) for key, item in value.items()
        }
    if isinstance(value, list):
        return [_diagnostic_json_null_free(item) for item in value]
    if isinstance(value, tuple):
        return [_diagnostic_json_null_free(item) for item in value]
    return value


def _local_mqtt_diagnostics(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> dict[str, Any]:
    """Build diagnostics for the integration's local MQTT client.

    Indicate explicitly when local MQTT is unavailable.

    Returns:
        dict[str, Any]: ``{"enabled": False, "disabled_reason": ...}`` when no local
        MQTT client is available, otherwise the client's diagnostics snapshot.
    """
    enabled = local_mqtt_opt_in(entry)
    host = config_entry_str_option(entry, CONF_THIRD_PARTY_MQTT_IP, "").strip()
    port = str(
        config_entry_int_option(
            entry,
            CONF_THIRD_PARTY_MQTT_PORT,
            DEFAULT_THIRD_PARTY_MQTT_PORT,
        )
    ).strip()
    username = config_entry_str_option(
        entry, CONF_THIRD_PARTY_MQTT_USERNAME, ""
    ).strip()
    password = config_entry_str_option(
        entry, CONF_THIRD_PARTY_MQTT_PASSWORD, ""
    ).strip()
    diagnostic_host = REDACTED_VALUE if host else ""
    diagnostic_port = REDACTED_VALUE if port else ""

    # Bewusst als ``object`` gehalten: der Typ von ``runtime_data`` verspricht den
    # Coordinator, zur Laufzeit kann er beim fehlgeschlagenen Setup oder waehrend
    # des Teardowns aber fehlen. Ohne die Annotation haelt mypy den Guard fuer
    # unerreichbar und der Diagnose-Pfad "coordinator_not_ready" faellt weg.
    coordinator: object = entry.runtime_data
    if not isinstance(coordinator, JackerySolarVaultCoordinator):
        reason = "coordinator_not_ready"
        return {
            "enabled": False,
            "disabled_reason": reason,
            "configured_local_mqtt": {
                "host": diagnostic_host,
                "port": diagnostic_port,
                "username_set": bool(username),
                "password_set": bool(password),
                "topic_filter": REDACTED_VALUE,
                "effective_topic_filter": REDACTED_VALUE,
            },
        }

    try:
        client = coordinator.local_mqtt_client
    except AttributeError:
        # Diagnostics must remain exportable while a coordinator is only
        # partially initialised (for example during failed startup handling).
        client = None
    if client is None:
        runtime_bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        if isinstance(runtime_bucket, dict):
            client = cast(
                "JackeryLocalMqttClient | None",
                runtime_bucket.get(LOCAL_MQTT_RUNTIME_KEY),
            )
    if client is None:
        if not enabled:
            reason = "bridge_disabled"
        elif not host:
            reason = "missing_broker_host"
        else:
            reason = "client_not_started"
        return {
            "enabled": False,
            "disabled_reason": reason,
            "configured_local_mqtt": {
                "host": diagnostic_host,
                "port": diagnostic_port,
                "username_set": bool(username),
                "password_set": bool(password),
                "topic_filter": REDACTED_VALUE,
                "effective_topic_filter": REDACTED_VALUE,
            },
        }
    return client.diagnostics_snapshot()
