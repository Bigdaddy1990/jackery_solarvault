"""Regression tests for transport-independent entities and ordered live merges."""

import asyncio
from datetime import timedelta
import logging
from types import MethodType, SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

from custom_components.jackery_solarvault import (
    button as button_module,
    coordinator as coordinator_module,
    sensor as sensor_module,
)
from custom_components.jackery_solarvault.const import (
    ACTION_ID_EPS_ENABLED,
    ACTION_ID_PORTABLE_OUTPUT_AC,
    FIELD_ACCESSORIES,
    FIELD_ACTION_ID,
    FIELD_CHARGE_PLAN_PW,
    FIELD_CT_POWER,
    FIELD_CT_VOLT,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_DEV_TYPE,
    FIELD_ENERGY_PLAN_PW,
    FIELD_IN_PW,
    FIELD_IP,
    FIELD_MESSAGE_TYPE,
    FIELD_ONLINE_STATUS,
    FIELD_PV1,
    FIELD_PV_PW,
    FIELD_SW_EPS,
    FIELD_SW_EPS_STATE,
    MQTT_CMD_DEVICE_PROPERTY_CHANGE,
    MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
    MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
    PAYLOAD_CT_METER,
    PAYLOAD_DEVICE,
    PAYLOAD_DISCOVERY,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SYSTEM,
    PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID,
    SUBDEVICE_DEV_TYPE_BATTERY_PACK,
    SUBDEVICE_DEV_TYPE_CT,
    SUBDEVICE_DEV_TYPE_METER_HEAD,
    SUBDEVICE_DEV_TYPE_SMOKE,
    SUBDEVICE_DEV_TYPE_SOCKET,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
    _serialize_mqtt_messages_by_device,  # regression-tests callback ordering wrapper  # ruff: ignore[import-private-name]
    merge_shelly_cloud_item,
    normalize_local_mqtt_payload,
    normalize_shelly_cloud_payload,
)
from custom_components.jackery_solarvault.entity import JackeryEntity
from custom_components.jackery_solarvault.ingest import TransportSource
from custom_components.jackery_solarvault.util import coordinator_entity_signature
from homeassistant.exceptions import HomeAssistantError

_LIVE_PV_W = 321
_CLOUD_PV_W = 100
_HTTP_PV_W = 450
_LIVE_CT_POWER_W = 900
_SHELLY_CT_POWER_W = 100
_CT_VOLTAGE_V = 230
_PLAN_POWER_W = 800

if TYPE_CHECKING:
    from collections.abc import Coroutine

    import pytest


class _ImmediateBackgroundEntry:
    """Config-entry test double that schedules HA background tasks immediately."""

    @staticmethod
    def async_create_background_task(
        _hass: object,
        coro: Coroutine[Any, Any, Any],
        *,
        name: str,
        eager_start: bool,
    ) -> asyncio.Task[Any]:
        """Schedule the given coroutine like Home Assistant's config entry helper."""
        del eager_start
        return asyncio.create_task(coro, name=name)


def _set_test_attr(target: object, name: str, value: Any) -> None:
    """Set private coordinator seams used by narrow regression test doubles."""
    setattr(target, name, value)


async def test_home_and_ct_entities_register_without_current_values() -> None:
    """A restart without push transports must re-create stable registry entities."""
    coordinator = MagicMock(name="coordinator")
    coordinator.data = {
        "dev-1": {
            PAYLOAD_PROPERTIES: {},
            PAYLOAD_DEVICE: {},
            PAYLOAD_DISCOVERY: {},
            PAYLOAD_SYSTEM: {},
        },
    }
    coordinator._has_smart_meter_accessory.return_value = True  # ruff: ignore[private-member-access]
    coordinator.async_add_listener.return_value = lambda: None
    entry = SimpleNamespace(
        data={},
        options={},
        runtime_data=coordinator,
        async_on_unload=MagicMock(),
    )
    added: list[Any] = []

    await sensor_module.async_setup_entry(
        cast("Any", None),
        cast("Any", entry),
        cast("Any", added.extend),
    )

    unique_ids = {entity.unique_id for entity in added}
    assert "dev-1_energy_plan_power" in unique_ids
    assert "dev-1_grid_standard" in unique_ids
    assert "dev-1_smart_mode_active" in unique_ids
    assert "dev-1_today_load" in unique_ids
    assert "dev-1_alarm_count" in unique_ids
    assert "dev-1_firmware_version" in unique_ids
    assert "dev-1_smart_meter_grid_import_energy" in unique_ids
    assert "dev-1_smart_meter_phase_3_lifetime_import_energy" in unique_ids
    assert "dev-1_home_consumption_power" in unique_ids


