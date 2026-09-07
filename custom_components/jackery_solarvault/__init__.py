"""Jackery SolarVault integration."""

# Home Assistant requires each integration's setup/unload entry points in this
# package module; moving them solely to satisfy the generic package-init rule
# would break HA's loader contract.
# ruff: file-ignore[non-empty-init-module]

import asyncio
from datetime import timedelta
import hashlib
import logging
import operator
import re
from typing import TYPE_CHECKING, Any, Final, Literal, cast

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, EntityCategory
from homeassistant.core import CoreState, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store as Store
from homeassistant.helpers.update_coordinator import UpdateFailed

from .client import (
    JackeryApi,
    JackeryApiError as JackeryApiError,
    JackeryAuthError,
    JackeryError,
)
from .client.local_mqtt import JackeryLocalMqttClient, LocalMqttConnectionSettings
from .client.mqtt_session_store import (
    async_load_mqtt_session,
    normalize_mqtt_session_snapshot,
)
from .const import (
    CALCULATED_POWER_SENSOR_SUFFIXES,
    CONF_CREATE_CALCULATED_POWER_SENSORS,
    CONF_CREATE_SAVINGS_DETAIL_SENSORS,
    CONF_CREATE_SMART_METER_DERIVED_SENSORS,
    CONF_ENABLE_BLE_TRANSPORT,
    CONF_MQTT_MAC_ID,
    CONF_REGION_CODE,
    CONF_SCAN_INTERVAL,
    CONF_THIRD_PARTY_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_PORT,
    CONF_THIRD_PARTY_MQTT_QOS,
    CONF_THIRD_PARTY_MQTT_TOKEN,
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
    CONF_THIRD_PARTY_MQTT_USERNAME,
    COORDINATOR_SHUTDOWN_TIMEOUT_SEC,
    CT_PERIOD_SENSOR_SUFFIXES,
    DEFAULT_CREATE_CALCULATED_POWER_SENSORS,
    DEFAULT_CREATE_SAVINGS_DETAIL_SENSORS,
    DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
    DEFAULT_SCAN_INTERVAL_SEC,
    DEFAULT_THIRD_PARTY_MQTT_ENABLE as DEFAULT_THIRD_PARTY_MQTT_ENABLE,
    DEFAULT_THIRD_PARTY_MQTT_PASSWORD as DEFAULT_THIRD_PARTY_MQTT_PASSWORD,
    DEFAULT_THIRD_PARTY_MQTT_PORT as DEFAULT_THIRD_PARTY_MQTT_PORT,
    DEFAULT_THIRD_PARTY_MQTT_QOS,
    DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER as DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER,
    DEFAULT_THIRD_PARTY_MQTT_USERNAME as DEFAULT_THIRD_PARTY_MQTT_USERNAME,
    DOMAIN,
    DUPLICATE_BINARY_SENSOR_SUFFIXES,
    ENTRY_BOOTSTRAP_MQTT_SESSION,
    FIELD_BAT_NUM,
    FIELD_DEVICE_NAME as FIELD_DEVICE_NAME,
    FIELD_DEV_TYPE as FIELD_DEV_TYPE,
    FIELD_FIRMWARE_VERSION as FIELD_FIRMWARE_VERSION,
    FIELD_HARDWARE_VERSION as FIELD_HARDWARE_VERSION,
    FIELD_MODEL_CODE as FIELD_MODEL_CODE,
    FIELD_MODEL_ID as FIELD_MODEL_ID,
    FIELD_MODEL_NAME as FIELD_MODEL_NAME,
    FIELD_SCAN_NAME as FIELD_SCAN_NAME,
    FIELD_SUB_TYPE as FIELD_SUB_TYPE,
    LOCAL_MQTT_DEFAULT_TOPIC,
    LOCAL_MQTT_RUNTIME_KEY as _LOCAL_MQTT_RUNTIME_KEY,
    MAX_SCAN_INTERVAL_SEC,
    MIN_SCAN_INTERVAL_SEC,
    MQTT_SESSION_MAC_ID,
    MQTT_SESSION_MAC_ID_SOURCE,
    MQTT_SESSION_SEED_B64,
    MQTT_SESSION_USER_ID,
    PAYLOAD_BATTERY_PACKS,
    PAYLOAD_CT_METER,
    PAYLOAD_PROPERTIES,
    PLATFORMS,
    REMOVED_LOCAL_MQTT_TLS_OPTION_KEYS,
    REMOVED_SENSOR_SUFFIXES,
    SAVINGS_DETAIL_SENSOR_SUFFIXES,
    SETUP_LOGIN_MAX_ATTEMPTS,
    SETUP_LOGIN_RETRY_DELAY_SEC,
    SMART_METER_DERIVED_SENSOR_SUFFIXES,
    STALE_ENERGY_HELPER_PREFIX,
    STALE_HELPER_VENDOR_TOKENS,
    STALE_NET_POWER_SUFFIX,
    _ENTITY_CREATING_OPTION_KEYS,
)
from .coordinator import (
    STORAGE_ERRORS,
    JackerySolarVaultCoordinator,
    battery_pack_serial,
    sorted_battery_pack_payloads,
)
from .services import async_setup_services
from .util import (
    config_entry_bool_option,
    config_entry_int_option,
    config_entry_str_option,
    local_mqtt_opt_in,
    nonblank_text,
    safe_bool,
    safe_int,
    smart_meter_identity,
    stable_subdevice_key,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine, Iterable

    from homeassistant.core import HomeAssistant

# Typed ConfigEntry alias — the runtime_data attribute is a
# JackerySolarVaultCoordinator. Per HA developer guide (2024.4+) this
# alias lets type-checkers see through ``entry.runtime_data`` to the
# concrete coordinator type without sprinkling cast/getattr around
# the integration. PEP 695 syntax requires Python 3.12+; HA 2025.x
# already requires Python 3.14.
# Typed alias for type-checkers only. ConfigEntry is not subscriptable at
# runtime in some typing/runtime combinations, so the generic parametrisation
# is resolved at type-check time only. The branch is keyed off ``not
# TYPE_CHECKING`` so the only ``if TYPE_CHECKING:`` blocks in this module stay
# import-only (HA collection never executes runtime code from them), while the
# runtime assignment binds the bare ``ConfigEntry`` to avoid subscripting it on
# older typing APIs.
if TYPE_CHECKING:
    type JackeryConfigEntry = ConfigEntry[JackerySolarVaultCoordinator]
else:
    JackeryConfigEntry = ConfigEntry

_LOGGER = logging.getLogger(__name__)


# This integration is config-entry-only — there is no YAML configuration
# surface. The `cv.config_entry_only_config_schema` helper documents
# that contract to hassfest and rejects any YAML the user might add by
# accident.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(  # ruff: ignore[unused-async]  # HA loader contract.
    hass: HomeAssistant,
    config: dict[str, Any],
) -> bool:
    """Set up global Jackery SolarVault services."""
    async_setup_services(hass)
    return True


