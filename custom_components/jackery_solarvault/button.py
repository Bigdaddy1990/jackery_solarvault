"""Button platform for Jackery SolarVault."""

import logging
from typing import TYPE_CHECKING

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from .const import DOMAIN, FIELD_REBOOT, PAYLOAD_PROPERTIES
from .entity import JackeryEntity
from .util import append_unique_entity

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import JackeryConfigEntry
    from .coordinator import JackerySolarVaultCoordinator

# Limit concurrent control-write/update calls. This is a setter platform:
# writes go to the cloud and to MQTT. Serializing keeps the queue depth on
# the broker bounded and prevents reordering of `DevicePropertyChange`
# commands per HA dev guidance for write-heavy platforms.
PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the platform from a config entry."""
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _append_unique(entities: list[ButtonEntity], entity: ButtonEntity) -> None:
        append_unique_entity(
            entities, seen_unique_ids, entity, platform="button", logger=_LOGGER
        )

    def _collect_entities() -> list[ButtonEntity]:
        entities: list[ButtonEntity] = []
        for dev_id, payload in (coordinator.data or {}).items():
            props = payload.get(PAYLOAD_PROPERTIES) or {}
            if coordinator.device_supports_advanced(dev_id) or FIELD_REBOOT in props:
                _append_unique(entities, JackeryRebootButton(coordinator, dev_id))
        return entities

    @callback
    def _add_new_entities() -> None:
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
        """Create a query button entity for a specific device from a query button.

        description.

        Parameters:
            description (JackeryQueryButtonDescription): Metadata for the button;
            `description.key` is used as the entity key and
            `description.translation_key` / `description.icon` are applied to the
            entity.
        """
        super().__init__(coordinator, device_id, description.key)
        self._query_description = description
        self._attr_translation_key = description.translation_key
        self._attr_icon = description.icon

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the app-command metadata for this query button.

        Returns:
            dict[str, Any]: Mapping with the command metadata. Keys:
                - FIELD_MESSAGE_TYPE: the MQTT/app message type for the command
                - "actionId": the action identifier sent with the command
                - FIELD_CMD: the command value
                - FIELD_DEV_TYPE: the device type (included only when available)
        """
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
        """Raise a Home Assistant translated "action failed" error for this button.

        Raises:
            HomeAssistantError: Error with translation_domain=DOMAIN,
            translation_key="entity_action_failed",
            and translation_placeholders containing `entity`, `device_id`, and `error`.
        """
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
        """Execute the configured query action for this entity's device.

        Raises an error if the device is offline. Propagates ConfigEntryAuthFailed
        unchanged. If a HomeAssistantError with a translation_key is raised, it is
        propagated unchanged; any other exception is converted and raised with
        translated error context.
        """
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
        except ACTION_WRITE_ERRORS as err:
            self._raise_action_error(err)


class JackeryRebootButton(JackeryEntity, ButtonEntity):
    """Restart the SolarVault device via MQTT_PROTOCOL.md reboot command."""

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
        """Reboot the device and request the coordinator to refresh its data.

        Raises HomeAssistantError if the device is offline or the reboot command fails.
        """
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
        except ACTION_WRITE_ERRORS as err:
            self._raise_action_error(err)


class JackeryRefreshWeatherPlanButton(JackeryEntity, ButtonEntity):
    """Query the app weather/storm plan via ``QueryWeatherPlan``."""

    _attr_translation_key = "refresh_weather_plan"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:weather-cloudy-clock"

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
    ) -> None:
        """Create the button entity that triggers querying the device weather and storm.

        plan.
        """
        super().__init__(coordinator, device_id, "refresh_weather_plan")

    def _raise_action_error(self, error: object) -> None:
        """Raise a translated Home AssistantError for a failed entity action on the.

        target device.

        Uses the integration translation domain and the "entity_action_failed"
        translation key.
        Placeholders set in the raised error:
        - "entity": "refresh_weather_plan"
        - "device_id": the target device identifier (self._device_id)
        - "error": the string representation of `error`

        Parameters:
            error (object): The original error to include in the translation
            placeholders.
        """
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
        """Refresh the device weather/storm plan and trigger a coordinator refresh.

        Raises:
            ConfigEntryAuthFailed: If authentication with the config entry has failed.
            HomeAssistantError: If the action fails; if the caught error already has a
            `translation_key` it is re-raised unchanged, otherwise a
            `HomeAssistantError` is raised with a translation key indicating the entity
            action failed.
        """
        if not self.available:
            self._raise_action_error("device is offline")
        try:
            await self.coordinator.async_query_weather_plan(self._device_id)
            await self.coordinator.async_request_refresh()
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            self._raise_action_error(err)
        except ACTION_WRITE_ERRORS as err:
            self._raise_action_error(err)


