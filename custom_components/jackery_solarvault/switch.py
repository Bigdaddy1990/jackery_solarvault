"""Switch platform for Jackery SolarVault writable controls.

Description-driven entities using central descriptions package with HA-standard
value_fn delegation. Inline helpers removed; all is_on/setter logic lives in
descriptions/switch.py.
"""

import logging
from typing import TYPE_CHECKING, Any, NoReturn

from homeassistant.components.switch import SwitchEntity
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo

from .client import JackeryAuthError
from .const import (
    ACTION_ID_PORTABLE_DISCHARGE_MEMORY,
    ACTION_ID_PORTABLE_LIGHT,
    ACTION_ID_PORTABLE_OUTPUT_AC,
    ACTION_ID_PORTABLE_OUTPUT_AC240,
    ACTION_ID_PORTABLE_OUTPUT_DC,
    ACTION_ID_PORTABLE_OUTPUT_DC_CAR,
    ACTION_ID_PORTABLE_OUTPUT_DC_USB,
    ACTION_ID_PORTABLE_OUTPUT_PRIORITY_SWITCH,
    ACTION_ID_PORTABLE_SUPER_CHARGE,
    DOMAIN,
    FIELD_AUTO_STANDBY,
    FIELD_COMM_MODE,
    FIELD_COMM_STATE,
    FIELD_CONTROL_ALLOWED,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_SN,
    FIELD_DEV_ID,
    FIELD_DEV_SN,
    FIELD_ID,
    FIELD_IDX,
    FIELD_IS_AUTO_STANDBY,
    FIELD_IS_CLOUD,
    FIELD_IS_FOLLOW_METER_PW,
    FIELD_NM,
    FIELD_OFF_GRID_DOWN,
    FIELD_PC,
    FIELD_PR,
    FIELD_SCAN_NAME,
    FIELD_SN,
    FIELD_SOCKET_PRIORITY,
    FIELD_SPH,
    FIELD_SPH_PC,
    FIELD_SW,
    FIELD_SWITCH_STATE,
    FIELD_SYS_SWITCH,
    FIELD_VERSION,
    FIELD_WNAME,
    FIELD_WPS,
    MANUFACTURER,
    PAYLOAD_CIRCUIT_PROPERTY,
    PAYLOAD_SMART_PLUGS,
    SUBDEVICE_DEV_TYPE_BREAKER,
    SUBDEVICE_DEV_TYPE_SOCKET,
)
from .coordinator import ACTION_WRITE_ERRORS, subdevice_accessories
from .descriptions import SWITCH_DESCRIPTIONS, JackerySwitchDescription
from .descriptions.switch import (
    _set_auto_standby,
    _set_eps,
    _set_follow_meter,
    _set_off_grid_shutdown,
    _set_portable_ac240_output,
    _set_portable_ac_output,
    _set_portable_dc_car_output,
    _set_portable_dc_output,
    _set_portable_dc_usb_output,
    _set_portable_discharge_memory,
    _set_portable_light,
    _set_portable_output_priority_switch,
    _set_portable_super_charge,
    _set_standby,
    _set_storm_warning,
    _set_third_party_mqtt_enabled,
)
from .entity import (
    ALL_LIVE_DATA_SOURCES,
    HTTP_AND_LAYER5_COMMAND_SOURCES,
    HTTP_COMMAND_SOURCES,
    HTTP_DATA_SOURCES,
    LAYER5_COMMAND_SOURCES,
    LAYER5_DATA_SOURCES,
    JackeryEntity,
    payload_properties_for_sources,
)
from .util import (
    append_unique_entity,
    circuit_id,
    coordinator_entity_signature,
    first_nonblank_int,
    is_portable_payload as _is_portable_payload,
    safe_bool,
    smart_plug_serial,
    sorted_circuits,
    sorted_smart_plugs,
    stable_subdevice_key,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import JackeryConfigEntry
    from .coordinator import JackerySolarVaultCoordinator

__all__ = [
    "ACTION_ID_PORTABLE_DISCHARGE_MEMORY",
    "ACTION_ID_PORTABLE_LIGHT",
    "ACTION_ID_PORTABLE_OUTPUT_AC",
    "ACTION_ID_PORTABLE_OUTPUT_AC240",
    "ACTION_ID_PORTABLE_OUTPUT_DC",
    "ACTION_ID_PORTABLE_OUTPUT_DC_CAR",
    "ACTION_ID_PORTABLE_OUTPUT_DC_USB",
    "ACTION_ID_PORTABLE_OUTPUT_PRIORITY_SWITCH",
    "ACTION_ID_PORTABLE_SUPER_CHARGE",
    "HTTP_DATA_SOURCES",
    "JackeryDescriptionSwitch",
    "JackerySwitchDescription",
    "_set_auto_standby",
    "_set_eps",
    "_set_follow_meter",
    "_set_off_grid_shutdown",
    "_set_portable_ac240_output",
    "_set_portable_ac_output",
    "_set_portable_dc_car_output",
    "_set_portable_dc_output",
    "_set_portable_dc_usb_output",
    "_set_portable_discharge_memory",
    "_set_portable_light",
    "_set_portable_output_priority_switch",
    "_set_portable_super_charge",
    "_set_standby",
    "_set_storm_warning",
    "_set_third_party_mqtt_enabled",
]

# Write platform: writes go to the cloud and to MQTT. Serializing keeps the
# queue depth on the broker bounded and prevents reordering of
# DevicePropertyChange commands.
PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)


