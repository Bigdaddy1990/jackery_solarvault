"""Focused statistic-sensor period resolution regressions."""

from datetime import UTC, date, datetime
from typing import Literal

import pytest

from custom_components.jackery_solarvault.const import (
    APP_CHART_SERIES_Y,
    APP_REQUEST_BEGIN_DATE_ALT,
    APP_REQUEST_DATE_TYPE_ALT,
    APP_REQUEST_END_DATE_ALT,
    APP_REQUEST_META,
    APP_SECTION_CT_STAT,
    APP_SECTION_HOME_STAT,
    APP_SECTION_PV_STAT,
    APP_STAT_TOTAL_CT_INPUT_ENERGY,
    APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
    APP_STAT_TOTAL_IN_GRID_ENERGY,
    APP_STAT_TOTAL_OUT_GRID_ENERGY,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
)
from custom_components.jackery_solarvault.sensor import (
    STAT_DESCRIPTIONS,
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


@pytest.mark.parametrize(
    [
        "description_key",
        "date_type",
        "fallback_stat_key",
        "begin_date",
        "end_date",
        "expected",
    ],
    [
        [
            "ct_input_day_energy",
            DATE_TYPE_DAY,
            APP_STAT_TOTAL_IN_GRID_ENERGY,
            "2026-08-21",
            "2026-08-21",
            0.11,
        ],
        [
            "ct_input_week_energy",
            DATE_TYPE_WEEK,
            APP_STAT_TOTAL_IN_GRID_ENERGY,
            "2026-08-17",
            "2026-08-23",
            0.51,
        ],
        [
            "ct_input_month_energy",
            DATE_TYPE_MONTH,
            APP_STAT_TOTAL_IN_GRID_ENERGY,
            "2026-08-01",
            "2026-08-31",
            1.31,
        ],
        [
            "ct_input_year_energy",
            DATE_TYPE_YEAR,
            APP_STAT_TOTAL_IN_GRID_ENERGY,
            "2026-01-01",
            "2026-12-31",
            2.41,
        ],
        [
            "ct_output_day_energy",
            DATE_TYPE_DAY,
            APP_STAT_TOTAL_OUT_GRID_ENERGY,
            "2026-08-21",
            "2026-08-21",
            5.04,
        ],
        [
            "ct_output_week_energy",
            DATE_TYPE_WEEK,
            APP_STAT_TOTAL_OUT_GRID_ENERGY,
            "2026-08-17",
            "2026-08-23",
            35.04,
        ],
        [
            "ct_output_month_energy",
            DATE_TYPE_MONTH,
            APP_STAT_TOTAL_OUT_GRID_ENERGY,
            "2026-08-01",
            "2026-08-31",
            95.04,
        ],
        [
            "ct_output_year_energy",
            DATE_TYPE_YEAR,
            APP_STAT_TOTAL_OUT_GRID_ENERGY,
            "2026-01-01",
            "2026-12-31",
            695.04,
        ],
    ],
)
def test_ct_period_uses_verified_system_grid_total_when_ct_chart_is_empty(
    description_key: str,
    date_type: Literal["day", "week", "month", "year"],
    fallback_stat_key: str,
    begin_date: str,
    end_date: str,
    expected: float,
) -> None:
    """A successful but empty CT chart falls back to the populated grid period."""
    ct_section = f"{APP_SECTION_CT_STAT}_{date_type}"
    home_section = f"{APP_SECTION_HOME_STAT}_{date_type}"
    description = next(
        item for item in STAT_DESCRIPTIONS if item.key == description_key
    )
    primary_stat_key = (
        APP_STAT_TOTAL_CT_INPUT_ENERGY
        if description_key.startswith("ct_input_")
        else APP_STAT_TOTAL_CT_OUTPUT_ENERGY
    )
    payload = {
        ct_section: {
            primary_stat_key: "0",
            "y1": [],
            "y2": [],
            APP_REQUEST_META: {
                APP_REQUEST_DATE_TYPE_ALT: date_type,
                APP_REQUEST_BEGIN_DATE_ALT: begin_date,
                APP_REQUEST_END_DATE_ALT: end_date,
            },
        },
        home_section: {
            fallback_stat_key: str(expected),
            APP_REQUEST_META: {
                APP_REQUEST_DATE_TYPE_ALT: date_type,
                APP_REQUEST_BEGIN_DATE_ALT: begin_date,
                APP_REQUEST_END_DATE_ALT: end_date,
            },
        },
    }
    sensor = JackeryStatSensor.__new__(JackeryStatSensor)
    sensor.entity_description = description
    sensor._reset_period = date_type
    context = _StatRefreshContext(
        payload=payload,
        local_now=datetime(2026, 8, 21, 15, 12, tzinfo=UTC),
        local_today=date(2026, 8, 21),
        local_daily_raw=None,
        local_period_raw=None,
    )

    snapshot = sensor._refresh_cache(context, {})

    assert snapshot.native_value == pytest.approx(expected)
    assert snapshot.attrs["source_section"] == home_section
