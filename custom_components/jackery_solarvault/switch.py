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
    """Treat the raw autoStandby integer flag as on=1 / off=0."""
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
        """Turn the entity off."""
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
        """Initialise the smart-plug switch from its sorted index and serial."""
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
        """Raise a translatable HA action error for this switch."""
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
        """Set the smart-plug state through ControlSubDevice."""
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
        """Return diagnostic attributes for the smart-plug switch."""
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
        """Initialise the smart-plug priority switch from its sorted index/serial."""
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
        """Raise a translatable HA action error for this switch."""
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
        """Set smart-plug priority through ControlSubDevice."""
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
    """Create description-driven switch entities."""
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _append_unique(entities: list[SwitchEntity], entity: SwitchEntity) -> None:
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
