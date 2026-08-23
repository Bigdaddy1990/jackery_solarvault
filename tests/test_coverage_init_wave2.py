"""Lifecycle coverage for the Jackery config-entry integration boundary."""

import asyncio
import contextlib
from datetime import timedelta
import time
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import jackery_solarvault as integration
from custom_components.jackery_solarvault.const import (
    CONF_CREATE_CALCULATED_POWER_SENSORS,
    CONF_ENABLE_BLE_TRANSPORT,
    CONF_SCAN_INTERVAL,
    CONF_THIRD_PARTY_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_ENABLE as CONF_LOCAL_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_IP as CONF_LOCAL_MQTT_HOST,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_PASSWORD as CONF_LOCAL_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_PORT,
    CONF_THIRD_PARTY_MQTT_PORT as CONF_LOCAL_MQTT_PORT,
    CONF_THIRD_PARTY_MQTT_QOS,
    CONF_THIRD_PARTY_MQTT_TOKEN,
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER as CONF_LOCAL_MQTT_TOPIC,
    CONF_THIRD_PARTY_MQTT_USERNAME,
    CONF_THIRD_PARTY_MQTT_USERNAME as CONF_LOCAL_MQTT_USERNAME,
    DOMAIN,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import device_registry as dr

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _entry(
    hass: HomeAssistant,
    *,
    entry_id: str,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Create and register one integration entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_USERNAME: "owner@example.com", CONF_PASSWORD: "secret"},
        options=options or {},
        entry_id=entry_id,
    )
    entry.add_to_hass(hass)
    return entry


async def test_setup_adopts_confirmed_device_mqtt_config_in_place(
    hass: HomeAssistant,
) -> None:
    """A valid 3047 readback updates options without reloading primary HTTP."""
    entry = _entry(
        hass,
        entry_id="adopt-device-mqtt",
        options={CONF_LOCAL_MQTT_TOPIC: "jackery/local/device"},
    )
    api = MagicMock(name="api")
    coordinator = MagicMock(name="coordinator")
    coordinator.data = {}
    forward = AsyncMock(return_value=None)

    # Configure coordinator async methods to return proper awaitables
    async def noop() -> None:  # noqa: RUF029
        return None

    coordinator.async_start_mqtt = AsyncMock(side_effect=noop)
    coordinator.async_start_local_mqtt_listener = AsyncMock(side_effect=noop)
    coordinator.async_start_ble_transport = AsyncMock(side_effect=noop)
    coordinator.async_apply_local_mqtt_config_to_devices = AsyncMock(side_effect=noop)

    with (
        patch.object(integration, "async_get_clientsession", return_value=MagicMock()),
        patch.object(integration, "JackeryApi", return_value=api),
        patch.object(
            integration,
            "JackerySolarVaultCoordinator",
            return_value=coordinator,
        ),
        patch.object(
            integration,
            "_async_release_fenced_coordinator",
            AsyncMock(return_value=True),
        ),
        patch.object(integration, "_async_prune_removed_local_mqtt_tls_options"),
        patch.object(
            integration,
            "_async_load_entry_caches",
            AsyncMock(return_value=False),
        ),
        patch.object(
            integration,
            "_async_prepare_primary_http",
            AsyncMock(return_value=None),
        ),
        patch.object(integration, "_async_clean_legacy_entities"),
        patch.object(integration, "_async_remove_legacy_system_parent_devices"),
        patch.object(hass.config_entries, "async_forward_entry_setups", forward),
    ):
        assert await integration.async_setup_entry(hass, entry) is True

    observer = coordinator.set_local_mqtt_config_observer.call_args.args[0]
    assert callable(observer)
    schedule = MagicMock()
    with (
        patch.object(integration, "_schedule_layer5_start_if_ready", schedule),
        patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload_entry,
    ):
        observer({
            "enable": 1,
            "ip": "192.168.2.212",
            "port": 1883,
            "userName": "bridge-user",
            "password": "bridge-pass",
            "token": "device-token",
        })
        await hass.async_block_till_done()

    assert entry.options == {
        CONF_LOCAL_MQTT_TOPIC: "jackery/local/device",
        CONF_LOCAL_MQTT_ENABLE: True,
        CONF_THIRD_PARTY_MQTT_ENABLE: True,
        CONF_LOCAL_MQTT_HOST: "192.168.2.212",
        CONF_LOCAL_MQTT_PORT: 1883,
        CONF_LOCAL_MQTT_USERNAME: "bridge-user",
        CONF_LOCAL_MQTT_PASSWORD: "bridge-pass",
        CONF_THIRD_PARTY_MQTT_IP: "192.168.2.212",
        CONF_THIRD_PARTY_MQTT_PORT: 1883,
        CONF_THIRD_PARTY_MQTT_USERNAME: "bridge-user",
        CONF_THIRD_PARTY_MQTT_PASSWORD: "bridge-pass",
        CONF_THIRD_PARTY_MQTT_TOKEN: "device-token",
    }
    # Options update is applied in-place via the entry-update listener;
    # no full entry reload is required (only data/credential changes reload)
    reload_entry.assert_not_awaited()
    # The confirmed 3047 state reconfigures the HA listener, but must not ask
    # the device to repeat the just-confirmed 3046/BLE-113 write cycle.
    schedule.assert_called_once()
    assert schedule.call_args.kwargs["device_config_keys"] == set()
    forward.assert_awaited_once()

    # A readback arriving after unload has fenced this coordinator belongs to
    # the old runtime and must not mutate the new config-entry state.
    options_before_late_readback = dict(entry.options)
    runtime_bucket = integration._entry_runtime_bucket(hass, entry)
    runtime_bucket[integration._UNLOADING_COORDINATOR_RUNTIME_KEY] = coordinator
    with patch.object(hass.config_entries, "async_update_entry") as update_entry:
        observer({
            "enable": 1,
            "ip": "192.168.2.250",
            "port": 1883,
            "userName": "late-user",
            "password": "late-password",
            "token": "late-token",
        })
    update_entry.assert_not_called()
    assert entry.options == options_before_late_readback


