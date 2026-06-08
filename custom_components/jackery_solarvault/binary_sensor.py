"""Binary sensor platform for Jackery SolarVault."""

from dataclasses import dataclass
import logging
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory

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
from .entity import JackeryEntity
from .util import (
    append_unique_entity,
    coordinator_entity_signature,
    safe_bool,
    smart_plug_serial,
    sorted_smart_plugs,
    stable_subdevice_key,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import JackeryConfigEntry
    from .coordinator import JackerySolarVaultCoordinator

# Coordinator-backed read-only platform: entities never perform their own
# refresh I/O, so disable per-entity parallel update scheduling.
PARALLEL_UPDATES = 0

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True, kw_only=True)
class JackeryBinaryDescription(BinarySensorEntityDescription):
    """Jackery binary description for the Jackery SolarVault entity description."""

    getter: Callable[[dict[str, Any], dict[str, Any]], Any]


# Getter receives (properties, device_meta). Field constants mirror the app/API
# payload names documented in PROTOCOL.md §2.
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


async def async_setup_entry(  # noqa: RUF029  # HA awaits this entry point
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up coordinator-backed binary sensor entities for a Jackery config entry and register a listener to rebuild entities when coordinator data changes.

    Discovers per-device binary sensors and per-plug smart-plug binary sensors from the coordinator data, de-duplicates entities across rebuilds, and calls the provided `async_add_entities` callback to register newly discovered entities when the coordinator's entity signature changes.
    """  # noqa: E501
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    seen_unique_ids: set[str] = set()

    def _append_unique(
        entities: list[BinarySensorEntity], entity: BinarySensorEntity
    ) -> None:
        """Add the entity to the provided list and record its unique ID if that ID has not already been seen.

        Parameters:
            entities (list[BinarySensorEntity]): List to append the entity to when its unique ID is new.
            entity (BinarySensorEntity): Binary sensor entity whose unique ID will be checked and recorded.
        """  # noqa: E501
        append_unique_entity(
            entities, seen_unique_ids, entity, platform="binary_sensor", logger=_LOGGER
        )

    def _collect_entities() -> list[BinarySensorEntity]:
        """Collect binary sensor entities for every device in the coordinator payload.

        For each device, create one JackeryBinarySensor per description in BINARY_DESCRIPTIONS and one JackerySmartPlugStateBinarySensor for each smart plug that has a serial number. Smart-plug entities capture a 1-based plug index and the plug serial to maintain stable binding across payload reorders.

        Returns:
            list[BinarySensorEntity]: Constructed binary sensor entities ready to be added.
        """  # noqa: E501
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
                plug_key = stable_subdevice_key("smart_plug", plug_sn, index)
                _append_unique(
                    entities,
                    JackerySmartPlugStateBinarySensor(
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
        """Register new binary sensor entities when the coordinator's entity signature changes.

        Compute the coordinator entity signature and, if it differs from the previously recorded signature, collect entities and register them via `async_add_entities`; update the stored signature. No action is taken when the signature is unchanged.
        """  # noqa: E501
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
            and description.entity_category != EntityCategory.DIAGNOSTIC
        )

    @property
    def is_on(self) -> bool | None:
        """Determine whether the binary sensor is currently active.

        Returns:
            bool | None: `True` if the sensor is on, `False` if the sensor is off, `None` if the state is unknown.
        """  # noqa: E501
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
        plug_key: str,
    ) -> None:
        """Create a binary sensor entity representing a specific smart plug's switch state.

        Parameters:
            coordinator (JackerySolarVaultCoordinator): Coordinator providing device payloads and updates.
            device_id (str): Identifier of the parent device this plug belongs to.
            plug_index (int): 1-based index of the plug within the device's sorted smart-plug list.
            plug_sn (str): Serial number of the smart plug used to locate the plug in payloads.

        Notes:
            Builds and stores the plug's device_info at construction so the device registry can use it when the entity is added.
        """  # noqa: E501
        super().__init__(coordinator, device_id, f"{plug_key}_switch_state")
        self._plug_index = plug_index
        self._plug_sn = plug_sn
        self._plug_key = plug_key
        # Build the per-plug device_info once at construction. Allocating it
        # on every state read is wasted work — HA reads the registry metadata
        # at entity-add time and merges later updates via the device registry.
        self._attr_device_info = self._build_smart_plug_device_info(
            plug_index, self._plug, plug_key
        )

    @property
    def _plug(self) -> dict[str, Any]:
        # Look the plug up by its captured serial so cloud-side re-ordering of
        # the plug array cannot reassign this entity to a different device.
        """Finds the smart-plug payload matching this entity's captured serial to keep the entity bound to the same physical plug if the cloud-side plug list is reordered.

        Returns:
            dict[str, Any]: The matching smart-plug dictionary from the current payload, or an empty dict if no match is found.
        """  # noqa: E501
        for plug in sorted_smart_plugs(self._payload.get(PAYLOAD_SMART_PLUGS)):
            if smart_plug_serial(plug) == self._plug_sn:
                return plug
        return {}

    @property
    def is_on(self) -> bool | None:
        """Determine whether the smart plug's power output is active.

        Checks the plug payload for switch state fields and coerces the value to a boolean.

        Returns:
            `True` if the plug reports an active output, `False` if it reports an inactive output, `None` if the state is unavailable.
        """  # noqa: E501
        raw = self._plug.get(FIELD_SWITCH_STATE)
        if raw is None:
            raw = self._plug.get(FIELD_SYS_SWITCH)
        return safe_bool(raw)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes for the smart plug, always including its 1-based `plug_index`.

        Includes any of the following fields from the matched plug payload when present: `FIELD_DEVICE_NAME`, `FIELD_SCAN_NAME`, `FIELD_COMM_STATE`, `FIELD_COMM_MODE`, `FIELD_SWITCH_STATE`, `FIELD_SYS_SWITCH`, `FIELD_VERSION`.

        Returns:
            dict[str, Any]: Mapping of attribute names to values; always contains `plug_index`.
        """  # noqa: E501
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
