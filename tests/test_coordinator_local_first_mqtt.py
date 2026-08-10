"""Regression tests for local transports and endpoint backoff policy."""

import sys
import time
from types import ModuleType, SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from custom_components.jackery_solarvault.client.api import JackeryApiError
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

_NOW = 1_000.0
_REPEATED_FAILURE_COUNT = 4
_KWH_BACKOFF_MAX_SEC = 120
_VERY_SLOW_BACKOFF_MAX_SEC = 21_600
_LOCAL_MQTT_PAYLOAD_POWER = 10
_KWH_BACKOFF_KEY = "dev:device-1:pv_stat:day"
_VERY_SLOW_BACKOFF_KEY = "diagnostic:static_model_metadata"
_BACKOFF_ERROR = JackeryApiError("cloud says code=10422")


def _bare_coordinator() -> JackerySolarVaultCoordinator:
    """Create a coordinator shell for private policy helpers without HA setup."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._endpoint_backoff = {}  # ruff: ignore[private-member-access]
    coordinator._local_mqtt_last_message_monotonic = float("-inf")  # ruff: ignore[private-member-access]
    coordinator._local_mqtt_last_device_message_monotonic = {}  # ruff: ignore[private-member-access]
    coordinator._local_mqtt_device_traffic_observed = False  # ruff: ignore[private-member-access]
    coordinator._cloud_mqtt_paused_by_local_mqtt_count = 0  # ruff: ignore[private-member-access]
    return coordinator


def _reachability_coordinator() -> JackerySolarVaultCoordinator:
    """Build a coordinator shell for local reachability checks."""
    coordinator = _bare_coordinator()
    coordinator._local_mqtt_last_device_message_monotonic = {}  # ruff: ignore[private-member-access]
    coordinator._ble_address_for_device = MagicMock(  # type: ignore[method-assign]  # ruff: ignore[private-member-access]
        return_value="AA:BB:CC:DD:EE:FF",
    )
    coordinator.hass = MagicMock()
    return coordinator


def test_local_reachability_does_not_import_optional_bluetooth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Return false without importing an optional HA Bluetooth component."""
    coordinator = _reachability_coordinator()
    monkeypatch.delitem(
        sys.modules,
        "homeassistant.components.bluetooth",
        raising=False,
    )

    assert coordinator.is_device_locally_reachable("device-1") is False
    assert "homeassistant.components.bluetooth" not in sys.modules


def test_local_reachability_uses_loaded_ha_bluetooth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Use HA's presence helper when Home Assistant already loaded it."""
    coordinator = _reachability_coordinator()
    address_present = MagicMock(return_value=True)
    bluetooth_module = ModuleType("homeassistant.components.bluetooth")
    setattr(bluetooth_module, "async_address_present", address_present)  # ruff:ignore[set-attr-with-constant]
    monkeypatch.setitem(
        sys.modules,
        "homeassistant.components.bluetooth",
        bluetooth_module,
    )

    assert coordinator.is_device_locally_reachable("device-1") is True
    address_present.assert_called_once_with(
        coordinator.hass,
        "AA:BB:CC:DD:EE:FF",
        connectable=True,
    )


def _backoff_remaining(coordinator: JackerySolarVaultCoordinator, key: str) -> int:
    """Return the rounded backoff delay stored for a test key."""
    state = coordinator._endpoint_backoff[key]  # ruff: ignore[private-member-access]
    return int(state["until"] - _NOW)


def test_kwh_endpoint_backoff_is_capped_at_two_minutes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Energy/stat endpoints must retry regularly instead of escalating to hours."""
    coordinator = _bare_coordinator()
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.coordinator.time.monotonic",
        lambda: _NOW,
    )

    for _ in range(_REPEATED_FAILURE_COUNT):
        assert coordinator._endpoint_backoff_note_failure(  # ruff: ignore[private-member-access]
            _KWH_BACKOFF_KEY,
            _BACKOFF_ERROR,
        )

    assert _backoff_remaining(coordinator, _KWH_BACKOFF_KEY) == _KWH_BACKOFF_MAX_SEC


def test_very_slow_endpoint_backoff_still_allows_long_escalation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Static diagnostic endpoints may keep the old long backoff ladder."""
    coordinator = _bare_coordinator()
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.coordinator.time.monotonic",
        lambda: _NOW,
    )

    for _ in range(_REPEATED_FAILURE_COUNT):
        assert coordinator._endpoint_backoff_note_failure(  # ruff: ignore[private-member-access]
            _VERY_SLOW_BACKOFF_KEY,
            _BACKOFF_ERROR,
        )

    assert (
        _backoff_remaining(coordinator, _VERY_SLOW_BACKOFF_KEY)
        == _VERY_SLOW_BACKOFF_MAX_SEC
    )


