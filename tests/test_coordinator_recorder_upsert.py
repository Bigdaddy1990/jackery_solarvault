"""Real-recorder regression tests for native app-chart energy reconciliation.

The current importer writes the sensor's own Recorder statistic. These tests
retain the historical correction, insertion, retry and user-adjustment cases
from the former external-statistics importer, using complete closed-day curves.
"""

import asyncio
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
import itertools
import operator
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

import custom_components.jackery_solarvault.coordinator as coordinator_module
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import StatisticsMeta
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    adjust_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfEnergy

if TYPE_CHECKING:
    from homeassistant.components.recorder import Recorder
    from homeassistant.core import HomeAssistant

_STAT_ID = "sensor.jackery_pv_energy_today"
_EXPECTED_FIRST_IMPORT_COUNT = 24
_BASELINE = datetime(2026, 6, 30, 23, tzinfo=UTC)


def _coordinator(hass: HomeAssistant) -> JackerySolarVaultCoordinator:
    """Build a bare coordinator wired to a real hass + recorder."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    obj = cast("Any", coordinator)
    obj.hass = hass
    return coordinator  # pyrefly: ignore [no-any-return-implicit]


def _point(start: datetime, value: float) -> SimpleNamespace:
    """Return a minimal app chart point exposing ``start_date`` and ``value``."""
    return SimpleNamespace(start_date=start, value=value)


async def _import(
    coordinator: JackerySolarVaultCoordinator,
    hass: HomeAssistant,
    points: list[SimpleNamespace],
) -> tuple[bool, int]:
    """Import a day-hourly series and block until the recorder has committed."""
    day = points[0].start_date.replace(hour=0)
    hours = [(day + timedelta(hours=hour)).timestamp() for hour in range(24)]
    count = await coordinator._async_reconcile_statistic_day(  # ruff: ignore[private-member-access]
        _STAT_ID,
        hours,
        {point.start_date.timestamp(): point.value for point in points},
    )
    await async_wait_recording_done(hass)
    return True, count


async def _read_rows(hass: HomeAssistant) -> list[dict[str, Any]]:
    """Return stored (start, state, sum) rows for the native sensor series."""
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


@pytest.fixture(autouse=True)
async def initial_native_history(recorder_mock: Recorder, hass: HomeAssistant) -> None:
    """Anchor corrections on an existing native sensor history, as in production."""
    del recorder_mock
    coordinator_module.async_import_statistics(
        hass,
        {
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
            "name": None,
            "source": "recorder",
            "statistic_id": _STAT_ID,
            "unit_class": "energy",
            "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
        },
        [{"start": _BASELINE, "sum": 0.0, "state": 0.0}],
    )
    await async_wait_recording_done(hass)


async def test_corrected_bucket_sum_is_updated_not_dropped(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """A corrected interval re-import updates the cumulative sum chain.

    Native rows carry both cumulative sums and daily-reset sensor states.
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
    assert _row_at(rows, hour11)["state"] == pytest.approx(6.0)
    assert _row_at(rows, hour11)["sum"] == pytest.approx(6.0)
    # The trailing bucket is rebased on the correction, keeping the sum monotonic.
    assert _row_at(rows, hour12)["sum"] == pytest.approx(9.0)
    _assert_monotonic(rows)


