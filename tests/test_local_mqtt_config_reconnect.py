"""Behavioral regressions for local-MQTT device configuration retries."""

import asyncio
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault import coordinator as coordinator_module
from custom_components.jackery_solarvault.const import (
    ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
    CONF_LOCAL_MQTT_ENABLE,
    CONF_LOCAL_MQTT_HOST,
    CONF_LOCAL_MQTT_PASSWORD,
    CONF_LOCAL_MQTT_PORT,
    CONF_LOCAL_MQTT_USERNAME,
    CONF_THIRD_PARTY_MQTT_TOKEN,
    FIELD_THIRD_PARTY_MQTT_ENABLE,
    FIELD_THIRD_PARTY_MQTT_IP,
    FIELD_THIRD_PARTY_MQTT_PASSWORD,
    FIELD_THIRD_PARTY_MQTT_PORT,
    FIELD_THIRD_PARTY_MQTT_TOKEN,
    FIELD_THIRD_PARTY_MQTT_USERNAME,
    PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

if TYPE_CHECKING:
    from collections.abc import Coroutine

_EXPECTED_CONFIG_PUSH_COUNT = 2
_LOCAL_MQTT_PORT = 1883


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


@pytest.mark.asyncio
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
    obj._mqtt = SimpleNamespace(session_generation=1)  # ruff: ignore[private-member-access]
    obj._mqtt_session_generation = 1  # ruff: ignore[private-member-access]
    obj._mqtt_session_actions_seen = set()  # ruff: ignore[private-member-access]
    obj._mqtt_birth_snapshot_pending = False  # ruff: ignore[private-member-access]
    obj._cloud_mqtt_command_failures = {}  # ruff: ignore[private-member-access]
    obj._cloud_mqtt_command_attempts = {}  # ruff: ignore[private-member-access]
    obj._local_mqtt_config_diagnostics = {}  # ruff: ignore[private-member-access]
    obj.data = {}
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


@pytest.mark.asyncio
async def test_failed_config_push_retries_without_cloud_reconnect() -> None:
    """A transient 3046/BLE-113 failure retries without requiring reconnect."""
    coordinator = JackerySolarVaultCoordinator.__new__(
        JackerySolarVaultCoordinator,
    )
    obj = cast("Any", coordinator)
    obj._shutdown_started = False  # ruff: ignore[private-member-access]
    obj._background_tasks = {}  # ruff: ignore[private-member-access]
    obj._local_mqtt_config_retry_pending = False  # ruff: ignore[private-member-access]
    obj._local_mqtt_config_diagnostics = {}  # ruff: ignore[private-member-access]
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


@pytest.mark.asyncio
async def test_automatic_bridge_preserves_stable_app_token() -> None:
    """The automatic bridge reuses its hidden persisted App-style token."""
    coordinator = JackerySolarVaultCoordinator.__new__(
        JackerySolarVaultCoordinator,
    )
    obj = cast("Any", coordinator)
    obj.entry = SimpleNamespace(
        data={},
        options={
            CONF_LOCAL_MQTT_ENABLE: True,
            CONF_LOCAL_MQTT_HOST: "192.168.2.212",
            CONF_LOCAL_MQTT_PORT: _LOCAL_MQTT_PORT,
            CONF_LOCAL_MQTT_USERNAME: "mqtt_user",
            CONF_LOCAL_MQTT_PASSWORD: "mqtt_password",
            CONF_THIRD_PARTY_MQTT_TOKEN: "123456789",
        },
    )
    obj._local_mqtt_config_applied_signature = None  # ruff: ignore[private-member-access]
    obj._local_mqtt_config_diagnostics = {}  # ruff: ignore[private-member-access]
    obj._local_mqtt_no_host_warned = False  # ruff: ignore[private-member-access]
    obj._generated_third_party_mqtt_token = None  # ruff: ignore[private-member-access]
    obj._device_index = {"device-1": {}}  # ruff: ignore[private-member-access]
    obj.data = {
        "device-1": {
            PAYLOAD_THIRD_PARTY_MQTT_CONFIG: {
                FIELD_THIRD_PARTY_MQTT_TOKEN: "123456789",
            },
        },
    }
    setter = AsyncMock(return_value=None)
    obj.async_set_third_party_mqtt_config = setter

    assert await coordinator.async_apply_local_mqtt_config_to_devices() is True

    setter.assert_awaited_once_with(
        "device-1",
        enable=True,
        ip="192.168.2.212",
        port=_LOCAL_MQTT_PORT,
        username="mqtt_user",
        password="mqtt_password",
        token="123456789",
    )
    assert obj._local_mqtt_config_diagnostics["last_status"] == "success"  # ruff: ignore[private-member-access]


def test_blank_setter_token_reuses_persisted_token_after_restart() -> None:
    """A fresh coordinator reuses the hidden token persisted in options."""
    coordinator = JackerySolarVaultCoordinator.__new__(
        JackerySolarVaultCoordinator,
    )
    obj = cast("Any", coordinator)
    obj.entry = SimpleNamespace(
        options={CONF_THIRD_PARTY_MQTT_TOKEN: "123456789"},
    )
    update_entry = MagicMock()
    obj.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_update_entry=update_entry),
    )
    obj._generated_third_party_mqtt_token = None  # ruff: ignore[private-member-access]

    assert coordinator._stable_third_party_mqtt_token("") == (  # ruff: ignore[private-member-access]
        "123456789",
        True,
    )
    update_entry.assert_not_called()


