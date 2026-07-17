"""Regression tests for cloud-MQTT rc=133 ban handling and teardown noise.

Covers the 2026-07-03 live incident: the broker rejected every CONNACK with
MQTT v5 reason code 133 (client identifier not valid / banned). The failure
was classified as *transient* (short backoff) instead of an auth/ban pause,
producing a reconnect storm whose teardown races flooded the HA log with
``Unexpected message ID`` tracebacks, unretrieved-future jobs, and birth
snapshot ERRORs.
"""

import asyncio
import logging
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault.client.mqtt.mqtt_push import (
    JackeryMqttPushClient,
)
from custom_components.jackery_solarvault.client.mqtt.mqtt_state import (
    MqttConnectionManager,
    is_mqtt_auth_failure,
    is_transient_connect_failure,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_PUSH_LOGGER = "custom_components.jackery_solarvault.client.mqtt.mqtt_push"
_AIOMQTT_LOGGER = f"{_PUSH_LOGGER}.aiomqtt"
_STATE_LOGGER = "custom_components.jackery_solarvault.client.mqtt.mqtt_state"

_RC133_MESSAGE = "connect rc=133 (client identifier not valid (banned))"
_TWO_ATTEMPTS = 2


# ---------------------------------------------------------------------------
# Fix A: rc classification — 128-135 is the v5 auth/ban class
# ---------------------------------------------------------------------------


def test_connack_ban_class_codes_are_auth_failures() -> None:
    """MQTT v5 reason codes 128-135 must pause like credential rejections."""
    for rc in (4, 5, 128, 133, 134, 135):
        assert JackeryMqttPushClient._is_connect_auth_failure_rc(rc), rc  # ruff:ignore[private-member-access]
    for rc in (0, 2, 3, 136):
        assert not JackeryMqttPushClient._is_connect_auth_failure_rc(rc), rc  # ruff:ignore[private-member-access]


def test_is_mqtt_auth_failure_matches_rc133_and_code128() -> None:
    """Both our connect message and aiomqtt's MqttCodeError text must match."""
    assert is_mqtt_auth_failure(_RC133_MESSAGE)
    assert is_mqtt_auth_failure(f"MQTT not connected yet ({_RC133_MESSAGE})")
    assert is_mqtt_auth_failure("[code:128] Unspecified error")
    assert not is_mqtt_auth_failure("connect rc=3 (server unavailable)")
    assert not is_mqtt_auth_failure("[code:7] The connection was lost")


def test_rc133_is_no_longer_a_transient_failure() -> None:
    """rc=133 is a ban: it must not select the short transient backoff."""
    assert not is_transient_connect_failure(_RC133_MESSAGE)


def test_handle_connect_error_rc133_pauses_instead_of_backoff() -> None:
    """A banned client id enters the app-conflict pause, not a backoff loop."""
    manager = MqttConnectionManager()
    stub = cast(
        "JackeryMqttPushClient",
        SimpleNamespace(
            diagnostics={"last_error": _RC133_MESSAGE},
            consecutive_auth_failures=2,
        ),
    )

    manager.handle_connect_error(
        stub, RuntimeError(f"MQTT not connected yet ({_RC133_MESSAGE})")
    )

    assert manager.paused_until_monotonic > time.monotonic()
    assert manager.backoff_until_monotonic == pytest.approx(0.0)
    assert manager.app_conflict_pause_cycles == 1


# ---------------------------------------------------------------------------
# Fix B: teardown — consume pending aiomqtt futures, silence late callbacks
# ---------------------------------------------------------------------------


async def test_finalize_raw_client_consumes_future_exceptions() -> None:  # ruff:ignore[unused-async]  # needs a running loop for create_future
    """Set exceptions are retrieved and pending futures are pre-completed."""
    loop = asyncio.get_running_loop()
    connected = loop.create_future()
    disconnected = loop.create_future()
    disconnected.set_exception(RuntimeError("[code:128] Unspecified error"))
    raw = SimpleNamespace(_connected=connected, _disconnected=disconnected)

    JackeryMqttPushClient._finalize_raw_client(raw)  # ruff:ignore[private-member-access]

    assert connected.done()
    assert connected.result() is None
    assert disconnected._log_traceback is False  # ruff:ignore[private-member-access]


async def test_finalize_raw_client_tolerates_cancelled_and_missing() -> None:  # ruff:ignore[unused-async]  # needs a running loop for create_future
    """Cancelled futures and non-future attributes must not raise."""
    loop = asyncio.get_running_loop()
    cancelled = loop.create_future()
    cancelled.cancel()

    JackeryMqttPushClient._finalize_raw_client(  # ruff:ignore[private-member-access]
        SimpleNamespace(_connected=cancelled, _disconnected=None)
    )
    JackeryMqttPushClient._finalize_raw_client(None)  # ruff:ignore[private-member-access]

    assert cancelled.cancelled()


def test_unexpected_message_id_is_suppressed_by_default(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Late-SUBACK teardown noise stays out of the HA log at default level."""
    logger = logging.getLogger(_AIOMQTT_LOGGER)
    with caplog.at_level(logging.INFO):
        logger.error('Unexpected message ID "%d" in on_subscribe callback', 1)
        logger.error("Caught exception in on_disconnect: CancelledError")

    noisy = [
        record
        for record in caplog.records
        if "Unexpected message ID" in record.getMessage()
        or "Caught exception in on_" in record.getMessage()
    ]
    assert not noisy


def test_unexpected_message_id_demoted_to_debug_when_debugging(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """With the child logger at DEBUG the record is visible, demoted to DEBUG."""
    logger = logging.getLogger(_AIOMQTT_LOGGER)
    with caplog.at_level(logging.DEBUG, logger=_AIOMQTT_LOGGER):
        logger.error('Unexpected message ID "%d" in on_subscribe callback', 3)

    records = [
        record
        for record in caplog.records
        if "Unexpected message ID" in record.getMessage()
    ]
    assert records
    assert records[0].levelno == logging.DEBUG


def test_aiomqtt_real_warnings_still_pass_the_filter(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Unrelated aiomqtt warnings must not be swallowed by the noise filter."""
    logger = logging.getLogger(_AIOMQTT_LOGGER)
    with caplog.at_level(logging.INFO):
        logger.warning("failed to receive on socket: unexpected TLS alert")

    assert any(
        "failed to receive on socket" in record.getMessage()
        for record in caplog.records
    )


# ---------------------------------------------------------------------------
# Fix C: birth snapshot — dispatch only while connected, quiet failure path
# ---------------------------------------------------------------------------


async def test_birth_snapshot_not_dispatched_when_connection_lost(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A birth scheduled into a dead session never invokes the publish."""
    callback = AsyncMock()
    client = JackeryMqttPushClient(hass, message_callback=AsyncMock())
    assert client.is_connected is False

    with caplog.at_level(logging.DEBUG, logger=_PUSH_LOGGER):
        client._schedule_birth_snapshot(callback)  # ruff:ignore[private-member-access]
        await hass.async_block_till_done()

    callback.assert_not_awaited()
    snapshot = client.diagnostics_snapshot()
    assert snapshot["birth_publishes"] == 1
    assert snapshot["birth_publish_failed"] == 1
    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]


async def test_birth_snapshot_not_connected_error_is_debug_and_deduplicated(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The rc=133 publish failure logs once at DEBUG instead of ERROR spam."""
    error = RuntimeError(f"MQTT not connected yet ({_RC133_MESSAGE})")
    callback = AsyncMock(side_effect=error)
    client = JackeryMqttPushClient(hass, message_callback=AsyncMock())
    client._connected = True  # ruff:ignore[private-member-access]

    with caplog.at_level(logging.DEBUG, logger=_PUSH_LOGGER):
        client._schedule_birth_snapshot(callback)  # ruff:ignore[private-member-access]
        await hass.async_block_till_done()
        client._schedule_birth_snapshot(callback)  # ruff:ignore[private-member-access]
        await hass.async_block_till_done()

    assert not [r for r in caplog.records if r.levelno >= logging.WARNING]
    debug_records = [r for r in caplog.records if "birth snapshot" in r.getMessage()]
    assert len(debug_records) == 1
    assert client.diagnostics_snapshot()["birth_publish_failed"] == _TWO_ATTEMPTS


async def test_birth_snapshot_unexpected_error_still_logged_as_error(
    hass: HomeAssistant,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Genuine handler bugs keep surfacing at ERROR."""
    callback = AsyncMock(side_effect=ValueError("boom"))
    client = JackeryMqttPushClient(hass, message_callback=AsyncMock())
    client._connected = True  # ruff:ignore[private-member-access]

    with caplog.at_level(logging.DEBUG, logger=_PUSH_LOGGER):
        client._schedule_birth_snapshot(callback)  # ruff:ignore[private-member-access]
        await hass.async_block_till_done()

    assert any(
        r.levelno == logging.ERROR and "birth snapshot" in r.getMessage()
        for r in caplog.records
    )


# ---------------------------------------------------------------------------
# Fix D: rate-limit repeated pause/backoff log lines
# ---------------------------------------------------------------------------


def test_pause_after_auth_failure_repeat_cycle_logs_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Only the first app-conflict pause cycle is announced at INFO."""
    manager = MqttConnectionManager()

    with caplog.at_level(logging.INFO, logger=_STATE_LOGGER):
        manager.pause_after_auth_failure(_RC133_MESSAGE, streak=1)
    assert any(
        r.levelno == logging.INFO and "paused" in r.getMessage() for r in caplog.records
    )

    caplog.clear()
    manager.paused_until_monotonic = 0.0
    with caplog.at_level(logging.INFO, logger=_STATE_LOGGER):
        manager.pause_after_auth_failure(_RC133_MESSAGE, streak=2)
    assert not [r for r in caplog.records if r.levelno >= logging.INFO]
    assert manager.app_conflict_pause_cycles == _TWO_ATTEMPTS


def test_note_connect_failure_repeated_signature_logs_debug(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Repeating the same connect-failure signature demotes the log to DEBUG."""
    manager = MqttConnectionManager()
    message = "connect failed: something odd"

    with caplog.at_level(logging.INFO, logger=_STATE_LOGGER):
        manager.note_connect_failure(message)
    assert any(r.levelno == logging.INFO for r in caplog.records)

    caplog.clear()
    manager.backoff_until_monotonic = 0.0
    with caplog.at_level(logging.INFO, logger=_STATE_LOGGER):
        manager.note_connect_failure(message)
    assert not [r for r in caplog.records if r.levelno >= logging.INFO]
