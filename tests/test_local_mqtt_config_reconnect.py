"""Behavioral regressions for local-MQTT device configuration retries."""

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine

_EXPECTED_CONFIG_PUSH_COUNT = 2


class _TaskHass:
    """Minimal HA task factory used by the coordinator background scheduler."""

    def async_create_background_task(  # ruff: ignore[no-self-use]
        self,
        coro: Coroutine[object, None, object],
        *,
        name: str,
        eager_start: bool,
    ) -> asyncio.Task[object]:
        """Create a named event-loop task without requiring a full HA fixture."""
        del eager_start
        return asyncio.create_task(coro, name=name)


@pytest.mark.asyncio()
async def test_reconnect_during_config_push_replays_without_overlap() -> None:
    """A second cloud connect coalesces one post-flight 3046/BLE-113 retry."""
    coordinator = JackerySolarVaultCoordinator.__new__(
        JackerySolarVaultCoordinator,
    )
    obj = cast("Any", coordinator)
    obj._shutdown_started = False  # ruff: ignore[private-member-access]
    obj._background_tasks = {}  # ruff: ignore[private-member-access]
    obj._local_mqtt_config_retry_pending = False  # ruff: ignore[private-member-access]
    obj.hass = _TaskHass()
    obj._mqtt = object()  # ruff: ignore[private-member-access]
    mqtt_mgr = MagicMock()
    obj._mqtt_mgr = mqtt_mgr  # ruff: ignore[private-member-access]
    obj.api = SimpleNamespace(mqtt_fingerprint=("client", "host", "session"))
    obj._async_query_system_info_for_missing = AsyncMock(  # ruff: ignore[private-member-access]
        return_value=None,
    )
    obj._async_query_weather_plan_for_missing = AsyncMock(  # ruff: ignore[private-member-access]
        return_value=None,
    )
    obj._async_query_subdevices_for_missing = AsyncMock(  # ruff: ignore[private-member-access]
        return_value=None,
    )

    first_started = asyncio.Event()
    release_first = asyncio.Event()
    call_count = 0
    active = 0
    max_active = 0

    async def _apply() -> None:
        nonlocal active, call_count, max_active
        call_count += 1
        active += 1
        max_active = max(max_active, active)
        try:
            if call_count == 1:
                first_started.set()
                await release_first.wait()
        finally:
            active -= 1

    obj.async_apply_local_mqtt_config_to_devices = _apply

    await coordinator._async_mqtt_connected()  # ruff: ignore[private-member-access]
    await first_started.wait()
    first_task = obj._background_tasks["local_mqtt_device_config"]  # ruff: ignore[private-member-access]

    await coordinator._async_mqtt_connected()  # ruff: ignore[private-member-access]
    assert call_count == 1

    release_first.set()
    await first_task

    assert call_count == _EXPECTED_CONFIG_PUSH_COUNT
    assert max_active == 1
    mqtt_mgr.record_connect_success.assert_called()


@pytest.mark.asyncio()
async def test_failed_config_push_retries_without_cloud_reconnect() -> None:
    """A transient 3046/BLE-113 failure retries without requiring reconnect."""
    coordinator = JackerySolarVaultCoordinator.__new__(
        JackerySolarVaultCoordinator,
    )
    obj = cast("Any", coordinator)
    obj._shutdown_started = False  # ruff: ignore[private-member-access]
    obj._background_tasks = {}  # ruff: ignore[private-member-access]
    obj._local_mqtt_config_retry_pending = False  # ruff: ignore[private-member-access]
    obj.hass = _TaskHass()
    retry_sleep = AsyncMock()
    obj._async_local_mqtt_config_retry_sleep = retry_sleep  # ruff: ignore[private-member-access]
    outcomes = iter((False, True))
    apply = AsyncMock(side_effect=lambda: next(outcomes))
    obj.async_apply_local_mqtt_config_to_devices = apply

    task = coordinator.async_schedule_local_mqtt_device_config()
    assert task is not None
    await task

    assert apply.await_count == _EXPECTED_CONFIG_PUSH_COUNT
    retry_sleep.assert_awaited_once()


def _rediscovery_coordinator(*, connected: bool) -> tuple[Any, MagicMock]:
    """Build the runtime-discovery slice needed by the cmd=113 lifecycle test."""
    coordinator = JackerySolarVaultCoordinator.__new__(
        JackerySolarVaultCoordinator,
    )
    obj = cast("Any", coordinator)
    obj._last_discovery_refresh_monotonic = float("-inf")  # ruff: ignore[private-member-access]
    obj._slow_metrics_interval_sec = 60  # ruff: ignore[private-member-access]
    obj._device_index = {"old-device": {}}  # ruff: ignore[private-member-access]
    obj._mqtt = SimpleNamespace(is_connected=connected)  # ruff: ignore[private-member-access]
    schedule = MagicMock()
    obj.async_schedule_local_mqtt_device_config = schedule
    return obj, schedule


@pytest.mark.asyncio()
async def test_runtime_discovery_pushes_config_to_new_device_when_connected() -> None:
    """A device added after startup receives 3046/BLE-113 without reconnect."""
    coordinator, schedule = _rediscovery_coordinator(connected=True)

    async def _discover() -> bool:
        await asyncio.sleep(0)
        coordinator._device_index["new-device"] = {}  # ruff: ignore[private-member-access]
        return True

    coordinator.async_discover = _discover

    await coordinator._async_refresh_discovery_if_due()  # ruff: ignore[private-member-access]

    schedule.assert_called_once_with()


@pytest.mark.asyncio()
async def test_runtime_discovery_schedules_config_while_cloud_disconnected() -> None:
    """BLE can configure a newly discovered device without Cloud-MQTT state."""
    coordinator, schedule = _rediscovery_coordinator(connected=False)

    async def _discover() -> bool:
        await asyncio.sleep(0)
        coordinator._device_index["new-device"] = {}  # ruff: ignore[private-member-access]
        return True

    coordinator.async_discover = _discover

    await coordinator._async_refresh_discovery_if_due()  # ruff: ignore[private-member-access]

    schedule.assert_called_once_with()


@pytest.mark.asyncio()
async def test_runtime_discovery_without_new_device_does_not_push_config() -> None:
    """An unchanged discovery snapshot does not emit redundant cmd=113 work."""
    coordinator, schedule = _rediscovery_coordinator(connected=True)
    coordinator.async_discover = AsyncMock(return_value=True)

    await coordinator._async_refresh_discovery_if_due()  # ruff: ignore[private-member-access]

    schedule.assert_not_called()
