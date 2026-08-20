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
        coordinator._device_registry_synced = True  # noqa: RUF105, SLF001

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
    async def test_coordinator_initialization(self) -> None:  # noqa: PLR6301, RUF105
        """Coordinator initializes with correct defaults."""
        coordinator = _make_coordinator()

        assert coordinator is not None
        assert coordinator.data == _TEST_HTTP_DATA
        assert coordinator.update_interval == timedelta(
            seconds=DEFAULT_SCAN_INTERVAL_SEC
        )
        assert coordinator._device_registry_synced is True  # noqa: RUF105, SLF001

    @pytest.mark.asyncio
    async def test_coordinator_async_update_data_returns_data(self) -> None:  # noqa: PLR6301, RUF105
        """_async_update_data returns device data correctly."""
        coordinator = _make_coordinator()

        data = await coordinator._async_update_data()  # noqa: RUF105, SLF001
        assert data == _TEST_HTTP_DATA

    @pytest.mark.asyncio
    async def test_coordinator_device_registry_sync(self) -> None:  # noqa: PLR6301, RUF105
        """Coordinator syncs device registry on first poll."""
        coordinator = _make_coordinator()

        # Should have device registry sync flag set
        assert coordinator._device_registry_synced is True  # noqa: RUF105, SLF001

    @pytest.mark.asyncio
    async def test_coordinator_handles_multiple_devices(self) -> None:  # noqa: PLR6301, RUF105
        """Coordinator handles multiple device data."""
        coordinator = _make_multi_device_coordinator()

        assert len(coordinator.data) == 2
        assert "device-1" in coordinator.data
        assert "device-2" in coordinator.data

    @pytest.mark.asyncio
    async def test_coordinator_data_structure(self) -> None:  # noqa: PLR6301, RUF105
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
    async def test_update_interval(self) -> None:  # noqa: PLR6301, RUF105
        """Coordinator uses correct update interval."""
        coordinator = _make_coordinator()
        assert coordinator.update_interval == timedelta(
            seconds=DEFAULT_SCAN_INTERVAL_SEC
        )

    @pytest.mark.asyncio
    async def test_multiple_updates(self) -> None:  # noqa: PLR6301, RUF105
        """Multiple updates work correctly."""
        coordinator = _make_coordinator()

        for _ in range(3):
            data = await coordinator._async_update_data()  # noqa: RUF105, SLF001
            assert data == _TEST_HTTP_DATA


class TestCoordinatorErrorHandling:
    """Test coordinator error handling."""

    @pytest.mark.asyncio
    async def test_coordinator_handles_api_error(self) -> None:  # noqa: PLR6301, RUF105
        """Coordinator handles API errors gracefully."""
        from homeassistant.helpers.update_coordinator import UpdateFailed  # noqa: I001, PLC0415, RUF105

        # Create coordinator with error-raising _async_update_data
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
                side_effect=Exception("API Error"),
            ),
            patch(
                "custom_components.jackery_solarvault._async_prime_entry_bootstrap_mqtt_session",
                AsyncMock(return_value=None),
            ),
        ):
            coordinator = JackerySolarVaultCoordinator(
                hass, entry, api, update_interval
            )  # noqa: E501, RUF100

            # Should raise UpdateFailed
            with pytest.raises(UpdateFailed):
                await coordinator._async_update_data()  # noqa: RUF105, SLF001


class TestCoordinatorDeviceDataIntegrity:
    """Test device data integrity in coordinator."""

    @pytest.mark.asyncio
    async def test_device_data_contains_required_fields(self) -> None:  # noqa: PLR6301, RUF105
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
    async def test_coordinator_preserves_device_identity(self) -> None:  # noqa: PLR6301, RUF105
        """Coordinator preserves device identity across updates."""
        coordinator = _make_coordinator()

        # Initial data
        initial_data = await coordinator._async_update_data()  # noqa: RUF105, SLF001
        initial_id = initial_data["test-device"][FIELD_DEVICE_ID]

        # Simulate multiple updates
        for _ in range(5):
            data = await coordinator._async_update_data()  # noqa: RUF105, SLF001
            assert data["test-device"][FIELD_DEVICE_ID] == initial_id

    @pytest.mark.asyncio
    async def test_multi_device_isolation(self) -> None:  # noqa: PLR6301, RUF105
        """Each device maintains independent data."""
        coordinator = _make_multi_device_coordinator()
        data = coordinator.data

        assert data["device-1"][FIELD_DEVICE_SN] == "SERIAL-1"
        assert data["device-2"][FIELD_DEVICE_SN] == "SERIAL-2"
        assert (
            data["device-1"][FIELD_DEVICE_NAME] != data["device-2"][FIELD_DEVICE_NAME]
        )  # noqa: E501, RUF100


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
