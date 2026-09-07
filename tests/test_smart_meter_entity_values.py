"""Regression tests for Smart-Meter entity value passthrough."""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from custom_components.jackery_solarvault.const import (
    FIELD_CT_TOTAL_PHASE_ENERGY,
    FIELD_DEVICE_SN,
    PAYLOAD_CT_METER,
)
from custom_components.jackery_solarvault.sensor import (
    SMART_METER_SENSOR_DESCRIPTIONS,
    JackerySmartMeterSensor,
)

_DEVICE_ID = "dev-1"
_HIGH_WATT_HOURS = 108_550
_LOWER_WATT_HOURS = 99_380
_HIGH_KWH = 108.55


def _lifetime_import_sensor() -> JackerySmartMeterSensor:
    return _sensor_by_key("lifetime_import_energy")


def _set_ct_total(sensor: JackerySmartMeterSensor, watt_hours: int) -> None:
    cast("Any", sensor).coordinator.data = {
        _DEVICE_ID: {
            PAYLOAD_CT_METER: {
                FIELD_CT_TOTAL_PHASE_ENERGY: watt_hours,
            },
        },
    }


def test_smart_meter_total_increasing_holds_non_reset_counter_regression() -> None:
    """Entity cache must not publish a non-reset lower CT lifetime total."""
    sensor = _lifetime_import_sensor()

    _set_ct_total(sensor, _HIGH_WATT_HOURS)
    sensor._refresh_cache()  # ruff: ignore[private-member-access]

    assert sensor.native_value == pytest.approx(_HIGH_KWH)

    _set_ct_total(sensor, _LOWER_WATT_HOURS)
    sensor._refresh_cache()  # ruff: ignore[private-member-access]

    assert sensor.native_value == pytest.approx(_HIGH_KWH)


def _sensor_by_key(key: str) -> JackerySmartMeterSensor:
    description = next(
        desc for desc in SMART_METER_SENSOR_DESCRIPTIONS if desc.key == key
    )
    return JackerySmartMeterSensor(
        cast("Any", SimpleNamespace(data={})), _DEVICE_ID, description
    )


def test_import_energy_falls_back_to_per_phase_sum_when_total_absent() -> None:
    """A meter reporting only per-phase import energy still yields a total kWh."""
    sensor = _sensor_by_key("lifetime_import_energy")
    cast("Any", sensor).coordinator.data = {
        _DEVICE_ID: {
            PAYLOAD_CT_METER: {
                "aPhaseEgy": 1_000,
                "bPhaseEgy": 2_000,
                "cPhaseEgy": 3_000,
            },
        },
    }

    sensor._refresh_cache()  # ruff: ignore[private-member-access]

    assert sensor.native_value == pytest.approx(6.0)


def test_import_energy_prefers_reported_total_over_phase_sum() -> None:
    """When ``tPhaseEgy`` is present it wins over the per-phase sum."""
    sensor = _sensor_by_key("lifetime_import_energy")
    cast("Any", sensor).coordinator.data = {
        _DEVICE_ID: {
            PAYLOAD_CT_METER: {
                FIELD_CT_TOTAL_PHASE_ENERGY: 62_598,
                "aPhaseEgy": 1_000,
            },
        },
    }

    sensor._refresh_cache()  # ruff: ignore[private-member-access]

    # 62_598 Wh -> 62.598 kWh, rounded to two decimals at the entity layer.
    assert sensor.native_value == pytest.approx(62.6)


def test_export_energy_falls_back_to_per_phase_negative_sum() -> None:
    """A meter reporting only per-phase export energy still yields a total kWh."""
    sensor = _sensor_by_key("lifetime_export_energy")
    cast("Any", sensor).coordinator.data = {
        _DEVICE_ID: {
            PAYLOAD_CT_METER: {
                "anPhaseEgy": 500,
                "bnPhaseEgy": 500,
                "cnPhaseEgy": 1_000,
            },
        },
    }

    sensor._refresh_cache()  # ruff: ignore[private-member-access]

    assert sensor.native_value == pytest.approx(2.0)


