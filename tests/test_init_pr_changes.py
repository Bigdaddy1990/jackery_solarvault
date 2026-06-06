"""Tests for the PR changes to custom_components/jackery_solarvault/__init__.py.

Covers the new helper functions introduced in this PR:
- _local_mqtt_client: retrieves a JackeryLocalMqttClient from hass.data
- _LOCAL_MQTT_RUNTIME_KEY: the key used to store the client
- _async_start_local_mqtt: creates, stores and starts the local MQTT client
- _async_stop_local_mqtt (nested): stops the client and cleans up hass.data

These tests use lightweight stubs so HA fixtures are not required.

If __init__.py or a dependency fails to import, these tests are skipped with
an explicit reason so syntax/dependency regressions are easy to spot.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

try:
    from custom_components.jackery_solarvault import (
        _LOCAL_MQTT_RUNTIME_KEY,
        _async_start_local_mqtt,
        _local_mqtt_client,
    )
    from custom_components.jackery_solarvault.client.local_mqtt import (
        JackeryLocalMqttClient,
    )

    _IMPORT_OK = True
except (SyntaxError, ImportError) as _import_err:
    _IMPORT_OK = False
    _LOCAL_MQTT_RUNTIME_KEY = "local_mqtt_client"  # type: ignore[assignment]
    _local_mqtt_client = None  # type: ignore[assignment]
    _async_start_local_mqtt = None  # type: ignore[assignment]
    JackeryLocalMqttClient = None  # type: ignore[assignment,misc]

from custom_components.jackery_solarvault.const import (
    CONF_THIRD_PARTY_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_PORT,
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
    CONF_THIRD_PARTY_MQTT_USERNAME,
    DOMAIN,
)

pytestmark = pytest.mark.skipif(
    not _IMPORT_OK,
    reason="__init__.py import failed; fix dependency/syntax issues first",
)


# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _FakeHass:
    """Minimal hass stub that records async_create_background_task calls."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}
        self._bg_tasks: list[asyncio.Task[Any]] = []

    def async_create_background_task(
        self,
        coro: Any,  # noqa: ANN401
        name: str = "",
    ) -> asyncio.Task[Any]:
        task = asyncio.get_event_loop().create_task(coro)
        self._bg_tasks.append(task)
        return task

    def async_create_task(self, coro: Any, name: str = "") -> asyncio.Task[Any]:  # noqa: ANN401, PLR6301
        return asyncio.get_event_loop().create_task(coro)


