"""Direct coordinator-level tests for the B4/B3/B6a HTTP write wrappers.

These exercise real ``JackerySolarVaultCoordinator`` methods with only the
Jackery ``api`` client mocked at its boundary (AsyncMock). The local
patch/merge path runs for real. Each test asserts the delegation contract (the
right client method with the right kwargs), that the optimistic patch lands
under the correct properties path, and that the patch never mutates the
pre-call ``self.data`` object graph in place.
"""

import copy
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.client.api import JackeryError
from custom_components.jackery_solarvault.const import (
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_SN,
    FIELD_LATITUDE,
    FIELD_LONGITUDE,
    FIELD_PV1,
    FIELD_PV_NAME,
    PAYLOAD_DEVICE,
    PAYLOAD_DEVICE_META,
    PAYLOAD_DISCOVERY,
    PAYLOAD_LOCATION,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SYSTEM,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

_DEVICE = "573702884982521856"
_OTHER = "111111111111111111"
_DEVICE_SN = "SN-DEVICE-1"


def _coordinator(
    data: dict[str, Any],
    device_index: dict[str, Any] | None = None,
) -> JackerySolarVaultCoordinator:
    """Build a bare coordinator with only the api boundary and refresh stubbed.

    The local-patch/merge helpers run for real; solely ``api`` and the HA
    refresh hook are mocked so the wrappers' branching is genuinely exercised.
    """
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    obj = cast("Any", coordinator)
    obj.data = data
    obj._device_index = device_index or {}
    obj._property_overrides = {}
    obj._listeners = {}
    obj._device_registry_observer = None
    obj._shutdown_started = False
    obj.last_update_success = True
    obj.last_update_exception = None
    obj.api = MagicMock()
    obj.async_request_refresh = AsyncMock()
    return coordinator


def _api(coordinator: JackerySolarVaultCoordinator) -> Any:
    return cast("Any", coordinator).api


# --- async_set_pv_name (Finding 1a/1b + Finding 2) ------------------------


@pytest.mark.asyncio
async def test_set_device_name_uses_diy_http_endpoint_and_patches_metadata() -> None:
    """The explicit App REST setter updates only the addressed device metadata."""
    data = {
        _DEVICE: {
            PAYLOAD_DEVICE: {FIELD_DEVICE_NAME: "Old"},
            PAYLOAD_DISCOVERY: {FIELD_DEVICE_NAME: "Old"},
            PAYLOAD_SYSTEM: {FIELD_DEVICE_NAME: "Old"},
        },
        _OTHER: {
            PAYLOAD_DEVICE: {FIELD_DEVICE_NAME: "Other"},
        },
    }
    coordinator = _coordinator(data)
    _api(coordinator).async_modify_device_name = AsyncMock()
    original = coordinator.data
    snapshot = copy.deepcopy(original)

    await coordinator.async_set_device_name(_DEVICE, "New Device")

    _api(coordinator).async_modify_device_name.assert_awaited_once_with(
        device_name="New Device",
        id=_DEVICE,
    )
    assert coordinator.data[_DEVICE][PAYLOAD_DEVICE][FIELD_DEVICE_NAME] == "New Device"
    assert (
        coordinator.data[_DEVICE][PAYLOAD_DISCOVERY][FIELD_DEVICE_NAME] == "New Device"
    )
    assert coordinator.data[_DEVICE][PAYLOAD_SYSTEM][FIELD_DEVICE_NAME] == "New Device"
    assert coordinator.data[_OTHER][PAYLOAD_DEVICE][FIELD_DEVICE_NAME] == "Other"
    assert original == snapshot
    cast("Any", coordinator).async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_pv_name_resolves_device_sn_and_patches_channel() -> None:
    """Channel 0 resolves the device's SN, forwards it, and patches pv1.name."""
    data = {
        _DEVICE: {
            PAYLOAD_PROPERTIES: {FIELD_PV1: {FIELD_PV_NAME: "Old", "pvPw": 100}},
        },
    }
    device_index = {_DEVICE: {PAYLOAD_DEVICE_META: {FIELD_DEVICE_SN: _DEVICE_SN}}}
    coordinator = _coordinator(data, device_index)
    _api(coordinator).async_modify_pv_name = AsyncMock()
    original = coordinator.data
    snapshot = copy.deepcopy(original)

    await coordinator.async_set_pv_name(device_id=_DEVICE, index=0, name="Roof East")

    _api(coordinator).async_modify_pv_name.assert_awaited_once_with(
        device_sn=_DEVICE_SN,
        index=0,
        name="Roof East",
    )
    patched = coordinator.data[_DEVICE][PAYLOAD_PROPERTIES][FIELD_PV1][FIELD_PV_NAME]
    assert patched == "Roof East"
    # The pre-call data object graph was not mutated in place.
    assert original == snapshot
    assert original[_DEVICE][PAYLOAD_PROPERTIES][FIELD_PV1][FIELD_PV_NAME] == "Old"
    cast("Any", coordinator).async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_set_pv_name_targets_addressed_device_on_multi_device_account() -> None:
    """The addressed device is renamed, not the first channel-bearing match."""
    data = {
        _OTHER: {PAYLOAD_PROPERTIES: {FIELD_PV1: {FIELD_PV_NAME: "Other"}}},
        _DEVICE: {PAYLOAD_PROPERTIES: {FIELD_PV1: {FIELD_PV_NAME: "Mine"}}},
    }
    device_index = {
        _OTHER: {PAYLOAD_DEVICE_META: {FIELD_DEVICE_SN: "SN-OTHER"}},
        _DEVICE: {PAYLOAD_DEVICE_META: {FIELD_DEVICE_SN: _DEVICE_SN}},
    }
    coordinator = _coordinator(data, device_index)
    _api(coordinator).async_modify_pv_name = AsyncMock()

    await coordinator.async_set_pv_name(device_id=_DEVICE, index=0, name="Renamed")

    _api(coordinator).async_modify_pv_name.assert_awaited_once_with(
        device_sn=_DEVICE_SN,
        index=0,
        name="Renamed",
    )
    props = coordinator.data
    assert props[_DEVICE][PAYLOAD_PROPERTIES][FIELD_PV1][FIELD_PV_NAME] == "Renamed"
    assert props[_OTHER][PAYLOAD_PROPERTIES][FIELD_PV1][FIELD_PV_NAME] == "Other"


@pytest.mark.asyncio
async def test_set_pv_name_without_device_sn_raises_and_skips_api() -> None:
    """A device with no resolvable SN raises before any API write."""
    data = {_DEVICE: {PAYLOAD_PROPERTIES: {FIELD_PV1: {FIELD_PV_NAME: "Old"}}}}
    coordinator = _coordinator(data)
    _api(coordinator).async_modify_pv_name = AsyncMock()

    with pytest.raises(JackeryError):
        await coordinator.async_set_pv_name(device_id=_DEVICE, index=0, name="Roof")

    _api(coordinator).async_modify_pv_name.assert_not_awaited()
    cast("Any", coordinator).async_request_refresh.assert_not_awaited()


# --- async_update_storm_alert_location (Finding 1c) -----------------------


@pytest.mark.asyncio
async def test_update_storm_alert_location_forwards_and_patches_fresh_copy() -> None:
    """Coordinates forward to the client and land in a fresh location block."""
    data = {_DEVICE: {PAYLOAD_LOCATION: {FIELD_LATITUDE: 1.0, FIELD_LONGITUDE: 2.0}}}
    coordinator = _coordinator(data)
    _api(coordinator).async_update_location = AsyncMock()
    original = coordinator.data
    snapshot = copy.deepcopy(original)

    await coordinator.async_update_storm_alert_location(
        device_id=_DEVICE,
        latitude=52.5,
        longitude=13.4,
    )

    _api(coordinator).async_update_location.assert_awaited_once_with(
        device_id=_DEVICE,
        latitude=52.5,
        longitude=13.4,
    )
    location = coordinator.data[_DEVICE][PAYLOAD_LOCATION]
    assert location[FIELD_LATITUDE] == pytest.approx(52.5)
    assert location[FIELD_LONGITUDE] == pytest.approx(13.4)
    # Fresh copy — the pre-call location block is untouched.
    assert original == snapshot
    assert original[_DEVICE][PAYLOAD_LOCATION][FIELD_LATITUDE] == pytest.approx(1.0)
    cast("Any", coordinator).async_request_refresh.assert_awaited_once()


# --- async_update_user_info (Finding 1d) ----------------------------------


@pytest.mark.asyncio
async def test_update_user_info_forwards_nick_name_without_local_patch() -> None:
    """The nickname forwards to the client and applies no coordinator patch."""
    data = {_DEVICE: {PAYLOAD_PROPERTIES: {"batSoc": 55}}}
    coordinator = _coordinator(data)
    _api(coordinator).async_update_user_info = AsyncMock()
    before = copy.deepcopy(coordinator.data)

    await coordinator.async_update_user_info("Nighthawk")

    _api(coordinator).async_update_user_info.assert_awaited_once_with(
        nick_name="Nighthawk",
    )
    # Account-scoped: no local coordinator data mutation, no refresh.
    assert coordinator.data == before
    cast("Any", coordinator).async_request_refresh.assert_not_awaited()
