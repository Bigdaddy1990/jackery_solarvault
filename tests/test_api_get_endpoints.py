"""Contract tests for simple Jackery API GET endpoint wrappers.

Each wrapper issues one GET with the documented params and unwraps the
response ``data`` envelope; these pin the path + params + unwrap shape.
"""

from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.jackery_solarvault.client.api import HttpProfile, JackeryApi
from custom_components.jackery_solarvault.const import (
    ALARM_PATH,
    DEVICE_PROPERTY_PATH,
    FIELD_DATA,
    FIELD_DEVICE_ID,
    FIELD_SYSTEM_ID,
    POWER_PRICE_PATH,
    PRICE_SOURCE_LIST_PATH,
    SYSTEM_STATISTIC_PATH,
)

_SYS = "sys-1"


def _api() -> JackeryApi:
    return JackeryApi(Mock(), "tester@example.com", "secret")


@pytest.mark.parametrize(
    ["method", "path"],
    [
        ["async_get_system_statistic", SYSTEM_STATISTIC_PATH],
        ["async_get_power_price", POWER_PRICE_PATH],
    ],
)
@pytest.mark.asyncio
async def test_system_id_dict_endpoints(method: str, path: str) -> None:
    """system_id GET endpoints unwrap the data dict with a systemId param."""
    api = _api()
    payload = {"k": 1}
    get_json = AsyncMock(return_value={FIELD_DATA: payload})

    with patch.object(api, "_get_json", get_json):
        result = await getattr(api, method)(_SYS)

    assert result == payload
    get_json.assert_awaited_once_with(path, params={FIELD_SYSTEM_ID: _SYS})


@pytest.mark.asyncio
async def test_get_alarm_returns_raw_data_field() -> None:
    """The alarm endpoint returns the raw data payload (list or dict)."""
    api = _api()
    alarms = [{"id": 1}]
    get_json = AsyncMock(return_value={FIELD_DATA: alarms})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_get_alarm(_SYS)

    assert result == alarms
    get_json.assert_awaited_once_with(ALARM_PATH, params={FIELD_SYSTEM_ID: _SYS})


@pytest.mark.asyncio
async def test_get_price_sources_unwraps_list() -> None:
    """The price-source endpoint unwraps a data list."""
    api = _api()
    sources = [{"cid": "a"}, {"cid": "b"}]
    get_json = AsyncMock(return_value={FIELD_DATA: sources})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_get_price_sources(_SYS)

    assert result == sources
    get_json.assert_awaited_once_with(
        PRICE_SOURCE_LIST_PATH, params={FIELD_SYSTEM_ID: _SYS}
    )


@pytest.mark.asyncio
async def test_get_device_property_uses_device_id_param() -> None:
    """The device-property GET keys on deviceId and unwraps the dict."""
    api = _api()
    props: dict[str, Any] = {"soc": 80}
    get_json = AsyncMock(return_value={FIELD_DATA: props})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_get_device_property(42)

    assert result == props
    get_json.assert_awaited_once_with(
        DEVICE_PROPERTY_PATH, params={FIELD_DEVICE_ID: "42"}, profile=HttpProfile.FAST
    )


@pytest.mark.asyncio
async def test_get_device_property_empty_when_no_data() -> None:
    """A missing data envelope yields an empty dict, not an error."""
    api = _api()
    get_json = AsyncMock(return_value={})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_get_device_property(42)

    assert result == {}
