"""Regression tests for statistic entity value passthrough."""

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
    mutable._device_id = _DEVICE_ID
    mutable.entity_description = description
    mutable._reset_period = description.reset_period
    mutable._cached_native_value = None
    mutable._cached_attrs = {}
    mutable._cached_source_section = description.section
    mutable._cached_last_reset = sensor._compute_period_start(description.reset_period)
    mutable._restored_lifetime_value = None
    return sensor


def test_stat_entity_does_not_clamp_negative_period_values() -> None:
    """Stats/trends quality decisions belong upstream, not in the entity."""
    sensor = _stat_sensor()

    payload = sensor.coordinator.data[_DEVICE_ID]
    context = sensor._capture_refresh_context(payload)
    snapshot = sensor._refresh_cache(context, {})
    sensor._apply_cache_snapshot(snapshot)

    assert sensor.native_value == pytest.approx(_NEGATIVE_KWH)


def test_period_last_reset_is_precomputed_before_ha_state_calculation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HA state serialization must not recalculate period metadata on the loop."""
    sensor = _stat_sensor()
    payload = sensor.coordinator.data[_DEVICE_ID]
    snapshot = sensor._refresh_cache(sensor._capture_refresh_context(payload), {})
    sensor._apply_cache_snapshot(snapshot)

    monkeypatch.setattr(
        sensor,
        "_compute_period_start",
        lambda _period: pytest.fail("last_reset was recomputed during state write"),
    )

    assert sensor.last_reset == snapshot.last_reset
    assert sensor.last_reset is not None


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
    mutable._device_id = _DEVICE_ID
    mutable.entity_description = description
    mutable._reset_period = description.reset_period
    mutable._cached_native_value = None
    mutable._cached_attrs = {}
    mutable._cached_source_section = description.section
    mutable._restored_lifetime_value = None

    payload = sensor.coordinator.data[_DEVICE_ID]
    context = sensor._capture_refresh_context(payload)
    snapshot = sensor._refresh_cache(context, {})
    sensor._apply_cache_snapshot(snapshot)

    assert sensor.native_value is None
    assert "period_values" not in sensor.extra_state_attributes
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
    mutable._device_id = _DEVICE_ID
    mutable.entity_description = description
    mutable._reset_period = description.reset_period
    mutable._cached_native_value = None
    mutable._cached_attrs = {}
    mutable._cached_source_section = description.section
    mutable._restored_lifetime_value = None

    payload = sensor.coordinator.data[_DEVICE_ID]
    snapshot = sensor._refresh_cache(sensor._capture_refresh_context(payload), {})
    sensor._apply_cache_snapshot(snapshot)

    assert sensor.native_value == pytest.approx(0.0)


def test_week_period_uses_larger_fully_covered_day_rebuild() -> None:
    """A complete daily rebuild exposes a stale positive App week total."""
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
    payload = {
        description.section: {
            description.stat_key: 4.85,
            APP_CHART_SERIES_Y1: [0.0, 4.7, 0.0, 0.15, 0.0, 0.0, 0.0],
            APP_STAT_UNIT: APP_UNIT_KWH,
            APP_REQUEST_META: request,
        },
        month_section: {
            description.stat_key: 5.28,
            APP_CHART_SERIES_Y1: [0.23, 4.7, 0.2, 0.15],
            APP_STAT_UNIT: APP_UNIT_KWH,
        },
        "verified_day_statistics": {
            "2026-08-10": {"device_pv_stat": {description.stat_key: 0.23}},
            "2026-08-11": {"device_pv_stat": {description.stat_key: 4.7}},
            "2026-08-12": {"device_pv_stat": {description.stat_key: 0.2}},
        },
    }
    mutable.coordinator = SimpleNamespace(
        data={_DEVICE_ID: payload},
        local_daily_energy_kwh=lambda _device_id, _metric_key: None,
    )
    mutable.hass = SimpleNamespace(config=SimpleNamespace(time_zone="UTC"))
    mutable._device_id = _DEVICE_ID
    mutable.entity_description = description
    mutable._reset_period = description.reset_period
    mutable._cached_native_value = None
    mutable._cached_attrs = {}
    mutable._cached_source_section = description.section
    mutable._restored_lifetime_value = None

    context = replace(
        sensor._capture_refresh_context(payload),
        local_now=datetime(2026, 8, 13, 12, 0, tzinfo=UTC),
        local_today=today,
    )
    snapshot = sensor._refresh_cache(context, {})
    sensor._apply_cache_snapshot(snapshot)

    assert sensor.native_value == pytest.approx(5.28)
    assert sensor.extra_state_attributes["source_section"] == description.section
    assert (
        sensor.extra_state_attributes["fallback"]
        == "current_open_week_from_daily_buckets"
    )


def test_battery_week_replaces_stale_today_bucket_with_local_day_total() -> None:
    """The open week can never remain below its verified current-day total."""
    description = next(
        desc for desc in STAT_DESCRIPTIONS if desc.key == "battery_discharge_week_energy"
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
    mutable._device_id = _DEVICE_ID
    mutable.entity_description = description
    mutable._reset_period = description.reset_period
    mutable._cached_native_value = None
    mutable._cached_attrs = {}
    mutable._cached_source_section = description.section
    mutable._restored_lifetime_value = None

    context = replace(
        sensor._capture_refresh_context(payload),
        local_now=datetime(2026, 8, 21, 16, 20, tzinfo=UTC),
        local_today=today,
    )
    snapshot = sensor._refresh_cache(context, {})
    sensor._apply_cache_snapshot(snapshot)

    assert sensor.native_value == pytest.approx(1.53)
    assert sensor.last_reset == datetime(2026, 8, 17, tzinfo=UTC)
    assert sensor.extra_state_attributes["source_section"] == description.section
    assert (
        sensor.extra_state_attributes["fallback"]
        == "current_open_week_from_daily_buckets"
    )
    assert None not in sensor.extra_state_attributes.values()


def test_ct_import_open_period_hierarchy_includes_current_local_day() -> None:
    """Open CT week/month/year totals include the latest 0.528 kWh day."""
    today = datetime(2026, 8, 21, tzinfo=UTC).date()
    payload = {
        "device_home_stat_week": {
            "totalInGridEnergy": 0.02,
            APP_CHART_SERIES_Y1: [0.0, 0.0, 0.02, 0.0, 0.0, 0.0, 0.0],
            APP_STAT_UNIT: APP_UNIT_KWH,
            APP_REQUEST_META: {
                APP_REQUEST_DATE_TYPE: DATE_TYPE_WEEK,
                APP_REQUEST_BEGIN_DATE: "2026-08-17",
                APP_REQUEST_END_DATE: "2026-08-23",
            },
        },
        "device_home_stat_month": {
            "totalInGridEnergy": 0.29,
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
        "device_home_stat_year": {
            "totalInGridEnergy": 2.41,
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
        "ct_input_month_energy": (0.818, "current_open_month_with_local_day"),
        "ct_input_year_energy": (2.938, "current_open_year_with_local_month"),
    }

    for sensor_key, (expected_value, expected_fallback) in expected.items():
        description = next(
            desc for desc in STAT_DESCRIPTIONS if desc.key == sensor_key
        )
        sensor = JackeryStatSensor.__new__(JackeryStatSensor)
        mutable = cast("Any", sensor)
        mutable.coordinator = SimpleNamespace(
            data={_DEVICE_ID: payload},
            local_daily_energy_kwh=lambda _device_id, _metric_key: None,
        )
        mutable.hass = SimpleNamespace(config=SimpleNamespace(time_zone="UTC"))
        mutable._device_id = _DEVICE_ID
        mutable.entity_description = description
        mutable._reset_period = description.reset_period
        mutable._cached_native_value = None
        mutable._cached_attrs = {}
        mutable._cached_source_section = description.section
        mutable._restored_lifetime_value = None

        context = replace(
            sensor._capture_refresh_context(payload),
            local_now=datetime(2026, 8, 21, 16, 27, tzinfo=UTC),
            local_today=today,
        )
        snapshot = sensor._refresh_cache(context, {})
        sensor._apply_cache_snapshot(snapshot)

        assert sensor.native_value == pytest.approx(expected_value)
        assert sensor.extra_state_attributes["fallback"] == expected_fallback
        assert None not in sensor.extra_state_attributes.values()


def test_local_day_fallback_omits_non_applicable_null_attributes() -> None:
    """Local day totals expose provenance without JSON null placeholders."""
    description = next(
        desc for desc in STAT_DESCRIPTIONS if desc.key == "device_today_battery_discharge"
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
    mutable._device_id = _DEVICE_ID
    mutable.entity_description = description
    mutable._reset_period = description.reset_period
    mutable._cached_native_value = None
    mutable._cached_attrs = {}
    mutable._cached_source_section = description.section
    mutable._restored_lifetime_value = None

    snapshot = sensor._refresh_cache(sensor._capture_refresh_context(payload), {})
    sensor._apply_cache_snapshot(snapshot)

    assert sensor.native_value == pytest.approx(1.22)
    assert sensor.extra_state_attributes["source_section"] == PAYLOAD_LOCAL_DAILY_ENERGY
    assert sensor.extra_state_attributes["fallback"] == "local_lifetime_delta"
    assert None not in sensor.extra_state_attributes.values()


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
    mutable._device_id = _DEVICE_ID
    mutable.entity_description = description
    mutable._reset_period = description.reset_period
    mutable._cached_native_value = None
    mutable._cached_attrs = {}
    mutable._cached_source_section = description.section
    mutable._restored_lifetime_value = None

    context = sensor._capture_refresh_context(payload)
    snapshot = sensor._refresh_cache(context, {})
    sensor._apply_cache_snapshot(snapshot)
    assert sensor.native_value is None

    mutable.coordinator.local_daily_energy_kwh = lambda _device_id, _metric_key: 0.0
    context = sensor._capture_refresh_context(payload)
    snapshot = sensor._refresh_cache(context, {})
    sensor._apply_cache_snapshot(snapshot)
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
    mutable._device_id = _DEVICE_ID
    mutable.entity_description = description
    mutable._reset_period = description.reset_period
    mutable._cached_native_value = None
    mutable._cached_attrs = {}
    mutable._cached_source_section = description.section
    mutable._restored_lifetime_value = None

    payload = sensor.coordinator.data[_DEVICE_ID]
    context = sensor._capture_refresh_context(payload)
    snapshot = sensor._refresh_cache(context, {})
    sensor._apply_cache_snapshot(snapshot)

    assert sensor.native_value == pytest.approx(1.25)
    assert sensor.extra_state_attributes["request"] == request


def test_pv_revenue_periods_follow_app_system_pv_trends() -> None:
    """PV revenue must use the SysPvStatApi source displayed by the App."""
    expected_sections = {
        "pv_revenue_day": "pv_trends_day",
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
    mutable._device_id = _DEVICE_ID
    mutable.entity_description = description
    mutable._reset_period = description.reset_period
    mutable._cached_native_value = None
    mutable._cached_attrs = {}
    mutable._cached_source_section = description.section
    mutable._restored_lifetime_value = None

    payload = sensor.coordinator.data[_DEVICE_ID]
    context = sensor._capture_refresh_context(payload)
    snapshot = sensor._refresh_cache(context, {})
    sensor._apply_cache_snapshot(snapshot)

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
    mutable._device_id = _DEVICE_ID
    mutable.entity_description = description
    mutable._reset_period = description.reset_period
    mutable._cached_native_value = None
    mutable._cached_attrs = {}
    mutable._cached_source_section = description.section
    mutable._restored_lifetime_value = None

    payload = sensor.coordinator.data[_DEVICE_ID]
    context = sensor._capture_refresh_context(payload)
    snapshot = sensor._refresh_cache(context, {})
    sensor._apply_cache_snapshot(snapshot)

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
    primary_source = {description.stat_key: primary} if primary is not None else {}
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
    mutable._device_id = _DEVICE_ID
    mutable.entity_description = description
    mutable._reset_period = description.reset_period
    mutable._cached_native_value = None
    mutable._cached_attrs = {}
    mutable._cached_source_section = description.section
    mutable._restored_lifetime_value = None

    payload = sensor.coordinator.data[_DEVICE_ID]
    context = sensor._capture_refresh_context(payload)
    snapshot = sensor._refresh_cache(context, {})
    sensor._apply_cache_snapshot(snapshot)
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
    mutable._device_id = _DEVICE_ID
    mutable.entity_description = description
    mutable._reset_period = reset_period
    # All period totals (day/week/month/year) are TOTAL so HA compiles their
    # long-term statistics (reverted 2026-07-18).
    mutable._attr_state_class = SensorStateClass.TOTAL
    mutable._cached_native_value = None
    mutable._cached_attrs = {}
    mutable._cached_source_section = description.section
    mutable._cached_last_reset = sensor._compute_period_start(cast("Any", reset_period))
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
