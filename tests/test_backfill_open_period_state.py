"""Contracts for open/closed HTTP statistics backfill state."""

import asyncio
from datetime import date
import json
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault import coordinator as coordinator_module
from custom_components.jackery_solarvault.const import (
    APP_SECTION_PV_STAT,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    CONF_ENABLE_MONTH_STATISTICS,
    CONF_ENABLE_WEEK_STATISTICS,
    CONF_ENABLE_YEAR_STATISTICS,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
)
from custom_components.jackery_solarvault.coordinator import (
    BackfillStatus,
    JackerySolarVaultCoordinator,
    _backfill_period_is_closed,  # ruff: ignore[import-private-name]
)
from custom_components.jackery_solarvault.util import (
    backfill_year_payload_from_months,
    verify_and_backfill,
)

_DEVICE_ID = "device-1"
_ONE_PV_METRIC = (
    (
        APP_SECTION_PV_STAT,
        APP_STAT_TOTAL_SOLAR_ENERGY,
        "pv_energy",
        "PV energy",
    ),
)


def _coordinator(today: date) -> JackerySolarVaultCoordinator:
    """Build only the state required by the bounded period queue."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    obj = cast("Any", coordinator)
    obj.entry = SimpleNamespace(
        options={
            CONF_ENABLE_MONTH_STATISTICS: True,
            CONF_ENABLE_WEEK_STATISTICS: False,
            CONF_ENABLE_YEAR_STATISTICS: False,
        },
        data={},
    )
    obj._statistics_import_diagnostics = {}  # ruff: ignore[private-member-access]
    obj._statistics_backfill_state = {"devices": {}}  # ruff: ignore[private-member-access]
    obj._statistics_backfill_state_loaded = True  # ruff: ignore[private-member-access]
    obj._async_save_statistics_backfill_state = AsyncMock()  # ruff: ignore[private-member-access]
    obj._local_today = lambda: today  # ruff: ignore[private-member-access]
    obj._device_index = {}  # ruff: ignore[private-member-access]
    obj._slow_http_request_semaphore = asyncio.Semaphore(2)  # ruff: ignore[private-member-access]
    obj._repair_containment_violations = lambda **_kwargs: set()  # ruff: ignore[private-member-access]
    obj._import_collected_repair_buckets = AsyncMock(return_value=(1, 0))  # ruff: ignore[private-member-access]
    obj._async_fetch_historical_app_chart_source = AsyncMock(return_value={})  # ruff: ignore[private-member-access]
    obj.data = None
    obj._push_partial_update = lambda new_data: setattr(obj, "data", new_data)  # ruff: ignore[private-member-access]
    return coordinator


def test_backfill_status_has_only_the_serializable_contract_states() -> None:
    """Persisted states are stable JSON strings, not transient fetch outcomes."""
    assert {status.value for status in BackfillStatus} == {
        "pending",
        "retryable",
        "imported",
        "unavailable_closed",
    }
    assert json.loads(json.dumps(BackfillStatus.RETRYABLE)) == "retryable"


@pytest.mark.parametrize(
    ["date_type", "period_start"],
    [
        [DATE_TYPE_DAY, date(2026, 3, 2)],
        [DATE_TYPE_WEEK, date(2026, 3, 2)],
        [DATE_TYPE_MONTH, date(2026, 3, 1)],
        [DATE_TYPE_YEAR, date(2026, 1, 1)],
    ],
)
def test_current_period_is_never_closed(
    date_type: str,
    period_start: date,
) -> None:
    """The active day/week/month/year cannot enter a terminal unavailable state."""
    assert not _backfill_period_is_closed(
        date_type,
        period_start,
        today=date(2026, 3, 2),
    )


@pytest.mark.asyncio
async def test_two_empty_responses_only_terminalize_closed_months(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closed history may finish unavailable; the active month remains retryable."""
    monkeypatch.setattr(
        coordinator_module,
        "APP_CHART_STAT_METRICS",
        _ONE_PV_METRIC,
    )
    coordinator = _coordinator(date(2026, 3, 2))

    for _attempt in range(coordinator_module._STATISTICS_HTTP_EMPTY_MAX_ATTEMPTS):  # ruff: ignore[private-member-access]
        await coordinator._async_http_backfill_period_statistics(  # ruff: ignore[private-member-access]
            {_DEVICE_ID: {}},
            request_budget=3,
        )

    month_states = cast("Any", coordinator)._statistics_backfill_state["devices"][  # ruff: ignore[private-member-access]
        _DEVICE_ID
    ]["http_period_backfill"]["sources"][APP_SECTION_PV_STAT][DATE_TYPE_MONTH]
    assert month_states["2026-01-01"]["status"] == BackfillStatus.UNAVAILABLE_CLOSED
    assert month_states["2026-02-01"]["status"] == BackfillStatus.UNAVAILABLE_CLOSED
    assert month_states["2026-03-01"]["status"] == BackfillStatus.RETRYABLE
    assert "completed_at" not in month_states["2026-03-01"]


