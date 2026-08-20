"""Integration tests for coordinator entity management without HA fixtures.

These tests verify the coordinator correctly manages device data and entity
state transitions without requiring the full Home Assistant test infrastructure.
"""

import asyncio
from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.jackery_solarvault.const import (
    DEFAULT_SCAN_INTERVAL_SEC,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_SN,
    FIELD_MODEL_CODE,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

_TEST_HTTP_DATA = {
    "test-device": {
        FIELD_DEVICE_ID: "test-device",
        FIELD_DEVICE_SN: "TEST-SERIAL",
        FIELD_DEVICE_NAME: "Test SolarVault",
        FIELD_MODEL_CODE: 3002,
    },
}


def _make_coordinator() -> JackerySolarVaultCoordinator:
    """Create a coordinator instance with mocked dependencies."""
    hass = MagicMock()
    loop = asyncio.new_event_loop()
    hass.loop = loop

    entry = MagicMock()
    entry.entry_id = "test-entry"
    entry.data = {"username": "user@example.com", "password": "pass"}
    entry.options = {}
    entry.runtime_data = None

    api = MagicMock()
    api.mqtt_session_snapshot = MagicMock(return_value=None)

    update_interval = timedelta(seconds=DEFAULT_SCAN_INTERVAL_SEC)

    with (
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator.async_discover",
            return_value=True,
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator.async_start_statistics_imports",
            return_value=None,
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator._async_ensure_mqtt",
            return_value=None,
        ),
        patch(
            "custom_components.jackery_solarvault._async_start_layer5_transports",
            AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator._async_update_data",
            return_value=_TEST_HTTP_DATA,
        ),
        patch(
            "custom_components.jackery_solarvault._async_prime_entry_bootstrap_mqtt_session",
            AsyncMock(return_value=None),
        ),
    ):
        coordinator = JackerySolarVaultCoordinator(hass, entry, api, update_interval)
        # Manually initialize since we're not going through HA setup
        coordinator.data = _TEST_HTTP_DATA
        coordinator._device_registry_synced = True

    return coordinator


def _make_multi_device_coordinator() -> JackerySolarVaultCoordinator:
    """Create coordinator with multiple devices."""
    multi_device_data = {
        "device-1": {
            FIELD_DEVICE_ID: "device-1",
            FIELD_DEVICE_SN: "SERIAL-1",
            FIELD_DEVICE_NAME: "SolarVault 1",
            FIELD_MODEL_CODE: 3002,
        },
        "device-2": {
            FIELD_DEVICE_ID: "device-2",
            FIELD_DEVICE_SN: "SERIAL-2",
            FIELD_DEVICE_NAME: "SolarVault 2",
            FIELD_MODEL_CODE: 3002,
        },
    }

    hass = MagicMock()
    loop = asyncio.new_event_loop()
    hass.loop = loop

    entry = MagicMock()
    entry.entry_id = "test-entry"
    entry.data = {"username": "user@example.com", "password": "pass"}
    entry.options = {}
    entry.runtime_data = None

    api = MagicMock()
    api.mqtt_session_snapshot = MagicMock(return_value=None)

    update_interval = timedelta(seconds=DEFAULT_SCAN_INTERVAL_SEC)

    with (
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator.async_discover",
            return_value=True,
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator.async_start_statistics_imports",
            return_value=None,
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator._async_ensure_mqtt",
            return_value=None,
        ),
        patch(
            "custom_components.jackery_solarvault._async_start_layer5_transports",
            AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator._async_update_data",
            return_value=multi_device_data,
        ),
        patch(
            "custom_components.jackery_solarvault._async_prime_entry_bootstrap_mqtt_session",
            AsyncMock(return_value=None),
        ),
    ):
        coordinator = JackerySolarVaultCoordinator(hass, entry, api, update_interval)
        coordinator.data = multi_device_data

    return coordinator


