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


async def _async_authenticate(
    hass: HomeAssistant, entry: JackeryConfigEntry
) -> JackeryApi:
    """Authenticate to the Jackery cloud and return the API client.

    Cloud HTTP login is the documented entry point (AGENTS.md §1.2): it is the
    highest-priority source for live data, backfill, trends and statistics. The
    MQTT session is hydrated from the persistent cache BEFORE the login so the
    coordinator can (re-)open the broker session even while ``/v1/user/login``
    is unreachable (local > cloud for live values).

    Bad credentials raise ``ConfigEntryAuthFailed`` so HA routes the entry into
    the re-auth flow without removing it. Transient cloud errors do NOT break
    setup (AGENTS.md §3.3): they are logged and setup keeps moving so cached
    discovery and a cached MQTT session can start the local transports without
    a successful cloud round-trip.

    Returns:
        JackeryApi: The API client (authenticated when the cloud was reachable).

    Raises:
        ConfigEntryAuthFailed: If the provided credentials are rejected.
    """
    session = async_get_clientsession(hass)
    api = JackeryApi(
        session=session,
        account=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        mqtt_mac_id=entry.data.get(CONF_MQTT_MAC_ID),
        region_code=entry.data.get(CONF_REGION_CODE),
    )

    # Hydrate the MQTT session from the persistent cache BEFORE attempting
    # the cloud login. The MQTT password derivation is local, so a cached
    # ``userId/macId/seed`` triple lets the coordinator (re-)open the broker
    # session even while ``/v1/user/login`` is unreachable.
    cached = await async_load_mqtt_session(hass, entry.entry_id)
    if cached:
        api.hydrate_mqtt_session(
            user_id=cached[MQTT_SESSION_USER_ID],
            seed_b64=cached[MQTT_SESSION_SEED_B64],
            mac_id=cached[MQTT_SESSION_MAC_ID],
            mac_id_source=cached.get(MQTT_SESSION_MAC_ID_SOURCE),
        )

    try:
        await api.async_login()
    except JackeryAuthError as err:
        raise ConfigEntryAuthFailed(  # noqa: TRY003
            f"Jackery login rejected the credentials: {err}"
        ) from err
    except JackeryError as err:
        # Cloud failure must not break setup (AGENTS.md §3.3): fall back to
        # cached discovery and local transports; cloud reconnect is retried.
        _LOGGER.warning(
            "Jackery cloud login is unavailable during setup; trying cached "
            "discovery and local transports: %s",
            err,
        )
    else:
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
    return api


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
    bucket = hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})
    bucket[_LOCAL_MQTT_RUNTIME_KEY] = client

    async def _async_stop_local_mqtt() -> None:
        """Stop the stored per-entry local MQTT client and remove its hass.data reference if it matches the stored instance.

        Exceptions raised while stopping the client are suppressed.
        """  # noqa: E501
        with contextlib.suppress(Exception):
            await client.async_stop()
        stashed = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        if isinstance(stashed, dict) and stashed.get(_LOCAL_MQTT_RUNTIME_KEY) is client:
            stashed.pop(_LOCAL_MQTT_RUNTIME_KEY, None)

    entry.async_on_unload(_async_stop_local_mqtt)
    await client.async_start()


