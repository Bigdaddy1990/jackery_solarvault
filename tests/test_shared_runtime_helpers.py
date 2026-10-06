"""Regression coverage for the shared Ponytail runtime helpers."""

import asyncio
import logging
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault import entity as entity_module, util
from custom_components.jackery_solarvault.const import DOMAIN
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from homeassistant.core import HomeAssistant


class _DiscoveredEntity(Entity):
    """Real HA entity with only the identity needed by discovery tests."""

    def __init__(self, unique_id: str) -> None:
        self._attr_unique_id = unique_id


async def test_discovery_registers_late_devices_once_and_unsubscribes(
    hass: HomeAssistant,
) -> None:
    """Discovery owns one listener and stops creating entities after unload."""
    coordinator = DataUpdateCoordinator[dict[str, Any]](
        hass, logging.getLogger(__name__), name="discovery-test", config_entry=None
    )
    coordinator.async_set_updated_data({})
    unload: list[Callable[[], None]] = []
    entry = SimpleNamespace(async_on_unload=unload.append)
    seen: set[str] = set()
    added: list[Entity] = []

    def add_entities(
        new_entities: Iterable[Entity], update_before_add: bool = False
    ) -> None:
        """Implement HA's add-entities callback without attaching the entities."""
        assert not update_before_add
        added.extend(new_entities)

    def collect() -> list[Entity]:
        entities: list[Entity] = []
        for device_id in coordinator.data:
            util.append_unique_entity(entities, seen, _DiscoveredEntity(device_id))
        return entities

    util.async_setup_entity_discovery(
        cast("Any", entry), coordinator, collect, add_entities
    )
    assert not added
    coordinator.async_set_updated_data({"first": {}})
    coordinator.async_set_updated_data({"first": {"properties": {"batSoc": 60}}})
    coordinator.async_set_updated_data({"first": {}, "second": {}})
    assert [entity.unique_id for entity in added] == ["first", "second"]
    assert len(unload) == 1
    unload[0]()
    coordinator.async_set_updated_data({"first": {}, "second": {}, "third": {}})
    assert [entity.unique_id for entity in added] == ["first", "second"]
    await hass.async_block_till_done()


async def test_discovery_adds_existing_devices_during_setup(
    hass: HomeAssistant,
) -> None:
    """An already populated coordinator is not deferred until its next update."""
    coordinator = DataUpdateCoordinator[dict[str, Any]](
        hass, logging.getLogger(__name__), name="initial-discovery", config_entry=None
    )
    coordinator.async_set_updated_data({"first": {}})
    unload: list[Callable[[], None]] = []
    entry = SimpleNamespace(async_on_unload=unload.append)
    expected = _DiscoveredEntity("first")
    added: list[Entity] = []

    def add_entities(
        new_entities: Iterable[Entity], update_before_add: bool = False
    ) -> None:
        """Implement HA's add-entities callback without attaching the entities."""
        assert not update_before_add
        added.extend(new_entities)

    util.async_setup_entity_discovery(
        cast("Any", entry), coordinator, lambda: [expected], add_entities
    )
    assert added == [expected]
    unload[0]()
    await hass.async_block_till_done()


async def test_store_locks_serialize_same_store_without_blocking_other_stores(
    hass: HomeAssistant,
) -> None:
    """Different Store keys remain independent while each key shares one lock."""
    first = util.get_store_lock(hass, "first.lock")
    second = util.get_store_lock(hass, "second.lock")
    assert isinstance(first, asyncio.Lock)
    async with first:
        assert util.get_store_lock(hass, "first.lock") is first
        assert first.locked()
        assert not second.locked()
    assert not first.locked()


def test_store_lock_replaces_invalid_runtime_state(hass: HomeAssistant) -> None:
    """A non-lock runtime value cannot disable serialization after setup."""
    hass.data["invalid.lock"] = object()
    lock = util.get_store_lock(hass, "invalid.lock")
    assert isinstance(lock, asyncio.Lock)
    assert hass.data["invalid.lock"] is lock


@pytest.mark.parametrize("with_entry", [False, True])
async def test_message_task_is_deferred_and_owned_by_its_config_entry(
    hass: HomeAssistant, with_entry: bool
) -> None:
    """Task creation preserves the scheduling boundary needed for MQTT FIFO."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    completed: list[str] = []

    async def operation() -> None:
        await asyncio.sleep(0)
        completed.append("delivered")

    with patch.object(
        entry, "async_create_task", wraps=entry.async_create_task
    ) as create:
        task = util.async_create_message_task(
            hass, entry if with_entry else None, operation(), name="ordered-message"
        )
        assert completed == []
        assert task.get_name().startswith("ordered-message")
        if with_entry:
            create.assert_called_once()
            assert create.call_args.kwargs["name"] == "ordered-message"
            assert create.call_args.kwargs["eager_start"] is False
        else:
            create.assert_not_called()
        await task
    assert completed == ["delivered"]


@pytest.mark.parametrize(
    ["raw", "expected"],
    [
        [None, None],
        [True, True],
        [False, False],
        [1, True],
        [0, False],
        [2, False],
        ["1", True],
        ["2", False],
        ["true", True],
        ["off", False],
        ["invalid", None],
    ],
)
def test_standby_parser_preserves_numeric_modes_and_boolean_text(
    raw: bool | float | str | None, expected: bool | None
) -> None:
    """Only numeric mode one enables manual standby; invalid text stays unknown."""
    assert util.standby_is_on(raw) is expected


@pytest.mark.parametrize("entity_key", ["reboot_device", "smart_plug_switch", "label"])
def test_action_error_preserves_translation_context(entity_key: str) -> None:
    """Translated action failures retain the entity, device and original error."""
    with pytest.raises(HomeAssistantError) as error:
        entity_module.raise_entity_action_error(
            entity_key, "device-1", RuntimeError("write rejected")
        )
    assert error.value.translation_domain == DOMAIN
    assert error.value.translation_key == "entity_action_failed"
    assert error.value.translation_placeholders == {
        "entity": entity_key,
        "device_id": "device-1",
        "error": "write rejected",
    }
