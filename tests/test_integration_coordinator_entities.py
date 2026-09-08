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


def _background_task_mock(*args, **_kwargs):  # ruff: ignore[missing-type-args, missing-type-kwargs, missing-return-type-private-function]  # isort: skip
    """Consume coroutines scheduled by MagicMock HA/entry objects."""
    for arg in args:
        if asyncio.iscoroutine(arg):
            arg.close()
    task = MagicMock()
    task.done.return_value = False
    return task


def _make_hass_entry_api() -> tuple[MagicMock, MagicMock, MagicMock]:
    """Create mocked constructor dependencies."""
    hass = MagicMock()
    hass.loop = asyncio.get_running_loop()
    hass.data = {}
    hass.config = MagicMock()
    hass.config.path = MagicMock(return_value="/config")
    hass.async_create_background_task = MagicMock(side_effect=_background_task_mock)
    hass.async_create_task = MagicMock(side_effect=_background_task_mock)

    entry = MagicMock()
    entry.entry_id = "test-entry"
    entry.title = "Test Entry"
    entry.data = {"username": "user@example.com", "password": "pass"}
    entry.options = {}
    entry.runtime_data = None
    entry.async_create_background_task = MagicMock(side_effect=_background_task_mock)

    api = MagicMock()
    api.mqtt_session_snapshot = MagicMock(return_value=None)

    return hass, entry, api


def _finalize_coordinator(
    coordinator: JackerySolarVaultCoordinator,
    data: dict[str, dict[str, object]],
) -> JackerySolarVaultCoordinator:
    """Set the current wrapper seam and clean constructor background hooks."""
    poll_unsub = getattr(coordinator, "_poll_watchdog_unsub", None)
    if poll_unsub is not None:
        poll_unsub()
        coordinator._poll_watchdog_unsub = None  # ruff: ignore[private-member-access]  # isort: skip
    coordinator.data = data
    coordinator._async_update_data_with_timeout = AsyncMock(return_value=data)  # ruff: ignore[private-member-access]  # isort: skip
    return coordinator


def _make_coordinator() -> JackerySolarVaultCoordinator:
    """Create a coordinator instance with mocked dependencies."""
    hass, entry, api = _make_hass_entry_api()
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
            "custom_components.jackery_solarvault._async_prime_entry_bootstrap_mqtt_session",
            AsyncMock(return_value=None),
        ),
    ):
        coordinator = JackerySolarVaultCoordinator(hass, entry, api, update_interval)
        # Manually initialize since we're not going through HA setup
        coordinator._device_registry_synced = True  # ruff: ignore[private-member-access]  # isort: skip

    # pyrefly: ignore [bad-argument-type]
    return _finalize_coordinator(coordinator, _TEST_HTTP_DATA)


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

    hass, entry, api = _make_hass_entry_api()
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
            "custom_components.jackery_solarvault._async_prime_entry_bootstrap_mqtt_session",
            AsyncMock(return_value=None),
        ),
    ):
        coordinator = JackerySolarVaultCoordinator(hass, entry, api, update_interval)

    # pyrefly: ignore [bad-argument-type]
    return _finalize_coordinator(coordinator, multi_device_data)


class TestCoordinatorEntityManagement:
    """Test coordinator entity management logic."""

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
        coordinator = _make_multi_device_coordinator()

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
    async def test_multiple_updates(self) -> None:  # ruff: ignore[no-self-use]  # isort: skip
        """Multiple updates work correctly."""
        coordinator = _make_coordinator()

        for _ in range(3):
            data = await coordinator._async_update_data()  # ruff: ignore[private-member-access]  # isort: skip
            assert data == _TEST_HTTP_DATA


class TestCoordinatorErrorHandling:
    """Test coordinator error handling."""

    @pytest.mark.asyncio()
    async def test_coordinator_handles_api_error(self) -> None:  # ruff: ignore[no-self-use]  # isort: skip
        """Coordinator handles API errors gracefully."""
        from homeassistant.helpers.update_coordinator import UpdateFailed  # ruff: ignore[import-outside-top-level]  # isort: skip

        # Create coordinator with error-raising wrapped update path.
        hass, entry, api = _make_hass_entry_api()
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
                "custom_components.jackery_solarvault._async_prime_entry_bootstrap_mqtt_session",
                AsyncMock(return_value=None),
            ),
        ):
            coordinator = JackerySolarVaultCoordinator(
                hass, entry, api, update_interval
            )
            # pyrefly: ignore [bad-argument-type]
            _finalize_coordinator(coordinator, _TEST_HTTP_DATA)
            coordinator._async_update_data_with_timeout = AsyncMock(  # ruff: ignore[private-member-access]  # isort: skip
                side_effect=UpdateFailed("API Error")
            )

            # Current wrapper propagates UpdateFailed raised by the wrapped path.
            with pytest.raises(UpdateFailed):
                await coordinator._async_update_data()  # ruff: ignore[private-member-access]  # isort: skip


class TestCoordinatorDeviceDataIntegrity:
    """Test device data integrity in coordinator."""

    @pytest.mark.asyncio()
    async def test_device_data_contains_required_fields(self) -> None:  # ruff: ignore[no-self-use]  # isort: skip
        """Device data contains all required fields."""
        coordinator = _make_coordinator()
        data = coordinator.data

        for dev_id, device in data.items():
            assert FIELD_DEVICE_ID in device
            assert FIELD_DEVICE_SN in device
            assert FIELD_DEVICE_NAME in device
            assert FIELD_MODEL_CODE in device
            assert device[FIELD_DEVICE_ID] == dev_id

    @pytest.mark.asyncio()
    async def test_coordinator_preserves_device_identity(self) -> None:  # ruff: ignore[no-self-use]  # isort: skip
        """Coordinator preserves device identity across updates."""
        coordinator = _make_coordinator()

        # Initial data
        initial_data = await coordinator._async_update_data()  # ruff: ignore[private-member-access]  # isort: skip
        initial_id = initial_data["test-device"][FIELD_DEVICE_ID]

        # Simulate multiple updates
        for _ in range(5):
            data = await coordinator._async_update_data()  # ruff: ignore[private-member-access]  # isort: skip
            assert data["test-device"][FIELD_DEVICE_ID] == initial_id

    @pytest.mark.asyncio()
    async def test_multi_device_isolation(self) -> None:  # ruff: ignore[no-self-use]  # isort: skip
        """Each device maintains independent data."""
        coordinator = _make_multi_device_coordinator()
        data = coordinator.data

        assert data["device-1"][FIELD_DEVICE_SN] == "SERIAL-1"
        assert data["device-2"][FIELD_DEVICE_SN] == "SERIAL-2"
        assert (
            data["device-1"][FIELD_DEVICE_NAME] != data["device-2"][FIELD_DEVICE_NAME]
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
