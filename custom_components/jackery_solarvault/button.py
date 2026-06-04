"""Button platform for Jackery SolarVault."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import JackeryConfigEntry
from .const import (
    ACTION_ID_GET_DEVICE_OTA_VERSION,
    ACTION_ID_GET_TIME_ZONE,
    ACTION_ID_QUERY_COMBINE_DATA,
    ACTION_ID_QUERY_DEVICE_PROPERTY,
    ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
    ACTION_ID_QUERY_WIFI_CONFIG,
    ACTION_ID_READ_WIFI_LIST,
    ACTION_ID_SEND_TIME_ZONE,
    ACTION_ID_SUBDEVICE_3014,
    ACTION_ID_SUBDEVICE_3031,
    ACTION_ID_SUBDEVICE_3032,
    ACTION_ID_SUBDEVICE_3033,
    ACTION_ID_SUBDEVICE_3037,
    ACTION_ID_SYNC_MQTT_CONNECT_INFO,
    DOMAIN,
    FIELD_ALERT_ID,
    FIELD_CMD,
    FIELD_DEVICE_SN,
    FIELD_DEV_SN,
    FIELD_DEV_TYPE,
    FIELD_END_TS,
    FIELD_MANUAL,
    FIELD_MESSAGE_TYPE,
    FIELD_REBOOT,
    FIELD_SN,
    FIELD_START_TS,
    FIELD_STATUS,
    FIELD_STORM,
    MQTT_CMD_GET_DEVICE_OTA_VERSION,
    MQTT_CMD_GET_TIME_ZONE,
    MQTT_CMD_QUERY_COMBINE_DATA,
    MQTT_CMD_QUERY_DEVICE_PROPERTY,
    MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
    MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG,
    MQTT_CMD_QUERY_WIFI_CONFIG,
    MQTT_CMD_READ_WIFI_LIST,
    MQTT_CMD_SEND_TIME_ZONE,
    MQTT_CMD_SYNC_MQTT_CONNECT_INFO,
    MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
    MQTT_MESSAGE_QUERY_COMBINE_DATA,
    MQTT_MESSAGE_QUERY_DEVICE_PROPERTY,
    MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
    MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG,
    MQTT_MESSAGE_QUERY_WIFI_CONFIG,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_WEATHER_PLAN,
    SUBDEVICE_DEV_TYPE_BATTERY_PACK,
    SUBDEVICE_DEV_TYPE_COMBO,
    SUBDEVICE_DEV_TYPE_CT,
    SUBDEVICE_DEV_TYPE_METER_HEAD,
    SUBDEVICE_DEV_TYPE_SOCKET,
    TIMER_TASK_TYPE_CUSTOM_MODE,
    TIMER_TASK_TYPE_SMART_PLUG,
    TIMER_TASK_TYPE_TIME_ELEC,
)
from .coordinator import JackerySolarVaultCoordinator
from .entity import JackeryEntity
from .util import append_unique_entity, coordinator_entity_signature, sorted_smart_plugs

# Limit concurrent control-write/update calls. This is a setter platform:
# writes go to the cloud and to MQTT. Serializing keeps the queue depth on
# the broker bounded and prevents reordering of `DevicePropertyChange`
# commands per HA dev guidance for write-heavy platforms.
PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)


QueryButtonAction = Callable[[JackerySolarVaultCoordinator, str], Awaitable[None]]


@dataclass(frozen=True, kw_only=True)
class JackeryQueryButtonDescription:
    """Metadata for a documented app read/query command."""

    key: str
    translation_key: str
    icon: str
    action: QueryButtonAction
    message_type: str
    action_id: int
    cmd: int
    dev_type: int | None = None


async def _query_system_info(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    await coordinator.async_query_system_info(device_id)


async def _query_device_info(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    await coordinator.async_query_device_info(device_id)


async def _query_wifi_list(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    await coordinator.async_query_wifi_list(device_id)


async def _get_time_zone(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    await coordinator.async_get_time_zone(device_id)


async def _send_time_zone(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    await coordinator.async_send_time_zone(device_id)


async def _sync_mqtt_connect_info(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    await coordinator.async_sync_mqtt_connect_info(device_id)


async def _query_device_ota_version(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    await coordinator.async_query_device_ota_version(device_id)


async def _query_third_party_mqtt_config(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    await coordinator.async_query_third_party_mqtt_config(device_id)


async def _query_wifi_config(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    await coordinator.async_query_wifi_config(device_id)


async def _query_battery_packs(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    await coordinator.async_query_battery_packs(device_id)


async def _query_smart_meter(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    await coordinator.async_query_smart_meter(device_id)


async def _query_meter_heads(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    await coordinator.async_query_meter_heads(device_id)


async def _query_smart_plugs(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    await coordinator.async_query_smart_plugs(device_id)


async def _query_subdevice_combo(
    coordinator: JackerySolarVaultCoordinator, device_id: str
) -> None:
    await coordinator.async_query_subdevice_combo(device_id)


QUERY_BUTTON_DESCRIPTIONS: tuple[JackeryQueryButtonDescription, ...] = (
    JackeryQueryButtonDescription(
        key="refresh_system_info",
        translation_key="refresh_system_info",
        icon="mdi:home-lightning-bolt",
        action=_query_system_info,
        message_type=MQTT_MESSAGE_QUERY_COMBINE_DATA,
        action_id=ACTION_ID_QUERY_COMBINE_DATA,
        cmd=MQTT_CMD_QUERY_COMBINE_DATA,
    ),
    JackeryQueryButtonDescription(
        key="refresh_device_info",
        translation_key="refresh_device_info",
        icon="mdi:database-refresh",
        action=_query_device_info,
        message_type=MQTT_MESSAGE_QUERY_DEVICE_PROPERTY,
        action_id=ACTION_ID_QUERY_DEVICE_PROPERTY,
        cmd=MQTT_CMD_QUERY_DEVICE_PROPERTY,
    ),
    JackeryQueryButtonDescription(
        key="refresh_wifi_list",
        translation_key="refresh_wifi_list",
        icon="mdi:wifi-refresh",
        action=_query_wifi_list,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_READ_WIFI_LIST,
        cmd=MQTT_CMD_READ_WIFI_LIST,
    ),
    JackeryQueryButtonDescription(
        key="refresh_time_zone",
        translation_key="refresh_time_zone",
        icon="mdi:map-clock",
        action=_get_time_zone,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_GET_TIME_ZONE,
        cmd=MQTT_CMD_GET_TIME_ZONE,
    ),
    JackeryQueryButtonDescription(
        key="sync_time_zone",
        translation_key="sync_time_zone",
        icon="mdi:clock-check",
        action=_send_time_zone,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_SEND_TIME_ZONE,
        cmd=MQTT_CMD_SEND_TIME_ZONE,
    ),
    JackeryQueryButtonDescription(
        key="sync_cloud_mqtt_info",
        translation_key="sync_cloud_mqtt_info",
        icon="mdi:cloud-sync",
        action=_sync_mqtt_connect_info,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_SYNC_MQTT_CONNECT_INFO,
        cmd=MQTT_CMD_SYNC_MQTT_CONNECT_INFO,
    ),
    JackeryQueryButtonDescription(
        key="refresh_device_ota_version",
        translation_key="refresh_device_ota_version",
        icon="mdi:update",
        action=_query_device_ota_version,
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_GET_DEVICE_OTA_VERSION,
        cmd=MQTT_CMD_GET_DEVICE_OTA_VERSION,
    ),
    JackeryQueryButtonDescription(
        key="refresh_third_party_mqtt_config",
        translation_key="refresh_third_party_mqtt_config",
        icon="mdi:mqtt",
        action=_query_third_party_mqtt_config,
        message_type=MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG,
        action_id=ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
        cmd=MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG,
    ),
    JackeryQueryButtonDescription(
        key="refresh_wifi_config",
        translation_key="refresh_wifi_config",
        icon="mdi:wifi-cog",
        action=_query_wifi_config,
        message_type=MQTT_MESSAGE_QUERY_WIFI_CONFIG,
        action_id=ACTION_ID_QUERY_WIFI_CONFIG,
        cmd=MQTT_CMD_QUERY_WIFI_CONFIG,
    ),
    JackeryQueryButtonDescription(
        key="refresh_battery_packs",
        translation_key="refresh_battery_packs",
        icon="mdi:battery-sync",
        action=_query_battery_packs,
        message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
        action_id=ACTION_ID_SUBDEVICE_3014,
        cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
        dev_type=SUBDEVICE_DEV_TYPE_BATTERY_PACK,
    ),
    JackeryQueryButtonDescription(
        key="refresh_smart_meter",
        translation_key="refresh_smart_meter",
        icon="mdi:meter-electric",
        action=_query_smart_meter,
        message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
        action_id=ACTION_ID_SUBDEVICE_3031,
        cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
        dev_type=SUBDEVICE_DEV_TYPE_CT,
    ),
    JackeryQueryButtonDescription(
        key="refresh_meter_heads",
        translation_key="refresh_meter_heads",
        icon="mdi:meter-electric-outline",
        action=_query_meter_heads,
        message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
        action_id=ACTION_ID_SUBDEVICE_3033,
        cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
        dev_type=SUBDEVICE_DEV_TYPE_METER_HEAD,
    ),
    JackeryQueryButtonDescription(
        key="refresh_smart_plugs",
        translation_key="refresh_smart_plugs",
        icon="mdi:power-plug-battery",
        action=_query_smart_plugs,
        message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
        action_id=ACTION_ID_SUBDEVICE_3032,
        cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
        dev_type=SUBDEVICE_DEV_TYPE_SOCKET,
    ),
    JackeryQueryButtonDescription(
        key="refresh_subdevice_combo",
        translation_key="refresh_subdevice_combo",
        icon="mdi:devices",
        action=_query_subdevice_combo,
        message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
        action_id=ACTION_ID_SUBDEVICE_3037,
        cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
        dev_type=SUBDEVICE_DEV_TYPE_COMBO,
    ),
)


def _storm_alert_id(alert: object) -> str | None:
    """Return the app ``storm[].alertId`` value when present."""
    if not isinstance(alert, dict):
        return None
    raw = alert.get(FIELD_ALERT_ID)
    if raw in (None, ""):
        return None
    return str(raw)


def _storm_alerts(weather_plan: object) -> list[dict[str, Any]]:
    """Return active storm alert payloads with stable alert ids."""
    if not isinstance(weather_plan, dict):
        return []
    storm = weather_plan.get(FIELD_STORM)
    if not isinstance(storm, list):
        return []
    return [
        alert
        for alert in storm
        if isinstance(alert, dict) and _storm_alert_id(alert) is not None
    ]


def _smart_plug_device_sn(plug: object) -> str | None:
    """Return the real smart-plug ``deviceSn`` required by schedule commands."""
    if not isinstance(plug, dict):
        return None
    raw = plug.get(FIELD_DEVICE_SN) or plug.get(FIELD_DEV_SN) or plug.get(FIELD_SN)
    if raw in (None, ""):
        return None
    return str(raw)


async def async_setup_entry(  # noqa: RUF029  # HA awaits this entry point
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up app-command Button entities for devices in the config entry.

    Create a JackeryRebootButton for each coordinator-managed device that either reports support for advanced features or exposes the reboot property, avoid registering duplicate entities, and only add entities when the coordinator-derived device signature changes. Registers a coordinator listener to update discovery when the signature changes.

    Parameters:
        entry (JackeryConfigEntry): Config entry whose runtime_data contains the integration coordinator.
        async_add_entities (AddEntitiesCallback): Callback to register new ButtonEntity instances with Home Assistant.
    """
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _append_unique(entities: list[ButtonEntity], entity: ButtonEntity) -> None:
        """Append a ButtonEntity to the list if its unique identifier has not been recorded, and record it to prevent duplicate button entities.

        Parameters:
            entities (list[ButtonEntity]): Target list to append the entity to when it is unique.
            entity (ButtonEntity): Button entity to append if its unique identifier has not been seen.
        """
        append_unique_entity(
            entities, seen_unique_ids, entity, platform="button", logger=_LOGGER
        )

    def _collect_entities() -> list[ButtonEntity]:
        """Collect button entities for devices managed by the coordinator.

        Create reboot, weather-plan refresh and active storm-alert delete buttons.

        Returns:
            list[ButtonEntity]: Unique `ButtonEntity` instances representing reboot actions for matching devices.
        """
        entities: list[ButtonEntity] = []
        for dev_id, payload in (coordinator.data or {}).items():
            props = payload.get(PAYLOAD_PROPERTIES) or {}
            for description in QUERY_BUTTON_DESCRIPTIONS:
                _append_unique(
                    entities,
                    JackeryQueryButton(coordinator, dev_id, description=description),
                )
            _append_unique(
                entities, JackeryRefreshWeatherPlanButton(coordinator, dev_id)
            )
            _append_unique(
                entities,
                JackeryReadScheduleButton(
                    coordinator,
                    dev_id,
                    task_type=TIMER_TASK_TYPE_CUSTOM_MODE,
                    key_suffix="read_custom_mode_schedule",
                    translation_key="read_custom_mode_schedule",
                    icon="mdi:calendar-clock",
                ),
            )
            _append_unique(
                entities,
                JackeryReadScheduleButton(
                    coordinator,
                    dev_id,
                    task_type=TIMER_TASK_TYPE_TIME_ELEC,
                    key_suffix="read_time_electricity_schedule",
                    translation_key="read_time_electricity_schedule",
                    icon="mdi:calendar-sync",
                ),
            )
            if coordinator.device_supports_advanced(dev_id) or FIELD_REBOOT in props:
                _append_unique(entities, JackeryRebootButton(coordinator, dev_id))
            for index, plug in enumerate(
                sorted_smart_plugs(payload.get(PAYLOAD_SMART_PLUGS)), start=1
            ):
                plug_sn = _smart_plug_device_sn(plug)
                if plug_sn is None:
                    continue
                _append_unique(
                    entities,
                    JackeryReadScheduleButton(
                        coordinator,
                        dev_id,
                        task_type=TIMER_TASK_TYPE_SMART_PLUG,
                        key_suffix=f"smart_plug_{index}_read_schedule",
                        translation_key="read_smart_plug_schedule",
                        icon="mdi:calendar-clock",
                        plug_sn=plug_sn,
                    ),
                )
            for alert in _storm_alerts(payload.get(PAYLOAD_WEATHER_PLAN)):
                alert_id = _storm_alert_id(alert)
                if alert_id is None:
                    continue
                _append_unique(
                    entities,
                    JackeryDeleteStormAlertButton(
                        coordinator,
                        dev_id,
                        alert_id=alert_id,
                    ),
                )
        return entities

    last_signature: tuple[Any, ...] = ()

    @callback
    def _add_new_entities() -> None:
        """Register newly discovered reboot button entities when the coordinator's device signature changes.

        If the coordinator-derived signature differs from the last cached signature, update the cache, collect new entities, and add them via `async_add_entities`.
        """
        nonlocal last_signature
        sig = coordinator_entity_signature(coordinator.data)
        if sig == last_signature:
            return
        last_signature = sig
        entities = _collect_entities()
        if entities:
            async_add_entities(entities)

    _add_new_entities()
    entry.async_on_unload(coordinator.async_add_listener(_add_new_entities))


