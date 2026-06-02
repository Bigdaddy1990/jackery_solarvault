"""Unit tests for shared Jackery entity metadata helpers."""

from types import SimpleNamespace
from typing import Any

from custom_components.jackery_solarvault.const import FIELD_CURRENT_VERSION
from custom_components.jackery_solarvault.const import FIELD_DEV_MODEL
from custom_components.jackery_solarvault.const import FIELD_DEV_SN
from custom_components.jackery_solarvault.const import FIELD_DEVICE_NAME
from custom_components.jackery_solarvault.const import FIELD_DEVICE_SN
from custom_components.jackery_solarvault.const import FIELD_MAC
from custom_components.jackery_solarvault.const import FIELD_MODEL
from custom_components.jackery_solarvault.const import FIELD_MODEL_NAME
from custom_components.jackery_solarvault.const import FIELD_SCAN_NAME
from custom_components.jackery_solarvault.const import FIELD_SN
from custom_components.jackery_solarvault.const import FIELD_TYPE_NAME
from custom_components.jackery_solarvault.const import FIELD_VERSION
from custom_components.jackery_solarvault.const import FIELD_WNAME
from custom_components.jackery_solarvault.const import PAYLOAD_BATTERY_PACKS
from custom_components.jackery_solarvault.const import PAYLOAD_CT_METER
from custom_components.jackery_solarvault.const import PAYLOAD_DEVICE
from custom_components.jackery_solarvault.const import PAYLOAD_DISCOVERY
from custom_components.jackery_solarvault.const import PAYLOAD_METER_HEADS
from custom_components.jackery_solarvault.const import PAYLOAD_OTA
from custom_components.jackery_solarvault.const import PAYLOAD_PROPERTIES
from custom_components.jackery_solarvault.const import PAYLOAD_SYSTEM
from custom_components.jackery_solarvault.entity import JackeryEntity
from custom_components.jackery_solarvault.sensor import JackeryBatteryPackSensor
from custom_components.jackery_solarvault.sensor import JackeryMeterHeadSensor
from custom_components.jackery_solarvault.sensor import JackerySmartMeterSensor


def _entity(payload: dict[str, object]) -> JackeryEntity:
    """Create a JackeryEntity test instance using the provided payload stored under the "dev1" key.

    Parameters:
        payload (dict[str, object]): Device payload to attach to the entity as its data.

    Returns:
        JackeryEntity: An entity whose data contains the given payload under the "dev1" key and that uses "dev1" as both the entity key and identifier.
    """
    return JackeryEntity(SimpleNamespace(data={"dev1": payload}), "dev1", "test")


def _sensor_entity(cls: type[Any], payload: dict[str, object]) -> Any:
    """Create and initialize an instance of the given sensor class for tests using the provided device payload.

    Parameters:
        cls (type[Any]): Sensor entity class to instantiate.
        payload (dict[str, object]): Device payload used to initialize the entity's data under the "dev1" key.

    Returns:
        Any: An instance of `cls` initialized with the given payload.
    """
    entity = cls.__new__(cls)
    JackeryEntity.__init__(
        entity,
        SimpleNamespace(data={"dev1": payload}),
        "dev1",
        "test",
    )
    return entity


def test_device_info_ignores_blank_metadata_fields() -> None:
    """Main device registry metadata should skip whitespace-only values."""
    entity = _entity({
        PAYLOAD_SYSTEM: {FIELD_DEVICE_NAME: "  "},
        PAYLOAD_DISCOVERY: {
            FIELD_DEVICE_NAME: " Discovery Name ",
            FIELD_DEV_MODEL: " ",
            FIELD_DEVICE_SN: " SN1 ",
        },
        PAYLOAD_PROPERTIES: {FIELD_WNAME: " Main Name "},
        PAYLOAD_DEVICE: {FIELD_MODEL_NAME: " Pro Model ", FIELD_DEVICE_SN: " "},
        PAYLOAD_OTA: {FIELD_CURRENT_VERSION: " "},
    })

    info = entity.device_info

    assert info["name"] == "Discovery Name"
    assert info["model"] == "Pro Model"
    assert info["serial_number"] == "SN1"
    assert info["sw_version"] is None


