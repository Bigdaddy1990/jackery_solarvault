"""Behavioral unit tests for pure helper functions in util.py.

These exercise the payload-math, period/date, coercion, sorting, chart-series,
and power-flow helpers without any Home Assistant recorder dependency.
"""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import NamedTuple

import pytest

from custom_components.jackery_solarvault import util
from custom_components.jackery_solarvault.const import (
    APP_CHART_SERIES_Y,
    APP_CHART_SERIES_Y1,
    APP_CHART_SERIES_Y2,
    APP_CHART_STAT_PERIODS,
    APP_REQUEST_BEGIN_DATE_ALT,
    APP_REQUEST_DATE_TYPE_ALT,
    APP_REQUEST_END_DATE_ALT,
    APP_REQUEST_META,
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_BATTERY_TRENDS,
    APP_SECTION_CT_STAT,
    APP_SECTION_EPS_STAT,
    APP_SECTION_HOME_STAT,
    APP_SECTION_PV_STAT,
    APP_STAT_PV1_ENERGY,
    APP_STAT_PV_PROFIT,
    APP_STAT_TOTAL_CHARGE,
    APP_STAT_TOTAL_CT_INPUT_ENERGY,
    APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
    APP_STAT_TOTAL_DISCHARGE,
    APP_STAT_TOTAL_IN_EPS_ENERGY,
    APP_STAT_TOTAL_IN_GRID_ENERGY,
    APP_STAT_TOTAL_OUT_EPS_ENERGY,
    APP_STAT_TOTAL_OUT_GRID_ENERGY,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    APP_STAT_TOTAL_SOLAR_REVENUE,
    APP_STAT_TOTAL_TREND_CHARGE_ENERGY,
    APP_STAT_UNIT,
    APP_UNIT_KWH,
    CONF_ENABLE_PAYLOAD_DEBUG_LOG,
    CT_PHASE_POWER_PAIRS,
    CT_TOTAL_POWER_PAIR,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_SN,
    FIELD_ID,
    FIELD_IN_GRID_SIDE_PW,
    FIELD_OTHER_LOAD_PW,
    FIELD_OUT_GRID_SIDE_PW,
    FIELD_SN,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SYSTEM,
)

_CT_TOTAL_POS, _CT_TOTAL_NEG = CT_TOTAL_POWER_PAIR

_METER_IMPORT = 500.0
_METER_EXPORT = 800.0
_REPORTED_LOAD = 123.0
_JACKERY_INPUT = 40.0
_JACKERY_OUTPUT = 700.0
_ZERO = 0.0
_THREE = 3.0
_FIVE = 5.0


# ---------------------------------------------------------------------------
# config-entry option readers
# ---------------------------------------------------------------------------
def _entry(options: dict | None = None, data: dict | None = None) -> object:
    """Build a config-entry-like stub with options and legacy data mappings."""
    return SimpleNamespace(options=options or {}, data=data or {})


def test_config_entry_bool_option_prefers_options_over_data() -> None:
    """Options mapping wins over legacy data for the same key."""
    entry = _entry(options={"flag": "true"}, data={"flag": "false"})
    assert util.config_entry_bool_option(entry, "flag", default=False) is True


def test_config_entry_bool_option_falls_back_to_data_then_default() -> None:
    """Legacy data supplies the value; absent keys yield the default."""
    from_data = util.config_entry_bool_option(
        _entry(data={"flag": "on"}), "flag", default=False
    )
    assert from_data is True
    assert util.config_entry_bool_option(_entry(), "missing", default=True) is True


def test_config_entry_bool_option_returns_default_when_unparseable() -> None:
    """Unrecognized boolean text falls back to the provided default."""
    entry = _entry(options={"flag": "maybe"})
    assert util.config_entry_bool_option(entry, "flag", default=True) is True


def test_config_entry_str_option_reads_and_defaults() -> None:
    """String option is coerced to str, falling back to data then default."""
    assert util.config_entry_str_option(_entry(options={"k": 5}), "k", "d") == "5"
    assert util.config_entry_str_option(_entry(data={"k": "x"}), "k", "d") == "x"
    assert util.config_entry_str_option(_entry(), "k", "d") == "d"


def test_config_entry_int_option_parses_and_defaults() -> None:
    """Integer option parses numeric strings, falling back to data then default."""
    expected_options = 7
    expected_data = 3
    expected_default = 9
    assert (
        util.config_entry_int_option(_entry(options={"n": "7"}), "n", 0)
        == expected_options
    )
    assert util.config_entry_int_option(_entry(data={"n": 3}), "n", 0) == expected_data
    assert util.config_entry_int_option(_entry(), "n", 9) == expected_default


def test_config_entry_int_option_returns_default_on_bad_value() -> None:
    """Non-integer values raise internally and yield the default."""
    expected = 4
    assert (
        util.config_entry_int_option(_entry(options={"n": "abc"}), "n", 4) == expected
    )
    assert (
        util.config_entry_int_option(_entry(options={"n": object()}), "n", 4)
        == expected
    )


def test_entry_bool_option_delegates() -> None:
    """The thin alias resolves the same value as the underlying reader."""
    entry = _entry(options={CONF_ENABLE_PAYLOAD_DEBUG_LOG: "yes"})
    resolved = util.entry_bool_option(
        entry, CONF_ENABLE_PAYLOAD_DEBUG_LOG, default=False
    )
    assert resolved is True


# ---------------------------------------------------------------------------
# subdevice branding / online state / text helpers
# ---------------------------------------------------------------------------
def test_subdevice_branding_unknown_and_invalid() -> None:
    """Empty, non-string, or unknown scan names yield no branding."""
    assert util.subdevice_branding("") == (None, None)
    assert util.subdevice_branding(123) == (None, None)
    assert util.subdevice_branding("not-a-real-scan-name") == (None, None)


