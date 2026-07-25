"""Regression tests for the stale-source guards (follow-ups to the soc-freeze).

A dead source must never masquerade as live:

* The CombineData system-info cache fills only HTTP-MISSING keys and
  expires after ``SYSTEM_INFO_CACHE_MAX_AGE_SEC`` (it used to overwrite
  fresh values unconditionally, forever).
* The SystemBody query runs BLE-first even when the cloud MQTT session is
  banned, and stays skipped when no command transport is available.
"""

from datetime import timedelta
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

from custom_components.jackery_solarvault.const import (
    PAYLOAD_PROPERTIES,
    SYSTEM_INFO_CACHE_MAX_AGE_SEC,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

if TYPE_CHECKING:
    import pytest

_NOW = 50_000.0
_DEVICE = "dev-1"
_CACHED_WORK_MODEL = 2
_FRESH_WORK_MODEL = 3
_PASSTHROUGH_SOC = 75


def _bare_coordinator(
    monkeypatch: pytest.MonkeyPatch | None,
) -> JackerySolarVaultCoordinator:
    """Create a coordinator shell for the guard helpers without HA setup."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._system_info_cache = {}
    coordinator._system_info_cache_monotonic = {}
    coordinator._configured_update_interval = timedelta(seconds=15)
    if monkeypatch is not None:
        monkeypatch.setattr(
            "custom_components.jackery_solarvault.coordinator.time.monotonic",
            lambda: _NOW,
        )
    return coordinator


def test_system_info_cache_never_overwrites_a_delivered_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fill-only: a fresh workModel from HTTP/MQTT must survive the overlay."""
    coordinator = _bare_coordinator(monkeypatch)
    coordinator._system_info_cache[_DEVICE] = {"workModel": _CACHED_WORK_MODEL}
    coordinator._system_info_cache_monotonic[_DEVICE] = _NOW

    filled = coordinator._overlay_cached_system_info(
        _DEVICE,
        {"workModel": _FRESH_WORK_MODEL, "standbyPw": None},
    )

    assert filled["workModel"] == _FRESH_WORK_MODEL


def test_system_info_cache_fills_missing_keys_while_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cache still bridges MQTT disconnects for keys HTTP never carries."""
    coordinator = _bare_coordinator(monkeypatch)
    coordinator._system_info_cache[_DEVICE] = {"workModel": _CACHED_WORK_MODEL}
    coordinator._system_info_cache_monotonic[_DEVICE] = _NOW - 10.0

    filled = coordinator._overlay_cached_system_info(
        _DEVICE,
        {"soc": _PASSTHROUGH_SOC},
    )

    assert filled["workModel"] == _CACHED_WORK_MODEL
    assert filled["soc"] == _PASSTHROUGH_SOC


def test_system_info_cache_expires_instead_of_lying(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An expired cache stops filling — hours-old config is not current state."""
    coordinator = _bare_coordinator(monkeypatch)
    coordinator._system_info_cache[_DEVICE] = {"workModel": _CACHED_WORK_MODEL}
    coordinator._system_info_cache_monotonic[_DEVICE] = (
        _NOW - SYSTEM_INFO_CACHE_MAX_AGE_SEC - 1.0
    )

    filled = coordinator._overlay_cached_system_info(
        _DEVICE,
        {"soc": _PASSTHROUGH_SOC},
    )

    assert "workModel" not in filled


async def test_system_info_query_runs_ble_first_without_cloud_mqtt() -> None:
    """A dead cloud session no longer blocks the SystemBody query (F6).

    The SYSTEM_INFO fields have no HTTP source; with a live BLE transport
    the BLE-first query must run even while the broker bans the session.
    """
    coordinator = _bare_coordinator(None)
    coordinator._mqtt = None
    coordinator._ble_listener = SimpleNamespace()
    coordinator._system_info_query_interval_sec = 180
    coordinator._last_system_info_query = {
        _DEVICE: time.monotonic() - coordinator._system_info_query_interval_sec - 1
    }
    coordinator.data = {_DEVICE: {PAYLOAD_PROPERTIES: {}}}
    query_device_info = AsyncMock(return_value=None)
    query_system_info = AsyncMock(return_value=None)
    cast("Any", coordinator).async_query_device_info = query_device_info
    cast("Any", coordinator).async_query_system_info = query_system_info

    await coordinator._async_query_system_info_for_missing(ensure_mqtt=False)

    query_system_info.assert_awaited_once()


async def test_system_info_query_skips_without_any_command_transport() -> None:
    """No BLE and no connected cloud client: the query stays skipped."""
    coordinator = _bare_coordinator(None)
    coordinator._mqtt = None
    coordinator._ble_listener = None
    coordinator._last_system_info_query = {}
    coordinator._system_info_query_interval_sec = 180
    coordinator.data = {_DEVICE: {PAYLOAD_PROPERTIES: {}}}
    query_system_info = AsyncMock(return_value=None)
    cast("Any", coordinator).async_query_system_info = query_system_info

    await coordinator._async_query_system_info_for_missing(ensure_mqtt=False)

    query_system_info.assert_not_awaited()
