"""Additional behavioural branch tests for the Jackery switch platform."""

from collections.abc import Callable
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault import switch as switch_mod
from custom_components.jackery_solarvault.const import (
    DISCOVERY_SOURCE_LEGACY_BIND_LIST,
    FIELD_SOCKET_PRIORITY,
    PAYLOAD_CIRCUIT_PROPERTY,
    PAYLOAD_DEVICE,
    PAYLOAD_DISCOVERY_SOURCE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_SYSTEM,
    PAYLOAD_WEATHER_PLAN,
)
from custom_components.jackery_solarvault.switch import (
    JackeryBreakerSwitch,
    JackeryDescriptionSwitch,
    JackerySmartPlugPrioritySwitch,
    JackerySmartPlugSwitch,
    JackerySwitchDescription,
)
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

_DEVICE_ID = "device-1"


def _coordinator(data: dict[str, Any] | None = None) -> MagicMock:
    """Return a coordinator double with every switch write boundary."""
    coordinator = MagicMock()
    coordinator.data = data or {}
    coordinator.last_update_success = True
    coordinator.device_supports_advanced.return_value = False
    coordinator.is_device_locally_reachable.return_value = False
    for name in (
        "async_set_eps",
        "async_set_auto_standby",
        "async_set_standby",
        "async_set_follow_meter",
        "async_set_off_grid_shutdown",
        "async_set_storm_warning",
        "async_update_third_party_mqtt_config",
        "async_portable_toggle_output",
        "async_set_shelly_cloud_switch",
        "async_set_smart_plug_switch",
        "async_set_smart_plug_priority",
        "async_set_breaker_switch",
    ):
        setattr(coordinator, name, AsyncMock())
    return coordinator


def _description_switch(
    description: JackerySwitchDescription,
    payload: dict[str, Any] | None = None,
) -> JackeryDescriptionSwitch:
    """Build a description switch without requiring an HA entity platform."""
    entity = JackeryDescriptionSwitch.__new__(JackeryDescriptionSwitch)
    mutable = cast("Any", entity)
    mutable.coordinator = _coordinator({_DEVICE_ID: payload or {}})
    mutable._device_id = _DEVICE_ID
    mutable.entity_description = description
    return entity


def _plug_switch(
    plug: dict[str, Any],
    *,
    priority: bool = False,
) -> JackerySmartPlugSwitch:
    """Build a plug switch bound to the supplied payload."""
    cls = JackerySmartPlugPrioritySwitch if priority else JackerySmartPlugSwitch
    entity = cls.__new__(cls)
    mutable = cast("Any", entity)
    mutable.coordinator = _coordinator(
        {_DEVICE_ID: {PAYLOAD_SMART_PLUGS: [plug]}},
    )
    mutable._device_id = _DEVICE_ID
    mutable._plug_index = 1
    mutable._plug_sn = str(plug.get("deviceSn") or "bound-sn")
    mutable._plug_key = "smart_plug_1"
    return entity


def _breaker_switch(breakers: list[dict[str, Any]]) -> JackeryBreakerSwitch:
    """Build a breaker switch bound to breaker index ``3``."""
    entity = JackeryBreakerSwitch.__new__(JackeryBreakerSwitch)
    mutable = cast("Any", entity)
    mutable.coordinator = _coordinator(
        {_DEVICE_ID: {PAYLOAD_CIRCUIT_PROPERTY: breakers}},
    )
    mutable._device_id = _DEVICE_ID
    mutable._breaker_index = 1
    mutable._breaker_id = "3"
    return entity


def test_payload_family_detection_prefers_home_evidence() -> None:
    """Home evidence wins over stale legacy-bind metadata."""
    portable = {
        PAYLOAD_DEVICE: {
            PAYLOAD_DISCOVERY_SOURCE: DISCOVERY_SOURCE_LEGACY_BIND_LIST,
        }
    }
    assert switch_mod._is_portable_payload(portable) is True
    assert (
        switch_mod._is_portable_payload(
            portable,
            {"swEps": 1},
        )
        is False
    )
    assert (
        switch_mod._payload_has_home_payload_evidence({
            PAYLOAD_SYSTEM: {"systemId": "system-1"}
        })
        is True
    )
    assert (
        switch_mod._payload_has_home_payload_evidence({
            "http_properties": {"batSoc": 50}
        })
        is True
    )
    assert switch_mod._is_portable_payload({}) is False