class JackeryQueryButton(JackeryEntity, ButtonEntity):
    """Run one documented app read/query command."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        description: JackeryQueryButtonDescription,
    ) -> None:
        """Initialise the app query button."""
        super().__init__(coordinator, device_id, description.key)
        self._query_description = description
        self._attr_translation_key = description.translation_key
        self._attr_icon = description.icon

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the exact app command metadata sent by this button."""
        description = self._query_description
        attrs: dict[str, Any] = {
            FIELD_MESSAGE_TYPE: description.message_type,
            "actionId": description.action_id,
            FIELD_CMD: description.cmd,
        }
        if description.dev_type is not None:
            attrs[FIELD_DEV_TYPE] = description.dev_type
        return attrs

    def _raise_action_error(self, error: object) -> None:
        """Raise a translatable HA action error for this button."""
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="entity_action_failed",
            translation_placeholders={
                "entity": str(self._attr_translation_key),
                "device_id": self._device_id,
                "error": str(error),
            },
        )

    async def async_press(self) -> None:
        """Send the documented getter over BLE-first MQTT transport."""
        if not self.available:
            self._raise_action_error("device is offline")
        try:
            await self._query_description.action(self.coordinator, self._device_id)
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            self._raise_action_error(err)
        except Exception as err:
            self._raise_action_error(err)


