"""Jackery SolarVault integration."""

import asyncio  # ruff:ignore[unsorted-imports]
import contextlib
from datetime import timedelta
import logging
import operator
import os
from pathlib import Path
import re
from typing import TYPE_CHECKING, Any, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME, EntityCategory
from homeassistant.core import callback
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
)
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,
    entity_registry as er,
)
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.start import async_at_started
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.helpers.storage import Store as Store

from .client import JackeryApi, JackeryAuthError, JackeryError
from .client.local_mqtt import (
    JackeryLocalMqttClient,
)
from .client.mqtt_session_cache import (
    async_load_mqtt_session,
)
from .const import (
    BLOCKED_LOCAL_MQTT_TOPIC_FILTERS,
    CALCULATED_POWER_SENSOR_SUFFIXES,
    CONF_CREATE_CALCULATED_POWER_SENSORS,
    CONF_CREATE_SAVINGS_DETAIL_SENSORS,
    CONF_CREATE_SMART_METER_DERIVED_SENSORS,
    CONF_ENABLE_BLE_TRANSPORT,
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
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
    CONF_THIRD_PARTY_MQTT_TOKEN,
    CONF_THIRD_PARTY_MQTT_USERNAME,
    COORDINATOR_SHUTDOWN_TIMEOUT_SEC,
    CT_PERIOD_SENSOR_SUFFIXES,
    DEFAULT_CREATE_CALCULATED_POWER_SENSORS,
    DEFAULT_CREATE_SAVINGS_DETAIL_SENSORS,
    DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
    DEFAULT_LOCAL_MQTT_ENABLE,
    DEFAULT_SCAN_INTERVAL_SEC,
    DEFAULT_THIRD_PARTY_MQTT_ENABLE,
    DEFAULT_THIRD_PARTY_MQTT_PASSWORD as DEFAULT_THIRD_PARTY_MQTT_PASSWORD,
    DEFAULT_THIRD_PARTY_MQTT_PORT,
    DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER,
    DEFAULT_THIRD_PARTY_MQTT_USERNAME as DEFAULT_THIRD_PARTY_MQTT_USERNAME,
    DOMAIN,
    DUPLICATE_BINARY_SENSOR_SUFFIXES,
    ENTRY_BOOTSTRAP_MQTT_SESSION,
    LOCAL_MQTT_RUNTIME_KEY as _LOCAL_MQTT_RUNTIME_KEY,
    MQTT_SESSION_MAC_ID,
    MQTT_SESSION_MAC_ID_SOURCE,
    MQTT_SESSION_SEED_B64,
    MQTT_SESSION_USER_ID,
    PLATFORMS,
    REMOVED_SENSOR_SUFFIXES,
    SAVINGS_DETAIL_SENSOR_SUFFIXES,
    SETUP_LOGIN_MAX_ATTEMPTS,
    SETUP_LOGIN_RETRY_DELAY_SEC,
    SMART_METER_DERIVED_SENSOR_SUFFIXES,
    STALE_ENERGY_HELPER_PREFIX,
    STALE_HELPER_VENDOR_TOKENS,
    _ENTITY_CREATING_OPTION_KEYS,
    STALE_NET_POWER_SUFFIX,
)
from .coordinator import JackerySolarVaultCoordinator, STORAGE_ERRORS
from .services import async_setup_services
from .util import (
    config_entry_bool_option,
    config_entry_int_option,
    config_entry_str_option,
    nonblank_text,
    stable_subdevice_key,
)

if TYPE_CHECKING:
    from collections.abc import Iterable

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

_LOGGER = logging.getLogger(__name__)  # ruff:ignore[non-empty-init-module]
_BLOCKED_LOCAL_MQTT_TOPIC_FILTERS = BLOCKED_LOCAL_MQTT_TOPIC_FILTERS  # ruff:ignore[non-empty-init-module]
_JACKERY_ENV_PREFIX = "JACKERY_"  # ruff:ignore[non-empty-init-module]


def _read_env_file_sync(env_file: Path) -> str | None:  # ruff:ignore[non-empty-init-module]
    """Read the .env file content if present.

    This blocking read is deliberately a module-level synchronous helper so it
    is only ever invoked from a worker thread (via ``asyncio.to_thread``), never
    directly on the event loop.

    Returns:
        str: The file contents decoded as UTF-8 if the .env file exists and is
        readable.
        None: If the file does not exist or cannot be read.
    """
    if not env_file.is_file():
        return None
    try:
        return env_file.read_text(encoding="utf-8")
    except OSError:
        return None


async def _load_dotenv_if_present(hass_config_path: Path) -> None:  # ruff:ignore[non-empty-init-module]
    """Load JACKERY_* variables from a .env file into os.environ.

    HA OS does not process .env files for custom integrations.  This
    helper reads ``<config_dir>/.env`` once at startup and injects
    any ``JACKERY_*`` keys into the process environment so that
    ``util.dev_mode_redactions_disabled()`` (and similar helpers)
    picks them up without requiring a dotenv library dependency.

    The file I/O is offloaded to a thread executor to avoid blocking
    the event loop (HA strict-async policy).
    """
    env_file = hass_config_path / ".env"

    text: str | None = await asyncio.to_thread(_read_env_file_sync, env_file)
    if text is None:
        return
    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        if not key.startswith(_JACKERY_ENV_PREFIX):
            continue
        value = value.strip().strip("\"'")
        os.environ.setdefault(key, value)
    # Reset the dev-mode cache so the freshly loaded env var takes effect.
    from . import util as _util  # ruff:ignore[import-outside-top-level]

    _util._DEV_MODE_CACHED = None  # ruff:ignore[private-member-access]


# This integration is config-entry-only — there is no YAML configuration
# surface. The `cv.config_entry_only_config_schema` helper documents
# that contract to hassfest and rejects any YAML the user might add by
# accident.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)  # ruff:ignore[non-empty-init-module]


async def async_setup(hass: HomeAssistant, config: dict[str, Any]) -> bool:  # ruff:ignore[non-empty-init-module]
    """Set up global Jackery SolarVault services."""
    await _load_dotenv_if_present(Path(hass.config.config_dir))
    async_setup_services(hass)
    return True


