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
    coordinator._system_info_cache = {}  # ruff: ignore[private-member-access]
    coordinator._system_info_cache_monotonic = {}  # ruff: ignore[private-member-access]
    coordinator._configured_update_interval = timedelta(seconds=15)  # ruff: ignore[private-member-access]
    if monkeypatch is not None:
        monkeypatch.setattr(
            "custom_components.jackery_solarvault.coordinator.time.monotonic",
            lambda: _NOW,
        )
    # pyrefly: ignore [no-any-return-implicit]
    return coordinator


def test_system_info_cache_never_overwrites_a_delivered_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fill-only: a fresh workModel from HTTP/MQTT must survive the overlay."""
    coordinator = _bare_coordinator(monkeypatch)
    coordinator._system_info_cache[_DEVICE] = {"workModel": _CACHED_WORK_MODEL}  # ruff: ignore[private-member-access]
    coordinator._system_info_cache_monotonic[_DEVICE] = _NOW  # ruff: ignore[private-member-access]

    filled = coordinator._overlay_cached_system_info(  # ruff: ignore[private-member-access]
        _DEVICE,
        {"workModel": _FRESH_WORK_MODEL, "standbyPw": None},
    )

    assert filled["workModel"] == _FRESH_WORK_MODEL


def test_system_info_cache_fills_missing_keys_while_fresh(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The cache still bridges MQTT disconnects for keys HTTP never carries."""
    coordinator = _bare_coordinator(monkeypatch)
    coordinator._system_info_cache[_DEVICE] = {"workModel": _CACHED_WORK_MODEL}  # ruff: ignore[private-member-access]
    coordinator._system_info_cache_monotonic[_DEVICE] = _NOW - 10.0  # ruff: ignore[private-member-access]

    filled = coordinator._overlay_cached_system_info(  # ruff: ignore[private-member-access]
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
    coordinator._system_info_cache[_DEVICE] = {"workModel": _CACHED_WORK_MODEL}  # ruff: ignore[private-member-access]
    coordinator._system_info_cache_monotonic[_DEVICE] = (  # ruff: ignore[private-member-access]
        _NOW - SYSTEM_INFO_CACHE_MAX_AGE_SEC - 1.0
    )

    filled = coordinator._overlay_cached_system_info(  # ruff: ignore[private-member-access]
        _DEVICE,
        {"soc": _PASSTHROUGH_SOC},
    )

    assert "workModel" not in filled


async def test_system_info_queries_use_ble_without_cloud_mqtt() -> None:
    """Read queries still run when BLE is the only usable command transport."""
    coordinator = _bare_coordinator(None)
    coordinator._mqtt = None  # ruff: ignore[private-member-access]
    coordinator._ble_listener = SimpleNamespace()  # ruff: ignore[private-member-access]
    cast("Any", coordinator)._ble_writes_enabled = lambda: True  # ruff: ignore[private-member-access]
    coordinator._system_info_query_interval_sec = 180  # ruff: ignore[private-member-access]
    coordinator._last_system_info_query = {  # ruff: ignore[private-member-access]
        _DEVICE: time.monotonic() - coordinator._system_info_query_interval_sec - 1  # ruff: ignore[private-member-access]
    }
    coordinator.data = {_DEVICE: {PAYLOAD_PROPERTIES: {}}}
    query_device_info = AsyncMock(return_value=None)
    query_system_info = AsyncMock(return_value=None)
    cast("Any", coordinator).async_query_device_info = query_device_info
    cast("Any", coordinator).async_query_system_info = query_system_info

    await coordinator._async_query_system_info_for_missing(  # ruff: ignore[private-member-access]
        force=True, ensure_mqtt=False
    )

    query_system_info.assert_awaited_once_with(_DEVICE, ensure_mqtt=False)
    query_device_info.assert_awaited_once_with(_DEVICE, ensure_mqtt=False)


async def test_ble_read_queries_do_not_wait_for_setter_acks() -> None:
    """Commands 106/110/120 use BLE data responses; setters keep ACKs."""
    coordinator = _bare_coordinator(None)
    coordinator._mqtt = None  # ruff: ignore[private-member-access]
    send_ble = AsyncMock(return_value=True)
    cast("Any", coordinator).async_send_ble_command = send_ble

    for action_id, cmd, expected_ack in (
        (3011, 106, False),
        (3014, 110, False),
        (3019, 120, False),
        (3001, 1, True),
    ):
        operations = coordinator._command_transport_operations(  # ruff: ignore[private-member-access]
            _DEVICE,
            cmd,
            cast(
                "Any",
                {
                    "action_id": action_id,
                    "body_fields": {},
                    "message_type": "test",
                    "ensure_mqtt": False,
                },
            ),
            cast("Any", SimpleNamespace()),
        )
        assert [label for label, _operation in operations] == ["BLE"]
        await operations[0][1]
        # pyrefly: ignore [missing-attribute]
        assert send_ble.await_args.kwargs["wait_for_ack"] is expected_ack


async def test_system_info_query_skips_without_any_command_transport() -> None:
    """No BLE and no connected cloud client: the query stays skipped."""
    coordinator = _bare_coordinator(None)
    coordinator._mqtt = None  # ruff: ignore[private-member-access]
    coordinator._ble_listener = None  # ruff: ignore[private-member-access]
    coordinator._last_system_info_query = {}  # ruff: ignore[private-member-access]
    coordinator._system_info_query_interval_sec = 180  # ruff: ignore[private-member-access]
    coordinator.data = {_DEVICE: {PAYLOAD_PROPERTIES: {}}}
    query_system_info = AsyncMock(return_value=None)
    cast("Any", coordinator).async_query_system_info = query_system_info

    await coordinator._async_query_system_info_for_missing(ensure_mqtt=False)  # ruff: ignore[private-member-access]

    query_system_info.assert_not_awaited()
