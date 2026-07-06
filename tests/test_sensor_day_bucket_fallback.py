"""Regression tests for the day-period chart-bucket fallback (B4 2026-07-03).

The Jackery cloud returns ``{"code": 0, "data": null}`` for every
``dateType=day`` stat endpoint at night, so the coordinator stores an
empty day section that only carries request metadata. The day-period
sensors (``device_today_pv_energy``, ``device_pv1_day_energy``, ...)
must then derive today's value from the month/week chart bucket —
exactly like the non-period day sensors already do — instead of going
``unknown`` until the first non-empty day payload of the morning.

Only the ``JackeryApi`` network boundary is mocked; setup, coordinator
dispatch, entity discovery and state writes run unmodified.
"""

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault.const import (
    APP_CHART_SERIES_Y,
    APP_REQUEST_BEGIN_DATE,
    APP_REQUEST_END_DATE,
    APP_REQUEST_META,
    APP_SECTION_PV_STAT,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DOMAIN,
    FIELD_DEVICE_SN,
    PAYLOAD_DEVICE,
    PAYLOAD_DISCOVERY,
    PAYLOAD_PROPERTIES,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.util import dt as dt_util

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from datetime import date

    from homeassistant.core import HomeAssistant

_DEVICE_ID = "dev-home-1"
_DEVICE_SN = "SN-HOME-0001"
_TODAY_BUCKET_KWH = 3.4
_DAY_SECTION = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_DAY}"
_MONTH_SECTION = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_MONTH}"


def _make_api_stub() -> MagicMock:
    """Build a ``JackeryApi`` stub that mocks only the network boundary.

    Returns:
        MagicMock: Stub exposing the coroutine surface the coordinator
        touches during setup, with no real IO.
    """
    api = MagicMock(name="JackeryApi")
    api.async_login = AsyncMock(return_value=None)
    api.async_get_mqtt_credentials = AsyncMock(return_value={"user_id": "user-1"})
    api.async_get_system_list = AsyncMock(return_value=[])
    api.async_list_devices_legacy = AsyncMock(return_value=[])
    api.mqtt_session_snapshot = MagicMock(return_value=None)
    api.hydrate_mqtt_session = MagicMock(return_value=None)
    api.async_close = AsyncMock(return_value=None)
    api.payload_debug_callback = None
    api.auth_rejection_callback = None
    return api


def _local_today(hass: HomeAssistant) -> date:
    """Return today's date in the configured HA timezone (sensor clock)."""
    timezone = dt_util.get_time_zone(hass.config.time_zone)
    return dt_util.now(timezone or dt_util.DEFAULT_TIME_ZONE).date()


def _night_payload(hass: HomeAssistant) -> dict[str, dict[str, Any]]:
    """Build the nightly coordinator snapshot reproducing B4.

    The day stat section carries only its request metadata (the cloud
    answered ``data: null``), while the month chart contains a real
    bucket for today. The month series intentionally holds non-zero
    values on other days so a "sum instead of bucket" regression would
    surface as a wrong state rather than a false pass.

    Returns:
        dict[str, dict[str, Any]]: ``coordinator.data`` mapping.
    """
    today = _local_today(hass)
    month_begin = today.replace(day=1)
    index = (today - month_begin).days
    series: list[float] = [1.0] * index + [_TODAY_BUCKET_KWH]
    return {
        _DEVICE_ID: {
            PAYLOAD_DEVICE: {FIELD_DEVICE_SN: _DEVICE_SN},
            PAYLOAD_DISCOVERY: {FIELD_DEVICE_SN: _DEVICE_SN},
            PAYLOAD_PROPERTIES: {"soc": 55},
            _DAY_SECTION: {
                APP_REQUEST_META: {
                    APP_REQUEST_BEGIN_DATE: today.isoformat(),
                    APP_REQUEST_END_DATE: today.isoformat(),
                },
            },
            _MONTH_SECTION: {
                APP_CHART_SERIES_Y: series,
                APP_REQUEST_META: {
                    APP_REQUEST_BEGIN_DATE: month_begin.isoformat(),
                    APP_REQUEST_END_DATE: today.isoformat(),
                },
            },
        },
    }


