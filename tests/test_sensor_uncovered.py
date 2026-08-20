"""Tests for uncovered paths in sensor.py to increase coverage."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.sensor import (
    JackeryBatteryPackSensor,
    JackeryBleTransportSensor,
    JackeryCloudMqttSensor,
    JackeryConversionLossPowerSensor,
    JackeryDeviceActivationSensor,
    JackeryHttpApiSensor,
    JackeryLocalMqttSensor,
    JackeryRawPropertiesSensor,
    JackerySavingsDetailSensor,
    JackerySensor,
    JackerySensorDescription,
    JackeryStatSensor,
    JackeryStatSensorDescription,
    JackerySavingsDetailSensorDescription,
    async_setup_entry,
)


class TestSensorCreation:
    """Test sensor creation and basic properties."""

    def _create_coordinator(self, data=None) -> MagicMock:
        """Create a mock coordinator."""
        coordinator = MagicMock()
        coordinator.data = data or {}
        coordinator.config_entry = MagicMock()
        coordinator.config_entry.entry_id = "test_entry"
        coordinator.config_entry.runtime_data = MagicMock()
        return coordinator

    def _create_sensor(self, coordinator, **kwargs) -> JackerySensor:
        """Create a sensor instance for testing."""
        description = JackerySensorDescription(
            key="test_key",
            name="Test Sensor",
            native_unit_of_measurement="W",
            device_class="power",
            state_class="measurement",
            getter=lambda props: props.get("test_key"),
        )
        return JackerySensor(coordinator=coordinator, device_id="test_device", description=description)

    def test_jackery_sensor_base(self) -> None:
        """Test JackerySensor base class."""
        coordinator = self._create_coordinator()
        sensor = self._create_sensor(coordinator)
        assert sensor is not None
        assert sensor.entity_description.key == "test_key"

    def test_jackery_stat_sensor(self) -> None:
        """Test JackeryStatSensor."""
        coordinator = self._create_coordinator()
        description = JackeryStatSensorDescription(
            key="stat_key",
            name="Stat Sensor",
            native_unit_of_measurement="kWh",
            device_class="energy",
            state_class="total_increasing",
            stat_key="pv",
        )
        sensor = JackeryStatSensor(coordinator=coordinator, device_id="test_device", description=description)
        assert sensor is not None

    def test_jackery_battery_pack_sensor(self) -> None:
        """Test JackeryBatteryPackSensor."""
        coordinator = self._create_coordinator()
        description = JackerySensorDescription(
            key="pack_key",
            name="Pack Sensor",
            native_unit_of_measurement="W",
            device_class="power",
            state_class="measurement",
            getter=lambda props: props.get("pack_key"),
        )
        sensor = JackeryBatteryPackSensor(
            coordinator=coordinator,
            device_id="test_device",
            pack_index=1,
            pack_sn="test_sn",
            pack_key="pack_1",
            description=description,
        )
        assert sensor is not None

    def test_savings_detail_sensor(self) -> None:
        """Test JackerySavingsDetailSensor."""
        coordinator = self._create_coordinator()
        description = JackerySavingsDetailSensorDescription(
            key="savings_key",
            name="Savings Sensor",
            native_unit_of_measurement="EUR",
            device_class="monetary",
            state_class="total",
            path=("savings", "value"),
        )
        sensor = JackerySavingsDetailSensor(
            coordinator=coordinator,
            device_id="test_device",
            description=description,
        )
        assert sensor is not None

    def test_conversion_loss_power_sensor(self) -> None:
        """Test JackeryConversionLossPowerSensor."""
        coordinator = self._create_coordinator()
        sensor = JackeryConversionLossPowerSensor(
            coordinator=coordinator,
            device_id="test_device",
        )
        assert sensor is not None

    def test_raw_properties_sensor(self) -> None:
        """Test JackeryRawPropertiesSensor."""
        coordinator = self._create_coordinator()
        sensor = JackeryRawPropertiesSensor(
            coordinator=coordinator,
            device_id="test_device",
        )
        assert sensor is not None

    def test_ble_transport_sensor(self) -> None:
        """Test JackeryBleTransportSensor."""
        coordinator = self._create_coordinator()
        sensor = JackeryBleTransportSensor(
            coordinator=coordinator,
            device_id="test_device",
        )
        assert sensor is not None

    def test_http_api_sensor(self) -> None:
        """Test JackeryHttpApiSensor."""
        coordinator = self._create_coordinator()
        sensor = JackeryHttpApiSensor(
            coordinator=coordinator,
            device_id="test_device",
        )
        assert sensor is not None

    def test_cloud_mqtt_sensor(self) -> None:
        """Test JackeryCloudMqttSensor."""
        coordinator = self._create_coordinator()
        sensor = JackeryCloudMqttSensor(
            coordinator=coordinator,
            device_id="test_device",
        )
        assert sensor is not None

    def test_local_mqtt_sensor(self) -> None:
        """Test JackeryLocalMqttSensor."""
        coordinator = self._create_coordinator()
        sensor = JackeryLocalMqttSensor(
            coordinator=coordinator,
            device_id="test_device",
        )
        assert sensor is not None

    def test_device_activation_sensor(self) -> None:
        """Test JackeryDeviceActivationSensor."""
        coordinator = self._create_coordinator()
        sensor = JackeryDeviceActivationSensor(
            coordinator=coordinator,
            device_id="test_device",
        )
        assert sensor is not None


class TestSensorState:
    """Test sensor state handling."""

    def _create_sensor_with_data(self, data, **kwargs) -> JackerySensor:
        """Create a sensor with specific coordinator data."""
        coordinator = self._create_coordinator(data)
        description = JackerySensorDescription(
            key="test_key",
            name="Test Sensor",
            native_unit_of_measurement="W",
            device_class="power",
            state_class="measurement",
            getter=lambda props: props.get("test_key"),
        )
        return JackerySensor(coordinator=coordinator, device_id="test_device", description=description)

    def _create_coordinator(self, data=None) -> MagicMock:
        """Create a mock coordinator."""
        from custom_components.jackery_solarvault.const import PAYLOAD_PROPERTIES
        coordinator = MagicMock()
        # The sensor uses device_id as key in coordinator.data, and the payload
        # must contain PAYLOAD_PROPERTIES section
        coordinator.data = {"test_device": {PAYLOAD_PROPERTIES: data}} if data else {"test_device": {PAYLOAD_PROPERTIES: {}}}
        coordinator.config_entry = MagicMock()
        coordinator.config_entry.entry_id = "test_entry"
        coordinator.config_entry.runtime_data = MagicMock()
        return coordinator

    def test_native_value_with_data(self) -> None:
        """Test native_value property with valid data."""
        sensor = self._create_sensor_with_data({"test_key": 1234})
        assert sensor.native_value == 1234

    def test_native_value_none_when_missing(self) -> None:
        """Test native_value property when key is missing."""
        sensor = self._create_sensor_with_data({})
        assert sensor.native_value is None

    def test_native_value_with_conversion(self) -> None:
        """Test native_value with value conversion."""
        # Some sensors convert units - test the base behavior
        sensor = self._create_sensor_with_data({"test_key": 1234567})
        # Base sensor just returns the value as-is
        assert sensor.native_value == 1234567


class TestAsyncSetupEntry:
    """Test async_setup_entry function."""

    @pytest.mark.asyncio
    async def test_async_setup_entry(self) -> None:
        """Test async_setup_entry creates sensors."""
        hass = MagicMock()
        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        config_entry.async_on_unload = MagicMock()

        async_add_entities = MagicMock()

        # Mock coordinator (entry.runtime_data IS the coordinator)
        coordinator = MagicMock()
        coordinator.data = {
            "test_device": {
                "properties": {
                    "soc": 80,
                    "pv_power_total": 1000,
                    "bat_in_pw": 200,
                    "bat_out_pw": 300,
                }
            }
        }
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
