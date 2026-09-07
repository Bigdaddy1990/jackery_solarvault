"""Button platform for Jackery SolarVault."""

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from .client import JackeryAuthError
from .const import (
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
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_WEATHER_PLAN,
    SUBDEVICE_DEV_TYPE_SOCKET,
    TIMER_TASK_TYPE_CUSTOM_MODE,
    TIMER_TASK_TYPE_SMART_PLUG,
    TIMER_TASK_TYPE_TIME_ELEC,
)
from .coordinator import ACTION_WRITE_ERRORS, subdevice_accessories
from .descriptions import BUTTON_DESCRIPTIONS, JackeryButtonDescription
from .entity import (
    LAYER5_COMMAND_SOURCES,
    LAYER5_DATA_SOURCES,
    JackeryEntity,
    payload_properties_for_sources,
)
from .util import (
    append_unique_entity,
    coordinator_entity_signature,
    is_portable_payload as _is_portable_payload,
    sorted_smart_plugs,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import JackeryConfigEntry
    from .coordinator import JackerySolarVaultCoordinator

__all__ = [
    "QUERY_BUTTON_DESCRIPTIONS",
    "JackeryQueryButtonDescription",
]

# Compatibility exports retained for callers that imported the former
# platform-local query-button specification.
QUERY_BUTTON_DESCRIPTIONS = BUTTON_DESCRIPTIONS
JackeryQueryButtonDescription = JackeryButtonDescription

# Limit concurrent control-write/update calls. This is a setter platform:
# writes go to the cloud and to MQTT. Serializing keeps the queue depth on
# the broker bounded and prevents reordering of `DevicePropertyChange`
# commands per HA dev guidance for write-heavy platforms.
PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)


type _ReadScheduleConfig = tuple[int, str, str]


# --- Portable / Explorer powerstation actions --------------------------------


def _storm_alert_id(alert: object) -> str | None:
    """Extract the storm alert's alertId from an alert mapping.

    Parameters:
        alert (object): The alert object, expected to be a mapping (dict) containing
        the alert identifier.

    Returns:
        str | None: The alert id as a string when present and not empty, otherwise
        `None`.
    """
    if not isinstance(alert, dict):
        return None
    raw = alert.get(FIELD_ALERT_ID)
    if raw in {None, ""}:
        return None
    return str(raw)


def _storm_alerts(weather_plan: object) -> list[dict[str, Any]]:
    """Extract active storm alert dictionaries from a weather plan that have stable.

    alert IDs.

    Parameters:
        weather_plan (object): The weather plan payload (expected to be a dict) which
        may contain a list of storm alerts under FIELD_STORM.

    Returns:
        list[dict[str, Any]]: List of alert dictionaries that include a stable alert
        id; returns an empty list if the input is invalid or no matching alerts exist.
    """
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
    """Extract the stable serial number for a smart plug from a plug mapping.

    Checks the plug mapping for FIELD_DEVICE_SN, then FIELD_DEV_SN, then FIELD_SN and
    returns the first non-empty value found.

    Parameters:
        plug (object): A mapping-like object representing a smart plug (typically a
        dict).

    Returns:
        str: The serial number as a string when available, `None` otherwise.
    """
    if not isinstance(plug, dict):
        return None
    raw = plug.get(FIELD_DEVICE_SN) or plug.get(FIELD_DEV_SN) or plug.get(FIELD_SN)
    if raw in {None, ""}:
        return None
    return str(raw)


