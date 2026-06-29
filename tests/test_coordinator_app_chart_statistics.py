"""Period-hierarchy gating during historical app-chart statistics repair.

These tests lock down AGENTS.md §2.2 enforcement on the *backfill* path
(:meth:`JackerySolarVaultCoordinator._async_repair_missing_app_chart_statistics`),
the historical sibling of the already-gated current-snapshot path
(``_gate_snapshot_period_hierarchy``).

The contract under test:

* A fetched repair bucket whose shorter-period total exceeds its
  longer-period container (e.g. a current month total greater than the
  current year total) is a §2.2 violation and must be withheld from the HA
  Recorder, with a warning naming the dropped section.
* A fetched bucket that is consistent with its container is imported once.
* A historical shorter-period bucket is validated against its *own*
  longer-period container (the year/month it actually falls inside), not
  against the current snapshot. A historical month exceeding its containing
  year is withheld; a historical month within its containing year is
  imported; and a legitimate prior-period week with a populated containing
  year is NOT over-blocked.
* Multi-year backfill validates each bucket against the container computed
  from the bucket's own ``period_start`` year, never the current snapshot.

Only the recorder boundary and the historical fetch seam are mocked; the
period-hierarchy detection runs against the real
:func:`app_data_quality_warnings` validator.
"""

# ruff: noqa: PLC0415, SLF001

from collections.abc import Callable, Coroutine, Iterable, Mapping
from datetime import date, datetime
import logging
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest

from custom_components.jackery_solarvault.const import (
    APP_SECTION_PV_STAT,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    DOMAIN,
    FIELD_SYSTEM_ID,
)
from custom_components.jackery_solarvault.util import external_trend_statistic_id
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

if TYPE_CHECKING:
    from unittest.mock import MagicMock, _Call

    from custom_components.jackery_solarvault.coordinator import (
        JackerySolarVaultCoordinator,
    )
    from homeassistant.core import HomeAssistant

pytestmark = pytest.mark.asyncio

_DEVICE_ID = "DEV-PERIOD-GATE-1"
_SYSTEM_ID = "595364183558991872"
_TO_DATE = date(2026, 5, 15)

# Section payload type and the async fetch seam signature.
type _Section = dict[str, object]
type _FetchKey = tuple[str, str, date]
type _FetchStub = Callable[..., Coroutine[Any, Any, _Section]]

# External statistic ids the PV buckets would import under.
_PV_MONTH_STAT_ID = external_trend_statistic_id(
    DOMAIN,
    _DEVICE_ID,
    "pv_energy",
    "month_daily",
)
_PV_WEEK_STAT_ID = external_trend_statistic_id(
    DOMAIN,
    _DEVICE_ID,
    "pv_energy",
    "week_daily",
)
_PV_YEAR_STAT_ID = external_trend_statistic_id(
    DOMAIN,
    _DEVICE_ID,
    "pv_energy",
    "year_monthly",
)


def _pv_year_section(total_kwh: float, *, year: int = 2026) -> _Section:
    """Build a PV year section whose monthly chart series sums to total_kwh."""
    months = [0.0] * 12
    months[0] = total_kwh
    return {
        "unit": "kWh",
        "_request": {
            "dateType": "year",
            "beginDate": f"{year:04d}-01-01",
            "endDate": f"{year:04d}-12-31",
        },
        "x": [str(i) for i in range(1, 13)],
        "y": months,
    }


def _pv_month_section(
    total_kwh: float,
    *,
    begin: str = "2026-05-01",
    end: str = "2026-05-31",
) -> _Section:
    """Build a PV month section whose daily chart series sums to total_kwh."""
    days = [total_kwh] + [0.0] * 30
    return {
        "unit": "kWh",
        "_request": {
            "dateType": "month",
            "beginDate": begin,
            "endDate": end,
        },
        "x": list(range(1, 32)),
        "y": days,
    }


