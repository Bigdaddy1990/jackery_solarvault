"""Tests for the new helper functions added to __init__.py for this integration.

Covers:
- _entry_bootstrap_mqtt_session: validates bootstrap MQTT session from entry.data
- _entry_runtime_bucket: creates/returns mutable per-entry runtime bucket
- _entry_startup_task: retrieves the per-entry background startup task
- _async_cancel_startup_task: cancels a running startup task
- _async_authenticate_api_layer: auth success/failure/transient-error paths
- async_remove_config_entry_device: always returns True
- New _async_start_local_mqtt guard using CONF_LOCAL_MQTT_ENABLE / CONF_LOCAL_MQTT_HOST

These tests use lightweight stubs (no HA fixtures required) to keep the suite fast.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.jackery_solarvault import (
    _STARTUP_TASK_RUNTIME_KEY,  # noqa: PLC2701
    _async_authenticate_api_layer,  # noqa: PLC2701
    _async_cancel_startup_task,  # noqa: PLC2701
    _async_start_local_mqtt,  # noqa: PLC2701
    _entry_bootstrap_mqtt_session,  # noqa: PLC2701
    _entry_runtime_bucket,  # noqa: PLC2701
    _entry_startup_task,  # noqa: PLC2701
    async_remove_config_entry_device,
)
from custom_components.jackery_solarvault.client.api import (
    JackeryAuthError,
    JackeryError,
)
from custom_components.jackery_solarvault.client.mqtt.local_mqtt import (
    JackeryLocalMqttClient,
)
from custom_components.jackery_solarvault.const import (
    CONF_LOCAL_MQTT_ENABLE,
    CONF_LOCAL_MQTT_HOST,
    CONF_LOCAL_MQTT_PASSWORD,
    CONF_LOCAL_MQTT_PORT,
    CONF_LOCAL_MQTT_USERNAME,
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
    DOMAIN,
    ENTRY_BOOTSTRAP_MQTT_SESSION,
    MQTT_SESSION_MAC_ID,
    MQTT_SESSION_MAC_ID_SOURCE,
    MQTT_SESSION_SEED_B64,
    MQTT_SESSION_USER_ID,
)
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _FakeHass:
    """Minimal hass stub with a mutable data dict."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self._tasks: list[asyncio.Task[Any]] = []

    def async_create_background_task(
        self,
        coro: Any,  # noqa: ANN401
        name: str = "",
    ) -> asyncio.Task[Any]:
        task = asyncio.get_event_loop().create_task(coro)
        self._tasks.append(task)
        return task

    def async_create_task(self, coro: Any, name: str = "") -> asyncio.Task[Any]:  # noqa: ANN401
        task = asyncio.get_event_loop().create_task(coro)
        self._tasks.append(task)
        return task


