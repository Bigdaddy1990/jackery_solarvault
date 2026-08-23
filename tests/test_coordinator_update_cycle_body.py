"""Behavioral tests for the guarded coordinator update-cycle body.

These drive ``_async_update_data_guarded`` through its main fetch / merge /
schedule paths using the reusable :mod:`tests._update_cycle_fixture`. Only the
Jackery cloud ``api`` boundary and the recorder statistics import are mocked;
all internal fetch, merge, gating, and scheduling logic runs for real.

The scenarios assert business outcomes (what ends up in the coordinator
result / diagnostics), never call order:

* a successful full cycle populates live device properties;
* a generic per-device property failure keeps the prior (stale) data;
* an invalid-device rejection (``code=20000``) is dropped and rediscovery is
  retried;
* an empty property payload is handled without raising;
* a broken third-party Shelly enrichment never breaks the L3 result;
* the recorder statistics import is dispatched once then throttled;
* an HTTP auth rejection escalates to ``ConfigEntryAuthFailed``.
"""

import asyncio
from datetime import date
from time import monotonic
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.client.api import (
    JackeryAuthError,
    JackeryError,
)
from custom_components.jackery_solarvault.const import (
    APP_CHART_SERIES_Y,
    APP_SECTION_HOME_TRENDS,
    APP_STAT_TOTAL_HOME_ENERGY,
    DATE_TYPE_MONTH,
    DATE_TYPE_YEAR,
    FIELD_DEVICE_SN,
    FIELD_MAX_SYS_OUT_PW,
    PAYLOAD_PROPERTIES,
)
from homeassistant.exceptions import ConfigEntryAuthFailed
from tests._update_cycle_fixture import (  # ruff:ignore[banned-api]
    DEVICE_ID,
    DEVICE_SN,
    SYSTEM_ID,
    make_update_cycle_api,
    setup_update_cycle_coordinator,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from custom_components.jackery_solarvault.coordinator import (
        JackerySolarVaultCoordinator,
    )
    from homeassistant.core import HomeAssistant


async def _teardown(hass: HomeAssistant, entry_id: str) -> None:
    """Unload the entry and drain background tasks."""
    await hass.config_entries.async_unload(entry_id)
    await hass.async_block_till_done()


@pytest.fixture
async def cycle(
    hass: HomeAssistant,
) -> AsyncGenerator[JackerySolarVaultCoordinator]:
    """Yield a default fully-wired coordinator and clean it up afterwards."""
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass)
    yield coordinator
    await _teardown(hass, entry.entry_id)


@pytest.mark.asyncio
async def test_full_cycle_populates_live_properties(
    cycle: JackerySolarVaultCoordinator,
    hass: HomeAssistant,
) -> None:
    """A fully-wired mock api yields a result with merged live properties."""
    result = await cycle._async_update_data_guarded()
    await hass.async_block_till_done()

    assert DEVICE_ID in result
    entry_data = result[DEVICE_ID]
    # HTTP-first merge: the pristine HTTP body and the merged properties both
    # carry the live battery SOC from the property payload.
    assert entry_data["http_properties"]["batSoc"] == 62
    assert entry_data["properties"]["batSoc"] == 62
    assert cycle._polling_diagnostics["property_fetch_completed"] is True