async def test_setup_ignores_incomplete_enabled_device_mqtt_config(
    hass: HomeAssistant,
) -> None:
    """An enabled 3047 payload without a valid broker cannot overwrite options."""
    entry = _entry(hass, entry_id="reject-device-mqtt")
    coordinator = MagicMock(name="coordinator")
    coordinator.data = {}

    # Configure coordinator async methods to return proper awaitables
    async def noop() -> None:  # noqa: RUF029
        return None

    coordinator.async_start_mqtt = AsyncMock(side_effect=noop)
    coordinator.async_start_local_mqtt_listener = AsyncMock(side_effect=noop)
    coordinator.async_start_ble_transport = AsyncMock(side_effect=noop)
    coordinator.async_apply_local_mqtt_config_to_devices = AsyncMock(side_effect=noop)

    with (
        patch.object(integration, "async_get_clientsession", return_value=MagicMock()),
        patch.object(integration, "JackeryApi", return_value=MagicMock()),
        patch.object(
            integration,
            "JackerySolarVaultCoordinator",
            return_value=coordinator,
        ),
        patch.object(
            integration,
            "_async_release_fenced_coordinator",
            AsyncMock(return_value=True),
        ),
        patch.object(integration, "_async_prune_removed_local_mqtt_tls_options"),
        patch.object(
            integration,
            "_async_load_entry_caches",
            AsyncMock(return_value=False),
        ),
        patch.object(
            integration,
            "_async_prepare_primary_http",
            AsyncMock(return_value=None),
        ),
        patch.object(integration, "_async_clean_legacy_entities"),
        patch.object(integration, "_async_remove_legacy_system_parent_devices"),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            AsyncMock(return_value=None),
        ),
        patch.object(integration, "_schedule_layer5_start_if_ready"),
    ):
        assert await integration.async_setup_entry(hass, entry) is True

    observer = coordinator.set_local_mqtt_config_observer.call_args.args[0]
    with (
        patch.object(hass.config_entries, "async_update_entry") as update_entry,
        patch.object(integration, "_schedule_layer5_start_if_ready") as reconcile,
    ):
        observer({"enable": True, "ip": "", "port": 1883})

    update_entry.assert_not_called()
    reconcile.assert_not_called()


async def test_entry_data_change_reloads_instead_of_mutating_transports(
    hass: HomeAssistant,
) -> None:
    """Credential/data changes reload, while no in-place transport action runs."""
    entry = _entry(hass, entry_id="data-change")
    coordinator = MagicMock(name="coordinator")
    entry.runtime_data = coordinator
    bucket = integration._entry_runtime_bucket(hass, entry)
    bucket[integration._ENTRY_DATA_SNAPSHOT_RUNTIME_KEY] = {
        CONF_USERNAME: "old@example.com",
        CONF_PASSWORD: "secret",
    }
    bucket[integration._OPTIONS_SNAPSHOT_RUNTIME_KEY] = {}

    with (
        patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload_entry,
        patch.object(integration, "_schedule_layer5_start_if_ready") as reconcile,
    ):
        await integration._async_entry_updated(hass, entry)

    reload_entry.assert_awaited_once_with(entry.entry_id)
    reconcile.assert_not_called()
    coordinator.async_set_scan_interval.assert_not_called()


async def test_entry_options_apply_polling_entities_and_layer5_in_place(
    hass: HomeAssistant,
) -> None:
    """Ordinary options update HTTP scheduling and Layer 5 without a reload."""
    entry = _entry(
        hass,
        entry_id="options-in-place",
        options={
            CONF_SCAN_INTERVAL: 37,
            CONF_CREATE_CALCULATED_POWER_SENSORS: True,
            CONF_ENABLE_BLE_TRANSPORT: True,
        },
    )
    coordinator = MagicMock(name="coordinator")
    entry.runtime_data = coordinator
    bucket = integration._entry_runtime_bucket(hass, entry)
    bucket[integration._ENTRY_DATA_SNAPSHOT_RUNTIME_KEY] = dict(entry.data)
    bucket[integration._OPTIONS_SNAPSHOT_RUNTIME_KEY] = {}

    with (
        patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload_entry,
        patch.object(integration, "_async_clean_legacy_entities") as clean_entities,
        patch.object(integration, "_schedule_layer5_start_if_ready") as reconcile,
    ):
        await integration._async_entry_updated(hass, entry)

    reload_entry.assert_not_awaited()
    coordinator.async_set_scan_interval.assert_called_once_with(timedelta(seconds=37))
    coordinator.async_update_listeners.assert_called_once_with()
    clean_entities.assert_called_once_with(hass, entry)
    reconcile.assert_called_once()
    assert CONF_ENABLE_BLE_TRANSPORT in reconcile.call_args.args[3]


