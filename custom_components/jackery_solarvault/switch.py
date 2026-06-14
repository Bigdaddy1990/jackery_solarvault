"""Switch platform for Jackery SolarVault writable controls.

Description-driven entities; one generic class handles every writable
boolean control. The pattern mirrors number.py: each switch is described by
a frozen dataclass that captures payload source key(s), an optional fallback
section, an optional task-plan fallback and the coordinator setter that pushes
the new state to the cloud / MQTT command path.
"""

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from .const import (
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
    FIELD_FOLLOW_METER,
    FIELD_ID,
    FIELD_IS_AUTO_STANDBY,
    FIELD_IS_CLOUD,
    FIELD_IS_FOLLOW_METER_PW,
    FIELD_OFF_GRID_DOWN,
    FIELD_SCAN_NAME,
    FIELD_SN,
    FIELD_SOCKET_PRIORITY,
    FIELD_SWITCH_STATE,
    FIELD_SW_EPS,
    FIELD_SYS_SWITCH,
    FIELD_THIRD_PARTY_MQTT_ENABLE,
    FIELD_VERSION,
    FIELD_WPS,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
    PAYLOAD_WEATHER_PLAN,
)
from .entity import JackeryEntity
from .entity_contract import (
    DEFAULT_LIVE_SOURCES,
    DEFAULT_NULL_SEMANTICS,
)
from .util import (
    append_unique_entity,
    coordinator_entity_signature,
    safe_bool,
    smart_plug_serial,
    sorted_smart_plugs,
    stable_subdevice_key,
    task_plan_value,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

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


# ---------------------------------------------------------------------------
# Description
# ---------------------------------------------------------------------------


def _standby_is_on(
    raw: Any,  # noqa: ANN401
) -> bool | None:  # arbitrary payload value, coerced at runtime
    """Convert a raw autoStandby payload value into an on/off state.

    Parameters:
        raw (Any): Raw payload value from the device; may be None, a number, or another truthy/falsey representation.

    Returns:
        `True` if the value represents on (for example, integer `1` or an equivalent truthy representation), `False` if the value represents off (for example, integer `0` or an equivalent falsey representation), or `None` if `raw` is `None` or the state cannot be determined.
    """
    if raw is None:
        return None
    try:
        return int(raw) == 1
    except ValueError:
        return safe_bool(raw)
    except TypeError:
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
    smali_field: str | None = None
    data_sources: tuple[str, ...] = DEFAULT_LIVE_SOURCES
    null_semantics: str = DEFAULT_NULL_SEMANTICS
    recorder_allowed: bool = True
    ha_derived: bool = False


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
    """Set the storm warning enabled state for the device identified by dev_id.

    Parameters:
        dev_id (str): Target device identifier.
        value (bool): `True` to enable storm warning, `False` to disable it.
    """
    await coord.async_set_storm_warning(dev_id, value)


async def _set_third_party_mqtt_enabled(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: bool
) -> None:
    """Toggle the app third-party MQTT bridge using the current config fields."""
    await coord.async_update_third_party_mqtt_config(
        dev_id,
        {FIELD_THIRD_PARTY_MQTT_ENABLE: 1 if value else 0},
    )


# --- Portable / Explorer powerstation switch setters -------------------------


async def _set_portable_dc_output(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: bool
) -> None:
    """Toggle the DC output on a portable Explorer device."""
    await coord.async_portable_toggle_output(
        dev_id, action_id=10, field="odc", enabled=value
    )


async def _set_portable_dc_usb_output(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: bool
) -> None:
    """Toggle the USB output on a portable Explorer device."""
    await coord.async_portable_toggle_output(
        dev_id, action_id=11, field="usba1", enabled=value
    )


async def _set_portable_dc_car_output(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: bool
) -> None:
    """Toggle the car (DC cigarette) output on a portable Explorer device."""
    await coord.async_portable_toggle_output(
        dev_id, action_id=12, field="idc", enabled=value
    )


async def _set_portable_ac_output(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: bool
) -> None:
    """Toggle the AC output on a portable Explorer device."""
    await coord.async_portable_toggle_output(
        dev_id, action_id=13, field="oac", enabled=value
    )


async def _set_portable_ac240_output(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: bool
) -> None:
    """Toggle the AC240 output on a portable Explorer device."""
    await coord.async_portable_toggle_output(
        dev_id, action_id=14, field="oac2", enabled=value
    )


async def _set_portable_light(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: bool
) -> None:
    """Toggle the LED light on a portable Explorer device."""
    await coord.async_portable_toggle_output(
        dev_id, action_id=17, field="lm", enabled=value
    )


async def _set_portable_screen(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: bool
) -> None:
    """Toggle the display screen on a portable Explorer device."""
    await coord.async_portable_toggle_output(
        dev_id, action_id=18, field="ss", enabled=value
    )


async def _set_portable_super_charge(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: bool
) -> None:
    """Enable/disable super charge mode on a portable Explorer device."""
    await coord.async_portable_toggle_output(
        dev_id, action_id=23, field="sfc", enabled=value
    )


async def _set_portable_energy_saving(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: bool
) -> None:
    """Enable/disable energy saving mode on a portable Explorer device."""
    await coord.async_portable_toggle_output(
        dev_id, action_id=20, field="ec", enabled=value
    )


async def _set_portable_output_priority_switch(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: bool
) -> None:
    """Toggle the output priority switch on a portable Explorer device."""
    await coord.async_portable_toggle_output(
        dev_id, action_id=47, field="pss", enabled=value
    )


async def _set_portable_discharge_memory(
    coord: JackerySolarVaultCoordinator, dev_id: str, value: bool
) -> None:
    """Enable/disable discharge memory on a portable Explorer device."""
    await coord.async_portable_toggle_output(
        dev_id, action_id=53, field="dl", enabled=value
    )


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
    JackerySwitchDescription(
        key="third_party_mqtt_enable",
        translation_key="third_party_mqtt_enable",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:mqtt",
        source_keys=(FIELD_THIRD_PARTY_MQTT_ENABLE,),
        source_section=PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
        setter=_set_third_party_mqtt_enabled,
    ),
    # --- Portable / Explorer powerstation switches ---
    JackerySwitchDescription(
        key="portable_dc_output",
        translation_key="portable_dc_output",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:power-plug",
        source_keys=("odc",),
        setter=_set_portable_dc_output,
    ),
    JackerySwitchDescription(
        key="portable_usb_output",
        translation_key="portable_usb_output",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:usb",
        source_keys=("usba1",),
        setter=_set_portable_dc_usb_output,
    ),
    JackerySwitchDescription(
        key="portable_car_output",
        translation_key="portable_car_output",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:car",
        source_keys=("idc",),
        setter=_set_portable_dc_car_output,
    ),
    JackerySwitchDescription(
        key="portable_ac_output",
        translation_key="portable_ac_output",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:power-plug-battery",
        source_keys=("oac",),
        setter=_set_portable_ac_output,
    ),
    JackerySwitchDescription(
        key="portable_ac240_output",
        translation_key="portable_ac240_output",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:power-plug-battery",
        source_keys=("oac2",),
        setter=_set_portable_ac240_output,
    ),
    JackerySwitchDescription(
        key="portable_light",
        translation_key="portable_light",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:lightbulb",
        source_keys=("lm",),
        setter=_set_portable_light,
    ),
    JackerySwitchDescription(
        key="portable_screen",
        translation_key="portable_screen",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:monitor",
        source_keys=("ss",),
        setter=_set_portable_screen,
    ),
    JackerySwitchDescription(
        key="portable_super_charge",
        translation_key="portable_super_charge",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:flash",
        source_keys=("sfc",),
        setter=_set_portable_super_charge,
    ),
    JackerySwitchDescription(
        key="portable_energy_saving",
        translation_key="portable_energy_saving",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:leaf",
        source_keys=("ec",),
        setter=_set_portable_energy_saving,
    ),
    JackerySwitchDescription(
        key="portable_output_priority_switch",
        translation_key="portable_output_priority_switch",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:sort-bool-descending",
        source_keys=("pss",),
        setter=_set_portable_output_priority_switch,
    ),
    JackerySwitchDescription(
        key="portable_discharge_memory",
        translation_key="portable_discharge_memory",
        entity_category=EntityCategory.CONFIG,
        icon="mdi:battery-arrow-down",
        source_keys=("dl",),
        setter=_set_portable_discharge_memory,
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
        """Determine the switch's current on/off state from the entity description and available payload data.

        Checks in order: the description's `source_section` for the first non-`None` `source_keys` value, the optional `fallback_section`, and the task-plan fallback when `use_task_plan_fallback` is enabled. If no value is found the state is unknown.

        Returns:
            `True` if the switch is on, `False` if the switch is off, `None` if the state cannot be determined.
        """
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
        """Turn this switch on.

        If the entity is writable, requests the configured setter to apply the on state and refreshes coordinator data.

        Raises:
            ConfigEntryAuthFailed: if the config entry authentication has failed.
            HomeAssistantError: when the action fails; errors that include a `translation_key` are propagated, other failures are converted to a translated entity action failure.
        """
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
        except Exception as err:  # noqa: BLE001
            self._raise_action_error(err)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the described switch off for the device.

        If the description has no setter this is a no-op.

        Raises:
            ConfigEntryAuthFailed: re-raised when authentication for the config entry failed.
            HomeAssistantError: re-raised when the caught error contains a `translation_key`; otherwise a translated `HomeAssistantError` describing the action failure is raised.
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
        except Exception as err:  # noqa: BLE001
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
        plug_key: str,
    ) -> None:
        """Create a switch entity bound to a specific smart plug.

        Binds the entity to a physical plug by capturing the plug's 1-based index and serial number so the entity remains associated with the same plug across payload reorderings, and constructs the plug-specific `device_info` used by Home Assistant.

        Parameters:
            plug_index (int): 1-based index of the smart plug within the device's sorted plug list.
            plug_sn (str): Smart plug serial number used to identify and bind to the physical plug.
        """
        super().__init__(coordinator, device_id, f"{plug_key}_switch")
        self._plug_index = plug_index
        self._plug_sn = plug_sn
        self._plug_key = plug_key
        # Build the per-plug device_info once at construction (see PROTOCOL §8
        # and binary_sensor.py for the rationale).
        self._attr_device_info = self._build_smart_plug_device_info(
            plug_index, self._plug, plug_key
        )

    @property
    def _plug(self) -> dict[str, Any]:
        # Look up by captured serial; cloud-side re-ordering of the plug
        # array must not switch this entity to a different physical plug.
        """Get the smart-plug payload that matches this entity's captured serial.

        Returns:
            dict[str, Any]: The payload dictionary for the matching smart plug, or an empty dict if no matching plug is found.
        """
        for plug in sorted_smart_plugs(self._payload.get(PAYLOAD_SMART_PLUGS)):
            if smart_plug_serial(plug) == self._plug_sn:
                return plug
        return {}

    @property
    def is_on(self) -> bool | None:
        """Determine whether the smart plug's output is active.

        Returns:
            True if the plug reports active output, False if it reports inactive, None if the state is unavailable.
        """
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
        """Raise a localized HomeAssistantError indicating the smart-plug switch action failed.

        Parameters:
            error (object): Underlying error or message to include in the translation placeholders.

        Raises:
            HomeAssistantError: Always raised with `translation_domain=DOMAIN`, `translation_key="entity_action_failed"`,
            and `translation_placeholders` containing `entity="smart_plug_switch"`, `device_id=self._device_id`, and `error=str(error)`.
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
        """Set the linked smart plug's on/off state and request a coordinator refresh.

        Parameters:
            value (bool): True to turn the plug on, False to turn it off.

        Raises:
            ConfigEntryAuthFailed: Re-raised when the coordinator reports an authentication failure.
            HomeAssistantError: Re-raised if the error contains a `translation_key`; other errors are converted to a translated action error via the entity's `_raise_action_error`.
        """
        plug = self._plug
        plug_sn = self._jackery_device_sn(plug)
        scan_name = str(plug.get(FIELD_SCAN_NAME) or "").lower()
        is_cloud = safe_bool(plug.get(FIELD_IS_CLOUD)) is True or scan_name.startswith(
            "shelly"
        )
        if is_cloud:
            shelly_device_id = self._cloud_device_id(plug)
            if shelly_device_id is None:
                self._raise_action_error("missing Shelly deviceId")
                return
            if safe_bool(plug.get(FIELD_CONTROL_ALLOWED)) is not True:
                self._raise_action_error("Shelly control is not allowed")
                return
        elif plug_sn is None:
            self._raise_action_error("missing deviceSn")
            return
        try:
            if is_cloud:
                await self.coordinator.async_set_shelly_cloud_switch(
                    self._device_id,
                    shelly_device_id=shelly_device_id,
                    on=value,
                )
            else:
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
        except Exception as err:  # noqa: BLE001
            self._raise_action_error(err)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn the bound smart plug on.

        Set the smart plug's switch to the on state and request a coordinator refresh.
        """
        await self._async_set_state(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the smart plug off."""
        await self._async_set_state(False)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic state attributes for the smart-plug switch.

        Always includes `plug_index`. Additionally includes any of these plug-specific fields if present: `deviceName`, `scanName`, `commState`, `commMode`, `socketPriority`, `switchState`, `sysSwitch`, `version`.

        Returns:
            dict[str, Any]: Mapping of extra state attributes for the entity.
        """
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
        plug_key: str,
    ) -> None:
        """Create a smart-plug priority-enabled switch entity bound to a specific smart plug.

        Parameters:
            plug_index (int): 1-based position of the smart plug in the device's sorted smart-plug list.
            plug_sn (str): Serial number of the target smart plug used to reliably identify the plug across payload updates.
        """
        JackeryEntity.__init__(
            self,
            coordinator,
            device_id,
            f"{plug_key}_priority_enabled",
        )
        self._plug_index = plug_index
        self._plug_sn = plug_sn
        self._plug_key = plug_key
        self._attr_device_info = self._build_smart_plug_device_info(
            plug_index, self._plug, plug_key
        )

    @property
    def is_on(self) -> bool | None:
        """Indicates whether the smart plug's priority is enabled.

        Returns:
            `true` if the plug's `socketPriority` indicates enabled, `false` if it indicates disabled, `None` if the field is absent or unknown.
        """
        return safe_bool(self._plug.get(FIELD_SOCKET_PRIORITY))

    def _raise_action_error(self, error: object) -> None:
        """Raise a localized HomeAssistantError for failures related to the smart-plug priority action.

        The raised error uses translation_key "entity_action_failed" with translation placeholders:
        `entity="smart_plug_priority_enabled"`, `device_id` set to this entity's device id, and
        `error` set to `str(error)`.

        Parameters:
            error (object): Original error or message to include in the `error` translation placeholder.

        Raises:
            HomeAssistantError: Error with translation_key "entity_action_failed" and the placeholders described above.
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
        """Set the smart plug's priority enabled state and request a coordinator refresh.

        Parameters:
            value (bool): True to enable priority for the plug, False to disable it.

        Raises:
            ConfigEntryAuthFailed: If the config entry authentication fails (re-raised).
            HomeAssistantError: If the plug serial is missing or the update action fails; errors include translation placeholders when available.
        """
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
            await self.coordinator.async_request_refresh()
        except ConfigEntryAuthFailed:
            raise
        except HomeAssistantError as err:
            if getattr(err, "translation_key", None):
                raise
            self._raise_action_error(err)
        except Exception as err:  # noqa: BLE001
            self._raise_action_error(err)


# ---------------------------------------------------------------------------
# Setup
# ---------------------------------------------------------------------------


async def async_setup_entry(  # noqa: RUF029  # HA awaits this entry point
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Create and register switch entities for devices and smart plugs based on coordinator data.

    Discover description-driven device switches and per-smart-plug switches (including priority switches when present), avoid duplicate unique IDs, and gate creation of certain description-driven switches by observed device properties or advanced-capability support. Register a listener that re-evaluates the coordinator data signature and adds newly discovered entities only when the signature changes.
    """
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _append_unique(entities: list[SwitchEntity], entity: SwitchEntity) -> None:
        """Append `entity` to `entities` if its unique id has not been seen for the switch platform.

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
        "third_party_mqtt_enable": lambda _props, adv: adv,
    }

    def _collect_entities() -> list[SwitchEntity]:
        """Build a list of switch entities to register for every device present in the coordinator data.

        The list includes description-driven JackeryDescriptionSwitch entities and per-smart-plug entities:
        JackerySmartPlugSwitch for each smart plug with a valid serial, and JackerySmartPlugPrioritySwitch when a plug exposes priority support.
        Entities for plugs missing a serial are omitted. Deduplication is applied via the platform's unique-id tracking.

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
                plug_key = stable_subdevice_key("smart_plug", plug_sn, index)
                _append_unique(
                    entities,
                    JackerySmartPlugSwitch(
                        coordinator,
                        dev_id,
                        plug_index=index,
                        plug_sn=plug_sn,
                        plug_key=plug_key,
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
                            plug_key=plug_key,
                        ),
                    )
        return entities

    last_signature: tuple[Any, ...] = ()

    def _add_new_entities() -> None:
        """Add newly discovered switch entities when the coordinator's entity signature changes.

        If the coordinator's current entity signature differs from the last recorded signature, update the stored signature, collect entities via _collect_entities(), and call async_add_entities() with any discovered entities.
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
