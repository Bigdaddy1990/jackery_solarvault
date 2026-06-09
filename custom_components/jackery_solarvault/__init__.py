"""Jackery SolarVault integration."""

import asyncio
import contextlib
from datetime import timedelta
import logging
import re
from typing import TYPE_CHECKING, Any, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import UpdateFailed

from .client import JackeryApi, JackeryAuthError, JackeryError
from .client.local_mqtt import JackeryLocalMqttClient
from .const import (
    CALCULATED_POWER_SENSOR_SUFFIXES,
    CONF_CREATE_CALCULATED_POWER_SENSORS,
    CONF_CREATE_SAVINGS_DETAIL_SENSORS,
    CONF_CREATE_SMART_METER_DERIVED_SENSORS,
    CONF_LOCAL_MQTT_ENABLE,
    CONF_LOCAL_MQTT_HOST,
    CONF_LOCAL_MQTT_PASSWORD,
    CONF_LOCAL_MQTT_PORT,
    CONF_LOCAL_MQTT_USERNAME,
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
    DEFAULT_LOCAL_MQTT_PORT,
    DEFAULT_SCAN_INTERVAL_SEC,
    DEFAULT_THIRD_PARTY_MQTT_ENABLE,
    DEFAULT_THIRD_PARTY_MQTT_PASSWORD,
    DEFAULT_THIRD_PARTY_MQTT_PORT,
    DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER,
    DEFAULT_THIRD_PARTY_MQTT_USERNAME,
    DOMAIN,
    DUPLICATE_BINARY_SENSOR_SUFFIXES,
    ENTRY_BOOTSTRAP_MQTT_SESSION,
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
    from homeassistant.helpers import device_registry as dr

# Typed ConfigEntry alias — the runtime_data attribute is a
# JackerySolarVaultCoordinator. Per HA developer guide (2024.4+) this
# alias lets type-checkers see through ``entry.runtime_data`` to the
# concrete coordinator type without sprinkling cast/getattr around
# the integration. PEP 695 syntax requires Python 3.12+; HA 2025.x
# already requires Python 3.14.
type JackeryConfigEntry = ConfigEntry[JackerySolarVaultCoordinator]

_LOGGER = logging.getLogger(__name__)
_BLOCKED_LOCAL_MQTT_TOPIC_FILTERS = frozenset({"#", "+/#"})
_STARTUP_TASK_RUNTIME_KEY = "startup_task"


# This integration is config-entry-only — there is no YAML configuration
# surface. The `cv.config_entry_only_config_schema` helper documents
# that contract to hassfest and rejects any YAML the user might add by
# accident.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:  # noqa: RUF029
    """Set up global Jackery SolarVault services.

    Declared ``async`` because Home Assistant awaits the integration's
    ``async_setup`` entry point; the framework contract mandates the
    coroutine signature even though the body currently performs only
    synchronous service registration. Hence RUF029 is suppressed here.
    """
    async_setup_services(hass)
    return True


def _async_clean_legacy_entities(
    hass: HomeAssistant, entry: JackeryConfigEntry
) -> None:
    """Drop entity-registry entries from older releases or disabled options.

    Keep entity-registry cleanup explicit and setup-local. This avoids hidden
    entry-version side effects while still removing entities that are no
    longer part of the documented app/HTTP/MQTT data model.
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


def _entry_bootstrap_mqtt_session(
    entry: JackeryConfigEntry,
) -> dict[str, str] | None:
    """Return a validated bootstrap MQTT session snapshot stored on the entry."""
    raw = entry.data.get(ENTRY_BOOTSTRAP_MQTT_SESSION)
    if not isinstance(raw, dict):
        return None
    user_id = raw.get(MQTT_SESSION_USER_ID)
    seed_b64 = raw.get(MQTT_SESSION_SEED_B64)
    mac_id = raw.get(MQTT_SESSION_MAC_ID)
    if not all(
        isinstance(value, str) and value for value in (user_id, seed_b64, mac_id)
    ):
        return None
    snapshot: dict[str, str] = {
        MQTT_SESSION_USER_ID: user_id,
        MQTT_SESSION_SEED_B64: seed_b64,
        MQTT_SESSION_MAC_ID: mac_id,
    }
    source = raw.get(MQTT_SESSION_MAC_ID_SOURCE)
    if isinstance(source, str) and source:
        snapshot[MQTT_SESSION_MAC_ID_SOURCE] = source
    return snapshot


async def _async_prime_entry_bootstrap_mqtt_session(
    hass: HomeAssistant, entry: JackeryConfigEntry
) -> dict[str, str] | None:
    """Persist a setup-flow bootstrap MQTT session into the cache store once."""
    snapshot = _entry_bootstrap_mqtt_session(entry)
    if snapshot is None:
        return None
    cached = await async_load_mqtt_session(hass, entry.entry_id)
    if cached != snapshot:
        await async_save_mqtt_session(
            hass,
            entry.entry_id,
            **snapshot,  # type: ignore[arg-type]
        )
    cleaned = dict(entry.data)
    if ENTRY_BOOTSTRAP_MQTT_SESSION in cleaned:
        cleaned.pop(ENTRY_BOOTSTRAP_MQTT_SESSION, None)
        hass.config_entries.async_update_entry(entry, data=cleaned)
    return snapshot


def _entry_runtime_bucket(
    hass: HomeAssistant, entry: JackeryConfigEntry
) -> dict[str, Any]:
    """Return the mutable per-entry runtime bucket stored in hass.data."""
    return hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})


def _entry_startup_task(
    hass: HomeAssistant, entry: JackeryConfigEntry
) -> asyncio.Task[None] | None:
    """Return the per-entry background startup task, if one is registered."""
    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not isinstance(bucket, dict):
        return None
    task = bucket.get(_STARTUP_TASK_RUNTIME_KEY)
    return task if isinstance(task, asyncio.Task) else None


def _build_api(hass: HomeAssistant, entry: JackeryConfigEntry) -> JackeryApi:
    """Construct the API client from persisted config-entry credentials."""
    session = async_get_clientsession(hass)
    return JackeryApi(
        session=session,
        account=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        mqtt_mac_id=entry.data.get(CONF_MQTT_MAC_ID),
        region_code=entry.data.get(CONF_REGION_CODE),
    )


async def _async_restore_cached_mqtt_session(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    api: JackeryApi,
) -> dict[str, str] | None:
    """Hydrate the API client from cached/bootstrap MQTT session data."""
    bootstrap = await _async_prime_entry_bootstrap_mqtt_session(hass, entry)
    cached = await async_load_mqtt_session(hass, entry.entry_id) or bootstrap
    if cached:
        api.hydrate_mqtt_session(
            user_id=cached[MQTT_SESSION_USER_ID],
            seed_b64=cached[MQTT_SESSION_SEED_B64],
            mac_id=cached[MQTT_SESSION_MAC_ID],
            mac_id_source=cached.get(MQTT_SESSION_MAC_ID_SOURCE),
        )
    return cached


async def _async_authenticate_api_layer(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    api: JackeryApi,
) -> None:
    """Authenticate the shared API client and persist a refreshed MQTT session.

    The setup path itself must stay fast and return before long-running network
    handshakes block Home Assistant startup. Therefore this login/auth step is
    executed by the background startup orchestration after the config entry has
    already been created and platform forwarding has completed.

    Raises:
        ConfigEntryAuthFailed: If the configured account credentials are invalid.
    """
    cached = await _async_restore_cached_mqtt_session(hass, entry, api)
    try:
        await api.async_login()
    except JackeryAuthError as err:
        raise ConfigEntryAuthFailed(  # noqa: TRY003
            f"Jackery login rejected the credentials: {err}"
        ) from err
    except JackeryError as err:
        _LOGGER.warning(
            "Jackery cloud login is unavailable during background startup; "
            "trying cached discovery and local transports: %s",
            err,
        )
        return

    snapshot = api.mqtt_session_snapshot()
    if snapshot is not None and snapshot != cached:
        await async_save_mqtt_session(
            hass,
            entry.entry_id,
            user_id=snapshot[MQTT_SESSION_USER_ID],
            seed_b64=snapshot[MQTT_SESSION_SEED_B64],
            mac_id=snapshot[MQTT_SESSION_MAC_ID],
            mac_id_source=snapshot.get(MQTT_SESSION_MAC_ID_SOURCE),
        )


_LOCAL_MQTT_RUNTIME_KEY = "local_mqtt_client"


def _local_mqtt_client(
    hass: HomeAssistant, entry: JackeryConfigEntry
) -> JackeryLocalMqttClient | None:
    """Return the per-entry local MQTT client stored in hass.data, if any.

    Returns:
        JackeryLocalMqttClient instance for the entry, or None if no client is
        stored or the stored value is not a JackeryLocalMqttClient.
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
    ):
        return
    host = config_entry_str_option(entry, CONF_LOCAL_MQTT_HOST, "").strip()
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
            "blocked for CPU safety; configure a scoped filter or leave empty",
            topic_filter,
        )
        return
    port = config_entry_int_option(entry, CONF_LOCAL_MQTT_PORT, DEFAULT_LOCAL_MQTT_PORT)
    username = config_entry_str_option(entry, CONF_LOCAL_MQTT_USERNAME, "")
    password = config_entry_str_option(entry, CONF_LOCAL_MQTT_PASSWORD, "")

    async def _sink(
        topic: str,
        data: dict[str, Any] | None,
        _raw_bytes: bytes,
    ) -> None:
        """Forward parsed LAN MQTT JSON into the coordinator payload router."""
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
    bucket = _entry_runtime_bucket(hass, entry)
    bucket[_LOCAL_MQTT_RUNTIME_KEY] = client

    async def _async_stop_local_mqtt() -> None:
        """Stop the stored per-entry local MQTT client.

        Remove its hass.data reference when it still points at the same
        instance. Exceptions raised while stopping the client are suppressed.
        """
        with contextlib.suppress(Exception):
            await client.async_stop()
        stashed = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        if isinstance(stashed, dict) and stashed.get(_LOCAL_MQTT_RUNTIME_KEY) is client:
            stashed.pop(_LOCAL_MQTT_RUNTIME_KEY, None)

    entry.async_on_unload(_async_stop_local_mqtt)
    await client.async_start()


