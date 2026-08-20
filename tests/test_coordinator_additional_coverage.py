"""Additional targeted tests for coordinator.py uncovered paths."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault.const import (
    BATTERY_PACK_HINT_KEYS,
    CT_METER_KEYS,
    FIELD_BATTERY_PACK,
    FIELD_BATTERY_PACKS,
    FIELD_BATTERY_PACK_LIST,
    FIELD_BAT_SOC,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
    battery_packs_from_source,
    call,
    find_dict_with_any_key,
    find_list_for_key,
    merge_missing_dict_values,
    merge_present_dict_values,
    normalize_live_property_payload,
    source_regions,
)


class TestCoordinatorMainPaths:
    """Test main coordinator paths for coverage."""

    def _bare_coordinator(self) -> Any:  # noqa: PLR6301
        coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
        shell = cast("Any", coordinator)
        shell._shutdown_started = False
        shell._property_source_state = {}
        shell._accessory_source_state = {}
        shell._property_overrides = {}
        shell._background_tasks = {}
        shell._configured_update_interval = timedelta(seconds=15)
        shell._polling_diagnostics = {}
        shell._polling_timeout_started_monotonic = None
        shell._mqtt = None
        shell._ble_listener = None
        shell._device_index = {}
        shell.entry = SimpleNamespace(options={}, data={})
        shell.api = SimpleNamespace(get_cached_mqtt_credentials=lambda: None)
        shell.hass = SimpleNamespace(
            async_create_background_task=lambda coro, **kwargs: AsyncMock()()
        )
        return shell

    def test_call_invokes_method(self) -> None:
        """Test call helper invokes method on coordinator."""
        coordinator = self._bare_coordinator()
        coordinator.async_test_method = AsyncMock(return_value="result")

        async def run():
            return await call(coordinator, "async_test_method", "arg1", kwarg="value")

        result = asyncio.run(run())
        assert result == "result"
        coordinator.async_test_method.assert_called_once_with("arg1", kwarg="value")


class TestBatteryPackFromSource:
    """Test battery_packs_from_source helper."""

    def test_extracts_from_battery_packs_key(self) -> None:  # noqa: PLR6301
        """Extracts battery packs from FIELD_BATTERY_PACKS key."""
        source = {FIELD_BATTERY_PACKS: [{FIELD_BAT_SOC: 50}]}
        result = battery_packs_from_source(
            source, CT_METER_KEYS, BATTERY_PACK_HINT_KEYS
        )
        assert result is not None
        assert len(result) == 1

    def test_extracts_from_battery_pack_key(self) -> None:  # noqa: PLR6301
        """Extracts from FIELD_BATTERY_PACK key (when it contains a list)."""
        source = {FIELD_BATTERY_PACK: [{FIELD_BAT_SOC: 50}]}
        result = battery_packs_from_source(
            source, CT_METER_KEYS, BATTERY_PACK_HINT_KEYS
        )
        assert result is not None
        assert len(result) == 1

    def test_extracts_from_battery_pack_list_key(self) -> None:  # noqa: PLR6301
        """Extracts from FIELD_BATTERY_PACK_LIST key."""
        source = {FIELD_BATTERY_PACK_LIST: [{FIELD_BAT_SOC: 50}]}
        result = battery_packs_from_source(
            source, CT_METER_KEYS, BATTERY_PACK_HINT_KEYS
        )
        assert result is not None
        assert len(result) == 1

    def test_returns_none_when_no_known_keys(self) -> None:  # noqa: PLR6301
        """Returns None when no known battery pack keys found."""
        source = {"unknown": [{"deviceSn": "pack-1"}]}
        result = battery_packs_from_source(
            source, CT_METER_KEYS, BATTERY_PACK_HINT_KEYS
        )
        assert result is None

    def test_extracts_from_sub_device_key(self) -> None:  # noqa: PLR6301
        """Extracts from FIELD_SUB_DEVICE key."""
        from custom_components.jackery_solarvault.const import FIELD_SUB_DEVICE  # noqa: I001

        source = {FIELD_SUB_DEVICE: [{FIELD_BAT_SOC: 50}]}
        result = battery_packs_from_source(
            source, CT_METER_KEYS, BATTERY_PACK_HINT_KEYS
        )
        assert result is not None
        assert len(result) == 1

    def test_extracts_from_list_source(self) -> None:  # noqa: PLR6301
        """Extracts when source is a list."""
        source = [{FIELD_BAT_SOC: 50}]
        result = battery_packs_from_source(
            source, CT_METER_KEYS, BATTERY_PACK_HINT_KEYS
        )
        assert result is not None
        assert len(result) == 1


class TestMergePresentDictValuesEdgeCases:
    """Test merge_present_dict_values edge cases."""

    def test_preserves_base_when_update_is_blank_string(self) -> None:  # noqa: PLR6301
        """Blank string in update preserves base value."""
        base = {"key": "value"}
        updates = {"key": ""}
        result = merge_present_dict_values(base, updates)
        assert result["key"] == "value"

    def test_preserves_base_when_update_is_empty_list(self) -> None:  # noqa: PLR6301
        """Empty list in update preserves base value."""
        base = {"key": [1, 2]}
        updates = {"key": []}
        result = merge_present_dict_values(base, updates)
        assert result["key"] == [1, 2]

    def test_preserves_base_when_update_is_empty_dict(self) -> None:  # noqa: PLR6301
        """Empty dict in update preserves base value."""
        base = {"key": {"nested": "value"}}
        updates = {"key": {}}
        result = merge_present_dict_values(base, updates)
        assert result["key"] == {"nested": "value"}

    def test_blank_update_with_non_blank_base_preserves(self) -> None:  # noqa: PLR6301
        """_is_blank_value logic preserves base."""
        base = {"soc": 50}
        updates = {"soc": None}
        result = merge_present_dict_values(base, updates)
        assert result["soc"] == 50

    def test_update_overwrites_when_base_is_blank(self) -> None:  # noqa: PLR6301
        """Non-blank update overwrites blank base."""
        base = {"soc": None}
        updates = {"soc": 60}
        result = merge_present_dict_values(base, updates)
        assert result["soc"] == 60


class TestMergeMissingDictValuesEdgeCases:
    """Test merge_missing_dict_values edge cases."""

    def test_fills_missing_nested_keys(self) -> None:  # noqa: PLR6301
        """Fills missing keys in nested dicts."""
        base = {"device": {"soc": 50}}
        updates = {"device": {"temp": 25, "voltage": 12}}
        result = merge_missing_dict_values(base, updates)
        assert result["device"]["soc"] == 50
        assert result["device"]["temp"] == 25
        assert result["device"]["voltage"] == 12

    def test_does_not_overwrite_existing(self) -> None:  # noqa: PLR6301
        """Does not overwrite existing non-blank values."""
        base = {"device": {"soc": 50}}
        updates = {"device": {"soc": 60, "temp": 25}}
        result = merge_missing_dict_values(base, updates)
        assert result["device"]["soc"] == 50
        assert result["device"]["temp"] == 25


class TestNormalizeLivePropertyPayload:
    """Test normalize_live_property_payload helper."""

    def test_returns_shallow_copy(self) -> None:  # noqa: PLR6301
        """Returns a shallow copy of the source."""
        source = {"soc": 50, "temp": 25}
        result = normalize_live_property_payload(source)
        assert result == source
        assert result is not source  # different object

    def test_preserves_all_keys(self) -> None:  # noqa: PLR6301
        """All keys from source are preserved."""
        source = {"soc": 50, "lifetimeEnergy": 1000, "temp": 25}
        result = normalize_live_property_payload(source)
        assert "lifetimeEnergy" in result
        assert result["lifetimeEnergy"] == 1000


class TestSourceRegions:
    """Test source_regions helper."""

    def test_extracts_region_from_price_sources(self) -> None:  # noqa: PLR6301
        """Extracts region from price source data."""
        from custom_components.jackery_solarvault.const import FIELD_SYSTEM_REGION  # noqa: I001

        source = {FIELD_SYSTEM_REGION: "DE", "other": "data"}
        result = source_regions(source)
        assert "DE" in result

    def test_extracts_region_from_country_field(self) -> None:  # noqa: PLR6301
        """Extracts region from country field."""
        from custom_components.jackery_solarvault.const import FIELD_COUNTRY  # noqa: I001

        source = {FIELD_COUNTRY: "DE, FR"}
        result = source_regions(source)
        assert "DE" in result
        assert "FR" in result

    def test_returns_empty_for_no_region(self) -> None:  # noqa: PLR6301
        """Returns empty list when no region key."""
        source = {"price": 0.15}
        result = source_regions(source)
        assert result == []

    def test_returns_empty_for_empty_string(self) -> None:  # noqa: PLR6301
        """Returns empty list when region is empty string."""
        from custom_components.jackery_solarvault.const import FIELD_SYSTEM_REGION  # noqa: I001

        source = {FIELD_SYSTEM_REGION: ""}
        result = source_regions(source)
        assert result == []

    def test_splits_comma_separated_regions(self) -> None:  # noqa: PLR6301
        """Splits comma-separated region strings."""
        from custom_components.jackery_solarvault.const import FIELD_SYSTEM_REGION  # noqa: I001

        source = {FIELD_SYSTEM_REGION: "DE, FR, IT"}
        result = source_regions(source)
        assert result == ["DE", "FR", "IT"]


class TestFindDictWithAnyKeyAdditional:
    """Additional tests for find_dict_with_any_key."""

    def test_finds_key_in_nested_list(self) -> None:  # noqa: PLR6301
        """Finds key in list within dict."""
        obj = {"devices": [{"deviceSn": "p1"}, {"deviceSn": "p2"}]}
        result = find_dict_with_any_key(obj, {"deviceSn"})
        assert result == obj["devices"][0]

    def test_returns_none_for_string_input(self) -> None:  # noqa: PLR6301
        """Returns None for string input."""
        result = find_dict_with_any_key("string", {"deviceSn"})
        assert result is None

    def test_returns_none_for_int_input(self) -> None:  # noqa: PLR6301
        """Returns None for int input."""
        result = find_dict_with_any_key(123, {"deviceSn"})
        assert result is None


class TestFindListForKeyAdditional:
    """Additional tests for find_list_for_key."""

    def test_finds_list_in_deeply_nested_dict(self) -> None:  # noqa: PLR6301
        """Finds list in deeply nested structure."""
        obj = {"data": {"nested": {"batteryPacks": [{"deviceSn": "p1"}]}}}
        result = find_list_for_key(obj, "batteryPacks")
        assert result == [{"deviceSn": "p1"}]

    def test_returns_none_for_string_input(self) -> None:  # noqa: PLR6301
        """Returns None for string input."""
        result = find_list_for_key("string", "batteryPacks")
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
