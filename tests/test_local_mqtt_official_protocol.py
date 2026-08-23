"""Official Jackery LAN MQTT protocol regressions."""

from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

_DEVICE_ID = "device-1"
_DEVICE_SN = "SV3PM123456"
_TOKEN = "123456789012"


def _coordinator_shell() -> JackerySolarVaultCoordinator:
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._device_index = {_DEVICE_ID: {"device_meta": {"deviceSn": _DEVICE_SN}}}
    coordinator.data = {
        _DEVICE_ID: {
            "device": {"deviceSn": _DEVICE_SN},
            "properties": {"batSoc": 40},
        }
    }
    coordinator._local_mqtt_last_message_monotonic = float("-inf")
    coordinator._local_mqtt_last_device_message_monotonic = {}
    coordinator._local_mqtt_any_traffic_observed_ids = set()
    coordinator._local_mqtt_head_traffic_observed_ids = set()
    coordinator._local_mqtt_lifetime_traffic_observed_ids = set()
    coordinator._local_mqtt_device_traffic_observed = False
    coordinator._local_mqtt_device_traffic_observed_ids = set()
    coordinator._shutdown_started = False
    cast("Any", coordinator)._local_mqtt_device_token = lambda _device_id: _TOKEN
    return coordinator


@pytest.mark.asyncio
async def test_topic_serial_is_injected_before_shared_ingest() -> None:
    """Flat status frames are bound to the host serial carried by the topic."""
    coordinator = _coordinator_shell()
    handler = AsyncMock(return_value=_DEVICE_ID)
    cast("Any", coordinator)._async_handle_mqtt_message = handler

    assert await coordinator.async_handle_local_mqtt_message(
        f"hb/device/{_DEVICE_SN}/status",
        {"batSoc": 55},
    )

    assert handler.await_args is not None
    normalized = handler.await_args.args[1]
    assert normalized["deviceSn"] == _DEVICE_SN
    assert normalized["body"]["batSoc"] == 55


@pytest.mark.asyncio
async def test_plural_topic_serial_is_injected_before_shared_ingest() -> None:
    """Plural broker topics bind the same host serial before shared ingest."""
    coordinator = _coordinator_shell()
    handler = AsyncMock(return_value=_DEVICE_ID)
    cast("Any", coordinator)._async_handle_mqtt_message = handler

    assert await coordinator.async_handle_local_mqtt_message(
        f"hb/devices/{_DEVICE_SN}/event",
        {"batSoc": 55},
    )

    assert handler.await_args is not None
    normalized = handler.await_args.args[1]
    assert normalized["deviceSn"] == _DEVICE_SN
    assert normalized["body"]["batSoc"] == 55


@pytest.mark.asyncio
async def test_official_type_2_status_reaches_shared_live_ingest() -> None:
    """The status snapshots seen in the live log are accepted as telemetry."""
    coordinator = _coordinator_shell()
    raw_coordinator = cast("Any", coordinator)
    raw_coordinator._async_payload_debug_event = AsyncMock()
    raw_coordinator._resolve_device_id_from_mqtt = MagicMock(return_value=_DEVICE_ID)
    raw_coordinator._transport_partial_update_base = MagicMock(
        return_value=coordinator.data[_DEVICE_ID]
    )
    raw_coordinator._merge_main_properties_for_device = MagicMock(
        return_value={"batSoc": 55, "pvPw": 1234}
    )
    push_partial_update = MagicMock()
    raw_coordinator._push_partial_update = push_partial_update
    raw_coordinator._schedule_battery_pack_ota_enrichment = MagicMock()

    assert await coordinator.async_handle_local_mqtt_message(
        f"hb/device/{_DEVICE_SN}/status",
        {
            "deviceSn": _DEVICE_SN,
            "type": 2,
            "body": {"cmd": 106, "batSoc": 55, "pvPw": 1234},
        },
    )
    push_partial_update.assert_called_once()


