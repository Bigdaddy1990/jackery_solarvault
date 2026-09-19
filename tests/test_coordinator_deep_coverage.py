"""Deep coverage tests for coordinator.py data processing paths."""

import pytest

from custom_components.jackery_solarvault.coordinator import (
    _clean_dict_list_update,  # ruff: ignore[import-private-name]
    _dict_list_identity_values,  # ruff: ignore[import-private-name]
    _is_blank_value,  # ruff: ignore[import-private-name]
    merge_dict_values,
    merge_missing_dict_values,
    merge_present_dict_values,
)


class TestDictListIdentityValues:
    """Test _dict_list_identity_values helper."""

    def test_returns_serial_keys(self) -> None:  # ruff: ignore[no-self-use]
        """Returns serial identities for devSn, deviceSn, sn keys."""
        item = {"deviceSn": "pack-1", "sn": "serial-123"}
        result = _dict_list_identity_values(item)
        assert "serial:pack-1" in result
        assert "serial:serial-123" in result

    def test_returns_id_keys(self) -> None:  # ruff: ignore[no-self-use]
        """Returns id identities for devId, deviceId, id, idx keys."""
        item = {"devId": "id-1", "deviceId": "id-2", "id": "id-3", "idx": 5}
        result = _dict_list_identity_values(item)
        assert "devId:id-1" in result
        assert "deviceId:id-2" in result
        assert "id:id-3" in result
        assert "idx:5" in result

    def test_skips_blank_values(self) -> None:  # ruff: ignore[no-self-use]
        """Skips blank/empty values."""
        item = {"deviceSn": "", "devId": None, "id": "valid"}
        result = _dict_list_identity_values(item)
        assert "serial:valid" not in result  # deviceSn is blank
        assert "id:valid" in result

    def test_returns_empty_for_no_identifiers(self) -> None:  # ruff: ignore[no-self-use]
        """Returns empty frozenset when no identity keys present."""
        item = {"soc": 50, "temp": 25}
        result = _dict_list_identity_values(item)
        assert result == frozenset()


class TestIsBlankValue:
    """Test _is_blank_value helper."""

    def test_returns_true_for_none(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert _is_blank_value(None) is True

    def test_returns_true_for_empty_string(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert _is_blank_value("") is True

    def test_returns_true_for_whitespace_string(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert _is_blank_value("   ") is True

    def test_returns_true_for_empty_list(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert _is_blank_value([]) is True

    def test_returns_true_for_empty_dict(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert _is_blank_value({}) is True

    def test_returns_false_for_non_blank_string(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert _is_blank_value("value") is False

    def test_returns_false_for_non_blank_list(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert _is_blank_value([1, 2]) is False

    def test_returns_false_for_non_blank_dict(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert _is_blank_value({"key": "value"}) is False

    def test_returns_false_for_zero(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert _is_blank_value(0) is False

    def test_returns_false_for_false_bool(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        assert _is_blank_value(False) is False


class TestCleanDictListUpdate:
    """Test _clean_dict_list_update helper."""

    def test_removes_blank_values(self) -> None:  # ruff: ignore[no-self-use]
        """Removes keys with blank values."""
        update = {"key1": "value", "key2": None, "key3": "", "key4": []}
        result = _clean_dict_list_update(update)
        assert result == {"key1": "value"}

    def test_preserves_non_blank_values(self) -> None:  # ruff: ignore[no-self-use]
        """Preserves keys with non-blank values."""
        update = {
            "key1": "value",
            "key2": 0,
            "key3": False,
            "key4": {"nested": "value"},
        }
        result = _clean_dict_list_update(update)
        assert "key1" in result
        assert "key2" in result
        assert "key3" in result
        assert "key4" in result

    def test_returns_empty_for_all_blank(self) -> None:  # ruff: ignore[no-self-use]
        """Returns empty dict when all values blank."""
        update = {"key1": None, "key2": ""}
        result = _clean_dict_list_update(update)
        assert result == {}


class TestMergeDictValues:
    """Test merge_dict_values helper."""

    def test_merges_updates_into_base(self) -> None:  # ruff: ignore[no-self-use]
        """Updates are merged into base."""
        base = {"a": 1, "b": 2}
        updates = {"b": 3, "c": 4}
        result = merge_dict_values(base, updates)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_overwrites_base_with_none(self) -> None:  # ruff: ignore[no-self-use]
        """Update overwrites base even with None (no blank-value preservation)."""
        base = {"a": 1}
        updates = {"a": None}
        result = merge_dict_values(base, updates)
        assert result == {"a": None}

    def test_handles_nested_dicts(self) -> None:  # ruff: ignore[no-self-use]
        """Recursively merges nested dicts."""
        base = {"device": {"soc": 50}}
        updates = {"device": {"temp": 25}}
        result = merge_dict_values(base, updates)
        assert result["device"]["soc"] == 50  # ruff: ignore[magic-value-comparison]
        assert result["device"]["temp"] == 25  # ruff: ignore[magic-value-comparison]


class TestMergePresentDictValuesAdditional:
    """Additional tests for merge_present_dict_values."""

    def test_handles_list_updates(self) -> None:  # ruff: ignore[no-self-use]
        """List updates are merged."""
        base = {"packs": [{"deviceSn": "p1", "soc": 50}]}
        updates = {"packs": [{"deviceSn": "p1", "soc": 60}]}
        result = merge_present_dict_values(base, updates)
        assert result["packs"][0]["soc"] == 60  # ruff: ignore[magic-value-comparison]

    def test_handles_list_with_new_item(self) -> None:  # ruff: ignore[no-self-use]
        """New items in list are appended."""
        base = {"packs": [{"deviceSn": "p1", "soc": 50}]}
        updates = {"packs": [{"deviceSn": "p2", "soc": 60}]}
        result = merge_present_dict_values(base, updates)
        assert len(result["packs"]) == 2  # ruff: ignore[magic-value-comparison]

    def test_preserves_base_when_update_is_blank_string(self) -> None:  # ruff: ignore[no-self-use]
        """Blank string in update preserves base value."""
        base = {"key": "value"}
        updates = {"key": ""}
        result = merge_present_dict_values(base, updates)
        assert result["key"] == "value"

    def test_update_overwrites_when_base_blank(self) -> None:  # ruff: ignore[no-self-use]
        """Non-blank update overwrites blank base."""
        base = {"key": None}
        updates = {"key": "new_value"}
        result = merge_present_dict_values(base, updates)
        assert result["key"] == "new_value"


class TestMergeMissingDictValuesAdditional:
    """Additional tests for merge_missing_dict_values."""

    def test_fills_missing_nested_keys(self) -> None:  # ruff: ignore[no-self-use]
        """Fills missing keys in nested dicts."""
        base = {"device": {"soc": 50}}
        updates = {"device": {"temp": 25, "voltage": 12}}
        result = merge_missing_dict_values(base, updates)
        assert result["device"]["soc"] == 50  # ruff: ignore[magic-value-comparison]
        assert result["device"]["temp"] == 25  # ruff: ignore[magic-value-comparison]
        assert result["device"]["voltage"] == 12  # ruff: ignore[magic-value-comparison]

    def test_does_not_overwrite_existing(self) -> None:  # ruff: ignore[no-self-use]
        """Does not overwrite existing non-blank values."""
        base = {"device": {"soc": 50}}
        updates = {"device": {"soc": 60, "temp": 25}}
        result = merge_missing_dict_values(base, updates)
        assert result["device"]["soc"] == 50  # ruff: ignore[magic-value-comparison]
        assert result["device"]["temp"] == 25  # ruff: ignore[magic-value-comparison]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
