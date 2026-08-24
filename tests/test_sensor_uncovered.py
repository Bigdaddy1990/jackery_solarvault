"""Tests for uncovered paths in sensor.py to increase coverage."""

from unittest.mock import MagicMock

import pytest

from custom_components.jackery_solarvault.sensor import (
    SAVINGS_DETAIL_SENSOR_DESCRIPTIONS,
    JackeryBatteryPackSensor,
    JackeryBleTransportSensor,
    JackeryCloudMqttSensor,
    JackeryConversionLossPowerSensor,
    JackeryDeviceActivationSensor,
    JackeryHttpApiSensor,
    JackeryLocalMqttSensor,
    JackeryRawPropertiesSensor,
    JackerySavingsDetailSensor,
    JackerySavingsDetailSensorDescription,
    JackerySensor,
    JackerySensorDescription,
    JackeryStatSensor,
    JackeryStatSensorDescription,
    _StatCacheSnapshot,
    _StatRefreshBatch,
    _StatRefreshRequest,
    _StatRefreshResult,
    async_setup_entry,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity


class TestSensorCreation:
    """Test sensor creation and basic properties."""

    def _create_coordinator(self, data=None) -> MagicMock:  # noqa: PLR6301
        """Create a mock coordinator."""
        coordinator = MagicMock()
        coordinator.data = data or {}
        coordinator.config_entry = MagicMock()
        coordinator.config_entry.entry_id = "test_entry"
        coordinator.config_entry.runtime_data = MagicMock()
        return coordinator

    def _create_sensor(self, coordinator, **kwargs) -> JackerySensor:  # noqa: PLR6301
        """Create a sensor instance for testing."""
        description = JackerySensorDescription(
            key="test_key",
            name="Test Sensor",
            native_unit_of_measurement="W",
            device_class="power",
            state_class="measurement",
            getter=lambda props: props.get("test_key"),
        )
        return JackerySensor(
            coordinator=coordinator, device_id="test_device", description=description
        )  # noqa: E501, RUF100

    def test_jackery_sensor_base(self) -> None:
        """Test JackerySensor base class."""
        coordinator = self._create_coordinator()
        sensor = self._create_sensor(coordinator)
        assert sensor is not None
        assert sensor.entity_description.key == "test_key"

    def test_standard_update_writes_only_prepared_cache(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A standard sensor must not re-enter base cache preparation on write."""
        coordinator = self._create_coordinator({"test_device": {}})
        sensor = self._create_sensor(coordinator)
        sensor._cache_refresh_active = True
        sensor._availability_cache_active = True
        refresh_value = MagicMock()
        refresh_availability = MagicMock()
        write_state = MagicMock()
        monkeypatch.setattr(sensor, "_refresh_cache", refresh_value)
        monkeypatch.setattr(sensor, "_refresh_availability_cache", refresh_availability)
        monkeypatch.setattr(
            CoordinatorEntity,
            "_handle_coordinator_update",
            write_state,
        )

        sensor._handle_coordinator_update()

        refresh_value.assert_called_once_with()
        refresh_availability.assert_called_once_with()
        write_state.assert_called_once_with()

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
        sensor = JackeryStatSensor(
            coordinator=coordinator, device_id="test_device", description=description
        )  # noqa: E501, RUF100
        assert sensor is not None

    def test_stat_update_defers_availability_until_snapshot_application(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Only the completed statistic snapshot may prepare availability."""
        coordinator = self._create_coordinator({"test_device": {}})
        description = JackeryStatSensorDescription(
            key="stat_key",
            name="Stat Sensor",
            native_unit_of_measurement="kWh",
            device_class="energy",
            state_class="total",
            stat_key="pv",
        )
        sensor = JackeryStatSensor(coordinator, "test_device", description)
        sensor._cache_refresh_active = True
        prepare = MagicMock()
        batch = MagicMock()
        monkeypatch.setattr(sensor, "_refresh_availability_cache", prepare)
        monkeypatch.setattr(
            "custom_components.jackery_solarvault.sensor._stat_refresh_batch_for",
            MagicMock(return_value=batch),
        )

        sensor._handle_coordinator_update()

        prepare.assert_not_called()
        batch.request.assert_called_once_with(sensor, write_state=True)

    def test_stat_batch_refreshes_availability_from_applied_snapshot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A statistics snapshot must set availability before its state write."""
        coordinator = self._create_coordinator({"test_device": {"statistic": {}}})
        coordinator.is_device_reachable = MagicMock(return_value=True)
        description = JackeryStatSensorDescription(
            key="stat_key",
            name="Stat Sensor",
            native_unit_of_measurement="kWh",
            device_class="energy",
            state_class="total",
            stat_key="pv",
        )
        sensor = JackeryStatSensor(coordinator, "test_device", description)
        sensor._cache_refresh_active = True
        sensor._availability_cache_active = True
        sensor._cache_generation = 1
        sensor._cached_native_value = None
        sensor._cached_available = False
        write = MagicMock()
        monkeypatch.setattr(sensor, "_write_cached_state", write)
        request = _StatRefreshRequest(
            entity=sensor,
            generation=1,
            context=MagicMock(),
            write_state=True,
        )
        result = _StatRefreshResult(
            request=request,
            snapshot=_StatCacheSnapshot(
                native_value=1.0,
                attrs={},
                source_section="statistic",
                last_reset=None,
            ),
        )

        _StatRefreshBatch()._apply_result(result)

        assert sensor.available is True
        write.assert_called_once_with()

    def test_stat_batch_marks_sensor_unavailable_when_snapshot_fails(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A failed snapshot must publish current unavailable state, not stale true."""
        coordinator = self._create_coordinator({})
        coordinator.is_device_reachable = MagicMock(return_value=False)
        description = JackeryStatSensorDescription(
            key="stat_key",
            name="Stat Sensor",
            native_unit_of_measurement="kWh",
            device_class="energy",
            state_class="total",
            stat_key="pv",
        )
        sensor = JackeryStatSensor(coordinator, "test_device", description)
        sensor._cache_refresh_active = True
        sensor._availability_cache_active = True
        sensor._cache_generation = 1
        sensor._cached_native_value = 1.0
        sensor._cached_available = True
        write = MagicMock()
        monkeypatch.setattr(sensor, "_write_cached_state", write)
        result = _StatRefreshResult(
            request=_StatRefreshRequest(
                entity=sensor,
                generation=1,
                context=MagicMock(),
                write_state=True,
            ),
            error=RuntimeError("test statistic failure"),
        )

        _StatRefreshBatch()._apply_result(result)

        assert sensor.available is False
        write.assert_called_once_with()

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

    def test_battery_balance_sensor_exposes_signed_value_without_reset(self) -> None:
        """The legacy entity identity publishes the signed non-loss balance."""
        coordinator = self._create_coordinator({
            "test_device": {
                "statistic": {
                    "_savings_calculation": {
                        "source_energy": {
                            "battery_charge_discharge_balance_year_kwh": -7.6
                        }
                    }
                }
            }
        })
        description = next(
            item
            for item in SAVINGS_DETAIL_SENSOR_DESCRIPTIONS
            if item.key == "savings_battery_loss_year_energy"
        )
        sensor = JackerySavingsDetailSensor(
            coordinator=coordinator,
            device_id="test_device",
            description=description,
        )

        assert sensor.native_value == pytest.approx(-7.6)
        assert sensor.last_reset is None
        assert sensor._attr_translation_key == "savings_battery_balance_year_energy"
        assert sensor.extra_state_attributes["source_path"] == (
            "source_energy.battery_charge_discharge_balance_year_kwh"
        )

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
        return JackerySensor(
            coordinator=coordinator, device_id="test_device", description=description
        )  # noqa: E501, RUF100

    def _create_coordinator(self, data=None) -> MagicMock:  # noqa: PLR6301
        """Create a mock coordinator."""
        from custom_components.jackery_solarvault.const import PAYLOAD_PROPERTIES  # noqa: I001

        coordinator = MagicMock()
        # The sensor uses device_id as key in coordinator.data, and the payload
        # must contain PAYLOAD_PROPERTIES section
        coordinator.data = (
            {"test_device": {PAYLOAD_PROPERTIES: data}}
            if data
            else {"test_device": {PAYLOAD_PROPERTIES: {}}}
        )  # noqa: E501, RUF100
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

    def test_standard_sensor_reads_cached_payload_during_state_write(self) -> None:
        """A state write must not re-evaluate the potentially stale payload."""
        sensor = self._create_sensor_with_data({"test_key": 1})
        sensor._cache_refresh_active = True
        sensor._refresh_cache()
        sensor.coordinator.data["test_device"]["properties"]["test_key"] = 2

        assert sensor.native_value == 1
        assert sensor.extra_state_attributes["merged_raw_value"] == 1

        sensor._refresh_cache()
        assert sensor.native_value == 2

    def test_activation_sensor_reads_cached_payload_during_state_write(self) -> None:
        """Activation diagnostics use one snapshot for a synchronous state write."""
        coordinator = self._create_coordinator()
        coordinator.data = {
            "test_device": {
                "device": {
                    "activated": 1,
                    "isCloud": 1,
                    "onlineStatus": 1,
                    "sn": "before",
                }
            }
        }
        sensor = JackeryDeviceActivationSensor(coordinator, "test_device")
        sensor._cache_refresh_active = True
        sensor._refresh_cache()
        coordinator.data["test_device"]["device"] = {
            "activated": 0,
            "isCloud": 0,
            "onlineStatus": 0,
            "sn": "after",
        }

        assert sensor.native_value == 1
        assert sensor.extra_state_attributes == {
            "is_cloud": 1,
            "activated": 1,
            "online_status": 1,
            "device_sn": "before",
        }

        sensor._refresh_cache()
        assert sensor.native_value == 0

    def test_savings_sensor_reads_cached_calculation_during_state_write(self) -> None:
        """Savings diagnostics use the same immutable state-write snapshot."""
        coordinator = self._create_coordinator()
        coordinator.data = {
            "test_device": {
                "statistic": {
                    "_savings_calculation": {
                        "detail": {"value": 1.5},
                        "method": "test_method",
                    }
                }
            }
        }
        description = JackerySavingsDetailSensorDescription(
            key="cached_savings",
            name="Cached savings",
            path=("detail", "value"),
        )
        sensor = JackerySavingsDetailSensor(coordinator, "test_device", description)
        sensor._cache_refresh_active = True
        sensor._refresh_cache()
        coordinator.data["test_device"]["statistic"]["_savings_calculation"]["detail"][
            "value"
        ] = 2.5

        assert sensor.native_value == pytest.approx(1.5)
        assert sensor.extra_state_attributes["method"] == "test_method"

        sensor._refresh_cache()
        assert sensor.native_value == pytest.approx(2.5)

    def test_http_api_sensor_reads_cached_observation_during_state_write(self) -> None:
        """The diagnostic state and attributes share one copied observation."""
        coordinator = self._create_coordinator()
        coordinator.http_api_observations.return_value = {"requests_total": 4}
        sensor = JackeryHttpApiSensor(coordinator, "test_device")
        sensor._cache_refresh_active = True
        sensor._refresh_cache()


class TestAsyncSetupEntry:
    """Test async_setup_entry function."""

    @pytest.mark.asyncio
    async def test_async_setup_entry(self) -> None:  # noqa: PLR6301
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
