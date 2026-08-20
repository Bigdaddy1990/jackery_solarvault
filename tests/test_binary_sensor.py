"""Tests for binary_sensor.py to increase coverage."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock

import pytest

from custom_components.jackery_solarvault.binary_sensor import (
    JackeryBinarySensor,
    JackerySmartPlugStateBinarySensor,
    JackerySubdeviceAlarmBinarySensor,
    async_setup_entry,
)
from custom_components.jackery_solarvault.const import SOLAR_VAULT_HEAD_UNIT_MODEL_CODE


class TestBinarySensor:
    """Test binary sensor classes."""

    def _bare_hass(self) -> Any:  # noqa: ANN401, PLR6301, RUF105
        hass = SimpleNamespace()
        hass.data = {}
        return hass

    def _bare_entry(self) -> Any:  # noqa: ANN401, PLR6301, RUF105
        entry = SimpleNamespace()
        entry.options = {}
        entry.data = {}
        entry.entry_id = "test_entry"
        entry.async_on_unload = MagicMock()
        return entry

    def _bare_coordinator(self, entry: Any) -> Any:  # noqa: ANN401, PLR6301, RUF105
        """Create a bare coordinator with the given entry."""
        coordinator = SimpleNamespace()
        coordinator.entry = entry
        coordinator.data = {
            "test-device": {
                "deviceSn": "test-device",
                "modelCode": SOLAR_VAULT_HEAD_UNIT_MODEL_CODE,
                "deviceType": 1,
                "smartPlugs": [],
                "subDevices": [],
            }
        }
        coordinator.device_data = {
            "deviceSn": "test-device",
            "modelCode": SOLAR_VAULT_HEAD_UNIT_MODEL_CODE,
            "deviceType": 1,
        }
        coordinator.get_device_data = MagicMock(return_value=coordinator.device_data)
        coordinator.async_add_listener = MagicMock()
        # Set entry.runtime_data to point to coordinator
        entry.runtime_data = coordinator
        return coordinator

    @pytest.mark.asyncio
    async def test_async_setup_entry(self) -> None:
        """Test async_setup_entry."""
        hass = self._bare_hass()
        entry = self._bare_entry()
        coordinator = self._bare_coordinator(entry)
        hass.data["jackery_solarvault"] = {entry.entry_id: coordinator}

        async_add_entities = MagicMock()

        await async_setup_entry(hass, entry, async_add_entities)

        async_add_entities.assert_called_once()
        args = async_add_entities.call_args[0][0]
        assert len(args) > 0

    def test_jackery_binary_sensor_creation(self) -> None:
        """Test JackeryBinarySensor creation."""
        entry = self._bare_entry()
        coordinator = self._bare_coordinator(entry)
        from custom_components.jackery_solarvault.binary_sensor import (  # noqa: PLC0415, RUF105
            JackeryBinaryDescription,
        )

        description = JackeryBinaryDescription(
            key="test_binary",
            name="Test Binary",
            getter=lambda props, meta: True,
        )
        sensor = JackeryBinarySensor(
            coordinator=coordinator,
            device_id="test-device",
            description=description,
        )
        assert sensor is not None

    def test_jackery_smart_plug_state_binary_sensor_creation(self) -> None:
        """Test JackerySmartPlugStateBinarySensor creation."""
        entry = self._bare_entry()
        coordinator = self._bare_coordinator(entry)
        sensor = JackerySmartPlugStateBinarySensor(
            coordinator=coordinator,
            device_id="test-device",
            plug_index=1,
            plug_sn="plug-1",
            plug_key="plug_key_1",
        )
        assert sensor is not None

    def test_jackery_subdevice_alarm_binary_sensor_creation(self) -> None:
        """Test JackerySubdeviceAlarmBinarySensor creation."""
        entry = self._bare_entry()
        coordinator = self._bare_coordinator(entry)
        from custom_components.jackery_solarvault.binary_sensor import (  # noqa: PLC0415, RUF105
            JackerySubdeviceAlarmBinarySensorDescription,
        )

        description = JackerySubdeviceAlarmBinarySensorDescription(
            key="test_alarm",
            translation_key="test_alarm",
            field="test_field",
        )
        sensor = JackerySubdeviceAlarmBinarySensor(
            coordinator=coordinator,
            device_id="test-device",
            sub_device_index=1,
            sub_device_sn="sub-device-1",
            sub_device_key="sub_key_1",
            description=description,
        )
        assert sensor is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
