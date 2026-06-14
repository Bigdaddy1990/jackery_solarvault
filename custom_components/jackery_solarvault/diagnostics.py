"""Diagnostics support for Jackery SolarVault."""

import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data

from .client.local_mqtt import _local_mqtt_client
from .const import (
    CONF_LOCAL_MQTT_ENABLE,
    CONF_LOCAL_MQTT_HOST,
    CONF_LOCAL_MQTT_PASSWORD,
    CONF_LOCAL_MQTT_PORT,
    CONF_LOCAL_MQTT_USERNAME,
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
    DEFAULT_LOCAL_MQTT_ENABLE,
    DEFAULT_LOCAL_MQTT_PORT,
    DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER,
    REDACT_KEYS as _STATIC_REDACT_KEYS,
)
from .util import (
    active_redact_keys,
    config_entry_bool_option,
    config_entry_str_option,
    dev_mode_redactions_disabled,
    diagnostic_redactions_disabled,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

    from . import JackeryConfigEntry
    from .coordinator import JackerySolarVaultCoordinator

_LOGGER = logging.getLogger(__name__)
_BLOCKED_LOCAL_MQTT_TOPIC_FILTERS = frozenset({"#", "+/#"})

# Kept as an import alias so tests / external callers can still reference the
# static redact-key set when needed. Runtime redaction in this module always
# goes through ``active_redact_keys()`` so the ``JACKERY_DEV_MODE`` env-var
# toggle takes effect without restart.
REDACT_KEYS = _STATIC_REDACT_KEYS


def _redacted_payload_map(
    payloads: Mapping[Any, Any],
    prefix: str,
    redact_keys: frozenset[str],
) -> dict[str, Any]:
    """Produce a deterministic mapping of redacted payloads with original keys replaced by stable generated labels.

    Processes entries in a stable order (sorted by the string form of each key). Each payload is redacted using `redact_keys`; non-mapping payloads are wrapped as `{"value": payload}` before redaction.

    Parameters:
        payloads (Mapping[Any, Any]): Mapping whose keys will be replaced by generated labels; values are payloads to redact.
        prefix (str): Prefix for generated labels; labels are formatted as "<prefix>_<index>" with index starting at 1.
        redact_keys (frozenset[str]): Field names to redact from each payload.

    Returns:
        dict[str, Any]: Mapping of generated labels to redacted payloads.
    """
    redacted: dict[str, Any] = {}
    for index, key in enumerate(sorted(payloads, key=str), start=1):
        payload = payloads[key]
        label = f"{prefix}_{index}"
        if isinstance(payload, dict):
            redacted[label] = async_redact_data(payload, redact_keys)
        else:
            redacted[label] = async_redact_data({"value": payload}, redact_keys)
    return redacted


async def async_get_config_entry_diagnostics(  # noqa: RUF029  # HA awaits this entry point
    hass: HomeAssistant, entry: JackeryConfigEntry
) -> dict[str, Any]:
    """Build a diagnostics export for the given config entry.

    The returned payload contains redacted copies of the entry's stored data and options, a stable mapping of labeled device payloads, and raw diagnostics from the coordinator, API responses, and transports. If diagnostics redactions are disabled for the entry, sensitive fields such as credentials, serial numbers, and `bluetoothKey` may be included unredacted.

    Returns:
        dict[str, Any]: Diagnostics export with keys:
            - `entry_data`: redacted copy of the config entry's stored data.
            - `options`: redacted copy of the config entry's options.
            - `devices`: mapping of stable local device labels to redacted device payloads.
            - `raw_api`: raw diagnostics including coordinator metadata, API response snapshots, MQTT/local MQTT/BLE diagnostics, and statistics backfill (redacted according to the entry's redaction settings).
    """
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    redact_keys = active_redact_keys(entry)
    redactions_disabled = diagnostic_redactions_disabled(entry)
    if redactions_disabled:
        source = (
            "JACKERY_DEV_MODE=1"
            if dev_mode_redactions_disabled()
            else "enable_unredacted_diagnostics option"
        )
        _LOGGER.warning(
            "Jackery diagnostics export is running with %s - "
            "credentials, serial numbers and the bluetoothKey are included "
            "unredacted. Do NOT share this export publicly.",
            source,
        )

    devices = _redacted_payload_map(coordinator.data or {}, "device", redact_keys)

    raw = {
        "coordinator": {
            "update_interval_seconds": (
                int(coordinator.configured_update_interval.total_seconds())
            ),
            "coordinator_polling": True,
            "dev_mode": dev_mode_redactions_disabled(),
            "redactions_disabled": redactions_disabled,
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
            coordinator.mqtt_diagnostics_snapshot(
                redact_topics=not redactions_disabled
            ),
            redact_keys,
        ),
        "local_mqtt": _local_mqtt_diagnostics(
            hass, entry, coordinator, redactions_disabled
        ),
        "ble_transport": _redacted_payload_map(
            coordinator.ble_observations(), "ble_device", redact_keys
        ),
        "statistics_backfill": async_redact_data(
            coordinator.statistics_backfill_diagnostics,
            redact_keys,
        ),
        "app_chart_import": async_redact_data(
            coordinator.app_chart_import_diagnostics(),
            redact_keys,
        ),
        "rejection_metrics": async_redact_data(
            coordinator.rejection_metrics.as_dict(),
            redact_keys,
        ),
    }

    return {
        "entry_data": async_redact_data(dict(entry.data), redact_keys),
        "options": async_redact_data(dict(entry.options), redact_keys),
        "devices": devices,
        "raw_api": raw,
    }


def _local_mqtt_diagnostics(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator | None = None,
    redactions_disabled: bool = False,
) -> dict[str, Any]:
    """Build a diagnostics block describing the integration's local MQTT status and configuration.

    When the coordinator indicates active local subscriptions, returns a block with "enabled": True, "mode": "homeassistant_mqtt_integration", "subscribed_topic_count", and a "configured_local_mqtt" mirror of host/port/auth/topic settings. If no client is available, returns "enabled": False with a concrete "disabled_reason" (one of "bridge_disabled", "missing_broker_host", "missing_broker_port", "broad_topic_filter_blocked", "missing_topic_filter", or "client_not_started") and the "configured_local_mqtt" mirror. When a client is available and no coordinator subscription indicator is present, returns the client's diagnostics snapshot.

    Parameters:
        redactions_disabled (bool): If True, request the client's diagnostics without redaction; if False, request a redacted snapshot.

    Returns:
        dict[str, Any]: Diagnostics block as described above — either the client's diagnostics snapshot or a structured dictionary reflecting enabled state, reason, and configured local MQTT details.
    """
    enabled = config_entry_bool_option(
        entry, CONF_LOCAL_MQTT_ENABLE, DEFAULT_LOCAL_MQTT_ENABLE
    )
    host = config_entry_str_option(entry, CONF_LOCAL_MQTT_HOST, "").strip()
    port = str(
        entry.options.get(
            CONF_LOCAL_MQTT_PORT,
            DEFAULT_LOCAL_MQTT_PORT,
        )
    ).strip()
    username = config_entry_str_option(entry, CONF_LOCAL_MQTT_USERNAME, "").strip()
    password = config_entry_str_option(entry, CONF_LOCAL_MQTT_PASSWORD, "").strip()
    topic_filter = config_entry_str_option(
        entry,
        CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
        DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER,
    ).strip()

    client = _local_mqtt_client(hass, entry)
    local_mqtt_unsubs = getattr(coordinator, "_local_mqtt_unsubs", None)
    if local_mqtt_unsubs:
        return {
            "enabled": True,
            "mode": "homeassistant_mqtt_integration",
            "subscribed_topic_count": len(local_mqtt_unsubs),
            "configured_local_mqtt": {
                "host": host,
                "port": port,
                "username_set": bool(username),
                "password_set": bool(password),
                "topic_filter": topic_filter,
            },
        }
    if client is None:
        if not enabled:
            reason = "bridge_disabled"
        elif not host:
            reason = "missing_broker_host"
        elif not port:
            reason = "missing_broker_port"
        elif topic_filter in _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS:
            reason = "broad_topic_filter_blocked"
        elif not topic_filter:
            reason = "missing_topic_filter"
        else:
            reason = "client_not_started"
        return {
            "enabled": False,
            "disabled_reason": reason,
            "configured_local_mqtt": {
                "host": host,
                "port": port,
                "username_set": bool(username),
                "password_set": bool(password),
                "topic_filter": topic_filter,
            },
        }
    return client.diagnostics_snapshot(redact=not redactions_disabled)