def _standby_is_on(
    raw: bool | float | str | None,
) -> bool | None:
    """Convert a raw autoStandby payload value into an on/off state."""
    if raw is None:
        return None
    parsed = first_nonblank_int(raw)
    if parsed is None:
        return safe_bool(raw)
    return parsed == 1


class JackerySwitch(JackeryEntity, SwitchEntity):
    """Generic description-driven Jackery switch."""

    _attr_has_entity_name = True

    entity_description: JackerySwitchDescription

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        description: JackerySwitchDescription,
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description
        self.device_registry_role = description.device_registry_role

    def _raise_action_error(self, error: object) -> NoReturn:
        """Raise a translatable HA action error for this switch."""
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="entity_action_failed",
            translation_placeholders={
                "entity": self.entity_description.key,
                "device_id": self._device_id,
                "error": str(error),
            },
        )

    @property
    def is_on(self) -> bool | None:
        """The entity's current state - delegates to description value_fn."""
        return self.entity_description.value_fn(self)

    async def async_turn_on(self, **kwargs: object) -> None:
        """Turn this switch on."""
        if self.entity_description.setter_fn is None:
            self._raise_action_error("entity is not writable")
        try:
            await self.entity_description.setter_fn(
                self.coordinator,
                self._device_id,
                True,
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

    async def async_turn_off(self, **kwargs: object) -> None:
        """Turn the described switch off for the device."""
        if self.entity_description.setter_fn is None:
            self._raise_action_error("entity is not writable")
        try:
            await self.entity_description.setter_fn(
                self.coordinator,
                self._device_id,
                False,
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


class JackerySmartPlugSwitch(JackeryEntity, SwitchEntity):
    """Writable switch for one smart-plug subdevice."""

    _attr_translation_key = "smart_plug_switch"
    data_sources: tuple[str, ...] = ALL_LIVE_DATA_SOURCES
    command_sources: tuple[str, ...] = HTTP_AND_LAYER5_COMMAND_SOURCES
    app_fields: tuple[str, ...] = (FIELD_SWITCH_STATE, FIELD_SYS_SWITCH)

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        plug_index: int,
        plug_sn: str,
        plug_key: str,
    ) -> None:
        """Create a switch entity bound to a specific smart plug."""
        super().__init__(coordinator, device_id, f"{plug_key}_switch")
        self._plug_index = plug_index
        self._plug_sn = plug_sn
        self._plug_key = plug_key
        plug = self._plug
        scan_name = str(plug.get(FIELD_SCAN_NAME) or "").lower()
        is_cloud = safe_bool(plug.get(FIELD_IS_CLOUD)) is True or scan_name.startswith(
            "shelly",
        )
        self.command_sources = (
            HTTP_COMMAND_SOURCES if is_cloud else LAYER5_COMMAND_SOURCES
        )
        # Build the per-plug device_info once at construction.
        self._attr_device_info = self._build_smart_plug_device_info(
            plug_index,
            self._plug,
            plug_key,
        )

    @property
    def _plug(self) -> dict[str, Any]:
        """Smart-plug payload matching this entity's captured serial."""
        for plug in sorted_smart_plugs(self._payload.get(PAYLOAD_SMART_PLUGS)):
            if smart_plug_serial(plug) == self._plug_sn:
                return plug
        return {}

    @property
    def is_on(self) -> bool | None:
        """Determine whether the smart plug's output is active."""
        raw = self._plug.get(FIELD_SWITCH_STATE)
        if raw is None:
            raw = self._plug.get(FIELD_SYS_SWITCH)
        return safe_bool(raw)

    @staticmethod
    def _cloud_device_id(plug: dict[str, Any]) -> str | None:
        """Return the Shelly Cloud ``deviceId`` used by the app control API."""
        raw = plug.get(FIELD_DEVICE_ID) or plug.get(FIELD_ID) or plug.get(FIELD_DEV_ID)
        if raw in {None, ""}:
            return None
        return str(raw)

    @staticmethod
    def _jackery_device_sn(plug: dict[str, Any]) -> str | None:
        """Return the real Jackery subdevice serial for local/BLE setters."""
        raw = plug.get(FIELD_DEVICE_SN) or plug.get(FIELD_DEV_SN) or plug.get(FIELD_SN)
        if raw in {None, ""}:
            return None
        return str(raw)

    def _raise_action_error(self, error: object) -> None:
        """Raise a localized error for a failed smart-plug switch action."""
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="entity_action_failed",
            translation_placeholders={
                "entity": "smart_plug_switch",
                "device_id": self._device_id,
                "error": str(error),
            },
        )

    async def _async_set_state(self, value: bool) -> None:
        """Set the linked plug state and request a coordinator refresh."""
        plug = self._plug
        plug_sn = self._jackery_device_sn(plug)
        scan_name = str(plug.get(FIELD_SCAN_NAME) or "").lower()
        is_cloud = safe_bool(plug.get(FIELD_IS_CLOUD)) is True or scan_name.startswith(
            "shelly",
        )
        if is_cloud:
            shelly_device_id = self._cloud_device_id(plug)
            if shelly_device_id is None:
                self._raise_action_error("missing Shelly deviceId")
                return
            if safe_bool(plug.get(FIELD_CONTROL_ALLOWED)) is not True:
                self._raise_action_error("Shelly control is not allowed")
                return
            write_coro = self.coordinator.async_set_shelly_cloud_switch(
                self._device_id,
                shelly_device_id=shelly_device_id,
                on=value,
            )
        else:
            if plug_sn is None:
                self._raise_action_error("missing deviceSn")
                return
            write_coro = self.coordinator.async_set_smart_plug_switch(
                self._device_id,
                plug_sn=plug_sn,
                on=value,
            )
        try:
            await write_coro
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

    async def async_turn_on(self, **kwargs: object) -> None:
        """Turn the bound smart plug on."""
        await self._async_set_state(True)

    async def async_turn_off(self, **kwargs: object) -> None:
        """Turn the smart plug off."""
        await self._async_set_state(False)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Diagnostic state attributes for the smart-plug switch."""
        attrs: dict[str, Any] = {"plug_index": self._plug_index}
        for key in (
            FIELD_DEVICE_NAME,
            FIELD_SCAN_NAME,
            FIELD_COMM_STATE,
            FIELD_COMM_MODE,
            FIELD_CONTROL_ALLOWED,
            FIELD_DEVICE_ID,
            FIELD_ID,
            FIELD_IS_CLOUD,
            FIELD_SOCKET_PRIORITY,
            FIELD_SWITCH_STATE,
            FIELD_SYS_SWITCH,
            FIELD_VERSION,
        ):
            if key in self._plug:
                attrs[key] = self._plug.get(key)
        return attrs


class JackeryBreakerSwitch(JackeryEntity, SwitchEntity):
    """Switch for a circuit breaker relay."""

    _attr_translation_key = "breaker_switch"
    data_sources = LAYER5_DATA_SOURCES
    command_sources = LAYER5_COMMAND_SOURCES
    app_fields = (FIELD_SW,)

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        breaker_index: int,
        breaker_id: str,
        breaker_key: str,
    ) -> None:
        """Initialize a circuit breaker switch."""
        super().__init__(coordinator, device_id, f"{breaker_key}_switch")
        self._breaker_index = breaker_index
        self._breaker_id = breaker_id
        self._breaker_key = breaker_key
        # Build the per-breaker device_info once at construction.
        self._attr_device_info = self._build_breaker_device_info(
            breaker_index,
            self._breaker,
            breaker_key,
        )

    @property
    def _breaker(self) -> dict[str, Any]:
        """Find the breaker payload dictionary that matches this entity's index."""
        for breaker in sorted_circuits(self._payload.get(PAYLOAD_CIRCUIT_PROPERTY)):
            if circuit_id(breaker) == self._breaker_id:
                return breaker
        return {}

    @property
    def is_on(self) -> bool | None:
        """Whether the breaker relay is closed."""
        return safe_bool(self._breaker.get(FIELD_SW))

    async def async_turn_on(self, **kwargs: object) -> None:
        """Close the breaker relay."""
        await self.coordinator.async_set_breaker_switch(
            self._device_id, self._breaker_id, True
        )

    async def async_turn_off(self, **kwargs: object) -> None:
        """Open the breaker relay."""
        await self.coordinator.async_set_breaker_switch(
            self._device_id, self._breaker_id, False
        )

    def _build_breaker_device_info(
        self,
        index: int,
        breaker: dict[str, Any],
        breaker_key: str,
    ) -> DeviceInfo:
        """Build device registry metadata for one circuit breaker."""
        base_name = (
            self._system.get(FIELD_DEVICE_NAME)
            or self._discovery.get(FIELD_DEVICE_NAME)
            or self._properties.get(FIELD_WNAME)
            or f"Jackery {self._device_id}"
        )
        name = breaker.get(FIELD_NM) or f"Sicherung {index}"
        info = DeviceInfo(
            identifiers={(DOMAIN, f"{self._device_id}_{breaker_key}")},
            manufacturer=MANUFACTURER,
            name=f"{base_name} {name}",
            model="Jackery Sicherung",
        )
        self._apply_via_device(info)
        return info

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Diagnostic state attributes for the breaker."""
        attrs: dict[str, Any] = {"breaker_index": self._breaker_index}
        for key in (
            FIELD_NM,
            FIELD_IDX,
            FIELD_PC,
            FIELD_PR,
            FIELD_SPH,
            FIELD_SPH_PC,
            FIELD_SW,
        ):
            if key in self._breaker:
                attrs[key] = self._breaker.get(key)
        return attrs


class JackerySmartPlugPrioritySwitch(JackerySmartPlugSwitch):
    """Writable priority toggle for one smart-plug subdevice."""

    _attr_translation_key = "smart_plug_priority_enabled"
    command_sources: tuple[str, ...] = LAYER5_COMMAND_SOURCES
    app_fields: tuple[str, ...] = (FIELD_SOCKET_PRIORITY,)

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        plug_index: int,
        plug_sn: str,
        plug_key: str,
    ) -> None:
        """Create a priority switch bound to one physical smart plug."""
        JackeryEntity.__init__(
            self,
            coordinator,
            device_id,
            f"{plug_key}_priority_enabled",
        )
        self._plug_index = plug_index
        self._plug_sn = plug_sn
        self._plug_key = plug_key
        self.command_sources = LAYER5_COMMAND_SOURCES
        self._attr_device_info = self._build_smart_plug_device_info(
            plug_index,
            self._plug,
            plug_key,
        )

    @property
    def is_on(self) -> bool | None:
        """Indicates whether the smart plug's priority is enabled."""
        return safe_bool(self._plug.get(FIELD_SOCKET_PRIORITY))

    def _raise_action_error(self, error: object) -> None:
        """Raise a localized error for a failed smart-plug priority action."""
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="entity_action_failed",
            translation_placeholders={
                "entity": "smart_plug_priority_enabled",
                "device_id": self._device_id,
                "error": str(error),
            },
        )

    async def _async_set_state(self, value: bool) -> None:
        """Set the smart plug's priority enabled state via the coordinator."""
        plug_sn = self._jackery_device_sn(self._plug)
        if plug_sn is None:
            self._raise_action_error("missing deviceSn")
            return
        try:
            await self.coordinator.async_set_smart_plug_priority(
                self._device_id,
                plug_sn=plug_sn,
                enabled=value,
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


# Home Assistant invokes platform setup as an awaitable callback.
_SWITCH_GATING: dict[str, Callable[[dict[str, Any], bool], bool]] = {
    "eps_output": lambda _props, _advanced: True,
    "auto_standby_set": lambda props, advanced: (
        advanced or FIELD_IS_AUTO_STANDBY in props or FIELD_AUTO_STANDBY in props
    ),
    "standby": lambda props, advanced: advanced or FIELD_AUTO_STANDBY in props,
    "follow_meter": lambda props, advanced: (
        advanced or FIELD_IS_FOLLOW_METER_PW in props
    ),
    "off_grid_shutdown": lambda props, advanced: (
        advanced or FIELD_OFF_GRID_DOWN in props
    ),
    "storm_warning": lambda props, advanced: advanced or FIELD_WPS in props,
    "third_party_mqtt_enable": lambda _props, advanced: advanced,
}


def _append_switch_entity(
    entities: list[SwitchEntity],
    seen_unique_ids: set[str],
    entity: SwitchEntity,
) -> None:
    """Append one switch only when its unique ID was not registered before."""
    append_unique_entity(entities, seen_unique_ids, entity)


def _collect_smart_plug_switches(
    coordinator: JackerySolarVaultCoordinator,
    dev_id: str,
    payload: dict[str, Any],
    entities: list[SwitchEntity],
    seen_unique_ids: set[str],
) -> None:
    """Collect relay and optional priority switches for smart plugs."""
    plugs = sorted_smart_plugs(payload.get(PAYLOAD_SMART_PLUGS))
    if not plugs:
        plugs = sorted_smart_plugs(
            subdevice_accessories(payload, dev_type=SUBDEVICE_DEV_TYPE_SOCKET)
        )
    for index, plug in enumerate(plugs, start=1):
        serial = smart_plug_serial(plug)
        if serial is None:
            continue
        key = stable_subdevice_key("smart_plug", serial, index)
        _append_switch_entity(
            entities,
            seen_unique_ids,
            JackerySmartPlugSwitch(
                coordinator,
                dev_id,
                plug_index=index,
                plug_sn=serial,
                plug_key=key,
            ),
        )
        if FIELD_SOCKET_PRIORITY in plug:
            _append_switch_entity(
                entities,
                seen_unique_ids,
                JackerySmartPlugPrioritySwitch(
                    coordinator,
                    dev_id,
                    plug_index=index,
                    plug_sn=serial,
                    plug_key=key,
                ),
            )


def _collect_breaker_switches(
    coordinator: JackerySolarVaultCoordinator,
    dev_id: str,
    payload: dict[str, Any],
    entities: list[SwitchEntity],
    seen_unique_ids: set[str],
) -> None:
    """Collect circuit-breaker relay switches."""
    breakers = sorted_circuits(payload.get(PAYLOAD_CIRCUIT_PROPERTY))
    if not breakers:
        breakers = sorted_circuits(
            subdevice_accessories(payload, dev_type=SUBDEVICE_DEV_TYPE_BREAKER)
        )
    for index, breaker in enumerate(breakers, start=1):
        breaker_id = circuit_id(breaker)
        if breaker_id is None:
            continue
        _append_switch_entity(
            entities,
            seen_unique_ids,
            JackeryBreakerSwitch(
                coordinator,
                dev_id,
                breaker_index=index,
                breaker_id=breaker_id,
                breaker_key=stable_subdevice_key("breaker", breaker_id, index),
            ),
        )


def _collect_switch_entities(
    coordinator: JackerySolarVaultCoordinator,
    seen_unique_ids: set[str],
) -> list[SwitchEntity]:
    """Collect supported main-device and accessory switches."""
    entities: list[SwitchEntity] = []
    for dev_id, payload in (coordinator.data or {}).items():
        props = payload_properties_for_sources(payload)
        is_portable = _is_portable_payload(payload, props)
        supports_advanced = coordinator.device_supports_advanced(dev_id)
        for description in SWITCH_DESCRIPTIONS:
            portable_description = description.key.startswith("portable_")
            if portable_description != is_portable:
                continue
            predicate = _SWITCH_GATING.get(description.key)
            if portable_description or (
                predicate is not None and predicate(props, supports_advanced)
            ):
                _append_switch_entity(
                    entities,
                    seen_unique_ids,
                    JackerySwitch(coordinator, dev_id, description),
                )
        if is_portable:
            continue
        _collect_smart_plug_switches(
            coordinator,
            dev_id,
            payload,
            entities,
            seen_unique_ids,
        )
        _collect_breaker_switches(
            coordinator,
            dev_id,
            payload,
            entities,
            seen_unique_ids,
        )
    return entities


async def async_setup_entry(  # ruff:ignore[unused-async]
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create and register switch entities from coordinator data."""
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _collect_entities() -> list[SwitchEntity]:
        """Collect switches from the current coordinator payload."""
        return _collect_switch_entities(coordinator, seen_unique_ids)

    last_signature: tuple[Any, ...] = ()

    @callback
    def _add_new_entities() -> None:
        """Add switches discovered after an entity-signature change."""
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


# Compatibility alias retained for callers that imported the former generic
# description-driven switch class.
JackeryDescriptionSwitch = JackerySwitch