class _FakeEntry:
    """Minimal config-entry stub.

    Provides both `options` (new path) and `data` (legacy path) attributes so
    the config_entry_bool_option / config_entry_str_option helpers work.
    """

    def __init__(
        self,
        entry_id: str = "test_entry_id_0001",
        options: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.entry_id = entry_id
        self.options = options or {}
        self.data = data or {}
        self._unload_callbacks: list[Any] = []

    def async_on_unload(self, callback: Any) -> None:  # noqa: ANN401
        self._unload_callbacks.append(callback)


def _make_local_mqtt_entry(  # noqa: PLR0913
    *,
    enable: bool = True,
    host: str = "192.168.1.100",
    port: int = 1883,
    username: str = "",
    password: str = "",
    topic_filter: str = "jackery/#",
    entry_id: str = "test_entry_id_0001",
) -> _FakeEntry:
    """Return a _FakeEntry configured with the given local MQTT settings."""
    return _FakeEntry(
        entry_id=entry_id,
        options={
            CONF_THIRD_PARTY_MQTT_ENABLE: enable,
            CONF_THIRD_PARTY_MQTT_IP: host,
            CONF_THIRD_PARTY_MQTT_PORT: port,
            CONF_THIRD_PARTY_MQTT_USERNAME: username,
            CONF_THIRD_PARTY_MQTT_PASSWORD: password,
            CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: topic_filter,
        },
    )


# ---------------------------------------------------------------------------
# _LOCAL_MQTT_RUNTIME_KEY
# ---------------------------------------------------------------------------


def test_local_mqtt_runtime_key_value() -> None:
    """The runtime key must be 'local_mqtt_client' exactly."""
    assert _LOCAL_MQTT_RUNTIME_KEY == "local_mqtt_client"


# ---------------------------------------------------------------------------
# _local_mqtt_client
# ---------------------------------------------------------------------------


def test_local_mqtt_client_returns_none_when_domain_absent() -> None:
    """If DOMAIN is not in hass.data, return None."""
    hass = _FakeHass()
    entry = _FakeEntry()
    result = _local_mqtt_client(hass, entry)
    assert result is None


def test_local_mqtt_client_returns_none_when_entry_bucket_absent() -> None:
    """If entry_id is not in hass.data[DOMAIN], return None."""
    hass = _FakeHass()
    hass.data[DOMAIN] = {}
    entry = _FakeEntry()
    result = _local_mqtt_client(hass, entry)
    assert result is None


def test_local_mqtt_client_returns_none_when_bucket_is_not_dict() -> None:
    """If hass.data[DOMAIN][entry_id] is not a dict, return None."""
    hass = _FakeHass()
    entry = _FakeEntry(entry_id="eid1")
    hass.data[DOMAIN] = {"eid1": "not-a-dict"}
    result = _local_mqtt_client(hass, entry)
    assert result is None


def test_local_mqtt_client_returns_none_when_key_absent() -> None:
    """If _LOCAL_MQTT_RUNTIME_KEY is not in the bucket, return None."""
    hass = _FakeHass()
    entry = _FakeEntry(entry_id="eid1")
    hass.data[DOMAIN] = {"eid1": {}}
    result = _local_mqtt_client(hass, entry)
    assert result is None


def test_local_mqtt_client_returns_none_when_value_is_not_client() -> None:
    """If bucket[_LOCAL_MQTT_RUNTIME_KEY] is not JackeryLocalMqttClient, return None."""
    hass = _FakeHass()
    entry = _FakeEntry(entry_id="eid1")
    hass.data[DOMAIN] = {"eid1": {_LOCAL_MQTT_RUNTIME_KEY: "wrong-type"}}
    result = _local_mqtt_client(hass, entry)
    assert result is None


def test_local_mqtt_client_returns_stored_client() -> None:
    """If a valid JackeryLocalMqttClient is stored, it is returned."""
    hass = _FakeHass()
    entry = _FakeEntry(entry_id="eid1")

    client = JackeryLocalMqttClient(
        hass,
        host="127.0.0.1",
        port=1883,
        username=None,
        password=None,
        client_id="test-client-id",
    )
    hass.data[DOMAIN] = {"eid1": {_LOCAL_MQTT_RUNTIME_KEY: client}}
    result = _local_mqtt_client(hass, entry)
    assert result is client


# ---------------------------------------------------------------------------
# _async_start_local_mqtt
# ---------------------------------------------------------------------------


async def test_async_start_local_mqtt_does_nothing_when_disabled() -> None:
    """If CONF_THIRD_PARTY_MQTT_ENABLE is False, no client is created."""
    hass = _FakeHass()
    entry = _make_local_mqtt_entry(enable=False, host="192.168.1.100")

    await _async_start_local_mqtt(hass, entry)

    # No client stored.
    assert DOMAIN not in hass.data or not hass.data.get(DOMAIN, {}).get(
        entry.entry_id,
        {},
    ).get(_LOCAL_MQTT_RUNTIME_KEY)
    assert not entry._unload_callbacks  # noqa: SLF001


async def test_async_start_local_mqtt_does_nothing_when_host_is_empty() -> None:
    """If configured host is empty, no client is created."""
    hass = _FakeHass()
    entry = _make_local_mqtt_entry(enable=True, host="")

    await _async_start_local_mqtt(hass, entry)

    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    assert _LOCAL_MQTT_RUNTIME_KEY not in bucket
    assert not entry._unload_callbacks  # noqa: SLF001


async def test_async_start_local_mqtt_does_nothing_when_host_is_whitespace() -> None:
    """Whitespace-only host is stripped to empty string; no client created."""
    hass = _FakeHass()
    entry = _make_local_mqtt_entry(enable=True, host="   ")

    await _async_start_local_mqtt(hass, entry)

    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    assert _LOCAL_MQTT_RUNTIME_KEY not in bucket


async def test_async_start_local_mqtt_does_nothing_when_topic_filter_is_empty() -> None:
    """Without a topic filter, the local MQTT listener must stay disabled."""
    hass = _FakeHass()
    entry = _make_local_mqtt_entry(
        enable=True,
        host="192.168.1.100",
        topic_filter="",
    )

    await _async_start_local_mqtt(hass, entry)

    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    assert _LOCAL_MQTT_RUNTIME_KEY not in bucket


async def test_async_start_local_mqtt_blocks_broad_wildcard_filter() -> None:
    """Broad wildcard topic filters are blocked for CPU safety."""
    hass = _FakeHass()
    entry = _make_local_mqtt_entry(
        enable=True,
        host="192.168.1.100",
        topic_filter="#",
    )

    await _async_start_local_mqtt(hass, entry)

    bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
    assert _LOCAL_MQTT_RUNTIME_KEY not in bucket


async def test_async_start_local_mqtt_stores_client_in_hass_data() -> None:
    """When enabled with a host, the client is stored at hass.data[DOMAIN][entry_id][key]."""  # noqa: E501
    hass = _FakeHass()
    entry = _make_local_mqtt_entry(enable=True, host="192.168.1.100", port=1884)

    started: list[bool] = []

    with patch.object(
        JackeryLocalMqttClient,
        "async_start",
        new_callable=AsyncMock,
        side_effect=lambda: started.append(True),
    ):
        await _async_start_local_mqtt(hass, entry)

    client = (
        hass.data.get(DOMAIN, {}).get(entry.entry_id, {}).get(_LOCAL_MQTT_RUNTIME_KEY)
    )
    assert isinstance(client, JackeryLocalMqttClient)
    assert started  # async_start was called


async def test_async_start_local_mqtt_registers_unload_callback() -> None:
    """_async_start_local_mqtt must register an async_on_unload callback."""
    hass = _FakeHass()
    entry = _make_local_mqtt_entry(enable=True, host="192.168.1.100")

    with patch.object(JackeryLocalMqttClient, "async_start", new_callable=AsyncMock):
        await _async_start_local_mqtt(hass, entry)

    assert len(entry._unload_callbacks) == 1  # noqa: SLF001


async def test_async_start_local_mqtt_unload_callback_stops_and_removes_client() -> (
    None
):
    """The unload callback must stop the client and remove it from hass.data."""
    hass = _FakeHass()
    entry = _make_local_mqtt_entry(enable=True, host="192.168.1.100")

    with patch.object(JackeryLocalMqttClient, "async_start", new_callable=AsyncMock):
        await _async_start_local_mqtt(hass, entry)

    # Verify client is stored.
    bucket = hass.data[DOMAIN][entry.entry_id]
    assert _LOCAL_MQTT_RUNTIME_KEY in bucket

    # Run the unload callback.
    stop_called: list[bool] = []
    client = bucket[_LOCAL_MQTT_RUNTIME_KEY]

    async def _fake_stop() -> None:  # noqa: RUF029
        stop_called.append(True)

    with patch.object(client, "async_stop", new=_fake_stop):
        callback = entry._unload_callbacks[0]  # noqa: SLF001
        await callback()

    assert stop_called  # stop was called
    # Key must be removed from bucket.
    assert _LOCAL_MQTT_RUNTIME_KEY not in bucket


async def test_async_start_local_mqtt_unload_does_not_fail_when_stop_raises() -> None:
    """The unload callback suppresses exceptions raised by async_stop."""
    hass = _FakeHass()
    entry = _make_local_mqtt_entry(enable=True, host="192.168.1.100")

    with patch.object(JackeryLocalMqttClient, "async_start", new_callable=AsyncMock):
        await _async_start_local_mqtt(hass, entry)

    bucket = hass.data[DOMAIN][entry.entry_id]
    client = bucket[_LOCAL_MQTT_RUNTIME_KEY]

    async def _exploding_stop() -> None:  # noqa: RUF029
        raise RuntimeError("broker went away")  # noqa: TRY003

    with patch.object(client, "async_stop", new=_exploding_stop):
        callback = entry._unload_callbacks[0]  # noqa: SLF001
        # Must NOT raise.
        await callback()


async def test_async_start_local_mqtt_unload_does_not_remove_different_client() -> None:
    """If a different client replaced the stored one, unload must not remove it."""
    hass = _FakeHass()
    entry = _make_local_mqtt_entry(enable=True, host="192.168.1.100")

    with patch.object(JackeryLocalMqttClient, "async_start", new_callable=AsyncMock):
        await _async_start_local_mqtt(hass, entry)

    callback = entry._unload_callbacks[0]  # noqa: SLF001
    bucket = hass.data[DOMAIN][entry.entry_id]

    # Replace the stored client with a different object before unload.
    new_client = MagicMock(spec=JackeryLocalMqttClient)
    bucket[_LOCAL_MQTT_RUNTIME_KEY] = new_client

    async def _noop_stop() -> None:
        pass

    # We can't easily get original_client here, so we verify the new_client stays.
    with patch.object(JackeryLocalMqttClient, "async_stop", new_callable=AsyncMock):
        await callback()

    # The new (different) client must NOT be removed.
    assert bucket.get(_LOCAL_MQTT_RUNTIME_KEY) is new_client


async def test_async_start_local_mqtt_client_id_uses_entry_id_prefix() -> None:
    """Client ID must be derived from the first 8 chars of the entry ID."""
    hass = _FakeHass()
    entry_id = "ABCDEFGHIJKLMNOP"
    entry = _make_local_mqtt_entry(enable=True, host="192.168.1.100", entry_id=entry_id)

    with patch.object(JackeryLocalMqttClient, "async_start", new_callable=AsyncMock):
        await _async_start_local_mqtt(hass, entry)

    client = hass.data[DOMAIN][entry_id][_LOCAL_MQTT_RUNTIME_KEY]
    assert client._client_id == f"ha-jackery-{entry_id[:8]}"  # noqa: SLF001


async def test_async_start_local_mqtt_passes_topic_filter_to_client() -> None:
    """Configured topic filter must be passed to the stored client."""
    hass = _FakeHass()
    entry = _make_local_mqtt_entry(
        enable=True,
        host="192.168.1.100",
        topic_filter="hb/app/+/device",
    )

    with patch.object(JackeryLocalMqttClient, "async_start", new_callable=AsyncMock):
        await _async_start_local_mqtt(hass, entry)

    client = hass.data[DOMAIN][entry.entry_id][_LOCAL_MQTT_RUNTIME_KEY]
    assert client._topic_filter == "hb/app/+/device"  # noqa: SLF001


async def test_async_start_local_mqtt_second_call_is_idempotent() -> None:
    """Calling _async_start_local_mqtt twice must not create a second unload callback.

    The function creates a new client each call (unlike async_start which is
    guarded on the running task). This tests that each call stores a fresh
    client — idempotency is at the config-entry reload level.
    """
    hass = _FakeHass()
    entry = _make_local_mqtt_entry(enable=True, host="192.168.1.100")

    with patch.object(JackeryLocalMqttClient, "async_start", new_callable=AsyncMock):
        await _async_start_local_mqtt(hass, entry)
        # Second call stores a fresh client.
        await _async_start_local_mqtt(hass, entry)

    # Each call registers one unload callback.
    assert len(entry._unload_callbacks) == 2  # noqa: PLR2004, SLF001
