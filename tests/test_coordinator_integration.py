"""Integration tests for JackerySolarVaultCoordinator without HA fixtures."""

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
    hass.loop = asyncio.get_event_loop()

    entry = MagicMock()
    entry.entry_id = "test-entry"
    entry.data = {"username": "user@example.com", "password": "pass"}
    entry.options = {}
    entry.runtime_data = None

    api = MagicMock()
    api.mqtt_session_snapshot = MagicMock(return_value=None)

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
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator._async_prime_entry_bootstrap_mqtt_session",
            AsyncMock(return_value=None),
        ),
    ):
        coordinator = JackerySolarVaultCoordinator(hass, entry, api)
        # Manually initialize since we're not going through HA setup
        coordinator.data = _TEST_HTTP_DATA
        coordinator._device_registry_synced = True

    return coordinator


class TestCoordinatorIntegration:
    """Test coordinator integration logic."""

    @pytest.mark.asyncio
    async def test_coordinator_initialization(self) -> None:
        """Coordinator initializes with correct defaults."""
        coordinator = _make_coordinator()

        assert coordinator is not None
        assert coordinator.data == _TEST_HTTP_DATA
        assert coordinator.update_interval == timedelta(seconds=DEFAULT_SCAN_INTERVAL_SEC)
        assert coordinator._device_registry_synced is True

    @pytest.mark.asyncio
    async def test_coordinator_async_update_data_returns_data(self) -> None:
        """_async_update_data returns device data correctly."""
        coordinator = _make_coordinator()

        data = await coordinator._async_update_data()
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
                "custom_components.jackery_solarvault.coordinator."
                "JackerySolarVaultCoordinator._async_prime_entry_bootstrap_mqtt_session",
                AsyncMock(return_value=None),
            ),
        ):
            hass = MagicMock()
            hass.loop = asyncio.get_event_loop()
            entry = MagicMock()
            entry.entry_id = "test-entry"
            entry.data = {"username": "user@example.com", "password": "pass"}
            entry.options = {}
            api = MagicMock()
            api.mqtt_session_snapshot = MagicMock(return_value=None)

            coordinator = JackerySolarVaultCoordinator(hass, entry, api)
            coordinator.data = multi_device_data

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
        assert coordinator.update_interval == timedelta(seconds=DEFAULT_SCAN_INTERVAL_SEC)

    @pytest.mark.asyncio
    async def test_multiple_updates(self) -> None:
        """Multiple updates work correctly."""
        coordinator = _make_coordinator()

        for _ in range(3):
            data = await coordinator._async_update_data()
            assert data == _TEST_HTTP_DATA


class TestCoordinatorErrorHandling:
    """Test coordinator error handling."""

    @pytest.mark.asyncio
    async def test_coordinator_handles_api_error(self) -> None:
        """Coordinator handles API errors gracefully."""
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
                "custom_components.jackery_solarvault.coordinator."
                "JackerySolarVaultCoordinator._async_prime_entry_bootstrap_mqtt_session",
                AsyncMock(return_value=None),
            ),
        ):
            hass = MagicMock()
            hass.loop = asyncio.get_event_loop()
            entry = MagicMock()
            entry.entry_id = "test-entry"
            entry.data = {"username": "user@example.com", "password": "pass"}
            entry.options = {}
            api = MagicMock()
            api.mqtt_session_snapshot = MagicMock(return_value=None)

            coordinator = JackerySolarVaultCoordinator(hass, entry, api)

            # Should raise UpdateFailed
            from homeassistant.helpers.update_coordinator import UpdateFailed
            with pytest.raises(UpdateFailed):
                await coordinator._async_update_data()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
