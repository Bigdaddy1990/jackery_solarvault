"""Background slow-metric refresh timeout + cancel-propagation tests.

These tests exercise the Layer 5 background refresh in isolation. The
background workload is wrapped in an ``asyncio.timeout`` ceiling so a single
hung slow endpoint cannot let cache staleness grow unbounded within a cycle,
and a cancelled refresh must re-raise so the cancel-on-restack guard in
``_launch_background_slow_refresh`` actually prevents task stacking.

Only the background seams are mocked (device refreshers, ``get_with_ttl``,
``hass.async_create_background_task`` and ``async_request_refresh``). No
network or file IO is touched, and no real sleeps are used.
"""

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault import coordinator as coordinator_module
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

# Patched-down ceiling so a real hang surfaces fast via the test's own
# wait_for guard rather than waiting on the production 120 s constant.
_FAST_TIMEOUT_SEC = 0.01


class _FakeHass:
    """Minimal hass stub that runs background tasks on the running loop."""

    @staticmethod
    def async_create_background_task(
        coro: Awaitable[Any],
        name: str,
    ) -> asyncio.Future[Any]:
        """Schedule ``coro`` on the loop, mirroring HA's helper contract."""
        return asyncio.ensure_future(coro)


def _make_coordinator(
    device_refreshers: list[Callable[[], Awaitable[Any]]],
) -> JackerySolarVaultCoordinator:
    """Build a bare coordinator wired only with the background seams."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._slow_metrics_bg_task = None  # noqa: SLF001
    coordinator.hass = _FakeHass()  # type: ignore[assignment]
    coordinator.async_request_refresh = AsyncMock()  # type: ignore[method-assign]
    coordinator._slow_metrics_interval_sec = 60  # noqa: SLF001
    coordinator._price_config_interval_sec = 600  # noqa: SLF001
    return coordinator


async def _noop_get_with_ttl(  # noqa: RUF029
    *_args: object,
    **_kwargs: object,
) -> dict[str, Any]:
    """Stand in for the cache fetcher without doing any IO."""
    return {}


async def test_background_refresh_times_out_on_hung_endpoint(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A hung device refresher aborts the cycle without a refresh request."""
    monkeypatch.setattr(
        coordinator_module,
        "BACKGROUND_SLOW_REFRESH_TIMEOUT_SEC",
        _FAST_TIMEOUT_SEC,
    )

    async def _hang() -> None:
        await asyncio.Event().wait()

    coordinator = _make_coordinator([_hang])

    with caplog.at_level("WARNING"):
        coordinator._launch_background_slow_refresh(  # noqa: SLF001
            {"sys-1"},
            _noop_get_with_ttl,
            device_refreshers=[_hang],
        )
        # wait_for guards the suite: pre-fix the infinite hang surfaces here
        # as a fast TimeoutError instead of blocking forever.
        await asyncio.wait_for(
            coordinator._slow_metrics_bg_task,  # noqa: SLF001
            timeout=2.0,
        )

    assert coordinator._slow_metrics_bg_task.done()  # noqa: SLF001
    assert "timed out" in caplog.text
    coordinator.async_request_refresh.assert_not_awaited()


async def test_background_refresh_completes_when_fast() -> None:
    """A fast device refresher runs and triggers a coordinator refresh."""
    ran: list[str] = []

    async def _fast() -> None:  # noqa: RUF029
        ran.append("fast")

    coordinator = _make_coordinator([_fast])

    coordinator._launch_background_slow_refresh(  # noqa: SLF001
        set(),
        _noop_get_with_ttl,
        device_refreshers=[_fast],
    )
    await coordinator._slow_metrics_bg_task  # noqa: SLF001

    assert ran == ["fast"]
    coordinator.async_request_refresh.assert_awaited_once()


async def test_background_refresh_cancel_propagates() -> None:
    """Cancelling the background task must re-raise CancelledError.

    A swallowed CancelledError would let the old task complete normally,
    defeating the cancel-on-restack guard and letting tasks stack.
    """

    async def _hang() -> None:
        await asyncio.Event().wait()

    coordinator = _make_coordinator([_hang])

    coordinator._launch_background_slow_refresh(  # noqa: SLF001
        {"sys-1"},
        _noop_get_with_ttl,
        device_refreshers=[_hang],
    )
    await asyncio.sleep(0)
    coordinator._slow_metrics_bg_task.cancel()  # noqa: SLF001

    with pytest.raises(asyncio.CancelledError):
        await coordinator._slow_metrics_bg_task  # noqa: SLF001
