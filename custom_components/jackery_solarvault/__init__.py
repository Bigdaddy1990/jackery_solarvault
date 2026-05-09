"""Jackery SolarVault integration."""

import asyncio
from collections.abc import Iterable
import contextlib
from datetime import timedelta
import logging
from typing import cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import JackeryApi, JackeryAuthError, JackeryError
from .const import (
    CALCULATED_POWER_SENSOR_SUFFIXES,
    CONF_CREATE_CALCULATED_POWER_SENSORS,
    CONF_CREATE_SAVINGS_DETAIL_SENSORS,
    CONF_CREATE_SMART_METER_DERIVED_SENSORS,
    CONF_MQTT_MAC_ID,
    CONF_REGION_CODE,
    CT_PERIOD_SENSOR_SUFFIXES,
    DEFAULT_CREATE_CALCULATED_POWER_SENSORS,
    DEFAULT_CREATE_SAVINGS_DETAIL_SENSORS,
    DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
    DEFAULT_SCAN_INTERVAL_SEC,
    DOMAIN,
    DUPLICATE_BINARY_SENSOR_SUFFIXES,
    PLATFORMS,
    REMOVED_SENSOR_SUFFIXES,
    SAVINGS_DETAIL_SENSOR_SUFFIXES,
    SMART_METER_DERIVED_SENSOR_SUFFIXES,
)
from .coordinator import JackerySolarVaultCoordinator
from .services import async_setup_services
from .util import config_entry_bool_option

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
    """Build the API client and run the initial login.

    Bad credentials raise ``ConfigEntryAuthFailed`` so HA routes the entry
    into the re-auth flow without removing it. Transient cloud errors raise
    ``ConfigEntryNotReady`` so HA retries setup after the configured backoff
    instead of marking the entry as failed.
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


async def async_setup_entry(hass: HomeAssistant, entry: JackeryConfigEntry) -> bool:
    """Set up Jackery SolarVault from a config entry."""
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
        await coordinator.async_discover()
        # asyncio.gather(return_exceptions=True) widens the result element
        # type to ``T | BaseException``; the cast surfaces that contract for
        # mypy without changing runtime behaviour.
        refresh_result, mqtt_result = cast(
            tuple[None | BaseException, None | BaseException],
            await asyncio.gather(
                coordinator.async_config_entry_first_refresh(),
                coordinator.async_start_mqtt(),
                return_exceptions=True,
            ),
        )
        if isinstance(refresh_result, BaseException):
            raise refresh_result
        if isinstance(mqtt_result, BaseException):
            # MQTT failure must not block setup — push is an optional channel
            # on top of polling. Log and continue.
            _LOGGER.warning(
                "Jackery MQTT push could not start during setup: %s", mqtt_result
            )

        entry.runtime_data = coordinator
        # Do not prune optional runtime sensors here: app/MQTT/Combine backed
        # properties can arrive after the first refresh and would otherwise be
        # removed incorrectly.

        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    except BaseException:
        with contextlib.suppress(BaseException):
            await coordinator.async_shutdown()
        if entry.runtime_data is coordinator:
            entry.runtime_data = None
        raise
    return True


# -----------------------------------------------------------------------------
# Registry cleanup helpers
# -----------------------------------------------------------------------------


def _async_remove_entities_with_suffixes(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    *,
    domain: str,
    suffixes: Iterable[str],
    log_label: str,
) -> None:
    """Remove entity-registry entries whose unique_id ends with any suffix.

    Unique IDs for this integration follow ``<device_id>_<key_suffix>`` per
    docs/UNIQUE_ID_CONTRACT.md, so suffix matching is the right way to drop
    legacy or option-disabled entities without scanning HA-wide registry
    entries owned by other integrations.

    Synchronous because the entity registry is already in-memory; the helper
    only schedules removals against it. Setup-local cleanup deliberately
    avoids hidden entry-version side effects.
    """
    suffix_tuple = tuple(suffixes)
    if not suffix_tuple:
        return
    registry = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        if ent.domain != domain:
            continue
        uid = ent.unique_id or ""
        if any(uid.endswith(suffix) for suffix in suffix_tuple):
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
    """Unload a config entry."""
    coordinator: JackerySolarVaultCoordinator | None = entry.runtime_data
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    # Keep the successful teardown explicitly gated by unload_ok so future
    # changes cannot stop the coordinator while HA still has loaded platforms.
    if not unload_ok:
        return False
    if isinstance(coordinator, JackerySolarVaultCoordinator):
        await coordinator.async_shutdown()
    entry.runtime_data = None
    return True