@pytest.mark.parametrize(
    ["key", "fields", "expected"],
    [
        [
            "grid_import_energy",
            {"aPhaseEgy": 10_000, "bPhaseEgy": 20_000, "cPhaseEgy": 30_000},
            60.0,
        ],
        [
            "grid_export_energy",
            {"anPhaseEgy": 1_000, "bnPhaseEgy": 2_000, "cnPhaseEgy": 3_000},
            6.0,
        ],
    ],
)
def test_dashboard_grid_energy_falls_back_to_three_phase_sum(
    key: str,
    fields: dict[str, int],
    expected: float,
) -> None:
    """Primary grid counters support CT meters that omit their total field."""
    sensor = _sensor_by_key(key)
    cast("Any", sensor).coordinator.data = {
        _DEVICE_ID: {PAYLOAD_CT_METER: fields},
    }

    sensor._refresh_cache()  # ruff: ignore[private-member-access]

    assert sensor.native_value == pytest.approx(expected)


def test_mac_address_falls_back_to_device_sn_when_mac_absent() -> None:
    """A CT meter without ``mac`` resolves its id from ``deviceSn``."""
    sensor = _sensor_by_key("mac_address")
    cast("Any", sensor).coordinator.data = {
        _DEVICE_ID: {
            PAYLOAD_CT_METER: {FIELD_DEVICE_SN: "5c013b048e3c"},
        },
    }

    sensor._refresh_cache()  # ruff: ignore[private-member-access]

    assert sensor.native_value == "5c013b048e3c"


def test_total_power_exposes_signed_ct_phase_t_attribute() -> None:
    """The App's T channel is the signed total CT power field."""
    sensor = _sensor_by_key("power")
    cast("Any", sensor).coordinator.data = {
        _DEVICE_ID: {
            PAYLOAD_CT_METER: {
                "tPhasePw": 100,
                "tnPhasePw": 25,
            },
        },
    }

    sensor._refresh_cache()  # ruff: ignore[private-member-access]

    assert sensor.native_value == pytest.approx(75.0)
    assert sensor.extra_state_attributes["phase_t_signed_power"] == pytest.approx(75.0)


@pytest.mark.parametrize(
    ["key", "fields", "expected"],
    [
        ["reactive_power", {"ap": 500, "power": 300}, 400.0],
        ["phase_1_reactive_power", {"ap1": 13, "power1": 5}, 12.0],
        ["phase_2_reactive_power", {"ap2": 25, "power2": -7}, 24.0],
        ["phase_3_reactive_power", {"ap3": 29, "power3": 21}, 20.0],
    ],
)
def test_reactive_power_derives_from_apparent_and_active_when_rep_absent(
    key: str,
    fields: dict[str, float],
    expected: float,
) -> None:
    """Derive reactive magnitude when the meter omits the rep field."""
    sensor = _sensor_by_key(key)
    cast("Any", sensor).coordinator.data = {_DEVICE_ID: {PAYLOAD_CT_METER: fields}}

    sensor._refresh_cache()  # ruff: ignore[private-member-access]

    assert sensor.native_value == pytest.approx(expected)
    assert sensor.extra_state_attributes["source"] == "derived_apparent_minus_active"


def test_reactive_power_prefers_reported_rep_over_derived_value() -> None:
    """Keep a direct reactive-power reading authoritative."""
    sensor = _sensor_by_key("reactive_power")
    cast("Any", sensor).coordinator.data = {
        _DEVICE_ID: {PAYLOAD_CT_METER: {"rep": 123, "ap": 500, "power": 300}}
    }

    sensor._refresh_cache()  # ruff: ignore[private-member-access]

    assert sensor.native_value == pytest.approx(123.0)
    assert sensor.extra_state_attributes["source"] == "raw_field"
