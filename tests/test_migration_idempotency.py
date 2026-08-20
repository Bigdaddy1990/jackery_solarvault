"""Tests for migration idempotency — no Registry spam on re-run."""

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.jackery_solarvault as _init_module
from custom_components.jackery_solarvault.const import DOMAIN, PAYLOAD_BATTERY_PACKS
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from custom_components.jackery_solarvault.util import stable_subdevice_key
from homeassistant.helpers import device_registry as dr, entity_registry as er

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_async_migrate_portable_screen_entity = (
    _init_module._async_migrate_portable_screen_entity  # ruff: ignore[private-member-access]
)
_async_migrate_grid_standard_entity = _init_module._async_migrate_grid_standard_entity  # ruff: ignore[private-member-access]
_async_migrate_battery_pack_identities = (
    _init_module._async_migrate_battery_pack_identities  # ruff: ignore[private-member-access]
)

_PORTABLE_SCREEN_UID = "12345_portable_screen"
_GRID_STANDARD_UID = "system-abc_grid_standard"
_PARENT_ID = "device-1"
_SN_A = "HQ2C01400955HP3"


def _config_entry(hass: HomeAssistant, entry_id: str = "entry-1") -> MockConfigEntry:
    entry = MockConfigEntry(domain=DOMAIN, entry_id=entry_id)
    entry.add_to_hass(hass)
    return entry


def _portable_screen_switch(
    registry: er.EntityRegistry, entry: MockConfigEntry
) -> er.RegistryEntry:
    return registry.async_get_or_create(
        "switch",
        DOMAIN,
        _PORTABLE_SCREEN_UID,
        config_entry=entry,
        suggested_object_id="portable_screen",
    )


def _grid_standard_text(
    registry: er.EntityRegistry, entry: MockConfigEntry
) -> er.RegistryEntry:
    return registry.async_get_or_create(
        "text",
        DOMAIN,
        _GRID_STANDARD_UID,
        config_entry=entry,
        suggested_object_id="grid_standard",
    )


def _grid_standard_sensor(
    registry: er.EntityRegistry, entry: MockConfigEntry
) -> er.RegistryEntry:
    return registry.async_get_or_create(
        "sensor",
        DOMAIN,
        _GRID_STANDARD_UID,
        config_entry=entry,
        suggested_object_id="grid_standard",
    )


def _pack_device(
    registry: er.EntityRegistry,
    entry: MockConfigEntry,
    identifier: str,
    serial: str | None = None,
) -> dr.DeviceEntry:
    """Create a device registry entry for a battery pack."""
    dr_registry = dr.async_get(registry.hass)
    return dr_registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, identifier)},
        serial_number=serial,
    )


def _pack_entity(
    registry: er.EntityRegistry, entry: MockConfigEntry, identifier: str, unique_id: str
) -> er.RegistryEntry:
    return registry.async_get_or_create(
        "sensor",
        DOMAIN,
        unique_id,
        config_entry=entry,
        device_id=_pack_device(registry, entry, identifier).id,
    )


def _serial_identifier(serial: str) -> str:
    return f"{_PARENT_ID}_{stable_subdevice_key("battery_pack", serial, 1)}"


def test_portable_screen_migration_is_idempotent_no_registry_writes(
    hass: HomeAssistant,
) -> None:
    """Re-running migration on already-migrated entity does not call registry.update/remove."""  # noqa: RUF105
    entry = _config_entry(hass)
    registry = er.async_get(hass)

    # Pre-create the migrated select entity
    target = registry.async_get_or_create(
        "select",
        DOMAIN,
        _PORTABLE_SCREEN_UID,
        config_entry=entry,
        suggested_object_id="existing_portable_screen",
    )
    target_entity_id = target.entity_id

    # Spy on registry operations
    with (
        patch.object(
            registry, "async_update_entity", wraps=registry.async_update_entity
        ) as mock_update,
        patch.object(
            registry, "async_remove", wraps=registry.async_remove
        ) as mock_remove,
    ):
        # Run migration twice
        _async_migrate_portable_screen_entity(hass, entry)
        _async_migrate_portable_screen_entity(hass, entry)

        # No registry writes should occur on second run
        assert mock_update.call_count == 0, (
            "async_update_entity should not be called on idempotent re-run"
        )
        assert mock_remove.call_count == 0, (
            "async_remove should not be called on idempotent re-run"
        )

        # Entity should remain unchanged
        preserved = registry.async_get(target_entity_id)
        assert preserved is not None
        assert preserved.entity_id == target_entity_id


