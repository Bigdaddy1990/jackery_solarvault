"""Unload never hangs on a stuck transport teardown (owner live capture 2026-07-05).

The unload log showed ~78 s between "startup task cancelled during teardown"
and the next setup — the entry was wedged in ``coordinator.async_shutdown()``
(an un-bounded bleak GATT disconnect / aiomqtt close / getaddrinfo-stuck task).
Every options-reload therefore froze polling for that whole window. The
shutdown call is now time-bounded; a hang becomes a warning and the unload
proceeds.
"""

import asyncio
import contextlib
import logging
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import jackery_solarvault as integration
from custom_components.jackery_solarvault import async_unload_entry
from custom_components.jackery_solarvault.const import DOMAIN
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

_MODULE = "custom_components.jackery_solarvault"


def _assert_ha_background_task(hass, task: asyncio.Task[object]) -> None:  # ruff: ignore[missing-type-function-argument]
    """Assert Home Assistant will not wait for ``task`` at its idle barrier."""
    assert task in hass._background_tasks  # ruff: ignore[private-member-access]
    assert task not in hass._tasks  # ruff: ignore[private-member-access]


@pytest.mark.asyncio()
async def test_unload_bounds_a_hung_shutdown() -> None:
    """A shutdown that never returns must not block the unload."""
    coordinator = MagicMock(spec=JackerySolarVaultCoordinator)

    async def _hang() -> None:
        await asyncio.sleep(1)

    coordinator.async_shutdown = _hang

    entry = MagicMock()
    entry.entry_id = "entry-1"
    entry.runtime_data = coordinator

    hass = MagicMock()
    hass.data = {DOMAIN: {}}
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)

    with patch(f"{_MODULE}.COORDINATOR_SHUTDOWN_TIMEOUT_SEC", 0.01):
        result = await async_unload_entry(hass, entry)

    assert result is True
    hass.config_entries.async_unload_platforms.assert_awaited_once()


@pytest.mark.asyncio()
async def test_bounded_shutdown_owns_cancellation_resistant_task(
    hass,  # ruff: ignore[missing-type-function-argument]
) -> None:
    """A shutdown that consumes cancellation cannot hold its caller hostage."""
    coordinator = MagicMock(spec=JackerySolarVaultCoordinator)
    coordinator.has_pending_supplemental_transport_cleanup = False
    release = asyncio.Event()
    entered = asyncio.Event()

    async def _resist_cancellation() -> None:
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    coordinator.async_shutdown = AsyncMock(side_effect=_resist_cancellation)
    entry = MagicMock()
    entry.entry_id = "hard-bounded-shutdown"
    entry.runtime_data = coordinator
    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = {}
    started = time.monotonic()
    try:
        with patch(f"{_MODULE}.COORDINATOR_SHUTDOWN_TIMEOUT_SEC", 0.01):
            result = await integration._async_shutdown_coordinator_bounded(  # ruff: ignore[private-member-access]
                coordinator,
                context="cancellation-resistant test",
                hass=hass,
                entry=entry,
            )

        assert result is False
        assert time.monotonic() - started < 0.1  # ruff: ignore[magic-value-comparison]
        await asyncio.wait_for(entered.wait(), timeout=1)
        bucket = integration._entry_runtime_bucket(hass, entry)  # ruff: ignore[private-member-access]
        record = bucket[integration._COORDINATOR_SHUTDOWN_RUNTIME_KEY]  # ruff: ignore[private-member-access]
        _assert_ha_background_task(hass, record[1])
    finally:
        release.set()
        await hass.async_block_till_done(wait_background_tasks=True)