class JackeryRebootButton(JackeryEntity, ButtonEntity):
    """Restart the SolarVault device via PROTOCOL.md §4 reboot command."""

    _attr_translation_key = "reboot_device"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:restart"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, "reboot_device")

    def _raise_action_error(self, error: object) -> None:
        """Raise a translatable HA action error for this button."""
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="entity_action_failed",
            translation_placeholders={
                "entity": "reboot_device",
                "device_id": self._device_id,
                "error": str(error),
            },
        )

    async def async_press(self) -> None:
        """Forward a button press to the device."""
        if not self.available:
            self._raise_action_error("device is offline")
        try:
            await self.coordinator.async_reboot_device(self._device_id)
            await self.coordinator.async_request_refresh()
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            self._raise_action_error(err)
        except Exception as err:
            self._raise_action_error(err)


class JackeryRefreshWeatherPlanButton(JackeryEntity, ButtonEntity):
    """Query the app weather/storm plan via ``QueryWeatherPlan``."""

    _attr_translation_key = "refresh_weather_plan"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:weather-cloudy-clock"

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        """Initialise the weather-plan refresh button."""
        super().__init__(coordinator, device_id, "refresh_weather_plan")

    def _raise_action_error(self, error: object) -> None:
        """Raise a translatable HA action error for this button."""
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="entity_action_failed",
            translation_placeholders={
                "entity": "refresh_weather_plan",
                "device_id": self._device_id,
                "error": str(error),
            },
        )

    async def async_press(self) -> None:
        """Refresh the weather/storm plan from the device."""
        try:
            await self.coordinator.async_query_weather_plan(self._device_id)
            await self.coordinator.async_request_refresh()
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            self._raise_action_error(err)
        except Exception as err:
            self._raise_action_error(err)