@pytest.mark.asyncio
async def test_automatic_bridge_generates_and_persists_missing_app_token() -> None:
    """A missing device/options token gets one stable App-style fallback."""
    coordinator = JackerySolarVaultCoordinator.__new__(
        JackerySolarVaultCoordinator,
    )
    obj = cast("Any", coordinator)
    obj.entry = SimpleNamespace(
        data={},
        options={
            CONF_LOCAL_MQTT_ENABLE: True,
            CONF_LOCAL_MQTT_HOST: "192.168.2.212",
            CONF_LOCAL_MQTT_PORT: _LOCAL_MQTT_PORT,
            CONF_LOCAL_MQTT_USERNAME: "mqtt_user",
            CONF_LOCAL_MQTT_PASSWORD: "mqtt_password",
        },
    )
    update_entry = MagicMock()
    obj.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_update_entry=update_entry),
    )
    obj._local_mqtt_config_applied_signature = None  # ruff: ignore[private-member-access]
    obj._local_mqtt_config_diagnostics = {}  # ruff: ignore[private-member-access]
    obj._local_mqtt_no_host_warned = False  # ruff: ignore[private-member-access]
    obj._generated_third_party_mqtt_token = None  # ruff: ignore[private-member-access]
    obj._device_index = {"device-1": {}}  # ruff: ignore[private-member-access]
    obj.data = {"device-1": {}}
    obj._async_query_third_party_mqtt_config_readback = AsyncMock(  # ruff: ignore[private-member-access]
        return_value=None,
    )
    setter = AsyncMock(return_value=None)
    obj.async_set_third_party_mqtt_config = setter

    assert await coordinator.async_apply_local_mqtt_config_to_devices() is True

    await_args = setter.await_args
    assert await_args is not None
    sent_token = await_args.kwargs["token"]
    assert len(sent_token) == 9
    assert sent_token.isdecimal()
    update_entry.assert_called_once()
    assert (
        update_entry.call_args.kwargs["options"][CONF_THIRD_PARTY_MQTT_TOKEN]
        == sent_token
    )


