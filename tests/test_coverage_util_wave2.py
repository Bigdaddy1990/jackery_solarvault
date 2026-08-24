"""Behavioral coverage for remaining high-value utility edge paths."""

from datetime import date, datetime
import json
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest

from custom_components.jackery_solarvault import util
from custom_components.jackery_solarvault.const import (
    APP_CHART_LABELS,
    APP_CHART_SERIES_Y,
    APP_REQUEST_BEGIN_DATE_ALT,
    APP_REQUEST_DATE_TYPE_ALT,
    APP_REQUEST_END_DATE_ALT,
    APP_REQUEST_META,
    APP_SECTION_PV_STAT,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    APP_STAT_UNIT,
    APP_UNIT_KWH,
    CT_PHASE_POWER_PAIRS,
    CT_TOTAL_POWER_PAIR,
    DATE_TYPE_WEEK,
    FIELD_IN_GRID_SIDE_PW,
    FIELD_OUT_GRID_SIDE_PW,
    REDACTED_VALUE,
)


def test_numeric_normalizers_reject_ambiguous_and_non_finite_values() -> None:
    """Malformed locale numbers, booleans, and infinities never become readings."""
    assert util.safe_float(" 1,25 ") == pytest.approx(1.25)
    assert util.safe_float("1,2,3") is None
    assert util.safe_float("nan") is None
    assert util.safe_float(float("inf")) is None
    assert util.safe_float(True) is None
    assert util.safe_int(4.0) == 4
    assert util.safe_int(4.5) is None
    assert util.safe_int(False) is None


def test_boolean_normalizer_handles_finite_numbers_and_text_markers() -> None:
    """Boolean coercion accepts documented markers but rejects unknown values."""
    assert util.safe_bool(-2) is True
    assert util.safe_bool(0.0) is False
    assert util.safe_bool(" YES ") is True
    assert util.safe_bool("off") is False
    assert util.safe_bool(float("nan")) is None
    assert util.safe_bool("sometimes") is None


def test_config_option_helpers_use_defaults_for_missing_or_bad_values() -> None:
    """Config option readers remain cache-compatible when fields are absent."""
    empty_entry = SimpleNamespace()
    invalid_entry = SimpleNamespace(
        options={"enabled": "unknown", "count": "1.5", "name": None},
        data={},
    )

    assert util.config_entry_bool_option(empty_entry, "enabled", True) is True
    assert util.config_entry_int_option(empty_entry, "count", 7) == 7
    assert util.config_entry_str_option(empty_entry, "name", "fallback") == "fallback"
    assert util.config_entry_bool_option(invalid_entry, "enabled", False) is False
    assert util.config_entry_int_option(invalid_entry, "count", 7) == 7
    assert util.config_entry_str_option(invalid_entry, "name", "fallback") == "fallback"


def test_redaction_scrubs_echoed_sensitive_literals_from_all_json_shapes() -> None:
    """Secrets remain hidden even when errors echo them outside sensitive keys."""
    secret = "device-secret-123"
    external_secret = "cached-secret-456"
    payload = {
        "token": secret,
        "message": f"broker rejected {secret}",
        "items": ({"password": 123456, "echo": 123456}, True),
        "external_error": f"cache contains {external_secret}",
    }

    redacted = util.redacted_json_safe_payload(
        payload,
        sensitive_sources=({"password": external_secret},),
    )

    assert redacted == {
        "token": REDACTED_VALUE,
        "message": f"broker rejected {REDACTED_VALUE}",
        "items": [
            {"password": REDACTED_VALUE, "echo": REDACTED_VALUE},
            True,
        ],
        "external_error": f"cache contains {REDACTED_VALUE}",
    }


def test_payload_debug_writer_emits_one_redacted_json_line(tmp_path: Path) -> None:
    """The debug writer serializes safely without leaking credentials."""
    debug_path = tmp_path / "nested" / "payload.jsonl"

    util.append_payload_debug_line(
        debug_path,
        {"password": "mqtt-secret", "when": date(2026, 8, 10)},
    )

    lines = debug_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0]) == {
        "password": REDACTED_VALUE,
        "when": "2026-08-10",
    }


def test_payload_debug_batch_writer_preserves_every_redacted_event(
    tmp_path: Path,
) -> None:
    """One batched disk write retains every ordered payload-debug event."""
    debug_path = tmp_path / "nested" / "payload.jsonl"

    util.append_payload_debug_lines(
        debug_path,
        [
            {"sequence": 1, "token": "first-secret"},
            {"sequence": 2, "password": "second-secret"},
        ],
    )

    assert [
        json.loads(line) for line in debug_path.read_text(encoding="utf-8").splitlines()
    ] == [
        {"sequence": 1, "token": REDACTED_VALUE},
        {"password": REDACTED_VALUE, "sequence": 2},
    ]


