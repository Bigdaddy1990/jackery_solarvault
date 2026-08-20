# mypy: disable-error-code="assignment,dict-item,no-untyped-def"
"""Behavioral tests for the coordinator's system-info enrichment query methods.

These drive `_async_query_system_info_for_missing`, `_async_query_weather_plan_for_missing`,
and `_async_query_subdevices_for_missing` through their state transitions with a
stubbed coordinator, asserting business outcomes (which devices are queried,
which queries are skipped by throttle/conditions) — never call order.
"""  # noqa: E501, RUF105

import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.const import (
    ACTION_ID_QUERY_COMBINE_DATA,
    ACTION_ID_QUERY_DEVICE_PROPERTY,
    ACTION_ID_QUERY_WEATHER_PLAN,
    PAYLOAD_DEVICE,
    PAYLOAD_PROPERTIES,
)
from tests._update_cycle_fixture import (  # ruff:ignore[banned-api]
    DEVICE_ID,
    make_update_cycle_api,
    setup_update_cycle_coordinator,
)


async def _teardown(hass, entry_id) -> None:  # noqa: ANN001, RUF105
    """Unload the entry and drain background tasks."""
    await hass.config_entries.async_unload(entry_id)
    await hass.async_block_till_done()


def _device_payload(  # noqa: RUF105
    *,
    has_system_info: bool = False,
    has_weather: bool = False,
    has_ct_meter: bool = False,
    has_battery_packs: bool = False,
    has_breaker: bool = False,
    has_sub_device: bool = False,
    has_meter_head: bool = False,
    has_smart_plug: bool = False,
    has_smart_meter: bool = False,
) -> dict[str, Any]:
    """Build a minimal device payload with selected enrichment sections."""
    from custom_components.jackery_solarvault.const import (  # noqa: PLC0415, RUF105
        FIELD_ACCESSORIES,
        FIELD_DEV_TYPE,
        FIELD_SUB_TYPE,
        PAYLOAD_BATTERY_PACKS,
        PAYLOAD_SUBDEVICES,
        PAYLOAD_SYSTEM,
        PAYLOAD_SYSTEM_META,
        SUBDEVICE_DEV_TYPE_BREAKER,
        SUBDEVICE_DEV_TYPE_METER_HEAD,
        SUBDEVICE_DEV_TYPE_SOCKET,
        SUBDEVICE_TYPE_COMBINE,
    )

    payload = {
        PAYLOAD_DEVICE: {DEVICE_ID: DEVICE_ID, "modelCode": "HTB2000"},
        PAYLOAD_PROPERTIES: {},
    }
    if has_system_info:
        # Populate with actual SYSTEM_INFO_KEYS fields so has_all check passes
        from custom_components.jackery_solarvault.const import SYSTEM_INFO_KEYS  # noqa: I001, PLC0415, RUF105

        payload[PAYLOAD_PROPERTIES].update(dict.fromkeys(SYSTEM_INFO_KEYS, "value"))
    if has_weather:
        payload["weather_plan"] = {"wpc": 5}
    if has_ct_meter:
        payload["ct_meter"] = {"voltage": 230}
    if has_battery_packs:
        payload[PAYLOAD_BATTERY_PACKS] = [{"packSn": "PACK1"}]
        # Also set batNum so battery_packs_need_query returns True
        payload[PAYLOAD_PROPERTIES]["batNum"] = 1
    if has_breaker:
        payload[PAYLOAD_SYSTEM] = {
            FIELD_ACCESSORIES: [{FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_BREAKER}]
        }
    if has_sub_device:
        payload[PAYLOAD_SUBDEVICES] = [{"devType": "generic"}]
    if has_meter_head:
        payload[PAYLOAD_SYSTEM_META] = {
            FIELD_ACCESSORIES: [{FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_METER_HEAD}]
        }
    if has_smart_plug:
        payload[PAYLOAD_SYSTEM_META] = {
            FIELD_ACCESSORIES: [{FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET}]
        }
    if has_smart_meter:
        # Smart meter uses subType "2" (SUBDEVICE_TYPE_COMBINE) in accessories
        payload[PAYLOAD_SYSTEM_META] = {
            FIELD_ACCESSORIES: [{FIELD_SUB_TYPE: SUBDEVICE_TYPE_COMBINE}]
        }
    return payload


