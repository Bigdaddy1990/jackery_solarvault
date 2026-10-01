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
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from custom_components.jackery_solarvault.util import (
    apply_year_month_backfill,
    day_power_energy_points,
)

_DEV = "dev-1"
_RETRY_AFTER_SECONDS = 17
_FULL_DAY_SAMPLES = 288


@pytest.mark.parametrize(
    "gap", ["none", "truncated", "missing", "duplicate", "extra_duplicate"]
)
def test_only_complete_day_curves_verify_period_totals(gap: str) -> None:
    """Partial five-minute evidence cannot certify a whole historical day."""
    coordinator = _ready_backfill_coordinator()
    values: list[float | None] = [1000.0] * 288
    labels = [f"{minute // 60:02}:{minute % 60:02}" for minute in range(0, 1440, 5)]
    labels[-1] = "24:00"  # The observed app end marker maps to the last slot.
    if gap == "truncated":
        values = values[:12]
    elif gap == "missing":
        values[150] = None
    elif gap == "duplicate":
        labels[150] = labels[149]
    elif gap == "extra_duplicate":
        values.append(1000.0)
        labels.append("12:00")
    source: dict[str, Any] = {
        "unit": "W",
        "x": labels,
        "y": values,
        "totalSolarEnergy": "0",
        "_request": {
            "dateType": "day",
            "beginDate": "2026-07-08",
            "endDate": "2026-07-08",
        },
    }
    # Sparse measured buckets remain usable by the Recorder converter.
    assert day_power_energy_points(source, APP_SECTION_PV_STAT, "totalSolarEnergy")
    totals = coordinator._verified_historical_day_totals(  # ruff: ignore[private-member-access]
        device_id=_DEV, section_prefix=APP_SECTION_PV_STAT, source=source
    )
    assert totals == ({"totalSolarEnergy": 24.0} if gap == "none" else {})


def test_verified_day_totals_survive_week_and_month_rollover() -> None:
    """Completed day evidence must remain usable by month/year reconciliation."""
    coordinator = _ready_backfill_coordinator()
    raw = cast("Any", coordinator)
    today = date(2026, 10, 1)
    raw._local_today = lambda: today  # ruff: ignore[private-member-access]
    raw.data = {
        _DEV: {
            "verified_day_statistics": {
                "2026-09-01": {APP_SECTION_PV_STAT: {"pv1Egy": 2.0}},
                "2026-09-29": {APP_SECTION_PV_STAT: {"pv1Egy": 2.1}},
                "2025-09-29": {APP_SECTION_PV_STAT: {"pv1Egy": 9.0}},
                "2026-10-01": {APP_SECTION_PV_STAT: {"pv1Egy": 8.0}},
            }
        }
    }
    updates: dict[str, dict[str, Any]] = {}
    coordinator._merge_verified_day_totals_update(  # ruff: ignore[private-member-access]
        updates,
        day_totals={"pv1Egy": 1.2},
        device_id=_DEV,
        target_day=date(2026, 9, 15),
        section_prefix=APP_SECTION_PV_STAT,
        week_start=date(2026, 9, 28),
        today=today,
    )
    expected = {
        "2026-09-01": {APP_SECTION_PV_STAT: {"pv1Egy": 2.0}},
        "2026-09-15": {APP_SECTION_PV_STAT: {"pv1Egy": 1.2}},
        "2026-09-29": {APP_SECTION_PV_STAT: {"pv1Egy": 2.1}},
    }
    assert updates[_DEV] == expected
    assert (
        coordinator._preserved_fast_payload_value(  # ruff: ignore[private-member-access]
            "verified_day_statistics", updates[_DEV]
        )
        == expected
    )


def test_unversioned_cached_day_total_is_not_a_complete_day_proof() -> None:
    """Legacy sums have no evidence that all five-minute slots were present."""
    coordinator = _ready_backfill_coordinator()
    updates: dict[str, dict[str, Any]] = {}
    _, _, verified = coordinator._restore_or_reopen_imported_day_totals(  # ruff: ignore[private-member-access]
        updates,
        state_status=co.BackfillStatus.IMPORTED,
        day_state={"verified_totals": {"totalSolarEnergy": 1.0}},
        device_id=_DEV,
        target_day=date(2026, 7, 8),
        section_prefix=APP_SECTION_PV_STAT,
        week_start=date(2026, 7, 6),
        today=date(2026, 7, 9),
    )
    assert not verified
    assert not updates


