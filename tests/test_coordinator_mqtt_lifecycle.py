"""Behavioral tests for the coordinator's Cloud-MQTT lifecycle methods.

These drive ``_async_ensure_mqtt`` and ``_async_mqtt_connected`` through their
state transitions with a stubbed MQTT push client, asserting business outcomes
(which path is taken, what manager state results, whether reauth is ever
triggered) — never call order. The governing invariant: MQTT is a supplemental
Layer-5 transport that must never gate the HTTP path or open HA reauth.
"""

import asyncio
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.jackery_solarvault.const import (
    MQTT_CREDENTIAL_CLIENT_ID,
    MQTT_CREDENTIAL_PASSWORD,
    MQTT_CREDENTIAL_USERNAME,
    MQTT_CREDENTIAL_USER_ID,
)
from homeassistant.exceptions import ConfigEntryAuthFailed
from tests._update_cycle_fixture import (  # ruff: ignore[banned-api]
    make_update_cycle_api,
    setup_update_cycle_coordinator,
)

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from homeassistant.core import HomeAssistant

_MODULE = "custom_components.jackery_solarvault.coordinator"


async def _teardown(hass: HomeAssistant, entry_id: str) -> None:
    """Unload the entry and drain background tasks."""
    await hass.config_entries.async_unload(entry_id)
    await hass.async_block_till_done()


def _fake_mqtt(*, connected: bool = False, last_error: Any = None) -> MagicMock:  # ruff: ignore[any-type]
    """Build a stubbed JackeryMqttPushClient surface for lifecycle tests."""
    mqtt = MagicMock(name="JackeryMqttPushClient")
    mqtt.is_connected = connected
    mqtt.is_started = connected
    mqtt.async_start = AsyncMock(return_value=None)
    mqtt.async_wait_until_connected = AsyncMock(return_value=None)
    mqtt.diagnostics = {"last_error": last_error} if last_error else {}
    mqtt.consecutive_auth_failures = 0
    mqtt.session_generation = 1
    return mqtt


def _credential_dict() -> dict[str, str]:
    return {
        MQTT_CREDENTIAL_CLIENT_ID: "client-1",
        MQTT_CREDENTIAL_USERNAME: "user-1",
        MQTT_CREDENTIAL_PASSWORD: "pw-1",
        MQTT_CREDENTIAL_USER_ID: "uid-1",
    }


@pytest.fixture()
async def coordinator(hass: HomeAssistant) -> AsyncGenerator[Any]:
    """Yield a coordinator with mocked api for MQTT lifecycle tests."""
    api = make_update_cycle_api()
    coord, entry, _api = await setup_update_cycle_coordinator(
        hass, api=api, discover=True
    )
    yield coord
    await _teardown(hass, entry.entry_id)


# ---------------------------------------------------------------------------
# _async_ensure_mqtt
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_ensure_mqtt_returns_early_without_runtime(coordinator: Any) -> None:  # ruff: ignore[any-type]
    """Without an MQTT runtime there is nothing to start."""
    coordinator._mqtt = None  # ruff: ignore[private-member-access]
    calls_before = coordinator.api.get_cached_mqtt_credentials.call_count
    await coordinator._async_ensure_mqtt(force=True, wait_connected=False)  # ruff: ignore[private-member-access]
    # No credentials are consulted when there is no runtime.
    assert coordinator.api.get_cached_mqtt_credentials.call_count == calls_before


@pytest.mark.asyncio()
async def test_ensure_mqtt_defers_without_cached_credentials(coordinator: Any) -> None:  # ruff: ignore[any-type]
    """No cached credentials: back off and let the HTTP login path acquire them."""
    coordinator._mqtt = _fake_mqtt()  # ruff: ignore[private-member-access]
    coordinator.api.get_cached_mqtt_credentials = MagicMock(return_value=None)

    await coordinator._async_ensure_mqtt(force=True, wait_connected=False)  # ruff: ignore[private-member-access]

    coordinator._mqtt.async_start.assert_not_awaited()  # ruff: ignore[private-member-access]


@pytest.mark.asyncio()
async def test_ensure_mqtt_skips_when_manager_says_so(coordinator: Any) -> None:  # ruff: ignore[any-type]
    """The manager's skip decision is honored (e.g. active pause/backoff)."""
    mqtt = _fake_mqtt(connected=False)
    coordinator._mqtt = mqtt  # ruff: ignore[private-member-access]
    coordinator.api.get_cached_mqtt_credentials = MagicMock(
        return_value=_credential_dict()
    )
    coordinator._mqtt_mgr.paused_until_monotonic = float("inf")  # ruff: ignore[private-member-access]

    await coordinator._async_ensure_mqtt(force=False, wait_connected=False)  # ruff: ignore[private-member-access]

    mqtt.async_start.assert_not_awaited()


