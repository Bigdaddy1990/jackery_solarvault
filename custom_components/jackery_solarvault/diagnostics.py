"""Diagnostics support for Jackery SolarVault."""

import copy
from functools import partial
import logging
from typing import TYPE_CHECKING, Any, cast

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_PASSWORD, CONF_TOKEN

# _local_mqtt_client moved to coordinator property
from .const import (
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_PORT,
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
    CONF_THIRD_PARTY_MQTT_USERNAME,
    DEFAULT_THIRD_PARTY_MQTT_PORT,
    DIAGNOSTICS_SCHEMA_VERSION,
    DOMAIN,
    LOCAL_MQTT_RUNTIME_KEY,
)
from .coordinator import JackerySolarVaultCoordinator
from .util import (
    active_redact_keys,
    config_entry_int_option,
    config_entry_str_option,
    jackery_dev_mode_enabled,
    local_mqtt_opt_in,
    redacted_json_safe_payload,
)

_LOGGER = logging.getLogger(__name__)

_CRITICAL_CREDENTIAL_KEYS: frozenset[str] = frozenset({
    CONF_PASSWORD,
    CONF_TOKEN,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    "password",
    "token",
    "seed",
    "bluetoothKey",
    "key",
    "secret",
    "credential",
})

if TYPE_CHECKING:
    from collections.abc import Mapping

    from homeassistant.core import HomeAssistant

    from . import JackeryConfigEntry
    from .client.local_mqtt import JackeryLocalMqttClient


def _redacted_payload_map(
    payloads: Mapping[Any, Any],
    _prefix: str,
    redact_keys: frozenset[str],
) -> dict[str, Any]:
    """Keep source keys and redact secrets before wrapping scalar payloads.

    Returns:
        dict[str, Any]: Sorted source identities mapped to redacted payloads.
    """
    redacted: dict[str, Any] = {}
    for key in sorted(payloads, key=str):
        payload = payloads[key]
        label = str(key)
        value = payload if isinstance(payload, dict) else {"value": payload}
        redacted.update(async_redact_data({label: value}, redact_keys))
    return redacted


async def async_get_config_entry_diagnostics(  # HA requires async.
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
            - `devices`: stable local labels mapped to redacted device payloads.
            - `raw_api`: redacted coordinator, API, transport, and statistics
              diagnostics.
    """
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    runtime_bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    publisher = (
        runtime_bucket.get("mqtt_sensor_publisher")
        if isinstance(runtime_bucket, dict)
        else None
    )
    mirror_snapshot = getattr(publisher, "diagnostics_snapshot", None)
    dev_mode = jackery_dev_mode_enabled(entry)
    source = None
    if dev_mode:
        source = (
            "JACKERY_DEV_MODE=1"
            if jackery_dev_mode_enabled()
            else "options.enable_unredacted_debug"
        )
    if source:
        _LOGGER.warning(
            "Jackery diagnostics exported UNREDACTED because %s is enabled: "
            "credentials, serials, device ids, topics and bluetoothKey are "
            "in clear text; do not share this export publicly",
            source,
        )
    redact_keys = (
        active_redact_keys(entry)
        if dev_mode
        else (active_redact_keys(entry) | _CRITICAL_CREDENTIAL_KEYS)
    )
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
            "redactions_enforced": not dev_mode,
            **({"dev_mode": source} if source else {}),
            "credential_contract": "v1",
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
        "interrupted_http_responses": _redacted_payload_map(
            getattr(coordinator.api, "last_interrupted_http_responses", {}),
            "interrupted_http_response",
            redact_keys,
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
        "mqtt_mirror": async_redact_data(
            mirror_snapshot() if callable(mirror_snapshot) else {}, redact_keys
        ),
        "mqtt_operations": async_redact_data(
            {
                key: value
                for key, value in hass.data
                .get(DOMAIN, {})
                .get("mqtt_operation_errors", {})
                .items()
                if key != "causes"
            },
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
        "layer_source_payloads": async_redact_data(
            coordinator.layer_source_diagnostics(),
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
        await hass.async_add_executor_job(
            partial(
                redacted_json_safe_payload,
                export,
                sensitive_sources=(
                    copy.deepcopy(sensitive_sources) if not dev_mode else ()
                ),
                unredacted=dev_mode,
            )
        ),
    )


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
    dev_mode = jackery_dev_mode_enabled(entry)
    diagnostic_host = host
    diagnostic_port = port
    topic_filter = config_entry_str_option(
        entry, CONF_THIRD_PARTY_MQTT_TOPIC_FILTER, ""
    )

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
                "topic_filter": topic_filter,
                "effective_topic_filter": topic_filter,
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
                "topic_filter": topic_filter,
                "effective_topic_filter": topic_filter,
            },
        }
    return (
        client.diagnostics_snapshot(redact=False)
        if dev_mode
        else client.diagnostics_snapshot()
    )
