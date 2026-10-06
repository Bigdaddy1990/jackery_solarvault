"""Official Jackery LAN MQTT protocol regressions."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
    TransportSource,
    local_mqtt_topic_device_serial,
    normalize_local_mqtt_payload,
)

_DEVICE_ID = "device-1"
_DEVICE_SN = "SV3PM123456"
_TOKEN = "123456789012"


@pytest.mark.parametrize("field", ["body", "data"])
@pytest.mark.parametrize("value", [[{"deviceSn": "PACK-3", "cellTemp": 274}], None])
def test_local_normalization_preserves_non_object_payload_fields(
    field: str, value: object
) -> None:
    """Normalization must not delete list-shaped App bodies or explicit nulls."""
    payload = {"deviceSn": _DEVICE_SN, field: value, "devType": 1}
    normalized = normalize_local_mqtt_payload(payload)
    assert normalized["body"][field] == value
    assert payload[field] == value


@pytest.mark.parametrize("field", ["body", "data"])
def test_local_pack_list_body_reaches_pack_extraction(field: str) -> None:
    """A typed pack list keeps its own serial and cellTemp through ingest."""
    pack = {"deviceSn": "PACK-3", "cellTemp": 274, "batSoc": 40}
    normalized = normalize_local_mqtt_payload({
        "deviceSn": _DEVICE_SN,
        "devType": 1,
        field: [pack],
    })
    assert JackerySolarVaultCoordinator._battery_packs_from_source(  # ruff: ignore[private-member-access]
        normalized["body"]
    ) == [pack]


def _coordinator_shell() -> JackerySolarVaultCoordinator:
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._device_index = {_DEVICE_ID: {"device_meta": {"deviceSn": _DEVICE_SN}}}  # ruff: ignore[private-member-access]
    coordinator.data = {
        _DEVICE_ID: {
            "device": {"deviceSn": _DEVICE_SN},
            "properties": {"batSoc": 40},
        }
    }
    coordinator._local_mqtt_last_message_monotonic = float("-inf")  # ruff: ignore[private-member-access]
    coordinator._local_mqtt_last_device_message_monotonic = {}  # ruff: ignore[private-member-access]
    coordinator._local_mqtt_any_traffic_observed_ids = set()  # ruff: ignore[private-member-access]
    coordinator._local_mqtt_head_traffic_observed_ids = set()  # ruff: ignore[private-member-access]
    coordinator._local_mqtt_lifetime_traffic_observed_ids = set()  # ruff: ignore[private-member-access]
    coordinator._local_mqtt_device_traffic_observed = False  # ruff: ignore[private-member-access]
    coordinator._local_mqtt_device_traffic_observed_ids = set()  # ruff: ignore[private-member-access]
    coordinator._shutdown_started = False  # ruff: ignore[private-member-access]
    cast("Any", coordinator)._local_mqtt_device_token = lambda _device_id: _TOKEN  # ruff: ignore[private-member-access]
    return coordinator  # pyrefly: ignore [no-any-return-implicit]


def test_local_alarm_fields_route_without_cloud_command_metadata() -> None:
    """The App HomeAlarmBody fields remain readable in a LAN report body."""
    coordinator = _coordinator_shell()
    context = coordinator._mqtt_route_context(  # ruff: ignore[private-member-access]
        f"hb/device/{_DEVICE_SN}/status",
        {
            "deviceSn": _DEVICE_SN,
            "body": {"sysAlertCount": 2, "alarmId": "402270", "batSoc": 40},
        },
        TransportSource.LOCAL_MQTT,
    )
    assert context is not None
    updated: dict[str, Any] = {}
    assert coordinator._apply_mqtt_app_shadow_updates(context, updated)  # ruff: ignore[private-member-access]
    assert updated["device_alert"]["alarmId"] == "402270"
    assert updated["device_alert"]["sysAlertCount"] == 2  # ruff: ignore[magic-value-comparison]
    assert updated["device_alert"]["batSoc"] == 40  # ruff: ignore[magic-value-comparison]
    assert context.is_alarm is False


def test_body_only_official_status_is_a_property_snapshot() -> None:
    """The documented LAN topic identifies a host report without cloud type."""
    coordinator = _coordinator_shell()
    payload = coordinator._normalize_local_mqtt_payload(  # ruff: ignore[private-member-access]
        {"batSoc": 55, "cellTemp": 274}, f"hb/device/{_DEVICE_SN}/status"
    )
    assert payload is not None
    context = coordinator._mqtt_route_context(  # ruff: ignore[private-member-access]
        f"hb/device/{_DEVICE_SN}/status", payload, TransportSource.LOCAL_MQTT
    )
    assert context is not None
    assert coordinator._mqtt_is_device_property_snapshot(context)  # ruff: ignore[private-member-access]


def test_app_mqtt_subdevices_keeps_pack_identity_and_soc() -> None:
    """App MqttBody.subDevices carries BatteryPackBody rb/ip/op fields."""
    coordinator = _coordinator_shell()
    body = {"subDevices": [{"deviceSn": "PACK-3", "rb": 45, "ip": 100, "op": 0}]}
    payload = coordinator._normalize_local_mqtt_payload(  # ruff: ignore[private-member-access]
        body, f"hb/device/{_DEVICE_SN}/status"
    )
    assert payload is not None
    context = coordinator._mqtt_route_context(  # ruff: ignore[private-member-access]
        f"hb/device/{_DEVICE_SN}/status", payload, TransportSource.LOCAL_MQTT
    )
    assert context is not None
    assert context.is_subdevice is False
    merge = MagicMock(return_value=True)
    cast("Any", coordinator)._merge_subdevice_data = merge  # ruff: ignore[private-member-access]
    assert coordinator._apply_mqtt_subdevice_update(context, {})  # ruff: ignore[private-member-access]
    assert merge.call_args.args[1] == context.body
    packs = coordinator._battery_packs_from_source(context.body)  # ruff: ignore[private-member-access]
    assert packs is not None
    assert packs[0]["deviceSn"] == "PACK-3"
    assert packs[0]["batSoc"] == 45  # ruff: ignore[magic-value-comparison]
    assert packs[0]["inPw"] == 100  # ruff: ignore[magic-value-comparison]
    assert packs[0]["outPw"] == 0


@pytest.mark.parametrize("children", [[], [{"deviceSn": "PACK-3", "rb": 45}]])
def test_app_mqtt_subdevices_does_not_hide_head_fields(
    children: list[dict[str, Any]],
) -> None:
    """An accessory container cannot suppress genuine top-level head values."""
    coordinator = _coordinator_shell()
    payload = coordinator._normalize_local_mqtt_payload(  # ruff: ignore[private-member-access]
        {"batInPw": 120, "batSoc": 55, "subDevices": children},
        f"hb/device/{_DEVICE_SN}/status",
    )
    assert payload is not None
    context = coordinator._mqtt_route_context(  # ruff: ignore[private-member-access]
        f"hb/device/{_DEVICE_SN}/status", payload, TransportSource.LOCAL_MQTT
    )
    assert context is not None
    assert coordinator._mqtt_is_device_property_snapshot(context)  # ruff: ignore[private-member-access]


@pytest.mark.parametrize(
    "payload",
    [
        {"body": {"name": "ESP32 IP Adresse", "stat_t": "esp32/sensor/ip/state"}},
        {"deviceId": "foreign-device", "body": {"soc": 10}},
        {"type": 2, "body": {"soc": 10}},
    ],
)
def test_unidentified_local_frames_cannot_claim_the_only_jackery(
    payload: dict[str, Any],
) -> None:
    """A shared broker cannot assign unrelated traffic to the sole known host."""
    coordinator = _coordinator_shell()
    assert (
        coordinator._mqtt_route_context(  # ruff: ignore[private-member-access]
            "homeassistant/sensor/esp32/ip/config", payload, TransportSource.LOCAL_MQTT
        )
        is None
    )


@pytest.mark.asyncio()
async def test_topic_serial_is_injected_before_shared_ingest() -> None:
    """Flat status frames are bound to the host serial carried in the payload."""
    coordinator = _coordinator_shell()
    handler = AsyncMock(return_value=_DEVICE_ID)
    coordinator.async_handle_mqtt_message = handler

    # New protocol: topic is hb/app/<userId>/device, serial in payload
    assert await coordinator.async_handle_local_mqtt_message(
        "hb/app/user123/device",
        {"batSoc": 55, "deviceSn": _DEVICE_SN},
    )

    assert handler.await_args is not None
    normalized = handler.await_args.args[1]
    assert normalized["deviceSn"] == _DEVICE_SN
    assert normalized["body"]["batSoc"] == 55  # ruff: ignore[magic-value-comparison]


@pytest.mark.asyncio()
async def test_device_topic_serial_routes_body_only_report() -> None:
    """An exact device topic identifies a body-only LAN report."""
    coordinator = _coordinator_shell()
    handler = AsyncMock(return_value=_DEVICE_ID)
    coordinator.async_handle_mqtt_message = handler

    assert await coordinator.async_handle_local_mqtt_message(
        f"hb/device/{_DEVICE_SN}/status", {"type": 2, "body": {"batSoc": 55}}
    )
    # pyrefly: ignore [missing-attribute]
    assert handler.await_args.args[1]["deviceSn"] == _DEVICE_SN
    assert (
        local_mqtt_topic_device_serial(f"hb/device/{_DEVICE_SN}/status") == _DEVICE_SN
    )
    assert local_mqtt_topic_device_serial("hb/app/user123/device") is None
    assert (
        local_mqtt_topic_device_serial(f"other/hb/device/{_DEVICE_SN}/status") is None
    )


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    "payload",
    [
        {"deviceSn": "FOREIGN", "type": 2, "body": {"batSoc": 55}},
        {"deviceId": "other-device", "type": 2, "body": {"batSoc": 55}},
        {"deviceId": "unregistered-device", "type": 2, "body": {"batSoc": 55}},
        {"deviceSn": "UNKNOWN-PACK", "type": 107, "cellTemp": 274},
        {"deviceSn": "UNKNOWN-PACK", "type": 107, "updates": {"cellTemp": 274}},
        {"deviceSn": "FOREIGN-PACK", "type": 107, "updates": {"cellTemp": 274}},
        {
            "deviceSn": "PACK-3",
            "type": 107,
            "updates": {"deviceSn": "FOREIGN-PACK", "cellTemp": 274},
        },
        {
            "deviceSn": "PACK-3",
            "deviceId": "other-device",
            "type": 107,
            "updates": {"cellTemp": 274},
        },
        {
            "deviceSn": "PACK-3",
            "deviceId": "unregistered-device",
            "type": 107,
            "updates": {"cellTemp": 274},
        },
        {
            "deviceSn": "PACK-3",
            "devType": 0,
            "type": 107,
            "updates": {"cellTemp": 274},
        },
    ],
)
async def test_device_topic_rejects_conflicting_payload_identity(
    payload: dict[str, Any],
) -> None:
    """A shared broker cannot reassign a conflicting device frame."""
    coordinator = _coordinator_shell()
    coordinator._device_index["other-device"] = {"device_meta": {"deviceSn": "FOREIGN"}}  # ruff: ignore[private-member-access]
    coordinator.data[_DEVICE_ID]["battery_packs"] = [{"deviceSn": "PACK-3"}]
    coordinator.data["other-device"] = {
        "device": {"deviceSn": "FOREIGN"},
        "battery_packs": [{"deviceSn": "FOREIGN-PACK"}],
    }
    coordinator.async_handle_mqtt_message = AsyncMock()

    assert not await coordinator.async_handle_local_mqtt_message(
        f"hb/device/{_DEVICE_SN}/status", payload
    )
    coordinator.async_handle_mqtt_message.assert_not_awaited()


@pytest.mark.asyncio()
async def test_device_topic_accepts_matching_nested_pack_identity() -> None:
    """Repeated matching pack identity does not hide nested measurements."""
    coordinator = _coordinator_shell()
    coordinator.data[_DEVICE_ID]["battery_packs"] = [{"deviceSn": "PACK-3"}]
    handler = AsyncMock(return_value=_DEVICE_ID)
    coordinator.async_handle_mqtt_message = handler
    updates = {"deviceSn": "PACK-3", "cellTemp": 274, "outPw": 0}

    assert await coordinator.async_handle_local_mqtt_message(
        f"hb/device/{_DEVICE_SN}/event",
        {"deviceSn": "PACK-3", "type": 107, "updates": updates},
    )

    assert handler.await_args is not None
    normalized = handler.await_args.args[1]
    assert normalized["deviceSn"] == _DEVICE_SN
    assert normalized["body"]["deviceSn"] == "PACK-3"
    assert normalized["body"]["updates"] == updates


@pytest.mark.asyncio()
async def test_rejected_local_frame_keeps_complete_ingress_evidence() -> None:
    """Identity rejection must not hide fields from the raw payload audit."""
    coordinator = _coordinator_shell()
    capture = MagicMock()
    cast("Any", coordinator)._schedule_payload_debug_event = capture  # ruff: ignore[private-member-access]
    payload = {
        "deviceSn": "FOREIGN-HEAD",
        "devType": 0,
        "cellTemp": 274,
        "inEgy": 36435,
        "outEgy": 34652,
    }

    assert not await coordinator.async_handle_local_mqtt_message(
        f"hb/device/{_DEVICE_SN}/event", payload
    )

    capture.assert_called_once()
    event = capture.call_args.args[0]()
    assert event["kind"] == "local_mqtt_ingress"
    assert event["payload"] == payload


@pytest.mark.asyncio()
async def test_foreign_discovery_message_is_not_device_traffic() -> None:
    """A retained HA discovery payload remains visible as an unrouted frame."""
    coordinator = _coordinator_shell()
    coordinator.async_handle_mqtt_message = AsyncMock(return_value=None)

    assert not await coordinator.async_handle_local_mqtt_message(
        "homeassistant/sensor/other/config", {"name": "unrelated"}
    )
    assert coordinator._local_mqtt_rejection_reasons == {"unsupported_report": 1}  # ruff: ignore[private-member-access]


@pytest.mark.asyncio()
async def test_plural_topic_serial_is_injected_before_shared_ingest() -> None:
    """Plural broker topics bind the same host serial before shared ingest."""
    coordinator = _coordinator_shell()
    handler = AsyncMock(return_value=_DEVICE_ID)
    coordinator.async_handle_mqtt_message = handler

    assert await coordinator.async_handle_local_mqtt_message(
        "hb/app/user123/device",
        {"batSoc": 55, "deviceSn": _DEVICE_SN},
    )

    assert handler.await_args is not None
    normalized = handler.await_args.args[1]
    assert normalized["deviceSn"] == _DEVICE_SN
    assert normalized["body"]["batSoc"] == 55  # ruff: ignore[magic-value-comparison]


@pytest.mark.asyncio()
async def test_official_type_2_status_reaches_shared_live_ingest() -> None:
    """The status snapshots seen in the live log are accepted as telemetry."""
    coordinator = _coordinator_shell()
    raw_coordinator = cast("Any", coordinator)
    raw_coordinator._async_payload_debug_event = AsyncMock()  # ruff: ignore[private-member-access]
    raw_coordinator._resolve_device_id_from_mqtt = MagicMock(return_value=_DEVICE_ID)  # ruff: ignore[private-member-access]
    raw_coordinator._transport_partial_update_base = MagicMock(  # ruff: ignore[private-member-access]
        return_value=coordinator.data[_DEVICE_ID]
    )
    raw_coordinator._merge_main_properties_for_device = MagicMock(  # ruff: ignore[private-member-access]
        return_value={"batSoc": 55, "pvPw": 1234}
    )
    push_partial_update = MagicMock()
    raw_coordinator._push_partial_update = push_partial_update  # ruff: ignore[private-member-access]
    raw_coordinator._schedule_battery_pack_ota_enrichment = MagicMock()  # ruff: ignore[private-member-access]

    assert await coordinator.async_handle_local_mqtt_message(
        f"hb/device/{_DEVICE_SN}/status",
        {
            "deviceSn": _DEVICE_SN,
            "type": 2,
            "body": {"cmd": 106, "batSoc": 55, "pvPw": 1234},
        },
    )
    push_partial_update.assert_called_once()


@pytest.mark.asyncio()
async def test_unchanged_official_response_refreshes_local_mqtt_liveness() -> None:
    """A valid no-op response is accepted even when no entity value changed."""
    coordinator = _coordinator_shell()
    raw_coordinator = cast("Any", coordinator)
    raw_coordinator._async_payload_debug_event = AsyncMock()  # ruff: ignore[private-member-access]
    raw_coordinator._resolve_device_id_from_mqtt = MagicMock(return_value=_DEVICE_ID)  # ruff: ignore[private-member-access]
    raw_coordinator._transport_partial_update_base = MagicMock(  # ruff: ignore[private-member-access]
        return_value=coordinator.data[_DEVICE_ID]
    )

    assert await coordinator.async_handle_local_mqtt_message(
        "hb/app/user123/device",
        {
            "deviceSn": _DEVICE_SN,
            "type": 101,
            "body": {"cmd": 110, "devType": 6, "plugs": []},
        },
    )
    assert coordinator._local_mqtt_device_traffic_observed is True  # ruff: ignore[private-member-access]
    assert coordinator._local_mqtt_head_traffic_observed_ids == {_DEVICE_ID}  # ruff: ignore[private-member-access]
    assert coordinator._local_mqtt_lifetime_traffic_observed_ids == set()  # ruff: ignore[private-member-access]
    assert coordinator._local_mqtt_last_message_monotonic > 0  # ruff: ignore[private-member-access]
    assert coordinator._local_mqtt_last_device_message_monotonic[_DEVICE_ID] > 0  # ruff: ignore[private-member-access]


@pytest.mark.asyncio()
@pytest.mark.parametrize("dev_type", [1, 2, 6])
async def test_unchanged_type_23_lifetime_snapshot_is_accepted(
    dev_type: int,
) -> None:
    """Valid lifetime snapshots remain accepted when values did not change."""
    coordinator = _coordinator_shell()
    raw_coordinator = cast("Any", coordinator)
    raw_coordinator._async_payload_debug_event = AsyncMock()  # ruff: ignore[private-member-access]
    raw_coordinator._resolve_device_id_from_mqtt = MagicMock(return_value=_DEVICE_ID)  # ruff: ignore[private-member-access]
    raw_coordinator._transport_partial_update_base = MagicMock(  # ruff: ignore[private-member-access]
        return_value=coordinator.data[_DEVICE_ID]
    )
    raw_coordinator.async_handle_mqtt_message = AsyncMock(return_value=None)

    assert await coordinator.async_handle_local_mqtt_message(
        "hb/app/user123/device",
        {
            "deviceSn": _DEVICE_SN,
            "type": 23,
            "body": {
                "cmd": 110,
                "devType": dev_type,
                "batChgEgy": 58871,
                "batDisChgEgy": 55870,
            },
        },
    )
    assert coordinator._local_mqtt_device_traffic_observed is True  # ruff: ignore[private-member-access]
    assert coordinator._local_mqtt_head_traffic_observed_ids == {_DEVICE_ID}  # ruff: ignore[private-member-access]
    assert coordinator._local_mqtt_lifetime_traffic_observed_ids == {_DEVICE_ID}  # ruff: ignore[private-member-access]
    assert coordinator._local_mqtt_last_device_message_monotonic[_DEVICE_ID] > 0  # ruff: ignore[private-member-access]


@pytest.mark.asyncio()
async def test_topic_serial_is_not_used_as_broker_filter() -> None:
    """A broad subscription forwards every payload to shared device routing."""
    coordinator = _coordinator_shell()
    handler = AsyncMock(return_value=_DEVICE_ID)
    coordinator.async_handle_mqtt_message = handler

    assert await coordinator.async_handle_local_mqtt_message(
        "hb/app/user123/device",
        {"type": 107, "body": {"soc": 10}, "deviceSn": _DEVICE_SN},
    )
    handler.assert_awaited_once()
    assert getattr(coordinator, "_local_mqtt_rejection_reasons", {}) == {}


@pytest.mark.asyncio()
async def test_local_poll_publishes_official_request_family() -> None:
    """The listener actively requests host, system, settings and child data."""
    coordinator = _coordinator_shell()
    client = JackeryLocalMqttClient.__new__(JackeryLocalMqttClient)
    client._connected = True  # ruff: ignore[private-member-access]
    publish = AsyncMock()
    cast("Any", client).async_publish = publish
    coordinator._local_mqtt_client = client  # ruff: ignore[private-member-access]

    sent = await coordinator.async_poll_local_mqtt_devices("hb/app/user123")

    assert sent == 7  # ruff: ignore[magic-value-comparison]
    calls = publish.await_args_list
    assert {call.args[1]["type"] for call in calls} == {2, 25, 100, 105}
    assert [
        call.args[1]["body"]["devType"]
        for call in calls
        if call.args[1]["type"] == 100  # ruff: ignore[magic-value-comparison]
    ] == [1, 2, 5, 6]
    assert all(
        call.args[0] == f"hb/app/user123/device/{_DEVICE_SN}/action" for call in calls
    )
    assert all(call.args[1]["token"] == _TOKEN for call in calls)
