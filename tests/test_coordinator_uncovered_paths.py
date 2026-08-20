"""Tests for specific uncovered paths in coordinator.py."""

from datetime import date

import pytest

from custom_components.jackery_solarvault.const import (
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
)
from custom_components.jackery_solarvault.coordinator import (
    BackfillStatus,
    _backfill_period_is_closed,  # noqa: PLC2701, RUF105
    _normalize_backfill_status,  # noqa: PLC2701, RUF105
    changed_dict_values,
    find_dict_with_any_key,
    find_list_for_key,
    merge_missing_dict_values,
    merge_present_dict_values,
)


class TestBackfillPeriodIsClosed:
    """Test _backfill_period_is_closed uncovered paths."""

    def test_day_type(self) -> None:  # noqa: D102, PLR6301, RUF105
        today = date(2026, 8, 16)
        assert (
            _backfill_period_is_closed(DATE_TYPE_DAY, date(2026, 8, 15), today=today)
            is True
        )
        assert _backfill_period_is_closed(DATE_TYPE_DAY, today, today=today) is False

    def test_week_type(self) -> None:  # noqa: D102, PLR6301, RUF105
        today = date(2026, 8, 16)  # Sunday
        # Week starts Monday
        last_week = date(2026, 8, 3)
        this_week = date(2026, 8, 10)
        assert (
            _backfill_period_is_closed(DATE_TYPE_WEEK, last_week, today=today) is True
        )
        assert (
            _backfill_period_is_closed(DATE_TYPE_WEEK, this_week, today=today) is False
        )

    def test_month_type_december_rollover(self) -> None:  # noqa: PLR6301, RUF105
        """December -> January year rollover (line 864)."""
        today = date(2026, 1, 15)
        dec_start = date(2025, 12, 1)
        assert (
            _backfill_period_is_closed(DATE_TYPE_MONTH, dec_start, today=today) is True
        )

    def test_month_type_current(self) -> None:  # noqa: D102, PLR6301, RUF105
        today = date(2026, 8, 16)
        aug_start = date(2026, 8, 1)
        assert (
            _backfill_period_is_closed(DATE_TYPE_MONTH, aug_start, today=today) is False
        )

    def test_year_type(self) -> None:  # noqa: D102, PLR6301, RUF105
        today = date(2026, 8, 16)
        assert (
            _backfill_period_is_closed(DATE_TYPE_YEAR, date(2025, 1, 1), today=today)
            is True
        )
        assert (
            _backfill_period_is_closed(DATE_TYPE_YEAR, date(2026, 1, 1), today=today)
            is False
        )

    def test_unknown_type_returns_false(self) -> None:  # noqa: D102, PLR6301, RUF105
        today = date(2026, 8, 16)
        assert _backfill_period_is_closed("UNKNOWN", today, today=today) is False


class TestNormalizeBackfillStatus:
    """Test _normalize_backfill_status helper."""

    def test_known_status_returns_enum(self) -> None:  # noqa: D102, PLR6301, RUF105
        result = _normalize_backfill_status(BackfillStatus.IMPORTED, closed=True)
        assert result == BackfillStatus.IMPORTED

    def test_auth_error_maps_to_retryable(self) -> None:  # noqa: D102, PLR6301, RUF105
        result = _normalize_backfill_status("auth_error", closed=True)
        assert result == BackfillStatus.RETRYABLE

    def test_unknown_open_maps_to_pending(self) -> None:  # noqa: D102, PLR6301, RUF105
        result = _normalize_backfill_status("unknown", closed=False)
        assert result == BackfillStatus.PENDING

    def test_unknown_closed_maps_to_pending(self) -> None:  # noqa: PLR6301, RUF105
        """Unknown values return PENDING even when closed (not in known retryable list)."""  # noqa: E501, RUF105
        result = _normalize_backfill_status("unknown", closed=True)
        assert result == BackfillStatus.PENDING

    def test_invalid_type_returns_pending(self) -> None:  # noqa: PLR6301, RUF105
        """Invalid types (not in known retryable list) return PENDING."""
        result = _normalize_backfill_status(123, closed=True)
        assert result == BackfillStatus.PENDING


class TestChangedDictValues:
    """Test changed_dict_values helper."""

    def test_detects_new_keys(self) -> None:  # noqa: D102, PLR6301, RUF105
        before = {"a": 1}
        after = {"a": 1, "b": 2}
        result = changed_dict_values(before, after)
        assert result == {"b": 2}

    def test_detects_changed_values(self) -> None:  # noqa: D102, PLR6301, RUF105
        before = {"a": 1}
        after = {"a": 2}
        result = changed_dict_values(before, after)
        assert result == {"a": 2}

    def test_handles_nested_dicts(self) -> None:  # noqa: D102, PLR6301, RUF105
        before = {"device": {"soc": 50}}
        after = {"device": {"soc": 60}}
        result = changed_dict_values(before, after)
        assert result == {"device": {"soc": 60}}

    def test_returns_empty_for_no_changes(self) -> None:  # noqa: D102, PLR6301, RUF105
        before = {"a": 1}
        after = {"a": 1}
        result = changed_dict_values(before, after)
        assert result == {}


class TestMergePresentDictValuesAdditional:
    """Additional tests for merge_present_dict_values."""

    def test_preserves_base_when_update_blank(self) -> None:  # noqa: D102, PLR6301, RUF105
        base = {"key": "value"}
        updates = {"key": None}
        result = merge_present_dict_values(base, updates)
        assert result["key"] == "value"

    def test_update_overwrites_when_base_blank(self) -> None:  # noqa: D102, PLR6301, RUF105
        base = {"key": None}
        updates = {"key": "new_value"}
        result = merge_present_dict_values(base, updates)
        assert result["key"] == "new_value"


class TestMergeMissingDictValuesAdditional:
    """Additional tests for merge_missing_dict_values."""

    def test_fills_missing_nested(self) -> None:  # noqa: D102, PLR6301, RUF105
        base = {"device": {"soc": 50}}
        updates = {"device": {"temp": 25}}
        result = merge_missing_dict_values(base, updates)
        assert result["device"]["soc"] == 50
        assert result["device"]["temp"] == 25

    def test_does_not_overwrite_existing(self) -> None:  # noqa: D102, PLR6301, RUF105
        base = {"device": {"soc": 50}}
        updates = {"device": {"soc": 60, "temp": 25}}
        result = merge_missing_dict_values(base, updates)
        assert result["device"]["soc"] == 50
        assert result["device"]["temp"] == 25


class TestFindDictWithAnyKeyAdditional:
    """Additional tests for find_dict_with_any_key."""

    def test_returns_none_for_none_input(self) -> None:  # noqa: D102, PLR6301, RUF105
        result = find_dict_with_any_key(None, {"deviceSn"})
        assert result is None

    def test_returns_none_for_number_input(self) -> None:  # noqa: D102, PLR6301, RUF105
        result = find_dict_with_any_key(123, {"deviceSn"})
        assert result is None


class TestFindListForKeyAdditional:
    """Additional tests for find_list_for_key."""

    def test_returns_none_for_none_input(self) -> None:  # noqa: D102, PLR6301, RUF105
        result = find_list_for_key(None, "batteryPacks")
        assert result is None

    def test_returns_none_for_number_input(self) -> None:  # noqa: D102, PLR6301, RUF105
        result = find_list_for_key(123, "batteryPacks")
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