@pytest.mark.asyncio()
async def test_ensure_mqtt_starts_with_cached_credentials(coordinator: Any) -> None:  # ruff: ignore[any-type]
    """Cached credentials drive a real async_start with the credential fields."""
    mqtt = _fake_mqtt(connected=False)
    coordinator._mqtt = mqtt  # ruff: ignore[private-member-access]
    coordinator.api.get_cached_mqtt_credentials = MagicMock(
        return_value=_credential_dict()
    )

    await coordinator._async_ensure_mqtt(force=True, wait_connected=False)  # ruff: ignore[private-member-access]

    mqtt.async_start.assert_awaited_once_with(
        client_id="client-1",
        username="user-1",
        password="pw-1",
        user_id="uid-1",
        wait_connected=False,
    )


@pytest.mark.asyncio()
async def test_ensure_mqtt_records_success_when_already_connected(
    coordinator: Any,  # ruff: ignore[any-type]
) -> None:
    """A client-verified no-op repairs manager state when already connected."""
    mqtt = _fake_mqtt(connected=True)
    coordinator._mqtt = mqtt  # ruff: ignore[private-member-access]
    coordinator.api.get_cached_mqtt_credentials = MagicMock(
        return_value=_credential_dict()
    )
    fingerprint = ("c", "h", "s")
    coordinator.api.mqtt_fingerprint = fingerprint

    await coordinator._async_ensure_mqtt(force=True, wait_connected=False)  # ruff: ignore[private-member-access]

    assert coordinator._mqtt_mgr.fingerprint == fingerprint  # ruff: ignore[private-member-access]
    mqtt.async_wait_until_connected.assert_not_awaited()


@pytest.mark.asyncio()
async def test_ensure_mqtt_wait_connected_success_path(coordinator: Any) -> None:  # ruff: ignore[any-type]
    """wait_connected=True waits for CONNACK then records the fingerprint."""
    mqtt = _fake_mqtt(connected=True)
    coordinator._mqtt = mqtt  # ruff: ignore[private-member-access]
    coordinator.api.get_cached_mqtt_credentials = MagicMock(
        return_value=_credential_dict()
    )
    fingerprint = ("c", "h", "s")
    coordinator.api.mqtt_fingerprint = fingerprint

    await coordinator._async_ensure_mqtt(force=True, wait_connected=True)  # ruff: ignore[private-member-access]

    mqtt.async_wait_until_connected.assert_awaited_once_with(timeout_sec=30.0)
    assert coordinator._mqtt_mgr.fingerprint == fingerprint  # ruff: ignore[private-member-access]


@pytest.mark.asyncio()
async def test_ensure_mqtt_wait_connected_auth_failure_pauses(coordinator: Any) -> None:  # ruff: ignore[any-type]
    """A broker credential rejection pauses MQTT and re-raises, never reauth."""
    mqtt = _fake_mqtt(connected=False)
    mqtt.diagnostics = {"last_error": "connect rc=5"}
    mqtt.consecutive_auth_failures = 3
    coordinator._mqtt = mqtt  # ruff: ignore[private-member-access]
    coordinator.api.get_cached_mqtt_credentials = MagicMock(
        return_value=_credential_dict()
    )
    coordinator.api.mqtt_fingerprint = ("c", "h", "s")
    mqtt.async_wait_until_connected = AsyncMock(
        side_effect=RuntimeError("MQTT not connected yet (connect rc=5)")
    )

    with pytest.raises(RuntimeError):
        await coordinator._async_ensure_mqtt(force=True, wait_connected=True)  # ruff: ignore[private-member-access]

    assert coordinator._mqtt_mgr.app_conflict_pause_cycles == 1  # ruff: ignore[private-member-access]


@pytest.mark.asyncio()
async def test_ensure_mqtt_wait_connected_network_error_backs_off(
    coordinator: Any,  # ruff: ignore[any-type]
) -> None:
    """A non-auth connect failure records backoff and re-raises."""
    mqtt = _fake_mqtt(connected=False)
    mqtt.diagnostics = {"last_error": "connection refused"}
    coordinator._mqtt = mqtt  # ruff: ignore[private-member-access]
    coordinator.api.get_cached_mqtt_credentials = MagicMock(
        return_value=_credential_dict()
    )
    mqtt.async_wait_until_connected = AsyncMock(
        side_effect=RuntimeError("connection refused")
    )

    with pytest.raises(RuntimeError):
        await coordinator._async_ensure_mqtt(force=True, wait_connected=True)  # ruff: ignore[private-member-access]

    assert coordinator._mqtt_mgr.backoff_remaining() > 0  # ruff: ignore[private-member-access]
    assert coordinator._mqtt_mgr.app_conflict_pause_cycles == 0  # ruff: ignore[private-member-access]


