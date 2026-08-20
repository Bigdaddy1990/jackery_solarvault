"""HTTP-only statistic ownership and period coverage contracts."""

import asyncio
from datetime import date
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault.const import (
    APP_CHART_SERIES_Y,
    APP_DEVICE_STAT_BATTERY_DISCHARGE,
    APP_DEVICE_STAT_BATTERY_TO_GRID,
    APP_DEVICE_STAT_ONGRID_INPUT,
    APP_DEVICE_STAT_ONGRID_TO_BATTERY,
    APP_DEVICE_STAT_PV_ENERGY,
    APP_DEVICE_STAT_PV_TO_BATTERY,
    APP_PERIOD_DATE_TYPES,
    APP_REQUEST_BEGIN_DATE,
    APP_REQUEST_META,
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_CT_STAT,
    APP_SECTION_HOME_STAT,
    APP_SECTION_HOME_TRENDS,
    APP_SECTION_TODAY_ENERGY,
    APP_STAT_TODAY_BATTERY_DISCHARGE,
    APP_STAT_TODAY_BATTERY_ENERGY,
    APP_STAT_TODAY_GENERATION,
    APP_STAT_TODAY_GRID_IMPORT_ENERGY,
    APP_STAT_TODAY_HOME_LOAD_ENERGY,
    APP_STAT_TODAY_SOLAR_ENERGY,
    APP_STAT_TOTAL_CT_INPUT_ENERGY,
    APP_STAT_TOTAL_HOME_ENERGY,
    APP_STAT_TOTAL_IN_GRID_ENERGY,
    APP_STAT_TOTAL_OUT_GRID_ENERGY,
    APP_STAT_UNIT,
    APP_TODAY_ENERGY_SOURCE_META,
    APP_UNIT_KWH,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    FIELD_CT_TOTAL_PHASE_ENERGY,
    PAYLOAD_HOME_TRENDS,
    PAYLOAD_LOCAL_DAILY_ENERGY,
    PAYLOAD_STATISTIC,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from custom_components.jackery_solarvault.sensor import (
    STAT_DESCRIPTIONS,
    JackeryStatSensor,
)
from tests._update_cycle_fixture import (  # ruff:ignore[banned-api]
    DEVICE_ID,
    make_update_cycle_api,
    setup_update_cycle_coordinator,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_DEVICE_ID = "device-1"


def _stat_sensor(
    sensor_key: str,
    payload: dict[str, Any],
    *,
    local_daily_kwh: dict[str, float] | None = None,
    local_period_kwh: float | None = None,
) -> JackeryStatSensor:
    """Build and refresh a statistic sensor against an isolated payload."""
    description = next(desc for desc in STAT_DESCRIPTIONS if desc.key == sensor_key)
    sensor = JackeryStatSensor.__new__(JackeryStatSensor)
    mutable = cast("Any", sensor)
    mutable.coordinator = SimpleNamespace(
        data={_DEVICE_ID: payload},
        local_daily_energy_kwh=lambda _device_id, metric_key: (
            None if local_daily_kwh is None else local_daily_kwh.get(metric_key)
        ),
        local_period_energy_kwh=lambda _device_id, _metric_key, **_kwargs: (
            local_period_kwh
        ),
    )
    mutable.hass = SimpleNamespace(config=SimpleNamespace(time_zone="UTC"))
    mutable._device_id = _DEVICE_ID  # ruff: ignore[private-member-access]
    mutable.entity_description = description
    mutable._reset_period = description.reset_period  # ruff: ignore[private-member-access]
    mutable._cached_native_value = None  # ruff: ignore[private-member-access]
    mutable._cached_attrs = {}  # ruff: ignore[private-member-access]
    mutable._cached_source_section = description.section  # ruff: ignore[private-member-access]

    context = sensor._capture_refresh_context(payload)  # ruff: ignore[private-member-access]
    sensor._apply_cache_snapshot(sensor._refresh_cache(context, {}))  # ruff: ignore[private-member-access]
    return sensor


def _native_value(sensor_key: str, payload: dict[str, Any]) -> float | None:
    """Evaluate a statistic description against an isolated coordinator payload."""
    return cast("float | None", _stat_sensor(sensor_key, payload).native_value)


def test_home_day_energy_uses_exact_app_dto_owners() -> None:
    """Grid import/export and home consumption cannot alias similar DTO fields."""
    home_stat_day = f"{APP_SECTION_HOME_STAT}_{DATE_TYPE_DAY}"
    payload = {
        home_stat_day: {
            APP_STAT_TOTAL_IN_GRID_ENERGY: 4.2,
            APP_STAT_TOTAL_OUT_GRID_ENERGY: 1.1,
            # Deliberately conflicting: HomeStat does not own this field.
            APP_STAT_TOTAL_HOME_ENERGY: 999.0,
        },
        PAYLOAD_HOME_TRENDS: {
            APP_STAT_TOTAL_HOME_ENERGY: 7.8,
        },
    }

    assert _native_value("device_today_ongrid_input", payload) == pytest.approx(4.2)
    assert _native_value("device_today_ongrid_output", payload) == pytest.approx(1.1)
    assert _native_value("home_day_energy", payload) == pytest.approx(7.8)


def test_home_day_description_names_the_system_home_trends_section() -> None:
    """Metadata itself documents the App DTO owning totalHomeEgy."""
    description = next(
        desc for desc in STAT_DESCRIPTIONS if desc.key == "home_day_energy"
    )

    assert description.section == PAYLOAD_HOME_TRENDS
    assert description.stat_key == APP_STAT_TOTAL_HOME_ENERGY


def test_compact_today_energy_uses_only_positive_documented_fallbacks() -> None:
    """Lagging compact zeros are repaired without manufacturing zero energy."""
    payload = {
        APP_SECTION_TODAY_ENERGY: {
            APP_STAT_TODAY_SOLAR_ENERGY: 0,
            APP_STAT_TODAY_GRID_IMPORT_ENERGY: 0,
            APP_STAT_TODAY_HOME_LOAD_ENERGY: 0,
            APP_STAT_TODAY_BATTERY_ENERGY: 0,
        },
        PAYLOAD_LOCAL_DAILY_ENERGY: {
            APP_DEVICE_STAT_PV_ENERGY: 1817,
            APP_DEVICE_STAT_ONGRID_INPUT: 5,
            APP_DEVICE_STAT_BATTERY_DISCHARGE: 124,
        },
        PAYLOAD_HOME_TRENDS: {APP_STAT_TOTAL_HOME_ENERGY: "0.75"},
    }

    JackerySolarVaultCoordinator._reconcile_compact_today_energy(  # ruff: ignore[private-member-access]
        payload,
        today=date(2026, 8, 11),
    )

    assert payload[APP_SECTION_TODAY_ENERGY] == {
        APP_STAT_TODAY_SOLAR_ENERGY: pytest.approx(18.17),
        APP_STAT_TODAY_GRID_IMPORT_ENERGY: pytest.approx(0.05),
        APP_STAT_TODAY_HOME_LOAD_ENERGY: pytest.approx(0.75),
        APP_STAT_TODAY_BATTERY_ENERGY: pytest.approx(1.24),
        APP_TODAY_ENERGY_SOURCE_META: {
            APP_STAT_TODAY_SOLAR_ENERGY: {
                "source_section": PAYLOAD_LOCAL_DAILY_ENERGY,
                "source_key": APP_DEVICE_STAT_PV_ENERGY,
                "fallback": "local_lifetime_delta",
            },
            APP_STAT_TODAY_GRID_IMPORT_ENERGY: {
                "source_section": PAYLOAD_LOCAL_DAILY_ENERGY,
                "source_key": APP_DEVICE_STAT_ONGRID_INPUT,
                "fallback": "local_lifetime_delta",
            },
            APP_STAT_TODAY_BATTERY_ENERGY: {
                "source_section": PAYLOAD_LOCAL_DAILY_ENERGY,
                "source_key": APP_DEVICE_STAT_BATTERY_DISCHARGE,
                "fallback": "local_lifetime_delta",
            },
            APP_STAT_TODAY_HOME_LOAD_ENERGY: {
                "source_section": PAYLOAD_HOME_TRENDS,
                "source_key": APP_STAT_TOTAL_HOME_ENERGY,
                "fallback": "documented_http_fallback",
            },
        },
    }

    home_sensor = _stat_sensor("today_home_load_energy", payload)
    assert home_sensor.extra_state_attributes == {
        "source_section": PAYLOAD_HOME_TRENDS,
        "source_key": APP_STAT_TOTAL_HOME_ENERGY,
        "fallback": "documented_http_fallback",
    }
    solar_sensor = _stat_sensor("today_feed_in_energy", payload)
    assert solar_sensor.extra_state_attributes == {
        "source_section": PAYLOAD_LOCAL_DAILY_ENERGY,
        "source_key": APP_DEVICE_STAT_PV_ENERGY,
        "fallback": "local_lifetime_delta",
        "fallback_metric": APP_DEVICE_STAT_PV_ENERGY,
    }


def test_compact_today_local_delta_can_beat_lagging_positive_http_value() -> None:
    """Current-day local deltas may replace lagging positive HTTP/App totals."""
    payload = {
        APP_SECTION_TODAY_ENERGY: {
            APP_STAT_TODAY_SOLAR_ENERGY: 20.62,
            APP_STAT_TODAY_BATTERY_ENERGY: 3.98,
            APP_TODAY_ENERGY_SOURCE_META: {
                APP_STAT_TODAY_SOLAR_ENERGY: {
                    "source_section": PAYLOAD_LOCAL_DAILY_ENERGY,
                    "source_key": APP_DEVICE_STAT_PV_ENERGY,
                    "fallback": "local_lifetime_delta",
                },
                APP_STAT_TODAY_BATTERY_ENERGY: {
                    "source_section": PAYLOAD_LOCAL_DAILY_ENERGY,
                    "source_key": APP_DEVICE_STAT_BATTERY_DISCHARGE,
                    "fallback": "local_lifetime_delta",
                },
            },
        },
        PAYLOAD_STATISTIC: {
            APP_STAT_TODAY_GENERATION: 0.65,
            APP_STAT_TODAY_BATTERY_DISCHARGE: 0.63,
        },
        PAYLOAD_LOCAL_DAILY_ENERGY: {
            APP_DEVICE_STAT_PV_ENERGY: 2062,
            APP_DEVICE_STAT_BATTERY_DISCHARGE: 398,
        },
    }

    JackerySolarVaultCoordinator._reconcile_compact_today_energy(  # ruff: ignore[private-member-access]
        payload,
        today=date(2026, 8, 13),
    )

    compact = cast("dict[str, Any]", payload[APP_SECTION_TODAY_ENERGY])
    assert compact[APP_STAT_TODAY_SOLAR_ENERGY] == pytest.approx(20.62)
    assert compact[APP_STAT_TODAY_BATTERY_ENERGY] == pytest.approx(3.98)
    provenance = cast("dict[str, Any]", compact[APP_TODAY_ENERGY_SOURCE_META])
    assert provenance[APP_STAT_TODAY_SOLAR_ENERGY] == {
        "source_section": PAYLOAD_LOCAL_DAILY_ENERGY,
        "source_key": APP_DEVICE_STAT_PV_ENERGY,
        "fallback": "local_lifetime_delta",
    }
    assert provenance[APP_STAT_TODAY_BATTERY_ENERGY] == {
        "source_section": PAYLOAD_LOCAL_DAILY_ENERGY,
        "source_key": APP_DEVICE_STAT_BATTERY_DISCHARGE,
        "fallback": "local_lifetime_delta",
    }

    local_daily_kwh = {
        APP_DEVICE_STAT_PV_ENERGY: 20.62,
        APP_DEVICE_STAT_BATTERY_DISCHARGE: 3.98,
    }
    solar_sensor = _stat_sensor(
        "today_feed_in_energy",
        payload,
        local_daily_kwh=local_daily_kwh,
    )
    battery_sensor = _stat_sensor(
        "today_battery_energy",
        payload,
        local_daily_kwh=local_daily_kwh,
    )
    assert solar_sensor.native_value == pytest.approx(20.62)
    assert battery_sensor.native_value == pytest.approx(3.98)
    assert solar_sensor.extra_state_attributes["source_section"] == (
        PAYLOAD_LOCAL_DAILY_ENERGY
    )
    assert battery_sensor.extra_state_attributes["source_section"] == (
        PAYLOAD_LOCAL_DAILY_ENERGY
    )


def test_compact_today_energy_uses_current_month_home_bucket() -> None:
    """Home load uses the documented current-month bucket when day trend is empty."""
    today = date(2026, 8, 11)
    home_month_section = f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_MONTH}"
    payload = {
        APP_SECTION_TODAY_ENERGY: {APP_STAT_TODAY_HOME_LOAD_ENERGY: 0},
        PAYLOAD_HOME_TRENDS: {},
        home_month_section: {
            APP_CHART_SERIES_Y: [0.0] * 10 + [0.9],
            APP_REQUEST_META: {APP_REQUEST_BEGIN_DATE: "2026-08-01"},
            APP_STAT_UNIT: APP_UNIT_KWH,
        },
    }

    JackerySolarVaultCoordinator._reconcile_compact_today_energy(  # ruff: ignore[private-member-access]
        payload,
        today=today,
    )

    compact = cast("dict[str, Any]", payload[APP_SECTION_TODAY_ENERGY])
    assert compact[APP_STAT_TODAY_HOME_LOAD_ENERGY] == pytest.approx(0.9)
    provenance = cast("dict[str, Any]", compact[APP_TODAY_ENERGY_SOURCE_META])
    assert provenance[APP_STAT_TODAY_HOME_LOAD_ENERGY] == {
        "source_section": home_month_section,
        "source_key": APP_STAT_TOTAL_HOME_ENERGY,
        "fallback": "current_month_bucket",
    }
    home_sensor = _stat_sensor("today_home_load_energy", payload)
    assert home_sensor.extra_state_attributes == {
        "source_section": home_month_section,
        "source_key": APP_STAT_TOTAL_HOME_ENERGY,
        "fallback": "current_month_bucket",
        "request": {APP_REQUEST_BEGIN_DATE: "2026-08-01"},
    }


def test_today_battery_flows_use_http_day_as_independent_fallback() -> None:
    """HTTP day totals supplement local deltas and independently confirm zero."""
    battery_day = f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}"
    payload = {
        PAYLOAD_LOCAL_DAILY_ENERGY: {
            APP_DEVICE_STAT_ONGRID_TO_BATTERY: 0,
            APP_DEVICE_STAT_PV_TO_BATTERY: 511,
            APP_DEVICE_STAT_BATTERY_TO_GRID: 123,
        },
        battery_day: {
            APP_DEVICE_STAT_ONGRID_TO_BATTERY: 0.0,
            APP_DEVICE_STAT_PV_TO_BATTERY: 5.11,
            APP_DEVICE_STAT_BATTERY_TO_GRID: 1.15,
        },
    }

    zero_sensor = _stat_sensor("device_today_ongrid_to_battery", payload)
    assert zero_sensor.native_value == pytest.approx(0.0)
    assert zero_sensor.extra_state_attributes == {
        "source_section": battery_day,
        "source_key": APP_DEVICE_STAT_ONGRID_TO_BATTERY,
        "fallback": "documented_http_day_fallback",
    }
    assert _native_value("device_today_pv_to_battery", payload) == pytest.approx(5.11)
    assert _native_value("device_today_battery_to_ongrid", payload) == pytest.approx(
        1.23
    )


