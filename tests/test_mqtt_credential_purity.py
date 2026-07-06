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
* ``publish_mqtt_command`` with no cached creds raises a plain
  ``HomeAssistantError`` (not ``ConfigEntryAuthFailed``) and never logs in.
"""

import base64
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.client.mqtt.mqtt_command import (
    publish_mqtt_command,
)
from custom_components.jackery_solarvault.const import MQTT_CREDENTIAL_USER_ID
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
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


@pytest.mark.asyncio()
async def test_ensure_mqtt_without_creds_does_not_reauth() -> None:
    """Missing cached creds must not raise ConfigEntryAuthFailed or connect."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    mqtt = MagicMock(name="mqtt")
    mqtt.is_connected = False
    coordinator._mqtt = mqtt  # noqa: SLF001
    cast("Any", coordinator)._async_local_first_blocks_reconnect = AsyncMock(  # noqa: SLF001
        return_value=False,
    )
    mgr = MagicMock(name="mqtt_mgr")
    mgr.should_skip_reconnect = MagicMock(return_value=False)
    coordinator._mqtt_mgr = mgr  # noqa: SLF001
    cast("Any", coordinator).api = SimpleNamespace(
        mqtt_fingerprint=(_USER_ID, _MAC_ID, _SEED_B64),
        get_cached_mqtt_credentials=MagicMock(return_value=None),
    )

    await coordinator._async_ensure_mqtt(force=True)  # noqa: SLF001

    mqtt.async_start.assert_not_called()


@pytest.mark.asyncio()
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

    with pytest.raises(HomeAssistantError) as excinfo:
        await publish_mqtt_command(
            mqtt=mqtt,
            api=cast("Any", api),
            device_id="dev-1",
            device_sn="SN-1",
            bt_key=None,
            message_type="QueryCombineData",
            action_id=3000,
            cmd=100,
            body_fields={},
            ensure_mqtt_cb=AsyncMock(),
            stop_mqtt_cb=AsyncMock(),
        )

    assert not isinstance(excinfo.value, ConfigEntryAuthFailed)
    login.assert_not_called()
