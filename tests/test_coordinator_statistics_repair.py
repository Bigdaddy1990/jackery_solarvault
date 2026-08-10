"""Behavioral tests for coordinator statistics config-gating and chart helpers.

These target decision helpers not already exercised by
``test_coordinator_diagnostics.py``: the enabled-period set gated on config-flow
toggles, the derived home-energy source fallback, the pre-recorder
period-hierarchy gate, the app-chart period/name lookups, the year-month
backfill trigger, and the day power-curve point builder's empty-source path. The
only integration boundary any of these touch is the config entry options
mapping; everything else is real production logic, so nothing internal is
mocked.
"""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, cast
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

_DEV = "dev-1"


def _entry(**options: object) -> SimpleNamespace:
    """Return a config-entry double exposing only an options mapping."""
    return SimpleNamespace(options=dict(options), data={})


def _coordinator(entry: SimpleNamespace | None = None) -> Any:  # ruff:ignore[any-type]
    """Build a bare coordinator wired with only a config entry."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    cast("Any", coordinator).entry = entry if entry is not None else _entry()
    return coordinator


def _backfill_store_double() -> SimpleNamespace:
    """Return a persistent store double with no-op async load/save."""
    return SimpleNamespace(
        async_load=AsyncMock(return_value=None),
        async_save=AsyncMock(return_value=None),
    )


def _ready_backfill_coordinator() -> Any:  # ruff:ignore[any-type]
    """Return a coordinator with an empty, pre-loaded backfill state."""
    coordinator = _coordinator()
    coordinator._statistics_backfill_store = (  # ruff: ignore[private-member-access]
        _backfill_store_double()
    )
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
    snapshot = {_DEV: {}}
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


# --- _gate_snapshot_period_hierarchy -------------------------------------


def test_gate_hierarchy_passes_clean_snapshot_through() -> None:
    """A snapshot with no period contradictions is returned per device."""
    snapshot = {_DEV: {"device_pv_stat_day": {"totalSolarEnergy": 1}}}

    gated = JackerySolarVaultCoordinator._gate_snapshot_period_hierarchy(  # ruff: ignore[private-member-access]
        snapshot,
        today=date(2026, 7, 9),
    )

    assert set(gated) == {_DEV}
    assert isinstance(gated[_DEV], dict)


def test_gate_hierarchy_empty_snapshot_is_empty() -> None:
    """An empty snapshot gates to an empty mapping."""
    assert (
        JackerySolarVaultCoordinator._gate_snapshot_period_hierarchy(  # ruff: ignore[private-member-access]
            {},
            today=date(2026, 7, 9),
        )
        == {}
    )


async def test_current_import_job_gates_snapshot_before_every_import() -> None:
    """The active bounded job must not pass hierarchy violations to Recorder."""
    coordinator = _coordinator()
    coordinator._statistics_startup_sync_pending = False  # ruff: ignore[private-member-access]
    coordinator._statistics_import_diagnostics = {}  # ruff: ignore[private-member-access]
    coordinator._local_today = lambda: date(2026, 7, 9)  # ruff: ignore[private-member-access]
    period_backfill = AsyncMock(return_value={"pending_sources": 0})
    backfill = AsyncMock(return_value={"pending_sources": 0})
    day_import = AsyncMock(return_value=set())
    period_import = AsyncMock(return_value=set())
    coordinator._async_http_backfill_period_statistics = period_backfill  # ruff: ignore[private-member-access]
    coordinator._async_http_backfill_recent_day_statistics = backfill  # ruff: ignore[private-member-access]
    coordinator._async_import_day_chart_statistics = day_import  # ruff: ignore[private-member-access]
    coordinator._async_import_app_chart_statistics = period_import  # ruff: ignore[private-member-access]
    # Both periods are closed (before today) and the month window lies fully
    # inside the year window, so the year_less_than_month contradiction is
    # real and must be withheld.
    snapshot = {
        _DEV: {
            "device_pv_stat_year": {
                "y": [1.0, 2.0],
                "_request": {
                    "beginDate": "2025-01-01",
                    "endDate": "2025-12-31",
                },
            },
            "device_pv_stat_month": {
                "y": [50.0],
                "_request": {
                    "beginDate": "2025-04-01",
                    "endDate": "2025-04-30",
                },
            },
            "properties": {"batSoc": 50},
        },
    }

    await coordinator._async_import_current_app_chart_statistics_job(snapshot)  # ruff: ignore[private-member-access]

    gated = backfill.await_args.args[0]
    assert period_backfill.await_args.args[0] == gated
    assert "device_pv_stat_month" not in gated[_DEV]
    assert gated[_DEV]["device_pv_stat_year"]["y"] == [1.0, 2.0]
    assert day_import.await_args.args[0] == gated
    assert period_import.await_args.args[0] == gated


async def test_startup_sync_stays_pending_without_backfill_progress() -> None:
    """Empty/time-out backfill runs do not falsely complete startup recovery."""
    coordinator = _coordinator()
    coordinator._statistics_startup_sync_pending = True  # ruff: ignore[private-member-access]
    coordinator._statistics_import_diagnostics = {}  # ruff: ignore[private-member-access]
    coordinator._local_today = lambda: date(2026, 7, 9)  # ruff: ignore[private-member-access]
    coordinator._async_http_backfill_period_statistics = AsyncMock(  # ruff: ignore[private-member-access]
        return_value={"pending_sources": 1},
    )
    coordinator._async_http_backfill_recent_day_statistics = AsyncMock(  # ruff: ignore[private-member-access]
        return_value={"pending_sources": 1},
    )
    coordinator._async_import_day_chart_statistics = AsyncMock(return_value=set())  # ruff: ignore[private-member-access]
    coordinator._async_import_app_chart_statistics = AsyncMock(return_value=set())  # ruff: ignore[private-member-access]

    await coordinator._async_import_current_app_chart_statistics_job(  # ruff: ignore[private-member-access]
        {_DEV: {"device_pv_stat_day": {"totalSolarEnergy": 1}}},
    )

    assert coordinator._statistics_startup_sync_pending is True  # ruff: ignore[private-member-access]


async def test_startup_sync_completes_only_after_both_queues_are_terminal() -> None:
    """The startup marker clears only after period and day queues both finish."""
    coordinator = _coordinator()
    coordinator._statistics_startup_sync_pending = True  # ruff: ignore[private-member-access]
    coordinator._statistics_import_diagnostics = {}  # ruff: ignore[private-member-access]
    coordinator._local_today = lambda: date(2026, 7, 9)  # ruff: ignore[private-member-access]
    coordinator._async_http_backfill_period_statistics = AsyncMock(  # ruff: ignore[private-member-access]
        return_value={"pending_sources": 0},
    )
    day_backfill = AsyncMock(return_value={"pending_sources": 0})
    coordinator._async_http_backfill_recent_day_statistics = day_backfill  # ruff: ignore[private-member-access]
    coordinator._async_import_day_chart_statistics = AsyncMock(return_value=set())  # ruff: ignore[private-member-access]
    coordinator._async_import_app_chart_statistics = AsyncMock(return_value=set())  # ruff: ignore[private-member-access]

    await coordinator._async_import_current_app_chart_statistics_job(  # ruff: ignore[private-member-access]
        {_DEV: {"device_pv_stat_day": {"totalSolarEnergy": 1}}},
    )

    assert coordinator._statistics_startup_sync_pending is False  # ruff: ignore[private-member-access]
    assert day_backfill.await_args.kwargs["include_current_year"] is True


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


# --- _statistics_repair_from_date ----------------------------------------


@pytest.mark.parametrize("failed_bucket_count", [0, 3])
def test_first_year_repair_runs_at_most_once_per_local_day(
    failed_bucket_count: int,
) -> None:
    """A first-run repair must not refetch the full year on every HTTP poll."""
    coordinator = _coordinator()
    coordinator._statistics_backfill_state = {  # ruff: ignore[private-member-access]
        "devices": {
            _DEV: {
                "last_repair_date": "2026-07-09",
                "last_failed_bucket_count": failed_bucket_count,
            },
        },
    }

    assert (
        coordinator._statistics_repair_from_date(  # ruff: ignore[private-member-access]
            _DEV,
            date(2026, 7, 9),
        )
        is None
    )
    assert coordinator._statistics_repair_from_date(  # ruff: ignore[private-member-access]
        _DEV,
        date(2026, 7, 10),
    ) == date(2026, 1, 1)


# --- _day_chart_points_for_metric ----------------------------------------


def test_day_chart_points_absent_sources_returns_empty() -> None:
    """When no candidate section is present, no day-curve points are built."""
    coordinator = _coordinator()

    assert (
        coordinator._day_chart_points_for_metric(  # ruff: ignore[private-member-access]
            {},
            "device_pv_stat",
            "totalSolarEnergy",
            "pv_energy",
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


# --- _async_repair_missing_app_chart_statistics (period opt-out) ---------


async def test_repair_missing_statistics_default_options_plans_all_periods() -> None:
    """With no opt-outs, the historical repair plan covers week/month/year."""
    coordinator = _coordinator()
    coordinator._device_index = {}  # ruff: ignore[private-member-access]
    collect = AsyncMock(return_value=({}, {}, 0))
    coordinator._collect_repair_buckets = collect  # ruff: ignore[private-member-access]

    await coordinator._async_repair_missing_app_chart_statistics(  # ruff: ignore[private-member-access]
        _DEV,
        {},
        date(2026, 7, 1),
        date(2026, 7, 9),
    )

    planned_types = {
        date_type
        for call in collect.await_args_list
        for date_type, _starts in call.kwargs["period_plan"]
    }
    assert planned_types == {DATE_TYPE_WEEK, DATE_TYPE_MONTH, DATE_TYPE_YEAR}
    assert {
        date_type
        for date_type, _starts in collect.await_args_list[0].kwargs["period_plan"]
    } == {DATE_TYPE_MONTH, DATE_TYPE_YEAR}
    assert {
        date_type
        for date_type, _starts in collect.await_args_list[1].kwargs["period_plan"]
    } == {DATE_TYPE_WEEK}


async def test_repair_missing_statistics_all_toggles_false_plans_nothing() -> None:
    """Disabling week/month/year leaves the historical repair plan empty."""
    coordinator = _coordinator(
        _entry(**{
            CONF_ENABLE_WEEK_STATISTICS: False,
            CONF_ENABLE_MONTH_STATISTICS: False,
            CONF_ENABLE_YEAR_STATISTICS: False,
        }),
    )
    coordinator._device_index = {}  # ruff: ignore[private-member-access]
    collect = AsyncMock(return_value=({}, {}, 0))
    coordinator._collect_repair_buckets = collect  # ruff: ignore[private-member-access]

    await coordinator._async_repair_missing_app_chart_statistics(  # ruff: ignore[private-member-access]
        _DEV,
        {},
        date(2026, 7, 1),
        date(2026, 7, 9),
    )

    collect.assert_not_awaited()


async def test_repair_missing_statistics_single_toggle_skips_only_that_period() -> None:
    """Disabling only the month toggle leaves week/year in the repair plan."""
    coordinator = _coordinator(_entry(**{CONF_ENABLE_MONTH_STATISTICS: False}))
    coordinator._device_index = {}  # ruff: ignore[private-member-access]
    collect = AsyncMock(return_value=({}, {}, 0))
    coordinator._collect_repair_buckets = collect  # ruff: ignore[private-member-access]

    await coordinator._async_repair_missing_app_chart_statistics(  # ruff: ignore[private-member-access]
        _DEV,
        {},
        date(2026, 7, 1),
        date(2026, 7, 9),
    )

    planned_types = {
        date_type
        for call in collect.await_args_list
        for date_type, _starts in call.kwargs["period_plan"]
    }
    assert planned_types == {DATE_TYPE_WEEK, DATE_TYPE_YEAR}


async def test_historical_day_idempotent_recorder_match_is_imported() -> None:
    """A matching existing Recorder series is a terminal success, not write_error."""
    coordinator = _coordinator()
    coordinator._local_now = lambda: datetime(2026, 7, 9, 12, tzinfo=UTC)  # ruff: ignore[private-member-access]
    add_stat = AsyncMock(return_value=(True, 0))
    coordinator._async_add_app_chart_statistics = add_stat  # ruff: ignore[private-member-access]
    source = {
        "unit": "kWh",
        "y": [1.25],
        "_request": {
            "beginDate": "2026-07-08",
            "endDate": "2026-07-08",
        },
    }

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
        withheld=set(),
        to_date=date(2026, 7, 9),
    )

    assert repaired == len(source["y"])
    assert failed == 0