@pytest.fixture
async def coordinator(hass):  # noqa: ANN001, ANN201, RUF105
    """Yield a coordinator with mocked api for enrichment query tests."""
    api = make_update_cycle_api()
    coord, entry, _api = await setup_update_cycle_coordinator(
        hass, api=api, discover=True
    )
    # Entry setup legitimately performs its own enrichment pass in background.
    # Most cases below exercise transport and error behavior, not the scheduler
    # window, so neutralize that setup throttle.  The dedicated throttle cases
    # explicitly restore non-zero intervals before asserting the gate.
    coord._system_info_query_interval_sec = 0  # ruff: ignore[private-member-access]
    coord._weather_plan_query_interval_sec = 0  # ruff: ignore[private-member-access]
    coord._subdevice_query_interval_sec = 0  # ruff: ignore[private-member-access]
    yield coord
    await _teardown(hass, entry.entry_id)


# ---------------------------------------------------------------------------
# Independent background dispatch
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_ble_background_getters_never_start_cloud_mqtt(coordinator) -> None:  # noqa: ANN001, RUF105
    """A BLE-only enrichment pass dispatches without Cloud-MQTT startup."""
    coordinator._ble_listener = MagicMock()  # noqa: RUF105, SLF001
    coordinator._mqtt = None  # noqa: RUF105, SLF001
    coordinator._mqtt_session_generation = 1  # noqa: RUF105, SLF001
    coordinator._mqtt_birth_snapshot_pending = False  # noqa: RUF105, SLF001
    coordinator._async_query_subdevices_for_missing = AsyncMock()  # noqa: RUF105, SLF001
    coordinator._async_query_system_info_for_missing = AsyncMock()  # noqa: RUF105, SLF001
    coordinator._async_query_weather_plan_for_missing = AsyncMock()  # noqa: RUF105, SLF001
    coordinator.async_start_mqtt = AsyncMock()
    snapshot = {DEVICE_ID: _device_payload()}

    await coordinator._async_mqtt_poll_queries(snapshot)  # noqa: RUF105, SLF001

    coordinator._async_query_subdevices_for_missing.assert_awaited_once_with(  # noqa: RUF105, SLF001
        force=False,
        snapshot=snapshot,
        ensure_mqtt=False,
    )
    coordinator._async_query_system_info_for_missing.assert_awaited_once_with(  # noqa: RUF105, SLF001
        force=False,
        snapshot=snapshot,
        ensure_mqtt=False,
    )
    coordinator._async_query_weather_plan_for_missing.assert_awaited_once_with(  # noqa: RUF105, SLF001
        force=False,
        snapshot=snapshot,
        ensure_mqtt=False,
    )
    coordinator.async_start_mqtt.assert_not_awaited()


# ---------------------------------------------------------------------------
# _async_query_system_info_for_missing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_system_info_query_skipped_without_transport(coordinator) -> None:  # noqa: ANN001, RUF105
    """No BLE and no Cloud MQTT: query is a no-op."""
    coordinator._ble_listener = None  # noqa: RUF105, SLF001
    coordinator._mqtt = None  # noqa: RUF105, SLF001
    coordinator.data = {DEVICE_ID: _device_payload()}
    coordinator.async_query_device_info = AsyncMock()
    coordinator.async_query_system_info = AsyncMock()
    await coordinator._async_query_system_info_for_missing()  # noqa: RUF105, SLF001
    coordinator.async_query_device_info.assert_not_awaited()
    coordinator.async_query_system_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_system_info_query_runs_when_mqtt_ready(coordinator) -> None:  # noqa: ANN001, RUF105
    """Connected Cloud MQTT enables the query path."""
    mqtt = MagicMock()
    mqtt.is_connected = True
    coordinator._mqtt = mqtt  # noqa: RUF105, SLF001
    coordinator._ble_listener = None  # noqa: RUF105, SLF001
    coordinator.data = {DEVICE_ID: _device_payload()}
    coordinator.async_query_device_info = AsyncMock()
    coordinator.async_query_system_info = AsyncMock()
    # Prevent actual MQTT command execution
    coordinator._async_publish_command_ble_first = AsyncMock()  # noqa: RUF105, SLF001

    await coordinator._async_query_system_info_for_missing()  # noqa: RUF105, SLF001

    coordinator.async_query_device_info.assert_awaited_once_with(
        DEVICE_ID, ensure_mqtt=True
    )
    coordinator.async_query_system_info.assert_awaited_once_with(
        DEVICE_ID, ensure_mqtt=True
    )


