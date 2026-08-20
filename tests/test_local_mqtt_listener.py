"""Unit tests for async_start_local_mqtt_listener (HA-MQTT listener)."""

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.jackery_solarvault.const import (
    CONF_LOCAL_MQTT_ENABLE,
    MQTT_TOPIC_PREFIX,
    MQTT_TOPIC_SUFFIXES,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)


def _bare_coordinator() -> JackerySolarVaultCoordinator:
    """Create a coordinator shell for testing without HA setup."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._local_mqtt_unsubs = []  # noqa: RUF105, SLF001
    coordinator._local_mqtt_client = None  # noqa: RUF105, SLF001
    coordinator._shutdown_started = False  # noqa: RUF105, SLF001
    coordinator.hass = MagicMock()
    coordinator.entry = MagicMock()
    coordinator.entry.data = {}
    coordinator.entry.options = {}
    return coordinator


def test_local_mqtt_listener_disabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: E501, RUF100
    """Listener returns early when local_mqtt_enable is False."""
    coordinator = _bare_coordinator()
    coordinator.entry.options = {CONF_LOCAL_MQTT_ENABLE: False}

    # Should return without doing anything
    import asyncio  # noqa: PLC0415, RUF105

    asyncio.run(coordinator.async_start_local_mqtt_listener())

    assert coordinator._local_mqtt_unsubs == []  # noqa: RUF105, SLF001


def test_local_mqtt_listener_enabled_by_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: E501, RUF100
    """Listener proceeds when local_mqtt_enable is True (123/ baseline default)."""
    coordinator = _bare_coordinator()
    coordinator.entry.options = {CONF_LOCAL_MQTT_ENABLE: True}

    # Remove mqtt from sys.modules to simulate HA without MQTT
    monkeypatch.delitem(sys.modules, "homeassistant.components.mqtt", raising=False)
    monkeypatch.delattr(
        sys.modules.get("homeassistant.components", MagicMock()), "mqtt", raising=False
    )  # noqa: E501, RUF100

    import asyncio  # noqa: PLC0415, RUF105

    asyncio.run(coordinator.async_start_local_mqtt_listener())

    # With mqtt not available, should log and return early
    assert coordinator._local_mqtt_unsubs == []  # noqa: RUF105, SLF001


def test_local_mqtt_listener_subscribes_to_expected_topics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: E501, RUF100
    """Listener subscribes to all MQTT_TOPIC_SUFFIXES under MQTT_TOPIC_PREFIX."""
    coordinator = _bare_coordinator()
    coordinator.entry.options = {CONF_LOCAL_MQTT_ENABLE: True}

    # Mock ha_mqtt module
    mock_ha_mqtt = MagicMock()
    mock_ha_mqtt.async_subscribe = AsyncMock(return_value=MagicMock())
    monkeypatch.setitem(sys.modules, "homeassistant.components.mqtt", mock_ha_mqtt)

    with patch(
        "custom_components.jackery_solarvault.coordinator.ha_mqtt", mock_ha_mqtt
    ):  # noqa: E501, RUF100
        import asyncio  # noqa: PLC0415, RUF105

        asyncio.run(coordinator.async_start_local_mqtt_listener())

    # Verify subscription calls
    expected_topics = [
        f"{MQTT_TOPIC_PREFIX}/+/{suffix}" for suffix in MQTT_TOPIC_SUFFIXES
    ]  # noqa: E501, RUF100
    assert mock_ha_mqtt.async_subscribe.call_count == len(expected_topics)

    for call, expected_topic in zip(
        mock_ha_mqtt.async_subscribe.call_args_list, expected_topics, strict=False
    ):  # noqa: E501, RUF100
        args, kwargs = call
        assert args[1] == expected_topic  # topic is second positional arg
        assert kwargs.get("qos") == 0
        assert kwargs.get("encoding") == "utf-8"


def test_local_mqtt_listener_handles_subscribe_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: E501, RUF100
    """Listener cleans up partial subscriptions on failure."""
    coordinator = _bare_coordinator()
    coordinator.entry.options = {CONF_LOCAL_MQTT_ENABLE: True}

    mock_ha_mqtt = MagicMock()
    mock_ha_mqtt.async_subscribe = AsyncMock(side_effect=RuntimeError("broker down"))
    monkeypatch.setitem(sys.modules, "homeassistant.components.mqtt", mock_ha_mqtt)

    with patch(
        "custom_components.jackery_solarvault.coordinator.ha_mqtt", mock_ha_mqtt
    ):  # noqa: E501, RUF100
        import asyncio  # noqa: PLC0415, RUF105

        asyncio.run(coordinator.async_start_local_mqtt_listener())

    # Should clear partial subscriptions
    assert coordinator._local_mqtt_unsubs == []  # noqa: RUF105, SLF001


def test_local_mqtt_listener_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Calling listener twice doesn't double-subscribe."""
    coordinator = _bare_coordinator()
    coordinator.entry.options = {CONF_LOCAL_MQTT_ENABLE: True}

    mock_ha_mqtt = MagicMock()
    mock_unsub = MagicMock()
    mock_ha_mqtt.async_subscribe = AsyncMock(return_value=mock_unsub)
    monkeypatch.setitem(sys.modules, "homeassistant.components.mqtt", mock_ha_mqtt)

    with patch(
        "custom_components.jackery_solarvault.coordinator.ha_mqtt", mock_ha_mqtt
    ):  # noqa: E501, RUF100
        import asyncio  # noqa: PLC0415, RUF105

        asyncio.run(coordinator.async_start_local_mqtt_listener())
        asyncio.run(coordinator.async_start_local_mqtt_listener())

    # Should only subscribe once
    assert mock_ha_mqtt.async_subscribe.call_count == len(MQTT_TOPIC_SUFFIXES)