@pytest.mark.asyncio
async def test_device_property_fetches_run_concurrently(
    hass: HomeAssistant,
) -> None:
    """All devices' /device/property fetches are issued concurrently.

    The device returns its payload at once and the per-device property fetches
    are mutually independent, so serializing them was the dominant cost of the
    poll cycle. This asserts the fetches overlap (max in-flight > 1) rather than
    completing strictly one after another.
    """
    import asyncio

    in_flight = 0
    max_in_flight = 0

    async def _tracking_property(dev_id: str) -> dict[str, Any]:
        nonlocal in_flight, max_in_flight
        in_flight += 1
        max_in_flight = max(max_in_flight, in_flight)
        try:
            await asyncio.sleep(0.02)
            return {"device": {"deviceId": dev_id}, PAYLOAD_PROPERTIES: {"batSoc": 50}}
        finally:
            in_flight -= 1

    api = make_update_cycle_api(
        async_get_device_property=AsyncMock(side_effect=_tracking_property),
    )
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass, api=api)
    # Duplicate the discovered device into a second index slot so the loop has
    # more than one independent property fetch to run.
    second_id = f"{DEVICE_ID}0002"
    original_idx = dict(coordinator._device_index[DEVICE_ID])
    coordinator._device_index[second_id] = original_idx

    await coordinator._async_update_data_guarded()
    await hass.async_block_till_done()

    assert max_in_flight >= 2
    await _teardown(hass, entry.entry_id)


@pytest.mark.asyncio
async def test_property_failure_keeps_stale_data(
    hass: HomeAssistant,
) -> None:
    """A generic property fetch error keeps the previous cycle's data."""
    api = make_update_cycle_api(
        async_get_device_property=AsyncMock(
            side_effect=JackeryError("code=10600 device offline"),
        ),
    )
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass, api=api)
    stale_entry: dict[str, Any] = {"properties": {"batSoc": 41}, "marker": "stale"}
    coordinator.data = {DEVICE_ID: stale_entry}

    result = await coordinator._async_update_data_guarded()
    await hass.async_block_till_done()

    # The device was not dropped (10600 is not an invalid-device code) and the
    # prior data survived the failed refresh.
    assert result[DEVICE_ID] == stale_entry
    assert coordinator._polling_diagnostics["failures"] >= 1
    await _teardown(hass, entry.entry_id)


@pytest.mark.asyncio
async def test_cold_device_failure_keeps_other_device_result(
    hass: HomeAssistant,
) -> None:
    """One cold device failure does not discard another device's HTTP data."""

    def _device_property(dev_id: str) -> dict[str, Any]:
        if dev_id == DEVICE_ID:
            raise JackeryError
        return {
            "device": {"deviceId": dev_id},
            PAYLOAD_PROPERTIES: {"batSoc": 55},
        }

    api = make_update_cycle_api(
        async_get_device_property=AsyncMock(side_effect=_device_property),
    )
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass, api=api)
    second_id = f"{DEVICE_ID}0002"
    coordinator._device_index[second_id] = dict(coordinator._device_index[DEVICE_ID])
    cast("Any", coordinator).data = None

    result = await coordinator._async_update_data_guarded()
    await hass.async_block_till_done()

    assert DEVICE_ID not in result
    assert result[second_id][PAYLOAD_PROPERTIES]["batSoc"] == 55
    await _teardown(hass, entry.entry_id)