class _FakeEntry:
    """Minimal config-entry stub with options, data, and unload callback support."""

    def __init__(
        self,
        entry_id: str = "test_entry_id_abcd",
        options: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.entry_id = entry_id
        self.options = options or {}
        self.data = data or {}
        self._unload_callbacks: list[Any] = []
        self.runtime_data: Any = None

    def async_on_unload(self, callback: Any) -> None:  # noqa: ANN401
        self._unload_callbacks.append(callback)


def _make_local_mqtt_entry(  # noqa: PLR0913
    *,
    enable: bool = True,
    host: str = "192.168.1.200",
    port: int = 1883,
    username: str = "",
    password: str = "",
    topic_filter: str = "jackery/device/#",
    entry_id: str = "test_entry_id_abcd",
) -> _FakeEntry:
    """Return a _FakeEntry configured with CONF_LOCAL_MQTT_* settings."""
    return _FakeEntry(
        entry_id=entry_id,
        options={
            CONF_LOCAL_MQTT_ENABLE: enable,
            CONF_LOCAL_MQTT_HOST: host,
            CONF_LOCAL_MQTT_PORT: port,
            CONF_LOCAL_MQTT_USERNAME: username,
            CONF_LOCAL_MQTT_PASSWORD: password,
            CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: topic_filter,
        },
    )


# ---------------------------------------------------------------------------
# _entry_bootstrap_mqtt_session
# ---------------------------------------------------------------------------


class TestEntryBootstrapMqttSession:
    """Tests for _entry_bootstrap_mqtt_session()."""

    def test_returns_none_when_no_bootstrap_key_in_data(self) -> None:  # noqa: PLR6301
        """Returns None when ENTRY_BOOTSTRAP_MQTT_SESSION is absent from entry.data."""
        entry = _FakeEntry(data={})
        result = _entry_bootstrap_mqtt_session(entry)
        assert result is None

    def test_returns_none_when_bootstrap_value_is_not_dict(self) -> None:  # noqa: PLR6301
        """Returns None when the bootstrap value is a non-dict type."""
        entry = _FakeEntry(data={ENTRY_BOOTSTRAP_MQTT_SESSION: "not-a-dict"})
        result = _entry_bootstrap_mqtt_session(entry)
        assert result is None

    def test_returns_none_when_bootstrap_value_is_none(self) -> None:  # noqa: PLR6301
        """Returns None when the bootstrap value is None."""
        entry = _FakeEntry(data={ENTRY_BOOTSTRAP_MQTT_SESSION: None})
        result = _entry_bootstrap_mqtt_session(entry)
        assert result is None

    def test_returns_none_when_required_keys_missing(self) -> None:  # noqa: PLR6301
        """Returns None when the dict lacks required user_id, seed_b64, or mac_id."""
        entry = _FakeEntry(
            data={
                ENTRY_BOOTSTRAP_MQTT_SESSION: {
                    MQTT_SESSION_USER_ID: "user1",
                    # seed_b64 and mac_id missing
                },
            },
        )
        result = _entry_bootstrap_mqtt_session(entry)
        assert result is None

    def test_returns_none_when_any_required_value_is_empty_string(self) -> None:  # noqa: PLR6301
        """Returns None when a required value is an empty string."""
        entry = _FakeEntry(
            data={
                ENTRY_BOOTSTRAP_MQTT_SESSION: {
                    MQTT_SESSION_USER_ID: "",
                    MQTT_SESSION_SEED_B64: "seed=",
                    MQTT_SESSION_MAC_ID: "mac123",
                },
            },
        )
        result = _entry_bootstrap_mqtt_session(entry)
        assert result is None

    def test_returns_none_when_any_required_value_is_non_string(self) -> None:  # noqa: PLR6301
        """Returns None when a required value is not a string (e.g. int)."""
        entry = _FakeEntry(
            data={
                ENTRY_BOOTSTRAP_MQTT_SESSION: {
                    MQTT_SESSION_USER_ID: 42,  # not a string
                    MQTT_SESSION_SEED_B64: "seed=",
                    MQTT_SESSION_MAC_ID: "mac123",
                },
            },
        )
        result = _entry_bootstrap_mqtt_session(entry)
        assert result is None

    def test_returns_snapshot_with_all_required_fields(self) -> None:  # noqa: PLR6301
        """Returns dict with user_id, seed_b64, mac_id when all fields are valid."""
        entry = _FakeEntry(
            data={
                ENTRY_BOOTSTRAP_MQTT_SESSION: {
                    MQTT_SESSION_USER_ID: "user-abc",
                    MQTT_SESSION_SEED_B64: "c2VlZF9iYXNlNjQ=",
                    MQTT_SESSION_MAC_ID: "2abc123456789abc",
                },
            },
        )
        result = _entry_bootstrap_mqtt_session(entry)
        assert result is not None
        assert result[MQTT_SESSION_USER_ID] == "user-abc"
        assert result[MQTT_SESSION_SEED_B64] == "c2VlZF9iYXNlNjQ="
        assert result[MQTT_SESSION_MAC_ID] == "2abc123456789abc"

    def test_includes_optional_mac_id_source_when_present(self) -> None:  # noqa: PLR6301
        """Includes MQTT_SESSION_MAC_ID_SOURCE when it is a non-empty string."""
        entry = _FakeEntry(
            data={
                ENTRY_BOOTSTRAP_MQTT_SESSION: {
                    MQTT_SESSION_USER_ID: "user-abc",
                    MQTT_SESSION_SEED_B64: "c2VlZA==",
                    MQTT_SESSION_MAC_ID: "2abc",
                    MQTT_SESSION_MAC_ID_SOURCE: "configured",
                },
            },
        )
        result = _entry_bootstrap_mqtt_session(entry)
        assert result is not None
        assert result[MQTT_SESSION_MAC_ID_SOURCE] == "configured"

    def test_omits_mac_id_source_when_empty_string(self) -> None:  # noqa: PLR6301
        """Omits MQTT_SESSION_MAC_ID_SOURCE from the snapshot when it is an empty.

        string.
        """
        entry = _FakeEntry(
            data={
                ENTRY_BOOTSTRAP_MQTT_SESSION: {
                    MQTT_SESSION_USER_ID: "user-abc",
                    MQTT_SESSION_SEED_B64: "c2VlZA==",
                    MQTT_SESSION_MAC_ID: "2abc",
                    MQTT_SESSION_MAC_ID_SOURCE: "",
                },
            },
        )
        result = _entry_bootstrap_mqtt_session(entry)
        assert result is not None
        assert MQTT_SESSION_MAC_ID_SOURCE not in result

    def test_omits_mac_id_source_when_not_string(self) -> None:  # noqa: PLR6301
        """Omits MQTT_SESSION_MAC_ID_SOURCE when its value is not a string."""
        entry = _FakeEntry(
            data={
                ENTRY_BOOTSTRAP_MQTT_SESSION: {
                    MQTT_SESSION_USER_ID: "user-abc",
                    MQTT_SESSION_SEED_B64: "c2VlZA==",
                    MQTT_SESSION_MAC_ID: "2abc",
                    MQTT_SESSION_MAC_ID_SOURCE: 123,  # int, not str
                },
            },
        )
        result = _entry_bootstrap_mqtt_session(entry)
        assert result is not None
        assert MQTT_SESSION_MAC_ID_SOURCE not in result

    def test_snapshot_does_not_include_extra_keys(self) -> None:  # noqa: PLR6301
        """The snapshot only includes the documented MQTT session keys."""
        entry = _FakeEntry(
            data={
                ENTRY_BOOTSTRAP_MQTT_SESSION: {
                    MQTT_SESSION_USER_ID: "uid",
                    MQTT_SESSION_SEED_B64: "c2VlZA==",
                    MQTT_SESSION_MAC_ID: "mac",
                    "unexpected_extra_key": "surprise",
                },
            },
        )
        result = _entry_bootstrap_mqtt_session(entry)
        assert result is not None
        assert "unexpected_extra_key" not in result


# ---------------------------------------------------------------------------
# _entry_runtime_bucket
# ---------------------------------------------------------------------------


class TestEntryRuntimeBucket:
    """Tests for _entry_runtime_bucket()."""

    def test_creates_domain_key_if_absent(self) -> None:  # noqa: PLR6301
        """Creates hass.data[DOMAIN] if it does not exist."""
        hass = _FakeHass()
        entry = _FakeEntry(entry_id="e1")
        _entry_runtime_bucket(hass, entry)
        assert DOMAIN in hass.data

    def test_creates_entry_bucket_if_absent(self) -> None:  # noqa: PLR6301
        """Creates hass.data[DOMAIN][entry_id] if it does not exist."""
        hass = _FakeHass()
        entry = _FakeEntry(entry_id="e2")
        bucket = _entry_runtime_bucket(hass, entry)
        assert isinstance(bucket, dict)
        assert hass.data[DOMAIN]["e2"] is bucket

    def test_returns_same_bucket_on_repeated_calls(self) -> None:  # noqa: PLR6301
        """Repeated calls for the same entry return the identical dict object."""
        hass = _FakeHass()
        entry = _FakeEntry(entry_id="e3")
        bucket1 = _entry_runtime_bucket(hass, entry)
        bucket2 = _entry_runtime_bucket(hass, entry)
        assert bucket1 is bucket2

    def test_preserves_existing_bucket_contents(self) -> None:  # noqa: PLR6301
        """Does not overwrite an existing bucket when one is already present."""
        hass = _FakeHass()
        entry = _FakeEntry(entry_id="e4")
        hass.data[DOMAIN] = {"e4": {"existing": "value"}}
        bucket = _entry_runtime_bucket(hass, entry)
        assert bucket["existing"] == "value"

    def test_separate_entries_get_separate_buckets(self) -> None:  # noqa: PLR6301
        """Different entry_ids must produce separate bucket objects."""
        hass = _FakeHass()
        entry_a = _FakeEntry(entry_id="ea")
        entry_b = _FakeEntry(entry_id="eb")
        bucket_a = _entry_runtime_bucket(hass, entry_a)
        bucket_b = _entry_runtime_bucket(hass, entry_b)
        assert bucket_a is not bucket_b


# ---------------------------------------------------------------------------
# _entry_startup_task
# ---------------------------------------------------------------------------


class TestEntryStartupTask:
    """Tests for _entry_startup_task()."""

    def test_returns_none_when_domain_absent(self) -> None:  # noqa: PLR6301
        """Returns None when DOMAIN is not in hass.data."""
        hass = _FakeHass()
        entry = _FakeEntry(entry_id="e1")
        result = _entry_startup_task(hass, entry)
        assert result is None

    def test_returns_none_when_entry_absent(self) -> None:  # noqa: PLR6301
        """Returns None when entry_id is not in hass.data[DOMAIN]."""
        hass = _FakeHass()
        hass.data[DOMAIN] = {}
        entry = _FakeEntry(entry_id="e1")
        result = _entry_startup_task(hass, entry)
        assert result is None

    def test_returns_none_when_bucket_is_not_dict(self) -> None:  # noqa: PLR6301
        """Returns None when hass.data[DOMAIN][entry_id] is not a dict."""
        hass = _FakeHass()
        entry = _FakeEntry(entry_id="e1")
        hass.data[DOMAIN] = {"e1": "not-a-dict"}
        result = _entry_startup_task(hass, entry)
        assert result is None

    def test_returns_none_when_startup_key_absent(self) -> None:  # noqa: PLR6301
        """Returns None when _STARTUP_TASK_RUNTIME_KEY is not in the bucket."""
        hass = _FakeHass()
        entry = _FakeEntry(entry_id="e1")
        hass.data[DOMAIN] = {"e1": {}}
        result = _entry_startup_task(hass, entry)
        assert result is None

    def test_returns_none_when_task_is_not_asyncio_task(self) -> None:  # noqa: PLR6301
        """Returns None when the stored value is not an asyncio.Task."""
        hass = _FakeHass()
        entry = _FakeEntry(entry_id="e1")
        hass.data[DOMAIN] = {"e1": {_STARTUP_TASK_RUNTIME_KEY: "not-a-task"}}
        result = _entry_startup_task(hass, entry)
        assert result is None

    async def test_returns_task_when_present(self) -> None:  # noqa: PLR6301
        """Returns the stored asyncio.Task when one is registered in hass.data."""
        hass = _FakeHass()
        entry = _FakeEntry(entry_id="e1")

        async def _noop() -> None:
            pass

        task: asyncio.Task[None] = asyncio.get_event_loop().create_task(_noop())
        hass.data[DOMAIN] = {"e1": {_STARTUP_TASK_RUNTIME_KEY: task}}
        result = _entry_startup_task(hass, entry)
        assert result is task
        # Clean up
        await task


# ---------------------------------------------------------------------------
# _async_cancel_startup_task
# ---------------------------------------------------------------------------


class TestAsyncCancelStartupTask:
    """Tests for _async_cancel_startup_task()."""

    async def test_does_nothing_when_no_task_registered(self) -> None:  # noqa: PLR6301
        """No error when called with no task in hass.data."""
        hass = _FakeHass()
        entry = _FakeEntry(entry_id="e1")
        # Should not raise
        await _async_cancel_startup_task(hass, entry)

    async def test_cancels_running_task(self) -> None:  # noqa: PLR6301
        """Cancels an in-flight background startup task."""
        hass = _FakeHass()
        entry = _FakeEntry(entry_id="e1")

        cancelled: list[bool] = []

        async def _long_running() -> None:
            try:
                await asyncio.sleep(3600)
            except asyncio.CancelledError:
                cancelled.append(True)
                raise

        task: asyncio.Task[None] = asyncio.get_event_loop().create_task(_long_running())
        # Give the task a chance to start
        await asyncio.sleep(0)
        hass.data[DOMAIN] = {"e1": {_STARTUP_TASK_RUNTIME_KEY: task}}

        await _async_cancel_startup_task(hass, entry)

        assert task.done()
        assert task.cancelled()
        assert cancelled

    async def test_removes_task_from_bucket_after_cancel(self) -> None:  # noqa: PLR6301
        """Removes the startup task key from hass.data after cancellation."""
        hass = _FakeHass()
        entry = _FakeEntry(entry_id="e1")

        async def _noop() -> None:
            await asyncio.sleep(3600)

        task: asyncio.Task[None] = asyncio.get_event_loop().create_task(_noop())
        await asyncio.sleep(0)
        bucket: dict[str, Any] = {_STARTUP_TASK_RUNTIME_KEY: task}
        hass.data[DOMAIN] = {"e1": bucket}

        await _async_cancel_startup_task(hass, entry)

        assert _STARTUP_TASK_RUNTIME_KEY not in bucket

    async def test_does_not_raise_when_task_already_done(self) -> None:  # noqa: PLR6301
        """No error when the task has already completed before cancel is called."""
        hass = _FakeHass()
        entry = _FakeEntry(entry_id="e1")

        async def _instant() -> None:
            pass

        task: asyncio.Task[None] = asyncio.get_event_loop().create_task(_instant())
        await task  # Task finishes immediately

        hass.data[DOMAIN] = {"e1": {_STARTUP_TASK_RUNTIME_KEY: task}}
        # Should not raise even though task is already done
        await _async_cancel_startup_task(hass, entry)


# ---------------------------------------------------------------------------
# async_remove_config_entry_device
# ---------------------------------------------------------------------------


class TestAsyncRemoveConfigEntryDevice:
    """Tests for async_remove_config_entry_device()."""

    async def test_always_returns_true(self) -> None:  # noqa: PLR6301
        """Cloud integrations always allow device removal from the UI."""
        hass = MagicMock()
        entry = MagicMock()
        device_entry = MagicMock()
        result = await async_remove_config_entry_device(hass, entry, device_entry)
        assert result is True

    async def test_returns_true_for_any_device(self) -> None:  # noqa: PLR6301
        """Return value is True regardless of the device entry provided."""
        hass = MagicMock()
        entry = MagicMock()
        for i in range(3):
            device_entry = MagicMock()
            device_entry.id = f"device_{i}"
            result = await async_remove_config_entry_device(hass, entry, device_entry)
            assert result is True


# ---------------------------------------------------------------------------
# _async_start_local_mqtt with CONF_LOCAL_MQTT_ENABLE / CONF_LOCAL_MQTT_HOST
# ---------------------------------------------------------------------------


class TestAsyncStartLocalMqttNewKeys:
    """Tests for _async_start_local_mqtt using the new CONF_LOCAL_MQTT_* config keys."""

    async def test_disabled_local_mqtt_enable_prevents_start(self) -> None:  # noqa: PLR6301
        """CONF_LOCAL_MQTT_ENABLE=False must prevent client creation."""
        hass = _FakeHass()
        entry = _make_local_mqtt_entry(enable=False, host="192.168.1.200")
        coordinator = MagicMock()

        await _async_start_local_mqtt(hass, entry, coordinator)

        bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        assert "local_mqtt_client" not in bucket

    async def test_enabled_with_valid_host_and_filter_creates_client(self) -> None:  # noqa: PLR6301
        """CONF_LOCAL_MQTT_ENABLE=True with a valid host and topic filter must create.

        the client.
        """
        hass = _FakeHass()
        entry = _make_local_mqtt_entry(
            enable=True,
            host="192.168.1.200",
            topic_filter="jackery/sv/+",
        )
        coordinator = MagicMock()
        coordinator.async_handle_local_mqtt_message = AsyncMock()

        with patch.object(
            JackeryLocalMqttClient,
            "async_start",
            new_callable=AsyncMock,
        ):
            await _async_start_local_mqtt(hass, entry, coordinator)

        client = (
            hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("local_mqtt_client")
        )
        assert isinstance(client, JackeryLocalMqttClient)

    async def test_empty_host_prevents_client_creation(self) -> None:  # noqa: PLR6301
        """An empty CONF_LOCAL_MQTT_HOST must prevent client creation."""
        hass = _FakeHass()
        entry = _make_local_mqtt_entry(enable=True, host="")
        coordinator = MagicMock()

        await _async_start_local_mqtt(hass, entry, coordinator)

        bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        assert "local_mqtt_client" not in bucket

    async def test_whitespace_only_host_prevents_client_creation(self) -> None:  # noqa: PLR6301
        """A whitespace-only CONF_LOCAL_MQTT_HOST must be stripped and treated as.

        empty.
        """
        hass = _FakeHass()
        entry = _make_local_mqtt_entry(enable=True, host="   ")
        coordinator = MagicMock()

        await _async_start_local_mqtt(hass, entry, coordinator)

        bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        assert "local_mqtt_client" not in bucket

    async def test_empty_topic_filter_prevents_client_creation(self) -> None:  # noqa: PLR6301
        """An empty topic filter must keep the listener disabled."""
        hass = _FakeHass()
        entry = _make_local_mqtt_entry(
            enable=True,
            host="192.168.1.200",
            topic_filter="",
        )
        coordinator = MagicMock()

        await _async_start_local_mqtt(hass, entry, coordinator)

        bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        assert "local_mqtt_client" not in bucket

    async def test_broad_wildcard_hash_is_blocked(self) -> None:  # noqa: PLR6301
        """The '#' topic filter must be blocked for CPU safety."""
        hass = _FakeHass()
        entry = _make_local_mqtt_entry(
            enable=True,
            host="192.168.1.200",
            topic_filter="#",
        )
        coordinator = MagicMock()

        await _async_start_local_mqtt(hass, entry, coordinator)

        bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        assert "local_mqtt_client" not in bucket

    async def test_broad_wildcard_plus_hash_is_blocked(self) -> None:  # noqa: PLR6301
        """The '+/#' topic filter must be blocked for CPU safety."""
        hass = _FakeHass()
        entry = _make_local_mqtt_entry(
            enable=True,
            host="192.168.1.200",
            topic_filter="+/#",
        )
        coordinator = MagicMock()

        await _async_start_local_mqtt(hass, entry, coordinator)

        bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        assert "local_mqtt_client" not in bucket

    async def test_scoped_filter_is_not_blocked(self) -> None:  # noqa: PLR6301
        """A scoped filter like 'hb/app/+/device' must NOT be blocked."""
        hass = _FakeHass()
        entry = _make_local_mqtt_entry(
            enable=True,
            host="192.168.1.200",
            topic_filter="hb/app/+/device",
        )
        coordinator = MagicMock()
        coordinator.async_handle_local_mqtt_message = AsyncMock()

        with patch.object(
            JackeryLocalMqttClient,
            "async_start",
            new_callable=AsyncMock,
        ):
            await _async_start_local_mqtt(hass, entry, coordinator)

        client = (
            hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get("local_mqtt_client")
        )
        assert isinstance(client, JackeryLocalMqttClient)

    async def test_client_id_uses_first_8_chars_of_entry_id(self) -> None:  # noqa: PLR6301
        """The MQTT client_id must be 'ha-jackery-' + first 8 chars of entry_id."""
        hass = _FakeHass()
        entry_id = "XYZWABCD1234"
        entry = _make_local_mqtt_entry(
            enable=True,
            host="192.168.1.200",
            entry_id=entry_id,
        )
        coordinator = MagicMock()
        coordinator.async_handle_local_mqtt_message = AsyncMock()

        with patch.object(
            JackeryLocalMqttClient,
            "async_start",
            new_callable=AsyncMock,
        ):
            await _async_start_local_mqtt(hass, entry, coordinator)

        client = hass.data[DOMAIN][entry_id]["local_mqtt_client"]
        assert client._client_id == f"ha-jackery-{entry_id[:8]}"  # noqa: SLF001

    async def test_topic_filter_is_passed_to_client(self) -> None:  # noqa: PLR6301
        """The configured topic filter must appear on the created client."""
        hass = _FakeHass()
        entry = _make_local_mqtt_entry(
            enable=True,
            host="192.168.1.200",
            topic_filter="sv3pro/+/status",
        )
        coordinator = MagicMock()
        coordinator.async_handle_local_mqtt_message = AsyncMock()

        with patch.object(
            JackeryLocalMqttClient,
            "async_start",
            new_callable=AsyncMock,
        ):
            await _async_start_local_mqtt(hass, entry, coordinator)

        client = hass.data[DOMAIN][entry.entry_id]["local_mqtt_client"]
        assert client._topic_filter == "sv3pro/+/status"  # noqa: SLF001

    async def test_sink_forwards_non_none_data_to_coordinator(self) -> None:  # noqa: PLR6301
        """The sink function must call coordinator.async_handle_local_mqtt_message for.

        non-None data.
        """
        hass = _FakeHass()
        entry = _make_local_mqtt_entry(
            enable=True,
            host="192.168.1.200",
            topic_filter="jackery/+/data",
        )
        handle_calls: list[tuple[str, dict[str, Any]]] = []

        async def _handle(topic: str, data: dict[str, Any]) -> None:  # noqa: RUF029
            handle_calls.append((topic, data))

        coordinator = MagicMock()
        coordinator.async_handle_local_mqtt_message = _handle

        with patch.object(
            JackeryLocalMqttClient,
            "async_start",
            new_callable=AsyncMock,
        ):
            await _async_start_local_mqtt(hass, entry, coordinator)

        # Retrieve the sink and call it directly
        client = hass.data[DOMAIN][entry.entry_id]["local_mqtt_client"]
        sink = client._sink  # noqa: SLF001
        await sink("jackery/device1/data", {"soc": 80}, b'{"soc":80}')

        assert handle_calls == [("jackery/device1/data", {"soc": 80})]

    async def test_sink_ignores_none_data(self) -> None:  # noqa: PLR6301
        """The sink must not forward a message when data is None."""
        hass = _FakeHass()
        entry = _make_local_mqtt_entry(
            enable=True,
            host="192.168.1.200",
            topic_filter="jackery/+/data",
        )
        coordinator = MagicMock()
        handle_calls: list[Any] = []
        coordinator.async_handle_local_mqtt_message = AsyncMock(
            side_effect=lambda *a, **kw: handle_calls.append(a),
        )

        with patch.object(
            JackeryLocalMqttClient,
            "async_start",
            new_callable=AsyncMock,
        ):
            await _async_start_local_mqtt(hass, entry, coordinator)

        client = hass.data[DOMAIN][entry.entry_id]["local_mqtt_client"]
        sink = client._sink  # noqa: SLF001
        await sink("some/topic", None, b"raw")

        assert not handle_calls

    async def test_unload_callback_registered(self) -> None:  # noqa: PLR6301
        """_async_start_local_mqtt must register exactly one unload callback."""
        hass = _FakeHass()
        entry = _make_local_mqtt_entry(enable=True, host="192.168.1.200")
        coordinator = MagicMock()
        coordinator.async_handle_local_mqtt_message = AsyncMock()

        with patch.object(
            JackeryLocalMqttClient,
            "async_start",
            new_callable=AsyncMock,
        ):
            await _async_start_local_mqtt(hass, entry, coordinator)

        assert len(entry._unload_callbacks) == 1  # noqa: SLF001

    async def test_unload_callback_stops_and_removes_client(self) -> None:  # noqa: PLR6301
        """The unload callback stops the client and removes it from hass.data."""
        hass = _FakeHass()
        entry = _make_local_mqtt_entry(enable=True, host="192.168.1.200")
        coordinator = MagicMock()
        coordinator.async_handle_local_mqtt_message = AsyncMock()

        with patch.object(
            JackeryLocalMqttClient,
            "async_start",
            new_callable=AsyncMock,
        ):
            await _async_start_local_mqtt(hass, entry, coordinator)

        bucket = hass.data[DOMAIN][entry.entry_id]
        client = bucket["local_mqtt_client"]

        stop_calls: list[bool] = []

        async def _fake_stop() -> None:  # noqa: RUF029
            stop_calls.append(True)

        with patch.object(client, "async_stop", new=_fake_stop):
            entry._unload_callbacks[0]()  # noqa: SLF001
            await asyncio.gather(*hass._tasks)  # noqa: SLF001

        assert stop_calls
        assert "local_mqtt_client" not in bucket


# ---------------------------------------------------------------------------
# _async_authenticate_api_layer
# ---------------------------------------------------------------------------


class TestAsyncAuthenticateApiLayer:
    """Tests for _async_authenticate_api_layer()."""

    async def test_raises_config_entry_auth_failed_on_jackery_auth_error(  # noqa: PLR6301
        self,
    ) -> None:
        """JackeryAuthError from async_login must be re-raised as.

        ConfigEntryAuthFailed.
        """
        hass = _FakeHass()
        entry = _FakeEntry()
        api = MagicMock()
        api.async_login = AsyncMock(side_effect=JackeryAuthError("bad creds"))
        api.mqtt_session_snapshot = MagicMock(return_value=None)

        with (
            patch(
                "custom_components.jackery_solarvault._async_prime_entry_bootstrap_mqtt_session",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "custom_components.jackery_solarvault.async_load_mqtt_session",
                new_callable=AsyncMock,
                return_value=None,
            ),
            pytest.raises(ConfigEntryAuthFailed, match="rejected the credentials"),
        ):
            await _async_authenticate_api_layer(hass, entry, api)

    async def test_jackery_error_blocks_layer5_startup(self) -> None:  # noqa: PLR6301
        """A transient JackeryError must block Layer 5 until HTTP login succeeds."""
        hass = _FakeHass()
        entry = _FakeEntry()
        api = MagicMock()
        api.async_login = AsyncMock(side_effect=JackeryError("timeout"))
        api.mqtt_session_snapshot = MagicMock(return_value=None)

        with (
            patch(
                "custom_components.jackery_solarvault._async_prime_entry_bootstrap_mqtt_session",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "custom_components.jackery_solarvault.async_load_mqtt_session",
                new_callable=AsyncMock,
                return_value=None,
            ),
            pytest.raises(UpdateFailed, match="Layer 5 startup is blocked"),
        ):
            await _async_authenticate_api_layer(hass, entry, api)

    async def test_saves_new_mqtt_session_after_successful_login(self) -> None:  # noqa: PLR6301
        """After a successful login, a changed MQTT session snapshot must be.

        persisted.
        """
        hass = _FakeHass()
        entry = _FakeEntry()
        api = MagicMock()
        api.async_login = AsyncMock(return_value="token")
        new_snapshot = {
            MQTT_SESSION_USER_ID: "uid_new",
            MQTT_SESSION_SEED_B64: "c2VlZA==",
            MQTT_SESSION_MAC_ID: "2newmac",
        }
        api.mqtt_session_snapshot = MagicMock(return_value=new_snapshot)

        saved: list[dict[str, Any]] = []

        async def _fake_save(h: Any, entry_id: str, **kwargs: Any) -> None:  # noqa: ANN401, RUF029
            saved.append(kwargs)

        with (
            patch(
                "custom_components.jackery_solarvault._async_prime_entry_bootstrap_mqtt_session",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "custom_components.jackery_solarvault.async_load_mqtt_session",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "custom_components.jackery_solarvault.async_save_mqtt_session",
                new=_fake_save,
            ),
        ):
            await _async_authenticate_api_layer(hass, entry, api)

        assert len(saved) == 1
        assert saved[0][MQTT_SESSION_USER_ID] == "uid_new"

    async def test_does_not_save_session_when_snapshot_unchanged(self) -> None:  # noqa: PLR6301
        """When the MQTT session snapshot matches cached data, no save is performed."""
        hass = _FakeHass()
        entry = _FakeEntry()
        api = MagicMock()
        api.async_login = AsyncMock(return_value="token")
        cached = {
            MQTT_SESSION_USER_ID: "uid_cached",
            MQTT_SESSION_SEED_B64: "c2VlZA==",
            MQTT_SESSION_MAC_ID: "2cachedmac",
        }
        api.mqtt_session_snapshot = MagicMock(return_value=cached)

        save_called: list[bool] = []

        async def _fake_save(*a: Any, **kw: Any) -> None:  # noqa: ANN401, RUF029
            save_called.append(True)

        with (
            patch(
                "custom_components.jackery_solarvault._async_prime_entry_bootstrap_mqtt_session",
                new_callable=AsyncMock,
                return_value=cached,
            ),
            patch(
                "custom_components.jackery_solarvault.async_load_mqtt_session",
                new_callable=AsyncMock,
                return_value=cached,
            ),
            patch(
                "custom_components.jackery_solarvault.async_save_mqtt_session",
                new=_fake_save,
            ),
        ):
            await _async_authenticate_api_layer(hass, entry, api)

        assert not save_called

    async def test_does_not_save_session_when_snapshot_is_none(self) -> None:  # noqa: PLR6301
        """No save is performed when mqtt_session_snapshot returns None."""
        hass = _FakeHass()
        entry = _FakeEntry()
        api = MagicMock()
        api.async_login = AsyncMock(return_value="token")
        api.mqtt_session_snapshot = MagicMock(return_value=None)

        save_called: list[bool] = []

        async def _fake_save(*a: Any, **kw: Any) -> None:  # noqa: ANN401, RUF029
            save_called.append(True)

        with (
            patch(
                "custom_components.jackery_solarvault._async_prime_entry_bootstrap_mqtt_session",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "custom_components.jackery_solarvault.async_load_mqtt_session",
                new_callable=AsyncMock,
                return_value=None,
            ),
            patch(
                "custom_components.jackery_solarvault.async_save_mqtt_session",
                new=_fake_save,
            ),
        ):
            await _async_authenticate_api_layer(hass, entry, api)

        assert not save_called
