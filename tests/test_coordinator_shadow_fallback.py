"""HTTP shadow-fallback unit tests (slice G-sub-1b).

These tests exercise ``_async_shadow_fallback_for_missing`` and the
``_schedule_shadow_fallback`` scheduler in isolation, using a lightweight
coordinator stub (mirroring ``tests/test_coordinator_backoff.py``).  The
Jackery cloud API (``async_get_sub_shadow``/``async_get_system_shadow``) and
the MQTT push state are mocked at the integration boundary only.
"""

from datetime import timedelta
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.client.api import (
    JackeryAuthError,
    JackeryError,
)
from custom_components.jackery_solarvault.const import (
    FIELD_ACCESSORIES,
    FIELD_DEVICE_SN,
    FIELD_DEV_TYPE,
    FIELD_PLUGS,
    FIELD_SUB_DEVICE,
    FIELD_SYSTEM_ID,
    PAYLOAD_CT_METER,
    PAYLOAD_DEVICE,
    PAYLOAD_MQTT_LAST,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_SUBDEVICES,
    PAYLOAD_SYSTEM_META,
    SUBDEVICE_DEV_TYPE_COMBO,
    SUBDEVICE_DEV_TYPE_CT,
    SUBDEVICE_DEV_TYPE_SOCKET,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

PARENT_SN = "PARENT-SN-001"
SYSTEM_ID = "sys-001"
PLUG_SN = "PLUG-SN-AAA"
PLUG_SN_B = "PLUG-SN-BBB"
CT_SN = "CT-SN-CCC"
COMBO_SN = "COMBO-SN-DDD"
DEVICE_ID = "dev-1"


def _coordinator_stub() -> JackerySolarVaultCoordinator:
    """Create a lightweight coordinator for pure shadow-fallback tests."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator.api = MagicMock()
    coordinator.api.async_get_sub_shadow = AsyncMock(return_value={})
    coordinator.api.async_get_system_shadow = AsyncMock(return_value={})
    coordinator._mqtt = None  # noqa: SLF001
    coordinator._configured_update_interval = timedelta(seconds=15)  # noqa: SLF001
    coordinator._last_shadow_query = {}  # noqa: SLF001
    coordinator._subdevice_query_interval_sec = 15  # noqa: SLF001
    coordinator._shadow_fallback_task = None  # noqa: SLF001
    coordinator.push_calls = []  # type: ignore[attr-defined]

    def _capture_push(new_data: dict[str, dict[str, Any]]) -> None:
        coordinator.data = new_data
        coordinator.push_calls.append(new_data)  # type: ignore[attr-defined]

    coordinator._push_partial_update = _capture_push  # type: ignore[method-assign]  # noqa: SLF001
    coordinator.data = {}
    return coordinator


def _socket_entry() -> dict[str, Any]:
    """Build an entry advertising one enumerated SOCKET accessory, no plug data."""
    return {
        PAYLOAD_DEVICE: {FIELD_DEVICE_SN: PARENT_SN},
        PAYLOAD_SYSTEM_META: {
            FIELD_SYSTEM_ID: SYSTEM_ID,
            FIELD_ACCESSORIES: [
                {FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET, FIELD_DEVICE_SN: PLUG_SN},
            ],
        },
    }


def _plug_sns(entry: dict[str, Any]) -> set[str]:
    """Collect the serials present in the smart-plug bucket."""
    plugs = entry.get(PAYLOAD_SMART_PLUGS) or []
    return {
        str(plug.get(FIELD_DEVICE_SN))
        for plug in plugs
        if isinstance(plug, dict) and plug.get(FIELD_DEVICE_SN)
    }


@pytest.mark.asyncio()
async def test_shadow_fires_when_mqtt_absent_and_plug_bucket_empty() -> None:
    """MQTT absent + enumerated SOCKET SN missing from bucket → shadow fills it."""
    coordinator = _coordinator_stub()
    snapshot = {DEVICE_ID: _socket_entry()}
    coordinator.api.async_get_sub_shadow.return_value = {
        FIELD_PLUGS: [{FIELD_DEVICE_SN: PLUG_SN, "sw": 1, "power": 42}],
    }

    await coordinator._async_shadow_fallback_for_missing(snapshot)  # noqa: SLF001

    coordinator.api.async_get_sub_shadow.assert_awaited_once_with(
        dev_type=str(SUBDEVICE_DEV_TYPE_SOCKET),
        device_sn=PARENT_SN,
        sub_device_sn=PLUG_SN,
    )
    assert PLUG_SN in _plug_sns(coordinator.data[DEVICE_ID])
    assert coordinator.push_calls


@pytest.mark.asyncio()
async def test_mqtt_live_frame_wins_over_shadow_single_entity() -> None:
    """A later MQTT frame for the same SN keeps one entity; MQTT values win."""
    coordinator = _coordinator_stub()
    entry = _socket_entry()
    snapshot = {DEVICE_ID: entry}
    coordinator.api.async_get_sub_shadow.return_value = {
        FIELD_PLUGS: [{FIELD_DEVICE_SN: PLUG_SN, "sw": 0, "power": 10}],
    }

    await coordinator._async_shadow_fallback_for_missing(snapshot)  # noqa: SLF001

    # Simulate an MQTT live frame landing for the same plug SN.
    live_entry = dict(coordinator.data[DEVICE_ID])
    coordinator._merge_subdevice_data(  # noqa: SLF001
        live_entry,
        {FIELD_PLUGS: [{FIELD_DEVICE_SN: PLUG_SN, "sw": 1, "power": 99}]},
        device_id=DEVICE_ID,
    )
    live_entry[PAYLOAD_MQTT_LAST] = {"received_at_monotonic": time.monotonic()}

    plugs = live_entry[PAYLOAD_SMART_PLUGS]
    assert len(plugs) == 1
    assert plugs[0]["power"] == 99  # noqa: PLR2004
    assert plugs[0]["sw"] == 1
    # Shadow must never have stamped the MQTT-freshness marker.
    assert PAYLOAD_MQTT_LAST not in coordinator.data[DEVICE_ID]


@pytest.mark.asyncio()
async def test_shadow_skipped_when_mqtt_fresh() -> None:
    """A connected broker with a fresh MQTT marker suppresses the shadow call."""
    coordinator = _coordinator_stub()
    mqtt = MagicMock()
    mqtt.is_connected = True
    coordinator._mqtt = mqtt  # noqa: SLF001
    entry = _socket_entry()
    entry[PAYLOAD_MQTT_LAST] = {"received_at_monotonic": time.monotonic()}
    snapshot = {DEVICE_ID: entry}

    await coordinator._async_shadow_fallback_for_missing(snapshot)  # noqa: SLF001

    coordinator.api.async_get_sub_shadow.assert_not_awaited()
    assert not coordinator.push_calls


@pytest.mark.asyncio()
async def test_shadow_merge_is_null_safe_for_ct() -> None:
    """A later shadow CT volt=None must not blank a prior volt=230 reading.

    The shadow fires once for the empty CT bucket (filling volt=230), then a
    follow-up CT frame carrying volt=None is merged via the shared sink. The
    ``merge_live_properties`` blank-guard must preserve the established voltage.
    """
    coordinator = _coordinator_stub()
    entry = {
        PAYLOAD_DEVICE: {FIELD_DEVICE_SN: PARENT_SN},
        PAYLOAD_SYSTEM_META: {
            FIELD_SYSTEM_ID: SYSTEM_ID,
            FIELD_ACCESSORIES: [
                {FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_CT, FIELD_DEVICE_SN: CT_SN},
            ],
        },
    }
    snapshot = {DEVICE_ID: entry}
    coordinator.api.async_get_sub_shadow.return_value = {
        FIELD_DEVICE_SN: CT_SN,
        "volt": 230,
        "curr": 5,
    }

    await coordinator._async_shadow_fallback_for_missing(snapshot)  # noqa: SLF001

    # A subsequent CT frame reports a momentary null voltage but fresh current.
    filled = dict(coordinator.data[DEVICE_ID])
    coordinator._merge_subdevice_data(  # noqa: SLF001
        filled,
        {FIELD_DEVICE_SN: CT_SN, "volt": None, "curr": 6},
        device_id=DEVICE_ID,
    )

    ct = filled[PAYLOAD_CT_METER]
    assert ct["volt"] == 230  # noqa: PLR2004
    assert ct["curr"] == 6  # noqa: PLR2004


@pytest.mark.asyncio()
async def test_combo_also_queries_system_shadow_and_merges_subdevices() -> None:
    """devType=2 (COMBO) fetches sub + system shadow and fills the subdevices."""
    coordinator = _coordinator_stub()
    entry = {
        PAYLOAD_DEVICE: {FIELD_DEVICE_SN: PARENT_SN},
        PAYLOAD_SYSTEM_META: {
            FIELD_SYSTEM_ID: SYSTEM_ID,
            FIELD_ACCESSORIES: [
                {FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_COMBO, FIELD_DEVICE_SN: COMBO_SN},
            ],
        },
    }
    snapshot = {DEVICE_ID: entry}
    coordinator.api.async_get_sub_shadow.return_value = {
        FIELD_SUB_DEVICE: [{FIELD_DEVICE_SN: COMBO_SN, "sw": 1}],
    }
    coordinator.api.async_get_system_shadow.return_value = {
        FIELD_SUB_DEVICE: [{FIELD_DEVICE_SN: COMBO_SN, "power": 120}],
    }

    await coordinator._async_shadow_fallback_for_missing(snapshot)  # noqa: SLF001

    coordinator.api.async_get_sub_shadow.assert_awaited_once_with(
        dev_type=str(SUBDEVICE_DEV_TYPE_COMBO),
        device_sn=PARENT_SN,
        sub_device_sn=COMBO_SN,
    )
    coordinator.api.async_get_system_shadow.assert_awaited_once_with(
        device_sn=PARENT_SN,
        diy_sn=SYSTEM_ID,
    )
    sub_devices = coordinator.data[DEVICE_ID][PAYLOAD_SUBDEVICES]
    merged = next(
        item
        for item in sub_devices
        if isinstance(item, dict) and item.get(FIELD_DEVICE_SN) == COMBO_SN
    )
    assert merged["sw"] == 1
    assert merged["power"] == 120  # noqa: PLR2004


@pytest.mark.asyncio()
async def test_shadow_best_effort_one_failure_does_not_abort_others() -> None:
    """SN-A raising JackeryError must not stop SN-B from being filled."""
    coordinator = _coordinator_stub()
    entry = {
        PAYLOAD_DEVICE: {FIELD_DEVICE_SN: PARENT_SN},
        PAYLOAD_SYSTEM_META: {
            FIELD_SYSTEM_ID: SYSTEM_ID,
            FIELD_ACCESSORIES: [
                {FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET, FIELD_DEVICE_SN: PLUG_SN},
                {FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET, FIELD_DEVICE_SN: PLUG_SN_B},
            ],
        },
    }
    snapshot = {DEVICE_ID: entry}

    def _shadow(
        *,
        dev_type: str,
        device_sn: str,
        sub_device_sn: str,
    ) -> dict[str, Any]:
        del dev_type, device_sn
        if sub_device_sn == PLUG_SN:
            msg = "request failed code=10422"
            raise JackeryError(msg)
        return {FIELD_PLUGS: [{FIELD_DEVICE_SN: PLUG_SN_B, "sw": 1}]}

    coordinator.api.async_get_sub_shadow = AsyncMock(side_effect=_shadow)

    await coordinator._async_shadow_fallback_for_missing(snapshot)  # noqa: SLF001

    sns = _plug_sns(coordinator.data[DEVICE_ID])
    assert PLUG_SN_B in sns
    assert PLUG_SN not in sns


@pytest.mark.asyncio()
async def test_shadow_auth_error_is_swallowed_not_raised() -> None:
    """JackeryAuthError from the shadow path must not escape the background task."""
    coordinator = _coordinator_stub()
    snapshot = {DEVICE_ID: _socket_entry()}
    coordinator.api.async_get_sub_shadow = AsyncMock(
        side_effect=JackeryAuthError("token rejected"),
    )

    # Must complete without raising ConfigEntryAuthFailed or JackeryAuthError.
    await coordinator._async_shadow_fallback_for_missing(snapshot)  # noqa: SLF001

    assert not coordinator.push_calls


@pytest.mark.asyncio()
async def test_per_device_throttle_blocks_second_run_within_interval() -> None:
    """A second run inside the throttle window must not re-issue the shadow call."""
    coordinator = _coordinator_stub()
    snapshot = {DEVICE_ID: _socket_entry()}
    coordinator.api.async_get_sub_shadow.return_value = {
        FIELD_PLUGS: [{FIELD_DEVICE_SN: PLUG_SN, "sw": 1}],
    }

    await coordinator._async_shadow_fallback_for_missing(snapshot)  # noqa: SLF001
    first_calls = coordinator.api.async_get_sub_shadow.await_count

    # Re-running immediately (fresh empty bucket again) must be throttled.
    snapshot2 = {DEVICE_ID: _socket_entry()}
    await coordinator._async_shadow_fallback_for_missing(snapshot2)  # noqa: SLF001

    assert coordinator.api.async_get_sub_shadow.await_count == first_calls


def test_schedule_shadow_fallback_runs_regardless_of_mqtt_state() -> None:
    """Scheduler must launch a background task even when MQTT never connected."""
    coordinator = _coordinator_stub()
    coordinator.hass = MagicMock()
    sentinel = object()
    coordinator.hass.async_create_background_task = MagicMock(return_value=sentinel)
    snapshot = {DEVICE_ID: _socket_entry()}

    coordinator._schedule_shadow_fallback(snapshot)  # noqa: SLF001

    coordinator.hass.async_create_background_task.assert_called_once()
    assert coordinator._shadow_fallback_task is sentinel  # noqa: SLF001
    # Close the un-awaited coroutine passed to the mocked task factory.
    coro = coordinator.hass.async_create_background_task.call_args.args[0]
    coro.close()


def test_schedule_shadow_fallback_skips_when_task_in_flight() -> None:
    """A second schedule while a task is running must not stack tasks."""
    coordinator = _coordinator_stub()
    coordinator.hass = MagicMock()
    running = MagicMock()
    running.done.return_value = False
    coordinator._shadow_fallback_task = running  # noqa: SLF001

    coordinator._schedule_shadow_fallback({DEVICE_ID: _socket_entry()})  # noqa: SLF001

    coordinator.hass.async_create_background_task.assert_not_called()