@pytest.mark.asyncio
async def test_automatic_bridge_reads_device_token_before_first_write() -> None:
    """The App-compatible automatic path reuses 3047 before sending 3046."""
    coordinator = JackerySolarVaultCoordinator.__new__(
        JackerySolarVaultCoordinator,
    )
    obj = cast("Any", coordinator)
    obj.entry = SimpleNamespace(
        data={},
        options={
            CONF_LOCAL_MQTT_ENABLE: True,
            CONF_LOCAL_MQTT_HOST: "192.168.2.212",
            CONF_LOCAL_MQTT_PORT: _LOCAL_MQTT_PORT,
            CONF_LOCAL_MQTT_USERNAME: "mqtt_user",
            CONF_LOCAL_MQTT_PASSWORD: "mqtt_password",
        },
    )
    obj.hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_update_entry=MagicMock()),
    )
    obj._local_mqtt_config_applied_signature = None  # ruff: ignore[private-member-access]
    obj._local_mqtt_config_diagnostics = {}  # ruff: ignore[private-member-access]
    obj._local_mqtt_no_host_warned = False  # ruff: ignore[private-member-access]
    obj._generated_third_party_mqtt_token = None  # ruff: ignore[private-member-access]
    obj._device_index = {"device-1": {}}  # ruff: ignore[private-member-access]
    obj.data = {"device-1": {}}
    call_order: list[str] = []

    def _readback(_device_id: str) -> dict[str, Any]:
        call_order.append("3047")
        return {
            FIELD_THIRD_PARTY_MQTT_ENABLE: 1,
            FIELD_THIRD_PARTY_MQTT_IP: "192.168.2.212",
            FIELD_THIRD_PARTY_MQTT_PORT: _LOCAL_MQTT_PORT,
            FIELD_THIRD_PARTY_MQTT_USERNAME: "mqtt_user",
            FIELD_THIRD_PARTY_MQTT_PASSWORD: "mqtt_password",
            FIELD_THIRD_PARTY_MQTT_TOKEN: "123456789",
        }

    def _set_config(_device_id: str, **kwargs: Any) -> None:
        call_order.append("3046")
        assert kwargs["token"] == "123456789"

    obj._async_query_third_party_mqtt_config_readback = AsyncMock(  # ruff: ignore[private-member-access]
        side_effect=_readback,
    )
    obj.async_set_third_party_mqtt_config = AsyncMock(side_effect=_set_config)

    assert await coordinator.async_apply_local_mqtt_config_to_devices() is True
    assert call_order == ["3047", "3046"]
    obj.hass.config_entries.async_update_entry.assert_not_called()


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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_runtime_discovery_without_new_device_does_not_push_config() -> None:
    """An unchanged discovery snapshot does not emit redundant cmd=113 work."""
    coordinator, schedule = _rediscovery_coordinator(connected=True)
    coordinator.async_discover = AsyncMock(return_value=True)

    await coordinator._async_refresh_discovery_if_due()  # ruff: ignore[private-member-access]

    schedule.assert_not_called()


def test_incomplete_3047_echo_does_not_resolve_readback_waiter() -> None:
    """Incomplete or undecodable 3047 bodies cannot confirm or persist config."""
    coordinator = JackerySolarVaultCoordinator.__new__(
        JackerySolarVaultCoordinator,
    )
    obj = cast("Any", coordinator)
    waiter = MagicMock()
    waiter.done.return_value = False
    obj._third_party_mqtt_config_waiters = {"device-1": [waiter]}  # ruff: ignore[private-member-access]
    observer = MagicMock()
    obj._local_mqtt_config_observer = observer  # ruff: ignore[private-member-access]
    obj._decode_third_party_mqtt_config_body = lambda _device_id, body: dict(body)  # ruff: ignore[private-member-access]

    for body in (
        {},
        {
            FIELD_THIRD_PARTY_MQTT_ENABLE: 1,
            FIELD_THIRD_PARTY_MQTT_IP: "192.168.2.212",
            FIELD_THIRD_PARTY_MQTT_PORT: _LOCAL_MQTT_PORT,
            FIELD_THIRD_PARTY_MQTT_USERNAME: "ciphertext",
            FIELD_THIRD_PARTY_MQTT_PASSWORD: "ciphertext",
            FIELD_THIRD_PARTY_MQTT_TOKEN: "ciphertext",
            "_decode_error": "missing_bluetooth_key",
        },
        {
            FIELD_THIRD_PARTY_MQTT_ENABLE: 1,
            FIELD_THIRD_PARTY_MQTT_IP: "192.168.2.212",
            FIELD_THIRD_PARTY_MQTT_PORT: _LOCAL_MQTT_PORT,
            FIELD_THIRD_PARTY_MQTT_USERNAME: "mqtt_user",
            FIELD_THIRD_PARTY_MQTT_PASSWORD: "mqtt_password",
            FIELD_THIRD_PARTY_MQTT_TOKEN: "",
        },
    ):
        coordinator._store_third_party_mqtt_config_body(  # ruff: ignore[private-member-access]
            "device-1",
            body,
            ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
        )

    waiter.set_result.assert_not_called()
    observer.assert_not_called()

    complete = {
        FIELD_THIRD_PARTY_MQTT_ENABLE: 1,
        FIELD_THIRD_PARTY_MQTT_IP: "192.168.2.212",
        FIELD_THIRD_PARTY_MQTT_PORT: _LOCAL_MQTT_PORT,
        FIELD_THIRD_PARTY_MQTT_USERNAME: "mqtt_user",
        FIELD_THIRD_PARTY_MQTT_PASSWORD: "mqtt_password",
        FIELD_THIRD_PARTY_MQTT_TOKEN: "123456789",
    }
    coordinator._store_third_party_mqtt_config_body(  # ruff: ignore[private-member-access]
        "device-1",
        complete,
        ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
    )

    waiter.set_result.assert_called_once_with(complete)
    observer.assert_not_called()


