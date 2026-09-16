"""Regression: subdevice alarm binary sensors report the accessory's real device_class.

Every subdevice alarm binary sensor previously shared one hardcoded `safety`
device_class regardless of accessory type (`devType`). A smoke-detector alarm
must report `smoke` and a water-leak alarm must report `moisture` so Home
Assistant assigns the correct icon and device_class-based automations behave
correctly.
"""

from types import SimpleNamespace
from typing import Any, cast

from custom_components.jackery_solarvault.binary_sensor import (
    SUBDEVICE_ALARM_DESCRIPTIONS,
    JackerySubdeviceAlarmBinarySensor,
)
from custom_components.jackery_solarvault.const import (
    FIELD_DEV_SN,
    FIELD_DEV_TYPE,
    PAYLOAD_SUBDEVICES,
    SUBDEVICE_DEV_TYPE_BATTERY_PACK,
    SUBDEVICE_DEV_TYPE_SMOKE,
    SUBDEVICE_DEV_TYPE_TEMP_HUMIDITY,
    SUBDEVICE_DEV_TYPE_WATER_LEAK,
)
from homeassistant.components.binary_sensor import BinarySensorDeviceClass

_DEVICE_ID = "home-power-3002"
_SUB_DEVICE_SN = "SN1"


def _alarm_sensor_for_dev_type(dev_type: int) -> JackerySubdeviceAlarmBinarySensor:
    """Bind a subdevice alarm binary sensor to a payload carrying `dev_type`.

    Bypasses `__init__` (mirrors `test_entity_device_info.py`) so `device_class`
    exercises only the real dev_type resolution logic without hass wiring.
    """
    payload = {
        PAYLOAD_SUBDEVICES: [
            {FIELD_DEV_SN: _SUB_DEVICE_SN, FIELD_DEV_TYPE: dev_type},
        ],
    }
    entity = JackerySubdeviceAlarmBinarySensor.__new__(
        JackerySubdeviceAlarmBinarySensor
    )
    mutable = cast("Any", entity)
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable.coordinator = SimpleNamespace(data={_DEVICE_ID: payload})
    mutable._sub_device_sn = _SUB_DEVICE_SN  # ruff: ignore[private-member-access]
    mutable.entity_description = SUBDEVICE_ALARM_DESCRIPTIONS[0]
    return entity


def test_smoke_subdevice_alarm_reports_smoke_device_class() -> None:
    """A smoke-detector accessory (devType=8) reports `smoke`, not `safety`."""
    entity = _alarm_sensor_for_dev_type(SUBDEVICE_DEV_TYPE_SMOKE)

    assert entity.device_class == BinarySensorDeviceClass.SMOKE


def test_water_leak_subdevice_alarm_reports_moisture_device_class() -> None:
    """A water-leak accessory (devType=10) reports `moisture`, not `safety`."""
    entity = _alarm_sensor_for_dev_type(SUBDEVICE_DEV_TYPE_WATER_LEAK)

    assert entity.device_class == BinarySensorDeviceClass.MOISTURE


def test_temp_humidity_subdevice_alarm_reports_problem_device_class() -> None:
    """A temp/humidity accessory (devType=9) has no directional HA class, so it.

    falls back to the generic `problem` class instead of the misleading `safety`
    class.
    """
    entity = _alarm_sensor_for_dev_type(SUBDEVICE_DEV_TYPE_TEMP_HUMIDITY)

    assert entity.device_class == BinarySensorDeviceClass.PROBLEM


def test_unmapped_subdevice_alarm_falls_back_to_safety_device_class() -> None:
    """Accessory types with no dedicated mapping (e.g. battery pack) keep the.

    original `safety` device_class — no regression for existing devices.
    """
    entity = _alarm_sensor_for_dev_type(SUBDEVICE_DEV_TYPE_BATTERY_PACK)

    assert entity.device_class == BinarySensorDeviceClass.SAFETY