def test_today_battery_flow_http_fallback_keeps_kwh_unit() -> None:
    """A positive HTTP-only day total is not divided as a local raw counter."""
    battery_day = f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}"
    payload = {
        battery_day: {APP_DEVICE_STAT_PV_TO_BATTERY: 5.11},
    }

    sensor = _stat_sensor("device_today_pv_to_battery", payload)

    assert sensor.native_value == pytest.approx(5.11)
    assert sensor.extra_state_attributes["fallback"] == ("documented_http_day_fallback")


def test_today_battery_flow_compares_local_and_http_in_kwh() -> None:
    """A small native local delta cannot mask a larger HTTP kWh day total."""
    battery_day = f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}"
    payload = {
        PAYLOAD_LOCAL_DAILY_ENERGY: {APP_DEVICE_STAT_PV_TO_BATTERY: 1},
        battery_day: {APP_DEVICE_STAT_PV_TO_BATTERY: 5.11},
    }

    sensor = _stat_sensor("device_today_pv_to_battery", payload)

    assert sensor.native_value == pytest.approx(5.11)
    assert sensor.extra_state_attributes["fallback"] == ("documented_http_day_fallback")


def test_today_battery_flow_does_not_publish_lone_http_zero() -> None:
    """An uncorroborated HTTP zero remains unavailable per AGENTS zero rules."""
    battery_day = f"{APP_SECTION_BATTERY_STAT}_{DATE_TYPE_DAY}"
    payload = {
        battery_day: {APP_DEVICE_STAT_ONGRID_TO_BATTERY: 0.0},
    }

    assert _native_value("device_today_ongrid_to_battery", payload) is None


