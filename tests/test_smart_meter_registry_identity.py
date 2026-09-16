"""Registry migration tests for the CT/smart-meter accessory identity."""

from typing import TYPE_CHECKING, Any, cast

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.jackery_solarvault as init_module
from custom_components.jackery_solarvault.const import (
    DOMAIN,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    PAYLOAD_CT_METER,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from custom_components.jackery_solarvault.descriptions.sensor import (
    SMART_METER_SENSOR_DESCRIPTIONS,
)
from custom_components.jackery_solarvault.sensor import JackerySmartMeterSensor
from custom_components.jackery_solarvault.util import stable_subdevice_key
from homeassistant.helpers import device_registry as dr, entity_registry as er

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_PARENT_ID = "device-1"
_METER_SN = "SMART-METER-SN-1"
_migrate_smart_meter_identity = init_module._async_migrate_smart_meter_identity  # ruff: ignore[private-member-access]


def _coordinator(smart_meter: dict[str, Any]) -> JackerySolarVaultCoordinator:
    """Build a coordinator shell containing one smart-meter payload."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    cast("Any", coordinator).data = {
        _PARENT_ID: {PAYLOAD_CT_METER: dict(smart_meter)},
    }
    return coordinator


def _entry(
    hass: HomeAssistant,
    coordinator: JackerySolarVaultCoordinator,
) -> MockConfigEntry:
    """Create an integration entry whose runtime data is the coordinator shell."""
    entry = MockConfigEntry(domain=DOMAIN, entry_id="entry-1")
    entry.add_to_hass(hass)
    entry.runtime_data = coordinator
    return entry


def parent_device(
    registry: dr.DeviceRegistry,
    entry: MockConfigEntry,
) -> dr.DeviceEntry:
    """Create the parent Jackery device."""
    return registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, _PARENT_ID)},
        name="Solar generator",
    )


def _meter_identifier(identity: str) -> str:
    """Return the accessory-backed smart-meter device identifier."""
    key = stable_subdevice_key("smart_meter", identity, 1)
    return f"{_PARENT_ID}_{key}"


def test_smart_meter_registry_migration_preserves_device_and_entity(
    hass: HomeAssistant,
) -> None:
    """The legacy parent-scoped device is rekeyed without replacing entities."""
    coordinator = _coordinator({FIELD_DEVICE_SN: _METER_SN})
    entry = _entry(hass, coordinator)
    device_registry = dr.async_get(hass)
    entity_registry = er.async_get(hass)
    parent_device(device_registry, entry)
    parent = device_registry.async_get_device_by_identifier(
        (DOMAIN, _PARENT_ID),
        entry.entry_id,
    )
    assert parent is not None
    legacy_identifier = f"{_PARENT_ID}_smart_meter"
    legacy_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, legacy_identifier)},
        name="Smart Meter",
        via_device_id=parent.id,
    )
    entity = entity_registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"{_PARENT_ID}_smart_meter_power",
        config_entry=entry,
        device_id=legacy_device.id,
        suggested_object_id="smart_meter_power",
    )

    _migrate_smart_meter_identity(hass, entry)

    target_identifier = _meter_identifier(_METER_SN)
    migrated_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, target_identifier),
        entry.entry_id,
    )
    assert migrated_device is not None
    assert migrated_device.id == legacy_device.id
    assert migrated_device.serial_number == _METER_SN
    assert (
        device_registry.async_get_device_by_identifier(
            (DOMAIN, legacy_identifier),
            entry.entry_id,
        )
        is None
    )
    migrated_entity = entity_registry.async_get(entity.entity_id)
    assert migrated_entity is not None
    assert migrated_entity.device_id == legacy_device.id
    assert migrated_entity.unique_id == f"{_PARENT_ID}_smart_meter_power"


def test_smart_meter_registry_migration_upgrades_positional_fallback(
    hass: HomeAssistant,
) -> None:
    """A later deviceId upgrades the stable positional fallback in place."""
    identity = "ct-device-42"
    coordinator = _coordinator({FIELD_DEVICE_ID: identity})
    entry = _entry(hass, coordinator)
    device_registry = dr.async_get(hass)
    parent_device(device_registry, entry)
    parent = device_registry.async_get_device_by_identifier(
        (DOMAIN, _PARENT_ID),
        entry.entry_id,
    )
    assert parent is not None
    positional_identifier = f"{_PARENT_ID}_smart_meter_1"
    positional_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, positional_identifier)},
        name="Smart Meter",
        via_device_id=parent.id,
    )

    _migrate_smart_meter_identity(hass, entry)

    migrated_device = device_registry.async_get_device_by_identifier(
        (DOMAIN, _meter_identifier(identity)),
        entry.entry_id,
    )
    assert migrated_device is not None
    assert migrated_device.id == positional_device.id
    assert migrated_device.serial_number == identity


def test_smart_meter_registry_migration_removes_empty_duplicate(
    hass: HomeAssistant,
) -> None:
    """An empty legacy CT device is removed when its serial target exists."""
    coordinator = _coordinator({FIELD_DEVICE_SN: _METER_SN})
    entry = _entry(hass, coordinator)
    registry = dr.async_get(hass)
    parent_device(registry, entry)
    parent = registry.async_get_device_by_identifier(
        (DOMAIN, _PARENT_ID),
        entry.entry_id,
    )
    assert parent is not None
    legacy_identifier = f"{_PARENT_ID}_smart_meter"
    legacy_device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, legacy_identifier)},
        name="Smart Meter",
        via_device_id=parent.id,
    )
    target_device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, _meter_identifier(_METER_SN))},
        name="Smart Meter",
        serial_number=_METER_SN,
        via_device_id=parent.id,
    )

    _migrate_smart_meter_identity(hass, entry)

    assert registry.async_get(legacy_device.id) is None
    assert registry.async_get(target_device.id) is not None


@pytest.mark.parametrize("serial", ["AABBCCDDEEFF", "CT-SERIAL-42"])
def test_late_ct_identity_updates_existing_registry_device(
    hass: HomeAssistant,
    serial: str,
) -> None:
    """Late identity enriches the same device without replacing its connections."""
    coordinator = _coordinator({FIELD_DEVICE_SN: serial})
    entry = _entry(hass, coordinator)
    cast("Any", coordinator).hass = hass
    cast("Any", coordinator).config_entry = entry
    registry = dr.async_get(hass)
    connection = ("test_connection", "existing")
    device = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, _meter_identifier(serial))},
        connections={connection},
    )
    sensor = JackerySmartMeterSensor(
        coordinator, _PARENT_ID, SMART_METER_SENSOR_DESCRIPTIONS[0]
    )
    sensor.hass = hass
    sensor._sync_device_mac_connection({FIELD_DEVICE_SN: serial})  # ruff: ignore[private-member-access]
    updated = registry.async_get(device.id)
    assert updated is not None
    assert updated.serial_number == serial
    expected = {connection}
    if serial == "AABBCCDDEEFF":
        expected.add((dr.CONNECTION_NETWORK_MAC, "aa:bb:cc:dd:ee:ff"))
    assert updated.connections == expected
    sensor._sync_device_mac_connection({FIELD_DEVICE_SN: serial})  # ruff: ignore[private-member-access]
    assert registry.async_get(device.id) is updated
