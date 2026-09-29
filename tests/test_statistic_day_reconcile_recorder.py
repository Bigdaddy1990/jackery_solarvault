"""Closed-day reconcile of a daily energy sensor against a real recorder.

Covers the live failure mode of 2026-09-25: a stored hour of +11.86 kWh
inflated the PV day to 25.4 kWh while the cloud reported 14.1 kWh, and the
former gap fill left every stored hour untouched.
"""

from datetime import UTC, datetime, timedelta
import itertools
import operator
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
from homeassistant.components.recorder.db_schema import (
    StatisticsMeta,
    StatisticsShortTerm,
)
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import (
    _compile_hourly_statistics,  # ruff: ignore[import-private-name]
    statistics_during_period,
)
from homeassistant.components.recorder.util import session_scope
from homeassistant.const import UnitOfEnergy
from homeassistant.util.unit_conversion import EnergyConverter

if TYPE_CHECKING:
    from homeassistant.components.recorder import Recorder
    from homeassistant.core import HomeAssistant

_STAT_ID = "sensor.solarvault_pv_ertrag_heute"
_DAY = datetime(2026, 7, 1, tzinfo=UTC)
_NEXT = _DAY + timedelta(days=1)
_HOURS = [(_DAY + timedelta(hours=h)).timestamp() for h in range(24)]


@pytest.fixture()
def mock_recorder_before_hass(recorder_db_url: str) -> None:
    """Prepare the recorder database before Home Assistant starts."""
    del recorder_db_url


def _coordinator(hass: HomeAssistant) -> JackerySolarVaultCoordinator:
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    cast("Any", coordinator).hass = hass
    return coordinator  # pyrefly: ignore [no-any-return-implicit]


async def _seed_overcounted_day(hass: HomeAssistant) -> None:
    """Store the day as the live recorder held it: one +11.86 kWh hour."""
    rows = [
        {
            "start": _DAY - timedelta(hours=1),
            "sum": 100.0,
            "state": 9.0,
            "last_reset": _DAY - timedelta(days=1),
        },
    ]
    stored = [100.0] * 9 + [103.12, 114.98] + [117.0] * 13
    rows.extend(
        # pyrefly: ignore [bad-assignment]
        {
            "start": _DAY + timedelta(hours=hour),
            "sum": total,
            "state": None,
            "last_reset": _DAY,
        }
        for hour, total in enumerate(stored)
    )
    rows.append({"start": _NEXT, "sum": 117.5, "state": 0.5, "last_reset": _NEXT})
    coordinator_module.async_import_statistics(
        hass,
        {
            "mean_type": StatisticMeanType.NONE,
            "has_sum": True,
            "name": None,
            "source": "recorder",
            "statistic_id": _STAT_ID,
            "unit_class": EnergyConverter.UNIT_CLASS,
            "unit_of_measurement": UnitOfEnergy.KILO_WATT_HOUR,
        },
        cast("Any", rows),
    )
    await async_wait_recording_done(hass)
    await get_instance(hass).async_add_executor_job(
        _add_short_term, hass, _NEXT + timedelta(minutes=55), 117.5
    )


def _meta_id(session: Any) -> int:
    return cast(
        "int",
        session
        .query(StatisticsMeta.id)
        .filter(StatisticsMeta.statistic_id == _STAT_ID)
        .scalar(),
    )


def _add_short_term(hass: HomeAssistant, start: datetime, total: float) -> None:
    with session_scope(session=get_instance(hass).get_session()) as session:
        row = {"start": start, "sum": total}
        session.add(StatisticsShortTerm.from_stats(_meta_id(session), row))


def _last_short_term_sum(hass: HomeAssistant) -> float:
    with session_scope(session=get_instance(hass).get_session()) as session:
        return cast(
            "float",
            session
            .query(StatisticsShortTerm.sum)
            .filter(StatisticsShortTerm.metadata_id == _meta_id(session))
            .order_by(StatisticsShortTerm.start_ts.desc())
            .limit(1)
            .scalar(),
        )