@pytest.mark.asyncio
async def test_system_info_query_runs_when_ble_ready(coordinator) -> None:  # noqa: ANN001, RUF105
    """Live BLE listener enables the query path."""
    coordinator._ble_listener = MagicMock()  # noqa: RUF105, SLF001
    coordinator._mqtt = None  # noqa: RUF105, SLF001
    coordinator.data = {DEVICE_ID: _device_payload()}
    coordinator.async_query_device_info = AsyncMock()
    coordinator.async_query_system_info = AsyncMock()
    # Prevent actual BLE command execution
    coordinator._async_publish_command_ble_first = AsyncMock()  # noqa: RUF105, SLF001

    await coordinator._async_query_system_info_for_missing()  # noqa: RUF105, SLF001

    coordinator.async_query_device_info.assert_awaited_once()
    coordinator.async_query_system_info.assert_awaited_once()


@pytest.mark.asyncio
async def test_system_info_query_refreshes_complete_data_when_periodic_due(
    coordinator,  # noqa: ANN001, RUF105
) -> None:
    """Complete data is refreshed after the periodic getter interval."""
    mqtt = MagicMock()
    mqtt.is_connected = True
    coordinator._mqtt = mqtt  # noqa: RUF105, SLF001
    coordinator.data = {DEVICE_ID: _device_payload(has_system_info=True)}
    coordinator._system_info_query_interval_sec = 60  # noqa: RUF105, SLF001
    coordinator._last_system_info_query[DEVICE_ID] = time.monotonic() - 61  # noqa: RUF105, SLF001
    coordinator._mqtt_session_actions_seen.update({  # noqa: RUF105, SLF001
        (DEVICE_ID, ACTION_ID_QUERY_DEVICE_PROPERTY),
        (DEVICE_ID, ACTION_ID_QUERY_COMBINE_DATA),
    })
    coordinator.async_query_device_info = AsyncMock()
    coordinator.async_query_system_info = AsyncMock()

    await coordinator._async_query_system_info_for_missing()  # noqa: RUF105, SLF001

    coordinator.async_query_device_info.assert_awaited_once_with(
        DEVICE_ID, ensure_mqtt=True
    )
    coordinator.async_query_system_info.assert_awaited_once_with(
        DEVICE_ID, ensure_mqtt=True
    )


@pytest.mark.asyncio
async def test_system_info_query_skips_complete_data_before_periodic_due(
    coordinator,  # noqa: ANN001, RUF105
) -> None:
    """Complete data remains throttled until the periodic interval expires."""
    mqtt = MagicMock()
    mqtt.is_connected = True
    coordinator._mqtt = mqtt  # noqa: RUF105, SLF001
    coordinator.data = {DEVICE_ID: _device_payload(has_system_info=True)}
    coordinator._system_info_query_interval_sec = 60  # noqa: RUF105, SLF001
    coordinator._last_system_info_query[DEVICE_ID] = time.monotonic() - 1  # noqa: RUF105, SLF001
    coordinator._mqtt_session_actions_seen.update({  # noqa: RUF105, SLF001
        (DEVICE_ID, ACTION_ID_QUERY_DEVICE_PROPERTY),
        (DEVICE_ID, ACTION_ID_QUERY_COMBINE_DATA),
    })
    coordinator.async_query_device_info = AsyncMock()
    coordinator.async_query_system_info = AsyncMock()

    await coordinator._async_query_system_info_for_missing()  # noqa: RUF105, SLF001

    coordinator.async_query_device_info.assert_not_awaited()
    coordinator.async_query_system_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_system_info_query_force_true_runs_even_with_complete_data(
    coordinator,  # noqa: ANN001, RUF105
) -> None:
    """force=True overrides the completeness check."""
    mqtt = MagicMock()
    mqtt.is_connected = True
    coordinator._mqtt = mqtt  # noqa: RUF105, SLF001
    coordinator.data = {DEVICE_ID: _device_payload(has_system_info=True)}
    coordinator.async_query_device_info = AsyncMock()
    coordinator.async_query_system_info = AsyncMock()

    await coordinator._async_query_system_info_for_missing(force=True)  # noqa: RUF105, SLF001

    coordinator.async_query_device_info.assert_awaited_once()
    coordinator.async_query_system_info.assert_awaited_once()