def _live_local_coordinator() -> JackerySolarVaultCoordinator:
    """Build a coordinator whose local MQTT channel counts as live."""
    coordinator = _bare_coordinator()
    coordinator._local_mqtt_last_message_monotonic = time.monotonic()  # ruff: ignore[private-member-access]
    coordinator._mqtt = cast(  # ruff: ignore[private-member-access]
        "Any",
        SimpleNamespace(
            is_connected=True,
            async_stop=AsyncMock(return_value=None),
        ),
    )
    cast("Any", coordinator).api = SimpleNamespace(
        mqtt_fingerprint=("client", "host", "session"),
        async_get_mqtt_credentials=AsyncMock(return_value={}),
    )
    coordinator._mqtt_mgr = MagicMock()  # ruff: ignore[private-member-access]
    return coordinator


@pytest.mark.asyncio()
async def test_cloud_mqtt_connect_is_suppressed_while_local_mqtt_is_live() -> None:
    """A live local MQTT channel must NOT gate the cloud MQTT channel.

    Layer independence: every transport delivers on its own and one
    working layer never pauses another. The old local-first pause (stop
    cloud MQTT while local frames flow) is gone on purpose. The connect
    path also stays cache-only — it never performs credential I/O; with
    no cached session it defers to the HTTP login path.
    """
    coordinator = _live_local_coordinator()
    mgr = cast("Any", coordinator._mqtt_mgr)  # ruff: ignore[private-member-access]
    mgr.should_skip_reconnect.return_value = False
    cast("Any", coordinator.api).get_cached_mqtt_credentials = Mock(return_value=None)

    await coordinator._async_ensure_mqtt(force=False)  # ruff: ignore[private-member-access]

    cast("Any", coordinator._mqtt).async_stop.assert_not_awaited()  # ruff: ignore[private-member-access]
    cast("Any", coordinator.api).async_get_mqtt_credentials.assert_not_awaited()
    cast("Any", coordinator.api).get_cached_mqtt_credentials.assert_called_once_with()
    mgr.should_skip_reconnect.assert_called_once()


@pytest.mark.asyncio()
async def test_forced_connect_bypasses_the_local_first_pause() -> None:
    """Command publishes (force=True) must keep the MQTT fallback working.

    If a forced connect were also suppressed, a command issued while BLE
    is down and local telemetry is live would silently fail — the pause
    only applies to passive reconnects.
    """
    coordinator = _live_local_coordinator()

    await coordinator._async_ensure_mqtt(force=True, wait_connected=True)  # ruff: ignore[private-member-access]

    cast("Any", coordinator._mqtt).async_stop.assert_not_awaited()  # ruff: ignore[private-member-access]
    cast("Any", coordinator._mqtt_mgr).should_skip_reconnect.assert_called_once()  # ruff: ignore[private-member-access]


@pytest.mark.asyncio()
async def test_connected_but_silent_local_client_does_not_pause_cloud(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Broker reachability without frames must not cost the cloud channel.

    Live regression 2026-07-04: the direct local client was CONNECTED to
    the (frame-less) broker, the connected-check counted as "live", cloud
    MQTT got paused and CombineData died — SystemBody sensors Unknown, no
    MQTT command fallback — while local delivered exactly nothing.
    """
    coordinator = _live_local_coordinator()
    coordinator._local_mqtt_last_message_monotonic = float("-inf")  # ruff: ignore[private-member-access]
    monkeypatch.setattr(
        type(coordinator),
        "_local_mqtt_direct_client_connected",
        lambda _self: True,
    )

    assert coordinator._local_mqtt_is_active() is False  # ruff: ignore[private-member-access]

    await coordinator._async_ensure_mqtt(force=False)  # ruff: ignore[private-member-access]

    cast("Any", coordinator._mqtt).async_stop.assert_not_awaited()  # ruff: ignore[private-member-access]


@pytest.mark.asyncio()
async def test_local_mqtt_message_marks_local_channel_live() -> None:
    """HA/local MQTT frames count as local activity even without direct-client state."""
    coordinator = _bare_coordinator()
    handler = AsyncMock(return_value="device-1")
    cast("Any", coordinator)._async_handle_mqtt_message = handler  # ruff: ignore[private-member-access]

    assert (
        await coordinator.async_handle_local_mqtt_message(
            "jackery/local",
            {
                "messageType": "UploadCombineData",
                "body": {"pvPw": _LOCAL_MQTT_PAYLOAD_POWER},
            },
        )
        is True
    )

    assert coordinator._local_mqtt_is_active() is True  # ruff: ignore[private-member-access]
    handler.assert_awaited_once()