def _pv_week_section(total_kwh: float, begin: str, end: str) -> _Section:
    """Build a PV week section whose daily chart series sums to total_kwh."""
    days = [total_kwh] + [0.0] * 6
    return {
        "unit": "kWh",
        "_request": {
            "dateType": "week",
            "beginDate": begin,
            "endDate": end,
        },
        "x": [str(i) for i in range(1, 8)],
        "y": days,
    }


async def _build_coordinator(
    hass: HomeAssistant,
) -> JackerySolarVaultCoordinator:
    """Set up a real config entry and return its live coordinator."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com",
        data={CONF_USERNAME: "user@example.com", CONF_PASSWORD: "pass"},
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator._async_update_data",
            return_value={},
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator._async_ensure_mqtt",
            return_value=None,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    coordinator._device_index[_DEVICE_ID] = {FIELD_SYSTEM_ID: _SYSTEM_ID}
    # Freeze "today" so calendar-period boundaries are deterministic.
    coordinator._local_today = lambda: _TO_DATE  # type: ignore[method-assign]
    return coordinator


def _fetch_stub(sections_by_key: dict[_FetchKey, _Section]) -> _FetchStub:
    """Return an async fetch replacement keyed by (prefix, date_type, start)."""

    async def _fetch(  # noqa: PLR0913, RUF029
        *,
        device_id: str,
        system_id: str | None,
        section_prefix: str,
        date_type: str,
        period_start: date,
        ct_device_id: str | None = None,
    ) -> _Section:
        return sections_by_key.get(
            (section_prefix, date_type, period_start),
            {},
        )

    return _fetch


# Positional indices of ``async_add_external_statistics(hass, metadata, stats)``.
_METADATA_ARG_INDEX = 1
_STATISTICS_ARG_INDEX = 2


def _call_metadata(call: _Call) -> object:
    """Return the metadata argument from one recorder import call."""
    if len(call.args) > _METADATA_ARG_INDEX:
        return call.args[_METADATA_ARG_INDEX]
    return call.kwargs.get("metadata")


def _call_statistics(call: _Call) -> object:
    """Return the statistics argument from one recorder import call."""
    if len(call.args) > _STATISTICS_ARG_INDEX:
        return call.args[_STATISTICS_ARG_INDEX]
    return call.kwargs.get("statistics")


def _metadata_statistic_id(metadata: object) -> str | None:
    """Return the statistic id from a metadata mapping or object."""
    if isinstance(metadata, Mapping):
        statistic_id = metadata.get("statistic_id")
        return statistic_id if isinstance(statistic_id, str) else None
    statistic_id = getattr(metadata, "statistic_id", None)
    return statistic_id if isinstance(statistic_id, str) else None


def _row_start_date(row: object) -> date | None:
    """Return the calendar date of one StatisticData row's start."""
    if isinstance(row, Mapping):
        start = row.get("start")
    else:
        start = getattr(row, "start", None)
    if isinstance(start, datetime):
        return start.date()
    return start if isinstance(start, date) else None


def _imported_statistic_ids(add_mock: MagicMock) -> set[str | None]:
    """Return statistic ids the recorder import was called with."""
    return {
        _metadata_statistic_id(_call_metadata(call)) for call in add_mock.call_args_list
    }


def _imported_start_dates(add_mock: MagicMock, statistic_id: str) -> set[date]:
    """Return the StatisticData.start calendar dates for one statistic id."""
    starts: set[date] = set()
    for call in add_mock.call_args_list:
        if _metadata_statistic_id(_call_metadata(call)) != statistic_id:
            continue
        statistics = _call_statistics(call)
        if not isinstance(statistics, Iterable):
            continue
        for row in statistics:
            row_start = _row_start_date(row)
            if row_start is not None:
                starts.add(row_start)
    return starts