async def test_options_reconcile_keeps_ble_running_when_local_mqtt_fails(
    hass: HomeAssistant,
) -> None:
    """One optional transport failure does not stop the independent peer."""
    entry = _entry(hass, entry_id="independent-options-reconcile")
    coordinator = MagicMock(name="coordinator")
    coordinator.async_reconcile_ble_transport = AsyncMock(return_value=None)
    entry.runtime_data = coordinator
    bucket = integration._entry_runtime_bucket(hass, entry)
    bucket[integration._OPTIONS_RECONCILE_PENDING_RUNTIME_KEY] = {
        CONF_LOCAL_MQTT_TOPIC,
        CONF_ENABLE_BLE_TRANSPORT,
    }
    local_failure = RuntimeError("local listener unavailable")

    with patch.object(
        integration,
        "_async_start_local_mqtt",
        AsyncMock(side_effect=local_failure),
    ) as start_local:
        await integration._async_reconcile_entry_options(
            hass,
            entry,
            coordinator,
        )

    start_local.assert_awaited_once_with(hass, entry, coordinator)
    coordinator.async_reconcile_ble_transport.assert_awaited_once_with()
    coordinator.async_schedule_local_mqtt_device_config.assert_called_once_with()


async def test_device_originated_reconcile_does_not_rewrite_device_config(
    hass: HomeAssistant,
) -> None:
    """A confirmed device-only 3047 readback leaves the HA listener intact."""
    entry = _entry(hass, entry_id="device-originated-options-reconcile")
    coordinator = MagicMock(name="coordinator")
    entry.runtime_data = coordinator
    bucket = integration._entry_runtime_bucket(hass, entry)
    bucket[integration._OPTIONS_RECONCILE_PENDING_RUNTIME_KEY] = {
        CONF_THIRD_PARTY_MQTT_IP,
    }
    bucket[integration._OPTIONS_DEVICE_CONFIG_PENDING_RUNTIME_KEY] = set()

    with patch.object(
        integration,
        "_async_start_local_mqtt",
        AsyncMock(return_value=None),
    ) as start_local:
        await integration._async_reconcile_entry_options(hass, entry, coordinator)

    start_local.assert_not_awaited()
    coordinator.async_schedule_local_mqtt_device_config.assert_not_called()


async def test_layer5_option_changes_coalesce_into_one_reconcile_task(
    hass: HomeAssistant,
) -> None:
    """Concurrent option callbacks merge keys instead of losing the later change."""
    entry = _entry(hass, entry_id="coalesced-options-reconcile")
    coordinator = MagicMock(name="coordinator")
    entry.runtime_data = coordinator

    with patch.object(
        integration,
        "_async_start_local_mqtt",
        AsyncMock(return_value=None),
    ) as start_local:
        integration._schedule_layer5_start_if_ready(
            hass,
            entry,
            coordinator,
            {CONF_LOCAL_MQTT_TOPIC},
        )
        first_task = integration._entry_runtime_task(
            hass,
            entry,
            integration._OPTIONS_RECONCILE_TASK_RUNTIME_KEY,
        )
        integration._schedule_layer5_start_if_ready(
            hass,
            entry,
            coordinator,
            {CONF_THIRD_PARTY_MQTT_QOS},
        )
        second_task = integration._entry_runtime_task(
            hass,
            entry,
            integration._OPTIONS_RECONCILE_TASK_RUNTIME_KEY,
        )

        assert first_task is not None
        assert second_task is first_task
        await first_task

    start_local.assert_awaited_once_with(hass, entry, coordinator)
    coordinator.async_schedule_local_mqtt_device_config.assert_called_once_with()


async def test_finished_options_reconcile_task_is_replaced(
    hass: HomeAssistant,
) -> None:
    """A done task still in the slot cannot strand a later option change."""
    entry = _entry(hass, entry_id="finished-options-reconcile")
    coordinator = MagicMock(name="coordinator")
    entry.runtime_data = coordinator
    finished = hass.async_create_task(asyncio.sleep(0), "finished-options-task")
    await finished
    bucket = integration._entry_runtime_bucket(hass, entry)
    bucket[integration._OPTIONS_RECONCILE_TASK_RUNTIME_KEY] = finished

    with patch.object(
        integration,
        "_async_reconcile_entry_options",
        AsyncMock(return_value=None),
    ) as reconcile:
        integration._schedule_layer5_start_if_ready(
            hass,
            entry,
            coordinator,
            {CONF_THIRD_PARTY_MQTT_QOS},
        )
        replacement = integration._entry_runtime_task(
            hass,
            entry,
            integration._OPTIONS_RECONCILE_TASK_RUNTIME_KEY,
        )

        assert replacement is not None
        assert replacement is not finished
        await replacement

    reconcile.assert_awaited_once_with(hass, entry, coordinator)


async def test_token_only_option_change_schedules_device_reconcile(
    hass: HomeAssistant,
) -> None:
    """Changing only the third-party token cannot be lost after snapshot advance."""
    entry = _entry(
        hass,
        entry_id="token-only-options-reconcile",
        options={CONF_THIRD_PARTY_MQTT_TOKEN: "new-token"},
    )
    coordinator = MagicMock(name="coordinator")
    entry.runtime_data = coordinator
    bucket = integration._entry_runtime_bucket(hass, entry)
    bucket[integration._ENTRY_DATA_SNAPSHOT_RUNTIME_KEY] = dict(entry.data)
    bucket[integration._OPTIONS_SNAPSHOT_RUNTIME_KEY] = {
        CONF_THIRD_PARTY_MQTT_TOKEN: "old-token"
    }

    with patch.object(integration, "_schedule_layer5_start_if_ready") as reconcile:
        await integration._async_entry_updated(hass, entry)

    reconcile.assert_called_once_with(
        hass,
        entry,
        coordinator,
        {CONF_THIRD_PARTY_MQTT_TOKEN},
        device_config_keys={CONF_THIRD_PARTY_MQTT_TOKEN},
    )


