"""Contract tests for the device pairing / sharing / QR API wrappers."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.const import (
    DEVICE_ACCEPT_BIND_PATH,
    DEVICE_BIND_PATH,
    DEVICE_NICKNAME_PATH,
    DEVICE_QR_CODE_PATH,
    DEVICE_SHARED_LIST_PATH,
    DEVICE_UNBIND_PATH,
    FIELD_DATA,
)

_TZ = 60
_BIND_KEY = 7


def _api() -> JackeryApi:
    return JackeryApi(Mock(), "tester@example.com", "secret")


@pytest.mark.asyncio()
async def test_bind_device_posts_full_body() -> None:
    """Binding posts bindKey/devId/guid/timezoneOffset and returns the response."""
    api = _api()
    resp = {"code": 0}
    post = AsyncMock(return_value=resp)

    with patch.object(api, "_post_form", post):
        result = await api.async_bind_device(
            bind_key=_BIND_KEY, dev_id="d-1", guid="g-1", timezone_offset=_TZ
        )

    assert result is resp
    post.assert_awaited_once_with(
        DEVICE_BIND_PATH,
        {"bindKey": _BIND_KEY, "devId": "d-1", "guid": "g-1", "timezoneOffset": _TZ},
    )


@pytest.mark.asyncio()
async def test_unbind_device_posts_device_id() -> None:
    """Unbinding posts the stringified deviceId."""
    api = _api()
    post = AsyncMock(return_value={"code": 0})

    with patch.object(api, "_post_form", post):
        await api.async_unbind_device(42)

    post.assert_awaited_once_with(DEVICE_UNBIND_PATH, {"deviceId": "42"})


@pytest.mark.asyncio()
async def test_set_device_nickname_posts_name() -> None:
    """Setting a nickname posts deviceId + nickname."""
    api = _api()
    post = AsyncMock(return_value={"code": 0})

    with patch.object(api, "_post_form", post):
        await api.async_set_device_nickname(42, "Garage")

    post.assert_awaited_once_with(
        DEVICE_NICKNAME_PATH, {"deviceId": "42", "nickname": "Garage"}
    )


@pytest.mark.asyncio()
async def test_accept_shared_device_posts_ids() -> None:
    """Accepting a share posts devId + qrCodeId."""
    api = _api()
    post = AsyncMock(return_value={"code": 0})

    with patch.object(api, "_post_form", post):
        await api.async_accept_shared_device(dev_id="d-1", qr_code_id="q-1")

    post.assert_awaited_once_with(
        DEVICE_ACCEPT_BIND_PATH, {"devId": "d-1", "qrCodeId": "q-1"}
    )


@pytest.mark.asyncio()
async def test_get_device_shared_list_unwraps_list() -> None:
    """The shared-device list endpoint unwraps a data list."""
    api = _api()
    shared = [{"deviceId": 1}]
    get_json = AsyncMock(return_value={FIELD_DATA: shared})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_get_device_shared_list()

    assert result == shared
    get_json.assert_awaited_once_with(DEVICE_SHARED_LIST_PATH)


@pytest.mark.asyncio()
async def test_get_qr_code_unwraps_dict() -> None:
    """The share-QR endpoint unwraps the data dict."""
    api = _api()
    qr = {"qrCodeId": "q", "userId": "u"}
    get_json = AsyncMock(return_value={FIELD_DATA: qr})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_get_qr_code()

    assert result == qr
    get_json.assert_awaited_once_with(DEVICE_QR_CODE_PATH)