async def _run_repair(
    coordinator: JackerySolarVaultCoordinator,
    *,
    payload: _Section,
    from_date: date,
    fetch_sections: dict[_FetchKey, _Section],
) -> MagicMock:
    """Invoke the repair with mocked recorder + fetch seams; return add mock."""
    with (
        patch(
            "homeassistant.components.recorder.statistics."
            "async_add_external_statistics",
        ) as add_mock,
        patch(
            "homeassistant.components.recorder.get_instance",
            side_effect=RuntimeError("recorder disabled in test"),
        ),
        patch(
            "homeassistant.components.recorder.statistics.statistics_during_period",
            return_value={},
        ),
        patch.object(
            coordinator,
            "_async_fetch_historical_app_chart_source",
            new=_fetch_stub(fetch_sections),
        ),
    ):
        await coordinator._async_repair_missing_app_chart_statistics(
            _DEVICE_ID,
            payload,
            from_date,
            _TO_DATE,
        )
    return add_mock


async def test_inverted_current_month_bucket_is_withheld_with_warning(
    hass: HomeAssistant,
    mock_jackery_login: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A current month total exceeding the year total is dropped, never imported."""
    coordinator = await _build_coordinator(hass)
    payload = {f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}": _pv_year_section(50.0)}
    fetch_sections = {
        (APP_SECTION_PV_STAT, DATE_TYPE_MONTH, date(2026, 5, 1)): _pv_month_section(
            80.0
        ),
    }

    with caplog.at_level(logging.WARNING):
        add_mock = await _run_repair(
            coordinator,
            payload=payload,
            from_date=_TO_DATE,
            fetch_sections=fetch_sections,
        )

    assert _PV_MONTH_STAT_ID not in _imported_statistic_ids(add_mock)
    combined_log = caplog.text.lower()
    assert "device_pv_stat_month" in combined_log
    assert "period hierarchy" in combined_log


async def test_consistent_current_month_bucket_is_imported(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """A current month total within the year total is imported exactly once."""
    coordinator = await _build_coordinator(hass)
    payload = {f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}": _pv_year_section(50.0)}
    fetch_sections = {
        (APP_SECTION_PV_STAT, DATE_TYPE_MONTH, date(2026, 5, 1)): _pv_month_section(
            30.0
        ),
    }

    add_mock = await _run_repair(
        coordinator,
        payload=payload,
        from_date=_TO_DATE,
        fetch_sections=fetch_sections,
    )

    imported = [
        statistic_id
        for statistic_id in _imported_statistic_ids(add_mock)
        if statistic_id == _PV_MONTH_STAT_ID
    ]
    assert imported == [_PV_MONTH_STAT_ID]


async def test_historical_month_exceeding_its_year_is_withheld_with_warning(
    hass: HomeAssistant,
    mock_jackery_login: None,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A fetched historical month above its *own* fetched year is withheld.

    The month (2024-03, 80 kWh) exceeds its containing year (2024, 50 kWh).
    The v1 gate only ever held the current-period snapshot, so this
    cross-period historical inversion reached the recorder. The collect →
    validate-containment → import restructure must drop it and warn.
    """
    coordinator = await _build_coordinator(hass)
    payload: _Section = {}
    fetch_sections = {
        (APP_SECTION_PV_STAT, DATE_TYPE_YEAR, date(2024, 1, 1)): _pv_year_section(
            50.0, year=2024
        ),
        (APP_SECTION_PV_STAT, DATE_TYPE_MONTH, date(2024, 3, 1)): _pv_month_section(
            80.0,
            begin="2024-03-01",
            end="2024-03-31",
        ),
    }

    with caplog.at_level(logging.WARNING):
        add_mock = await _run_repair(
            coordinator,
            payload=payload,
            from_date=date(2024, 3, 1),
            fetch_sections=fetch_sections,
        )

    march_2024 = date(2024, 3, 1)
    assert march_2024 not in _imported_start_dates(add_mock, _PV_MONTH_STAT_ID)
    combined_log = caplog.text.lower()
    assert "device_pv_stat_month" in combined_log
    assert "period hierarchy" in combined_log


async def test_consistent_historical_month_and_year_are_imported(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """A historical month within its own year imports both buckets."""
    coordinator = await _build_coordinator(hass)
    payload: _Section = {}
    fetch_sections = {
        (APP_SECTION_PV_STAT, DATE_TYPE_YEAR, date(2024, 1, 1)): _pv_year_section(
            50.0, year=2024
        ),
        (APP_SECTION_PV_STAT, DATE_TYPE_MONTH, date(2024, 3, 1)): _pv_month_section(
            30.0,
            begin="2024-03-01",
            end="2024-03-31",
        ),
    }

    add_mock = await _run_repair(
        coordinator,
        payload=payload,
        from_date=date(2024, 3, 1),
        fetch_sections=fetch_sections,
    )

    imported_ids = _imported_statistic_ids(add_mock)
    assert _PV_MONTH_STAT_ID in imported_ids
    assert _PV_YEAR_STAT_ID in imported_ids
    assert date(2024, 3, 1) in _imported_start_dates(add_mock, _PV_MONTH_STAT_ID)


async def test_prior_week_with_populated_container_is_not_over_blocked(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """A prior-period week within its fetched year container is imported.

    The week (2026-04-06..12, 90 kWh) sits inside its containing year
    (2026, 200 kWh), so it is a legitimate backfill bucket. The fix must not
    over-block it: an absent month container is not a licence to withhold.
    """
    coordinator = await _build_coordinator(hass)
    payload: _Section = {}
    prior_week_start = date(2026, 4, 6)
    fetch_sections = {
        (APP_SECTION_PV_STAT, DATE_TYPE_YEAR, date(2026, 1, 1)): _pv_year_section(
            200.0
        ),
        (APP_SECTION_PV_STAT, DATE_TYPE_WEEK, prior_week_start): _pv_week_section(
            90.0,
            "2026-04-06",
            "2026-04-12",
        ),
    }

    add_mock = await _run_repair(
        coordinator,
        payload=payload,
        from_date=prior_week_start,
        fetch_sections=fetch_sections,
    )

    assert _PV_WEEK_STAT_ID in _imported_statistic_ids(add_mock)


async def test_multi_year_backfill_uses_each_buckets_own_year_container(
    hass: HomeAssistant,
    mock_jackery_login: None,
) -> None:
    """Each month bucket validates against its own calendar year, not today.

    Two month buckets share the ``month_daily`` statistic id. The 2024 month
    (60 kWh) exceeds its 2024 year (40 kWh) and must be withheld, while the
    2026 month (60 kWh) is within its 2026 year (500 kWh) and must import.
    They are distinguished by the StatisticData.start dates: the withheld
    2024 June start must be absent, the imported 2026 May start present.
    """
    coordinator = await _build_coordinator(hass)
    payload: _Section = {}
    fetch_sections = {
        (APP_SECTION_PV_STAT, DATE_TYPE_YEAR, date(2024, 1, 1)): _pv_year_section(
            40.0, year=2024
        ),
        (APP_SECTION_PV_STAT, DATE_TYPE_MONTH, date(2024, 6, 1)): _pv_month_section(
            60.0,
            begin="2024-06-01",
            end="2024-06-30",
        ),
        (APP_SECTION_PV_STAT, DATE_TYPE_YEAR, date(2026, 1, 1)): _pv_year_section(
            500.0
        ),
        (APP_SECTION_PV_STAT, DATE_TYPE_MONTH, date(2026, 5, 1)): _pv_month_section(
            60.0,
            begin="2026-05-01",
            end="2026-05-31",
        ),
    }

    add_mock = await _run_repair(
        coordinator,
        payload=payload,
        from_date=date(2024, 1, 1),
        fetch_sections=fetch_sections,
    )

    month_starts = _imported_start_dates(add_mock, _PV_MONTH_STAT_ID)
    assert date(2024, 6, 1) not in month_starts
    assert date(2026, 5, 1) in month_starts