@pytest.mark.parametrize("samples", [288, 12, 0])
async def test_imported_day_totals_recovery_never_reimports(samples: int) -> None:
    """A budgeted verification reads evidence once and preserves import history."""
    coordinator = _ready_backfill_coordinator()
    raw = cast("Any", coordinator)
    raw._statistics_startup_sync_pending = False  # ruff: ignore[private-member-access]
    payload = {"system_meta": {"id": "system-1"}}
    source, days = coordinator._http_day_backfill_days_state(_DEV, APP_SECTION_PV_STAT)  # ruff: ignore[private-member-access]
    source["curve_rule"] = co._HTTP_DAY_CURVE_IMPORT_RULE  # ruff: ignore[private-member-access]
    original = {
        "status": "imported",
        "imported_rows": 24,
        "completed_at": "2026-07-08T23:00:00+00:00",
    }
    days["2026-07-08"] = deepcopy(original)
    progress = co._HttpDayBackfillProgress(  # ruff: ignore[private-member-access]
        target_days=[date(2026, 7, 8)],
        force=True,
        window_days=1,
        include_current_year=True,
        now_monotonic=0.0,
        verification_only=True,
    )
    candidates = coordinator._collect_http_day_backfill_candidates(  # ruff: ignore[private-member-access]
        {_DEV: payload},
        progress.target_days,
        today=date(2026, 7, 9),
        now_epoch=datetime(2026, 7, 9, tzinfo=UTC).timestamp(),
        progress=progress,
    )
    selected = [c for c in candidates if c.section_prefix == APP_SECTION_PV_STAT]
    assert len(selected) == 1
    raw._async_fetch_historical_day_chart_source = AsyncMock(  # ruff: ignore[private-member-access]
        return_value=(
            "fetched",
            {
                "unit": "W",
                "y": [1000] * samples,
                "_request": {
                    "dateType": "day",
                    "beginDate": "2026-07-08",
                    "endDate": "2026-07-08",
                },
            },
        )
    )
    raw._async_import_historical_day_chart_statistics_for_device = AsyncMock()  # ruff: ignore[private-member-access]
    await coordinator._async_process_http_day_backfill_candidates(  # ruff: ignore[private-member-access]
        selected,
        progress,
        request_budget=1,
        week_start=date(2026, 7, 6),
        today=date(2026, 7, 9),
    )
    raw._async_import_historical_day_chart_statistics_for_device.assert_not_awaited()  # ruff: ignore[private-member-access]
    assert progress.requests == 1
    assert {k: days["2026-07-08"][k] for k in original} == original
    updates: dict[str, dict[str, Any]] = {}
    _, _, verified = coordinator._restore_or_reopen_imported_day_totals(  # ruff: ignore[private-member-access]
        updates,
        state_status=co.BackfillStatus.IMPORTED,
        day_state=days["2026-07-08"],
        device_id=_DEV,
        target_day=date(2026, 7, 8),
        section_prefix=APP_SECTION_PV_STAT,
        week_start=date(2026, 7, 6),
        today=date(2026, 7, 9),
    )
    assert verified is (samples == _FULL_DAY_SAMPLES)
    if verified:
        assert updates[_DEV]["2026-07-08"][APP_SECTION_PV_STAT] == {
            "totalSolarEnergy": 24.0
        }
    else:
        assert not updates
    again = coordinator._collect_http_day_backfill_candidates(  # ruff: ignore[private-member-access]
        {_DEV: payload},
        progress.target_days,
        today=date(2026, 7, 9),
        now_epoch=datetime(2026, 7, 9, tzinfo=UTC).timestamp(),
        progress=progress,
    )
    assert not any(c.section_prefix == APP_SECTION_PV_STAT for c in again)


@pytest.mark.parametrize("verification_only", [False, True])
def test_day_verification_queue_uses_its_own_attempt_clock(
    verification_only: bool,
) -> None:
    """A failed totals probe rotates without changing Recorder import history."""
    coordinator = _ready_backfill_coordinator()
    payload = {"system_meta": {"id": "system-1"}}
    source, days = coordinator._http_day_backfill_days_state(_DEV, APP_SECTION_PV_STAT)  # ruff: ignore[private-member-access]
    source["curve_rule"] = co._HTTP_DAY_CURVE_IMPORT_RULE  # ruff: ignore[private-member-access]
    status = "imported" if verification_only else "pending"
    days["2026-07-08"] = {
        "status": status,
        "last_attempt_at": "2026-07-08T23:00:00+00:00",
        "totals_last_attempt_at": "2026-07-10T03:00:00+00:00",
        "totals_last_error": "transport_error",
    }
    days["2026-07-09"] = {
        "status": status,
        "last_attempt_at": "2026-07-09T23:00:00+00:00",
    }
    original = deepcopy(days)
    progress = co._HttpDayBackfillProgress(  # ruff: ignore[private-member-access]
        target_days=[date(2026, 7, 8), date(2026, 7, 9)],
        force=True,
        window_days=2,
        include_current_year=True,
        now_monotonic=0.0,
        verification_only=verification_only,
    )
    candidates = coordinator._collect_http_day_backfill_candidates(  # ruff: ignore[private-member-access]
        {_DEV: payload},
        progress.target_days,
        today=date(2026, 7, 10),
        now_epoch=datetime(2026, 7, 10, 4, tzinfo=UTC).timestamp(),
        progress=progress,
    )
    selected = [
        c.target_day for c in candidates if c.section_prefix == APP_SECTION_PV_STAT
    ]
    expected = (
        [date(2026, 7, 9), date(2026, 7, 8)]
        if verification_only
        else progress.target_days
    )
    assert selected == expected
    if verification_only:
        assert days == original


