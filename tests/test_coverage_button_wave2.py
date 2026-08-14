"""Behavior coverage for Jackery button mappings and discovery gates."""

from collections.abc import Callable, Iterable
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.button import (
    QUERY_BUTTON_DESCRIPTIONS,
    JackeryDeleteStormAlertButton,
    JackeryQueryButton,
    JackeryReadScheduleButton,
    JackeryRebootButton,
    JackeryRefreshWeatherPlanButton,
    async_setup_entry,
)
from custom_components.jackery_solarvault.client import JackeryAuthError
from custom_components.jackery_solarvault.const import (
    DISCOVERY_SOURCE_LEGACY_BIND_LIST,
    FIELD_ALERT_ID,
    FIELD_DEVICE_SN,
    FIELD_END_TS,
    FIELD_MANUAL,
    FIELD_REBOOT,
    FIELD_START_TS,
    FIELD_STATUS,
    FIELD_STORM,
    PAYLOAD_DISCOVERY,
    PAYLOAD_DISCOVERY_SOURCE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_WEATHER_PLAN,
    TIMER_TASK_TYPE_SMART_PLUG,
)
from custom_components.jackery_solarvault.entity import (
    ALL_LIVE_DATA_SOURCES,
    HTTP_DATA_SOURCES,
    LAYER5_COMMAND_SOURCES,
)
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.entity import Entity
from homeassistant.helpers.entity_platform import AddEntitiesCallback

_DEVICE_ID = "device-1"


def _description(key: str) -> Any:
    """Return the query-button description with the requested key."""
    return next(item for item in QUERY_BUTTON_DESCRIPTIONS if item.key == key)


def _coordinator(payload: dict[str, Any] | None = None) -> MagicMock:
    """Build a reachable coordinator boundary double for button behavior."""
    coordinator = MagicMock()
    coordinator.data = {_DEVICE_ID: payload or {}}
    coordinator.last_update_success = True
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    coordinator.is_entity_source_available = MagicMock(return_value=True)
    coordinator.is_device_reachable = MagicMock(return_value=True)
    return coordinator


def _query_button(key: str, coordinator: MagicMock | None = None) -> JackeryQueryButton:
    """Create one query button with a reachable coordinator."""
    target = coordinator or _coordinator()
    return JackeryQueryButton(target, _DEVICE_ID, description=_description(key))


def _entity_collector(target: list[Any]) -> AddEntitiesCallback:
    """Return an HA-compatible entity callback that appends to ``target``."""

    def _add_entities(
        new_entities: Iterable[Entity],
        update_before_add: bool = False,
    ) -> None:
        del update_before_add
        target.extend(new_entities)

    return _add_entities


@pytest.mark.parametrize(
    ["key", "method_name"],
    [
        ["refresh_system_info", "async_query_system_info"],
        ["refresh_device_info", "async_query_device_info"],
        ["refresh_wifi_list", "async_query_wifi_list"],
        ["refresh_time_zone", "async_get_time_zone"],
        ["sync_time_zone", "async_send_time_zone"],
        ["sync_cloud_mqtt_info", "async_sync_mqtt_connect_info"],
        ["refresh_device_ota_version", "async_query_device_ota_version"],
        ["refresh_third_party_mqtt_config", "async_query_third_party_mqtt_config"],
        ["refresh_wifi_config", "async_query_wifi_config"],
        ["refresh_battery_packs", "async_query_battery_packs"],
        ["refresh_smart_meter", "async_query_smart_meter"],
        ["refresh_meter_heads", "async_query_meter_heads"],
        ["refresh_smart_plugs", "async_query_smart_plugs"],
        ["refresh_subdevice_combo", "async_query_subdevice_combo"],
    ],
)
@pytest.mark.asyncio
async def test_home_query_mapping_calls_its_independent_coordinator_action(
    key: str,
    method_name: str,
) -> None:
    """Every Home query description reaches its dedicated coordinator action."""
    coordinator = _coordinator()
    action = AsyncMock()
    setattr(coordinator, method_name, action)

    await _description(key).action(coordinator, _DEVICE_ID)

    action.assert_awaited_once_with(_DEVICE_ID)