@pytest.mark.asyncio
async def test_historical_home_months_refresh_off_the_http_hot_path(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Month-only year repair stays non-blocking and completes in background."""

    def _home_trends(
        _system_id: str,
        **_request_kwargs: str,
    ) -> dict[str, Any]:
        return {APP_STAT_TOTAL_HOME_ENERGY: 2}

    api = make_update_cycle_api(
        async_get_home_trends=AsyncMock(side_effect=_home_trends),
    )
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass, api=api)
    today = date(2026, 5, 15)
    monkeypatch.setattr(coordinator, "_local_today", lambda: today)
    year_section = f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_YEAR}"
    month_section = f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_MONTH}"
    coordinator._slow_cache[SYSTEM_ID] = {
        year_section: (
            monotonic(),
            {APP_CHART_SERIES_Y: [0, 0, 0, 0, 7, 0, 0, 0, 0, 0, 0, 0]},
        ),
        month_section: (
            monotonic(),
            {APP_STAT_TOTAL_HOME_ENERGY: 2},
        ),
    }

    first_result = await coordinator._async_update_data_guarded()

    assert DEVICE_ID in first_result
    api.async_get_home_trends.assert_not_awaited()

    await hass.async_block_till_done()
    repaired_result = await coordinator._async_update_data_guarded()

    repaired_year = repaired_result[DEVICE_ID][year_section]
    assert repaired_year[APP_STAT_TOTAL_HOME_ENERGY] == 10
    await _teardown(hass, entry.entry_id)


@pytest.mark.asyncio
async def test_historical_home_months_acquire_shared_http_gate_once(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A configured one-request HTTP gate must not self-deadlock backfill."""

    def _home_trends(
        _system_id: str,
        **_request_kwargs: str,
    ) -> dict[str, Any]:
        return {APP_STAT_TOTAL_HOME_ENERGY: 2}

    api = make_update_cycle_api(
        async_get_home_trends=AsyncMock(side_effect=_home_trends),
    )
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass, api=api)
    slow_refresh_task: asyncio.Task[None] | None = None
    try:
        coordinator._slow_http_request_semaphore = asyncio.Semaphore(1)
        today = date(2026, 5, 15)
        monkeypatch.setattr(coordinator, "_local_today", lambda: today)
        year_section = f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_YEAR}"
        month_section = f"{APP_SECTION_HOME_TRENDS}_{DATE_TYPE_MONTH}"
        coordinator._slow_cache[SYSTEM_ID] = {
            year_section: (
                monotonic(),
                {APP_CHART_SERIES_Y: [0, 0, 0, 0, 7, 0, 0, 0, 0, 0, 0, 0]},
            ),
            month_section: (
                monotonic(),
                {APP_STAT_TOTAL_HOME_ENERGY: 2},
            ),
        }

        first_result = await coordinator._async_update_data_guarded()

        assert DEVICE_ID in first_result
        slow_refresh_task = coordinator._slow_metrics_bg_task
        assert slow_refresh_task is not None
        await asyncio.wait_for(slow_refresh_task, timeout=2)

        repaired_result = await coordinator._async_update_data_guarded()
        assert (
            repaired_result[DEVICE_ID][year_section][APP_STAT_TOTAL_HOME_ENERGY] == 10
        )
    finally:
        if slow_refresh_task is not None and not slow_refresh_task.done():
            slow_refresh_task.cancel()
            await asyncio.gather(slow_refresh_task, return_exceptions=True)
        await _teardown(hass, entry.entry_id)


