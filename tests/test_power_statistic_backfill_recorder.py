"""Backfilled days also fill the power sensors' hourly means.

Live 2026-09-28: the Energy Dashboard power graph ("Stromquellen") showed
values only while Home Assistant was connected; the cloud day curves are
5-minute power curves, but only energy statistics were backfilled.
"""

from datetime import UTC, datetime, timedelta
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
from homeassistant.components.recorder.models import StatisticMeanType
from homeassistant.components.recorder.statistics import statistics_during_period
from homeassistant.const import UnitOfPower
from homeassistant.util.unit_conversion import PowerConverter

if TYPE_CHECKING:
    from homeassistant.components.recorder import Recorder
    from homeassistant.core import HomeAssistant

_STAT_ID = "sensor.solarvault_pv_leistung_gesamt"
_DAY = datetime(2026, 7, 1, tzinfo=UTC)


@pytest.fixture()
def mock_recorder_before_hass(recorder_db_url: str) -> None:
    """Prepare the recorder database before Home Assistant starts."""
    del recorder_db_url


def _coordinator(hass: HomeAssistant) -> Any:
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    cast("Any", coordinator).hass = hass
    return coordinator


async def _seed_live_hour(hass: HomeAssistant, unit: str) -> None:
    """One hour Home Assistant recorded live: 09:00 at 500 W."""
    coordinator_module.async_import_statistics(
        hass,
        {
            "mean_type": StatisticMeanType.ARITHMETIC,
            "has_sum": False,
            "name": None,
            "source": "recorder",
            "statistic_id": _STAT_ID,
            "unit_class": PowerConverter.UNIT_CLASS,
            "unit_of_measurement": unit,
        },
        cast(
            "Any",
            [
                {
                    "start": _DAY + timedelta(hours=9),
                    "mean": 500.0,
                    "min": 400.0,
                    "max": 600.0,
                }
            ],
        ),
    )
    await async_wait_recording_done(hass)


async def _means(hass: HomeAssistant) -> dict[int, float]:
    rows = await get_instance(hass).async_add_executor_job(
        statistics_during_period,
        hass,
        _DAY,
        _DAY + timedelta(days=1),
        {_STAT_ID},
        "hour",
        None,
        {"mean"},
    )
    series = sorted(
        cast("list[dict[str, Any]]", rows.get(_STAT_ID, [])),
        key=operator.itemgetter("start"),
    )
    return {
        datetime.fromtimestamp(row["start"], UTC).hour: row["mean"] for row in series
    }


def _curve() -> dict[float, float]:
    """Mean W of 08:00-11:00 derived from the App day curve."""
    return {
        (_DAY + timedelta(hours=hour)).timestamp(): watts
        for hour, watts in ((8, 120.0), (9, 480.0), (10, 910.0), (11, 1330.0))
    }


async def test_missing_hours_are_filled_and_recorded_hours_kept(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """Hours without statistics get the curve mean; 09:00 stays the live row."""
    del recorder_mock
    await _seed_live_hour(hass, UnitOfPower.WATT)

    written = await _coordinator(hass)._async_fill_power_statistic_day(  # ruff: ignore[private-member-access]
        _STAT_ID, _curve()
    )
    await async_wait_recording_done(hass)

    assert written == 3  # ruff: ignore[magic-value-comparison]
    assert await _means(hass) == {
        8: pytest.approx(120.0),
        9: pytest.approx(500.0),
        10: pytest.approx(910.0),
        11: pytest.approx(1330.0),
    }


async def test_power_statistic_in_another_unit_is_left_alone(
    recorder_mock: Recorder,
    hass: HomeAssistant,
) -> None:
    """Never write W means into a statistic kept in kW."""
    del recorder_mock
    await _seed_live_hour(hass, UnitOfPower.KILO_WATT)

    written = await _coordinator(hass)._async_fill_power_statistic_day(  # ruff: ignore[private-member-access]
        _STAT_ID, _curve()
    )
    await async_wait_recording_done(hass)

    assert written == 0
    assert await _means(hass) == {9: pytest.approx(500.0)}
