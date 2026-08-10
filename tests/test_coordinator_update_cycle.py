"""Behavioral tests for the guarded coordinator update cycle's guard rails.

These drive ``_async_update_data_guarded`` through its non-orchestration guard
paths: the deferred background-auth notice is cleared without pausing the HTTP
poll (the HTTP fetch owns reauth, never MQTT/BLE), the local SystemBody fill is
dispatched off the critical path, and an empty device index after discovery
raises ``UpdateFailed`` rather than returning stale data. Only integration
boundaries are mocked — the background-task launcher, discovery, and the
off-critical-path SystemBody query — so the guard logic itself runs for real.
"""

import asyncio
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from homeassistant.helpers.update_coordinator import UpdateFailed


def _consume_background_task(
    coro: Any,  # ruff:ignore[any-type]
    *,
    name: str,
    eager_start: bool,
) -> asyncio.Task[None]:
    """Close a scheduled coroutine so it never warns about being un-awaited."""
    del name, eager_start
    coro.close()
    return asyncio.create_task(asyncio.sleep(0))


def _guarded_coordinator(
    *,
    auth_failure_message: str | None = None,
) -> Any:  # ruff:ignore[any-type]
    """Build a bare coordinator wired for the guarded-update guard paths."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    obj = cast("Any", coordinator)
    obj._mqtt_mgr = SimpleNamespace(auth_failure_message=auth_failure_message)  # ruff: ignore[private-member-access]
    obj._mqtt = None  # ruff: ignore[private-member-access]
    obj._device_index = {}  # ruff: ignore[private-member-access]
    obj._shutdown_started = False  # ruff: ignore[private-member-access]
    obj._background_tasks = {}  # ruff: ignore[private-member-access]
    obj.hass = SimpleNamespace(async_create_background_task=_consume_background_task)
    obj._async_query_system_info_for_missing = AsyncMock()  # ruff: ignore[private-member-access]
    obj.async_discover = AsyncMock()
    return coordinator


@pytest.mark.asyncio()
async def test_guarded_update_raises_when_no_devices_discovered() -> None:
    """An empty index after discovery fails the cycle instead of returning data."""
    coordinator = _guarded_coordinator()

    with pytest.raises(UpdateFailed, match="No Jackery devices found"):
        await coordinator._async_update_data_guarded()  # ruff: ignore[private-member-access]

    coordinator.async_discover.assert_awaited_once()


@pytest.mark.asyncio()
async def test_guarded_update_clears_deferred_auth_notice_without_pausing() -> None:
    """A background auth notice is cleared; the HTTP poll continues to discovery.

    The invariant: MQTT/BLE rejections must never pause the HTTP path or open
    reauth. The guarded update consumes the deferred notice and keeps polling
    (here reaching the no-devices failure), proving the notice did not short
    -circuit the cycle.
    """
    coordinator = _guarded_coordinator(auth_failure_message="broker rejected")

    with pytest.raises(UpdateFailed, match="No Jackery devices found"):
        await coordinator._async_update_data_guarded()  # ruff: ignore[private-member-access]

    # The deferred notice was consumed rather than re-raised as reauth.
    assert coordinator._mqtt_mgr.auth_failure_message is None  # ruff: ignore[private-member-access]


@pytest.mark.asyncio()
async def test_guarded_update_dispatches_system_body_fill_off_critical_path() -> None:
    """The SystemBody fill is scheduled without ``ensure_mqtt`` on every cycle."""
    coordinator = _guarded_coordinator()

    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data_guarded()  # ruff: ignore[private-member-access]

    coordinator._async_query_system_info_for_missing.assert_called_once_with(  # ruff: ignore[private-member-access]
        ensure_mqtt=False,
    )