def _async_clean_legacy_entities(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Drop entity-registry entries from older releases or disabled options.

    Keep entity-registry cleanup explicit and setup-local. This avoids hidden
    entry-version side effects while still removing entities that are no
    longer part of the documented app/HTTP/MQTT data model.
    """
    _async_remove_stale_energy_helpers(hass)
    _async_migrate_portable_screen_entity(hass, entry)
    _async_migrate_grid_standard_entity(hass, entry)
    _async_migrate_smart_meter_identity(hass, entry)
    _async_migrate_battery_pack_identities(hass, entry)
    _async_remove_phantom_battery_pack_devices(hass, entry)
    _async_remove_entities_with_suffixes(
        hass,
        entry,
        domain="sensor",
        suffixes=REMOVED_SENSOR_SUFFIXES,
        log_label="removed Jackery sensor",
    )
    _async_remove_entities_with_suffixes(
        hass,
        entry,
        domain="sensor",
        suffixes=CT_PERIOD_SENSOR_SUFFIXES,
        log_label="removed CT period sensor",
    )
    if not config_entry_bool_option(
        entry,
        CONF_CREATE_SMART_METER_DERIVED_SENSORS,
        DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
    ):
        _async_remove_entities_with_suffixes(
            hass,
            entry,
            domain="sensor",
            suffixes=SMART_METER_DERIVED_SENSOR_SUFFIXES,
            log_label="disabled calculated smart-meter sensor",
        )
    if not config_entry_bool_option(
        entry,
        CONF_CREATE_CALCULATED_POWER_SENSORS,
        DEFAULT_CREATE_CALCULATED_POWER_SENSORS,
    ):
        _async_remove_entities_with_suffixes(
            hass,
            entry,
            domain="sensor",
            suffixes=CALCULATED_POWER_SENSOR_SUFFIXES,
            log_label="disabled calculated power sensor",
        )
    if not config_entry_bool_option(
        entry,
        CONF_CREATE_SAVINGS_DETAIL_SENSORS,
        DEFAULT_CREATE_SAVINGS_DETAIL_SENSORS,
    ):
        _async_remove_entities_with_suffixes(
            hass,
            entry,
            domain="sensor",
            suffixes=SAVINGS_DETAIL_SENSOR_SUFFIXES,
            log_label="disabled savings detail sensor",
        )
    _async_remove_entities_with_suffixes(
        hass,
        entry,
        domain="binary_sensor",
        suffixes=DUPLICATE_BINARY_SENSOR_SUFFIXES,
        log_label="duplicate binary sensor",
    )


def _async_register_main_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
) -> None:
    """Register every main device before the platforms add their entities.

    Subdevices link to their parent through ``DeviceInfo["via_device_id"]``,
    which HA 2026.8 requires to be a real device-registry id rather than the
    identifier tuple the retired ``via_device`` accepted. A registry id only
    exists once the parent device is registered, and entity order across
    platforms is not guaranteed, so the parents are created up front here.
    Registering with the identifiers alone is enough: the head entity's own
    ``device_info`` fills in name, model and versions when it is added.
    """
    # Typed as always a dict, but an autospec'd coordinator in tests hands out
    # a MagicMock; go through `Any` so the guard stays real at runtime instead
    # of mypy narrowing it away as unreachable.
    data: Any = coordinator.data
    if not isinstance(data, dict):
        return
    registry = dr.async_get(hass)
    for device_id in data:
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={(DOMAIN, device_id)},
        )


def _async_remove_legacy_system_parent_devices(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Undo the obsolete split between one SolarVault and its system record."""
    registry = dr.async_get(hass)
    entry_devices = tuple(dr.async_entries_for_config_entry(registry, entry.entry_id))
    obsolete_device_ids = {
        device.id
        for device in entry_devices
        if any(
            domain == DOMAIN and identifier.startswith(("system_", "system_sn_"))
            for domain, identifier in device.identifiers
        )
    }
    if not obsolete_device_ids:
        return

    for device in entry_devices:
        if (
            device.id not in obsolete_device_ids
            and device.via_device_id in obsolete_device_ids
        ):
            registry.async_update_device(device.id, via_device_id=None)

    for device_id in obsolete_device_ids:
        # HA 2026.9: a device belongs to exactly one config entry, so detaching
        # ours is removal — `remove_config_entry_id` now raises.
        registry.async_remove_device(device_id)


def _local_mqtt_client(
    hass: HomeAssistant, entry: JackeryConfigEntry
) -> JackeryLocalMqttClient | None:
    """Return the per-entry local MQTT client stored in hass.data.

    Returns:
        JackeryLocalMqttClient instance for the entry, or None if no client is
        stored or the stored value is not a JackeryLocalMqttClient.
    """
    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not isinstance(bucket, dict):
        return None
    client = bucket.get(_LOCAL_MQTT_RUNTIME_KEY)
    if client is None:
        return None
    return client if isinstance(client, JackeryLocalMqttClient) else None


_PRIMARY_SETUP_TIMEOUT_SEC = 90.0
_ENTRY_TASK_CANCEL_TIMEOUT_SEC = 5.0
_LAYER5_TASK_RUNTIME_KEY = "layer5_start_task"
_OPTIONS_RECONCILE_TASK_RUNTIME_KEY = "options_reconcile_task"
_OPTIONS_RECONCILE_PENDING_RUNTIME_KEY = "options_reconcile_pending"
_OPTIONS_DEVICE_CONFIG_PENDING_RUNTIME_KEY = "options_device_config_pending"
_DEVICE_MQTT_ADOPTED_OPTIONS_RUNTIME_KEY = "device_mqtt_adopted_options"
_OPTIONS_SNAPSHOT_RUNTIME_KEY = "options_snapshot"
_ENTRY_DATA_SNAPSHOT_RUNTIME_KEY = "entry_data_snapshot"
_LOCAL_MQTT_OPTION_KEYS = frozenset({
    CONF_THIRD_PARTY_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_PORT,
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
    CONF_THIRD_PARTY_MQTT_QOS,
    CONF_THIRD_PARTY_MQTT_TOKEN,
    CONF_THIRD_PARTY_MQTT_USERNAME,
})
_SUPPLEMENTAL_OPTION_KEYS: Final[frozenset[str]] = frozenset()
_DIRECT_LOCAL_MQTT_LISTENER_OPTION_KEYS = frozenset({
    CONF_THIRD_PARTY_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_PORT,
    CONF_THIRD_PARTY_MQTT_QOS,
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
    CONF_THIRD_PARTY_MQTT_USERNAME,
})
_UNLOADING_COORDINATOR_RUNTIME_KEY = "unloading_coordinator"
_PRIMARY_SETUP_COORDINATOR_RUNTIME_KEY = "primary_setup_coordinator"
_SUPPLEMENTAL_TRANSPORT_COORDINATORS_RUNTIME_KEY = "supplemental_transport_coordinators"
_SUPPLEMENTAL_LOCAL_MQTT_RUNTIME_KEY = "supplemental_local_mqtt_clients"
_SUPPLEMENTAL_LAYER5_TASKS_RUNTIME_KEY = "supplemental_layer5_tasks"
_SUPPLEMENTAL_CLEANUP_TASK_RUNTIME_KEY = "supplemental_cleanup_task"
_LOCAL_MQTT_RESTART_AFTER_CLEANUP_RUNTIME_KEY = "local_mqtt_restart_after_cleanup"
_LOCAL_MQTT_STOP_TASKS_RUNTIME_KEY = "local_mqtt_stop_tasks"
_LOCAL_MQTT_RECONCILE_LOCK_RUNTIME_KEY = "local_mqtt_reconcile_lock"
_COORDINATOR_SHUTDOWN_RUNTIME_KEY = "coordinator_shutdown"
_COORDINATOR_SHUTDOWN_REAPER_TASK_RUNTIME_KEY = "coordinator_shutdown_reaper_task"
_SUPPLEMENTAL_CLEANUP_RETRY_SEC = 5.0
_COORDINATOR_SHUTDOWN_RETRY_SEC = 5.0
_RUNTIME_TASK_RECORD_LENGTH = 2
_TOPIC_SUFFIX_PART_COUNT = 2
_MAX_TCP_PORT = 65_535
_HTTP_SETUP_TIMEOUT_MESSAGE = "Jackery HTTP setup timeout"


# The ``local_mqtt_*`` option family duplicated ``third_party_mqtt_*``: the
# options flow wrote the former while the start path read the latter, so a
# configured port / username / password never reached the broker connection.
# ``third_party_mqtt_*`` wins because the device-side 3046/3047 cycle owns it.
_LEGACY_LOCAL_MQTT_OPTION_MAP: Final = {
    "local_mqtt_enable": CONF_THIRD_PARTY_MQTT_ENABLE,
    "local_mqtt_host": CONF_THIRD_PARTY_MQTT_IP,
    "local_mqtt_port": CONF_THIRD_PARTY_MQTT_PORT,
    "local_mqtt_username": CONF_THIRD_PARTY_MQTT_USERNAME,
    "local_mqtt_password": CONF_THIRD_PARTY_MQTT_PASSWORD,
    "local_mqtt_topic": CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
}


@callback
def _async_migrate_legacy_local_mqtt_options(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> None:
    """Fold legacy ``local_mqtt_*`` options into the ``third_party_mqtt_*`` family."""
    legacy_present = _LEGACY_LOCAL_MQTT_OPTION_MAP.keys() & entry.options.keys()
    options = dict(entry.options)
    migrated: list[str] = []
    for legacy_key in sorted(legacy_present):
        canonical_key = _LEGACY_LOCAL_MQTT_OPTION_MAP[legacy_key]
        legacy_value = options.pop(legacy_key)
        if legacy_value is None:
            continue
        # A populated canonical option is the current form value. Migration
        # removes retired aliases but must never overwrite an explicit False,
        # QoS 0, empty credential, or any other deliberate current value.
        if canonical_key not in options or options[canonical_key] is None:
            options[canonical_key] = legacy_value
            migrated.append(f"{legacy_key} -> {canonical_key}")
    if options == dict(entry.options):
        return
    hass.config_entries.async_update_entry(entry, options=options)
    _LOGGER.info(
        "Jackery: migrated legacy local MQTT options (%s)",
        ", ".join(migrated) if migrated else "removed empty legacy keys only",
    )


@callback
def _async_prune_removed_local_mqtt_tls_options(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> None:
    """Drop obsolete Local-MQTT TLS options without reloading the entry."""
    removed = REMOVED_LOCAL_MQTT_TLS_OPTION_KEYS.intersection(entry.options)
    if not removed:
        return
    options = {key: value for key, value in entry.options.items() if key not in removed}
    hass.config_entries.async_update_entry(entry, options=options)
    _LOGGER.info(
        "Removed obsolete Jackery local MQTT TLS options: %s",
        ", ".join(sorted(removed)),
    )


def _entry_bootstrap_mqtt_session(entry: ConfigEntry) -> dict[str, str] | None:
    """Validate and extract the config entry's bootstrap MQTT session.

    Parameters:
        entry (ConfigEntry): Config entry whose data may contain an
        `ENTRY_BOOTSTRAP_MQTT_SESSION` mapping.

    Returns:
        dict[str, str]: Validated snapshot containing `MQTT_SESSION_USER_ID`,
        `MQTT_SESSION_SEED_B64`, and `MQTT_SESSION_MAC_ID`, and optionally
        `MQTT_SESSION_MAC_ID_SOURCE`, or `None` if the snapshot is missing or any
        required field is absent/invalid.
    """
    return normalize_mqtt_session_snapshot(
        entry.data.get(ENTRY_BOOTSTRAP_MQTT_SESSION),
    )


def _entry_runtime_bucket(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Get or create the config entry's mutable ``hass.data`` bucket.

    NOT a second copy of ``entry.runtime_data`` — do not "consolidate" the two.
    The coordinator lives in ``entry.runtime_data`` and that remains the single
    store for the integration's runtime state. This bucket holds only
    LIFECYCLE-TRANSITION state, which needs a lifetime that ``runtime_data``
    structurally cannot provide:

    * Home Assistant resets ``entry.runtime_data`` per setup, and this module
      additionally clears it to ``None`` during teardown (see
      ``_async_release_fenced_coordinator``). Anything stored there is gone
      exactly when the teardown path still needs it.
    * ``_async_release_fenced_coordinator`` runs as the FIRST statement of
      ``async_setup_entry`` and reads ``_UNLOADING_COORDINATOR_RUNTIME_KEY``
      from a *previous* setup that has not finished unwinding. That fence is
      what prevents two overlapping HTTP runtimes; it has to survive the
      boundary between two setups.
    * Deferred reaping (``_SUPPLEMENTAL_LOCAL_MQTT_RUNTIME_KEY``,
      ``_LAYER5_TASK_RUNTIME_KEY``, ``_SUPPLEMENTAL_CLEANUP_TASK_RUNTIME_KEY``)
      deliberately outlives the coordinator that created it.

    Moving these onto the coordinator or into ``runtime_data`` would drop the
    fence and allow a new coordinator to start while the previous one is still
    holding an HTTP session open.

    Parameters:
        hass (HomeAssistant): Home Assistant core instance.
        entry (ConfigEntry): The config entry whose runtime bucket is requested.

    Returns:
        dict[str, Any]: The dictionary stored at hass.data[DOMAIN][entry.entry_id];
        created and inserted if it did not already exist.
    """
    domain_bucket = hass.data.setdefault(DOMAIN, {})
    bucket = domain_bucket.get(entry.entry_id)
    if not isinstance(bucket, dict):
        bucket = {}
        domain_bucket[entry.entry_id] = bucket
    return bucket


def _entry_runtime_task(
    hass: HomeAssistant,
    entry: ConfigEntry,
    key: str,
) -> asyncio.Task[Any] | None:
    """Get a background task from the entry's integration runtime bucket.

    Returns:
        The stored asyncio task, or `None` when the bucket or value is invalid.
    """
    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not isinstance(bucket, dict):
        return None
    task = bucket.get(key)
    return task if isinstance(task, asyncio.Task) else None


def _configured_scan_interval_sec(entry: ConfigEntry) -> int:
    """Return the user's live-poll interval, clamped to the supported range.

    Clamping happens here as well as in the options schema so a value written
    by an older release, a YAML import, or a hand-edited ``.storage`` entry can
    never drive the coordinator below the rate limit the Jackery cloud tolerates.

    Returns:
        int: Poll interval in seconds within
        [``MIN_SCAN_INTERVAL_SEC``, ``MAX_SCAN_INTERVAL_SEC``].
    """
    configured = config_entry_int_option(
        entry,
        CONF_SCAN_INTERVAL,
        DEFAULT_SCAN_INTERVAL_SEC,
    )
    return min(max(configured, MIN_SCAN_INTERVAL_SEC), MAX_SCAN_INTERVAL_SEC)


def _entry_owns_coordinator(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
) -> bool:
    """Return whether the coordinator still owns an active entry runtime."""
    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    unloading = (
        bucket.get(_UNLOADING_COORDINATOR_RUNTIME_KEY)
        if isinstance(bucket, dict)
        else None
    )
    return entry.runtime_data is coordinator and unloading is not coordinator


def _clear_option_reconcile_runtime_state(bucket: dict[str, Any]) -> None:
    """Remove transition-only option state that cannot cross an unload fence."""
    bucket.pop(_OPTIONS_RECONCILE_PENDING_RUNTIME_KEY, None)
    bucket.pop(_OPTIONS_DEVICE_CONFIG_PENDING_RUNTIME_KEY, None)
    bucket.pop(_DEVICE_MQTT_ADOPTED_OPTIONS_RUNTIME_KEY, None)


def _store_entry_runtime_task(
    hass: HomeAssistant,
    entry: ConfigEntry,
    key: str,
    task: asyncio.Task[Any],
) -> None:
    """Store a task and consume its outcome with identity-safe slot cleanup."""
    _entry_runtime_bucket(hass, entry)[key] = task

    def _task_done(done: asyncio.Future[Any]) -> None:
        """Clear the owned task slot and consume the background task outcome."""
        bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if isinstance(bucket, dict) and bucket.get(key) is task:
            bucket.pop(key, None)
        try:
            done.result()
        except asyncio.CancelledError:
            return
        except Exception as err:  # ruff: ignore[blind-except]
            _LOGGER.warning(
                "Jackery background task %s for entry %s failed: %s",
                task.get_name(),
                entry.entry_id,
                err,
            )

    task.add_done_callback(_task_done)


def _cancel_layer5_start_task(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> asyncio.Task[Any] | None:
    """Cancel a still-running Layer-5 start task and drop its reference."""
    task = _entry_runtime_task(hass, entry, _LAYER5_TASK_RUNTIME_KEY)
    if task is None:
        return None
    if not task.done():
        task.cancel()
    bucket = _entry_runtime_bucket(hass, entry)
    if bucket.get(_LAYER5_TASK_RUNTIME_KEY) is task:
        bucket.pop(_LAYER5_TASK_RUNTIME_KEY, None)
    return task


async def _async_await_cancelled_runtime_task(
    hass: HomeAssistant,
    entry: ConfigEntry,
    task: asyncio.Task[Any] | None,
    *,
    label: str,
) -> None:
    """Bound shutdown on a cancelled entry task before dependent resources stop."""
    if not isinstance(task, asyncio.Task):
        return
    try:
        done, pending = await asyncio.wait(
            {task},
            timeout=_ENTRY_TASK_CANCEL_TIMEOUT_SEC,
        )
    except asyncio.CancelledError:
        if not task.done():
            task.cancel()
        _append_supplemental_runtime_object(
            hass,
            entry,
            _SUPPLEMENTAL_LAYER5_TASKS_RUNTIME_KEY,
            task,
        )
        raise
    if pending:
        _LOGGER.warning(
            "Jackery %s task did not stop within %.0fs; deferring cleanup",
            label,
            _ENTRY_TASK_CANCEL_TIMEOUT_SEC,
        )
        _append_supplemental_runtime_object(
            hass,
            entry,
            _SUPPLEMENTAL_LAYER5_TASKS_RUNTIME_KEY,
            task,
        )
        return
    try:
        next(iter(done)).result()
    except asyncio.CancelledError:
        return
    except Exception as err:  # ruff: ignore[blind-except]
        _LOGGER.debug("Jackery %s task stopped with an error: %s", label, err)


async def _async_cancel_runtime_task(
    hass: HomeAssistant,
    entry: ConfigEntry,
    key: str,
    *,
    label: str,
) -> None:
    """Cancel, remove and reap one stored entry runtime task."""
    runtime_task = _entry_runtime_task(hass, entry, key)
    if runtime_task is None:
        return
    task: asyncio.Task[Any] = runtime_task
    bucket = _entry_runtime_bucket(hass, entry)
    if bucket.get(key) is task:
        bucket.pop(key, None)
    if not task.done():
        task.cancel()
    await _async_await_cancelled_runtime_task(
        hass,
        entry,
        task,
        label=label,
    )


async def _async_cancel_layer5_start_task(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Cancel and reap the Layer-5 startup task before transport teardown."""
    task = _cancel_layer5_start_task(hass, entry)
    await _async_await_cancelled_runtime_task(
        hass,
        entry,
        task,
        label="Layer-5 startup",
    )


async def _async_stop_local_mqtt_client(
    hass: HomeAssistant,
    entry: ConfigEntry,
    client: JackeryLocalMqttClient,
) -> bool:
    """Stop a local MQTT client and drop its runtime reference on success."""
    bucket = _entry_runtime_bucket(hass, entry)
    stop_records = bucket.get(_LOCAL_MQTT_STOP_TASKS_RUNTIME_KEY)
    if not isinstance(stop_records, dict):
        stop_records = {}
        bucket[_LOCAL_MQTT_STOP_TASKS_RUNTIME_KEY] = stop_records
    record = stop_records.get(id(client))
    if (
        isinstance(record, tuple)
        and len(record) == _RUNTIME_TASK_RECORD_LENGTH
        and record[0] is client
        and isinstance(record[1], asyncio.Task)
    ):
        stop_task = record[1]
    else:
        stop_operation = (
            client.async_stop(wait_for_drain=False)
            if type(client) is JackeryLocalMqttClient
            else client.async_stop()
        )
        stop_task = hass.async_create_background_task(
            stop_operation,
            f"{DOMAIN}_local_mqtt_stop_{entry.entry_id}",
            eager_start=False,
        )
        stop_records[id(client)] = (client, stop_task)

    def _clear_stop_record() -> None:
        current_records = bucket.get(_LOCAL_MQTT_STOP_TASKS_RUNTIME_KEY)
        if not isinstance(current_records, dict):
            return
        current = current_records.get(id(client))
        if (
            isinstance(current, tuple)
            and len(current) == _RUNTIME_TASK_RECORD_LENGTH
            and current[0] is client
            and current[1] is stop_task
        ):
            current_records.pop(id(client), None)
        if not current_records:
            bucket.pop(_LOCAL_MQTT_STOP_TASKS_RUNTIME_KEY, None)

    try:
        done, pending = await asyncio.wait(
            {stop_task},
            timeout=_ENTRY_TASK_CANCEL_TIMEOUT_SEC,
        )
    except asyncio.CancelledError:
        # The stop operation is HA-owned and may have multiple lifecycle
        # waiters. Cancelling one waiter must not interrupt the shared
        # unsubscribe halfway through or make a surviving waiter fail.
        _defer_supplemental_local_mqtt(hass, entry, client)
        coordinator = getattr(entry, "runtime_data", None)
        if (
            isinstance(coordinator, JackerySolarVaultCoordinator)
            and coordinator.local_mqtt_client is client
        ):
            coordinator.set_local_mqtt_client(None)
        _schedule_supplemental_cleanup(hass, entry)
        raise
    if pending:
        # Keep waiting for this exact task in the supplemental reaper. Starting
        # a second ``async_stop`` could register/unregister the same callback
        # concurrently and create duplicate Local-MQTT subscribers.
        _defer_supplemental_local_mqtt(hass, entry, client)
        coordinator = getattr(entry, "runtime_data", None)
        if (
            isinstance(coordinator, JackerySolarVaultCoordinator)
            and coordinator.local_mqtt_client is client
        ):
            coordinator.set_local_mqtt_client(None)
        _schedule_supplemental_cleanup(hass, entry)
        _LOGGER.warning(
            "Jackery local MQTT client did not stop within %.0fs",
            _ENTRY_TASK_CANCEL_TIMEOUT_SEC,
        )
        return False
    try:
        next(iter(done)).result()
    except asyncio.CancelledError:
        _clear_stop_record()
        return False
    except Exception as err:  # ruff: ignore[blind-except]
        _clear_stop_record()
        _LOGGER.warning("Jackery local MQTT client did not stop cleanly: %s", err)
        return False
    _clear_stop_record()
    if bucket.get(_LOCAL_MQTT_RUNTIME_KEY) is client:
        bucket.pop(_LOCAL_MQTT_RUNTIME_KEY, None)
    return True


def _append_supplemental_runtime_object(
    hass: HomeAssistant,
    entry: ConfigEntry,
    key: str,
    value: object,
) -> None:
    """Keep a stale Layer-5 resource for background-only cleanup."""
    bucket = _entry_runtime_bucket(hass, entry)
    values = bucket.get(key)
    if not isinstance(values, list):
        values = []
        bucket[key] = values
    if not any(item is value for item in values):
        values.append(value)


def _supplemental_runtime_items(
    bucket: dict[str, Any],
    key: str,
) -> list[object]:
    """Return the valid list of deferred supplementary runtime objects."""
    values = bucket.get(key)
    return list(values) if isinstance(values, list) else []


def _set_supplemental_runtime_items(
    bucket: dict[str, Any],
    key: str,
    values: list[object],
) -> None:
    """Replace a deferred supplementary runtime list or remove it when empty."""
    if values:
        bucket[key] = values
    else:
        bucket.pop(key, None)


def _reconcile_supplemental_runtime_items(
    bucket: dict[str, Any],
    key: str,
    *,
    processed: list[object],
    retained: list[object],
) -> None:
    """Remove only processed objects while preserving concurrent appends."""
    current = _supplemental_runtime_items(bucket, key)

    def _contains_identity(values: list[object], candidate: object) -> bool:
        return any(item is candidate for item in values)

    reconciled = [
        item
        for item in current
        if not _contains_identity(processed, item) or _contains_identity(retained, item)
    ]
    for item in retained:
        if not _contains_identity(reconciled, item):
            reconciled.append(item)
    _set_supplemental_runtime_items(bucket, key, reconciled)


def _supplemental_cleanup_pending(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> bool:
    """Return whether an old Layer-5 resource still blocks a replacement."""
    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not isinstance(bucket, dict):
        return False
    return any(
        _supplemental_runtime_items(bucket, key)
        for key in (
            _SUPPLEMENTAL_TRANSPORT_COORDINATORS_RUNTIME_KEY,
            _SUPPLEMENTAL_LOCAL_MQTT_RUNTIME_KEY,
            _SUPPLEMENTAL_LAYER5_TASKS_RUNTIME_KEY,
        )
    )


def _defer_layer5_start_task(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> None:
    """Move an uncooperative old Layer-5 start task off the HTTP setup path."""
    runtime_task = _entry_runtime_task(hass, entry, _LAYER5_TASK_RUNTIME_KEY)
    if runtime_task is None:
        return
    task: asyncio.Task[Any] = runtime_task
    bucket = _entry_runtime_bucket(hass, entry)
    if bucket.get(_LAYER5_TASK_RUNTIME_KEY) is runtime_task:
        bucket.pop(_LAYER5_TASK_RUNTIME_KEY, None)
    if not task.done():
        task.cancel()
        _append_supplemental_runtime_object(
            hass,
            entry,
            _SUPPLEMENTAL_LAYER5_TASKS_RUNTIME_KEY,
            task,
        )


def _defer_supplemental_local_mqtt(
    hass: HomeAssistant,
    entry: ConfigEntry,
    client: JackeryLocalMqttClient,
) -> None:
    """Move a direct local MQTT client into independent cleanup."""
    bucket = _entry_runtime_bucket(hass, entry)
    if bucket.get(_LOCAL_MQTT_RUNTIME_KEY) is client:
        bucket.pop(_LOCAL_MQTT_RUNTIME_KEY, None)
    _append_supplemental_runtime_object(
        hass,
        entry,
        _SUPPLEMENTAL_LOCAL_MQTT_RUNTIME_KEY,
        client,
    )


def _defer_supplemental_transports(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
) -> None:
    """Keep failed MQTT/BLE teardown separate from the authoritative coordinator."""
    if coordinator.has_pending_supplemental_transport_cleanup:
        _append_supplemental_runtime_object(
            hass,
            entry,
            _SUPPLEMENTAL_TRANSPORT_COORDINATORS_RUNTIME_KEY,
            coordinator,
        )


async def _async_run_supplemental_cleanup_call(
    hass: HomeAssistant,
    entry: ConfigEntry,
    operation: Coroutine[Any, Any, Any],
    *,
    name: str,
) -> bool:
    """Run one cleanup operation in a separately owned, hard-bounded task."""
    task = hass.async_create_background_task(
        operation,
        name,
        eager_start=False,
    )
    try:
        done, pending = await asyncio.wait(
            {task},
            timeout=_ENTRY_TASK_CANCEL_TIMEOUT_SEC,
        )
    except asyncio.CancelledError:
        if not task.done():
            task.cancel()
        _append_supplemental_runtime_object(
            hass,
            entry,
            _SUPPLEMENTAL_LAYER5_TASKS_RUNTIME_KEY,
            task,
        )
        raise
    if pending:
        task.cancel()
        _append_supplemental_runtime_object(
            hass,
            entry,
            _SUPPLEMENTAL_LAYER5_TASKS_RUNTIME_KEY,
            task,
        )
        return False
    try:
        next(iter(done)).result()
    except asyncio.CancelledError:
        return False
    except Exception as err:  # ruff: ignore[blind-except]
        _LOGGER.warning(
            "Jackery supplemental cleanup %s failed for entry %s: %s: %s",
            name,
            entry.entry_id,
            type(err).__name__,
            err,
        )
        return False
    return True


async def _async_cancel_supplemental_layer5_tasks(
    bucket: dict[str, Any],
    entry: JackeryConfigEntry,
) -> bool:
    """Cancel stale Layer-5 tasks and retain only tasks still draining."""
    current_task = asyncio.current_task()
    snapshot = _supplemental_runtime_items(
        bucket,
        _SUPPLEMENTAL_LAYER5_TASKS_RUNTIME_KEY,
    )
    tasks = [
        item
        for item in snapshot
        if isinstance(item, asyncio.Task) and item is not current_task
    ]
    for task in tasks:
        if not task.done():
            task.cancel()

    pending: set[asyncio.Task[Any]] = set()
    if tasks:
        done, pending = await asyncio.wait(
            tasks,
            timeout=_ENTRY_TASK_CANCEL_TIMEOUT_SEC,
        )
        for task in done:
            try:
                task.result()
            except asyncio.CancelledError:
                pass
            except Exception as err:  # ruff: ignore[blind-except]
                _LOGGER.warning(
                    "Jackery supplemental cleanup %s failed late for entry %s: %s: %s",
                    task.get_name(),
                    entry.entry_id,
                    type(err).__name__,
                    err,
                )

    _reconcile_supplemental_runtime_items(
        bucket,
        _SUPPLEMENTAL_LAYER5_TASKS_RUNTIME_KEY,
        processed=[item for item in snapshot if item is not current_task],
        retained=list(pending),
    )
    return bool(pending)


async def _async_stop_supplemental_local_mqtt(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    bucket: dict[str, Any],
) -> None:
    """Stop stale local MQTT clients and retain only unfinished clients."""
    snapshot = _supplemental_runtime_items(
        bucket,
        _SUPPLEMENTAL_LOCAL_MQTT_RUNTIME_KEY,
    )
    pending: list[object] = [
        item
        for item in snapshot
        if isinstance(item, JackeryLocalMqttClient)
        and not await _async_stop_local_mqtt_client(hass, entry, item)
    ]
    _reconcile_supplemental_runtime_items(
        bucket,
        _SUPPLEMENTAL_LOCAL_MQTT_RUNTIME_KEY,
        processed=snapshot,
        retained=pending,
    )


async def _async_stop_supplemental_transports(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    bucket: dict[str, Any],
) -> None:
    """Stop stale transport coordinators and retain unfinished cleanup."""
    snapshot = _supplemental_runtime_items(
        bucket,
        _SUPPLEMENTAL_TRANSPORT_COORDINATORS_RUNTIME_KEY,
    )
    pending: list[object] = []
    for item in snapshot:
        if not isinstance(item, JackerySolarVaultCoordinator):
            continue
        stopped = await _async_run_supplemental_cleanup_call(
            hass,
            entry,
            item.async_stop_supplemental_transports(),
            name=f"{DOMAIN}_deferred_transport_stop_{entry.entry_id}",
        )
        if not stopped or item.has_pending_supplemental_transport_cleanup:
            pending.append(item)
    _reconcile_supplemental_runtime_items(
        bucket,
        _SUPPLEMENTAL_TRANSPORT_COORDINATORS_RUNTIME_KEY,
        processed=snapshot,
        retained=pending,
    )


def _restart_local_mqtt_after_supplemental_cleanup(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    bucket: dict[str, Any],
) -> None:
    """Restart local MQTT only when cleanup belonged to the active runtime."""
    restart = bool(bucket.pop(_LOCAL_MQTT_RESTART_AFTER_CLEANUP_RUNTIME_KEY, False))
    coordinator = getattr(entry, "runtime_data", None)
    if (
        restart
        and isinstance(coordinator, JackerySolarVaultCoordinator)
        and _entry_owns_coordinator(hass, entry, coordinator)
    ):
        _schedule_layer5_start_if_ready(
            hass,
            entry,
            coordinator,
            set(_DIRECT_LOCAL_MQTT_LISTENER_OPTION_KEYS),
            device_config_keys=set(),
        )


async def _async_cleanup_stale_supplemental(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> None:
    """Retry stale Layer-5 cleanup without delaying HTTP setup or polling."""
    while _supplemental_cleanup_pending(hass, entry):
        bucket = _entry_runtime_bucket(hass, entry)
        if await _async_cancel_supplemental_layer5_tasks(bucket, entry):
            await asyncio.sleep(_SUPPLEMENTAL_CLEANUP_RETRY_SEC)
            continue
        await _async_stop_supplemental_local_mqtt(hass, entry, bucket)
        await _async_stop_supplemental_transports(hass, entry, bucket)
        if _supplemental_cleanup_pending(hass, entry):
            await asyncio.sleep(_SUPPLEMENTAL_CLEANUP_RETRY_SEC)

    bucket = _entry_runtime_bucket(hass, entry)
    _restart_local_mqtt_after_supplemental_cleanup(hass, entry, bucket)


def _schedule_supplemental_cleanup(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> None:
    """Schedule bounded retries for stale supplementary transports."""
    if not _supplemental_cleanup_pending(hass, entry):
        return
    existing = _entry_runtime_task(
        hass,
        entry,
        _SUPPLEMENTAL_CLEANUP_TASK_RUNTIME_KEY,
    )
    if existing is not None and not existing.done():
        return
    task = hass.async_create_background_task(
        _async_cleanup_stale_supplemental(hass, entry),
        name=f"{DOMAIN}_supplemental_cleanup_{entry.entry_id}",
        eager_start=False,
    )
    _store_entry_runtime_task(
        hass,
        entry,
        _SUPPLEMENTAL_CLEANUP_TASK_RUNTIME_KEY,
        task,
    )


async def _async_release_fenced_coordinator(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> bool:
    """Finish HTTP cleanup for a prior unload before allowing a new runtime."""
    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not isinstance(bucket, dict):
        return True
    coordinator = bucket.get(_UNLOADING_COORDINATOR_RUNTIME_KEY)
    if coordinator is None:
        return True
    if not isinstance(coordinator, JackerySolarVaultCoordinator):
        bucket.pop(_UNLOADING_COORDINATOR_RUNTIME_KEY, None)
        return True
    if not await _async_shutdown_coordinator_bounded(
        coordinator,
        context="prior entry unload",
        hass=hass,
        entry=entry,
    ):
        _LOGGER.warning(
            "Jackery prior HTTP coordinator cleanup is still pending; "
            "deferring setup to prevent overlapping HTTP runtimes",
        )
        return False
    _defer_supplemental_transports(hass, entry, coordinator)
    if bucket.get(_UNLOADING_COORDINATOR_RUNTIME_KEY) is coordinator:
        bucket.pop(_UNLOADING_COORDINATOR_RUNTIME_KEY, None)
    if bucket.get(_PRIMARY_SETUP_COORDINATOR_RUNTIME_KEY) is coordinator:
        bucket.pop(_PRIMARY_SETUP_COORDINATOR_RUNTIME_KEY, None)
    if entry.runtime_data is coordinator:
        entry.runtime_data = cast("Any", None)
    _schedule_supplemental_cleanup(hass, entry)
    return True


async def _async_prime_entry_bootstrap_mqtt_session(  # ruff: ignore[unused-async]
    _hass: HomeAssistant,
    entry: ConfigEntry,
    api: JackeryApi,
) -> dict[str, str] | None:
    """Hydrate a bootstrap MQTT session without touching persistent storage."""
    snapshot = _entry_bootstrap_mqtt_session(entry)
    if snapshot is None:
        return None
    api.hydrate_mqtt_session(
        user_id=snapshot[MQTT_SESSION_USER_ID],
        seed_b64=snapshot[MQTT_SESSION_SEED_B64],
        mac_id=snapshot[MQTT_SESSION_MAC_ID],
        mac_id_source=snapshot.get(MQTT_SESSION_MAC_ID_SOURCE),
    )
    return snapshot


async def _async_load_entry_caches(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
) -> bool:
    """Load all restart caches before the first HTTP authentication attempt.

    Returns:
        True when cached discovery can seed platform setup, otherwise False.
    """
    discovery_ready = await coordinator.async_load_cached_discovery(
        "startup cache bootstrap",
    )

    persisted_mqtt: dict[str, str] | None = None
    try:
        persisted_mqtt = await async_load_mqtt_session(hass, entry.entry_id)
    except STORAGE_ERRORS as err:
        _LOGGER.debug("Jackery MQTT session cache load failed: %s", err)

    if coordinator.api.mqtt_session_snapshot() is None:
        fallback = persisted_mqtt or _entry_bootstrap_mqtt_session(entry)
        if fallback is not None:
            coordinator.api.hydrate_mqtt_session(
                user_id=fallback[MQTT_SESSION_USER_ID],
                seed_b64=fallback[MQTT_SESSION_SEED_B64],
                mac_id=fallback[MQTT_SESSION_MAC_ID],
                mac_id_source=fallback.get(MQTT_SESSION_MAC_ID_SOURCE),
            )
    coordinator.mark_mqtt_session_cache_loaded(persisted_mqtt)

    await coordinator.async_load_local_daily_snapshots()

    cached_snapshot = coordinator.cached_discovery_snapshot()
    if discovery_ready and cached_snapshot:
        # This is discovery/topology only. It is sufficient to create stable
        # entities and start BLE/local transports, but it is not represented as
        # a successful HTTP coordinator refresh.
        coordinator.data = cached_snapshot
        return True
    return False


async def _async_authenticate_api_layer(
    hass: HomeAssistant,
    entry: ConfigEntry,
    api: JackeryApi,
) -> None:
    """Authenticate HTTP without awaiting supplemental MQTT persistence.

    The config-entry bootstrap is hydrated in memory before the unchanged HTTP login
    retry loop. Persistent MQTT cache reconciliation is deferred until Layer 5 starts
    after mandatory HTTP setup has completed.

    Parameters:
        hass (HomeAssistant): Home Assistant core instance.
        entry (ConfigEntry): Configuration entry for the integration.
        api (JackeryApi): API client instance to authenticate and hydrate.

    Raises:
        ConfigEntryAuthFailed: If the Jackery credentials are rejected (triggers
        re-auth flow).
    """
    if api.mqtt_session_snapshot() is None:
        await _async_prime_entry_bootstrap_mqtt_session(hass, entry, api)
    # Retry a transient login rejection before triggering reauth. Every reload
    # (OptionsFlowWithReload fires one per options change) re-runs this login;
    # on the single-session Jackery account a reload that races the mobile app
    # or a burst of option toggles can get a transient JackeryAuthError /
    # rate-limit. The poll path already tolerates one transient 401 — the setup
    # login must too, otherwise a transient escalates to ConfigEntryAuthFailed
    # and pauses polling with a reauth prompt (owner 2026-07-05: "reauth must
    # not pause polling"). Only a rejection that persists across all attempts is
    # treated as a real credential failure.
    for attempt in range(SETUP_LOGIN_MAX_ATTEMPTS):
        try:
            await api.async_login()
        except JackeryAuthError as err:
            if attempt + 1 >= SETUP_LOGIN_MAX_ATTEMPTS:
                msg = (
                    "Jackery login rejected the credentials after "
                    f"{SETUP_LOGIN_MAX_ATTEMPTS} attempts: {err}"
                )
                raise ConfigEntryAuthFailed(msg) from err
            _LOGGER.info(
                "Jackery setup login rejected (attempt %d/%d); retrying in "
                "%.0fs instead of triggering reauth: %s",
                attempt + 1,
                SETUP_LOGIN_MAX_ATTEMPTS,
                SETUP_LOGIN_RETRY_DELAY_SEC,
                err,
            )
            await asyncio.sleep(SETUP_LOGIN_RETRY_DELAY_SEC)
        except JackeryError as err:
            msg = (
                "Jackery cloud HTTP/API login is unavailable; primary startup "
                f"cannot continue until login succeeds: {err}"
            )
            raise UpdateFailed(msg) from err
        else:
            break


async def _async_run_primary_http_startup(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
) -> None:
    """Run the ordered HTTP authentication and discovery sequence."""
    await _async_authenticate_api_layer(hass, entry, coordinator.api)
    await coordinator.async_persist_http_mqtt_session()
    await coordinator.async_discover()
    if entry.state is ConfigEntryState.SETUP_IN_PROGRESS:
        await coordinator.async_config_entry_first_refresh()
    elif entry.state is ConfigEntryState.LOADED:
        await coordinator.async_request_refresh()
    else:
        return
    if not coordinator.data:
        msg = (
            "Jackery HTTP discovery returned no devices; "
            "Home Assistant will retry setup"
        )
        raise ConfigEntryNotReady(msg)


async def _async_prepare_primary_http(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
) -> None:
    """Authenticate, discover, and complete HA's mandatory first HTTP refresh."""
    try:
        await _async_run_primary_http_startup(hass, entry, coordinator)
    except ConfigEntryAuthFailed, ConfigEntryNotReady:
        raise
    except JackeryAuthError as err:
        msg = f"Jackery credentials were rejected by the HTTP API: {err}"
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, OSError, UpdateFailed) as err:
        msg = f"Jackery primary HTTP setup is temporarily unavailable: {err}"
        raise ConfigEntryNotReady(msg) from err


async def _async_prepare_primary_http_from_cache(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
) -> None:
    """Refresh cached setup without delaying entity availability on reload."""
    try:
        async with asyncio.timeout(_PRIMARY_SETUP_TIMEOUT_SEC):
            await _async_prepare_primary_http(hass, entry, coordinator)
    except asyncio.CancelledError:
        raise
    except ConfigEntryAuthFailed:
        entry.async_start_reauth(hass)
        raise
    except TimeoutError:
        _LOGGER.warning("Jackery HTTP setup timeout; continuing from cache")
    except ConfigEntryNotReady:
        # Let ConfigEntryNotReady propagate so the entry gets re-tried
        raise
    except JackeryAuthError as err:
        # Propagate auth failures so the user gets re-auth prompt
        msg = f"Jackery HTTP login failed from cache: {err}"
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, OSError, UpdateFailed) as err:
        # Let other errors propagate for retry
        msg = f"Jackery HTTP refresh from cache failed: {err}"
        raise ConfigEntryNotReady(msg) from err


def _handle_optional_startup_result(
    coordinator: JackerySolarVaultCoordinator,
    result: BaseException | object,
    *,
    label: str,
) -> None:
    """Handle an optional startup result without failing primary HTTP setup.

    Parameters:
        coordinator (JackerySolarVaultCoordinator): Coordinator instance used to record
        or defer auth failures.
        result (BaseException | object): The outcome from a parallel startup task; may
        be an exception.
        label (str): Short label identifying the startup layer (used in logs).
    """
    if isinstance(result, ConfigEntryAuthFailed):
        coordinator.defer_background_auth_failure(result)
    elif isinstance(result, BaseException):
        _LOGGER.warning("Jackery %s could not start: %s", label, result)


async def _async_start_layer5_transports(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
) -> None:
    """Start all Layer-5 clients concurrently without a shared lifecycle owner."""
    # Yield once so the entry-owned background task cannot begin inside the
    # ``async_setup_entry`` call stack. HTTP authentication/cache hydration has
    # already completed before this task is scheduled; the three transports
    # can now start independently without waiting on unrelated HA tasks.
    await asyncio.sleep(0)
    if not _entry_owns_coordinator(hass, entry, coordinator):
        return

    # Device-side 3046/3047 configuration is independent of the HA broker
    # subscription. Schedule it before any transport await so a stuck broker,
    # DNS lookup or unsubscribe can never suppress the device command path.
    coordinator.async_schedule_local_mqtt_device_config()

    operations = (
        ("cloud MQTT", coordinator.async_start_mqtt()),
        ("local MQTT", _async_start_local_mqtt(hass, entry, coordinator)),
        ("BLE", coordinator.async_start_ble_transport()),
    )
    tasks = {
        entry.async_create_background_task(
            hass,
            operation,
            name=(f"{DOMAIN}_{label.lower().replace(" ", "_")}_start_{entry.entry_id}"),
            eager_start=False,
        ): label
        for label, operation in operations
    }
    try:
        while tasks:
            done, _pending = await asyncio.wait(
                tasks,
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in done:
                label = tasks.pop(task)
                try:
                    result: BaseException | object = task.result()
                except asyncio.CancelledError as err:
                    result = err
                except Exception as err:  # ruff: ignore[blind-except]
                    result = err
                if _entry_owns_coordinator(hass, entry, coordinator):
                    _handle_optional_startup_result(
                        coordinator,
                        result,
                        label=label,
                    )
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


def _schedule_layer5_start_if_ready(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
    changed_keys: set[str] | None = None,
    *,
    device_config_keys: set[str] | None = None,
) -> None:
    """Schedule independent Layer-5 clients for the active coordinator."""
    if not _entry_owns_coordinator(hass, entry, coordinator):
        return
    if changed_keys:
        bucket = _entry_runtime_bucket(hass, entry)
        pending = bucket.get(_OPTIONS_RECONCILE_PENDING_RUNTIME_KEY)
        if not isinstance(pending, set):
            pending = set()
            bucket[_OPTIONS_RECONCILE_PENDING_RUNTIME_KEY] = pending
        pending.update(key for key in changed_keys if isinstance(key, str))
        device_pending = bucket.get(_OPTIONS_DEVICE_CONFIG_PENDING_RUNTIME_KEY)
        if not isinstance(device_pending, set):
            device_pending = set()
            bucket[_OPTIONS_DEVICE_CONFIG_PENDING_RUNTIME_KEY] = device_pending
        requested_device_keys = (
            changed_keys & _LOCAL_MQTT_OPTION_KEYS
            if device_config_keys is None
            else device_config_keys & _LOCAL_MQTT_OPTION_KEYS
        )
        device_pending.update(requested_device_keys)
        existing = _entry_runtime_task(
            hass,
            entry,
            _OPTIONS_RECONCILE_TASK_RUNTIME_KEY,
        )
        if existing is not None and not existing.done():
            return
        if existing is not None:
            current = _entry_runtime_bucket(hass, entry)
            if current.get(_OPTIONS_RECONCILE_TASK_RUNTIME_KEY) is existing:
                current.pop(_OPTIONS_RECONCILE_TASK_RUNTIME_KEY, None)
        task = entry.async_create_background_task(
            hass,
            _async_reconcile_entry_options(hass, entry, coordinator),
            name=f"{DOMAIN}_options_reconcile_{entry.entry_id}",
            eager_start=False,
        )
        _store_entry_runtime_task(
            hass,
            entry,
            _OPTIONS_RECONCILE_TASK_RUNTIME_KEY,
            task,
        )
        return
    if _entry_runtime_task(hass, entry, _LAYER5_TASK_RUNTIME_KEY) is not None:
        return
    task = entry.async_create_background_task(
        hass,
        _async_start_layer5_transports(hass, entry, coordinator),
        name=f"{DOMAIN}_layer5_{entry.entry_id}",
        eager_start=False,
    )
    _store_entry_runtime_task(
        hass,
        entry,
        _LAYER5_TASK_RUNTIME_KEY,
        task,
    )

    @callback
    def _cancel_layer5_task() -> None:
        """Cancel supplementary transport startup when the entry unloads."""
        task.cancel()

    entry.async_on_unload(_cancel_layer5_task)


async def _async_reconcile_entry_options(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
) -> None:
    """Apply supplemental transport options without touching primary HTTP."""
    while _entry_owns_coordinator(hass, entry, coordinator):
        bucket = _entry_runtime_bucket(hass, entry)
        pending = bucket.pop(_OPTIONS_RECONCILE_PENDING_RUNTIME_KEY, None)
        if not isinstance(pending, set) or not pending:
            return
        changed_keys = {key for key in pending if isinstance(key, str)}
        device_pending = bucket.pop(
            _OPTIONS_DEVICE_CONFIG_PENDING_RUNTIME_KEY,
            None,
        )
        device_config_changed_keys = (
            {key for key in device_pending if isinstance(key, str)}
            if isinstance(device_pending, set)
            else changed_keys
        )
        if device_config_changed_keys & _LOCAL_MQTT_OPTION_KEYS:
            coordinator.async_schedule_local_mqtt_device_config()
        operations: list[tuple[str, Any]] = []
        if changed_keys & _DIRECT_LOCAL_MQTT_LISTENER_OPTION_KEYS:
            operations.append((
                "local MQTT listener reconfiguration",
                _async_start_local_mqtt(hass, entry, coordinator),
            ))
        if CONF_ENABLE_BLE_TRANSPORT in changed_keys:
            operations.append((
                "BLE transport reconfiguration",
                coordinator.async_reconcile_ble_transport(),
            ))
        if operations:
            results = await asyncio.gather(
                *(operation for _label, operation in operations),
                return_exceptions=True,
            )
            if not _entry_owns_coordinator(hass, entry, coordinator):
                return
            for (label, _operation), result in zip(
                operations,
                results,
                strict=True,
            ):
                _handle_optional_startup_result(coordinator, result, label=label)


async def _async_start_local_mqtt(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
) -> None:
    """Serialize all entry-level subscriber reconciliation calls."""
    if not _entry_owns_coordinator(hass, entry, coordinator):
        return
    bucket = _entry_runtime_bucket(hass, entry)
    lock = bucket.get(_LOCAL_MQTT_RECONCILE_LOCK_RUNTIME_KEY)
    if not isinstance(lock, asyncio.Lock):
        lock = asyncio.Lock()
        bucket[_LOCAL_MQTT_RECONCILE_LOCK_RUNTIME_KEY] = lock
    async with lock:
        await _async_start_local_mqtt_locked(hass, entry, coordinator)


def _configured_local_mqtt_listener(
    entry: ConfigEntry,
) -> tuple[bool, str, Literal[0, 1, 2]]:
    """Return the canonical HA-broker listener configuration for one entry."""
    configured_topic_filter = config_entry_str_option(
        entry,
        CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
        "",
    ).strip()
    configured_topic_filter = configured_topic_filter or LOCAL_MQTT_DEFAULT_TOPIC
    configured_qos_value = config_entry_int_option(
        entry,
        CONF_THIRD_PARTY_MQTT_QOS,
        DEFAULT_THIRD_PARTY_MQTT_QOS,
    )
    if configured_qos_value not in {0, 1, 2}:
        _LOGGER.warning(
            "Ignoring invalid Local MQTT QoS %r; using %d",
            configured_qos_value,
            DEFAULT_THIRD_PARTY_MQTT_QOS,
        )
        configured_qos_value = DEFAULT_THIRD_PARTY_MQTT_QOS
    return (
        local_mqtt_opt_in(entry),
        configured_topic_filter,
        # mypy needs this cast: `in {0, 1, 2}` doesn't narrow to Literal[0, 1, 2].
        # ty narrows it without the cast and flags the cast as redundant.
        cast(  # ty: ignore[redundant-cast]
            "Literal[0, 1, 2]",
            configured_qos_value,
        ),
    )


def _local_mqtt_snapshot_route(
    configured_topic_filter: str,
) -> tuple[str, Literal["device", "devices"]]:
    """Resolve the official snapshot route represented by a response filter."""
    topic_marker = next(
        (
            marker
            for marker in ("/device/", "/devices/")
            if marker in configured_topic_filter
        ),
        None,
    )
    if topic_marker is None:
        configured_root = configured_topic_filter.rstrip("/#").strip("/")
        return configured_root or LOCAL_MQTT_DEFAULT_TOPIC, "device"
    topic_prefix, topic_suffix = configured_topic_filter.split(topic_marker, 1)
    suffix_parts = topic_suffix.split("/")
    official_response_filter = topic_suffix == "#" or (
        len(suffix_parts) == _TOPIC_SUFFIX_PART_COUNT
        and bool(suffix_parts[0])
        and suffix_parts[1] in {"#", "+", "status", "event"}
    )
    if not topic_prefix or not official_response_filter:
        return "hb", "device"
    return (
        topic_prefix,
        "devices" if topic_marker == "/devices/" else "device",
    )


def _local_mqtt_connection_settings(
    entry: JackeryConfigEntry,
) -> tuple[bool, LocalMqttConnectionSettings, int]:
    """Return whether local MQTT should run and its exact connection settings."""
    should_run, configured_topic_filter, configured_qos = (
        _configured_local_mqtt_listener(entry)
    )
    configured_host = config_entry_str_option(
        entry,
        CONF_THIRD_PARTY_MQTT_IP,
        "",
    ).strip()
    configured_port = config_entry_int_option(
        entry,
        CONF_THIRD_PARTY_MQTT_PORT,
        DEFAULT_THIRD_PARTY_MQTT_PORT,
    )
    configured_username = (
        config_entry_str_option(entry, CONF_THIRD_PARTY_MQTT_USERNAME, "").strip()
        or None
    )
    configured_password = (
        config_entry_str_option(entry, CONF_THIRD_PARTY_MQTT_PASSWORD, "") or None
    )
    snapshot_interval_sec = _configured_scan_interval_sec(entry)
    if should_run and not configured_host:
        _LOGGER.warning(
            "Jackery local MQTT is enabled without a broker host; "
            "the direct listener will remain stopped"
        )
        should_run = False
    configured_settings = LocalMqttConnectionSettings(
        host=configured_host,
        port=configured_port,
        username=configured_username,
        password=configured_password,
        client_id=f"ha-jackery-{entry.entry_id[:8]}",
        topic_filter=configured_topic_filter,
        qos=configured_qos,
    )
    return should_run, configured_settings, snapshot_interval_sec


def _set_local_mqtt_restart_after_cleanup(
    bucket: dict[str, Any],
    *,
    should_run: bool,
) -> None:
    """Remember whether stale cleanup must restart the configured listener."""
    if should_run:
        bucket[_LOCAL_MQTT_RESTART_AFTER_CLEANUP_RUNTIME_KEY] = True
    else:
        bucket.pop(_LOCAL_MQTT_RESTART_AFTER_CLEANUP_RUNTIME_KEY, None)


async def _async_reconcile_existing_local_mqtt(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
    settings: LocalMqttConnectionSettings,
    *,
    should_run: bool,
) -> bool:
    """Reconcile the existing listener; return whether this start is complete."""
    existing_client = _local_mqtt_client(hass, entry)
    if existing_client is None:
        return False
    if (
        should_run
        and existing_client.is_started is True
        and existing_client.matches_configuration(settings) is True
    ):
        existing_client.set_snapshot_interval(_configured_scan_interval_sec(entry))
        if coordinator.local_mqtt_client is not existing_client:
            coordinator.set_local_mqtt_client(existing_client)
        return True

    if not await _async_stop_local_mqtt_client(hass, entry, existing_client):
        if coordinator.local_mqtt_client is existing_client:
            coordinator.set_local_mqtt_client(None)
        _defer_supplemental_local_mqtt(hass, entry, existing_client)
        bucket = _entry_runtime_bucket(hass, entry)
        _set_local_mqtt_restart_after_cleanup(bucket, should_run=should_run)
        _schedule_supplemental_cleanup(hass, entry)
        return True

    if coordinator.local_mqtt_client is existing_client:
        coordinator.set_local_mqtt_client(None)
    return not _entry_owns_coordinator(hass, entry, coordinator)


async def _async_detach_local_mqtt_client(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
    client: JackeryLocalMqttClient,
) -> None:
    """Detach a failed listener and preserve unfinished no-drop cleanup."""
    if coordinator.local_mqtt_client is client:
        coordinator.set_local_mqtt_client(None)
    if not await _async_stop_local_mqtt_client(hass, entry, client):
        _defer_supplemental_local_mqtt(hass, entry, client)
        _schedule_supplemental_cleanup(hass, entry)


async def _async_start_new_local_mqtt_client(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
    settings: LocalMqttConnectionSettings,
    *,
    snapshot_interval_sec: int,
) -> None:
    """Start one listener and attach its exact snapshot route."""

    async def _handle_local_mqtt_data(
        topic: str,
        data: dict[str, Any] | None,
        raw_bytes: bytes,
    ) -> bool:
        """Dispatch every decoded local MQTT candidate to the coordinator."""
        return await coordinator.async_handle_local_mqtt_message(
            topic,
            data,
            raw_bytes,
        )

    client = JackeryLocalMqttClient(
        hass,
        settings,
        sink=_handle_local_mqtt_data,
        config_entry=entry,
    )
    if not _entry_owns_coordinator(hass, entry, coordinator):
        return
    bucket = hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})
    bucket[_LOCAL_MQTT_RUNTIME_KEY] = client
    coordinator.set_local_mqtt_client(client)

    try:
        await client.async_start()
    except asyncio.CancelledError, Exception:
        await _async_detach_local_mqtt_client(hass, entry, coordinator, client)
        raise
    if not _entry_owns_coordinator(hass, entry, coordinator):
        await _async_detach_local_mqtt_client(hass, entry, coordinator, client)
        return

    snapshot_topic_prefix, snapshot_device_topic_segment = _local_mqtt_snapshot_route(
        settings.topic_filter
    )
    client.set_snapshot_requester(
        lambda: coordinator.async_poll_local_mqtt_devices(
            snapshot_topic_prefix,
            device_topic_segment=snapshot_device_topic_segment,
        ),
        interval_sec=snapshot_interval_sec,
    )


async def _async_start_local_mqtt_locked(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
) -> None:
    """Reconcile one direct local-broker client while holding the per-entry lock."""
    if not _entry_owns_coordinator(hass, entry, coordinator):
        return
    should_run, configured_settings, snapshot_interval_sec = (
        _local_mqtt_connection_settings(entry)
    )
    bucket = _entry_runtime_bucket(hass, entry)
    deferred_local_mqtt = _supplemental_runtime_items(
        bucket,
        _SUPPLEMENTAL_LOCAL_MQTT_RUNTIME_KEY,
    )
    if deferred_local_mqtt:
        _set_local_mqtt_restart_after_cleanup(bucket, should_run=should_run)
        _schedule_supplemental_cleanup(hass, entry)
        return
    if await _async_reconcile_existing_local_mqtt(
        hass,
        entry,
        coordinator,
        configured_settings,
        should_run=should_run,
    ):
        return
    if not should_run:
        _set_local_mqtt_restart_after_cleanup(bucket, should_run=False)
        coordinator.set_local_mqtt_client(None)
        return
    await _async_start_new_local_mqtt_client(
        hass,
        entry,
        coordinator,
        configured_settings,
        snapshot_interval_sec=snapshot_interval_sec,
    )
    # Device-side publishing remains configured separately through the
    # App-proven 3046/BLE-113 command router.


def _coordinator_shutdown_task_from_record(
    record: object,
    coordinator: JackerySolarVaultCoordinator,
) -> tuple[asyncio.Task[Any] | None, bool]:
    """Return a reusable owned shutdown task and whether another one blocks it."""
    if (
        isinstance(record, tuple)
        and len(record) == _RUNTIME_TASK_RECORD_LENGTH
        and record[0] is coordinator
        and isinstance(record[1], asyncio.Task)
    ):
        return record[1], False
    blocked = (
        isinstance(record, tuple)
        and len(record) == _RUNTIME_TASK_RECORD_LENGTH
        and isinstance(record[1], asyncio.Task)
        and not record[1].done()
    )
    return None, blocked


def _coordinator_shutdown_record_matches(
    record: object,
    coordinator: JackerySolarVaultCoordinator,
    task: asyncio.Task[Any],
) -> bool:
    """Return whether a runtime shutdown record belongs to coordinator and task."""
    return (
        isinstance(record, tuple)
        and len(record) == _RUNTIME_TASK_RECORD_LENGTH
        and record[0] is coordinator
        and record[1] is task
    )


def _create_coordinator_shutdown_task(
    coordinator: JackerySolarVaultCoordinator,
    hass: HomeAssistant | None,
    entry: ConfigEntry | None,
    bucket: dict[str, Any] | None,
) -> asyncio.Task[Any]:
    """Create one shutdown task under Home Assistant ownership when available."""
    operation = coordinator.async_shutdown()
    task = (
        hass.async_create_background_task(
            operation,
            f"{DOMAIN}_coordinator_shutdown_{entry.entry_id}",
            eager_start=False,
        )
        if hass is not None and entry is not None and bucket is not None
        else asyncio.create_task(operation)
    )
    if bucket is not None:
        bucket[_COORDINATOR_SHUTDOWN_RUNTIME_KEY] = (coordinator, task)
    return task


def _schedule_coordinator_shutdown_reaper(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
    shutdown_task: asyncio.Task[Any],
    bucket: dict[str, Any],
) -> None:
    """Schedule one HA-owned reaper for a shutdown task still in flight."""
    if hass.state is not CoreState.running:
        return
    existing = bucket.get(_COORDINATOR_SHUTDOWN_REAPER_TASK_RUNTIME_KEY)
    if isinstance(existing, asyncio.Task) and not existing.done():
        return
    reaper = hass.async_create_background_task(
        _async_reap_coordinator_shutdown(
            hass,
            entry,
            coordinator,
            shutdown_task,
        ),
        name=f"{DOMAIN}_coordinator_shutdown_reaper_{entry.entry_id}",
        eager_start=False,
    )
    _store_entry_runtime_task(
        hass,
        entry,
        _COORDINATOR_SHUTDOWN_REAPER_TASK_RUNTIME_KEY,
        reaper,
    )


def _clear_coordinator_shutdown_record(
    bucket: dict[str, Any] | None,
    coordinator: JackerySolarVaultCoordinator,
    shutdown_task: asyncio.Task[Any],
) -> None:
    """Clear only the shutdown record owned by this coordinator and task."""
    if bucket is None:
        return
    current = bucket.get(_COORDINATOR_SHUTDOWN_RUNTIME_KEY)
    if _coordinator_shutdown_record_matches(current, coordinator, shutdown_task):
        bucket.pop(_COORDINATOR_SHUTDOWN_RUNTIME_KEY, None)


def _preserve_or_cancel_coordinator_shutdown(
    coordinator: JackerySolarVaultCoordinator,
    shutdown_task: asyncio.Task[Any],
    hass: HomeAssistant | None,
    entry: ConfigEntry | None,
    bucket: dict[str, Any] | None,
) -> None:
    """Preserve an HA-owned shutdown for reaping, otherwise cancel it."""
    if hass is not None and entry is not None and bucket is not None:
        _schedule_coordinator_shutdown_reaper(
            hass,
            entry,
            coordinator,
            shutdown_task,
            bucket,
        )
    elif not shutdown_task.done():
        shutdown_task.cancel()


def _defer_or_clear_failed_coordinator_shutdown(
    coordinator: JackerySolarVaultCoordinator,
    shutdown_task: asyncio.Task[Any],
    hass: HomeAssistant | None,
    entry: ConfigEntry | None,
    bucket: dict[str, Any] | None,
) -> None:
    """Keep HA-owned failures reapable; clear unowned completed records."""
    if hass is not None and entry is not None and bucket is not None:
        _schedule_coordinator_shutdown_reaper(
            hass,
            entry,
            coordinator,
            shutdown_task,
            bucket,
        )
    else:
        _clear_coordinator_shutdown_record(bucket, coordinator, shutdown_task)


async def _async_shutdown_coordinator_bounded(
    coordinator: JackerySolarVaultCoordinator,
    *,
    context: str,
    hass: HomeAssistant | None = None,
    entry: ConfigEntry | None = None,
) -> bool:
    """Shut down a coordinator without allowing cleanup to hang indefinitely."""
    ha_task_owner = (
        hass is not None
        and entry is not None
        and isinstance(getattr(hass, "loop", None), asyncio.AbstractEventLoop)
    )
    bucket = (
        _entry_runtime_bucket(hass, entry)
        if ha_task_owner and hass is not None and entry is not None
        else None
    )
    record = bucket.get(_COORDINATOR_SHUTDOWN_RUNTIME_KEY) if bucket else None
    shutdown_task, blocked = _coordinator_shutdown_task_from_record(
        record,
        coordinator,
    )
    if blocked:
        _LOGGER.warning(
            "Jackery coordinator shutdown during %s is blocked by another "
            "owned shutdown task",
            context,
        )
        return False
    if shutdown_task is None:
        shutdown_task = _create_coordinator_shutdown_task(
            coordinator,
            hass,
            entry,
            bucket,
        )

    try:
        done, pending = await asyncio.wait(
            {shutdown_task},
            timeout=COORDINATOR_SHUTDOWN_TIMEOUT_SEC,
        )
    except asyncio.CancelledError:
        _preserve_or_cancel_coordinator_shutdown(
            coordinator,
            shutdown_task,
            hass,
            entry,
            bucket,
        )
        raise
    if pending:
        _preserve_or_cancel_coordinator_shutdown(
            coordinator,
            shutdown_task,
            hass,
            entry,
            bucket,
        )
        _LOGGER.warning(
            "Jackery coordinator shutdown exceeded %.0fs during %s",
            COORDINATOR_SHUTDOWN_TIMEOUT_SEC,
            context,
        )
        return False
    try:
        next(iter(done)).result()
    except asyncio.CancelledError:
        _defer_or_clear_failed_coordinator_shutdown(
            coordinator,
            shutdown_task,
            hass,
            entry,
            bucket,
        )
        return False
    except Exception as err:  # ruff: ignore[blind-except]
        _defer_or_clear_failed_coordinator_shutdown(
            coordinator,
            shutdown_task,
            hass,
            entry,
            bucket,
        )
        _LOGGER.warning(
            "Jackery coordinator shutdown failed during %s: %s: %s",
            context,
            type(err).__name__,
            err,
        )
        return False
    _clear_coordinator_shutdown_record(bucket, coordinator, shutdown_task)
    return True


def _runtime_bucket_if_present(
    hass: HomeAssistant,
    entry: ConfigEntry,
) -> dict[str, Any] | None:
    """Return an existing per-entry runtime bucket without creating one."""
    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    return bucket if isinstance(bucket, dict) else None


def _release_reaped_coordinator(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
    task: asyncio.Task[Any],
    bucket: dict[str, Any],
) -> None:
    """Release runtime references after a deferred shutdown completes."""
    _clear_coordinator_shutdown_record(bucket, coordinator, task)
    _defer_supplemental_transports(hass, entry, coordinator)
    if bucket.get(_UNLOADING_COORDINATOR_RUNTIME_KEY) is coordinator:
        bucket.pop(_UNLOADING_COORDINATOR_RUNTIME_KEY, None)
    if bucket.get(_PRIMARY_SETUP_COORDINATOR_RUNTIME_KEY) is coordinator:
        bucket.pop(_PRIMARY_SETUP_COORDINATOR_RUNTIME_KEY, None)
    if entry.runtime_data is coordinator:
        entry.runtime_data = cast("Any", None)
    _schedule_supplemental_cleanup(hass, cast("JackeryConfigEntry", entry))


def _shutdown_reaper_still_owns_runtime(
    entry: ConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
    task: asyncio.Task[Any],
    bucket: dict[str, Any],
) -> bool:
    """Return whether the failed shutdown still owns an active runtime fence."""
    if (
        bucket.get(_UNLOADING_COORDINATOR_RUNTIME_KEY) is coordinator
        or entry.runtime_data is coordinator
    ):
        return True
    _clear_coordinator_shutdown_record(bucket, coordinator, task)
    return False


def _next_coordinator_shutdown_task(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
    current_task: asyncio.Task[Any],
    bucket: dict[str, Any],
) -> asyncio.Task[Any] | None:
    """Reuse a replacement shutdown task or create the next bounded retry."""
    record = bucket.get(_COORDINATOR_SHUTDOWN_RUNTIME_KEY)
    replacement, blocked = _coordinator_shutdown_task_from_record(
        record,
        coordinator,
    )
    if replacement is not None and replacement is not current_task:
        return replacement
    if blocked or not _coordinator_shutdown_record_matches(
        record,
        coordinator,
        current_task,
    ):
        return None
    retry_task = hass.async_create_background_task(
        coordinator.async_shutdown(),
        f"{DOMAIN}_coordinator_shutdown_retry_{entry.entry_id}",
        eager_start=False,
    )
    bucket[_COORDINATOR_SHUTDOWN_RUNTIME_KEY] = (coordinator, retry_task)
    return retry_task


async def _async_reap_coordinator_shutdown(
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
    shutdown_task: asyncio.Task[Any],
) -> None:
    """Retry an owned shutdown until success, then release its runtime fence."""
    current_task = shutdown_task
    while True:
        try:
            await asyncio.shield(current_task)
        except asyncio.CancelledError:
            # Reaper cancellation must never cancel a separately owned shutdown.
            if not current_task.done() or hass.state is not CoreState.running:
                return
            _LOGGER.warning(
                "Jackery deferred coordinator shutdown task was cancelled for "
                "entry %s; retrying",
                entry.entry_id,
            )
        except Exception as err:  # ruff: ignore[blind-except]
            _LOGGER.warning(
                "Jackery deferred coordinator shutdown failed for entry %s: %s: %s",
                entry.entry_id,
                type(err).__name__,
                err,
            )
        else:
            bucket = _runtime_bucket_if_present(hass, entry)
            if bucket is not None:
                _release_reaped_coordinator(
                    hass,
                    entry,
                    coordinator,
                    current_task,
                    bucket,
                )
            return

        bucket = _runtime_bucket_if_present(hass, entry)
        if bucket is None or not _shutdown_reaper_still_owns_runtime(
            entry,
            coordinator,
            current_task,
            bucket,
        ):
            return
        await asyncio.sleep(_COORDINATOR_SHUTDOWN_RETRY_SEC)
        next_task = _next_coordinator_shutdown_task(
            hass,
            entry,
            coordinator,
            current_task,
            bucket,
        )
        if next_task is None:
            return
        current_task = next_task


async def _async_entry_updated(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    *,
    previous_options_override: dict[str, Any] | None = None,
) -> None:
    """Reload credential changes; apply ordinary options in place."""
    coordinator = getattr(entry, "runtime_data", None)
    if coordinator is None or not _entry_owns_coordinator(hass, entry, coordinator):
        return
    bucket = _entry_runtime_bucket(hass, entry)
    previous_options = (
        previous_options_override
        if previous_options_override is not None
        else bucket.get(_OPTIONS_SNAPSHOT_RUNTIME_KEY)
    )
    previous_data = bucket.get(_ENTRY_DATA_SNAPSHOT_RUNTIME_KEY)
    current_options = dict(entry.options)
    current_data = dict(entry.data)
    bucket[_OPTIONS_SNAPSHOT_RUNTIME_KEY] = current_options
    bucket[_ENTRY_DATA_SNAPSHOT_RUNTIME_KEY] = current_data

    if isinstance(previous_data, dict) and current_data != previous_data:
        await hass.config_entries.async_reload(entry.entry_id)
        return
    old_options = previous_options if isinstance(previous_options, dict) else {}
    changed_keys = {
        key
        for key in old_options.keys() | current_options.keys()
        if old_options.get(key) != current_options.get(key)
    }
    if not changed_keys:
        return
    adopted_values = bucket.pop(_DEVICE_MQTT_ADOPTED_OPTIONS_RUNTIME_KEY, None)
    adopted_keys = (
        {
            key
            for key, value in adopted_values.items()
            if isinstance(key, str) and current_options.get(key) == value
        }
        if isinstance(adopted_values, dict)
        else set()
    )
    if CONF_SCAN_INTERVAL in changed_keys:
        # Applied in place: the coordinator schedules each next cycle from its
        # own ``update_interval``, so no reload is needed and every running
        # transport keeps its session.
        interval_sec = _configured_scan_interval_sec(entry)
        coordinator.async_set_scan_interval(timedelta(seconds=interval_sec))
        if local_mqtt_client := _local_mqtt_client(hass, entry):
            local_mqtt_client.set_snapshot_interval(interval_sec)
        _LOGGER.info(
            "Jackery: coordinator polling interval changed to %ss",
            interval_sec,
        )
    local_mqtt_changed = bool(changed_keys & _LOCAL_MQTT_OPTION_KEYS)
    if CONF_ENABLE_BLE_TRANSPORT in changed_keys and not local_mqtt_changed:
        # Applied in place: the BLE transport is reconciled through the
        # layer-5 start task without tearing down the entry.
        _LOGGER.info("Jackery: BLE transport option changed, reconciling")
        _schedule_layer5_start_if_ready(hass, entry, coordinator, changed_keys)
    if changed_keys & _ENTITY_CREATING_OPTION_KEYS:
        _async_clean_legacy_entities(hass, entry)
        coordinator.async_update_listeners()
    if local_mqtt_changed:
        # Listener-facing keys are applied in place; token-only changes still
        # require the independent device-side 3046/3047 reconciliation.
        _LOGGER.info("Jackery: local MQTT options changed, reconciling Layer 5")
        _schedule_layer5_start_if_ready(
            hass,
            entry,
            coordinator,
            changed_keys,
            device_config_keys=(changed_keys & _LOCAL_MQTT_OPTION_KEYS) - adopted_keys,
        )


async def _async_stop_entry_local_mqtt(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator | None,
) -> None:
    """Stop an entry's local MQTT listener without dropping its drain."""
    local_mqtt = _local_mqtt_client(hass, entry)
    if local_mqtt is None or await _async_stop_local_mqtt_client(
        hass,
        entry,
        local_mqtt,
    ):
        return
    if coordinator is not None and coordinator.local_mqtt_client is local_mqtt:
        coordinator.set_local_mqtt_client(None)
    _defer_supplemental_local_mqtt(hass, entry, local_mqtt)


async def _async_unload_partial_platforms(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> bool:
    """Unload partially set-up platforms within the coordinator shutdown bound."""
    try:
        async with asyncio.timeout(COORDINATOR_SHUTDOWN_TIMEOUT_SEC):
            return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    except TimeoutError:
        _LOGGER.warning(
            "Jackery partial platform rollback exceeded %.0fs",
            COORDINATOR_SHUTDOWN_TIMEOUT_SEC,
        )
    except Exception as err:  # ruff: ignore[blind-except]
        _LOGGER.warning("Jackery partial platform rollback failed: %s", err)
    return False


def _clear_entry_coordinator_runtime_references(
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator | None,
    bucket: dict[str, Any] | None,
) -> None:
    """Clear runtime references only when they still point to this coordinator."""
    if coordinator is not None and bucket is not None:
        if bucket.get(_UNLOADING_COORDINATOR_RUNTIME_KEY) is coordinator:
            bucket.pop(_UNLOADING_COORDINATOR_RUNTIME_KEY, None)
        if bucket.get(_PRIMARY_SETUP_COORDINATOR_RUNTIME_KEY) is coordinator:
            bucket.pop(_PRIMARY_SETUP_COORDINATOR_RUNTIME_KEY, None)
    if entry.runtime_data is coordinator:
        entry.runtime_data = cast("Any", None)


async def _async_run_entry_setup_rollback(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
    bucket: dict[str, Any],
    *,
    platforms_started: bool,
) -> tuple[bool, bool, bool]:
    """Run rollback operations and return platform, shutdown, cleanup outcomes."""
    await _async_cancel_layer5_start_task(hass, entry)
    await _async_cancel_runtime_task(
        hass,
        entry,
        _OPTIONS_RECONCILE_TASK_RUNTIME_KEY,
        label="options reconcile",
    )
    _clear_option_reconcile_runtime_state(bucket)
    await _async_stop_entry_local_mqtt(hass, entry, coordinator)
    platforms_rolled_back = (
        not platforms_started or await _async_unload_partial_platforms(hass, entry)
    )
    if not platforms_rolled_back:
        return False, False, False
    shutdown_ok = await _async_shutdown_coordinator_bounded(
        coordinator,
        context="setup rollback",
        hass=hass,
        entry=entry,
    )
    if not shutdown_ok:
        _LOGGER.warning(
            "Jackery HTTP coordinator did not stop during setup rollback; "
            "preserving the runtime fence",
        )
        return True, False, False
    _defer_supplemental_transports(hass, entry, coordinator)
    _schedule_supplemental_cleanup(hass, entry)
    return True, True, True


async def _async_rollback_entry_setup(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
    *,
    platforms_started: bool,
) -> bool:
    """Roll back HTTP/platform setup while reaping Layer-5 resources separately."""
    bucket = _entry_runtime_bucket(hass, entry)
    bucket[_UNLOADING_COORDINATOR_RUNTIME_KEY] = coordinator
    platforms_rolled_back = not platforms_started
    shutdown_ok = False
    rollback_ok = True
    cleanup_scheduled = False
    try:
        (
            platforms_rolled_back,
            shutdown_ok,
            cleanup_scheduled,
        ) = await _async_run_entry_setup_rollback(
            hass,
            entry,
            coordinator,
            bucket,
            platforms_started=platforms_started,
        )
    except Exception as err:  # ruff: ignore[blind-except]
        rollback_ok = False
        _LOGGER.warning("Jackery setup rollback cleanup failed: %s", err)
    finally:
        if not cleanup_scheduled and _supplemental_cleanup_pending(hass, entry):
            _schedule_supplemental_cleanup(hass, entry)
        success = rollback_ok and platforms_rolled_back and shutdown_ok
        if success:
            _clear_entry_coordinator_runtime_references(
                entry,
                coordinator,
                _runtime_bucket_if_present(hass, entry),
            )
    return rollback_ok and platforms_rolled_back and shutdown_ok


def _adopt_device_local_mqtt_config(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
    config: dict[str, Any],
) -> None:
    """Apply a confirmed device 3047 readback to the direct listener options."""
    if not _entry_owns_coordinator(hass, entry, coordinator):
        return
    enabled = safe_bool(config.get("enable"))
    if enabled is None:
        return
    host = str(config.get("ip") or "").strip()
    port = safe_int(config.get("port"))
    if enabled and (not host or port is None or not 1 <= port <= _MAX_TCP_PORT):
        return

    updates: dict[str, Any] = {CONF_THIRD_PARTY_MQTT_ENABLE: enabled}
    if enabled:
        updates.update({
            CONF_THIRD_PARTY_MQTT_IP: host,
            CONF_THIRD_PARTY_MQTT_PORT: port,
            CONF_THIRD_PARTY_MQTT_USERNAME: str(config.get("userName") or ""),
            CONF_THIRD_PARTY_MQTT_PASSWORD: str(config.get("password") or ""),
            CONF_THIRD_PARTY_MQTT_TOKEN: str(config.get("token") or ""),
        })
    options = dict(entry.options)
    changed = {
        key: value for key, value in updates.items() if options.get(key) != value
    }
    if not changed:
        return
    options.update(changed)
    bucket = _entry_runtime_bucket(hass, entry)
    adopted = bucket.get(_DEVICE_MQTT_ADOPTED_OPTIONS_RUNTIME_KEY)
    if not isinstance(adopted, dict):
        adopted = {}
        bucket[_DEVICE_MQTT_ADOPTED_OPTIONS_RUNTIME_KEY] = adopted
    adopted.update(changed)
    hass.config_entries.async_update_entry(entry, options=options)
    entry.async_create_task(
        hass,
        _async_entry_updated(hass, entry),
        name=f"{DOMAIN}_adopted_local_mqtt_options_{entry.entry_id}",
        eager_start=False,
    )


async def async_setup_entry(hass: HomeAssistant, entry: JackeryConfigEntry) -> bool:
    """Set up the Jackery SolarVault config entry and optional transports.

    Performs authentication, constructs the coordinator, runs discovery and the initial
    refresh, and starts cloud MQTT plus an optional local MQTT listener and BLE
    transport. Transport startup failures that indicate invalid credentials will surface
    re-auth; other transport failures are logged and do not block setup. On successful
    setup the coordinator is stored on the entry's runtime state and platform setups and
    listeners are registered. If setup fails after the coordinator is created, the
    coordinator is shut down and the entry's runtime state is cleared before the error
    is re-raised.

    Returns:
        True if setup completed successfully.
    """
    if not await _async_release_fenced_coordinator(hass, entry):
        msg = (
            "A previous Jackery coordinator is still stopping; "
            "Home Assistant will retry setup"
        )
        raise ConfigEntryNotReady(msg)
    # The entry update listener is registered only after setup completes, so
    # pruning legacy fields here cannot reload or pause HTTP/L5 transports.
    _async_migrate_legacy_local_mqtt_options(hass, entry)
    _async_prune_removed_local_mqtt_tls_options(hass, entry)
    await _async_cancel_layer5_start_task(hass, entry)

    session = async_get_clientsession(hass)
    api = JackeryApi(
        session=session,
        account=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        mqtt_mac_id=entry.data.get(CONF_MQTT_MAC_ID),
        region_code=entry.data.get(CONF_REGION_CODE),
    )

    interval_sec = _configured_scan_interval_sec(entry)
    coordinator = JackerySolarVaultCoordinator(
        hass,
        entry,
        api,
        timedelta(seconds=interval_sec),
    )

    coordinator.set_local_mqtt_config_observer(
        lambda config: _adopt_device_local_mqtt_config(
            hass,
            entry,
            coordinator,
            config,
        )
    )
    entry.runtime_data = coordinator
    _LOGGER.info("Jackery: coordinator polling interval set to %ss", interval_sec)

    setup_complete = False
    platforms_started = False
    try:
        cache_ready = await _async_load_entry_caches(hass, entry, coordinator)
        runtime_bucket = _entry_runtime_bucket(hass, entry)
        runtime_bucket[_PRIMARY_SETUP_COORDINATOR_RUNTIME_KEY] = coordinator
        runtime_bucket[_OPTIONS_SNAPSHOT_RUNTIME_KEY] = dict(entry.options)
        runtime_bucket[_ENTRY_DATA_SNAPSHOT_RUNTIME_KEY] = dict(entry.data)
        cached_http_startup: asyncio.Task[None] | None = None
        if cache_ready:
            _schedule_layer5_start_if_ready(hass, entry, coordinator)
            cached_http_startup = entry.async_create_background_task(
                hass,
                _async_prepare_primary_http_from_cache(hass, entry, coordinator),
                name=f"{DOMAIN}_cached_http_startup_{entry.entry_id}",
                eager_start=True,
            )
            # Propagate immediately available auth/cancellation failures while
            # never waiting for network I/O on a cache-backed reload.
            await asyncio.sleep(0)
            if cached_http_startup.done():
                cached_http_startup.result()
        if not cache_ready:
            try:
                async with asyncio.timeout(_PRIMARY_SETUP_TIMEOUT_SEC):
                    await _async_prepare_primary_http(hass, entry, coordinator)
            except TimeoutError as err:
                raise ConfigEntryNotReady(_HTTP_SETUP_TIMEOUT_MESSAGE) from err

        _async_clean_legacy_entities(hass, entry)
        _async_remove_legacy_system_parent_devices(hass, entry)
        # Must run before the platforms add entities: subdevice DeviceInfo
        # resolves `via_device_id` against an already-registered parent.
        _async_register_main_devices(hass, entry, coordinator)
        platforms_started = True
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

        # Statistics imports starten (non-blocking)
        coordinator.async_start_statistics_imports()

        if not cache_ready:
            _schedule_layer5_start_if_ready(hass, entry, coordinator)

        setup_complete = True
        return True
    finally:
        if not setup_complete:
            rollback_ok = await _async_rollback_entry_setup(
                hass,
                entry,
                coordinator,
                platforms_started=platforms_started,
            )
            if not rollback_ok:
                _LOGGER.error(
                    "Jackery setup rollback was incomplete; "
                    "the runtime remains fenced until cleanup succeeds",
                )


# -----------------------------------------------------------------------------
# Registry cleanup helpers
# -----------------------------------------------------------------------------


def _async_remove_stale_energy_helpers(hass: HomeAssistant) -> None:
    """Remove stale Energy helper entities created without a measurement unit.

    Scans the entity registry for entities whose entity_id starts with the configured
    STALE_ENERGY_HELPER_PREFIX and ends with STALE_NET_POWER_SUFFIX. If an entity's
    current state has no `unit_of_measurement` (missing or empty) and its entity_id
    contains any token from STALE_HELPER_VENDOR_TOKENS, the entity is removed from
    the registry and an informational log entry is emitted.
    """
    registry = er.async_get(hass)
    to_remove: list[str] = []
    for ent in registry.entities.values():
        entity_id = ent.entity_id or ""
        if not entity_id.startswith(STALE_ENERGY_HELPER_PREFIX):
            continue
        if not entity_id.endswith(STALE_NET_POWER_SUFFIX):
            continue
        lowered = entity_id.lower()
        state = hass.states.get(entity_id)
        unit = None if state is None else state.attributes.get("unit_of_measurement")
        if unit not in {None, ""}:
            continue

        # Only stale helpers that explicitly reference this integration should
        # be removed. A generic battery charge/discharge helper without a unit
        # may belong to another integration or to a user-created template.
        if any(token in lowered for token in STALE_HELPER_VENDOR_TOKENS):
            to_remove.append(entity_id)

    for entity_id in to_remove:
        _LOGGER.info(
            "Removing stale Energy helper without unit: %s "
            "(please recreate with Jackery battery_net_power)",
            entity_id,
        )
        registry.async_remove(entity_id)


_LEGACY_UID_HEAD_RE = re.compile(r"\d+(?:_battery_pack_\d+)?")
_PORTABLE_SCREEN_UID_SUFFIX = "_portable_screen"
_PORTABLE_SCREEN_TRANSLATION_KEY = "portable_screen"
_GRID_STANDARD_UID_SUFFIX = "_grid_standard"
_GRID_STANDARD_TRANSLATION_KEY = "grid_standard"
_BATTERY_PACK_INDEX_MAX = 5


def _legacy_suffix_matches(uid: str, key_suffix: str) -> bool:
    """Check whether a legacy device head is followed by ``key_suffix``.

    A legacy device head has the form `<digits>` or `<digits>_battery_pack_<digits>`.
    This function returns `True` only when `uid` ends with `key_suffix` and the
    substring before that suffix exactly matches the legacy head pattern.

    Returns:
        `True` if `uid` is a legacy head concatenated with `key_suffix`, `False`
        otherwise.
    """
    if not key_suffix:
        return _LEGACY_UID_HEAD_RE.fullmatch(uid) is not None
    if not uid.endswith(key_suffix):
        return False
    head = uid[: -len(key_suffix)]
    return _LEGACY_UID_HEAD_RE.fullmatch(head) is not None


def _async_migrate_portable_screen_entity(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> None:
    """Replace the obsolete portable-screen switch registry entry with a select.

    The entity platform domain cannot be changed in place. A new select entry is
    therefore created with the old entry's user-controlled registry metadata,
    then the switch entry is removed. Existing selects are never overwritten;
    a target owned by another config entry is treated as a collision and leaves
    the switch untouched.
    """
    registry = er.async_get(hass)
    for old_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        unique_id = old_entry.unique_id or ""
        if old_entry.domain != "switch" or not _legacy_suffix_matches(
            unique_id, _PORTABLE_SCREEN_UID_SUFFIX
        ):
            continue

        target_entity_id = registry.async_get_entity_id("select", DOMAIN, unique_id)
        if target_entity_id is not None:
            target_entry = registry.async_get(target_entity_id)
            if target_entry is None or target_entry.config_entry_id != entry.entry_id:
                _LOGGER.warning(
                    "Skipping portable-screen registry migration for %s: "
                    "select target %s belongs to another config entry",
                    old_entry.entity_id,
                    target_entity_id,
                )
                continue
            registry.async_remove(old_entry.entity_id)
            _LOGGER.info(
                "Removed obsolete portable-screen switch %s; select %s already exists",
                old_entry.entity_id,
                target_entity_id,
            )
            continue

        target_entry = registry.async_get_or_create(
            "select",
            DOMAIN,
            unique_id,
            config_entry=entry,
            device_id=old_entry.device_id,
            disabled_by=old_entry.disabled_by,
            hidden_by=old_entry.hidden_by,
            entity_category=old_entry.entity_category,
            has_entity_name=old_entry.has_entity_name,
            suggested_object_id=old_entry.entity_id.partition(".")[2],
            translation_key=_PORTABLE_SCREEN_TRANSLATION_KEY,
        )
        registry.async_update_entity(
            target_entry.entity_id,
            # ``async_update_entity`` erwartet fuer aliases eine Liste (labels
            # dagegen ein Set) — ein Set fuehrt zu einem Typfehler in der
            # Entity-Registry-API.
            aliases=list(old_entry.aliases),
            area_id=old_entry.area_id,
            categories=dict(old_entry.categories),
            disabled_by=old_entry.disabled_by,
            hidden_by=old_entry.hidden_by,
            icon=old_entry.icon,
            labels=set(old_entry.labels),
            name=old_entry.name,
        )
        registry.async_remove(old_entry.entity_id)
        _LOGGER.info(
            "Migrated portable-screen entity %s to %s",
            old_entry.entity_id,
            target_entry.entity_id,
        )


def _async_migrate_grid_standard_entity(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> None:
    """Replace the obsolete grid-standard text entry with a diagnostic sensor."""
    registry = er.async_get(hass)
    for old_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        unique_id = old_entry.unique_id or ""
        if (
            old_entry.domain != "text"
            or old_entry.platform != DOMAIN
            or unique_id == _GRID_STANDARD_UID_SUFFIX
            or not unique_id.endswith(_GRID_STANDARD_UID_SUFFIX)
        ):
            continue

        target_entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
        if target_entity_id is not None:
            target_entry = registry.async_get(target_entity_id)
            if target_entry is None or target_entry.config_entry_id != entry.entry_id:
                _LOGGER.warning(
                    "Skipping grid-standard registry migration for %s: "
                    "sensor target %s belongs to another config entry",
                    old_entry.entity_id,
                    target_entity_id,
                )
                continue
            registry.async_remove(old_entry.entity_id)
            _LOGGER.info(
                "Removed obsolete grid-standard text %s; sensor %s already exists",
                old_entry.entity_id,
                target_entity_id,
            )
            continue

        target_entry = registry.async_get_or_create(
            "sensor",
            DOMAIN,
            unique_id,
            config_entry=entry,
            device_id=old_entry.device_id,
            disabled_by=old_entry.disabled_by,
            hidden_by=old_entry.hidden_by,
            entity_category=EntityCategory.DIAGNOSTIC,
            has_entity_name=old_entry.has_entity_name,
            suggested_object_id=old_entry.entity_id.partition(".")[2],
            translation_key=_GRID_STANDARD_TRANSLATION_KEY,
        )
        registry.async_update_entity(
            target_entry.entity_id,
            # ``async_update_entity`` erwartet fuer aliases eine Liste (labels
            # dagegen ein Set) — ein Set fuehrt zu einem Typfehler in der
            # Entity-Registry-API.
            aliases=list(old_entry.aliases),
            area_id=old_entry.area_id,
            categories=dict(old_entry.categories),
            disabled_by=old_entry.disabled_by,
            hidden_by=old_entry.hidden_by,
            icon=old_entry.icon,
            labels=set(old_entry.labels),
            name=old_entry.name,
        )
        registry.async_remove(old_entry.entity_id)
        _LOGGER.info(
            "Migrated grid-standard entity %s to %s",
            old_entry.entity_id,
            target_entry.entity_id,
        )


def _device_stable_identity(device: dr.DeviceEntry) -> str:
    """Generate a stable identity hash from device info when serial is unavailable.

    Uses a combination of model, firmware, hardware, scan_name, and type_name
    to create a deterministic identifier that persists across HA restarts.
    """
    parts = []
    if device.model:
        parts.append(f"model:{device.model}")
    if device.sw_version:
        parts.append(f"fw:{device.sw_version}")
    if device.hw_version:
        parts.append(f"hw:{device.hw_version}")
    # These are from device entry attributes if available
    # We also check the device's name/suggested_area as fallback
    if device.name:
        parts.append(f"name:{device.name}")
    if device.suggested_area:
        parts.append(f"area:{device.suggested_area}")

    # Create deterministic hash from available info
    raw = "|".join(sorted(parts)) if parts else "unknown"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def _battery_pack_registry_identity(
    registry: dr.DeviceRegistry,
    device: dr.DeviceEntry,
) -> tuple[str, str, str] | None:
    """Return parent id, pack identifier, and suffix for one pack device."""
    if device.via_device_id is None:
        return None
    parent = registry.async_get(device.via_device_id)
    if parent is None:
        return None
    parent_identifiers = [
        identifier for domain, identifier in parent.identifiers if domain == DOMAIN
    ]
    for parent_device_id in parent_identifiers:
        prefix = f"{parent_device_id}_battery_pack_"
        pack_identifiers = [
            identifier
            for domain, identifier in device.identifiers
            if domain == DOMAIN and identifier.startswith(prefix)
        ]
        if len(pack_identifiers) == 1:
            pack_identifier = pack_identifiers[0]
            return (
                parent_device_id,
                pack_identifier,
                pack_identifier.removeprefix(prefix),
            )
    return None


def _async_migrate_smart_meter_identity(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> None:
    """Rekey the legacy parent-scoped smart-meter device to its accessory id."""
    coordinator = entry.runtime_data
    if not coordinator.data:
        return

    device_registry = dr.async_get(hass)
    for parent_device_id, payload in coordinator.data.items():
        smart_meter = payload.get(PAYLOAD_CT_METER)
        if not isinstance(smart_meter, dict) or not smart_meter:
            continue

        identity = smart_meter_identity(smart_meter)
        target_key = stable_subdevice_key("smart_meter", identity, 1)
        target_identifier = (DOMAIN, f"{parent_device_id}_{target_key}")
        target_device = device_registry.async_get_device_by_identifier(
            target_identifier,
            entry.entry_id,
        )
        legacy_identifiers = (
            (DOMAIN, f"{parent_device_id}_smart_meter"),
            (DOMAIN, f"{parent_device_id}_smart_meter_1"),
        )
        for legacy_identifier in legacy_identifiers:
            if legacy_identifier == target_identifier:
                continue
            legacy_device = device_registry.async_get_device_by_identifier(
                legacy_identifier,
                entry.entry_id,
            )
            if legacy_device is None:
                continue
            if target_device is not None and target_device.id != legacy_device.id:
                entity_registry = er.async_get(hass)
                legacy_entities = er.async_entries_for_device(
                    entity_registry,
                    legacy_device.id,
                    include_disabled_entities=True,
                )
                attributable_entities = [
                    entity
                    for entity in legacy_entities
                    if (
                        entity.config_entry_id == entry.entry_id
                        and entity.platform == DOMAIN
                    )
                    or (
                        entity.domain == "sensor"
                        and entity.platform == "energy"
                        and entity.entity_id.startswith("sensor.energy_grid_")
                    )
                ]
                for entity in attributable_entities:
                    entity_registry.async_update_entity(
                        entity.entity_id,
                        device_id=target_device.id,
                    )
                moved_entity_ids = {
                    entity.entity_id for entity in attributable_entities
                }
                remaining_entities = [
                    entity
                    for entity in legacy_entities
                    if entity.entity_id not in moved_entity_ids
                ]
                if remaining_entities:
                    _LOGGER.warning(
                        "Keeping legacy smart-meter registry device %s: target %s "
                        "already exists and %d entit%s remain attached",
                        legacy_identifier[1],
                        target_identifier[1],
                        len(remaining_entities),
                        "y" if len(remaining_entities) == 1 else "ies",
                    )
                    continue
                device_registry.async_remove_device(legacy_device.id)
                _LOGGER.info(
                    "Removed empty duplicate smart-meter registry device %s; "
                    "normalized target %s already exists",
                    legacy_identifier[1],
                    target_identifier[1],
                )
                continue

            new_identifiers = set(legacy_device.identifiers)
            new_identifiers.discard(legacy_identifier)
            new_identifiers.add(target_identifier)
            device_registry.async_update_device(
                legacy_device.id,
                new_identifiers=new_identifiers,
                serial_number=identity,
            )
            target_device = legacy_device
            _LOGGER.info(
                "Migrated smart-meter registry identity %s to %s",
                legacy_identifier[1],
                target_identifier[1],
            )


def _battery_pack_serial_token(serial: str, index: int) -> str:
    """Return the normalized serial token used by a stable pack key."""
    return stable_subdevice_key("battery_pack", serial, index).rsplit("_", 1)[-1]


def _observed_battery_pack_indices(
    coordinator: JackerySolarVaultCoordinator,
    parent_device_id: str,
) -> tuple[dict[str, int], dict[str, list[int]]]:
    """Map each observed serial and stable key to its live payload index."""
    serial_to_index: dict[str, int] = {}
    key_to_indices: dict[str, list[int]] = {}
    for index in range(1, _BATTERY_PACK_INDEX_MAX + 1):
        serial = coordinator.battery_pack_observed_serial(parent_device_id, index)
        if serial is None:
            continue
        serial_to_index[_battery_pack_serial_token(serial, index)] = index
        key = stable_subdevice_key("battery_pack", serial, index)
        key_to_indices.setdefault(key, []).append(index)
    return serial_to_index, key_to_indices


def _disable_ambiguous_battery_pack_overrides(
    coordinator: JackerySolarVaultCoordinator,
    parent_device_id: str,
    observed_indices: dict[str, list[int]],
) -> set[str]:
    """Disable serial overrides when a live payload repeats a normalized serial."""
    ambiguous = {key for key, indices in observed_indices.items() if len(indices) > 1}
    for key in ambiguous:
        for index in observed_indices[key]:
            coordinator.set_battery_pack_identity_override(
                parent_device_id,
                index,
                None,
            )
    if ambiguous:
        _LOGGER.warning(
            "Using index identities for %s: live payload repeats a "
            "normalized battery-pack serial",
            parent_device_id,
        )
    return ambiguous


def _seed_battery_pack_registry_identities(
    coordinator: JackerySolarVaultCoordinator,
    serial_records: dict[str, list[tuple[str, int | None]]],
    remaining_old_indices: dict[str, set[int]],
) -> None:
    """Seed session identities from already-migrated pack registry devices."""
    for parent_device_id, records in serial_records.items():
        keys = [
            stable_subdevice_key("battery_pack", serial, index or 1)
            for serial, index in records
        ]
        if len(keys) != len(set(keys)):
            _LOGGER.warning(
                "Skipping battery-pack registry identity seeding for %s: "
                "duplicate normalized serials",
                parent_device_id,
            )
            continue

        observed_serial_to_live_index, observed_indices = (
            _observed_battery_pack_indices(coordinator, parent_device_id)
        )
        ambiguous_keys = _disable_ambiguous_battery_pack_overrides(
            coordinator,
            parent_device_id,
            observed_indices,
        )

        protected_indices = set(remaining_old_indices.get(parent_device_id, ()))
        matched_keys: set[str] = set()
        blocked_keys = set(ambiguous_keys)
        for serial, fallback_index in sorted(records, key=operator.itemgetter(0)):
            # Use normalized serial to find the live index, not the registry
            # fallback_index
            norm_serial = _battery_pack_serial_token(serial, fallback_index or 1)
            live_index = observed_serial_to_live_index.get(norm_serial)
            if live_index is None:
                continue
            key = stable_subdevice_key("battery_pack", serial, live_index)
            if live_index in protected_indices:
                blocked_keys.add(key)
                continue
            coordinator.set_battery_pack_identity_override(
                parent_device_id, live_index, serial
            )
            matched_keys.add(key)

        used_indices = protected_indices | {
            index for indices in observed_indices.values() for index in indices
        }
        free_indices = [
            index
            for index in range(1, _BATTERY_PACK_INDEX_MAX + 1)
            if index not in used_indices
        ]
        for serial, stored_index in sorted(records, key=operator.itemgetter(0)):
            norm_serial = _battery_pack_serial_token(serial, stored_index or 1)
            live_index = observed_serial_to_live_index.get(norm_serial)
            if live_index is None:
                continue
            key = stable_subdevice_key("battery_pack", serial, live_index)
            if key in matched_keys or key in blocked_keys:
                continue
            # Use live_index for the override, but prefer stored_index if it's free
            if stored_index is not None and stored_index in free_indices:
                index = stored_index
                free_indices.remove(index)
            elif free_indices:
                index = free_indices.pop(0)
            else:
                _LOGGER.warning(
                    "Skipping ambiguous battery-pack identity %s for %s",
                    serial,
                    parent_device_id,
                )
                continue
            coordinator.set_battery_pack_identity_override(
                parent_device_id, index, serial
            )


type _BatteryPackMigrationCandidate = tuple[dr.DeviceEntry, str, str, int, str]
type _BatteryPackMigrationState = tuple[
    list[_BatteryPackMigrationCandidate],
    dict[tuple[str, str], set[str]],
    dict[str, set[int]],
    dict[str, list[tuple[str, int | None]]],
]


def _battery_pack_target_can_migrate(
    device_registry: dr.DeviceRegistry,
    candidate: _BatteryPackMigrationCandidate,
    candidates: list[_BatteryPackMigrationCandidate],
    target_owners: dict[tuple[str, str], set[str]],
    entry_id: str,
) -> bool:
    """Return whether one serial target has a deterministic registry owner."""
    device, parent_device_id, old_identifier, pack_index, serial = candidate
    pack_key = stable_subdevice_key("battery_pack", serial, pack_index)
    target_identifier = (DOMAIN, f"{parent_device_id}_{pack_key}")
    target_device = device_registry.async_get_device_by_identifier(
        target_identifier,
        entry_id,
    )
    target_owner_ids = target_owners.get(target_identifier, set())
    if target_device is None and len(target_owner_ids) > 1:
        canonical_index = min(
            candidate_index
            for (
                _,
                candidate_parent_id,
                _,
                candidate_index,
                candidate_serial,
            ) in candidates
            if candidate_parent_id == parent_device_id
            and stable_subdevice_key(
                "battery_pack",
                candidate_serial,
                candidate_index,
            )
            == pack_key
        )
        if pack_index != canonical_index:
            _LOGGER.debug(
                "Deferring duplicate battery-pack serial target %s for %s "
                "until canonical index %d is migrated",
                target_identifier[1],
                old_identifier,
                canonical_index,
            )
            return False
    elif target_device is None and len(target_owner_ids) != 1:
        _LOGGER.warning(
            "Skipping battery-pack registry migration for %s: unresolved "
            "serial target %s",
            old_identifier,
            target_identifier[1],
        )
        return False
    if target_device is not None and target_device.id != device.id:
        _LOGGER.debug(
            "Keeping numeric battery-pack registry device %s until existing "
            "serial target %s is topology-confirmed",
            old_identifier,
            target_identifier[1],
        )
        return False
    return True


def _battery_pack_entity_updates(
    entity_registry: er.EntityRegistry,
    entry: JackeryConfigEntry,
    device: dr.DeviceEntry,
    old_identifier: str,
    target_identifier: tuple[str, str],
) -> list[tuple[er.RegistryEntry, str]] | None:
    """Return collision-free entity unique-id updates for one pack migration."""
    old_prefix = f"{old_identifier}_"
    target_prefix = f"{target_identifier[1]}_"
    owned_entries = [
        entity
        for entity in er.async_entries_for_device(
            entity_registry,
            device.id,
            include_disabled_entities=True,
        )
        if entity.config_entry_id == entry.entry_id and entity.platform == DOMAIN
    ]
    if any(
        not (entity.unique_id or "").startswith(old_prefix) for entity in owned_entries
    ):
        _LOGGER.warning(
            "Skipping battery-pack registry migration for %s: mixed old/new "
            "entity identities",
            old_identifier,
        )
        return None

    updates: list[tuple[er.RegistryEntry, str]] = []
    target_keys: set[tuple[str, str]] = set()
    for entity in owned_entries:
        new_unique_id = target_prefix + entity.unique_id.removeprefix(old_prefix)
        target_key = (entity.domain, new_unique_id)
        target_entity_id = entity_registry.async_get_entity_id(
            entity.domain,
            DOMAIN,
            new_unique_id,
        )
        if target_key in target_keys or target_entity_id not in {
            None,
            entity.entity_id,
        }:
            _LOGGER.warning(
                "Skipping battery-pack registry migration for %s: target entity "
                "unique_id collision",
                old_identifier,
            )
            return None
        target_keys.add(target_key)
        updates.append((entity, new_unique_id))
    return updates


def _migrate_battery_pack_registry_candidate(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    candidate: _BatteryPackMigrationCandidate,
    state: _BatteryPackMigrationState,
) -> None:
    """Migrate one deterministic battery-pack registry candidate."""
    candidates, target_owners, remaining_old_indices, serial_records = state
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    coordinator = entry.runtime_data
    device, parent_device_id, old_identifier, pack_index, serial = candidate
    target_identifier = (
        DOMAIN,
        f"{parent_device_id}_{
            stable_subdevice_key(
                "battery_pack",
                serial,
                pack_index,
            )
        }",
    )
    if not _battery_pack_target_can_migrate(
        device_registry,
        candidate,
        candidates,
        target_owners,
        entry.entry_id,
    ):
        return
    entity_updates = _battery_pack_entity_updates(
        entity_registry,
        entry,
        device,
        old_identifier,
        target_identifier,
    )
    if entity_updates is None:
        return

    for entity, new_unique_id in entity_updates:
        entity_registry.async_update_entity(
            entity.entity_id,
            new_unique_id=new_unique_id,
        )
    new_identifiers = set(device.identifiers)
    new_identifiers.discard((DOMAIN, old_identifier))
    new_identifiers.add(target_identifier)
    device_registry.async_update_device(
        device.id,
        new_identifiers=new_identifiers,
        serial_number=serial,
    )
    remaining_old_indices[parent_device_id].discard(pack_index)
    coordinator.set_battery_pack_identity_override(parent_device_id, pack_index, serial)
    serial_records.setdefault(parent_device_id, []).append((serial, pack_index))
    _LOGGER.info(
        "Migrated battery-pack registry identity %s to %s (%d entities)",
        old_identifier,
        target_identifier[1],
        len(entity_updates),
    )


def _async_migrate_battery_pack_identities(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> None:
    """Migrate index-based battery-pack registry identities to serial keys."""
    device_registry = dr.async_get(hass)
    old_candidates: list[_BatteryPackMigrationCandidate] = []
    serial_records: dict[str, list[tuple[str, int | None]]] = {}
    remaining_old_indices: dict[str, set[int]] = {}
    target_owners: dict[tuple[str, str], set[str]] = {}

    for device in dr.async_entries_for_config_entry(device_registry, entry.entry_id):
        identity = _battery_pack_registry_identity(device_registry, device)
        if identity is None:
            continue
        parent_device_id, current_identifier, suffix = identity
        stored_serial = nonblank_text(device.serial_number)
        numeric_index = int(suffix) if suffix.isdecimal() else None
        if stored_serial is not None and current_identifier == (
            f"{parent_device_id}_"
            f"{stable_subdevice_key("battery_pack", stored_serial, numeric_index or 1)}"
        ):
            serial_records.setdefault(parent_device_id, []).append((
                stored_serial,
                numeric_index,
            ))
            target_owners.setdefault((DOMAIN, current_identifier), set()).add(device.id)
            continue
        if numeric_index is None or not 1 <= numeric_index <= _BATTERY_PACK_INDEX_MAX:
            continue

        remaining_old_indices.setdefault(parent_device_id, set()).add(numeric_index)
        live_serial = entry.runtime_data.battery_pack_observed_serial(
            parent_device_id, numeric_index
        )
        entry.runtime_data.set_battery_pack_identity_override(
            parent_device_id, numeric_index, None
        )
        if (
            stored_serial is not None
            and live_serial is not None
            and stable_subdevice_key("battery_pack", stored_serial, numeric_index)
            != stable_subdevice_key("battery_pack", live_serial, numeric_index)
        ):
            _LOGGER.warning(
                "Skipping battery-pack registry migration for %s: stored serial "
                "%s conflicts with live serial %s",
                current_identifier,
                stored_serial,
                live_serial,
            )
            continue
        serial = stored_serial or live_serial
        if serial is None:
            continue
        old_candidates.append((
            device,
            parent_device_id,
            current_identifier,
            numeric_index,
            serial,
        ))
        target_identifier = (
            DOMAIN,
            (
                f"{parent_device_id}_"
                f"{stable_subdevice_key("battery_pack", serial, numeric_index)}"
            ),
        )
        target_owners.setdefault(target_identifier, set()).add(device.id)

    migration_state: _BatteryPackMigrationState = (
        old_candidates,
        target_owners,
        remaining_old_indices,
        serial_records,
    )
    for candidate in old_candidates:
        _migrate_battery_pack_registry_candidate(
            hass,
            entry,
            candidate,
            migration_state,
        )

    _async_migrate_parent_attached_battery_pack_entities(
        hass,
        entry,
        serial_records,
        remaining_old_indices,
    )

    _seed_battery_pack_registry_identities(
        entry.runtime_data,
        serial_records,
        remaining_old_indices,
    )


def _move_parent_attached_battery_pack_entities(
    entity_registry: er.EntityRegistry,
    parent_entries: list[er.RegistryEntry],
    target_device_id: str,
    legacy_prefix: str,
    target_prefix: str,
) -> int:
    """Move matching parent entities to one battery-pack child device."""
    moved = 0
    for entity in parent_entries:
        current_unique_id = entity.unique_id or ""
        if current_unique_id.startswith(legacy_prefix):
            new_unique_id = target_prefix + current_unique_id.removeprefix(
                legacy_prefix
            )
        elif current_unique_id.startswith(target_prefix):
            new_unique_id = current_unique_id
        else:
            continue
        target_entity_id = entity_registry.async_get_entity_id(
            entity.domain,
            DOMAIN,
            new_unique_id,
        )
        if target_entity_id not in {None, entity.entity_id}:
            _LOGGER.warning(
                "Skipping parent-attached battery-pack entity migration "
                "for %s: target unique_id collision %s",
                entity.entity_id,
                new_unique_id,
            )
            continue
        update: dict[str, Any] = {"device_id": target_device_id}
        if new_unique_id != current_unique_id:
            update["new_unique_id"] = new_unique_id
        entity_registry.async_update_entity(entity.entity_id, **update)
        moved += 1
    return moved


def _async_migrate_parent_attached_battery_pack_entities(  # ruff: ignore[too-many-locals]
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    serial_records: dict[str, list[tuple[str, int | None]]],
    remaining_old_indices: dict[str, set[int]],
) -> None:
    """Move legacy pack entities that are still attached to the parent device."""
    coordinator = entry.runtime_data
    if not coordinator.data:
        return

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    for parent_device_id, payload in coordinator.data.items():
        parent_device = device_registry.async_get_device_by_identifier(
            (DOMAIN, parent_device_id),
            entry.entry_id,
        )
        if parent_device is None:
            continue
        raw_packs = payload.get(PAYLOAD_BATTERY_PACKS)
        if not isinstance(raw_packs, list):
            continue
        packs = sorted_battery_pack_payloads(raw_packs)
        parent_entries = [
            entity
            for entity in er.async_entries_for_device(
                entity_registry,
                parent_device.id,
                include_disabled_entities=True,
            )
            if entity.config_entry_id == entry.entry_id
            and entity.platform == DOMAIN
            and entity.unique_id is not None
        ]
        for index, pack in enumerate(packs[:_BATTERY_PACK_INDEX_MAX], start=1):
            if index in remaining_old_indices.get(parent_device_id, set()):
                continue
            serial = battery_pack_serial(
                pack
            ) or coordinator.battery_pack_identity_serial(parent_device_id, index)
            pack_key = stable_subdevice_key("battery_pack", serial, index)
            target_identifier = (DOMAIN, f"{parent_device_id}_{pack_key}")
            parent_entry = device_registry.async_get_device_by_identifier(
                (DOMAIN, parent_device_id),
                entry.entry_id,
            )
            # HA 2026.8 takes a registry id here, not an identifier tuple.
            # Omit the link rather than guess when the parent is absent.
            if parent_entry is not None:
                target_device = device_registry.async_get_or_create(
                    config_entry_id=entry.entry_id,
                    identifiers={target_identifier},
                    serial_number=serial,
                    via_device_id=parent_entry.id,
                )
            else:
                target_device = device_registry.async_get_or_create(
                    config_entry_id=entry.entry_id,
                    identifiers={target_identifier},
                    serial_number=serial,
                )
            legacy_prefix = f"{parent_device_id}_battery_pack_{index}_"
            target_prefix = f"{target_identifier[1]}_"
            moved = _move_parent_attached_battery_pack_entities(
                entity_registry,
                parent_entries,
                target_device.id,
                legacy_prefix,
                target_prefix,
            )
            if moved:
                coordinator.set_battery_pack_identity_override(
                    parent_device_id, index, serial
                )
                remaining_old_indices.setdefault(parent_device_id, set()).discard(index)
                if serial is not None:
                    record = (serial, index)
                    records = serial_records.setdefault(parent_device_id, [])
                    if record not in records:
                        records.append(record)
                _LOGGER.info(
                    "Moved %d parent-attached battery-pack entit%s for %s pack %d "
                    "to child device %s",
                    moved,
                    "y" if moved == 1 else "ies",
                    parent_device_id,
                    index,
                    target_identifier[1],
                )


def _is_head_unit_battery_pack_duplicate(
    device_registry: dr.DeviceRegistry,
    device: dr.DeviceEntry,
    suffix: str,
) -> bool:
    """Return whether a pack device repeats its parent head-unit identity."""
    parent = (
        device_registry.async_get(
            device.via_device_id,
            include_child_devices=False,
        )
        if device.via_device_id is not None
        else None
    )
    parent_serial = nonblank_text(parent.serial_number) if parent is not None else None
    child_serial = nonblank_text(device.serial_number)
    return parent_serial is not None and parent_serial in {child_serial, suffix}


def _complete_battery_pack_topology(
    coordinator: JackerySolarVaultCoordinator,
    parent_device_id: str,
) -> list[dict[str, Any]] | None:
    """Return packs only when list and announced count independently agree."""
    payload = (coordinator.data or {}).get(parent_device_id)
    if not isinstance(payload, dict):
        return None
    raw_packs = payload.get(PAYLOAD_BATTERY_PACKS)
    properties = payload.get(PAYLOAD_PROPERTIES)
    if not isinstance(raw_packs, list) or not isinstance(properties, dict):
        return None
    packs = sorted_battery_pack_payloads(raw_packs)
    announced_count = safe_int(properties.get(FIELD_BAT_NUM))
    return (
        packs if announced_count is not None and announced_count == len(packs) else None
    )


def _live_battery_pack_identifiers(
    parent_device_id: str,
    packs: list[dict[str, Any]],
) -> set[str]:
    """Return stable child identifiers announced by the current topology."""
    return {
        f"{parent_device_id}_{
            stable_subdevice_key(
                "battery_pack",
                battery_pack_serial(pack),
                index,
            )
        }"
        for index, pack in enumerate(packs, start=1)
    }


type _BatteryPackTargetScope = tuple[str, str]


def _matching_serial_battery_pack_target(
    device_registry: dr.DeviceRegistry,
    scope: _BatteryPackTargetScope,
    device: dr.DeviceEntry,
    numeric_index: int,
    packs: list[dict[str, Any]],
) -> tuple[str, str] | None:
    """Return a verified serial target for one duplicate numeric pack device."""
    entry_id, parent_device_id = scope
    child_serial = nonblank_text(device.serial_number)
    live_serial = battery_pack_serial(packs[numeric_index - 1])
    if child_serial is None or live_serial is None:
        return None
    live_key = stable_subdevice_key("battery_pack", live_serial, numeric_index)
    identifier = (DOMAIN, f"{parent_device_id}_{live_key}")
    serial_device = device_registry.async_get_device_by_identifier(
        identifier,
        entry_id,
    )
    if (
        serial_device is None
        or serial_device.id == device.id
        or entry_id not in serial_device.config_entries
    ):
        return None
    stored_serial = nonblank_text(serial_device.serial_number)
    if stored_serial is None:
        return None
    if (
        stable_subdevice_key("battery_pack", child_serial, numeric_index) != live_key
        or stable_subdevice_key("battery_pack", stored_serial, numeric_index)
        != live_key
    ):
        return None
    expected_identity = (
        parent_device_id,
        identifier[1],
        live_key.removeprefix("battery_pack_"),
    )
    if (
        _battery_pack_registry_identity(device_registry, serial_device)
        != expected_identity
    ):
        return None
    return identifier[1], live_serial


def _async_remove_phantom_battery_pack_device(
    device_registry: dr.DeviceRegistry,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
    device: dr.DeviceEntry,
) -> None:
    """Remove one pack device only when current topology disproves it."""
    identity = _battery_pack_registry_identity(device_registry, device)
    if identity is None:
        return
    parent_device_id, current_identifier, suffix = identity
    if _is_head_unit_battery_pack_duplicate(device_registry, device, suffix):
        device_registry.async_remove_device(device.id)
        _LOGGER.info(
            "Removed head-unit duplicate battery-pack registry device %s",
            current_identifier,
        )
        return

    packs = _complete_battery_pack_topology(coordinator, parent_device_id)
    if packs is None or current_identifier in _live_battery_pack_identifiers(
        parent_device_id,
        packs,
    ):
        return
    numeric_index = int(suffix) if suffix.isdecimal() else None
    if numeric_index is not None and 1 <= numeric_index <= len(packs):
        target = _matching_serial_battery_pack_target(
            device_registry,
            (entry.entry_id, parent_device_id),
            device,
            numeric_index,
            packs,
        )
        if target is None:
            return
        target_identifier, live_serial = target
        device_registry.async_remove_device(device.id)
        coordinator.set_battery_pack_identity_override(
            parent_device_id,
            numeric_index,
            live_serial,
        )
        _LOGGER.info(
            "Removed duplicate numeric battery-pack registry device %s; "
            "serial target %s already exists",
            current_identifier,
            target_identifier,
        )
        return

    device_registry.async_remove_device(device.id)
    _LOGGER.info(
        "Removed stale battery-pack registry device %s; current topology "
        "contains %d pack(s)",
        current_identifier,
        len(packs),
    )


def _async_remove_phantom_battery_pack_devices(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> None:
    """Remove pack registry devices disproved by a complete current topology."""
    coordinator = entry.runtime_data
    device_registry = dr.async_get(hass)
    for device in list(
        dr.async_entries_for_config_entry(device_registry, entry.entry_id)
    ):
        _async_remove_phantom_battery_pack_device(
            device_registry,
            entry,
            coordinator,
            device,
        )


def _async_remove_entities_with_suffixes(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    *,
    domain: str,
    suffixes: Iterable[str],
    log_label: str,
) -> None:
    """Remove matching legacy entity-registry entries.

    Matching only applies when the unique ID conforms to the legacy unique-id shape to
    avoid accidental removal of current entities. If `suffixes` is empty, the function
    performs no action.

    Parameters:
        domain (str): Entity domain to restrict removals, such as ``sensor``.
        suffixes (Iterable[str]): Iterable of legacy unique-id suffix strings; an entity
        is removed if its unique ID matches any suffix.
        log_label (str): Human-readable label included in removal log messages.
    """
    suffix_tuple = tuple(suffixes)
    if not suffix_tuple:
        return
    registry = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        if ent.domain != domain:
            continue
        uid = ent.unique_id or ""
        if any(_legacy_suffix_matches(uid, suffix) for suffix in suffix_tuple):
            _LOGGER.info(
                "Removing %s %s (%s)",
                log_label,
                ent.entity_id,
                ent.unique_id,
            )
            registry.async_remove(ent.entity_id)


async def _async_teardown_unloaded_entry(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator | None,
    bucket: dict[str, Any],
) -> bool:
    """Tear down runtime resources after Home Assistant unloaded all platforms."""
    if coordinator is not None:
        bucket[_UNLOADING_COORDINATOR_RUNTIME_KEY] = coordinator
    await _async_cancel_layer5_start_task(hass, entry)
    await _async_cancel_runtime_task(
        hass,
        entry,
        _OPTIONS_RECONCILE_TASK_RUNTIME_KEY,
        label="options reconcile",
    )
    _clear_option_reconcile_runtime_state(bucket)
    await _async_stop_entry_local_mqtt(hass, entry, coordinator)
    if coordinator is not None:
        cleanup_ok = await _async_shutdown_coordinator_bounded(
            coordinator,
            context="entry unload",
            hass=hass,
            entry=entry,
        )
        if not cleanup_ok:
            _LOGGER.error(
                "Jackery HTTP coordinator cleanup failed after platforms were "
                "unloaded; keeping the old runtime fenced for the next setup",
            )
            return False
        _defer_supplemental_transports(hass, entry, coordinator)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: JackeryConfigEntry) -> bool:
    """Unload the config entry and tear down its runtime resources.

    If platform unload succeeds, shuts down the coordinator (if present) and clears the
    entry's runtime data to avoid retaining the coordinator. Teardown is performed only
    when platforms are successfully unloaded.

    Returns:
        True if platforms were unloaded and runtime teardown completed, False otherwise.
    """
    coordinator: JackerySolarVaultCoordinator | None = entry.runtime_data
    unload_ok = False
    teardown_complete = False
    bucket = _entry_runtime_bucket(hass, entry)
    try:
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        if not unload_ok:
            return False
        teardown_complete = await _async_teardown_unloaded_entry(
            hass,
            entry,
            coordinator,
            bucket,
        )
        return True
    finally:
        if _supplemental_cleanup_pending(hass, entry):
            _schedule_supplemental_cleanup(hass, entry)
        if unload_ok and teardown_complete:
            _clear_entry_coordinator_runtime_references(
                entry,
                coordinator,
                _runtime_bucket_if_present(hass, entry),
            )


async def async_remove_config_entry_device(  # ruff: ignore[unused-async]
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow a user to remove a device from the config entry.

    If the device still exists in the Jackery account, the integration's coordinator
    may rediscover it on the next poll; permitting removal here only affects the Home
    Assistant device registry entry.

    Returns:
        True if removal is allowed, False to prevent removal.
    """
    return True


async def async_migrate_entry(  # ruff: ignore[unused-async]
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> bool:
    """Migrate old config entries to the current version."""
    if entry.version < 1 or (entry.version == 1 and entry.minor_version < 1):
        update_kwargs: Any = {"version": 1, "minor_version": 1}
        hass.config_entries.async_update_entry(entry, **update_kwargs)
    return True
