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
from datetime import UTC, datetime, timedelta
import itertools
import operator
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

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
from homeassistant.const import UnitOfEnergy

if TYPE_CHECKING:
    from collections.abc import Callable

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
    obj._stat_import_last_sig = {}  # ruff: ignore[private-member-access]
    obj._statistics_import_diagnostics = {}  # ruff: ignore[private-member-access]
    obj._statistics_recorder_lock = asyncio.Lock()  # ruff: ignore[private-member-access]
    obj._device_index = {}  # ruff: ignore[private-member-access]
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
    result = await coordinator._async_add_app_chart_statistics(  # ruff: ignore[private-member-access]
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


@pytest.fixture()
def mock_recorder_before_hass(recorder_db_url: str) -> None:
    """Prepare the recorder database before Home Assistant starts."""
    del recorder_db_url


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


async def test_import_waits_for_public_recorder_commit_before_success(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A queued external statistic is not successful until Recorder commits it."""
    await hass.config.async_set_time_zone("UTC")
    coordinator = _coordinator(hass)
    queued_import = MagicMock()
    recorder_commit = AsyncMock()
    recorder = get_instance(hass)
    assert recorder is recorder_mock
    monkeypatch.setattr(
        coordinator_module,
        "async_add_external_statistics",
        queued_import,
    )
    monkeypatch.setattr(recorder, "async_block_till_done", recorder_commit)
    kwargs = {
        "device_id": _DEVICE_ID,
        "name_prefix": "Jackery",
        "metric_key": "queued_live_energy",
        "label": "Queued live energy",
        "bucket": EXTERNAL_STAT_BUCKET_DAY_HOURLY,
        "bucket_label": "Day (hourly)",
        "points": [_point(datetime(2026, 7, 1, 18, tzinfo=UTC), 4.5)],
    }
    first = await coordinator._async_add_app_chart_statistics(**kwargs)  # ruff: ignore[private-member-access]
    duplicate = await coordinator._async_add_app_chart_statistics(**kwargs)  # ruff: ignore[private-member-access]

    assert first == (True, 1)
    assert duplicate == (True, 0)
    assert queued_import.call_count == 1
    recorder_commit.assert_awaited_once_with()


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


async def test_downward_cloud_correction_preserves_later_interval_energy(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """Revising an old bucket must not invent energy in the following day."""
    await hass.config.async_set_time_zone("UTC")
    coordinator = _coordinator(hass)
    d1 = datetime(2026, 7, 20, 10, tzinfo=UTC)
    d2 = d1 + timedelta(days=1)

    await _import(
        coordinator, hass, [_point(d1, 6.0), _point(d1 + timedelta(hours=1), 1.0)]
    )
    await _import(coordinator, hass, [_point(d2, 2.0)])

    # The cloud now reports a *smaller* value for the already imported day.
    await _import(
        coordinator, hass, [_point(d1, 1.0), _point(d1 + timedelta(hours=1), 1.0)]
    )

    rows = await _read_rows(hass)
    _assert_monotonic(rows)
    assert _row_at(rows, d1 + timedelta(hours=1))["sum"] == pytest.approx(2.0)
    assert _row_at(rows, d2)["sum"] == pytest.approx(4.0)


async def test_late_historical_day_rebases_already_imported_future_day(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """A late historical day must rebase an already imported later day."""
    await hass.config.async_set_time_zone("UTC")
    coordinator = _coordinator(hass)
    d0 = datetime(2026, 7, 8, 10, tzinfo=UTC)
    d1 = d0 + timedelta(days=1)
    d2 = d1 + timedelta(days=1)

    await _import(coordinator, hass, [_point(d0, 100.0)])
    await _import(
        coordinator,
        hass,
        [_point(d2, 4.0), _point(d2 + timedelta(hours=1), 5.0)],
    )
    await _import(
        coordinator,
        hass,
        [_point(d1, 10.0), _point(d1 + timedelta(hours=1), 2.0)],
    )

    rows = await _read_rows(hass)
    assert [row["sum"] for row in rows] == pytest.approx([
        100.0,
        110.0,
        112.0,
        116.0,
        121.0,
    ])
    _assert_monotonic(rows)


@pytest.mark.parametrize(
    "failed_query", ["_load_offset", "statistics_during_period", "_load_future"]
)
async def test_recorder_read_failure_preserves_rows_and_allows_retry(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    failed_query: str,
) -> None:
    """A failed read must not become an empty baseline or acknowledge an import."""
    await hass.config.async_set_time_zone("UTC")
    coordinator = _coordinator(hass)
    start = datetime(2026, 7, 10, 10, tzinfo=UTC)
    await _import(coordinator, hass, [_point(start, 10.0)])
    await _import(coordinator, hass, [_point(start + timedelta(days=1), 2.0)])
    before = await _read_rows(hass)
    execute = recorder_mock.async_add_executor_job

    async def fail_read(target: Callable[..., object], *args: object) -> object:
        if getattr(target, "__name__", None) == failed_query:
            message = "recorder read unavailable"
            raise RuntimeError(message)
        return await execute(target, *args)

    with monkeypatch.context() as patch:
        patch.setattr(recorder_mock, "async_add_executor_job", fail_read)
        result = await _import(coordinator, hass, [_point(start, 3.0)])

    assert result == (False, 0)
    assert await _read_rows(hass) == before
    assert (await _import(coordinator, hass, [_point(start, 3.0)]))[0]
    assert [row["sum"] for row in await _read_rows(hass)] == pytest.approx([3.0, 5.0])


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
