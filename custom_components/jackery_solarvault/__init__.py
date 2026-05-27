"""Jackery SolarVault integration."""

import asyncio
from collections.abc import Iterable
import contextlib
from datetime import timedelta
import logging
import re
from typing import cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .client import JackeryApi, JackeryAuthError, JackeryError
from .client.local_mqtt import JackeryLocalMqttClient
from .const import (
    CALCULATED_POWER_SENSOR_SUFFIXES,
    CONF_CREATE_CALCULATED_POWER_SENSORS,
    CONF_CREATE_SAVINGS_DETAIL_SENSORS,
    CONF_CREATE_SMART_METER_DERIVED_SENSORS,
    CONF_MQTT_MAC_ID,
    CONF_REGION_CODE,
    CONF_THIRD_PARTY_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_PORT,
    CONF_THIRD_PARTY_MQTT_USERNAME,
    CT_PERIOD_SENSOR_SUFFIXES,
    DEFAULT_CREATE_CALCULATED_POWER_SENSORS,
    DEFAULT_CREATE_SAVINGS_DETAIL_SENSORS,
    DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
    DEFAULT_SCAN_INTERVAL_SEC,
    DEFAULT_THIRD_PARTY_MQTT_ENABLE,
    DEFAULT_THIRD_PARTY_MQTT_PASSWORD,
    DEFAULT_THIRD_PARTY_MQTT_PORT,
    DEFAULT_THIRD_PARTY_MQTT_USERNAME,
    DOMAIN,
    DUPLICATE_BINARY_SENSOR_SUFFIXES,
    PLATFORMS,
    REMOVED_SENSOR_SUFFIXES,
    SAVINGS_DETAIL_SENSOR_SUFFIXES,
    SMART_METER_DERIVED_SENSOR_SUFFIXES,
    STALE_ENERGY_HELPER_PREFIX,
    STALE_HELPER_VENDOR_TOKENS,
    STALE_NET_POWER_SUFFIX,
)
from .coordinator import JackerySolarVaultCoordinator
from .services import async_setup_services
from .util import (
    config_entry_bool_option,
    config_entry_int_option,
    config_entry_str_option,
)

# Typed ConfigEntry alias — the runtime_data attribute is a
# JackerySolarVaultCoordinator. Per HA developer guide (2024.4+) this
# alias lets type-checkers see through ``entry.runtime_data`` to the
# concrete coordinator type without sprinkling cast/getattr around
# the integration. PEP 695 syntax requires Python 3.12+; HA 2025.x
# already requires Python 3.14.
type JackeryConfigEntry = ConfigEntry[JackerySolarVaultCoordinator]

_LOGGER = logging.getLogger(__name__)


# This integration is config-entry-only — there is no YAML configuration
# surface. The `cv.config_entry_only_config_schema` helper documents
# that contract to hassfest and rejects any YAML the user might add by
# accident.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up global Jackery SolarVault services."""
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
    """
    Create a JackeryApi client from the config entry and perform the initial authentication.
    
    Returns:
        JackeryApi: Authenticated API client ready for use.
    
    Raises:
        ConfigEntryAuthFailed: If the provided credentials are rejected (triggers re-auth flow).
        ConfigEntryNotReady: If the Jackery cloud cannot be reached (setup should be retried later).
    """
    session = async_get_clientsession(hass)
    api = JackeryApi(
        session=session,
        account=entry.data[CONF_USERNAME],
        password=entry.data[CONF_PASSWORD],
        mqtt_mac_id=entry.data.get(CONF_MQTT_MAC_ID),
        region_code=entry.data.get(CONF_REGION_CODE),
    )
    try:
        await api.async_login()
    except JackeryAuthError as err:
        raise ConfigEntryAuthFailed(
            f"Jackery login rejected the credentials: {err}"
        ) from err
    except JackeryError as err:
        raise ConfigEntryNotReady(
            f"Cannot reach Jackery cloud right now: {err}"
        ) from err
    return api


_LOCAL_MQTT_RUNTIME_KEY = "local_mqtt_client"


