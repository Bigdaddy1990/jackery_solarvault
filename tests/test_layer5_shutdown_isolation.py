"""Regression tests for HTTP-independent Layer-5 shutdown cleanup."""

import asyncio
from collections.abc import Coroutine
import logging
import time
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault import coordinator as coordinator_module
from custom_components.jackery_solarvault.const import CONF_ENABLE_BLE_TRANSPORT
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class _CancellationResistantTransport:
    """Transport double that proves timeout does not await cancellation."""

    def __init__(self, entered: asyncio.Event, release: asyncio.Event) -> None:
        self._entered = entered
        self._release = release
        self.stop_calls = 0

    async def async_stop(self) -> None:
        """Ignore task cancellation until the test explicitly releases cleanup."""
        self.stop_calls += 1
        self._entered.set()
        try:
            await self._release.wait()
        except asyncio.CancelledError:
            await self._release.wait()


def _bare_coordinator(hass: HomeAssistant) -> JackerySolarVaultCoordinator:
    """Create only the state required by the bounded Layer-5 shutdown helpers."""
    coordinator = JackerySolarVaultCoordinator.__new__(
        JackerySolarVaultCoordinator,
    )
    coordinator.hass = hass
    coordinator.entry = SimpleNamespace(entry_id="layer5-shutdown-test")
    coordinator._mqtt = None
    coordinator._ble_listener = None
    coordinator._ble_start_lock = asyncio.Lock()
    coordinator._layer5_stop_lock = asyncio.Lock()
    coordinator._layer5_stop_tasks = {}
    return coordinator


async def test_concurrent_layer5_stop_callers_share_one_successful_result(
    hass: HomeAssistant,
) -> None:
    """Concurrent shutdown and reaper calls cannot consume one task twice."""
    entered = asyncio.Event()
    release = asyncio.Event()

    class _Transport:
        def __init__(self) -> None:
            self.stop_calls = 0

        async def async_stop(self) -> None:
            self.stop_calls += 1
            entered.set()
            await release.wait()

    transport = _Transport()
    coordinator = _bare_coordinator(hass)
    coordinator._ble_listener = transport

    first = asyncio.create_task(coordinator._async_stop_layer5_transports())
    await asyncio.wait_for(entered.wait(), timeout=1.0)
    second = asyncio.create_task(coordinator._async_stop_layer5_transports())
    await asyncio.sleep(0)
    release.set()
    first_errors, second_errors = await asyncio.gather(first, second)

    assert first_errors == []
    assert second_errors == []
    assert transport.stop_calls == 1
    assert coordinator._ble_listener is None


