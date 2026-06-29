"""Behavioral tests for period PV-revenue sensors (day/week/month/year).

Slice G-revenue: surface ``totalSolarRevenue`` per dateType as MONETARY period
sensors. These mirror the period-energy sensors but expose the device's own
currency symbol (``PvStatApi$Bean.currency``) as the native unit, use
``device_class=MONETARY`` with ``state_class=TOTAL`` (the HA-valid combination
for a period total), and reset on the app's day/week/month/year boundary.

The tests drive the entity cache directly with a lightweight coordinator stub so
no Home Assistant fixtures are required.
"""

from datetime import timedelta
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.jackery_solarvault.const import (
    APP_REQUEST_BEGIN_DATE,
    APP_REQUEST_META,
    APP_SECTION_PV_STAT,
    APP_STAT_TOTAL_SOLAR_REVENUE,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    FIELD_CURRENCY,
)
from custom_components.jackery_solarvault.sensor import (
    STAT_DESCRIPTIONS,
    JackeryStatSensor,
)
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import CURRENCY_EURO
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from datetime import date

    from custom_components.jackery_solarvault.sensor import (
        JackeryStatSensorDescription,
    )

DEVICE_ID = "dev_pv_rev"
REVENUE_VALUE = 12.34
PERIOD_REVENUE_KEYS = {
    "pv_revenue_day": DATE_TYPE_DAY,
    "pv_revenue_week": DATE_TYPE_WEEK,
    "pv_revenue_month": DATE_TYPE_MONTH,
    "pv_revenue_year": DATE_TYPE_YEAR,
}


def _description(key: str) -> JackeryStatSensorDescription:
    for desc in STAT_DESCRIPTIONS:
        if desc.key == key:
            return desc
    msg = f"missing descriptor {key}"
    raise AssertionError(msg)


def _make_coordinator(section: str, source: dict[str, Any]) -> SimpleNamespace:
    return SimpleNamespace(
        data={DEVICE_ID: {section: source}},
        async_add_listener=lambda *_a, **_k: lambda: None,
        last_update_success=True,
    )


def _make_sensor(key: str, section: str, source: dict[str, Any]) -> JackeryStatSensor:
    coordinator = _make_coordinator(section, source)
    sensor = JackeryStatSensor(coordinator, DEVICE_ID, _description(key))
    sensor.hass = SimpleNamespace(config=SimpleNamespace(time_zone="UTC"))
    return sensor


def _period_begin(reset_period: str) -> date:
    today = dt_util.now().date()
    if reset_period == DATE_TYPE_WEEK:
        return today - timedelta(days=today.weekday())
    if reset_period == DATE_TYPE_MONTH:
        return today.replace(day=1)
    if reset_period == DATE_TYPE_YEAR:
        return today.replace(month=1, day=1)
    return today


def _fresh_source(
    revenue: str | float,
    currency: str | None,
    reset_period: str,
) -> dict[str, Any]:
    source: dict[str, Any] = {
        APP_STAT_TOTAL_SOLAR_REVENUE: revenue,
        APP_REQUEST_META: {
            APP_REQUEST_BEGIN_DATE: _period_begin(reset_period).isoformat(),
        },
    }
    if currency is not None:
        source[FIELD_CURRENCY] = currency
    return source


def test_period_revenue_descriptors_exist_with_monetary_total() -> None:
    """Each period revenue descriptor is MONETARY/TOTAL with the right reset."""
    for key, reset_period in PERIOD_REVENUE_KEYS.items():
        desc = _description(key)
        assert desc.device_class == SensorDeviceClass.MONETARY, key
        assert desc.state_class == SensorStateClass.TOTAL, key
        assert desc.reset_period == reset_period, key
        assert desc.stat_key == APP_STAT_TOTAL_SOLAR_REVENUE, key
        assert desc.section == f"{APP_SECTION_PV_STAT}_{reset_period}", key
        assert desc.translation_key == key, key


def test_period_revenue_value_parses_total_solar_revenue() -> None:
    """The cached native value equals the parsed period totalSolarRevenue."""
    for key, reset_period in PERIOD_REVENUE_KEYS.items():
        section = f"{APP_SECTION_PV_STAT}_{reset_period}"
        source = _fresh_source(str(REVENUE_VALUE), "€", reset_period)
        sensor = _make_sensor(key, section, source)
        sensor._refresh_cache()  # noqa: SLF001
        assert sensor.native_value == pytest.approx(REVENUE_VALUE), key


def test_period_revenue_uses_device_currency_as_native_unit() -> None:
    """The native unit follows the device currency symbol from the payload."""
    for key, reset_period in PERIOD_REVENUE_KEYS.items():
        section = f"{APP_SECTION_PV_STAT}_{reset_period}"
        sensor = _make_sensor(key, section, _fresh_source("5.0", "$", reset_period))
        sensor._refresh_cache()  # noqa: SLF001
        assert sensor.native_unit_of_measurement == "$", key


def test_period_revenue_falls_back_to_euro_when_currency_absent() -> None:
    """Without a payload currency the unit falls back to the EUR default."""
    section = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}"
    source = _fresh_source("1.0", None, DATE_TYPE_WEEK)
    sensor = _make_sensor("pv_revenue_week", section, source)
    sensor._refresh_cache()  # noqa: SLF001
    assert sensor.native_unit_of_measurement == CURRENCY_EURO


def test_period_revenue_last_reset_tracks_period_boundary() -> None:
    """last_reset is the local period start (not None) for a fresh period."""
    section = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}"
    source = _fresh_source("9.9", "€", DATE_TYPE_YEAR)
    sensor = _make_sensor("pv_revenue_year", section, source)
    sensor._refresh_cache()  # noqa: SLF001
    reset = sensor.last_reset
    assert reset is not None
    assert reset.month == 1
    assert reset.day == 1
