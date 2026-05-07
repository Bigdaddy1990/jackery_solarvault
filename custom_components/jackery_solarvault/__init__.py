"""Jackery SolarVault integration."""

from datetime import timedelta
import logging
from pathlib import Path
import shutil

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers import config_validation as cv, entity_registry as er
from homeassistant.helpers.aiohttp_client import async_get_clientsession
import voluptuous as vol

from .api import JackeryApi, JackeryAuthError, JackeryError
from .const import (
    CALCULATED_POWER_SENSOR_SUFFIXES,
    CONF_CREATE_CALCULATED_POWER_SENSORS,
    CONF_CREATE_SAVINGS_DETAIL_SENSORS,
    CONF_CREATE_SMART_METER_DERIVED_SENSORS,
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
    SERVICE_DELETE_STORM_ALERT,
    SERVICE_FIELD_ALERT_ID,
    SERVICE_FIELD_DEVICE_ID,
    SERVICE_FIELD_NEW_NAME,
    SERVICE_FIELD_SYSTEM_ID,
    SERVICE_NON_EMPTY_TEXT_PATTERN,
    SERVICE_NUMERIC_ID_PATTERN,
    SERVICE_REFRESH_WEATHER_PLAN,
    SERVICE_RENAME_SYSTEM,
    SMART_METER_DERIVED_SENSOR_SUFFIXES,
    STALE_ENERGY_HELPER_PREFIX,
    STALE_HELPER_BATTERY_TOKENS,
    STALE_HELPER_CHARGE_TOKENS,
    STALE_HELPER_DISCHARGE_TOKENS,
    STALE_HELPER_VENDOR_TOKENS,
    STALE_NET_POWER_SUFFIX,
)
from .coordinator import JackerySolarVaultCoordinator

# Typed ConfigEntry alias — the runtime_data attribute is a
# JackerySolarVaultCoordinator. Per HA developer guide (2024.4+) this
# alias lets type-checkers see through ``entry.runtime_data`` to the
# concrete coordinator type without sprinkling cast/getattr around
# the integration. PEP 695 syntax requires Python 3.12+; HA 2025.x
# already requires Python 3.14.
type JackeryConfigEntry = ConfigEntry[JackerySolarVaultCoordinator]

_LOGGER = logging.getLogger(__name__)

RENAME_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_SYSTEM_ID): vol.All(
        cv.string, vol.Match(SERVICE_NUMERIC_ID_PATTERN)
    ),
    vol.Required(SERVICE_FIELD_NEW_NAME): vol.All(
        cv.string,
        vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN),
        vol.Length(max=64),
    ),
})
REFRESH_WEATHER_PLAN_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string, vol.Match(SERVICE_NUMERIC_ID_PATTERN)
    ),
})
DELETE_STORM_ALERT_SCHEMA = vol.Schema({
    vol.Required(SERVICE_FIELD_DEVICE_ID): vol.All(
        cv.string, vol.Match(SERVICE_NUMERIC_ID_PATTERN)
    ),
    vol.Required(SERVICE_FIELD_ALERT_ID): vol.All(
        cv.string, vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN)
    ),
})


BRAND_IMAGE_FILENAMES = (
    "icon.png",
    "icon@2x.png",
    "dark_icon.png",
    "dark_icon@2x.png",
    "logo.png",
    "logo@2x.png",
    "dark_logo.png",
    "dark_logo@2x.png",
)
BRAND_CACHE_INTEGRATION_DOMAIN = "jackery"


def _copy_cached_jackery_brand_images(source_dirs: tuple[str, ...]) -> list[str]:
    """Copy official cached Jackery brand PNGs into the custom integration brand folder.

    Home Assistant 2026.3+ serves custom integration brand images from
    custom_components/<domain>/brand/. The Jackery brand already exists in the
    server-side brands cache as integration domain ``jackery`` on affected HA
    systems, while this custom integration uses domain ``jackery_solarvault``.
    Copying the cached PNG files keeps the UI on HA's local brand source instead
    of shipping hand-made SVG stand-ins.
    """
    target_dir = Path(__file__).with_name("brand")
    copied: list[str] = []
    for raw_source_dir in source_dirs:
        source_dir = Path(raw_source_dir)
        if not source_dir.is_dir():
            continue
        target_dir.mkdir(exist_ok=True)
        for filename in BRAND_IMAGE_FILENAMES:
            source_file = source_dir / filename
            if not source_file.is_file():
                continue
            target_file = target_dir / filename
            if target_file.is_file():
                try:
                    same_size = source_file.stat().st_size == target_file.stat().st_size
                except OSError:
                    same_size = False
                if same_size:
                    continue
            shutil.copy2(source_file, target_file)
            copied.append(filename)
        break
    return copied


