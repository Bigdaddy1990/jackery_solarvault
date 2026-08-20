"""Structured-concurrency tests for coordinator-owned Layer-3 I/O."""

import asyncio
from typing import Any, cast

import pytest

from custom_components.jackery_solarvault.client import JackeryAuthError
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)


def _coordinator(limit: int = 2) -> JackerySolarVaultCoordinator:
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    cast("Any", coordinator)._http_request_semaphore = asyncio.Semaphore(limit)  # ruff: ignore[private-member-access]
    return coordinator


async def test_http_calls_are_keyed_and_bounded_with_partial_failure() -> None:
    """Slow devices overlap, retain stable keys and isolate one bad payload."""
    coordinator = _coordinator(2)
    active = maximum = 0

    async def request(value: str) -> str:
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        await asyncio.sleep(0.01)
        active -= 1
        if value == "broken":
            raise ValueError("invalid payload")
        return value

    calls = {
        ("device-a", "shadow"): lambda: request("a"),
        ("device-b", "ct-history"): lambda: request("broken"),
        ("device-c", "eps-history"): lambda: request("c"),
    }
    results = await coordinator._async_http_calls(calls)  # ruff: ignore[private-member-access]

    assert maximum == 2
    assert results["device-a", "shadow"] == "a"
    assert isinstance(results["device-b", "ct-history"], ValueError)
    assert results["device-c", "eps-history"] == "c"


async def test_http_auth_failure_cancels_slow_sibling_immediately() -> None:
    """An authentication rejection escapes and TaskGroup cancels peers."""
    coordinator = _coordinator()
    cancelled = asyncio.Event()

    async def slow() -> None:
        try:
            await asyncio.sleep(60)
        finally:
            cancelled.set()

    async def rejected() -> None:
        await asyncio.sleep(0)
        raise JackeryAuthError("expired")

    with pytest.raises(JackeryAuthError):
        await coordinator._async_http_calls({  # ruff: ignore[private-member-access]
            ("device-a", "property"): slow,
            ("device-b", "property"): rejected,
        })
    assert cancelled.is_set()


async def test_http_batch_propagates_cancellation() -> None:
    """Unload cancellation is not converted into a per-device result."""
    coordinator = _coordinator(1)
    started = asyncio.Event()

    async def blocked() -> None:
        started.set()
        await asyncio.sleep(60)

    task = asyncio.create_task(  # test-owned task, not integration runtime work
        coordinator._async_http_calls({("device-a", "property"): blocked})  # ruff: ignore[private-member-access]
    )
    await started.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
