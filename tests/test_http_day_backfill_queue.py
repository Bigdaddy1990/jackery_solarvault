"""White-box tests for the bounded persistent HTTP day-backfill queue."""

import asyncio
from datetime import date
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault import coordinator as coordinator_module
from custom_components.jackery_solarvault.const import (
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_CT_STAT,
    CT_STAT_TYPE_L1,
    CT_STAT_TYPE_L2,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

_DEVICE_ID = "device-1"
_TODAY = date(2026, 7, 23)
_REQUEST_BUDGET = 4


def _coordinator() -> JackerySolarVaultCoordinator:
    """Build the persistent-state slice used by the bounded backfill."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    obj = cast("Any", coordinator)
    obj._statistics_import_diagnostics = {}  # ruff: ignore[private-member-access]
    obj._last_statistics_http_backfill_monotonic = float("-inf")  # ruff: ignore[private-member-access]
    obj._statistics_backfill_state = {"devices": {}}  # ruff: ignore[private-member-access]
    obj._statistics_backfill_state_loaded = True  # ruff: ignore[private-member-access]
    obj._async_save_statistics_backfill_state = AsyncMock()  # ruff: ignore[private-member-access]
    obj._local_today = lambda: _TODAY  # ruff: ignore[private-member-access]
    obj._slow_http_request_semaphore = asyncio.Semaphore(2)  # ruff: ignore[private-member-access]
    obj.hass = SimpleNamespace(
        config=SimpleNamespace(time_zone="Europe/Berlin"),
    )
    obj.api = SimpleNamespace()
    return coordinator


@pytest.mark.asyncio()
async def test_day_backfill_shares_slow_http_concurrency_gate() -> None:
    """A bounded historical day request must wait for the shared HTTP gate."""
    coordinator = _coordinator()
    raw = cast("Any", coordinator)
    coordinator._slow_http_request_semaphore = asyncio.Semaphore(1)  # ruff: ignore[private-member-access]
    raw._historical_day_source_prefixes = lambda _device_id, _payload: (  # ruff: ignore[private-member-access]
        APP_SECTION_BATTERY_STAT,
    )
    fetch_started = asyncio.Event()

    # Mock the API call that _async_fetch_historical_day_chart_source makes
    async def _mock_battery_stat(  # ruff: ignore[unused-async]
        *_args: object, **_kwargs: object
    ) -> dict[str, object]:
        fetch_started.set()
        return {}

    raw.api.async_get_device_battery_stat = _mock_battery_stat
    await coordinator._slow_http_request_semaphore.acquire()  # ruff: ignore[private-member-access]
    task = asyncio.create_task(
        coordinator._async_http_backfill_recent_day_statistics(  # ruff: ignore[private-member-access]
            {_DEVICE_ID: {}},
            force=True,
            window_days=1,
            request_budget=1,
        ),
    )

    await asyncio.sleep(0)
    try:
        assert not fetch_started.is_set()
    finally:
        coordinator._slow_http_request_semaphore.release()  # ruff: ignore[private-member-access]

    await task
    assert fetch_started.is_set()


def test_automatic_day_backfill_horizon_reaches_mid_april() -> None:
    """The default July queue starts no later than the requested mid-April date."""
    days = JackerySolarVaultCoordinator._statistics_http_backfill_dates(  # ruff: ignore[private-member-access]
        _TODAY,
        window_days=coordinator_module._STATISTICS_HTTP_BACKFILL_WINDOW_DAYS,  # ruff: ignore[private-member-access]
    )

    assert days[0] <= date(2026, 4, 15)
    assert days[-1] == date(2026, 7, 22)


@pytest.mark.asyncio()
async def test_empty_historical_payload_is_not_reported_as_success() -> None:
    """An empty/gated cloud response cannot complete a source/day pair."""
    coordinator = _coordinator()

    result = await coordinator._async_import_historical_day_chart_statistics_for_device(  # ruff: ignore[private-member-access]
        device_id=_DEVICE_ID,
        payload={},
        section_sources={},
    )

    assert result == (None, 0)


@pytest.mark.asyncio()
@pytest.mark.parametrize(
    ["response", "expected_status"],
    [
        [{}, "empty_ambiguous"],
        [{"y": [1.0]}, "fetched"],
    ],
)
async def test_single_source_fetch_distinguishes_empty_from_data(
    response: dict[str, object],
    expected_status: str,
) -> None:
    """The queue keeps empty responses pending but accepts gated chart data."""
    coordinator = _coordinator()
    cast("Any", coordinator)._device_index = {}  # ruff: ignore[private-member-access]
    cast("Any", coordinator).api = SimpleNamespace(
        async_get_device_battery_stat=AsyncMock(return_value=response),
    )

    status, source = await coordinator._async_fetch_historical_day_chart_source(  # ruff: ignore[private-member-access]
        device_id=_DEVICE_ID,
        payload={},
        target_day=date(2026, 4, 15),
        section_prefix=APP_SECTION_BATTERY_STAT,
    )

    assert status == expected_status
    assert bool(source) is bool(response)


@pytest.mark.asyncio()
async def test_single_source_timeout_remains_retryable() -> None:
    """A temporary network timeout is persisted as pending transport failure."""
    coordinator = _coordinator()
    cast("Any", coordinator)._device_index = {}  # ruff: ignore[private-member-access]
    cast("Any", coordinator).api = SimpleNamespace(
        async_get_device_battery_stat=AsyncMock(side_effect=TimeoutError),
    )

    status, source = await coordinator._async_fetch_historical_day_chart_source(  # ruff: ignore[private-member-access]
        device_id=_DEVICE_ID,
        payload={},
        target_day=date(2026, 4, 15),
        section_prefix=APP_SECTION_BATTERY_STAT,
    )

    assert status == "transport_error"
    assert source == {}


@pytest.mark.asyncio()
async def test_ct_day_uses_cloud_response_directly() -> None:
    """The Jackery CT day endpoint is the only historical CT source."""
    coordinator = _coordinator()
    cast("Any", coordinator)._device_index = {}  # ruff: ignore[private-member-access]
    cloud_source = {
        "unit": "kWh",
        "x": ["00:00"],
        "y1": [2.0],
        "y2": [0.25],
        "totalInCtEnergy": 2.0,
        "totalOutCtEnergy": 0.25,
    }
    cast("Any", coordinator).api = SimpleNamespace(
        async_get_device_ct_stat=AsyncMock(return_value=cloud_source),
    )

    status, source = await coordinator._async_fetch_historical_day_chart_source(  # ruff: ignore[private-member-access]
        device_id=_DEVICE_ID,
        payload={},
        target_day=date(2026, 4, 15),
        section_prefix=APP_SECTION_CT_STAT,
    )

    assert status == "fetched"
    assert source == cloud_source


@pytest.mark.asyncio()
async def test_ct_day_retries_l2_when_l1_chart_is_empty() -> None:
    """A placeholder L1 CT chart does not hide the App's L2 CT path."""
    coordinator = _coordinator()
    cast("Any", coordinator)._device_index = {}  # ruff: ignore[private-member-access]
    l1_placeholder = {
        "unit": "kWh",
        "x": [],
        "y1": [],
        "y2": [],
        "totalInCtEnergy": 0,
        "totalOutCtEnergy": 0,
    }
    l2_source = {
        "unit": "kWh",
        "x": ["00:00"],
        "y1": [2.0],
        "y2": [0.25],
        "totalInCtEnergy": 2.0,
        "totalOutCtEnergy": 0.25,
    }
    api = SimpleNamespace(
        async_get_device_ct_stat=AsyncMock(side_effect=[l1_placeholder, l2_source]),
    )
    cast("Any", coordinator).api = api

    status, source = await coordinator._async_fetch_historical_day_chart_source(  # ruff: ignore[private-member-access]
        device_id=_DEVICE_ID,
        payload={},
        target_day=date(2026, 4, 15),
        section_prefix=APP_SECTION_CT_STAT,
    )

    assert status == "fetched"
    assert source == l2_source
    assert [
        call.kwargs["query"].stat_type
        for call in api.async_get_device_ct_stat.await_args_list
    ] == [CT_STAT_TYPE_L1, CT_STAT_TYPE_L2]


@pytest.mark.asyncio()
async def test_empty_ct_day_stays_empty_without_lan_fallback() -> None:
    """An empty CT envelope is reported ambiguous; no LAN fallback exists."""
    coordinator = _coordinator()
    cast("Any", coordinator)._device_index = {}  # ruff: ignore[private-member-access]
    cast("Any", coordinator).api = SimpleNamespace(
        async_get_device_ct_stat=AsyncMock(return_value={}),
    )

    status, source = await coordinator._async_fetch_historical_day_chart_source(  # ruff: ignore[private-member-access]
        device_id=_DEVICE_ID,
        payload={},
        target_day=date(2026, 4, 15),
        section_prefix=APP_SECTION_CT_STAT,
    )

    assert status == "empty_ambiguous"
    assert source == {}


@pytest.mark.asyncio()
async def test_failed_source_day_remains_pending_while_peer_completes() -> None:
    """Success for one source never marks another source/day as complete."""
    coordinator = _coordinator()
    cast("Any", coordinator)._historical_day_source_prefixes = (  # ruff: ignore[private-member-access]
        lambda _device_id, _payload: ("battery", "ct")
    )

    async def _fetch(
        *,
        section_prefix: str,
        **_kwargs: object,
    ) -> tuple[str, dict[str, object]]:
        await asyncio.sleep(0)
        if section_prefix == "battery":
            return "fetched", {"y": [1.0]}
        return "transport_error", {}

    cast("Any", coordinator)._async_fetch_historical_day_chart_source = _fetch  # ruff: ignore[private-member-access]
    cast(  # ruff: ignore[private-member-access]
        "Any",
        coordinator,
    )._async_import_historical_day_chart_statistics_for_device = AsyncMock(
        return_value=(True, 1),
    )

    await coordinator._async_http_backfill_recent_day_statistics(  # ruff: ignore[private-member-access]
        {_DEVICE_ID: {}},
        force=True,
    )

    day_key = coordinator_module.statistics_http_backfill_dates(
        _TODAY,
        window_days=coordinator_module._STATISTICS_HTTP_BACKFILL_WINDOW_DAYS,  # ruff: ignore[private-member-access]
    )[0].isoformat()
    sources = cast("Any", coordinator)._statistics_backfill_state["devices"][  # ruff: ignore[private-member-access]
        _DEVICE_ID
    ]["http_day_backfill"]["sources"]
    assert sources["battery"]["days"][day_key]["status"] == "imported"
    assert sources["ct"]["days"][day_key]["status"] == "pending"


@pytest.mark.asyncio()
async def test_empty_day_does_not_block_other_source_days() -> None:
    """An unavailable bucket remains pending while another day still advances."""
    coordinator = _coordinator()
    cast("Any", coordinator)._historical_day_source_prefixes = (  # ruff: ignore[private-member-access]
        lambda _device_id, _payload: ("battery",)
    )
    requested_days: list[date] = []

    async def _fetch(
        *,
        target_day: date,
        **_kwargs: object,
    ) -> tuple[str, dict[str, object]]:
        await asyncio.sleep(0)
        requested_days.append(target_day)
        if len(requested_days) == 1:
            return "empty_ambiguous", {}
        return "fetched", {"y": [1.0]}

    cast("Any", coordinator)._async_fetch_historical_day_chart_source = _fetch  # ruff: ignore[private-member-access]
    cast(  # ruff: ignore[private-member-access]
        "Any",
        coordinator,
    )._async_import_historical_day_chart_statistics_for_device = AsyncMock(
        return_value=(True, 1),
    )

    await coordinator._async_http_backfill_recent_day_statistics(  # ruff: ignore[private-member-access]
        {_DEVICE_ID: {}},
        force=True,
    )
    await coordinator._async_http_backfill_recent_day_statistics(  # ruff: ignore[private-member-access]
        {_DEVICE_ID: {}},
        force=True,
    )

    assert requested_days[1] != requested_days[0]


@pytest.mark.asyncio()
async def test_current_week_curves_stay_prioritized_in_chronological_order() -> None:
    """Verified totals do not postpone the hourly Recorder curves they lack."""
    coordinator = _coordinator()
    raw = cast("Any", coordinator)
    raw.data = {}
    raw._push_partial_update = lambda _update: None  # ruff: ignore[private-member-access]
    raw._historical_day_source_prefixes = lambda _device_id, _payload: (  # ruff: ignore[private-member-access]
        APP_SECTION_BATTERY_STAT,
    )
    stat_key = next(
        metric_stat_key
        for metric_section, metric_stat_key, _metric_key, _label in (
            coordinator_module.APP_CHART_STAT_METRICS
        )
        if metric_section == APP_SECTION_BATTERY_STAT
    )
    current_week_days = [
        date(2026, 7, 20),
        date(2026, 7, 21),
        date(2026, 7, 22),
    ]
    raw._statistics_backfill_state = {  # ruff: ignore[private-member-access]
        "devices": {
            _DEVICE_ID: {
                "http_day_backfill": {
                    "sources": {
                        APP_SECTION_BATTERY_STAT: {
                            "days": {
                                target_day.isoformat(): {
                                    "attempts": 0,
                                    "status": "pending",
                                    "verified_totals": {stat_key: 1.0},
                                }
                                for target_day in current_week_days
                            },
                        },
                    },
                },
            },
        },
    }
    requested_days: list[date] = []

    async def _fetch(
        *,
        target_day: date,
        **_kwargs: object,
    ) -> tuple[str, dict[str, object]]:
        await asyncio.sleep(0)
        requested_days.append(target_day)
        return "fetched", {"unit": "kWh", "y": [1.0]}

    raw._async_fetch_historical_day_chart_source = _fetch  # ruff: ignore[private-member-access]
    raw._record_verified_day_totals_update = lambda _updates, **_kwargs: {stat_key: 1.0}  # ruff: ignore[private-member-access]
    raw._async_import_historical_day_chart_statistics_for_device = AsyncMock(  # ruff: ignore[private-member-access]
        return_value=(True, 1),
    )

    await coordinator._async_http_backfill_recent_day_statistics(  # ruff: ignore[private-member-access]
        {_DEVICE_ID: {}},
        force=True,
        window_days=10,
        request_budget=len(current_week_days),
    )

    assert requested_days == current_week_days


@pytest.mark.asyncio()
async def test_transport_failure_is_deferred_not_permanently_lost() -> None:
    """Repeated network failures leave a cooldown-backed retry, not unavailable."""
    coordinator = _coordinator()
    cast("Any", coordinator)._historical_day_source_prefixes = (  # ruff: ignore[private-member-access]
        lambda _device_id, _payload: ("battery",)
    )
    cast("Any", coordinator)._async_fetch_historical_day_chart_source = AsyncMock(  # ruff: ignore[private-member-access]
        return_value=("transport_error", {}),
    )

    for _attempt in range(
        coordinator_module._STATISTICS_HTTP_TRANSPORT_ERROR_MAX_ATTEMPTS  # ruff: ignore[private-member-access]
    ):
        result = await coordinator._async_http_backfill_recent_day_statistics(  # ruff: ignore[private-member-access]
            {_DEVICE_ID: {}},
            force=True,
            window_days=1,
            request_budget=1,
        )

    day_key = (_TODAY.replace(day=_TODAY.day - 1)).isoformat()
    day_state = cast("Any", coordinator)._statistics_backfill_state["devices"][  # ruff: ignore[private-member-access]
        _DEVICE_ID
    ]["http_day_backfill"]["sources"]["battery"]["days"][day_key]
    assert day_state["status"] == "pending"
    assert "retry_after_epoch" not in day_state
    assert day_state["last_error"] == "transport_error"
    assert result["pending_sources"] == 1

    immediate_retry = await coordinator._async_http_backfill_recent_day_statistics(  # ruff: ignore[private-member-access]
        {_DEVICE_ID: {}},
        force=True,
        window_days=1,
        request_budget=1,
    )
    assert immediate_retry["requests"] == 1


@pytest.mark.asyncio()
async def test_repeated_empty_day_becomes_terminal_without_queue_loop() -> None:
    """A completed historical day with no source data stops rapid retries."""
    coordinator = _coordinator()
    cast("Any", coordinator)._historical_day_source_prefixes = (  # ruff: ignore[private-member-access]
        lambda _device_id, _payload: ("battery",)
    )
    cast("Any", coordinator)._async_fetch_historical_day_chart_source = AsyncMock(  # ruff: ignore[private-member-access]
        return_value=("empty_ambiguous", {}),
    )

    for _attempt in range(coordinator_module._STATISTICS_HTTP_EMPTY_MAX_ATTEMPTS):  # ruff: ignore[private-member-access]
        await coordinator._async_http_backfill_recent_day_statistics(  # ruff: ignore[private-member-access]
            {_DEVICE_ID: {}},
            force=True,
            window_days=1,
            request_budget=1,
        )

    third_run = await coordinator._async_http_backfill_recent_day_statistics(  # ruff: ignore[private-member-access]
        {_DEVICE_ID: {}},
        force=True,
        window_days=1,
        request_budget=1,
    )
    assert third_run["requests"] == 0
