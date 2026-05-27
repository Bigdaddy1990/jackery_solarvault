"""Switch platform for Jackery SolarVault writable controls.

Description-driven entities; one generic class handles every writable
boolean control. The pattern mirrors number.py: each switch is described by
a frozen dataclass that captures payload source key(s), an optional fallback
section, an optional task-plan fallback and the coordinator setter that pushes
the new state to the cloud / MQTT command path.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import JackeryConfigEntry
from .const import (
    DOMAIN,
    FIELD_AUTO_STANDBY,
    FIELD_COMM_MODE,
    FIELD_COMM_STATE,
    FIELD_DEVICE_NAME,
    FIELD_FOLLOW_METER,
    FIELD_IS_AUTO_STANDBY,
    FIELD_IS_FOLLOW_METER_PW,
    FIELD_OFF_GRID_DOWN,
    FIELD_SCAN_NAME,
    FIELD_SOCKET_PRIORITY,
    FIELD_SW_EPS,
    FIELD_SWITCH_STATE,
    FIELD_SYS_SWITCH,
    FIELD_VERSION,
    FIELD_WPS,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_WEATHER_PLAN,
)
from .coordinator import JackerySolarVaultCoordinator
from .entity import JackeryEntity
from .util import (
    append_unique_entity,
    coordinator_entity_signature,
    safe_bool,
    smart_plug_serial,
    sorted_smart_plugs,
    task_plan_value,
)

# Limit concurrent control-write/update calls. This is a setter platform:
# writes go to the cloud and to MQTT. Serializing keeps the queue depth on
# the broker bounded and prevents reordering of `DevicePropertyChange`
# commands per HA dev guidance for write-heavy platforms.
PARALLEL_UPDATES = 1

_LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Description
# ---------------------------------------------------------------------------


def _standby_is_on(raw: Any) -> bool | None:
    """
    Convert a raw `autoStandby` payload value into an on/off boolean.
    
    Parameters:
        raw (Any): Raw payload value (typically an integer flag or other truthy/falsy representation).
    
    Returns:
        bool | None: `True` if the value represents "on" (e.g., integer `1` or otherwise truthy), `False` if it represents "off" (e.g., integer `0` or otherwise falsy), or `None` if `raw` is `None` or the state cannot be determined.
    """
    if raw is None:
        return None
    try:
        return int(raw) == 1
    except (TypeError, ValueError):
        return safe_bool(raw)


@dataclass(frozen=True, kw_only=True)
class JackerySwitchDescription(SwitchEntityDescription):
    """Describes a Jackery writable switch.

    The description captures everything that previously lived in a hand-
    written subclass:

    * ``source_keys`` — payload field(s) that hold the current state. The
      first one that is present wins.
    * ``source_section`` — payload section that owns ``source_keys`` (defaults
      to ``properties`` because most controls live there).
    * ``fallback_section`` — optional second payload section to consult before
      falling back to the task plan (used by the storm-warning switch which
      may surface ``wps`` from ``properties`` *or* ``weather_plan``).
    * ``use_task_plan_fallback`` — read ``task_plan_value`` for the same keys
      when neither section provides a value.
    * ``setter`` — coroutine that pushes the new boolean state to the cloud /
      MQTT command path.
    * ``is_on_transform`` — optional override for special-case interpretation
      of the raw value (defaults to ``safe_bool``).
    """

    source_keys: tuple[str, ...]
    source_section: str = PAYLOAD_PROPERTIES
    fallback_section: str | None = None
    use_task_plan_fallback: bool = False
    setter: (
        Callable[[JackerySolarVaultCoordinator, str, bool], Awaitable[None]] | None
    ) = None
    is_on_transform: Callable[[Any], bool | None] = safe_bool


# ---------------------------------------------------------------------------
# Setter helpers
# ---------------------------------------------------------------------------


async def _set_eps(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: bool
) -> None:
    """Toggle the EPS output on a device."""
    await coord.async_set_eps(dev_id, value)


async def _set_auto_standby(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: bool
) -> None:
    """Toggle auto-standby on a device."""
    await coord.async_set_auto_standby(dev_id, value)


async def _set_standby(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: bool
) -> None:
    """Toggle manual standby on a device."""
    await coord.async_set_standby(dev_id, value)


async def _set_follow_meter(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: bool
) -> None:
    """Toggle smart-meter following on a device."""
    await coord.async_set_follow_meter(dev_id, value)


async def _set_off_grid_shutdown(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: bool
) -> None:
    """Toggle off-grid shutdown on a device."""
    await coord.async_set_off_grid_shutdown(dev_id, value)


async def _set_storm_warning(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: bool
) -> None:
    """
    Set the storm warning state for the specified device.
    
    Parameters:
        coord (JackerySolarVaultCoordinator): Coordinator used to apply the change.
        dev_id (str): Unique identifier of the target device.
        value (bool): `True` to enable storm warning, `False` to disable it.
    """
    await coord.async_set_storm_warning(dev_id, value)


_smart_plug_serial = smart_plug_serial


# ---------------------------------------------------------------------------
# Description registry
# ---------------------------------------------------------------------------

SWITCH_DESCRIPTIONS: tuple[JackerySwitchDescription, ...] = (
    JackerySwitchDescription(
        key="eps_output",
        translation_key="eps_output",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:power-plug",
        source_keys=(FIELD_SW_EPS,),
        setter=_set_eps,
    ),
    JackerySwitchDescription(
        key="auto_standby_set",
        translation_key="auto_standby_set",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:power-sleep",
        source_keys=(FIELD_IS_AUTO_STANDBY,),
        use_task_plan_fallback=True,
        setter=_set_auto_standby,
    ),
    JackerySwitchDescription(
        key="standby",
        translation_key="standby",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:power-sleep",
        source_keys=(FIELD_AUTO_STANDBY,),
        setter=_set_standby,
        is_on_transform=_standby_is_on,
    ),
    JackerySwitchDescription(
        key="follow_meter",
        translation_key="follow_meter",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:gauge",
        source_keys=(FIELD_IS_FOLLOW_METER_PW, FIELD_FOLLOW_METER),
        use_task_plan_fallback=True,
        setter=_set_follow_meter,
    ),
    JackerySwitchDescription(
        key="off_grid_shutdown",
        translation_key="off_grid_shutdown",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:power-off",
        source_keys=(FIELD_OFF_GRID_DOWN,),
        use_task_plan_fallback=True,
        setter=_set_off_grid_shutdown,
    ),
    JackerySwitchDescription(
        key="storm_warning",
        translation_key="storm_warning",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:weather-lightning-rainy",
        source_keys=(FIELD_WPS,),
        fallback_section=PAYLOAD_WEATHER_PLAN,
        use_task_plan_fallback=True,
        setter=_set_storm_warning,
    ),
)


# ---------------------------------------------------------------------------
# Generic entity
# ---------------------------------------------------------------------------


class JackeryDescriptionSwitch(JackeryEntity, SwitchEntity):
    """Generic description-driven Jackery switch."""

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

    def _raise_action_error(self, error: object) -> None:
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
        """Return True when the entity is on."""
        description = self.entity_description
        section = self._payload.get(description.source_section) or {}
        raw: Any = None
        for key in description.source_keys:
            value = section.get(key)
            if value is not None:
                raw = value
                break
        if raw is None and description.fallback_section is not None:
            fallback = self._payload.get(description.fallback_section) or {}
            for key in description.source_keys:
                value = fallback.get(key)
                if value is not None:
                    raw = value
                    break
        if raw is None and description.use_task_plan_fallback:
            raw = task_plan_value(self._task_plan, *description.source_keys)
        if raw is None:
            return None
        return description.is_on_transform(raw)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the entity on."""
        if self.entity_description.setter is None:
            return
        try:
            await self.entity_description.setter(
                self.coordinator, self._device_id, True
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

    async def async_turn_off(self, **kwargs: Any) -> None:
        """
        Turn the described switch off for the device.
        
        Raises:
            ConfigEntryAuthFailed: if authentication for the config entry failed (re-raised).
            HomeAssistantError: re-raised if it contains a `translation_key`; otherwise a localized action `HomeAssistantError` describing the failure is raised for other errors.
        """
        if self.entity_description.setter is None:
            return
        try:
            await self.entity_description.setter(
                self.coordinator, self._device_id, False
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


class JackerySmartPlugSwitch(JackeryEntity, SwitchEntity):
    """Writable switch for one smart-plug subdevice."""

    _attr_translation_key = "smart_plug_switch"
    _attr_icon = "mdi:power-socket-de"

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        plug_index: int,
        plug_sn: str,
    ) -> None:
        """
        Initialize the smart-plug switch entity for a specific plug.
        
        Parameters:
            plug_index (int): 1-based index of the smart plug within the device's sorted plug list.
            plug_sn (str): Smart plug serial number used to bind the entity to the physical plug.
        """
        super().__init__(coordinator, device_id, f"smart_plug_{plug_index}_switch")
        self._plug_index = plug_index
        self._plug_sn = plug_sn
        # Build the per-plug device_info once at construction (see PROTOCOL §8
        # and binary_sensor.py for the rationale).
        self._attr_device_info = self._build_smart_plug_device_info(
            plug_index, self._plug
        )

    @property
    def _plug(self) -> dict[str, Any]:
        # Look up by captured serial; cloud-side re-ordering of the plug
        # array must not switch this entity to a different physical plug.
        """
        Return the smart-plug payload that matches this entity's captured serial.
        
        Searches the sorted list of smart-plug payloads for a plug whose serial equals the entity's stored `_plug_sn`.
        
        Returns:
            dict: The payload dictionary for the matching smart plug, or an empty dict if no matching plug is found.
        """
        for plug in sorted_smart_plugs(self._payload.get(PAYLOAD_SMART_PLUGS)):
            if smart_plug_serial(plug) == self._plug_sn:
                return plug
        return {}

    @property
    def is_on(self) -> bool | None:
        """
        Determine whether the smart plug's output is active.
        
        Checks the plug's reported switch state, falling back to the system switch if the primary field is missing.
        
        Returns:
            bool | None: `True` if the plug reports active output, `False` if it reports inactive, `None` if the state is unavailable.
        """
        raw = self._plug.get(FIELD_SWITCH_STATE)
        if raw is None:
            raw = self._plug.get(FIELD_SYS_SWITCH)
        return safe_bool(raw)

    def _raise_action_error(self, error: object) -> None:
        """
        Raise a localized HomeAssistantError describing a failed action for this smart-plug switch.
        
        Raises a HomeAssistantError with translation_domain=DOMAIN and translation_key="entity_action_failed".
        The error's translation placeholders include "entity" = "smart_plug_switch", "device_id" = this entity's device id, and "error" = str(error).
        
        Parameters:
            error (object): The underlying error or message to include in the translation placeholders.
        
        Raises:
            HomeAssistantError: Always raised to surface a translated action-failed message to the caller.
        """
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
        """
        Apply the on/off state to the associated smart plug and request a coordinator refresh.
        
        Parameters:
            value (bool): True to turn the smart plug on, False to turn it off.
        
        Raises:
            ConfigEntryAuthFailed: If the coordinator reports an authentication failure (re-raised).
            HomeAssistantError: If an action-related error occurs; errors with a `translation_key` are re-raised, other errors are converted to a translated action error via the entity's error helper.
        """
        plug_sn = _smart_plug_serial(self._plug)
        if plug_sn is None:
            self._raise_action_error("missing deviceSn")
            return
        try:
            await self.coordinator.async_set_smart_plug_switch(
                self._device_id,
                plug_sn=plug_sn,
                on=value,
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

    async def async_turn_on(self, **kwargs: Any) -> None:
        """
        Turn this smart-plug entity on.
        """
        await self._async_set_state(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """
        Turn the smart plug off.
        """
        await self._async_set_state(False)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """
        Provide diagnostic state attributes for the smart-plug switch.
        
        Always includes `plug_index`; additionally includes any of the following plug-specific fields
        if present: `deviceName`, `scanName`, `commState`, `commMode`, `socketPriority`, `switchState`,
        `sysSwitch`, `version`.
        
        Returns:
            dict[str, Any]: Mapping of extra state attributes for the entity.
        """
        attrs: dict[str, Any] = {"plug_index": self._plug_index}
        for key in (
            FIELD_DEVICE_NAME,
            FIELD_SCAN_NAME,
            FIELD_COMM_STATE,
            FIELD_COMM_MODE,
            FIELD_SOCKET_PRIORITY,
            FIELD_SWITCH_STATE,
            FIELD_SYS_SWITCH,
            FIELD_VERSION,
        ):
            if key in self._plug:
                attrs[key] = self._plug.get(key)
        return attrs


class JackerySmartPlugPrioritySwitch(JackerySmartPlugSwitch):
    """Writable priority toggle for one smart-plug subdevice."""

    _attr_translation_key = "smart_plug_priority_enabled"
    _attr_icon = "mdi:priority-high"

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        plug_index: int,
        plug_sn: str,
    ) -> None:
        """
        Create a smart-plug priority switch entity for the given device using the plug's index and serial.
        
        Parameters:
            coordinator (JackerySolarVaultCoordinator): Coordinator owning device data and actions.
            device_id (str): The parent device identifier.
            plug_index (int): One-based index of the smart plug within the device's sorted smart-plug list.
            plug_sn (str): Serial number of the targeted smart plug used to match its payload across updates.
        """
        JackeryEntity.__init__(
            self,
            coordinator,
            device_id,
            f"smart_plug_{plug_index}_priority_enabled",
        )
        self._plug_index = plug_index
        self._plug_sn = plug_sn

    @property
    def is_on(self) -> bool | None:
        """
        Determine whether the smart-plug's priority is enabled.
        
        Returns:
            `true` if the plug's `socketPriority` indicates enabled, `false` if it indicates disabled, `None` if the value is absent.
        """
        return safe_bool(self._plug.get(FIELD_SOCKET_PRIORITY))

    def _raise_action_error(self, error: object) -> None:
        """
        Raise a localized Home Assistant error indicating the smart-plug priority action failed.
        
        Parameters:
            error (object): The original error or message to include in the translation placeholders.
        
        Raises:
            HomeAssistantError: Error with translation_key "entity_action_failed" and placeholders
            for "entity" ("smart_plug_priority_enabled"), "device_id", and "error".
        """
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
        """
        Enable or disable the smart-plug's priority setting.
        
        Parameters:
            value (bool): True to enable priority, False to disable.
        
        Raises:
            ConfigEntryAuthFailed: If the config entry authentication has failed (re-raised).
            HomeAssistantError: If the action fails for other reasons; raised as a localized action error.
        """
        plug_sn = _smart_plug_serial(self._plug)
        if plug_sn is None:
            self._raise_action_error("missing deviceSn")
            return
        try:
            await self.coordinator.async_set_smart_plug_priority(
                self._device_id,
                plug_sn=plug_sn,
                enabled=value,
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


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """
    Create and register switch entities for devices and smart plugs from coordinator data.
    
    Reads the coordinator's payloads to instantiate description-driven switches and per-smart-plug switches (including priority switches when present), avoids duplicate unique IDs, and gates creation of certain description-driven switches by observed properties or device advanced-capability support. Adds discovered entities via the provided callback and registers a listener that re-evaluates entities when the coordinator data signature changes to add new entities only when the signature differs.
    """
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _append_unique(entities: list[SwitchEntity], entity: SwitchEntity) -> None:
        """
        Append `entity` to `entities` if its unique id has not been seen for the switch platform.
        
        Parameters:
            entities (list[SwitchEntity]): Mutable list to which the entity will be appended when unique.
            entity (SwitchEntity): Entity to add if its unique id has not already been recorded.
        """
        append_unique_entity(
            entities, seen_unique_ids, entity, platform="switch", logger=_LOGGER
        )

    # PROTOCOL.md §2/§4 documents SolarVault advanced controls as app
    # state plus MQTT command paths. Create those entities eagerly for known
    # SolarVault devices; otherwise gate them by the observed property keys.
    gating: dict[str, Callable[[dict[str, Any], bool], bool]] = {
        "eps_output": lambda props, _adv: FIELD_SW_EPS in props,
        "auto_standby_set": lambda props, adv: (
            adv or FIELD_IS_AUTO_STANDBY in props or FIELD_AUTO_STANDBY in props
        ),
        "standby": lambda props, adv: adv or FIELD_AUTO_STANDBY in props,
        "follow_meter": lambda props, adv: adv or FIELD_IS_FOLLOW_METER_PW in props,
        "off_grid_shutdown": lambda props, adv: adv or FIELD_OFF_GRID_DOWN in props,
        "storm_warning": lambda props, adv: adv or FIELD_WPS in props,
    }

    def _collect_entities() -> list[SwitchEntity]:
        """
        Builds the list of switch entities to register for all devices present in the coordinator data.
        
        The returned list contains description-driven Jackery switches and per-smart-plug switch entities (including priority switches when applicable). Entities that cannot be mapped to a valid smart-plug serial are omitted.
        
        Returns:
            list[SwitchEntity]: Switch entity instances to add for the current coordinator dataset.
        """
        entities: list[SwitchEntity] = []
        for dev_id, payload in (coordinator.data or {}).items():
            props = payload.get(PAYLOAD_PROPERTIES) or {}
            supports_advanced = coordinator.device_supports_advanced(dev_id)
            for description in SWITCH_DESCRIPTIONS:
                predicate = gating.get(description.key)
                if predicate is not None and predicate(props, supports_advanced):
                    _append_unique(
                        entities,
                        JackeryDescriptionSwitch(coordinator, dev_id, description),
                    )
            for index, plug in enumerate(
                sorted_smart_plugs(payload.get(PAYLOAD_SMART_PLUGS)), start=1
            ):
                plug_sn = smart_plug_serial(plug)
                if plug_sn is None:
                    continue
                _append_unique(
                    entities,
                    JackerySmartPlugSwitch(
                        coordinator,
                        dev_id,
                        plug_index=index,
                        plug_sn=plug_sn,
                    ),
                )
                if FIELD_SOCKET_PRIORITY in plug:
                    _append_unique(
                        entities,
                        JackerySmartPlugPrioritySwitch(
                            coordinator,
                            dev_id,
                            plug_index=index,
                            plug_sn=plug_sn,
                        ),
                    )
        return entities

    last_signature: tuple[Any, ...] = ()

    def _add_new_entities() -> None:
        """
        Add any newly available switch entities when the coordinator's entity signature changes.
        
        Compares the current coordinator entity signature with the previously recorded signature; if different, collects entities and registers them by calling `async_add_entities`. Updates the stored signature to the current value.
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