async def _async_cancel_startup_task(
    hass: HomeAssistant, entry: JackeryConfigEntry
) -> None:
    """Cancel the per-entry background startup task, if still running."""
    task = _entry_startup_task(hass, entry)
    if task is None:
        return
    if not task.done():
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if isinstance(bucket, dict):
        bucket.pop(_STARTUP_TASK_RUNTIME_KEY, None)


async def _async_finish_entry_startup(  # noqa: PLR0912, PLR0915
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    coordinator: JackerySolarVaultCoordinator,
) -> None:
    """Complete cloud discovery and transport startup after setup returned."""
    try:
        try:
            await _async_authenticate_api_layer(hass, entry, coordinator.api)
        except ConfigEntryAuthFailed as err:
            coordinator._mqtt_auth_failure_message = str(err)  # noqa: SLF001
            _LOGGER.warning(
                "Jackery HTTP/API credentials were rejected during background "
                "startup; reauth will be triggered on the next refresh"
            )
            return

        try:
            await coordinator.async_discover()
        except ConfigEntryAuthFailed as err:
            coordinator._mqtt_auth_failure_message = str(err)  # noqa: SLF001
            _LOGGER.warning(
                "Jackery discovery rejected the stored credentials during "
                "background startup; reauth will be triggered on the next refresh"
            )
            return
        except UpdateFailed as err:
            if await coordinator._async_load_cached_discovery(str(err)):  # noqa: SLF001
                coordinator.async_set_updated_data(
                    coordinator.cached_discovery_snapshot()
                )
            else:
                _LOGGER.warning(
                    "Jackery discovery unavailable during background startup and "
                    "no cache was available: %s",
                    err,
                )
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning(
                "Jackery discovery failed during background startup: %s",
                err,
            )

        async def _async_start_direct_local_mqtt_if_needed() -> None:
            """Start the direct local-MQTT client when absent."""
            if _local_mqtt_client(hass, entry) is not None:
                return
            await _async_start_local_mqtt(hass, entry, coordinator)

        (
            refresh_result,
            mqtt_result,
            local_listener_result,
            direct_local_mqtt_result,
            ble_result,
        ) = cast(
            (
                "tuple[BaseException | None, BaseException | None, "
                "BaseException | None, BaseException | None, "
                "BaseException | None]"
            ),
            await asyncio.gather(
                coordinator.async_config_entry_first_refresh(),
                coordinator.async_start_mqtt(),
                coordinator.async_start_local_mqtt_listener(),
                _async_start_direct_local_mqtt_if_needed(),
                coordinator.async_start_ble_transport(),
                return_exceptions=True,
            ),
        )
        if isinstance(refresh_result, BaseException):
            cached_snapshot = coordinator.cached_discovery_snapshot()
            if isinstance(refresh_result, UpdateFailed) and cached_snapshot:
                _LOGGER.warning(
                    "Jackery first HTTP refresh failed during background startup; "
                    "loading cached discovery payload so local MQTT/BLE can stay "
                    "active: %s",
                    refresh_result,
                )
                coordinator.async_set_updated_data(cached_snapshot)
            else:
                _LOGGER.warning(
                    "Jackery first HTTP refresh failed during background startup: %s",
                    refresh_result,
                )
        if isinstance(mqtt_result, ConfigEntryAuthFailed):
            coordinator._defer_background_auth_failure(mqtt_result)  # noqa: SLF001
        elif isinstance(mqtt_result, BaseException):
            _LOGGER.warning(
                "Jackery MQTT push could not start during background startup: %s",
                mqtt_result,
            )
        if isinstance(local_listener_result, BaseException):
            _LOGGER.warning(
                "Jackery HA-MQTT listener could not start during background startup: %s",  # noqa: E501
                local_listener_result,
            )
        if isinstance(direct_local_mqtt_result, BaseException):
            _LOGGER.warning(
                "Jackery direct local MQTT client could not start during "
                "background startup: %s",
                direct_local_mqtt_result,
            )
        if isinstance(ble_result, BaseException):
            _LOGGER.warning(
                "Jackery BLE transport could not start during background startup: %s",
                ble_result,
            )

        try:
            await coordinator.async_apply_local_mqtt_config_to_devices()
        except BaseException as err:  # noqa: BLE001
            _LOGGER.warning(
                "Jackery local third-party MQTT bridge configuration could not be "
                "applied during background startup: %s",
                err,
            )
    finally:
        bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if isinstance(bucket, dict):
            bucket.pop(_STARTUP_TASK_RUNTIME_KEY, None)


