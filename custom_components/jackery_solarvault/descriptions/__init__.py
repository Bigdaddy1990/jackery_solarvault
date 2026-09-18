"""Central description package for Jackery SolarVault integration.

All entity descriptions are defined here following HA-standard patterns:
- SensorEntityDescription with value_fn(entity) -> StateType
- NumberEntityDescription with value_fn(entity) -> float | None
- SelectEntityDescription with value_fn(entity) -> str | None
- SwitchEntityDescription with value_fn(entity) -> bool
- ButtonEntityDescription with press_fn(entity) -> None
- BinarySensorEntityDescription with value_fn(entity) -> bool | None
"""

from .binary_sensor import (
    BINARY_SENSOR_DESCRIPTIONS,
    JackeryBinaryDescription,
    JackerySubdeviceAlarmBinarySensorDescription,
)
from .button import BUTTON_DESCRIPTIONS, JackeryButtonDescription
from .number import NUMBER_DESCRIPTIONS, JackeryNumberDescription
from .select import SELECT_DESCRIPTIONS, JackerySelectDescription
from .sensor import (
    BATTERY_PACK_SENSOR_DESCRIPTIONS,
    BREAKER_SENSOR_DESCRIPTIONS,
    DYNAMIC_PRICE_SENSOR_DESCRIPTIONS,
    METER_HEAD_SENSOR_DESCRIPTIONS,
    PORTABLE_SENSOR_DESCRIPTIONS,
    SAVINGS_DETAIL_SENSOR_DESCRIPTIONS,
    SENSOR_DESCRIPTIONS,
    SMART_METER_SENSOR_DESCRIPTIONS,
    SMART_MODE_SENSOR_DESCRIPTIONS,
    SMART_PLUG_SENSOR_DESCRIPTIONS,
    SMART_SCHEDULE_SENSOR_DESCRIPTIONS,
    STAT_DESCRIPTIONS,
    SUBDEVICE_ALARM_SENSOR_DESCRIPTIONS,
    TOU_PLAN_SENSOR_DESCRIPTIONS,
    JackeryBatteryPackSensorDescription,
    JackeryBreakerSensorDescription,
    JackeryMeterHeadSensorDescription,
    JackerySavingsDetailSensorDescription,
    JackerySensorDescription,
    JackerySmartMeterSensorDescription,
    JackerySmartPlugSensorDescription,
    JackeryStatSensorDescription,
    JackerySubdeviceAlarmSensorDescription,
)
from .switch import SWITCH_DESCRIPTIONS, JackerySwitchDescription

__all__ = [
    "BATTERY_PACK_SENSOR_DESCRIPTIONS",
    "BINARY_SENSOR_DESCRIPTIONS",
    "BREAKER_SENSOR_DESCRIPTIONS",
    "BUTTON_DESCRIPTIONS",
    "DYNAMIC_PRICE_SENSOR_DESCRIPTIONS",
    "METER_HEAD_SENSOR_DESCRIPTIONS",
    "NUMBER_DESCRIPTIONS",
    "PORTABLE_SENSOR_DESCRIPTIONS",
    "SAVINGS_DETAIL_SENSOR_DESCRIPTIONS",
    "SELECT_DESCRIPTIONS",
    "SENSOR_DESCRIPTIONS",
    "SMART_METER_SENSOR_DESCRIPTIONS",
    "SMART_MODE_SENSOR_DESCRIPTIONS",
    "SMART_PLUG_SENSOR_DESCRIPTIONS",
    "SMART_SCHEDULE_SENSOR_DESCRIPTIONS",
    "STAT_DESCRIPTIONS",
    "SUBDEVICE_ALARM_SENSOR_DESCRIPTIONS",
    "SWITCH_DESCRIPTIONS",
    "TOU_PLAN_SENSOR_DESCRIPTIONS",
    "JackeryBatteryPackSensorDescription",
    # Binary Sensor
    "JackeryBinaryDescription",
    "JackeryBreakerSensorDescription",
    # Button
    "JackeryButtonDescription",
    "JackeryMeterHeadSensorDescription",
    # Number
    "JackeryNumberDescription",
    "JackerySavingsDetailSensorDescription",
    # Select
    "JackerySelectDescription",
    # Sensor
    "JackerySensorDescription",
    "JackerySmartMeterSensorDescription",
    "JackerySmartPlugSensorDescription",
    "JackeryStatSensorDescription",
    "JackerySubdeviceAlarmBinarySensorDescription",
    "JackerySubdeviceAlarmSensorDescription",
    # Switch
    "JackerySwitchDescription",
]
