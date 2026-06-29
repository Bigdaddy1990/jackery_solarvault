"""HTTP accessory enumeration discovery-source unit tests (Slice G-sub-1a).

These tests verify that ``async_discover`` consumes ``async_get_accessories_list``
as the HTTP-primary source for the ``accessories`` metadata read by the subdevice
presence predicates, so subdevices are discovered over HTTP even when MQTT never
connects. Enumeration must be idempotent (no duplicate ``deviceSn`` entries), run
only on the discovery cadence (never on the hot ``_async_update_data`` path), and
be fully best-effort: every cloud failure — including an authentication failure —
must be swallowed so it cannot break discovery. Reauthentication is owned by the
primary ``system/list`` block, not by enumeration.
"""

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
    PAYLOAD_SYSTEM_META,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from homeassistant.exceptions import ConfigEntryAuthFailed

DEVICE_ID = "dev-1"
PARENT_SN = "SN-PARENT"
SOCKET_SN = "SN-SOCKET"
METER_HEAD_SN = "SN-METER"
DEV_TYPE_SOCKET = 6
DEV_TYPE_METER_HEAD = 4


def _system_list_without_accessories() -> list[dict[str, Any]]:
    """Return a system/list response whose device array has no accessories."""
    return [
        {
            "id": "sys-1",
            "devices": [
                {
                    "deviceId": DEVICE_ID,
                    "deviceSn": PARENT_SN,
                    "modelCode": "SV-2000",
                    "devModel": "SolarVault",
                    "bindKey": 1,
                },
            ],
        },
    ]


def _http_accessories() -> list[dict[str, Any]]:
    """Return an accessories/list response with a socket and a meter head."""
    return [
        {
            "deviceSn": SOCKET_SN,
            "devType": DEV_TYPE_SOCKET,
            "scanName": "Smart Plug",
            "deviceName": "Patio Plug",
        },
        {
            "deviceSn": METER_HEAD_SN,
            "devType": DEV_TYPE_METER_HEAD,
            "scanName": "Meter Head",
            "deviceName": "Main CT",
        },
    ]


def _coordinator_with_api(
    accessories: list[dict[str, Any]],
    *,
    get_side_effect: BaseException | None = None,
) -> JackerySolarVaultCoordinator:
    """Build a lightweight coordinator wired to mocked discovery API methods."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._device_index = {}  # noqa: SLF001
    coordinator._last_discovery_refresh_monotonic = 0.0  # noqa: SLF001
    coordinator._slow_metrics_interval_sec = 600.0  # noqa: SLF001
    coordinator._async_save_discovery_cache = AsyncMock()  # noqa: SLF001

    api = MagicMock()
    api.async_get_system_list = AsyncMock(
        return_value=_system_list_without_accessories()
    )
    api.async_sync_smart_accessories = AsyncMock(return_value={})
    if get_side_effect is not None:
        api.async_get_accessories_list = AsyncMock(side_effect=get_side_effect)
    else:
        api.async_get_accessories_list = AsyncMock(return_value=accessories)
    coordinator.api = api
    return coordinator


def _system_meta(coordinator: JackerySolarVaultCoordinator) -> dict[str, Any]:
    """Return the discovered system_meta dict for the single test device."""
    return coordinator._device_index[DEVICE_ID][PAYLOAD_SYSTEM_META]  # noqa: SLF001


@pytest.mark.asyncio()
async def test_http_enumeration_populates_accessories_for_predicates() -> None:
    """Discovery overlays HTTP accessories so presence predicates return True."""
    coordinator = _coordinator_with_api(_http_accessories())

    await coordinator.async_discover()

    system_meta = _system_meta(coordinator)
    accessories = system_meta[FIELD_ACCESSORIES]
    sns = {item[FIELD_DEVICE_SN] for item in accessories}
    assert {SOCKET_SN, METER_HEAD_SN} <= sns

    payload = {PAYLOAD_SYSTEM_META: system_meta}
    assert coordinator._has_smart_plug_accessory(payload) is True  # noqa: SLF001
    assert coordinator._has_meter_head_accessory(payload) is True  # noqa: SLF001


@pytest.mark.asyncio()
async def test_http_enumeration_is_idempotent_for_duplicate_sn() -> None:
    """A deviceSn present in both system/list and accessories/list collapses to one."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._device_index = {}  # noqa: SLF001
    coordinator._last_discovery_refresh_monotonic = 0.0  # noqa: SLF001
    coordinator._slow_metrics_interval_sec = 600.0  # noqa: SLF001
    coordinator._async_save_discovery_cache = AsyncMock()  # noqa: SLF001

    systems = [
        {
            "id": "sys-1",
            "devices": [
                {
                    "deviceId": DEVICE_ID,
                    "deviceSn": PARENT_SN,
                    "modelCode": "SV-2000",
                    "devModel": "SolarVault",
                    "bindKey": 1,
                },
                {
                    "deviceSn": SOCKET_SN,
                    "devType": DEV_TYPE_SOCKET,
                    "scanName": "system-list socket",
                },
            ],
        },
    ]
    api = MagicMock()
    api.async_get_system_list = AsyncMock(return_value=systems)
    api.async_sync_smart_accessories = AsyncMock(return_value={})
    api.async_get_accessories_list = AsyncMock(
        return_value=[
            {
                "deviceSn": SOCKET_SN,
                "devType": DEV_TYPE_SOCKET,
                "scanName": "accessories-list socket",
                "deviceName": "Renamed Plug",
            },
        ]
    )
    coordinator.api = api

    await coordinator.async_discover()

    accessories = _system_meta(coordinator)[FIELD_ACCESSORIES]
    matching = [a for a in accessories if a.get(FIELD_DEVICE_SN) == SOCKET_SN]
    assert len(matching) == 1
    # The HTTP overlay merged onto the existing system/list entry.
    assert matching[0]["scanName"] == "accessories-list socket"
    assert matching[0]["deviceName"] == "Renamed Plug"