def test_smart_plug_device_info_ignores_blank_metadata_fields() -> None:
    """Smart-plug device registry metadata should skip blank payload values."""
    entity = _entity({
        PAYLOAD_SYSTEM: {FIELD_DEVICE_NAME: " "},
        PAYLOAD_DISCOVERY: {FIELD_DEVICE_NAME: " "},
        PAYLOAD_PROPERTIES: {FIELD_WNAME: " Main Name "},
    })

    info = entity._build_smart_plug_device_info(
        2,
        {
            FIELD_DEVICE_NAME: " ",
            FIELD_SCAN_NAME: " Plug A ",
            FIELD_MODEL: " ",
            FIELD_MODEL_NAME: " Socket Model ",
            FIELD_DEVICE_SN: " ",
            FIELD_DEV_SN: " SN2 ",
            FIELD_VERSION: " ",
            FIELD_CURRENT_VERSION: " 1.2.3 ",
        },
    )

    assert info["name"] == "Main Name Plug A"
    assert info["model"] == "Socket Model"
    assert info["serial_number"] == "SN2"
    assert info["sw_version"] == "1.2.3"


def test_battery_pack_device_info_ignores_blank_metadata_fields() -> None:
    """Battery-pack device registry metadata should skip blank payload values."""
    entity = _sensor_entity(
        JackeryBatteryPackSensor,
        {
            PAYLOAD_SYSTEM: {FIELD_DEVICE_NAME: " "},
            PAYLOAD_DISCOVERY: {FIELD_DEVICE_NAME: " "},
            PAYLOAD_PROPERTIES: {FIELD_WNAME: " Main Name "},
            PAYLOAD_BATTERY_PACKS: [
                {
                    FIELD_DEVICE_SN: " ",
                    FIELD_DEV_SN: " Pack SN ",
                    FIELD_MODEL: " ",
                    FIELD_MODEL_NAME: " Battery Model ",
                    FIELD_VERSION: " ",
                    FIELD_CURRENT_VERSION: " 2.3.4 ",
                },
            ],
        },
    )
    entity._pack_index = 1

    info = entity.device_info

    assert info["name"] == "Main Name Zusatzbatterie 1"
    assert info["model"] == "Battery Model"
    assert info["serial_number"] == "Pack SN"
    assert info["sw_version"] == "2.3.4"


def test_meter_head_device_info_ignores_blank_metadata_fields() -> None:
    """Meter-head device registry metadata should skip blank payload values."""
    entity = _sensor_entity(
        JackeryMeterHeadSensor,
        {
            PAYLOAD_SYSTEM: {FIELD_DEVICE_NAME: " "},
            PAYLOAD_DISCOVERY: {FIELD_DEVICE_NAME: " Main Name "},
            PAYLOAD_METER_HEADS: [
                {
                    FIELD_DEVICE_NAME: " ",
                    FIELD_SCAN_NAME: " Meter A ",
                    FIELD_DEVICE_SN: " ",
                    FIELD_SN: " Meter SN ",
                    FIELD_MODEL: " ",
                    FIELD_TYPE_NAME: " Meter Model ",
                    FIELD_VERSION: " ",
                    FIELD_CURRENT_VERSION: " 3.4.5 ",
                },
            ],
        },
    )
    entity._meter_head_index = 1

    info = entity.device_info

    assert info["name"] == "Main Name Meter A"
    assert info["model"] == "Meter Model"
    assert info["serial_number"] == "Meter SN"
    assert info["sw_version"] == "3.4.5"


def test_smart_meter_device_info_ignores_blank_metadata_fields() -> None:
    """Smart-meter device registry metadata should skip blank payload values."""
    entity = _sensor_entity(
        JackerySmartMeterSensor,
        {
            PAYLOAD_PROPERTIES: {FIELD_WNAME: " Main Name "},
            PAYLOAD_CT_METER: {
                FIELD_SCAN_NAME: " ",
                FIELD_DEVICE_SN: " ",
                FIELD_SN: " ",
                FIELD_MAC: " Meter MAC ",
            },
        },
    )

    info = entity.device_info

    assert info["name"] == "Main Name Smart Meter"
    assert info["model"] == "Smart Meter"
    assert info["serial_number"] == "Meter MAC"
