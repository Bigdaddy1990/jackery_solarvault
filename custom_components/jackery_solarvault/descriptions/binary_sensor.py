"""Binary sensor descriptions for Jackery SolarVault integration."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory

from ..const import (
    DEFAULT_NULL_SEMANTICS,
    FIELD_ALERT_COUNT,
    FIELD_COMM_STATE,
    FIELD_ETH_PORT,
    FIELD_ONLINE_STATUS,
    FIELD_PV1,
    FIELD_PV2,
    FIELD_PV3,
    FIELD_PV4,
    FIELD_SW_EPS_STATE,
)
from ..entity import ALL_LIVE_DATA_SOURCES, HTTP_DATA_SOURCES, property_data_sources
from ..util import safe_int

if TYPE_CHECKING:
    from collections.abc import Callable

    from ..entity import JackeryEntity


def _default_binary_value(_entity: object) -> None:
    """Return no state when a compatibility description has no reader."""
    return


@dataclass(frozen=True, kw_only=True)
class JackeryBinaryDescription(BinarySensorEntityDescription):
    """Describes a Jackery binary sensor entity."""

    value_fn: Callable[[Any], bool | None] = _default_binary_value
    getter: Callable[[dict[str, Any], dict[str, Any]], Any] | None = None
    is_on_fn: Callable[[Any], bool | None] | None = None
    smali_field: str | None = None
    app_fields: tuple[str, ...] = ()
    data_sources: tuple[str, ...] = ()
    device_registry_role: str = "head"
    null_semantics: str = DEFAULT_NULL_SEMANTICS
    recorder_allowed: bool = True
    ha_derived: bool = False
    required_property_keys: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        """Resolve property-field and source metadata."""
        if self.entity_category is EntityCategory.DIAGNOSTIC:
            object.__setattr__(self, "entity_registry_enabled_default", False)
        if self.getter is not None:
            getter = self.getter
            object.__setattr__(
                self,
                "value_fn",
                lambda entity: getter(
                    entity.merged_properties,
                    entity.device_meta,
                ),
            )
        app_fields = self.app_fields or self.required_property_keys
        object.__setattr__(self, "app_fields", app_fields)
        if not self.data_sources:
            object.__setattr__(
                self,
                "data_sources",
                property_data_sources(*app_fields, layer5_proven=True)
                if app_fields
                else HTTP_DATA_SOURCES,
            )


@dataclass(frozen=True, kw_only=True)
class JackerySubdeviceAlarmBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Jackery subdevice alarm binary sensor entity."""

    field: str
    value_fn: Callable[[Any], bool | None] = _default_binary_value
    transform: Callable[[Any], Any] = lambda x: x
    data_sources: tuple[str, ...] = ALL_LIVE_DATA_SOURCES


# ---------------------------------------------------------------------------
# Binary sensor description tuples (migrated from binary_sensor.py)
# ---------------------------------------------------------------------------


def _pv_comm_state(entity: JackeryEntity, channel: str) -> bool | None:
    """Return an MPPT channel's ``commState`` from its nested PV object."""
    pv = entity.merged_properties.get(channel)
    state = pv.get(FIELD_COMM_STATE) if isinstance(pv, dict) else None
    return None if state is None else state == 1


BINARY_SENSOR_DESCRIPTIONS: tuple[
    JackeryBinaryDescription | JackerySubdeviceAlarmBinarySensorDescription, ...
] = (
    JackeryBinaryDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="online",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="online",
        # pyrefly: ignore [unexpected-keyword]
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.device_meta.get(FIELD_ONLINE_STATUS),
    ),
    JackeryBinaryDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="eps_active",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="eps_active",
        # pyrefly: ignore [unexpected-keyword]
        device_class=BinarySensorDeviceClass.RUNNING,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.merged_properties.get(FIELD_SW_EPS_STATE),
        required_property_keys=(FIELD_SW_EPS_STATE,),
    ),
    JackeryBinaryDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="eth_connected",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="eth_connected",
        # pyrefly: ignore [unexpected-keyword]
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        # pyrefly: ignore [unexpected-keyword]
        entity_registry_enabled_default=False,
        value_fn=lambda e: e.merged_properties.get(FIELD_ETH_PORT),
        required_property_keys=(FIELD_ETH_PORT,),
    ),
    JackerySubdeviceAlarmBinarySensorDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="alarm",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="subdevice_alarm",
        # pyrefly: ignore [unexpected-keyword]
        device_class=BinarySensorDeviceClass.SAFETY,
        field=FIELD_ALERT_COUNT,
        value_fn=lambda e: (
            (count := safe_int(e.sub_device.get(FIELD_ALERT_COUNT))) is not None
            and count > 0
        ),
    ),
    JackeryBinaryDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="pv1_connected",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="pv1_connected",
        # pyrefly: ignore [unexpected-keyword]
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _pv_comm_state(e, FIELD_PV1),
        required_property_keys=(FIELD_PV1,),
    ),
    JackeryBinaryDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="pv2_connected",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="pv2_connected",
        # pyrefly: ignore [unexpected-keyword]
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _pv_comm_state(e, FIELD_PV2),
        required_property_keys=(FIELD_PV2,),
    ),
    JackeryBinaryDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="pv3_connected",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="pv3_connected",
        # pyrefly: ignore [unexpected-keyword]
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _pv_comm_state(e, FIELD_PV3),
        required_property_keys=(FIELD_PV3,),
    ),
    JackeryBinaryDescription(
        # pyrefly: ignore [unexpected-keyword]
        key="pv4_connected",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="pv4_connected",
        # pyrefly: ignore [unexpected-keyword]
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda e: _pv_comm_state(e, FIELD_PV4),
        required_property_keys=(FIELD_PV4,),
    ),
)