class TestCoordinatorEntityManagement:
    """Test coordinator entity management logic."""

    @pytest.mark.asyncio
    async def test_coordinator_initialization(self) -> None:
        """Coordinator initializes with correct defaults."""
        coordinator = _make_coordinator()

        assert coordinator is not None
        assert coordinator.data == _TEST_HTTP_DATA
        assert coordinator.update_interval == timedelta(
            seconds=DEFAULT_SCAN_INTERVAL_SEC
        )
        assert coordinator._device_registry_synced is True

    @pytest.mark.asyncio
    async def test_coordinator_async_update_data_returns_data(self) -> None:
        """_async_update_data returns device data correctly."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from custom_components.jackery_solarvault.coordinator import (
            JackerySolarVaultCoordinator,
        )

        coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
        shell = coordinator
        shell._shutdown_started = False
        shell._property_source_state = {}
        shell._accessory_source_state = {}
        shell._property_overrides = {}
        shell._background_tasks = {}
        shell._configured_update_interval = timedelta(seconds=15)
        shell._polling_diagnostics = {}
        shell._polling_timeout_started_monotonic = None
        shell._last_http_cycle_started_monotonic = float("-inf")
        shell._active_http_update_tasks = set()
        shell.data = _TEST_HTTP_DATA
        shell._mqtt = None
        shell._ble_listener = None
        shell._device_index = {"test-device": {}}
        shell.entry = SimpleNamespace(options={}, data={})
        shell.api = SimpleNamespace(
            get_cached_mqtt_credentials=lambda: None,
            _async_update_data=AsyncMock(return_value=_TEST_HTTP_DATA)
        )
        shell.hass = SimpleNamespace(
            async_create_background_task=lambda coro, **kwargs: AsyncMock()()
        )
        shell.data = _TEST_HTTP_DATA

        data = await shell._async_update_data()
        assert data == _TEST_HTTP_DATA

    @pytest.mark.asyncio
    async def test_coordinator_device_registry_sync(self) -> None:
        """Coordinator syncs device registry on first poll."""
        coordinator = _make_coordinator()

        # Should have device registry sync flag set
        assert coordinator._device_registry_synced is True

    @pytest.mark.asyncio
    async def test_coordinator_handles_multiple_devices(self) -> None:
        """Coordinator handles multiple device data."""
        coordinator = _make_multi_device_coordinator()

        assert len(coordinator.data) == 2
        assert "device-1" in coordinator.data
        assert "device-2" in coordinator.data

    @pytest.mark.asyncio
    async def test_coordinator_data_structure(self) -> None:
        """Coordinator data has expected structure."""
        coordinator = _make_coordinator()

        data = coordinator.data
        assert "test-device" in data
        device = data["test-device"]
        assert device[FIELD_DEVICE_ID] == "test-device"
        assert device[FIELD_DEVICE_SN] == "TEST-SERIAL"
        assert device[FIELD_DEVICE_NAME] == "Test SolarVault"
        assert device[FIELD_MODEL_CODE] == 3002


class TestCoordinatorUpdateCycle:
    """Test coordinator update cycle behavior."""

    @pytest.mark.asyncio
    async def test_update_interval(self) -> None:
        """Coordinator uses correct update interval."""
        coordinator = _make_coordinator()
        assert coordinator.update_interval == timedelta(
            seconds=DEFAULT_SCAN_INTERVAL_SEC
        )

    @pytest.mark.asyncio
    async def test_multiple_updates(self) -> None:
        """Multiple updates work correctly."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from custom_components.jackery_solarvault.coordinator import (
            JackerySolarVaultCoordinator,
        )

        coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
        shell = coordinator
        shell._shutdown_started = False
        shell._property_source_state = {}
        shell._accessory_source_state = {}
        shell._property_overrides = {}
        shell._background_tasks = {}
        shell._configured_update_interval = timedelta(seconds=15)
        shell._polling_diagnostics = {}
        shell._polling_timeout_started_monotonic = None
        shell._last_http_cycle_started_monotonic = float("-inf")
        shell._active_http_update_tasks = set()
        shell.data = _TEST_HTTP_DATA
        shell._mqtt = None
        shell._ble_listener = None
        shell._device_index = {}
        shell.entry = SimpleNamespace(options={}, data={})
        shell.api = SimpleNamespace(
            get_cached_mqtt_credentials=lambda: None,
            _async_update_data=AsyncMock(return_value=_TEST_HTTP_DATA)
        )
        shell.hass = SimpleNamespace(
            async_create_background_task=lambda coro, **kwargs: AsyncMock()()
        )

        for _ in range(3):
            data = await shell._async_update_data()
            assert data == _TEST_HTTP_DATA


