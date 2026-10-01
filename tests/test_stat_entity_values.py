"""Regression tests for statistic entity value passthrough."""

from copy import deepcopy
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from custom_components.jackery_solarvault.const import (
    APP_CHART_SERIES_Y1,
    APP_CHART_SERIES_Y2,
    APP_DEVICE_STAT_BATTERY_DISCHARGE,
    APP_REQUEST_BEGIN_DATE,
    APP_REQUEST_DATE_TYPE,
    APP_REQUEST_END_DATE,
    APP_REQUEST_META,
    APP_STAT_UNIT,
    APP_UNIT_KWH,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    FIELD_CT_TOTAL_PHASE_ENERGY,
    PAYLOAD_LOCAL_DAILY_ENERGY,
)
from custom_components.jackery_solarvault.descriptions.sensor import _section_share  # ruff: ignore[import-private-name]
from custom_components.jackery_solarvault.sensor import (
    STAT_DESCRIPTIONS,
    JackeryStatSensor,
    _period_from_stat_description,  # ruff: ignore[import-private-name]
)
from custom_components.jackery_solarvault.util import (
    effective_period_total_value,
    trend_series_total,
)
from homeassistant.components.sensor import SensorStateClass

_DEVICE_ID = "dev-1"
_STAT_KEY = "device_today_pv_energy"
_NEGATIVE_KWH = -1.5


def _stat_sensor() -> JackeryStatSensor:
    description = next(desc for desc in STAT_DESCRIPTIONS if desc.key == _STAT_KEY)
    assert description.reset_period is not None
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
    mutable._cached_last_reset = sensor._compute_period_start(description.reset_period)  # ruff: ignore[private-member-access]
    mutable._restored_lifetime_value = None  # ruff: ignore[private-member-access]
    return sensor  # pyrefly: ignore [no-any-return-implicit]


def test_stat_entity_does_not_clamp_negative_period_values() -> None:
    """Stats/trends quality decisions belong upstream, not in the entity."""
    sensor = _stat_sensor()

    payload = sensor.coordinator.data[_DEVICE_ID]
    context = sensor._capture_refresh_context(payload)  # ruff: ignore[private-member-access]
    snapshot = sensor._refresh_cache(context, {})  # ruff: ignore[private-member-access]
    sensor._apply_cache_snapshot(snapshot)  # ruff: ignore[private-member-access]

    assert sensor.native_value == pytest.approx(_NEGATIVE_KWH)


def test_bad_pv_scalar_does_not_hide_a_complete_valid_channel_series() -> None:
    """A broken scalar cannot erase independently valid same-period buckets."""
    source = {
        "unit": "kWh",
        "totalSolarEnergy": "4.42",
        "pv1Egy": 120432.3,
        "y": [1.53, 2.89, 0, 0, 0, 0, 0],
        "y1": [0.34, 0.72, 0, 0, 0, 0, 0],
        "_request": {
            "dateType": "week",
            "beginDate": "2026-09-28",
            "endDate": "2026-10-04",
        },
    }
    values, total, scalar = JackeryStatSensor._resolve_period_value(  # ruff: ignore[private-member-access]
        source, "device_pv_stat_week", "pv1Egy", {}
    )

    assert values == source["y1"]
    assert total == pytest.approx(1.06)
    assert scalar is None
    assert effective_period_total_value(
        source, "device_pv_stat_week", "pv1Egy"
    ) == pytest.approx(1.06)
    assert trend_series_total(source, "device_pv_stat_week", "pv1Egy") == pytest.approx(
        1.06
    )


