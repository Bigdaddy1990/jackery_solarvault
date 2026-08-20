"""Behavior tests for BLE connect-attempt pacing (ESPHome-proxy protection).

The ESPHome BT-proxy crashes under rapid GATT connect/disconnect cycles
(8 stored ``esp32.crash`` reports delivered 2026-07-03 22:33:41 when the
proxy reconnected after a reboot). These tests pin the pacing contract
without any BLE hardware:

* connect attempts are spaced by an exponential per-device backoff
  (initial 30 s, doubling on repeated failures, capped at 10 minutes),
* only a stable notify session resets the escalation,
* while a backoff window is open, ``async_ensure_connected`` must not spawn
  an extra runner; a cache/advertisement-owned runner waits before the next
  physical connect attempt.
"""

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock

import pytest

from custom_components.jackery_solarvault.client.ble_transport import (
    BleFrameObservation,
    JackeryBleListener,
)
from custom_components.jackery_solarvault.const import (
    BLE_CONNECT_BACKOFF_INITIAL_SEC,
    BLE_CONNECT_BACKOFF_MAX_SEC,
)
from custom_components.jackery_solarvault.coordinator import BleConnectBackoff

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
    """After one failed connect the next attempt waits the initial delay (±25% jitter)."""  # noqa: E501, RUF105
    backoff = BleConnectBackoff()

    applied = backoff.record_failure(_NOW)

    assert applied == pytest.approx(BLE_CONNECT_BACKOFF_INITIAL_SEC)
    # seconds_until_allowed includes ±25% jitter (0.75-1.25 x initial_sec)
    wait = backoff.seconds_until_allowed(_NOW)
    assert wait >= BLE_CONNECT_BACKOFF_INITIAL_SEC * 0.75
    assert wait <= BLE_CONNECT_BACKOFF_INITIAL_SEC * 1.25
    halfway = _NOW + wait / 2
    assert backoff.seconds_until_allowed(halfway) == pytest.approx(wait / 2, rel=0.01)
    after = _NOW + wait
    assert backoff.seconds_until_allowed(after) == pytest.approx(0.0)


def test_repeated_failures_escalate_exponentially_and_cap() -> None:
    """Consecutive failures grow to a long proxy-safe cooldown cap."""
    backoff = BleConnectBackoff()

    delays = [backoff.record_failure(_NOW) for _ in range(8)]

    assert delays[0] == pytest.approx(BLE_CONNECT_BACKOFF_INITIAL_SEC)
    assert delays[1] == pytest.approx(
        min(BLE_CONNECT_BACKOFF_INITIAL_SEC * 2, BLE_CONNECT_BACKOFF_MAX_SEC)
    )
    assert delays[2] > delays[1]
    assert delays[-1] == pytest.approx(BLE_CONNECT_BACKOFF_MAX_SEC)
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


def test_success_resets_escalation_from_capped_delay() -> None:
    """A success after the ladder has hit the cap still fully resets it.

    Regression guard: resetting from a *partially* escalated state (as in
    ``test_success_resets_escalation``) is not proof that the ladder index
    itself was cleared rather than merely the currently-open window. Escalate
    all the way to the cap first, then confirm the very next failure after a
    reset is back at the initial delay, not one step down from the cap.
    """
    backoff = BleConnectBackoff()
    for _ in range(8):
        backoff.record_failure(_NOW)
    # At cap, seconds_until_allowed includes ±25% jitter (0.75-1.25 x max_sec)
    wait_at_cap = backoff.seconds_until_allowed(_NOW)
    assert wait_at_cap >= BLE_CONNECT_BACKOFF_MAX_SEC * 0.75
    assert wait_at_cap <= BLE_CONNECT_BACKOFF_MAX_SEC * 1.25

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


async def _noop_sink(  # ruff: ignore[unused-async]
    _device_id: str,
    _observation: BleFrameObservation,
) -> bool:  # listener sink must be a coroutine function
    return True


def _make_listener(
    hass: _StubHass,
    backoff: BleConnectBackoff | None = None,
) -> JackeryBleListener:
    backoff = backoff or BleConnectBackoff()
    listener = JackeryBleListener(
        hass,  # type: ignore[arg-type]
        _noop_sink,
        key_resolver=lambda _device_id: None,
        ble_address_resolver=lambda _device_id: None,
        connect_backoff_remaining=(
            lambda _device_id, now: backoff.seconds_until_allowed(now)
        ),
        connect_backoff_note_failure=(
            lambda _device_id, now: backoff.record_failure(now)
        ),
        connect_backoff_note_success=lambda _device_id: backoff.record_success(),
        keep_alive_msg_id=None,
        keep_alive_ble_msg_type=None,
    )
    listener._device_addresses[_DEVICE_ID] = _ADDRESS  # ruff: ignore[private-member-access]
    return listener


async def test_ensure_connected_does_not_respawn_during_backoff() -> None:
    """A failed connect must not trigger a new runner in the same cycle."""
    hass = _StubHass()
    backoff = BleConnectBackoff()
    listener = _make_listener(hass, backoff)
    now = asyncio.get_running_loop().time()
    backoff.record_failure(now)

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


async def test_advertisement_callback_owns_runner_during_backoff() -> None:
    """An advertisement owns one runner while its connect attempt stays paced."""
    await asyncio.sleep(0)
    hass = _StubHass()
    backoff = BleConnectBackoff()
    listener = _make_listener(hass, backoff)
    cast("Any", listener)._device_id_from_service_info = lambda _info: _DEVICE_ID  # ruff: ignore[private-member-access]
    backoff.record_failure(asyncio.get_running_loop().time())

    listener._on_advertisement(  # ruff: ignore[private-member-access]
        SimpleNamespace(address=_ADDRESS, source="proxy"),  # type: ignore[arg-type]
        MagicMock(),
    )

    assert hass.spawned == [f"jackery_ble_{_DEVICE_ID}"]


async def test_owned_runner_waits_before_physical_connect_during_backoff() -> None:
    """Runner ownership during backoff does not contact the BLE stack early."""
    hass = _StubHass()
    backoff = BleConnectBackoff()
    listener = _make_listener(hass, backoff)
    bluetooth = MagicMock()
    listener._ha_bluetooth = bluetooth  # ruff: ignore[private-member-access]
    backoff.record_failure(asyncio.get_running_loop().time())

    runner = asyncio.create_task(
        listener._async_run_connection(_DEVICE_ID, _ADDRESS),  # ruff: ignore[private-member-access]
    )
    await asyncio.sleep(0)

    bluetooth.async_ble_device_from_address.assert_not_called()
    listener._stop_event.set()  # ruff: ignore[private-member-access]
    await runner


def test_short_session_does_not_reset_connect_backoff() -> None:
    """A bare GATT connect is not success until notify traffic stays stable."""
    listener = _make_listener(_StubHass())
    reset = MagicMock()
    cast("Any", listener)._connect_backoff_note_success = reset  # ruff: ignore[private-member-access]

    assert (
        listener._reset_backoff_after_stable_session(  # ruff: ignore[private-member-access]
            _DEVICE_ID,
            started_at=100.0,
            now=159.0,
        )
        is False
    )
    reset.assert_not_called()
    assert (
        listener._reset_backoff_after_stable_session(  # ruff: ignore[private-member-access]
            _DEVICE_ID,
            started_at=100.0,
            now=160.0,
        )
        is True
    )
    reset.assert_called_once_with(_DEVICE_ID)
