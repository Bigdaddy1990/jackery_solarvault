"""Behavioral coverage for independent coordinator runtime paths."""

import asyncio
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.jackery_solarvault import coordinator as coord_mod
from custom_components.jackery_solarvault.client import JackeryApiError
from custom_components.jackery_solarvault.const import (
    CONF_THIRD_PARTY_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
)
from custom_components.jackery_solarvault.coordinator import (
    BackfillStatus,
    JackerySolarVaultCoordinator,
)
from custom_components.jackery_solarvault.ingest import TransportSource

# Alias used in this test file for clarity (coordinator imports it as THIRD_PARTY_MQTT_ENABLE)
CONF_LOCAL_MQTT_ENABLE = CONF_THIRD_PARTY_MQTT_ENABLE
CONF_LOCAL_MQTT_HOST = CONF_THIRD_PARTY_MQTT_IP

_DEVICE_ID = "device-1"
_TARGET_DAY = date(2026, 8, 10)


def _bare_coordinator() -> Any:
    """Return a coordinator shell with only state used by these contracts."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    shell = cast("Any", coordinator)
    shell._shutdown_started = False
    shell._property_source_state = {}
    shell._accessory_source_state = {}
    shell._property_overrides = {}
    shell._background_tasks = {}
    shell._configured_update_interval = timedelta(seconds=15)
    shell._polling_diagnostics = {}
    shell._polling_timeout_started_monotonic = None
    shell._mqtt = None
    shell._ble_listener = None
    shell._device_index = {}
    shell.entry = SimpleNamespace(options={}, data={})
    shell.api = SimpleNamespace(get_cached_mqtt_credentials=lambda: None)
    return shell


def test_polling_timeout_incident_is_counted_once_and_recovers() -> None:
    """Repeated timeouts extend one incident; a clean cycle closes it."""
    coordinator = _bare_coordinator()

    with patch.object(coord_mod.time, "monotonic", side_effect=[120.0, 124.0, 130.0]):
        coordinator._note_polling_timeout(100.0)
        coordinator._note_polling_timeout(100.0)
        coordinator._recover_polling_timeout()

    diagnostics = coordinator._polling_diagnostics
    assert diagnostics["timeout_incident_count"] == 1
    assert diagnostics["last_timeout_elapsed_sec"] == pytest.approx(24.0)
    assert diagnostics["incident_max_timeout_elapsed_sec"] == pytest.approx(24.0)
    assert diagnostics["max_overrun_sec"] == pytest.approx(9.0)
    assert diagnostics["timeout_active"] is False
    assert diagnostics["last_timeout_recovery_duration_sec"] == pytest.approx(30.0)


@pytest.mark.asyncio
async def test_background_scheduler_reuses_active_task_then_allows_replay() -> None:
    """One logical operation never overlaps, but can run again after completion."""
    coordinator = _bare_coordinator()
    release = asyncio.Event()
    starts: list[int] = []

    async def operation() -> int:
        starts.append(len(starts) + 1)
        await release.wait()
        return starts[-1]

    coordinator.hass = SimpleNamespace(
        async_create_background_task=lambda coro, **kwargs: asyncio.create_task(
            coro,
            name=kwargs["name"],
        )
    )

    first = coordinator._schedule_background_once(
        "refresh",
        operation,
        name="refresh-one",
    )
    duplicate = coordinator._schedule_background_once(
        "refresh",
        operation,
        name="refresh-two",
    )
    assert duplicate is first
    await asyncio.sleep(0)
    assert starts == [1]

    release.set()
    assert first is not None
    await first
    await asyncio.sleep(0)
    assert "refresh" not in coordinator._background_tasks

    replay = coordinator._schedule_background_once(
        "refresh",
        operation,
        name="refresh-three",
    )
    assert replay is not None and replay is not first
    assert await replay == 2


def test_stale_live_property_is_rejected_without_blocking_http_configuration() -> None:
    """An old live frame cannot rewind SOC while its static field still merges."""
    coordinator = _bare_coordinator()
    stale_at = datetime.now(UTC) - timedelta(hours=6)

    merged = coordinator._property_updates_for_source(
        _DEVICE_ID,
        {"soc": 12, "temperatureUnit": 1},
        TransportSource.HTTP,
        base={"soc": 83, "temperatureUnit": 0},
        observed_at=stale_at,
    )

    assert merged["soc"] == 83
    assert merged["temperatureUnit"] == 1


def test_stale_accessory_frame_keeps_identity_but_not_old_telemetry() -> None:
    """Accessory identity remains discoverable while stale telemetry is ignored."""
    coordinator = _bare_coordinator()
    stale_at = datetime.now(UTC) - timedelta(hours=6)

    merged = coordinator._accessory_updates_for_source(
        _DEVICE_ID,
        "battery_packs",
        "pack-1",
        {"deviceSn": "pack-1", "soc": 11},
        TransportSource.LOCAL_MQTT,
        current={"deviceSn": "pack-1", "soc": 72},
        observed_at=stale_at,
    )

    assert merged["deviceSn"] == "pack-1"
    assert merged["soc"] == 72


def test_system_info_cache_fills_only_missing_values_until_expiry() -> None:
    """Cached HTTP system info fills gaps but never overwrites fresh values."""
    coordinator = _bare_coordinator()
    coordinator._system_info_cache = {
        _DEVICE_ID: {"firmwareVersion": "1.2.3", "wifiName": "cached"}
    }
    coordinator._system_info_cache_monotonic = {_DEVICE_ID: 100.0}

    with patch.object(coord_mod.time, "monotonic", return_value=101.0):
        filled = coordinator._overlay_cached_system_info(
            _DEVICE_ID,
            {"wifiName": "fresh"},
        )

    assert filled == {"firmwareVersion": "1.2.3", "wifiName": "fresh"}

    with patch.object(
        coord_mod.time,
        "monotonic",
        return_value=100.0 + coord_mod.SYSTEM_INFO_CACHE_MAX_AGE_SEC + 1,
    ):
        expired = coordinator._overlay_cached_system_info(
            _DEVICE_ID,
            {},
        )
    assert expired == {}


def test_transport_supervisors_are_independent_and_configuration_visible() -> None:
    """A disconnected supplemental client does not hide other supervisors."""
    coordinator = _bare_coordinator()
    coordinator.entry = SimpleNamespace(
        options={
            coord_mod.CONF_ENABLE_BLE_TRANSPORT: True,
            CONF_LOCAL_MQTT_ENABLE: True,
        },
        data={},
    )
    coordinator.api = SimpleNamespace(
        get_cached_mqtt_credentials=lambda: {"username": "cached"}
    )

    assert coordinator._data_source_supervisor_available("http") is True
    assert coordinator._data_source_supervisor_available("cloud_mqtt") is True
    assert coordinator._data_source_supervisor_available("ble") is True
    assert coordinator._data_source_supervisor_available("local_mqtt") is True
    assert coordinator._data_source_supervisor_available("unknown") is False

    coordinator._shutdown_started = True
    assert coordinator._data_source_supervisor_available("http") is False
    assert coordinator._command_source_available(_DEVICE_ID, "http") is False


def test_command_sources_use_transport_specific_readiness() -> None:
    """HTTP, Cloud MQTT, BLE, and unsupported Local MQTT have separate gates."""
    coordinator = _bare_coordinator()
    coordinator.api = SimpleNamespace(
        get_cached_mqtt_credentials=lambda: {"username": "cached"}
    )
    coordinator._mqtt = SimpleNamespace(is_connected=False)
    coordinator._ble_listener = object()
    coordinator._ble_writes_enabled = lambda: True
    coordinator.device_bluetooth_key = lambda _device_id: "key"
    coordinator._ble_address_for_device = lambda _device_id: None

    assert coordinator._command_source_available(_DEVICE_ID, "http") is True
    assert coordinator._command_source_available(_DEVICE_ID, "cloud_mqtt") is True
    assert coordinator._command_source_available(_DEVICE_ID, "ble") is True
    assert coordinator._command_source_available(_DEVICE_ID, "local_mqtt") is False


def test_historical_sources_include_system_routes_only_with_system_id() -> None:
    """PV and trend backfill routes require a resolved system identifier."""
    coordinator = _bare_coordinator()
    without_system = coordinator._historical_day_source_prefixes(
        _DEVICE_ID,
        {},
    )
    with_system = coordinator._historical_day_source_prefixes(
        _DEVICE_ID,
        {coord_mod.PAYLOAD_SYSTEM: {coord_mod.FIELD_ID: "system-1"}},
    )

    assert coord_mod.APP_SECTION_PV_STAT not in without_system
    assert coord_mod.APP_SECTION_HOME_TRENDS not in without_system
    assert coord_mod.APP_SECTION_PV_STAT in with_system
    assert coord_mod.APP_SECTION_HOME_TRENDS in with_system


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ["section", "api_method"],
    [
        [coord_mod.APP_SECTION_BATTERY_STAT, "async_get_device_battery_stat"],
        [coord_mod.APP_SECTION_HOME_STAT, "async_get_device_home_stat"],
        [coord_mod.APP_SECTION_CT_STAT, "async_get_device_ct_stat"],
        [coord_mod.APP_SECTION_EPS_STAT, "async_get_device_eps_stat"],
        [coord_mod.APP_SECTION_PV_STAT, "async_get_device_pv_stat"],
        [coord_mod.APP_SECTION_HOME_TRENDS, "async_get_home_trends"],
    ],
)
async def test_historical_http_sources_route_independently(
    section: str,
    api_method: str,
) -> None:
    """Every supported statistics source invokes only its matching HTTP getter."""
    coordinator = _bare_coordinator()
    api = MagicMock()
    method = AsyncMock(return_value={"series": [1]})
    setattr(api, api_method, method)
    coordinator.api = api
    payload = {coord_mod.PAYLOAD_SYSTEM: {coord_mod.FIELD_ID: "system-1"}}

    status, result = await coordinator._async_fetch_historical_day_chart_source(
        device_id=_DEVICE_ID,
        payload=payload,
        target_day=_TARGET_DAY,
        section_prefix=section,
    )

    assert status == "fetched"
    assert result == {"series": [1]}
    method.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ["failure", "expected_status"],
    [
        [coord_mod.JackeryAuthError("rejected"), "auth_error"],
        [JackeryApiError("code=10426 busy"), "rate_limited"],
        [TimeoutError(), "transport_error"],
        [RuntimeError("unexpected"), "transport_error"],
    ],
)
async def test_historical_http_failures_remain_retryable_and_local(
    failure: Exception,
    expected_status: str,
) -> None:
    """Supplemental history errors are classified without stopping live HTTP."""
    coordinator = _bare_coordinator()
    coordinator.api = SimpleNamespace(
        async_get_device_battery_stat=AsyncMock(side_effect=failure)
    )

    status, result = await coordinator._async_fetch_historical_day_chart_source(
        device_id=_DEVICE_ID,
        payload={},
        target_day=_TARGET_DAY,
        section_prefix=coord_mod.APP_SECTION_BATTERY_STAT,
    )

    assert status == expected_status
    assert result == {}


def test_backfill_state_never_marks_an_open_period_imported() -> None:
    """Open buckets remain retryable while closed imported buckets stay durable."""
    normalize = coord_mod._normalize_backfill_status
    is_closed = coord_mod._backfill_period_is_closed

    assert normalize("imported", closed=False) is BackfillStatus.RETRYABLE
    assert normalize("imported", closed=True) is BackfillStatus.IMPORTED
    assert normalize("recorder_error", closed=True) is BackfillStatus.RETRYABLE
    assert normalize("unknown", closed=True) is BackfillStatus.PENDING
    assert is_closed("week", date(2026, 8, 3), today=date(2026, 8, 10)) is True
    assert is_closed("week", date(2026, 8, 10), today=date(2026, 8, 10)) is False
