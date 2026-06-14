"""Regression tests for the sensor domain module split."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SENSOR_PLATFORM = ROOT / "custom_components/jackery_solarvault/sensor.py"
SENSOR_PACKAGE = ROOT / "custom_components/jackery_solarvault/sensors"
BASE_MODULE = SENSOR_PACKAGE / "base.py"


def test_sensor_platform_remains_thin_async_setup_entry_aggregator() -> None:
    """The HA sensor platform must delegate setup to the split sensor package."""
    source = SENSOR_PLATFORM.read_text()

    assert "from .sensors.base import async_setup_entry" in source
    assert "async def async_setup_entry" not in source
    assert "SENSOR_DESCRIPTIONS: tuple" not in source


def test_sensor_domain_modules_expose_description_groups() -> None:
    """Each sensor domain module must own a named, reviewable description group."""
    expected_exports = {
        "live.py": [
            "SENSOR_DESCRIPTIONS",
            "SMART_MODE_SENSOR_DESCRIPTIONS",
            "TOU_PLAN_SENSOR_DESCRIPTIONS",
        ],
        "statistics.py": [
            "STAT_DESCRIPTIONS",
            "SAVINGS_DETAIL_SENSOR_DESCRIPTIONS",
            "SMART_PLUG_STATISTIC_FIELDS",
        ],
        "portable.py": [
            "PORTABLE_SENSOR_DESCRIPTIONS",
            "BATTERY_PACK_SENSOR_DESCRIPTIONS",
        ],
        "accessories.py": [
            "SMART_PLUG_SENSOR_DESCRIPTIONS",
            "METER_HEAD_SENSOR_DESCRIPTIONS",
            "SMART_METER_SENSOR_DESCRIPTIONS",
        ],
    }

    for module_name, exported_names in expected_exports.items():
        source = (SENSOR_PACKAGE / module_name).read_text()
        for exported_name in exported_names:
            assert exported_name in source


def test_shared_sensor_base_keeps_entity_classes_and_helpers() -> None:
    """Shared helpers and entity classes stay in the base module after splitting."""
    source = BASE_MODULE.read_text()

    assert "class JackerySensorDescription" in source
    assert "class JackerySensor(JackeryEntity, SensorEntity):" in source
    assert "class JackeryStatSensor(JackeryEntity, SensorEntity):" in source
    assert "def _prop(" in source
    assert "def _period_from_stat_description(" in source


def test_sensor_unique_id_contract_stays_suffix_based() -> None:
    """Entity IDs must remain stable by using stable keys, not localized names."""
    source = BASE_MODULE.read_text()

    assert "super().__init__(coordinator, device_id, description.key)" in source
    assert "battery_pack_{pack_index}_{description.key}" in source
    assert 'f"{plug_key}_{description.key}"' in source
    assert 'f"{meter_head_key}_{description.key}"' in source
    assert 'f"smart_meter_{description.key}"' in source


def test_sensor_entity_metadata_contract_is_preserved() -> None:
    """Sensor entities keep HA metadata and unavailable-state behavior centralized."""
    source = BASE_MODULE.read_text()

    entity_source = (
        ROOT / "custom_components/jackery_solarvault/entity.py"
    ).read_text()

    assert "_attr_has_entity_name = True" in entity_source
    assert "@property\n    def device_info(self) -> DeviceInfo:" in source
    assert "native_unit_of_measurement=" in source
    assert "device_class=" in source
    assert "state_class=" in source
    assert "return None" in source
