"""Regression tests for independent live-statistics and history scheduling."""

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from custom_components.jackery_solarvault.coordinator import (
    _STATISTICS_IMPORT_THROTTLE_SEC,  # ruff: ignore[import-private-name]
    JackerySolarVaultCoordinator,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine


def test_startup_backfill_does_not_slow_current_statistics_imports() -> None:
    """Pending startup history must not change the current import cadence."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    raw = cast("Any", coordinator)
    raw._shutdown_started = False  # ruff: ignore[private-member-access]
    raw._statistics_import_ready = True  # ruff: ignore[private-member-access]
    raw._statistics_import_task = None  # ruff: ignore[private-member-access]
    raw._statistics_startup_sync_pending = True  # ruff: ignore[private-member-access]
    raw._slow_metrics_interval_sec = _STATISTICS_IMPORT_THROTTLE_SEC * 10  # ruff: ignore[private-member-access]
    raw._last_stat_import_monotonic = 100.0  # ruff: ignore[private-member-access]

    created: list[Coroutine[Any, Any, Any]] = []

    def _create_background_task(
        coro: Coroutine[Any, Any, Any],
        **_kwargs: Any,
    ) -> Any:
        created.append(coro)
        coro.close()
        return MagicMock(done=MagicMock(return_value=False))

    raw.hass = SimpleNamespace(async_create_background_task=_create_background_task)

    with patch(
        "custom_components.jackery_solarvault.coordinator.time.monotonic",
        return_value=100.0 + _STATISTICS_IMPORT_THROTTLE_SEC + 0.1,
    ):
        coordinator._schedule_statistics_import({  # ruff: ignore[private-member-access]
            "device-1": {"device": {"deviceSn": "SV3PM123456"}}
        })

    assert len(created) == 1


async def test_completed_startup_keeps_one_backfill_running() -> None:
    """Later imports must fill new history while keeping one backfill task."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    raw = cast("Any", coordinator)
    raw._shutdown_started = False  # ruff: ignore[private-member-access]
    raw._statistics_startup_sync_pending = False  # ruff: ignore[private-member-access]
    raw._statistics_import_task = None  # ruff: ignore[private-member-access]
    raw._statistics_backfill_task = None  # ruff: ignore[private-member-access]
    release_history = asyncio.Event()
    snapshot = {"device-1": {"device": {"deviceSn": "SV3PM123456"}}}
    created: list[asyncio.Task[None]] = []

    def create_task(
        coro: Coroutine[Any, Any, None],
        **_kwargs: Any,
    ) -> asyncio.Task[None]:
        task = asyncio.create_task(coro)
        created.append(task)
        return task

    async def advance_history(_snapshot: dict[str, dict[str, Any]]) -> None:
        await release_history.wait()

    with (
        patch.object(
            coordinator, "_create_entry_background_task", side_effect=create_task
        ),
        patch.object(
            coordinator,
            "_async_import_and_repair_app_chart_statistics",
            new_callable=AsyncMock,
        ) as import_current,
        patch.object(
            coordinator,
            "_async_advance_statistics_backfill",
            side_effect=advance_history,
        ) as advance,
    ):
        try:
            await coordinator._async_statistics_import_job(snapshot)  # ruff: ignore[private-member-access]
            await asyncio.sleep(0)
            advance.assert_awaited_once_with(snapshot)
            import_current.reset_mock()
            await coordinator._async_statistics_import_job(snapshot)  # ruff: ignore[private-member-access]
            import_current.assert_awaited_once_with(snapshot)
            assert len(created) == 1
        finally:
            release_history.set()
            await asyncio.gather(*created)
