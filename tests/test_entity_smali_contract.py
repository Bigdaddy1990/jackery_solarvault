"""Contract tests for active entity descriptions and Smali provenance."""

from custom_components.jackery_solarvault.entity_contract import contract_field
from custom_components.jackery_solarvault.number import NUMBER_DESCRIPTIONS
from custom_components.jackery_solarvault.select import SELECT_DESCRIPTIONS
from custom_components.jackery_solarvault.sensor import (
    BATTERY_PACK_SENSOR_DESCRIPTIONS,
    METER_HEAD_SENSOR_DESCRIPTIONS,
    PORTABLE_SENSOR_DESCRIPTIONS,
    SAVINGS_DETAIL_SENSOR_DESCRIPTIONS,
    SENSOR_DESCRIPTIONS,
    SMART_METER_SENSOR_DESCRIPTIONS,
    SMART_MODE_SENSOR_DESCRIPTIONS,
    SMART_PLUG_SENSOR_DESCRIPTIONS,
    STAT_DESCRIPTIONS,
    TOU_PLAN_SENSOR_DESCRIPTIONS,
)
from custom_components.jackery_solarvault.switch import SWITCH_DESCRIPTIONS


def _all_active_descriptions() -> tuple[object, ...]:
    return (
        *SENSOR_DESCRIPTIONS,
        *SMART_MODE_SENSOR_DESCRIPTIONS,
        *TOU_PLAN_SENSOR_DESCRIPTIONS,
        *PORTABLE_SENSOR_DESCRIPTIONS,
        *STAT_DESCRIPTIONS,
        *SAVINGS_DETAIL_SENSOR_DESCRIPTIONS,
        *BATTERY_PACK_SENSOR_DESCRIPTIONS,
        *SMART_PLUG_SENSOR_DESCRIPTIONS,
        *METER_HEAD_SENSOR_DESCRIPTIONS,
        *SMART_METER_SENSOR_DESCRIPTIONS,
        *SWITCH_DESCRIPTIONS,
        *NUMBER_DESCRIPTIONS,
        *SELECT_DESCRIPTIONS,
    )


def test_active_entity_descriptions_have_smali_contract_metadata() -> None:
    """Every active description must map to Smali or HA-derived data."""
    for desc in _all_active_descriptions():
        field = contract_field(desc)  # type: ignore[arg-type]
        assert field
        assert desc.data_sources
        assert set(desc.data_sources) <= {"REST", "MQTT", "BLE"}
        assert desc.null_semantics
        assert isinstance(desc.recorder_allowed, bool)


def test_no_active_entity_exists_for_legacy_agent_documentation_only() -> None:
    """Agent-only documentation must not create active entities."""
    for desc in _all_active_descriptions():
        searchable = " ".join(
            str(value)
            for value in (
                getattr(desc, "key", ""),
                getattr(desc, "translation_key", ""),
                getattr(desc, "smali_field", ""),
            )
        ).lower()
        assert "agent" not in searchable
