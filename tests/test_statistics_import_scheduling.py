"""Regression tests for independent live-statistics and history scheduling."""

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock, patch

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
        **_kwargs: Any,  # ruff: ignore[any-type]
    ) -> Any:  # ruff: ignore[any-type]
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
