"""Binary sensor platform for Jackery SolarVault."""

from collections.abc import Callable
from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from . import JackeryConfigEntry
from .const import (
    FIELD_COMM_MODE,
    FIELD_COMM_STATE,
    FIELD_DEVICE_NAME,
    FIELD_ETH_PORT,
    FIELD_ONLINE_STATUS,
    FIELD_SCAN_NAME,
    FIELD_SWITCH_STATE,
    FIELD_SW_EPS_STATE,
    FIELD_SYS_SWITCH,
    FIELD_VERSION,
    PAYLOAD_SMART_PLUGS,
)
from .coordinator import JackerySolarVaultCoordinator
from .entity import JackeryEntity
from .util import (
    append_unique_entity,
    coordinator_entity_signature,
    safe_bool,
    smart_plug_serial,
    sorted_smart_plugs,
)

# Coordinator-backed read-only platform: entities never perform their own
# refresh I/O, so disable per-entity parallel update scheduling.
PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class JackeryBinaryDescription(BinarySensorEntityDescription):
    """Jackery binary description for the Jackery SolarVault entity description."""

    getter: Callable[[dict[str, Any], dict[str, Any]], Any]


# Getter receives (properties, device_meta). Field constants mirror the app/API
# payload names documented in implementation notes §2.
BINARY_DESCRIPTIONS: tuple[JackeryBinaryDescription, ...] = (
    JackeryBinaryDescription(
        key="online",
        translation_key="online",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        getter=lambda p, d: d.get(FIELD_ONLINE_STATUS),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackeryBinaryDescription(
        key="eps_active",
        translation_key="eps_active",
        device_class=BinarySensorDeviceClass.RUNNING,
        getter=lambda p, d: p.get(FIELD_SW_EPS_STATE),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
    JackeryBinaryDescription(
        key="eth_connected",
        translation_key="eth_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        getter=lambda p, d: p.get(FIELD_ETH_PORT),
        entity_category=EntityCategory.DIAGNOSTIC,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the platform from a config entry."""
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _append_unique(
        entities: list[BinarySensorEntity], entity: BinarySensorEntity
    ) -> None:
        append_unique_entity(
            entities, seen_unique_ids, entity, platform="binary_sensor", logger=_LOGGER
        )

    def _collect_entities() -> list[BinarySensorEntity]:
        entities: list[BinarySensorEntity] = []
        for dev_id, payload in (coordinator.data or {}).items():
            for desc in BINARY_DESCRIPTIONS:
                _append_unique(entities, JackeryBinarySensor(coordinator, dev_id, desc))
            for index, plug in enumerate(
                sorted_smart_plugs(payload.get(PAYLOAD_SMART_PLUGS)), start=1
            ):
                plug_sn = smart_plug_serial(plug)
                if plug_sn is None:
                    continue
                _append_unique(
                    entities,
                    JackerySmartPlugStateBinarySensor(
                        coordinator,
                        dev_id,
                        plug_index=index,
                        plug_sn=plug_sn,
                    ),
                )
        return entities

    last_signature: tuple[Any, ...] = ()

    @callback
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


class JackeryBinarySensor(JackeryEntity, BinarySensorEntity):
    """Jackery binary sensor for the Jackery SolarVault integration."""

    entity_description: JackeryBinaryDescription

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        description: JackeryBinaryDescription,
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator, device_id, description.key)
        self.entity_description = description
        self._attr_entity_registry_enabled_default = (
            description.entity_registry_enabled_default
        )

    @property
    def is_on(self) -> bool | None:
        """Return True when the entity is on."""
        return safe_bool(
            self.entity_description.getter(self._properties, self._device_meta)
        )


class JackerySmartPlugStateBinarySensor(JackeryEntity, BinarySensorEntity):
    """Current on/off state for one smart-plug subdevice."""

    _attr_translation_key = "smart_plug_switch_state"
    _attr_device_class = BinarySensorDeviceClass.POWER
    _attr_icon = "mdi:power-socket-de"

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        plug_index: int,
        plug_sn: str,
    ) -> None:
        """Initialise the entity from the coordinator, sorted index and serial."""
        super().__init__(
            coordinator, device_id, f"smart_plug_{plug_index}_switch_state"
        )
        self._plug_index = plug_index
        self._plug_sn = plug_sn
        # Build the per-plug device_info once at construction. Allocating it
        # on every state read is wasted work — HA reads the registry metadata
        # at entity-add time and merges later updates via the device registry.
        self._attr_device_info = self._build_smart_plug_device_info(
            plug_index, self._plug
        )

    @property
    def _plug(self) -> dict[str, Any]:
        # Look the plug up by its captured serial so cloud-side re-ordering of
        # the plug array cannot reassign this entity to a different device.
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

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes for the smart-plug state."""
        attrs: dict[str, Any] = {"plug_index": self._plug_index}
        for key in (
            FIELD_DEVICE_NAME,
            FIELD_SCAN_NAME,
            FIELD_COMM_STATE,
            FIELD_COMM_MODE,
            FIELD_SWITCH_STATE,
            FIELD_SYS_SWITCH,
            FIELD_VERSION,
        ):
            if key in self._plug:
                attrs[key] = self._plug.get(key)
        return attrs
