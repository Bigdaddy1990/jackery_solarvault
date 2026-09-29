"""CT week/month/year come from the lifetime counter's Recorder reading.

Live 2026-09-28: the Shelly cloud meter returns ``{}`` for every
device/stat/ct period, so month and year stayed unknown. sensor.py already
asked the coordinator for ``local_period_energy_kwh`` — the method never
existed, so the local period path was dead.
"""

from datetime import date, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault import coordinator as coordinator_module
from custom_components.jackery_solarvault.const import (
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_YEAR,
    DOMAIN,
    FIELD_CT_TOTAL_PHASE_ENERGY,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from homeassistant.helpers import entity_registry as er

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_DEVICE = "dev"
_ENTITY = "sensor.smart_meter_netzbezug_gesamt"
_TODAY = date(2026, 9, 28)


def _coordinator(
    hass: HomeAssistant, rows: list[dict[str, Any]]
) -> tuple[Any, AsyncMock]:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "sensor",
        DOMAIN,
        f"{_DEVICE}_smart_meter_lifetime_import_energy",
        config_entry=entry,
        suggested_object_id="smart_meter_netzbezug_gesamt",
    )
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    obj = cast("Any", coordinator)
    obj.hass = hass
    obj.data = {}
    obj._listeners = {}  # ruff: ignore[private-member-access]
    obj._ct_period_baselines = {}  # ruff: ignore[private-member-access]
    obj._ct_period_baseline_loading = set()  # ruff: ignore[private-member-access]
    obj.local_daily_energy_kwh = lambda _device, _metric: 2.32
    return coordinator, AsyncMock(return_value={_ENTITY: rows})


async def _load(
    hass: HomeAssistant, coordinator: Any, executor: AsyncMock, period: str
) -> float | None:
    with patch.object(
        coordinator_module,
        "get_instance",
        return_value=SimpleNamespace(async_add_executor_job=executor),
    ):
        # The first read schedules the Recorder query and reports nothing yet.
        assert (
            coordinator.local_period_energy_kwh(
                _DEVICE, FIELD_CT_TOTAL_PHASE_ENERGY, period=period, today=_TODAY
            )
            is None
        )
        await hass.async_block_till_done()
    # pyrefly: ignore [no-any-return-implicit]
    return coordinator.local_period_energy_kwh(
        _DEVICE, FIELD_CT_TOTAL_PHASE_ENERGY, period=period, today=_TODAY
    )


@pytest.mark.asyncio()
async def test_month_is_lifetime_now_minus_reading_at_month_start(
    hass: HomeAssistant,
) -> None:
    """September: 1381.3 kWh now minus 1168.46 kWh at 1 September 00:00."""
    coordinator, executor = _coordinator(hass, [{"state": 1167.9}, {"state": 1168.46}])
    hass.states.async_set(_ENTITY, "1381.3")

    value = await _load(hass, coordinator, executor, DATE_TYPE_MONTH)

    assert value == pytest.approx(212.84)
    # pyrefly: ignore [missing-attribute]
    function, _hass, start, end, ids, period, _units, types = executor.await_args.args
    assert function is coordinator_module.statistics_during_period
    assert (end.year, end.month, end.day, end.hour) == (2026, 9, 1, 0)
    assert isinstance(start, datetime)
    assert (end - start).days == 1
    assert ids == {_ENTITY}
    assert period == "hour"
    assert types == {"state"}


@pytest.mark.asyncio()
async def test_year_without_an_earlier_reading_is_the_lifetime_total(
    hass: HomeAssistant,
) -> None:
    """No reading before 1 January: the counter began this year (owner rule)."""
    coordinator, executor = _coordinator(hass, [])
    hass.states.async_set(_ENTITY, "1381.3")

    assert await _load(hass, coordinator, executor, DATE_TYPE_YEAR) == pytest.approx(
        1381.3
    )
    _function, _hass, _start, end, _ids, period, _units, _types = (
        # pyrefly: ignore [missing-attribute]
        executor.await_args.args
    )
    assert (end.year, end.month, end.day) == (2026, 1, 1)
    assert period == "month"


@pytest.mark.asyncio()
async def test_year_counts_from_the_last_reading_before_january(
    hass: HomeAssistant,
) -> None:
    """A counter running since last year starts the year at its December state."""
    coordinator, executor = _coordinator(hass, [{"state": 800.0}, {"state": 950.5}])
    hass.states.async_set(_ENTITY, "1381.3")

    assert await _load(hass, coordinator, executor, DATE_TYPE_YEAR) == pytest.approx(
        430.8
    )


@pytest.mark.asyncio()
async def test_month_without_a_reading_at_its_start_stays_unknown(
    hass: HomeAssistant,
) -> None:
    """HA down at the month boundary: no invented month total."""
    coordinator, executor = _coordinator(hass, [])
    hass.states.async_set(_ENTITY, "1381.3")

    assert await _load(hass, coordinator, executor, DATE_TYPE_MONTH) is None


@pytest.mark.asyncio()
async def test_counter_reset_below_the_reading_is_not_a_negative_period(
    hass: HomeAssistant,
) -> None:
    """A replaced meter restarts at zero; never report a negative month."""
    coordinator, executor = _coordinator(hass, [{"state": 1168.46}])
    hass.states.async_set(_ENTITY, "3.2")

    assert await _load(hass, coordinator, executor, DATE_TYPE_MONTH) is None


def test_day_and_non_ct_metrics_keep_their_existing_sources(
    hass: HomeAssistant,
) -> None:
    """Day stays the local midnight delta; other metrics get no period value."""
    coordinator, _executor = _coordinator(hass, [])

    assert coordinator.local_period_energy_kwh(
        _DEVICE, FIELD_CT_TOTAL_PHASE_ENERGY, period=DATE_TYPE_DAY, today=_TODAY
    ) == pytest.approx(2.32)
    assert (
        coordinator.local_period_energy_kwh(
            _DEVICE, "pvEgy", period=DATE_TYPE_MONTH, today=_TODAY
        )
        is None
    )