@pytest.mark.asyncio()
async def test_ensure_mqtt_returns_early_on_stale_handle(coordinator: Any) -> None:  # ruff: ignore[any-type]
    """A concurrent unload/reload replacing the runtime bails out quietly."""
    mqtt = _fake_mqtt(connected=False)
    coordinator._mqtt = mqtt  # ruff: ignore[private-member-access]
    coordinator.api.get_cached_mqtt_credentials = MagicMock(
        return_value=_credential_dict()
    )

    async def _replace_runtime(  # ruff: ignore[unused-async]
        *args: object,
        **kwargs: object,
    ) -> None:
        coordinator._mqtt = _fake_mqtt()  # ruff: ignore[private-member-access]

    mqtt.async_start = AsyncMock(side_effect=_replace_runtime)

    await coordinator._async_ensure_mqtt(force=True, wait_connected=False)  # ruff: ignore[private-member-access]

    # The stale handle's async_start ran, but nothing beyond it.
    mqtt.async_wait_until_connected.assert_not_awaited()


@pytest.mark.asyncio()
async def test_ensure_mqtt_logs_generated_mac_warning_once(coordinator: Any) -> None:  # ruff: ignore[any-type]
    """A generated macId source is flagged once, not every connect."""
    mqtt = _fake_mqtt(connected=False)
    coordinator._mqtt = mqtt  # ruff: ignore[private-member-access]
    coordinator.api.get_cached_mqtt_credentials = MagicMock(
        return_value=_credential_dict()
    )
    coordinator.api.mqtt_mac_id_source = "generated_from_seed"
    coordinator._mqtt_mgr.generated_mac_warning_logged = False  # ruff: ignore[private-member-access]

    await coordinator._async_ensure_mqtt(force=True, wait_connected=False)  # ruff: ignore[private-member-access]
    assert coordinator._mqtt_mgr.generated_mac_warning_logged is True  # ruff: ignore[private-member-access]

    mqtt.async_start.assert_awaited_once()


@pytest.mark.asyncio()
async def test_ensure_mqtt_logs_credential_session_change(coordinator: Any) -> None:  # ruff: ignore[any-type]
    """A changed credential fingerprint logs a reconnect notice."""
    mqtt = _fake_mqtt(connected=False)
    coordinator._mqtt = mqtt  # ruff: ignore[private-member-access]
    coordinator.api.get_cached_mqtt_credentials = MagicMock(
        return_value=_credential_dict()
    )
    coordinator.api.mqtt_fingerprint = ("new", "h", "s")
    coordinator._mqtt_mgr.fingerprint = ("old", "h", "s")  # ruff: ignore[private-member-access]

    await coordinator._async_ensure_mqtt(force=True, wait_connected=False)  # ruff: ignore[private-member-access]

    mqtt.async_start.assert_awaited_once()


# ---------------------------------------------------------------------------
# _async_mqtt_connected
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_mqtt_connected_returns_early_during_shutdown(coordinator: Any) -> None:  # ruff: ignore[any-type]
    """No snapshot work happens once shutdown has begun."""
    coordinator._shutdown_started = True  # ruff: ignore[private-member-access]
    coordinator._mqtt = _fake_mqtt(connected=True)  # ruff: ignore[private-member-access]

    with (
        patch.object(
            coordinator,
            "async_schedule_local_mqtt_device_config",
            return_value=None,
        ) as schedule_config,
        patch.object(
            coordinator,
            "_async_query_system_info_for_missing",
            AsyncMock(return_value=None),
        ) as query_system,
    ):
        await coordinator._async_mqtt_connected()  # ruff: ignore[private-member-access]

    schedule_config.assert_not_called()
    query_system.assert_not_called()


