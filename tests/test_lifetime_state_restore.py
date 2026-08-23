"""Regression coverage for Layer-5 lifetime counters across HA restarts."""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from custom_components.jackery_solarvault.const import (
    FIELD_CT_TOTAL_PHASE_ENERGY,
    FIELD_DEVICE_SN,
    FIELD_IN_EGY,
    FIELD_TOTAL_ENERGY,
    PAYLOAD_BATTERY_PACKS,
    PAYLOAD_CT_METER,
    PAYLOAD_SMART_PLUGS,
)
from custom_components.jackery_solarvault.sensor import (
    BATTERY_PACK_SENSOR_DESCRIPTIONS,
    SMART_METER_SENSOR_DESCRIPTIONS,
    SMART_PLUG_SENSOR_DESCRIPTIONS,
    STAT_DESCRIPTIONS,
    JackeryBatteryPackSensor,
    JackerySmartMeterSensor,
    JackerySmartPlugSensor,
    JackeryStatSensor,
    _StatCacheSnapshot,
    _async_restored_lifetime_energy_value,
)
from homeassistant.components.sensor import SensorExtraStoredData
from homeassistant.const import UnitOfEnergy

_DEVICE_ID = "dev-1"


def test_live_main_and_pack_counters_use_hundredths_of_kwh() -> None:
    """Captured Layer-5 counters convert from 0.01 kWh to native kWh."""
    main = next(
        item for item in STAT_DESCRIPTIONS if item.key == "battery_charge_energy"
    )
    pack = next(
        item
        for item in BATTERY_PACK_SENSOR_DESCRIPTIONS
        if item.key == "lifetime_charge_energy"
    )

    assert main.transform(54_762) == pytest.approx(547.62)
    assert pack.transform(26_779) == pytest.approx(267.79)


@pytest.mark.asyncio
async def test_restore_validator_accepts_nonnegative_kwh_lifetime_value() -> None:
    """HA's stored native kWh value remains usable while Layer 5 is absent."""
    entity = SimpleNamespace(
        async_get_last_sensor_data=lambda: _async_value(
            SensorExtraStoredData(54.25, UnitOfEnergy.KILO_WATT_HOUR)
        )
    )

    restored = await _async_restored_lifetime_energy_value(
        cast("Any", entity),
        UnitOfEnergy.KILO_WATT_HOUR,
    )

    assert restored == pytest.approx(54.25)


@pytest.mark.asyncio
async def test_restore_validator_converts_legacy_wh_lifetime_value() -> None:
    """A prior Wh state becomes the equivalent native kWh lifetime anchor."""
    entity = SimpleNamespace(
        async_get_last_sensor_data=lambda: _async_value(
            SensorExtraStoredData(108_550, UnitOfEnergy.WATT_HOUR)
        )
    )

    restored = await _async_restored_lifetime_energy_value(
        cast("Any", entity),
        UnitOfEnergy.KILO_WATT_HOUR,
    )

    assert restored == pytest.approx(108.55)


async def _async_value(value: Any) -> Any:  # ruff: ignore[unused-async]
    """Return one value through the same await boundary as RestoreSensor."""
    return value


def test_system_lifetime_restore_is_replaced_by_real_transport_value() -> None:
    """A restored battery total is temporary and never overrides fresh telemetry."""
    description = next(
        item for item in STAT_DESCRIPTIONS if item.key == "battery_charge_energy"
    )
    sensor = JackeryStatSensor(
        cast("Any", SimpleNamespace()),
        _DEVICE_ID,
        description,
    )
    sensor._restored_lifetime_value = 54.25

    sensor._apply_cache_snapshot(
        _StatCacheSnapshot(None, {"source_section": "properties"}, "properties")
    )
    assert sensor.native_value == pytest.approx(54.25)
    assert sensor.extra_state_attributes["restored"] is True

    sensor._apply_cache_snapshot(
        _StatCacheSnapshot(55.0, {"source_section": "properties"}, "properties")
    )
    assert sensor.native_value == pytest.approx(55.0)
    assert "restored" not in sensor.extra_state_attributes