@pytest.mark.asyncio
async def test_system_info_query_respects_throttle(coordinator) -> None:  # noqa: ANN001, RUF105
    """A recent query for the same device is throttled."""
    mqtt = MagicMock()
    mqtt.is_connected = True
    coordinator._mqtt = mqtt  # noqa: RUF105, SLF001
    coordinator.data = {DEVICE_ID: _device_payload()}
    coordinator._last_system_info_query[DEVICE_ID] = time.monotonic() - 1  # noqa: RUF105, SLF001
    coordinator._system_info_query_interval_sec = 60  # noqa: RUF105, SLF001
    coordinator.async_query_device_info = AsyncMock()
    coordinator.async_query_system_info = AsyncMock()

    await coordinator._async_query_system_info_for_missing()  # noqa: RUF105, SLF001

    coordinator.async_query_device_info.assert_not_awaited()
    coordinator.async_query_system_info.assert_not_awaited()


@pytest.mark.asyncio
async def test_system_info_query_force_true_bypasses_throttle(coordinator) -> None:  # noqa: ANN001, RUF105
    """force=True ignores the throttle window."""
    import time  # noqa: PLC0415, RUF105

    mqtt = MagicMock()
    mqtt.is_connected = True
    coordinator._mqtt = mqtt  # noqa: RUF105, SLF001
    coordinator.data = {DEVICE_ID: _device_payload()}
    coordinator._last_system_info_query[DEVICE_ID] = time.monotonic() - 1  # noqa: RUF105, SLF001
    coordinator._system_info_query_interval_sec = 60  # noqa: RUF105, SLF001
    coordinator.async_query_device_info = AsyncMock()
    coordinator.async_query_system_info = AsyncMock()

    await coordinator._async_query_system_info_for_missing(force=True)  # noqa: RUF105, SLF001

    coordinator.async_query_device_info.assert_awaited_once()
    coordinator.async_query_system_info.assert_awaited_once()


@pytest.mark.asyncio
async def test_system_info_query_handles_device_info_failure(coordinator) -> None:  # noqa: ANN001, RUF105
    """DeviceInfo error is caught, system-info query still runs."""
    mqtt = MagicMock()
    mqtt.is_connected = True
    coordinator._mqtt = mqtt  # noqa: RUF105, SLF001
    coordinator.data = {DEVICE_ID: _device_payload()}
    coordinator.async_query_device_info = AsyncMock(side_effect=TimeoutError("timeout"))
    coordinator.async_query_system_info = AsyncMock()
    # Prevent actual MQTT command execution
    coordinator._async_publish_command_ble_first = AsyncMock()  # noqa: RUF105, SLF001

    await coordinator._async_query_system_info_for_missing()  # noqa: RUF105, SLF001

    coordinator.async_query_system_info.assert_awaited_once()


@pytest.mark.asyncio
async def test_system_info_query_handles_system_info_failure(coordinator) -> None:  # noqa: ANN001, RUF105
    """SystemInfo error is caught and logged."""
    mqtt = MagicMock()
    mqtt.is_connected = True
    coordinator._mqtt = mqtt  # noqa: RUF105, SLF001
    coordinator.data = {DEVICE_ID: _device_payload()}
    coordinator.async_query_device_info = AsyncMock()
    coordinator.async_query_system_info = AsyncMock(side_effect=TimeoutError("timeout"))
    # Prevent actual MQTT command execution
    coordinator._async_publish_command_ble_first = AsyncMock()  # noqa: RUF105, SLF001

    await coordinator._async_query_system_info_for_missing()  # noqa: RUF105, SLF001

    coordinator.async_query_device_info.assert_awaited_once()