def _local_mqtt_client(
    hass: HomeAssistant, entry: JackeryConfigEntry
) -> JackeryLocalMqttClient | None:
    """
    Get the stored local MQTT client for the given config entry, if present.
    
    Returns:
        JackeryLocalMqttClient | None: The stored `JackeryLocalMqttClient` instance for the entry, or `None` if no client is stored or the stored value is not a `JackeryLocalMqttClient`.
    """
    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
    if not isinstance(bucket, dict):
        return None
    client = bucket.get(_LOCAL_MQTT_RUNTIME_KEY)
    return client if isinstance(client, JackeryLocalMqttClient) else None


async def _async_start_local_mqtt(
    hass: HomeAssistant, entry: JackeryConfigEntry
) -> None:
    """
    Start an optional local MQTT listener for the given config entry.
    
    If the third-party MQTT bridge option is disabled or the configured host is empty, this function returns without action. When started, the listener is created and stored in hass.data[DOMAIN][entry.entry_id][_LOCAL_MQTT_RUNTIME_KEY], and an unload callback is registered to stop the client and remove that reference. Exceptions raised while stopping the client are suppressed. Failures to start or run the local MQTT client do not block overall integration setup.
    """
    if not config_entry_bool_option(
        entry, CONF_THIRD_PARTY_MQTT_ENABLE, DEFAULT_THIRD_PARTY_MQTT_ENABLE
    ):
        return
    host = config_entry_str_option(entry, CONF_THIRD_PARTY_MQTT_IP, "").strip()
    if not host:
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
    client = JackeryLocalMqttClient(
        hass,
        host=host,
        port=port,
        username=username or None,
        password=password or None,
        client_id=f"ha-jackery-{entry.entry_id[:8]}",
    )
    bucket = hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})
    bucket[_LOCAL_MQTT_RUNTIME_KEY] = client

    async def _async_stop_local_mqtt() -> None:
        """
        Stop the local MQTT client and remove its runtime reference from hass.data if it matches the stored instance.
        
        Stops the provided local MQTT client while suppressing any exceptions raised during shutdown. If the client instance is still stored under hass.data[DOMAIN][entry.entry_id][_LOCAL_MQTT_RUNTIME_KEY], that reference is removed.
        """
        with contextlib.suppress(Exception):
            await client.async_stop()
        stashed = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        if isinstance(stashed, dict) and stashed.get(_LOCAL_MQTT_RUNTIME_KEY) is client:
            stashed.pop(_LOCAL_MQTT_RUNTIME_KEY, None)

    entry.async_on_unload(_async_stop_local_mqtt)
    await client.async_start()


