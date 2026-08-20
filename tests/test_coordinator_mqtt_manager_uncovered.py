"""Tests for uncovered paths in MQTT manager and merge functions."""

import pytest

from custom_components.jackery_solarvault.coordinator import (
    _merge_identified_dict_lists,
    find_list_for_key,
    merge_present_dict_values,
)


class TestMergeIdentifiedDictListsUncovered:
    """Test _merge_identified_dict_lists uncovered paths."""

    def test_returns_none_for_unidentified_lists(self) -> None:
        """Test returns None when items have no identifiable keys."""
        # Lists with items that don't have identity keys
        base = [{"soc": 50}, {"temp": 25}]
        updates = [{"soc": 60}, {"temp": 30}]
        result = _merge_identified_dict_lists(base, updates)
        assert result is None

    def test_appends_new_items_when_base_empty(self) -> None:
        """Test appends new items when base is empty."""
        base = []
        updates = [{"deviceSn": "p1", "soc": 50}]
        result = _merge_identified_dict_lists(base, updates)
        assert result == [{"deviceSn": "p1", "soc": 50}]

    def test_returns_none_for_empty_updates(self) -> None:
        """Test returns None when updates is empty."""
        base = [{"deviceSn": "p1", "soc": 50}]
        updates = []
        result = _merge_identified_dict_lists(base, updates)
        assert result is None


class TestMergePresentDictValuesUncovered:
    """Test merge_present_dict_values uncovered paths."""

    def test_list_with_identified_items(self) -> None:
        """Test list merge with identified items (line 1618)."""
        base = {"packs": [{"deviceSn": "p1", "soc": 50}]}
        updates = {"packs": [{"deviceSn": "p1", "soc": 60}]}
        result = merge_present_dict_values(base, updates)
        assert result["packs"][0]["soc"] == 60

    def test_list_with_new_identified_item(self) -> None:
        """Test list merge with new identified item."""
        base = {"packs": [{"deviceSn": "p1", "soc": 50}]}
        updates = {"packs": [{"deviceSn": "p2", "soc": 60}]}
        result = merge_present_dict_values(base, updates)
        assert len(result["packs"]) == 2

    def test_list_with_unidentified_items(self) -> None:
        """Test list merge falls back to value when _merge_identified_dict_lists returns None (line 1619)."""
        base = {"packs": [{"soc": 50}]}
        updates = {"packs": [{"soc": 60}]}
        result = merge_present_dict_values(base, updates)
        assert result["packs"] == [{"soc": 60}]


class TestFindListForKeyUncovered:
    """Test find_list_for_key uncovered paths."""

    def test_finds_list_in_dict_values(self) -> None:
        """Test finds list in dict values (line 1684)."""
        obj = {"data": {"nested": {"batteryPacks": [{"deviceSn": "p1"}]}}}
        result = find_list_for_key(obj, "batteryPacks")
        assert result == [{"deviceSn": "p1"}]

    def test_finds_list_in_list_elements(self) -> None:
        """Test finds list in list elements (line 1689)."""
        obj = [{"nested": {"batteryPacks": [{"deviceSn": "p1"}]}}]
        result = find_list_for_key(obj, "batteryPacks")
        assert result == [{"deviceSn": "p1"}]

    def test_returns_none_for_string(self) -> None:
        """Test returns None for string input."""
        result = find_list_for_key("string", "batteryPacks")
        assert result is None

    def test_returns_none_for_int(self) -> None:
        """Test returns None for int input."""
        result = find_list_for_key(123, "batteryPacks")
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
