"""HTTP-only statistic ownership and period coverage contracts."""

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault.const import (
    APP_PERIOD_DATE_TYPES,
    APP_SECTION_HOME_STAT,
    APP_SECTION_TODAY_ENERGY,
    APP_STAT_TODAY_HOME_LOAD_ENERGY,
    APP_STAT_TOTAL_HOME_ENERGY,
    APP_STAT_TOTAL_IN_GRID_ENERGY,
    APP_STAT_TOTAL_OUT_GRID_ENERGY,
    DATE_TYPE_DAY,
    PAYLOAD_HOME_TRENDS,
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


def _native_value(sensor_key: str, payload: dict[str, Any]) -> float | None:
    """Evaluate a statistic description against an isolated coordinator payload."""
    description = next(desc for desc in STAT_DESCRIPTIONS if desc.key == sensor_key)
    sensor = JackeryStatSensor.__new__(JackeryStatSensor)
    mutable = cast("Any", sensor)
    mutable.coordinator = SimpleNamespace(
        data={_DEVICE_ID: payload},
        local_daily_energy_kwh=lambda _device_id, _metric_key: None,
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
    return cast("float | None", sensor.native_value)


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

    async def _hold_slow_fetch(result: Any) -> Any:
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

    def _blocking_endpoint(result: Any) -> AsyncMock:
        async def _fetch(*_args: Any, **_kwargs: Any) -> Any:
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