@pytest.mark.asyncio()
async def test_enumeration_gated_to_discovery_cadence_not_hot_path() -> None:
    """Enumeration runs on the discovery cadence but never off-cadence.

    ``_async_refresh_discovery_if_due`` is the only scheduled re-entry to discovery
    that the hot poll cycle reaches; within the slow-metrics interval it must
    early-return without awaiting enumeration. A direct ``async_discover`` (the
    discovery cadence) must await enumeration.
    """
    coordinator = _coordinator_with_api(_http_accessories())
    # Interval not elapsed: refresh-if-due must early-return, no enumeration.
    coordinator._slow_metrics_interval_sec = 10_000_000.0  # noqa: SLF001
    coordinator._last_discovery_refresh_monotonic = time.monotonic()  # noqa: SLF001

    await coordinator._async_refresh_discovery_if_due()  # noqa: SLF001

    coordinator.api.async_get_accessories_list.assert_not_awaited()

    # Discovery cadence (direct discover) does enumerate.
    await coordinator.async_discover()
    coordinator.api.async_get_accessories_list.assert_awaited()


@pytest.mark.asyncio()
async def test_enumeration_is_best_effort_on_cloud_failure() -> None:
    """A JackeryError from enumeration must not break discovery."""
    coordinator = _coordinator_with_api(
        [], get_side_effect=JackeryError("accessories/list code=10500")
    )

    await coordinator.async_discover()

    # Discovery still completed: the parent device is indexed.
    assert DEVICE_ID in coordinator._device_index  # noqa: SLF001


@pytest.mark.asyncio()
async def test_enumeration_auth_failure_is_swallowed_discovery_completes() -> None:
    """A JackeryAuthError from enumeration must not break discovery.

    Enumeration runs after the primary ``system/list`` block, which owns reauth.
    A mid-discovery token expiry on the enumeration calls is best-effort: it is
    swallowed (auth is a ``JackeryError`` subclass) and the primary path catches
    the expiry on the next cycle. Discovery itself must still complete.
    """
    coordinator = _coordinator_with_api(
        [], get_side_effect=JackeryAuthError("token rejected")
    )

    await coordinator.async_discover()

    # Discovery still completed: the parent device is indexed.
    assert DEVICE_ID in coordinator._device_index  # noqa: SLF001


