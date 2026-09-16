"""Final behavioral branch coverage for day-energy helpers in util.py."""

from datetime import date, datetime

import pytest

from custom_components.jackery_solarvault import util
from custom_components.jackery_solarvault.const import (
    APP_CHART_LABELS,
    APP_CHART_SERIES_Y,
    APP_CHART_SERIES_Y1,
    APP_CHART_SERIES_Y2,
    APP_REQUEST_BEGIN_DATE_ALT,
    APP_REQUEST_END_DATE_ALT,
    APP_REQUEST_META,
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_HOME_TRENDS,
    APP_SECTION_PV_STAT,
    APP_STAT_TOTAL_CHARGE,
    APP_STAT_TOTAL_DISCHARGE,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    APP_STAT_UNIT,
    APP_UNIT_KWH,
    DATE_TYPE_DAY,
    PAYLOAD_HOME_TRENDS,
)

_MINUTES_AT_23_59 = 23 * 60 + 59
_FIRST_FIVE_MINUTE_KWH_AT_120_W = 0.01


def test_day_chart_minute_parses_boundaries_and_rejects_end_marker() -> None:
    """Only real in-day H:MM labels resolve to a minute offset."""
    parse = util._parse_day_chart_minute

    assert parse("0:00") == 0
    assert parse("23:59") == _MINUTES_AT_23_59
    assert parse("24:00") is None
    assert parse("23:60") is None
    assert parse("not-a-time") is None
    assert parse(1200) is None


def test_day_power_sample_minute_prefers_label_then_falls_back_to_index() -> None:
    """Valid labels win; malformed or absent labels use five-minute indexes."""
    sample_minute = util._day_power_sample_minute

    assert sample_minute(["1:30"], 0) == 90
    assert sample_minute(["bad"], 1) == 5
    assert sample_minute(None, 2) == 10
    assert sample_minute(None, 288) is None


def test_day_power_sample_energy_value_preserves_directional_semantics() -> None:
    """Signed combined and split battery curves keep their documented direction."""
    sample_value = util._day_power_sample_energy_value
    battery_day = f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}"

    assert (
        sample_value(None, battery_day, APP_STAT_TOTAL_CHARGE, APP_CHART_SERIES_Y1) == 0
    )
    assert (
        sample_value({}, battery_day, APP_STAT_TOTAL_CHARGE, APP_CHART_SERIES_Y1)
        is None
    )
    assert (
        sample_value(-5, battery_day, APP_STAT_TOTAL_CHARGE, APP_CHART_SERIES_Y1) == 0
    )
    assert (
        sample_value(-5, battery_day, APP_STAT_TOTAL_DISCHARGE, APP_CHART_SERIES_Y1)
        == 5
    )
    assert (
        sample_value(5, battery_day, APP_STAT_TOTAL_DISCHARGE, APP_CHART_SERIES_Y1) == 0
    )
    assert (
        sample_value(5, battery_day, APP_STAT_TOTAL_DISCHARGE, APP_CHART_SERIES_Y2) == 5
    )
    assert (
        sample_value(
            -5, APP_SECTION_PV_STAT, APP_STAT_TOTAL_SOLAR_ENERGY, APP_CHART_SERIES_Y
        )
        == 0
    )


def test_reconcile_rounded_day_values_handles_positive_and_negative_delta() -> None:
    """Rounding correction adds to or removes from trailing populated buckets."""
    reconcile = util._reconcile_rounded_day_values

    assert reconcile([], 1.0) == []
    assert reconcile([0.1, 0.2], 0.31) == [0.1, 0.21]
    assert reconcile([0.1, 0.2], 0.15) == [0.1, 0.05]
    assert reconcile([0.0, 0.0], 0.01) == [0.0, 0.01]
    assert reconcile([0.1], -1.0) == [0.0]