@pytest.mark.asyncio
async def test_system_info_query_uses_snapshot_when_provided(coordinator) -> None:  # noqa: ANN001, RUF105
    """A caller-provided snapshot overrides coordinator.data."""
    mqtt = MagicMock()
    mqtt.is_connected = True
    coordinator._mqtt = mqtt  # noqa: RUF105, SLF001
    coordinator.data = {DEVICE_ID: _device_payload()}
    snapshot = {DEVICE_ID: _device_payload(has_system_info=True)}
    coordinator._system_info_query_interval_sec = 60  # noqa: RUF105, SLF001
    coordinator._last_system_info_query[DEVICE_ID] = time.monotonic() - 1  # noqa: RUF105, SLF001
    coordinator._mqtt_session_actions_seen.update({  # noqa: RUF105, SLF001
        (DEVICE_ID, ACTION_ID_QUERY_DEVICE_PROPERTY),
        (DEVICE_ID, ACTION_ID_QUERY_COMBINE_DATA),
    })
    coordinator.async_query_device_info = AsyncMock()
    coordinator.async_query_system_info = AsyncMock()

    await coordinator._async_query_system_info_for_missing(snapshot=snapshot)  # noqa: RUF105, SLF001

    coordinator.async_query_device_info.assert_not_awaited()


# ---------------------------------------------------------------------------
# _async_query_weather_plan_for_missing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_weather_plan_query_skipped_without_mqtt(coordinator) -> None:  # noqa: ANN001, RUF105
    """No Cloud MQTT: weather plan query is a no-op."""
    coordinator._mqtt = None  # noqa: RUF105, SLF001
    coordinator.data = {DEVICE_ID: _device_payload()}
    coordinator.async_query_weather_plan = AsyncMock()

    await coordinator._async_query_weather_plan_for_missing()  # noqa: RUF105, SLF001

    coordinator.async_query_weather_plan.assert_not_awaited()


@pytest.mark.asyncio
async def test_weather_plan_query_runs_when_mqtt_connected(coordinator) -> None:  # noqa: ANN001, RUF105
    """Connected Cloud MQTT enables the query."""
    mqtt = MagicMock()
    mqtt.is_connected = True
    coordinator._mqtt = mqtt  # noqa: RUF105, SLF001
    coordinator.data = {DEVICE_ID: _device_payload()}
    coordinator.async_query_weather_plan = AsyncMock()
    # Prevent actual MQTT command execution
    coordinator._async_publish_command_ble_first = AsyncMock()  # noqa: RUF105, SLF001

    await coordinator._async_query_weather_plan_for_missing()  # noqa: RUF105, SLF001

    coordinator.async_query_weather_plan.assert_awaited_once_with(
        DEVICE_ID, ensure_mqtt=True
    )


@pytest.mark.asyncio
async def test_weather_plan_query_refreshes_minutes_when_periodic_due(
    coordinator,  # noqa: ANN001, RUF105
) -> None:
    """Existing lead-time fields are refreshed after the periodic interval."""
    mqtt = MagicMock()
    mqtt.is_connected = True
    coordinator._mqtt = mqtt  # noqa: RUF105, SLF001
    coordinator.data = {DEVICE_ID: _device_payload(has_weather=True)}
    coordinator._weather_plan_query_interval_sec = 300  # noqa: RUF105, SLF001
    coordinator._last_weather_plan_query[DEVICE_ID] = time.monotonic() - 301  # noqa: RUF105, SLF001
    coordinator._mqtt_session_actions_seen.add(  # noqa: RUF105, SLF001
        (DEVICE_ID, ACTION_ID_QUERY_WEATHER_PLAN)
    )
    coordinator.async_query_weather_plan = AsyncMock()

    await coordinator._async_query_weather_plan_for_missing()  # noqa: RUF105, SLF001

    coordinator.async_query_weather_plan.assert_awaited_once_with(
        DEVICE_ID, ensure_mqtt=True
    )


@pytest.mark.asyncio
async def test_weather_plan_query_skips_minutes_before_periodic_due(
    coordinator,  # noqa: ANN001, RUF105
) -> None:
    """Existing lead-time fields remain throttled before the interval expires."""
    mqtt = MagicMock()
    mqtt.is_connected = True
    coordinator._mqtt = mqtt  # noqa: RUF105, SLF001
    coordinator.data = {DEVICE_ID: _device_payload(has_weather=True)}
    coordinator._weather_plan_query_interval_sec = 300  # noqa: RUF105, SLF001
    coordinator._last_weather_plan_query[DEVICE_ID] = time.monotonic() - 1  # noqa: RUF105, SLF001
    coordinator._mqtt_session_actions_seen.add(  # noqa: RUF105, SLF001
        (DEVICE_ID, ACTION_ID_QUERY_WEATHER_PLAN)
    )
    coordinator.async_query_weather_plan = AsyncMock()

    await coordinator._async_query_weather_plan_for_missing()  # noqa: RUF105, SLF001

    coordinator.async_query_weather_plan.assert_not_awaited()