@pytest.mark.parametrize(
    "key",
    [
        "portable_restart",
        "portable_power_off",
        "portable_power_pack_blink",
        "portable_refresh_device_info",
        "portable_refresh_wifi_list",
        "portable_refresh_battery_packs",
        "portable_refresh_electricity_count",
        "portable_sync_mqtt_info",
        "portable_refresh_wifi_config",
        "portable_get_charge_plan",
        "portable_current_charge_plan",
        "portable_get_peaks_troughs",
        "portable_refresh_sub_ct",
    ],
)
@pytest.mark.asyncio
async def test_portable_query_mapping_preserves_catalog_command_metadata(
    key: str,
) -> None:
    """Portable query buttons forward their catalog action and command unchanged."""
    coordinator = _coordinator()
    coordinator.async_send_portable_command = AsyncMock()
    description = _description(key)

    await description.action(coordinator, _DEVICE_ID)

    call = coordinator.async_send_portable_command.await_args
    assert call.args == (_DEVICE_ID,)
    assert call.kwargs["action_id"] == description.action_id
    assert call.kwargs["cmd"] == description.cmd
    assert call.kwargs["body_fields"] == (
        {FIELD_REBOOT: 1}
        if key == "portable_restart"
        else {FIELD_REBOOT: 2}
        if key == "portable_power_off"
        else {}
    )
    keys_with_message_type = {
        "portable_get_charge_plan",
        "portable_current_charge_plan",
        "portable_get_peaks_troughs",
        "portable_refresh_sub_ct",
    }
    if key in keys_with_message_type:
        assert call.kwargs["message_type"] == description.message_type
    else:
        assert "message_type" not in call.kwargs


@pytest.mark.asyncio
async def test_portable_time_zone_uses_its_dedicated_setter() -> None:
    """Portable time-zone sync remains independent of generic portable commands."""
    coordinator = _coordinator()
    coordinator.async_send_portable_time_zone = AsyncMock()
    coordinator.async_send_portable_command = AsyncMock()

    await _description("portable_sync_time_zone").action(coordinator, _DEVICE_ID)

    coordinator.async_send_portable_time_zone.assert_awaited_once_with(_DEVICE_ID)
    coordinator.async_send_portable_command.assert_not_awaited()


def test_query_descriptions_declare_http_and_layer5_sources_independently() -> None:
    """HTTP-capable reads add HTTP without removing any Layer-5 command source."""
    for description in QUERY_BUTTON_DESCRIPTIONS:
        assert set(LAYER5_COMMAND_SOURCES) <= set(description.command_sources)
        if description.has_http_read:
            assert description.data_sources == ALL_LIVE_DATA_SOURCES
            assert description.command_sources[0] == HTTP_DATA_SOURCES[0]
        else:
            assert description.data_sources == LAYER5_COMMAND_SOURCES
            assert description.command_sources == LAYER5_COMMAND_SOURCES


@pytest.mark.asyncio
async def test_setup_home_device_discovers_queries_and_accessory_buttons() -> None:
    """Home discovery adds only Home commands plus schedules, plug, alert, and reboot."""
    payload = {
        PAYLOAD_PROPERTIES: {"batSoc": 50, FIELD_REBOOT: 0},
        PAYLOAD_SMART_PLUGS: [{FIELD_DEVICE_SN: "PLUG-1"}],
        PAYLOAD_WEATHER_PLAN: {
            FIELD_STORM: [
                {FIELD_ALERT_ID: "ALERT-1"},
                {FIELD_ALERT_ID: ""},
                "invalid",
            ],
        },
    }
    coordinator = _coordinator(payload)
    coordinator.device_supports_advanced.return_value = False
    entry = MagicMock(runtime_data=coordinator)
    added: list[Any] = []

    await async_setup_entry(MagicMock(), entry, _entity_collector(added))

    unique_ids = {entity.unique_id for entity in added}
    home_keys = {
        description.key
        for description in QUERY_BUTTON_DESCRIPTIONS
        if not description.key.startswith("portable_")
    }
    assert {f"{_DEVICE_ID}_{key}" for key in home_keys} <= unique_ids
    assert f"{_DEVICE_ID}_reboot_device" in unique_ids
    assert f"{_DEVICE_ID}_read_custom_mode_schedule" in unique_ids
    assert f"{_DEVICE_ID}_read_time_electricity_schedule" in unique_ids
    assert f"{_DEVICE_ID}_smart_plug_PLUG-1_read_schedule" in unique_ids
    assert f"{_DEVICE_ID}_delete_storm_alert_ALERT-1" in unique_ids
    assert not any("portable_" in str(unique_id) for unique_id in unique_ids)
    entry.async_on_unload.assert_called_once()


