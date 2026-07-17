"""Behavior tests for BLE connect-attempt pacing (ESPHome-proxy protection).

The ESPHome BT-proxy crashes under rapid GATT connect/disconnect cycles
(8 stored ``esp32.crash`` reports delivered 2026-07-03 22:33:41 when the
proxy reconnected after a reboot). These tests pin the pacing contract
without any BLE hardware:

* connect attempts are spaced by an exponential per-device backoff
  (initial 30 s, doubling on repeated failures, capped at 60 s),
* a successful connect resets the escalation,
* while a backoff window is open, ``async_ensure_connected`` must not
  spawn a new connection runner — a failed connect earlier in the same
  coordinator cycle may not trigger an immediate second attempt.
"""

import asyncio
from typing import TYPE_CHECKING

import pytest

from custom_components.jackery_solarvault.client.ble.backoff import (
    BleConnectBackoff,
)
from custom_components.jackery_solarvault.client.ble.ble_transport import (
    JackeryBleListener,
)
from custom_components.jackery_solarvault.const import (
    BLE_CONNECT_BACKOFF_INITIAL_SEC,
    BLE_CONNECT_BACKOFF_MAX_SEC,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine

_NOW = 1_000.0
_DEVICE_ID = "573702884982521856"
_ADDRESS = "80:F1:B2:F9:AA:BB"


# ---------------------------------------------------------------------------
# BleConnectBackoff — pure pacing logic
# ---------------------------------------------------------------------------


def test_fresh_backoff_allows_immediate_attempt() -> None:
    """A device with no failure history may connect right away."""
    backoff = BleConnectBackoff()

    assert backoff.seconds_until_allowed(_NOW) == pytest.approx(0.0)


def test_first_failure_blocks_for_initial_window() -> None:
    """After one failed connect the next attempt waits the initial delay."""
    backoff = BleConnectBackoff()

    applied = backoff.record_failure(_NOW)

    assert applied == pytest.approx(BLE_CONNECT_BACKOFF_INITIAL_SEC)
    assert backoff.seconds_until_allowed(_NOW) == pytest.approx(
        BLE_CONNECT_BACKOFF_INITIAL_SEC
    )
    halfway = _NOW + BLE_CONNECT_BACKOFF_INITIAL_SEC / 2
    assert backoff.seconds_until_allowed(halfway) == pytest.approx(
        BLE_CONNECT_BACKOFF_INITIAL_SEC / 2
    )
    after = _NOW + BLE_CONNECT_BACKOFF_INITIAL_SEC
    assert backoff.seconds_until_allowed(after) == pytest.approx(0.0)


def test_repeated_failures_escalate_exponentially_and_cap() -> None:
    """Consecutive failures double the delay up to the 60 s cap."""
    backoff = BleConnectBackoff()

    delays = [backoff.record_failure(_NOW) for _ in range(4)]

    assert delays[0] == pytest.approx(BLE_CONNECT_BACKOFF_INITIAL_SEC)
    assert delays[1] == pytest.approx(
        min(BLE_CONNECT_BACKOFF_INITIAL_SEC * 2, BLE_CONNECT_BACKOFF_MAX_SEC)
    )
    assert delays[2] == pytest.approx(BLE_CONNECT_BACKOFF_MAX_SEC)
    assert delays[3] == pytest.approx(BLE_CONNECT_BACKOFF_MAX_SEC)
    assert max(delays) <= BLE_CONNECT_BACKOFF_MAX_SEC


def test_success_resets_escalation() -> None:
    """A successful connect clears the window and resets the delay ladder."""
    backoff = BleConnectBackoff()
    backoff.record_failure(_NOW)
    backoff.record_failure(_NOW)

    backoff.record_success()

    assert backoff.seconds_until_allowed(_NOW) == pytest.approx(0.0)
    assert backoff.record_failure(_NOW) == pytest.approx(
        BLE_CONNECT_BACKOFF_INITIAL_SEC
    )


# ---------------------------------------------------------------------------
# JackeryBleListener — backoff gates runner respawn
# ---------------------------------------------------------------------------


class _StubTask:
    """Task stand-in that always reports itself as still running."""

    @staticmethod
    def done() -> bool:
        return False


class _StubHass:
    """Minimal hass stand-in that records background-task spawns."""

    def __init__(self) -> None:
        self.spawned: list[str] = []

    def async_create_background_task(
        self,
        coro: Coroutine[object, object, object],
        name: str = "",
        **_kwargs: object,
    ) -> _StubTask:
        """Record the spawn and close the coroutine instead of running it."""
        self.spawned.append(name)
        coro.close()
        return _StubTask()


async def _noop_sink(_device_id: str, _observation: object) -> None:  # ruff:ignore[unused-async]  # listener sink must be a coroutine function
    return None


def _make_listener(hass: _StubHass) -> JackeryBleListener:
    listener = JackeryBleListener(
        hass,  # type: ignore[arg-type]
        _noop_sink,
        key_resolver=lambda _device_id: None,
        ble_address_resolver=lambda _device_id: None,
    )
    listener._device_addresses[_DEVICE_ID] = _ADDRESS  # ruff:ignore[private-member-access]
    return listener


async def test_ensure_connected_does_not_respawn_during_backoff() -> None:
    """A failed connect must not trigger a new runner in the same cycle."""
    hass = _StubHass()
    listener = _make_listener(hass)
    now = asyncio.get_running_loop().time()
    listener.connect_backoff_for(_DEVICE_ID).record_failure(now)

    result = await listener.async_ensure_connected(_DEVICE_ID, timeout_sec=0)

    assert result is False
    assert hass.spawned == []


async def test_ensure_connected_spawns_runner_when_backoff_clear() -> None:
    """With no open backoff window the runner is (re)spawned as before."""
    hass = _StubHass()
    listener = _make_listener(hass)

    result = await listener.async_ensure_connected(_DEVICE_ID, timeout_sec=0)

    assert result is False  # no live client yet; timeout_sec=0 skips the wait
    assert hass.spawned == [f"jackery_ble_{_DEVICE_ID}"]
