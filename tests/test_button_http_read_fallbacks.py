"""Documented HTTP read fallbacks for manual refresh buttons."""

import asyncio
from datetime import timedelta
import time
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.button import (
    QUERY_BUTTON_DESCRIPTIONS,
    JackeryQueryButton,
)
from custom_components.jackery_solarvault.const import (
    FIELD_ACCESSORIES,
    FIELD_BATTERY_PACKS,
    FIELD_BAT_SOC,
    FIELD_COLLECTORS,
    FIELD_CT_POWER,
    FIELD_DEVICE_SN,
    FIELD_DEV_TYPE,
    FIELD_ENERGY_PLAN_PW,
    FIELD_ID,
    FIELD_PLUGS,
    FIELD_SUB_DEVICE,
    PAYLOAD_BATTERY_PACKS,
    PAYLOAD_CT_METER,
    PAYLOAD_DEVICE,
    PAYLOAD_HTTP_PROPERTIES,
    PAYLOAD_METER_HEADS,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_SUBDEVICES,
    PAYLOAD_SYSTEM,
    SUBDEVICE_DEV_TYPE_BATTERY_PACK,
    SUBDEVICE_DEV_TYPE_COMBO,
    SUBDEVICE_DEV_TYPE_CT,
    SUBDEVICE_DEV_TYPE_METER_HEAD,
    SUBDEVICE_DEV_TYPE_SOCKET,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from custom_components.jackery_solarvault.ingest import TransportSource
from custom_components.jackery_solarvault.models import FieldProvenance
from homeassistant.exceptions import HomeAssistantError

_DEVICE_ID = "dev-1"
_DEVICE_SN = "PARENT-SN"
_HTTP_PV_POWER = 300
_LIVE_PV_POWER = 900
_NEWER_LIVE_PV_POWER = 950
_LIVE_PACK_SOC = 80
_SYSTEM_ENERGY_PLAN_POWER = 725


def _description(key: str) -> Any:  # noqa: RUF105
    """Return one query-button description by key."""
    return next(item for item in QUERY_BUTTON_DESCRIPTIONS if item.key == key)


def _bare_coordinator(entry: dict[str, Any]) -> JackerySolarVaultCoordinator:
    """Create the minimal coordinator shell needed by documented HTTP reads."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    shell = cast("Any", coordinator)
    shell.data = {_DEVICE_ID: entry}
    shell.api = MagicMock()
    shell.api.async_get_device_property = AsyncMock(return_value={})
    shell.api.async_get_system_shadow = AsyncMock(return_value={})
    shell.api.async_get_battery_pack_list = AsyncMock(return_value=[])
    shell.api.async_get_sub_shadow = AsyncMock(return_value={})
    shell._shutdown_started = False  # ruff: ignore[private-member-access]
    shell._listeners = {}  # ruff: ignore[private-member-access]
    shell._property_overrides = {}  # ruff: ignore[private-member-access]
    shell._property_source_state = {}  # ruff: ignore[private-member-access]
    shell._accessory_source_state = {}  # ruff: ignore[private-member-access]
    shell._live_property_received_monotonic = {}  # ruff: ignore[private-member-access]
    shell._live_ct_received_monotonic = {}  # ruff: ignore[private-member-access]
    shell._last_http_device_refresh_monotonic = {}  # ruff: ignore[private-member-access]
    shell._configured_update_interval = timedelta(seconds=15)  # ruff: ignore[private-member-access]
    shell._system_info_cache = {}  # ruff: ignore[private-member-access]
    shell._system_info_cache_monotonic = {}  # ruff: ignore[private-member-access]
    shell._pending_device_removals = []  # ruff: ignore[private-member-access]
    shell._device_index = {}  # ruff: ignore[private-member-access]
    shell._device_registry_observer = None  # ruff: ignore[private-member-access]
    return coordinator


def _mock_api(coordinator: JackerySolarVaultCoordinator) -> Any:  # noqa: RUF105
    """Return the API test double behind the coordinator's typed boundary."""
    return cast("Any", coordinator.api)


def _button(coordinator: Any, key: str) -> JackeryQueryButton:  # noqa: RUF105
    """Create a query button against a lightweight coordinator double."""
    coordinator.data = {_DEVICE_ID: {}}
    coordinator.last_update_success = True
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    return JackeryQueryButton(
        coordinator,
        _DEVICE_ID,
        description=_description(key),
    )


def test_only_documented_refresh_buttons_enable_http_reads() -> None:
    """Non-equivalent Wi-Fi/weather/config queries do not invent HTTP paths."""
    expected = {
        "refresh_system_info",
        "refresh_device_info",
        "refresh_battery_packs",
        "refresh_smart_meter",
        "refresh_meter_heads",
        "refresh_smart_plugs",
        "refresh_subdevice_combo",
        "portable_refresh_device_info",
        "portable_refresh_battery_packs",
        "portable_refresh_sub_ct",
    }

    actual = {item.key for item in QUERY_BUTTON_DESCRIPTIONS if item.has_http_read}

    assert actual == expected


@pytest.mark.asyncio
async def test_button_succeeds_when_http_read_fills_failed_query_transport() -> None:
    """A usable HTTP read makes a failed BLE/cloud-MQTT query non-fatal."""
    coordinator = MagicMock()
    coordinator.async_query_device_info = AsyncMock(
        side_effect=HomeAssistantError("push unavailable"),
    )
    coordinator.async_refresh_documented_http_read = AsyncMock(return_value=True)
    button = _button(coordinator, "refresh_device_info")

    await button.async_press()

    coordinator.async_query_device_info.assert_awaited_once_with(_DEVICE_ID)
    coordinator.async_refresh_documented_http_read.assert_awaited_once()


@pytest.mark.asyncio
async def test_button_succeeds_when_query_works_and_http_read_fails() -> None:
    """The existing BLE/cloud-MQTT query remains independently sufficient."""
    coordinator = MagicMock()
    coordinator.async_query_device_info = AsyncMock(return_value=None)
    coordinator.async_refresh_documented_http_read = AsyncMock(
        side_effect=HomeAssistantError("HTTP unavailable"),
    )
    button = _button(coordinator, "refresh_device_info")

    await button.async_press()


@pytest.mark.asyncio
async def test_valid_http_noop_still_recovers_failed_query_transport() -> None:
    """A priority-suppressed valid HTTP response is still transport success."""
    plug_sn = "PLUG-1"
    live_plug = {FIELD_DEVICE_SN: plug_sn, "sysSwitch": 1}
    entry = {
        PAYLOAD_DEVICE: {FIELD_DEVICE_SN: _DEVICE_SN},
        PAYLOAD_PROPERTIES: {},
        PAYLOAD_SYSTEM: {
            FIELD_ID: "SYSTEM-1",
            FIELD_ACCESSORIES: [
                {
                    FIELD_DEVICE_SN: plug_sn,
                    FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET,
                },
            ],
        },
        PAYLOAD_SMART_PLUGS: [live_plug],
    }
    coordinator = _bare_coordinator(entry)
    coordinator.last_update_success = True
    api = _mock_api(coordinator)
    api.async_get_sub_shadow.return_value = {
        FIELD_PLUGS: [{FIELD_DEVICE_SN: plug_sn, "sysSwitch": 0}],
    }
    coordinator_mock = cast("Any", coordinator)
    coordinator_mock._async_publish_command_ble_first = AsyncMock(  # ruff: ignore[private-member-access]
        side_effect=HomeAssistantError("push unavailable"),
    )
    coordinator_mock._merge_subdevice_data = MagicMock(  # ruff: ignore[private-member-access]
        return_value=False,
    )
    coordinator_mock._push_partial_update = MagicMock()  # ruff: ignore[private-member-access]
    button = JackeryQueryButton(
        coordinator,
        _DEVICE_ID,
        description=_description("refresh_smart_plugs"),
    )

    await button.async_press()

    assert coordinator.data[_DEVICE_ID][PAYLOAD_SMART_PLUGS] == [live_plug]
    coordinator_mock._push_partial_update.assert_not_called()  # ruff: ignore[private-member-access]


@pytest.mark.asyncio
async def test_button_never_swallows_cancellation() -> None:
    """Task cancellation wins even if the other refresh transport succeeds."""
    coordinator = MagicMock()
    coordinator.async_query_device_info = AsyncMock(
        side_effect=asyncio.CancelledError,
    )
    coordinator.async_refresh_documented_http_read = AsyncMock(return_value=True)
    button = _button(coordinator, "refresh_device_info")

    with pytest.raises(asyncio.CancelledError):
        await button.async_press()


@pytest.mark.asyncio
async def test_device_property_http_read_preserves_fresh_live_value() -> None:
    """Raw HTTP is recorded without replaying a pre-await live snapshot."""
    entry = {
        PAYLOAD_DEVICE: {FIELD_DEVICE_SN: _DEVICE_SN},
        PAYLOAD_PROPERTIES: {"pvPw": _LIVE_PV_POWER},
        PAYLOAD_HTTP_PROPERTIES: {"pvPw": 100},
    }
    coordinator = _bare_coordinator(entry)
    api = _mock_api(coordinator)
    request_started = asyncio.Event()
    release_response = asyncio.Event()

    async def _delayed_property(_device_id: str) -> dict[str, Any]:
        request_started.set()
        await release_response.wait()
        return {
            PAYLOAD_DEVICE: {FIELD_DEVICE_SN: _DEVICE_SN},
            PAYLOAD_PROPERTIES: {"pvPw": _HTTP_PV_POWER},
        }

    api.async_get_device_property.side_effect = _delayed_property
    refresh = asyncio.create_task(
        coordinator.async_refresh_documented_http_read(
            _DEVICE_ID,
            device_property=True,
        )
    )
    await request_started.wait()
    coordinator.data[_DEVICE_ID] = {
        **entry,
        PAYLOAD_PROPERTIES: {"pvPw": _NEWER_LIVE_PV_POWER},
    }
    coordinator._property_source_state = {  # ruff: ignore[private-member-access]
        _DEVICE_ID: {
            "pvPw": FieldProvenance(
                source=TransportSource.LOCAL_MQTT,
                section=PAYLOAD_PROPERTIES,
                observed_at=None,
                received_at_monotonic=time.monotonic(),
            ),
        },
    }
    release_response.set()

    assert await refresh

    updated = coordinator.data[_DEVICE_ID]
    assert updated[PAYLOAD_HTTP_PROPERTIES]["pvPw"] == _HTTP_PV_POWER
    assert updated[PAYLOAD_PROPERTIES]["pvPw"] == _NEWER_LIVE_PV_POWER


@pytest.mark.asyncio
async def test_system_shadow_http_read_surfaces_system_info() -> None:
    """The system-info button can fill SystemBody-only fields without MQTT."""
    entry = {
        PAYLOAD_DEVICE: {FIELD_DEVICE_SN: _DEVICE_SN},
        PAYLOAD_PROPERTIES: {},
        PAYLOAD_SYSTEM: {FIELD_ID: "SYSTEM-1"},
    }
    coordinator = _bare_coordinator(entry)
    api = _mock_api(coordinator)
    api.async_get_system_shadow.return_value = {
        FIELD_ENERGY_PLAN_PW: _SYSTEM_ENERGY_PLAN_POWER,
    }

    assert await coordinator.async_refresh_documented_http_read(
        _DEVICE_ID,
        system_shadow=True,
    )

    api.async_get_system_shadow.assert_awaited_once_with(
        device_sn=_DEVICE_SN,
        diy_sn="SYSTEM-1",
    )
    assert (
        coordinator.data[_DEVICE_ID][PAYLOAD_PROPERTIES][FIELD_ENERGY_PLAN_PW]
        == _SYSTEM_ENERGY_PLAN_POWER
    )


@pytest.mark.asyncio
async def test_battery_pack_read_combines_list_and_type_one_shadow() -> None:
    """Pack refresh runs pack/list and known type-1 subShadow in parallel."""
    pack_sn = "PACK-1"
    entry = {
        PAYLOAD_DEVICE: {FIELD_DEVICE_SN: _DEVICE_SN},
        PAYLOAD_PROPERTIES: {},
        PAYLOAD_BATTERY_PACKS: [
            {
                FIELD_DEVICE_SN: pack_sn,
                FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_BATTERY_PACK,
                FIELD_BAT_SOC: _LIVE_PACK_SOC,
            },
        ],
    }
    coordinator = _bare_coordinator(entry)
    api = _mock_api(coordinator)
    coordinator._accessory_source_state = {  # ruff: ignore[private-member-access]
        (_DEVICE_ID, PAYLOAD_BATTERY_PACKS, pack_sn): {
            FIELD_BAT_SOC: FieldProvenance(
                source=TransportSource.BLE,
                section=PAYLOAD_BATTERY_PACKS,
                observed_at=None,
                received_at_monotonic=time.monotonic(),
            ),
        },
    }
    api.async_get_battery_pack_list.return_value = [
        {
            FIELD_DEVICE_SN: pack_sn,
            FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_BATTERY_PACK,
            FIELD_BAT_SOC: 20,
        },
    ]
    api.async_get_sub_shadow.return_value = {
        FIELD_BATTERY_PACKS: [
            {
                FIELD_DEVICE_SN: pack_sn,
                FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_BATTERY_PACK,
                FIELD_BAT_SOC: 21,
            },
        ],
    }

    assert await coordinator.async_refresh_documented_http_read(
        _DEVICE_ID,
        battery_packs=True,
    )

    api.async_get_battery_pack_list.assert_awaited_once_with(_DEVICE_SN)
    api.async_get_sub_shadow.assert_awaited_once_with(
        dev_type=str(SUBDEVICE_DEV_TYPE_BATTERY_PACK),
        device_sn=_DEVICE_SN,
        sub_device_sn=pack_sn,
    )
    assert (
        coordinator.data[_DEVICE_ID][PAYLOAD_BATTERY_PACKS][0][FIELD_BAT_SOC]
        == _LIVE_PACK_SOC
    )


@pytest.mark.parametrize(
    ["dev_type", "serial", "body", "bucket"],
    [
        [
            SUBDEVICE_DEV_TYPE_CT,
            "CT-1",
            {FIELD_DEVICE_SN: "CT-1", FIELD_CT_POWER: 111},
            PAYLOAD_CT_METER,
        ],
        [
            SUBDEVICE_DEV_TYPE_METER_HEAD,
            "HEAD-1",
            {FIELD_COLLECTORS: [{FIELD_DEVICE_SN: "HEAD-1", "commSta": 1}]},
            PAYLOAD_METER_HEADS,
        ],
        [
            SUBDEVICE_DEV_TYPE_SOCKET,
            "PLUG-1",
            {FIELD_PLUGS: [{FIELD_DEVICE_SN: "PLUG-1", "sysSwitch": 1}]},
            PAYLOAD_SMART_PLUGS,
        ],
        [
            SUBDEVICE_DEV_TYPE_COMBO,
            "COMBO-1",
            {
                FIELD_SUB_DEVICE: [
                    {
                        FIELD_DEVICE_SN: "COMBO-1",
                        FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_COMBO,
                    },
                ],
            },
            PAYLOAD_SUBDEVICES,
        ],
    ],
)
@pytest.mark.asyncio
async def test_known_subdevice_refresh_uses_documented_sub_shadow(
    dev_type: int,
    serial: str,
    body: dict[str, Any],
    bucket: str,
) -> None:
    """Known devTypes 2/3/4/6 use subShadow and feed their normal bucket."""
    entry = {
        PAYLOAD_DEVICE: {FIELD_DEVICE_SN: _DEVICE_SN},
        PAYLOAD_PROPERTIES: {},
        PAYLOAD_SYSTEM: {
            FIELD_ID: "SYSTEM-1",
            FIELD_ACCESSORIES: [
                {FIELD_DEVICE_SN: serial, FIELD_DEV_TYPE: dev_type},
            ],
        },
    }
    coordinator = _bare_coordinator(entry)
    api = _mock_api(coordinator)
    api.async_get_sub_shadow.return_value = body

    assert await coordinator.async_refresh_documented_http_read(
        _DEVICE_ID,
        subdevice_dev_type=dev_type,
    )

    api.async_get_sub_shadow.assert_awaited_once_with(
        dev_type=str(dev_type),
        device_sn=_DEVICE_SN,
        sub_device_sn=serial,
    )
    assert bucket in coordinator.data[_DEVICE_ID]