def _async_clean_legacy_entities(  # ruff:ignore[non-empty-init-module]
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
    _async_migrate_battery_pack_identities(hass, entry)
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


_PRIMARY_SETUP_TIMEOUT_SEC = 90.0  # ruff:ignore[non-empty-init-module]
_ENTRY_TASK_CANCEL_TIMEOUT_SEC = 5.0  # ruff:ignore[non-empty-init-module]
_LAYER5_TASK_RUNTIME_KEY = "layer5_start_task"  # ruff:ignore[non-empty-init-module]
_OPTIONS_RECONCILE_TASK_RUNTIME_KEY = "options_reconcile_task"  # ruff:ignore[non-empty-init-module]
_OPTIONS_RECONCILE_PENDING_RUNTIME_KEY = "options_reconcile_pending"  # ruff:ignore[non-empty-init-module]
_OPTIONS_SNAPSHOT_RUNTIME_KEY = "options_snapshot"  # ruff:ignore[non-empty-init-module]
_ENTRY_DATA_SNAPSHOT_RUNTIME_KEY = "entry_data_snapshot"  # ruff:ignore[non-empty-init-module]
_LOCAL_MQTT_OPTION_KEYS = frozenset({  # ruff:ignore[non-empty-init-module]
    CONF_LOCAL_MQTT_ENABLE,
    CONF_LOCAL_MQTT_HOST,
    CONF_LOCAL_MQTT_PASSWORD,
    CONF_LOCAL_MQTT_PORT,
    CONF_LOCAL_MQTT_TOPIC,
    CONF_LOCAL_MQTT_USERNAME,
    CONF_THIRD_PARTY_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_PORT,
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
    CONF_THIRD_PARTY_MQTT_TOKEN,
    CONF_THIRD_PARTY_MQTT_USERNAME,
})
_SUPPLEMENTAL_OPTION_KEYS = frozenset({  # ruff:ignore[non-empty-init-module]
    CONF_ENABLE_BLE_TRANSPORT,
    *_LOCAL_MQTT_OPTION_KEYS,
})
_UNLOADING_COORDINATOR_RUNTIME_KEY = "unloading_coordinator"  # ruff:ignore[non-empty-init-module]
_PRIMARY_SETUP_COORDINATOR_RUNTIME_KEY = "primary_setup_coordinator"  # ruff:ignore[non-empty-init-module]
_SUPPLEMENTAL_TRANSPORT_COORDINATORS_RUNTIME_KEY = "supplemental_transport_coordinators"  # ruff:ignore[non-empty-init-module]
_SUPPLEMENTAL_LOCAL_MQTT_RUNTIME_KEY = "supplemental_local_mqtt_clients"  # ruff:ignore[non-empty-init-module]
_SUPPLEMENTAL_LAYER5_TASKS_RUNTIME_KEY = "supplemental_layer5_tasks"  # ruff:ignore[non-empty-init-module]
_SUPPLEMENTAL_CLEANUP_TASK_RUNTIME_KEY = "supplemental_cleanup_task"  # ruff:ignore[non-empty-init-module]
_SUPPLEMENTAL_CLEANUP_RETRY_SEC = 5.0  # ruff:ignore[non-empty-init-module]


def _entry_bootstrap_mqtt_session(entry: ConfigEntry) -> dict[str, str] | None:  # ruff:ignore[non-empty-init-module]
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
    raw = entry.data.get(ENTRY_BOOTSTRAP_MQTT_SESSION)
    if not isinstance(raw, dict):
        return None
    required = (
        MQTT_SESSION_USER_ID,
        MQTT_SESSION_SEED_B64,
        MQTT_SESSION_MAC_ID,
    )
    snapshot: dict[str, str] = {}
    for key in required:
        value = raw.get(key)
        if not isinstance(value, str) or not value:
            return None
        snapshot[key] = value
    mac_id_source = raw.get(MQTT_SESSION_MAC_ID_SOURCE)
    if isinstance(mac_id_source, str) and mac_id_source:
        snapshot[MQTT_SESSION_MAC_ID_SOURCE] = mac_id_source
    return snapshot


def _entry_runtime_bucket(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:  # ruff:ignore[non-empty-init-module]
    """Get or create the config entry's mutable ``hass.data`` bucket.

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


def _entry_runtime_task(  # ruff:ignore[non-empty-init-module]
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


def _entry_owns_coordinator(  # ruff:ignore[non-empty-init-module]
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


def _entry_primary_setup_complete(  # ruff:ignore[non-empty-init-module]
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
) -> bool:
    """Return whether this coordinator completed its mandatory HTTP setup."""
    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    return (
        _entry_owns_coordinator(hass, entry, coordinator)
        and isinstance(bucket, dict)
        and bucket.get(_PRIMARY_SETUP_COORDINATOR_RUNTIME_KEY) is coordinator
    )


def _store_entry_runtime_task(  # ruff:ignore[non-empty-init-module]
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
        except Exception as err:  # ruff:ignore[blind-except]
            _LOGGER.warning(
                "Jackery background task %s for entry %s failed: %s",
                task.get_name(),
                entry.entry_id,
                err,
            )

    task.add_done_callback(_task_done)


def _append_supplemental_runtime_object(  # ruff:ignore[non-empty-init-module]
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


def _supplemental_runtime_items(  # ruff:ignore[non-empty-init-module]
    bucket: dict[str, Any],
    key: str,
) -> list[object]:
    """Return the valid list of deferred supplementary runtime objects."""
    values = bucket.get(key)
    return list(values) if isinstance(values, list) else []


def _set_supplemental_runtime_items(  # ruff:ignore[non-empty-init-module]
    bucket: dict[str, Any],
    key: str,
    values: list[object],
) -> None:
    """Replace a deferred supplementary runtime list or remove it when empty."""
    if values:
        bucket[key] = values
    else:
        bucket.pop(key, None)


def _supplemental_cleanup_pending(  # ruff:ignore[non-empty-init-module]
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


def _defer_layer5_start_task(  # ruff:ignore[non-empty-init-module]
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


def _defer_supplemental_local_mqtt(  # ruff:ignore[non-empty-init-module]
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


def _defer_supplemental_transports(  # ruff:ignore[non-empty-init-module]
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


async def _async_cleanup_stale_supplemental(  # ruff:ignore[too-many-branches, non-empty-init-module]
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> None:
    """Retry stale Layer-5 cleanup without delaying HTTP setup or polling."""
    while _supplemental_cleanup_pending(hass, entry):
        bucket = _entry_runtime_bucket(hass, entry)
        current_task = asyncio.current_task()
        layer5_tasks = [
            item
            for item in _supplemental_runtime_items(
                bucket,
                _SUPPLEMENTAL_LAYER5_TASKS_RUNTIME_KEY,
            )
            if isinstance(item, asyncio.Task) and item is not current_task
        ]
        for task in layer5_tasks:
            if not task.done():
                task.cancel()
        pending_layer5_tasks: set[asyncio.Task[Any]] = set()
        if layer5_tasks:
            done_layer5_tasks, pending_layer5_tasks = await asyncio.wait(
                layer5_tasks,
                timeout=_ENTRY_TASK_CANCEL_TIMEOUT_SEC,
            )
            for task in done_layer5_tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    task.result()
        _set_supplemental_runtime_items(
            bucket,
            _SUPPLEMENTAL_LAYER5_TASKS_RUNTIME_KEY,
            list(pending_layer5_tasks),
        )

        pending_local_mqtt: list[object] = []
        for item in _supplemental_runtime_items(
            bucket,
            _SUPPLEMENTAL_LOCAL_MQTT_RUNTIME_KEY,
        ):
            if not isinstance(item, JackeryLocalMqttClient):
                continue
            try:
                async with asyncio.timeout(_ENTRY_TASK_CANCEL_TIMEOUT_SEC):
                    await item.async_stop()
            except Exception:  # ruff:ignore[blind-except]
                pending_local_mqtt.append(item)
        _set_supplemental_runtime_items(
            bucket,
            _SUPPLEMENTAL_LOCAL_MQTT_RUNTIME_KEY,
            pending_local_mqtt,
        )

        pending_transport_coordinators: list[object] = []
        for item in _supplemental_runtime_items(
            bucket,
            _SUPPLEMENTAL_TRANSPORT_COORDINATORS_RUNTIME_KEY,
        ):
            if not isinstance(item, JackerySolarVaultCoordinator):
                continue
            try:
                async with asyncio.timeout(_ENTRY_TASK_CANCEL_TIMEOUT_SEC):
                    await item.async_stop_supplemental_transports()
            except Exception:  # ruff:ignore[blind-except]
                pending_transport_coordinators.append(item)
                continue
            if item.has_pending_supplemental_transport_cleanup:
                pending_transport_coordinators.append(item)
        _set_supplemental_runtime_items(
            bucket,
            _SUPPLEMENTAL_TRANSPORT_COORDINATORS_RUNTIME_KEY,
            pending_transport_coordinators,
        )

        if _supplemental_cleanup_pending(hass, entry):
            await asyncio.sleep(_SUPPLEMENTAL_CLEANUP_RETRY_SEC)

    coordinator = entry.runtime_data
    if not isinstance(
        coordinator,
        JackerySolarVaultCoordinator,
    ) or not _entry_primary_setup_complete(hass, entry, coordinator):
        return
    layer5_task = _entry_runtime_task(hass, entry, _LAYER5_TASK_RUNTIME_KEY)
    if layer5_task is not None:
        if layer5_task.done():
            bucket = _entry_runtime_bucket(hass, entry)
            if bucket.get(_LAYER5_TASK_RUNTIME_KEY) is layer5_task:
                bucket.pop(_LAYER5_TASK_RUNTIME_KEY, None)
        else:

            @callback
            def _retry_after_current_start(_done: asyncio.Future[Any]) -> None:
                """Retry supplementary startup after the active task completes."""
                _schedule_layer5_start_if_ready(hass, entry, coordinator)

            layer5_task.add_done_callback(_retry_after_current_start)
            return
    _register_deferred_layer5_start(hass, entry, coordinator)


def _schedule_supplemental_cleanup(  # ruff:ignore[non-empty-init-module]
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


async def _async_release_fenced_coordinator(  # ruff:ignore[non-empty-init-module]
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


async def _async_prime_entry_bootstrap_mqtt_session(  # ruff:ignore[unused-async, non-empty-init-module]
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


async def _async_authenticate_api_layer(  # ruff:ignore[non-empty-init-module]
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


async def _async_prepare_primary_http(  # ruff:ignore[non-empty-init-module]
    hass: HomeAssistant,
    entry: ConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
) -> None:
    """Authenticate, discover, and complete HA's mandatory first HTTP refresh."""
    try:  # ruff:ignore[too-many-statements-in-try-clause]
        await _async_authenticate_api_layer(hass, entry, coordinator.api)
        await coordinator.async_discover()
        await coordinator.async_config_entry_first_refresh()
        if not coordinator.data:
            msg = (
                "Jackery HTTP discovery returned no devices; "
                "Home Assistant will retry setup"
            )
            raise ConfigEntryNotReady(msg)  # ruff:ignore[raise-within-try]
    except ConfigEntryAuthFailed, ConfigEntryNotReady:
        raise
    except JackeryAuthError as err:
        msg = f"Jackery credentials were rejected by the HTTP API: {err}"
        raise ConfigEntryAuthFailed(msg) from err
    except (JackeryError, OSError, UpdateFailed) as err:
        msg = f"Jackery primary HTTP setup is temporarily unavailable: {err}"
        raise ConfigEntryNotReady(msg) from err


def _handle_optional_startup_result(  # ruff:ignore[non-empty-init-module]
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
        coordinator._defer_background_auth_failure(result)  # ruff:ignore[private-member-access]
    elif isinstance(result, BaseException):
        _LOGGER.warning("Jackery %s could not start: %s", label, result)


async def _async_start_layer5_transports(  # ruff:ignore[non-empty-init-module]
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
) -> None:
    """Start the supplementary Layer-5 transports after Home Assistant has started.

    Layer 5 — cloud MQTT push, the HA-MQTT listener, the direct local MQTT
    listener, and the BLE diagnostic channel — is supplementary and must never
    run on the HA startup/boot path: the HTTP/cloud path is the single
    unconditional data path and must never be blocked or delayed by Layer 5
    (docs/AGENTS.md §1.2; RESUME §HART-VERBOTEN "kein MQTT in async_setup_entry").
    This coroutine is scheduled only from an EVENT_HOMEASSISTANT_STARTED hook
    (see :func:`_register_deferred_layer5_start`), so a slow or blocking
    transport connect can never stall Home Assistant boot. Each transport
    failure is non-fatal: auth failures are deferred to the coordinator, other
    failures are logged, and none prevent the others from starting.
    """
    # Even on a reload after HA has started, yield once so transports cannot
    # begin inside the async_setup_entry call stack.
    await asyncio.sleep(0)
    if not _entry_primary_setup_complete(hass, entry, coordinator):
        return
    if _supplemental_cleanup_pending(hass, entry):
        _schedule_supplemental_cleanup(hass, entry)
        return

    async def _async_start_cloud_mqtt() -> None:
        """Reconcile optional session cache, then start only cloud MQTT."""
        persisted: dict[str, str] | None = None
        if coordinator.api.mqtt_session_snapshot() is None:
            try:
                persisted = await async_load_mqtt_session(hass, entry.entry_id)
            except STORAGE_ERRORS as err:
                _LOGGER.debug(
                    "Jackery deferred MQTT session cache load failed; "
                    "Layer 5 will use login/bootstrap state: %s",
                    err,
                )
            if not _entry_owns_coordinator(hass, entry, coordinator):
                return
            if coordinator.api.mqtt_session_snapshot() is None:
                fallback = persisted or _entry_bootstrap_mqtt_session(entry)
                if fallback is not None:
                    coordinator.api.hydrate_mqtt_session(
                        user_id=fallback[MQTT_SESSION_USER_ID],
                        seed_b64=fallback[MQTT_SESSION_SEED_B64],
                        mac_id=fallback[MQTT_SESSION_MAC_ID],
                        mac_id_source=fallback.get(MQTT_SESSION_MAC_ID_SOURCE),
                    )
        if not coordinator.mark_mqtt_session_cache_loaded(persisted):
            return
        await coordinator.async_start_mqtt()

    (
        mqtt_result,
        local_listener_result,
        direct_local_mqtt_result,
        ble_result,
    ) = await asyncio.gather(
        _async_start_cloud_mqtt(),
        coordinator.async_start_local_mqtt_listener(),
        _async_start_local_mqtt(hass, entry, coordinator),
        coordinator.async_start_ble_transport(),
        return_exceptions=True,
    )
    if not _entry_owns_coordinator(hass, entry, coordinator):
        return
    _handle_optional_startup_result(coordinator, mqtt_result, label="MQTT push")
    _handle_optional_startup_result(
        coordinator,
        local_listener_result,
        label="HA-MQTT listener",
    )
    _handle_optional_startup_result(
        coordinator,
        direct_local_mqtt_result,
        label="local MQTT listener",
    )
    _handle_optional_startup_result(coordinator, ble_result, label="BLE transport")


def _schedule_layer5_start_if_ready(  # ruff:ignore[non-empty-init-module]
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
) -> None:
    """Create a Layer-5 task only after HTTP setup and stale cleanup finish."""
    if not _entry_primary_setup_complete(hass, entry, coordinator):
        return
    if _supplemental_cleanup_pending(hass, entry):
        _schedule_supplemental_cleanup(hass, entry)
        return
    if _entry_runtime_task(hass, entry, _LAYER5_TASK_RUNTIME_KEY) is not None:
        return
    task = hass.async_create_background_task(
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


def _register_deferred_layer5_start(  # ruff:ignore[non-empty-init-module]
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
) -> None:
    """Schedule supplementary startup only after Home Assistant has started."""

    @callback
    def _on_hass_started(_hass: HomeAssistant) -> None:
        """Schedule supplementary transports after Home Assistant starts."""
        _schedule_layer5_start_if_ready(hass, entry, coordinator)

    entry.async_on_unload(async_at_started(hass, _on_hass_started))


def _local_mqtt_client(  # ruff:ignore[non-empty-init-module]
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> JackeryLocalMqttClient | None:
    """Return the per-entry local MQTT client stored in hass.data for the given config
    entry.

    Returns:
        JackeryLocalMqttClient instance for the entry, or None if no client is stored or
        the stored value is not a JackeryLocalMqttClient.
    """  # ruff: ignore[missing-blank-line-after-summary]
    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not isinstance(bucket, dict):
        return None
    client = bucket.get(_LOCAL_MQTT_RUNTIME_KEY)
    return client if isinstance(client, JackeryLocalMqttClient) else None


async def _async_start_local_mqtt(  # ruff:ignore[too-many-return-statements, too-many-branches, too-many-statements, non-empty-init-module]
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
) -> None:
    """Start a per-entry local MQTT client when the entry is explicitly scoped.

    The listener only starts when the Third-Party MQTT bridge is enabled, the
    host is set, and a non-empty topic filter is configured. This prevents an
    accidental broad wildcard subscription from ingesting unrelated broker
    traffic and causing high CPU load.
    """
    if not _entry_owns_coordinator(hass, entry, coordinator):
        return
    existing_client = _local_mqtt_client(hass, entry)
    if existing_client is not None:
        try:
            async with asyncio.timeout(_ENTRY_TASK_CANCEL_TIMEOUT_SEC):
                await existing_client.async_stop()
        except Exception as err:  # ruff:ignore[blind-except]  # never overlap an old broker session
            _LOGGER.warning(
                "Jackery local MQTT client is still stopping; deferring its "
                "cleanup before a replacement: %s",
                err,
            )
            _defer_supplemental_local_mqtt(hass, entry, existing_client)
            _schedule_supplemental_cleanup(hass, entry)
            return
        stashed = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        if (
            isinstance(stashed, dict)
            and stashed.get(_LOCAL_MQTT_RUNTIME_KEY) is existing_client
        ):
            stashed.pop(_LOCAL_MQTT_RUNTIME_KEY, None)
        if not _entry_owns_coordinator(hass, entry, coordinator):
            return
    # Enable precedence mirrors diagnostics._local_mqtt_diagnostics (the
    # source-of-truth gating contract): the dedicated CONF_LOCAL_MQTT_ENABLE key
    # wins *only when present*; otherwise the bridge follows
    # CONF_THIRD_PARTY_MQTT_ENABLE (default off), so local MQTT cannot start
    # merely because a legacy bridge option is absent.
    options = getattr(entry, "options", {}) or {}
    data = getattr(entry, "data", {}) or {}
    if CONF_LOCAL_MQTT_ENABLE in options or CONF_LOCAL_MQTT_ENABLE in data:
        enabled = config_entry_bool_option(
            entry,
            CONF_LOCAL_MQTT_ENABLE,
            DEFAULT_LOCAL_MQTT_ENABLE,
        )
    else:
        enabled = config_entry_bool_option(
            entry,
            CONF_THIRD_PARTY_MQTT_ENABLE,
            DEFAULT_THIRD_PARTY_MQTT_ENABLE,
        )
    if not enabled:
        return
    host = (
        config_entry_str_option(entry, CONF_LOCAL_MQTT_HOST, "")
        or config_entry_str_option(entry, CONF_THIRD_PARTY_MQTT_IP, "")
    ).strip()
    if not host:
        return

    # The configured topic is used verbatim: the device publishes on the topic
    # it was configured with (here ``homeassistant``) and the direct local
    # client subscribes to exactly that topic. ``hb/app/<userId>/...`` is the
    # CLOUD-MQTT namespace and must never be forced onto the local listener
    # (owner decision 2026-07-22). An empty filter falls back to the central
    # default instead of disabling the listener, because the UI tells users
    # they may leave it empty (owner escalation 2026-07-05).
    configured_topic_filter = (
        config_entry_str_option(entry, CONF_THIRD_PARTY_MQTT_TOPIC_FILTER, "")
        or config_entry_str_option(entry, CONF_LOCAL_MQTT_TOPIC, "")
        or DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER
    ).strip()
    if configured_topic_filter in _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS:
        _LOGGER.warning(
            "Jackery local MQTT listener not started: broad topic filter %r is"
            " blocked for CPU safety; configure a scoped filter or leave empty"
            " to use the configured default topic",
            configured_topic_filter,
        )
        return
    topic_filter = configured_topic_filter

    port = config_entry_int_option(
        entry,
        CONF_LOCAL_MQTT_PORT,
        0,
    ) or config_entry_int_option(
        entry,
        CONF_THIRD_PARTY_MQTT_PORT,
        DEFAULT_THIRD_PARTY_MQTT_PORT,
    )
    username = (
        config_entry_str_option(entry, CONF_LOCAL_MQTT_USERNAME, "")
        or config_entry_str_option(entry, CONF_THIRD_PARTY_MQTT_USERNAME, "")
    ).strip()
    password = config_entry_str_option(
        entry,
        CONF_LOCAL_MQTT_PASSWORD,
        "",
    ) or config_entry_str_option(
        entry,
        CONF_THIRD_PARTY_MQTT_PASSWORD,
        "",
    )

    async def _sink(
        topic: str,
        data: dict[str, Any] | None,
        _raw_bytes: bytes,
    ) -> None:
        """Dispatches a parsed local MQTT JSON message to the coordinator.

        Parameters:
            topic (str): MQTT topic the message was received on.
            data (dict[str, Any] | None): Parsed JSON payload, or ``None`` when the
                client could not decode the frame — a binary payload, invalid JSON, or
                JSON that is not an object. ``JackeryLocalMqttClient._handle_message``
                forwards those deliberately so a future binary decoder can inspect the
                raw bytes; until one exists they carry no routable body and are
                skipped here instead of reaching the coordinator as ``None``.
        """
        if not _entry_owns_coordinator(hass, entry, coordinator):
            return
        if data is None:
            return
        await coordinator.async_handle_local_mqtt_message(topic, data)

    client = JackeryLocalMqttClient(
        hass,
        host=host,
        port=port,
        username=username or None,
        password=password or None,
        client_id=f"ha-jackery-{entry.entry_id[:8]}",
        sink=_sink,
        topic_filter=topic_filter,
    )
    if not _entry_owns_coordinator(hass, entry, coordinator):
        return
    bucket = hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})
    bucket[_LOCAL_MQTT_RUNTIME_KEY] = client

    try:
        await client.async_start()
    except asyncio.CancelledError:
        _defer_supplemental_local_mqtt(hass, entry, client)
        _schedule_supplemental_cleanup(hass, entry)
        raise
    except Exception:
        _defer_supplemental_local_mqtt(hass, entry, client)
        _schedule_supplemental_cleanup(hass, entry)
        raise
    if not _entry_owns_coordinator(hass, entry, coordinator):
        _defer_supplemental_local_mqtt(hass, entry, client)
        _schedule_supplemental_cleanup(hass, entry)


async def _async_shutdown_coordinator_bounded(  # ruff:ignore[non-empty-init-module]
    coordinator: JackerySolarVaultCoordinator,
    *,
    context: str,
) -> bool:
    """Shut down a coordinator without allowing cleanup to hang indefinitely."""
    try:
        async with asyncio.timeout(COORDINATOR_SHUTDOWN_TIMEOUT_SEC):
            await coordinator.async_shutdown()
    except TimeoutError:
        _LOGGER.warning(
            "Jackery coordinator shutdown exceeded %.0fs during %s",
            COORDINATOR_SHUTDOWN_TIMEOUT_SEC,
            context,
        )
        return False
    except Exception as err:  # ruff:ignore[blind-except]
        _LOGGER.debug("Jackery coordinator shutdown failed during %s: %s", context, err)
        return False
    return True


async def _async_reconcile_entry_options(  # ruff:ignore[non-empty-init-module]
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
        operations: list[tuple[str, Any]] = []
        if changed_keys & _LOCAL_MQTT_OPTION_KEYS:
            operations.extend((
                (
                    "HA-MQTT listener reconfiguration",
                    coordinator.async_reconcile_local_mqtt_listener(),
                ),
                (
                    "local MQTT listener reconfiguration",
                    _async_start_local_mqtt(hass, entry, coordinator),
                ),
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
        if changed_keys & _LOCAL_MQTT_OPTION_KEYS:
            coordinator.async_schedule_local_mqtt_device_config()


def _schedule_options_reconcile(  # ruff:ignore[non-empty-init-module]
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
    changed_keys: set[str],
) -> None:
    """Coalesce option changes into one entry-owned background task."""
    bucket = _entry_runtime_bucket(hass, entry)
    pending = bucket.get(_OPTIONS_RECONCILE_PENDING_RUNTIME_KEY)
    if not isinstance(pending, set):
        pending = set()
        bucket[_OPTIONS_RECONCILE_PENDING_RUNTIME_KEY] = pending
    pending.update(changed_keys)
    current_task = _entry_runtime_task(
        hass,
        entry,
        _OPTIONS_RECONCILE_TASK_RUNTIME_KEY,
    )
    if current_task is not None and not current_task.done():
        return
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


async def _async_entry_updated(  # ruff:ignore[non-empty-init-module]
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> None:
    """Reload credential changes; apply ordinary options in place."""
    coordinator = entry.runtime_data
    bucket = _entry_runtime_bucket(hass, entry)
    previous_options = bucket.get(_OPTIONS_SNAPSHOT_RUNTIME_KEY)
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
    if changed_keys & _ENTITY_CREATING_OPTION_KEYS:
        _async_clean_legacy_entities(hass, entry)
        coordinator.async_update_listeners()
    if changed_keys & _SUPPLEMENTAL_OPTION_KEYS:
        _schedule_options_reconcile(hass, entry, coordinator, changed_keys)


async def _async_rollback_entry_setup(  # ruff:ignore[non-empty-init-module]
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
    try:  # ruff:ignore[too-many-statements-in-try-clause]
        _defer_layer5_start_task(hass, entry)
        local_mqtt = _local_mqtt_client(hass, entry)
        if local_mqtt is not None:
            _defer_supplemental_local_mqtt(hass, entry, local_mqtt)
        _schedule_supplemental_cleanup(hass, entry)
        if platforms_started:
            try:
                async with asyncio.timeout(COORDINATOR_SHUTDOWN_TIMEOUT_SEC):
                    platforms_rolled_back = (
                        await hass.config_entries.async_unload_platforms(
                            entry,
                            PLATFORMS,
                        )
                    )
            except TimeoutError:
                _LOGGER.warning(
                    "Jackery partial platform rollback exceeded %.0fs",
                    COORDINATOR_SHUTDOWN_TIMEOUT_SEC,
                )
                platforms_rolled_back = False
            except Exception as err:  # ruff:ignore[blind-except]
                _LOGGER.warning("Jackery partial platform rollback failed: %s", err)
                platforms_rolled_back = False
        if not platforms_rolled_back:
            return False
        shutdown_ok = await _async_shutdown_coordinator_bounded(
            coordinator,
            context="setup rollback",
        )
        if not shutdown_ok:
            _LOGGER.warning(
                "Jackery HTTP coordinator did not stop during setup rollback; "
                "preserving the runtime fence",
            )
            return False
        _defer_supplemental_transports(hass, entry, coordinator)
        _schedule_supplemental_cleanup(hass, entry)
    except Exception as err:  # ruff:ignore[blind-except]
        rollback_ok = False
        _LOGGER.warning("Jackery setup rollback cleanup failed: %s", err)
    finally:
        current_bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if rollback_ok and platforms_rolled_back and shutdown_ok:
            if (
                isinstance(current_bucket, dict)
                and current_bucket.get(_UNLOADING_COORDINATOR_RUNTIME_KEY)
                is coordinator
            ):
                current_bucket.pop(_UNLOADING_COORDINATOR_RUNTIME_KEY, None)
            if (
                isinstance(current_bucket, dict)
                and current_bucket.get(_PRIMARY_SETUP_COORDINATOR_RUNTIME_KEY)
                is coordinator
            ):
                current_bucket.pop(_PRIMARY_SETUP_COORDINATOR_RUNTIME_KEY, None)
            if entry.runtime_data is coordinator:
                entry.runtime_data = cast("Any", None)
    return rollback_ok and platforms_rolled_back and shutdown_ok


async def async_setup_entry(hass: HomeAssistant, entry: JackeryConfigEntry) -> bool:  # ruff:ignore[non-empty-init-module]
    """Set up the Jackery SolarVault config entry, start its coordinator and optional
    transports, and forward platform setup.

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
    """  # ruff: ignore[missing-blank-line-after-summary]
    if not await _async_release_fenced_coordinator(hass, entry):
        msg = (
            "A previous Jackery coordinator is still stopping; "
            "Home Assistant will retry setup"
        )
        raise ConfigEntryNotReady(msg)
    if _entry_runtime_task(hass, entry, _LAYER5_TASK_RUNTIME_KEY) is not None:
        _defer_layer5_start_task(hass, entry)
        _schedule_supplemental_cleanup(hass, entry)

    session = async_get_clientsession(hass)
    api = JackeryApi(
        session=session,
        account=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        mqtt_mac_id=entry.data.get(CONF_MQTT_MAC_ID),
        region_code=entry.data.get(CONF_REGION_CODE),
        ca_path=hass.config.path(
            "custom_components",
            DOMAIN,
            "jackery_ca.crt",
        ),
    )

    interval_sec = DEFAULT_SCAN_INTERVAL_SEC
    coordinator = JackerySolarVaultCoordinator(
        hass,
        entry,
        api,
        timedelta(seconds=interval_sec),
    )
    entry.runtime_data = coordinator
    _LOGGER.info("Jackery: coordinator polling interval set to %ss", interval_sec)

    setup_complete = False
    platforms_started = False
    try:
        try:
            async with asyncio.timeout(_PRIMARY_SETUP_TIMEOUT_SEC):
                await _async_prepare_primary_http(hass, entry, coordinator)
                _async_clean_legacy_entities(hass, entry)
                platforms_started = True
                await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        except TimeoutError as err:
            msg = (
                "Jackery primary HTTP setup exceeded "
                f"{_PRIMARY_SETUP_TIMEOUT_SEC:.0f}s; Home Assistant will retry"
            )
            raise ConfigEntryNotReady(msg) from err

        # Entities now exist and the mandatory first HTTP refresh succeeded.
        # This method only enables/schedules imports; no backfill is awaited here.
        _entry_runtime_bucket(hass, entry)[_PRIMARY_SETUP_COORDINATOR_RUNTIME_KEY] = (
            coordinator
        )
        entry.async_create_background_task(
            hass,
            coordinator.async_load_local_daily_snapshots(),
            name=f"{DOMAIN}_local_daily_cache_restore",
            eager_start=False,
        )
        coordinator.async_start_statistics_imports()
        runtime_bucket = _entry_runtime_bucket(hass, entry)
        runtime_bucket[_OPTIONS_SNAPSHOT_RUNTIME_KEY] = dict(entry.options)
        runtime_bucket[_ENTRY_DATA_SNAPSHOT_RUNTIME_KEY] = dict(entry.data)
        entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
        _register_deferred_layer5_start(hass, entry, coordinator)
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


def _async_remove_stale_energy_helpers(hass: HomeAssistant) -> None:  # ruff:ignore[non-empty-init-module]
    """Remove stale Energy helper entities that were created without a unit of
    measurement.

    Scans the entity registry for entities whose entity_id starts with the configured
    STALE_ENERGY_HELPER_PREFIX and ends with STALE_NET_POWER_SUFFIX. If an entity's
    current state has no `unit_of_measurement` (missing or empty) and its entity_id
    contains any token from STALE_HELPER_VENDOR_TOKENS, the entity is removed from
    the registry and an informational log entry is emitted.
    """  # ruff: ignore[missing-blank-line-after-summary]
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


_LEGACY_UID_HEAD_RE = re.compile(r"\d+(?:_battery_pack_\d+)?")  # ruff:ignore[non-empty-init-module]
_PORTABLE_SCREEN_UID_SUFFIX = "_portable_screen"  # ruff:ignore[non-empty-init-module]
_PORTABLE_SCREEN_TRANSLATION_KEY = "portable_screen"  # ruff:ignore[non-empty-init-module]
_GRID_STANDARD_UID_SUFFIX = "_grid_standard"  # ruff:ignore[non-empty-init-module]
_GRID_STANDARD_TRANSLATION_KEY = "grid_standard"  # ruff:ignore[non-empty-init-module]
_BATTERY_PACK_INDEX_MAX = 5  # ruff:ignore[non-empty-init-module]


def _legacy_suffix_matches(uid: str, key_suffix: str) -> bool:  # ruff:ignore[non-empty-init-module]
    """Determine whether `uid` consists of a legacy device head immediately followed by
    `key_suffix`.

    A legacy device head has the form `<digits>` or `<digits>_battery_pack_<digits>`.
    This function returns `True` only when `uid` ends with `key_suffix` and the
    substring before that suffix exactly matches the legacy head pattern.

    Returns:
        `True` if `uid` is a legacy head concatenated with `key_suffix`, `False`
        otherwise.
    """  # ruff: ignore[missing-blank-line-after-summary]
    if not key_suffix:
        return _LEGACY_UID_HEAD_RE.fullmatch(uid) is not None
    if not uid.endswith(key_suffix):
        return False
    head = uid[: -len(key_suffix)]
    return _LEGACY_UID_HEAD_RE.fullmatch(head) is not None


def _async_migrate_portable_screen_entity(  # ruff:ignore[non-empty-init-module]
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


def _async_migrate_grid_standard_entity(  # ruff:ignore[non-empty-init-module]
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


def _battery_pack_registry_identity(  # ruff:ignore[non-empty-init-module]
    registry: dr.DeviceRegistry,
    device: dr.DeviceEntry,
) -> tuple[str, str, str] | None:
    """Return parent id, pack identifier, and suffix for one pack device."""
    if device.via_device_id is None:
        return None
    parent = registry.devices.get(device.via_device_id)
    if parent is None:
        return None
    parent_identifiers = [
        identifier for domain, identifier in parent.identifiers if domain == DOMAIN
    ]
    if len(parent_identifiers) != 1:
        return None
    parent_device_id = parent_identifiers[0]
    prefix = f"{parent_device_id}_battery_pack_"
    pack_identifiers = [
        identifier
        for domain, identifier in device.identifiers
        if domain == DOMAIN and identifier.startswith(prefix)
    ]
    if len(pack_identifiers) != 1:
        return None
    pack_identifier = pack_identifiers[0]
    return parent_device_id, pack_identifier, pack_identifier.removeprefix(prefix)


def _seed_battery_pack_registry_identities(  # ruff:ignore[too-many-branches, non-empty-init-module]
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

        observed_indices: dict[str, list[int]] = {}
        for index in range(1, _BATTERY_PACK_INDEX_MAX + 1):
            serial = coordinator.battery_pack_observed_serial(parent_device_id, index)
            if serial is None:
                continue
            key = stable_subdevice_key("battery_pack", serial, index)
            observed_indices.setdefault(key, []).append(index)

        ambiguous_keys = {
            key for key, indices in observed_indices.items() if len(indices) > 1
        }
        for key in ambiguous_keys:
            for index in observed_indices[key]:
                coordinator.set_battery_pack_identity_override(
                    parent_device_id, index, None
                )
        if ambiguous_keys:
            _LOGGER.warning(
                "Using index identities for %s: live payload repeats a "
                "normalized battery-pack serial",
                parent_device_id,
            )

        protected_indices = set(remaining_old_indices.get(parent_device_id, ()))
        matched_keys: set[str] = set()
        blocked_keys = set(ambiguous_keys)
        for serial, fallback_index in sorted(records, key=operator.itemgetter(0)):
            key = stable_subdevice_key("battery_pack", serial, fallback_index or 1)
            live_indices = observed_indices.get(key, ())
            if len(live_indices) != 1:
                continue
            index = live_indices[0]
            if index in protected_indices:
                blocked_keys.add(key)
                continue
            coordinator.set_battery_pack_identity_override(
                parent_device_id, index, serial
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
            key = stable_subdevice_key("battery_pack", serial, stored_index or 1)
            if key in matched_keys or key in blocked_keys:
                continue
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


def _async_migrate_battery_pack_identities(  # ruff:ignore[too-many-branches, too-many-locals, too-many-statements, non-empty-init-module]
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> None:
    """Migrate index-based battery-pack registry identities to serial keys."""
    coordinator = entry.runtime_data

    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    old_candidates: list[tuple[dr.DeviceEntry, str, str, int, str]] = []
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
        live_serial = coordinator.battery_pack_observed_serial(
            parent_device_id, numeric_index
        )
        coordinator.set_battery_pack_identity_override(
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

    for device, parent_device_id, old_identifier, pack_index, serial in old_candidates:
        pack_key = stable_subdevice_key("battery_pack", serial, pack_index)
        target_identifier = (DOMAIN, f"{parent_device_id}_{pack_key}")
        if len(target_owners.get(target_identifier, ())) != 1:
            _LOGGER.warning(
                "Skipping battery-pack registry migration for %s: duplicate "
                "normalized serial target %s",
                old_identifier,
                target_identifier[1],
            )
            continue
        target_device = device_registry.async_get_device(
            identifiers={target_identifier}
        )
        if target_device is not None and target_device.id != device.id:
            _LOGGER.warning(
                "Skipping battery-pack registry migration for %s: target device "
                "%s already exists",
                old_identifier,
                target_identifier[1],
            )
            continue

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
            not (entity.unique_id or "").startswith(old_prefix)
            for entity in owned_entries
        ):
            _LOGGER.warning(
                "Skipping battery-pack registry migration for %s: mixed old/new "
                "entity identities",
                old_identifier,
            )
            continue

        entity_updates: list[tuple[er.RegistryEntry, str]] = []
        target_keys: set[tuple[str, str]] = set()
        collision = False
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
                collision = True
                break
            target_keys.add(target_key)
            entity_updates.append((entity, new_unique_id))
        if collision:
            _LOGGER.warning(
                "Skipping battery-pack registry migration for %s: target entity "
                "unique_id collision",
                old_identifier,
            )
            continue

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
        coordinator.set_battery_pack_identity_override(
            parent_device_id, pack_index, serial
        )
        serial_records.setdefault(parent_device_id, []).append((serial, pack_index))
        _LOGGER.info(
            "Migrated battery-pack registry identity %s to %s (%d entities)",
            old_identifier,
            target_identifier[1],
            len(entity_updates),
        )

    _seed_battery_pack_registry_identities(
        coordinator,
        serial_records,
        remaining_old_indices,
    )


def _async_remove_entities_with_suffixes(  # ruff:ignore[non-empty-init-module]
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    *,
    domain: str,
    suffixes: Iterable[str],
    log_label: str,
) -> None:
    """Remove entity-registry entries for the given config entry and domain whose legacy
    unique IDs end with any of the provided legacy suffixes.

    Matching only applies when the unique ID conforms to the legacy unique-id shape to
    avoid accidental removal of current entities. If `suffixes` is empty, the function
    performs no action.

    Parameters:
        domain (str): Entity domain to restrict removals (e.g., "sensor", "binary_sensor").
        suffixes (Iterable[str]): Iterable of legacy unique-id suffix strings; an entity
        is removed if its unique ID matches any suffix.
        log_label (str): Human-readable label included in removal log messages.
    """  # ruff: ignore[missing-blank-line-after-summary, line-too-long]
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


async def async_unload_entry(hass: HomeAssistant, entry: JackeryConfigEntry) -> bool:  # ruff:ignore[too-many-branches, non-empty-init-module]
    """Unload the config entry and tear down its runtime resources.

    If platform unload succeeds, shuts down the coordinator (if present) and clears the
    entry's runtime data to avoid retaining the coordinator. Teardown is performed only
    when platforms are successfully unloaded.

    Returns:
        True if platforms were unloaded and runtime teardown completed, False otherwise.
    """
    coordinator: JackerySolarVaultCoordinator | None = entry.runtime_data
    unload_ok = False
    coordinator_cleanup_ok = True
    bucket = _entry_runtime_bucket(hass, entry)
    if isinstance(coordinator, JackerySolarVaultCoordinator):
        # Invalidate deferred callbacks before the first await. A started-event
        # racing with reload must not launch transports for the old coordinator.
        bucket[_UNLOADING_COORDINATOR_RUNTIME_KEY] = coordinator
    try:
        options_task = _entry_runtime_task(
            hass,
            entry,
            _OPTIONS_RECONCILE_TASK_RUNTIME_KEY,
        )
        bucket.pop(_OPTIONS_RECONCILE_PENDING_RUNTIME_KEY, None)
        if options_task is not None:
            if bucket.get(_OPTIONS_RECONCILE_TASK_RUNTIME_KEY) is options_task:
                bucket.pop(_OPTIONS_RECONCILE_TASK_RUNTIME_KEY, None)
            if not options_task.done():
                options_task.cancel()
                _append_supplemental_runtime_object(
                    hass,
                    entry,
                    _SUPPLEMENTAL_LAYER5_TASKS_RUNTIME_KEY,
                    options_task,
                )
        _defer_layer5_start_task(hass, entry)
        local_mqtt = _local_mqtt_client(hass, entry)
        if local_mqtt is not None:
            _defer_supplemental_local_mqtt(hass, entry, local_mqtt)
        _schedule_supplemental_cleanup(hass, entry)
        unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
        if not unload_ok:
            return False
        if isinstance(coordinator, JackerySolarVaultCoordinator):
            coordinator_cleanup_ok = await _async_shutdown_coordinator_bounded(
                coordinator,
                context="entry unload",
            )
            if not coordinator_cleanup_ok:
                _LOGGER.error(
                    "Jackery HTTP coordinator cleanup failed after platforms were "
                    "unloaded; keeping the old runtime fenced for the next setup",
                )
            else:
                _defer_supplemental_transports(hass, entry, coordinator)
                _schedule_supplemental_cleanup(hass, entry)
        return True
    finally:
        current_bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if unload_ok and coordinator_cleanup_ok:
            if (
                isinstance(coordinator, JackerySolarVaultCoordinator)
                and isinstance(current_bucket, dict)
                and current_bucket.get(_UNLOADING_COORDINATOR_RUNTIME_KEY)
                is coordinator
            ):
                current_bucket.pop(_UNLOADING_COORDINATOR_RUNTIME_KEY, None)
            if (
                isinstance(current_bucket, dict)
                and current_bucket.get(_PRIMARY_SETUP_COORDINATOR_RUNTIME_KEY)
                is coordinator
            ):
                current_bucket.pop(_PRIMARY_SETUP_COORDINATOR_RUNTIME_KEY, None)
            if entry.runtime_data is coordinator:
                entry.runtime_data = cast("Any", None)


async def async_remove_config_entry_device(  # ruff:ignore[unused-async, non-empty-init-module]
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