def test_grid_standard_migration_is_idempotent_no_registry_writes(
    hass: HomeAssistant,
) -> None:
    """Re-running migration on already-migrated entity does not call registry.update/remove."""  # noqa: RUF105
    entry = _config_entry(hass)
    registry = er.async_get(hass)

    # Pre-create the migrated sensor entity
    target = _grid_standard_sensor(registry, entry)
    target_entity_id = target.entity_id

    with (
        patch.object(
            registry, "async_update_entity", wraps=registry.async_update_entity
        ) as mock_update,
        patch.object(
            registry, "async_remove", wraps=registry.async_remove
        ) as mock_remove,
    ):
        _async_migrate_grid_standard_entity(hass, entry)
        _async_migrate_grid_standard_entity(hass, entry)

        assert mock_update.call_count == 0, (
            "async_update_entity should not be called on idempotent re-run"
        )
        assert mock_remove.call_count == 0, (
            "async_remove should not be called on idempotent re-run"
        )

        preserved = registry.async_get(target_entity_id)
        assert preserved is not None
        assert preserved.entity_id == target_entity_id


def test_battery_pack_migration_is_idempotent_no_registry_writes(
    hass: HomeAssistant,
) -> None:
    """Re-running migration on already-migrated pack does not call registry.update/remove."""  # noqa: RUF105
    entry = _config_entry(hass)
    registry = er.async_get(hass)
    device_registry = dr.async_get(hass)

    # Pre-create the migrated pack device + entity with serial-based identity
    new_identifier = _serial_identifier(_SN_A)
    pack = _pack_device(registry, entry, new_identifier, serial=_SN_A)
    entity = _pack_entity(
        registry, entry, new_identifier, f"{new_identifier}_state_of_charge"
    )
    entity_id = entity.entity_id

    # Coordinator with matching live serial
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    shell = cast("Any", coordinator)
    shell.data = {_PARENT_ID: {PAYLOAD_BATTERY_PACKS: [{"deviceSn": _SN_A}]}}
    shell._battery_pack_identity_overrides = {}  # ruff: ignore[private-member-access]
    entry.runtime_data = coordinator

    with (
        patch.object(
            device_registry,
            "async_update_device",
            wraps=device_registry.async_update_device,
        ) as mock_dev_update,
        patch.object(
            device_registry,
            "async_remove_device",
            wraps=device_registry.async_remove_device,
        ) as mock_dev_remove,
        patch.object(
            registry, "async_update_entity", wraps=registry.async_update_entity
        ) as mock_ent_update,
        patch.object(
            registry, "async_remove", wraps=registry.async_remove
        ) as mock_ent_remove,
    ):
        _async_migrate_battery_pack_identities(hass, entry)
        _async_migrate_battery_pack_identities(hass, entry)

        # No device/entity registry writes should occur on second run
        assert mock_dev_update.call_count == 0, (
            "device async_update_device should not be called on idempotent re-run"
        )
        assert mock_dev_remove.call_count == 0, (
            "device async_remove_device should not be called on idempotent re-run"
        )
        assert mock_ent_update.call_count == 0, (
            "entity async_update_entity should not be called on idempotent re-run"
        )
        assert mock_ent_remove.call_count == 0, (
            "entity async_remove should not be called on idempotent re-run"
        )

        # Entity and device should remain unchanged
        preserved_ent = registry.async_get(entity_id)
        assert preserved_ent is not None
        assert preserved_ent.entity_id == entity_id
        assert preserved_ent.unique_id == f"{new_identifier}_state_of_charge"

        preserved_dev = device_registry.async_get_device(
            identifiers={(DOMAIN, new_identifier)}
        )
        assert preserved_dev is not None
        assert preserved_dev.id == pack.id