@pytest.mark.asyncio()
async def test_bounded_shutdown_reaper_releases_fence_after_real_completion(
    hass,  # ruff: ignore[missing-type-function-argument]
) -> None:
    """The owned shutdown is reaped after unload without requiring another setup."""
    coordinator = MagicMock(spec=JackerySolarVaultCoordinator)
    coordinator.has_pending_supplemental_transport_cleanup = False
    release = asyncio.Event()
    entered = asyncio.Event()

    async def _finish_after_release() -> None:
        entered.set()
        await release.wait()

    coordinator.async_shutdown = AsyncMock(side_effect=_finish_after_release)
    entry = MagicMock()
    entry.entry_id = "reaped-bounded-shutdown"
    entry.runtime_data = coordinator
    bucket = hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})
    bucket[integration._UNLOADING_COORDINATOR_RUNTIME_KEY] = coordinator  # ruff: ignore[private-member-access]

    with patch(f"{_MODULE}.COORDINATOR_SHUTDOWN_TIMEOUT_SEC", 0.01):
        assert not await integration._async_shutdown_coordinator_bounded(  # ruff: ignore[private-member-access]
            coordinator,
            context="background reaper test",
            hass=hass,
            entry=entry,
        )
    await asyncio.wait_for(entered.wait(), timeout=1)
    assert integration._COORDINATOR_SHUTDOWN_RUNTIME_KEY in bucket  # ruff: ignore[private-member-access]
    assert integration._COORDINATOR_SHUTDOWN_REAPER_TASK_RUNTIME_KEY in bucket  # ruff: ignore[private-member-access]

    release.set()
    reaper = bucket[integration._COORDINATOR_SHUTDOWN_REAPER_TASK_RUNTIME_KEY]  # ruff: ignore[private-member-access]
    await asyncio.wait_for(asyncio.shield(reaper), timeout=1)
    await asyncio.sleep(0)

    assert integration._COORDINATOR_SHUTDOWN_RUNTIME_KEY not in bucket  # ruff: ignore[private-member-access]
    assert integration._COORDINATOR_SHUTDOWN_REAPER_TASK_RUNTIME_KEY not in bucket  # ruff: ignore[private-member-access]
    assert integration._UNLOADING_COORDINATOR_RUNTIME_KEY not in bucket  # ruff: ignore[private-member-access]
    assert entry.runtime_data is None


@pytest.mark.asyncio()
async def test_failed_shutdown_is_retried_until_the_fence_can_be_released(
    hass,  # ruff: ignore[missing-type-function-argument]
) -> None:
    """An immediate shutdown exception must not leave cleanup for the next setup."""
    coordinator = MagicMock(spec=JackerySolarVaultCoordinator)
    coordinator.has_pending_supplemental_transport_cleanup = False
    attempts = 0

    async def _fail_once() -> None:
        nonlocal attempts
        await asyncio.sleep(0)
        attempts += 1
        if attempts == 1:
            raise RuntimeError("first shutdown failed")  # ruff: ignore[raise-vanilla-args]

    coordinator.async_shutdown = AsyncMock(side_effect=_fail_once)
    entry = MagicMock()
    entry.entry_id = "retry-immediate-shutdown"
    entry.runtime_data = coordinator
    bucket = hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})
    bucket[integration._UNLOADING_COORDINATOR_RUNTIME_KEY] = coordinator  # ruff: ignore[private-member-access]

    with patch.object(
        integration,
        "_COORDINATOR_SHUTDOWN_RETRY_SEC",
        0.0,
        create=True,
    ):
        assert not await integration._async_shutdown_coordinator_bounded(  # ruff: ignore[private-member-access]
            coordinator,
            context="retry immediate failure",
            hass=hass,
            entry=entry,
        )
        reaper = bucket[integration._COORDINATOR_SHUTDOWN_REAPER_TASK_RUNTIME_KEY]  # ruff: ignore[private-member-access]
        await asyncio.wait_for(asyncio.shield(reaper), timeout=1)

    assert attempts == 2  # ruff: ignore[magic-value-comparison]
    assert integration._COORDINATOR_SHUTDOWN_RUNTIME_KEY not in bucket  # ruff: ignore[private-member-access]
    assert integration._UNLOADING_COORDINATOR_RUNTIME_KEY not in bucket  # ruff: ignore[private-member-access]
    assert entry.runtime_data is None


