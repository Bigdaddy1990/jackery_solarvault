"""Unit tests for Cloud MQTT push-client birth/availability diagnostics.

The Cloud MQTT diagnostic sensors advertise ``birth_publishes``,
``birth_publish_failed`` and ``last_birth_at`` (see
``coordinator.cloud_mqtt_observations`` / ``http_api_observations`` and the
``JackeryCloudMqttSensor`` / ``JackeryHttpApiSensor`` docstrings). Before the
Q5 fix the push client never produced those keys, so the birth/availability
signal was permanently 0/None — a faulty diagnostics contract.

These tests pin the corrected behavior: the on-connect app-snapshot publish
(MQTT_PROTOCOL.md §3, the protocol "birth") is counted, timestamped, and its
failures are tracked, and the diagnostics snapshot exposes the keys the
sensors read. The Jackery broker protocol uses clean_session + QoS 0 and sets
no Last Will (MQTT_PROTOCOL.md "Clean Session: Yes"), so no LWT is asserted —
presence is the snapshot publish, not a will.
"""

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

from custom_components.jackery_solarvault.client.mqtt.mqtt_push import (
    JackeryMqttPushClient,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

_SNAPSHOT_FAILED = "snapshot publish failed"


class _ImmediateTask:
    """Task stub that runs the coroutine to completion synchronously.

    The push client only needs ``add_done_callback``; the callback is invoked
    immediately so birth success/failure tracking is observable without a
    running event loop or a real broker.
    """

    def __init__(self, coro: Coroutine[Any, Any, None]) -> None:
        """Drive ``coro`` to completion, capturing any raised exception."""
        self._error: BaseException | None = None
        try:
            asyncio.run(coro)
        except BaseException as err:  # noqa: BLE001
            self._error = err

    def add_done_callback(
        self,
        callback: Callable[[_ImmediateTask], None],
    ) -> None:
        """Invoke ``callback`` immediately with this completed task stub."""
        callback(self)

    def result(self) -> None:
        """Re-raise the captured exception, mirroring ``asyncio.Task.result``."""
        if self._error is not None:
            raise self._error


def _make_hass() -> MagicMock:
    """Return a hass stub whose ``async_create_task`` runs the coroutine inline."""
    hass = MagicMock()
    hass.async_create_task.side_effect = lambda coro, name=None: _ImmediateTask(coro)
    return hass


def _make_client(hass: MagicMock) -> JackeryMqttPushClient:
    """Construct a push client with a no-op message callback."""

    async def _noop_message(_topic: str, _payload: dict[str, Any]) -> None:
        await asyncio.sleep(0)

    return JackeryMqttPushClient(hass, _noop_message)


def test_birth_counters_start_at_zero() -> None:
    """A fresh push client reports no birth publishes and no timestamp."""
    client = _make_client(_make_hass())
    snap = client.diagnostics_snapshot()
    assert snap["birth_publishes"] == 0
    assert snap["birth_publish_failed"] == 0
    assert snap["last_birth_at"] is None


def test_birth_snapshot_dispatch_is_counted_and_timestamped() -> None:
    """Dispatching the connect snapshot records a birth publish + timestamp."""
    client = _make_client(_make_hass())

    async def _snapshot() -> None:
        await asyncio.sleep(0)

    client._schedule_birth_snapshot(_snapshot())  # noqa: SLF001

    snap = client.diagnostics_snapshot()
    assert snap["birth_publishes"] == 1
    assert snap["birth_publish_failed"] == 0
    assert snap["last_birth_at"] is not None


def test_birth_snapshot_failure_is_tracked() -> None:
    """A raising snapshot still counts as a birth attempt and records failure."""
    client = _make_client(_make_hass())

    async def _failing_snapshot() -> None:
        await asyncio.sleep(0)
        raise RuntimeError(_SNAPSHOT_FAILED)

    client._schedule_birth_snapshot(_failing_snapshot())  # noqa: SLF001

    snap = client.diagnostics_snapshot()
    assert snap["birth_publishes"] == 1
    assert snap["birth_publish_failed"] == 1
    # The attempt is timestamped even though the publish failed.
    assert snap["last_birth_at"] is not None


def test_diagnostics_snapshot_exposes_birth_contract_keys() -> None:
    """The keys the Cloud MQTT / HTTP-API sensors read must always be present."""
    client = _make_client(_make_hass())
    snap = client.diagnostics_snapshot()
    for key in ("birth_publishes", "birth_publish_failed", "last_birth_at"):
        assert key in snap