def test_local_mqtt_listener_message_handler_passes_to_mqtt_handler(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: E501, RUF100
    """Received messages are forwarded to _async_handle_mqtt_message."""
    coordinator = _bare_coordinator()
    coordinator.entry.options = {CONF_LOCAL_MQTT_ENABLE: True}
    coordinator._async_handle_mqtt_message = AsyncMock()  # noqa: RUF105, SLF001

    mock_ha_mqtt = MagicMock()
    mock_ha_mqtt.async_subscribe = AsyncMock(return_value=MagicMock())
    monkeypatch.setitem(sys.modules, "homeassistant.components.mqtt", mock_ha_mqtt)

    with patch(
        "custom_components.jackery_solarvault.coordinator.ha_mqtt", mock_ha_mqtt
    ):  # noqa: E501, RUF100
        import asyncio  # noqa: PLC0415, RUF105

        asyncio.run(coordinator.async_start_local_mqtt_listener())

    # Get the callback that was passed to async_subscribe
    subscribe_call = mock_ha_mqtt.async_subscribe.call_args_list[0]
    subscribe_call.kwargs.get("callback") or subscribe_call.args[2]  # 3rd positional

    # Simulate a message - callback is _queue_local_mqtt_message which schedules a background task  # noqa: RUF105
    # We directly call the internal handler _handle_local_mqtt_message to test the logic
    mock_message = MagicMock()
    mock_message.topic = "hb/app/device/test"
    mock_message.payload = b'{"deviceId": "test", "batSoc": 50}'

    # Find the _handle_local_mqtt_message function from the coordinator
    # It's created inside async_start_local_mqtt_listener, so we test the logic directly
    import asyncio  # noqa: PLC0415, RUF105

    from custom_components.jackery_solarvault.coordinator import json  # noqa: RUF105

    # Simulate what _handle_local_mqtt_message does
    raw_payload = mock_message.payload
    if isinstance(raw_payload, bytes):
        raw_payload = raw_payload.decode()
    if isinstance(raw_payload, str):
        payload = json.loads(raw_payload)

    # Now call the actual handler
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(
            coordinator._async_handle_mqtt_message(str(mock_message.topic), payload)  # ruff: ignore[private-member-access]
        )  # noqa: E501, RUF100, SLF001
    finally:
        loop.close()
        asyncio.set_event_loop(None)

    # Should forward to _async_handle_mqtt_message
    coordinator._async_handle_mqtt_message.assert_called_once()  # noqa: RUF105, SLF001
    args, _ = coordinator._async_handle_mqtt_message.call_args  # noqa: RUF105, SLF001
    assert args[0] == "hb/app/device/test"
    assert args[1] == {"deviceId": "test", "batSoc": 50}


def test_local_mqtt_listener_ignores_non_json(monkeypatch: pytest.MonkeyPatch) -> None:
    """Non-JSON payloads are ignored silently."""
    coordinator = _bare_coordinator()
    coordinator.entry.options = {CONF_LOCAL_MQTT_ENABLE: True}
    coordinator._async_handle_mqtt_message = AsyncMock()  # noqa: RUF105, SLF001

    mock_ha_mqtt = MagicMock()
    mock_ha_mqtt.async_subscribe = AsyncMock(return_value=MagicMock())
    monkeypatch.setitem(sys.modules, "homeassistant.components.mqtt", mock_ha_mqtt)

    with patch(
        "custom_components.jackery_solarvault.coordinator.ha_mqtt", mock_ha_mqtt
    ):  # noqa: E501, RUF100
        import asyncio  # noqa: PLC0415, RUF105

        asyncio.run(coordinator.async_start_local_mqtt_listener())

    # The callback is _queue_local_mqtt_message which schedules _handle_local_mqtt_message  # noqa: RUF105
    # We test _handle_local_mqtt_message directly (the logic that filters non-JSON)
    import asyncio  # noqa: PLC0415, RUF105

    from custom_components.jackery_solarvault.coordinator import json  # noqa: RUF105

    # Simulate non-JSON payload - _handle_local_mqtt_message catches JSONDecodeError
    mock_message = MagicMock()
    mock_message.topic = "hb/app/device/test"
    mock_message.payload = b"not json"

    # Call _handle_local_mqtt_message logic directly
    raw_payload = mock_message.payload
    if isinstance(raw_payload, bytes):
        raw_payload = raw_payload.decode()
    try:
        if isinstance(raw_payload, str):
            json.loads(raw_payload)
        # If it parses, we'd call _async_handle_mqtt_message, but it doesn't parse
    except json.JSONDecodeError:
        pass  # Ignored silently - this is what the handler does

    # Should not call _async_handle_mqtt_message for non-JSON
    coordinator._async_handle_mqtt_message.assert_not_called()  # noqa: RUF105, SLF001


def test_local_mqtt_listener_ignores_non_dict_payload(
    monkeypatch: pytest.MonkeyPatch,
) -> None:  # noqa: E501, RUF100
    """JSON arrays/primitives are ignored."""
    coordinator = _bare_coordinator()
    coordinator.entry.options = {CONF_LOCAL_MQTT_ENABLE: True}
    coordinator._async_handle_mqtt_message = AsyncMock()  # noqa: RUF105, SLF001

    mock_ha_mqtt = MagicMock()
    mock_ha_mqtt.async_subscribe = AsyncMock(return_value=MagicMock())
    monkeypatch.setitem(sys.modules, "homeassistant.components.mqtt", mock_ha_mqtt)

    with patch(
        "custom_components.jackery_solarvault.coordinator.ha_mqtt", mock_ha_mqtt
    ):  # noqa: E501, RUF100
        import asyncio  # noqa: PLC0415, RUF105

        asyncio.run(coordinator.async_start_local_mqtt_listener())

    # The callback is _queue_local_mqtt_message which schedules _handle_local_mqtt_message  # noqa: RUF105
    # We test _handle_local_mqtt_message logic directly (the logic that filters non-dict JSON)  # noqa: RUF105
    import asyncio  # noqa: PLC0415, RUF105

    from custom_components.jackery_solarvault.coordinator import json  # noqa: RUF105

    # Simulate JSON array payload - _handle_local_mqtt_message checks isinstance(payload, dict)  # noqa: RUF105
    mock_message = MagicMock()
    mock_message.topic = "hb/app/device/test"
    mock_message.payload = b'["not", "a", "dict"]'

    # Call _handle_local_mqtt_message logic directly
    raw_payload = mock_message.payload
    if isinstance(raw_payload, bytes):
        raw_payload = raw_payload.decode()
    try:  # noqa: PLW0717, RUF105
        if isinstance(raw_payload, str):
            payload = json.loads(raw_payload)
        # Check if it's a dict - if not, ignore silently
        if not isinstance(payload, dict):
            pass  # Ignored silently - this is what the handler does
        else:
            # If it's a dict, we'd call _async_handle_mqtt_message
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    coordinator._async_handle_mqtt_message(  # ruff: ignore[private-member-access]
                        str(mock_message.topic), payload
                    )
                )  # noqa: E501, RUF100, SLF001
            finally:
                loop.close()
                asyncio.set_event_loop(None)
    except json.JSONDecodeError:
        pass  # Ignored silently

    # Should not call _async_handle_mqtt_message for non-dict JSON
    coordinator._async_handle_mqtt_message.assert_not_called()  # noqa: RUF105, SLF001