def test_nonblank_text_variants() -> None:
    """Blank text becomes None; other values are trimmed and stringified."""
    assert util.nonblank_text(None) is None
    assert util.nonblank_text("  ") is None
    assert util.nonblank_text("  hi ") == "hi"
    assert util.nonblank_text(42) == "42"


def test_first_nonblank_text_and_fallback() -> None:
    """The first non-blank value is returned, else the fallback."""
    assert util.first_nonblank_text(None, "", "  ", "x") == "x"
    assert util.first_nonblank_text(None, fallback="fb") == "fb"


def test_first_nonblank_and_int() -> None:
    """Text/int variants of the first-nonblank helpers coerce as documented."""
    expected_twelve = 12
    expected_three = 3
    expected_one = 1
    assert util.first_nonblank(None, "  ", "y") == "y"
    assert util.first_nonblank() is None
    assert util.first_nonblank_int(None, "  ", "12") == expected_twelve
    assert util.first_nonblank_int(3.0) == expected_three
    assert util.first_nonblank_int(3.5) is None
    assert util.first_nonblank_int(True) is None
    assert util.first_nonblank_int("1.0") == expected_one
    assert util.first_nonblank_int("nope") is None


def test_jackery_online_state() -> None:
    """Online/offline markers map to booleans; unknown text is None."""
    assert util.jackery_online_state("online") is True
    assert util.jackery_online_state("OFFLINE") is False
    assert util.jackery_online_state(1) is True
    assert util.jackery_online_state("weird") is None


# ---------------------------------------------------------------------------
# time parsing
# ---------------------------------------------------------------------------
def test_utc_now_is_timezone_aware() -> None:
    """utc_now returns a UTC-aware datetime."""
    assert util.utc_now().tzinfo is UTC


def test_parse_utc_datetime_from_naive_and_aware() -> None:
    """Naive datetimes gain UTC; aware datetimes are preserved."""
    naive = datetime(2026, 1, 2, 3, 4, 5)
    assert util.parse_utc_datetime(naive).tzinfo is UTC
    aware = datetime(2026, 1, 2, tzinfo=UTC)
    assert util.parse_utc_datetime(aware) == aware


def test_parse_utc_datetime_from_seconds_and_millis() -> None:
    """Numeric timestamps parse as seconds; large magnitudes as milliseconds."""
    assert util.parse_utc_datetime(0) == datetime(1970, 1, 1, tzinfo=UTC)
    millis = util.parse_utc_datetime(1_700_000_000_000)
    seconds = util.parse_utc_datetime(1_700_000_000)
    assert millis == seconds


def test_parse_utc_datetime_from_strings() -> None:
    """Numeric and ISO-8601 strings (with trailing Z) parse to UTC."""
    assert util.parse_utc_datetime("0") == datetime(1970, 1, 1, tzinfo=UTC)
    assert util.parse_utc_datetime("2026-01-02T00:00:00Z") == datetime(
        2026, 1, 2, tzinfo=UTC
    )


def test_parse_utc_datetime_rejects_bad_input() -> None:
    """Empty/invalid strings raise ValueError; unsupported types raise TypeError."""
    with pytest.raises(ValueError, match="empty"):
        util.parse_utc_datetime("")
    with pytest.raises(ValueError, match="invalid"):
        util.parse_utc_datetime("not-a-date")
    with pytest.raises(TypeError):
        util.parse_utc_datetime([1, 2])  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# app period range / bounds / request kwargs
# ---------------------------------------------------------------------------
def test_validate_app_period_date_type() -> None:
    """Supported period types pass through; others raise ValueError."""
    assert util.validate_app_period_date_type(DATE_TYPE_DAY) == DATE_TYPE_DAY
    with pytest.raises(ValueError, match="Unsupported"):
        util.validate_app_period_date_type("decade")


def test_app_period_range_all_types() -> None:
    """Each period type resolves to its inclusive begin/end date pair."""
    today = date(2026, 7, 8)  # a Wednesday
    assert util.app_period_range(DATE_TYPE_DAY, today=today) == (today, today)
    assert util.app_period_range(DATE_TYPE_WEEK, today=today) == (
        date(2026, 7, 6),
        date(2026, 7, 12),
    )
    assert util.app_period_range(DATE_TYPE_MONTH, today=today) == (
        date(2026, 7, 1),
        date(2026, 7, 31),
    )
    assert util.app_period_range(DATE_TYPE_YEAR, today=today) == (
        date(2026, 1, 1),
        date(2026, 12, 31),
    )


def test_app_period_date_bounds_defaults_and_explicit() -> None:
    """Defaults derive from the period; explicit bounds are validated and echoed."""
    today = date(2026, 7, 8)
    assert util.app_period_date_bounds(DATE_TYPE_DAY, today=today) == (
        "2026-07-08",
        "2026-07-08",
    )
    assert util.app_period_date_bounds(
        DATE_TYPE_MONTH,
        begin_date="2026-07-01",
        end_date=date(2026, 7, 15),
        today=today,
    ) == ("2026-07-01", "2026-07-15")


def test_app_period_date_bounds_rejects_reversed_and_empty() -> None:
    """Reversed, empty, or malformed bounds raise ValueError."""
    with pytest.raises(ValueError, match="before or equal"):
        util.app_period_date_bounds(
            DATE_TYPE_DAY, begin_date="2026-07-10", end_date="2026-07-01"
        )
    with pytest.raises(ValueError, match="must not be empty"):
        util.app_period_date_bounds(DATE_TYPE_DAY, begin_date="  ")
    with pytest.raises(ValueError, match="ISO date"):
        util.app_period_date_bounds(DATE_TYPE_DAY, begin_date="07/2026")


