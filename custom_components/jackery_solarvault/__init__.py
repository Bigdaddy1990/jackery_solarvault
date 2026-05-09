"""Jackery SolarVault integration."""

import asyncio
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
from .brand import _async_ensure_cached_brand_images
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
    await _async_ensure_cached_brand_images(hass)
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: JackeryConfigEntry) -> bool:
    """Set up Jackery SolarVault from a config entry."""
    # Keep entity-registry cleanup explicit and setup-local. This avoids hidden
    # entry-version side effects while still removing entities that are no
    # longer part of the documented app/HTTP/MQTT data model.
    await _async_remove_removed_sensors(hass, entry)
    if not config_entry_bool_option(
        entry,
        CONF_CREATE_SMART_METER_DERIVED_SENSORS,
        DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
    ):
        await _async_remove_smart_meter_derived_sensors(hass, entry)
    if not config_entry_bool_option(
        entry,
        CONF_CREATE_CALCULATED_POWER_SENSORS,
        DEFAULT_CREATE_CALCULATED_POWER_SENSORS,
    ):
        await _async_remove_calculated_power_sensors(hass, entry)
    if not config_entry_bool_option(
        entry,
        CONF_CREATE_SAVINGS_DETAIL_SENSORS,
        DEFAULT_CREATE_SAVINGS_DETAIL_SENSORS,
    ):
        await _async_remove_savings_detail_sensors(hass, entry)
    await _async_remove_duplicate_binary_sensors(hass, entry)
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
        # Bad credentials -> trigger re-auth flow so user can update password
        # without removing/re-adding the integration.
        raise ConfigEntryAuthFailed(
            f"Jackery login rejected the credentials: {err}"
        ) from err
    except JackeryError as err:
        # Network hickup, server overload, transient error.
        # HA will retry async_setup_entry in 30s instead of marking it failed.
        raise ConfigEntryNotReady(
            f"Cannot reach Jackery cloud right now: {err}"
        ) from err

    interval_sec = DEFAULT_SCAN_INTERVAL_SEC

    coordinator = JackerySolarVaultCoordinator(
        hass, entry, api, timedelta(seconds=interval_sec)
    )
    _LOGGER.info("Jackery: fixed polling interval set to %ss", interval_sec)

    try:
        # Discovery must run first (MQTT subscriptions and the first refresh
        # both rely on the device list it produces). The HTTP first refresh and
        # the MQTT connect afterwards are independent and run in parallel to
        # cut the observed config-entry setup time roughly in half on slow
        # cloud connections. If any mandatory setup step fails, shut down the
        # partially initialized coordinator so MQTT and timer tasks cannot leak.
        await coordinator.async_discover()
        setup_results = cast(
            list[None | BaseException],
            await asyncio.gather(
                coordinator.async_config_entry_first_refresh(),
                coordinator.async_start_mqtt(),
                return_exceptions=True,
            ),
        )
        refresh_result, mqtt_result = setup_results
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
        coordinator.async_start_periodic_refresh()

        entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    except BaseException:
        with contextlib.suppress(BaseException):
            await coordinator.async_shutdown()
        if entry.runtime_data is coordinator:
            entry.runtime_data = None  # type: ignore[assignment]
        raise
    return True


async def _async_remove_smart_meter_derived_sensors(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> None:
    """Remove optional calculated smart-meter sensors when disabled."""
    registry = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        uid = ent.unique_id or ""
        if ent.domain == "sensor" and any(
            uid.endswith(suffix) for suffix in SMART_METER_DERIVED_SENSOR_SUFFIXES
        ):
            _LOGGER.info(
                "Removing disabled calculated smart-meter sensor %s (%s)",
                ent.entity_id,
                ent.unique_id,
            )
            registry.async_remove(ent.entity_id)


async def _async_remove_calculated_power_sensors(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> None:
    """Remove optional calculated net-power sensors when disabled."""
    registry = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        uid = ent.unique_id or ""
        if ent.domain == "sensor" and any(
            uid.endswith(suffix) for suffix in CALCULATED_POWER_SENSOR_SUFFIXES
        ):
            _LOGGER.info(
                "Removing disabled calculated power sensor %s (%s)",
                ent.entity_id,
                ent.unique_id,
            )
            registry.async_remove(ent.entity_id)


async def _async_remove_savings_detail_sensors(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> None:
    """Remove optional savings-calculation detail sensors when disabled."""
    registry = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        uid = ent.unique_id or ""
        if ent.domain == "sensor" and any(
            uid.endswith(suffix) for suffix in SAVINGS_DETAIL_SENSOR_SUFFIXES
        ):
            _LOGGER.info(
                "Removing disabled savings detail sensor %s (%s)",
                ent.entity_id,
                ent.unique_id,
            )
            registry.async_remove(ent.entity_id)


async def _async_remove_duplicate_binary_sensors(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> None:
    """Remove read-only binary sensors duplicated by config switches."""
    registry = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        uid = ent.unique_id or ""
        if ent.domain == "binary_sensor" and any(
            uid.endswith(suffix) for suffix in DUPLICATE_BINARY_SENSOR_SUFFIXES
        ):
            _LOGGER.info(
                "Removing duplicate binary sensor %s (%s)",
                ent.entity_id,
                ent.unique_id,
            )
            registry.async_remove(ent.entity_id)


# -----------------------------------------------------------------------------
# Registry cleanup helpers
# -----------------------------------------------------------------------------
async def _async_remove_removed_sensors(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
) -> None:
    """Remove sensor entities that have been removed from the integration.

    Drop stale entity-registry entries whose source is no longer part of the
    documented app/HTTP/MQTT data model.
    """
    registry = er.async_get(hass)
    for ent in er.async_entries_for_config_entry(registry, entry.entry_id):
        uid = ent.unique_id or ""
        # Consider only sensors. Unique IDs are of the form
        # <device_id>_<sensor_key>, so we can match suffixes.
        if ent.domain != "sensor":
            continue
        if any(uid.endswith(suffix) for suffix in REMOVED_SENSOR_SUFFIXES):
            _LOGGER.info(
                "Removing removed Jackery sensor %s (%s)",
                ent.entity_id,
                ent.unique_id,
            )
            registry.async_remove(ent.entity_id)
        # CT period sensors are not exposed by the active app data model.
        elif any(uid.endswith(suffix) for suffix in CT_PERIOD_SENSOR_SUFFIXES):
            _LOGGER.info(
                "Removing removed CT period sensor %s (%s)",
                ent.entity_id,
                ent.unique_id,
            )
            registry.async_remove(ent.entity_id)


async def _async_update_listener(
    hass: HomeAssistant, entry: JackeryConfigEntry
) -> None:
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
    entry.runtime_data = None  # type: ignore[assignment]
    return True