class JackeryReadScheduleButton(JackeryEntity, ButtonEntity):
    """Read one app schedule bucket via ``DownloadDeviceSchedule``."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        task_type: int,
        key_suffix: str,
        translation_key: str,
        icon: str,
        plug_sn: str = "",
    ) -> None:
        """Initialise the schedule-read button."""
        super().__init__(coordinator, device_id, key_suffix)
        self._task_type = task_type
        self._plug_sn = plug_sn
        self._attr_translation_key = translation_key
        self._attr_icon = icon

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return app task command metadata."""
        attrs: dict[str, Any] = {"taskType": self._task_type}
        if self._plug_sn:
            attrs[FIELD_DEVICE_SN] = self._plug_sn
        return attrs

    def _raise_action_error(self, error: object) -> None:
        """Raise a translatable HA action error for this button."""
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="entity_action_failed",
            translation_placeholders={
                "entity": str(self._attr_translation_key),
                "device_id": self._device_id,
                "error": str(error),
            },
        )

    async def async_press(self) -> None:
        """Read this schedule bucket through the documented app command."""
        try:
            await self.coordinator.async_read_device_schedule(
                self._device_id,
                task_type=self._task_type,
                plug_sn=self._plug_sn,
            )
            await self.coordinator.async_request_refresh()
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            self._raise_action_error(err)
        except Exception as err:
            self._raise_action_error(err)