async def test_cancel_wait_is_hard_bounded_for_uncooperative_task(
    hass: HomeAssistant,
) -> None:
    """A task that consumes cancellation cannot hold entry teardown open."""
    entry = _entry(hass, entry_id="bounded-cancel-wait")
    release = asyncio.Event()

    async def ignore_cancellation() -> None:
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    task = hass.async_create_task(ignore_cancellation(), "uncooperative-entry-task")
    await asyncio.sleep(0)
    task.cancel()
    release_handle = hass.loop.call_later(0.2, release.set)

    try:
        started = time.monotonic()
        with patch.object(integration, "_ENTRY_TASK_CANCEL_TIMEOUT_SEC", 0.01):
            await integration._async_await_cancelled_runtime_task(
                hass,
                entry,
                task,
                label="uncooperative",
            )
        elapsed = time.monotonic() - started

        assert elapsed < 0.1
        bucket = integration._entry_runtime_bucket(hass, entry)
        assert task in bucket[integration._SUPPLEMENTAL_LAYER5_TASKS_RUNTIME_KEY]
    finally:
        release_handle.cancel()
        release.set()
        await task


async def test_cancelled_cancel_wait_retains_child_cleanup_ownership(
    hass: HomeAssistant,
) -> None:
    """Cancelling teardown cannot orphan its cancellation-resistant child task."""
    entry = _entry(hass, entry_id="cancelled-cancel-wait")
    release = asyncio.Event()

    async def ignore_cancellation() -> None:
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    child = hass.async_create_task(ignore_cancellation(), "owned-cancel-child")
    await asyncio.sleep(0)
    child.cancel()
    waiter = hass.async_create_task(
        integration._async_await_cancelled_runtime_task(
            hass,
            entry,
            child,
            label="owned child",
        ),
        "cancelled-cancel-waiter",
    )
    await asyncio.sleep(0)
    waiter.cancel()

    try:
        with pytest.raises(asyncio.CancelledError):
            await waiter
        bucket = integration._entry_runtime_bucket(hass, entry)
        assert child in bucket[integration._SUPPLEMENTAL_LAYER5_TASKS_RUNTIME_KEY]
    finally:
        release.set()
        await child


async def test_cancelled_local_mqtt_stop_is_deferred_and_owned(
    hass: HomeAssistant,
) -> None:
    """Mid-stop cancellation retains both the client and its only stop task."""
    entry = _entry(hass, entry_id="cancelled-local-mqtt-stop")
    release = asyncio.Event()

    async def ignore_cancellation() -> None:
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    client = MagicMock(spec=integration.JackeryLocalMqttClient)
    client.async_stop = AsyncMock(side_effect=ignore_cancellation)
    bucket = integration._entry_runtime_bucket(hass, entry)
    bucket[integration._LOCAL_MQTT_RUNTIME_KEY] = client

    with patch.object(integration, "_schedule_supplemental_cleanup") as cleanup:
        waiter = hass.async_create_task(
            integration._async_stop_local_mqtt_client(hass, entry, client),
            "cancelled-local-mqtt-stop-waiter",
        )
        await asyncio.sleep(0)
        waiter.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await waiter

            assert integration._LOCAL_MQTT_RUNTIME_KEY not in bucket
            assert client in bucket[integration._SUPPLEMENTAL_LOCAL_MQTT_RUNTIME_KEY]
            stop_records = bucket[integration._LOCAL_MQTT_STOP_TASKS_RUNTIME_KEY]
            assert len(stop_records) == 1
            owned_client, stop_task = stop_records[id(client)]
            assert owned_client is client
            assert not stop_task.done()
            cleanup.assert_called_once_with(hass, entry)
        finally:
            release.set()
            await stop_task
            assert await integration._async_stop_local_mqtt_client(
                hass,
                entry,
                client,
            )


async def test_supplemental_cleanup_call_is_hard_bounded(
    hass: HomeAssistant,
) -> None:
    """A cleanup coroutine that consumes cancellation cannot trap its reaper."""
    entry = _entry(hass, entry_id="bounded-supplemental-call")
    release = asyncio.Event()

    async def ignore_cancellation() -> None:
        while not release.is_set():
            try:
                await release.wait()
            except asyncio.CancelledError:
                continue

    bucket = integration._entry_runtime_bucket(hass, entry)
    try:
        started = time.monotonic()
        with patch.object(integration, "_ENTRY_TASK_CANCEL_TIMEOUT_SEC", 0.01):
            assert not await integration._async_run_supplemental_cleanup_call(
                hass,
                entry,
                ignore_cancellation(),
                name="bounded-supplemental-call-child",
            )
        assert time.monotonic() - started < 0.1

        stop_tasks = bucket[integration._SUPPLEMENTAL_LAYER5_TASKS_RUNTIME_KEY]
        assert len(stop_tasks) == 1
    finally:
        release.set()
        for task in bucket.get(
            integration._SUPPLEMENTAL_LAYER5_TASKS_RUNTIME_KEY,
            [],
        ):
            with contextlib.suppress(asyncio.CancelledError):
                await task


async def test_layer5_device_config_does_not_wait_for_ble_start(
    hass: HomeAssistant,
) -> None:
    """A hanging BLE start cannot block Local-MQTT device configuration."""
    entry = _entry(hass, entry_id="layer5-independent-device-config")
    coordinator = MagicMock(name="coordinator")
    entry.runtime_data = coordinator
    ble_release = asyncio.Event()
    local_started = asyncio.Event()

    async def start_local(
        _hass: HomeAssistant,
        _entry: MockConfigEntry,
        _coordinator: MagicMock,
    ) -> None:
        await asyncio.sleep(0)
        local_started.set()

    async def start_ble() -> None:
        await ble_release.wait()

    coordinator.async_start_mqtt = AsyncMock(return_value=None)
    coordinator.async_start_ble_transport = AsyncMock(side_effect=start_ble)

    with patch.object(integration, "_async_start_local_mqtt", side_effect=start_local):
        task = hass.async_create_task(
            integration._async_start_layer5_transports(hass, entry, coordinator),
            "independent-layer5-start",
        )
        try:
            await asyncio.wait_for(local_started.wait(), timeout=1)
            await asyncio.sleep(0)

            assert not task.done()
            coordinator.async_schedule_local_mqtt_device_config.assert_called_once_with()
        finally:
            ble_release.set()
            await task