def test_description_resolves_app_and_transport_sources() -> None:
    """Descriptions infer app, read, and command capabilities from their fields."""
    writable = JackerySwitchDescription(
        key="writable",
        source_keys=("field",),
        setter=switch_mod._set_eps,
    )
    http_only = JackerySwitchDescription(
        key="http_only",
        source_keys=("weather",),
        source_section=PAYLOAD_WEATHER_PLAN,
    )
    smali_only = JackerySwitchDescription(
        key="smali_only",
        source_keys=(),
        smali_field="smaliField",
    )

    assert writable.app_fields == ("field",)
    assert writable.data_sources
    assert writable.command_sources == switch_mod.LAYER5_COMMAND_SOURCES
    assert http_only.data_sources == switch_mod.HTTP_DATA_SOURCES
    assert smali_only.app_fields == ("smaliField",)


@pytest.mark.parametrize(
    ["helper", "method", "expected"],
    [
        ["_set_eps", "async_set_eps", (_DEVICE_ID, True)],
        ["_set_auto_standby", "async_set_auto_standby", (_DEVICE_ID, True)],
        ["_set_standby", "async_set_standby", (_DEVICE_ID, True)],
        ["_set_follow_meter", "async_set_follow_meter", (_DEVICE_ID, True)],
        ["_set_off_grid_shutdown", "async_set_off_grid_shutdown", (_DEVICE_ID, True)],
        ["_set_storm_warning", "async_set_storm_warning", (_DEVICE_ID, True)],
    ],
)
async def test_boolean_setter_helpers_forward_exact_state(
    helper: str,
    method: str,
    expected: tuple[str, bool],
) -> None:
    """Simple setter helpers preserve device identity and boolean state."""
    coordinator = _coordinator()

    await cast("Callable[..., Any]", getattr(switch_mod, helper))(
        coordinator,
        _DEVICE_ID,
        True,
    )

    getattr(coordinator, method).assert_awaited_once_with(*expected)


async def test_third_party_mqtt_helper_encodes_boolean() -> None:
    """The third-party bridge setter encodes HA booleans as app integers."""
    coordinator = _coordinator()

    await switch_mod._set_third_party_mqtt_enabled(coordinator, _DEVICE_ID, False)

    coordinator.async_update_third_party_mqtt_config.assert_awaited_once_with(
        _DEVICE_ID,
        {"enable": 0},
    )


@pytest.mark.parametrize(
    ["helper", "action_id", "field"],
    [
        ["_set_portable_dc_output", switch_mod.ACTION_ID_PORTABLE_OUTPUT_DC, "odc"],
        [
            "_set_portable_dc_usb_output",
            switch_mod.ACTION_ID_PORTABLE_OUTPUT_DC_USB,
            "odcu",
        ],
        [
            "_set_portable_dc_car_output",
            switch_mod.ACTION_ID_PORTABLE_OUTPUT_DC_CAR,
            "odcc",
        ],
        ["_set_portable_ac_output", switch_mod.ACTION_ID_PORTABLE_OUTPUT_AC, "oac"],
        [
            "_set_portable_ac240_output",
            switch_mod.ACTION_ID_PORTABLE_OUTPUT_AC240,
            "oac2",
        ],
        ["_set_portable_light", switch_mod.ACTION_ID_PORTABLE_LIGHT, "lm"],
        [
            "_set_portable_super_charge",
            switch_mod.ACTION_ID_PORTABLE_SUPER_CHARGE,
            "sfc",
        ],
        [
            "_set_portable_output_priority_switch",
            switch_mod.ACTION_ID_PORTABLE_OUTPUT_PRIORITY_SWITCH,
            "outPrio",
        ],
        [
            "_set_portable_discharge_memory",
            switch_mod.ACTION_ID_PORTABLE_DISCHARGE_MEMORY,
            "dhg_recall",
        ],
    ],
)
async def test_portable_setter_helpers_preserve_protocol_mapping(
    helper: str,
    action_id: int,
    field: str,
) -> None:
    """Every portable helper forwards its documented action and field pair."""
    coordinator = _coordinator()

    await cast("Callable[..., Any]", getattr(switch_mod, helper))(
        coordinator,
        _DEVICE_ID,
        False,
    )

    coordinator.async_portable_toggle_output.assert_awaited_once_with(
        _DEVICE_ID,
        action_id=action_id,
        field=field,
        enabled=False,
    )


