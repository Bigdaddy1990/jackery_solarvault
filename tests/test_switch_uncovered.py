"""Tests for uncovered paths in switch.py to increase coverage."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.switch import (
    JackeryBreakerSwitch,
    JackeryDescriptionSwitch,
    JackerySmartPlugPrioritySwitch,
    JackerySmartPlugSwitch,
    JackerySwitchDescription,
    _standby_is_on,
    async_setup_entry,
)
from homeassistant.helpers.entity import EntityCategory


class TestStandbyIsOn:
    """Test _standby_is_on helper function."""

    def test_none_returns_none(self) -> None:
        """Test None returns None."""
        assert _standby_is_on(None) is None

    def test_one_returns_true(self) -> None:
        """Test 1 returns True."""
        assert _standby_is_on(1) is True

    def test_zero_returns_false(self) -> None:
        """Test 0 returns False."""
        assert _standby_is_on(0) is False

    def test_true_returns_true(self) -> None:
        """Test True returns True."""
        assert _standby_is_on(True) is True

    def test_false_returns_false(self) -> None:
        """Test False returns False."""
        assert _standby_is_on(False) is False


class TestJackeryDescriptionSwitch:
    """Test JackeryDescriptionSwitch class."""

    def _create_coordinator(self, data=None):
        """Create a mock coordinator."""
        coordinator = MagicMock()
        coordinator.data = data or {}
        coordinator.config_entry = MagicMock()
        coordinator.config_entry.entry_id = "test_entry"
        coordinator.config_entry.runtime_data = MagicMock()
        coordinator.async_set_eps = AsyncMock()
        coordinator.async_set_auto_standby = AsyncMock()
        coordinator.async_set_standby = AsyncMock()
        coordinator.async_set_follow_meter = AsyncMock()
        coordinator.async_set_off_grid_shutdown = AsyncMock()
        coordinator.async_set_storm_warning = AsyncMock()
        coordinator.async_update_third_party_mqtt_config = AsyncMock()
        coordinator.async_portable_toggle_output = AsyncMock()
        coordinator.device_supports_advanced = MagicMock(return_value=True)
        return coordinator

    def test_creation(self) -> None:
        """Test description switch creation."""
        coordinator = self._create_coordinator()
        description = JackerySwitchDescription(
            key="eps_output",
            translation_key="eps_output",
            entity_category=EntityCategory.CONFIG,
            source_keys=("swEps",),
            setter=lambda c, d, v: None,
        )
        sensor = JackeryDescriptionSwitch(
            coordinator=coordinator, device_id="test_device", description=description
        )
        assert sensor is not None
        assert sensor.entity_description.key == "eps_output"

    def test_is_on_with_data(self) -> None:
        """Test is_on property with data."""
        coordinator = self._create_coordinator({
            "test_device": {"properties": {"swEps": 1}}
        })
        description = JackerySwitchDescription(
            key="eps_output",
            translation_key="eps_output",
            entity_category=EntityCategory.CONFIG,
            source_keys=("swEps",),
            setter=lambda c, d, v: None,
        )
        sensor = JackeryDescriptionSwitch(
            coordinator=coordinator, device_id="test_device", description=description
        )
        assert sensor.is_on is True

    def test_is_on_false_with_data(self) -> None:
        """Test is_on property with false data."""
        coordinator = self._create_coordinator({
            "test_device": {"properties": {"swEps": 0}}
        })
        description = JackerySwitchDescription(
            key="eps_output",
            translation_key="eps_output",
            entity_category=EntityCategory.CONFIG,
            source_keys=("swEps",),
            setter=lambda c, d, v: None,
        )
        sensor = JackeryDescriptionSwitch(
            coordinator=coordinator, device_id="test_device", description=description
        )
        assert sensor.is_on is False

    def test_is_on_none_when_missing(self) -> None:
        """Test is_on property when key is missing."""
        coordinator = self._create_coordinator({"test_device": {"properties": {}}})
        description = JackerySwitchDescription(
            key="eps_output",
            translation_key="eps_output",
            entity_category=EntityCategory.CONFIG,
            source_keys=("swEps",),
            setter=lambda c, d, v: None,
        )
        sensor = JackeryDescriptionSwitch(
            coordinator=coordinator, device_id="test_device", description=description
        )
        assert sensor.is_on is None

    @pytest.mark.asyncio
    async def test_async_turn_on(self) -> None:
        """Test async_turn_on method."""
        coordinator = self._create_coordinator()
        mock_setter = AsyncMock()
        description = JackerySwitchDescription(
            key="eps_output",
            translation_key="eps_output",
            entity_category=EntityCategory.CONFIG,
            source_keys=("swEps",),
            setter=mock_setter,
        )
        sensor = JackeryDescriptionSwitch(
            coordinator=coordinator, device_id="test_device", description=description
        )

        await sensor.async_turn_on()
        mock_setter.assert_called_once()

    @pytest.mark.asyncio
    async def test_async_turn_off(self) -> None:
        """Test async_turn_off method."""
        coordinator = self._create_coordinator()
        mock_setter = AsyncMock()
        description = JackerySwitchDescription(
            key="eps_output",
            translation_key="eps_output",
            entity_category=EntityCategory.CONFIG,
            source_keys=("swEps",),
            setter=mock_setter,
        )
        sensor = JackeryDescriptionSwitch(
            coordinator=coordinator, device_id="test_device", description=description
        )

        await sensor.async_turn_off()
        mock_setter.assert_called_once()


class TestJackerySmartPlugSwitch:
    """Test JackerySmartPlugSwitch class."""

    def _create_coordinator(self, data=None):
        """Create a mock coordinator."""
        coordinator = MagicMock()
        coordinator.data = data or {}
        coordinator.config_entry = MagicMock()
        coordinator.config_entry.entry_id = "test_entry"
        coordinator.config_entry.runtime_data = MagicMock()
        coordinator.async_set_shelly_cloud_switch = AsyncMock()
        coordinator.async_set_smart_plug_switch = AsyncMock()
        return coordinator

    def test_creation(self) -> None:
        """Test smart plug switch creation."""
        coordinator = self._create_coordinator({
            "test_device": {
                "smartPlugs": [
                    {"serialNumber": "plug123", "switchState": 1, "scanName": "test"}
                ]
            }
        })
        sensor = JackerySmartPlugSwitch(
            coordinator=coordinator,
            device_id="test_device",
            plug_index=1,
            plug_sn="plug123",
            plug_key="test_key",
        )
        assert sensor is not None
        assert sensor._plug_sn == "plug123"

    def test_is_on_with_data(self) -> None:
        """Test is_on property with data."""
        coordinator = self._create_coordinator({
            "test_device": {
                "smart_plugs": [
                    {
                        "deviceSn": "plug123",
                        "switchSta": 1,
                        "sysSwitch": 1,
                    }
                ]
            }
        })
        sensor = JackerySmartPlugSwitch(
            coordinator=coordinator,
            device_id="test_device",
            plug_index=1,
            plug_sn="plug123",
            plug_key="plug_key_1",
        )
        assert sensor.is_on is True

    def test_is_on_false_with_data(self) -> None:
        """Test is_on property with false data."""
        coordinator = self._create_coordinator({
            "test_device": {
                "smart_plugs": [
                    {
                        "deviceSn": "plug123",
                        "switchSta": 0,
                        "sysSwitch": 0,
                    }
                ]
            }
        })
        sensor = JackerySmartPlugSwitch(
            coordinator=coordinator,
            device_id="test_device",
            plug_index=1,
            plug_sn="plug123",
            plug_key="plug_key_1",
        )
        assert sensor.is_on is False


class TestJackeryBreakerSwitch:
    """Test JackeryBreakerSwitch class."""

    def _create_coordinator(self, data=None):
        """Create a mock coordinator."""
        coordinator = MagicMock()
        coordinator.data = data or {}
        coordinator.config_entry = MagicMock()
        coordinator.config_entry.entry_id = "test_entry"
        coordinator.config_entry.runtime_data = MagicMock()
        coordinator.async_set_breaker_switch = AsyncMock()
        return coordinator

    def test_creation(self) -> None:
        """Test breaker switch creation."""
        from custom_components.jackery_solarvault.const import PAYLOAD_CIRCUIT_PROPERTY
        coordinator = self._create_coordinator({
            "test_device": {PAYLOAD_CIRCUIT_PROPERTY: [{"id": "br1", "sw": 1}]}
        })
        sensor = JackeryBreakerSwitch(
            coordinator=coordinator,
            device_id="test_device",
            breaker_index=1,
            breaker_id="br1",
            breaker_key="test_key",
        )
        assert sensor is not None
        assert sensor._breaker_id == "br1"

    def test_is_on_with_data(self) -> None:
        """Test is_on property with data."""
        from custom_components.jackery_solarvault.const import PAYLOAD_CIRCUIT_PROPERTY
        coordinator = self._create_coordinator({
            "test_device": {PAYLOAD_CIRCUIT_PROPERTY: [{"id": "br1", "sw": 1}]}
        })
        sensor = JackeryBreakerSwitch(
            coordinator=coordinator,
            device_id="test_device",
            breaker_index=1,
            breaker_id="br1",
            breaker_key="test_key",
        )
        assert sensor.is_on is True

    def test_is_on_false_with_data(self) -> None:
        """Test is_on property with false data."""
        from custom_components.jackery_solarvault.const import PAYLOAD_CIRCUIT_PROPERTY
        coordinator = self._create_coordinator({
            "test_device": {PAYLOAD_CIRCUIT_PROPERTY: [{"id": "br1", "sw": 0}]}
        })
        sensor = JackeryBreakerSwitch(
            coordinator=coordinator,
            device_id="test_device",
            breaker_index=1,
            breaker_id="br1",
            breaker_key="test_key",
        )
        assert sensor.is_on is False

    @pytest.mark.asyncio
    async def test_async_turn_on(self) -> None:
        """Test async_turn_on method."""
        coordinator = self._create_coordinator()
        sensor = JackeryBreakerSwitch(
            coordinator=coordinator,
            device_id="test_device",
            breaker_index=1,
            breaker_id="br1",
            breaker_key="test_key",
        )

        await sensor.async_turn_on()
        coordinator.async_set_breaker_switch.assert_called_once_with(
            "test_device", "br1", True
        )

    @pytest.mark.asyncio
    async def test_async_turn_off(self) -> None:
        """Test async_turn_off method."""
        coordinator = self._create_coordinator()
        sensor = JackeryBreakerSwitch(
            coordinator=coordinator,
            device_id="test_device",
            breaker_index=1,
            breaker_id="br1",
            breaker_key="test_key",
        )

        await sensor.async_turn_off()
        coordinator.async_set_breaker_switch.assert_called_once_with(
            "test_device", "br1", False
        )


class TestJackerySmartPlugPrioritySwitch:
    """Test JackerySmartPlugPrioritySwitch class."""

    def _create_coordinator(self, data=None):
        """Create a mock coordinator."""
        coordinator = MagicMock()
        coordinator.data = data or {}
        coordinator.config_entry = MagicMock()
        coordinator.config_entry.entry_id = "test_entry"
        coordinator.config_entry.runtime_data = MagicMock()
        coordinator.async_set_smart_plug_priority = AsyncMock()
        return coordinator

    def test_creation(self) -> None:
        """Test priority switch creation."""
        coordinator = self._create_coordinator({
            "test_device": {
                "smart_plugs": [{"deviceSn": "plug123", "socketPri": 1}]
            }
        })
        sensor = JackerySmartPlugPrioritySwitch(
            coordinator=coordinator,
            device_id="test_device",
            plug_index=1,
            plug_sn="plug123",
            plug_key="test_key",
        )
        assert sensor is not None
        assert sensor._plug_sn == "plug123"

    def test_is_on_with_data(self) -> None:
        """Test is_on property with data."""
        coordinator = self._create_coordinator({
            "test_device": {
                "smart_plugs": [{"deviceSn": "plug123", "socketPri": 1}]
            }
        })
        sensor = JackerySmartPlugPrioritySwitch(
            coordinator=coordinator,
            device_id="test_device",
            plug_index=1,
            plug_sn="plug123",
            plug_key="test_key",
        )
        assert sensor.is_on is True

    def test_is_on_false_with_data(self) -> None:
        """Test is_on property with false data."""
        coordinator = self._create_coordinator({
            "test_device": {
                "smart_plugs": [{"deviceSn": "plug123", "socketPri": 0}]
            }
        })
        sensor = JackerySmartPlugPrioritySwitch(
            coordinator=coordinator,
            device_id="test_device",
            plug_index=1,
            plug_sn="plug123",
            plug_key="test_key",
        )
        assert sensor.is_on is False


class TestAsyncSetupEntry:
    """Test async_setup_entry function."""

    @pytest.mark.asyncio
    async def test_async_setup_entry(self) -> None:
        """Test async_setup_entry creates switch entities."""
        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        config_entry.async_on_unload = MagicMock()

        async_add_entities = MagicMock()

        # Mock coordinator (entry.runtime_data IS the coordinator)
        coordinator = MagicMock()
        from custom_components.jackery_solarvault.const import (
            PAYLOAD_CIRCUIT_PROPERTY,
            PAYLOAD_SMART_PLUGS,
        )
        coordinator.data = {
            "test_device": {
                "properties": {"swEps": 1},
                PAYLOAD_SMART_PLUGS: [{"deviceSn": "plug123", "socketPriority": 1}],
                PAYLOAD_CIRCUIT_PROPERTY: [{"id": "br1", "sw": 1}],
            }
        }
        coordinator.device_supports_advanced = MagicMock(return_value=True)
        coordinator.async_add_listener = MagicMock(return_value=lambda: None)
        config_entry.runtime_data = coordinator

        await async_setup_entry(hass, config_entry, async_add_entities)

        # Verify async_add_entities was called
        assert async_add_entities.called
        args = async_add_entities.call_args
        sensors = args[0][0]
        assert len(sensors) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