def test_resolve_day_request_window_rejects_missing_reversed_and_future_dates() -> None:
    """A day curve is importable only when its request window is coherent."""
    resolve = util._resolve_day_request_window
    today = date(2026, 8, 10)
    now = datetime(2026, 8, 10, 10, 30)

    assert resolve({}, today=today, now=now) is None
    assert (
        resolve(
            {
                APP_REQUEST_META: {
                    APP_REQUEST_BEGIN_DATE_ALT: "2026-08-10",
                    APP_REQUEST_END_DATE_ALT: "2026-08-09",
                }
            },
            today=today,
            now=now,
        )
        is None
    )
    assert (
        resolve(
            {
                APP_REQUEST_META: {
                    APP_REQUEST_BEGIN_DATE_ALT: "2026-08-11",
                    APP_REQUEST_END_DATE_ALT: "2026-08-11",
                }
            },
            today=today,
            now=now,
        )
        is None
    )

    resolved = resolve(
        {
            APP_REQUEST_META: {
                APP_REQUEST_BEGIN_DATE_ALT: "2026-08-10",
                APP_REQUEST_END_DATE_ALT: "2026-08-10",
            }
        },
        today=today,
        now=now,
    )
    assert resolved == (today, now)


def test_day_power_energy_points_cut_off_future_current_day_samples() -> None:
    """Current-day curves never import labeled samples beyond the supplied clock."""
    today = date(2026, 8, 10)
    source = {
        APP_CHART_SERIES_Y: [120, 120],
        APP_CHART_LABELS: ["0:00", "12:00"],
        APP_STAT_UNIT: "w",
        APP_REQUEST_META: {
            APP_REQUEST_BEGIN_DATE_ALT: today.isoformat(),
            APP_REQUEST_END_DATE_ALT: today.isoformat(),
        },
    }

    points = util.day_power_energy_points(
        source,
        f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}",
        APP_STAT_TOTAL_SOLAR_ENERGY,
        today=today,
        now=datetime(2026, 8, 10, 10),
    )

    assert len(points) == 1
    assert points[0].start_date == datetime(2026, 8, 10)
    assert points[0].value == pytest.approx(_FIRST_FIVE_MINUTE_KWH_AT_120_W)


@pytest.mark.parametrize("bucket_minutes", [0, -5, 17])
def test_day_power_energy_points_rejects_invalid_bucket_sizes(
    bucket_minutes: int,
) -> None:
    """Output buckets must be positive and divide a full day exactly."""
    assert (
        util.day_power_energy_points(
            {},
            f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}",
            APP_STAT_TOTAL_SOLAR_ENERGY,
            bucket_minutes=bucket_minutes,
        )
        == []
    )


def test_day_power_energy_points_rejects_unproven_scalar_and_unknown_unit() -> None:
    """A scalar without positive curve energy and an unknown unit remain unimportable."""
    today = date(2026, 8, 10)
    base = {
        APP_CHART_SERIES_Y: [0],
        APP_STAT_TOTAL_SOLAR_ENERGY: 1,
        APP_REQUEST_META: {
            APP_REQUEST_BEGIN_DATE_ALT: today.isoformat(),
            APP_REQUEST_END_DATE_ALT: today.isoformat(),
        },
    }
    section = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}"

    assert (
        util.day_power_energy_points(
            {**base, APP_STAT_UNIT: APP_UNIT_KWH},
            section,
            APP_STAT_TOTAL_SOLAR_ENERGY,
            today=today,
            now=datetime(2026, 8, 10, 12),
        )
        == []
    )
    assert (
        util.day_power_energy_points(
            {**base, APP_STAT_UNIT: "joule"},
            section,
            APP_STAT_TOTAL_SOLAR_ENERGY,
            today=today,
            now=datetime(2026, 8, 10, 12),
        )
        == []
    )


def test_historical_day_payload_maps_home_trends_without_empty_sources() -> None:
    """Home trends retain their live section key while empty sources are omitted."""
    source = {"totalLoad": 3}

    assert util.historical_day_payload_from_sources({
        APP_SECTION_HOME_TRENDS: source,
        "empty": {},
    }) == {PAYLOAD_HOME_TRENDS: source}