async def test_discovered_accessory_sensors_register_before_live_push() -> None:
    """Discovery topology keeps accessory identities stable without MQTT/BLE."""
    coordinator = MagicMock(name="coordinator")
    coordinator.data = {
        "dev-1": {
            PAYLOAD_PROPERTIES: {},
            PAYLOAD_SYSTEM: {
                FIELD_ACCESSORIES: [
                    {
                        FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_BATTERY_PACK,
                        FIELD_DEVICE_SN: "pack-1",
                    },
                    {
                        FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_METER_HEAD,
                        FIELD_DEVICE_SN: "meter-1",
                    },
                    {
                        FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET,
                        FIELD_DEVICE_SN: "plug-1",
                    },
                    {
                        FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SMOKE,
                        FIELD_DEVICE_SN: "smoke-1",
                    },
                ],
            },
        },
    }
    coordinator._has_smart_meter_accessory.return_value = False  # ruff: ignore[private-member-access]
    coordinator.async_add_listener.return_value = lambda: None
    entry = SimpleNamespace(
        data={},
        options={},
        runtime_data=coordinator,
        async_on_unload=MagicMock(),
    )
    added: list[Any] = []

    await sensor_module.async_setup_entry(
        cast("Any", None),
        cast("Any", entry),
        cast("Any", added.extend),
    )

    unique_ids = {entity.unique_id for entity in added}
    assert "dev-1_battery_pack_pack_1_soc" in unique_ids
    assert "dev-1_meter_head_meter_1_input_power" in unique_ids
    assert "dev-1_smart_plug_plug_1_input_power" in unique_ids
    assert "dev-1_sub_device_smoke_1_alert_count" in unique_ids


async def test_home_and_plug_buttons_register_from_http_discovery() -> None:
    """Buttons do not require a BLE/local-MQTT property snapshot."""
    coordinator = MagicMock(name="coordinator")
    coordinator.data = {
        "dev-1": {
            PAYLOAD_PROPERTIES: {},
            PAYLOAD_SYSTEM: {
                FIELD_ACCESSORIES: [
                    {
                        FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET,
                        FIELD_DEVICE_SN: "plug-1",
                    },
                ],
            },
        },
    }
    coordinator.device_supports_advanced.return_value = True
    coordinator.async_add_listener.return_value = lambda: None
    entry = SimpleNamespace(
        runtime_data=coordinator,
        async_on_unload=MagicMock(),
    )
    added: list[Any] = []

    await button_module.async_setup_entry(
        cast("Any", None),
        cast("Any", entry),
        cast("Any", added.extend),
    )

    unique_ids = {entity.unique_id for entity in added}
    assert "dev-1_reboot_device" in unique_ids
    assert "dev-1_smart_plug_plug-1_read_schedule" in unique_ids


def test_entity_signature_tracks_discovery_accessory_topology() -> None:
    """Late accessory discovery triggers platform registration listeners."""
    empty = coordinator_entity_signature({
        "dev-1": {PAYLOAD_SYSTEM: {FIELD_ACCESSORIES: []}},
    })
    discovered = coordinator_entity_signature({
        "dev-1": {
            PAYLOAD_SYSTEM: {
                FIELD_ACCESSORIES: [
                    {
                        FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET,
                        FIELD_DEVICE_SN: "plug-1",
                    },
                ],
            },
        },
    })

    assert discovered != empty


def test_stale_cloud_offline_flag_does_not_hide_fresh_transport() -> None:
    """Fresh HTTP/MQTT/BLE reachability wins over a stale onlineStatus=0."""
    coordinator = MagicMock(name="coordinator")
    coordinator.data = {
        "dev-1": {PAYLOAD_DEVICE: {FIELD_ONLINE_STATUS: 0}},
    }
    coordinator.last_update_success = True
    coordinator.is_device_reachable.return_value = True
    entity = JackeryEntity(coordinator, "dev-1", "test")

    assert entity.available is True


