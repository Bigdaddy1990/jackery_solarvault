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
    raw._statistics_backfill_task = None  # ruff: ignore[private-member-access]
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


def test_slow_cache_day_curve_reaches_statistics_import() -> None:
    """A slow HTTP day response keeps its _day key in the Recorder snapshot."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    raw = cast("Any", coordinator)
    raw._shutdown_started = False  # ruff: ignore[private-member-access]
    raw._statistics_import_ready = True  # ruff: ignore[private-member-access]
    raw._statistics_backfill_task = None  # ruff: ignore[private-member-access]
    raw._last_stat_import_monotonic = float("-inf")  # ruff: ignore[private-member-access]
    chart = {"unit": "W", "y": [600], "totalSolarEnergy": "0.05"}
    raw._slow_cache = {  # ruff: ignore[private-member-access]
        "dev:device-1": {
            "device_pv_stat_day": (0.0, chart),
            "device_ct_stat_day": (0.0, {"total": 1}),
        }
    }
    received: list[dict[str, dict[str, Any]]] = []

    with patch.object(
        coordinator, "_schedule_statistics_backfill", side_effect=received.append
    ):
        coordinator._schedule_statistics_import({  # ruff: ignore[private-member-access]
            "device-1": {"device": {}, "properties": {"soc": 50}}
        })

    assert received[0]["device-1"]["device_pv_stat_day"] == chart
    assert received[0]["device-1"]["device_ct_stat_day"] == {"total": 1}
    assert received[0]["device-1"]["properties"] == {"soc": 50}
