"""Days without any curve are filled from the month chart's daily kWh bucket.

Live 2026-09-27: device and system day curves end ~90 days back, so every day
before 29.06. stayed "empty" and the day sensors had no history from April,
although the month charts carry one kWh value per day back to commissioning.
"""

from datetime import UTC, date, datetime
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault import coordinator as coordinator_module
from custom_components.jackery_solarvault.const import (
    APP_SECTION_BATTERY_STAT,
    APP_STAT_TOTAL_CHARGE,
    DATE_TYPE_DAY,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
    JackeryStatisticHistoryPendingError,
    _HttpDayBackfillCandidate,  # ruff: ignore[import-private-name]
    _HttpDayBackfillProgress,  # ruff: ignore[import-private-name]
)

_DEVICE = "dev-1"
_STAT_ID = "sensor.solarvault_batterieladung_heute"
_CURVE_RULE = coordinator_module._HTTP_DAY_CURVE_IMPORT_RULE  # ruff: ignore[private-member-access]


def _month_payload(begin: str, end: str, charge: list[float]) -> dict[str, Any]:
    return {
        "_request": {"beginDate": begin, "dateType": "month", "endDate": end},
        "unit": "kWh",
        "totalCharge": str(sum(charge)),
        "y1": charge,
        "y2": [0.0] * len(charge),
    }


def _coordinator(month: dict[str, Any]) -> tuple[JackerySolarVaultCoordinator, Any]:
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    obj = cast("Any", coordinator)
    obj.hass = SimpleNamespace(config=SimpleNamespace(time_zone="UTC"))
    obj._device_index = {}  # ruff: ignore[private-member-access]
    obj.api = SimpleNamespace(
        async_get_device_battery_stat=AsyncMock(return_value=month)
    )
    obj._energy_statistic_targets = lambda _device, _section, stat_key: (  # ruff: ignore[private-member-access]
        [(_STAT_ID, DATE_TYPE_DAY)] if stat_key == APP_STAT_TOTAL_CHARGE else []
    )
    obj._async_reconcile_statistic_day = AsyncMock(return_value=24)  # ruff: ignore[private-member-access]
    return coordinator, obj


def _candidate(
    day: date, state: dict[str, Any] | None = None
) -> _HttpDayBackfillCandidate:
    return _HttpDayBackfillCandidate(
        priority=1,
        attempted=0,
        last_attempt="",
        target_day=day,
        attempts=0,
        device_id=_DEVICE,
        section_prefix=APP_SECTION_BATTERY_STAT,
        payload={},
        day_state=state if state is not None else {},
        days_state={},
    )


def _progress() -> _HttpDayBackfillProgress:
    return _HttpDayBackfillProgress(
        target_days=[],
        force=True,
        window_days=120,
        include_current_year=True,
        now_monotonic=0.0,
    )


@pytest.mark.asyncio()
async def test_curveless_day_books_its_month_bucket_in_the_last_hour() -> None:
    """May 2 carries 3.26 kWh in the May chart; the day total lands at 23:00."""
    coordinator, obj = _coordinator(
        _month_payload("2026-05-01", "2026-05-31", [0.0, 3.26, 1.42] + [0.0] * 28)
    )

    progress = _progress()
    candidate = _candidate(date(2026, 5, 2))

    ok = await coordinator._async_import_month_chart_day(  # ruff: ignore[private-member-access]
        candidate, progress
    )

    assert ok is True
    assert progress.gap_filled_rows == 24  # ruff: ignore[magic-value-comparison]
    assert candidate.day_state["source"] == "month_chart"
    reconcile = obj._async_reconcile_statistic_day  # ruff: ignore[private-member-access]
    statistic_id, hours, energy = reconcile.await_args.args
    day_start = datetime(2026, 5, 2, tzinfo=UTC).timestamp()
    assert statistic_id == _STAT_ID
    assert hours[0] == day_start
    assert len(hours) == 24  # ruff: ignore[magic-value-comparison]
    assert energy == {day_start + 23 * 3600: pytest.approx(3.26)}