async def async_setup_entry(hass: HomeAssistant, entry: JackeryConfigEntry) -> bool:
    """Set up the config entry without blocking Home Assistant startup.

    The config entry returns as soon as the coordinator, cached local state and
    entity platforms are ready. Network-dependent login, discovery, first HTTP
    refresh and transport bring-up continue in a dedicated background task so a
    slow or unavailable Jackery cloud cannot stall Home Assistant boot or trip
    the integration setup timeout.
    """
    _async_clean_legacy_entities(hass, entry)
    api = _build_api(hass, entry)
    await _async_restore_cached_mqtt_session(hass, entry, api)

    interval_sec = DEFAULT_SCAN_INTERVAL_SEC
    coordinator = JackerySolarVaultCoordinator(
        hass, entry, api, timedelta(seconds=interval_sec)
    )
    _LOGGER.info("Jackery: coordinator polling interval set to %ss", interval_sec)

    try:  # noqa: PLW0717
        await coordinator.async_load_statistics_backfill_state()
        await coordinator.async_load_local_daily_snapshots()
        if await coordinator._async_load_cached_discovery("startup bootstrap"):  # noqa: SLF001
            coordinator.async_set_updated_data(coordinator.cached_discovery_snapshot())

        entry.runtime_data = coordinator
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        coordinator.async_start_statistics_imports()

        await _async_cancel_startup_task(hass, entry)
        startup_task = hass.async_create_background_task(
            _async_finish_entry_startup(hass, entry, coordinator),
            name=f"{DOMAIN}_startup_{entry.entry_id[:8]}",
        )
        _entry_runtime_bucket(hass, entry)[_STARTUP_TASK_RUNTIME_KEY] = startup_task
        # NOTE: add_update_listener is intentionally NOT used here.
        # It triggers async_reload which stops BLE + MQTT on every
        # options change, causing data loss and BLE/MQTT cycling.
        # Option changes are picked up by the coordinator's next
        # poll cycle instead.
    except Exception as err:
        with contextlib.suppress(Exception):
            await _async_cancel_startup_task(hass, entry)
        with contextlib.suppress(Exception):
            await coordinator.async_shutdown()
        if getattr(entry, "runtime_data", None) is coordinator:
            entry.runtime_data = cast("Any", None)
        if isinstance(err, UpdateFailed):
            raise ConfigEntryNotReady(str(err)) from err
        raise
    return True


