"""Behavioral tests for independent week/month/year HTTP backfill queues."""

import asyncio
from datetime import date
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault import coordinator as coordinator_module
from custom_components.jackery_solarvault.const import (
    APP_SECTION_CT_STAT,
    APP_SECTION_PV_STAT,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    CONF_ENABLE_MONTH_STATISTICS,
    CONF_ENABLE_WEEK_STATISTICS,
    CONF_ENABLE_YEAR_STATISTICS,
    CT_STAT_TYPE_L1,
    CT_STAT_TYPE_L2,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from tests._update_cycle_fixture import SYSTEM_ID  # ruff:ignore[banned-api]

_DEVICE_ID = "device-1"
_TODAY = date(2026, 7, 23)
_ONE_PV_METRIC = (
    (
        APP_SECTION_PV_STAT,
        APP_STAT_TOTAL_SOLAR_ENERGY,
        "pv_energy",
        "PV energy",
    ),
)


def _coordinator(
    *,
    today: date = _TODAY,
    **options: object,
) -> JackerySolarVaultCoordinator:
    """Build the persistent-state slice used by period backfill."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    obj = cast("Any", coordinator)
    obj.entry = SimpleNamespace(options=options, data={})
    obj._statistics_import_diagnostics = {}
    obj._statistics_backfill_state = {
        "devices": {
            _DEVICE_ID: {
                "http_period_backfill": {
                    "sources": {
                        "device_pv_stat": {
                            "month": {
                                "2026-01-01": {
                                    "status": "pending",
                                    "period_open": False,
                                },
                                "2026-02-01": {
                                    "status": "pending",
                                    "period_open": False,
                                },
                            },
                            "year": {
                                "2026-01-01": {
                                    "status": "pending",
                                    "period_open": True,
                                },
                            },
                        }
                    }
                }
            }
        }
    }
    obj._statistics_backfill_state_loaded = True
    obj._async_save_statistics_backfill_state = AsyncMock()
    obj._local_today = lambda: today
    obj._device_index = {_DEVICE_ID: {"id": SYSTEM_ID, "systemId": SYSTEM_ID}}
    obj._slow_http_request_semaphore = asyncio.Semaphore(2)
    obj._repair_containment_violations = lambda **_kwargs: set()
    obj._import_collected_repair_buckets = AsyncMock(return_value=(1, 0))
    return coordinator


@pytest.mark.asyncio
async def test_period_backfill_shares_slow_http_concurrency_gate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bounded period request must wait for the shared slow-HTTP gate."""
    monkeypatch.setattr(
        coordinator_module,
        "APP_CHART_STAT_METRICS",
        _ONE_PV_METRIC,
    )
    coordinator = _coordinator()
    coordinator._slow_http_request_semaphore = asyncio.Semaphore(1)
    fetch_started = asyncio.Event()

    # Mock the API call that _async_fetch_historical_app_chart_source makes
    async def _mock_pv_stat(*_args: object, **_kwargs: object) -> dict[str, object]:  # ruff: ignore[unused-async]
        fetch_started.set()
        return {}

    cast("Any", coordinator).api = SimpleNamespace(
        async_get_device_pv_stat=_mock_pv_stat,
    )
    await coordinator._slow_http_request_semaphore.acquire()
    task = asyncio.create_task(
        coordinator._async_http_backfill_period_statistics(
            {_DEVICE_ID: {"id": SYSTEM_ID, "systemId": SYSTEM_ID}},
            request_budget=1,
        ),
    )

    # Yield control to let the task start and block on the semaphore
    await asyncio.sleep(0)
    try:
        assert not fetch_started.is_set()
    finally:
        coordinator._slow_http_request_semaphore.release()

    await task
    assert fetch_started.is_set()


def _source(period_start: date) -> dict[str, object]:
    """Return one gated kWh chart source anchored to its requested period."""
    return {
        "unit": "kWh",
        "y": [1.0],
        "_request": {
            "beginDate": period_start.isoformat(),
            "endDate": period_start.isoformat(),
        },
    }


@pytest.mark.asyncio
async def test_ct_period_retries_l2_when_l1_chart_is_empty() -> None:
    """Closed CT backfill periods follow the App's L2 path if L1 is empty."""
    coordinator = _coordinator()
    raw = cast("Any", coordinator)
    raw.api = SimpleNamespace(
        async_get_device_ct_stat=AsyncMock(
            side_effect=[
                {
                    "unit": "kWh",
                    "x": [],
                    "y1": [],
                    "y2": [],
                    "totalInCtEnergy": 0,
                    "totalOutCtEnergy": 0,
                },
                {
                    "unit": "kWh",
                    "x": ["2026-04-15"],
                    "y1": [4.0],
                    "y2": [0.5],
                    "totalInCtEnergy": 4.0,
                    "totalOutCtEnergy": 0.5,
                },
            ],
        ),
    )

    source = await coordinator._async_fetch_historical_app_chart_source(
        device_id=_DEVICE_ID,
        system_id=SYSTEM_ID,
        ct_device_id="ct-device-1",
        section_prefix=APP_SECTION_CT_STAT,
        date_type=DATE_TYPE_MONTH,
        period_start=date(2026, 4, 1),
    )

    assert source["totalInCtEnergy"] == pytest.approx(4.0)
    assert [
        call.kwargs["stat_type"]
        for call in raw.api.async_get_device_ct_stat.await_args_list
    ] == [CT_STAT_TYPE_L1, CT_STAT_TYPE_L2]


@pytest.mark.asyncio
async def test_closed_months_then_weeks_skip_open_periods(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Closed months lead closed weeks; active month/year stay unqueued."""
    monkeypatch.setattr(
        coordinator_module,
        "APP_CHART_STAT_METRICS",
        _ONE_PV_METRIC,
    )
    coordinator = _coordinator()
    requested: list[tuple[str, date]] = []

    def _fetch(
        *,
        date_type: str,
        period_start: date,
        **_kwargs: object,
    ) -> dict[str, object]:
        requested.append((date_type, period_start))
        return _source(period_start)

    cast("Any", coordinator)._async_fetch_historical_app_chart_source = AsyncMock(
        side_effect=_fetch,
    )

    await coordinator._async_http_backfill_period_statistics(
        {_DEVICE_ID: {}},
        request_budget=8,
    )

    assert [date_type for date_type, _start in requested[:6]] == [DATE_TYPE_MONTH] * 6
    assert [date_type for date_type, _start in requested[6:]] == [DATE_TYPE_WEEK] * 2
    assert DATE_TYPE_YEAR not in {date_type for date_type, _start in requested}


@pytest.mark.asyncio
async def test_disabled_periods_are_never_queued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Setup opt-outs independently remove month/year while week keeps running."""
    monkeypatch.setattr(
        coordinator_module,
        "APP_CHART_STAT_METRICS",
        _ONE_PV_METRIC,
    )
    coordinator = cast("Any", _coordinator)(
        **{
            CONF_ENABLE_MONTH_STATISTICS: False,
            CONF_ENABLE_YEAR_STATISTICS: False,
        },
    )
    fetch = AsyncMock(return_value=_source(date(2026, 1, 1)))
    cast("Any", coordinator)._async_fetch_historical_app_chart_source = fetch

    await coordinator._async_http_backfill_period_statistics(
        {_DEVICE_ID: {}},
        request_budget=1,
    )

    call = fetch.await_args
    assert call is not None
    assert call.kwargs["date_type"] == DATE_TYPE_WEEK


@pytest.mark.asyncio
async def test_all_period_opt_outs_make_no_http_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Disabling every optional period leaves the separate day queue untouched."""
    monkeypatch.setattr(
        coordinator_module,
        "APP_CHART_STAT_METRICS",
        _ONE_PV_METRIC,
    )
    coordinator = cast("Any", _coordinator)(
        **{
            CONF_ENABLE_MONTH_STATISTICS: False,
            CONF_ENABLE_WEEK_STATISTICS: False,
            CONF_ENABLE_YEAR_STATISTICS: False,
        },
    )
    fetch = AsyncMock()
    cast("Any", coordinator)._async_fetch_historical_app_chart_source = fetch

    result = await coordinator._async_http_backfill_period_statistics(
        {_DEVICE_ID: {}},
    )

    assert result["requests"] == 0
    assert result["pending_sources"] == 0
    fetch.assert_not_awaited()


@pytest.mark.asyncio
async def test_new_calendar_period_is_added_incrementally(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """After startup, an imported month stays done and only the new month is fetched."""
    monkeypatch.setattr(
        coordinator_module,
        "APP_CHART_STAT_METRICS",
        _ONE_PV_METRIC,
    )
    coordinator = _coordinator(
        today=date(2026, 1, 31),
        **{
            CONF_ENABLE_WEEK_STATISTICS: False,
            CONF_ENABLE_YEAR_STATISTICS: False,
        },
    )
    requested: list[date] = []

    def _fetch(
        *,
        period_start: date,
        **_kwargs: object,
    ) -> dict[str, object]:
        requested.append(period_start)
        return _source(period_start)

    cast("Any", coordinator)._async_fetch_historical_app_chart_source = AsyncMock(
        side_effect=_fetch,
    )
    await coordinator._async_http_backfill_period_statistics(
        {_DEVICE_ID: {}},
        request_budget=1,
    )

    cast("Any", coordinator)._local_today = lambda: date(2026, 2, 1)
    await coordinator._async_http_backfill_period_statistics(
        {_DEVICE_ID: {}},
        request_budget=1,
    )

    # January is skipped while open. The first February run fetches it once
    # after it closes; the new active February bucket stays on the normal poll.
    assert requested == [date(2026, 1, 1)]


@pytest.mark.asyncio
async def test_period_transport_failure_uses_retry_cooldown(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A transient period failure finishes bootstrap but remains retryable."""
    monkeypatch.setattr(
        coordinator_module,
        "APP_CHART_STAT_METRICS",
        _ONE_PV_METRIC,
    )
    coordinator = _coordinator(
        today=date(2026, 2, 2),
        **{
            CONF_ENABLE_WEEK_STATISTICS: False,
            CONF_ENABLE_YEAR_STATISTICS: False,
        },
    )
    fetch = AsyncMock(side_effect=TimeoutError)
    cast("Any", coordinator)._async_fetch_historical_app_chart_source = fetch

    for _attempt in range(
        coordinator_module._STATISTICS_HTTP_TRANSPORT_ERROR_MAX_ATTEMPTS
    ):
        result = await coordinator._async_http_backfill_period_statistics(
            {_DEVICE_ID: {}},
            request_budget=1,
        )

    bucket_state = cast("Any", coordinator)._statistics_backfill_state["devices"][
        _DEVICE_ID
    ]["http_period_backfill"]["sources"][APP_SECTION_PV_STAT][DATE_TYPE_MONTH][
        "2026-01-01"
    ]
    assert bucket_state["status"] == "retryable"
    assert bucket_state["retry_after_epoch"] > 0
    assert result["pending_sources"] == 1
    assert result["actionable_sources"] == 0

    immediate_retry = await coordinator._async_http_backfill_period_statistics(
        {_DEVICE_ID: {}},
        request_budget=1,
    )
    assert immediate_retry["requests"] == 0