def test_successful_recent_http_fetch_proves_device_reachability(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP keeps entities usable when BLE and local MQTT are disabled."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._configured_update_interval = timedelta(seconds=15)  # ruff: ignore[private-member-access]
    coordinator._shutdown_started = False  # ruff: ignore[private-member-access]
    coordinator._last_http_device_refresh_monotonic = {"dev-1": 100.0}  # ruff: ignore[private-member-access]
    coordinator.data = {"dev-1": {}}
    coordinator_any = cast("Any", coordinator)
    coordinator_any.is_device_locally_reachable = MethodType(
        lambda _self, _device_id: False,
        coordinator,
    )
    monkeypatch.setattr(coordinator_module.time, "monotonic", lambda: 110.0)

    assert coordinator.is_device_reachable("dev-1") is True


def _source_priority_coordinator() -> JackerySolarVaultCoordinator:
    """Return a minimal coordinator shell for source-priority merge tests."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._configured_update_interval = timedelta(seconds=15)  # ruff: ignore[private-member-access]
    coordinator._shutdown_started = False  # ruff: ignore[private-member-access]
    coordinator._property_source_state = {}  # ruff: ignore[private-member-access]
    coordinator._accessory_source_state = {}  # ruff: ignore[private-member-access]
    coordinator._property_overrides = {}  # ruff: ignore[private-member-access]
    _set_test_attr(coordinator, "_live_property_received_monotonic", {})
    _set_test_attr(coordinator, "_live_ct_received_monotonic", {})
    coordinator._last_property_push_monotonic = float("-inf")  # ruff: ignore[private-member-access]
    return coordinator


def _command_coordinator() -> JackerySolarVaultCoordinator:
    """Return a minimal coordinator shell for independent command tests."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator_any = cast("Any", coordinator)
    coordinator_any.hass = SimpleNamespace()
    coordinator_any.entry = _ImmediateBackgroundEntry()
    _set_test_attr(coordinator, "_mqtt_session_generation", 0)
    _set_test_attr(coordinator, "_cloud_mqtt_command_attempt_sequence", 0)
    _set_test_attr(coordinator, "_bind_cloud_command_attempt", MagicMock())
    _set_test_attr(
        coordinator,
        "_record_successful_command_transports",
        MagicMock(),
    )
    _set_test_attr(
        coordinator,
        "_record_independent_cloud_mqtt_result",
        MagicMock(),
    )
    return coordinator


def test_layer5_property_arrival_order_beats_same_tier_then_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Layer-5 transports share one tier: later fresh L5 frames win."""
    clock = {"now": 100.0}
    monkeypatch.setattr(
        coordinator_module.time,
        "monotonic",
        lambda: clock["now"],
    )
    coordinator = _source_priority_coordinator()

    merged = coordinator._merge_main_properties_for_device(  # ruff: ignore[private-member-access]
        "dev-1",
        {},
        {FIELD_PV_PW: _LIVE_PV_W},
        source=TransportSource.LOCAL_MQTT,
    )
    merged = coordinator._merge_main_properties_for_device(  # ruff: ignore[private-member-access]
        "dev-1",
        merged,
        {
            FIELD_PV_PW: _CLOUD_PV_W,
            FIELD_CHARGE_PLAN_PW: _PLAN_POWER_W,
        },
        source=TransportSource.CLOUD_MQTT,
    )

    assert merged[FIELD_PV_PW] == _CLOUD_PV_W
    assert merged[FIELD_CHARGE_PLAN_PW] == _PLAN_POWER_W

    merged = coordinator._merge_main_properties_for_device(  # ruff: ignore[private-member-access]
        "dev-1",
        merged,
        {FIELD_PV_PW: _LIVE_PV_W},
        source=TransportSource.LOCAL_MQTT,
    )
    assert merged[FIELD_PV_PW] == _LIVE_PV_W

    clock["now"] = 161.0
    merged = coordinator._merge_main_properties_for_device(  # ruff: ignore[private-member-access]
        "dev-1",
        merged,
        {FIELD_PV_PW: _CLOUD_PV_W},
        source=TransportSource.CLOUD_MQTT,
    )

    assert merged[FIELD_PV_PW] == _CLOUD_PV_W


def test_fresh_ble_property_beats_http_while_http_fills_missing_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """HTTP stays active but cannot reverse a fresh BLE field."""
    monkeypatch.setattr(coordinator_module.time, "monotonic", lambda: 100.0)
    coordinator = _source_priority_coordinator()

    merged = coordinator._merge_main_properties_for_device(  # ruff: ignore[private-member-access]
        "dev-1",
        {},
        {FIELD_ENERGY_PLAN_PW: _LIVE_CT_POWER_W},
        source=TransportSource.BLE,
    )
    merged = coordinator._merge_main_properties_for_device(  # ruff: ignore[private-member-access]
        "dev-1",
        merged,
        {FIELD_ENERGY_PLAN_PW: 0, FIELD_PV_PW: _HTTP_PV_W},
        source=TransportSource.HTTP,
    )

    assert merged[FIELD_ENERGY_PLAN_PW] == _LIVE_CT_POWER_W
    assert merged[FIELD_PV_PW] == _HTTP_PV_W


def test_layer5_ct_arrival_order_while_cloud_fills_missing_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Layer-5 accessory telemetry follows arrival order and keeps filled gaps."""
    monkeypatch.setattr(coordinator_module.time, "monotonic", lambda: 100.0)
    coordinator = _source_priority_coordinator()
    updated: dict[str, Any] = {}

    coordinator._merge_subdevice_data(  # ruff: ignore[private-member-access]
        updated,
        {FIELD_CT_POWER: _LIVE_CT_POWER_W},
        device_id="dev-1",
        source_transport=TransportSource.LOCAL_MQTT,
    )
    coordinator._merge_subdevice_data(  # ruff: ignore[private-member-access]
        updated,
        {
            FIELD_CT_POWER: _SHELLY_CT_POWER_W,
            FIELD_CT_VOLT: _CT_VOLTAGE_V,
        },
        device_id="dev-1",
        source_transport=TransportSource.CLOUD_MQTT,
    )

    assert updated[PAYLOAD_CT_METER][FIELD_CT_POWER] == _SHELLY_CT_POWER_W
    assert updated[PAYLOAD_CT_METER][FIELD_CT_VOLT] == _CT_VOLTAGE_V

    coordinator._merge_subdevice_data(  # ruff: ignore[private-member-access]
        updated,
        {FIELD_CT_POWER: _LIVE_CT_POWER_W},
        device_id="dev-1",
        source_transport=TransportSource.LOCAL_MQTT,
    )

    assert updated[PAYLOAD_CT_METER][FIELD_CT_POWER] == _LIVE_CT_POWER_W


async def test_cloud_subdevice_frame_is_ingested_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A `/device` subdevice frame must not traverse the accessory ingest twice."""
    coordinator = _source_priority_coordinator()
    current: dict[str, Any] = {
        PAYLOAD_PROPERTIES: {},
        PAYLOAD_DEVICE: {},
    }
    coordinator._merge_subdevice_data(  # ruff: ignore[private-member-access]
        current,
        {FIELD_CT_POWER: _LIVE_CT_POWER_W},
        device_id="dev-1",
        source_transport=TransportSource.LOCAL_MQTT,
    )
    coordinator.data = {"dev-1": current}
    coordinator._device_index = {"dev-1": {}}  # ruff: ignore[private-member-access]
    _set_test_attr(coordinator, "_async_payload_debug_event", AsyncMock())
    _set_test_attr(coordinator, "_schedule_battery_pack_ota_enrichment", MagicMock())

    def _capture(new_data: dict[str, dict[str, Any]]) -> None:
        coordinator.data = new_data

    _set_test_attr(coordinator, "_push_partial_update", _capture)

    with caplog.at_level(
        logging.DEBUG,
        logger="custom_components.jackery_solarvault.coordinator",
    ):
        accepted = await coordinator._async_handle_mqtt_message(  # ruff: ignore[private-member-access]
            "hb/app/user/device",
            {
                FIELD_DEVICE_ID: "dev-1",
                FIELD_MESSAGE_TYPE: MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
                "body": {FIELD_CT_POWER: _SHELLY_CT_POWER_W},
            },
        )

    assert accepted == "dev-1"
    rejections = [
        record
        for record in caplog.records
        if "rejected ct_meter/ct fields" in record.getMessage()
    ]
    assert not rejections
    assert coordinator.data["dev-1"][PAYLOAD_CT_METER][FIELD_CT_POWER] == (
        _SHELLY_CT_POWER_W
    )


async def test_portable_write_uses_ble_before_cloud_mqtt() -> None:
    """Portable action IDs dispatch through every supported app transport."""
    coordinator = _command_coordinator()
    ble_mock = AsyncMock(return_value=True)
    publish_mock = AsyncMock()
    coordinator_any = cast("Any", coordinator)
    coordinator_any.async_send_ble_command = ble_mock
    _set_test_attr(coordinator, "_async_publish_command", publish_mock)

    await coordinator._async_publish_command_ble_first(  # ruff: ignore[private-member-access]
        "dev-1",
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_PORTABLE_OUTPUT_AC,
        cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[ACTION_ID_PORTABLE_OUTPUT_AC],
        body_fields={"ac": 1},
    )

    ble_mock.assert_awaited_once()
    publish_mock.assert_awaited_once()


async def test_portable_write_falls_back_to_cloud_mqtt_after_ble_failure() -> None:
    """A portable BLE error cannot swallow the Cloud-MQTT fallback."""
    coordinator = _command_coordinator()
    ble_mock = AsyncMock(side_effect=RuntimeError("BLE unavailable"))
    publish_mock = AsyncMock()
    coordinator_any = cast("Any", coordinator)
    coordinator_any.async_send_ble_command = ble_mock
    _set_test_attr(coordinator, "_async_publish_command", publish_mock)

    await coordinator._async_publish_command_ble_first(  # ruff: ignore[private-member-access]
        "dev-1",
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_PORTABLE_OUTPUT_AC,
        cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[ACTION_ID_PORTABLE_OUTPUT_AC],
        body_fields={"ac": 1},
    )

    publish_mock.assert_awaited_once()


async def test_mqtt_frames_for_one_device_keep_callback_order() -> None:
    """A delayed older frame cannot finish after and overwrite a newer frame."""
    release_first = asyncio.Event()
    events: list[str] = []

    class _OrderedHandler:
        def __init__(self) -> None:
            self.data: dict[str, dict[str, Any]] = {"dev-1": {}}
            self._mqtt_message_locks: dict[str, asyncio.Lock] = {}

        @staticmethod
        def _resolve_device_id_from_mqtt(_payload: dict[str, Any]) -> str:
            return "dev-1"

        @_serialize_mqtt_messages_by_device
        async def handle(self, topic: str, _payload: dict[str, Any]) -> str:
            assert self.data
            events.append(f"start:{topic}")
            if topic == "first":
                await release_first.wait()
            events.append(f"end:{topic}")
            return "dev-1"

    handler = _OrderedHandler()
    first = asyncio.ensure_future(handler.handle("first", {}))
    await asyncio.sleep(0)
    second = asyncio.ensure_future(handler.handle("second", {}))
    await asyncio.sleep(0)

    assert events == ["start:first"]
    release_first.set()
    await asyncio.gather(first, second)
    assert events == ["start:first", "end:first", "start:second", "end:second"]


async def test_ble_proxy_failure_cannot_block_cloud_command_fallback() -> None:
    """A failed optional BLE/ESPHome path must still dispatch through cloud MQTT."""
    coordinator = _command_coordinator()
    ble_mock = AsyncMock(
        side_effect=HomeAssistantError("ESPHome Bluetooth proxy unavailable"),
    )
    publish_mock = AsyncMock()
    coordinator_any = cast("Any", coordinator)
    coordinator_any.async_send_ble_command = ble_mock
    _set_test_attr(coordinator, "_async_publish_command", publish_mock)

    await coordinator._async_publish_command_ble_first(  # ruff: ignore[private-member-access]
        "device-1",
        message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        action_id=ACTION_ID_EPS_ENABLED,
        cmd=MQTT_CMD_DEVICE_PROPERTY_CHANGE,
        body_fields={FIELD_SW_EPS: 1},
    )

    publish_mock.assert_awaited_once()
    await_args = publish_mock.await_args
    assert await_args is not None
    assert await_args.args == ("device-1",)
    assert await_args.kwargs["message_type"] == MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE
    assert await_args.kwargs["action_id"] == ACTION_ID_EPS_ENABLED
    assert await_args.kwargs["cmd"] == MQTT_CMD_DEVICE_PROPERTY_CHANGE
    assert await_args.kwargs["body_fields"] == {FIELD_SW_EPS: 1}
    assert await_args.kwargs["ensure_mqtt"] is True


def test_local_metadata_does_not_hide_top_level_live_body() -> None:
    """messageType/actionId on body-only LAN JSON must not drop live fields."""
    normalized = normalize_local_mqtt_payload({
        FIELD_MESSAGE_TYPE: "UploadCombineData",
        FIELD_ACTION_ID: 3012,
        FIELD_DEVICE_ID: "dev-1",
        FIELD_PV_PW: _LIVE_PV_W,
    })

    assert normalized[FIELD_DEVICE_ID] == "dev-1"
    assert normalized["body"][FIELD_PV_PW] == _LIVE_PV_W


async def test_body_only_local_mqtt_routes_each_live_control_and_pv_field() -> None:
    """A single-field LAN frame must reach properties without envelope metadata."""
    field_values: tuple[tuple[str, object], ...] = (
        (FIELD_CHARGE_PLAN_PW, 800),
        (FIELD_ENERGY_PLAN_PW, 700),
        (FIELD_SW_EPS, 1),
        (FIELD_SW_EPS_STATE, 1),
        (FIELD_PV1, {"pw": _LIVE_PV_W}),
    )

    for field, value in field_values:
        coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
        coordinator.data = {"dev-1": {PAYLOAD_PROPERTIES: {}}}
        coordinator._device_index = {"dev-1": {}}  # ruff: ignore[private-member-access]
        coordinator._property_overrides = {}  # ruff: ignore[private-member-access]
        coordinator._last_property_push_monotonic = float("-inf")  # ruff: ignore[private-member-access]
        _set_test_attr(coordinator, "_live_property_received_monotonic", {})
        coordinator._local_mqtt_last_message_monotonic = float("-inf")  # ruff: ignore[private-member-access]
        coordinator._local_mqtt_last_device_message_monotonic = {}  # ruff: ignore[private-member-access]
        captured: dict[str, dict[str, Any]] = {}

        async def _debug_event(  # ruff: ignore[unused-async]
            _event_or_factory: object,
        ) -> None:
            return

        _set_test_attr(coordinator, "_async_payload_debug_event", _debug_event)
        _set_test_attr(coordinator, "_push_partial_update", captured.update)
        _set_test_attr(
            coordinator,
            "_schedule_battery_pack_ota_enrichment",
            lambda _device_id: None,
        )

        await coordinator.async_handle_local_mqtt_message(
            "jackery/live",
            {FIELD_DEVICE_ID: "dev-1", field: value},
        )

        assert captured["dev-1"][PAYLOAD_PROPERTIES][field] == value


def test_stale_shelly_cache_fills_but_does_not_replace_live_ct() -> None:
    """A cached Shelly response may add fields but not reverse live CT power."""
    entry = {
        PAYLOAD_CT_METER: {
            FIELD_DEVICE_SN: "5c013b048e3c",
            FIELD_CT_POWER: _LIVE_CT_POWER_W,
        },
    }

    assert merge_shelly_cloud_item(
        entry,
        {
            FIELD_DEVICE_SN: "5c013b048e3c",
            FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_CT,
            FIELD_CT_POWER: _SHELLY_CT_POWER_W,
            FIELD_CT_VOLT: _CT_VOLTAGE_V,
        },
        fill_only=True,
    )
    assert entry[PAYLOAD_CT_METER][FIELD_CT_POWER] == _LIVE_CT_POWER_W
    assert entry[PAYLOAD_CT_METER][FIELD_CT_VOLT] == _CT_VOLTAGE_V


def test_shelly_ip_address_is_not_copied_into_input_power() -> None:
    """Shelly network metadata and portable PowerBody ``ip`` must not collide."""
    normalized = normalize_shelly_cloud_payload({FIELD_IP: "192.168.2.109"})

    assert normalized[FIELD_IP] == "192.168.2.109"
    assert FIELD_IN_PW not in normalized