class JackeryDeleteStormAlertButton(JackeryEntity, ButtonEntity):
    """Delete one active app storm alert via ``CancelWeatherAlert``."""

    _attr_translation_key = "delete_storm_alert"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:weather-lightning-rainy"

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        alert_id: str,
    ) -> None:
        """Initialise the storm-alert delete button."""
        super().__init__(coordinator, device_id, f"delete_storm_alert_{alert_id}")
        self._alert_id = alert_id

    @property
    def _alert(self) -> dict[str, Any]:
        """Return the current storm alert payload for this button."""
        for alert in _storm_alerts(self._payload.get(PAYLOAD_WEATHER_PLAN)):
            if _storm_alert_id(alert) == self._alert_id:
                return alert
        return {}

    @property
    def available(self) -> bool:
        """Only expose the delete action while the alert still exists."""
        return super().available and bool(self._alert)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return app storm alert metadata."""
        attrs: dict[str, Any] = {FIELD_ALERT_ID: self._alert_id}
        alert = self._alert
        for key in (FIELD_START_TS, FIELD_END_TS, FIELD_STATUS, FIELD_MANUAL):
            if key in alert:
                attrs[key] = alert.get(key)
        return attrs

    def _raise_action_error(self, error: object) -> None:
        """Raise a translatable HA action error for this button."""
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="entity_action_failed",
            translation_placeholders={
                "entity": "delete_storm_alert",
                "device_id": self._device_id,
                "error": str(error),
            },
        )

    async def async_press(self) -> None:
        """Delete this storm alert through the documented app command."""
        try:
            await self.coordinator.async_delete_storm_alert(
                self._device_id,
                self._alert_id,
            )
            await self.coordinator.async_request_refresh()
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            self._raise_action_error(err)
        except Exception as err:
            self._raise_action_error(err)
