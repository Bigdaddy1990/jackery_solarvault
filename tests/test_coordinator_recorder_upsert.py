"""Real-recorder tests for app-chart external statistics upsert behavior.

These exercise :meth:`JackerySolarVaultCoordinator._async_add_app_chart_statistics`
against a genuine Home Assistant recorder (``recorder_mock``) and assert the
actual stored ``state``/``sum`` rows via ``statistics_during_period``. The only
mocked boundary is the recorder fixture itself; all statistic-import logic is
real production code.

The day-hourly ``statistic_id`` has no date part, so its cumulative ``sum``
runs across every imported day. The bug under test dropped Jackery's historical
corrections and left trailing rows with stale sums (a non-monotonic sequence HA
reads as a spurious counter reset). Each test is written so it fails on the
pre-fix behavior and passes once corrections are re-emitted from the first
divergent bucket.
"""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
import itertools
import operator
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock

import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.jackery_solarvault.const import (
    DOMAIN,
    EXTERNAL_STAT_BUCKET_DAY_HOURLY,
)
import custom_components.jackery_solarvault.coordinator as coordinator_module
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from custom_components.jackery_solarvault.util import external_trend_statistic_id
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.statistics import (
    adjust_statistics,
    statistics_during_period,
)
from homeassistant.components.recorder.tasks import SynchronizeTask
from homeassistant.const import UnitOfEnergy

if TYPE_CHECKING:
    from homeassistant.components.recorder import Recorder
    from homeassistant.core import HomeAssistant

_DEVICE_ID = "dev1"
_METRIC_KEY = "pv_energy"
_STAT_ID = external_trend_statistic_id(
    DOMAIN,
    _DEVICE_ID,
    _METRIC_KEY,
    EXTERNAL_STAT_BUCKET_DAY_HOURLY,
)
_EXPECTED_FIRST_IMPORT_COUNT = 3


def _coordinator(hass: HomeAssistant) -> JackerySolarVaultCoordinator:
    """Build a bare coordinator wired to a real hass + recorder."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    obj = cast("Any", coordinator)
    obj.hass = hass
    obj._stat_import_last_sig = {}
    obj._stat_import_pending = {}
    obj._statistics_import_diagnostics = {}
    obj._statistics_recorder_lock = asyncio.Lock()
    obj._device_index = {}
    return coordinator


def _point(start: datetime, value: float) -> SimpleNamespace:
    """Return a minimal app chart point exposing ``start_date`` and ``value``."""
    return SimpleNamespace(start_date=start, value=value)


async def _import(
    coordinator: JackerySolarVaultCoordinator,
    hass: HomeAssistant,
    points: list[SimpleNamespace],
) -> tuple[bool, int]:
    """Import a day-hourly series and block until the recorder has committed."""
    result = await coordinator._async_add_app_chart_statistics(
        device_id=_DEVICE_ID,
        name_prefix="Jackery",
        metric_key=_METRIC_KEY,
        label="PV Energy",
        bucket=EXTERNAL_STAT_BUCKET_DAY_HOURLY,
        bucket_label="Day (hourly)",
        points=points,
    )
    await async_wait_recording_done(hass)
    return result


async def _read_rows(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Return the stored (start, state, sum) rows for the day-hourly series."""
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        datetime(2026, 6, 1, tzinfo=UTC),
        datetime(2026, 8, 1, tzinfo=UTC),
        {_STAT_ID},
        "hour",
        None,
        {"start", "state", "sum"},
    )
    series = rows.get(_STAT_ID, [])
    generic_rows = cast("list[dict[str, Any]]", series)
    return sorted(generic_rows, key=operator.itemgetter("start"))


def _row_at(rows: list[dict[str, Any]], start: datetime) -> dict[str, Any]:
    """Return the stored row whose start matches ``start`` (unix seconds)."""
    target = start.timestamp()
    for row in rows:
        if abs(row["start"] - target) < 1.0:
            return row
    msg = f"no stored row at {start.isoformat()}"
    raise AssertionError(msg)


def _assert_monotonic(rows: list[dict[str, Any]]) -> None:
    """Assert the stored sum sequence never decreases at any adjacent pair."""
    sums = [row["sum"] for row in rows]
    for earlier, later in itertools.pairwise(sums):
        assert later >= earlier - 1e-6, f"sum went backwards: {sums}"


@pytest.fixture
def mock_recorder_before_hass(recorder_db_url: str) -> None:
    """Prepare the recorder database before Home Assistant starts."""
    del recorder_db_url


