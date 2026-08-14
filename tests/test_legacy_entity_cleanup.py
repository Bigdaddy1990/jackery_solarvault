"""Behavioral tests for legacy entity unique-ID matching."""

from typing import TYPE_CHECKING

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault import (
    _async_migrate_grid_standard_entity,  # ruff: ignore[import-private-name]
    _async_migrate_portable_screen_entity,  # ruff: ignore[import-private-name]
    _legacy_suffix_matches,  # ruff: ignore[import-private-name]
)
from custom_components.jackery_solarvault.const import DOMAIN
from homeassistant.helpers import area_registry as ar, entity_registry as er
from homeassistant.helpers.entity import EntityCategory

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_PORTABLE_SCREEN_UID = "12345_portable_screen"
_GRID_STANDARD_UID = "system-abc_grid_standard"


def _config_entry(
    hass: HomeAssistant,
    entry_id: str = "entry-1",
) -> MockConfigEntry:
    """Create a registered integration entry for registry migration tests."""
    entry = MockConfigEntry(domain=DOMAIN, entry_id=entry_id)
    entry.add_to_hass(hass)
    return entry


def _portable_screen_switch(
    registry: er.EntityRegistry,
    entry: MockConfigEntry,
) -> er.RegistryEntry:
    """Create the obsolete switch registry entry migrated by setup."""
    return registry.async_get_or_create(
        "switch",
        DOMAIN,
        _PORTABLE_SCREEN_UID,
        config_entry=entry,
        suggested_object_id="portable_screen",
    )


def _grid_standard_text(
    registry: er.EntityRegistry,
    entry: MockConfigEntry,
) -> er.RegistryEntry:
    """Create the obsolete editable grid-standard registry entry."""
    return registry.async_get_or_create(
        "text",
        DOMAIN,
        _GRID_STANDARD_UID,
        config_entry=entry,
        entity_category=EntityCategory.CONFIG,
        has_entity_name=True,
        suggested_object_id="grid_standard",
        translation_key="grid_standard",
    )


def _grid_standard_sensor(
    registry: er.EntityRegistry,
    entry: MockConfigEntry,
    *,
    suggested_object_id: str = "grid_standard",
) -> er.RegistryEntry:
    """Create the read-only grid-standard registry target."""
    return registry.async_get_or_create(
        "sensor",
        DOMAIN,
        _GRID_STANDARD_UID,
        config_entry=entry,
        entity_category=EntityCategory.DIAGNOSTIC,
        has_entity_name=True,
        suggested_object_id=suggested_object_id,
        translation_key="grid_standard",
    )


@pytest.mark.parametrize(
    ("unique_id", "suffix"),  # ruff:ignore[pytest-parametrize-names-wrong-type]
    [
        ("12345_battery_soc", "_battery_soc"),  # ruff:ignore[pytest-parametrize-values-wrong-type]
        ("9_some_key", "_some_key"),  # ruff:ignore[pytest-parametrize-values-wrong-type]
        ("12345_battery_pack_0_current", "_current"),  # ruff:ignore[pytest-parametrize-values-wrong-type]
        ("99_battery_pack_12_temp", "_temp"),  # ruff:ignore[pytest-parametrize-values-wrong-type]
        ("12345", ""),  # ruff:ignore[pytest-parametrize-values-wrong-type]
        ("12345_battery_pack_2", ""),  # ruff:ignore[pytest-parametrize-values-wrong-type]
    ],
)
def test_legacy_suffix_matches_supported_ids(unique_id: str, suffix: str) -> None:
    """Match only supported numeric legacy heads and battery-pack heads."""
    assert _legacy_suffix_matches(unique_id, suffix)