@pytest.mark.parametrize("period", [DATE_TYPE_MONTH, DATE_TYPE_YEAR])
@pytest.mark.parametrize("missing_day", [False, True])
def test_pv_period_rebuild_requires_every_invalid_bucket_to_be_verified(
    period: str, missing_day: bool
) -> None:
    """A dated daily rebuild fills an invalid channel bucket, never a gap."""
    description = next(
        desc for desc in STAT_DESCRIPTIONS if desc.key == f"device_pv1_{period}_energy"
    )
    sensor = _stat_sensor()
    mutable = cast("Any", sensor)
    mutable.entity_description = description
    mutable._reset_period = description.reset_period  # ruff: ignore[private-member-access]
    today = datetime(2026, 9, 30, 23, tzinfo=UTC)
    series = [1.0] * 30
    series[28] = 120431.96
    source: dict[str, Any] = {
        "pv1Egy": 120460.96,
        "totalSolarEnergy": 60.0,
        "unit": "kWh",
        "y1": series,
        "y": [2.0] * 30,
        "_request": {
            "dateType": "month",
            "beginDate": "2026-09-01",
            "endDate": "2026-09-30",
        },
    }
    verified = {
        f"2026-09-{day:02}": {"device_pv_stat": {"pv1Egy": 1.0}} for day in range(1, 30)
    }
    if period == DATE_TYPE_YEAR:
        today = datetime(2026, 10, 1, 1, tzinfo=UTC)
        verified["2026-09-30"] = {"device_pv_stat": {"pv1Egy": 1.0}}
        source = {
            "pv1Egy": 120475.96,
            "totalSolarEnergy": 120.0,
            "unit": "kWh",
            "y1": [5.0, 10.0, 0, 0, 0, 0, 0, 0, 120460.96, 0, 0, 0],
            "y": [10.0, 20.0, 0, 0, 0, 0, 0, 0, 90.0, 0, 0, 0],
            "_request": {
                "dateType": "year",
                "beginDate": "2026-01-01",
                "endDate": "2026-12-31",
            },
        }
    if missing_day:
        verified.pop("2026-09-29")
    original = deepcopy(source)
    payload = {description.section: source, "verified_day_statistics": verified}
    mutable.coordinator.data = {_DEVICE_ID: payload}
    context = replace(
        sensor._capture_refresh_context(payload),  # ruff: ignore[private-member-access]
        local_now=today,
        local_today=today.date(),
    )
    snapshot = sensor._refresh_cache(context, {})  # ruff: ignore[private-member-access]
    sensor._apply_cache_snapshot(snapshot)  # ruff: ignore[private-member-access]

    if missing_day:
        assert sensor.native_value is None
    else:
        expected = 30.0 if period == DATE_TYPE_MONTH else 45.0
        assert sensor.native_value == pytest.approx(expected)
    assert source == original


@pytest.mark.parametrize("series", [None, [], [31.0]])
@pytest.mark.parametrize("missing_day", [False, True])
def test_year_rebuild_does_not_require_cloud_month_series(
    series: list[float] | None, missing_day: bool
) -> None:
    """Fully dated daily evidence survives a missing cloud month series."""
    description = next(
        desc for desc in STAT_DESCRIPTIONS if desc.key == "device_pv1_year_energy"
    )
    sensor = _stat_sensor()
    mutable = cast("Any", sensor)
    mutable.entity_description = description
    mutable._reset_period = description.reset_period  # ruff: ignore[private-member-access]
    today = datetime(2026, 2, 3, 12, tzinfo=UTC)
    source: dict[str, Any] = {
        "unit": "kWh",
        "_request": {
            "dateType": "year",
            "beginDate": "2026-01-01",
            "endDate": "2026-12-31",
        },
    }
    if series is not None:
        source["y1"] = series
    verified = {
        f"2026-01-{day:02}": {"device_pv_stat": {"pv1Egy": 1.0}} for day in range(1, 32)
    }
    verified["2026-02-01"] = {"device_pv_stat": {"pv1Egy": 1.5}}
    verified["2026-02-02"] = {"device_pv_stat": {"pv1Egy": 2.0}}
    if missing_day:
        verified.pop("2026-02-02")
    payload = {description.section: source, "verified_day_statistics": verified}
    original = deepcopy(payload)
    mutable.coordinator.data = {_DEVICE_ID: payload}
    context = replace(
        sensor._capture_refresh_context(payload),  # ruff: ignore[private-member-access]
        local_now=today,
        local_today=today.date(),
        local_daily_raw=(3.0, "pv1Egy"),
    )
    snapshot = sensor._refresh_cache(context, {})  # ruff: ignore[private-member-access]
    sensor._apply_cache_snapshot(snapshot)  # ruff: ignore[private-member-access]

    rebuilt = sensor._current_open_month_or_year_with_local_day(  # ruff: ignore[private-member-access]
        description.section,
        description.stat_key,
        context=context,
        cloud_total=None,
    )
    if missing_day:
        assert rebuilt is None
    else:
        assert rebuilt is not None
        assert sensor.native_value == pytest.approx(37.5)
    assert payload == original


