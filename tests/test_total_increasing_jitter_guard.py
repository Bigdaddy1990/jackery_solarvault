"""Regression tests for tiny counter regressions rejected by HA Recorder."""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from custom_components.jackery_solarvault.const import (
    FIELD_CT_TOTAL_PHASE_ENERGY,
    FIELD_DEVICE_SN,
    FIELD_IN_EGY,
    PAYLOAD_BATTERY_PACKS,
    PAYLOAD_CT_METER,
)
from custom_components.jackery_solarvault.sensor import (
    BATTERY_PACK_SENSOR_DESCRIPTIONS,
    SMART_METER_SENSOR_DESCRIPTIONS,
    JackeryBatteryPackSensor,
    JackerySmartMeterSensor,
)

_DEVICE_ID = "dev-1"


def _smart_meter_sensor(key: str) -> JackerySmartMeterSensor:
    """Build a bare Smart Meter entity for one real description."""
    sensor = JackerySmartMeterSensor.__new__(JackerySmartMeterSensor)
    mutable = cast("Any", sensor)
    mutable.coordinator = SimpleNamespace(data={})
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable.entity_description = next(
        description
        for description in SMART_METER_SENSOR_DESCRIPTIONS
        if description.key == key
    )
    mutable._cached_native_value = None  # ruff: ignore[private-member-access]
    mutable._cached_attrs = {}  # ruff: ignore[private-member-access]
    mutable._restored_lifetime_value = None  # ruff: ignore[private-member-access]
    # Mirrors __init__: _sync_device_mac_connection reads this on every refresh.
    mutable._registered_identity = None  # ruff: ignore[private-member-access]
    return sensor  # pyrefly: ignore [no-any-return-implicit]


def _set_ct_counter(sensor: JackerySmartMeterSensor, value_wh: int) -> None:
    """Publish one CT lifetime counter into the entity's coordinator payload."""
    cast("Any", sensor).coordinator.data = {
        _DEVICE_ID: {
            PAYLOAD_CT_METER: {
                FIELD_CT_TOTAL_PHASE_ENERGY: value_wh,
            },
        },
    }


@pytest.mark.parametrize(
    ["key", "first_wh", "second_wh", "expected"],
    [
        ["grid_import_energy", 77_915, 77_913, 77.92],
        ["lifetime_import_energy", 77_915, 77_913, 77.92],
    ],
)
def test_smart_meter_small_counter_regression_keeps_last_state(
    key: str,
    first_wh: int,
    second_wh: int,
    expected: float,
) -> None:
    """A 2 Wh source wobble must not create a decreasing Recorder state."""
    sensor = _smart_meter_sensor(key)
    _set_ct_counter(sensor, first_wh)
    sensor._refresh_cache()  # ruff: ignore[private-member-access]

    _set_ct_counter(sensor, second_wh)
    sensor._refresh_cache()  # ruff: ignore[private-member-access]

    assert sensor.native_value == pytest.approx(expected)


def test_smart_meter_non_reset_counter_regression_keeps_last_state() -> None:
    """A nonzero 10% dip is held instead of being sent to HA as a counter reset."""
    sensor = _smart_meter_sensor("lifetime_import_energy")
    _set_ct_counter(sensor, 10_000)
    sensor._refresh_cache()  # ruff: ignore[private-member-access]

    _set_ct_counter(sensor, 9_000)
    sensor._refresh_cache()  # ruff: ignore[private-member-access]

    assert sensor.native_value == pytest.approx(10.0)


def test_smart_meter_deep_or_zero_regression_keeps_lifetime_total() -> None:
    """A lifetime counter must not reset statistics on partial or zero frames."""
    sensor = _smart_meter_sensor("lifetime_import_energy")
    _set_ct_counter(sensor, 10_000)
    sensor._refresh_cache()  # ruff: ignore[private-member-access]

    _set_ct_counter(sensor, 8_990)
    sensor._refresh_cache()  # ruff: ignore[private-member-access]
    assert sensor.native_value == pytest.approx(10.0)

    _set_ct_counter(sensor, 0)
    sensor._refresh_cache()  # ruff: ignore[private-member-access]
    assert sensor.native_value == pytest.approx(10.0)