class JackeryReadScheduleButton(JackeryEntity, ButtonEntity):
    """Read one app schedule bucket via ``DownloadDeviceSchedule``."""

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(  # noqa: PLR0913
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
        """Create a button entity that triggers reading a specific schedule/task bucket.

        from the device.

        Parameters:
            coordinator: Coordinator that manages device communication and state.
            device_id (str): Unique device identifier this button targets.
            task_type (int): Identifier of the schedule/task bucket to read (use the
            module's TIMER_TASK_TYPE_* constants).
            key_suffix (str): Suffix appended to the entity unique key to distinguish
            this schedule read button.
            translation_key (str): Translation key used for the button's name.
            icon (str): Material Design Icon name for the button.
            plug_sn (str, optional): Smart-plug device serial number to target when
            reading a plug-specific schedule; omit for device-level schedules.
        """
        super().__init__(coordinator, device_id, key_suffix)
        self._task_type = task_type
        self._plug_sn = plug_sn
        self._attr_translation_key = translation_key
        self._attr_icon = icon

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the schedule-read command metadata as entity attributes.

        Includes "taskType" and, when this button targets a specific smart plug, the
        plug's device serial under FIELD_DEVICE_SN.

        Returns:
            dict[str, Any]: Attributes dictionary containing "taskType" and optionally
            FIELD_DEVICE_SN.
        """
        attrs: dict[str, Any] = {"taskType": self._task_type}
        if self._plug_sn:
            attrs[FIELD_DEVICE_SN] = self._plug_sn
        return attrs

    def _raise_action_error(self, error: object) -> None:
        """Raise a Home Assistant translated "action failed" error for this button.

        Raises:
            HomeAssistantError: Error with translation_domain=DOMAIN,
            translation_key="entity_action_failed",
            and translation_placeholders containing `entity`, `device_id`, and `error`.
        """
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
        """Read the configured device schedule and refresh the coordinator.

        Raises:
            ConfigEntryAuthFailed: Re-raised when authentication has failed.
            HomeAssistantError: Raised if the device is offline or if the
            schedule read fails.
        """
        if not self.available:
            self._raise_action_error("device is offline")
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
        except ACTION_WRITE_ERRORS as err:
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
        """Create a delete-storm-alert button entity bound to a specific alert id.

        Parameters:
            coordinator (JackerySolarVaultCoordinator): Coordinator managing device
            state and actions.
            device_id (str): Identifier of the device the alert belongs to.
            alert_id (str): Stable identifier of the storm alert; included in the
            entity's unique id.
        """
        super().__init__(coordinator, device_id, f"delete_storm_alert_{alert_id}")
        self._alert_id = alert_id

    @property
    def _alert(self) -> dict[str, Any]:
        """Finds the storm alert in the current payload that matches this button's.

        alert id.

        Scans the entity payload's weather plan alerts and returns the alert dictionary
        whose stable alert id equals this button's stored alert id.

        Returns:
            dict[str, Any]: The matching alert dictionary, or an empty dict if no
            matching alert is present.
        """
        payload = self._payload
        if payload:
            for alert in _storm_alerts(payload.get(PAYLOAD_WEATHER_PLAN)):
                if _storm_alert_id(alert) == self._alert_id:
                    return alert
        return {}

    @property
    def available(self) -> bool:
        """Determine whether the delete storm alert button is currently available.

        The button is available only when the base entity is available and the targeted
        storm alert still exists.

        Returns:
            True if the base entity is available and the referenced storm alert exists,
            False otherwise.
        """
        return super().available and bool(self._alert)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return extra state attributes for the delete-storm-alert button.

        Includes the alert's ID under FIELD_ALERT_ID and, if present in the current
        alert, any of FIELD_START_TS, FIELD_END_TS, FIELD_STATUS, and FIELD_MANUAL.

        Returns:
            dict[str, Any]: Mapping containing `FIELD_ALERT_ID` and any of
            `FIELD_START_TS`, `FIELD_END_TS`, `FIELD_STATUS`, `FIELD_MANUAL` present on
            the alert.
        """
        attrs: dict[str, Any] = {FIELD_ALERT_ID: self._alert_id}
        alert = self._alert
        for key in (FIELD_START_TS, FIELD_END_TS, FIELD_STATUS, FIELD_MANUAL):
            if key in alert:
                attrs[key] = alert.get(key)
        return attrs

    def _raise_action_error(self, error: object) -> None:
        """Raise a localized Home AssistantError indicating the delete-storm-alert.

        action failed.

        The error uses the integration translation domain and the
        `entity_action_failed` translation key.
        Placeholders provided: `entity` ("delete_storm_alert"), `device_id`, and
        `error`.

        Raises:
            HomeAssistantError: localized error for a failed entity action.
        """
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
        """Delete the associated storm alert and request a coordinator data refresh.

        Raises:
            ConfigEntryAuthFailed: If authentication with the config entry failed
            (re-raised).
            HomeAssistantError: If an error occurs; errors that already have a
            `translation_key` are re-raised, other exceptions are converted and raised
            via the entity's `_raise_action_error`.
        """
        if not super().available:
            self._raise_action_error("device is offline")
        if not self._alert:
            self._raise_action_error("storm alert is no longer active")
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
        except ACTION_WRITE_ERRORS as err:
            self._raise_action_error(err)