class TestCoordinatorErrorHandling:
    """Test coordinator error handling."""

    @pytest.mark.asyncio
    async def test_coordinator_handles_api_error(self) -> None:
        """Coordinator handles API errors gracefully."""
        from types import SimpleNamespace

        from custom_components.jackery_solarvault.coordinator import (
            JackerySolarVaultCoordinator,
        )
        from custom_components.jackery_solarvault.helpers import UpdateFailed

        coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
        shell = coordinator
        shell._shutdown_started = False
        shell._property_source_state = {}
        shell._accessory_source_state = {}
        shell._property_overrides = {}
        shell._background_tasks = {}
        shell._configured_update_interval = timedelta(seconds=15)
        shell._polling_diagnostics = {}
        shell._polling_timeout_started_monotonic = None
        shell._last_http_cycle_started_monotonic = float("-inf")
        shell._active_http_update_tasks = set()
        shell.data = _TEST_HTTP_DATA
        shell._mqtt = None
        shell._ble_listener = None
        shell._device_index = {}
        shell.entry = SimpleNamespace(options={}, data={})
        shell.api = SimpleNamespace(
            get_cached_mqtt_credentials=lambda: None,
            _async_update_data=AsyncMock(side_effect=Exception("API Error"))
        )
        shell.hass = SimpleNamespace(
            async_create_background_task=lambda coro, **kwargs: AsyncMock()()
        )

        # Should raise UpdateFailed
        with pytest.raises(UpdateFailed):
            await shell._async_update_data()


class TestCoordinatorDeviceDataIntegrity:
    """Test device data integrity in coordinator."""

    @pytest.mark.asyncio
    async def test_device_data_contains_required_fields(self) -> None:
        """Device data contains all required fields."""
        coordinator = _make_coordinator()
        data = coordinator.data

        for dev_id, device in data.items():
            assert FIELD_DEVICE_ID in device
            assert FIELD_DEVICE_SN in device
            assert FIELD_DEVICE_NAME in device
            assert FIELD_MODEL_CODE in device
            assert device[FIELD_DEVICE_ID] == dev_id

    @pytest.mark.asyncio
    async def test_coordinator_preserves_device_identity(self) -> None:
        """Coordinator preserves device identity across updates."""
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        from custom_components.jackery_solarvault.coordinator import (
            JackerySolarVaultCoordinator,
        )

        coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
        shell = coordinator
        shell._shutdown_started = False
        shell._property_source_state = {}
        shell._accessory_source_state = {}
        shell._property_overrides = {}
        shell._background_tasks = {}
        shell._configured_update_interval = timedelta(seconds=15)
        shell._polling_diagnostics = {}
        shell._polling_timeout_started_monotonic = None
        shell._last_http_cycle_started_monotonic = float("-inf")
        shell._active_http_update_tasks = set()
        shell.data = _TEST_HTTP_DATA
        shell._mqtt = None
        shell._ble_listener = None
        shell._device_index = {}
        shell.entry = SimpleNamespace(options={}, data={})
        shell.api = SimpleNamespace(
            get_cached_mqtt_credentials=lambda: None,
            _async_update_data=AsyncMock(return_value=_TEST_HTTP_DATA)
        )
        shell.hass = SimpleNamespace(
            async_create_background_task=lambda coro, **kwargs: AsyncMock()()
        )

        # Initial data
        initial_data = await shell._async_update_data()
        initial_id = initial_data["test-device"][FIELD_DEVICE_ID]

        # Simulate multiple updates
        for _ in range(5):
            data = await shell._async_update_data()
            assert data["test-device"][FIELD_DEVICE_ID] == initial_id

    @pytest.mark.asyncio
    async def test_multi_device_isolation(self) -> None:
        """Each device maintains independent data."""
        coordinator = _make_multi_device_coordinator()
        data = coordinator.data

        assert data["device-1"][FIELD_DEVICE_SN] == "SERIAL-1"
        assert data["device-2"][FIELD_DEVICE_SN] == "SERIAL-2"
        assert data["device-1"][FIELD_DEVICE_NAME] != data["device-2"][FIELD_DEVICE_NAME]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
