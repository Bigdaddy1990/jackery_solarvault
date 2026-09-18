"""Contract tests for share-admin / system-bind / bluetooth-key API wrappers."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.const import (
    DEVICE_BLUETOOTH_KEY_PATH,
    DEVICE_SHARED_REMOVE_ALL_PATH,
    DEVICE_SHARED_REMOVE_PATH,
    FIELD_DATA,
    SYSTEM_CREATE_PATH,
    SYSTEM_EXIST_PATH,
)

_LEVEL = 2


def _api() -> JackeryApi:
    return JackeryApi(Mock(), "tester@example.com", "secret")


@pytest.mark.asyncio()
async def test_remove_shared_access_posts_user_and_device() -> None:
    """Removing one share posts bindUserId + stringified deviceId."""
    api = _api()
    post = AsyncMock(return_value={"code": 0})

    with patch.object(api, "_post_form", post):
        await api.async_remove_shared_access(bind_user_id="u-1", device_id=9)

    post.assert_awaited_once_with(
        DEVICE_SHARED_REMOVE_PATH, {"bindUserId": "u-1", "deviceId": "9"}
    )


@pytest.mark.asyncio()
async def test_remove_all_shared_access_posts_level() -> None:
    """Removing all shares posts bindUserId + level."""
    api = _api()
    post = AsyncMock(return_value={"code": 0})

    with patch.object(api, "_post_form", post):
        await api.async_remove_all_shared_access(bind_user_id="u-1", level=_LEVEL)

    post.assert_awaited_once_with(
        DEVICE_SHARED_REMOVE_ALL_PATH, {"bindUserId": "u-1", "level": _LEVEL}
    )


@pytest.mark.asyncio()
async def test_check_system_bound_gets_with_params() -> None:
    """The system-bound check GETs with bindKey/deviceSn/guid params."""
    api = _api()
    resp = {"code": 0, "data": {"bound": True}}
    get_json = AsyncMock(return_value=resp)

    with patch.object(api, "_get_json", get_json):
        result = await api.async_check_system_bound(
            bind_key="bk", device_sn="SN", guid="g"
        )

    assert result is resp
    get_json.assert_awaited_once_with(
        SYSTEM_EXIST_PATH,
        params={"bindKey": "bk", "deviceSn": "SN", "guid": "g"},
    )


@pytest.mark.asyncio()
async def test_get_bluetooth_key_unwraps_dict() -> None:
    """The bluetooth-key endpoint GETs with deviceSn/guid and unwraps data."""
    api = _api()
    payload = {"bluetoothKey": "abc", "region": "eu"}
    get_json = AsyncMock(return_value={FIELD_DATA: payload})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_get_device_bluetooth_key(device_sn="SN", guid="g")

    assert result == payload
    get_json.assert_awaited_once_with(
        DEVICE_BLUETOOTH_KEY_PATH, {"deviceSn": "SN", "guid": "g"}
    )


@pytest.mark.asyncio()
async def test_create_system_forwards_kwargs_body() -> None:
    """System creation forwards its keyword arguments as the POST body."""
    api = _api()
    post = AsyncMock(return_value={"code": 0})

    with patch.object(api, "_post_form", post):
        await api.async_create_system(systemName="Home", countryCode="DE")

    post.assert_awaited_once_with(
        SYSTEM_CREATE_PATH, {"systemName": "Home", "countryCode": "DE"}
    )
