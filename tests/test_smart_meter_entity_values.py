"""Regression tests for Smart-Meter entity value passthrough."""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from custom_components.jackery_solarvault.const import (
    FIELD_CT_TOTAL_PHASE_ENERGY,
    PAYLOAD_CT_METER,
)
from custom_components.jackery_solarvault.sensor import (
    SMART_METER_SENSOR_DESCRIPTIONS,
    JackerySmartMeterSensor,
)

_DEVICE_ID = "dev-1"
_HIGH_WATT_HOURS = 10_000
_LOWER_WATT_HOURS = 9_000
_HIGH_KWH = 10.0
_LOWER_KWH = 9.0


def _lifetime_import_sensor() -> JackerySmartMeterSensor:
    sensor = JackerySmartMeterSensor.__new__(JackerySmartMeterSensor)
    mutable = cast("Any", sensor)
    mutable.coordinator = SimpleNamespace(data={})
    mutable._device_id = _DEVICE_ID  # ruff:ignore[private-member-access]
    mutable.entity_description = next(
        desc
        for desc in SMART_METER_SENSOR_DESCRIPTIONS
        if desc.key == "lifetime_import_energy"
    )
    mutable._cached_native_value = None  # ruff:ignore[private-member-access]
    mutable._cached_attrs = {}  # ruff:ignore[private-member-access]
    return sensor


def _set_ct_total(sensor: JackerySmartMeterSensor, watt_hours: int) -> None:
    cast("Any", sensor).coordinator.data = {
        _DEVICE_ID: {
            PAYLOAD_CT_METER: {
                FIELD_CT_TOTAL_PHASE_ENERGY: watt_hours,
            },
        },
    }


def test_smart_meter_total_increasing_reports_current_coordinator_value() -> None:
    """Entity cache must not clamp lower raw CT totals at the entity layer."""
    sensor = _lifetime_import_sensor()

    _set_ct_total(sensor, _HIGH_WATT_HOURS)
    sensor._refresh_cache()  # ruff:ignore[private-member-access]

    assert sensor.native_value == pytest.approx(_HIGH_KWH)

    _set_ct_total(sensor, _LOWER_WATT_HOURS)
    sensor._refresh_cache()  # ruff:ignore[private-member-access]

    assert sensor.native_value == pytest.approx(_LOWER_KWH)