def test_app_period_bound_accepts_datetime() -> None:
    """Datetime bounds are reduced to their calendar date."""
    bounds = util.app_period_date_bounds(
        DATE_TYPE_DAY,
        begin_date=datetime(2026, 7, 8, 12, tzinfo=UTC),
        end_date=datetime(2026, 7, 8, 23, tzinfo=UTC),
    )
    assert bounds == ("2026-07-08", "2026-07-08")


def test_app_period_request_kwargs() -> None:
    """Period request kwargs carry the date type and inclusive bounds."""
    kwargs = util.app_period_request_kwargs(DATE_TYPE_DAY, today=date(2026, 7, 8))
    assert kwargs[APP_REQUEST_DATE_TYPE_ALT] == DATE_TYPE_DAY
    assert kwargs[APP_REQUEST_BEGIN_DATE_ALT] == "2026-07-08"
    assert kwargs[APP_REQUEST_END_DATE_ALT] == "2026-07-08"


def test_app_month_request_kwargs() -> None:
    """Month kwargs span the first through last calendar day of the month."""
    kwargs = util.app_month_request_kwargs(2026, 2)
    assert kwargs[APP_REQUEST_DATE_TYPE_ALT] == DATE_TYPE_MONTH
    assert kwargs[APP_REQUEST_BEGIN_DATE_ALT] == "2026-02-01"
    assert kwargs[APP_REQUEST_END_DATE_ALT] == "2026-02-28"


def test_app_month_request_kwargs_rejects_bad_month() -> None:
    """Out-of-range month numbers raise ValueError."""
    with pytest.raises(ValueError, match="month"):
        util.app_month_request_kwargs(2026, 13)


def test_app_year_request_kwargs() -> None:
    """Year kwargs span Jan 1 through Dec 31 of the requested year."""
    kwargs = util.app_year_request_kwargs(2026)
    assert kwargs[APP_REQUEST_BEGIN_DATE_ALT] == "2026-01-01"
    assert kwargs[APP_REQUEST_END_DATE_ALT] == "2026-12-31"


# ---------------------------------------------------------------------------
# backfill date windows / recovery
# ---------------------------------------------------------------------------
def test_statistics_http_backfill_dates_rolling_window() -> None:
    """The rolling window excludes today and spans window_days completed days."""
    today = date(2026, 7, 8)
    dates = util.statistics_http_backfill_dates(today, window_days=3)
    assert dates == [date(2026, 7, 5), date(2026, 7, 6), date(2026, 7, 7)]


def test_statistics_http_backfill_dates_empty_and_current_year() -> None:
    """A zero window is empty; current-year mode starts at Jan 1."""
    today = date(2026, 7, 8)
    assert util.statistics_http_backfill_dates(today, window_days=0) == []
    year = util.statistics_http_backfill_dates(
        today, window_days=3, include_current_year=True
    )
    assert year[0] == date(2026, 1, 1)
    assert year[-1] == date(2026, 7, 7)


def test_statistics_http_backfill_dates_start_after_end_returns_empty() -> None:
    """When the computed start is past the end day the window is empty."""
    today = date(2026, 1, 1)
    assert util.statistics_http_backfill_dates(today, window_days=-5) == []


def test_parse_statistics_backfill_date() -> None:
    """ISO date prefixes parse; non-strings and bad text yield None."""
    assert util.parse_statistics_backfill_date("2026-07-08T00:00") == date(2026, 7, 8)
    assert util.parse_statistics_backfill_date("bad") is None
    assert util.parse_statistics_backfill_date(123) is None


def test_statistics_current_year_recovery_january_and_other_year() -> None:
    """January and prior-year success markers never trigger recovery."""
    assert (
        util.statistics_current_year_recovery_needed(
            last_success=date(2026, 1, 5),
            last_repair=None,
            failed_bucket_count=0,
            today=date(2026, 1, 8),
        )
        is False
    )
    assert (
        util.statistics_current_year_recovery_needed(
            last_success=date(2025, 7, 5),
            last_repair=None,
            failed_bucket_count=0,
            today=date(2026, 7, 8),
        )
        is False
    )


def test_statistics_current_year_recovery_pending_and_complete() -> None:
    """Pending failures/no repair need recovery; a same-month repair completes it."""
    today = date(2026, 7, 8)
    assert (
        util.statistics_current_year_recovery_needed(
            last_success=date(2026, 7, 5),
            last_repair=None,
            failed_bucket_count=2,
            today=today,
        )
        is True
    )
    assert (
        util.statistics_current_year_recovery_needed(
            last_success=date(2026, 7, 5),
            last_repair=None,
            failed_bucket_count=0,
            today=today,
        )
        is True
    )
    assert (
        util.statistics_current_year_recovery_needed(
            last_success=date(2026, 7, 5),
            last_repair=date(2026, 7, 6),
            failed_bucket_count=0,
            today=today,
        )
        is False
    )


# ---------------------------------------------------------------------------
# calendar iterators
# ---------------------------------------------------------------------------
def test_iter_calendar_months_spans_year_boundary() -> None:
    """Month iteration includes every first-of-month across a year boundary."""
    months = util.iter_calendar_months(date(2025, 11, 20), date(2026, 2, 3))
    assert months == [
        date(2025, 11, 1),
        date(2025, 12, 1),
        date(2026, 1, 1),
        date(2026, 2, 1),
    ]