@pytest.mark.asyncio()
async def test_timed_out_shutdown_retries_after_late_and_immediate_failures(
    hass,  # ruff: ignore[missing-type-function-argument]
) -> None:
    """A timed-out attempt remains single-flight and retries after it later fails."""
    coordinator = MagicMock(spec=JackerySolarVaultCoordinator)
    coordinator.has_pending_supplemental_transport_cleanup = False
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    attempts = 0

    async def _fail_twice() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            first_started.set()
            await release_first.wait()
            raise RuntimeError("late first failure")  # ruff: ignore[raise-vanilla-args]
        if attempts == 2:  # ruff: ignore[magic-value-comparison]
            raise RuntimeError("immediate retry failure")  # ruff: ignore[raise-vanilla-args]

    coordinator.async_shutdown = AsyncMock(side_effect=_fail_twice)
    entry = MagicMock()
    entry.entry_id = "retry-timeout-shutdown"
    entry.runtime_data = coordinator
    bucket = hass.data.setdefault(DOMAIN, {}).setdefault(entry.entry_id, {})
    bucket[integration._UNLOADING_COORDINATOR_RUNTIME_KEY] = coordinator  # ruff: ignore[private-member-access]

    with (
        patch(f"{_MODULE}.COORDINATOR_SHUTDOWN_TIMEOUT_SEC", 0.01),
        patch.object(
            integration,
            "_COORDINATOR_SHUTDOWN_RETRY_SEC",
            0.0,
            create=True,
        ),
    ):
        assert not await integration._async_shutdown_coordinator_bounded(  # ruff: ignore[private-member-access]
            coordinator,
            context="retry timeout failure",
            hass=hass,
            entry=entry,
        )
        await asyncio.wait_for(first_started.wait(), timeout=1)
        reaper = bucket[integration._COORDINATOR_SHUTDOWN_REAPER_TASK_RUNTIME_KEY]  # ruff: ignore[private-member-access]
        release_first.set()
        await asyncio.wait_for(asyncio.shield(reaper), timeout=1)

    assert attempts == 3  # ruff: ignore[magic-value-comparison]
    assert integration._COORDINATOR_SHUTDOWN_RUNTIME_KEY not in bucket  # ruff: ignore[private-member-access]
    assert integration._UNLOADING_COORDINATOR_RUNTIME_KEY not in bucket  # ruff: ignore[private-member-access]
    assert entry.runtime_data is None


@pytest.mark.asyncio()
async def test_layer5_children_do_not_block_home_assistant_idle_barrier(hass) -> None:  # ruff: ignore[missing-type-function-argument]
    """Independent live transports must remain HA background work while starting."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="layer5-background")
    entry.add_to_hass(hass)
    coordinator = MagicMock(spec=JackerySolarVaultCoordinator)
    entry.runtime_data = coordinator
    release = asyncio.Event()
    all_started = asyncio.Event()
    starts = 0

    async def _block_start(*_args: object) -> None:
        nonlocal starts
        starts += 1
        if starts == 3:  # ruff: ignore[magic-value-comparison]
            all_started.set()
        await release.wait()

    coordinator.async_start_mqtt = AsyncMock(side_effect=_block_start)
    coordinator.async_start_ble_transport = AsyncMock(side_effect=_block_start)
    coordinator.async_schedule_local_mqtt_device_config = MagicMock()
    with patch.object(
        integration,
        "_async_start_local_mqtt",
        AsyncMock(side_effect=_block_start),
    ):
        outer = entry.async_create_background_task(
            hass,
            integration._async_start_layer5_transports(hass, entry, coordinator),  # ruff: ignore[private-member-access]
            name="test-layer5-owner",
            eager_start=False,
        )
        try:
            await asyncio.wait_for(all_started.wait(), timeout=1)
            child_names = {
                f"{DOMAIN}_cloud_mqtt_start_{entry.entry_id}",
                f"{DOMAIN}_local_mqtt_start_{entry.entry_id}",
                f"{DOMAIN}_ble_start_{entry.entry_id}",
            }
            children = {
                task for task in asyncio.all_tasks() if task.get_name() in child_names
            }
            assert len(children) == 3  # ruff: ignore[magic-value-comparison]
            for task in children:
                _assert_ha_background_task(hass, task)
        finally:
            release.set()
            await asyncio.wait_for(asyncio.shield(outer), timeout=1)


@pytest.mark.asyncio()
async def test_local_mqtt_stop_does_not_block_home_assistant_idle_barrier(hass) -> None:  # ruff: ignore[missing-type-function-argument]
    """A cancellation-resistant unsubscribe remains owned but never blocks startup."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="mqtt-stop-background")
    entry.add_to_hass(hass)
    entry.runtime_data = None
    client = MagicMock(spec=integration.JackeryLocalMqttClient)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _resist_cancellation() -> None:
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    client.async_stop = AsyncMock(side_effect=_resist_cancellation)
    bucket = integration._entry_runtime_bucket(hass, entry)  # ruff: ignore[private-member-access]
    try:
        with (
            patch.object(integration, "_ENTRY_TASK_CANCEL_TIMEOUT_SEC", 0.01),
            patch.object(integration, "_schedule_supplemental_cleanup"),
        ):
            assert not await integration._async_stop_local_mqtt_client(  # ruff: ignore[private-member-access]
                hass,
                entry,
                client,
            )
        await asyncio.wait_for(entered.wait(), timeout=1)
        records = bucket[integration._LOCAL_MQTT_STOP_TASKS_RUNTIME_KEY]  # ruff: ignore[private-member-access]
        _assert_ha_background_task(hass, records[id(client)][1])
    finally:
        release.set()
        records = bucket.get(integration._LOCAL_MQTT_STOP_TASKS_RUNTIME_KEY, {})  # ruff: ignore[private-member-access]
        record = records.get(id(client)) if isinstance(records, dict) else None
        if isinstance(record, tuple) and isinstance(record[1], asyncio.Task):
            await asyncio.wait_for(asyncio.shield(record[1]), timeout=1)


