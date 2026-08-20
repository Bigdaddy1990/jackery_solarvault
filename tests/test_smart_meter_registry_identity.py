"""Registry migration tests for the CT/smart-meter accessory identity."""

from typing import TYPE_CHECKING, Any, cast

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
from custom_components.jackery_solarvault.util import stable_subdevice_key
from homeassistant.helpers import device_registry as dr, entity_registry as er

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_PARENT_ID = "device-1"
_METER_SN = "SMART-METER-SN-1"
_migrate_smart_meter_identity = init_module._async_migrate_smart_meter_identity


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


def _parent_device(
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
    _parent_device(device_registry, entry)
    legacy_identifier = f"{_PARENT_ID}_smart_meter"
    legacy_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, legacy_identifier)},
        name="Smart Meter",
        via_device=(DOMAIN, _PARENT_ID),
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
    migrated_device = device_registry.async_get_device(
        identifiers={(DOMAIN, target_identifier)}
    )
    assert migrated_device is not None
    assert migrated_device.id == legacy_device.id
    assert migrated_device.serial_number == _METER_SN
    assert (
        device_registry.async_get_device(identifiers={(DOMAIN, legacy_identifier)})
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
    _parent_device(device_registry, entry)
    positional_identifier = f"{_PARENT_ID}_smart_meter_1"
    positional_device = device_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, positional_identifier)},
        name="Smart Meter",
        via_device=(DOMAIN, _PARENT_ID),
    )

    _migrate_smart_meter_identity(hass, entry)

    migrated_device = device_registry.async_get_device(
        identifiers={(DOMAIN, _meter_identifier(identity))}
    )
    assert migrated_device is not None
    assert migrated_device.id == positional_device.id
    assert migrated_device.serial_number == identity