def test_description_state_fallback_order_and_unknown_transform() -> None:
    """Fallback section precedes task plan and transform may report unknown."""
    description = JackerySwitchDescription(
        key="fallback",
        source_keys=("flag",),
        fallback_section=PAYLOAD_WEATHER_PLAN,
        use_task_plan_fallback=True,
        is_on_transform=lambda value: None if value == "unknown" else bool(value),
    )
    weather = _description_switch(
        description,
        {
            PAYLOAD_PROPERTIES: {},
            PAYLOAD_WEATHER_PLAN: {"flag": 1},
            "task_plan": {"flag": 0},
        },
    )
    task = _description_switch(
        description,
        {PAYLOAD_PROPERTIES: {}, "task_plan": {"flag": "unknown"}},
    )

    assert weather.is_on is True
    assert task.is_on is None


async def test_description_no_setter_and_error_passthrough_branches() -> None:
    """Read-only switches no-op and translated/auth errors retain HA semantics."""
    no_setter = _description_switch(
        JackerySwitchDescription(key="readonly", source_keys=("flag",)),
    )
    await no_setter.async_turn_on()
    await no_setter.async_turn_off()

    translated = HomeAssistantError(
        translation_domain="jackery_solarvault",
        translation_key="already_translated",
    )
    translated_setter = AsyncMock(side_effect=translated)
    entity = _description_switch(
        JackerySwitchDescription(
            key="translated",
            source_keys=("flag",),
            setter=translated_setter,
        )
    )
    with pytest.raises(HomeAssistantError) as err:
        await entity.async_turn_off()
    assert err.value is translated

    auth = _description_switch(
        JackerySwitchDescription(
            key="auth",
            source_keys=("flag",),
            setter=AsyncMock(side_effect=ConfigEntryAuthFailed),
        )
    )
    with pytest.raises(ConfigEntryAuthFailed):
        await auth.async_turn_off()


def test_smart_plug_identity_state_and_attributes_fallbacks() -> None:
    """Plug identity aliases and sysSwitch fallback remain observable."""
    plug = {
        "devSn": "plug-dev-sn",
        "id": "cloud-id",
        "sysSwitch": "1",
        "deviceName": "Office",
        "commState": 1,
    }
    entity = _plug_switch(plug)
    cast("Any", entity)._plug_sn = "plug-dev-sn"

    assert entity.is_on is True
    assert entity._cloud_device_id(plug) == "cloud-id"
    assert entity._jackery_device_sn(plug) == "plug-dev-sn"
    assert entity._cloud_device_id({"devId": "fallback-id"}) == "fallback-id"
    assert entity._cloud_device_id({}) is None
    assert entity._jackery_device_sn({"sn": "fallback-sn"}) == "fallback-sn"
    assert entity._jackery_device_sn({}) is None
    assert entity.extra_state_attributes == {
        "plug_index": 1,
        "deviceName": "Office",
        "commState": 1,
        "id": "cloud-id",
        "sysSwitch": "1",
    }


@pytest.mark.parametrize(
    "plug",
    [
        {"deviceSn": "shelly-bound", "scanName": "shellyplus1", "isCloud": 1},
        {"scanName": "local"},
    ],
)
async def test_smart_plug_missing_write_identity_is_translated(
    plug: dict[str, Any],
) -> None:
    """Cloud and local writes fail explicitly when their required identity is absent."""
    entity = _plug_switch(plug)

    with pytest.raises(HomeAssistantError) as err:
        await entity.async_turn_on()

    assert err.value.translation_key == "entity_action_failed"


async def test_smart_plug_write_error_branches() -> None:
    """Plug writes preserve auth/translated errors and translate boundary failures."""
    entity = _plug_switch({"deviceSn": "plug-1", "switchState": 0})
    translated = HomeAssistantError(
        translation_domain="jackery_solarvault",
        translation_key="already_translated",
    )
    entity.coordinator.async_set_smart_plug_switch.side_effect = translated
    with pytest.raises(HomeAssistantError) as err:
        await entity.async_turn_off()
    assert err.value is translated

    entity.coordinator.async_set_smart_plug_switch.side_effect = (
        switch_mod.JackeryAuthError("expired")
    )
    with pytest.raises(ConfigEntryAuthFailed):
        await entity.async_turn_on()

    entity.coordinator.async_set_smart_plug_switch.side_effect = TimeoutError("slow")
    with pytest.raises(HomeAssistantError) as err:
        await entity.async_turn_on()
    assert err.value.translation_key == "entity_action_failed"