def test_native_home_day_scalar_is_not_replaced_by_a_watt_curve() -> None:
    """The source contract separates native day energy from Recorder W curves."""
    source = {
        "unit": "W",
        "totalHomeEgy": "0.00",
        "x": ["00:00", "00:05"],
        "y": [600, 600],
        "_request": {
            "dateType": "day",
            "beginDate": "2026-09-30",
            "endDate": "2026-09-30",
        },
    }
    payload: dict[str, Any] = {
        "home_trends": source,
        "device_today_energy": {"dh": 0},
    }

    JackerySolarVaultCoordinator._reconcile_compact_today_energy(  # ruff: ignore[private-member-access]
        payload, today=date(2026, 9, 30)
    )

    assert payload["device_today_energy"]["dh"] == 0
    assert source["totalHomeEgy"] == "0.00"
    assert source["y"] == [600, 600]


@pytest.mark.parametrize("store_fails", [False, True])
async def test_cancelled_backfill_persists_fetched_progress(store_fails: bool) -> None:
    """Reload must preserve fetched evidence while propagating task cancellation."""
    coordinator = _ready_backfill_coordinator()
    if store_fails:
        store = cast("Any", coordinator)._statistics_backfill_store  # ruff: ignore[private-member-access]
        store.async_save.side_effect = RuntimeError("store unavailable")
    coordinator._statistics_startup_sync_pending = False  # ruff: ignore[private-member-access]
    coordinator._statistics_backfill_task = None  # ruff: ignore[private-member-access]
    state = coordinator._statistics_backfill_device_state(_DEV)  # ruff: ignore[private-member-access]
    state["inflight"] = {"unimported_source": {"unit": "W", "y": [500]}}
    coordinator._async_advance_statistics_backfill = AsyncMock(  # ruff: ignore[private-member-access]
        side_effect=asyncio.CancelledError
    )
    with pytest.raises(asyncio.CancelledError):
        await coordinator._async_statistics_backfill_job({_DEV: {}})  # ruff: ignore[private-member-access]
    store = cast("Any", coordinator)._statistics_backfill_store  # ruff: ignore[private-member-access]
    saved = store.async_save.await_args.args[0]
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
        now_epoch=datetime(2026, 7, 9, tzinfo=UTC).timestamp(),
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
        now_epoch=datetime(2026, 7, 9, tzinfo=UTC).timestamp(),
        progress=progress,
    )
    assert [
        c.target_day for c in candidates if c.section_prefix == APP_SECTION_HOME_STAT
    ] == [date(2026, 4, 15)]