@pytest.mark.parametrize(
    ("unique_id", "suffix"),  # ruff:ignore[pytest-parametrize-names-wrong-type]
    [
        ("my_device_battery_soc", "_battery_soc"),  # ruff:ignore[pytest-parametrize-values-wrong-type]
        ("abc123_voltage", "_voltage"),  # ruff:ignore[pytest-parametrize-values-wrong-type]
        ("12345_battery_pack_abc_voltage", "_voltage"),  # ruff:ignore[pytest-parametrize-values-wrong-type]
        ("12345_battery_soc", "_voltage"),  # ruff:ignore[pytest-parametrize-values-wrong-type]
        ("12345_pv_power_w", "_power_w"),  # ruff:ignore[pytest-parametrize-values-wrong-type]
        ("", "_voltage"),  # ruff:ignore[pytest-parametrize-values-wrong-type]
        ("_voltage", "_voltage"),  # ruff:ignore[pytest-parametrize-values-wrong-type]
    ],
)
def test_legacy_suffix_rejects_current_or_malformed_ids(
    unique_id: str,
    suffix: str,
) -> None:
    """Reject current-schema, malformed, and suffix-only unique IDs."""
    assert not _legacy_suffix_matches(unique_id, suffix)


def test_portable_screen_migration_is_noop_without_legacy_switch(
    hass: HomeAssistant,
) -> None:
    """Setup does not invent a select when no obsolete switch exists."""
    entry = _config_entry(hass)
    registry = er.async_get(hass)

    _async_migrate_portable_screen_entity(hass, entry)

    assert registry.async_get_entity_id("select", DOMAIN, _PORTABLE_SCREEN_UID) is None


def test_portable_screen_migration_preserves_user_registry_metadata(
    hass: HomeAssistant,
) -> None:
    """The replacement select retains user-controlled switch metadata."""
    entry = _config_entry(hass)
    registry = er.async_get(hass)
    area = ar.async_get(hass).async_create("Workshop")
    old_entry = _portable_screen_switch(registry, entry)
    old_entry = registry.async_update_entity(
        old_entry.entity_id,
        area_id=area.id,
        disabled_by=er.RegistryEntryDisabler.USER,
        icon="mdi:television",
        name="Portable display",
    )

    _async_migrate_portable_screen_entity(hass, entry)

    target_id = registry.async_get_entity_id(
        "select",
        DOMAIN,
        _PORTABLE_SCREEN_UID,
    )
    assert target_id is not None
    migrated = registry.async_get(target_id)
    assert migrated is not None
    assert registry.async_get(old_entry.entity_id) is None
    assert migrated.unique_id == _PORTABLE_SCREEN_UID
    assert migrated.translation_key == "portable_screen"
    assert migrated.name == "Portable display"
    assert migrated.icon == "mdi:television"
    assert migrated.area_id == area.id
    assert migrated.disabled_by is er.RegistryEntryDisabler.USER


def test_grid_standard_migration_creates_diagnostic_sensor(
    hass: HomeAssistant,
) -> None:
    """The obsolete text entry becomes a read-only diagnostic sensor."""
    entry = _config_entry(hass)
    registry = er.async_get(hass)
    area = ar.async_get(hass).async_create("Utility room")
    old_entry = _grid_standard_text(registry, entry)
    old_entry = registry.async_update_entity(
        old_entry.entity_id,
        area_id=area.id,
        icon="mdi:transmission-tower",
        name="Grid code",
    )

    _async_migrate_grid_standard_entity(hass, entry)

    target_id = registry.async_get_entity_id("sensor", DOMAIN, _GRID_STANDARD_UID)
    assert target_id is not None
    migrated = registry.async_get(target_id)
    assert migrated is not None
    assert registry.async_get(old_entry.entity_id) is None
    assert migrated.entity_category == EntityCategory.DIAGNOSTIC
    assert migrated.translation_key == "grid_standard"
    assert migrated.name == "Grid code"
    assert migrated.icon == "mdi:transmission-tower"
    assert migrated.area_id == area.id


def test_grid_standard_migration_keeps_existing_same_entry_sensor(
    hass: HomeAssistant,
) -> None:
    """An existing sensor is retained while the obsolete text entry is removed."""
    entry = _config_entry(hass)
    registry = er.async_get(hass)
    target = _grid_standard_sensor(
        registry,
        entry,
        suggested_object_id="existing_grid_standard",
    )
    target = registry.async_update_entity(
        target.entity_id,
        icon="mdi:meter-electric",
        name="Keep this sensor",
    )
    old_entry = _grid_standard_text(registry, entry)

    _async_migrate_grid_standard_entity(hass, entry)

    assert registry.async_get(old_entry.entity_id) is None
    preserved = registry.async_get(target.entity_id)
    assert preserved is not None
    assert preserved.name == "Keep this sensor"
    assert preserved.icon == "mdi:meter-electric"


