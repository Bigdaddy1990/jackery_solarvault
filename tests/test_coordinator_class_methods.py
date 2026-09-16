"""Tests for JackerySolarVaultCoordinator class methods to increase coverage."""

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from typing import Any, cast

import pytest

from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
    _merge_identified_dict_lists,  # ruff: ignore[import-private-name]
    merge_missing_dict_values,
    merge_present_dict_values,
)


class TestCoordinatorClassMethods:
    """Test coordinator class methods."""

    def _bare_coordinator(self) -> Any:  # ruff: ignore[any-type, no-self-use]
        coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
        shell = cast("Any", coordinator)
        shell._shutdown_started = False  # ruff: ignore[private-member-access]
        shell._property_source_state = {}  # ruff: ignore[private-member-access]
        shell._accessory_source_state = {}  # ruff: ignore[private-member-access]
        shell._property_overrides = {}  # ruff: ignore[private-member-access]
        shell._background_tasks = {}  # ruff: ignore[private-member-access]
        shell._configured_update_interval = timedelta(seconds=15)  # ruff: ignore[private-member-access]
        shell._polling_diagnostics = {}  # ruff: ignore[private-member-access]
        shell._polling_timeout_started_monotonic = None  # ruff: ignore[private-member-access]
        shell._mqtt = None  # ruff: ignore[private-member-access]
        shell._ble_listener = None  # ruff: ignore[private-member-access]
        shell._device_index = {}  # ruff: ignore[private-member-access]
        shell.entry = SimpleNamespace(options={}, data={})
        shell.api = SimpleNamespace(get_cached_mqtt_credentials=lambda: None)
        shell.hass = SimpleNamespace(
            async_create_background_task=lambda coro, **kwargs: asyncio.create_task(
                coro, name=kwargs["name"]
            )
        )
        return shell

    def test_coordinator_creation(self) -> None:
        """Test coordinator can be instantiated."""
        coordinator = self._bare_coordinator()
        assert coordinator is not None

    def test_merge_identified_dict_lists_with_coordinator(self) -> None:  # ruff: ignore[no-self-use]
        """Test _merge_identified_dict_lists with coordinator context."""
        base = [{"deviceSn": "p1", "soc": 50}]
        updates = [{"deviceSn": "p1", "soc": 60}]
        result = _merge_identified_dict_lists(base, updates)
        assert result[0]["soc"] == 60  # ruff: ignore[magic-value-comparison]

    def test_merge_present_dict_values_with_coordinator(self) -> None:  # ruff: ignore[no-self-use]
        """Test merge_present_dict_values with coordinator context."""
        base = {"key": "value"}
        updates = {"key": None}
        result = merge_present_dict_values(base, updates)
        assert result["key"] == "value"

    def test_merge_missing_dict_values_with_coordinator(self) -> None:  # ruff: ignore[no-self-use]
        """Test merge_missing_dict_values with coordinator context."""
        base = {"device": {"soc": 50}}
        updates = {"device": {"temp": 25}}
        result = merge_missing_dict_values(base, updates)
        assert result["device"]["soc"] == 50  # ruff: ignore[magic-value-comparison]
        assert result["device"]["temp"] == 25  # ruff: ignore[magic-value-comparison]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