async def test_layer5_device_config_does_not_wait_for_local_mqtt_start(
    hass: HomeAssistant,
) -> None:
    """A hanging HA-MQTT subscribe cannot block device-side configuration."""
    entry = _entry(hass, entry_id="layer5-local-does-not-block-device-config")
    coordinator = MagicMock(name="coordinator")
    entry.runtime_data = coordinator
    local_entered = asyncio.Event()
    local_release = asyncio.Event()

    async def start_local(
        _hass: HomeAssistant,
        _entry: MockConfigEntry,
        _coordinator: MagicMock,
    ) -> None:
        local_entered.set()
        await local_release.wait()

    coordinator.async_start_mqtt = AsyncMock(return_value=None)
    coordinator.async_start_ble_transport = AsyncMock(return_value=None)

    with patch.object(integration, "_async_start_local_mqtt", side_effect=start_local):
        task = hass.async_create_task(
            integration._async_start_layer5_transports(hass, entry, coordinator),
            "local-independent-layer5-start",
        )
        try:
            await asyncio.wait_for(local_entered.wait(), timeout=1)
            await asyncio.sleep(0)

            assert not task.done()
            coordinator.async_schedule_local_mqtt_device_config.assert_called_once_with()
        finally:
            local_release.set()
            await task


async def test_layer5_handles_cloud_failure_before_ble_start_finishes(
    hass: HomeAssistant,
) -> None:
    """A hanging BLE start cannot delay handling a completed cloud auth failure."""
    entry = _entry(hass, entry_id="layer5-cloud-result-independent")
    coordinator = MagicMock(name="coordinator")
    entry.runtime_data = coordinator
    ble_release = asyncio.Event()
    cloud_failed = ConfigEntryAuthFailed("cloud MQTT credentials rejected")

    async def start_cloud() -> None:
        await asyncio.sleep(0)
        raise cloud_failed

    async def start_ble() -> None:
        await ble_release.wait()

    coordinator.async_start_mqtt = AsyncMock(side_effect=start_cloud)
    coordinator.async_start_ble_transport = AsyncMock(side_effect=start_ble)

    with patch.object(
        integration,
        "_async_start_local_mqtt",
        AsyncMock(return_value=None),
    ):
        task = hass.async_create_task(
            integration._async_start_layer5_transports(hass, entry, coordinator),
            "cloud-result-independent-layer5-start",
        )
        try:
            for _ in range(5):
                await asyncio.sleep(0)
                if coordinator.defer_background_auth_failure.called:
                    break

            assert not task.done()
            coordinator.defer_background_auth_failure.assert_called_once_with(
                cloud_failed
            )
        finally:
            ble_release.set()
            await task


async def test_unload_cancellation_preserves_runtime_fence(
    hass: HomeAssistant,
) -> None:
    """Cancellation after platform unload cannot orphan a running coordinator."""
    entry = _entry(hass, entry_id="cancelled-unload")
    coordinator = MagicMock(spec=JackerySolarVaultCoordinator)
    entry.runtime_data = coordinator
    bucket = integration._entry_runtime_bucket(hass, entry)

    with (
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            AsyncMock(return_value=True),
        ),
        patch.object(
            integration,
            "_async_cancel_layer5_start_task",
            AsyncMock(side_effect=asyncio.CancelledError),
        ),
        pytest.raises(asyncio.CancelledError),
    ):
        await integration.async_unload_entry(hass, entry)

    assert entry.runtime_data is coordinator
    assert bucket[integration._UNLOADING_COORDINATOR_RUNTIME_KEY] is coordinator


async def test_concurrent_local_mqtt_stop_reuses_one_owned_task(
    hass: HomeAssistant,
) -> None:
    """Concurrent lifecycle callers never unsubscribe one client twice."""
    entry = _entry(hass, entry_id="single-local-mqtt-stop")
    client = MagicMock(spec=integration.JackeryLocalMqttClient)
    stop_entered = asyncio.Event()
    release_stop = asyncio.Event()

    async def _blocking_stop() -> None:
        stop_entered.set()
        await release_stop.wait()

    client.async_stop = AsyncMock(side_effect=_blocking_stop)
    bucket = integration._entry_runtime_bucket(hass, entry)
    bucket[integration._LOCAL_MQTT_RUNTIME_KEY] = client
    first = hass.async_create_task(
        integration._async_stop_local_mqtt_client(hass, entry, client),
        "first-local-mqtt-stop",
    )
    second: asyncio.Task[bool] | None = None
    try:
        await asyncio.wait_for(stop_entered.wait(), timeout=1)
        second = hass.async_create_task(
            integration._async_stop_local_mqtt_client(hass, entry, client),
            "second-local-mqtt-stop",
        )
        await asyncio.sleep(0)

        client.async_stop.assert_awaited_once_with()
        release_stop.set()
        assert await first is True
        assert await second is True
    finally:
        release_stop.set()
        pending = [task for task in (first, second) if task is not None]
        await asyncio.gather(*pending, return_exceptions=True)


