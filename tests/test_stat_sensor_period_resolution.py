"""Focused statistic-sensor period resolution regressions."""

from datetime import UTC, date, datetime

import pytest

from custom_components.jackery_solarvault.const import (
    APP_CHART_SERIES_Y,
    APP_REQUEST_BEGIN_DATE_ALT,
    APP_REQUEST_DATE_TYPE_ALT,
    APP_REQUEST_END_DATE_ALT,
    APP_REQUEST_META,
    APP_SECTION_PV_STAT,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    DATE_TYPE_YEAR,
)
from custom_components.jackery_solarvault.sensor import (
    JackeryStatSensor,
    JackeryStatSensorDescription,
    _StatRefreshContext,
)
from custom_components.jackery_solarvault.util import safe_float
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfEnergy


def test_year_period_sensor_uses_positive_scalar_when_chart_is_zero_placeholder() -> (
    None
):
    """App 2.4.x year charts can be zero-filled while scalar totals are valid."""
    section = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}"
    payload = {
        section: {
            APP_CHART_SERIES_Y: [0.0] * 12,
            APP_STAT_TOTAL_SOLAR_ENERGY: "954.98",
            APP_REQUEST_META: {
                APP_REQUEST_DATE_TYPE_ALT: DATE_TYPE_YEAR,
                APP_REQUEST_BEGIN_DATE_ALT: "2026-01-01",
                APP_REQUEST_END_DATE_ALT: "2026-12-31",
            },
        },
    }
    description = JackeryStatSensorDescription(
        key="pv_year_energy",
        translation_key="pv_year_energy",
        stat_key=APP_STAT_TOTAL_SOLAR_ENERGY,
        section=section,
        transform=safe_float,
        device_class=SensorDeviceClass.ENERGY,
        state_class=SensorStateClass.TOTAL,
        reset_period=DATE_TYPE_YEAR,
        native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
    )
    sensor = JackeryStatSensor.__new__(JackeryStatSensor)
    sensor.entity_description = description
    sensor._reset_period = DATE_TYPE_YEAR
    context = _StatRefreshContext(
        payload=payload,
        local_now=datetime(2026, 8, 13, 22, 41, tzinfo=UTC),
        local_today=date(2026, 8, 13),
        local_daily_raw=None,
        local_period_raw=None,
    )

    snapshot = sensor._refresh_cache(context, {})

    assert snapshot.native_value == pytest.approx(954.98)
    assert snapshot.attrs["server_total"] == pytest.approx(954.98)
    assert snapshot.attrs["chart_series_sum"] == pytest.approx(0.0)