@pytest.mark.asyncio
async def test_setup_portable_device_excludes_home_and_accessory_buttons() -> None:
    """Legacy portable discovery exposes only the portable command family."""
    payload = {
        PAYLOAD_DISCOVERY: {
            PAYLOAD_DISCOVERY_SOURCE: DISCOVERY_SOURCE_LEGACY_BIND_LIST,
        },
        PAYLOAD_SMART_PLUGS: [{FIELD_DEVICE_SN: "IGNORED-PLUG"}],
        PAYLOAD_WEATHER_PLAN: {FIELD_STORM: [{FIELD_ALERT_ID: "IGNORED-ALERT"}]},
    }
    coordinator = _coordinator(payload)
    entry = MagicMock(runtime_data=coordinator)
    added: list[Any] = []

    await async_setup_entry(MagicMock(), entry, _entity_collector(added))

    assert added
    assert all(isinstance(entity, JackeryQueryButton) for entity in added)
    assert {entity.unique_id for entity in added} == {
        f"{_DEVICE_ID}_{description.key}"
        for description in QUERY_BUTTON_DESCRIPTIONS
        if description.key.startswith("portable_")
    }


@pytest.mark.asyncio
async def test_setup_listener_adds_only_newly_discovered_storm_alert() -> None:
    """An unchanged signature is ignored and a new alert creates one entity."""
    payload = {
        PAYLOAD_PROPERTIES: {"batSoc": 50},
        PAYLOAD_WEATHER_PLAN: {FIELD_STORM: []},
    }
    coordinator = _coordinator(payload)
    coordinator.device_supports_advanced.return_value = False
    listeners: list[Callable[[], None]] = []

    def _register_listener(listener: Callable[[], None]) -> Callable[[], None]:
        listeners.append(listener)
        return lambda: None

    coordinator.async_add_listener.side_effect = _register_listener
    entry = MagicMock(runtime_data=coordinator)
    batches: list[list[Any]] = []

    def _add_batch(
        new_entities: Iterable[Entity],
        update_before_add: bool = False,
    ) -> None:
        del update_before_add
        batches.append(list(new_entities))

    await async_setup_entry(MagicMock(), entry, _add_batch)
    assert len(batches) == 1

    listeners[0]()
    assert len(batches) == 1

    coordinator.data[_DEVICE_ID][PAYLOAD_WEATHER_PLAN] = {
        FIELD_STORM: [{FIELD_ALERT_ID: "ALERT-NEW"}],
    }
    listeners[0]()

    assert len(batches) == 2
    assert [entity.unique_id for entity in batches[1]] == [
        f"{_DEVICE_ID}_delete_storm_alert_ALERT-NEW",
    ]


@pytest.mark.asyncio
async def test_non_http_query_runs_without_http_refresh() -> None:
    """A query without an HTTP equivalent uses its own Layer-5 action only."""
    coordinator = _coordinator()
    coordinator.async_query_wifi_list = AsyncMock()
    coordinator.async_refresh_documented_http_read = AsyncMock()

    await _query_button("refresh_wifi_list", coordinator).async_press()

    coordinator.async_query_wifi_list.assert_awaited_once_with(_DEVICE_ID)
    coordinator.async_refresh_documented_http_read.assert_not_awaited()


def _specialized_button(
    kind: str,
    coordinator: MagicMock,
) -> tuple[Any, AsyncMock]:
    """Create a specialized button and return its coordinator action mock."""
    if kind == "reboot":
        coordinator.async_reboot_device = AsyncMock()
        return JackeryRebootButton(
            coordinator, _DEVICE_ID
        ), coordinator.async_reboot_device
    if kind == "weather":
        coordinator.async_query_weather_plan = AsyncMock()
        return (
            JackeryRefreshWeatherPlanButton(coordinator, _DEVICE_ID),
            coordinator.async_query_weather_plan,
        )
    if kind == "schedule":
        coordinator.async_read_device_schedule = AsyncMock()
        return (
            JackeryReadScheduleButton(
                coordinator,
                _DEVICE_ID,
                task_type=TIMER_TASK_TYPE_SMART_PLUG,
                key_suffix="plug_schedule",
                translation_key="read_smart_plug_schedule",
                plug_sn="PLUG-1",
            ),
            coordinator.async_read_device_schedule,
        )
    coordinator.async_delete_storm_alert = AsyncMock()
    return (
        JackeryDeleteStormAlertButton(
            coordinator,
            _DEVICE_ID,
            alert_id="ALERT-1",
        ),
        coordinator.async_delete_storm_alert,
    )