async def test_priority_switch_state_write_and_errors() -> None:
    """Priority switches expose state, route writes, and translate invalid identity."""
    entity = cast(
        "JackerySmartPlugPrioritySwitch",
        _plug_switch(
            {"deviceSn": "plug-1", FIELD_SOCKET_PRIORITY: 1},
            priority=True,
        ),
    )
    assert entity.is_on is True
    await entity.async_turn_off()
    entity.coordinator.async_set_smart_plug_priority.assert_awaited_once_with(
        _DEVICE_ID,
        plug_sn="plug-1",
        enabled=False,
    )

    missing = cast(
        "JackerySmartPlugPrioritySwitch",
        _plug_switch({FIELD_SOCKET_PRIORITY: 0}, priority=True),
    )
    with pytest.raises(HomeAssistantError) as err:
        await missing.async_turn_on()
    assert err.value.translation_key == "entity_action_failed"

    entity.coordinator.async_set_smart_plug_priority.side_effect = TimeoutError("slow")
    with pytest.raises(HomeAssistantError) as err:
        await entity.async_turn_on()
    assert err.value.translation_key == "entity_action_failed"


async def test_breaker_lookup_state_writes_metadata_and_attributes() -> None:
    """A breaker stays bound by id and exposes relay behavior and metadata."""
    entity = _breaker_switch([
        {
            "id": "breaker-1",
            "nm": "Kitchen",
            "idx": 3,
            "pc": 10,
            "sw": 1,
        },
        {"id": "other", "sw": 0},
    ])

    assert entity.is_on is True
    await entity.async_turn_on()
    await entity.async_turn_off()
    assert entity.coordinator.async_set_breaker_switch.await_args_list[0].args == (
        _DEVICE_ID,
        "3",
        True,
    )
    assert entity.coordinator.async_set_breaker_switch.await_args_list[1].args == (
        _DEVICE_ID,
        "3",
        False,
    )
    assert entity.extra_state_attributes == {
        "breaker_index": 1,
        "nm": "Kitchen",
        "idx": 3,
        "pc": 10,
        "sw": 1,
    }
    info = entity._build_breaker_device_info(
        1,
        entity._breaker,
        "breaker_1",
    )
    assert info["name"] == "Jackery device-1 Kitchen"

    cast("Any", entity)._breaker_id = "missing"
    assert entity._breaker == {}
    assert entity.is_on is None


async def test_setup_listener_adds_new_breakers_once_and_skips_missing_ids() -> None:
    """Runtime discovery adds valid breakers once without duplicating prior entities."""
    coordinator = _coordinator({_DEVICE_ID: {PAYLOAD_PROPERTIES: {}}})
    listener: list[Callable[[], None]] = []

    def _add_listener(callback: Callable[[], None]) -> Callable[[], None]:
        listener.append(callback)
        return lambda: None

    coordinator.async_add_listener.side_effect = _add_listener
    entry = SimpleNamespace(runtime_data=coordinator, async_on_unload=MagicMock())
    batches: list[list[Any]] = []

    await cast("Any", switch_mod.async_setup_entry)(None, entry, batches.append)
    initial_count = sum(len(batch) for batch in batches)
    assert initial_count > 0
    assert len(listener) == 1

    listener[0]()
    assert sum(len(batch) for batch in batches) == initial_count

    coordinator.data[_DEVICE_ID][PAYLOAD_CIRCUIT_PROPERTY] = [
        {"id": None, "sw": 1},
        {"id": "breaker-1", "sw": 1},
    ]
    listener[0]()
    breaker_entities = [
        entity
        for batch in batches
        for entity in batch
        if isinstance(entity, JackeryBreakerSwitch)
    ]
    assert len(breaker_entities) == 1

    listener[0]()
    assert (
        len([
            entity
            for batch in batches
            for entity in batch
            if isinstance(entity, JackeryBreakerSwitch)
        ])
        == 1
    )
