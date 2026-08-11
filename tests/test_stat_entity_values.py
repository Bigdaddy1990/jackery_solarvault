"""Regression tests for statistic entity value passthrough."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from custom_components.jackery_solarvault.const import (
    APP_CHART_SERIES_Y1,
    APP_REQUEST_BEGIN_DATE,
    APP_REQUEST_DATE_TYPE,
    APP_REQUEST_END_DATE,
    APP_REQUEST_META,
    APP_STAT_UNIT,
    APP_UNIT_KWH,
    DATE_TYPE_DAY,
    DATE_TYPE_WEEK,
)
from custom_components.jackery_solarvault.sensor import (
    STAT_DESCRIPTIONS,
    JackeryStatSensor,
    _period_from_stat_description,  # ruff: ignore[import-private-name]
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
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable.entity_description = description
    mutable._reset_period = description.reset_period  # ruff: ignore[private-member-access]
    mutable._cached_native_value = None  # ruff: ignore[private-member-access]
    mutable._cached_attrs = {}  # ruff: ignore[private-member-access]
    mutable._cached_source_section = description.section  # ruff: ignore[private-member-access]
    mutable._restored_lifetime_value = None  # ruff: ignore[private-member-access]
    return sensor


def test_stat_entity_does_not_clamp_negative_period_values() -> None:
    """Stats/trends quality decisions belong upstream, not in the entity."""
    sensor = _stat_sensor()

    payload = sensor.coordinator.data[_DEVICE_ID]
    context = sensor._capture_refresh_context(payload)  # ruff: ignore[private-member-access]
    snapshot = sensor._refresh_cache(context, {})  # ruff: ignore[private-member-access]
    sensor._apply_cache_snapshot(snapshot)  # ruff: ignore[private-member-access]

    assert sensor.native_value == pytest.approx(_NEGATIVE_KWH)


def test_week_period_rejects_one_payloads_placeholder_zero() -> None:
    """A scalar and zero series from one HTTP bucket are one zero source."""
    description = next(
        desc for desc in STAT_DESCRIPTIONS if desc.key == "device_pv1_week_energy"
    )
    sensor = JackeryStatSensor.__new__(JackeryStatSensor)
    mutable = cast("Any", sensor)
    today = datetime.now(UTC).date()
    week_start = today - timedelta(days=today.weekday())
    request = {
        APP_REQUEST_DATE_TYPE: DATE_TYPE_WEEK,
        APP_REQUEST_BEGIN_DATE: week_start.isoformat(),
        APP_REQUEST_END_DATE: (week_start + timedelta(days=6)).isoformat(),
    }
    mutable.coordinator = SimpleNamespace(
        data={
            _DEVICE_ID: {
                description.section: {
                    description.stat_key: 0,
                    APP_CHART_SERIES_Y1: [0, "", None],
                    APP_STAT_UNIT: APP_UNIT_KWH,
                    APP_REQUEST_META: request,
                },
            },
        },
        local_daily_energy_kwh=lambda _device_id, _metric_key: None,
    )
    mutable.hass = SimpleNamespace(config=SimpleNamespace(time_zone="UTC"))
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable.entity_description = description
    mutable._reset_period = description.reset_period  # ruff: ignore[private-member-access]
    mutable._cached_native_value = None  # ruff: ignore[private-member-access]
    mutable._cached_attrs = {}  # ruff: ignore[private-member-access]
    mutable._cached_source_section = description.section  # ruff: ignore[private-member-access]
    mutable._restored_lifetime_value = None  # ruff: ignore[private-member-access]

    payload = sensor.coordinator.data[_DEVICE_ID]
    context = sensor._capture_refresh_context(payload)  # ruff: ignore[private-member-access]
    snapshot = sensor._refresh_cache(context, {})  # ruff: ignore[private-member-access]
    sensor._apply_cache_snapshot(snapshot)  # ruff: ignore[private-member-access]

    assert sensor.native_value is None
    assert sensor.extra_state_attributes["period_values"] == [0.0, None, None]


def test_day_period_accepts_zero_only_after_distinct_source_confirmation() -> None:
    """Two different HTTP buckets may confirm a genuine scalar period zero."""
    description = next(
        desc for desc in STAT_DESCRIPTIONS if desc.key == "device_today_pv_energy"
    )
    sensor = JackeryStatSensor.__new__(JackeryStatSensor)
    mutable = cast("Any", sensor)
    today = datetime.now(UTC).date().isoformat()
    request = {
        APP_REQUEST_DATE_TYPE: DATE_TYPE_DAY,
        APP_REQUEST_BEGIN_DATE: today,
        APP_REQUEST_END_DATE: today,
    }
    payload = {
        description.section: {
            description.stat_key: 0,
            APP_REQUEST_META: request,
        },
    }
    mutable.coordinator = SimpleNamespace(
        data={_DEVICE_ID: payload},
        local_daily_energy_kwh=lambda _device_id, _metric_key: None,
    )
    mutable.hass = SimpleNamespace(config=SimpleNamespace(time_zone="UTC"))
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable.entity_description = description
    mutable._reset_period = description.reset_period  # ruff: ignore[private-member-access]
    mutable._cached_native_value = None  # ruff: ignore[private-member-access]
    mutable._cached_attrs = {}  # ruff: ignore[private-member-access]
    mutable._cached_source_section = description.section  # ruff: ignore[private-member-access]
    mutable._restored_lifetime_value = None  # ruff: ignore[private-member-access]

    context = sensor._capture_refresh_context(payload)  # ruff: ignore[private-member-access]
    snapshot = sensor._refresh_cache(context, {})  # ruff: ignore[private-member-access]
    sensor._apply_cache_snapshot(snapshot)  # ruff: ignore[private-member-access]
    assert sensor.native_value is None

    mutable.coordinator.local_daily_energy_kwh = (
        lambda _device_id, _metric_key: 0.0
    )
    context = sensor._capture_refresh_context(payload)  # ruff: ignore[private-member-access]
    snapshot = sensor._refresh_cache(context, {})  # ruff: ignore[private-member-access]
    sensor._apply_cache_snapshot(snapshot)  # ruff: ignore[private-member-access]
    assert sensor.native_value == pytest.approx(0.0)


def test_pv_revenue_period_exposes_http_request_range() -> None:
    """Scalar PV revenue period entities retain their HTTP request range."""
    description = next(
        desc for desc in STAT_DESCRIPTIONS if desc.key == "pv_revenue_week"
    )
    sensor = JackeryStatSensor.__new__(JackeryStatSensor)
    mutable = cast("Any", sensor)
    request = {
        APP_REQUEST_DATE_TYPE: DATE_TYPE_WEEK,
        APP_REQUEST_BEGIN_DATE: "2026-08-10",
        APP_REQUEST_END_DATE: "2026-08-16",
    }
    mutable.coordinator = SimpleNamespace(
        data={
            _DEVICE_ID: {
                description.section: {
                    description.stat_key: 1.25,
                    APP_REQUEST_META: request,
                },
            },
        },
        local_daily_energy_kwh=lambda _device_id, _metric_key: None,
    )
    mutable.hass = SimpleNamespace(config=SimpleNamespace(time_zone="UTC"))
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable.entity_description = description
    mutable._reset_period = description.reset_period  # ruff: ignore[private-member-access]
    mutable._cached_native_value = None  # ruff: ignore[private-member-access]
    mutable._cached_attrs = {}  # ruff: ignore[private-member-access]
    mutable._cached_source_section = description.section  # ruff: ignore[private-member-access]
    mutable._restored_lifetime_value = None  # ruff: ignore[private-member-access]

    payload = sensor.coordinator.data[_DEVICE_ID]
    context = sensor._capture_refresh_context(payload)  # ruff: ignore[private-member-access]
    snapshot = sensor._refresh_cache(context, {})  # ruff: ignore[private-member-access]
    sensor._apply_cache_snapshot(snapshot)  # ruff: ignore[private-member-access]

    assert sensor.native_value == pytest.approx(1.25)
    assert sensor.extra_state_attributes["request"] == request


@pytest.mark.parametrize(
    "sensor_key",
    [
        "device_today_ongrid_to_battery",
        "device_today_pv_to_battery",
        "device_today_battery_to_ongrid",
    ],
)
def test_device_daily_flow_converts_local_counter_delta_to_kwh(
    sensor_key: str,
) -> None:
    """Direct daily flow sensors convert persisted 0.01 kWh deltas to kWh."""
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
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable.entity_description = description
    mutable._reset_period = description.reset_period  # ruff: ignore[private-member-access]
    mutable._cached_native_value = None  # ruff: ignore[private-member-access]
    mutable._cached_attrs = {}  # ruff: ignore[private-member-access]
    mutable._cached_source_section = description.section  # ruff: ignore[private-member-access]
    mutable._restored_lifetime_value = None  # ruff: ignore[private-member-access]

    payload = sensor.coordinator.data[_DEVICE_ID]
    context = sensor._capture_refresh_context(payload)  # ruff: ignore[private-member-access]
    snapshot = sensor._refresh_cache(context, {})  # ruff: ignore[private-member-access]
    sensor._apply_cache_snapshot(snapshot)  # ruff: ignore[private-member-access]

    assert sensor.native_value == pytest.approx(35.8)


@pytest.mark.parametrize(
    "sensor_key",
    [
        "device_today_ongrid_to_battery",
        "device_today_pv_to_battery",
        "device_today_battery_to_ongrid",
    ],
)
def test_device_daily_flow_falls_back_to_local_kwh_delta(sensor_key: str) -> None:
    """A missing HTTP day flow falls back to the transport-neutral delta."""
    description = next(desc for desc in STAT_DESCRIPTIONS if desc.key == sensor_key)
    sensor = JackeryStatSensor.__new__(JackeryStatSensor)
    mutable = cast("Any", sensor)
    mutable.coordinator = SimpleNamespace(
        data={_DEVICE_ID: {description.section: {}}},
        local_daily_energy_kwh=lambda _device_id, _metric_key: 3.58,
    )
    mutable.hass = SimpleNamespace(config=SimpleNamespace(time_zone="UTC"))
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable.entity_description = description
    mutable._reset_period = description.reset_period  # ruff: ignore[private-member-access]
    mutable._cached_native_value = None  # ruff: ignore[private-member-access]
    mutable._cached_attrs = {}  # ruff: ignore[private-member-access]
    mutable._cached_source_section = description.section  # ruff: ignore[private-member-access]
    mutable._restored_lifetime_value = None  # ruff: ignore[private-member-access]

    payload = sensor.coordinator.data[_DEVICE_ID]
    context = sensor._capture_refresh_context(payload)  # ruff: ignore[private-member-access]
    snapshot = sensor._refresh_cache(context, {})  # ruff: ignore[private-member-access]
    sensor._apply_cache_snapshot(snapshot)  # ruff: ignore[private-member-access]

    assert sensor.native_value == pytest.approx(3.58)


def _today_battery_value(
    primary: float | None,
    fallback: float | None,
) -> float | None:
    """Resolve today battery energy from the two independent HTTP sources."""
    description = next(
        desc for desc in STAT_DESCRIPTIONS if desc.key == "today_battery_energy"
    )
    fallback_section, fallback_key = description.fallback_sources[0]
    primary_source = (
        {description.stat_key: primary} if primary is not None else {}
    )
    fallback_source = {fallback_key: fallback} if fallback is not None else {}
    sensor = JackeryStatSensor.__new__(JackeryStatSensor)
    mutable = cast("Any", sensor)
    mutable.coordinator = SimpleNamespace(
        data={
            _DEVICE_ID: {
                description.section: primary_source,
                fallback_section: fallback_source,
            },
        },
        local_daily_energy_kwh=lambda _device_id, _metric_key: None,
    )
    mutable.hass = SimpleNamespace(config=SimpleNamespace(time_zone="UTC"))
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable.entity_description = description
    mutable._reset_period = description.reset_period  # ruff: ignore[private-member-access]
    mutable._cached_native_value = None  # ruff: ignore[private-member-access]
    mutable._cached_attrs = {}  # ruff: ignore[private-member-access]
    mutable._cached_source_section = description.section  # ruff: ignore[private-member-access]
    mutable._restored_lifetime_value = None  # ruff: ignore[private-member-access]

    payload = sensor.coordinator.data[_DEVICE_ID]
    context = sensor._capture_refresh_context(payload)  # ruff: ignore[private-member-access]
    snapshot = sensor._refresh_cache(context, {})  # ruff: ignore[private-member-access]
    sensor._apply_cache_snapshot(snapshot)  # ruff: ignore[private-member-access]
    return cast("float | None", sensor.native_value)


def test_today_battery_rejects_single_source_zero() -> None:
    """One fallback zero does not prove a real zero day total."""
    assert _today_battery_value(None, 0.0) is None


def test_today_battery_accepts_two_source_zero() -> None:
    """Two independent HTTP zero observations corroborate a real zero."""
    assert _today_battery_value(0.0, 0.0) == pytest.approx(0.0)


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
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable.entity_description = description
    mutable._reset_period = reset_period  # ruff: ignore[private-member-access]
    # All period totals (day/week/month/year) are TOTAL so HA compiles their
    # long-term statistics (reverted 2026-07-18).
    mutable._attr_state_class = SensorStateClass.TOTAL  # ruff: ignore[private-member-access]
    mutable._cached_native_value = None  # ruff: ignore[private-member-access]
    mutable._cached_attrs = {}  # ruff: ignore[private-member-access]
    mutable._cached_source_section = description.section  # ruff: ignore[private-member-access]
    return sensor


def test_week_period_sensor_is_total_with_last_reset() -> None:
    """A week total is TOTAL and carries a period-start last_reset.

    Reverted 2026-07-18: week/month/year period totals are TOTAL again so HA
    compiles their long-term statistics (an earlier state_class=None stripped
    those — HA repair "no longer has a state class"). last_reset is valid on a
    TOTAL sensor, so it returns the period start.
    """
    sensor = _period_sensor(DATE_TYPE_WEEK)

    assert sensor._attr_state_class is SensorStateClass.TOTAL  # ruff: ignore[private-member-access]
    assert sensor.last_reset is not None


def test_day_period_sensor_still_reports_last_reset() -> None:
    """The TOTAL day total keeps its last_reset (guards against over-correction)."""
    sensor = _period_sensor(DATE_TYPE_DAY)

    assert sensor._attr_state_class is SensorStateClass.TOTAL  # ruff: ignore[private-member-access]
    assert isinstance(sensor.last_reset, datetime)