async def test_cancelled_waiter_does_not_cancel_shared_local_mqtt_stop(
    hass: HomeAssistant,
) -> None:
    """One cancelled lifecycle waiter cannot cancel the shared stop operation."""
    entry = _entry(hass, entry_id="cancelled-local-mqtt-stop-waiter")
    client = MagicMock(spec=integration.JackeryLocalMqttClient)
    stop_entered = asyncio.Event()
    release_stop = asyncio.Event()

    async def _blocking_stop() -> None:
        stop_entered.set()
        await release_stop.wait()

    client.async_stop = AsyncMock(side_effect=_blocking_stop)
    bucket = integration._entry_runtime_bucket(hass, entry)
    bucket[integration._LOCAL_MQTT_RUNTIME_KEY] = client
    with patch.object(integration, "_schedule_supplemental_cleanup"):
        first = hass.async_create_task(
            integration._async_stop_local_mqtt_client(hass, entry, client),
            "cancelled-local-mqtt-stop-waiter",
        )
        await asyncio.wait_for(stop_entered.wait(), timeout=1)
        second = hass.async_create_task(
            integration._async_stop_local_mqtt_client(hass, entry, client),
            "surviving-local-mqtt-stop-waiter",
        )
        await asyncio.sleep(0)

        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        assert not second.done()

        release_stop.set()
        assert await second is True

    client.async_stop.assert_awaited_once_with()


async def test_entry_update_after_runtime_clear_is_ignored(
    hass: HomeAssistant,
) -> None:
    """A late HA update-listener callback cannot touch a cleared runtime."""
    entry = _entry(
        hass,
        entry_id="late-entry-update",
        options={CONF_SCAN_INTERVAL: 30},
    )
    entry.runtime_data = None
    bucket = integration._entry_runtime_bucket(hass, entry)
    bucket[integration._OPTIONS_SNAPSHOT_RUNTIME_KEY] = {CONF_SCAN_INTERVAL: 15}
    bucket[integration._ENTRY_DATA_SNAPSHOT_RUNTIME_KEY] = dict(entry.data)

    with patch.object(integration, "_schedule_layer5_start_if_ready") as schedule:
        await integration._async_entry_updated(hass, entry)

    schedule.assert_not_called()


async def test_supplemental_cleanup_preserves_concurrent_layer5_append(
    hass: HomeAssistant,
) -> None:
    """Cleanup removes only its snapshot and never overwrites a later append."""
    entry = _entry(hass, entry_id="supplemental-concurrent-append")
    entry.runtime_data = None
    cancel_seen = asyncio.Event()
    release_initial = asyncio.Event()
    concurrent_release = asyncio.Event()

    async def _cancellation_resistant_initial() -> None:
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            cancel_seen.set()
            await release_initial.wait()

    initial_task = hass.async_create_task(
        _cancellation_resistant_initial(),
        "initial-supplemental-task",
    )
    concurrent_task = hass.async_create_task(
        concurrent_release.wait(),
        "concurrent-supplemental-task",
    )
    integration._append_supplemental_runtime_object(
        hass,
        entry,
        integration._SUPPLEMENTAL_LAYER5_TASKS_RUNTIME_KEY,
        initial_task,
    )
    cleanup_pending = MagicMock(side_effect=[True, False, False])
    cleanup_task: asyncio.Task[None] | None = None
    try:
        with patch.object(
            integration,
            "_supplemental_cleanup_pending",
            cleanup_pending,
        ):
            cleanup_task = hass.async_create_task(
                integration._async_cleanup_stale_supplemental(hass, entry),
                "supplemental-concurrent-cleanup",
            )
            await asyncio.wait_for(cancel_seen.wait(), timeout=1)
            integration._append_supplemental_runtime_object(
                hass,
                entry,
                integration._SUPPLEMENTAL_LAYER5_TASKS_RUNTIME_KEY,
                concurrent_task,
            )
            release_initial.set()
            await cleanup_task

        bucket = integration._entry_runtime_bucket(hass, entry)
        remaining = integration._supplemental_runtime_items(
            bucket,
            integration._SUPPLEMENTAL_LAYER5_TASKS_RUNTIME_KEY,
        )
        assert any(item is concurrent_task for item in remaining)
    finally:
        release_initial.set()
        concurrent_release.set()
        await asyncio.gather(
            initial_task,
            concurrent_task,
            *(() if cleanup_task is None else (cleanup_task,)),
            return_exceptions=True,
        )


async def test_successful_unload_defers_pending_supplemental_cleanup(
    hass: HomeAssistant,
) -> None:
    """A stopped HTTP owner remains tracked while its background tasks unwind."""
    entry = _entry(hass, entry_id="pending-supplemental-unload")
    coordinator = MagicMock(spec=JackerySolarVaultCoordinator)
    coordinator.has_pending_supplemental_transport_cleanup = True
    entry.runtime_data = coordinator
    bucket = integration._entry_runtime_bucket(hass, entry)
    bucket[integration._OPTIONS_RECONCILE_PENDING_RUNTIME_KEY] = {"old-topic"}

    with (
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            AsyncMock(return_value=True),
        ),
        patch.object(
            integration,
            "_async_shutdown_coordinator_bounded",
            AsyncMock(return_value=True),
        ),
        patch.object(integration, "_defer_supplemental_transports") as defer,
        patch.object(integration, "_schedule_supplemental_cleanup") as cleanup,
    ):
        assert await integration.async_unload_entry(hass, entry) is True

    defer.assert_called_once_with(hass, entry, coordinator)
    cleanup.assert_called_once_with(hass, entry)
    assert integration._OPTIONS_RECONCILE_PENDING_RUNTIME_KEY not in bucket


