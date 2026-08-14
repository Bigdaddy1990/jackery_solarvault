"""MQTT transports consume cached credentials only — never trigger auth.

Owner invariant 2026-07-05: HTTP/API is the SOLE path for login/auth/reauth.
MQTT (cloud + local) and BLE are data-transfer only; they read already-cached
credentials and must NEVER call ``async_login`` or raise
``ConfigEntryAuthFailed``. The audit confirmed the breach: the single
credential accessor was login-capable (``async_get_mqtt_credentials`` ->
``_ensure_token`` -> ``async_login``) and both the cloud-MQTT connect path
and the MQTT command path escalated failures to reauth — the reauth storms.

These tests pin the split:

* ``get_cached_mqtt_credentials`` derives creds from the cached session
  without any login, and returns ``None`` (never raises) when no session
  is cached.
* ``_async_ensure_mqtt`` with no cached creds backs off quietly — no
  ``ConfigEntryAuthFailed``, no connect.
* Coordinator command publish with no cached creds raises a plain
  ``HomeAssistantError`` (not ``ConfigEntryAuthFailed``) and never logs in.
"""

import asyncio
import base64
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.const import (
    FIELD_DEVICE_SN,
    MQTT_CREDENTIAL_CLIENT_ID,
    MQTT_CREDENTIAL_PASSWORD,
    MQTT_CREDENTIAL_USERNAME,
    MQTT_CREDENTIAL_USER_ID,
    PAYLOAD_DEVICE,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
    MqttConnectionManager,
)
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

_USER_ID = "2041425653828689920"
_MAC_ID = "aabbccddeeff"
_SEED_B64 = base64.b64encode(bytes(range(32))).decode("ascii")


class _FakeSession:
    """Transport boundary stand-in; never used (no IO in these tests)."""

    @staticmethod
    def get(url: str, **kwargs: object) -> None:  # pragma: no cover - unused
        msg = "no HTTP IO expected in credential-purity tests"
        raise AssertionError(msg)


def _hydrated_api() -> JackeryApi:
    """Build an API client with a cached MQTT session but no HTTP token."""
    api = JackeryApi(cast("Any", _FakeSession()), "tester@example.com", "secret")
    api.hydrate_mqtt_session(user_id=_USER_ID, seed_b64=_SEED_B64, mac_id=_MAC_ID)
    return api


def test_cached_credentials_derive_without_login() -> None:
    """A hydrated session yields creds without ever calling async_login."""
    api = _hydrated_api()
    login = AsyncMock(
        side_effect=AssertionError("MQTT credential access must not log in"),
    )
    cast("Any", api).async_login = login

    creds = api.get_cached_mqtt_credentials()

    assert creds is not None
    assert creds[MQTT_CREDENTIAL_USER_ID] == _USER_ID
    login.assert_not_called()


def test_cached_credentials_none_without_session() -> None:
    """Without a cached session the accessor returns None and never logs in."""
    api = JackeryApi(cast("Any", _FakeSession()), "tester@example.com", "secret")
    login = AsyncMock(
        side_effect=AssertionError("MQTT credential access must not log in"),
    )
    cast("Any", api).async_login = login

    assert api.get_cached_mqtt_credentials() is None
    login.assert_not_called()


