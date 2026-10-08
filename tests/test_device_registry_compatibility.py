"""Exercise registry ownership without the removed multi-entry API."""

import ast
import inspect
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault.client.mqtt_discovery import (
    JackeryMqttSensorPublisher,
)
from custom_components.jackery_solarvault.const import DOMAIN
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from homeassistant.helpers import device_registry as dr

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@pytest.fixture(autouse=True)
def reject_deprecated_device_ownership(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make deprecated device membership fail instead of logging a warning."""
    original = inspect.getattr_static(dr.DeviceEntry, "config_entries")

    def guarded(entry: dr.DeviceEntry) -> set[str]:
        current = inspect.currentframe()
        assert current is not None
        frame = current.f_back
        assert frame is not None
        module = frame.f_globals.get("__name__", "")
        if module.startswith(f"custom_components.{DOMAIN}"):
            message = "deprecated device ownership"
            raise AssertionError(message)
        return cast("set[str]", original.__get__(entry, dr.DeviceEntry))

    monkeypatch.setattr(dr.DeviceEntry, "config_entries", property(guarded))


@pytest.mark.parametrize("native_first", [True, False])
def test_mqtt_mirror_links_after_either_discovery_order(
    hass: HomeAssistant, native_first: bool
) -> None:
    """A mirror joins its native parent even when MQTT discovery arrives first."""
    native_entry = MockConfigEntry(domain=DOMAIN)
    native_entry.add_to_hass(hass)
    mqtt_entry = MockConfigEntry(domain="mqtt")
    mqtt_entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    publisher = JackeryMqttSensorPublisher(hass, entry_id=native_entry.entry_id)

    def native_device() -> dr.DeviceEntry:
        return registry.async_get_or_create(
            config_entry_id=native_entry.entry_id,
            identifiers={(DOMAIN, "registry-compat")},
        )

    native = native_device() if native_first else None
    mirror = registry.async_get_or_create(
        config_entry_id=mqtt_entry.entry_id,
        identifiers={("mqtt", f"{DOMAIN}:registry-compat")},
    )
    publisher._link_mqtt_device(mirror.id)  # ruff: ignore[private-member-access]
    if native is None:
        unlinked = registry.async_get(mirror.id)
        assert isinstance(unlinked, dr.DeviceEntry)
        assert unlinked.via_device_id is None
        native = native_device()
    publisher._link_mqtt_device(native.id)  # ruff: ignore[private-member-access]
    publisher._link_mqtt_device(mirror.id)  # ruff: ignore[private-member-access]
    linked = registry.async_get(mirror.id)
    assert isinstance(linked, dr.DeviceEntry)
    assert linked.via_device_id == native.id
    assert linked.config_entry_id == mqtt_entry.entry_id
    publisher.async_retire()


def test_parent_removal_preserves_foreign_devices(hass: HomeAssistant) -> None:
    """Removal follows our descendants without deleting another entry's device."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    foreign_entry = MockConfigEntry(domain="mqtt")
    foreign_entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    parent = registry.async_get_or_create(
        config_entry_id=entry.entry_id, identifiers={(DOMAIN, "removed")}
    )
    descendant = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "descendant")},
        via_device_id=parent.id,
    )
    foreign = registry.async_get_or_create(
        config_entry_id=foreign_entry.entry_id,
        identifiers={("mqtt", "foreign")},
        via_device_id=parent.id,
    )
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    state = cast("Any", coordinator)
    state.hass = hass
    state.config_entry = entry
    state.entry = entry
    for name in (
        "_slow_cache",
        "_last_system_info_query",
        "_last_weather_plan_query",
        "_last_subdevice_query",
        "_last_shadow_query",
        "_system_info_cache",
        "_system_info_cache_monotonic",
        "_battery_pack_http_cache_seen",
    ):
        setattr(state, name, {})
    expected_removed = 2
    assert coordinator._unlink_removed_parent_devices({"removed"}) == expected_removed  # ruff: ignore[private-member-access]
    assert registry.async_get(parent.id) is None
    assert registry.async_get(descendant.id) is None
    preserved = registry.async_get(foreign.id)
    assert preserved is not None
    assert preserved.config_entry_id == foreign_entry.entry_id
    assert coordinator._unlink_removed_parent_devices({"removed"}) == 0  # ruff: ignore[private-member-access]


def test_runtime_has_no_removed_suggested_area_access() -> None:
    """A removed DeviceEntry attribute must not reappear in runtime code."""
    root = Path(__file__).resolve().parents[1] / "custom_components" / DOMAIN
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        assert not any(
            isinstance(node, ast.Attribute) and node.attr == "suggested_area"
            for node in ast.walk(tree)
        ), str(path)