def test_missing_smart_meter_sample_keeps_jitter_anchor() -> None:
    """A missing CT frame cannot erase the previous total-increasing anchor."""
    sensor = _smart_meter_sensor("lifetime_import_energy")
    _set_ct_counter(sensor, 77_915)
    sensor._refresh_cache()  # ruff: ignore[private-member-access]

    cast("Any", sensor).coordinator.data = {
        _DEVICE_ID: {PAYLOAD_CT_METER: []},
    }
    sensor._refresh_cache()  # ruff: ignore[private-member-access]
    assert sensor.native_value == pytest.approx(77.92)

    _set_ct_counter(sensor, 77_913)
    sensor._refresh_cache()  # ruff: ignore[private-member-access]
    assert sensor.native_value == pytest.approx(77.92)


def test_battery_pack_small_counter_regression_keeps_last_state() -> None:
    """The BLE pack lifetime counter receives the same bounded jitter guard."""
    sensor = JackeryBatteryPackSensor.__new__(JackeryBatteryPackSensor)
    mutable = cast("Any", sensor)
    mutable.coordinator = SimpleNamespace(data={})
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable._pack_index = 1  # ruff: ignore[private-member-access]
    mutable._pack_sn = "PACK-1"  # ruff: ignore[private-member-access]
    mutable._pack_key = "battery_pack_pack_1"  # ruff: ignore[private-member-access]
    mutable.entity_description = next(
        description
        for description in BATTERY_PACK_SENSOR_DESCRIPTIONS
        if description.key == "lifetime_charge_energy"
    )
    mutable._cached_native_value = None  # ruff: ignore[private-member-access]
    mutable._cached_attrs = {}  # ruff: ignore[private-member-access]
    mutable._restored_lifetime_value = None  # ruff: ignore[private-member-access]

    mutable.coordinator.data = {
        _DEVICE_ID: {
            PAYLOAD_BATTERY_PACKS: [
                {FIELD_DEVICE_SN: "PACK-1", FIELD_IN_EGY: 21_740},
            ],
        },
    }
    sensor._refresh_cache()  # ruff: ignore[private-member-access]
    mutable.coordinator.data[_DEVICE_ID][PAYLOAD_BATTERY_PACKS][0][FIELD_IN_EGY] = (
        21_738
    )
    sensor._refresh_cache()  # ruff: ignore[private-member-access]

    assert sensor.native_value == pytest.approx(217.4)


def test_missing_battery_pack_sample_keeps_jitter_anchor() -> None:
    """A missing pack frame cannot erase its lifetime-energy guard anchor."""
    sensor = JackeryBatteryPackSensor.__new__(JackeryBatteryPackSensor)
    mutable = cast("Any", sensor)
    mutable.coordinator = SimpleNamespace(data={})
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable._pack_index = 1  # ruff: ignore[private-member-access]
    mutable._pack_sn = "PACK-1"  # ruff: ignore[private-member-access]
    mutable._pack_key = "battery_pack_pack_1"  # ruff: ignore[private-member-access]
    mutable.entity_description = next(
        description
        for description in BATTERY_PACK_SENSOR_DESCRIPTIONS
        if description.key == "lifetime_charge_energy"
    )
    mutable._cached_native_value = None  # ruff: ignore[private-member-access]
    mutable._cached_attrs = {}  # ruff: ignore[private-member-access]
    mutable.coordinator.data = {
        _DEVICE_ID: {
            PAYLOAD_BATTERY_PACKS: [
                {FIELD_DEVICE_SN: "PACK-1", FIELD_IN_EGY: 21_740},
            ],
        },
    }
    sensor._refresh_cache()  # ruff: ignore[private-member-access]

    mutable.coordinator.data[_DEVICE_ID][PAYLOAD_BATTERY_PACKS] = []
    sensor._refresh_cache()  # ruff: ignore[private-member-access]
    assert sensor.native_value == pytest.approx(217.4)

    mutable.coordinator.data[_DEVICE_ID][PAYLOAD_BATTERY_PACKS] = [
        {FIELD_DEVICE_SN: "PACK-1", FIELD_IN_EGY: 21_738},
    ]
    sensor._refresh_cache()  # ruff: ignore[private-member-access]
    assert sensor.native_value == pytest.approx(217.4)
