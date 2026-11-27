"""Binary sensor descriptions for Jackery SolarVault integration."""

from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Any

from homeassistant.components.binary_sensor import BinarySensorEntityDescription
from homeassistant.const import EntityCategory

from ..binary_sensor_compat import BinarySensorDeviceClass
from ..const import (
    BATTERY_PASSPORT_HOME_BIT,
    BATTERY_PASSPORT_PORTABLE_BIT,
    DEFAULT_NULL_SEMANTICS,
    FIELD_ABILITY,
    FIELD_ALERT_COUNT,
    FIELD_BLS,
    FIELD_BOX_BACKUP_PERIOD_ENABLED,
    FIELD_COMM_STATE,
    FIELD_ETH_PORT,
    FIELD_IS_PACK_CONNECT,
    FIELD_OACL1,
    FIELD_OACL2,
    FIELD_ONLINE_STATUS,
    FIELD_PSS,
    FIELD_PV1,
    FIELD_PV2,
    FIELD_PV3,
    FIELD_PV4,
    FIELD_STORM,
    FIELD_SW_EPS_STATE,
    FIELD_UPS,
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

    # Home Assistant's FrozenOrThawed metaclass injects these inherited fields.
    key: str
    translation_key: str | None = None
    device_class: BinarySensorDeviceClass | None = None

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


def _box_ac_connected(entity: JackeryEntity, channel: str) -> bool | None:
    """Return the Box app's connection state for an AC channel."""
    ac = entity.merged_properties.get(channel)
    state = safe_int(ac.get("bi")) if isinstance(ac, dict) else None
    return None if state is None else state != 0


def _box_backup_period_enabled(entity: JackeryEntity) -> bool | None:
    """Return the App's explicit enabled/disabled backup-period state."""
    state = safe_int(entity.merged_properties.get(FIELD_BOX_BACKUP_PERIOD_ENABLED))
    return {0: False, 1: True}.get(state) if state is not None else None


def _app_flag(entity: JackeryEntity, field: str) -> bool | None:
    state = safe_int(entity.merged_properties.get(field))
    return {0: False, 1: True}.get(state) if state is not None else None


def _battery_passport_supported(entity: JackeryEntity) -> bool | None:
    """Decode App 2.4.7's different Home and Portable capability bits."""
    family = entity.coordinator.device_family(entity.device_id)
    bit = {
        "home": BATTERY_PASSPORT_HOME_BIT,
        "portable": BATTERY_PASSPORT_PORTABLE_BIT,
    }.get(family)
    raw = entity.merged_properties.get(FIELD_ABILITY)
    if bit is None or isinstance(raw, bool):
        return None
    mask = safe_int(raw)
    if mask is None or mask < 0:
        return None
    return bool(mask & (1 << bit))


def _portable_pack_connected(entity: JackeryEntity) -> bool | None:
    value = entity.merged_properties.get(FIELD_IS_PACK_CONNECT)
    return (
        value if isinstance(value, bool) else _app_flag(entity, FIELD_IS_PACK_CONNECT)
    )


def _box_storm_active(entity: JackeryEntity) -> bool | None:
    """Match the Box app's active-warning status without inventing malformed data."""
    rows = entity.merged_properties.get(FIELD_STORM)
    if not isinstance(rows, list):
        return None
    statuses = [row.get("status") if isinstance(row, dict) else None for row in rows]
    if any(type(value) is int and value == 1 for value in statuses):
        return True
    if any(type(value) is not int for value in statuses):
        return None
    return False


BOX_BINARY_SENSOR_DESCRIPTIONS: tuple[JackeryBinaryDescription, ...] = (
    JackeryBinaryDescription(
        key="box_ac1_connected",
        translation_key="box_ac1_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda entity: _box_ac_connected(entity, "ac1"),
        app_fields=("ac1.bi",),
        required_property_keys=("ac1",),
    ),
    JackeryBinaryDescription(
        key="box_ac2_connected",
        translation_key="box_ac2_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=lambda entity: _box_ac_connected(entity, "ac2"),
        app_fields=("ac2.bi",),
        required_property_keys=("ac2",),
    ),
    JackeryBinaryDescription(
        key="box_backup_period_enabled",
        translation_key="box_backup_period_enabled",
        value_fn=_box_backup_period_enabled,
        app_fields=(FIELD_BOX_BACKUP_PERIOD_ENABLED,),
        required_property_keys=(FIELD_BOX_BACKUP_PERIOD_ENABLED,),
    ),
    JackeryBinaryDescription(
        key="box_ble_ota_flag",
        translation_key="box_ble_ota_flag",
        value_fn=lambda entity: _app_flag(entity, FIELD_BLS),
        required_property_keys=(FIELD_BLS,),
    ),
    JackeryBinaryDescription(
        key="box_storm_active",
        translation_key="box_storm_active",
        value_fn=_box_storm_active,
        required_property_keys=(FIELD_STORM,),
    ),
    JackeryBinaryDescription(
        key="box_charge_now",
        translation_key="box_charge_now",
        value_fn=lambda entity: _app_flag(entity, "rc"),
        required_property_keys=("rc",),
    ),
    JackeryBinaryDescription(
        key="box_ups_enabled",
        translation_key="box_ups_enabled",
        value_fn=lambda entity: _app_flag(entity, FIELD_UPS),
        required_property_keys=(FIELD_UPS,),
    ),
    JackeryBinaryDescription(
        key="box_pss",
        translation_key="box_pss",
        value_fn=lambda entity: _app_flag(entity, FIELD_PSS),
        required_property_keys=(FIELD_PSS,),
    ),
)


PORTABLE_BLE_OTA_DESCRIPTION = JackeryBinaryDescription(
    key="portable_ble_ota_flag",
    translation_key="portable_ble_ota_flag",
    value_fn=lambda entity: _app_flag(entity, FIELD_BLS),
    required_property_keys=(FIELD_BLS,),
)

PORTABLE_BINARY_SENSOR_DESCRIPTIONS = (
    PORTABLE_BLE_OTA_DESCRIPTION,
    JackeryBinaryDescription(
        key="portable_pack_connected",
        translation_key="portable_pack_connected",
        device_class=BinarySensorDeviceClass.CONNECTIVITY,
        value_fn=_portable_pack_connected,
        required_property_keys=(FIELD_IS_PACK_CONNECT,),
    ),
    *(
        JackeryBinaryDescription(
            key=f"portable_ac{index}_on",
            translation_key=f"portable_ac{index}_on",
            value_fn=partial(_app_flag, field=field),
            required_property_keys=(field,),
        )
        for index, field in ((1, FIELD_OACL1), (2, FIELD_OACL2))
    ),
)


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
        key="battery_passport_supported",
        # pyrefly: ignore [unexpected-keyword]
        translation_key="battery_passport_supported",
        # pyrefly: ignore [unexpected-keyword]
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_battery_passport_supported,
        app_fields=(FIELD_ABILITY,),
        required_property_keys=(FIELD_ABILITY,),
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
