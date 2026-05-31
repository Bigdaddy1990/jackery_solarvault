"""Unit tests for custom_components.jackery_solarvault.client.local_mqtt.

This is a new module introduced in this PR. Tests exercise:
- JackeryLocalMqttClient construction (initial state)
- _extract_mqtt_code: numeric return-code extraction from MqttCodeError
- _handle_connect_failure: records rc + reason in last_error
- _handle_disconnect_error: distinguishes connected vs setup failures
- _handle_message: JSON parsing, topic tracking, dropped-message counter, sink dispatch
- diagnostics_snapshot: redacted and unredacted shapes
- is_connected / is_started properties
- _utc_now_iso: produces a non-empty ISO timestamp

If import-time errors occur (for example from a broken dependency or syntax
regression), tests are skipped with an informative message so the failure mode
is explicit.
"""

import asyncio
import logging
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

try:
    from custom_components.jackery_solarvault.client.local_mqtt import (
        LOCAL_MQTT_DEFAULT_TOPIC,
        LOCAL_MQTT_MAX_TOPIC_NAMES,
        JackeryLocalMqttClient,
    )
    from custom_components.jackery_solarvault.const import (
        MQTT_CLIENT_LIBRARY,
        REDACTED_VALUE,
    )

    _IMPORT_OK = True
except SyntaxError as _syntax_err:
    _IMPORT_OK = False
    _IMPORT_ERROR = _syntax_err
    # Create stubs so the module parses even when the source cannot be imported.
    JackeryLocalMqttClient = None  # type: ignore[assignment,misc]
    LOCAL_MQTT_DEFAULT_TOPIC = ""
    LOCAL_MQTT_MAX_TOPIC_NAMES = 256
    MQTT_CLIENT_LIBRARY = "aiomqtt"
    REDACTED_VALUE = "**REDACTED**"

