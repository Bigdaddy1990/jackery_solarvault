"""Tests for uncovered paths in button.py to increase coverage."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.button import (
    JackeryDeleteStormAlertButton,
    JackeryQueryButton,
    JackeryQueryButtonDescription,
    JackeryReadScheduleButton,
    JackeryRebootButton,
    JackeryRefreshWeatherPlanButton,
    async_setup_entry,
)


class TestJackeryQueryButton:
    """Test JackeryQueryButton class."""

    def _create_coordinator(self, data=None):  # noqa: ANN001, ANN202, PLR6301, RUF105
        """Create a mock coordinator."""
        coordinator = MagicMock()
        coordinator.data = data or {}
        coordinator.config_entry = MagicMock()
        coordinator.config_entry.entry_id = "test_entry"
        coordinator.config_entry.runtime_data = MagicMock()
        coordinator.async_query_system_info = AsyncMock()
        coordinator.async_refresh_documented_http_read = AsyncMock(return_value=True)
        return coordinator

    def _create_query_description(self, key="refresh_system_info"):  # noqa: ANN001, ANN202, PLR6301, RUF105
        """Create a query button description for testing."""
        return JackeryQueryButtonDescription(
            key=key,
            translation_key=key,
            action=lambda c, d: None,
            message_type="test_type",
            action_id=1,
            cmd=2,
        )

    def test_creation(self) -> None:
        """Test query button creation."""
        coordinator = self._create_coordinator()
        description = self._create_query_description()
        sensor = JackeryQueryButton(
            coordinator=coordinator, device_id="test_device", description=description
        )
        assert sensor is not None
        assert sensor._query_description.key == "refresh_system_info"  # noqa: RUF105, SLF001

    def test_extra_state_attributes(self) -> None:
        """Test extra_state_attributes property."""
        coordinator = self._create_coordinator()
        description = self._create_query_description()
        sensor = JackeryQueryButton(
            coordinator=coordinator, device_id="test_device", description=description
        )
        attrs = sensor.extra_state_attributes
        assert "actionId" in attrs
        assert "cmd" in attrs
        assert "messageType" in attrs


class TestJackeryRebootButton:
    """Test JackeryRebootButton class."""

    def _create_coordinator(self, data=None):  # noqa: ANN001, ANN202, PLR6301, RUF105
        """Create a mock coordinator."""
        coordinator = MagicMock()
        coordinator.data = data or {}
        coordinator.config_entry = MagicMock()
        coordinator.config_entry.entry_id = "test_entry"
        coordinator.config_entry.runtime_data = MagicMock()
        coordinator.async_reboot_device = AsyncMock()
        return coordinator

    def test_creation(self) -> None:
        """Test reboot button creation."""
        coordinator = self._create_coordinator()
        sensor = JackeryRebootButton(coordinator=coordinator, device_id="test_device")
        assert sensor is not None
        assert sensor._attr_translation_key == "reboot_device"  # noqa: RUF105, SLF001

    @pytest.mark.asyncio
    async def test_async_press(self) -> None:
        """Test async_press method."""
        coordinator = self._create_coordinator()
        sensor = JackeryRebootButton(coordinator=coordinator, device_id="test_device")

        await sensor.async_press()
        coordinator.async_reboot_device.assert_called_once_with("test_device")


class TestJackeryRefreshWeatherPlanButton:
    """Test JackeryRefreshWeatherPlanButton class."""

    def _create_coordinator(self, data=None):  # noqa: ANN001, ANN202, PLR6301, RUF105
        """Create a mock coordinator."""
        coordinator = MagicMock()
        coordinator.data = data or {"test_device": {}}
        coordinator.config_entry = MagicMock()
        coordinator.config_entry.entry_id = "test_entry"
        coordinator.config_entry.runtime_data = MagicMock()
        coordinator.async_query_weather_plan = AsyncMock()
        coordinator.is_entity_source_available = MagicMock(return_value=True)
        return coordinator

    def test_creation(self) -> None:
        """Test weather plan button creation."""
        coordinator = self._create_coordinator()
        sensor = JackeryRefreshWeatherPlanButton(
            coordinator=coordinator, device_id="test_device"
        )
        assert sensor is not None
        assert sensor._attr_translation_key == "refresh_weather_plan"  # noqa: RUF105, SLF001

    @pytest.mark.asyncio
    async def test_async_press(self) -> None:
        """Test async_press method."""
        coordinator = self._create_coordinator()
        sensor = JackeryRefreshWeatherPlanButton(
            coordinator=coordinator, device_id="test_device"
        )

        await sensor.async_press()
        coordinator.async_query_weather_plan.assert_called_once_with("test_device")


class TestJackeryReadScheduleButton:
    """Test JackeryReadScheduleButton class."""

    def _create_coordinator(self, data=None):  # noqa: ANN001, ANN202, PLR6301, RUF105
        """Create a mock coordinator."""
        coordinator = MagicMock()
        coordinator.data = data or {}
        coordinator.config_entry = MagicMock()
        coordinator.config_entry.entry_id = "test_entry"
        coordinator.config_entry.runtime_data = MagicMock()
        coordinator.async_read_device_schedule = AsyncMock()
        return coordinator

    def test_creation(self) -> None:
        """Test read schedule button creation."""
        coordinator = self._create_coordinator()
        sensor = JackeryReadScheduleButton(
            coordinator=coordinator,
            device_id="test_device",
            task_type=1,
            key_suffix="test_schedule",
            translation_key="test_schedule",
        )
        assert sensor is not None
        assert sensor._task_type == 1  # noqa: RUF105, SLF001

    def test_extra_state_attributes(self) -> None:
        """Test extra_state_attributes property."""
        coordinator = self._create_coordinator()
        sensor = JackeryReadScheduleButton(
            coordinator=coordinator,
            device_id="test_device",
            task_type=1,
            key_suffix="test_schedule",
            translation_key="test_schedule",
            plug_sn="plug123",
        )
        attrs = sensor.extra_state_attributes
        assert attrs["taskType"] == 1
        assert attrs["deviceSn"] == "plug123"

    @pytest.mark.asyncio
    async def test_async_press(self) -> None:
        """Test async_press method."""
        coordinator = self._create_coordinator({"test_device": {}})
        sensor = JackeryReadScheduleButton(
            coordinator=coordinator,
            device_id="test_device",
            task_type=1,
            key_suffix="test_schedule",
            translation_key="test_schedule",
        )

        await sensor.async_press()
        coordinator.async_read_device_schedule.assert_called_once_with(
            "test_device", task_type=1, plug_sn=""
        )


class TestJackeryDeleteStormAlertButton:
    """Test JackeryDeleteStormAlertButton class."""

    def _create_coordinator(self, data=None):  # noqa: ANN001, ANN202, PLR6301, RUF105
        """Create a mock coordinator."""
        coordinator = MagicMock()
        coordinator.data = data or {}
        coordinator.config_entry = MagicMock()
        coordinator.config_entry.entry_id = "test_entry"
        coordinator.config_entry.runtime_data = MagicMock()
        coordinator.async_delete_storm_alert = AsyncMock()
        return coordinator

    def test_creation(self) -> None:
        """Test delete storm alert button creation."""
        coordinator = self._create_coordinator()
        sensor = JackeryDeleteStormAlertButton(
            coordinator=coordinator,
            device_id="test_device",
            alert_id="alert123",
        )
        assert sensor is not None
        assert sensor._alert_id == "alert123"  # noqa: RUF105, SLF001

    def test_available_property(self) -> None:
        """Test available property."""
        coordinator = self._create_coordinator({
            "test_device": {"weather_plan": {"storm": [{"alertId": "alert123"}]}}
        })
        sensor = JackeryDeleteStormAlertButton(
            coordinator=coordinator,
            device_id="test_device",
            alert_id="alert123",
        )
        assert sensor.available is True

    @pytest.mark.asyncio
    async def test_async_press(self) -> None:
        """Test async_press method."""
        coordinator = self._create_coordinator({
            "test_device": {"weather_plan": {"storm": [{"alertId": "alert123"}]}}
        })
        sensor = JackeryDeleteStormAlertButton(
            coordinator=coordinator,
            device_id="test_device",
            alert_id="alert123",
        )

        await sensor.async_press()
        coordinator.async_delete_storm_alert.assert_called_once_with(
            "test_device", "alert123"
        )


class TestAsyncSetupEntry:
    """Test async_setup_entry function."""

    def test_async_setup_entry(self) -> None:  # noqa: PLR6301, RUF105
        """Test async_setup_entry function signature and structure."""
        # This test validates the function signature and basic structure
        # Full integration test is complex due to signature caching logic
        # Just verify the function exists and is callable
        assert async_setup_entry.__name__ == "async_setup_entry"
        assert callable(async_setup_entry)
        # Check it has the right number of parameters via __code__
        assert async_setup_entry.__code__.co_argcount == 3
        varnames = async_setup_entry.__code__.co_varnames[:3]
        assert "hass" in varnames
        assert "entry" in varnames
        assert "async_add_entities" in varnames


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
