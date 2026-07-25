"""Contract tests for the today-energy / portable-CT / socket / OTA wrappers."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.const import (
    BLE_OTA_VERSIONS_PATH,
    DEVICE_PORTABLE_CT_STAT_PATH,
    DEVICE_SOCKET_STATISTIC_PATH,
    DEVICE_TODAY_ENERGY_PATH,
    FIELD_DATA,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_LIST,
    FIELD_SMART_SOCKET_ID,
)


def _api() -> JackeryApi:
    return JackeryApi(Mock(), "tester@example.com", "secret")


@pytest.mark.asyncio()
async def test_today_energy_gets_by_device_sn() -> None:
    """Today-energy GETs by deviceSn and unwraps the data dict."""
    api = _api()
    payload = {"de": "1", "dg": "2"}
    get_json = AsyncMock(return_value={FIELD_DATA: payload})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_get_today_energy("SN-9")

    assert result == payload
    get_json.assert_awaited_once_with(
        DEVICE_TODAY_ENERGY_PATH, params={FIELD_DEVICE_SN: "SN-9"}
    )


@pytest.mark.asyncio()
async def test_portable_ct_stat_gets_by_device_id() -> None:
    """Portable CT stat GETs by deviceId and unwraps the data dict."""
    api = _api()
    payload = {"total": 3}
    get_json = AsyncMock(return_value={FIELD_DATA: payload})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_get_portable_ct_stat(11)

    assert result == payload
    get_json.assert_awaited_once_with(
        DEVICE_PORTABLE_CT_STAT_PATH, params={FIELD_DEVICE_ID: "11"}
    )


@pytest.mark.asyncio()
async def test_socket_statistic_gets_by_socket_id() -> None:
    """Socket panel statistic GETs by smartSocketId and unwraps the data dict."""
    api = _api()
    payload = {"panel": 1}
    get_json = AsyncMock(return_value={FIELD_DATA: payload})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_get_device_socket_statistic(77)

    assert result == payload
    get_json.assert_awaited_once_with(
        DEVICE_SOCKET_STATISTIC_PATH, params={FIELD_SMART_SOCKET_ID: "77"}
    )


@pytest.mark.asyncio()
async def test_ble_ota_versions_posts_version_list() -> None:
    """BLE OTA versions POSTs the version list under the list field."""
    api = _api()
    resp = {"code": 0}
    post = AsyncMock(return_value=resp)

    with patch.object(api, "_post_json", post):
        result = await api.async_get_ble_ota_versions("v1,v2")

    assert result is resp
    post.assert_awaited_once_with(BLE_OTA_VERSIONS_PATH, {FIELD_LIST: "v1,v2"})
