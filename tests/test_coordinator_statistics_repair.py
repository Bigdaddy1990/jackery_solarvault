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


def _coordinator(entry: SimpleNamespace | None = None) -> Any:
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


def _ready_backfill_coordinator() -> Any:
    """Return a coordinator with an empty, pre-loaded backfill state."""
    coordinator = _coordinator()
    coordinator._statistics_backfill_store = _backfill_store_double()
    coordinator._statistics_backfill_state = {
        co._STATISTICS_BACKFILL_STORE_DEVICES: {},
    }
    coordinator._statistics_backfill_state_loaded = True
    coordinator._statistics_import_diagnostics = {}
    coordinator._local_today = lambda: date(2026, 7, 9)
    return coordinator


async def test_period_backfill_queue_budget_zero_starts_no_fetch() -> None:
    """A zero request budget must not start any historical fetch."""
    coordinator = _ready_backfill_coordinator()
    fetch = AsyncMock(return_value={})
    coordinator._async_fetch_historical_app_chart_source = fetch

    result = await coordinator._async_http_backfill_period_statistics(
        {_DEV: {"device_pv_stat_month": {"y": [50.0]}}},
        request_budget=0,
    )

    fetch.assert_not_awaited()
    assert result["requests"] == 0


async def test_period_backfill_queue_budget_caps_requests_per_cycle() -> None:
    """A positive budget bounds the number of fetches in one cycle."""
    coordinator = _ready_backfill_coordinator()
    fetch = AsyncMock(return_value={})
    coordinator._async_fetch_historical_app_chart_source = fetch

    await coordinator._async_http_backfill_period_statistics(
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
    coordinator._async_import_current_app_chart_statistics_job = bounded_job
    coordinator._async_repair_missing_app_chart_statistics = legacy_repair

    await coordinator._async_import_and_repair_app_chart_statistics(snapshot)

    bounded_job.assert_awaited_once_with(snapshot)
    legacy_repair.assert_not_awaited()


# --- _enabled_app_chart_date_types ---------------------------------------


def test_enabled_date_types_default_enables_all_periods() -> None:
    """With no opt-outs, day/week/month/year statistics are all enabled."""
    coordinator = _coordinator()

    assert coordinator._enabled_app_chart_date_types() == {
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

    assert coordinator._enabled_app_chart_date_types() == {DATE_TYPE_DAY}


# --- derived home-energy source fallback ---------------------------------


def test_metric_candidates_home_energy_excludes_grid_source_by_default() -> None:
    """Derived home-energy fallback is off by default, so no grid substitute."""
    coordinator = _coordinator()

    candidates = coordinator._metric_source_candidates(
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

    candidates = coordinator._metric_source_candidates(
        APP_SECTION_HOME_STAT,
        "totalHomeEgy",
        "home_energy",
    )

    assert candidates[-1] == (APP_SECTION_HOME_STAT, APP_STAT_TOTAL_OUT_GRID_ENERGY)


async def test_current_import_job_imports_without_advancing_history() -> None:
    """Current verified imports stay independent of historical HTTP queues."""
    coordinator = _coordinator()
    coordinator._statistics_startup_sync_pending = False
    coordinator._statistics_import_diagnostics = {}
    day_import = AsyncMock(return_value={"day-device"})
    period_import = AsyncMock(return_value={"period-device"})
    coordinator._async_import_day_chart_statistics = day_import
    coordinator._async_import_app_chart_statistics = period_import
    snapshot = {
        _DEV: {
            "device_pv_stat_day": {"totalSolarEnergy": 1.0},
            "properties": {"batSoc": 50},
        },
    }

    result = await coordinator._async_import_current_app_chart_statistics_job(
        snapshot,
    )

    day_import.assert_awaited_once_with(snapshot)
    period_import.assert_awaited_once_with(snapshot)
    assert result == {"day-device", "period-device"}
    assert (
        coordinator._statistics_import_diagnostics[
            "last_external_successful_device_count"
        ]
        == 2
    )


async def test_startup_sync_stays_pending_without_backfill_progress() -> None:
    """Actionable historical queues keep startup recovery pending."""
    coordinator = _coordinator()
    coordinator._statistics_startup_sync_pending = True
    coordinator._statistics_import_diagnostics = {}
    period_backfill = AsyncMock(return_value={"actionable_sources": 1})
    day_backfill = AsyncMock(return_value={"actionable_sources": 1})
    coordinator._async_http_backfill_period_statistics = period_backfill
    coordinator._async_http_backfill_recent_day_statistics = day_backfill
    snapshot = {_DEV: {"device_pv_stat_day": {"totalSolarEnergy": 1}}}

    await coordinator._async_advance_statistics_backfill(
        snapshot,
    )

    assert coordinator._statistics_startup_sync_pending is True
    day_backfill.assert_awaited_once_with(
        snapshot,
        force=True,
        window_days=co._STATISTICS_HTTP_STARTUP_BACKFILL_MIN_DAYS,
        include_current_year=True,
        request_budget=co._STATISTICS_HTTP_BACKFILL_REQUEST_BUDGET,
    )
    period_backfill.assert_awaited_once_with(snapshot)


async def test_startup_sync_completes_only_after_both_queues_are_terminal() -> None:
    """The startup marker clears only after period and day queues both finish."""
    coordinator = _coordinator()
    coordinator._statistics_startup_sync_pending = True
    coordinator._statistics_import_diagnostics = {}
    period_backfill = AsyncMock(return_value={"actionable_sources": 0})
    day_backfill = AsyncMock(return_value={"actionable_sources": 0})
    coordinator._async_http_backfill_period_statistics = period_backfill
    coordinator._async_http_backfill_recent_day_statistics = day_backfill
    snapshot = {_DEV: {"device_pv_stat_day": {"totalSolarEnergy": 1}}}

    await coordinator._async_advance_statistics_backfill(
        snapshot,
    )

    assert coordinator._statistics_startup_sync_pending is False
    assert day_backfill.await_args is not None
    assert day_backfill.await_args.kwargs["include_current_year"] is True
    period_backfill.assert_awaited_once_with(snapshot)


# --- app-chart period / name lookups -------------------------------------


def test_app_chart_period_meta_known_and_unknown() -> None:
    """A known period resolves to a bucket/label pair; unknown resolves None."""
    assert (
        JackerySolarVaultCoordinator._app_chart_period_meta(DATE_TYPE_WEEK) is not None
    )
    assert JackerySolarVaultCoordinator._app_chart_period_meta("nonsense") is None


def test_app_chart_name_prefix_falls_back_to_device_id() -> None:
    """With no name fields in the payload, the prefix defaults to the id."""
    assert (
        JackerySolarVaultCoordinator._app_chart_name_prefix(_DEV, {})
        == f"Jackery {_DEV}"
    )


# --- _needs_year_month_backfill ------------------------------------------


def test_needs_year_month_backfill_missing_section_is_false() -> None:
    """A payload lacking the year section needs no historical month fetch."""
    coordinator = _coordinator()

    assert (
        coordinator._needs_year_month_backfill(
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
        coordinator._day_chart_points_for_metric(
            _DEV,
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
    coordinator._local_today = lambda: date(2026, 7, 9)
    add_stat = AsyncMock(return_value=(True, 1))
    coordinator._async_add_app_chart_statistics = add_stat

    await coordinator._async_import_app_chart_statistics(
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
    coordinator._local_today = lambda: date(2026, 7, 9)
    add_stat = AsyncMock(return_value=(True, 1))
    coordinator._async_add_app_chart_statistics = add_stat

    await coordinator._async_import_app_chart_statistics(
        _pv_week_month_year_snapshot(),
    )

    add_stat.assert_not_awaited()


async def test_import_app_chart_statistics_single_toggle_skips_only_that_period() -> (
    None
):
    """Disabling only the year toggle leaves the week/month imports untouched."""
    coordinator = _coordinator(_entry(**{CONF_ENABLE_YEAR_STATISTICS: False}))
    coordinator._local_today = lambda: date(2026, 7, 9)
    add_stat = AsyncMock(return_value=(True, 1))
    coordinator._async_add_app_chart_statistics = add_stat

    await coordinator._async_import_app_chart_statistics(
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
    coordinator._local_now = lambda: datetime(2026, 7, 9, 12, tzinfo=UTC)
    add_stat = AsyncMock(return_value=(True, 0))
    coordinator._async_add_app_chart_statistics = add_stat
    source = {
        "unit": "kWh",
        "x": ["00:00"],
        "y": [1.25],
        "_request": {
            "beginDate": "2026-07-08",
            "endDate": "2026-07-08",
        },
    }
    historical_payload = coordinator._historical_day_payload_from_sources(
        {APP_SECTION_PV_STAT: source},
    )
    assert coordinator._day_chart_source_candidates(
        APP_SECTION_PV_STAT,
        "totalSolarEnergy",
        "pv_energy",
    )[-1] == ("device_pv_stat_day", "totalSolarEnergy")
    assert coordinator._day_chart_points_for_metric(
        _DEV,
        historical_payload,
        APP_SECTION_PV_STAT,
        "totalSolarEnergy",
        "pv_energy",
        bucket_minutes=60,
        now=datetime(2026, 7, 9, 12, tzinfo=UTC),
        use_local_day_guard=False,
    )

    (
        ok,
        imported_rows,
    ) = await coordinator._async_import_historical_day_chart_statistics_for_device(
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
    coordinator._async_add_app_chart_statistics = AsyncMock(
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

    repaired, failed = await coordinator._import_collected_repair_buckets(
        device_id=_DEV,
        name_prefix="SolarVault",
        collected={(APP_SECTION_PV_STAT, DATE_TYPE_MONTH, period_start): source},
        period_meta_by_type={DATE_TYPE_MONTH: ("month_daily", "daily")},
        to_date=date(2026, 7, 9),
    )

    assert repaired == len(source["y"])
    assert failed == 0