# -----------------------------------------------------------------------------
# Registry cleanup helpers
# -----------------------------------------------------------------------------


def _async_remove_stale_energy_helpers(hass: HomeAssistant) -> None:
    """Remove stale Energy helper entities lacking a unit of measurement.

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
    """Check whether ``uid`` ends with ``key_suffix`` after a legacy head.

    A legacy device head has the form ``<digits>`` or
    ``<digits>_battery_pack_<digits>``. This returns ``True`` only when ``uid``
    ends with ``key_suffix`` and the substring before it exactly matches the
    legacy head pattern.

    The unique-id contract (docs/PROTOCOL.md §11) says every entity-registry id
    is ``<device_id>_<key_suffix>`` for the main device or
    ``<device_id>_battery_pack_<index>_<key_suffix>`` for an add-on battery. A
    plain ``str.endswith`` therefore over-matches when a current key contains a
    legacy key as its tail — e.g. legacy ``_today_battery_charge`` would
    otherwise also match the current ``_device_today_battery_charge`` unique id
    and delete a live sensor on every reload (the regression that caused
    statistics gaps at the user's site). This helper anchors the suffix to a
    valid head so only the legacy id matches.

    Returns:
        ``True`` if ``uid`` is a legacy head concatenated with ``key_suffix``,
        ``False`` otherwise.
    """
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
    """Remove matching legacy entity-registry entries for one domain.

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


async def async_unload_entry(hass: HomeAssistant, entry: JackeryConfigEntry) -> bool:
    """Unload the config entry and tear down its runtime resources.

    If platform unload succeeds, shuts down the coordinator (if present),
    cancels any deferred background startup task and clears the entry's runtime
    data to avoid retaining the coordinator.

    Returns:
        True if platforms were unloaded and runtime teardown completed, False
        otherwise.
    """
    coordinator: JackerySolarVaultCoordinator | None = entry.runtime_data
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False
    await _async_cancel_startup_task(hass, entry)
    if isinstance(coordinator, JackerySolarVaultCoordinator):
        await coordinator.async_shutdown()
    entry.runtime_data = cast("Any", None)
    return True


async def async_remove_config_entry_device(  # noqa: RUF029
    hass: HomeAssistant, entry: JackeryConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow removal of a device from the config entry.

    Returns True to allow the user to remove the device from the UI. Cloud
    integrations generally allow removal; the coordinator will re-discover
    the device on the next poll if it still exists in the Jackery account.
    """
    return True