@pytest.mark.asyncio
async def test_ensure_mqtt_without_creds_does_not_reauth() -> None:
    """Missing cached creds must not raise ConfigEntryAuthFailed or connect."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    mqtt = MagicMock(name="mqtt")
    mqtt.is_connected = False
    coordinator._mqtt = mqtt  # ruff: ignore[private-member-access]
    cast("Any", coordinator)._async_local_first_blocks_reconnect = AsyncMock(  # ruff: ignore[private-member-access]
        return_value=False,
    )
    mgr = MagicMock(name="mqtt_mgr")
    mgr.should_skip_reconnect = MagicMock(return_value=False)
    coordinator._mqtt_mgr = mgr  # ruff: ignore[private-member-access]
    cast("Any", coordinator).api = SimpleNamespace(
        mqtt_fingerprint=(_USER_ID, _MAC_ID, _SEED_B64),
        get_cached_mqtt_credentials=MagicMock(return_value=None),
    )

    await coordinator._async_ensure_mqtt(force=True)  # ruff: ignore[private-member-access]

    mqtt.async_start.assert_not_called()


@pytest.mark.asyncio
async def test_connected_mqtt_clears_backoff_after_client_verifies_credentials() -> (
    None
):
    """A client-verified active session repairs delayed manager state."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    mqtt = MagicMock(name="mqtt")
    mqtt.is_started = True
    mqtt.is_connected = True
    mqtt.async_start = AsyncMock(return_value=None)
    coordinator._mqtt = mqtt  # ruff: ignore[private-member-access]
    cast("Any", coordinator)._async_local_first_blocks_reconnect = AsyncMock(  # ruff: ignore[private-member-access]
        return_value=False,
    )
    mgr = MqttConnectionManager()
    mgr.note_connect_failure("permanent boom")
    coordinator._mqtt_mgr = mgr  # ruff: ignore[private-member-access]
    api = _hydrated_api()
    cast("Any", coordinator).api = api
    creds = api.get_cached_mqtt_credentials()
    assert creds is not None

    await coordinator._async_ensure_mqtt()  # ruff: ignore[private-member-access]

    mqtt.async_start.assert_awaited_once_with(
        client_id=creds[MQTT_CREDENTIAL_CLIENT_ID],
        username=creds[MQTT_CREDENTIAL_USERNAME],
        password=creds[MQTT_CREDENTIAL_PASSWORD],
        user_id=creds[MQTT_CREDENTIAL_USER_ID],
        wait_connected=False,
    )
    assert mgr.fingerprint == api.mqtt_fingerprint
    assert mgr.backoff_remaining() == pytest.approx(0.0)