@pytest.mark.asyncio()
async def test_month_chart_is_fetched_once_for_all_days_of_the_month() -> None:
    """One request per device, source and month, not one per day."""
    coordinator, obj = _coordinator(
        _month_payload("2026-05-01", "2026-05-31", [1.0] * 31)
    )
    progress = _progress()

    for day in (date(2026, 5, 2), date(2026, 5, 3)):
        await coordinator._async_import_month_chart_day(  # ruff: ignore[private-member-access]
            _candidate(day), progress
        )

    assert obj.api.async_get_device_battery_stat.await_count == 1
    assert progress.requests == 1


@pytest.mark.asyncio()
async def test_empty_month_chart_leaves_the_day_empty() -> None:
    """A month without data writes nothing and keeps the day retryable."""
    coordinator, obj = _coordinator({})

    progress = _progress()

    ok = await coordinator._async_import_month_chart_day(  # ruff: ignore[private-member-access]
        _candidate(date(2026, 5, 2)), progress
    )

    assert ok is None
    assert progress.gap_filled_rows == 0
    obj._async_reconcile_statistic_day.assert_not_awaited()  # ruff: ignore[private-member-access]


@pytest.mark.asyncio()
async def test_curveless_queue_day_is_imported_from_the_month_chart() -> None:
    """The day queue marks a curveless day imported once the month chart filled it."""
    coordinator, obj = _coordinator(
        _month_payload("2026-05-01", "2026-05-31", [0.0, 3.26] + [0.0] * 29)
    )
    obj._async_fetch_historical_day_chart_source = AsyncMock(  # ruff: ignore[private-member-access]
        return_value=("empty_ambiguous", {})
    )
    state: dict[str, Any] = {}
    progress = _progress()

    await coordinator._async_process_http_day_backfill_candidate(  # ruff: ignore[private-member-access]
        _candidate(date(2026, 5, 2), state),
        progress,
        week_start=date(2026, 9, 21),
        today=date(2026, 9, 28),
    )

    assert state["status"] == "imported"
    assert state["source"] == "month_chart"
    assert state["imported_rows"] == 24  # ruff: ignore[magic-value-comparison]
    assert progress.gap_filled_rows == 24  # ruff: ignore[magic-value-comparison]


@pytest.mark.asyncio()
async def test_pending_recorder_history_does_not_block_other_days() -> None:
    """A day awaiting hourly history remains retryable while the next imports."""
    coordinator, obj = _coordinator(
        _month_payload("2026-05-01", "2026-05-31", [0.0, 3.26, 1.42] + [0.0] * 28)
    )
    obj._async_fetch_historical_day_chart_source = AsyncMock(  # ruff: ignore[private-member-access]
        return_value=("empty_ambiguous", {})
    )
    obj._async_reconcile_statistic_day = AsyncMock(  # ruff: ignore[private-member-access]
        side_effect=[
            JackeryStatisticHistoryPendingError("Waiting for hourly history"),
            24,
        ]
    )
    pending = _candidate(date(2026, 5, 2))
    ready = _candidate(date(2026, 5, 3))
    progress = _progress()
    progress.pending_sources = progress.actionable_sources = 2

    await coordinator._async_process_http_day_backfill_candidates(  # ruff: ignore[private-member-access]
        [pending, ready],
        progress,
        request_budget=None,
        week_start=date(2026, 9, 21),
        today=date(2026, 9, 28),
    )

    assert pending.day_state["status"] == "pending"
    assert pending.day_state["last_error"] == "Waiting for hourly history"
    assert "checked_date" not in pending.day_state
    assert ready.day_state["status"] == "imported"
    assert ready.day_state["imported_rows"] == 24  # ruff: ignore[magic-value-comparison]
    assert progress.pending_sources == progress.actionable_sources == 1
    assert progress.state_changed is True