async def _async_ensure_cached_brand_images(hass: HomeAssistant) -> None:
    """Install cached Jackery brand PNGs without blocking the event loop."""
    source_dirs = (
        hass.config.path(
            ".cache", "brands", "integrations", BRAND_CACHE_INTEGRATION_DOMAIN
        ),
        f"/homeassistant/.cache/brands/integrations/{BRAND_CACHE_INTEGRATION_DOMAIN}",
        f"/config/.cache/brands/integrations/{BRAND_CACHE_INTEGRATION_DOMAIN}",
    )
    copied = await hass.async_add_executor_job(
        _copy_cached_jackery_brand_images, source_dirs
    )
    if copied:
        _LOGGER.info(
            "Jackery: copied cached brand image(s) into local integration brand folder: %s",
            ", ".join(copied),
        )


def _entry_bool_option(entry: ConfigEntry, key: str, default: bool) -> bool:
    """Return a boolean option while preserving older setup-stored values."""
    return bool(entry.options.get(key, entry.data.get(key, default)))


def _loaded_coordinators(hass: HomeAssistant) -> list[JackerySolarVaultCoordinator]:
    """Return runtime coordinators for loaded Jackery config entries."""
    coordinators: list[JackerySolarVaultCoordinator] = []
    for loaded_entry in hass.config_entries.async_loaded_entries(DOMAIN):
        coordinator = getattr(loaded_entry, "runtime_data", None)
        if isinstance(coordinator, JackerySolarVaultCoordinator):
            coordinators.append(coordinator)
    return coordinators


# This integration is config-entry-only — there is no YAML configuration
# surface. The `cv.config_entry_only_config_schema` helper documents
# that contract to hassfest and rejects any YAML the user might add by
# accident.
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    """Set up global Jackery SolarVault services."""
    await _async_ensure_cached_brand_images(hass)

    if not hass.services.has_service(DOMAIN, SERVICE_RENAME_SYSTEM):

        async def _handle_rename(call: ServiceCall) -> None:
            system_id = call.data[SERVICE_FIELD_SYSTEM_ID].strip()
            new_name = call.data[SERVICE_FIELD_NEW_NAME].strip()
            last_err: Exception | None = None
            for coord in _loaded_coordinators(hass):
                try:
                    await coord.api.async_set_system_name(system_id, new_name)
                    await coord.async_request_refresh()
                    return
                except JackeryError as err:
                    last_err = err
                    _LOGGER.debug(
                        "rename_system via %s failed: %s",
                        coord.entry.entry_id,
                        err,
                    )
                    continue
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="rename_system_failed",
                translation_placeholders={
                    "system_id": str(system_id),
                    "error": str(last_err or "no loaded Jackery entry"),
                },
            )

        hass.services.async_register(
            DOMAIN,
            SERVICE_RENAME_SYSTEM,
            _handle_rename,
            schema=RENAME_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_REFRESH_WEATHER_PLAN):

        async def _handle_refresh_weather_plan(call: ServiceCall) -> None:
            device_id = call.data[SERVICE_FIELD_DEVICE_ID].strip()
            last_err: Exception | None = None
            for coord in _loaded_coordinators(hass):
                try:
                    await coord.async_query_weather_plan(device_id)
                    return
                except (JackeryError, LookupError) as err:
                    last_err = err
                    _LOGGER.debug(
                        "refresh_weather_plan via %s failed: %s",
                        coord.entry.entry_id,
                        err,
                    )
                    continue
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="refresh_weather_plan_failed",
                translation_placeholders={
                    "device_id": str(device_id),
                    "error": str(last_err or "no loaded Jackery entry"),
                },
            )

        hass.services.async_register(
            DOMAIN,
            SERVICE_REFRESH_WEATHER_PLAN,
            _handle_refresh_weather_plan,
            schema=REFRESH_WEATHER_PLAN_SCHEMA,
        )

    if not hass.services.has_service(DOMAIN, SERVICE_DELETE_STORM_ALERT):

        async def _handle_delete_storm_alert(call: ServiceCall) -> None:
            device_id = call.data[SERVICE_FIELD_DEVICE_ID].strip()
            alert_id = call.data[SERVICE_FIELD_ALERT_ID].strip()
            last_err: Exception | None = None
            for coord in _loaded_coordinators(hass):
                try:
                    await coord.async_delete_storm_alert(device_id, alert_id)
                    return
                except (JackeryError, LookupError) as err:
                    last_err = err
                    _LOGGER.debug(
                        "delete_storm_alert via %s failed: %s",
                        coord.entry.entry_id,
                        err,
                    )
                    continue
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="delete_storm_alert_failed",
                translation_placeholders={
                    "device_id": str(device_id),
                    "alert_id": str(alert_id),
                    "error": str(last_err or "no loaded Jackery entry"),
                },
            )

        hass.services.async_register(
            DOMAIN,
            SERVICE_DELETE_STORM_ALERT,
            _handle_delete_storm_alert,
            schema=DELETE_STORM_ALERT_SCHEMA,
        )

    return True