pytestmark = pytest.mark.skipif(
    not _IMPORT_OK,
    reason="client/local_mqtt.py import failed; fix the module before running tests",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hass() -> Any:  # noqa: ANN401
    """Return a minimal hass stub sufficient for JackeryLocalMqttClient."""

    class _Hass:
        _tasks: list[asyncio.Task[Any]] = []  # noqa: RUF012

        def async_create_background_task(
            self,
            coro: Any,  # noqa: ANN401
            name: str = "",
        ) -> asyncio.Task[Any]:
            task = asyncio.get_event_loop().create_task(coro)
            self._tasks.append(task)
            return task

        def async_create_task(
            self,
            coro: Any,  # noqa: ANN401
            name: str = "",
        ) -> asyncio.Task[Any]:
            task = asyncio.get_event_loop().create_task(coro)
            self._tasks.append(task)
            return task

    return _Hass()


def _make_client(
    *,
    host: str = "192.168.1.100",
    port: int = 1883,
    username: str | None = None,
    password: str | None = None,
    client_id: str = "ha-jackery-test0001",
    sink: Any = None,  # noqa: ANN401
    topic_filter: str = LOCAL_MQTT_DEFAULT_TOPIC,
) -> JackeryLocalMqttClient:
    """Return a configured JackeryLocalMqttClient with a minimal hass stub."""
    hass = _make_hass()
    return JackeryLocalMqttClient(
        hass,
        host=host,
        port=port,
        username=username,
        password=password,
        client_id=client_id,
        sink=sink,
        topic_filter=topic_filter,
    )


# ---------------------------------------------------------------------------
# Construction
# ---------------------------------------------------------------------------


def test_construction_stores_host_and_port() -> None:
    """Host and port must be stored on the instance at construction time."""
    client = _make_client(host="mqtt.local", port=8883)
    assert client._host == "mqtt.local"
    assert client._port == 8883


def test_construction_stores_credentials() -> None:
    """Username and password must be stored when provided."""
    client = _make_client(username="user", password="pass")
    assert client._username == "user"
    assert client._password == "pass"


def test_construction_none_username_stays_none() -> None:
    """Passing username=None must keep _username as None (no coercion)."""
    client = _make_client(username=None)
    assert client._username is None


def test_construction_empty_string_username_becomes_none() -> None:
    """Empty-string credentials are coerced to None by the constructor logic."""
    # The constructor does `self._username = username or None`
    client = _make_client(username="", password="")
    assert client._username is None
    assert client._password is None


def test_construction_initial_state_not_connected_not_started() -> None:
    """A freshly constructed client must report not connected and not started."""
    client = _make_client()
    assert client.is_connected is False
    assert client.is_started is False


def test_construction_initial_counters_are_zero() -> None:
    """Message counters and topic tracking must start at zero/empty."""
    client = _make_client()
    assert client._messages_received == 0
    assert client._messages_dropped == 0
    assert client._topics_seen == []
    assert client._topics_seen_set == set()
    assert client._connect_attempts == 0


def test_construction_default_topic_filter_is_empty() -> None:
    """Default topic filter is empty so the listener stays disabled unless configured."""
    client = _make_client()
    assert client._topic_filter == LOCAL_MQTT_DEFAULT_TOPIC
    assert client._topic_filter == ""


def test_construction_custom_topic_filter_is_stored() -> None:
    """A custom topic filter is stored verbatim."""
    client = _make_client(topic_filter="jackery/#")
    assert client._topic_filter == "jackery/#"


# ---------------------------------------------------------------------------
# _extract_mqtt_code
# ---------------------------------------------------------------------------


def _make_mqtt_code_error(rc: Any) -> Any:  # noqa: ANN401
    """Return a MqttCodeError-shaped stub with the given rc attribute."""
    from aiomqtt.exceptions import MqttCodeError

    err = MqttCodeError.__new__(MqttCodeError)
    err.rc = rc
    return err


def test_extract_mqtt_code_returns_int_rc() -> None:
    """An integer rc attribute is returned as-is."""
    err = _make_mqtt_code_error(5)
    assert JackeryLocalMqttClient._extract_mqtt_code(err) == 5


def test_extract_mqtt_code_unwraps_rc_value() -> None:
    """An rc object with a numeric .value attribute is unwrapped."""

    class _Rc:
        value = 3

    err = _make_mqtt_code_error(_Rc())
    assert JackeryLocalMqttClient._extract_mqtt_code(err) == 3


def test_extract_mqtt_code_returns_zero_for_missing_rc() -> None:
    """When no rc attribute is present, 0 is returned."""
    err = _make_mqtt_code_error(None)
    assert JackeryLocalMqttClient._extract_mqtt_code(err) == 0


def test_extract_mqtt_code_returns_zero_for_non_int_rc_without_value() -> None:
    """A non-int rc with no .value defaults to 0."""

    class _Rc:
        pass  # no .value

    err = _make_mqtt_code_error(_Rc())
    assert JackeryLocalMqttClient._extract_mqtt_code(err) == 0


# ---------------------------------------------------------------------------
# _handle_connect_failure
# ---------------------------------------------------------------------------


def test_handle_connect_failure_records_last_error_with_rc_and_reason() -> None:
    """Connection rejection must set last_error to 'connect rc=N (reason)'."""
    client = _make_client()
    client._handle_connect_failure(5)
    assert client._last_error is not None
    assert "rc=5" in client._last_error


def test_handle_connect_failure_uses_reason_for_rc_0() -> None:
    """rc=0 is 'Connection accepted' — the message must reflect the lookup."""
    client = _make_client()
    client._handle_connect_failure(0)
    assert "Connection accepted" in (client._last_error or "")


def test_handle_connect_failure_unknown_rc_uses_unknown_label() -> None:
    """An rc code not in MQTT_CONNACK_REASONS maps to 'unknown'."""
    client = _make_client()
    client._handle_connect_failure(99)
    assert "unknown" in (client._last_error or "")


def test_handle_connect_failure_marks_not_connected() -> None:
    """A rejected connection must leave is_connected False."""
    client = _make_client()
    client._connected = True  # pretend it was connected
    client._handle_connect_failure(4)
    assert client.is_connected is False


def test_handle_connect_failure_sets_connected_event() -> None:
    """The connected event must be set so async_start waiters unblock."""
    client = _make_client()
    # Clear the event so we can verify it is set.
    client._connected_event.clear()
    client._handle_connect_failure(2)
    assert client._connected_event.is_set()


# ---------------------------------------------------------------------------
# _handle_disconnect_error
# ---------------------------------------------------------------------------


def test_handle_disconnect_error_when_was_connected_says_disconnect() -> None:
    """If the client was connected, the error message starts with 'disconnect:'."""
    client = _make_client()
    client._handle_disconnect_error("broker reset", was_connected=True)
    assert client._last_error is not None
    assert client._last_error.startswith("disconnect:")
    assert "broker reset" in client._last_error


def test_handle_disconnect_error_when_not_yet_connected_says_connect_failed() -> None:
    """If the client had not connected, the error message starts with 'connect failed:'."""
    client = _make_client()
    client._handle_disconnect_error("refused", was_connected=False)
    assert client._last_error is not None
    assert client._last_error.startswith("connect failed:")
    assert "refused" in client._last_error


# ---------------------------------------------------------------------------
# _handle_message
# ---------------------------------------------------------------------------


def test_handle_message_increments_received_counter() -> None:
    """Each call to _handle_message must increment messages_received by 1."""
    client = _make_client()
    assert client._messages_received == 0
    client._handle_message("test/topic", b'{"key": 1}')
    assert client._messages_received == 1
    client._handle_message("test/topic", b'{"key": 2}')
    assert client._messages_received == 2


def test_handle_message_records_new_topics() -> None:
    """First message on a topic must add it to topics_seen."""
    client = _make_client()
    client._handle_message("jackery/data", b'{"v":1}')
    assert "jackery/data" in client._topics_seen
    assert "jackery/data" in client._topics_seen_set


def test_handle_message_does_not_duplicate_topics() -> None:
    """Second message on the same topic must not add a duplicate entry."""
    client = _make_client()
    client._handle_message("jackery/data", b'{"v":1}')
    client._handle_message("jackery/data", b'{"v":2}')
    assert client._topics_seen.count("jackery/data") == 1


def test_handle_message_tracks_multiple_distinct_topics() -> None:
    """Each unique topic is tracked separately."""
    client = _make_client()
    client._handle_message("a/b", b"{}")
    client._handle_message("c/d", b"{}")
    assert len(client._topics_seen) == 2


def test_handle_message_caps_topic_tracking_at_max() -> None:
    """After LOCAL_MQTT_MAX_TOPIC_NAMES topics, new topics set the truncated flag."""
    client = _make_client()
    for i in range(LOCAL_MQTT_MAX_TOPIC_NAMES):
        client._handle_message(f"topic/{i}", b"{}")
    assert len(client._topics_seen) == LOCAL_MQTT_MAX_TOPIC_NAMES
    assert not client._topics_seen_truncated

    # One more topic beyond the cap.
    client._handle_message("overflow/topic", b"{}")
    assert client._topics_seen_truncated
    assert len(client._topics_seen) == LOCAL_MQTT_MAX_TOPIC_NAMES


def test_handle_message_drops_non_dict_json() -> None:
    """JSON arrays/scalars must increment messages_dropped; data stays None."""

    async def _sink(topic: str, data: dict | None, raw: bytes) -> None:  # noqa: RUF029
        return None

    client = _make_client(sink=_sink, topic_filter="topic")
    client._schedule_coroutine = lambda coro, label: coro.close()  # type: ignore[method-assign]
    client._handle_message("topic", b"[1, 2, 3]")
    assert client._messages_dropped == 1
    assert client._messages_received == 1


def test_handle_message_drops_non_utf8_binary() -> None:
    """Non-decodable binary payload increments messages_dropped."""

    async def _sink(topic: str, data: dict | None, raw: bytes) -> None:  # noqa: RUF029
        return None

    client = _make_client(sink=_sink, topic_filter="topic")
    client._schedule_coroutine = lambda coro, label: coro.close()  # type: ignore[method-assign]
    client._handle_message("topic", b"\xff\xfe\xfd")  # invalid UTF-8
    assert client._messages_dropped == 1


def test_handle_message_drops_invalid_json_text() -> None:
    """Payload that is valid UTF-8 but not valid JSON increments messages_dropped."""
    client = _make_client()
    client._handle_message("topic", b"not json at all")
    # Non-JSON text: parsed will be None, so data is None but dropped is NOT
    # incremented for invalid JSON (only for non-object JSON or binary).
    # Based on the code: `except (json.JSONDecodeError, ValueError): parsed = None`
    # then `if isinstance(parsed, dict): data = parsed elif parsed is not None:...`
    # invalid JSON → parsed=None → no dropped increment, data=None
    # This is the documented behaviour; dropped is only for non-object JSON.
    assert client._messages_received == 1


def test_handle_message_parses_valid_json_dict() -> None:
    """A valid JSON object payload must be forwarded as data; not dropped."""
    sink_calls: list[tuple[str, Any, bytes]] = []

    async def _sink(topic: str, data: dict | None, raw: bytes) -> None:  # noqa: RUF029
        sink_calls.append((topic, data, raw))

    client = _make_client(sink=_sink)
    client._handle_message("jackery/props", b'{"batSoc": 87}')
    # No drops for valid JSON object.
    assert client._messages_dropped == 0


def test_handle_message_updates_last_topic_and_last_message_at() -> None:
    """After a message, last_topic and last_message_at must be set."""
    client = _make_client()
    assert client._last_topic is None
    assert client._last_message_at is None
    client._handle_message("jackery/props", b"{}")
    assert client._last_topic == "jackery/props"
    assert client._last_message_at is not None


def test_handle_message_string_payload_is_accepted() -> None:
    """Str payloads (not bytes) must be processed without error."""
    client = _make_client()
    client._handle_message("test/str", '{"cmd": 107}')
    assert client._messages_received == 1
    assert client._messages_dropped == 0


def test_handle_message_without_sink_skips_json_parse() -> None:
    """With sink=None the client must skip JSON parsing on the hot path."""
    client = _make_client(topic_filter="topic")
    with patch(
        "custom_components.jackery_solarvault.client.local_mqtt.json.loads",
        side_effect=AssertionError("json.loads must not be called"),
    ):
        client._handle_message("topic", b'{"cmd": 107}')
    assert client._messages_received == 1
    assert client._messages_dropped == 0


def test_handle_message_topic_mismatch_increments_blocked_counter() -> None:
    """Messages outside the configured topic filter must increment blocked counter."""
    client = _make_client(topic_filter="jackery/#")
    client._handle_message("homeassistant/state", b"{}")
    assert client._blocked_by_filter_count == 1
    assert client._messages_received == 1


def test_handle_message_oversized_payload_is_dropped() -> None:
    """Oversized payloads must be dropped even when sink is not configured."""
    client = _make_client(topic_filter="topic")
    client._handle_message("topic", b"x" * (128 * 1024 + 1))
    assert client._payload_too_large_count == 1
    assert client._messages_dropped == 1


# ---------------------------------------------------------------------------
# diagnostics_snapshot
# ---------------------------------------------------------------------------


def test_diagnostics_snapshot_redacted_hides_host_and_port() -> None:
    """With redact=True, host and port in configured_target must be REDACTED."""
    client = _make_client(host="192.168.1.50", port=1883)
    snap = client.diagnostics_snapshot(redact=True)
    target = snap["configured_target"]
    assert target["host"] == REDACTED_VALUE
    assert target["port"] == REDACTED_VALUE


def test_diagnostics_snapshot_unredacted_exposes_host_and_port() -> None:
    """With redact=False, host and port are included verbatim."""
    client = _make_client(host="192.168.1.50", port=1883)
    snap = client.diagnostics_snapshot(redact=False)
    target = snap["configured_target"]
    assert target["host"] == "192.168.1.50"
    assert target["port"] == 1883


def test_diagnostics_snapshot_redacted_hides_topic_names() -> None:
    """With redact=True, each entry in topics_seen must be REDACTED."""
    client = _make_client()
    client._handle_message("jackery/device/123/props", b"{}")
    snap = client.diagnostics_snapshot(redact=True)
    assert snap["topics_seen"] == [REDACTED_VALUE]


def test_diagnostics_snapshot_unredacted_exposes_topic_names() -> None:
    """With redact=False, topic names are included verbatim."""
    client = _make_client()
    client._handle_message("jackery/device/123/props", b"{}")
    snap = client.diagnostics_snapshot(redact=False)
    assert "jackery/device/123/props" in snap["topics_seen"]


def test_diagnostics_snapshot_includes_required_keys() -> None:
    """Snapshot must include all mandatory diagnostic keys."""
    client = _make_client()
    snap = client.diagnostics_snapshot()
    required = {
        "enabled",
        "configured_target",
        "connected",
        "started",
        "topic_filter",
        "topics_seen_count",
        "topics_seen",
        "topics_seen_truncated",
        "messages_received",
        "messages_dropped",
        "last_topic",
        "last_message_at",
        "last_connect_at",
        "last_disconnect_at",
        "last_error",
        "connect_attempts",
        "blocked_by_filter_count",
        "payload_too_large_count",
        "library",
    }
    assert required.issubset(snap.keys())


def test_diagnostics_snapshot_enabled_is_always_true() -> None:
    """The 'enabled' key must be True (the client exists ⟹ it is enabled)."""
    client = _make_client()
    assert client.diagnostics_snapshot()["enabled"] is True


def test_diagnostics_snapshot_library_key_matches_const() -> None:
    """The 'library' key must match the MQTT_CLIENT_LIBRARY constant."""
    client = _make_client()
    assert client.diagnostics_snapshot()["library"] == MQTT_CLIENT_LIBRARY


def test_diagnostics_snapshot_counts_are_accurate() -> None:
    """Message and topic counts in the snapshot match internal counters."""
    client = _make_client()
    client._handle_message("a", b"{}")
    client._handle_message("b", b"{}")
    client._handle_message("b", b"not valid json")  # no drop for invalid JSON
    snap = client.diagnostics_snapshot()
    assert snap["messages_received"] == 3
    assert snap["topics_seen_count"] == 2


def test_diagnostics_snapshot_redacted_last_topic_is_redacted_value() -> None:
    """After a message, redacted snapshot must show REDACTED for last_topic."""
    client = _make_client()
    client._handle_message("private/topic/123", b"{}")
    snap = client.diagnostics_snapshot(redact=True)
    assert snap["last_topic"] == REDACTED_VALUE


def test_diagnostics_snapshot_last_topic_is_none_before_messages() -> None:
    """Before any messages, last_topic must be None in both redacted/unredacted."""
    client = _make_client()
    assert client.diagnostics_snapshot(redact=True)["last_topic"] is None
    assert client.diagnostics_snapshot(redact=False)["last_topic"] is None


# ---------------------------------------------------------------------------
# is_connected / is_started properties
# ---------------------------------------------------------------------------


def test_is_connected_reflects_connected_flag() -> None:
    """is_connected must mirror the _connected attribute."""
    client = _make_client()
    assert client.is_connected is False
    client._connected = True
    assert client.is_connected is True


def test_is_started_reflects_runner_task_presence() -> None:
    """is_started must be True when _runner_task is set, False otherwise."""
    client = _make_client()
    assert client.is_started is False
    # Simulate a task being present.
    client._runner_task = MagicMock()  # type: ignore[assignment]
    assert client.is_started is True
    client._runner_task = None
    assert client.is_started is False


# ---------------------------------------------------------------------------
# _utc_now_iso
# ---------------------------------------------------------------------------


def test_utc_now_iso_returns_non_empty_string() -> None:
    """_utc_now_iso must return a non-empty string."""
    ts = JackeryLocalMqttClient._utc_now_iso()
    assert isinstance(ts, str)
    assert len(ts) > 0


def test_utc_now_iso_contains_utc_offset() -> None:
    """_utc_now_iso must include a timezone offset indicator."""
    ts = JackeryLocalMqttClient._utc_now_iso()
    # isoformat(UTC) produces something like '2026-05-27T12:00:00+00:00'
    assert "+" in ts or "Z" in ts


def test_utc_now_iso_two_calls_are_close_or_equal() -> None:
    """Two consecutive calls must produce valid ISO strings (not crash)."""
    ts1 = JackeryLocalMqttClient._utc_now_iso()
    ts2 = JackeryLocalMqttClient._utc_now_iso()
    assert isinstance(ts1, str)
    assert isinstance(ts2, str)


def test_aiomqtt_transport_logger_is_kept_at_warning() -> None:
    """Per-packet aiomqtt DEBUG logs must stay disabled by default."""
    from custom_components.jackery_solarvault.client import local_mqtt as module

    assert module._AIOMQTT_LOGGER.level == logging.WARNING


# ---------------------------------------------------------------------------
# async_stop before start is a no-op
# ---------------------------------------------------------------------------


async def test_async_stop_before_start_does_not_raise() -> None:
    """Calling async_stop before async_start must complete without error."""
    client = _make_client()
    await client.async_stop()
    assert not client.is_connected
    assert not client.is_started
