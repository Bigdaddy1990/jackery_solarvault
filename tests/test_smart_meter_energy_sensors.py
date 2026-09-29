"""Regression: the smart meter exposes cumulative grid import/export energy.

Third-party CT meters (Shelly Pro 3EM cloud2cloud) report their kWh counters
as the ``tPhaseEgy`` (import) / ``tnPhaseEgy`` (export) phase-energy totals in
the MQTT payload; the Jackery ``device/stat/meter`` panel totals stay empty for
non-native meters. Without dedicated energy sensors the smart meter had no kWh
values at all.
"""

from typing import Any, cast

from custom_components.jackery_solarvault.const import (
    FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_TOTAL_PHASE_ENERGY,
    REMOVED_SENSOR_SUFFIXES,
)
from custom_components.jackery_solarvault.sensor import SMART_METER_SENSOR_DESCRIPTIONS
from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import UnitOfEnergy


def _by_key(key: str) -> Any:
    return cast("Any", next(d for d in SMART_METER_SENSOR_DESCRIPTIONS if d.key == key))


def test_lifetime_import_energy_reads_total_phase_energy() -> None:
    """Grid import energy reads the cumulative ``tPhaseEgy`` counter."""
    desc = _by_key("lifetime_import_energy")

    assert desc.field == FIELD_CT_TOTAL_PHASE_ENERGY
    assert desc.device_class == SensorDeviceClass.ENERGY
    assert desc.state_class == SensorStateClass.TOTAL_INCREASING
    assert desc.native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR


def test_lifetime_export_energy_reads_negative_total_phase_energy() -> None:
    """Grid export energy reads the cumulative ``tnPhaseEgy`` counter."""
    desc = _by_key("lifetime_export_energy")

    assert desc.field == FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY
    assert desc.device_class == SensorDeviceClass.ENERGY
    assert desc.state_class == SensorStateClass.TOTAL_INCREASING
    assert desc.native_unit_of_measurement == UnitOfEnergy.KILO_WATT_HOUR


def test_each_lifetime_counter_backs_exactly_one_sensor() -> None:
    """The former grid_* twins read the same counters and are retired."""
    fields = [d.field for d in SMART_METER_SENSOR_DESCRIPTIONS]

    assert fields.count(FIELD_CT_TOTAL_PHASE_ENERGY) == 1
    assert fields.count(FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY) == 1
    assert {
        "_smart_meter_grid_import_energy",
        "_smart_meter_grid_export_energy",
    } <= REMOVED_SENSOR_SUFFIXES


def test_energy_sensors_have_no_derived_calculation() -> None:
    """The energy sensors are raw counters, so they are always created."""
    assert _by_key("lifetime_import_energy").calculation is None
    assert _by_key("lifetime_export_energy").calculation is None