@pytest.mark.parametrize("period", [DATE_TYPE_MONTH, DATE_TYPE_YEAR])
@pytest.mark.parametrize("missing_day", [False, True])
@pytest.mark.parametrize("zero_days", [False, True])
@pytest.mark.parametrize("month_day", [1, 3])
def test_period_verified_days_replace_stale_high_cloud_totals(
    period: str, missing_day: bool, zero_days: bool, month_day: int
) -> None:
    """Only complete verified past days authorize lowering a cloud total."""
    description = next(
        desc for desc in STAT_DESCRIPTIONS if desc.key == f"device_pv1_{period}_energy"
    )
    sensor = _stat_sensor()
    mutable = cast("Any", sensor)
    mutable.entity_description = description
    mutable._reset_period = description.reset_period  # ruff: ignore[private-member-access]
    today = datetime(2026, 2, month_day, 12, tzinfo=UTC)
    month_source: dict[str, Any] = {
        "pv1Egy": 150.0,
        "totalSolarEnergy": 300.0,
        "unit": "kWh",
        "y1": [50.0, 50.0, 50.0],
        "y": [100.0, 100.0, 100.0],
        "_request": {
            "dateType": "month",
            "beginDate": "2026-02-01",
            "endDate": "2026-02-28",
        },
    }
    source = month_source
    if period == DATE_TYPE_YEAR:
        source = {
            "pv1Egy": 500.0,
            "totalSolarEnergy": 1000.0,
            "unit": "kWh",
            "y1": [200.0, 300.0],
            "y": [400.0, 600.0],
            "_request": {
                "dateType": "year",
                "beginDate": "2026-01-01",
                "endDate": "2026-12-31",
            },
        }
    verified = {
        f"2026-01-{day:02}": {"device_pv_stat": {"pv1Egy": 0.0 if zero_days else 1.0}}
        for day in range(1, 32)
    }
    verified["2026-02-01"] = {"device_pv_stat": {"pv1Egy": 0.0 if zero_days else 1.5}}
    verified["2026-02-02"] = {"device_pv_stat": {"pv1Egy": 0.0 if zero_days else 2.0}}
    if missing_day:
        missing_date = "2026-01-31" if month_day == 1 else "2026-02-02"
        verified.pop(missing_date)
    payload: dict[str, Any] = {
        "device_pv_stat_month": month_source,
        description.section: source,
        "verified_day_statistics": verified,
    }
    original = deepcopy(payload)
    mutable.coordinator.data = {_DEVICE_ID: payload}
    context = replace(
        sensor._capture_refresh_context(payload),  # ruff: ignore[private-member-access]
        local_now=today,
        local_today=today.date(),
        local_daily_raw=(50.0, "pv1Egy"),
    )
    snapshot = sensor._refresh_cache(context, {})  # ruff: ignore[private-member-access]
    sensor._apply_cache_snapshot(snapshot)  # ruff: ignore[private-member-access]
    expected = 50.0 if zero_days else (53.5 if period == DATE_TYPE_MONTH else 84.5)
    if month_day == 1:
        expected = (
            150.0 if period == DATE_TYPE_MONTH else (300.0 if zero_days else 331.0)
        )
    if missing_day:
        expected = 150.0 if period == DATE_TYPE_MONTH else 500.0
    assert sensor.native_value == pytest.approx(expected)
    assert payload == original


