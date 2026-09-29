"""Cloud-bridged meters get no AccCTBody-only measurement sensors.

Live 2026-09-27: a Shelly Pro 3EM (isCloud) showed 19 permanently unknown
sensors (voltage, current, power factor, frequency, apparent/reactive power);
those fields exist only on Jackery's own CT clamp (App model AccCTBody).
"""

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault import sensor as sensor_module
from custom_components.jackery_solarvault.const import DOMAIN, PAYLOAD_CT_METER
from homeassistant.helpers import entity_registry as er

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_DEV = "dev"


def _collect(hass: HomeAssistant, ct: dict[str, Any]) -> set[str]:
    collection = sensor_module._SensorCollection(  # ruff: ignore[private-member-access]
        SimpleNamespace(hass=hass, has_smart_meter_accessory=lambda _payload: True),
        set(),
        {},
        True,
        False,
        False,
        [],
    )
    sensor_module._collect_smart_meter_entities(  # ruff: ignore[private-member-access]
        collection, _DEV, {PAYLOAD_CT_METER: ct}
    )
    return {str(entity.unique_id) for entity in collection.entities}


@pytest.fixture(autouse=True)
def _light_sensor(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        sensor_module,
        "JackerySmartMeterSensor",
        lambda _coordinator, dev_id, description: SimpleNamespace(
            unique_id=f"{dev_id}_smart_meter_{description.key}"
        ),
    )


def test_cloud_meter_skips_and_removes_clamp_only_sensors(hass: HomeAssistant) -> None:
    """A registered never-delivered sensor is removed; power sensors stay."""
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    stale = registry.async_get_or_create(
        "sensor", DOMAIN, f"{_DEV}_smart_meter_voltage", config_entry=entry
    )

    created = _collect(hass, {"isCloud": True, "scanName": "shellypro3em"})

    assert f"{_DEV}_smart_meter_phase_1_power" in created
    assert f"{_DEV}_smart_meter_voltage" not in created
    assert f"{_DEV}_smart_meter_phase_3_reactive_power" not in created
    assert registry.async_get(stale.entity_id) is None


def test_jackery_clamp_keeps_measurement_sensors(hass: HomeAssistant) -> None:
    """Jackery's own CT clamp (no isCloud) still gets voltage and current."""
    created = _collect(hass, {"scanName": "jackery_ct"})

    assert f"{_DEV}_smart_meter_voltage" in created
    assert f"{_DEV}_smart_meter_phase_1_current" in created