def test_pack_lifetime_restore_is_replaced_by_real_transport_value() -> None:
    """A pack keeps its prior total until a fresh inEgy counter arrives."""
    sensor = JackeryBatteryPackSensor.__new__(JackeryBatteryPackSensor)
    mutable = cast("Any", sensor)
    mutable.coordinator = SimpleNamespace(
        data={
            _DEVICE_ID: {
                PAYLOAD_BATTERY_PACKS: [{FIELD_DEVICE_SN: "PACK-1"}],
            }
        }
    )
    mutable._device_id = _DEVICE_ID
    mutable._pack_index = 1
    mutable._pack_sn = "PACK-1"
    mutable._pack_key = "battery_pack_pack_1"
    mutable.entity_description = next(
        item
        for item in BATTERY_PACK_SENSOR_DESCRIPTIONS
        if item.key == "lifetime_charge_energy"
    )
    mutable._cached_native_value = None
    mutable._cached_attrs = {}
    mutable._restored_lifetime_value = 26.523

    sensor._refresh_cache()
    assert sensor.native_value == pytest.approx(26.523)
    assert mutable._cached_attrs["restored"] is True

    mutable.coordinator.data[_DEVICE_ID][PAYLOAD_BATTERY_PACKS][0][FIELD_IN_EGY] = (
        26_600
    )
    sensor._refresh_cache()
    assert sensor.native_value == pytest.approx(266.0)
    assert "restored" not in mutable._cached_attrs


def test_smart_meter_lifetime_restore_is_replaced_by_real_transport_value() -> None:
    """A CT lifetime total survives a gap and yields to the next real frame."""
    sensor = JackerySmartMeterSensor.__new__(JackerySmartMeterSensor)
    mutable = cast("Any", sensor)
    mutable.coordinator = SimpleNamespace(data={_DEVICE_ID: {PAYLOAD_CT_METER: {}}})
    mutable._device_id = _DEVICE_ID
    mutable.entity_description = next(
        item
        for item in SMART_METER_SENSOR_DESCRIPTIONS
        if item.key == "lifetime_import_energy"
    )
    mutable._cached_native_value = None
    mutable._cached_attrs = {}
    mutable._restored_lifetime_value = 77.92

    sensor._refresh_cache()
    assert sensor.native_value == pytest.approx(77.92)
    assert mutable._cached_attrs["restored"] is True

    mutable.coordinator.data[_DEVICE_ID][PAYLOAD_CT_METER] = {
        FIELD_CT_TOTAL_PHASE_ENERGY: 78_000,
    }
    sensor._refresh_cache()
    assert sensor.native_value == pytest.approx(78.0)
    assert "restored" not in mutable._cached_attrs


def test_smart_plug_lifetime_restore_is_replaced_by_real_transport_value() -> None:
    """A plug total survives a gap and yields to the next real PlugSub frame."""
    sensor = JackerySmartPlugSensor.__new__(JackerySmartPlugSensor)
    mutable = cast("Any", sensor)
    mutable.coordinator = SimpleNamespace(data={_DEVICE_ID: {PAYLOAD_SMART_PLUGS: []}})
    mutable._device_id = _DEVICE_ID
    mutable._plug_index = 1
    mutable._plug_sn = "PLUG-1"
    mutable._plug_key = "smart_plug_plug_1"
    mutable.entity_description = next(
        item for item in SMART_PLUG_SENSOR_DESCRIPTIONS if item.key == "total_energy"
    )
    mutable._cached_native_value = None
    mutable._cached_attrs = {}
    mutable._restored_lifetime_value = 12.5

    sensor._refresh_cache()
    assert sensor.native_value == pytest.approx(12.5)

    mutable.coordinator.data[_DEVICE_ID][PAYLOAD_SMART_PLUGS] = [
        {FIELD_DEVICE_SN: "PLUG-1", FIELD_TOTAL_ENERGY: 12.6},
    ]
    sensor._refresh_cache()
    assert sensor.native_value == pytest.approx(12.6)
    assert "restored" not in mutable._cached_attrs