def test_iter_calendar_weeks() -> None:
    """Week iteration yields the Monday of each intersecting week."""
    weeks = util.iter_calendar_weeks(date(2026, 7, 8), date(2026, 7, 20))
    assert weeks == [date(2026, 7, 6), date(2026, 7, 13), date(2026, 7, 20)]


def test_iter_calendar_years() -> None:
    """Year iteration yields the inclusive integer year range."""
    assert util.iter_calendar_years(date(2024, 5, 1), date(2026, 1, 1)) == [
        2024,
        2025,
        2026,
    ]


# ---------------------------------------------------------------------------
# chart period meta / name prefix / stat row
# ---------------------------------------------------------------------------
def test_app_chart_period_meta_known_and_unknown() -> None:
    """Known period types resolve bucket meta; unknown ones return None."""
    known_date_type = APP_CHART_STAT_PERIODS[0][0]
    assert util.app_chart_period_meta(known_date_type) is not None
    assert util.app_chart_period_meta("nonsense") is None


def test_app_chart_name_prefix_precedence_and_fallback() -> None:
    """System device name wins; empty payloads fall back to a device id label."""
    named = util.app_chart_name_prefix(
        "dev1", {PAYLOAD_SYSTEM: {FIELD_DEVICE_NAME: "Sys"}}
    )
    assert named == "Sys"
    assert util.app_chart_name_prefix("dev1", {}) == "Jackery dev1"


def test_stat_row_start_datetime_and_scalar() -> None:
    """Datetime rows return an epoch timestamp; scalars parse; None stays None."""
    dt = datetime(2026, 1, 1, tzinfo=UTC)
    assert util.stat_row_start({"start": dt}) == dt.timestamp()
    assert util.stat_row_start({"start": "5"}) == _FIVE
    assert util.stat_row_start({"start": None}) is None


# ---------------------------------------------------------------------------
# entity_targets_for_app_points / filter_completed_app_points
# ---------------------------------------------------------------------------


class _Point(NamedTuple):
    """Minimal app-point stub exposing only a start_date."""

    start_date: object


def test_filter_completed_app_points_day_passthrough() -> None:
    """Day imports return their points unchanged."""
    points = [_Point(date(2026, 7, 8))]
    assert (
        util.filter_completed_app_points(points, "day", "day", date(2026, 7, 8))
        is points
    )


def test_filter_completed_app_points_excludes_current_and_future() -> None:
    """Non-day imports drop today's bucket and undated points."""
    today = date(2026, 7, 8)
    points = [
        _Point(date(2026, 7, 7)),
        _Point(datetime(2026, 7, 8, tzinfo=UTC)),
        _Point("not-a-date"),
    ]
    result = util.filter_completed_app_points(points, "week", "week", today)
    assert [p.start_date for p in result] == [date(2026, 7, 7)]


def test_filter_completed_app_points_year_reset() -> None:
    """Year-reset imports drop buckets in the current month or later."""
    today = date(2026, 7, 8)
    points = [_Point(date(2026, 6, 1)), _Point(date(2026, 7, 1))]
    result = util.filter_completed_app_points(points, "month", "year", today)
    assert [p.start_date for p in result] == [date(2026, 6, 1)]


def test_historical_day_payload_from_sources() -> None:
    """Section sources are re-keyed onto day payload keys, dropping empties."""
    payload = util.historical_day_payload_from_sources({
        APP_SECTION_PV_STAT: {"a": 1},
        "empty": {},
    })
    assert payload == {f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}": {"a": 1}}


# ---------------------------------------------------------------------------
# numeric coercion
# ---------------------------------------------------------------------------
def test_safe_float() -> None:
    """safe_float parses numbers and comma decimals, rejecting bad input."""
    expected = 1.5
    assert util.safe_float(None) is None
    assert util.safe_float(3) == _THREE
    assert util.safe_float("1,5") == expected
    assert util.safe_float("1.5") == expected
    assert util.safe_float("1,5,6") is None
    assert util.safe_float("  ") is None
    assert util.safe_float("bad") is None
    assert util.safe_float(object()) is None  # type: ignore[arg-type]


def test_safe_int() -> None:
    """safe_int keeps exact integers/integral floats, rejecting everything else."""
    expected = 8
    assert util.safe_int(None) is None
    assert util.safe_int(True) is None
    assert util.safe_int(8) == expected
    assert util.safe_int(8.0) == expected
    assert util.safe_int(8.9) is None
    assert util.safe_int(float("nan")) is None
    assert util.safe_int("8") == expected
    assert util.safe_int("8.0") is None
    assert util.safe_int([1]) is None


def test_safe_bool() -> None:
    """safe_bool interprets numeric and textual truthiness, else None."""
    assert util.safe_bool(None) is None
    assert util.safe_bool(True) is True
    assert util.safe_bool(0) is False
    assert util.safe_bool(5) is True
    assert util.safe_bool("yes") is True
    assert util.safe_bool("off") is False
    assert util.safe_bool("maybe") is None


# ---------------------------------------------------------------------------
# subdevice sorting helpers
# ---------------------------------------------------------------------------
def test_smart_plug_serial_and_sorting() -> None:
    """Plug serials are trimmed; plugs sort by identity, dropping id-less ones."""
    assert util.smart_plug_serial("not-a-dict") is None
    assert util.smart_plug_serial({FIELD_SN: " abc "}) == "abc"
    plugs = [{FIELD_SN: "b"}, {FIELD_SN: "a"}, {"no": "id"}]
    assert util.sorted_smart_plugs(plugs) == [{FIELD_SN: "a"}, {FIELD_SN: "b"}]
    assert util.sorted_smart_plugs("nope") == []