@pytest.fixture()
async def night_setup(
    hass: HomeAssistant,
) -> AsyncGenerator[MockConfigEntry]:
    """Set up the integration with the nightly B4 payload snapshot.

    Yields:
        MockConfigEntry: The configured entry after entity discovery.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_USERNAME: "tester@example.com", CONF_PASSWORD: "secret"},
        title="Jackery Home",
        entry_id="home-entry",
    )
    entry.add_to_hass(hass)

    api = _make_api_stub()
    with (
        patch(
            "custom_components.jackery_solarvault.JackeryApi",
            return_value=api,
        ),
        patch(
            "custom_components.jackery_solarvault._async_finish_entry_startup",
            AsyncMock(return_value=None),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = entry.runtime_data
    coordinator.async_set_updated_data(_night_payload(hass))
    await hass.async_block_till_done()

    yield entry

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


def _entity_id_for(hass: HomeAssistant, key: str) -> str:
    """Resolve the registered sensor entity id for a stat description key.

    Returns:
        str: The concrete ``sensor.*`` entity id registered in HA.
    """
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    registry = er.async_get(hass)
    unique_id = f"{_DEVICE_ID}_{key}"
    entity_id = registry.async_get_entity_id("sensor", DOMAIN, unique_id)
    assert entity_id is not None, (
        f"sensor entity for unique_id {unique_id!r} was not registered"
    )
    return entity_id


async def test_day_period_sensor_uses_month_chart_bucket_when_day_data_is_null(
    hass: HomeAssistant,
    night_setup: MockConfigEntry,
) -> None:
    """A day-period stat sensor reads today's month-chart bucket at night.

    Given the cloud answered ``data: null`` for the day endpoint and the
    month chart carries today's bucket, the sensor state must equal the
    bucket value (not the month sum, not ``unknown``).
    """
    await hass.async_block_till_done()
    entity_id = _entity_id_for(hass, "device_today_pv_energy")
    state = hass.states.get(entity_id)

    assert state is not None
    assert state.state == str(_TODAY_BUCKET_KWH)
    assert state.attributes["fallback"] == (f"current_day_bucket_from_{_MONTH_SECTION}")


async def test_day_period_sensor_prefers_scalar_total_over_power_curve_sum(
    hass: HomeAssistant,
    night_setup: MockConfigEntry,
) -> None:
    """A populated day payload reports the scalar total, never the curve sum.

    Live finding 2026-07-03: the day payload's ``y`` series is the
    intraday POWER curve (5-min samples, unit "w"). Summing it produced
    47493 "kWh" while the server's ``totalSolarEnergy`` scalar said
    3.58. The scalar is the authoritative day total.
    """
    entry = night_setup
    coordinator = entry.runtime_data
    payload = _night_payload(hass)
    today = _local_today(hass)
    payload[_DEVICE_ID][_DAY_SECTION] = {
        APP_CHART_SERIES_Y: [500.0, 1500.0, 1000.0],
        "totalSolarEnergy": 3.58,
        "unit": "w",
        APP_REQUEST_META: {
            APP_REQUEST_BEGIN_DATE: today.isoformat(),
            APP_REQUEST_END_DATE: today.isoformat(),
        },
    }
    coordinator.async_set_updated_data(payload)
    await hass.async_block_till_done()

    entity_id = _entity_id_for(hass, "device_today_pv_energy")
    state = hass.states.get(entity_id)

    assert state is not None
    assert state.state == "3.58"


async def test_day_period_sensor_never_sums_the_intraday_power_curve(
    hass: HomeAssistant,
    night_setup: MockConfigEntry,
) -> None:
    """Without a scalar total the power curve must not masquerade as energy.

    The month-chart bucket fallback is the correct source then — not the
    watt-sample sum of the day curve.
    """
    entry = night_setup
    coordinator = entry.runtime_data
    payload = _night_payload(hass)
    today = _local_today(hass)
    payload[_DEVICE_ID][_DAY_SECTION] = {
        APP_CHART_SERIES_Y: [500.0, 1500.0, 1000.0],
        "unit": "w",
        APP_REQUEST_META: {
            APP_REQUEST_BEGIN_DATE: today.isoformat(),
            APP_REQUEST_END_DATE: today.isoformat(),
        },
    }
    coordinator.async_set_updated_data(payload)
    await hass.async_block_till_done()

    entity_id = _entity_id_for(hass, "device_today_pv_energy")
    state = hass.states.get(entity_id)

    assert state is not None
    assert state.state == str(_TODAY_BUCKET_KWH)
    assert state.attributes["fallback"] == (f"current_day_bucket_from_{_MONTH_SECTION}")


async def test_day_period_sensor_stays_unknown_without_todays_bucket(
    hass: HomeAssistant,
    night_setup: MockConfigEntry,
) -> None:
    """Without today's bucket in any sibling chart the sensor stays unknown.

    A stale month chart (yesterday's range) must not leak an old bucket
    into today's value — the midnight-race guard semantics stay intact.
    """
    entry = night_setup
    coordinator = entry.runtime_data
    payload = _night_payload(hass)
    today = _local_today(hass)
    stale_begin = today.replace(day=1)
    # Truncate the series so it no longer contains today's index.
    payload[_DEVICE_ID][_MONTH_SECTION] = {
        APP_CHART_SERIES_Y: [1.0] * (today - stale_begin).days,
        APP_REQUEST_META: {
            APP_REQUEST_BEGIN_DATE: stale_begin.isoformat(),
            APP_REQUEST_END_DATE: today.isoformat(),
        },
    }
    coordinator.async_set_updated_data(payload)
    await hass.async_block_till_done()

    entity_id = _entity_id_for(hass, "device_today_pv_energy")
    state = hass.states.get(entity_id)

    assert state is not None
    assert state.state == "unknown"