async def test_shutdown_timeout_clears_transition_only_option_markers(
    hass: HomeAssistant,
) -> None:
    """Old option work cannot leak across a fenced shutdown into the next runtime."""
    entry = _entry(hass, entry_id="shutdown-timeout-clears-option-markers")
    coordinator = MagicMock(spec=JackerySolarVaultCoordinator)
    entry.runtime_data = coordinator
    bucket = integration._entry_runtime_bucket(hass, entry)
    bucket[integration._OPTIONS_RECONCILE_PENDING_RUNTIME_KEY] = {
        CONF_THIRD_PARTY_MQTT_QOS
    }
    bucket[integration._OPTIONS_DEVICE_CONFIG_PENDING_RUNTIME_KEY] = {
        CONF_THIRD_PARTY_MQTT_TOKEN
    }
    bucket[integration._DEVICE_MQTT_ADOPTED_OPTIONS_RUNTIME_KEY] = {
        CONF_THIRD_PARTY_MQTT_QOS: 1
    }

    with (
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            AsyncMock(return_value=True),
        ),
        patch.object(
            integration,
            "_async_shutdown_coordinator_bounded",
            AsyncMock(return_value=False),
        ),
    ):
        assert await integration.async_unload_entry(hass, entry) is True

    assert integration._OPTIONS_RECONCILE_PENDING_RUNTIME_KEY not in bucket
    assert integration._OPTIONS_DEVICE_CONFIG_PENDING_RUNTIME_KEY not in bucket
    assert integration._DEVICE_MQTT_ADOPTED_OPTIONS_RUNTIME_KEY not in bucket


async def test_release_fenced_coordinator_clears_only_after_http_shutdown(
    hass: HomeAssistant,
) -> None:
    """A previous HTTP owner remains fenced until its bounded shutdown succeeds."""
    entry = _entry(hass, entry_id="fenced-owner")
    coordinator = MagicMock(spec=JackerySolarVaultCoordinator)
    entry.runtime_data = coordinator
    bucket = integration._entry_runtime_bucket(hass, entry)
    bucket[integration._UNLOADING_COORDINATOR_RUNTIME_KEY] = coordinator
    bucket[integration._PRIMARY_SETUP_COORDINATOR_RUNTIME_KEY] = coordinator

    with (
        patch.object(
            integration,
            "_async_shutdown_coordinator_bounded",
            AsyncMock(side_effect=[False, True]),
        ) as shutdown,
        patch.object(integration, "_defer_supplemental_transports") as defer,
        patch.object(integration, "_schedule_supplemental_cleanup") as cleanup,
    ):
        assert not await integration._async_release_fenced_coordinator(hass, entry)
        assert entry.runtime_data is coordinator
        assert bucket[integration._UNLOADING_COORDINATOR_RUNTIME_KEY] is coordinator

        assert await integration._async_release_fenced_coordinator(hass, entry)

    assert shutdown.await_count == 2
    assert entry.runtime_data is None
    assert integration._UNLOADING_COORDINATOR_RUNTIME_KEY not in bucket
    assert integration._PRIMARY_SETUP_COORDINATOR_RUNTIME_KEY not in bucket
    defer.assert_called_once_with(hass, entry, coordinator)
    cleanup.assert_called_once_with(hass, entry)


async def test_failed_platform_unload_preserves_http_runtime_fence(
    hass: HomeAssistant,
) -> None:
    """A failed platform unload leaves every still-loaded runtime operational."""
    entry = _entry(hass, entry_id="failed-unload")
    coordinator = MagicMock(spec=JackerySolarVaultCoordinator)
    entry.runtime_data = coordinator
    shutdown = AsyncMock(return_value=True)
    local_mqtt = MagicMock(name="local_mqtt")
    stop_local_mqtt = AsyncMock()
    layer5_gate = asyncio.Event()
    layer5_task = hass.async_create_task(
        layer5_gate.wait(),
        "failed-unload-layer5",
    )
    bucket = integration._entry_runtime_bucket(hass, entry)
    bucket[integration._LAYER5_TASK_RUNTIME_KEY] = layer5_task
    bucket[integration._LOCAL_MQTT_RUNTIME_KEY] = local_mqtt

    try:
        with (
            patch.object(
                hass.config_entries,
                "async_unload_platforms",
                AsyncMock(return_value=False),
            ),
            patch.object(
                integration,
                "_local_mqtt_client",
                return_value=local_mqtt,
            ),
            patch.object(
                integration,
                "_async_stop_local_mqtt_client",
                stop_local_mqtt,
            ),
            patch.object(integration, "_async_shutdown_coordinator_bounded", shutdown),
            patch.object(integration, "_schedule_supplemental_cleanup"),
        ):
            assert await integration.async_unload_entry(hass, entry) is False

        shutdown.assert_not_awaited()
        stop_local_mqtt.assert_not_awaited()
        assert entry.runtime_data is coordinator
        assert not layer5_task.done()
        assert bucket[integration._LAYER5_TASK_RUNTIME_KEY] is layer5_task
        assert bucket[integration._LOCAL_MQTT_RUNTIME_KEY] is local_mqtt
        assert integration._UNLOADING_COORDINATOR_RUNTIME_KEY not in bucket
    finally:
        layer5_gate.set()
        with contextlib.suppress(asyncio.CancelledError):
            await layer5_task