def test_today_battery_flow_does_not_publish_lone_local_zero() -> None:
    """An uncorroborated local zero remains unavailable per AGENTS zero rules."""
    payload = {
        PAYLOAD_LOCAL_DAILY_ENERGY: {APP_DEVICE_STAT_ONGRID_TO_BATTERY: 0},
    }

    assert _native_value("device_today_ongrid_to_battery", payload) is None


def test_ct_week_uses_fully_covered_local_period_when_cloud_is_placeholder() -> None:
    """A verified local period can replace one unconfirmed cloud zero."""
    ct_week = f"{APP_SECTION_CT_STAT}_{DATE_TYPE_WEEK}"
    payload = {
        ct_week: {
            APP_STAT_TOTAL_CT_INPUT_ENERGY: 0.0,
            APP_CHART_SERIES_Y: [],
        },
    }

    sensor = _stat_sensor("ct_input_week_energy", payload, local_period_kwh=6.5)

    assert sensor.native_value == pytest.approx(6.5)
    assert sensor.extra_state_attributes["source_section"] == PAYLOAD_LOCAL_DAILY_ENERGY
    assert sensor.extra_state_attributes["fallback"] == "local_lifetime_delta"
    assert (
        sensor.extra_state_attributes["fallback_metric"] == FIELD_CT_TOTAL_PHASE_ENERGY
    )