def test_meter_head_and_circuit_and_subdevice_sorting() -> None:
    """Meter heads, circuits, and subdevices sort by their stable identities."""
    assert util.meter_head_serial({FIELD_DEVICE_SN: "m1"}) == "m1"
    assert util.sorted_meter_heads([{FIELD_SN: "z"}, {FIELD_SN: "a"}]) == [
        {FIELD_SN: "a"},
        {FIELD_SN: "z"},
    ]
    assert util.circuit_id({FIELD_ID: "c1"}) == "c1"
    assert util.circuit_id("x") is None
    assert util.sorted_circuits([{FIELD_ID: "2"}, {FIELD_ID: "1"}]) == [
        {FIELD_ID: "1"},
        {FIELD_ID: "2"},
    ]
    assert util.sub_device_serial({FIELD_SN: "s"}) == "s"
    assert util.sub_device_serial({FIELD_DEVICE_ID: "cloud-id"}) is None
    assert util.sorted_sub_devices([{FIELD_SN: "b"}, {FIELD_SN: "a"}]) == [
        {FIELD_SN: "a"},
        {FIELD_SN: "b"},
    ]
    assert util.sorted_sub_devices("nope") == []


def test_stable_subdevice_key() -> None:
    """Identities normalize to safe suffixes, falling back to the index."""
    assert util.stable_subdevice_key("plug", "AB:cd 12", 0) == "plug_ab_cd_12"
    assert util.stable_subdevice_key("plug", None, 3) == "plug_3"
    assert util.stable_subdevice_key("plug", "!!!", 7) == "plug_7"


# ---------------------------------------------------------------------------
# statistic ids
# ---------------------------------------------------------------------------
def test_statistic_id_part() -> None:
    """Id parts are lowercased/slugified; blank inputs become 'unknown'."""
    assert util.statistic_id_part("Foo Bar!!") == "foo_bar"
    assert util.statistic_id_part("") == "unknown"
    assert util.statistic_id_part(None) == "unknown"


def test_external_trend_statistic_id() -> None:
    """External ids join normalized domain/device/metric/bucket parts."""
    built = util.external_trend_statistic_id("sensor", "Dev 1", "PV Energy", "day")
    assert built == "sensor:dev_1_pv_energy_day"


# ---------------------------------------------------------------------------
# period-type inference
# ---------------------------------------------------------------------------
def test_is_day_period_payload_by_suffix_and_request() -> None:
    """Day detection uses the section suffix and falls back to request meta."""
    assert util.is_day_period_payload({}, "pv_stat_day") is True
    assert util.is_day_period_payload({}, "pv_stat_week") is False
    request_source = {APP_REQUEST_META: {APP_REQUEST_DATE_TYPE_ALT: DATE_TYPE_DAY}}
    assert util.is_day_period_payload(request_source, "pv_stat") is True
    assert util.is_day_period_payload({}, "pv_stat") is False


def test_is_device_year_period_section() -> None:
    """Year device sections require a year request type and a device prefix."""
    source = {APP_REQUEST_META: {APP_REQUEST_DATE_TYPE_ALT: DATE_TYPE_YEAR}}
    assert util.is_device_year_period_section(source, APP_SECTION_PV_STAT) is True
    assert util.is_device_year_period_section(source, "some_other_section") is False
    assert util.is_device_year_period_section({}, APP_SECTION_PV_STAT) is False


# ---------------------------------------------------------------------------
# compact-year expansion
# ---------------------------------------------------------------------------
def test_compact_year_parts() -> None:
    """Compact year buckets pack two integer month values ("13.26" = 13 and 26).

    Anchored by the documented cloud totals (audit 2026-07-24): "40,96" sums to
    136 with its sibling months, never 40.96 kWh — the separator packs two
    whole-number buckets. Values without a packed non-zero fraction stay a
    plain scalar in the current bucket.
    """
    assert util._compact_year_parts("13.26") == (13.0, 26.0)  # ruff: ignore[private-member-access]
    assert util._compact_year_parts("2.00") == (0.0, 2.0)  # ruff: ignore[private-member-access]
    assert util._compact_year_parts("3") == (0.0, 3.0)  # ruff: ignore[private-member-access]
    assert util._compact_year_parts(None) is None  # ruff: ignore[private-member-access]
    assert util._compact_year_parts(True) is None  # ruff: ignore[private-member-access]
    assert util._compact_year_parts(float("nan")) is None  # ruff: ignore[private-member-access]


def test_effective_trend_series_values_normalizes() -> None:
    """Series values coerce to floats (bad entries become 0.0), rounded to 5dp."""
    section = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}"
    source = {APP_CHART_SERIES_Y: ["1.5", "bad", 2]}
    assert util.effective_trend_series_values(
        source, section, APP_STAT_TOTAL_SOLAR_ENERGY
    ) == [1.5, 0.0, 2.0]
    assert (
        util.effective_trend_series_values({}, section, APP_STAT_TOTAL_SOLAR_ENERGY)
        is None
    )


def test_effective_period_total_value_scalar() -> None:
    """Non-year sections return the parsed scalar total."""
    section = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}"
    expected = 12.5
    total = util.effective_period_total_value(
        {APP_STAT_TOTAL_SOLAR_ENERGY: "12.5"},
        section,
        APP_STAT_TOTAL_SOLAR_ENERGY,
    )
    assert total == expected


# ---------------------------------------------------------------------------
# power flow
# ---------------------------------------------------------------------------
def _ct(net: float) -> dict:
    """Build a CT payload whose total net power equals ``net`` watts."""
    if net >= 0:
        return {_CT_TOTAL_POS: net, _CT_TOTAL_NEG: 0}
    return {_CT_TOTAL_POS: 0, _CT_TOTAL_NEG: -net}