@pytest.mark.asyncio()
async def test_system_list_auth_failure_still_raises_config_entry_auth_failed() -> None:
    """The primary system/list block must still convert auth to ConfigEntryAuthFailed.

    The enumeration change must not weaken the existing reauth trigger owned by the
    primary discovery path.
    """
    coordinator = _coordinator_with_api(_http_accessories())
    coordinator.api.async_get_system_list = AsyncMock(
        side_effect=JackeryAuthError("token rejected at system/list")
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator.async_discover()

    # Enumeration never runs when system/list rejects auth.
    coordinator.api.async_get_accessories_list.assert_not_awaited()


@pytest.mark.asyncio()
async def test_overlay_does_not_blank_devtype_with_http_null() -> None:
    """An HTTP null devType must not blank a populated system/list devType.

    Blanking would make the predicate compare ``str(None) == "6"`` (False) and
    silently defeat smart-plug detection.
    """
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._device_index = {}  # noqa: SLF001
    coordinator._last_discovery_refresh_monotonic = 0.0  # noqa: SLF001
    coordinator._slow_metrics_interval_sec = 600.0  # noqa: SLF001
    coordinator._async_save_discovery_cache = AsyncMock()  # noqa: SLF001

    systems = [
        {
            "id": "sys-1",
            "devices": [
                {
                    "deviceId": DEVICE_ID,
                    "deviceSn": PARENT_SN,
                    "modelCode": "SV-2000",
                    "devModel": "SolarVault",
                    "bindKey": 1,
                },
                {
                    "deviceSn": SOCKET_SN,
                    "devType": DEV_TYPE_SOCKET,
                    "scanName": "system-list socket",
                },
            ],
        },
    ]
    api = MagicMock()
    api.async_get_system_list = AsyncMock(return_value=systems)
    api.async_sync_smart_accessories = AsyncMock(return_value={})
    # HTTP returns the same SN but with devType explicitly None.
    api.async_get_accessories_list = AsyncMock(
        return_value=[
            {
                "deviceSn": SOCKET_SN,
                "devType": None,
                "deviceName": "Renamed Plug",
            },
        ]
    )
    coordinator.api = api

    await coordinator.async_discover()

    system_meta = _system_meta(coordinator)
    accessories = system_meta[FIELD_ACCESSORIES]
    matching = [a for a in accessories if a.get(FIELD_DEVICE_SN) == SOCKET_SN]
    assert len(matching) == 1
    # devType from system/list survives; the HTTP None did not blank it.
    assert str(matching[0][FIELD_DEV_TYPE]) == str(DEV_TYPE_SOCKET)
    # The non-null HTTP field still merged.
    assert matching[0]["deviceName"] == "Renamed Plug"
    # Detection still works.
    payload = {PAYLOAD_SYSTEM_META: system_meta}
    assert coordinator._has_smart_plug_accessory(payload) is True  # noqa: SLF001


@pytest.mark.asyncio()
async def test_shared_system_meta_multi_device_has_no_duplicate_sn() -> None:
    """Two devices sharing one system_meta must not duplicate a system-wide SN.

    ``async_discover`` assigns the same ``system_meta`` dict by reference to both
    device records in a system. Enumeration overlays per-device accessories/list
    onto that shared dict, so a SN returned for both devices must merge in place,
    not append twice.
    """
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._device_index = {}  # noqa: SLF001
    coordinator._last_discovery_refresh_monotonic = 0.0  # noqa: SLF001
    coordinator._slow_metrics_interval_sec = 600.0  # noqa: SLF001
    coordinator._async_save_discovery_cache = AsyncMock()  # noqa: SLF001

    second_device_id = "dev-2"
    systems = [
        {
            "id": "sys-1",
            "devices": [
                {
                    "deviceId": DEVICE_ID,
                    "deviceSn": PARENT_SN,
                    "modelCode": "SV-2000",
                    "devModel": "SolarVault",
                    "bindKey": 1,
                },
                {
                    "deviceId": second_device_id,
                    "deviceSn": "SN-PARENT-2",
                    "modelCode": "SV-2000",
                    "devModel": "SolarVault",
                    "bindKey": 1,
                },
            ],
        },
    ]
    # Both devices' enumeration returns the same system-wide socket SN.
    shared_socket = {
        "deviceSn": SOCKET_SN,
        "devType": DEV_TYPE_SOCKET,
        "scanName": "shared socket",
    }
    api = MagicMock()
    api.async_get_system_list = AsyncMock(return_value=systems)
    api.async_sync_smart_accessories = AsyncMock(return_value={})
    api.async_get_accessories_list = AsyncMock(return_value=[dict(shared_socket)])
    coordinator.api = api

    await coordinator.async_discover()

    record_one = coordinator._device_index[DEVICE_ID]  # noqa: SLF001
    record_two = coordinator._device_index[second_device_id]  # noqa: SLF001
    # The records share the same system_meta object by reference.
    assert record_one[PAYLOAD_SYSTEM_META] is record_two[PAYLOAD_SYSTEM_META]
    accessories = record_one[PAYLOAD_SYSTEM_META][FIELD_ACCESSORIES]
    matching = [a for a in accessories if a.get(FIELD_DEVICE_SN) == SOCKET_SN]
    assert len(matching) == 1