@pytest.mark.asyncio()
async def test_supplemental_cleanup_child_is_background_and_errors_are_visible(
    hass,  # ruff: ignore[missing-type-function-argument]
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Cleanup children neither block HA nor hide a failed cleanup operation."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="cleanup-background")
    entry.add_to_hass(hass)
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _resist_cancellation() -> None:
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    try:
        with patch.object(integration, "_ENTRY_TASK_CANCEL_TIMEOUT_SEC", 0.01):
            assert not await integration._async_run_supplemental_cleanup_call(  # ruff: ignore[private-member-access]
                hass,
                entry,
                _resist_cancellation(),
                name="blocked supplemental cleanup",
            )
        await asyncio.wait_for(entered.wait(), timeout=1)
        bucket = integration._entry_runtime_bucket(hass, entry)  # ruff: ignore[private-member-access]
        retained = integration._supplemental_runtime_items(  # ruff: ignore[private-member-access]
            bucket,
            integration._SUPPLEMENTAL_LAYER5_TASKS_RUNTIME_KEY,  # ruff: ignore[private-member-access]
        )
        assert len(retained) == 1
        retained_task = retained[0]
        assert isinstance(retained_task, asyncio.Task)
        _assert_ha_background_task(hass, retained_task)
    finally:
        release.set()
        bucket = integration._entry_runtime_bucket(hass, entry)  # ruff: ignore[private-member-access]
        retained = integration._supplemental_runtime_items(  # ruff: ignore[private-member-access]
            bucket,
            integration._SUPPLEMENTAL_LAYER5_TASKS_RUNTIME_KEY,  # ruff: ignore[private-member-access]
        )
        for task in retained:
            if isinstance(task, asyncio.Task):
                await asyncio.wait_for(asyncio.shield(task), timeout=1)

    async def _fail() -> None:
        await asyncio.sleep(0)
        raise RuntimeError("visible cleanup failure")  # ruff: ignore[raise-vanilla-args]

    caplog.set_level(logging.WARNING)
    assert not await integration._async_run_supplemental_cleanup_call(  # ruff: ignore[private-member-access]
        hass,
        entry,
        _fail(),
        name="failing supplemental cleanup",
    )
    assert "failing supplemental cleanup" in caplog.text
    assert "RuntimeError" in caplog.text


@pytest.mark.asyncio()
async def test_late_supplemental_cleanup_failure_is_logged_before_retry(
    hass,  # ruff: ignore[missing-type-function-argument]
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A timed-out child stays single-flight and reports its eventual failure."""
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="late-cleanup-failure")
    entry.add_to_hass(hass)
    entry.runtime_data = None
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _fail_after_release() -> None:
        entered.set()
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue
        raise RuntimeError("late cleanup failure")  # ruff: ignore[raise-vanilla-args]

    coordinator = MagicMock(spec=JackerySolarVaultCoordinator)
    coordinator.async_stop_supplemental_transports = AsyncMock(return_value=None)
    coordinator.has_pending_supplemental_transport_cleanup = False
    bucket = integration._entry_runtime_bucket(hass, entry)  # ruff: ignore[private-member-access]
    integration._set_supplemental_runtime_items(  # ruff: ignore[private-member-access]
        bucket,
        integration._SUPPLEMENTAL_TRANSPORT_COORDINATORS_RUNTIME_KEY,  # ruff: ignore[private-member-access]
        [coordinator],
    )

    caplog.set_level(logging.WARNING)
    with (
        patch.object(integration, "_ENTRY_TASK_CANCEL_TIMEOUT_SEC", 0.01),
        patch.object(integration, "_SUPPLEMENTAL_CLEANUP_RETRY_SEC", 0.001),
    ):
        assert not await integration._async_run_supplemental_cleanup_call(  # ruff: ignore[private-member-access]
            hass,
            entry,
            _fail_after_release(),
            name="late cleanup operation",
        )
        await asyncio.wait_for(entered.wait(), timeout=1)
        reaper = hass.async_create_background_task(
            integration._async_cleanup_stale_supplemental(hass, entry),  # ruff: ignore[private-member-access]
            name="test late supplemental cleanup reaper",
            eager_start=False,
        )
        await asyncio.sleep(0.03)
        coordinator.async_stop_supplemental_transports.assert_not_awaited()
        release.set()
        await asyncio.wait_for(asyncio.shield(reaper), timeout=1)

    coordinator.async_stop_supplemental_transports.assert_awaited_once_with()
    assert caplog.text.count("late cleanup failure") == 1
    assert "late cleanup operation" in caplog.text
    assert entry.entry_id in caplog.text


@pytest.mark.asyncio()
async def test_completed_supplemental_task_handles_are_reaped(hass) -> None:  # ruff: ignore[missing-type-function-argument]
    """A completed retained task must not keep the five-second reaper alive."""
    coordinator = JackerySolarVaultCoordinator.__new__(
        JackerySolarVaultCoordinator,
    )
    completed = hass.async_create_background_task(
        asyncio.sleep(0),
        name="completed-supplemental-task",
        eager_start=False,
    )
    await completed
    coordinator._statistics_import_task = None  # ruff: ignore[private-member-access]
    coordinator._statistics_backfill_task = None  # ruff: ignore[private-member-access]
    coordinator._slow_metrics_bg_task = completed  # ruff: ignore[private-member-access]
    coordinator._mqtt_poll_task = None  # ruff: ignore[private-member-access]
    coordinator._shadow_fallback_task = None  # ruff: ignore[private-member-access]
    coordinator._battery_pack_ota_tasks = {}  # ruff: ignore[private-member-access]
    coordinator._background_tasks = {}  # ruff: ignore[private-member-access]

    assert coordinator.has_pending_supplemental_transport_cleanup is False
    assert coordinator._slow_metrics_bg_task is None  # ruff: ignore[private-member-access]

    with contextlib.suppress(asyncio.CancelledError):
        await completed


@pytest.mark.asyncio()
async def test_parallel_local_mqtt_reconcile_starts_only_one_subscriber(hass) -> None:  # ruff: ignore[missing-type-function-argument]
    """Initial Layer 5 and an options reconcile share one subscriber start."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            "third_party_mqtt_enable": True,
            "third_party_mqtt_ip": "192.0.2.10",
        },
        entry_id="local-mqtt-start-singleflight",
    )
    entry.add_to_hass(hass)
    coordinator = MagicMock(spec=JackerySolarVaultCoordinator)
    coordinator.local_mqtt_client = None
    coordinator.set_local_mqtt_client = MagicMock(
        side_effect=lambda client: setattr(coordinator, "local_mqtt_client", client)
    )
    entry.runtime_data = coordinator
    first_started = asyncio.Event()
    release = asyncio.Event()
    starts = 0

    async def _blocking_start(_client) -> None:  # ruff: ignore[missing-type-function-argument]
        nonlocal starts
        starts += 1
        _client._subscription_active = True  # ruff: ignore[private-member-access]
        _client._runner_task = MagicMock()  # ruff: ignore[private-member-access]
        _client._runner_task.done.return_value = False  # ruff: ignore[private-member-access]
        first_started.set()
        await release.wait()

    async def _fast_stop(_client) -> None:  # ruff: ignore[missing-type-function-argument]
        await asyncio.sleep(0)

    with (
        patch.object(
            integration.JackeryLocalMqttClient,
            "async_start",
            _blocking_start,
        ),
        patch.object(
            integration.JackeryLocalMqttClient,
            "async_stop",
            _fast_stop,
        ),
    ):
        first = hass.async_create_task(
            integration._async_start_local_mqtt(hass, entry, coordinator),  # ruff: ignore[private-member-access]
            "first-local-mqtt-reconcile",
        )
        second: asyncio.Task[None] | None = None
        try:
            await asyncio.wait_for(first_started.wait(), timeout=1)
            second = hass.async_create_task(
                integration._async_start_local_mqtt(hass, entry, coordinator),  # ruff: ignore[private-member-access]
                "second-local-mqtt-reconcile",
            )
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            assert starts == 1
        finally:
            release.set()
            pending = [task for task in (first, second) if task is not None]
            await asyncio.gather(*pending, return_exceptions=True)

    assert starts == 1