def test_directional_power_value() -> None:
    """Directional power sums positive keys and subtracts negative ones."""
    expected = 7.0
    assert util.directional_power_value({"a": 10, "b": 3}, ("a",), ("b",)) == expected
    assert util.directional_power_value({}, ("a",), ("b",)) is None


def test_smart_meter_net_power_total_and_phases() -> None:
    """Net meter power reads the total pair, signed for import/export."""
    assert util.smart_meter_net_power(_ct(_METER_IMPORT)) == _METER_IMPORT
    assert util.smart_meter_net_power(_ct(-_METER_EXPORT)) == -_METER_EXPORT
    assert util.smart_meter_net_power({}) is None


def test_calculated_smart_meter_power_modes() -> None:
    """Derived calculations clamp import/export and reject unknown modes."""
    assert (
        util.calculated_smart_meter_power(_ct(_METER_IMPORT), "net_import")
        == _METER_IMPORT
    )
    assert util.calculated_smart_meter_power(_ct(_METER_IMPORT), "net_export") == _ZERO
    assert (
        util.calculated_smart_meter_power(_ct(-_METER_EXPORT), "net_export")
        == _METER_EXPORT
    )
    assert util.calculated_smart_meter_power({}, "net_import") is None
    assert util.calculated_smart_meter_power(_ct(_METER_IMPORT), "unknown_mode") is None


def test_first_power_value_and_nonzero() -> None:
    """first_power_value returns the first present; nonzero prefers non-zero."""
    src = {"a": 0, "b": 5}
    assert util.first_power_value(src, "a", "b") == _ZERO
    assert util.first_nonzero_power_value(src, "a", "b") == _FIVE
    assert util.first_nonzero_power_value({"a": 0}, "a") == _ZERO
    assert util.first_power_value({}, "a") is None


def test_jackery_power_helpers() -> None:
    """Device power readers pull the documented grid-side fields."""
    props = {
        FIELD_OTHER_LOAD_PW: _REPORTED_LOAD,
        FIELD_IN_GRID_SIDE_PW: _JACKERY_INPUT,
        FIELD_OUT_GRID_SIDE_PW: _JACKERY_OUTPUT,
    }
    expected_net = int(_JACKERY_INPUT - _JACKERY_OUTPUT)
    assert util.jackery_reported_home_load_power(props) == _REPORTED_LOAD
    assert util.jackery_grid_side_input_power(props) == _JACKERY_INPUT
    assert util.jackery_grid_side_output_power(props) == _JACKERY_OUTPUT
    assert util.jackery_grid_net_power(props) == expected_net
    assert util.jackery_grid_net_power({FIELD_IN_GRID_SIDE_PW: 10}) is None


def test_corrected_home_consumption_none_when_no_inputs() -> None:
    """No CT or device power yields no corrected home-consumption result."""
    assert util.jackery_corrected_home_consumption_power({}, {}) is None


# ---------------------------------------------------------------------------
# chart-series key mapping
# ---------------------------------------------------------------------------
def test_chart_series_key_for_stat() -> None:
    """Section/stat pairs map to their chart-series keys, else None."""
    assert (
        util._chart_series_key_for_stat(APP_SECTION_PV_STAT, APP_STAT_PV1_ENERGY)  # ruff: ignore[private-member-access]
        == APP_CHART_SERIES_Y1
    )
    assert (
        util._chart_series_key_for_stat(  # ruff: ignore[private-member-access]
            APP_SECTION_PV_STAT, APP_STAT_TOTAL_SOLAR_ENERGY
        )
        == APP_CHART_SERIES_Y
    )
    assert (
        util._chart_series_key_for_stat(  # ruff: ignore[private-member-access]
            APP_SECTION_HOME_STAT, APP_STAT_TOTAL_IN_GRID_ENERGY
        )
        == APP_CHART_SERIES_Y1
    )
    assert util._chart_series_key_for_stat("mystery", "x") is None  # ruff: ignore[private-member-access]


def test_trend_series_key_requires_period_suffix() -> None:
    """Series keys resolve only for period-suffixed sections."""
    section = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}"
    assert (
        util.trend_series_key(section, APP_STAT_TOTAL_SOLAR_ENERGY)
        == APP_CHART_SERIES_Y
    )
    assert (
        util.trend_series_key(APP_SECTION_PV_STAT, APP_STAT_TOTAL_SOLAR_ENERGY) is None
    )


def test_series_contains_negative_samples() -> None:
    """Negative-sample detection scans a numeric chart series list."""
    assert util._series_contains_negative_samples({"y1": [1, -2, 3]}, "y1") is True  # ruff: ignore[private-member-access]
    assert util._series_contains_negative_samples({"y1": [1, 2]}, "y1") is False  # ruff: ignore[private-member-access]
    assert util._series_contains_negative_samples({"y1": "x"}, "y1") is False  # ruff: ignore[private-member-access]


def test_trend_series_total_sums_series() -> None:
    """A kWh series total is the rounded sum of its samples."""
    section = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}"
    source = {
        APP_CHART_SERIES_Y: [1.0, 2.0, 3.0],
        APP_STAT_UNIT: APP_UNIT_KWH,
    }
    expected = 6.0
    assert (
        util.trend_series_total(source, section, APP_STAT_TOTAL_SOLAR_ENERGY)
        == expected
    )


def test_trend_series_total_wrong_unit_returns_none() -> None:
    """A non-kWh unit disqualifies the chart-series total."""
    section = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}"
    source = {APP_CHART_SERIES_Y: [1.0], APP_STAT_UNIT: "w"}
    assert util.trend_series_total(source, section, APP_STAT_TOTAL_SOLAR_ENERGY) is None