def test_chart_series_debug_reports_only_real_series_and_metadata() -> None:
    """Chart diagnostics preserve raw shapes while parsing usable numbers."""
    request = {APP_REQUEST_DATE_TYPE_ALT: DATE_TYPE_WEEK}
    result = util.chart_series_debug({
        APP_CHART_SERIES_Y: ["1,5", None, "bad", 2],
        "y1": "not-a-series",
        APP_CHART_LABELS: ["Mon", "Tue"],
        APP_REQUEST_META: request,
    })

    assert result[APP_CHART_SERIES_Y]["raw_count"] == 4
    assert result[APP_CHART_SERIES_Y]["parsed_sum"] == pytest.approx(3.5)
    assert result[APP_CHART_SERIES_Y]["items"][1]["parsed_float"] is None
    assert "y1" not in result
    assert result["labels"] == ["Mon", "Tue"]
    assert result["request"] is request
    assert util.chart_series_debug([]) == {}


def test_payload_debug_rotation_ignores_file_removed_during_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A concurrent rotation cannot turn a debug write into a warning."""
    debug_path = tmp_path / "payload.jsonl"
    debug_path.write_text("old payload", encoding="utf-8")
    monkeypatch.setattr(util, "PAYLOAD_DEBUG_LOG_MAX_BYTES", 1)

    def _remove_then_fail(self: Path, _backup: Path) -> Path:
        self.unlink()
        raise FileNotFoundError

    monkeypatch.setattr(Path, "replace", _remove_then_fail)
    util.append_payload_debug_line(debug_path, {"event": "current"})

    assert "could not rotate payload debug log" not in caplog.text
    assert [
        json.loads(line) for line in debug_path.read_text(encoding="utf-8").splitlines()
    ] == [{"event": "current"}]


def test_calendar_bucket_iterators_return_empty_for_reversed_ranges() -> None:
    """Reversed calendar ranges cannot manufacture month, week, or year buckets."""
    start = date(2027, 2, 1)
    end = date(2026, 1, 31)

    assert util.iter_calendar_months(start, end) == []
    assert util.iter_calendar_weeks(start, end) == []
    assert util.iter_calendar_years(start, end) == []


def test_completed_point_filter_drops_invalid_and_current_period_starts() -> None:
    """Only completed dated buckets survive non-day Recorder imports."""
    invalid = util.TrendStatisticPoint(
        cast("date | datetime", "not-a-date"),
        1.0,
    )
    previous = util.TrendStatisticPoint(date(2026, 8, 9), 2.0)
    current = util.TrendStatisticPoint(datetime(2026, 8, 10, 3), 3.0)

    filtered = util.filter_completed_app_points(
        [invalid, previous, current],
        DATE_TYPE_WEEK,
        "week",
        date(2026, 8, 10),
    )

    assert filtered == [previous]


def test_trend_points_skip_invalid_values_and_request_overflow() -> None:
    """A period chart imports only parseable buckets inside its request range."""
    source = {
        APP_CHART_SERIES_Y: [1, None, "bad", 2],
        APP_STAT_UNIT: APP_UNIT_KWH,
        APP_REQUEST_META: {
            APP_REQUEST_DATE_TYPE_ALT: DATE_TYPE_WEEK,
            APP_REQUEST_BEGIN_DATE_ALT: "2026-08-01",
            APP_REQUEST_END_DATE_ALT: "2026-08-03",
        },
    }

    points = util.trend_series_points(
        source,
        f"{APP_SECTION_PV_STAT}_{DATE_TYPE_WEEK}",
        APP_STAT_TOTAL_SOLAR_ENERGY,
        today=date(2026, 8, 10),
    )

    assert points == [util.TrendStatisticPoint(date(2026, 8, 1), 1.0)]


def test_smart_meter_modes_keep_total_and_phase_requirements_separate() -> None:
    """Net modes can use meter totals while gross modes require every phase."""
    total_positive, total_negative = CT_TOTAL_POWER_PAIR
    totals_only = {total_positive: 100, total_negative: 250}

    assert util.smart_meter_net_power(totals_only) == pytest.approx(-150)
    assert util.calculated_smart_meter_power(totals_only, "net_import") == 0
    assert util.calculated_smart_meter_power(totals_only, "net_export") == 150
    assert util.calculated_smart_meter_power(totals_only, "gross_flow") is None

    phases: dict[str, float] = {}
    for index, (positive, negative) in enumerate(CT_PHASE_POWER_PAIRS, start=1):
        phases[positive] = float(index * 10)
        phases[negative] = float(index)
    assert util.calculated_smart_meter_power(phases, "unsupported") is None


def test_grid_net_power_requires_both_grid_side_measurements() -> None:
    """Device grid net power never substitutes inverter-side measurements."""
    assert util.jackery_grid_net_power({FIELD_IN_GRID_SIDE_PW: "200"}) is None
    assert (
        util.jackery_grid_net_power({
            FIELD_IN_GRID_SIDE_PW: "200",
            FIELD_OUT_GRID_SIDE_PW: "75",
        })
        == 125
    )


def test_backfill_date_parser_accepts_timestamp_prefix_only() -> None:
    """Persisted date markers accept ISO timestamps but reject other cache shapes."""
    assert util.parse_statistics_backfill_date("2026-08-10T23:59:00Z") == date(
        2026, 8, 10
    )
    assert util.parse_statistics_backfill_date("2026-13-40") is None
    assert util.parse_statistics_backfill_date(date(2026, 8, 10)) is None