@pytest.mark.asyncio
async def test_update_cycle_does_not_spawn_unowned_enrichment_tasks(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Slow accessory enrichments use only the tracked refresh worker."""
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass)
    real_create_background_task = hass.async_create_background_task
    create_background_task = MagicMock(wraps=real_create_background_task)
    monkeypatch.setattr(
        hass,
        "async_create_background_task",
        create_background_task,
    )
    try:
        await coordinator._async_update_data_guarded()
        names = [
            str(call.kwargs.get("name") or (call.args[1] if len(call.args) > 1 else ""))
            for call in create_background_task.call_args_list
        ]

        assert not [name for name in names if name.startswith("jackery_enrich_")]
        assert coordinator._slow_metrics_bg_task is not None
        await coordinator._slow_metrics_bg_task
    finally:
        await _teardown(hass, entry.entry_id)


@pytest.mark.asyncio
async def test_property_10600_without_prior_data_keeps_first_refresh_failed(
    hass: HomeAssistant,
) -> None:
    """An undocumented cold property failure must not become a false success.

    Existing data may survive a transient ``code=10600`` response, but a first
    refresh has no authoritative HTTP state to retain. Shadow enrichment is
    supplemental and must not turn that cold failure into successful setup.
    """
    api = make_update_cycle_api(
        async_get_device_property=AsyncMock(
            side_effect=JackeryError("code=10600 system has no property endpoint"),
        ),
        async_get_system_shadow=AsyncMock(
            return_value={FIELD_DEVICE_SN: DEVICE_SN, FIELD_MAX_SYS_OUT_PW: 3000},
        ),
        async_get_sub_shadow=AsyncMock(return_value={}),
        async_get_smart_mode_info=AsyncMock(return_value={}),
        async_get_smart_schedule_prediction=AsyncMock(return_value={}),
        async_query_tou_plan=AsyncMock(return_value={}),
    )
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass, api=api)
    # No prior data: this is the first refresh for a fresh system.
    cast("Any", coordinator).data = None

    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert coordinator.last_update_success is False
    assert not coordinator.data
    api.async_get_system_shadow.assert_not_awaited()
    await _teardown(hass, entry.entry_id)


@pytest.mark.asyncio
async def test_invalid_device_code_20000_is_dropped_and_rediscovered(
    hass: HomeAssistant,
) -> None:
    """A ``code=20000`` rejection drops the device and retries discovery once."""
    api = make_update_cycle_api(
        async_get_device_property=AsyncMock(
            side_effect=JackeryError("code=20000 invalid device"),
        ),
    )
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass, api=api)
    coordinator.data = {DEVICE_ID: {"marker": "stale"}}

    result = await coordinator._async_update_data_guarded()
    await hass.async_block_till_done()

    # Rediscovery ran a second time (drop -> empty index -> async_discover),
    # and the stale entry was preserved because prior data existed.
    assert api.async_get_system_list.await_count >= 2
    assert result[DEVICE_ID] == {"marker": "stale"}
    await _teardown(hass, entry.entry_id)


@pytest.mark.asyncio
async def test_empty_property_payload_is_handled(
    hass: HomeAssistant,
) -> None:
    """An empty property payload still builds an entry without raising."""
    api = make_update_cycle_api(
        async_get_device_property=AsyncMock(return_value={}),
    )
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass, api=api)

    result = await coordinator._async_update_data_guarded()
    await hass.async_block_till_done()

    assert DEVICE_ID in result
    assert coordinator._polling_diagnostics["empty_fetches"] >= 1
    await _teardown(hass, entry.entry_id)


@pytest.mark.asyncio
async def test_broken_shelly_enrichment_never_breaks_l3(
    hass: HomeAssistant,
) -> None:
    """A failing L5 Shelly realtime fetch does not gate or fail the L3 result."""
    api = make_update_cycle_api(
        async_get_shelly_realtime_power=AsyncMock(
            side_effect=JackeryError("shelly token expired"),
        ),
    )
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass, api=api)

    result = await coordinator._async_update_data_guarded()
    await hass.async_block_till_done()

    # L3 property data is present despite the third-party enrichment failure.
    assert result[DEVICE_ID]["properties"]["batSoc"] == 62
    await _teardown(hass, entry.entry_id)


@pytest.mark.asyncio
async def test_statistics_import_dispatched_then_throttled(
    hass: HomeAssistant,
) -> None:
    """The recorder import runs again after slow metrics advanced period caches."""
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass)
    import_job = AsyncMock(return_value=None)
    coordinator._async_import_current_app_chart_statistics_job = import_job  # type: ignore[method-assign]
    coordinator._statistics_import_ready = True

    await coordinator._async_update_data_guarded()
    await hass.async_block_till_done()
    await coordinator._async_update_data_guarded()
    await hass.async_block_till_done()

    # The background slow-metrics refresh advances periodic caches and resets
    # the import throttle so the next coordinator cycle consumes fresh buckets.
    assert import_job.await_count == 2
    await _teardown(hass, entry.entry_id)


@pytest.mark.asyncio
async def test_http_auth_rejection_raises_config_entry_auth_failed(
    hass: HomeAssistant,
) -> None:
    """An auth rejection on the authoritative property fetch opens reauth."""
    api = make_update_cycle_api(
        async_get_device_property=AsyncMock(
            side_effect=JackeryAuthError("credentials rejected"),
        ),
    )
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass, api=api)

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data_guarded()
    await _teardown(hass, entry.entry_id)