async def test_day_batch_fifo_queues_exactly_one_recorder_barrier(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One batch barrier orders every deferred day series before periods."""
    coordinator = _coordinator(hass)
    recorder_any = cast("Any", recorder_mock)
    real_queue_task = recorder_any.queue_task
    synchronize_tasks = 0

    def counting_queue_task(task: object) -> None:
        nonlocal synchronize_tasks
        if isinstance(task, SynchronizeTask):
            synchronize_tasks += 1
        real_queue_task(task)

    monkeypatch.setattr(recorder_mock, "queue_task", counting_queue_task)

    assert await coordinator._async_wait_for_statistics_recorder_fifo() is True
    assert synchronize_tasks == 1


async def test_day_batch_fifo_timeout_stays_retryable(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A recorder stall returns control without claiming the batch committed."""
    coordinator = _coordinator(hass)
    recorder_any = cast("Any", recorder_mock)
    real_queue_task = recorder_any.queue_task

    def queue_without_sync(task: object) -> None:
        if isinstance(task, SynchronizeTask):
            return
        real_queue_task(task)

    monkeypatch.setattr(
        coordinator_module,
        "_STATISTICS_RECORDER_VERIFICATION_TIMEOUT_SEC",
        0.01,
    )
    monkeypatch.setattr(recorder_mock, "queue_task", queue_without_sync)

    async with asyncio.timeout(0.5):
        result = await coordinator._async_wait_for_statistics_recorder_fifo()

    assert result is False
    assert coordinator._statistics_import_diagnostics["last_recorder_fifo_failure"] == {
        "reason": "day_batch_timeout"
    }


async def test_corrected_bucket_sum_is_updated_not_dropped(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """A corrected interval re-import updates the cumulative sum chain.

    External app-chart rows intentionally carry only ``sum``: the chart value
    is an interval increment, not a second HA sensor-state channel.
    """
    await hass.config.async_set_time_zone("UTC")
    coordinator = _coordinator(hass)
    base = datetime(2026, 7, 1, 10, tzinfo=UTC)
    hour10, hour11, hour12 = base, base + timedelta(hours=1), base + timedelta(hours=2)

    await _import(
        coordinator,
        hass,
        [_point(hour10, 1.0), _point(hour11, 2.0), _point(hour12, 3.0)],
    )

    ok, _count = await _import(
        coordinator,
        hass,
        [_point(hour10, 1.0), _point(hour11, 5.0), _point(hour12, 3.0)],
    )

    assert ok is True
    rows = await _read_rows(hass)
    assert _row_at(rows, hour11)["state"] is None
    assert _row_at(rows, hour11)["sum"] == pytest.approx(6.0)
    # The trailing bucket is rebased on the correction, keeping the sum monotonic.
    assert _row_at(rows, hour12)["sum"] == pytest.approx(9.0)
    _assert_monotonic(rows)


async def test_import_uses_fifo_recorder_barrier(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verification does not use the racy queue-empty commit-future probe."""
    await hass.config.async_set_time_zone("UTC")
    coordinator = _coordinator(hass)
    monkeypatch.setattr(
        recorder_mock,
        "async_block_till_done",
        lambda: pytest.fail("racy recorder queue probe was used"),
    )

    ok, count = await coordinator._async_add_app_chart_statistics(
        device_id=_DEVICE_ID,
        name_prefix="Jackery",
        metric_key="fifo_barrier_energy",
        label="FIFO barrier energy",
        bucket=EXTERNAL_STAT_BUCKET_DAY_HOURLY,
        bucket_label="Day (hourly)",
        points=[_point(datetime(2026, 7, 1, 15, tzinfo=UTC), 1.25)],
    )

    assert ok is True
    assert count == 1


async def test_import_waits_for_delayed_recorder_visibility(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A bounded verifier outlives three stale post-import database reads."""
    await hass.config.async_set_time_zone("UTC")
    coordinator = _coordinator(hass)
    real_reader = cast(
        "Callable[..., object]",
        coordinator_module.statistics_during_period,
    )
    read_count = 0

    def delayed_reader(*args: object, **kwargs: object) -> object:
        nonlocal read_count
        read_count += 1
        # Read 1 is the pre-import prefix lookup. Simulate three stale reads on
        # the separate verification connection before the committed row appears.
        if 2 <= read_count <= 4:
            return {}
        return real_reader(*args, **kwargs)

    monkeypatch.setattr(
        coordinator_module,
        "statistics_during_period",
        delayed_reader,
    )

    ok, count = await coordinator._async_add_app_chart_statistics(
        device_id=_DEVICE_ID,
        name_prefix="Jackery",
        metric_key="delayed_visibility_energy",
        label="Delayed visibility energy",
        bucket=EXTERNAL_STAT_BUCKET_DAY_HOURLY,
        bucket_label="Day (hourly)",
        points=[_point(datetime(2026, 7, 1, 16, tzinfo=UTC), 2.5)],
    )

    assert ok is True
    assert count == 1
    assert read_count >= 5


async def test_import_deadline_leaves_unverified_recorder_retryable(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unverified queued HA retry must remain retryable after timeout."""
    await hass.config.async_set_time_zone("UTC")
    coordinator = _coordinator(hass)
    recorder_any = cast("Any", recorder_mock)
    real_queue_task = recorder_any.queue_task

    def queue_without_sync(task: object) -> None:
        if isinstance(task, SynchronizeTask):
            return
        real_queue_task(task)

    monkeypatch.setattr(
        coordinator_module,
        "_STATISTICS_RECORDER_VERIFICATION_TIMEOUT_SEC",
        0.01,
    )
    monkeypatch.setattr(recorder_mock, "queue_task", queue_without_sync)
    queued_import = MagicMock()
    monkeypatch.setattr(
        coordinator_module,
        "async_add_external_statistics",
        queued_import,
    )

    async with asyncio.timeout(0.5):
        ok, count = await coordinator._async_add_app_chart_statistics(
            device_id=_DEVICE_ID,
            name_prefix="Jackery",
            metric_key="stalled_barrier_energy",
            label="Stalled barrier energy",
            bucket=EXTERNAL_STAT_BUCKET_DAY_HOURLY,
            bucket_label="Day (hourly)",
            points=[_point(datetime(2026, 7, 1, 17, tzinfo=UTC), 3.5)],
        )
        (
            duplicate_ok,
            duplicate_count,
        ) = await coordinator._async_add_app_chart_statistics(
            device_id=_DEVICE_ID,
            name_prefix="Jackery",
            metric_key="stalled_barrier_energy",
            label="Stalled barrier energy",
            bucket=EXTERNAL_STAT_BUCKET_DAY_HOURLY,
            bucket_label="Day (hourly)",
            points=[_point(datetime(2026, 7, 1, 17, tzinfo=UTC), 3.5)],
        )

    assert (ok, count) == (False, 0)
    assert (duplicate_ok, duplicate_count) == (False, 0)
    assert queued_import.call_count == 2


async def test_live_import_defers_verification_without_fifo_barrier(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live chart write must not synchronously wait for the Recorder queue."""
    await hass.config.async_set_time_zone("UTC")
    coordinator = _coordinator(hass)
    queued_import = MagicMock()
    monkeypatch.setattr(
        coordinator_module,
        "async_add_external_statistics",
        queued_import,
    )
    recorder_any = cast("Any", recorder_mock)
    real_queue_task = recorder_any.queue_task

    def reject_synchronize_task(task: object) -> None:
        if isinstance(task, SynchronizeTask):
            pytest.fail("live import queued a synchronous FIFO barrier")
        real_queue_task(task)

    monkeypatch.setattr(recorder_mock, "queue_task", reject_synchronize_task)

    kwargs = {
        "device_id": _DEVICE_ID,
        "name_prefix": "Jackery",
        "metric_key": "deferred_live_energy",
        "label": "Deferred live energy",
        "bucket": EXTERNAL_STAT_BUCKET_DAY_HOURLY,
        "bucket_label": "Day (hourly)",
        "points": [_point(datetime(2026, 7, 1, 18, tzinfo=UTC), 4.5)],
        "defer_verification": True,
    }
    first = await coordinator._async_add_app_chart_statistics(**kwargs)
    duplicate = await coordinator._async_add_app_chart_statistics(**kwargs)

    assert first == (False, 0)
    assert duplicate == (False, 0)
    assert queued_import.call_count == 1


async def test_live_import_marks_signature_confirmed_after_recorder_commits(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """A deferred live import becomes successful only after a Recorder read."""
    del recorder_mock
    await hass.config.async_set_time_zone("UTC")
    coordinator = _coordinator(hass)
    kwargs = {
        "device_id": _DEVICE_ID,
        "name_prefix": "Jackery",
        "metric_key": "deferred_confirmation_energy",
        "label": "Deferred confirmation energy",
        "bucket": EXTERNAL_STAT_BUCKET_DAY_HOURLY,
        "bucket_label": "Day (hourly)",
        "points": [_point(datetime(2026, 7, 1, 19, tzinfo=UTC), 5.5)],
        "defer_verification": True,
    }

    queued = await coordinator._async_add_app_chart_statistics(**kwargs)
    await async_wait_recording_done(hass)
    confirmed = await coordinator._async_add_app_chart_statistics(**kwargs)

    assert queued == (False, 0)
    assert confirmed == (True, 0)


async def test_mid_series_insertion_keeps_sum_monotonic(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """FINDING 4/1: inserting a mid-series bucket rebases the trailing rows.

    Pre-fix, only the new bucket was appended while the later existing bucket
    kept its old (now too-low) sum, so the sequence went backwards. All stored
    sums must be monotonically non-decreasing after the insertion.
    """
    await hass.config.async_set_time_zone("UTC")
    coordinator = _coordinator(hass)
    base = datetime(2026, 7, 2, 10, tzinfo=UTC)
    hour10, hour11, hour12 = base, base + timedelta(hours=1), base + timedelta(hours=2)

    # Hour 11 is absent from the first import.
    await _import(coordinator, hass, [_point(hour10, 1.0), _point(hour12, 2.0)])

    await _import(
        coordinator,
        hass,
        [_point(hour10, 1.0), _point(hour11, 4.0), _point(hour12, 2.0)],
    )

    rows = await _read_rows(hass)
    assert [row["sum"] for row in rows] == pytest.approx([1.0, 5.0, 7.0])
    _assert_monotonic(rows)


async def test_identical_reimport_is_idempotent(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """Re-importing an unchanged series writes nothing (signature short-circuit)."""
    await hass.config.async_set_time_zone("UTC")
    coordinator = _coordinator(hass)
    base = datetime(2026, 7, 3, 10, tzinfo=UTC)
    points = [
        _point(base, 1.0),
        _point(base + timedelta(hours=1), 2.0),
        _point(base + timedelta(hours=2), 3.0),
    ]

    ok_first, count_first = await _import(coordinator, hass, points)
    rows_first = await _read_rows(hass)

    ok_second, count_second = await _import(coordinator, hass, points)
    rows_second = await _read_rows(hass)

    assert ok_first is True
    assert count_first == _EXPECTED_FIRST_IMPORT_COUNT
    assert ok_second is True
    assert count_second == 0
    assert [row["sum"] for row in rows_second] == pytest.approx([
        row["sum"] for row in rows_first
    ])


async def test_earlier_day_correction_rebases_later_day(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """Cross-day: correcting day 1 re-bases day 2's shared-id cumulative sums.

    The day-hourly ``statistic_id`` spans all days, so day 2's offset is day 1's
    last sum. Pre-fix, re-importing day 2 (unchanged raw states) short-circuited
    on the raw-only signature and its sums stayed stale, dropping below day 1's
    corrected tail. Folding the offset into the signature forces day 2 to
    re-import and rebase.
    """
    await hass.config.async_set_time_zone("UTC")
    coordinator = _coordinator(hass)
    d1 = datetime(2026, 7, 4, 10, tzinfo=UTC)
    d2 = datetime(2026, 7, 5, 10, tzinfo=UTC)
    day1 = [_point(d1, 1.0), _point(d1 + timedelta(hours=1), 2.0)]
    day1_corrected = [_point(d1, 10.0), _point(d1 + timedelta(hours=1), 2.0)]
    day2 = [_point(d2, 4.0), _point(d2 + timedelta(hours=1), 5.0)]

    await _import(coordinator, hass, day1)
    await _import(coordinator, hass, day2)

    await _import(coordinator, hass, day1_corrected)
    await _import(coordinator, hass, day2)

    rows = await _read_rows(hass)
    _assert_monotonic(rows)
    day1_last = _row_at(rows, d1 + timedelta(hours=1))["sum"]
    day2_first = _row_at(rows, d2)["sum"]
    assert day1_last == pytest.approx(12.0)
    assert day2_first == pytest.approx(16.0)
    assert day1_last <= day2_first


async def test_user_adjusted_prior_sums_are_preserved(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """An intentional HA sum adjustment must remain the cumulative baseline."""
    await hass.config.async_set_time_zone("UTC")
    coordinator = _coordinator(hass)
    recorder = get_instance(hass)
    d1 = datetime(2026, 7, 6, 10, tzinfo=UTC)
    d2 = datetime(2026, 7, 7, 10, tzinfo=UTC)

    await _import(
        coordinator,
        hass,
        [_point(d1, 1.0), _point(d1 + timedelta(hours=1), 2.0)],
    )
    await recorder.async_add_executor_job(
        adjust_statistics,
        recorder,
        _STAT_ID,
        d1,
        100_000.0,
        UnitOfEnergy.KILO_WATT_HOUR,
    )
    adjusted = await _read_rows(hass)
    assert _row_at(adjusted, d1 + timedelta(hours=1))["sum"] == pytest.approx(
        100_003.0,
    )

    await _import(
        coordinator,
        hass,
        [_point(d2, 4.0), _point(d2 + timedelta(hours=1), 5.0)],
    )

    continued = await _read_rows(hass)
    assert [row["sum"] for row in continued] == pytest.approx(
        [100_001.0, 100_003.0, 100_007.0, 100_012.0],
    )
    _assert_monotonic(continued)