@pytest.mark.parametrize("kind", ["reboot", "weather", "schedule", "delete"])
@pytest.mark.asyncio
async def test_specialized_buttons_convert_jackery_auth_to_reauth(kind: str) -> None:
    """All specialized buttons convert HTTP auth loss into HA reauthentication."""
    button, action = _specialized_button(kind, _coordinator())
    action.side_effect = JackeryAuthError("expired")

    with pytest.raises(ConfigEntryAuthFailed):
        await button.async_press()


@pytest.mark.parametrize("kind", ["reboot", "weather", "schedule", "delete"])
@pytest.mark.asyncio
async def test_specialized_buttons_preserve_translated_ha_errors(kind: str) -> None:
    """All specialized buttons preserve an already translated domain error."""
    button, action = _specialized_button(kind, _coordinator())
    expected = HomeAssistantError(
        translation_domain="jackery_solarvault",
        translation_key="alert_not_found",
    )
    action.side_effect = expected

    with pytest.raises(HomeAssistantError) as raised:
        await button.async_press()

    assert raised.value is expected


@pytest.mark.parametrize("kind", ["reboot", "weather", "schedule", "delete"])
@pytest.mark.asyncio
async def test_specialized_buttons_wrap_transport_errors(kind: str) -> None:
    """All specialized buttons expose a translated failure for transport errors."""
    button, action = _specialized_button(kind, _coordinator())
    action.side_effect = TimeoutError("transport timeout")

    with pytest.raises(HomeAssistantError) as raised:
        await button.async_press()

    assert raised.value.translation_key == "entity_action_failed"
    placeholders = raised.value.translation_placeholders
    assert placeholders is not None
    assert placeholders["device_id"] == _DEVICE_ID
    assert placeholders["error"] == "transport timeout"


@pytest.mark.asyncio
async def test_unavailable_accessory_buttons_do_not_send_commands() -> None:
    """Unavailable schedule, weather, and deleted-alert buttons reject a press."""
    coordinator = _coordinator({PAYLOAD_WEATHER_PLAN: {FIELD_STORM: []}})
    coordinator.is_entity_source_available.return_value = False

    for kind in ("weather", "schedule", "delete"):
        button, action = _specialized_button(kind, coordinator)
        with pytest.raises(HomeAssistantError) as raised:
            await button.async_press()
        assert raised.value.translation_key == "entity_action_failed"
        action.assert_not_awaited()


def test_storm_alert_button_tracks_payload_and_exposes_metadata() -> None:
    """A storm-alert button reflects disappearance and exposes alert metadata."""
    alert = {
        FIELD_ALERT_ID: "ALERT-1",
        FIELD_START_TS: 100,
        FIELD_END_TS: 200,
        FIELD_STATUS: 1,
        FIELD_MANUAL: True,
    }
    coordinator = _coordinator(
        {PAYLOAD_WEATHER_PLAN: {FIELD_STORM: [alert]}},
    )
    button = JackeryDeleteStormAlertButton(
        coordinator,
        _DEVICE_ID,
        alert_id="ALERT-1",
    )

    assert button.available
    assert button.extra_state_attributes == alert

    coordinator.data[_DEVICE_ID][PAYLOAD_WEATHER_PLAN] = {FIELD_STORM: []}

    assert not button.available
    assert button.extra_state_attributes == {FIELD_ALERT_ID: "ALERT-1"}


def test_smart_plug_schedule_metadata_keeps_target_serial() -> None:
    """A plug schedule button exposes the exact task type and accessory serial."""
    button, _action = _specialized_button("schedule", _coordinator())

    assert button.extra_state_attributes == {
        "taskType": TIMER_TASK_TYPE_SMART_PLUG,
        FIELD_DEVICE_SN: "PLUG-1",
    }
