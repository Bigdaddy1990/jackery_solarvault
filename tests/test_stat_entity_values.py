"""Regression tests for statistic entity value passthrough."""

from datetime import datetime
from types import SimpleNamespace
from typing import Any, cast

import pytest

from custom_components.jackery_solarvault.const import DATE_TYPE_DAY, DATE_TYPE_WEEK
from custom_components.jackery_solarvault.sensor import (
    STAT_DESCRIPTIONS,
    JackeryStatSensor,
    _period_from_stat_description,
)
from homeassistant.components.sensor import SensorStateClass

_DEVICE_ID = "dev-1"
_STAT_KEY = "device_today_pv_energy"
_NEGATIVE_KWH = -1.5


def _stat_sensor() -> JackeryStatSensor:
    description = next(desc for desc in STAT_DESCRIPTIONS if desc.key == _STAT_KEY)
    sensor = JackeryStatSensor.__new__(JackeryStatSensor)
    mutable = cast("Any", sensor)
    mutable.coordinator = SimpleNamespace(
        data={
            _DEVICE_ID: {
                description.section: {
                    description.stat_key: _NEGATIVE_KWH,
                },
            },
        },
        local_daily_energy_kwh=lambda _device_id, _metric_key: None,
    )
    mutable.hass = SimpleNamespace(config=SimpleNamespace(time_zone="UTC"))
    mutable._device_id = _DEVICE_ID
    mutable.entity_description = description
    mutable._reset_period = description.reset_period
    mutable._cached_native_value = None
    mutable._cached_attrs = {}
    mutable._cached_source_section = description.section
    return sensor


def test_stat_entity_does_not_clamp_negative_period_values() -> None:
    """Stats/trends quality decisions belong upstream, not in the entity."""
    sensor = _stat_sensor()

    payload = sensor.coordinator.data[_DEVICE_ID]
    context = sensor._capture_refresh_context(payload)
    snapshot = sensor._refresh_cache(context, {})
    sensor._apply_cache_snapshot(snapshot)

    assert sensor.native_value == pytest.approx(_NEGATIVE_KWH)


@pytest.mark.parametrize(
    "sensor_key",
    [
        "device_today_ongrid_to_battery",
        "device_today_pv_to_battery",
        "device_today_battery_to_ongrid",
    ],
)
def test_device_daily_flow_converts_local_wh_delta_to_kwh(sensor_key: str) -> None:
    """Direct daily flow sensors publish the local delta, never lifetime Wh."""
    description = next(desc for desc in STAT_DESCRIPTIONS if desc.key == sensor_key)
    sensor = JackeryStatSensor.__new__(JackeryStatSensor)
    mutable = cast("Any", sensor)
    mutable.coordinator = SimpleNamespace(
        data={
            _DEVICE_ID: {
                description.section: {
                    description.stat_key: 3_580,
                },
            },
        },
        local_daily_energy_kwh=lambda _device_id, _metric_key: None,
    )
    mutable.hass = SimpleNamespace(config=SimpleNamespace(time_zone="UTC"))
    mutable._device_id = _DEVICE_ID
    mutable.entity_description = description
    mutable._reset_period = description.reset_period
    mutable._cached_native_value = None
    mutable._cached_attrs = {}
    mutable._cached_source_section = description.section

    payload = sensor.coordinator.data[_DEVICE_ID]
    context = sensor._capture_refresh_context(payload)
    snapshot = sensor._refresh_cache(context, {})
    sensor._apply_cache_snapshot(snapshot)

    assert sensor.native_value == pytest.approx(3.58)


def _period_sensor(reset_period: str) -> JackeryStatSensor:
    """Build a period JackeryStatSensor mirroring __init__ state_class wiring."""
    description = next(
        desc
        for desc in STAT_DESCRIPTIONS
        if _period_from_stat_description(desc) == reset_period
    )
    sensor = JackeryStatSensor.__new__(JackeryStatSensor)
    mutable = cast("Any", sensor)
    mutable.coordinator = SimpleNamespace(
        data={_DEVICE_ID: {description.section: {}}},
        local_daily_energy_kwh=lambda _device_id, _metric_key: None,
    )
    mutable.hass = SimpleNamespace(config=SimpleNamespace(time_zone="UTC"))
    mutable._device_id = _DEVICE_ID
    mutable.entity_description = description
    mutable._reset_period = reset_period
    # All period totals (day/week/month/year) are TOTAL so HA compiles their
    # long-term statistics (reverted 2026-07-18).
    mutable._attr_state_class = SensorStateClass.TOTAL
    mutable._cached_native_value = None
    mutable._cached_attrs = {}
    mutable._cached_source_section = description.section
    return sensor


def test_week_period_sensor_is_total_with_last_reset() -> None:
    """A week total is TOTAL and carries a period-start last_reset.

    Reverted 2026-07-18: week/month/year period totals are TOTAL again so HA
    compiles their long-term statistics (an earlier state_class=None stripped
    those — HA repair "no longer has a state class"). last_reset is valid on a
    TOTAL sensor, so it returns the period start.
    """
    sensor = _period_sensor(DATE_TYPE_WEEK)

    assert sensor._attr_state_class is SensorStateClass.TOTAL
    assert sensor.last_reset is not None


def test_day_period_sensor_still_reports_last_reset() -> None:
    """The TOTAL day total keeps its last_reset (guards against over-correction)."""
    sensor = _period_sensor(DATE_TYPE_DAY)

    assert sensor._attr_state_class is SensorStateClass.TOTAL
    assert isinstance(sensor.last_reset, datetime)