@pytest.mark.asyncio
async def test_weather_plan_query_force_true_bypasses_completeness(coordinator) -> None:  # noqa: ANN001, RUF105
    """force=True ignores existing lead-time fields."""
    mqtt = MagicMock()
    mqtt.is_connected = True
    coordinator._mqtt = mqtt  # noqa: RUF105, SLF001
    coordinator.data = {DEVICE_ID: _device_payload(has_weather=True)}
    coordinator.async_query_weather_plan = AsyncMock()

    await coordinator._async_query_weather_plan_for_missing(force=True)  # noqa: RUF105, SLF001

    coordinator.async_query_weather_plan.assert_awaited_once()


@pytest.mark.asyncio
async def test_weather_plan_query_respects_throttle(coordinator) -> None:  # noqa: ANN001, RUF105
    """A recent query for the same device is throttled."""
    import time  # noqa: PLC0415, RUF105

    mqtt = MagicMock()
    mqtt.is_connected = True
    coordinator._mqtt = mqtt  # noqa: RUF105, SLF001
    coordinator.data = {DEVICE_ID: _device_payload()}
    coordinator._last_weather_plan_query[DEVICE_ID] = time.monotonic() - 1  # noqa: RUF105, SLF001
    coordinator._weather_plan_query_interval_sec = 300  # noqa: RUF105, SLF001
    coordinator.async_query_weather_plan = AsyncMock()

    await coordinator._async_query_weather_plan_for_missing()  # noqa: RUF105, SLF001

    coordinator.async_query_weather_plan.assert_not_awaited()


@pytest.mark.asyncio
async def test_weather_plan_query_force_true_bypasses_throttle(coordinator) -> None:  # noqa: ANN001, RUF105
    """force=True ignores the throttle window."""
    import time  # noqa: PLC0415, RUF105

    mqtt = MagicMock()
    mqtt.is_connected = True
    coordinator._mqtt = mqtt  # noqa: RUF105, SLF001
    coordinator.data = {DEVICE_ID: _device_payload()}
    coordinator._last_weather_plan_query[DEVICE_ID] = time.monotonic() - 1  # noqa: RUF105, SLF001
    coordinator._weather_plan_query_interval_sec = 300  # noqa: RUF105, SLF001
    coordinator.async_query_weather_plan = AsyncMock()

    await coordinator._async_query_weather_plan_for_missing(force=True)  # noqa: RUF105, SLF001

    coordinator.async_query_weather_plan.assert_awaited_once()


# ---------------------------------------------------------------------------
# _async_query_subdevices_for_missing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subdevice_query_skipped_when_no_accessories(coordinator) -> None:  # noqa: ANN001, RUF105
    """No accessory flags: query is a no-op."""
    coordinator.data = {DEVICE_ID: _device_payload()}
    coordinator.async_query_battery_packs = AsyncMock()
    coordinator.async_query_subdevice_combo = AsyncMock()
    coordinator.async_query_smart_meter = AsyncMock()
    coordinator.async_query_meter_heads = AsyncMock()
    coordinator.async_query_smart_plugs = AsyncMock()

    await coordinator._async_query_subdevices_for_missing()  # noqa: RUF105, SLF001

    coordinator.async_query_battery_packs.assert_not_awaited()
    coordinator.async_query_subdevice_combo.assert_not_awaited()
    coordinator.async_query_smart_meter.assert_not_awaited()
    coordinator.async_query_meter_heads.assert_not_awaited()
    coordinator.async_query_smart_plugs.assert_not_awaited()


@pytest.mark.asyncio
async def test_subdevice_query_battery_packs_when_present(coordinator) -> None:  # noqa: ANN001, RUF105
    """Battery pack accessory triggers pack query."""
    coordinator.data = {DEVICE_ID: _device_payload(has_battery_packs=True)}
    coordinator.async_query_battery_packs = AsyncMock()
    coordinator.async_query_subdevice_combo = AsyncMock()
    coordinator.async_query_smart_meter = AsyncMock()
    coordinator.async_query_meter_heads = AsyncMock()
    coordinator.async_query_smart_plugs = AsyncMock()

    await coordinator._async_query_subdevices_for_missing()  # noqa: RUF105, SLF001

    coordinator.async_query_battery_packs.assert_awaited_once_with(
        DEVICE_ID, ensure_mqtt=True
    )


