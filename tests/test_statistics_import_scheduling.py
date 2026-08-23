"""Regression tests for independent live-statistics and history scheduling."""

from collections.abc import Coroutine
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import MagicMock, patch

from custom_components.jackery_solarvault.coordinator import (
    _STATISTICS_IMPORT_THROTTLE_SEC,
    JackerySolarVaultCoordinator,
)


def test_startup_backfill_does_not_slow_current_statistics_imports() -> None:
    """Pending startup history must not change the current import cadence."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    raw = cast("Any", coordinator)
    raw._shutdown_started = False
    raw._statistics_import_ready = True
    raw._statistics_import_task = None
    raw._statistics_startup_sync_pending = True
    raw._slow_metrics_interval_sec = _STATISTICS_IMPORT_THROTTLE_SEC * 10
    raw._last_stat_import_monotonic = 100.0

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
        coordinator._schedule_statistics_import(
            {"device-1": {"device": {"deviceSn": "SV3PM123456"}}}
        )

    assert len(created) == 1
