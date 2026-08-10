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

from datetime import UTC, datetime, timedelta
import itertools
import operator
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

import pytest
from pytest_homeassistant_custom_component.components.recorder.common import (
    async_wait_recording_done,
)

from custom_components.jackery_solarvault.const import (
    DOMAIN,
    EXTERNAL_STAT_BUCKET_DAY_HOURLY,
)
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
    return sorted(series, key=operator.itemgetter("start"))


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


async def test_corrected_bucket_state_and_sum_are_updated_not_dropped(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """FINDING 6: a re-imported bucket with a corrected state updates the row.

    The pre-fix loop skipped an existing bucket whose state differed, so the
    correction was permanently dropped. The row must instead carry the new
    state and a rebased sum.
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
    assert _row_at(rows, hour11)["state"] == pytest.approx(5.0)
    assert _row_at(rows, hour11)["sum"] == pytest.approx(6.0)
    # The trailing bucket is rebased on the correction, keeping the sum monotonic.
    assert _row_at(rows, hour12)["sum"] == pytest.approx(9.0)
    _assert_monotonic(rows)


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