@pytest.mark.asyncio
async def test_subdevice_query_combo_when_breaker_present(coordinator) -> None:  # noqa: ANN001, RUF105
    """Breaker accessory triggers combo query."""
    coordinator.data = {DEVICE_ID: _device_payload(has_breaker=True)}
    coordinator.async_query_battery_packs = AsyncMock()
    coordinator.async_query_subdevice_combo = AsyncMock()
    coordinator.async_query_smart_meter = AsyncMock()
    coordinator.async_query_meter_heads = AsyncMock()
    coordinator.async_query_smart_plugs = AsyncMock()

    await coordinator._async_query_subdevices_for_missing()  # noqa: RUF105, SLF001

    coordinator.async_query_subdevice_combo.assert_awaited_once_with(
        DEVICE_ID, ensure_mqtt=True
    )


@pytest.mark.asyncio
async def test_subdevice_query_smart_meter_when_present(coordinator) -> None:  # noqa: ANN001, RUF105
    """Smart meter accessory triggers meter query."""
    coordinator.data = {DEVICE_ID: _device_payload(has_smart_meter=True)}
    coordinator.async_query_battery_packs = AsyncMock()
    coordinator.async_query_subdevice_combo = AsyncMock()
    coordinator.async_query_smart_meter = AsyncMock()
    coordinator.async_query_meter_heads = AsyncMock()
    coordinator.async_query_smart_plugs = AsyncMock()

    await coordinator._async_query_subdevices_for_missing()  # noqa: RUF105, SLF001

    coordinator.async_query_smart_meter.assert_awaited_once_with(
        DEVICE_ID, ensure_mqtt=True
    )


@pytest.mark.asyncio
async def test_subdevice_query_ct_meter_triggers_meter_query(coordinator) -> None:  # noqa: ANN001, RUF105
    """CT meter in payload triggers smart meter query."""
    coordinator.data = {DEVICE_ID: _device_payload(has_ct_meter=True)}
    coordinator.async_query_battery_packs = AsyncMock()
    coordinator.async_query_subdevice_combo = AsyncMock()
    coordinator.async_query_smart_meter = AsyncMock()
    coordinator.async_query_meter_heads = AsyncMock()
    coordinator.async_query_smart_plugs = AsyncMock()

    await coordinator._async_query_subdevices_for_missing()  # noqa: RUF105, SLF001

    coordinator.async_query_smart_meter.assert_awaited_once()


@pytest.mark.asyncio
async def test_subdevice_query_meter_head_when_present(coordinator) -> None:  # noqa: ANN001, RUF105
    """Meter head accessory triggers meter head query."""
    coordinator.data = {DEVICE_ID: _device_payload(has_meter_head=True)}
    coordinator.async_query_battery_packs = AsyncMock()
    coordinator.async_query_subdevice_combo = AsyncMock()
    coordinator.async_query_smart_meter = AsyncMock()
    coordinator.async_query_meter_heads = AsyncMock()
    coordinator.async_query_smart_plugs = AsyncMock()

    await coordinator._async_query_subdevices_for_missing()  # noqa: RUF105, SLF001

    coordinator.async_query_meter_heads.assert_awaited_once_with(
        DEVICE_ID, ensure_mqtt=True
    )


@pytest.mark.asyncio
async def test_subdevice_query_smart_plug_when_present(coordinator) -> None:  # noqa: ANN001, RUF105
    """Smart plug accessory triggers smart plug query."""
    coordinator.data = {DEVICE_ID: _device_payload(has_smart_plug=True)}
    coordinator.async_query_battery_packs = AsyncMock()
    coordinator.async_query_subdevice_combo = AsyncMock()
    coordinator.async_query_smart_meter = AsyncMock()
    coordinator.async_query_meter_heads = AsyncMock()
    coordinator.async_query_smart_plugs = AsyncMock()

    await coordinator._async_query_subdevices_for_missing()  # noqa: RUF105, SLF001

    coordinator.async_query_smart_plugs.assert_awaited_once_with(
        DEVICE_ID, ensure_mqtt=True
    )


