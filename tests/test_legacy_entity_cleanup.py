"""Behavioral tests for legacy entity unique-ID matching."""

from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault import (
    _async_clean_legacy_entities,  # ruff: ignore[import-private-name]
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
    ["unique_id", "suffix"],
    [
        ["12345_battery_soc", "_battery_soc"],
        ["9_some_key", "_some_key"],
        ["12345_battery_pack_0_current", "_current"],
        ["99_battery_pack_12_temp", "_temp"],
        ["12345", ""],
        ["12345_battery_pack_2", ""],
    ],
)
def test_legacy_suffix_matches_supported_ids(unique_id: str, suffix: str) -> None:
    """Match only supported numeric legacy heads and battery-pack heads."""
    assert _legacy_suffix_matches(unique_id, suffix)


@pytest.mark.parametrize(
    ["unique_id", "suffix"],
    [
        ["my_device_battery_soc", "_battery_soc"],
        ["abc123_voltage", "_voltage"],
        ["12345_battery_pack_abc_voltage", "_voltage"],
        ["12345_battery_soc", "_voltage"],
        ["12345_pv_power_w", "_power_w"],
        ["", "_voltage"],
        ["_voltage", "_voltage"],
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


def test_setup_cleanup_keeps_writable_grid_standard_text(hass: HomeAssistant) -> None:
    """The editable text and the read-only sensor share one unique_id by design.

    Live 2026-09-26: a stale text->sensor migration removed the text entry on
    every setup, so HA re-registered it as new on each restart.
    """
    entry = _config_entry(hass)
    entry.runtime_data = SimpleNamespace(data={})
    registry = er.async_get(hass)
    text = _grid_standard_text(registry, entry)
    sensor = _grid_standard_sensor(registry, entry)

    _async_clean_legacy_entities(hass, entry)

    kept = registry.async_get(text.entity_id)
    assert kept is not None
    assert kept.id == text.id
    assert registry.async_get(sensor.entity_id) is not None


def test_setup_cleanup_retires_duplicate_alert_count(hass: HomeAssistant) -> None:
    """Retire the old counter while preserving the documented alarm_count ID."""
    entry = _config_entry(hass)
    entry.runtime_data = SimpleNamespace(data={})
    registry = er.async_get(hass)
    obsolete = registry.async_get_or_create(
        "sensor", DOMAIN, "12345_alert_count", config_entry=entry
    )
    canonical = registry.async_get_or_create(
        "sensor", DOMAIN, "12345_alarm_count", config_entry=entry
    )

    _async_clean_legacy_entities(hass, entry)

    assert registry.async_get(obsolete.entity_id) is None
    assert registry.async_get(canonical.entity_id) is not None


@pytest.mark.parametrize(
    "key", ["main_battery_charge_energy", "main_battery_discharge_energy"]
)
def test_setup_cleanup_preserves_main_battery_lifetime_identity(
    hass: HomeAssistant,
    key: str,
) -> None:
    """Setup must preserve head battery IDs and their Recorder consumers."""
    entry = _config_entry(hass)
    entry.runtime_data = SimpleNamespace(data={})
    registry = er.async_get(hass)
    original = registry.async_get_or_create(
        "sensor",
        DOMAIN,
        f"12345_{key}",
        config_entry=entry,
        suggested_object_id=f"solarvault_hauptbatterie_{key}",
    )

    _async_clean_legacy_entities(hass, entry)

    preserved = registry.async_get(original.entity_id)
    assert preserved is not None
    assert preserved.id == original.id
    assert preserved.unique_id == original.unique_id