@pytest.mark.asyncio
async def test_legacy_unavailable_state_is_migrated_back_to_retryable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Old two-empty terminal markers get one deterministic safe migration."""
    monkeypatch.setattr(
        coordinator_module,
        "APP_CHART_STAT_METRICS",
        _ONE_PV_METRIC,
    )
    coordinator = _coordinator(date(2026, 3, 2))
    cast("Any", coordinator)._statistics_backfill_state = {  # ruff: ignore[private-member-access]
        "devices": {
            _DEVICE_ID: {
                "http_period_backfill": {
                    "sources": {
                        APP_SECTION_PV_STAT: {
                            DATE_TYPE_MONTH: {
                                "2026-02-01": {
                                    "status": "unavailable",
                                    "attempts": 2,
                                },
                                "2026-03-01": {
                                    "status": "unavailable",
                                    "attempts": 2,
                                },
                            }
                        }
                    }
                }
            }
        }
    }

    await coordinator._async_http_backfill_period_statistics(  # ruff: ignore[private-member-access]
        {_DEVICE_ID: {}},
        request_budget=0,
    )

    month_states = cast("Any", coordinator)._statistics_backfill_state["devices"][  # ruff: ignore[private-member-access]
        _DEVICE_ID
    ]["http_period_backfill"]["sources"][APP_SECTION_PV_STAT][DATE_TYPE_MONTH]
    assert month_states["2026-02-01"]["status"] == BackfillStatus.RETRYABLE
    assert month_states["2026-03-01"]["status"] == BackfillStatus.RETRYABLE


@pytest.mark.asyncio
async def test_imported_open_period_is_merged_into_coordinator_snapshot(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A successful Recorder import and the live entity snapshot stay coherent."""
    monkeypatch.setattr(
        coordinator_module,
        "APP_CHART_STAT_METRICS",
        _ONE_PV_METRIC,
    )
    coordinator = _coordinator(date(2026, 1, 2))
    source = {
        "unit": "kWh",
        "y": [5.0],
        APP_STAT_TOTAL_SOLAR_ENERGY: 5.0,
    }
    cast("Any", coordinator)._async_fetch_historical_app_chart_source = AsyncMock(  # ruff: ignore[private-member-access]
        return_value=source,
    )
    snapshot: dict[str, dict[str, Any]] = {_DEVICE_ID: {}}
    cast("Any", coordinator).data = {_DEVICE_ID: {}}

    await coordinator._async_http_backfill_period_statistics(  # ruff: ignore[private-member-access]
        snapshot,
        request_budget=1,
    )

    section = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}"
    assert snapshot[_DEVICE_ID][section] == source
    assert cast("Any", coordinator).data[_DEVICE_ID][section] == source


def test_current_month_is_included_in_current_year_reconstruction() -> None:
    """Explicit monthly data, including the active month, survives reconstruction."""
    result = backfill_year_payload_from_months(
        {
            "unit": "kWh",
            APP_STAT_TOTAL_SOLAR_ENERGY: 3.0,
            "y": [3.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        },
        APP_SECTION_PV_STAT,
        (APP_STAT_TOTAL_SOLAR_ENERGY,),
        {
            1: {APP_STAT_TOTAL_SOLAR_ENERGY: 3.0},
            7: {APP_STAT_TOTAL_SOLAR_ENERGY: 4.0},
        },
    )

    assert result[APP_STAT_TOTAL_SOLAR_ENERGY] == 7.0  # ruff: ignore[float-equality-comparison]
    assert result["y"][6] == 4.0  # ruff: ignore[float-equality-comparison]


def test_large_cloud_local_divergence_keeps_conservative_minimum() -> None:
    """The project-required ten-percent rule prevents Energy Dashboard spikes."""
    rejections: list[str] = []

    assert (
        verify_and_backfill(  # ruff: ignore[float-equality-comparison]
            12.0,
            8.0,
            label="pv_energy",
            on_rejection=rejections.append,
        )
        == 8.0
    )
    assert rejections == ["pv_energy:divergence"]