async def async_setup_entry(hass: HomeAssistant, entry: JackeryConfigEntry) -> bool:
    """
    Initialize the integration for a Jackery SolarVault config entry.
    
    Performs authentication, constructs and starts the coordinator (including optional cloud and local MQTT listeners and BLE transport), forwards platform setup, registers the entry update/unload handlers, and stores runtime coordinator state on success.
    
    Returns:
        True if setup completed successfully.
    """
    _async_clean_legacy_entities(hass, entry)
    api = await _async_authenticate(hass, entry)

    interval_sec = DEFAULT_SCAN_INTERVAL_SEC
    coordinator = JackerySolarVaultCoordinator(
        hass, entry, api, timedelta(seconds=interval_sec)
    )
    _LOGGER.info("Jackery: coordinator polling interval set to %ss", interval_sec)

    try:
        # Discovery must run first (MQTT subscriptions and the first refresh
        # both rely on the device list it produces). The HTTP first refresh and
        # the MQTT connect afterwards are independent and run in parallel to
        # cut the observed config-entry setup time roughly in half on slow
        # cloud connections. If any mandatory setup step fails, shut down the
        # partially initialized coordinator so MQTT and timer tasks cannot leak.
        await coordinator.async_load_statistics_backfill_state()
        await coordinator.async_discover()
        # asyncio.gather(return_exceptions=True) widens the result element
        # type to ``T | BaseException``; the cast surfaces that contract for
        # mypy without changing runtime behaviour.
        refresh_result, mqtt_result, local_mqtt_result = cast(
            tuple[
                None | BaseException,
                None | BaseException,
                None | BaseException,
            ],
            await asyncio.gather(
                coordinator.async_config_entry_first_refresh(),
                coordinator.async_start_mqtt(),
                return_exceptions=True,
            await _async_push_third_party_mqtt_config(coordinator, host, port, username, password)
            ),
        )
        if isinstance(refresh_result, BaseException):
            raise refresh_result
        if isinstance(mqtt_result, ConfigEntryAuthFailed):
            # Broker explicitly rejected MQTT credentials. HTTP login may have
            # succeeded, but the user must update credentials regardless —
            # surface this so HA opens the reauth UI.
            raise mqtt_result
        if isinstance(mqtt_result, BaseException):
            # Other MQTT failures (network, TLS handshake, broker outage) must
            # not block setup — push is an optional channel on top of polling.
            _LOGGER.warning(
                "Jackery MQTT push could not start during setup: %s", mqtt_result
            )
        if isinstance(local_mqtt_result, BaseException):
            # Local broker outage / wrong credentials must not block setup.
            _LOGGER.warning(
                "Jackery local MQTT listener could not start during setup: %s",
                local_mqtt_result,
            )

        entry.runtime_data = coordinator
        # Do not prune optional runtime sensors here: app/MQTT/Combine backed
        # properties can arrive after the first refresh and would otherwise be
        # removed incorrectly.

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        coordinator.async_start_statistics_imports()
        # Optional BLE listener (Phase 3a). Failures are absorbed inside
        # the coordinator: BLE is an opt-in diagnostic extra, never a
        # hard dependency of integration setup.
        await coordinator.async_start_ble_transport()
        entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    except Exception:
        with contextlib.suppress(Exception):
            await coordinator.async_shutdown()
        if entry.runtime_data is coordinator:
            # HA convention: clear runtime_data on setup failure so the next
            # retry sees a clean slate. The typed alias narrows the attribute
            # to ``JackerySolarVaultCoordinator``; the None assignment here
            # is the documented cleanup path and matches HA core integrations.
            entry.runtime_data = None  # type: ignore[assignment]
        raise
    return True


# -----------------------------------------------------------------------------
# Registry cleanup helpers
# -----------------------------------------------------------------------------


def _async_remove_stale_energy_helpers(hass: HomeAssistant) -> None:
    """
    Remove stale Energy helper sensors that were created without a unit.
    
    Scans the entity registry for entities whose IDs start with the stale helper prefix
    and end with the net-power suffix, checks the current state for a missing or empty
    `unit_of_measurement`, and removes those entities only when their entity ID contains
    one of the known vendor tokens indicating they belong to this integration. Each
    removal is logged with an informational message.
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
        if unit not in (None, ""):
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
    """
    Determine whether a unique ID has a legacy device head immediately followed by the specified suffix.
    
    The function verifies that `uid` ends with `key_suffix` and that the portion before the suffix matches the legacy unique-id head shape (`<device_id>` or `<device_id>_battery_pack_<index>`). This prevents accidental matches where a current key contains a legacy key as its tail.
    
    Returns:
        `True` if `uid` is exactly a legacy head followed by `key_suffix`, `False` otherwise.
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
    """
    Remove entities owned by the given config entry whose registry unique IDs end with any of the provided suffixes.
    
    Matching is anchored using the legacy UID shape so suffixes only match intended legacy keys and do not accidentally match other unique IDs.
    
    Parameters:
        hass (HomeAssistant): Home Assistant instance.
        entry (JackeryConfigEntry): Config entry whose entities may be removed.
        domain (str): Entity domain to restrict removals (e.g., "sensor", "binary_sensor").
        suffixes (Iterable[str]): Suffix strings to match against entity unique IDs.
        log_label (str): Human-readable label used in removal log messages.
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
    """Reload the entry when the user toggles options.

    The optional sensor toggles change which platform entities exist, so a
    full reload is the simplest way to apply them. ``add_update_listener``
    in ``async_setup_entry`` wires this listener and removes it on unload.
    """
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: JackeryConfigEntry) -> bool:
    """
    Unload the config entry by unloading its platforms, shutting down the coordinator if active, and clearing the entry's runtime data.
    
    Returns:
        bool: True if platforms were unloaded and teardown completed, False if platform unload aborted.
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
    entry.runtime_data = None  # type: ignore[assignment]
    return True
