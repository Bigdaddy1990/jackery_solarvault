"""Smart-plug command availability follows the live transport classification."""

from copy import deepcopy
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.const import (
    FIELD_CONTROL_ALLOWED,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_IS_CLOUD,
    FIELD_SCAN_NAME,
    FIELD_SWITCH_STATE,
    PAYLOAD_SMART_PLUGS,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from custom_components.jackery_solarvault.entity import (
    HTTP_COMMAND_SOURCES,
    LAYER5_COMMAND_SOURCES,
)
from custom_components.jackery_solarvault.switch import (
    JackerySmartPlugPrioritySwitch,
    JackerySmartPlugSwitch,
)
from homeassistant.exceptions import HomeAssistantError

_DEVICE = "parent-1"
_SERIAL = "PLUG-A"
_CLOUD_ID = "shelly-device-a"
_KEY = "smart_plug_plug_a"


def _coordinator(plug: dict[str, Any], live_source: str) -> MagicMock:
    """Retain production source availability with one executable transport."""
    coordinator = MagicMock(name="smart-plug-coordinator")
    coordinator.data = {_DEVICE: {PAYLOAD_SMART_PLUGS: [plug]}}
    coordinator.last_update_success = True
    coordinator.config_entry = None
    coordinator.device_supports_advanced.return_value = False
    coordinator._command_source_available.side_effect = (  # ruff: ignore[private-member-access]
        lambda _device_id, source: source == live_source
    )
    coordinator.is_entity_source_available.side_effect = lambda *args, **kwargs: (
        JackerySolarVaultCoordinator.is_entity_source_available(
            coordinator, *args, **kwargs
        )
    )
    coordinator.async_set_smart_plug_switch = AsyncMock()
    coordinator.async_set_shelly_cloud_switch = AsyncMock()
    coordinator.async_set_smart_plug_priority = AsyncMock()
    return coordinator


def _switch(coordinator: Any, *, priority: bool = False) -> JackerySmartPlugSwitch:
    """Create one entity with a captured serial and stable registry identity."""
    entity_type = JackerySmartPlugPrioritySwitch if priority else JackerySmartPlugSwitch
    return entity_type(
        coordinator,
        _DEVICE,
        plug_index=1,
        plug_sn=_SERIAL,
        plug_key=_KEY,
    )


def _prepare_availability(entity: JackerySmartPlugSwitch) -> None:
    """Refresh the same availability cache used by HA coordinator listeners."""
    entity._availability_cache_active = True  # ruff: ignore[private-member-access]
    entity._refresh_availability_cache()  # ruff: ignore[private-member-access]


@pytest.mark.parametrize(
    "cloud_marker",
    [{FIELD_SCAN_NAME: "shellyplusplugs"}, {FIELD_IS_CLOUD: 1}],
    ids=["scan-name", "is-cloud"],
)
@pytest.mark.parametrize("live_source", ["http", "ble", "cloud_mqtt"])
async def test_live_cloud_classification_updates_availability_and_routing(
    cloud_marker: dict[str, Any],
    live_source: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Late cloud identification and its removal select the live command layer."""
    sparse = {FIELD_DEVICE_SN: _SERIAL, FIELD_SWITCH_STATE: 0}
    cloud = {
        **sparse,
        FIELD_DEVICE_ID: _CLOUD_ID,
        FIELD_CONTROL_ALLOWED: 1,
        **cloud_marker,
    }
    coordinator = _coordinator(sparse, live_source)
    relay = _switch(coordinator)
    priority = _switch(coordinator, priority=True)
    identities = [
        (entity.unique_id, deepcopy(entity.device_info)) for entity in (relay, priority)
    ]
    for entity in (relay, priority):
        _prepare_availability(entity)
        monkeypatch.setattr(entity, "_write_prepared_state", MagicMock())

    for plug, expected_sources in (
        (sparse, LAYER5_COMMAND_SOURCES),
        (cloud, HTTP_COMMAND_SOURCES),
        (sparse, LAYER5_COMMAND_SOURCES),
    ):
        coordinator.data[_DEVICE] = {PAYLOAD_SMART_PLUGS: [plug]}
        relay._handle_coordinator_update()  # ruff: ignore[private-member-access]
        priority._handle_coordinator_update()  # ruff: ignore[private-member-access]
        assert relay.command_sources == expected_sources
        contract = relay._source_capability_contract()  # ruff: ignore[private-member-access]
        assert contract[2] == expected_sources
        assert relay.available is (live_source in expected_sources)
        assert priority.command_sources == LAYER5_COMMAND_SOURCES
        assert priority.available is (live_source in LAYER5_COMMAND_SOURCES)
        assert [
            (entity.unique_id, entity.device_info) for entity in (relay, priority)
        ] == identities
        coordinator.async_set_shelly_cloud_switch.reset_mock()
        coordinator.async_set_smart_plug_switch.reset_mock()
        await relay.async_turn_on()
        if expected_sources == HTTP_COMMAND_SOURCES:
            coordinator.async_set_shelly_cloud_switch.assert_awaited_once_with(
                _DEVICE, shelly_device_id=_CLOUD_ID, on=True
            )
            coordinator.async_set_smart_plug_switch.assert_not_awaited()
        else:
            coordinator.async_set_smart_plug_switch.assert_awaited_once_with(
                _DEVICE, plug_sn=_SERIAL, on=True
            )
            coordinator.async_set_shelly_cloud_switch.assert_not_awaited()


async def test_late_cloud_classification_respects_live_permissions() -> None:
    """A changed permission is checked without recreating the existing entity."""
    sparse = {FIELD_DEVICE_SN: _SERIAL, FIELD_SWITCH_STATE: 0}
    coordinator = _coordinator(sparse, "http")
    relay = _switch(coordinator)
    cloud = {
        **sparse,
        FIELD_DEVICE_ID: _CLOUD_ID,
        FIELD_SCAN_NAME: "shellyplusplugs",
    }

    for allowed in (1, 0, 1):
        coordinator.data[_DEVICE] = {
            PAYLOAD_SMART_PLUGS: [{**cloud, FIELD_CONTROL_ALLOWED: allowed}]
        }
        _prepare_availability(relay)
        assert relay.command_sources == HTTP_COMMAND_SOURCES
        assert relay.available is True
        coordinator.async_set_shelly_cloud_switch.reset_mock()
        if allowed:
            await relay.async_turn_off()
            coordinator.async_set_shelly_cloud_switch.assert_awaited_once_with(
                _DEVICE, shelly_device_id=_CLOUD_ID, on=False
            )
        else:
            with pytest.raises(HomeAssistantError) as err:
                await relay.async_turn_off()
            assert err.value.translation_placeholders is not None
            assert (
                err.value.translation_placeholders["error"]
                == "Shelly control is not allowed"
            )
            coordinator.async_set_shelly_cloud_switch.assert_not_awaited()
        coordinator.async_set_smart_plug_switch.assert_not_awaited()


async def test_shared_cloud_target_never_routes_a_serial_bound_shelly_write() -> None:
    """Conflicting serial claims cannot turn one socket's cloud ID into a target."""
    cloud = {
        FIELD_DEVICE_SN: _SERIAL,
        FIELD_DEVICE_ID: _CLOUD_ID,
        FIELD_SCAN_NAME: "shellyplusplugs",
        FIELD_CONTROL_ALLOWED: 1,
        FIELD_SWITCH_STATE: 0,
    }
    coordinator = _coordinator(cloud, "http")
    relay = _switch(coordinator)
    coordinator.data[_DEVICE] = {
        PAYLOAD_SMART_PLUGS: [cloud, {**cloud, FIELD_DEVICE_SN: "PLUG-B"}]
    }

    with pytest.raises(HomeAssistantError) as err:
        await relay.async_turn_on()

    assert err.value.translation_placeholders is not None
    assert err.value.translation_placeholders["error"] == "ambiguous Shelly deviceId"
    coordinator.async_set_shelly_cloud_switch.assert_not_awaited()
    coordinator.async_set_smart_plug_switch.assert_not_awaited()