def test_trend_series_total_ct_server_total_fallback() -> None:
    """CT sections fall back to the server-reported scalar total."""
    section = f"{APP_SECTION_CT_STAT}_{DATE_TYPE_MONTH}"
    source = {APP_STAT_TOTAL_CT_INPUT_ENERGY: "9.0"}
    expected = 9.0
    assert (
        util.trend_series_total(source, section, APP_STAT_TOTAL_CT_INPUT_ENERGY)
        == expected
    )


def test_trend_series_has_value_and_payload_has_value() -> None:
    """Presence checks succeed for populated kWh series and day scalars."""
    section = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}"
    source = {APP_CHART_SERIES_Y: [1.0], APP_STAT_UNIT: APP_UNIT_KWH}
    assert util.trend_series_has_value(source, section, APP_STAT_TOTAL_SOLAR_ENERGY)
    assert util.trend_payload_has_value(source, section, APP_STAT_TOTAL_SOLAR_ENERGY)
    day_section = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}"
    assert util.trend_series_has_value(
        {APP_STAT_TOTAL_SOLAR_ENERGY: "3"}, day_section, APP_STAT_TOTAL_SOLAR_ENERGY
    )


def test_day_power_series_key() -> None:
    """Day payloads resolve a power-curve series key; other periods do not."""
    day_section = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}"
    assert (
        util.day_power_series_key({}, day_section, APP_STAT_TOTAL_SOLAR_ENERGY)
        == APP_CHART_SERIES_Y
    )
    month_section = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}"
    assert (
        util.day_power_series_key({}, month_section, APP_STAT_TOTAL_SOLAR_ENERGY)
        is None
    )


# ---------------------------------------------------------------------------
# task-plan / signed-curve classifiers / normalize
# ---------------------------------------------------------------------------
def test_task_plan_value_search_order() -> None:
    """Values resolve from top-level, then body, then task list, else None."""
    top = 1
    body = 2
    task = 3
    assert util.task_plan_value({"a": 1}, "a") == top
    assert util.task_plan_value({"body": {"a": 2}}, "a") == body
    assert util.task_plan_value({"tasks": [{"a": 3}]}, "a") == task
    assert util.task_plan_value({}, "a") is None


def test_signed_curve_classifiers() -> None:
    """Home grid energy day totals may be distributed; other sections may not."""
    assert util._can_distribute_scalar_day_total(  # ruff: ignore[private-member-access]
        APP_SECTION_HOME_STAT, APP_STAT_TOTAL_OUT_GRID_ENERGY
    )
    assert not util._can_distribute_scalar_day_total(APP_SECTION_PV_STAT, "x")  # ruff: ignore[private-member-access]


def test_normalize_account() -> None:
    """Account identifiers are trimmed of surrounding whitespace."""
    assert util.normalize_account("  user@example.com  ") == "user@example.com"


# ---------------------------------------------------------------------------
# coordinator signature / dedupe
# ---------------------------------------------------------------------------
def test_coordinator_entity_signature_empty() -> None:
    """Missing or empty coordinator data yields an empty signature."""
    assert util.coordinator_entity_signature(None) == ()
    assert util.coordinator_entity_signature({}) == ()


def test_coordinator_entity_signature_shape() -> None:
    """Each signature entry leads with its device id."""
    sig = util.coordinator_entity_signature({"dev1": {PAYLOAD_PROPERTIES: {"a": 1}}})
    assert sig[0][0] == "dev1"


class _Ent(NamedTuple):
    """Minimal entity stub exposing a unique_id."""

    unique_id: str | None


def test_append_unique_entity_dedupes() -> None:
    """Entities append once; duplicate unique ids are skipped."""
    entities: list = []
    seen: set[str] = set()
    assert util.append_unique_entity(entities, seen, _Ent("u1"))
    assert not util.append_unique_entity(entities, seen, _Ent("u1"))
    assert len(entities) == 1


# ---------------------------------------------------------------------------
# additional cheap pure-branch coverage
# ---------------------------------------------------------------------------
def _phase_ct(a: float, b: float, c: float) -> dict:
    """Build a CT payload with explicit signed per-phase powers."""
    payload: dict = {}
    for (pos_key, neg_key), value in zip(CT_PHASE_POWER_PAIRS, (a, b, c), strict=True):
        if value >= 0:
            payload[pos_key] = value
            payload[neg_key] = 0
        else:
            payload[pos_key] = 0
            payload[neg_key] = -value
    return payload


def test_signed_phase_power_values() -> None:
    """Per-phase signed values follow the import/export convention."""
    assert util.signed_phase_power_values(_phase_ct(100, -50, 0)) == [
        100.0,
        -50.0,
        0.0,
    ]
    assert util.signed_phase_power_values({}) is None


def test_calculated_smart_meter_power_gross_modes() -> None:
    """Gross calculations aggregate per-phase imports, exports, and flow."""
    ct = _phase_ct(100, -50, 30)
    expected_import = 130.0
    expected_export = 50.0
    expected_flow = 180.0
    assert util.calculated_smart_meter_power(ct, "gross_import") == expected_import
    assert util.calculated_smart_meter_power(ct, "gross_export") == expected_export
    assert util.calculated_smart_meter_power(ct, "gross_flow") == expected_flow
    assert util.calculated_smart_meter_power({}, "gross_flow") is None


