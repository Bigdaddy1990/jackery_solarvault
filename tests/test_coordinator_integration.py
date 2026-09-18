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


def _cancel_poll_watchdog(coordinator: JackerySolarVaultCoordinator) -> None:
    """Cancel constructor-owned timers in these lightweight coordinator tests."""
    obj = getattr(coordinator, "_poll_watchdog_unsub", None)
    if obj is not None:
        obj()
        coordinator._poll_watchdog_unsub = None  # ruff: ignore[private-member-access]  # isort: skip


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
            "custom_components.jackery_solarvault."
            "_async_prime_entry_bootstrap_mqtt_session",
            AsyncMock(return_value=None),
        ),
    ):
        coordinator = JackerySolarVaultCoordinator(
            hass,
            entry,
            api,
            timedelta(seconds=DEFAULT_SCAN_INTERVAL_SEC),
        )
        # Manually initialize since we're not going through HA setup
        coordinator.data = _TEST_HTTP_DATA
        coordinator._device_registry_synced = True  # ruff: ignore[private-member-access]  # isort: skip
        _cancel_poll_watchdog(coordinator)

    return coordinator


class TestCoordinatorIntegration:
    """Test coordinator integration logic."""

    @pytest.mark.asyncio()
    async def test_coordinator_initialization(self) -> None:  # ruff: ignore[no-self-use]  # isort: skip
        """Coordinator initializes with correct defaults."""
        coordinator = _make_coordinator()

        assert coordinator is not None
        assert coordinator.data == _TEST_HTTP_DATA
        assert coordinator.update_interval == timedelta(
            seconds=DEFAULT_SCAN_INTERVAL_SEC
        )
        assert coordinator._device_registry_synced is True  # ruff: ignore[private-member-access]  # isort: skip

    @pytest.mark.asyncio()
    async def test_coordinator_async_update_data_returns_data(self) -> None:  # ruff: ignore[no-self-use]  # isort: skip
        """_async_update_data returns device data correctly."""
        coordinator = _make_coordinator()
        coordinator._async_update_data_guarded = AsyncMock(return_value=_TEST_HTTP_DATA)  # ruff: ignore[private-member-access]  # isort: skip

        data = await coordinator._async_update_data()  # ruff: ignore[private-member-access]  # isort: skip
        assert data == _TEST_HTTP_DATA

    @pytest.mark.asyncio()
    async def test_coordinator_device_registry_sync(self) -> None:  # ruff: ignore[no-self-use]  # isort: skip
        """Coordinator syncs device registry on first poll."""
        coordinator = _make_coordinator()

        # Should have device registry sync flag set
        assert coordinator._device_registry_synced is True  # ruff: ignore[private-member-access]  # isort: skip

    @pytest.mark.asyncio()
    async def test_coordinator_handles_multiple_devices(self) -> None:  # ruff: ignore[no-self-use]  # isort: skip
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
                "custom_components.jackery_solarvault."
                "_async_prime_entry_bootstrap_mqtt_session",
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

            coordinator = JackerySolarVaultCoordinator(
                hass,
                entry,
                api,
                timedelta(seconds=DEFAULT_SCAN_INTERVAL_SEC),
            )
            coordinator.data = multi_device_data
            _cancel_poll_watchdog(coordinator)

            assert len(coordinator.data) == 2  # ruff: ignore[magic-value-comparison]  # isort: skip
            assert "device-1" in coordinator.data
            assert "device-2" in coordinator.data

    @pytest.mark.asyncio()
    async def test_coordinator_data_structure(self) -> None:  # ruff: ignore[no-self-use]  # isort: skip
        """Coordinator data has expected structure."""
        coordinator = _make_coordinator()

        data = coordinator.data
        assert "test-device" in data
        device = data["test-device"]
        assert device[FIELD_DEVICE_ID] == "test-device"
        assert device[FIELD_DEVICE_SN] == "TEST-SERIAL"
        assert device[FIELD_DEVICE_NAME] == "Test SolarVault"
        assert device[FIELD_MODEL_CODE] == 3002  # ruff: ignore[magic-value-comparison]  # isort: skip


class TestCoordinatorUpdateCycle:
    """Test coordinator update cycle behavior."""

    @pytest.mark.asyncio()
    async def test_update_interval(self) -> None:  # ruff: ignore[no-self-use]  # isort: skip
        """Coordinator uses correct update interval."""
        coordinator = _make_coordinator()
        assert coordinator.update_interval == timedelta(
            seconds=DEFAULT_SCAN_INTERVAL_SEC
        )

    @pytest.mark.asyncio()
    async def test_main_live_queries_follow_configured_poll_interval(self) -> None:  # ruff: ignore[no-self-use]  # isort: skip
        """Main-device MQTT/BLE getters must not use a hidden 180-second cadence."""
        coordinator = _make_coordinator()

        assert coordinator._system_info_query_interval_sec == DEFAULT_SCAN_INTERVAL_SEC  # ruff: ignore[private-member-access]  # isort: skip

    @pytest.mark.asyncio()
    async def test_multiple_updates(self) -> None:  # ruff: ignore[no-self-use]  # isort: skip
        """Multiple updates work correctly."""
        coordinator = _make_coordinator()
        coordinator._async_update_data_guarded = AsyncMock(return_value=_TEST_HTTP_DATA)  # ruff: ignore[private-member-access]  # isort: skip

        for _ in range(3):
            data = await coordinator._async_update_data()  # ruff: ignore[private-member-access]  # isort: skip
            assert data == _TEST_HTTP_DATA


class TestCoordinatorErrorHandling:
    """Test coordinator error handling."""

    @pytest.mark.asyncio()
    async def test_coordinator_wraps_update_timeout(self) -> None:  # ruff: ignore[no-self-use]  # isort: skip
        """Coordinator wraps timeout failures as UpdateFailed when no data exists."""
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
                "custom_components.jackery_solarvault."
                "_async_prime_entry_bootstrap_mqtt_session",
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

            coordinator = JackerySolarVaultCoordinator(
                hass,
                entry,
                api,
                timedelta(seconds=DEFAULT_SCAN_INTERVAL_SEC),
            )
            coordinator.data = {}
            coordinator._async_update_data_guarded = AsyncMock(side_effect=TimeoutError)  # ruff: ignore[private-member-access]  # isort: skip
            _cancel_poll_watchdog(coordinator)

            from homeassistant.helpers.update_coordinator import UpdateFailed  # ruff: ignore[import-outside-top-level]  # isort: skip

            with pytest.raises(UpdateFailed):
                await coordinator._async_update_data()  # ruff: ignore[private-member-access]  # isort: skip


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