@pytest.mark.asyncio
async def test_rotated_mqtt_session_waits_for_new_connection_success() -> None:
    """A credential-triggered restart is not marked successful before CONNACK."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    mqtt = MagicMock(name="mqtt")
    mqtt.is_started = True
    mqtt.is_connected = True

    def _restart_for_rotated_credentials(**_kwargs: str | bool) -> None:
        mqtt.is_connected = False

    mqtt.async_start = AsyncMock(side_effect=_restart_for_rotated_credentials)
    coordinator._mqtt = mqtt  # ruff: ignore[private-member-access]
    cast("Any", coordinator)._async_local_first_blocks_reconnect = AsyncMock(  # ruff: ignore[private-member-access]
        return_value=False,
    )
    mgr = MqttConnectionManager()
    mgr.note_connect_failure("permanent boom")
    coordinator._mqtt_mgr = mgr  # ruff: ignore[private-member-access]
    cast("Any", coordinator).api = _hydrated_api()

    await coordinator._async_ensure_mqtt()  # ruff: ignore[private-member-access]

    assert mgr.fingerprint is None
    assert mgr.backoff_remaining() > 0


@pytest.mark.asyncio
async def test_publish_command_without_creds_raises_non_auth() -> None:
    """Missing cached creds fail the publish as a plain HomeAssistantError."""
    login = AsyncMock(
        side_effect=AssertionError("command path must not log in"),
    )
    api = SimpleNamespace(
        get_cached_mqtt_credentials=MagicMock(return_value=None),
        async_login=login,
    )
    mqtt = MagicMock(name="mqtt")
    mqtt.is_connected = True

    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._mqtt = mqtt  # ruff: ignore[private-member-access]
    cast("Any", coordinator).api = api
    cast("Any", coordinator)._device_index = {}  # ruff: ignore[private-member-access]
    cast("Any", coordinator).data = {
        "dev-1": {PAYLOAD_DEVICE: {FIELD_DEVICE_SN: "SN-1"}}
    }
    cast("Any", coordinator)._async_ensure_mqtt = AsyncMock()  # ruff: ignore[private-member-access]
    cast("Any", coordinator).device_bluetooth_key = MagicMock(return_value=None)

    with pytest.raises(HomeAssistantError) as excinfo:
        await coordinator._async_publish_command(  # ruff: ignore[private-member-access]
            "dev-1",
            message_type="QueryCombineData",
            action_id=3000,
            cmd=100,
            body_fields={},
            ensure_mqtt=False,
        )

    assert not isinstance(excinfo.value, ConfigEntryAuthFailed)
    login.assert_not_called()


@pytest.mark.asyncio
async def test_publish_command_lazily_starts_missing_cloud_client() -> None:
    """A foreground switch command must not depend on deferred Layer-5 startup."""
    creds = {
        MQTT_CREDENTIAL_CLIENT_ID: "client-1",
        MQTT_CREDENTIAL_USERNAME: "user-1",
        MQTT_CREDENTIAL_PASSWORD: "secret",
        MQTT_CREDENTIAL_USER_ID: _USER_ID,
    }
    api = SimpleNamespace(
        get_cached_mqtt_credentials=MagicMock(return_value=creds),
    )
    mqtt = MagicMock(name="mqtt")
    mqtt.is_connected = True
    mqtt.session_generation = 1
    mqtt.async_publish_json = AsyncMock()
    mqtt.diagnostics = {}

    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._mqtt = None  # ruff: ignore[private-member-access]
    coordinator._mqtt_session_generation = 0  # ruff: ignore[private-member-access]
    coordinator._mqtt_session_actions_seen = set()  # ruff: ignore[private-member-access]
    coordinator._mqtt_birth_snapshot_pending = False  # ruff: ignore[private-member-access]
    coordinator._cloud_mqtt_command_failures = {}  # ruff: ignore[private-member-access]
    coordinator._cloud_mqtt_command_attempts = {}  # ruff: ignore[private-member-access]
    coordinator._cloud_mqtt_command_attempt_sequence = 0  # ruff: ignore[private-member-access]
    cast("Any", coordinator).api = api
    cast("Any", coordinator)._device_index = {}  # ruff: ignore[private-member-access]
    cast("Any", coordinator).data = {
        "dev-1": {PAYLOAD_DEVICE: {FIELD_DEVICE_SN: "SN-1"}}
    }
    cast("Any", coordinator)._async_ensure_mqtt = AsyncMock()  # ruff: ignore[private-member-access]
    cast("Any", coordinator)._async_payload_debug_event = AsyncMock()  # ruff: ignore[private-member-access]

    def _start_mqtt() -> None:
        coordinator._mqtt = mqtt  # ruff: ignore[private-member-access]

    start_mqtt = AsyncMock(side_effect=_start_mqtt)
    cast("Any", coordinator).async_start_mqtt = start_mqtt

    await coordinator._async_publish_command(  # ruff: ignore[private-member-access]
        "dev-1",
        message_type="ControlDeviceProperty",
        action_id=3022,
        cmd=110,
        body_fields={"swEps": 1},
    )

    start_mqtt.assert_awaited_once()
    mqtt.async_publish_json.assert_awaited_once()


@pytest.mark.asyncio
async def test_concurrent_cloud_mqtt_start_constructs_one_runtime() -> None:
    """Deferred startup and a command must not orphan a duplicate MQTT client."""

    class _FakeMqtt:
        constructed = 0

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            type(self).constructed += 1

    class _BarrierHass:
        participant_count = 2

        def __init__(self) -> None:
            self.calls = 0
            self.both_waiting = asyncio.Event()

        async def async_add_executor_job(
            self,
            _target: object,
        ) -> type[_FakeMqtt]:
            self.calls += 1
            if self.calls == self.participant_count:
                self.both_waiting.set()
            await self.both_waiting.wait()
            return _FakeMqtt

    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._mqtt = None  # ruff: ignore[private-member-access]
    cast("Any", coordinator).hass = _BarrierHass()
    cast("Any", coordinator)._async_ensure_mqtt = AsyncMock()  # ruff: ignore[private-member-access]

    await asyncio.gather(
        coordinator.async_start_mqtt(),
        coordinator.async_start_mqtt(),
    )

    assert _FakeMqtt.constructed == 1
    assert isinstance(coordinator._mqtt, _FakeMqtt)  # ruff: ignore[private-member-access]