def test_corrected_home_consumption_reported_and_calculated() -> None:
    """Reported load is used directly; otherwise the CT correction is applied."""
    reported = util.jackery_corrected_home_consumption_power(
        _ct(_METER_IMPORT),
        {FIELD_OTHER_LOAD_PW: _REPORTED_LOAD},
    )
    assert reported is not None
    assert reported.value == _REPORTED_LOAD
    assert reported.source == FIELD_OTHER_LOAD_PW

    calculated = util.jackery_corrected_home_consumption_power(
        _ct(_METER_IMPORT),
        {
            FIELD_IN_GRID_SIDE_PW: _JACKERY_INPUT,
            FIELD_OUT_GRID_SIDE_PW: _JACKERY_OUTPUT,
        },
    )
    assert calculated is not None
    expected = _METER_IMPORT - _JACKERY_INPUT + _JACKERY_OUTPUT
    assert calculated.value == expected
    assert calculated.source == "smart_meter_net_minus_input_plus_output"


def test_chart_series_key_for_stat_all_sections() -> None:
    """Every documented section/stat pair resolves to its Y-series key."""
    assert (
        util._chart_series_key_for_stat(  # ruff: ignore[private-member-access]
            APP_SECTION_HOME_STAT, APP_STAT_TOTAL_OUT_GRID_ENERGY
        )
        == APP_CHART_SERIES_Y2
    )
    assert (
        util._chart_series_key_for_stat(  # ruff: ignore[private-member-access]
            APP_SECTION_CT_STAT, APP_STAT_TOTAL_CT_OUTPUT_ENERGY
        )
        == APP_CHART_SERIES_Y2
    )
    assert (
        util._chart_series_key_for_stat(  # ruff: ignore[private-member-access]
            APP_SECTION_EPS_STAT, APP_STAT_TOTAL_IN_EPS_ENERGY
        )
        == APP_CHART_SERIES_Y1
    )
    assert (
        util._chart_series_key_for_stat(  # ruff: ignore[private-member-access]
            APP_SECTION_EPS_STAT, APP_STAT_TOTAL_OUT_EPS_ENERGY
        )
        == APP_CHART_SERIES_Y2
    )
    assert (
        util._chart_series_key_for_stat(  # ruff: ignore[private-member-access]
            APP_SECTION_BATTERY_TRENDS, APP_STAT_TOTAL_TREND_CHARGE_ENERGY
        )
        == APP_CHART_SERIES_Y1
    )
    assert (
        util._chart_series_key_for_stat(  # ruff: ignore[private-member-access]
            APP_SECTION_BATTERY_STAT, APP_STAT_TOTAL_DISCHARGE
        )
        == APP_CHART_SERIES_Y2
    )


def test_day_power_series_key_signed_battery_discharge() -> None:
    """A negative battery discharge day curve maps to the signed Y1 series."""
    section = f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}"
    source = {APP_CHART_SERIES_Y1: [0, -3, -5]}
    assert (
        util.day_power_series_key(source, section, APP_STAT_TOTAL_DISCHARGE)
        == APP_CHART_SERIES_Y1
    )


def test_is_signed_battery_energy_curve() -> None:
    """Signed battery curves are recognized only for charge/discharge stats."""
    assert util._is_signed_battery_energy_curve(  # ruff: ignore[private-member-access]
        APP_SECTION_BATTERY_STAT, APP_STAT_TOTAL_CHARGE
    )
    assert not util._is_signed_battery_energy_curve(  # ruff: ignore[private-member-access]
        APP_SECTION_PV_STAT, APP_STAT_TOTAL_CHARGE
    )


def test_meter_head_and_circuit_and_subdevice_reject_non_dict() -> None:
    """Identity extractors return None for non-dict inputs."""
    assert util.meter_head_serial("x") is None
    assert util.sub_device_serial(5) is None
    assert util.sorted_circuits("nope") == []
    assert util.sorted_meter_heads("nope") == []


def test_jackery_online_state_none_falls_through() -> None:
    """None markers reach the generic parser and yield None."""
    assert util.jackery_online_state(None) is None


def test_effective_period_total_value_year_section() -> None:
    """Year device sections sum their expanded series into a rounded total."""
    section = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}"
    source = {APP_CHART_SERIES_Y: [1.0, 2.0, 3.0]}
    expected = 6.0
    total = util.effective_period_total_value(
        source, section, APP_STAT_TOTAL_SOLAR_ENERGY
    )
    assert total == expected


def test_year_payload_appears_current_month_only() -> None:
    """Only-current-month year payloads are flagged as the app month-only bug."""
    current_month = 5
    only_may = [0, 0, 0, 0, 7, 0, 0, 0, 0, 0, 0, 0]
    section = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}"
    source = {APP_CHART_SERIES_Y: only_may}
    assert util.year_payload_appears_current_month_only(
        source, section, (APP_STAT_TOTAL_SOLAR_ENERGY,), current_month=current_month
    )
    assert not util.year_payload_appears_current_month_only(
        source, section, (APP_STAT_TOTAL_SOLAR_ENERGY,), current_month=1
    )


def test_pv_revenue_value_direct_and_derived() -> None:
    """Direct solar revenue wins; otherwise profit is scaled to currency units."""
    expected_direct = 12.5
    assert (
        util._pv_revenue_value({APP_STAT_TOTAL_SOLAR_REVENUE: "12.5"})  # ruff: ignore[private-member-access]
        == expected_direct
    )
    derived = util._pv_revenue_value({APP_STAT_PV_PROFIT: 100_000_000})  # ruff: ignore[private-member-access]
    expected_derived = 10.0
    assert derived == expected_derived
    assert util._pv_revenue_value({}) is None  # ruff: ignore[private-member-access]


def test_can_distribute_scalar_day_total_in_grid() -> None:
    """Home in-grid energy day totals are eligible for scalar distribution."""
    assert util._can_distribute_scalar_day_total(  # ruff: ignore[private-member-access]
        APP_SECTION_HOME_STAT, APP_STAT_TOTAL_IN_GRID_ENERGY
    )