def test_previous_day_backfill_waits_until_recorder_window_closes() -> None:
    """A midnight fetch must not mark a valid day unmapped for the whole day."""
    coordinator = _ready_backfill_coordinator()
    target = date(2026, 9, 28)
    today = date(2026, 9, 29)
    ready = coordinator._local_statistic_start(today).timestamp() + 2 * 3600  # ruff: ignore[private-member-access]
    _, days = coordinator._http_day_backfill_days_state(_DEV, APP_SECTION_HOME_STAT)  # ruff: ignore[private-member-access]
    days[target.isoformat()] = {
        "status": "unmapped",
        "checked_date": today.isoformat(),
        "last_attempt_at": datetime.fromtimestamp(ready - 3600, UTC).isoformat(),
        "unimported_source": {"unit": "W", "y": [500] * 288},
    }
    progress = co._HttpDayBackfillProgress(  # ruff: ignore[private-member-access]
        target_days=[target],
        force=True,
        window_days=1,
        include_current_year=True,
        now_monotonic=0.0,
    )

    def candidates(now_epoch: float) -> list[co._HttpDayBackfillCandidate]:
        return coordinator._collect_http_day_backfill_candidates(  # ruff: ignore[private-member-access]
            {_DEV: {}},
            [target],
            today=today,
            now_epoch=now_epoch,
            progress=progress,
        )

    assert candidates(ready - 1) == []
    assert days[target.isoformat()]["status"] == "unmapped"
    assert [candidate.section_prefix for candidate in candidates(ready + 1)].count(
        APP_SECTION_HOME_STAT
    ) == 1
    assert days[target.isoformat()]["status"] == "pending"


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
    # pyrefly: ignore [no-any-return-implicit]
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
    cast("Any", coordinator).hass = SimpleNamespace(
        config=SimpleNamespace(time_zone="Europe/Berlin")
    )
    cast("Any", coordinator)._statistics_backfill_store = _backfill_store_double()  # ruff: ignore[private-member-access]
    coordinator._statistics_backfill_state = {  # ruff: ignore[private-member-access]
        co._STATISTICS_BACKFILL_STORE_DEVICES: {},  # ruff: ignore[private-member-access]
    }
    coordinator._statistics_backfill_state_loaded = True  # ruff: ignore[private-member-access]
    coordinator._statistics_import_diagnostics = {}  # ruff: ignore[private-member-access]
    cast("Any", coordinator)._local_today = lambda: date(2026, 7, 9)  # ruff: ignore[private-member-access]
    return coordinator


# --- _enabled_app_chart_date_types ---------------------------------------


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
    # An unattempted day precedes a day already tried four times.
    assert [
        candidate.target_day
        for candidate in candidates
        if candidate.section_prefix == APP_SECTION_HOME_STAT
    ] == [date(2026, 4, 16), date(2026, 4, 15)]


def test_startup_preserves_imported_and_checked_empty_days() -> None:
    """Restart reuses completed rows and does not refetch today's empty source."""
    coordinator = _ready_backfill_coordinator()
    coordinator._statistics_startup_sync_pending = True  # ruff: ignore[private-member-access]
    source, days = coordinator._http_day_backfill_days_state(  # ruff: ignore[private-member-access]
        _DEV, APP_SECTION_PV_STAT
    )
    source["curve_rule"] = co._HTTP_DAY_CURVE_IMPORT_RULE  # ruff: ignore[private-member-access]
    days["2026-07-07"] = {"status": "imported"}
    days["2026-07-08"] = {"status": "empty", "checked_date": "2026-07-09"}
    progress = co._HttpDayBackfillProgress(  # ruff: ignore[private-member-access]
        target_days=[date(2026, 7, 7), date(2026, 7, 8)],
        force=True,
        window_days=2,
        include_current_year=True,
        now_monotonic=0,
    )

    candidates = coordinator._collect_http_day_backfill_candidates(  # ruff: ignore[private-member-access]
        {_DEV: {}},
        progress.target_days,
        today=date(2026, 7, 9),
        now_epoch=datetime(2026, 7, 9, tzinfo=UTC).timestamp(),
        progress=progress,
    )

    assert not any(c.section_prefix == APP_SECTION_PV_STAT for c in candidates)
    assert days["2026-07-07"]["status"] == "imported"
    assert days["2026-07-08"]["status"] == "empty"


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


def test_partial_year_rechecks_zero_month_before_active_months() -> None:
    """The September export has April=0 although April's month chart is nonzero."""
    coordinator = _coordinator()
    year = {
        "unit": "kWh",
        "totalCharge": "163.27",
        "y1": [0.0, 0.0, 0.0, 0.0, 92.62, 70.65] + [0.0] * 6,
    }

    assert coordinator._needs_year_month_backfill(  # ruff: ignore[private-member-access]
        {"device_battery_stat_year": year},
        "device_battery_stat",
        ("totalCharge",),
        today=date(2026, 9, 30),
    )


def test_april_month_fills_partial_year_without_discarding_may_and_june() -> None:
    """Same-endpoint April 47.05 kWh augments a year that retains May/June."""
    payload = {
        "device_battery_stat_year": {
            "unit": "kWh",
            "totalCharge": "163.27",
            "y1": [0.0, 0.0, 0.0, 0.0, 92.62, 70.65] + [0.0] * 6,
        }
    }
    april = {
        "unit": "kWh",
        "totalCharge": "47.05",
        "y1": [47.05] + [0.0] * 29,
    }

    apply_year_month_backfill(payload, {"device_battery_stat": {4: april}})

    corrected = payload["device_battery_stat_year"]
    assert corrected["totalCharge"] == pytest.approx(210.32)
    assert corrected["y1"][3:6] == [47.05, 92.62, 70.65]


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
