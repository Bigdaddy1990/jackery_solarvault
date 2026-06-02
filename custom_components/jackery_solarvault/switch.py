"""Switch platform for Jackery SolarVault writable controls.

Description-driven entities; one generic class handles every writable
boolean control. The pattern mirrors number.py: each switch is described by
a frozen dataclass that captures payload source key(s), an optional fallback
section, an optional task-plan fallback and the coordinator setter that pushes
the new state to the cloud / MQTT command path.
"""

import logging
from collections.abc import Awaitable
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.components.switch import SwitchEntityDescription
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import JackeryConfigEntry
from .const import DOMAIN
from .const import FIELD_AUTO_STANDBY
from .const import FIELD_COMM_MODE
from .const import FIELD_COMM_STATE
from .const import FIELD_DEVICE_NAME
from .const import FIELD_FOLLOW_METER
from .const import FIELD_IS_AUTO_STANDBY
from .const import FIELD_IS_FOLLOW_METER_PW
from .const import FIELD_OFF_GRID_DOWN
from .const import FIELD_SCAN_NAME
from .const import FIELD_SOCKET_PRIORITY
from .const import FIELD_SW_EPS
from .const import FIELD_SWITCH_STATE
from .const import FIELD_SYS_SWITCH
from .const import FIELD_VERSION
from .const import FIELD_WPS
from .const import PAYLOAD_PROPERTIES
from .const import PAYLOAD_SMART_PLUGS
from .const import PAYLOAD_WEATHER_PLAN
from .coordinator import JackerySolarVaultCoordinator
from .entity import JackeryEntity
from .util import append_unique_entity
from .util import coordinator_entity_signature
from .util import safe_bool
from .util import smart_plug_serial
from .util import sorted_smart_plugs
from .util import task_plan_value

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
    """Treat the raw autoStandby integer flag as on=1 / off=0."""
    if raw is None:
        return None
    try:
        return int(raw) == 1
    except TypeError, ValueError:
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
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: bool,
) -> None:
    """Toggle the EPS output on a device."""
    await coord.async_set_eps(dev_id, value)


async def _set_auto_standby(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: bool,
) -> None:
    """Toggle auto-standby on a device."""
    await coord.async_set_auto_standby(dev_id, value)


async def _set_standby(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: bool,
) -> None:
    """Toggle manual standby on a device."""
    await coord.async_set_standby(dev_id, value)


async def _set_follow_meter(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: bool,
) -> None:
    """Toggle smart-meter following on a device."""
    await coord.async_set_follow_meter(dev_id, value)


async def _set_off_grid_shutdown(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: bool,
) -> None:
    """Toggle off-grid shutdown on a device."""
    await coord.async_set_off_grid_shutdown(dev_id, value)


