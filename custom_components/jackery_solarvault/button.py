"""Button platform for Jackery SolarVault."""

from dataclasses import replace
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.button import ButtonEntity
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo

from .client import JackeryAuthError
from .const import (
    ACTION_ID_DELETE_STORM_ALERT,
    ACTION_ID_PORTABLE_POWER_PACK_BLINK,
    ACTION_ID_QUERY_WEATHER_PLAN,
    ACTION_ID_REBOOT_DEVICE,
    DOMAIN,
    FIELD_ALERT_ID,
    FIELD_CMD,
    FIELD_DEVICE_SN,
    FIELD_DEV_SN,
    FIELD_DEV_TYPE,
    FIELD_END_TS,
    FIELD_HOST,
    FIELD_MANUAL,
    FIELD_MESSAGE_TYPE,
    FIELD_REBOOT,
    FIELD_SN,
    FIELD_START_TS,
    FIELD_STATUS,
    FIELD_STORM,
    MQTT_CMD_DEVICE_PROPERTY_CHANGE,
    MQTT_CMD_NONE,
    MQTT_CMD_QUERY_WEATHER_PLAN,
    MQTT_HOST,
    MQTT_MESSAGE_CANCEL_WEATHER_ALERT,
    MQTT_MESSAGE_CONTROL_SUB_DEVICE,
    MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
    MQTT_MESSAGE_QUERY_WEATHER_PLAN,
    MQTT_PORT,
    PAYLOAD_BATTERY_PACKS,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_WEATHER_PLAN,
    PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID,
    SUBDEVICE_DEV_TYPE_BATTERY_PACK,
    SUBDEVICE_DEV_TYPE_SOCKET,
    TIMER_TASK_TYPE_CUSTOM_MODE,
    TIMER_TASK_TYPE_SMART_PLUG,
    TIMER_TASK_TYPE_TIME_ELEC,
)
from .coordinator import (
    ACTION_WRITE_ERRORS,
    battery_pack_serial,
    sorted_battery_pack_payloads,
    subdevice_accessories,
)
from .descriptions import BUTTON_DESCRIPTIONS, JackeryButtonDescription
from .entity import (
    LAYER5_COMMAND_SOURCES,
    LAYER5_DATA_SOURCES,
    JackeryEntity,
    payload_properties_for_sources,
    raise_entity_action_error,
)
from .util import (
    append_unique_entity,
    coordinator_entity_signature,
    sorted_smart_plugs,
    stable_subdevice_key,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import ChildDeviceInfo
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


async def async_setup_entry(  # ruff: ignore[unused-async, complex-structure]  # HA requires an async platform hook.  # ruff: ignore[too-many-statements]
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

    def _collect_entities() -> list[ButtonEntity]:  # ruff: ignore[complex-structure, too-many-branches]
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
            family = coordinator.device_family(dev_id)
            if family == "unknown":
                continue
            if family == "box":
                # Box uses its own App action IDs; Home actions are not a
                # substitute even where their user-facing labels match.
                box_reads = {
                    "refresh_device_info": (252, 9),
                    "refresh_wifi_list": (254, 1),
                    "refresh_device_ota_version": (248, 100),
                }
                for description in BUTTON_DESCRIPTIONS:
                    pair = box_reads.get(description.key)
                    if pair is None:
                        continue
                    box_description = replace(
                        description,
                        action_id=pair[0],
                        cmd=pair[1],
                    )
                    append_unique_entity(
                        entities,
                        seen_unique_ids,
                        JackeryQueryButton(
                            coordinator,
                            dev_id,
                            description=replace(
                                box_description,
                                command_sources=("http", "ble")
                                if description.has_http_read
                                else ("ble",),
                            ),
                        ),
                    )
                    if description.key == "refresh_device_info":
                        for key, action, cmd in (
                            ("box_refresh_circuits", 7, 10),
                            ("box_refresh_weather", 19, 23),
                        ):
                            box_extra = replace(
                                description,
                                key=key,
                                action_id=action,
                                cmd=cmd,
                                http_device_property=False,
                            )
                            append_unique_entity(
                                entities,
                                seen_unique_ids,
                                JackeryQueryButton(
                                    coordinator,
                                    dev_id,
                                    description=replace(
                                        box_extra, command_sources=("ble",)
                                    ),
                                ),
                            )
                continue
            is_portable = family == "portable"
            for description in BUTTON_DESCRIPTIONS:
                if description.key.startswith("portable_") != is_portable:
                    continue
                if description.key == "portable_power_pack_blink":
                    continue  # The action is registered once per physical pack below.
                append_unique_entity(
                    entities,
                    seen_unique_ids,
                    JackeryQueryButton(coordinator, dev_id, description=description),
                )
            if is_portable:
                packs = payload.get(PAYLOAD_BATTERY_PACKS)
                if not isinstance(packs, list) or not packs:
                    packs = subdevice_accessories(
                        payload, dev_type=SUBDEVICE_DEV_TYPE_BATTERY_PACK
                    )
                for index, pack in enumerate(
                    sorted_battery_pack_payloads(packs), start=1
                ):
                    serial = battery_pack_serial(pack)
                    if (
                        serial is None
                        or coordinator.battery_pack_identity_serial(dev_id, index)
                        != serial
                    ):
                        continue
                    pack_key = stable_subdevice_key("battery_pack", serial, index)
                    append_unique_entity(
                        entities,
                        seen_unique_ids,
                        JackeryPortablePackBlinkButton(
                            coordinator,
                            dev_id,
                            pack_index=index,
                            pack_sn=serial,
                            pack_key=pack_key,
                        ),
                    )
                continue
            append_unique_entity(
                entities,
                seen_unique_ids,
                JackeryRefreshWeatherPlanButton(coordinator, dev_id),
            )
            append_unique_entity(
                entities,
                seen_unique_ids,
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
            append_unique_entity(
                entities,
                seen_unique_ids,
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
                append_unique_entity(
                    entities, seen_unique_ids, JackeryRebootButton(coordinator, dev_id)
                )
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
                append_unique_entity(
                    entities,
                    seen_unique_ids,
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
                append_unique_entity(
                    entities,
                    seen_unique_ids,
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
    device_registry_role: str

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        description: JackeryButtonDescription,
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, description.key)
        self.device_registry_role = description.device_registry_role
        self.command_sources = description.command_sources
        self.entity_description = description
        self._query_description = self.entity_description

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
            "command_transports": self.command_sources,
        }
        if description.dev_type is not None:
            attrs[FIELD_DEV_TYPE] = description.dev_type
        return attrs

    async def _async_run_query(self) -> None:
        """Run the query through the coordinator's independent transport dispatcher."""
        if self.coordinator.device_family(self._device_id) == "box":
            description = self.entity_description
            await self.coordinator._async_publish_command_ble_first(  # ruff: ignore[private-member-access]
                self._device_id,
                message_type=description.message_type,
                action_id=description.action_id,
                cmd=description.cmd,
                body_fields={},
                allow_ble=True,
                allow_cloud=False,
                http_read={"device_property": description.http_device_property},
            )
            return
        await self.entity_description.action(self.coordinator, self._device_id)

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
            raise_entity_action_error(self.entity_description.key, self._device_id, err)
        except ACTION_WRITE_ERRORS as err:
            raise_entity_action_error(self.entity_description.key, self._device_id, err)


class JackeryTransportQueryButton(JackeryQueryButton):
    """Run a documented device read over exactly one selected transport."""

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        description: JackeryButtonDescription,
        transport: str,
    ) -> None:
        """Bind the entity identity, name and availability to one transport."""
        if transport not in {"http", "ble", "cloud_mqtt"} or (
            transport == "http" and not description.has_http_read
        ):
            raise ValueError("Unsupported transport-specific query")  # ruff: ignore[raise-vanilla-args]
        self._transport = transport
        super().__init__(
            coordinator,
            device_id,
            description=replace(
                description,
                key=f"{description.key}_{transport}",
                translation_key=f"{description.key}_{transport}",
                command_sources=(transport,),
            ),
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose the exact selected transport alongside command metadata."""
        attributes = {
            **super().extra_state_attributes,
            "command_transport": self._transport,
        }
        family = getattr(self.coordinator, "device_family", lambda _device_id: None)
        if self._transport == "ble" and family(self._device_id) == "box":
            # Box BLE action/cmd pairs do not establish an MQTT message type.
            attributes.pop(FIELD_MESSAGE_TYPE, None)
        return attributes

    async def _async_run_query(self) -> None:
        """Send the selected App read without starting another transport."""
        description = self.entity_description
        key = description.key.removesuffix(f"_{self._transport}")
        if key in {"sync_time_zone", "portable_sync_time_zone"}:
            action = (
                self.coordinator.async_send_portable_time_zone
                if key.startswith("portable_")
                else self.coordinator.async_send_time_zone
            )
            await action(
                self._device_id,
                allow_ble=self._transport == "ble",
                allow_cloud=self._transport == "cloud_mqtt",
            )
            return
        if key == "sync_cloud_mqtt_info":
            await self.coordinator.async_sync_mqtt_connect_info(
                self._device_id,
                allow_ble=self._transport == "ble",
                allow_cloud=self._transport == "cloud_mqtt",
            )
            return
        if key == "portable_sync_mqtt_info":
            await self.coordinator._async_publish_command_ble_first(  # ruff: ignore[private-member-access]
                self._device_id,
                message_type=description.message_type,
                action_id=description.action_id,
                cmd=description.cmd,
                body_fields={FIELD_HOST: MQTT_HOST, "port": MQTT_PORT},
                allow_ble=self._transport == "ble",
                allow_cloud=self._transport == "cloud_mqtt",
                allow_local_mqtt=False,
            )
            return
        if key == "portable_power_pack_blink":
            payload = (self.coordinator.data or {}).get(self._device_id, {})
            packs = (
                payload.get(PAYLOAD_BATTERY_PACKS)
                if isinstance(payload, dict)
                else None
            )
            serials = (
                {
                    serial
                    for pack in packs
                    if isinstance(pack, dict)
                    if (serial := battery_pack_serial(pack)) is not None
                }
                if isinstance(packs, list)
                else set()
            )
            if len(serials) != 1:
                raise HomeAssistantError(  # ruff: ignore[raise-vanilla-args]
                    "Pack blink requires one unambiguous battery-pack serial"
                )
            target = next(iter(serials))

            def identity_current() -> bool:
                current = (self.coordinator.data or {}).get(self._device_id, {})
                rows = (
                    current.get(PAYLOAD_BATTERY_PACKS)
                    if isinstance(current, dict)
                    else None
                )
                return isinstance(rows, list) and [
                    serial
                    for row in rows
                    if isinstance(row, dict)
                    if (serial := battery_pack_serial(row)) is not None
                ] == [target]

            await self.coordinator._async_publish_command_ble_first(  # ruff: ignore[private-member-access]
                self._device_id,
                message_type=description.message_type,
                action_id=description.action_id,
                cmd=description.cmd,
                body_fields={FIELD_SN: target, "blink": 1},
                allow_ble=self._transport == "ble",
                allow_cloud=self._transport == "cloud_mqtt",
                allow_local_mqtt=False,
                identity_guard=identity_current,
            )
            return
        if self._transport == "http":
            success = await self.coordinator.async_refresh_documented_http_read(
                self._device_id,
                device_property=description.http_device_property,
                system_shadow=description.http_system_shadow,
                battery_packs=description.http_battery_packs,
                subdevice_dev_type=description.http_subdevice_dev_type,
            )
            if not success:
                raise HomeAssistantError(  # ruff: ignore[raise-vanilla-args]
                    "The selected HTTP read returned no matching data"
                )
            return
        body = (
            {}
            if description.dev_type is None
            else {FIELD_DEV_TYPE: description.dev_type}
        )
        await self.coordinator._async_publish_command_ble_first(  # ruff: ignore[private-member-access]
            self._device_id,
            message_type=description.message_type,
            action_id=description.action_id,
            cmd=description.cmd,
            body_fields=body,
            allow_ble=self._transport == "ble",
            allow_cloud=self._transport == "cloud_mqtt",
            allow_local_mqtt=False,
        )


class JackeryRebootButton(JackeryEntity, ButtonEntity):
    """Restart the SolarVault device via PROTOCOL.md §4 reboot command."""

    _attr_translation_key = "reboot_device"
    _attr_entity_category = EntityCategory.CONFIG
    data_sources = LAYER5_DATA_SOURCES
    command_sources: tuple[str, ...] = LAYER5_COMMAND_SOURCES
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

    async def async_press(self) -> None:
        """Reboot the associated device through the coordinator.

        If authentication fails, the original ConfigEntryAuthFailed is propagated. A
        HomeAssistantError that already has a `translation_key` is re-raised unchanged;
        all other exceptions are converted and surfaced via `raise_entity_action_error`.
        """
        try:
            await self.coordinator._async_publish_command_ble_first(  # ruff: ignore[private-member-access]
                self._device_id,
                message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
                action_id=ACTION_ID_REBOOT_DEVICE,
                cmd=MQTT_CMD_DEVICE_PROPERTY_CHANGE,
                body_fields={FIELD_REBOOT: 1},
                allow_ble=True,
                allow_cloud=True,
                allow_local_mqtt=False,
            )
            self.coordinator._apply_local_property_patch(  # ruff: ignore[private-member-access]
                self._device_id, {FIELD_REBOOT: 1}
            )
        except JackeryAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            raise_entity_action_error("reboot_device", self._device_id, err)
        except ACTION_WRITE_ERRORS as err:
            raise_entity_action_error("reboot_device", self._device_id, err)


class JackeryTransportRebootButton(JackeryRebootButton):
    """Send the Home reboot command through one selected App transport."""

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str, transport: str
    ) -> None:
        """Create a stable entity bound to a single command source."""
        if transport not in {"ble", "cloud_mqtt"}:
            raise ValueError("Unsupported reboot transport")  # ruff: ignore[raise-vanilla-args]
        JackeryEntity.__init__(
            self, coordinator, device_id, f"reboot_device_{transport}"
        )
        self._transport = transport
        self.command_sources = (transport,)
        self._attr_translation_key = f"reboot_device_{transport}"

    async def async_press(self) -> None:
        """Send one reboot request with the documented App command body."""
        try:
            await self.coordinator._async_publish_command_ble_first(  # ruff: ignore[private-member-access]
                self._device_id,
                message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
                action_id=ACTION_ID_REBOOT_DEVICE,
                cmd=MQTT_CMD_DEVICE_PROPERTY_CHANGE,
                body_fields={FIELD_REBOOT: 1},
                allow_ble=self._transport == "ble",
                allow_cloud=self._transport == "cloud_mqtt",
                allow_local_mqtt=False,
            )
            self.coordinator._apply_local_property_patch(  # ruff: ignore[private-member-access]
                self._device_id, {FIELD_REBOOT: 1}
            )
        except JackeryAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            raise_entity_action_error(
                self._attr_translation_key or "button_action", self._device_id, err
            )
        except ACTION_WRITE_ERRORS as err:
            raise_entity_action_error(
                self._attr_translation_key or "button_action", self._device_id, err
            )


class JackeryRefreshWeatherPlanButton(JackeryEntity, ButtonEntity):
    """Query the app weather/storm plan via ``QueryWeatherPlan``."""

    device_registry_role = "system"
    _attr_translation_key = "refresh_weather_plan"
    _attr_entity_category = EntityCategory.CONFIG
    data_sources = LAYER5_DATA_SOURCES
    command_sources: tuple[str, ...] = LAYER5_COMMAND_SOURCES

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
    ) -> None:
        """Create the button entity that triggers querying the device weather and storm.

        plan.
        """
        super().__init__(coordinator, device_id, "refresh_weather_plan")

    async def _async_run_query(self) -> None:
        """Run the default App weather query."""
        await self.coordinator._async_publish_command_ble_first(  # ruff: ignore[private-member-access]
            self._device_id,
            message_type=MQTT_MESSAGE_QUERY_WEATHER_PLAN,
            action_id=ACTION_ID_QUERY_WEATHER_PLAN,
            cmd=MQTT_CMD_QUERY_WEATHER_PLAN,
            body_fields={},
            allow_ble=True,
            allow_cloud=True,
            allow_local_mqtt=False,
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
            raise_entity_action_error(
                "refresh_weather_plan", self._device_id, "entity unavailable"
            )
        try:
            await self._async_run_query()
        except JackeryAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            raise_entity_action_error("refresh_weather_plan", self._device_id, err)
        except ACTION_WRITE_ERRORS as err:
            raise_entity_action_error("refresh_weather_plan", self._device_id, err)


class JackeryTransportWeatherPlanButton(JackeryRefreshWeatherPlanButton):
    """Read weather plans through one selected App transport."""

    def __init__(
        self, coordinator: JackerySolarVaultCoordinator, device_id: str, transport: str
    ) -> None:
        """Bind the weather query identity and availability to one layer."""
        if transport not in {"ble", "cloud_mqtt"}:
            raise ValueError("Unsupported weather-query transport")  # ruff: ignore[raise-vanilla-args]
        JackeryEntity.__init__(
            self, coordinator, device_id, f"refresh_weather_plan_{transport}"
        )
        self._transport = transport
        self.command_sources = (transport,)
        self._attr_translation_key = f"refresh_weather_plan_{transport}"

    async def _async_run_query(self) -> None:
        """Send the App weather query only through the selected layer."""
        await self.coordinator._async_publish_command_ble_first(  # ruff: ignore[private-member-access]
            self._device_id,
            message_type=MQTT_MESSAGE_QUERY_WEATHER_PLAN,
            action_id=ACTION_ID_QUERY_WEATHER_PLAN,
            cmd=MQTT_CMD_QUERY_WEATHER_PLAN,
            body_fields={},
            allow_ble=self._transport == "ble",
            allow_cloud=self._transport == "cloud_mqtt",
            allow_local_mqtt=False,
        )


class JackeryReadScheduleButton(JackeryEntity, ButtonEntity):
    """Read one app schedule bucket via ``DownloadDeviceSchedule``."""

    _attr_entity_category = EntityCategory.CONFIG
    data_sources = LAYER5_DATA_SOURCES
    command_sources: tuple[str, ...] = LAYER5_COMMAND_SOURCES

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

    async def _async_run_schedule(self) -> None:
        """Run the default App schedule read."""
        await self.coordinator.async_read_device_schedule(
            self._device_id,
            task_type=self._task_type,
            plug_sn=self._plug_sn,
            allow_ble=True,
            allow_cloud=True,
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
            raise_entity_action_error(
                str(self._attr_translation_key), self._device_id, "entity unavailable"
            )
        try:
            await self._async_run_schedule()
        except JackeryAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            raise_entity_action_error(
                str(self._attr_translation_key), self._device_id, err
            )
        except ACTION_WRITE_ERRORS as err:
            raise_entity_action_error(
                str(self._attr_translation_key), self._device_id, err
            )


class JackeryTransportReadScheduleButton(JackeryReadScheduleButton):
    """Read one schedule through a single selected App transport."""

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        config: _ReadScheduleConfig,
        plug_sn: str,
        transport: str,
    ) -> None:
        if transport not in {"ble", "cloud_mqtt"}:
            raise ValueError("Unsupported schedule-query transport")  # ruff: ignore[raise-vanilla-args]
        task_type, key, translation = config
        super().__init__(
            coordinator,
            device_id,
            config=(task_type, f"{key}_{transport}", f"{translation}_{transport}"),
            plug_sn=plug_sn,
        )
        self._transport = transport
        self.command_sources = (transport,)

    async def _async_run_schedule(self) -> None:
        """Keep the selected plug identity and send only on one layer."""
        if self._plug_sn:
            payload = (self.coordinator.data or {}).get(self._device_id, {})
            if not isinstance(payload, dict):
                raise HomeAssistantError(  # ruff: ignore[raise-vanilla-args]
                    "The selected smart plug identity is not current"
                )
            plugs = sorted_smart_plugs(payload.get(PAYLOAD_SMART_PLUGS))
            if not plugs:
                plugs = sorted_smart_plugs(
                    subdevice_accessories(payload, dev_type=SUBDEVICE_DEV_TYPE_SOCKET)
                )
            if sum(_smart_plug_device_sn(plug) == self._plug_sn for plug in plugs) != 1:
                raise HomeAssistantError(  # ruff: ignore[raise-vanilla-args]
                    "The selected smart plug identity is not current"
                )
        await self.coordinator.async_read_device_schedule(
            self._device_id,
            task_type=self._task_type,
            plug_sn=self._plug_sn,
            allow_ble=self._transport == "ble",
            allow_cloud=self._transport == "cloud_mqtt",
        )


class JackeryDeleteStormAlertButton(JackeryEntity, ButtonEntity):
    """Delete one active app storm alert via ``CancelWeatherAlert``."""

    device_registry_role = "system"
    _attr_translation_key = "delete_storm_alert"
    _attr_entity_category = EntityCategory.CONFIG
    data_sources = LAYER5_DATA_SOURCES
    command_sources: tuple[str, ...] = LAYER5_COMMAND_SOURCES
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

    async def _async_delete_alert(self) -> None:
        """Run the default App storm-alert deletion."""
        if not self._alert:
            raise HomeAssistantError("The selected storm alert is not current")  # ruff: ignore[raise-vanilla-args]
        await self.coordinator._async_publish_command_ble_first(  # ruff: ignore[private-member-access]
            self._device_id,
            message_type=MQTT_MESSAGE_CANCEL_WEATHER_ALERT,
            action_id=ACTION_ID_DELETE_STORM_ALERT,
            cmd=MQTT_CMD_NONE,
            body_fields={FIELD_ALERT_ID: self._alert_id},
            allow_ble=True,
            allow_cloud=True,
            allow_local_mqtt=False,
        )

    async def async_press(self) -> None:
        """Delete the associated storm alert through the coordinator.

        Raises:
            ConfigEntryAuthFailed: If authentication with the config entry failed
            (re-raised).
            HomeAssistantError: If an error occurs; errors that already have a
            `translation_key` are re-raised, other exceptions are converted and raised
            via the entity's `raise_entity_action_error`.
        """
        if not self.available:
            raise_entity_action_error(
                "delete_storm_alert", self._device_id, "entity unavailable"
            )
        try:
            await self._async_delete_alert()
        except JackeryAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            raise_entity_action_error("delete_storm_alert", self._device_id, err)
        except ACTION_WRITE_ERRORS as err:
            raise_entity_action_error("delete_storm_alert", self._device_id, err)


class JackeryTransportDeleteStormAlertButton(JackeryDeleteStormAlertButton):
    """Delete one currently identified storm alert through a single layer."""

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        alert_id: str,
        transport: str,
    ) -> None:
        if transport not in {"ble", "cloud_mqtt"}:
            raise ValueError("Unsupported storm-alert transport")  # ruff: ignore[raise-vanilla-args]
        JackeryEntity.__init__(
            self, coordinator, device_id, f"delete_storm_alert_{alert_id}_{transport}"
        )
        self._alert_id = alert_id
        self._transport = transport
        self.command_sources = (transport,)
        self._attr_translation_key = f"delete_storm_alert_{transport}"

    async def _async_delete_alert(self) -> None:
        """Send only when the alert identity is still in the current plan."""
        if not self._alert:
            raise HomeAssistantError("The selected storm alert is not current")  # ruff: ignore[raise-vanilla-args]
        await self.coordinator._async_publish_command_ble_first(  # ruff: ignore[private-member-access]
            self._device_id,
            message_type=MQTT_MESSAGE_CANCEL_WEATHER_ALERT,
            action_id=ACTION_ID_DELETE_STORM_ALERT,
            cmd=MQTT_CMD_NONE,
            body_fields={FIELD_ALERT_ID: self._alert_id},
            allow_ble=self._transport == "ble",
            allow_cloud=self._transport == "cloud_mqtt",
            allow_local_mqtt=False,
        )


class JackeryPortablePackBlinkButton(JackeryEntity, ButtonEntity):
    """Blink a specific portable battery pack through one App transport."""

    _attr_entity_category = EntityCategory.CONFIG
    data_sources = LAYER5_DATA_SOURCES

    def __init__(  # ruff: ignore[too-many-arguments]
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        pack_index: int,
        pack_sn: str,
        pack_key: str,
        transport: str | None = None,
    ) -> None:
        if transport not in {None, "ble", "cloud_mqtt"}:
            raise ValueError("Unsupported pack blink transport")  # ruff: ignore[raise-vanilla-args]
        key = (
            f"{pack_key}_blink"
            if transport is None
            else f"{pack_key}_blink_{transport}"
        )
        super().__init__(coordinator, device_id, key)
        self._pack_index, self._pack_sn, self._pack_key = pack_index, pack_sn, pack_key
        self._transport = transport
        self.command_sources = (
            LAYER5_COMMAND_SOURCES if transport is None else (transport,)
        )
        self._attr_translation_key = "portable_power_pack_blink"

    @property
    def device_info(self) -> DeviceInfo | ChildDeviceInfo:
        """Attach this button to the same stable pack device as its sensors."""
        child = self._pack_child_device_info()
        if child is not None:
            return child
        return DeviceInfo(identifiers={(DOMAIN, f"{self._device_id}_{self._pack_key}")})

    async def async_press(self) -> None:
        """Send the App blink body only while the same pack owns this slot."""
        revision = self.coordinator.battery_pack_identity_revision(
            self._device_id, self._pack_sn
        )

        def identity_current() -> bool:
            payload = (self.coordinator.data or {}).get(self._device_id)
            if not isinstance(payload, dict):
                return False
            packs = payload.get(PAYLOAD_BATTERY_PACKS)
            if not isinstance(packs, list) or not packs:
                packs = subdevice_accessories(
                    payload, dev_type=SUBDEVICE_DEV_TYPE_BATTERY_PACK
                )
            matches = [
                pack
                for pack in sorted_battery_pack_payloads(packs)
                if battery_pack_serial(pack) == self._pack_sn
            ]
            return (
                len(matches) == 1
                and self.coordinator.battery_pack_identity_serial(
                    self._device_id, self._pack_index
                )
                == self._pack_sn
                and self.coordinator.battery_pack_identity_revision(
                    self._device_id, self._pack_sn
                )
                == revision
            )

        if not identity_current():
            raise HomeAssistantError(  # ruff: ignore[raise-vanilla-args]
                "The selected battery pack identity is not current"
            )
        try:
            await self.coordinator._async_publish_command_ble_first(  # ruff: ignore[private-member-access]
                self._device_id,
                message_type=MQTT_MESSAGE_CONTROL_SUB_DEVICE,
                action_id=ACTION_ID_PORTABLE_POWER_PACK_BLINK,
                cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[
                    ACTION_ID_PORTABLE_POWER_PACK_BLINK
                ],
                body_fields={FIELD_SN: self._pack_sn, "blink": 1},
                allow_ble=self._transport in {None, "ble"},
                allow_cloud=self._transport in {None, "cloud_mqtt"},
                allow_local_mqtt=False,
                identity_guard=identity_current,
            )
        except JackeryAuthError as err:
            raise ConfigEntryAuthFailed from err
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            raise_entity_action_error(
                self._attr_translation_key or "button_action", self._device_id, err
            )
        except ACTION_WRITE_ERRORS as err:
            raise_entity_action_error(
                self._attr_translation_key or "button_action", self._device_id, err
            )