@pytest.mark.asyncio()
@pytest.mark.parametrize("pending_day", [date(2026, 5, 2), date(2026, 9, 21)])
async def test_bounded_queue_rotates_days_waiting_for_recorder_history(
    pending_day: date,
) -> None:
    """Even a pending current-week day cannot monopolize each one-slot pass."""
    coordinator, obj = _coordinator({})
    ready_day = date(2026, 5, 3)
    today = date(2026, 9, 23)
    obj._statistics_backfill_state = {"devices": {}}  # ruff: ignore[private-member-access]
    obj._historical_day_source_prefixes = lambda _device, _payload: (  # ruff: ignore[private-member-access]
        APP_SECTION_BATTERY_STAT,
    )

    def fetch(_device: str, **kwargs: Any) -> dict[str, Any]:
        if kwargs["date_type"] != "month":
            return {}
        begin = kwargs["begin_date"]
        september = begin.startswith("2026-09")
        end = "2026-09-30" if september else "2026-05-31"
        return _month_payload(begin, end, [1.0] * (30 if september else 31))

    def reconcile(
        _statistic: str, hours: list[float], *_args: Any, **_kwargs: Any
    ) -> int:
        if datetime.fromtimestamp(hours[0], UTC).date() == pending_day:
            message = "Waiting for hourly history"
            raise JackeryStatisticHistoryPendingError(message)
        return 24

    obj.api.async_get_device_battery_stat = AsyncMock(side_effect=fetch)
    obj._async_reconcile_statistic_day = AsyncMock(side_effect=reconcile)  # ruff: ignore[private-member-access]
    for _pass in range(2):
        progress = _progress()
        candidates = coordinator._collect_http_day_backfill_candidates(  # ruff: ignore[private-member-access]
            {_DEVICE: {}},
            [pending_day, ready_day],
            today=today,
            now_epoch=datetime(2026, 9, 23, tzinfo=UTC).timestamp(),
            progress=progress,
        )
        await coordinator._async_process_http_day_backfill_candidates(  # ruff: ignore[private-member-access]
            candidates,
            progress,
            request_budget=1,
            week_start=date(2026, 9, 21),
            today=today,
        )

    _, days = coordinator._http_day_backfill_days_state(  # ruff: ignore[private-member-access]
        _DEVICE, APP_SECTION_BATTERY_STAT
    )
    assert days[pending_day.isoformat()]["status"] == "pending"
    assert days[ready_day.isoformat()]["status"] == "imported"


def test_days_checked_empty_by_older_code_reopen_once() -> None:
    """Live 2026-09-28: 140 days marked empty at 00:45 waited until the next day.

    The month-chart source arrived with the 15:53 deploy; the rule marker
    reopens only "empty" days once, never imported ones.
    """
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    obj = cast("Any", coordinator)
    days = {
        "2026-05-02": {"status": "empty", "checked_date": "2026-09-28"},
        "2026-07-02": {"status": "unmapped", "checked_date": "2026-09-28"},
        "2026-07-01": {"status": "imported", "checked_date": "2026-09-27"},
    }
    obj._statistics_backfill_state = {  # ruff: ignore[private-member-access]
        "devices": {
            _DEVICE: {
                "http_day_backfill": {
                    "sources": {
                        APP_SECTION_BATTERY_STAT: {
                            "curve_rule": _CURVE_RULE,
                            "days": days,
                        }
                    }
                }
            }
        }
    }

    coordinator._http_day_backfill_days_state(  # ruff: ignore[private-member-access]
        _DEVICE, APP_SECTION_BATTERY_STAT
    )

    assert "checked_date" not in days["2026-05-02"]
    assert "checked_date" not in days["2026-07-02"]
    assert days["2026-07-01"] == {"status": "imported", "checked_date": "2026-09-27"}

    days["2026-05-02"]["checked_date"] = "2026-09-28"
    coordinator._http_day_backfill_days_state(  # ruff: ignore[private-member-access]
        _DEVICE, APP_SECTION_BATTERY_STAT
    )
    assert days["2026-05-02"]["checked_date"] == "2026-09-28"