async def test_layer5_stop_is_hard_bounded_and_single_flight(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cancellation-resistant stop returns on time and is never duplicated."""
    entered = asyncio.Event()
    release = asyncio.Event()
    transport = _CancellationResistantTransport(entered, release)
    coordinator = _bare_coordinator(hass)
    coordinator._mqtt = transport
    monkeypatch.setattr(
        coordinator_module,
        "_BACKGROUND_TASK_STOP_TIMEOUT_SEC",
        0.01,
    )

    async def _release_later() -> None:
        await asyncio.sleep(0.2)
        release.set()

    release_task = asyncio.create_task(_release_later())
    started = time.monotonic()
    first_errors = await coordinator._async_stop_layer5_transports()
    elapsed = time.monotonic() - started
    second_errors = await coordinator._async_stop_layer5_transports()

    assert elapsed < 0.1
    assert transport.stop_calls == 1
    assert first_errors
    assert second_errors
    assert coordinator._mqtt is transport

    await release_task
    await asyncio.sleep(0)
    assert await coordinator._async_stop_layer5_transports() == []
    assert coordinator._mqtt is None


async def test_primary_shutdown_does_not_fail_on_supplemental_stop_error(
    hass: HomeAssistant,
    caplog: Any,
) -> None:
    """A stopped HTTP coordinator remains releasable while Layer 5 retries later."""
    coordinator = _bare_coordinator(hass)
    coordinator._mqtt = object()
    coordinator._shutdown_started = False
    coordinator._poll_watchdog_unsub = None
    coordinator._active_http_update_tasks = set()
    coordinator._async_flush_payload_debug_events = AsyncMock(return_value=None)
    coordinator._supplemental_transport_tasks = MagicMock(return_value=set())
    coordinator._retain_pending_supplemental_tasks = MagicMock()
    coordinator._async_stop_layer5_transports = AsyncMock(
        return_value=["MQTT stop is still pending"]
    )
    base_shutdown = hass.async_create_background_task(
        asyncio.sleep(0),
        name="completed-primary-http-shutdown",
        eager_start=False,
    )
    await base_shutdown
    coordinator._base_shutdown_task = base_shutdown

    with caplog.at_level(
        logging.WARNING,
        logger="custom_components.jackery_solarvault.coordinator",
    ):
        await coordinator.async_shutdown()

    assert coordinator.has_pending_supplemental_transport_cleanup is True
    assert "MQTT stop is still pending" in caplog.text


async def test_primary_shutdown_remains_retryable_while_ble_drain_is_pending(
    hass: HomeAssistant,
) -> None:
    """Accepted BLE FIFO work must commit before shutdown reports success."""
    coordinator = _bare_coordinator(hass)
    coordinator._ble_listener = object()
    coordinator._shutdown_started = False
    coordinator._poll_watchdog_unsub = None
    coordinator._active_http_update_tasks = set()
    coordinator._async_flush_payload_debug_events = AsyncMock(return_value=None)
    coordinator._supplemental_transport_tasks = MagicMock(return_value=set())
    coordinator._retain_pending_supplemental_tasks = MagicMock()
    coordinator._async_stop_layer5_transports = AsyncMock(
        return_value=["BLE stop still pending after 2.0s"]
    )
    base_shutdown = hass.async_create_background_task(
        asyncio.sleep(0),
        name="completed-primary-http-shutdown-with-ble-drain",
        eager_start=False,
    )
    await base_shutdown
    coordinator._base_shutdown_task = base_shutdown

    with pytest.raises(RuntimeError, match="BLE stop still pending"):
        await coordinator.async_shutdown()

    assert coordinator._ble_shutdown_drain_active is True
    assert coordinator._ble_listener is not None


async def test_shutdown_waits_for_inflight_ble_start_before_transport_snapshot(
    hass: HomeAssistant,
) -> None:
    """Shutdown cannot miss a BLE listener published by an in-flight start."""
    start_holds_lock = asyncio.Event()
    release_start = asyncio.Event()

    class _Transport:
        def __init__(self) -> None:
            self.stop_calls = 0

        async def async_stop(self) -> None:
            self.stop_calls += 1

    transport = _Transport()
    coordinator = _bare_coordinator(hass)
    coordinator._shutdown_started = False
    coordinator._poll_watchdog_unsub = None
    coordinator._active_http_update_tasks = set()
    coordinator._async_flush_payload_debug_events = AsyncMock(return_value=None)
    coordinator._supplemental_transport_tasks = MagicMock(return_value=set())
    coordinator._retain_pending_supplemental_tasks = MagicMock()
    base_shutdown = hass.async_create_background_task(
        asyncio.sleep(0),
        name="completed-primary-http-shutdown-with-inflight-ble-start",
        eager_start=False,
    )
    await base_shutdown
    coordinator._base_shutdown_task = base_shutdown

    async def _finish_start() -> None:
        async with coordinator._ble_start_lock:
            start_holds_lock.set()
            await release_start.wait()
            coordinator._ble_listener = transport

    start_task = asyncio.create_task(_finish_start())
    await asyncio.wait_for(start_holds_lock.wait(), timeout=1.0)
    shutdown_task = asyncio.create_task(coordinator.async_shutdown())
    try:
        await asyncio.sleep(0)
        assert coordinator._shutdown_started is True
        assert not shutdown_task.done()
    finally:
        release_start.set()

    await asyncio.wait_for(start_task, timeout=1.0)
    await asyncio.wait_for(shutdown_task, timeout=1.0)
    assert transport.stop_calls == 1
    assert coordinator._ble_listener is None
    assert coordinator._ble_shutdown_drain_active is False


async def test_ble_start_rechecks_shutdown_after_executor_import() -> None:
    """A shutdown fence raised during import prevents a new BLE listener start."""
    import_started = asyncio.Event()
    release_import = asyncio.Event()
    constructed = 0

    class _Listener:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            nonlocal constructed
            constructed += 1

        async def async_start(  # ruff: ignore[no-self-use]
            self,
            _device_ids: list[str],
        ) -> None:
            raise AssertionError("post-fence BLE listener must not start")

    async def _async_add_executor_job(
        _call: object,
        *_args: object,
    ) -> object:
        import_started.set()
        await release_import.wait()
        return SimpleNamespace(JackeryBleListener=_Listener)

    coordinator = JackerySolarVaultCoordinator.__new__(
        JackerySolarVaultCoordinator,
    )
    coordinator.hass = SimpleNamespace(
        async_add_executor_job=_async_add_executor_job,
    )
    coordinator.entry = SimpleNamespace(
        entry_id="ble-start-shutdown-race",
        data={},
        options={CONF_ENABLE_BLE_TRANSPORT: True},
    )
    coordinator._ble_start_lock = asyncio.Lock()
    coordinator._ble_listener = None
    coordinator._shutdown_started = False
    coordinator._device_index = {"dev": {}}
    coordinator._ble_connect_backoff = {}

    start_task = asyncio.create_task(coordinator.async_start_ble_transport())
    await asyncio.wait_for(import_started.wait(), timeout=1.0)
    coordinator._shutdown_started = True
    release_import.set()
    await asyncio.wait_for(start_task, timeout=1.0)

    assert constructed == 0
    assert coordinator._ble_listener is None


async def test_partial_ble_start_cleanup_failure_retains_exact_owner() -> None:
    """A partly started listener is retained when its cleanup cannot finish."""
    scheduled_retries = 0

    class _Listener:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return

        async def async_start(  # ruff: ignore[no-self-use]
            self,
            _device_ids: list[str],
        ) -> None:
            raise RuntimeError("partial start failed")

        async def async_stop(self) -> None:  # ruff: ignore[no-self-use]
            raise RuntimeError("partial cleanup failed")

    async def _async_add_executor_job(  # ruff: ignore[unused-async]
        _call: object,
        *_args: object,
    ) -> object:
        return SimpleNamespace(JackeryBleListener=_Listener)

    coordinator = JackerySolarVaultCoordinator.__new__(
        JackerySolarVaultCoordinator,
    )
    coordinator.hass = SimpleNamespace(
        async_add_executor_job=_async_add_executor_job,
    )
    coordinator.entry = SimpleNamespace(
        entry_id="ble-partial-start-failure",
        data={},
        options={CONF_ENABLE_BLE_TRANSPORT: True},
    )
    coordinator._ble_start_lock = asyncio.Lock()
    coordinator._ble_listener = None
    coordinator._shutdown_started = False
    coordinator._device_index = {"dev": {}}
    coordinator._ble_connect_backoff = {}

    def _schedule_retry() -> None:
        nonlocal scheduled_retries
        scheduled_retries += 1

    coordinator._schedule_ble_start_retry = _schedule_retry  # type: ignore[method-assign]

    await coordinator.async_start_ble_transport()

    assert isinstance(coordinator._ble_listener, _Listener)
    assert scheduled_retries == 0


async def test_layer5_stop_task_factory_failure_keeps_transport_retryable() -> None:
    """Rejected HA task creation closes coroutines and preserves stop ownership."""
    created_wrapper: Coroutine[Any, Any, None] | None = None

    class _RejectingHass:
        def async_create_background_task(  # ruff: ignore[no-self-use]
            self,
            target: Coroutine[Any, Any, None],
            **_kwargs: object,
        ) -> asyncio.Task[None]:
            nonlocal created_wrapper
            created_wrapper = target
            raise RuntimeError("task factory rejected")

    class _Transport:
        def __init__(self) -> None:
            self.operation: Coroutine[Any, Any, None] | None = None

        async def _async_stop(self) -> None:  # ruff: ignore[no-self-use]
            return

        def async_stop(self) -> Coroutine[Any, Any, None]:
            self.operation = self._async_stop()
            return self.operation

    transport = _Transport()
    coordinator = JackerySolarVaultCoordinator.__new__(
        JackerySolarVaultCoordinator,
    )
    coordinator.hass = _RejectingHass()
    coordinator.entry = SimpleNamespace(entry_id="rejected-stop-owner")
    coordinator._mqtt = transport
    coordinator._ble_listener = None
    coordinator._layer5_stop_tasks = {}

    errors = await coordinator._async_stop_layer5_transports()

    assert errors == ["MQTT stop task creation failed: task factory rejected"]
    assert coordinator._mqtt is transport
    assert created_wrapper is not None and created_wrapper.cr_frame is None
    assert transport.operation is not None and transport.operation.cr_frame is None