@pytest.mark.asyncio()
async def test_mqtt_connected_records_success_and_queries_missing(
    coordinator: Any,  # ruff: ignore[any-type]
) -> None:
    """On connect the manager records success and the enrichment queries run."""
    mqtt = _fake_mqtt(connected=True)
    coordinator._mqtt = mqtt  # ruff: ignore[private-member-access]
    coordinator.data = {"device-1": {}}
    fingerprint = ("c", "h", "s")
    coordinator.api.mqtt_fingerprint = fingerprint

    with (
        patch.object(
            coordinator,
            "async_schedule_local_mqtt_device_config",
            return_value=None,
        ) as schedule_config,
        patch.object(
            coordinator,
            "_async_query_system_info_for_missing",
            AsyncMock(return_value=None),
        ) as query_system,
        patch.object(
            coordinator,
            "_async_query_weather_plan_for_missing",
            AsyncMock(return_value=None),
        ) as query_weather,
        patch.object(
            coordinator,
            "_async_query_subdevices_for_missing",
            AsyncMock(return_value=None),
        ) as query_sub,
    ):
        await coordinator._async_mqtt_connected()  # ruff: ignore[private-member-access]

    assert coordinator._mqtt_mgr.fingerprint == fingerprint  # ruff: ignore[private-member-access]
    schedule_config.assert_called_once()
    query_system.assert_awaited_once_with(
        force=True,
        ensure_mqtt=False,
        allow_ble=False,
        snapshot={"device-1": {}},
    )
    query_weather.assert_awaited_once_with(
        force=True,
        ensure_mqtt=False,
        allow_ble=False,
        snapshot={"device-1": {}},
    )
    query_sub.assert_awaited_once_with(
        force=True,
        ensure_mqtt=False,
        allow_ble=False,
        snapshot={"device-1": {}},
    )


@pytest.mark.asyncio()
async def test_mqtt_birth_and_poll_overlap_share_one_query_flight(
    coordinator: Any,  # ruff: ignore[any-type]
) -> None:
    """A scheduled poll must not duplicate an in-flight connect birth."""
    coordinator._mqtt = _fake_mqtt(connected=True)  # ruff: ignore[private-member-access]
    coordinator.data = {"device-1": {}}
    coordinator.api.mqtt_fingerprint = ("c", "h", "s")
    entered = asyncio.Event()
    release = asyncio.Event()

    async def _block_first_query(**_kwargs: Any) -> None:  # ruff: ignore[any-type]
        entered.set()
        await release.wait()

    with (
        patch.object(
            coordinator,
            "async_schedule_local_mqtt_device_config",
            return_value=None,
        ),
        patch.object(
            coordinator,
            "_async_query_third_party_mqtt_configs",
            AsyncMock(return_value=None),
        ) as query_third_party,
        patch.object(
            coordinator,
            "_async_query_subdevices_for_missing",
            AsyncMock(side_effect=_block_first_query),
        ) as query_sub,
        patch.object(
            coordinator,
            "_async_query_system_info_for_missing",
            AsyncMock(return_value=None),
        ) as query_system,
        patch.object(
            coordinator,
            "_async_query_weather_plan_for_missing",
            AsyncMock(return_value=None),
        ) as query_weather,
    ):
        connect_task = asyncio.create_task(coordinator._async_mqtt_connected())  # ruff: ignore[private-member-access]
        await entered.wait()
        poll_task = asyncio.create_task(
            coordinator._async_mqtt_poll_queries({"device-1": {}})  # ruff: ignore[private-member-access]
        )
        await asyncio.sleep(0)
        release.set()
        await asyncio.gather(connect_task, poll_task)

    query_third_party.assert_awaited_once_with({"device-1": {}})
    query_sub.assert_awaited_once()
    query_system.assert_awaited_once()
    query_weather.assert_awaited_once()


@pytest.mark.asyncio()
async def test_mqtt_connected_defers_background_auth_failure(coordinator: Any) -> None:  # ruff: ignore[any-type]
    """A ConfigEntryAuthFailed during enrichment is deferred, not raised."""
    coordinator._mqtt = _fake_mqtt(connected=True)  # ruff: ignore[private-member-access]
    coordinator.data = {"device-1": {}}
    coordinator.api.mqtt_fingerprint = ("c", "h", "s")

    with (
        patch.object(
            coordinator,
            "async_schedule_local_mqtt_device_config",
            return_value=None,
        ),
        patch.object(
            coordinator,
            "_async_query_system_info_for_missing",
            AsyncMock(side_effect=ConfigEntryAuthFailed("rejected")),
        ),
        patch.object(
            coordinator,
            "_async_query_weather_plan_for_missing",
            AsyncMock(return_value=None),
        ),
        patch.object(
            coordinator,
            "_async_query_subdevices_for_missing",
            AsyncMock(return_value=None),
        ),
        patch.object(
            coordinator,
            "defer_background_auth_failure",
            return_value=None,
        ) as defer,
    ):
        await coordinator._async_mqtt_connected()  # ruff: ignore[private-member-access]

    defer.assert_called_once()