def _compile(hass: HomeAssistant, hour: datetime) -> None:
    with session_scope(session=get_instance(hass).get_session()) as session:
        _compile_hourly_statistics(session, hour)


async def _sums(hass: HomeAssistant) -> dict[datetime, float]:
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        _DAY - timedelta(hours=1),
        _NEXT + timedelta(hours=3),
        {_STAT_ID},
        "hour",
        None,
        {"sum"},
    )
    series = sorted(
        cast("list[dict[str, Any]]", rows.get(_STAT_ID, [])),
        key=operator.itemgetter("start"),
    )
    return {datetime.fromtimestamp(row["start"], UTC): row["sum"] for row in series}


async def test_overcounted_day_is_corrected_and_next_compile_stays_continuous(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """The day equals the curve; later hourly and 5-minute rows shift together."""
    del recorder_mock
    await hass.config.async_set_time_zone("UTC")
    await _seed_overcounted_day(hass)
    curve = {_HOURS[h]: 1.0 for h in range(9, 15)}

    written = await _coordinator(hass)._async_reconcile_statistic_day(  # ruff: ignore[private-member-access]
        _STAT_ID, _HOURS, curve
    )
    await async_wait_recording_done(hass)
    short_term = await get_instance(hass).async_add_executor_job(
        _last_short_term_sum, hass
    )
    await get_instance(hass).async_add_executor_job(
        _add_short_term, hass, _NEXT + timedelta(hours=1, minutes=55), short_term + 0.5
    )
    await get_instance(hass).async_add_executor_job(
        _compile, hass, _NEXT + timedelta(hours=1)
    )
    await async_wait_recording_done(hass)
    sums = await _sums(hass)
    ordered = [sums[key] for key in sorted(sums)]
    steps = [b - a for a, b in itertools.pairwise(ordered)]

    assert written > 0
    assert sums[_NEXT - timedelta(hours=1)] - sums[_DAY - timedelta(hours=1)] == (
        pytest.approx(6.0)
    )
    assert sums[_NEXT] == pytest.approx(106.5)
    assert short_term == pytest.approx(106.5)
    assert sums[_NEXT + timedelta(hours=1)] == pytest.approx(107.0)
    assert all(step >= 0 for step in steps)


async def test_consecutive_days_read_the_previous_days_committed_shift(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """The next day must build on the corrected sums, not on queued writes."""
    del recorder_mock
    await hass.config.async_set_time_zone("UTC")
    await _seed_overcounted_day(hass)
    coordinator = _coordinator(hass)
    next_hours = [(_NEXT + timedelta(hours=h)).timestamp() for h in range(24)]

    await coordinator._async_reconcile_statistic_day(  # ruff: ignore[private-member-access]
        _STAT_ID, _HOURS, {_HOURS[h]: 1.0 for h in range(9, 15)}
    )
    await coordinator._async_reconcile_statistic_day(  # ruff: ignore[private-member-access]
        _STAT_ID, next_hours, {next_hours[0]: 0.5}
    )
    await async_wait_recording_done(hass)
    sums = await _sums(hass)

    assert sums[_NEXT] == pytest.approx(106.5)
    assert sums[_NEXT + timedelta(hours=2)] == pytest.approx(106.5)


async def test_second_run_changes_nothing(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """A reconciled day is left alone, so the backfill queue can safely re-run."""
    del recorder_mock
    await hass.config.async_set_time_zone("UTC")
    await _seed_overcounted_day(hass)
    coordinator = _coordinator(hass)
    curve = {_HOURS[h]: 1.0 for h in range(9, 15)}

    await coordinator._async_reconcile_statistic_day(_STAT_ID, _HOURS, curve)  # ruff: ignore[private-member-access]
    await async_wait_recording_done(hass)
    before = await _sums(hass)
    second = await coordinator._async_reconcile_statistic_day(_STAT_ID, _HOURS, curve)  # ruff: ignore[private-member-access]
    await async_wait_recording_done(hass)

    assert second == 0
    assert await _sums(hass) == before
