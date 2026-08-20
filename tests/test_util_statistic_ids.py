"""Characterisation tests for statistic-id and trend-metadata helpers."""

from datetime import UTC, date, datetime

from custom_components.jackery_solarvault.const import (
    APP_CHART_STAT_PERIODS,
    APP_REQUEST_DATE_TYPE,
    APP_REQUEST_META,
    DATE_TYPE_MONTH,
    DOMAIN,
    FIELD_DEVICE_NAME,
    PAYLOAD_SYSTEM,
)
from custom_components.jackery_solarvault.util import (
    _parse_iso_date,
    _trend_date_type,
    app_chart_name_prefix,
    app_chart_period_meta,
    external_trend_statistic_id,
    stat_row_start,
    statistic_id_part,
)

_EPOCH = 1_700_000_000.0


def test_statistic_id_part_normalises() -> None:
    """Ids are lower-cased, non-alphanumerics collapse to single underscores."""
    assert statistic_id_part("PV Energy!!") == "pv_energy"
    assert statistic_id_part("__a__b__") == "a_b"


def test_statistic_id_part_defaults_to_unknown() -> None:
    """Empty / None values fall back to 'unknown'."""
    assert statistic_id_part(None) == "unknown"
    assert statistic_id_part("") == "unknown"


def test_external_trend_statistic_id_composes_normalised_parts() -> None:
    """The external id joins normalised device/metric/bucket under the domain."""
    result = external_trend_statistic_id(DOMAIN, "Dev 1", "PV Energy", "Day")

    assert result == "jackery_solarvault:dev_1_pv_energy_day"


def test_parse_iso_date_variants() -> None:
    """ISO date and datetime prefixes parse; other inputs are None."""
    assert _parse_iso_date("2024-05-15") == date(2024, 5, 15)
    assert _parse_iso_date("2024-05-15T10:00:00") == date(2024, 5, 15)
    assert _parse_iso_date(12345) is None
    assert _parse_iso_date("bad") is None


def test_trend_date_type_prefers_request_meta() -> None:
    """An explicit request date type overrides the section suffix."""
    source = {APP_REQUEST_META: {APP_REQUEST_DATE_TYPE: DATE_TYPE_MONTH}}

    assert _trend_date_type("pv_day", source) == DATE_TYPE_MONTH


def test_trend_date_type_infers_from_suffix() -> None:
    """Without metadata the section suffix decides the period."""
    assert _trend_date_type("pv_month", {}) == DATE_TYPE_MONTH
    assert _trend_date_type("pv_trend", {}) is None


def test_app_chart_period_meta_known_and_unknown() -> None:
    """A configured chart period returns its bucket/label; others return None."""
    known_type = APP_CHART_STAT_PERIODS[0][0]
    expected = (APP_CHART_STAT_PERIODS[0][1], APP_CHART_STAT_PERIODS[0][2])

    assert app_chart_period_meta(known_type) == expected
    assert app_chart_period_meta("no-such-period") is None


def test_stat_row_start_from_datetime_and_number() -> None:
    """Row start reads a datetime timestamp or coerces a numeric value."""
    dt = datetime(2024, 5, 15, tzinfo=UTC)

    assert stat_row_start({"start": dt}) == dt.timestamp()
    assert stat_row_start({"start": _EPOCH}) == _EPOCH
    assert stat_row_start({"start": "bad"}) is None


def test_app_chart_name_prefix_uses_system_name_then_fallback() -> None:
    """The system device name wins; otherwise a stable fallback is used."""
    named = app_chart_name_prefix(
        "dev-1", {PAYLOAD_SYSTEM: {FIELD_DEVICE_NAME: "Garage"}}
    )
    fallback = app_chart_name_prefix("dev-1", {})

    assert named == "Garage"
    assert fallback == "Jackery dev-1"