async def async_setup_entry(hass: HomeAssistant, entry: JackeryConfigEntry) -> bool:
    """Set up the Jackery SolarVault config entry, start its coordinator and optional transports, and forward platform setup.

    Performs authentication, constructs the coordinator, runs discovery and the
    initial refresh, and starts cloud MQTT plus an optional local MQTT listener
    and BLE transport. Cloud/transport failures do not block setup (AGENTS.md
    §3.3): cached discovery is used when the first HTTP refresh fails, and MQTT
    auth failures are deferred so local transports keep running. On successful
    setup the coordinator is stored on the entry's runtime state and platform
    setups and listeners are registered. If setup fails after the coordinator is
    created, the coordinator is shut down and the entry's runtime state is
    cleared before the error is re-raised.

    Returns:
        True if setup completed successfully.
    """  # noqa: E501
    _async_clean_legacy_entities(hass, entry)
    api = await _async_authenticate(hass, entry)

    interval_sec = DEFAULT_SCAN_INTERVAL_SEC
    coordinator = JackerySolarVaultCoordinator(
        hass, entry, api, timedelta(seconds=interval_sec)
    )
    _LOGGER.info("Jackery: coordinator polling interval set to %ss", interval_sec)

    try:  # noqa: PLW0717
        # Discovery must run first (MQTT subscriptions and the first refresh
        # both rely on the device list it produces). The HTTP first refresh and
        # the MQTT connect afterwards are independent and run in parallel to
        # cut the observed config-entry setup time roughly in half on slow
        # cloud connections. If any mandatory setup step fails, shut down the
        # partially initialized coordinator so MQTT and timer tasks cannot leak.
        await coordinator.async_load_statistics_backfill_state()
        await coordinator.async_discover()
        # Restore midnight-anchor snapshots for the local "today" deltas
        # before the first refresh so the very first update cycle can already
        # produce ``PAYLOAD_LOCAL_DAILY_ENERGY`` entries.
        await coordinator.async_load_local_daily_snapshots()
        # asyncio.gather(return_exceptions=True) widens the result element
        # type to ``T | BaseException``; the cast surfaces that contract for
        # mypy without changing runtime behaviour.
        refresh_result, mqtt_result = cast(
            "tuple[BaseException | None, BaseException | None]",
            await asyncio.gather(
                coordinator.async_config_entry_first_refresh(),
                coordinator.async_start_mqtt(),
                return_exceptions=True,
            ),
        )
        if isinstance(refresh_result, BaseException):
            # Cloud first refresh failed: fall back to cached discovery so the
            # local MQTT/BLE transports can still start (AGENTS.md §3.3).
            cached_snapshot = coordinator.cached_discovery_snapshot()
            if isinstance(refresh_result, UpdateFailed) and cached_snapshot:
                _LOGGER.warning(
                    "Jackery first HTTP refresh failed; loading cached discovery "
                    "payload so local MQTT/BLE transports can start: %s",
                    refresh_result,
                )
                coordinator.async_set_updated_data(cached_snapshot)
            else:
                raise refresh_result  # noqa: TRY301
        if isinstance(mqtt_result, ConfigEntryAuthFailed):
            # Broker-side MQTT credential/session conflicts must not block
            # cached discovery, local MQTT or BLE. HTTP auth failures are
            # surfaced earlier in _async_authenticate.
            coordinator._defer_background_auth_failure(mqtt_result)  # noqa: SLF001
        if isinstance(mqtt_result, BaseException):
            # Other MQTT failures (network, TLS handshake, broker outage) must
            # not block setup — push is an optional channel on top of polling.
            _LOGGER.warning(
                "Jackery MQTT push could not start during setup: %s", mqtt_result
            )
        entry.runtime_data = coordinator
        # Do not prune optional runtime sensors here: app/MQTT/Combine backed
        # properties can arrive after the first refresh and would otherwise be
        # removed incorrectly.

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        coordinator.async_start_statistics_imports()
        await coordinator.async_start_local_mqtt_listener()
        if _local_mqtt_client(hass, entry) is None:
            try:
                await _async_start_local_mqtt(hass, entry, coordinator)
            except BaseException as err:  # noqa: BLE001
                _LOGGER.warning(
                    "Jackery direct local MQTT client could not start during setup: %s",
                    err,
                )
        # Optional BLE listener (Phase 3a). Failures are absorbed inside
        # the coordinator: BLE is an opt-in diagnostic extra, never a
        # hard dependency of integration setup.
        await coordinator.async_start_ble_transport()
        # Push the local third-party MQTT bridge config to every device when the
        # user has enabled it in the options flow. This sends cmd=113 via BLE
        # first (so it works during a cloud outage) and lets the device push
        # live telemetry to the user's broker instead of emqx.jackeryapp.com.
        await coordinator.async_apply_local_mqtt_config_to_devices()
        # NOTE: add_update_listener is intentionally NOT used here.
        # It triggers async_reload which stops BLE + MQTT on every
        # options change, causing data loss and BLE/MQTT cycling.
        # Option changes are picked up by the coordinator's next
        # poll cycle instead.
    except Exception as err:
        with contextlib.suppress(Exception):
            await coordinator.async_shutdown()
        # HA convention: clear runtime_data on setup failure so the next retry
        # sees a clean slate. The typed alias narrows the attribute to
        # ``JackerySolarVaultCoordinator``; the None assignment is the
        # documented cleanup path and matches HA core integrations.
        if getattr(entry, "runtime_data", None) is coordinator:
            entry.runtime_data = cast("Any", None)
        # UpdateFailed surfaced from a mandatory step becomes ConfigEntryNotReady
        # so HA retries setup with backoff (60s cloud reconnect, AGENTS.md §1.2).
        if isinstance(err, UpdateFailed):
            raise ConfigEntryNotReady(str(err)) from err
        raise
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
    """  # noqa: E501
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
    """Determine whether ``uid`` is a legacy device head immediately followed by ``key_suffix``.

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
    """  # noqa: E501
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
    """  # noqa: E501
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

    If platform unload succeeds, shuts down the coordinator (if present) and
    clears the entry's runtime data to avoid retaining the coordinator. Teardown
    is performed only when platforms are successfully unloaded.

    Returns:
        True if platforms were unloaded and runtime teardown completed, False
        otherwise.
    """
    coordinator: JackerySolarVaultCoordinator | None = entry.runtime_data
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
    """Allow removal of a device from the config entry.

    Returns True to allow the user to remove the device from the UI. Cloud
    integrations generally allow removal; the coordinator will re-discover
    the device on the next poll if it still exists in the Jackery account.
    """
    return True