@pytest.mark.asyncio
async def test_subdevice_query_force_true_runs_all_for_device(coordinator) -> None:  # noqa: ANN001, RUF105
    """force=True triggers all subdevice query types for the device."""
    coordinator.data = {DEVICE_ID: _device_payload()}
    coordinator.async_query_battery_packs = AsyncMock()
    coordinator.async_query_subdevice_combo = AsyncMock()
    coordinator.async_query_smart_meter = AsyncMock()
    coordinator.async_query_meter_heads = AsyncMock()
    coordinator.async_query_smart_plugs = AsyncMock()

    await coordinator._async_query_subdevices_for_missing(force=True)  # noqa: RUF105, SLF001

    coordinator.async_query_battery_packs.assert_awaited_once()
    coordinator.async_query_subdevice_combo.assert_awaited_once()
    coordinator.async_query_smart_meter.assert_awaited_once()
    coordinator.async_query_meter_heads.assert_awaited_once()
    coordinator.async_query_smart_plugs.assert_awaited_once()


@pytest.mark.asyncio
async def test_subdevice_query_respects_throttle(coordinator) -> None:  # noqa: ANN001, RUF105
    """A recent query for the same device is throttled."""
    import time  # noqa: PLC0415, RUF105

    coordinator.data = {DEVICE_ID: _device_payload(has_battery_packs=True)}
    coordinator._last_subdevice_query[DEVICE_ID] = time.monotonic() - 1  # noqa: RUF105, SLF001
    coordinator._subdevice_query_interval_sec = 300  # noqa: RUF105, SLF001
    coordinator.async_query_battery_packs = AsyncMock()

    await coordinator._async_query_subdevices_for_missing()  # noqa: RUF105, SLF001

    coordinator.async_query_battery_packs.assert_not_awaited()


@pytest.mark.asyncio
async def test_subdevice_query_force_true_bypasses_throttle(coordinator) -> None:  # noqa: ANN001, RUF105
    """force=True ignores the throttle window."""
    import time  # noqa: PLC0415, RUF105

    coordinator.data = {DEVICE_ID: _device_payload(has_battery_packs=True)}
    coordinator._last_subdevice_query[DEVICE_ID] = time.monotonic() - 1  # noqa: RUF105, SLF001
    coordinator._subdevice_query_interval_sec = 300  # noqa: RUF105, SLF001
    coordinator.async_query_battery_packs = AsyncMock()
    # Prevent actual MQTT command execution
    coordinator._async_publish_command_ble_first = AsyncMock()  # noqa: RUF105, SLF001

    await coordinator._async_query_subdevices_for_missing(force=True)  # noqa: RUF105, SLF001

    coordinator.async_query_battery_packs.assert_awaited_once()


@pytest.mark.asyncio
async def test_subdevice_query_uses_snapshot_when_provided(coordinator) -> None:  # noqa: ANN001, RUF105
    """A caller-provided snapshot overrides coordinator.data."""
    coordinator.data = {DEVICE_ID: _device_payload(has_battery_packs=True)}
    snapshot = {DEVICE_ID: _device_payload()}
    coordinator.async_query_battery_packs = AsyncMock()

    await coordinator._async_query_subdevices_for_missing(snapshot=snapshot)  # noqa: RUF105, SLF001

    coordinator.async_query_battery_packs.assert_not_awaited()


@pytest.mark.asyncio
async def test_subdevice_query_handles_battery_pack_failure(coordinator) -> None:  # noqa: ANN001, RUF105
    """Battery pack error is caught, other queries still run."""
    coordinator.data = {
        DEVICE_ID: _device_payload(has_battery_packs=True, has_breaker=True)
    }
    coordinator.async_query_battery_packs = AsyncMock(
        side_effect=TimeoutError("timeout")
    )
    coordinator.async_query_subdevice_combo = AsyncMock()
    coordinator.async_query_smart_meter = AsyncMock()
    coordinator.async_query_meter_heads = AsyncMock()
    coordinator.async_query_smart_plugs = AsyncMock()
    # Prevent actual MQTT command execution
    coordinator._async_publish_command_ble_first = AsyncMock()  # noqa: RUF105, SLF001

    await coordinator._async_query_subdevices_for_missing()  # noqa: RUF105, SLF001

    coordinator.async_query_subdevice_combo.assert_awaited_once()