@pytest.mark.parametrize(
    "failure",
    [
        ConfigEntryAuthFailed("invalid credentials"),
        asyncio.CancelledError(),
    ],
    ids=("auth-failed", "cancelled"),
)
async def test_setup_propagates_non_fallback_primary_failures(
    hass: HomeAssistant,
    failure: BaseException,
) -> None:
    """Auth failures and cancellation never continue into platform setup."""
    entry = _entry(hass, entry_id=f"setup-{type(failure).__name__}")
    api = MagicMock(name="api")
    coordinator = MagicMock(spec=JackerySolarVaultCoordinator)
    coordinator.data = {}
    rollback = AsyncMock(return_value=True)
    forward = AsyncMock(return_value=None)

    with (
        patch.object(integration, "async_get_clientsession", return_value=MagicMock()),
        patch.object(integration, "JackeryApi", return_value=api),
        patch.object(
            integration,
            "JackerySolarVaultCoordinator",
            return_value=coordinator,
        ),
        patch.object(
            integration,
            "_async_release_fenced_coordinator",
            AsyncMock(return_value=True),
        ),
        patch.object(integration, "_async_migrate_legacy_local_mqtt_options"),
        patch.object(integration, "_async_prune_removed_local_mqtt_tls_options"),
        patch.object(
            integration,
            "_async_load_entry_caches",
            AsyncMock(return_value=True),
        ),
        patch.object(
            integration,
            "_async_prepare_primary_http",
            AsyncMock(side_effect=failure),
        ),
        patch.object(integration, "_async_clean_legacy_entities"),
        patch.object(integration, "_async_remove_legacy_system_parent_devices"),
        patch.object(hass.config_entries, "async_forward_entry_setups", forward),
        patch.object(integration, "_async_rollback_entry_setup", rollback),
        pytest.raises(type(failure)),
    ):
        await integration.async_setup_entry(hass, entry)

    forward.assert_not_awaited()
    rollback.assert_awaited_once_with(
        hass,
        entry,
        coordinator,
        platforms_started=False,
    )


async def test_partial_platform_setup_is_always_rolled_back(
    hass: HomeAssistant,
) -> None:
    """A forwarding exception can follow successful sibling platform setups."""
    entry = _entry(hass, entry_id="partial-platform-rollback")
    api = MagicMock(name="api")
    coordinator = MagicMock(spec=JackerySolarVaultCoordinator)
    coordinator.data = {}
    rollback = AsyncMock(return_value=True)

    with (
        patch.object(integration, "async_get_clientsession", return_value=MagicMock()),
        patch.object(integration, "JackeryApi", return_value=api),
        patch.object(
            integration,
            "JackerySolarVaultCoordinator",
            return_value=coordinator,
        ),
        patch.object(
            integration,
            "_async_release_fenced_coordinator",
            AsyncMock(return_value=True),
        ),
        patch.object(integration, "_async_migrate_legacy_local_mqtt_options"),
        patch.object(integration, "_async_prune_removed_local_mqtt_tls_options"),
        patch.object(
            integration,
            "_async_load_entry_caches",
            AsyncMock(return_value=False),
        ),
        patch.object(
            integration,
            "_async_prepare_primary_http",
            AsyncMock(return_value=None),
        ),
        patch.object(integration, "_async_clean_legacy_entities"),
        patch.object(integration, "_async_remove_legacy_system_parent_devices"),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            AsyncMock(side_effect=RuntimeError("one platform failed")),
        ),
        patch.object(integration, "_async_rollback_entry_setup", rollback),
        pytest.raises(RuntimeError, match="one platform failed"),
    ):
        await integration.async_setup_entry(hass, entry)

    rollback.assert_awaited_once_with(
        hass,
        entry,
        coordinator,
        platforms_started=True,
    )


def test_battery_pack_registry_identity_requires_one_parent_scoped_id(
    hass: HomeAssistant,
) -> None:
    """Battery-pack migration accepts exactly one identity scoped to its parent."""
    entry = _entry(hass, entry_id="pack-registry-identity")
    registry = dr.async_get(hass)
    parent = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "head-1")},
        name="SolarVault",
    )
    child = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "head-1_battery_pack_PACK-1")},
        name="Pack",
        via_device=(DOMAIN, "head-1"),
    )

    assert integration._battery_pack_registry_identity(
        registry,
        child,
    ) == ("head-1", "head-1_battery_pack_PACK-1", "PACK-1")
    assert child.via_device_id == parent.id

    ambiguous = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={
            (DOMAIN, "head-1_battery_pack_PACK-2"),
            (DOMAIN, "head-1_battery_pack_PACK-3"),
        },
        name="Ambiguous pack",
        via_device=(DOMAIN, "head-1"),
    )
    assert (
        integration._battery_pack_registry_identity(
            registry,
            ambiguous,
        )
        is None
    )


def test_phantom_cleanup_removes_head_unit_duplicate_pack(
    hass: HomeAssistant,
) -> None:
    """A head serial masquerading as a pack is detached without payload filtering."""
    entry = _entry(hass, entry_id="head-duplicate-pack")
    coordinator = MagicMock(name="coordinator")
    coordinator.data = {}
    entry.runtime_data = coordinator
    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "head-2")},
        name="SolarVault",
        serial_number="HEAD-SERIAL",
    )
    child = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "head-2_battery_pack_HEAD-SERIAL")},
        name="False pack",
        serial_number="HEAD-SERIAL",
        via_device=(DOMAIN, "head-2"),
    )

    integration._async_remove_phantom_battery_pack_devices(
        hass,
        entry,
    )

    remaining = registry.async_get(child.id)
    assert remaining is None or entry.entry_id not in remaining.config_entries


async def test_config_entry_device_removal_is_allowed(
    hass: HomeAssistant,
) -> None:
    """Registry removal is never blocked by transient discovery state."""
    entry = _entry(hass, entry_id="allow-device-remove")
    device = MagicMock(spec=dr.DeviceEntry)

    assert await integration.async_remove_config_entry_device(hass, entry, device)
