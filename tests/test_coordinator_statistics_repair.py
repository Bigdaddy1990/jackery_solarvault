"""White-box tests for coordinator statistics config-gating and chart helpers.

These target decision helpers not already exercised by
``test_coordinator_diagnostics.py``: the enabled-period set gated on config-flow
toggles, the derived home-energy source fallback, the pre-recorder
period-hierarchy gate, the app-chart period/name lookups, the year-month
backfill trigger, and the day power-curve point builder's empty-source path. The
only integration boundary any of these touch is the config entry options
mapping; everything else is real production logic, so nothing internal is
mocked.
"""

import asyncio
from copy import deepcopy
from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, ClassVar, cast
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault import coordinator as co
from custom_components.jackery_solarvault.const import (
    APP_SECTION_HOME_STAT,
    APP_SECTION_PV_STAT,
    APP_STAT_TOTAL_OUT_GRID_ENERGY,
    CONF_ENABLE_DERIVED_HOME_ENERGY_FALLBACK,
    CONF_ENABLE_MONTH_STATISTICS,
    CONF_ENABLE_WEEK_STATISTICS,
    CONF_ENABLE_YEAR_STATISTICS,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    EXTERNAL_STAT_BUCKET_MONTH_DAILY,
    EXTERNAL_STAT_BUCKET_WEEK_DAILY,
    EXTERNAL_STAT_BUCKET_YEAR_MONTHLY,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from custom_components.jackery_solarvault.util import day_power_energy_points

_DEV = "dev-1"
_EXPECTED_SUCCESSFUL_DEVICE_COUNT = 2
_RETRY_AFTER_SECONDS = 17


async def test_startup_fetches_prior_periods_once_then_only_missing_periods() -> None:
    """Full startup history is not repeated by the following maintenance pass."""
    coordinator = _ready_backfill_coordinator()
    coordinator._statistics_startup_sync_pending = True  # ruff: ignore[private-member-access]
    coordinator.entry = _entry(**{
        CONF_ENABLE_WEEK_STATISTICS: False,
        CONF_ENABLE_YEAR_STATISTICS: False,
    })
    fetched: list[date] = []

    async def fetch(**kwargs: Any) -> dict[str, Any]:  # ruff: ignore[any-type]
        fetched.append(kwargs["period_start"])
        await asyncio.sleep(0)
        return {}

    coordinator._async_fetch_historical_app_chart_source = fetch  # ruff: ignore[private-member-access]
    first = await coordinator._async_http_backfill_period_statistics({_DEV: {}})  # ruff: ignore[private-member-access]
    assert set(fetched) == {
        date(2026, 1, 1),
        date(2026, 2, 1),
        date(2026, 3, 1),
        date(2026, 4, 1),
        date(2026, 5, 1),
        date(2026, 6, 1),
    }
    assert first["requests"] == first["empty_sources"]
    second = await coordinator._async_http_backfill_period_statistics({_DEV: {}})  # ruff: ignore[private-member-access]
    assert second["requests"] == 0
    coordinator._statistics_startup_sync_pending = False  # ruff: ignore[private-member-access]
    maintenance = await coordinator._async_http_backfill_period_statistics({_DEV: {}})  # ruff: ignore[private-member-access]
    assert maintenance["requests"] == 0


@pytest.mark.parametrize("store_fails", [False, True])
async def test_cancelled_backfill_persists_fetched_progress(store_fails: bool) -> None:
    """Reload must preserve fetched evidence while propagating task cancellation."""
    coordinator = _ready_backfill_coordinator()
    if store_fails:
        coordinator._statistics_backfill_store.async_save.side_effect = RuntimeError(  # ruff: ignore[private-member-access]
            "store unavailable"
        )
    coordinator._statistics_startup_sync_pending = False  # ruff: ignore[private-member-access]
    coordinator._statistics_backfill_task = None  # ruff: ignore[private-member-access]
    state = coordinator._statistics_backfill_device_state(_DEV)  # ruff: ignore[private-member-access]
    state["inflight"] = {"unimported_source": {"unit": "W", "y": [500]}}
    coordinator._async_advance_statistics_backfill = AsyncMock(  # ruff: ignore[private-member-access]
        side_effect=asyncio.CancelledError
    )
    with pytest.raises(asyncio.CancelledError):
        await coordinator._async_statistics_backfill_job({_DEV: {}})  # ruff: ignore[private-member-access]
    saved = coordinator._statistics_backfill_store.async_save.await_args.args[0]  # ruff: ignore[private-member-access]
    assert saved["devices"][_DEV]["inflight"]["unimported_source"]["y"] == [500]


def test_history_start_survives_malformed_legacy_branches() -> None:
    """Corrupt optional queue state must not block history reconstruction."""
    for legacy in (None, [], {"sources": None}, {"sources": {"pv": {"days": None}}}):
        coordinator = _ready_backfill_coordinator()
        state = coordinator._statistics_backfill_device_state(_DEV)  # ruff: ignore[private-member-access]  # behavior test uses private queue API and hand-checked expected values
        state["http_day_backfill"] = legacy
        assert coordinator._statistics_history_start(  # ruff: ignore[private-member-access]  # behavior test uses private queue API and hand-checked expected values
            {_DEV: {}}, date(2026, 7, 9)
        ) == date(2026, 1, 1)


def test_history_start_includes_previous_year_period_only_history() -> None:
    """A year rollover must retain the earliest known month even without day state."""
    coordinator = _ready_backfill_coordinator()
    state = coordinator._statistics_backfill_device_state(_DEV)  # ruff: ignore[private-member-access]  # behavior test uses private queue API and hand-checked expected values
    state["http_period_backfill"] = {
        "sources": {
            "device_pv_stat": {
                "month": {
                    "2025-12-01": {"status": "imported", "imported_rows": 31},
                }
            }
        }
    }
    assert coordinator._statistics_history_start({_DEV: {}}, date(2026, 7, 9)) == date(  # ruff: ignore[private-member-access]  # behavior test uses private queue API and hand-checked expected values
        2025, 12, 1
    )


def test_explicit_eps_day_curves_are_not_discarded() -> None:
    """Directional EPS power arrays must produce their actual hourly energy."""
    source = {
        "unit": "W",
        "y1": [1000] * 288,
        "y2": [500] * 288,
        "_request": {
            "dateType": "day",
            "beginDate": "2026-04-20",
            "endDate": "2026-04-20",
        },
    }
    incoming = day_power_energy_points(
        source,
        "device_eps_stat_day",
        "totalInEpsEnergy",
        bucket_minutes=60,
        today=date(2026, 4, 21),
        now=datetime(2026, 4, 21, tzinfo=UTC),
    )
    outgoing = day_power_energy_points(
        source,
        "device_eps_stat_day",
        "totalOutEpsEnergy",
        bucket_minutes=60,
        today=date(2026, 4, 21),
        now=datetime(2026, 4, 21, tzinfo=UTC),
    )
    assert len(incoming) == 24  # ruff: ignore[magic-value-comparison]  # behavior test uses private queue API and hand-checked expected values
    assert sum(point.value for point in incoming) == 24  # ruff: ignore[magic-value-comparison]  # behavior test uses private queue API and hand-checked expected values
    assert sum(point.value for point in outgoing) == 12  # ruff: ignore[magic-value-comparison]  # behavior test uses private queue API and hand-checked expected values


def test_fill_preserves_history_and_idempotent_completion() -> None:
    """A later fill must neither forget old imports nor reopen unchanged data."""
    coordinator = _ready_backfill_coordinator()
    _, days = coordinator._http_day_backfill_days_state(_DEV, APP_SECTION_HOME_STAT)  # ruff: ignore[private-member-access]  # behavior test uses private queue API and hand-checked expected values
    days.update({
        "2026-01-01": {"status": "imported", "imported_rows": 24},
        "2026-07-08": {"status": "imported", "imported_rows": 0},
    })
    progress = co._HttpDayBackfillProgress(  # ruff: ignore[private-member-access]  # behavior test uses private queue API and hand-checked expected values
        target_days=[date(2026, 7, 8)],
        force=False,
        window_days=1,
        include_current_year=False,
        now_monotonic=0.0,
    )
    candidates = coordinator._collect_http_day_backfill_candidates(  # ruff: ignore[private-member-access]  # behavior test uses private queue API and hand-checked expected values
        {_DEV: {}},
        [date(2026, 7, 8)],
        today=date(2026, 7, 9),
        now_epoch=0.0,
        progress=progress,
    )
    assert "2026-01-01" in days
    assert days["2026-07-08"]["status"] == "imported"
    assert not [c for c in candidates if c.section_prefix == APP_SECTION_HOME_STAT]


def test_fill_does_not_honor_legacy_week_long_empty_cooldown() -> None:
    """A stored retry timestamp must not hide a missing historical day."""
    coordinator = _ready_backfill_coordinator()
    _, days = coordinator._http_day_backfill_days_state(_DEV, APP_SECTION_HOME_STAT)  # ruff: ignore[private-member-access]  # behavior test uses private queue API and hand-checked expected values
    days["2026-04-15"] = {
        "status": "retryable",
        "retry_after_epoch": 9999999999.0,
        "empty_deferrals": 3,
    }
    progress = co._HttpDayBackfillProgress(  # ruff: ignore[private-member-access]  # behavior test uses private queue API and hand-checked expected values
        target_days=[date(2026, 4, 15)],
        force=True,
        window_days=120,
        include_current_year=True,
        now_monotonic=0.0,
    )
    candidates = coordinator._collect_http_day_backfill_candidates(  # ruff: ignore[private-member-access]  # behavior test uses private queue API and hand-checked expected values
        {_DEV: {}},
        [date(2026, 4, 15)],
        today=date(2026, 7, 9),
        now_epoch=0.0,
        progress=progress,
    )
    assert [
        c.target_day for c in candidates if c.section_prefix == APP_SECTION_HOME_STAT
    ] == [date(2026, 4, 15)]


async def test_full_period_pass_fetches_all_closed_months_without_budget() -> None:
    """One pass covers all six sources and six closed months, not one slice."""
    coordinator = _ready_backfill_coordinator()
    coordinator.entry = _entry(**{
        CONF_ENABLE_WEEK_STATISTICS: False,
        CONF_ENABLE_YEAR_STATISTICS: False,
    })
    coordinator._async_fetch_historical_app_chart_source = AsyncMock(return_value={})  # ruff: ignore[private-member-access]  # behavior test uses private queue API and hand-checked expected values
    result = await coordinator._async_http_backfill_period_statistics({_DEV: {}})  # ruff: ignore[private-member-access]  # behavior test uses private queue API and hand-checked expected values
    assert result["requests"] == 36  # ruff: ignore[magic-value-comparison]  # behavior test uses private queue API and hand-checked expected values


def _entry(**options: object) -> SimpleNamespace:
    """Return a config-entry double exposing only an options mapping."""
    return SimpleNamespace(options=dict(options), data={})


def _coordinator(
    entry: SimpleNamespace | None = None,
) -> JackerySolarVaultCoordinator:
    """Build a bare coordinator wired with only a config entry."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    cast("Any", coordinator).entry = entry if entry is not None else _entry()
    coordinator._shutdown_started = False  # ruff: ignore[private-member-access]  # behavior test uses private queue API and hand-checked expected values
    coordinator._slow_metrics_bg_task = None  # ruff: ignore[private-member-access]  # behavior test uses private queue API and hand-checked expected values
    coordinator.data = {}
    return coordinator


def _backfill_store_double() -> SimpleNamespace:
    """Return a persistent store double with no-op async load/save."""
    return SimpleNamespace(
        async_load=AsyncMock(return_value=None),
        async_save=AsyncMock(return_value=None),
    )


def _ready_backfill_coordinator() -> JackerySolarVaultCoordinator:
    """Return a coordinator with an empty, pre-loaded backfill state."""
    coordinator = _coordinator()
    coordinator._statistics_backfill_store = _backfill_store_double()  # ruff: ignore[private-member-access]
    coordinator._statistics_backfill_state = {  # ruff: ignore[private-member-access]
        co._STATISTICS_BACKFILL_STORE_DEVICES: {},  # ruff: ignore[private-member-access]
    }
    coordinator._statistics_backfill_state_loaded = True  # ruff: ignore[private-member-access]
    coordinator._statistics_import_diagnostics = {}  # ruff: ignore[private-member-access]
    coordinator._local_today = lambda: date(2026, 7, 9)  # ruff: ignore[private-member-access]
    return coordinator


async def test_period_backfill_queue_budget_zero_starts_no_fetch() -> None:
    """A zero request budget must not start any historical fetch."""
    coordinator = _ready_backfill_coordinator()
    fetch = AsyncMock(return_value={})
    coordinator._async_fetch_historical_app_chart_source = fetch  # ruff: ignore[private-member-access]

    result = await coordinator._async_http_backfill_period_statistics(  # ruff: ignore[private-member-access]
        {_DEV: {"device_pv_stat_month": {"y": [50.0]}}},
        request_budget=0,
    )

    fetch.assert_not_awaited()
    assert result["requests"] == 0


async def test_period_backfill_queue_budget_caps_requests_per_cycle() -> None:
    """A positive budget bounds the number of fetches in one cycle."""
    coordinator = _ready_backfill_coordinator()
    fetch = AsyncMock(return_value={})
    coordinator._async_fetch_historical_app_chart_source = fetch  # ruff: ignore[private-member-access]

    await coordinator._async_http_backfill_period_statistics(  # ruff: ignore[private-member-access]
        {_DEV: {"device_pv_stat_month": {"y": [50.0]}}},
        request_budget=1,
    )

    assert fetch.await_count == 1


async def test_statistics_import_wrapper_uses_only_bounded_backfill_job() -> None:
    """The compatibility wrapper must not revive the unbounded legacy repair."""
    coordinator = _coordinator()
    snapshot: dict[str, dict[str, Any]] = {_DEV: {}}
    bounded_job = AsyncMock(return_value={_DEV})
    legacy_repair = AsyncMock(return_value=(0, 0))
    coordinator._async_import_current_app_chart_statistics_job = bounded_job  # ruff: ignore[private-member-access]
    coordinator._async_repair_missing_app_chart_statistics = legacy_repair  # ruff: ignore[private-member-access]

    await coordinator._async_import_and_repair_app_chart_statistics(snapshot)  # ruff: ignore[private-member-access]

    bounded_job.assert_awaited_once_with(snapshot)
    legacy_repair.assert_not_awaited()


# --- _enabled_app_chart_date_types ---------------------------------------


def test_enabled_date_types_default_enables_all_periods() -> None:
    """With no opt-outs, day/week/month/year statistics are all enabled."""
    coordinator = _coordinator()

    assert coordinator._enabled_app_chart_date_types() == {  # ruff: ignore[private-member-access]
        DATE_TYPE_DAY,
        DATE_TYPE_WEEK,
        DATE_TYPE_MONTH,
        DATE_TYPE_YEAR,
    }


def test_enabled_date_types_opt_out_keeps_day_always_on() -> None:
    """Disabling week/month/year still leaves the always-on day period."""
    coordinator = _coordinator(
        _entry(**{
            CONF_ENABLE_WEEK_STATISTICS: False,
            CONF_ENABLE_MONTH_STATISTICS: False,
            CONF_ENABLE_YEAR_STATISTICS: False,
        }),
    )

    assert coordinator._enabled_app_chart_date_types() == {DATE_TYPE_DAY}  # ruff: ignore[private-member-access]


# --- derived home-energy source fallback ---------------------------------


def test_metric_candidates_home_energy_excludes_grid_source_by_default() -> None:
    """Derived home-energy fallback is off by default, so no grid substitute."""
    coordinator = _coordinator()

    candidates = coordinator._metric_source_candidates(  # ruff: ignore[private-member-access]
        APP_SECTION_HOME_STAT,
        "totalHomeEgy",
        "home_energy",
    )

    assert (APP_SECTION_HOME_STAT, APP_STAT_TOTAL_OUT_GRID_ENERGY) not in candidates


def test_metric_candidates_home_energy_adds_grid_source_when_enabled() -> None:
    """Enabling the derived fallback appends the grid-side source last."""
    coordinator = _coordinator(
        _entry(**{CONF_ENABLE_DERIVED_HOME_ENERGY_FALLBACK: True}),
    )

    candidates = coordinator._metric_source_candidates(  # ruff: ignore[private-member-access]
        APP_SECTION_HOME_STAT,
        "totalHomeEgy",
        "home_energy",
    )

    assert candidates[-1] == (APP_SECTION_HOME_STAT, APP_STAT_TOTAL_OUT_GRID_ENERGY)


async def test_current_import_job_imports_without_advancing_history() -> None:
    """Current verified imports stay independent of historical HTTP queues."""
    coordinator = _coordinator()
    coordinator._statistics_startup_sync_pending = False  # ruff: ignore[private-member-access]
    coordinator._statistics_import_diagnostics = {}  # ruff: ignore[private-member-access]
    call_order: list[str] = []

    def _day_import(_snapshot: object) -> set[str]:
        call_order.append("day")
        return {"day-device"}

    def _period_import(_snapshot: object) -> set[str]:
        call_order.append("period")
        return {"period-device"}

    day_import = AsyncMock(side_effect=_day_import)
    period_import = AsyncMock(side_effect=_period_import)
    coordinator._async_import_day_chart_statistics = day_import  # ruff: ignore[private-member-access]
    coordinator._async_import_app_chart_statistics = period_import  # ruff: ignore[private-member-access]
    snapshot = {
        _DEV: {
            "device_pv_stat_day": {"totalSolarEnergy": 1.0},
            "properties": {"batSoc": 50},
        },
    }

    result = await coordinator._async_import_current_app_chart_statistics_job(  # ruff: ignore[private-member-access]
        snapshot,
    )

    day_import.assert_awaited_once_with(snapshot)
    period_import.assert_awaited_once_with(snapshot)
    assert call_order == ["day", "period"]
    assert result == {"day-device", "period-device"}
    assert (
        coordinator._statistics_import_diagnostics[  # ruff: ignore[private-member-access]
            "last_external_successful_device_count"
        ]
        == _EXPECTED_SUCCESSFUL_DEVICE_COUNT
    )


async def test_post_startup_backfill_job_advances_one_retry_cycle() -> None:
    """Deferred empty buckets remain eligible after the startup loop exits."""
    coordinator = _coordinator()
    coordinator._statistics_startup_sync_pending = False  # ruff: ignore[private-member-access]
    coordinator._statistics_backfill_task = None  # ruff: ignore[private-member-access]
    coordinator._async_complete_statistics_startup_sync = AsyncMock()  # ruff: ignore[private-member-access]
    coordinator._async_advance_statistics_backfill = AsyncMock()  # ruff: ignore[private-member-access]
    snapshot = {_DEV: {"device_pv_stat_day": {"totalSolarEnergy": 1}}}

    await coordinator._async_statistics_backfill_job(snapshot)  # ruff: ignore[private-member-access]

    coordinator._async_complete_statistics_startup_sync.assert_not_awaited()  # ruff: ignore[private-member-access]
    coordinator._async_advance_statistics_backfill.assert_awaited_once_with(snapshot)  # ruff: ignore[private-member-access]


async def test_startup_sync_stays_pending_without_backfill_progress() -> None:
    """Actionable historical queues keep startup recovery pending."""
    coordinator = _coordinator()
    coordinator._statistics_startup_sync_pending = True  # ruff: ignore[private-member-access]
    coordinator._statistics_import_diagnostics = {}  # ruff: ignore[private-member-access]
    period_backfill = AsyncMock(return_value={"actionable_sources": 1})
    day_backfill = AsyncMock(return_value={"actionable_sources": 1})
    coordinator._async_http_backfill_period_statistics = period_backfill  # ruff: ignore[private-member-access]
    coordinator._async_http_backfill_recent_day_statistics = day_backfill  # ruff: ignore[private-member-access]
    snapshot = {_DEV: {"device_pv_stat_day": {"totalSolarEnergy": 1}}}

    await coordinator._async_advance_statistics_backfill(  # ruff: ignore[private-member-access]
        snapshot,
    )

    assert coordinator._statistics_startup_sync_pending is True  # ruff: ignore[private-member-access]
    day_backfill.assert_awaited_once_with(
        snapshot,
        force=True,
        include_current_year=True,
        request_budget=None,
    )
    period_backfill.assert_awaited_once_with(snapshot)


async def test_startup_sync_completes_when_both_queues_have_no_immediate_work() -> None:
    """Cooldown-backed retries move from the startup loop to regular cycles."""
    coordinator = _coordinator()
    coordinator._statistics_startup_sync_pending = True  # ruff: ignore[private-member-access]
    coordinator._statistics_import_diagnostics = {}  # ruff: ignore[private-member-access]
    period_backfill = AsyncMock(return_value={"actionable_sources": 0})
    day_backfill = AsyncMock(return_value={"actionable_sources": 0})
    coordinator._async_http_backfill_period_statistics = period_backfill  # ruff: ignore[private-member-access]
    coordinator._async_http_backfill_recent_day_statistics = day_backfill  # ruff: ignore[private-member-access]
    snapshot = {_DEV: {"device_pv_stat_day": {"totalSolarEnergy": 1}}}

    await coordinator._async_advance_statistics_backfill(  # ruff: ignore[private-member-access]
        snapshot,
    )

    assert coordinator._statistics_startup_sync_pending is False  # ruff: ignore[private-member-access]
    assert day_backfill.await_args is not None
    assert day_backfill.await_args.kwargs["include_current_year"] is True
    period_backfill.assert_awaited_once_with(snapshot)


async def test_complete_period_pass_precedes_complete_day_pass() -> None:
    """Period history and day history both run without an automatic request cap."""
    coordinator = _coordinator()
    coordinator._statistics_startup_sync_pending = True  # ruff: ignore[private-member-access]
    coordinator._statistics_import_diagnostics = {}  # ruff: ignore[private-member-access]
    coordinator._shutdown_started = False  # ruff: ignore[private-member-access]
    call_order: list[str] = []
    period_budgets: list[int | None] = []
    day_budgets: list[object] = []

    async def period_backfill(
        _snapshot: dict[str, dict[str, object]],
        *,
        request_budget: int | None = None,
    ) -> dict[str, int]:
        call_order.append("period")
        period_budgets.append(request_budget)
        await asyncio.sleep(0)
        return {"requests": 36, "actionable_sources": 0}

    async def day_backfill(
        _snapshot: dict[str, dict[str, object]],
        **kwargs: object,
    ) -> dict[str, int]:
        call_order.append("day")
        budget = kwargs["request_budget"]
        day_budgets.append(budget)
        await asyncio.sleep(0)
        return {"requests": 180, "actionable_sources": 0}

    coordinator._async_http_backfill_period_statistics = period_backfill  # ruff: ignore[private-member-access]
    coordinator._async_http_backfill_recent_day_statistics = day_backfill  # ruff: ignore[private-member-access]

    await coordinator._async_advance_statistics_backfill({_DEV: {}})  # ruff: ignore[private-member-access]

    assert call_order == ["period", "day"]
    assert period_budgets == [None]
    assert day_budgets == [None]
    assert coordinator._statistics_startup_sync_pending is False  # ruff: ignore[private-member-access]  # behavior test uses private queue API and hand-checked expected values


def test_period_queue_preserves_completed_buckets_with_legacy_metadata() -> None:
    """Queue construction must not reset imports because of old version metadata."""
    coordinator = _ready_backfill_coordinator()
    type_state: dict[str, object] = {
        "sum_chain_version": 1,
        "2026-04-01": {
            "status": "imported",
            "attempts": 2,
            "completed_at": "2026-08-20T12:00:00+00:00",
            "imported_rows": 30,
            "period_open": False,
        },
        "2026-05-01": {
            "status": "imported",
            "attempts": 2,
            "completed_at": "2026-08-20T12:00:00+00:00",
            "imported_rows": 0,
            "period_open": False,
        },
    }
    before = deepcopy(type_state)
    sources = coordinator._http_period_backfill_sources_state(_DEV)  # ruff: ignore[private-member-access]
    sources[APP_SECTION_PV_STAT] = {DATE_TYPE_MONTH: type_state}
    candidates = coordinator._collect_http_period_backfill_candidates(  # ruff: ignore[private-member-access]
        {_DEV: {}},
        ((DATE_TYPE_MONTH, [date(2026, 4, 1), date(2026, 5, 1)]),),
        today=date(2026, 9, 6),
        now_epoch=datetime(2026, 9, 6, tzinfo=UTC).timestamp(),
        progress=co._HttpPeriodBackfillProgress(),  # ruff: ignore[private-member-access]
    )
    assert type_state["2026-04-01"] == before["2026-04-01"]
    assert type_state["sum_chain_version"] == before["sum_chain_version"]
    assert [
        candidate.period_start
        for candidate in candidates
        if candidate.section_prefix == APP_SECTION_PV_STAT
    ] == []


def test_day_queue_preserves_imports_and_retry_deadlines_without_version_reset() -> (
    None
):
    """Recheck zero-row markers while retaining valid imports and retry deadlines."""
    coordinator = _ready_backfill_coordinator()
    source_state, days_state = coordinator._http_day_backfill_days_state(  # ruff: ignore[private-member-access]
        _DEV, APP_SECTION_HOME_STAT
    )
    source_state["sum_chain_version"] = 1
    days_state.update({
        "2026-04-01": {
            "status": "imported",
            "attempts": 2,
            "completed_at": "2026-08-20T12:00:00+00:00",
            "imported_rows": 30,
        },
        "2026-04-15": {
            "status": "retryable",
            "attempts": 4,
            "empty_deferrals": 3,
            "last_attempt_at": "2026-09-03T21:59:08.344060+00:00",
            "retry_after_epoch": 1789077548.0,
        },
        "2026-04-02": {
            "status": "imported",
            "attempts": 2,
            "completed_at": "2026-08-20T12:00:00+00:00",
            "imported_rows": 0,
        },
        "2026-04-16": {"status": "pending", "attempts": 0},
    })
    before = deepcopy(days_state)
    targets = [date.fromisoformat(key) for key in days_state]
    progress = co._HttpDayBackfillProgress(  # ruff: ignore[private-member-access]
        target_days=targets,
        force=True,
        window_days=120,
        include_current_year=True,
        now_monotonic=0.0,
    )
    candidates = coordinator._collect_http_day_backfill_candidates(  # ruff: ignore[private-member-access]
        {_DEV: {}},
        targets,
        today=date(2026, 9, 6),
        now_epoch=datetime(2026, 9, 6, tzinfo=UTC).timestamp(),
        progress=progress,
    )
    assert days_state["2026-04-01"] == before["2026-04-01"]
    assert days_state["2026-04-15"]["status"] == "pending"
    assert "retry_after_epoch" not in days_state["2026-04-15"]
    assert days_state["2026-04-02"] == before["2026-04-02"]
    assert [
        candidate.target_day
        for candidate in candidates
        if candidate.section_prefix == APP_SECTION_HOME_STAT
    ] == [date(2026, 4, 15), date(2026, 4, 16)]


async def test_backfill_completes_each_device_without_budget_slices() -> None:
    """Serial full-device passes share HTTP capacity with the live coordinator."""
    coordinator = _coordinator()
    coordinator._statistics_startup_sync_pending = False  # ruff: ignore[private-member-access]
    coordinator._statistics_import_diagnostics = {}  # ruff: ignore[private-member-access]
    coordinator._shutdown_started = False  # ruff: ignore[private-member-access]
    active = 0
    peak = 0
    budgets: list[object] = []

    async def day_backfill(
        _snapshot: dict[str, dict[str, object]],
        **kwargs: object,
    ) -> dict[str, int]:
        nonlocal active, peak
        budgets.append(kwargs["request_budget"])
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0)
        active -= 1
        return {"requests": 100, "actionable_sources": 0}

    coordinator._async_http_backfill_recent_day_statistics = day_backfill  # ruff: ignore[private-member-access]
    coordinator._async_http_backfill_period_statistics = AsyncMock(  # ruff: ignore[private-member-access]
        return_value={"requests": 0, "actionable_sources": 0},
    )

    await coordinator._async_advance_statistics_backfill({  # ruff: ignore[private-member-access]
        "device-a": {},
        "device-b": {},
        "device-c": {},
    })

    assert peak == 1
    assert budgets == [None, None, None]


def test_rate_limit_retry_after_header_is_honoured() -> None:
    """A server Retry-After value overrides the generic busy cooldown."""

    class RateLimitedError(Exception):
        headers: ClassVar[dict[str, str]] = {"Retry-After": str(_RETRY_AFTER_SECONDS)}

    assert (
        co._rate_limit_retry_after_seconds(RateLimitedError()) == _RETRY_AFTER_SECONDS  # ruff: ignore[private-member-access]
    )


def test_rate_limit_retry_after_zero_uses_minimum_delay() -> None:
    """An explicit zero means retry promptly, not use the generic cooldown."""

    class RateLimitedError(Exception):
        headers: ClassVar[dict[str, str]] = {"Retry-After": "0"}

    assert co._rate_limit_retry_after_seconds(RateLimitedError()) == 1  # ruff: ignore[private-member-access]


# --- app-chart period / name lookups -------------------------------------


def test_app_chart_period_meta_known_and_unknown() -> None:
    """A known period resolves to a bucket/label pair; unknown resolves None."""
    assert (
        JackerySolarVaultCoordinator._app_chart_period_meta(DATE_TYPE_WEEK) is not None  # ruff: ignore[private-member-access]
    )
    assert JackerySolarVaultCoordinator._app_chart_period_meta("nonsense") is None  # ruff: ignore[private-member-access]


def test_app_chart_name_prefix_falls_back_to_device_id() -> None:
    """With no name fields in the payload, the prefix defaults to the id."""
    assert (
        JackerySolarVaultCoordinator._app_chart_name_prefix(_DEV, {})  # ruff: ignore[private-member-access]
        == f"Jackery {_DEV}"
    )


# --- _needs_year_month_backfill ------------------------------------------


def test_needs_year_month_backfill_missing_section_is_false() -> None:
    """A payload lacking the year section needs no historical month fetch."""
    coordinator = _coordinator()

    assert (
        coordinator._needs_year_month_backfill(  # ruff: ignore[private-member-access]
            {},
            "device_pv_stat",
            ("totalSolarEnergy",),
            today=date(2026, 7, 9),
        )
        is False
    )


# --- _day_chart_points_for_metric ----------------------------------------


def test_day_chart_points_absent_sources_returns_empty() -> None:
    """When no candidate section is present, no day-curve points are built."""
    coordinator = _coordinator()

    assert (
        coordinator._day_chart_points_for_metric(  # ruff: ignore[private-member-access]
            device_id=_DEV,
            payload={},
            section_prefix="device_pv_stat",
            stat_key="totalSolarEnergy",
            metric_key="pv_energy",
            bucket_minutes=15,
            now=datetime(2026, 7, 9, 12, 0, tzinfo=UTC),
        )
        == []
    )


# --- _async_import_app_chart_statistics (period opt-out) -----------------


def _pv_week_month_year_snapshot() -> dict[str, dict[str, Any]]:
    """Return a snapshot with real week/month/year PV chart-series sources."""
    return {
        _DEV: {
            "device_pv_stat_week": {
                "unit": "kwh",
                "y": [1.0, 2.0],
                "_request": {"beginDate": "2026-07-06"},
            },
            "device_pv_stat_month": {
                "unit": "kwh",
                "y": [1.0, 2.0, 3.0],
                "_request": {"beginDate": "2026-07-01"},
            },
            "device_pv_stat_year": {
                "unit": "kwh",
                "y": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0],
                "_request": {"beginDate": "2026-01-01"},
            },
        },
    }


async def test_import_app_chart_statistics_default_options_imports_all_periods() -> (
    None
):
    """With no opt-outs, week/month/year chart buckets are all imported."""
    coordinator = _coordinator()
    coordinator._local_today = lambda: date(2026, 7, 9)  # ruff: ignore[private-member-access]
    add_stat = AsyncMock(return_value=(True, 1))
    coordinator._async_add_app_chart_statistics = add_stat  # ruff: ignore[private-member-access]

    await coordinator._async_import_app_chart_statistics(  # ruff: ignore[private-member-access]
        _pv_week_month_year_snapshot(),
    )

    imported_buckets = {call.kwargs["bucket"] for call in add_stat.call_args_list}
    assert imported_buckets == {
        EXTERNAL_STAT_BUCKET_WEEK_DAILY,
        EXTERNAL_STAT_BUCKET_MONTH_DAILY,
        EXTERNAL_STAT_BUCKET_YEAR_MONTHLY,
    }


async def test_import_app_chart_statistics_all_toggles_false_imports_nothing() -> None:
    """Disabling week/month/year skips every period bucket (day lives elsewhere)."""
    coordinator = _coordinator(
        _entry(**{
            CONF_ENABLE_WEEK_STATISTICS: False,
            CONF_ENABLE_MONTH_STATISTICS: False,
            CONF_ENABLE_YEAR_STATISTICS: False,
        }),
    )
    coordinator._local_today = lambda: date(2026, 7, 9)  # ruff: ignore[private-member-access]
    add_stat = AsyncMock(return_value=(True, 1))
    coordinator._async_add_app_chart_statistics = add_stat  # ruff: ignore[private-member-access]

    await coordinator._async_import_app_chart_statistics(  # ruff: ignore[private-member-access]
        _pv_week_month_year_snapshot(),
    )

    add_stat.assert_not_awaited()


async def test_import_app_chart_statistics_single_toggle_skips_only_that_period() -> (
    None
):
    """Disabling only the year toggle leaves the week/month imports untouched."""
    coordinator = _coordinator(_entry(**{CONF_ENABLE_YEAR_STATISTICS: False}))
    coordinator._local_today = lambda: date(2026, 7, 9)  # ruff: ignore[private-member-access]
    add_stat = AsyncMock(return_value=(True, 1))
    coordinator._async_add_app_chart_statistics = add_stat  # ruff: ignore[private-member-access]

    await coordinator._async_import_app_chart_statistics(  # ruff: ignore[private-member-access]
        _pv_week_month_year_snapshot(),
    )

    imported_buckets = {call.kwargs["bucket"] for call in add_stat.call_args_list}
    assert imported_buckets == {
        EXTERNAL_STAT_BUCKET_WEEK_DAILY,
        EXTERNAL_STAT_BUCKET_MONTH_DAILY,
    }


async def test_historical_day_idempotent_recorder_match_is_imported() -> None:
    """A matching existing Recorder series is a terminal success, not write_error."""
    coordinator = _coordinator()
    coordinator._local_now = lambda: datetime(2026, 7, 9, 12, tzinfo=UTC)  # ruff: ignore[private-member-access]
    add_stat = AsyncMock(return_value=(True, 0))
    coordinator._async_add_app_chart_statistics = add_stat  # ruff: ignore[private-member-access]
    source = {
        "unit": "kWh",
        "x": ["00:00"],
        "y": [1.25],
        "_request": {
            "beginDate": "2026-07-08",
            "endDate": "2026-07-08",
        },
    }
    historical_payload = coordinator._historical_day_payload_from_sources(  # ruff: ignore[private-member-access]
        {APP_SECTION_PV_STAT: source},
    )
    assert coordinator._day_chart_source_candidates(  # ruff: ignore[private-member-access]
        APP_SECTION_PV_STAT,
        "totalSolarEnergy",
        "pv_energy",
    )[-1] == ("device_pv_stat_day", "totalSolarEnergy")
    assert coordinator._day_chart_points_for_metric(  # ruff: ignore[private-member-access]
        device_id=_DEV,
        payload=historical_payload,
        section_prefix=APP_SECTION_PV_STAT,
        stat_key="totalSolarEnergy",
        metric_key="pv_energy",
        bucket_minutes=60,
        now=datetime(2026, 7, 9, 12, tzinfo=UTC),
        use_local_day_guard=False,
    )

    (
        ok,
        imported_rows,
    ) = await coordinator._async_import_historical_day_chart_statistics_for_device(  # ruff: ignore[private-member-access]
        device_id=_DEV,
        payload={},
        section_sources={APP_SECTION_PV_STAT: source},
    )

    assert ok is True
    assert imported_rows == 0
    add_stat.assert_awaited()


async def test_repair_counts_idempotently_verified_month_points() -> None:
    """Existing matching month rows still complete the persistent repair marker."""
    coordinator = _coordinator()
    coordinator._async_add_app_chart_statistics = AsyncMock(  # ruff: ignore[private-member-access]
        return_value=(True, 0),
    )
    period_start = date(2026, 7, 1)
    source = {
        "unit": "kWh",
        "y": [1.0, 2.0],
        "_request": {
            "beginDate": period_start.isoformat(),
            "endDate": "2026-07-31",
        },
    }

    repaired, failed = await coordinator._import_collected_repair_buckets(  # ruff: ignore[private-member-access]
        device_id=_DEV,
        name_prefix="SolarVault",
        collected={(APP_SECTION_PV_STAT, DATE_TYPE_MONTH, period_start): source},
        period_meta_by_type={DATE_TYPE_MONTH: ("month_daily", "daily")},
        to_date=date(2026, 7, 9),
    )

    assert repaired == len(source["y"])
    assert failed == 0


async def test_unimportable_payload_is_retained_without_false_import() -> None:
    """No usable points is distinct from an idempotent successful Recorder write."""
    coordinator = _coordinator()
    day_state: dict[str, Any] = {"status": co.BackfillStatus.PENDING.value}
    candidate = co._HttpDayBackfillCandidate(  # ruff: ignore[private-member-access]
        priority=1,
        attempted=0,
        last_attempt="",
        target_day=date(2026, 5, 1),
        attempts=0,
        device_id=_DEV,
        section_prefix=APP_SECTION_PV_STAT,
        payload={},
        day_state=day_state,
        days_state={"2026-05-01": day_state},
    )
    progress = co._HttpDayBackfillProgress(  # ruff: ignore[private-member-access]
        target_days=[date(2026, 5, 1)],
        force=True,
        window_days=120,
        include_current_year=True,
        now_monotonic=0.0,
    )

    async def _fetch(**_kwargs: object) -> tuple[str, dict[str, Any]]:
        await asyncio.sleep(0)
        return "fetched", {"x": ["2026-05-01"], "y": [1.0]}

    async def _import(**_kwargs: object) -> tuple[bool | None, int]:
        await asyncio.sleep(0)
        return None, 0  # no points could be mapped from this payload

    mutable = cast("Any", coordinator)
    mutable._async_fetch_historical_day_chart_source = _fetch  # ruff: ignore[private-member-access]
    mutable._record_verified_day_totals_update = (  # ruff: ignore[private-member-access]
        lambda *_a, **_k: {"totalSolarEnergy": 1.0}
    )
    mutable._async_import_historical_day_chart_statistics_for_device = _import  # ruff: ignore[private-member-access]

    await coordinator._async_process_http_day_backfill_candidate(  # ruff: ignore[private-member-access]
        candidate,
        progress,
        week_start=date(2026, 4, 27),
        today=date(2026, 9, 6),
    )

    assert day_state["status"] == "unmapped"
    assert day_state["last_error"] == "no_mapped_points"
    assert "completed_at" not in day_state
    assert "imported_rows" not in day_state
    assert day_state["unimported_source"]["y"] == [1.0]