@pytest.mark.asyncio
async def test_unchanged_official_response_refreshes_local_mqtt_liveness() -> None:
    """A valid no-op response is accepted even when no entity value changed."""
    coordinator = _coordinator_shell()
    raw_coordinator = cast("Any", coordinator)
    raw_coordinator._async_payload_debug_event = AsyncMock()
    raw_coordinator._resolve_device_id_from_mqtt = MagicMock(return_value=_DEVICE_ID)
    raw_coordinator._transport_partial_update_base = MagicMock(
        return_value=coordinator.data[_DEVICE_ID]
    )

    assert await coordinator.async_handle_local_mqtt_message(
        f"hb/device/{_DEVICE_SN}/event",
        {
            "deviceSn": _DEVICE_SN,
            "type": 101,
            "body": {"cmd": 110, "devType": 6, "plugs": []},
        },
    )
    assert coordinator._local_mqtt_device_traffic_observed is True
    assert coordinator._local_mqtt_head_traffic_observed_ids == {_DEVICE_ID}
    assert coordinator._local_mqtt_lifetime_traffic_observed_ids == set()
    assert coordinator._local_mqtt_last_message_monotonic > 0
    assert coordinator._local_mqtt_last_device_message_monotonic[_DEVICE_ID] > 0


@pytest.mark.asyncio
@pytest.mark.parametrize("dev_type", [1, 2, 6])
async def test_unchanged_type_23_lifetime_snapshot_is_accepted(
    dev_type: int,
) -> None:
    """Valid lifetime snapshots remain accepted when values did not change."""
    coordinator = _coordinator_shell()
    raw_coordinator = cast("Any", coordinator)
    raw_coordinator._async_payload_debug_event = AsyncMock()
    raw_coordinator._resolve_device_id_from_mqtt = MagicMock(return_value=_DEVICE_ID)
    raw_coordinator._transport_partial_update_base = MagicMock(
        return_value=coordinator.data[_DEVICE_ID]
    )
    raw_coordinator._async_handle_mqtt_message = AsyncMock(return_value=None)

    assert await coordinator.async_handle_local_mqtt_message(
        f"hb/device/{_DEVICE_SN}/event",
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
    assert coordinator._local_mqtt_device_traffic_observed is True
    assert coordinator._local_mqtt_head_traffic_observed_ids == {_DEVICE_ID}
    assert coordinator._local_mqtt_lifetime_traffic_observed_ids == {_DEVICE_ID}
    assert coordinator._local_mqtt_last_device_message_monotonic[_DEVICE_ID] > 0


@pytest.mark.asyncio
async def test_foreign_topic_serial_is_rejected() -> None:
    """A broad broker subscription cannot mix another Jackery host into this entry."""
    coordinator = _coordinator_shell()
    handler = AsyncMock(return_value=_DEVICE_ID)
    cast("Any", coordinator)._async_handle_mqtt_message = handler

    assert not await coordinator.async_handle_local_mqtt_message(
        "hb/device/OTHER-SERIAL/event",
        {"type": 107, "body": {"soc": 10}},
    )
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_poll_publishes_official_request_family() -> None:
    """The listener actively requests host, system, settings and child data."""
    coordinator = _coordinator_shell()
    client = JackeryLocalMqttClient.__new__(JackeryLocalMqttClient)
    client._connected = True
    publish = AsyncMock()
    cast("Any", client).async_publish = publish
    coordinator._local_mqtt_client = client

    sent = await coordinator.async_poll_local_mqtt_devices("hb")

    assert sent == 6
    calls = publish.await_args_list
    assert {call.args[1]["type"] for call in calls} == {2, 25, 100, 105}
    assert [
        call.args[1]["body"]["devType"] for call in calls if call.args[1]["type"] == 100
    ] == [1, 2, 6]
    assert all(call.args[0] == f"hb/device/{_DEVICE_SN}/action" for call in calls)
    assert all(call.args[1]["token"] == _TOKEN for call in calls)