def test_period_last_reset_is_precomputed_before_ha_state_calculation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HA state serialization must not recalculate period metadata on the loop."""
    sensor = _stat_sensor()
    payload = sensor.coordinator.data[_DEVICE_ID]
    snapshot = sensor._refresh_cache(sensor._capture_refresh_context(payload), {})  # ruff: ignore[private-member-access]
    sensor._apply_cache_snapshot(snapshot)  # ruff: ignore[private-member-access]

    monkeypatch.setattr(
        sensor,
        "_compute_period_start",
        lambda _period: pytest.fail("last_reset was recomputed during state write"),
    )

    assert sensor.last_reset == snapshot.last_reset
    assert sensor.last_reset is not None


def test_week_period_preserves_explicit_current_zero() -> None:
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

    assert sensor.native_value == pytest.approx(0.0)
    assert sensor.extra_state_attributes["period_values"] == [0.0, None, None]
    assert None not in sensor.extra_state_attributes.values()


@pytest.mark.parametrize(
    ["sensor_key", "section", "stat_key", "unit", "series"],
    [
        [
            "ct_input_day_energy",
            "device_ct_stat_day",
            "totalInCtEnergy",
            APP_UNIT_KWH,
            {"y1": [], "y2": []},
        ],
        [
            "ct_output_day_energy",
            "device_ct_stat_day",
            "totalOutCtEnergy",
            APP_UNIT_KWH,
            {"y1": [], "y2": []},
        ],
        [
            "eps_input_day_energy",
            "device_eps_stat_day",
            "totalInEpsEnergy",
            "W",
            {"y": [0, 0], "y1": [0, 0], "y2": [0, 0]},
        ],
        [
            "eps_output_day_energy",
            "device_eps_stat_day",
            "totalOutEpsEnergy",
            "W",
            {"y": [0, 0], "y1": [0, 0], "y2": [0, 0]},
        ],
    ],
)
def test_ct_eps_day_scalar_zero_is_not_exposed_as_unknown(
    sensor_key: str,
    section: str,
    stat_key: str,
    unit: str,
    series: dict[str, list[int]],
) -> None:
    """An App scalar zero remains real without treating W curves as energy."""
    description = next(desc for desc in STAT_DESCRIPTIONS if desc.key == sensor_key)
    sensor = JackeryStatSensor.__new__(JackeryStatSensor)
    mutable = cast("Any", sensor)
    mutable.coordinator = SimpleNamespace(
        data={
            _DEVICE_ID: {
                section: {
                    APP_STAT_UNIT: unit,
                    stat_key: 0,
                    **series,
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
    snapshot = sensor._refresh_cache(sensor._capture_refresh_context(payload), {})  # ruff: ignore[private-member-access]
    sensor._apply_cache_snapshot(snapshot)  # ruff: ignore[private-member-access]

    assert sensor.native_value == pytest.approx(0.0)


@pytest.mark.parametrize(
    ["week_bucket", "month_bucket", "verified_bucket", "verified_count"],
    [
        [4.7, 4.7, 4.7, 3],
        [40.0, 40.0, 4.7, 3],
        [40.0, 4.7, 4.7, 3],
        [4.7, 40.0, 4.7, 3],
        [40.0, 40.0, 0.0, 3],
        [0.0, 1.0, 1.0, 0],
        [0.0, 1.0, 1.0, 2],
    ],
)
def test_week_period_uses_verified_completed_days(
    week_bucket: float,
    month_bucket: float,
    verified_bucket: float,
    verified_count: int,
) -> None:
    """Verified complete days replace stale charts, including a genuine zero."""
    description = next(
        desc for desc in STAT_DESCRIPTIONS if desc.key == "device_pv1_week_energy"
    )
    sensor = JackeryStatSensor.__new__(JackeryStatSensor)
    mutable = cast("Any", sensor)
    today = datetime(2026, 8, 13, tzinfo=UTC).date()
    week_start = today - timedelta(days=today.weekday())
    request = {
        APP_REQUEST_DATE_TYPE: DATE_TYPE_WEEK,
        APP_REQUEST_BEGIN_DATE: week_start.isoformat(),
        APP_REQUEST_END_DATE: (week_start + timedelta(days=6)).isoformat(),
    }
    month_section = description.section.replace("_week", "_month")
    completed_day_count = (today - week_start).days
    payload: dict[str, Any] = {
        description.section: {
            description.stat_key: (
                week_bucket + 0.15 if verified_count == completed_day_count else 40.0
            ),
            APP_CHART_SERIES_Y1: [
                0.0,
                week_bucket,
                0.0,
                0.15 if verified_count == completed_day_count else 0.0,
                0.0,
                0.0,
                0.0,
            ],
            APP_STAT_UNIT: APP_UNIT_KWH,
            APP_REQUEST_META: request,
        },
        month_section: {
            description.stat_key: month_bucket + 0.58,
            APP_CHART_SERIES_Y1: [0.23, month_bucket, 0.2, 0.15],
            APP_STAT_UNIT: APP_UNIT_KWH,
        },
        "verified_day_statistics": {
            "2026-08-10": {"device_pv_stat": {description.stat_key: 0.23}},
            "2026-08-11": {"device_pv_stat": {description.stat_key: verified_bucket}},
            "2026-08-12": {"device_pv_stat": {description.stat_key: 0.2}},
        },
    }
    payload["verified_day_statistics"] = dict(
        list(payload["verified_day_statistics"].items())[:verified_count]
    )
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

    context = replace(
        sensor._capture_refresh_context(payload),  # ruff: ignore[private-member-access]
        local_now=datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        local_today=today,
    )
    snapshot = sensor._refresh_cache(context, {})  # ruff: ignore[private-member-access]
    sensor._apply_cache_snapshot(snapshot)  # ruff: ignore[private-member-access]

    if verified_count < completed_day_count:
        assert sensor.native_value == pytest.approx(40.0)
        assert "fallback" not in sensor.extra_state_attributes
        return
    assert sensor.native_value == pytest.approx(0.58 + verified_bucket)
    assert sensor.extra_state_attributes["source_section"] == description.section
    assert (
        sensor.extra_state_attributes["fallback"]
        == "current_open_week_from_daily_buckets"
    )


def test_battery_week_replaces_stale_today_bucket_with_local_day_total() -> None:
    """The open week can never remain below its verified current-day total."""
    description = next(
        desc
        for desc in STAT_DESCRIPTIONS
        if desc.key == "battery_discharge_week_energy"
    )
    sensor = JackeryStatSensor.__new__(JackeryStatSensor)
    mutable = cast("Any", sensor)
    today = datetime(2026, 8, 21, tzinfo=UTC).date()
    week_start = today - timedelta(days=today.weekday())
    month_start = today.replace(day=1)
    month_section = description.section.replace(
        f"_{DATE_TYPE_WEEK}", f"_{DATE_TYPE_MONTH}"
    )
    payload = {
        description.section: {
            description.stat_key: 0.79,
            APP_CHART_SERIES_Y2: [0.13, 0.0, 0.0, 0.18, 0.48, 0.0, 0.0],
            APP_STAT_UNIT: APP_UNIT_KWH,
            APP_REQUEST_META: {
                APP_REQUEST_DATE_TYPE: DATE_TYPE_WEEK,
                APP_REQUEST_BEGIN_DATE: week_start.isoformat(),
                APP_REQUEST_END_DATE: (week_start + timedelta(days=6)).isoformat(),
            },
        },
        month_section: {
            description.stat_key: 18.62,
            APP_CHART_SERIES_Y2: [
                *([0.0] * 16),
                0.13,
                0.0,
                0.0,
                0.18,
                0.48,
                *([0.0] * 10),
            ],
            APP_STAT_UNIT: APP_UNIT_KWH,
            APP_REQUEST_META: {
                APP_REQUEST_DATE_TYPE: DATE_TYPE_MONTH,
                APP_REQUEST_BEGIN_DATE: month_start.isoformat(),
                APP_REQUEST_END_DATE: "2026-08-31",
            },
        },
        PAYLOAD_LOCAL_DAILY_ENERGY: {APP_DEVICE_STAT_BATTERY_DISCHARGE: 122},
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

    context = replace(
        sensor._capture_refresh_context(payload),  # ruff: ignore[private-member-access]
        local_now=datetime(2026, 8, 21, 16, 20, tzinfo=UTC),
        local_today=today,
    )
    snapshot = sensor._refresh_cache(context, {})  # ruff: ignore[private-member-access]
    sensor._apply_cache_snapshot(snapshot)  # ruff: ignore[private-member-access]

    assert sensor.native_value == pytest.approx(1.53)
    assert sensor.last_reset == datetime(2026, 8, 17, tzinfo=UTC)
    assert sensor.extra_state_attributes["source_section"] == description.section
    assert (
        sensor.extra_state_attributes["fallback"]
        == "current_open_week_from_daily_buckets"
    )
    assert None not in sensor.extra_state_attributes.values()


def test_ct_import_open_period_hierarchy_includes_current_local_day() -> None:
    """Open CT week/month/year totals include the latest 0.528 kWh day.

    The closed buckets come from ``device/stat/ct`` itself; the device grid
    side (``device/stat/onGrid``) is a different boundary and never feeds CT.
    """
    today = datetime(2026, 8, 21, tzinfo=UTC).date()
    payload = {
        "device_ct_stat_week": {
            "totalInCtEnergy": 0.02,
            APP_CHART_SERIES_Y1: [0.0, 0.0, 0.02, 0.0, 0.0, 0.0, 0.0],
            APP_STAT_UNIT: APP_UNIT_KWH,
            APP_REQUEST_META: {
                APP_REQUEST_DATE_TYPE: DATE_TYPE_WEEK,
                APP_REQUEST_BEGIN_DATE: "2026-08-17",
                APP_REQUEST_END_DATE: "2026-08-23",
            },
        },
        "device_ct_stat_month": {
            "totalInCtEnergy": 0.29,
            APP_CHART_SERIES_Y1: [
                0.14,
                0.06,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.01,
                0.0,
                0.05,
                0.0,
                0.0,
                0.01,
                0.0,
                0.0,
                0.0,
                0.0,
                0.02,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
                0.0,
            ],
            APP_STAT_UNIT: APP_UNIT_KWH,
            APP_REQUEST_META: {
                APP_REQUEST_DATE_TYPE: DATE_TYPE_MONTH,
                APP_REQUEST_BEGIN_DATE: "2026-08-01",
                APP_REQUEST_END_DATE: "2026-08-31",
            },
        },
        "device_ct_stat_year": {
            "totalInCtEnergy": 2.41,
            APP_CHART_SERIES_Y1: [
                0.0,
                0.0,
                0.0,
                0.0,
                0.86,
                0.55,
                0.71,
                0.29,
                0.0,
                0.0,
                0.0,
                0.0,
            ],
            APP_STAT_UNIT: APP_UNIT_KWH,
            APP_REQUEST_META: {
                APP_REQUEST_DATE_TYPE: DATE_TYPE_YEAR,
                APP_REQUEST_BEGIN_DATE: "2026-01-01",
                APP_REQUEST_END_DATE: "2026-12-31",
            },
        },
        PAYLOAD_LOCAL_DAILY_ENERGY: {FIELD_CT_TOTAL_PHASE_ENERGY: 528},
    }
    expected = {
        "ct_input_week_energy": (
            0.548,
            "current_open_week_from_daily_buckets",
        ),
        "ct_input_month_energy": (0.818, "current_open_month_from_daily_buckets"),
        "ct_input_year_energy": (
            2.938,
            "current_open_year_from_month_and_daily_buckets",
        ),
    }

    for sensor_key, (expected_value, expected_fallback) in expected.items():
        description = next(desc for desc in STAT_DESCRIPTIONS if desc.key == sensor_key)
        sensor = JackeryStatSensor.__new__(JackeryStatSensor)
        mutable = cast("Any", sensor)
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

        context = replace(
            sensor._capture_refresh_context(payload),  # ruff: ignore[private-member-access]
            local_now=datetime(2026, 8, 21, 16, 27, tzinfo=UTC),
            local_today=today,
        )
        snapshot = sensor._refresh_cache(context, {})  # ruff: ignore[private-member-access]
        sensor._apply_cache_snapshot(snapshot)  # ruff: ignore[private-member-access]

        assert sensor.native_value == pytest.approx(expected_value)
        assert sensor.extra_state_attributes["fallback"] == expected_fallback
        assert None not in sensor.extra_state_attributes.values()


def test_local_day_fallback_omits_non_applicable_null_attributes() -> None:
    """Local day totals expose provenance without JSON null placeholders."""
    description = next(
        desc
        for desc in STAT_DESCRIPTIONS
        if desc.key == "device_today_battery_discharge"
    )
    sensor = JackeryStatSensor.__new__(JackeryStatSensor)
    mutable = cast("Any", sensor)
    payload = {
        description.section: {},
        PAYLOAD_LOCAL_DAILY_ENERGY: {APP_DEVICE_STAT_BATTERY_DISCHARGE: 122},
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

    snapshot = sensor._refresh_cache(sensor._capture_refresh_context(payload), {})  # ruff: ignore[private-member-access]
    sensor._apply_cache_snapshot(snapshot)  # ruff: ignore[private-member-access]

    assert sensor.native_value == pytest.approx(1.22)
    assert sensor.extra_state_attributes["source_section"] == PAYLOAD_LOCAL_DAILY_ENERGY
    assert sensor.extra_state_attributes["fallback"] == "local_lifetime_delta"
    assert None not in sensor.extra_state_attributes.values()


def test_day_period_preserves_explicit_current_zero() -> None:
    """An explicitly dated HTTP zero remains valid with or without a local zero."""
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
    assert sensor.native_value == pytest.approx(0.0)

    mutable.coordinator.local_daily_energy_kwh = lambda _device_id, _metric_key: 0.0
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
    today = datetime.now(UTC).date()
    request_begin = today - timedelta(days=today.weekday())
    request = {
        APP_REQUEST_DATE_TYPE: DATE_TYPE_WEEK,
        APP_REQUEST_BEGIN_DATE: request_begin.isoformat(),
        APP_REQUEST_END_DATE: (request_begin + timedelta(days=6)).isoformat(),
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


def test_pv_revenue_periods_follow_app_system_pv_trends() -> None:
    """PV revenue must use the SysPvStatApi source displayed by the App."""
    expected_sections = {
        "pv_revenue_day": "pv_trends",
        "pv_revenue_week": "pv_trends_week",
        "pv_revenue_month": "pv_trends_month",
        "pv_revenue_year": "pv_trends_year",
    }

    actual_sections = {
        description.key: description.section
        for description in STAT_DESCRIPTIONS
        if description.key in expected_sections
    }

    assert actual_sections == expected_sections


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
    primary: float | str | None,
    fallback: float | str | None,
    *,
    key: str = "today_battery_energy",
    curve: list[int] | None = None,
) -> float | None:
    """Resolve a today battery value from its two independent HTTP sources."""
    description = next(desc for desc in STAT_DESCRIPTIONS if desc.key == key)
    fallback_section, fallback_key = description.fallback_sources[0]
    primary_source = {description.stat_key: primary} if primary is not None else {}
    fallback_source = {fallback_key: fallback} if fallback is not None else {}
    sections = {
        description.section: primary_source,
        fallback_section: fallback_source,
    }
    if curve is not None:
        curve_section, curve_key = description.fallback_sources[1]
        sections[curve_section] = {
            curve_key: 0,
            APP_CHART_SERIES_Y1: curve,
            APP_STAT_UNIT: "W",
        }
    sensor = JackeryStatSensor.__new__(JackeryStatSensor)
    mutable = cast("Any", sensor)
    mutable.coordinator = SimpleNamespace(
        data={_DEVICE_ID: sections},
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
    now = datetime(2026, 9, 29, 12, tzinfo=UTC)
    context = replace(
        sensor._capture_refresh_context(payload),  # ruff: ignore[private-member-access]
        local_now=now,
        local_today=now.date(),
    )
    snapshot = sensor._refresh_cache(context, {})  # ruff: ignore[private-member-access]
    sensor._apply_cache_snapshot(snapshot)  # ruff: ignore[private-member-access]
    return cast("float | None", sensor.native_value)


def test_today_battery_rejects_single_source_zero() -> None:
    """One fallback zero does not prove a real zero day total."""
    assert _today_battery_value(None, 0.0) is None


def test_today_battery_accepts_two_source_zero() -> None:
    """Two independent HTTP zero observations corroborate a real zero."""
    assert _today_battery_value(0.0, 0.0) == pytest.approx(0.0)


def test_today_battery_charge_replaces_stalled_system_zero() -> None:
    """Live 2026-09-27: systemStatistic stayed 0.00 while deviceStatistic counted."""
    value = _today_battery_value("0.00", "7.22", key="today_battery_charge_energy")
    assert value == pytest.approx(7.22)


def test_today_battery_charge_keeps_present_system_value() -> None:
    """A non-zero system value is authoritative; the fallback only fills zero."""
    value = _today_battery_value("2.94", "2.50", key="today_battery_charge_energy")
    assert value == pytest.approx(2.94)


def test_today_battery_charge_keeps_zero_without_positive_fallback() -> None:
    """Zero stays zero when the device statistic also reports zero."""
    value = _today_battery_value("0.00", "0.00", key="today_battery_charge_energy")
    assert value == pytest.approx(0.0)


def test_today_battery_charge_uses_measured_curve_when_cloud_totals_are_zero() -> None:
    """A measured W curve beats two stale 0 kWh Cloud day scalars."""
    value = _today_battery_value(
        "0.00",
        "0.00",
        key="today_battery_charge_energy",
        curve=[600] * 12 + [0] * 276,
    )
    assert value == pytest.approx(0.6)


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
    mutable._cached_last_reset = sensor._compute_period_start(cast("Any", reset_period))  # ruff: ignore[private-member-access]
    return sensor  # pyrefly: ignore [no-any-return-implicit]


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


@pytest.mark.parametrize(
    ["shares", "expected"],
    [
        [{"ac": 0, "home": 0, "pv": 0}, None],
        [{"ac": 0, "home": 2, "pv": 98}, 98],
        [{"ac": 0, "home": 0, "pv": 100}, 100],
    ],
)
def test_all_zero_share_split_is_unknown(
    shares: dict[str, int], expected: int | None
) -> None:
    """An all-zero split is the cloud's not-aggregated placeholder, not 0 %."""
    entity = SimpleNamespace(
        payload_section_for_sources=lambda _section: {"batterySources": shares}
    )
    value = _section_share(cast("Any", entity), "battery", "batterySources", "pv")
    assert value == expected