async def async_setup_entry(  # ruff: ignore[unused-async]  # HA requires an async platform hook.
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up reboot Button entities for devices in the config entry.

    Create a JackeryRebootButton for each coordinator-managed device that either reports
    support for advanced features or exposes the reboot property, avoid registering
    duplicate entities, and only add entities when the coordinator-derived device
    signature changes. Registers a coordinator listener to update discovery when the
    signature changes.

    Parameters:
        entry (JackeryConfigEntry): Config entry whose runtime_data contains the
        integration coordinator.
        async_add_entities (AddEntitiesCallback): Callback to register new ButtonEntity
        instances with Home Assistant.
    """
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _append_unique(entities: list[ButtonEntity], entity: ButtonEntity) -> None:
        """Append the entity unless its unique ID was already seen."""
        append_unique_entity(entities, seen_unique_ids, entity)

    def _collect_entities() -> list[ButtonEntity]:
        """Collect reboot button entities for devices managed by the coordinator.

        Create a JackeryRebootButton for each device that either supports advanced
        features or exposes the reboot property; duplicate entities are omitted.

        Returns:
            list[ButtonEntity]: Unique `ButtonEntity` instances representing reboot
            actions for matching devices.
        """
        entities: list[ButtonEntity] = []
        for dev_id, payload in (coordinator.data or {}).items():
            props = payload_properties_for_sources(payload)
            # Keep command families separate: portable devices use the portable
            # catalog actionIds, home systems use the home catalog actionIds.
            is_portable = _is_portable_payload(payload, props)
            for description in BUTTON_DESCRIPTIONS:
                if description.key.startswith("portable_") != is_portable:
                    continue
                _append_unique(
                    entities,
                    JackeryQueryButton(coordinator, dev_id, description=description),
                )
            if is_portable:
                continue
            _append_unique(
                entities,
                JackeryRefreshWeatherPlanButton(coordinator, dev_id),
            )
            _append_unique(
                entities,
                JackeryReadScheduleButton(
                    coordinator,
                    dev_id,
                    config=(
                        TIMER_TASK_TYPE_CUSTOM_MODE,
                        "read_custom_mode_schedule",
                        "read_custom_mode_schedule",
                    ),
                ),
            )
            _append_unique(
                entities,
                JackeryReadScheduleButton(
                    coordinator,
                    dev_id,
                    config=(
                        TIMER_TASK_TYPE_TIME_ELEC,
                        "read_time_electricity_schedule",
                        "read_time_electricity_schedule",
                    ),
                ),
            )
            if coordinator.device_supports_advanced(dev_id) or FIELD_REBOOT in props:
                _append_unique(entities, JackeryRebootButton(coordinator, dev_id))
            valid_plugs = sorted_smart_plugs(payload.get(PAYLOAD_SMART_PLUGS))
            if not valid_plugs:
                valid_plugs = sorted_smart_plugs(
                    subdevice_accessories(
                        payload,
                        dev_type=SUBDEVICE_DEV_TYPE_SOCKET,
                    )
                )
            for plug in valid_plugs:
                plug_sn = _smart_plug_device_sn(plug)
                if plug_sn is None:
                    continue
                _append_unique(
                    entities,
                    JackeryReadScheduleButton(
                        coordinator,
                        dev_id,
                        config=(
                            TIMER_TASK_TYPE_SMART_PLUG,
                            f"smart_plug_{plug_sn}_read_schedule",
                            "read_smart_plug_schedule",
                        ),
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
        """Register newly discovered buttons after the device signature changes.

        If the coordinator-derived signature differs from the last cached signature,
        update the cache, collect new entities, and add them via `async_add_entities`.
        """
        nonlocal last_signature
        storm_signature = tuple(
            (
                dev_id,
                tuple(
                    sorted(
                        alert_id
                        for alert in _storm_alerts(
                            (payload or {}).get(PAYLOAD_WEATHER_PLAN),
                        )
                        if (alert_id := _storm_alert_id(alert)) is not None
                    ),
                ),
            )
            for dev_id, payload in sorted((coordinator.data or {}).items())
        )
        sig = (coordinator_entity_signature(coordinator.data), storm_signature)
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

    entity_description: JackeryButtonDescription
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        description: JackeryButtonDescription,
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description
        self._query_description = description
        self.device_registry_role = description.device_registry_role

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
        description = self.entity_description
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

    async def _async_run_query(self) -> None:
        """Run the push query and optional documented HTTP read concurrently."""
        description = self.entity_description
        if not description.has_http_read:
            await description.action(self.coordinator, self._device_id)
            return

        query_result: object
        http_result: object
        query_result, http_result = await asyncio.gather(
            description.action(self.coordinator, self._device_id),
            self.coordinator.async_refresh_documented_http_read(
                self._device_id,
                device_property=description.http_device_property,
                system_shadow=description.http_system_shadow,
                battery_packs=description.http_battery_packs,
                subdevice_dev_type=description.http_subdevice_dev_type,
            ),
            return_exceptions=True,
        )
        for result in (query_result, http_result):
            if isinstance(result, asyncio.CancelledError):
                raise result
        query_succeeded = not isinstance(query_result, BaseException)
        http_succeeded = http_result is True
        if query_succeeded or http_succeeded:
            if isinstance(query_result, BaseException):
                _LOGGER.debug(
                    "Jackery query transport failed for %s/%s after the "
                    "documented HTTP read succeeded: %s",
                    self._device_id,
                    description.key,
                    query_result,
                )
            elif isinstance(http_result, BaseException):
                _LOGGER.debug(
                    "Jackery documented HTTP read failed for %s/%s after the "
                    "query transport succeeded: %s",
                    self._device_id,
                    description.key,
                    http_result,
                )
            return
        if isinstance(query_result, BaseException):
            raise query_result
        if isinstance(http_result, BaseException):
            raise http_result
        self._raise_action_error("No documented refresh transport returned data")

    async def async_press(self) -> None:
        """Forward a button press to the device."""
        try:
            await self._async_run_query()
        except JackeryAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            self._raise_action_error(err)
        except ACTION_WRITE_ERRORS as err:
            self._raise_action_error(err)


class JackeryRebootButton(JackeryEntity, ButtonEntity):
    """Restart the SolarVault device via PROTOCOL.md §4 reboot command."""

    _attr_translation_key = "reboot_device"
    _attr_entity_category = EntityCategory.CONFIG
    data_sources = LAYER5_DATA_SOURCES
    command_sources = LAYER5_COMMAND_SOURCES
    app_fields = (FIELD_REBOOT,)

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
    ) -> None:
        """Create a button entity that triggers a reboot of the specified device.

        Parameters:
            coordinator (JackerySolarVaultCoordinator): Coordinator that manages device
            state and provides control actions.
            device_id (str): Unique identifier of the target device for the reboot
            action.
        """
        super().__init__(coordinator, device_id, "reboot_device")

    def _raise_action_error(self, error: object) -> None:
        """Raise a translatable HomeAssistantError for a failed reboot action.

        The exception uses translation_domain=DOMAIN and
        translation_key="entity_action_failed" and includes translation placeholders:
        - "entity": "reboot_device"
        - "device_id": this entity's device id
        - "error": str(error)
        """
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
        """Reboot the associated device through the coordinator.

        If authentication fails, the original ConfigEntryAuthFailed is propagated. A
        HomeAssistantError that already has a `translation_key` is re-raised unchanged;
        all other exceptions are converted and surfaced via `_raise_action_error`.
        """
        try:
            await self.coordinator.async_reboot_device(self._device_id)
        except JackeryAuthError as err:
            raise ConfigEntryAuthFailed from err
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

    device_registry_role = "system"
    _attr_translation_key = "refresh_weather_plan"
    _attr_entity_category = EntityCategory.CONFIG
    data_sources = LAYER5_DATA_SOURCES
    command_sources = LAYER5_COMMAND_SOURCES

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
        """Query the device weather/storm plan through the coordinator.

        Raises:
            ConfigEntryAuthFailed: If authentication with the config entry has failed.
            HomeAssistantError: If the action fails; if the caught error already has a
            `translation_key` it is re-raised unchanged, otherwise a
            `HomeAssistantError` is raised with a translation key indicating the entity
            action failed.
        """
        if not self.available:
            self._raise_action_error("entity unavailable")
        try:
            await self.coordinator.async_query_weather_plan(self._device_id)
        except JackeryAuthError as err:
            raise ConfigEntryAuthFailed from err
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
    data_sources = LAYER5_DATA_SOURCES
    command_sources = LAYER5_COMMAND_SOURCES

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        config: _ReadScheduleConfig,
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
            plug_sn (str, optional): Smart-plug device serial number to target when
            reading a plug-specific schedule; omit for device-level schedules.
        """
        task_type, key_suffix, translation_key = config
        super().__init__(coordinator, device_id, key_suffix)
        self._task_type = task_type
        self._plug_sn = plug_sn
        self._attr_translation_key = translation_key

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
        """Trigger a device schedule read for the configured task bucket.

        Raises:
            ConfigEntryAuthFailed: Re-raised when authentication has failed.
            HomeAssistantError: Re-raised unchanged if it already has a
            `translation_key`; other exceptions are converted into a translated
            `HomeAssistantError` indicating the entity action failed.
        """
        if not self.available:
            self._raise_action_error("entity unavailable")
        try:
            await self.coordinator.async_read_device_schedule(
                self._device_id,
                task_type=self._task_type,
                plug_sn=self._plug_sn,
            )
        except JackeryAuthError as err:
            raise ConfigEntryAuthFailed from err
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

    device_registry_role = "system"
    _attr_translation_key = "delete_storm_alert"
    _attr_entity_category = EntityCategory.CONFIG
    data_sources = ("cloud_mqtt",)
    command_sources = ("cloud_mqtt",)
    app_fields = (FIELD_ALERT_ID,)

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

        Availability mirrors what the integration actually knows about the alert:

        * If the base entity is unavailable, the button is unavailable.
        * If the weather plan has not been loaded yet, the alert's existence is
          *unknown*. The button stays available so that pressing it runs the real
          delete path, which surfaces the specific domain error (for example
          ``alert_already_deleted`` / ``alert_not_found``) instead of HA short-
          circuiting the press with a generic ``entity_action_failed``.
        * If the weather plan *is* loaded, the alert is definitively present or
          gone, so availability tracks whether the targeted alert still exists.

        Returns:
            True if the base entity is available and either the weather plan is not
            yet loaded or the referenced storm alert still exists; False otherwise.
        """
        if not super().available:
            return False
        if PAYLOAD_WEATHER_PLAN not in self._payload:
            return True
        return bool(self._alert)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Extra state attributes for the delete-storm-alert button.

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
        """Delete the associated storm alert through the coordinator.

        Raises:
            ConfigEntryAuthFailed: If authentication with the config entry failed
            (re-raised).
            HomeAssistantError: If an error occurs; errors that already have a
            `translation_key` are re-raised, other exceptions are converted and raised
            via the entity's `_raise_action_error`.
        """
        if not self.available:
            self._raise_action_error("entity unavailable")
        try:
            await self.coordinator.async_delete_storm_alert(
                self._device_id,
                self._alert_id,
            )
        except JackeryAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            self._raise_action_error(err)
        except ACTION_WRITE_ERRORS as err:
            self._raise_action_error(err)