async def test_reconcile_waits_for_public_recorder_commit_before_reading(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """History reads wait for the public commit barrier before queuing repairs."""
    await hass.config.async_set_time_zone("UTC")
    coordinator = _coordinator(hass)
    recorder = get_instance(hass)
    assert recorder is recorder_mock
    waiting = asyncio.Event()
    release = asyncio.Event()
    events: list[str] = []
    commit = recorder.async_block_till_done
    import_statistics = coordinator_module.async_import_statistics

    async def commit_pending() -> None:
        events.append("commit")
        waiting.set()
        await release.wait()
        await commit()

    def enqueue(*args: Any, **kwargs: Any) -> None:
        events.append("import")
        import_statistics(*args, **kwargs)

    day = datetime(2026, 7, 1, tzinfo=UTC)
    hours = [(day + timedelta(hours=h)).timestamp() for h in range(24)]
    energy = {hours[18]: 4.5}
    with monkeypatch.context() as patch:
        patch.setattr(recorder, "async_block_till_done", commit_pending)
        patch.setattr(coordinator_module, "async_import_statistics", enqueue)
        task = asyncio.create_task(
            coordinator._async_reconcile_statistic_day(  # ruff: ignore[private-member-access]
                _STAT_ID, hours, energy
            )
        )
        try:
            await waiting.wait()
            assert not task.done()
            assert events == ["commit"]
        finally:
            release.set()
            first = await task

    assert first == _EXPECTED_FIRST_IMPORT_COUNT
    assert events == ["commit", "import"]
    await async_wait_recording_done(hass)
    assert _row_at(await _read_rows(hass), day + timedelta(hours=18))[
        "sum"
    ] == pytest.approx(4.5)
    duplicate = await coordinator._async_reconcile_statistic_day(  # ruff: ignore[private-member-access]
        _STAT_ID, hours, energy
    )
    assert duplicate == 0


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
    assert [
        _row_at(rows, hour)["sum"] for hour in (hour10, hour11, hour12)
    ] == pytest.approx([1.0, 5.0, 7.0])
    _assert_monotonic(rows)


async def test_identical_reimport_is_idempotent(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """Re-importing an unchanged series writes nothing after reading stored rows."""
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
    """A native day correction shifts later sums without changing their energy."""
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
    assert [
        _row_at(rows, hour)["sum"]
        for hour in (d0, d1, d1 + timedelta(hours=1), d2, d2 + timedelta(hours=1))
    ] == pytest.approx([
        100.0,
        110.0,
        112.0,
        116.0,
        121.0,
    ])
    _assert_monotonic(rows)


@pytest.mark.parametrize("failed_read", ["metadata", "day", "before", "after"])
async def test_recorder_read_failure_preserves_rows_and_allows_retry(
    recorder_mock: Recorder,
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    failed_read: str,
) -> None:
    """Failure at any native history read preserves rows and permits retry."""
    await hass.config.async_set_time_zone("UTC")
    coordinator = _coordinator(hass)
    start = datetime(2026, 7, 10, 10, tzinfo=UTC)
    await _import(coordinator, hass, [_point(start, 10.0)])
    await _import(coordinator, hass, [_point(start + timedelta(days=1), 2.0)])
    before = await _read_rows(hass)
    scope = coordinator_module.session_scope
    method_name = "first" if failed_read == "metadata" else "all"
    failure_index = {"metadata": 0, "day": 0, "before": 1, "after": 2}[failed_read]

    @contextmanager
    def failing_scope(*args: Any, **kwargs: Any) -> Any:
        with scope(*args, **kwargs) as session:
            query_class = type(session.query(StatisticsMeta))
            original_read = getattr(query_class, method_name)
            calls = 0

            def read(query_self: Any, *read_args: Any, **read_kwargs: Any) -> Any:
                nonlocal calls
                current = calls
                calls += 1
                if current == failure_index:
                    message = "recorder read unavailable"
                    raise RuntimeError(message)
                return original_read(query_self, *read_args, **read_kwargs)

            with monkeypatch.context() as reader_patch:
                reader_patch.setattr(query_class, method_name, read)
                yield session

    with monkeypatch.context() as patch:
        # The executor runs the actual metadata/day/before/after SQL reads.
        patch.setattr(coordinator_module, "session_scope", failing_scope)
        with pytest.raises(RuntimeError, match="recorder read unavailable"):
            await _import(coordinator, hass, [_point(start, 3.0)])

    assert await _read_rows(hass) == before
    assert (await _import(coordinator, hass, [_point(start, 3.0)]))[0]
    rows = await _read_rows(hass)
    assert _row_at(rows, start)["sum"] == pytest.approx(3.0)
    assert _row_at(rows, start + timedelta(days=1))["sum"] == pytest.approx(5.0)


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
    assert [
        _row_at(continued, hour)["sum"]
        for hour in (d1, d1 + timedelta(hours=1), d2, d2 + timedelta(hours=1))
    ] == pytest.approx(
        [100_001.0, 100_003.0, 100_007.0, 100_012.0],
    )
    _assert_monotonic(continued)