def test_grid_standard_migration_is_idempotent(hass: HomeAssistant) -> None:
    """Repeating setup leaves the already migrated sensor unchanged."""
    entry = _config_entry(hass)
    registry = er.async_get(hass)
    old_entry = _grid_standard_text(registry, entry)

    _async_migrate_grid_standard_entity(hass, entry)
    target_id = registry.async_get_entity_id("sensor", DOMAIN, _GRID_STANDARD_UID)
    assert target_id is not None

    _async_migrate_grid_standard_entity(hass, entry)

    assert registry.async_get(old_entry.entity_id) is None
    assert (
        registry.async_get_entity_id("sensor", DOMAIN, _GRID_STANDARD_UID) == target_id
    )


def test_grid_standard_migration_skips_cross_entry_collision(
    hass: HomeAssistant,
) -> None:
    """A sensor owned by another entry blocks migration without data loss."""
    source_entry = _config_entry(hass, "source-entry")
    other_entry = _config_entry(hass, "other-entry")
    registry = er.async_get(hass)
    old_entry = _grid_standard_text(registry, source_entry)
    collision = _grid_standard_sensor(registry, other_entry)

    _async_migrate_grid_standard_entity(hass, source_entry)

    assert registry.async_get(old_entry.entity_id) is not None
    preserved = registry.async_get(collision.entity_id)
    assert preserved is not None
    assert preserved.config_entry_id == other_entry.entry_id


def test_portable_screen_migration_keeps_existing_same_entry_select(
    hass: HomeAssistant,
) -> None:
    """An existing select is retained while its obsolete switch is removed."""
    entry = _config_entry(hass)
    registry = er.async_get(hass)
    target = registry.async_get_or_create(
        "select",
        DOMAIN,
        _PORTABLE_SCREEN_UID,
        config_entry=entry,
        suggested_object_id="existing_portable_screen",
    )
    target = registry.async_update_entity(
        target.entity_id,
        icon="mdi:monitor",
        name="Keep this select",
    )
    old_entry = _portable_screen_switch(registry, entry)

    _async_migrate_portable_screen_entity(hass, entry)

    assert registry.async_get(old_entry.entity_id) is None
    preserved = registry.async_get(target.entity_id)
    assert preserved is not None
    assert preserved.name == "Keep this select"
    assert preserved.icon == "mdi:monitor"


def test_portable_screen_migration_is_idempotent(
    hass: HomeAssistant,
) -> None:
    """Repeating setup leaves the already migrated select unchanged."""
    entry = _config_entry(hass)
    registry = er.async_get(hass)
    old_entry = _portable_screen_switch(registry, entry)

    _async_migrate_portable_screen_entity(hass, entry)
    target_id = registry.async_get_entity_id(
        "select",
        DOMAIN,
        _PORTABLE_SCREEN_UID,
    )
    assert target_id is not None

    _async_migrate_portable_screen_entity(hass, entry)

    assert registry.async_get(old_entry.entity_id) is None
    assert (
        registry.async_get_entity_id("select", DOMAIN, _PORTABLE_SCREEN_UID)
        == target_id
    )


def test_portable_screen_migration_skips_cross_entry_collision(
    hass: HomeAssistant,
) -> None:
    """A select owned by another entry blocks migration without data loss."""
    source_entry = _config_entry(hass, "source-entry")
    other_entry = _config_entry(hass, "other-entry")
    registry = er.async_get(hass)
    old_entry = _portable_screen_switch(registry, source_entry)
    collision = registry.async_get_or_create(
        "select",
        DOMAIN,
        _PORTABLE_SCREEN_UID,
        config_entry=other_entry,
        suggested_object_id="other_portable_screen",
    )

    _async_migrate_portable_screen_entity(hass, source_entry)

    assert registry.async_get(old_entry.entity_id) is not None
    preserved = registry.async_get(collision.entity_id)
    assert preserved is not None
    assert preserved.config_entry_id == other_entry.entry_id