async def _set_storm_warning(
    coord: JackerySolarVaultCoordinator,
    dev_id: str,
    value: bool,
) -> None:
    """Toggle storm warning on a device."""
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
        """Raise a translatable HomeAssistantError indicating the entity action failed.

        Parameters:
            error (object): The error object to include; its string form is placed into the translation placeholders.

        Raises:
            HomeAssistantError: With translation_key "entity_action_failed" and translation placeholders
            "entity" (entity key), "device_id" (the device identifier), and "error" (stringified error).
        """
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
        """Turn the entity on using the configured description setter and request a coordinator refresh.

        If no setter is configured this is a no-op. Re-raises ConfigEntryAuthFailed and re-raises HomeAssistantError instances that already include a `translation_key`; all other errors are converted into a translatable action error via `_raise_action_error`.
        """
        if self.entity_description.setter is None:
            return
        try:
            await self.entity_description.setter(
                self.coordinator,
                self._device_id,
                True,
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
        """Turn the entity off using its configured setter.

        If the description has no `setter`, this is a no-op. Otherwise the configured setter is awaited to apply the `off` state and the coordinator is asked to refresh. Authentication errors (`ConfigEntryAuthFailed`) and `HomeAssistantError` instances that include a `translation_key` are re-raised; other errors are converted into a translatable entity action error via `_raise_action_error`.
        """
        if self.entity_description.setter is None:
            return
        try:
            await self.entity_description.setter(
                self.coordinator,
                self._device_id,
                False,
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
        """Initialize the smart-plug switch entity for a specific plug.

        Parameters:
            plug_index (int): 1-based index of the plug within the device.
            plug_sn (str): Serial number of the smart plug.

        Notes:
            Builds and assigns the per-plug `device_info` at construction.
        """
        super().__init__(coordinator, device_id, f"smart_plug_{plug_index}_switch")
        self._plug_index = plug_index
        self._plug_sn = plug_sn
        # Build the per-plug device_info once at construction (see PROTOCOL §8
        # and binary_sensor.py for the rationale).
        self._attr_device_info = self._build_smart_plug_device_info(
            plug_index,
            self._plug,
        )

    @property
    def _plug(self) -> dict[str, Any]:
        # Look up by captured serial; cloud-side re-ordering of the plug
        # array must not switch this entity to a different physical plug.
        for plug in sorted_smart_plugs(self._payload.get(PAYLOAD_SMART_PLUGS)):
            if smart_plug_serial(plug) == self._plug_sn:
                return plug
        return {}

    @property
    def is_on(self) -> bool | None:
        """Return True when the smart plug reports an active output."""
        raw = self._plug.get(FIELD_SWITCH_STATE)
        if raw is None:
            raw = self._plug.get(FIELD_SYS_SWITCH)
        return safe_bool(raw)

    def _raise_action_error(self, error: object) -> None:
        """Raise a HomeAssistantError that is ready for translation when an action on the switch fails.

        This always raises HomeAssistantError with translation_domain set to DOMAIN, translation_key set to "entity_action_failed", and translation_placeholders containing:
        - "entity": "smart_plug_switch"
        - "device_id": the current entity's device id
        - "error": the stringified `error` argument

        Parameters:
            error (object): The original error object; its string representation is included in the raised error's placeholders.
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
        """Set the smart plug's on/off state and request a coordinator refresh.

        Parameters:
            value (bool): True to turn the plug on, False to turn it off.

        Raises:
            ConfigEntryAuthFailed: Re-raised when authentication with the config entry fails.
            HomeAssistantError: Raised (via the entity's action error helper) when the plug serial is missing or an unexpected error occurs while applying the state.
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
        """Turn the smart plug on."""
        await self._async_set_state(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn the smart plug off."""
        await self._async_set_state(False)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Diagnostic attributes for the smart-plug switch.

        Includes the plug index and any available per-plug fields: `deviceName`, `scanName`, `commState`, `commMode`,
        `socketPriority`, `switchState`, `sysSwitch`, and `version`. Only fields present on the current plug are included.

        Returns:
            dict[str, Any]: Mapping of attribute names to their values.
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
        """Create a smart-plug priority switch entity for a specific plug.

        Parameters:
            plug_index (int): 1-based index of the plug in the device's sorted smart-plug list.
            plug_sn (str): Serial number of the target smart plug.
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
        """Return True when smart-plug priority is enabled."""
        return safe_bool(self._plug.get(FIELD_SOCKET_PRIORITY))

    def _raise_action_error(self, error: object) -> None:
        """Raise a translatable HomeAssistantError indicating an entity action failure for the priority switch.

        Parameters:
            error (object): The original error to include in the translation placeholders.

        Raises:
            HomeAssistantError: Contains translation_key "entity_action_failed" with placeholders:
                - "entity": "smart_plug_priority_enabled"
                - "device_id": the entity's device id
                - "error": stringified `error`
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
        """Set the smart-plug's priority-enabled state.

        Attempts to apply the priority state for the plug identified by the entity and requests a coordinator refresh on success.

        Parameters:
            value (bool): True to enable priority for the smart plug, False to disable it.

        Raises:
            ConfigEntryAuthFailed: If the coordinator reports authentication failure.
            HomeAssistantError: If the plug serial is missing or the coordinator call fails (converted to a translatable action error).
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
    """Set up switch entities for devices managed by the coordinator and add them to Home Assistant.

    Collects description-driven switches and per-device smart-plug switches (including priority toggles) based on each device's payload and capability gating, ensures unique entity IDs, and calls the provided add-entities callback for any newly discovered entities. Registers a listener so the entity set is re-evaluated and new entities are added when coordinator data changes.

    Parameters:
        hass: Home Assistant instance.
        entry: Integration config entry containing the coordinator in runtime_data.
        async_add_entities: Callback used to register new SwitchEntity instances with Home Assistant.
    """
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _append_unique(entities: list[SwitchEntity], entity: SwitchEntity) -> None:
        """Append a switch entity to the list if its unique identifier has not been added before.

        Parameters:
            entities (list[SwitchEntity]): Target list to which the entity will be appended when not duplicated.
            entity (SwitchEntity): Switch entity to add; its unique identifier will be recorded to prevent future duplicates.
        """
        append_unique_entity(
            entities,
            seen_unique_ids,
            entity,
            platform="switch",
            logger=_LOGGER,
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
        """Collects switch entities for all devices present in the coordinator's data.

        Builds a list of description-driven switches (subject to the per-description gating
        predicate), a smart-plug switch for each discovered smart plug, and a smart-plug
        priority switch when the plug exposes the socket-priority field. Entities are
        de-duplicated via the module's `_append_unique` helper.

        Returns:
            list[SwitchEntity]: Discovered SwitchEntity instances for all devices; may be empty.
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
                sorted_smart_plugs(payload.get(PAYLOAD_SMART_PLUGS)),
                start=1,
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