@pytest.mark.asyncio
async def test_third_party_mqtt_write_retries_stale_complete_readback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale first 3047 response is retried inside the bounded confirmation."""
    coordinator = JackerySolarVaultCoordinator.__new__(
        JackerySolarVaultCoordinator,
    )
    obj = cast("Any", coordinator)
    obj.entry = SimpleNamespace(
        options={CONF_THIRD_PARTY_MQTT_TOKEN: "123456789"},
    )
    obj._generated_third_party_mqtt_token = None  # ruff: ignore[private-member-access]
    obj.device_bluetooth_key = lambda _device_id: b"0123456789abcdef"
    obj._async_publish_command_ble_first = AsyncMock(return_value=None)  # ruff: ignore[private-member-access]
    expected = {
        FIELD_THIRD_PARTY_MQTT_ENABLE: 1,
        FIELD_THIRD_PARTY_MQTT_IP: "192.168.2.212",
        FIELD_THIRD_PARTY_MQTT_PORT: _LOCAL_MQTT_PORT,
        FIELD_THIRD_PARTY_MQTT_USERNAME: "mqtt_user",
        FIELD_THIRD_PARTY_MQTT_PASSWORD: "mqtt_password",
        FIELD_THIRD_PARTY_MQTT_TOKEN: "123456789",
    }
    stale = {
        FIELD_THIRD_PARTY_MQTT_ENABLE: 0,
        FIELD_THIRD_PARTY_MQTT_IP: "",
        FIELD_THIRD_PARTY_MQTT_PORT: _LOCAL_MQTT_PORT,
        FIELD_THIRD_PARTY_MQTT_USERNAME: "",
        FIELD_THIRD_PARTY_MQTT_PASSWORD: "",
        FIELD_THIRD_PARTY_MQTT_TOKEN: "",
    }
    query = AsyncMock(side_effect=(stale, expected))
    obj._async_query_third_party_mqtt_config_readback = query  # ruff: ignore[private-member-access]
    observer = MagicMock()
    obj._local_mqtt_config_observer = observer  # ruff: ignore[private-member-access]
    monkeypatch.setattr(
        coordinator_module,
        "_THIRD_PARTY_MQTT_READBACK_RETRY_DELAY_SEC",
        0.0,
    )

    await coordinator.async_set_third_party_mqtt_config(
        "device-1",
        enable=True,
        ip="192.168.2.212",
        port=_LOCAL_MQTT_PORT,
        username="mqtt_user",
        password="mqtt_password",
        token="123456789",
    )

    assert query.await_count == _EXPECTED_CONFIG_PUSH_COUNT
    observer.assert_called_once_with(expected)
