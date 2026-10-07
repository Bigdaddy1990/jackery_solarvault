"""Preserve Home Assistant enum identity across the binary-sensor relocation."""

from importlib import import_module
from importlib.util import find_spec

import pytest

from custom_components.jackery_solarvault import binary_sensor as platform
from custom_components.jackery_solarvault.const import (
    SUBDEVICE_DEV_TYPE_SMOKE,
    SUBDEVICE_DEV_TYPE_TEMP_HUMIDITY,
    SUBDEVICE_DEV_TYPE_WATER_LEAK,
)
from custom_components.jackery_solarvault.descriptions import (
    BINARY_SENSOR_DESCRIPTIONS,
    binary_sensor as descriptions,
)
from homeassistant.components.binary_sensor import BinarySensorDeviceClass


def test_binary_device_class_matches_home_assistant_canonical_enum() -> None:
    """The old public export and the relocated definition are the same enum."""
    relocated_module = "homeassistant.components.binary_sensor.const"
    canonical_module = import_module(
        relocated_module
        if find_spec(relocated_module) is not None
        else "homeassistant.components.binary_sensor"
    )

    assert BinarySensorDeviceClass is canonical_module.BinarySensorDeviceClass
    assert platform.BinarySensorDeviceClass is BinarySensorDeviceClass
    assert descriptions.BinarySensorDeviceClass is BinarySensorDeviceClass


@pytest.mark.parametrize(
    ["key", "expected"],
    [
        ["online", BinarySensorDeviceClass.CONNECTIVITY],
        ["eps_active", BinarySensorDeviceClass.RUNNING],
        ["eth_connected", BinarySensorDeviceClass.CONNECTIVITY],
        ["alarm", BinarySensorDeviceClass.SAFETY],
        ["pv1_connected", BinarySensorDeviceClass.CONNECTIVITY],
        ["pv2_connected", BinarySensorDeviceClass.CONNECTIVITY],
        ["pv3_connected", BinarySensorDeviceClass.CONNECTIVITY],
        ["pv4_connected", BinarySensorDeviceClass.CONNECTIVITY],
    ],
)
def test_binary_descriptions_keep_enum_members(
    key: str, expected: BinarySensorDeviceClass
) -> None:
    """Descriptions must retain enum members, which also compare equal to strings."""
    description = next(item for item in BINARY_SENSOR_DESCRIPTIONS if item.key == key)

    assert description.device_class is expected


@pytest.mark.parametrize(
    ["dev_type", "expected"],
    [
        [SUBDEVICE_DEV_TYPE_SMOKE, BinarySensorDeviceClass.SMOKE],
        [SUBDEVICE_DEV_TYPE_WATER_LEAK, BinarySensorDeviceClass.MOISTURE],
        [SUBDEVICE_DEV_TYPE_TEMP_HUMIDITY, BinarySensorDeviceClass.PROBLEM],
    ],
)
def test_accessory_device_classes_keep_enum_members(
    dev_type: int, expected: BinarySensorDeviceClass
) -> None:
    """Accessory overrides use the Home Assistant enum, preserving its type."""
    assert platform.SUBDEVICE_ALARM_DEVICE_CLASSES[dev_type] is expected


def test_smart_plug_device_class_keeps_enum_member() -> None:
    """The platform exposes the actual power enum through the entity property."""
    entity = platform.JackerySmartPlugStateBinarySensor.__new__(
        platform.JackerySmartPlugStateBinarySensor
    )

    assert entity.device_class is BinarySensorDeviceClass.POWER
