"""Jackery SolarVault integration."""

import asyncio  # noqa: I001, RUF100
import contextlib
import inspect
from datetime import timedelta
import logging
import os
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import (
    config_validation as cv,
    device_registry as dr,  # noqa: TC001
    entity_registry as er,
)  # noqa: E501, RUF100, TC001
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed

from .client import JackeryApi, JackeryAuthError, JackeryError
from .client.local_mqtt import (
    JackeryLocalMqttClient,
)
from .const import (
    CALCULATED_POWER_SENSOR_SUFFIXES,
    CONF_CREATE_CALCULATED_POWER_SENSORS,
    CONF_CREATE_SAVINGS_DETAIL_SENSORS,
    CONF_CREATE_SMART_METER_DERIVED_SENSORS,
    CONF_LOCAL_MQTT_ENABLE,
    CONF_LOCAL_MQTT_HOST,
    CONF_MQTT_MAC_ID,
    CONF_REGION_CODE,
    CONF_THIRD_PARTY_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_PORT,
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
    CONF_THIRD_PARTY_MQTT_USERNAME,
    CT_PERIOD_SENSOR_SUFFIXES,
    DEFAULT_CREATE_CALCULATED_POWER_SENSORS,
    DEFAULT_CREATE_SAVINGS_DETAIL_SENSORS,
    DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
    DEFAULT_LOCAL_MQTT_ENABLE,
    DEFAULT_SCAN_INTERVAL_SEC,
    DEFAULT_THIRD_PARTY_MQTT_ENABLE,
    DEFAULT_THIRD_PARTY_MQTT_PASSWORD,
    DEFAULT_THIRD_PARTY_MQTT_PORT,
    DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER,
    DEFAULT_THIRD_PARTY_MQTT_USERNAME,
    DOMAIN,
    ENTRY_BOOTSTRAP_MQTT_SESSION,
    DUPLICATE_BINARY_SENSOR_SUFFIXES,
    MQTT_SESSION_MAC_ID,
    MQTT_SESSION_MAC_ID_SOURCE,
    MQTT_SESSION_SEED_B64,
    MQTT_SESSION_USER_ID,
    PLATFORMS,
    REMOVED_SENSOR_SUFFIXES,
    SAVINGS_DETAIL_SENSOR_SUFFIXES,
    SMART_METER_DERIVED_SENSOR_SUFFIXES,
    STALE_ENERGY_HELPER_PREFIX,
    STALE_HELPER_VENDOR_TOKENS,
    STALE_NET_POWER_SUFFIX,
)
from .coordinator import JackerySolarVaultCoordinator
from .mqtt_session_cache import async_load_mqtt_session, async_save_mqtt_session
from .services import async_setup_services
from .util import (
    config_entry_bool_option,
    config_entry_int_option,
    config_entry_str_option,
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
type JackeryConfigEntry = ConfigEntry[JackerySolarVaultCoordinator]

_LOGGER = logging.getLogger(__name__)
_BLOCKED_LOCAL_MQTT_TOPIC_FILTERS = frozenset({"#", "+/#"})
_JACKERY_ENV_PREFIX = "JACKERY_"


async def _load_dotenv_if_present(hass_config_path: Path) -> None:
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

    def _read_env_sync() -> str | None:
        """Read the .env file content if present.

        Returns:
            str: The file contents decoded as UTF-8 if the .env file exists and is readable.
            None: If the file does not exist or cannot be read.
        """
        if not env_file.is_file():
            return None
        try:
            return env_file.read_text(encoding="utf-8")
        except OSError:
            return None

    text: str | None = await asyncio.to_thread(_read_env_sync)
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
    from . import util as _util

    _util._DEV_MODE_CACHED = None  # noqa: SLF001


# This integration is config-entry-only — there is no YAML configuration
# surface. The `cv.config_entry_only_config_schema` helper documents
# that contract to hassfest and rejects any YAML the user might add by
# accident.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Perform global integration setup for Jackery SolarVault.

    If a .env file exists in Home Assistant's config directory, load any environment
    variables prefixed with `JACKERY_` into the process environment. Register
    integration-level services.

    Parameters:
        hass (HomeAssistant): Home Assistant core instance.
        config (dict): Configuration passed by Home Assistant (unused).

    Returns:
        True on successful setup.
    """
    await _load_dotenv_if_present(Path(hass.config.config_dir))
    async_setup_services(hass)
    return True


def _async_clean_legacy_entities(
    hass: HomeAssistant, entry: JackeryConfigEntry
) -> None:
    """Remove entity-registry entries that are obsolete or disabled by the entry's options.

    This scans and removes stale energy helper entities and legacy sensors/binary sensors whose unique IDs match known legacy suffixes. Sensors associated with optional features are removed when the corresponding config entry option is disabled. The function performs in-place removals in Home Assistant's entity registry.
    """
    _async_remove_stale_energy_helpers(hass)
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


_STARTUP_TASK_RUNTIME_KEY = "startup_task"


def _entry_bootstrap_mqtt_session(entry: ConfigEntry) -> dict[str, str] | None:
    """Validate and extract a bootstrap MQTT session snapshot from a config entry's data.

    Parameters:
        entry (ConfigEntry): Config entry whose data may contain an `ENTRY_BOOTSTRAP_MQTT_SESSION` mapping.

    Returns:
        dict[str, str]: Validated snapshot containing `MQTT_SESSION_USER_ID`, `MQTT_SESSION_SEED_B64`, and `MQTT_SESSION_MAC_ID`, and optionally `MQTT_SESSION_MAC_ID_SOURCE`, or `None` if the snapshot is missing or any required field is absent/invalid.
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


def _entry_runtime_bucket(hass: HomeAssistant, entry: ConfigEntry) -> dict[str, Any]:
    """Get or create the mutable runtime data bucket for a config entry stored in hass.data under the integration domain.

    Parameters:
        hass (HomeAssistant): Home Assistant core instance.
        entry (ConfigEntry): The config entry whose runtime bucket is requested.

    Returns:
        dict[str, Any]: The dictionary stored at hass.data[DOMAIN][entry.entry_id]; created and inserted if it did not already exist.
    """
    domain_bucket = hass.data.setdefault(DOMAIN, {})
    bucket = domain_bucket.get(entry.entry_id)
    if not isinstance(bucket, dict):
        bucket = {}
        domain_bucket[entry.entry_id] = bucket
    return bucket


def _entry_startup_task(
    hass: HomeAssistant, entry: ConfigEntry
) -> asyncio.Task[Any] | None:
    """Get the background startup task registered for the given config entry.

    Returns:
        The asyncio.Task instance for the entry's startup task, or `None` if no task is registered or the runtime bucket is missing/invalid.
    """
    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not isinstance(bucket, dict):
        return None
    task = bucket.get(_STARTUP_TASK_RUNTIME_KEY)
    return task if isinstance(task, asyncio.Task) else None


async def _async_cancel_startup_task(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Cancel and remove the per-entry background startup task if present."""
    task = _entry_startup_task(hass, entry)
    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if isinstance(bucket, dict):
        bucket.pop(_STARTUP_TASK_RUNTIME_KEY, None)
    if task is None or task.done():
        return
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _async_call_if_present(obj: object, name: str) -> None:
    """Call and await the attribute named by `name` on `obj` if it exists and is callable.

    Parameters:
        obj (object): The object to inspect for the attribute.
        name (str): The attribute name to call. The attribute will be invoked with no arguments;
            if the call returns an awaitable, it will be awaited.
    """
    method = getattr(obj, name, None)
    if not callable(method):
        return
    result = method()
    if inspect.isawaitable(result):
        await result


async def _async_prime_entry_bootstrap_mqtt_session(
    hass: HomeAssistant,
    entry: ConfigEntry,
    api: JackeryApi,
) -> dict[str, str] | None:
    """Hydrate and persist a bootstrap MQTT session stored in the config entry."""
    snapshot = _entry_bootstrap_mqtt_session(entry)
    if snapshot is None:
        return None
    api.hydrate_mqtt_session(
        user_id=snapshot[MQTT_SESSION_USER_ID],
        seed_b64=snapshot[MQTT_SESSION_SEED_B64],
        mac_id=snapshot[MQTT_SESSION_MAC_ID],
        mac_id_source=snapshot.get(MQTT_SESSION_MAC_ID_SOURCE),
    )
    await async_save_mqtt_session(hass, entry.entry_id, **snapshot)  # type: ignore[arg-type]
    return snapshot


async def _async_authenticate_api_layer(
    hass: HomeAssistant,
    entry: ConfigEntry,
    api: JackeryApi,
) -> None:
    """Authenticate the cloud API layer and persist an updated MQTT session snapshot when appropriate.

    Primes the API with any bootstrap or cached MQTT session, performs an HTTP login, and, after a successful login,
    saves the API's current MQTT session snapshot to persistent storage if it differs from the cached or bootstrap snapshot.

    Parameters:
        hass (HomeAssistant): Home Assistant core instance.
        entry (ConfigEntry): Configuration entry for the integration.
        api (JackeryApi): API client instance to authenticate and hydrate.

    Raises:
        ConfigEntryAuthFailed: If the Jackery credentials are rejected (triggers re-auth flow).
    """
    # Prefer the persisted session: it is refreshed after every successful login
    # and therefore holds the freshest credentials. Only fall back to the
    # one-time config-entry bootstrap snapshot when no persisted session exists.
    # Priming the bootstrap also *saves* it, so doing that unconditionally would
    # clobber the fresher persisted session before it is loaded back.
    cached = await async_load_mqtt_session(hass, entry.entry_id)
    if cached:
        bootstrap = None
        api.hydrate_mqtt_session(
            user_id=cached[MQTT_SESSION_USER_ID],
            seed_b64=cached[MQTT_SESSION_SEED_B64],
            mac_id=cached[MQTT_SESSION_MAC_ID],
            mac_id_source=cached.get(MQTT_SESSION_MAC_ID_SOURCE),
        )
    else:
        bootstrap = await _async_prime_entry_bootstrap_mqtt_session(hass, entry, api)
    try:
        await api.async_login()
    except JackeryAuthError as err:
        raise ConfigEntryAuthFailed(  # noqa: TRY003
            f"Jackery login rejected the credentials: {err}"
        ) from err
    except JackeryError as err:
        _LOGGER.warning(
            "Jackery cloud login is unavailable; continuing with cached local "
            "transports: %s",
            err,
        )
        return
    snapshot = api.mqtt_session_snapshot()
    if snapshot is not None and not any(
        snapshot == existing for existing in (cached, bootstrap)
    ):
        await async_save_mqtt_session(hass, entry.entry_id, **snapshot)  # type: ignore[arg-type]


def _defer_coordinator_auth_failure(
    coordinator: JackerySolarVaultCoordinator,
    err: ConfigEntryAuthFailed,
) -> None:
    """Record an authentication failure on the coordinator for later background handling.

    Parameters:
        coordinator: Coordinator that will store the deferred auth failure and message.
        err: The ConfigEntryAuthFailed exception to record.
    """
    coordinator._defer_background_auth_failure(err)  # noqa: SLF001
    coordinator._mqtt_auth_failure_message = str(err)  # noqa: SLF001


async def _async_discover_with_cache_fallback(
    coordinator: JackerySolarVaultCoordinator,
) -> bool:
    """Run discovery on the coordinator, falling back to a cached discovery snapshot if discovery fails.

    If discovery raises a ConfigEntryAuthFailed, the authentication failure is deferred and startup should stop. If discovery raises an UpdateFailed and a cached discovery snapshot exists, that snapshot is injected so transports that depend on discovery can still start.

    Returns:
        bool: `True` if discovery succeeded or startup may continue; `False` if authentication failed and startup must stop.
    """
    try:
        await coordinator.async_discover()
    except ConfigEntryAuthFailed as err:
        _defer_coordinator_auth_failure(coordinator, err)
        return False
    except UpdateFailed as err:
        cached_snapshot = coordinator.cached_discovery_snapshot()
        if cached_snapshot:
            _LOGGER.warning(
                "Jackery discovery failed; loading cached discovery payload so "
                "local MQTT/BLE transports can start: %s",
                err,
            )
            coordinator.async_set_updated_data(cached_snapshot)
        else:
            _LOGGER.warning("Jackery discovery failed without cached data: %s", err)
    return True


def _handle_refresh_startup_result(
    coordinator: JackerySolarVaultCoordinator,
    result: BaseException | object,
) -> None:
    """Process the coordinator's initial HTTP refresh result performed during startup.

    If `result` is a `ConfigEntryAuthFailed`, the coordinator's auth failure is deferred. If `result` is a `UpdateFailed` and the coordinator has a cached discovery snapshot, that cached payload is applied so transports can proceed. If `result` is any other exception, a warning is logged. If `result` is not an exception, no action is taken.

    Parameters:
        coordinator: The coordinator instance whose state may be updated or where auth failures are deferred.
        result: The value or exception produced by the coordinator's first refresh attempt.
    """
    if isinstance(result, ConfigEntryAuthFailed):
        _defer_coordinator_auth_failure(coordinator, result)
        return
    if not isinstance(result, BaseException):
        return
    if isinstance(result, UpdateFailed) and (
        cached := coordinator.cached_discovery_snapshot()
    ):
        _LOGGER.warning(
            "Jackery first HTTP refresh failed; loading cached discovery payload so "
            "local MQTT/BLE transports can start: %s",
            result,
        )
        coordinator.async_set_updated_data(cached)
        return
    _LOGGER.warning("Jackery first HTTP refresh failed: %s", result)


def _handle_optional_startup_result(
    coordinator: JackerySolarVaultCoordinator,
    result: BaseException | object,
    *,
    label: str,
) -> None:
    """Process a non-critical startup-task result, deferring auth failures or logging other exceptions.

    Parameters:
        coordinator (JackerySolarVaultCoordinator): Coordinator instance used to record or defer auth failures.
        result (BaseException | object): The outcome from a parallel startup task; may be an exception.
        label (str): Short label identifying the startup layer (used in logs).
    """
    if isinstance(result, ConfigEntryAuthFailed):
        _defer_coordinator_auth_failure(coordinator, result)
    elif isinstance(result, BaseException):
        _LOGGER.warning("Jackery %s could not start: %s", label, result)


async def _async_finish_entry_startup(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
) -> None:
    """Complete the entry's background startup sequence for API/cloud and local transports.

    Performs API/cloud authentication (defers coordinator auth failure on auth errors), attempts discovery with cache fallback, runs optional state/load hooks, and concurrently starts the first data refresh plus MQTT, HA-MQTT listener, direct local MQTT listener, and BLE transport. Non-fatal transport failures are logged/handled by the coordinator and do not block overall entry startup. After transports are started it triggers statistics imports and schedules applying local MQTT configuration to devices. Always removes the per-entry startup task reference from the runtime bucket on exit.
    """
    try:
        try:
            await _async_authenticate_api_layer(hass, entry, coordinator.api)
        except ConfigEntryAuthFailed as err:
            _defer_coordinator_auth_failure(coordinator, err)
            return

        await _async_call_if_present(
            coordinator, "async_load_statistics_backfill_state"
        )
        if not await _async_discover_with_cache_fallback(coordinator):
            return

        await _async_call_if_present(coordinator, "async_load_local_daily_snapshots")
        (
            refresh_result,
            mqtt_result,
            local_listener_result,
            direct_local_mqtt_result,
            ble_result,
        ) = await asyncio.gather(
            coordinator.async_refresh(),
            coordinator.async_start_mqtt(),
            coordinator.async_start_local_mqtt_listener(),
            _async_start_local_mqtt(hass, entry, coordinator),
            coordinator.async_start_ble_transport(),
            return_exceptions=True,
        )
        _handle_refresh_startup_result(coordinator, refresh_result)
        _handle_optional_startup_result(coordinator, mqtt_result, label="MQTT push")
        _handle_optional_startup_result(
            coordinator, local_listener_result, label="HA-MQTT listener"
        )
        _handle_optional_startup_result(
            coordinator, direct_local_mqtt_result, label="local MQTT listener"
        )
        _handle_optional_startup_result(coordinator, ble_result, label="BLE transport")

        coordinator.async_start_statistics_imports()
        hass.async_create_background_task(
            coordinator.async_apply_local_mqtt_config_to_devices(),
            name=f"{DOMAIN}_apply_local_mqtt_config",
        )
    finally:
        bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if isinstance(bucket, dict):
            bucket.pop(_STARTUP_TASK_RUNTIME_KEY, None)


_LOCAL_MQTT_RUNTIME_KEY = "local_mqtt_client"


def _local_mqtt_client(
    hass: HomeAssistant, entry: JackeryConfigEntry
) -> JackeryLocalMqttClient | None:
    """Get the per-entry local MQTT client stored in hass.data for the given config entry, if present.

    Returns:
        The JackeryLocalMqttClient for the entry, or `None` if no client is stored or the stored value is not a `JackeryLocalMqttClient`.
    """
    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not isinstance(bucket, dict):
        return None
    client = bucket.get(_LOCAL_MQTT_RUNTIME_KEY)
    return client if isinstance(client, JackeryLocalMqttClient) else None


async def _async_start_local_mqtt(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
) -> None:
    """Start the optional per-entry local MQTT listener (PROTOCOL.md §5).

    The listener only starts when the Third-Party MQTT bridge is enabled, the
    host is set, and a non-empty topic filter is configured. This prevents an
    accidental broad wildcard subscription from ingesting unrelated broker
    traffic and causing high CPU load. When started, the client is stored at
    ``hass.data[DOMAIN][entry.entry_id][_LOCAL_MQTT_RUNTIME_KEY]`` and an unload
    callback is registered that stops the client and removes that runtime
    reference only if it still points to the same client instance.

    Local MQTT is an additive channel on top of HTTP polling + cloud MQTT push;
    failures here never block setup (AGENTS.md §3.3).
    """
    if not config_entry_bool_option(
        entry, CONF_LOCAL_MQTT_ENABLE, DEFAULT_LOCAL_MQTT_ENABLE
    ) and not config_entry_bool_option(
        entry, CONF_THIRD_PARTY_MQTT_ENABLE, DEFAULT_THIRD_PARTY_MQTT_ENABLE
    ):
        return
    host = config_entry_str_option(entry, CONF_LOCAL_MQTT_HOST, "").strip()
    if not host:
        host = config_entry_str_option(entry, CONF_THIRD_PARTY_MQTT_IP, "").strip()
    if not host:
        return
    topic_filter = config_entry_str_option(
        entry,
        CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
        DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER,
    ).strip()
    if not topic_filter:
        return
    if topic_filter in _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS:
        _LOGGER.warning(
            "Jackery local MQTT listener not started: broad topic filter %r is "
            "blocked for CPU safety; configure a scoped filter or leave empty to disable",
            topic_filter,
        )
        return
    port = config_entry_int_option(
        entry, CONF_THIRD_PARTY_MQTT_PORT, DEFAULT_THIRD_PARTY_MQTT_PORT
    )
    username = config_entry_str_option(
        entry, CONF_THIRD_PARTY_MQTT_USERNAME, DEFAULT_THIRD_PARTY_MQTT_USERNAME
    )
    password = config_entry_str_option(
        entry, CONF_THIRD_PARTY_MQTT_PASSWORD, DEFAULT_THIRD_PARTY_MQTT_PASSWORD
    )

    async def _sink(
        topic: str,
        data: dict[str, Any] | None,
        _raw_bytes: bytes,
    ) -> None:
        """Dispatches a parsed local MQTT JSON message to the coordinator.

        Parameters:
            topic (str): MQTT topic the message was received on.
            data (dict[str, Any] | None): Parsed JSON payload to route; if `None`, the message is ignored.
        """
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
    bucket = hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})
    bucket[_LOCAL_MQTT_RUNTIME_KEY] = client

    async def _async_stop_local_mqtt() -> None:
        """Stop the stored per-entry local MQTT client and remove its runtime reference if it matches.

        If stopping the client raises any exception, the exception is suppressed and the runtime reference removal is attempted regardless.
        """
        with contextlib.suppress(Exception):
            await client.async_stop()
        stashed = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        if isinstance(stashed, dict) and stashed.get(_LOCAL_MQTT_RUNTIME_KEY) is client:
            stashed.pop(_LOCAL_MQTT_RUNTIME_KEY, None)

    def _on_unload() -> None:
        hass.async_create_task(
            _async_stop_local_mqtt(),
            name=f"{DOMAIN}_stop_local_mqtt_{entry.entry_id}",
        )

    entry.async_on_unload(_on_unload)
    await client.async_start()


async def async_setup_entry(hass: HomeAssistant, entry: JackeryConfigEntry) -> bool:
    """Set up the config entry and start all transport layers in the background.

    The UI config flow already performs the mandatory HTTP login and stores the
    credential/cache bootstrap data. During HA startup we therefore create the
    coordinator, forward platforms, then let HTTP refresh, API push/MQTT, BLE and
    local MQTT start in parallel. Background auth failures are deferred to the
    coordinator so a rejected session cannot bootloop Home Assistant.
    """
    _async_clean_legacy_entities(hass, entry)
    await _async_cancel_startup_task(hass, entry)

    session = async_get_clientsession(hass)
    api = JackeryApi(
        session=session,
        account=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        mqtt_mac_id=entry.data.get(CONF_MQTT_MAC_ID),
        region_code=entry.data.get(CONF_REGION_CODE),
    )
    cached = await async_load_mqtt_session(hass, entry.entry_id)
    if cached:
        api.hydrate_mqtt_session(
            user_id=cached[MQTT_SESSION_USER_ID],
            seed_b64=cached[MQTT_SESSION_SEED_B64],
            mac_id=cached[MQTT_SESSION_MAC_ID],
            mac_id_source=cached.get(MQTT_SESSION_MAC_ID_SOURCE),
        )

    interval_sec = DEFAULT_SCAN_INTERVAL_SEC
    coordinator = JackerySolarVaultCoordinator(
        hass, entry, api, timedelta(seconds=interval_sec)
    )
    entry.runtime_data = coordinator
    entry._last_applied_options = dict(entry.options or {})  # noqa: SLF001
    _LOGGER.info("Jackery: coordinator polling interval set to %ss", interval_sec)

    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception as err:  # noqa: BLE001
        _LOGGER.debug("Jackery setup failed after coordinator creation: %s", err)
        with contextlib.suppress(Exception):
            await coordinator.async_shutdown()
        if entry.runtime_data is coordinator:
            entry.runtime_data = cast("Any", None)
        raise

    startup_task = hass.async_create_background_task(
        _async_finish_entry_startup(hass, entry, coordinator),
        name=f"{DOMAIN}_startup_{entry.entry_id}",
    )
    _entry_runtime_bucket(hass, entry)[_STARTUP_TASK_RUNTIME_KEY] = startup_task
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


# -----------------------------------------------------------------------------
# Registry cleanup helpers
# -----------------------------------------------------------------------------


def _async_remove_stale_energy_helpers(hass: HomeAssistant) -> None:
    """Remove stale Energy helper entities that were created without a unit of measurement.

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


def _legacy_suffix_matches(uid: str, key_suffix: str) -> bool:
    """Check whether a unique id consists of a legacy device head immediately followed by the given suffix.

    A legacy device head is either `<digits>` or `<digits>_battery_pack_<digits>`.

    Parameters:
        uid (str): The unique id to test.
        key_suffix (str): The suffix to anchor to the legacy head.

    Returns:
        bool: `True` if `uid` equals a legacy head concatenated with `key_suffix`, `False` otherwise.
    """
    if not key_suffix:
        return _LEGACY_UID_HEAD_RE.fullmatch(uid) is not None
    if not uid.endswith(key_suffix):
        return False
    head = uid[: -len(key_suffix)]
    return _LEGACY_UID_HEAD_RE.fullmatch(head) is not None


def _async_remove_entities_with_suffixes(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    *,
    domain: str,
    suffixes: Iterable[str],
    log_label: str,
) -> None:
    """Remove entity-registry entries for the given config entry and domain whose legacy unique IDs end with any of the provided legacy suffixes.

    Matching only applies when the unique ID conforms to the legacy unique-id
    shape to avoid accidental removal of current entities. Unique IDs for this
    integration follow ``<device_id>_<key_suffix>`` per docs/PROTOCOL.md §11, so
    anchored suffix matching is the right way to drop legacy or option-disabled
    entities without scanning HA-wide registry entries owned by other
    integrations. The match is anchored via :func:`_legacy_suffix_matches` so a
    legacy suffix cannot accidentally delete a current entity whose key happens
    to contain the legacy tail. If ``suffixes`` is empty, no action is taken.

    Parameters:
        domain (str): Entity domain to restrict removals (e.g., "sensor",
            "binary_sensor").
        suffixes (Iterable[str]): Iterable of legacy unique-id suffix strings;
            an entity is removed if its unique ID matches any suffix.
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


async def _async_update_listener(
    hass: HomeAssistant, entry: JackeryConfigEntry
) -> None:
    """Apply options changes without a full reload when possible.

    Entity-creating option toggles (smart_meter_derived, calculated_power,
    savings_detail) require a full reload because entity sets change.
    All other options (MQTT settings, BLE toggle, statistics toggles) are
    applied in-place by the coordinator without tearing down platforms.
    """
    entity_creating_options: frozenset[str] = frozenset({
        CONF_CREATE_SMART_METER_DERIVED_SENSORS,
        CONF_CREATE_CALCULATED_POWER_SENSORS,
        CONF_CREATE_SAVINGS_DETAIL_SENSORS,
    })
    coordinator: JackerySolarVaultCoordinator | None = entry.runtime_data
    old_options = dict(getattr(entry, "_last_applied_options", {}) or {})
    new_options = dict(entry.options or {})
    entry._last_applied_options = dict(new_options)  # noqa: SLF001

    entity_options_changed = any(
        old_options.get(key) != new_options.get(key) for key in entity_creating_options
    )
    if entity_options_changed:
        await hass.config_entries.async_reload(entry.entry_id)
        return

    if isinstance(coordinator, JackerySolarVaultCoordinator):
        await coordinator.async_request_refresh()


async def async_unload_entry(hass: HomeAssistant, entry: JackeryConfigEntry) -> bool:
    """Unload the config entry and tear down its runtime resources.

    If platform unload succeeds, shuts down the coordinator (if present) and
    clears the entry's runtime data to avoid retaining the coordinator. Teardown
    is performed only when platforms are successfully unloaded.

    Returns:
        True if platforms were unloaded and runtime teardown completed, False
        otherwise.
    """
    coordinator: JackerySolarVaultCoordinator | None = entry.runtime_data
    await _async_cancel_startup_task(hass, entry)
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    # Keep the successful teardown explicitly gated by unload_ok so future
    # changes cannot stop the coordinator while HA still has loaded platforms.
    if not unload_ok:
        return False
    if isinstance(coordinator, JackerySolarVaultCoordinator):
        await coordinator.async_shutdown()
    # HA convention on unload: drop the runtime_data reference so any
    # stragglers cannot keep the coordinator alive. Same narrowing caveat
    # as the setup-failure path above.
    entry.runtime_data = cast("Any", None)
    return True


async def async_remove_config_entry_device(  # noqa: RUF029
    hass: HomeAssistant, entry: JackeryConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow a user to remove a device from the config entry.

    If the device still exists in the Jackery account, the integration's coordinator may rediscover it on the next poll; permitting removal here only affects the Home Assistant device registry entry.

    Returns:
        True if removal is allowed, False to prevent removal.
    """
    return True
