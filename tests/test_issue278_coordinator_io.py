"""Exercise defensive coordinator I/O paths repaired for issue 278."""

import asyncio
import base64
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault.client import JackeryAuthError
from custom_components.jackery_solarvault.const import (
    APP_DEVICE_STAT_PV_ENERGY,
    CONF_ENABLE_BLE_TRANSPORT,
    DOMAIN,
    FIELD_BLUETOOTH_KEY,
    FIELD_CT_TOTAL_PHASE_ENERGY,
    PAYLOAD_DEVICE_META,
    PAYLOAD_LOCAL_DAILY_ENERGY,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_DEVICE_ID = "issue278-device"
_DAY = "2026-10-07"


@pytest.mark.parametrize("circular", [False, True])
async def test_ble_rejects_invalid_json_before_writing(*, circular: bool) -> None:
    """Malformed command bodies propagate their error without a BLE write."""
    coordinator = cast(
        "Any", JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    )
    coordinator.entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={CONF_ENABLE_BLE_TRANSPORT: True},
    )
    listener = MagicMock()
    listener.async_send_command = AsyncMock()
    coordinator._ble_listener = listener  # ruff: ignore[private-member-access]
    body: dict[str, Any] = {}
    if circular:
        body["invalid"] = body
        expected_error = ValueError
    else:
        body["invalid"] = object()
        expected_error = TypeError

    with pytest.raises(expected_error):
        await coordinator.async_send_ble_command(
            _DEVICE_ID, cmd=106, flags=3011, body=body
        )

    listener.async_send_command.assert_not_awaited()
    assert not hasattr(coordinator, "_device_locks")


def test_local_mqtt_rejects_malformed_binary_base64() -> None:
    """Invalid base64 and an invalid raw frame cannot produce device data."""
    coordinator = cast(
        "Any", JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    )
    coordinator.data = {}
    coordinator._device_index = {  # ruff: ignore[private-member-access]
        _DEVICE_ID: {
            PAYLOAD_DEVICE_META: {
                FIELD_BLUETOOTH_KEY: base64.b64encode(b"0123456789abcdef").decode(),
            },
        },
    }

    result = coordinator._decode_local_mqtt_binary_payload(  # ruff: ignore[private-member-access]
        b"invalid!base64"
    )

    assert result is None
    assert coordinator.data == {}
    assert coordinator.device_bluetooth_key(_DEVICE_ID) == b"0123456789abcdef"


@pytest.mark.parametrize(
    "error", [JackeryAuthError("expired credentials"), asyncio.CancelledError()]
)
async def test_named_device_request_propagates_auth_and_cancel(
    error: JackeryAuthError | asyncio.CancelledError,
) -> None:
    """Auth failures and cancellation escape without replacing peer data."""
    peer_key = (_DEVICE_ID, "peer")
    values: dict[tuple[str, str], Any | BaseException] = {peer_key: {"ok": True}}

    async def rejected() -> None:
        await asyncio.sleep(0)
        raise error

    with pytest.raises(type(error)) as raised:
        await JackerySolarVaultCoordinator._async_store_named_device_request(  # ruff: ignore[private-member-access]
            values, _DEVICE_ID, "failed", rejected()
        )

    assert raised.value is error
    assert values == {peer_key: {"ok": True}}


async def test_daily_cache_reconcile_skips_malformed_runtime_counters(
    hass: HomeAssistant,
) -> None:
    """Concurrent valid observations merge while malformed counters are skipped."""
    coordinator = cast(
        "Any", JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    )
    entry = MockConfigEntry(domain=DOMAIN, data={}, entry_id="issue278-daily-cache")
    entry.add_to_hass(hass)
    entry.runtime_data = coordinator
    coordinator.hass = hass
    coordinator.entry = entry
    coordinator._shutdown_started = False  # ruff: ignore[private-member-access]
    coordinator._local_daily_snapshots = {}  # ruff: ignore[private-member-access]
    coordinator._local_daily_cache_loaded = False  # ruff: ignore[private-member-access]
    schedule = MagicMock()
    coordinator._schedule_background_once = schedule  # ruff: ignore[private-member-access]
    cached = {
        _DEVICE_ID: {"day": _DAY, "values": {APP_DEVICE_STAT_PV_ENERGY: 100}},
    }

    def load_with_concurrent_observation(
        _hass: HomeAssistant, _entry_id: str
    ) -> dict[str, dict[str, Any]]:
        coordinator._local_daily_snapshots = {  # ruff: ignore[private-member-access]
            _DEVICE_ID: {
                "day": _DAY,
                "values": {
                    APP_DEVICE_STAT_PV_ENERGY: 150,
                    "valid-new-counter": "7",
                    "invalid-type": [],
                    "invalid-value": "not a number",
                },
            },
        }
        return cached

    with patch(
        "custom_components.jackery_solarvault.coordinator.async_load_daily_cache",
        side_effect=load_with_concurrent_observation,
    ):
        assert await coordinator.async_load_local_daily_snapshots()

    assert coordinator._local_daily_snapshots == {  # ruff: ignore[private-member-access]
        _DEVICE_ID: {
            "day": _DAY,
            "values": {APP_DEVICE_STAT_PV_ENERGY: 100, "valid-new-counter": 7},
        },
    }
    assert coordinator._local_daily_cache_loaded is True  # ruff: ignore[private-member-access]
    assert (
        coordinator._persisted_local_daily_signature  # ruff: ignore[private-member-access]
        == coordinator._local_daily_signature(cached)  # ruff: ignore[private-member-access]
    )
    schedule.assert_called_once_with(
        "daily_persist",
        coordinator._async_persist_local_daily_snapshots_if_changed,  # ruff: ignore[private-member-access]
        name=f"{DOMAIN}_daily_persist",
    )


@pytest.mark.parametrize("invalid_value", [[], "not a number"])
@pytest.mark.parametrize(
    "metric", [APP_DEVICE_STAT_PV_ENERGY, FIELD_CT_TOTAL_PHASE_ENERGY]
)
def test_local_daily_energy_omits_invalid_values(
    metric: str, invalid_value: object
) -> None:
    """Malformed device or CT counter deltas never become published energy."""
    coordinator = cast(
        "Any", JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    )
    coordinator.data = {
        _DEVICE_ID: {PAYLOAD_LOCAL_DAILY_ENERGY: {metric: invalid_value}},
    }

    assert coordinator.local_daily_energy_kwh(_DEVICE_ID, metric) is None
    assert coordinator.data[_DEVICE_ID][PAYLOAD_LOCAL_DAILY_ENERGY] == {
        metric: invalid_value,
    }
