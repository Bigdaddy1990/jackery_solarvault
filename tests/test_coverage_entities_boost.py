"""Comprehensive entity unit test coverage boost for Home Assistant Quality Scale Platinum level."""

from unittest.mock import MagicMock

from custom_components.jackery_solarvault.sensor import JackeryStatSensor
from custom_components.jackery_solarvault.util import (
    is_day_period_payload,
    safe_bool,
    safe_float,
    safe_int,
)
from homeassistant.components.sensor import SensorDeviceClass


def test_jackery_stat_sensor_non_negative_clamping() -> None:
    """Test non negative energy value clamping in JackeryStatSensor."""
    sensor = MagicMock(spec=JackeryStatSensor)
    sensor._reset_period = "day"  # ruff: ignore[private-member-access]
    sensor.entity_description = MagicMock()
    sensor.entity_description.device_class = SensorDeviceClass.ENERGY

    # Test clamping negative values to 0.0
    result = JackeryStatSensor._non_negative_period_raw(sensor, -15.5)  # ruff: ignore[private-member-access]
    assert result == 0.0  # ruff: ignore[float-equality-comparison]

    # Test non-negative values are preserved
    result_pos = JackeryStatSensor._non_negative_period_raw(sensor, 42.0)  # ruff: ignore[private-member-access]
    assert result_pos == 42.0  # ruff: ignore[float-equality-comparison]

    # Test non-energy class is ignored
    sensor.entity_description.device_class = SensorDeviceClass.POWER
    result_power = JackeryStatSensor._non_negative_period_raw(sensor, -5.0)  # ruff: ignore[private-member-access]
    assert result_power == -5.0  # ruff: ignore[float-equality-comparison]


def test_jackery_stat_sensor_derived_home_energy_fallback() -> None:
    """Test derived home energy fallback flag."""
    assert JackeryStatSensor._derived_home_energy_fallback_enabled() is True  # ruff: ignore[private-member-access]


def test_safe_helpers_edge_cases() -> None:
    """Test safe conversion helper functions."""
    assert safe_float(None) is None
    assert safe_float("invalid") is None
    assert safe_float(12.34) == 12.34  # ruff: ignore[float-equality-comparison]

    assert safe_int(None) is None
    assert safe_int("invalid") is None
    assert safe_int(100) == 100

    assert safe_bool(None) is None
    assert safe_bool("true") is True
    assert safe_bool("false") is False
    assert safe_bool(1) is True
    assert safe_bool(0) is False


def test_is_day_period_payload() -> None:
    """Test day period payload detection helper."""
    assert is_day_period_payload({"dateType": "day"}, "sys_pv_day") is True
    assert is_day_period_payload({"dateType": "month"}, "sys_pv_month") is False
    assert is_day_period_payload({}, "sys_pv_day") is True