@pytest.mark.asyncio
async def test_http_only_cycle_fetches_every_proven_device_stat_period(
    hass: HomeAssistant,
) -> None:
    """Every App period is fetched over HTTP without a Layer-5 transport."""
    api = make_update_cycle_api()
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass, api=api)

    try:
        await coordinator._async_update_data_guarded()  # ruff: ignore[private-member-access]
        assert coordinator._slow_metrics_bg_task is not None  # ruff: ignore[private-member-access]
        await coordinator._slow_metrics_bg_task  # ruff: ignore[private-member-access]

        for endpoint_name in (
            "async_get_device_pv_stat",
            "async_get_device_battery_stat",
            "async_get_device_home_stat",
            "async_get_device_ct_stat",
            "async_get_device_eps_stat",
        ):
            endpoint = getattr(api, endpoint_name)
            fetched_periods = {
                call.kwargs["date_type"] for call in endpoint.await_args_list
            }
            assert fetched_periods == set(APP_PERIOD_DATE_TYPES), endpoint_name
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_http_only_cycle_reconciles_today_home_load_from_home_trends(
    hass: HomeAssistant,
) -> None:
    """The 2.4.0 home-trend DTO repairs a stale zero from device/stat/today."""
    api = make_update_cycle_api(
        async_get_today_energy=AsyncMock(
            return_value={
                "de": 0.75,
                "dg": 0,
                APP_STAT_TODAY_HOME_LOAD_ENERGY: 0,
                "ds": 0.5,
            },
        ),
        async_get_home_trends=AsyncMock(
            return_value={APP_STAT_TOTAL_HOME_ENERGY: "0.75"},
        ),
    )
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass, api=api)

    try:
        await coordinator._async_update_data_guarded()  # ruff: ignore[private-member-access]
        assert coordinator._slow_metrics_bg_task is not None  # ruff: ignore[private-member-access]
        await coordinator._slow_metrics_bg_task  # ruff: ignore[private-member-access]

        result = await coordinator._async_update_data_guarded()  # ruff: ignore[private-member-access]

        assert result[DEVICE_ID][APP_SECTION_TODAY_ENERGY][
            APP_STAT_TODAY_HOME_LOAD_ENERGY
        ] == pytest.approx(0.75)
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_slow_http_refresh_bounds_request_concurrency_without_blocking_property(
    hass: HomeAssistant,
) -> None:
    """Slow HTTP enrichment cannot burst or block the authoritative property poll."""
    release_slow_fetches = asyncio.Event()
    concurrency_limit_reached = asyncio.Event()
    active_slow_fetches = 0
    max_active_slow_fetches = 0

    async def _hold_slow_fetch(result: Any) -> Any:  # noqa: RUF105
        nonlocal active_slow_fetches, max_active_slow_fetches
        active_slow_fetches += 1
        max_active_slow_fetches = max(
            max_active_slow_fetches,
            active_slow_fetches,
        )
        if active_slow_fetches >= 2:
            concurrency_limit_reached.set()
        try:
            await release_slow_fetches.wait()
        finally:
            active_slow_fetches -= 1
        return result

    def _blocking_endpoint(result: Any) -> AsyncMock:  # noqa: RUF105
        async def _fetch(*_args: Any, **_kwargs: Any) -> Any:  # noqa: RUF105
            return await _hold_slow_fetch(result)

        return AsyncMock(side_effect=_fetch)

    api = make_update_cycle_api(
        async_get_system_statistic=_blocking_endpoint({}),
        async_get_alarm=_blocking_endpoint(None),
        async_get_pv_trends=_blocking_endpoint({}),
        async_get_home_trends=_blocking_endpoint({}),
        async_get_battery_trends=_blocking_endpoint({}),
        async_get_dynamic_price=_blocking_endpoint({}),
        async_get_power_price=_blocking_endpoint({}),
        async_get_price_sources=_blocking_endpoint([]),
        async_get_price_history_config=_blocking_endpoint({}),
    )
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass, api=api)
    slow_refresh_task = None

    try:
        await coordinator._async_update_data_guarded()  # ruff: ignore[private-member-access]
        slow_refresh_task = coordinator._slow_metrics_bg_task  # ruff: ignore[private-member-access]
        assert slow_refresh_task is not None

        await asyncio.wait_for(concurrency_limit_reached.wait(), timeout=1)
        # Give every task in the background gather a chance to enter its
        # endpoint. Without a shared limiter this rises to the whole burst.
        await asyncio.sleep(0)
        await asyncio.sleep(0)

        assert api.async_get_device_property.await_count == 1
        assert max_active_slow_fetches <= 2
    finally:
        release_slow_fetches.set()
        if slow_refresh_task is not None:
            await slow_refresh_task
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