async def async_setup_entry(hass: HomeAssistant, entry: JackeryConfigEntry) -> bool:
    """Set up Jackery SolarVault from a config entry."""
    # Keep entity-registry cleanup explicit and setup-local. This avoids hidden
    # entry-version side effects while still removing entities that are no
    # longer part of the documented app/HTTP/MQTT data model.
    await _async_remove_stale_energy_helpers(hass)
    await _async_remove_removed_sensors(hass, entry)
    if not _entry_bool_option(
        entry,
        CONF_CREATE_SMART_METER_DERIVED_SENSORS,
        DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS,
    ):
        await _async_remove_smart_meter_derived_sensors(hass, entry)
    if not _entry_bool_option(
        entry,
        CONF_CREATE_CALCULATED_POWER_SENSORS,
        DEFAULT_CREATE_CALCULATED_POWER_SENSORS,
    ):
        await _async_remove_calculated_power_sensors(hass, entry)
    if not _entry_bool_option(
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

    await coordinator.async_discover()
    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_start_mqtt()

    entry.runtime_data = coordinator
    # Do not prune optional runtime sensors here: app/MQTT/Combine backed
    # properties can arrive after the first refresh and would otherwise be
    # removed incorrectly.

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    coordinator.async_start_periodic_refresh()

    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def _async_remove_stale_energy_helpers(hass: HomeAssistant) -> None:
    """Remove known stale Energy helper sensors that were created without a unit."""
    registry = er.async_get(hass)
    to_remove: list[str] = []
    for ent in registry.entities.values():
        entity_id = ent.entity_id or ""
        if not entity_id.startswith(STALE_ENERGY_HELPER_PREFIX):
            continue
        if not entity_id.endswith(STALE_NET_POWER_SUFFIX):
            continue
        lowered = entity_id.lower()
        # Any stale helper referencing SolarVault/Jackery net-power entities
        # should be removed when it no longer has a valid unit.
        if any(token in lowered for token in STALE_HELPER_VENDOR_TOKENS):
            state = hass.states.get(entity_id)
            unit = (
                None if state is None else state.attributes.get("unit_of_measurement")
            )
            if unit in (None, ""):
                to_remove.append(entity_id)
                continue
        # Broken helper pattern seen in user logs:
        # energy_<battery_discharge>_<battery_charge>_net_power
        # Keep this locale-agnostic (DE/EN), because helper IDs depend on UI
        # language at creation time.
        if not any(token in lowered for token in STALE_HELPER_BATTERY_TOKENS):
            continue
        if not any(token in lowered for token in STALE_HELPER_CHARGE_TOKENS):
            continue
        if not any(token in lowered for token in STALE_HELPER_DISCHARGE_TOKENS):
            continue
        state = hass.states.get(entity_id)
        unit = None if state is None else state.attributes.get("unit_of_measurement")
        if unit in (None, ""):
            to_remove.append(entity_id)

    for entity_id in to_remove:
        _LOGGER.info(
            "Removing stale Energy helper without unit: %s (please recreate with Jackery battery_net_power)",
            entity_id,
        )
        registry.async_remove(entity_id)


async def _async_remove_smart_meter_derived_sensors(
    hass: HomeAssistant,
    entry: ConfigEntry,
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
    entry: ConfigEntry,
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
    entry: ConfigEntry,
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
    entry: ConfigEntry,
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
    entry: ConfigEntry,
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
    if isinstance(coordinator, JackerySolarVaultCoordinator):
        await coordinator.async_shutdown()

    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    # Note: entry.runtime_data is reset by HA on unload; explicit clear
    # only happens on success to avoid leaking the coordinator if unload
    # rolls back.
    if unload_ok:
        entry.runtime_data = None  # type: ignore[assignment]
    return unload_ok
