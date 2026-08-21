"""Contract tests for the device period-statistics API helper and wrappers."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.const import (
    APP_REQUEST_BEGIN_DATE,
    APP_REQUEST_DATE_TYPE,
    APP_REQUEST_END_DATE,
    APP_REQUEST_META,
    APP_REQUEST_STAT_TYPE,
    CT_STAT_TYPE_L1,
    DATE_TYPE_DAY,
    DEVICE_CT_STAT_PATH,
    DEVICE_EPS_STAT_PATH,
    DEVICE_PV_STAT_PATH,
    DEVICE_STATISTIC_PATH,
    FIELD_DATA,
    FIELD_DEVICE_ID,
    FIELD_SYSTEM_ID,
    PV_TRENDS_PATH,
)

_BEGIN = "2024-05-01"
_END = "2024-05-01"
_DEV = 7
_SYS = 3


def _api() -> JackeryApi:
    return JackeryApi(Mock(), "tester@example.com", "secret")


@pytest.mark.asyncio
async def test_device_statistic_unwraps_dict() -> None:
    """Current-day device statistic unwraps the data dict with deviceId."""
    api = _api()
    payload = {"pvEgy": "1.2"}
    get_json = AsyncMock(return_value={FIELD_DATA: payload})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_get_device_statistic(5)

    assert result == payload
    get_json.assert_awaited_once_with(
        DEVICE_STATISTIC_PATH, params={FIELD_DEVICE_ID: "5"}
    )


@pytest.mark.asyncio
async def test_period_stat_builds_params_and_meta() -> None:
    """The period-stat helper sends the full range and annotates request meta."""
    api = _api()
    series = {"x": ["00:00"], "y": [1.0]}
    get_json = AsyncMock(return_value={FIELD_DATA: dict(series)})

    with patch.object(api, "_get_json", get_json):
        result = await api._async_get_device_period_stat(
            DEVICE_PV_STAT_PATH,
            device_id=7,
            date_type=DATE_TYPE_DAY,
            begin_date=_BEGIN,
            end_date=_END,
        )

    get_json.assert_awaited_once_with(
        DEVICE_PV_STAT_PATH,
        params={
            FIELD_DEVICE_ID: "7",
            APP_REQUEST_DATE_TYPE: DATE_TYPE_DAY,
            APP_REQUEST_BEGIN_DATE: _BEGIN,
            APP_REQUEST_END_DATE: _END,
        },
    )
    # Request meta is annotated and excludes the deviceId.
    meta = result[APP_REQUEST_META]
    assert meta[APP_REQUEST_DATE_TYPE] == DATE_TYPE_DAY
    assert FIELD_DEVICE_ID not in meta
    assert result["y"] == [1.0]


@pytest.mark.asyncio
async def test_period_stat_includes_system_id_when_given() -> None:
    """A system_id is added to the query params when provided."""
    api = _api()
    get_json = AsyncMock(return_value={FIELD_DATA: {}})

    with patch.object(api, "_get_json", get_json):
        await api._async_get_device_period_stat(
            DEVICE_PV_STAT_PATH,
            device_id=7,
            system_id=3,
            date_type=DATE_TYPE_DAY,
            begin_date=_BEGIN,
            end_date=_END,
        )

    awaited = get_json.await_args
    assert awaited is not None
    params = awaited.kwargs["params"]
    assert params[FIELD_SYSTEM_ID] == "3"


@pytest.mark.asyncio
async def test_ct_stat_uses_app_first_chart_type() -> None:
    """The CT-stat request mirrors App 2.4.x ``CtStatApi.type`` selection."""
    api = _api()
    get_json = AsyncMock(return_value={FIELD_DATA: {"totalInCtEnergy": "1.5"}})

    with patch.object(api, "_get_json", get_json):
        await api.async_get_device_ct_stat(11, date_type=DATE_TYPE_DAY)

    awaited = get_json.await_args
    assert awaited is not None
    args, kwargs = awaited
    assert args[0] == DEVICE_CT_STAT_PATH
    assert kwargs["params"][APP_REQUEST_STAT_TYPE] == str(CT_STAT_TYPE_L1)


@pytest.mark.asyncio
async def test_eps_stat_omits_ct_only_type_parameter() -> None:
    """The App EpsStatApi contract has no ``type`` request field."""
    api = _api()
    get_json = AsyncMock(return_value={FIELD_DATA: {"totalInEpsEnergy": "1.5"}})

    with patch.object(api, "_get_json", get_json):
        await api.async_get_device_eps_stat(11, date_type=DATE_TYPE_DAY)

    awaited = get_json.await_args
    assert awaited is not None
    args, kwargs = awaited
    assert args[0] == DEVICE_EPS_STAT_PATH
    assert APP_REQUEST_STAT_TYPE not in kwargs["params"]


@pytest.mark.asyncio
async def test_pv_stat_wrapper_delegates_with_system_id() -> None:
    """The PV wrapper forwards device_id and system_id to the shared helper."""
    api = _api()
    delegate = AsyncMock(return_value={})

    with patch.object(api, "_async_get_device_period_stat", delegate):
        await api.async_get_device_pv_stat(_DEV, _SYS, date_type=DATE_TYPE_DAY)

    awaited = delegate.await_args
    assert awaited is not None
    kwargs = awaited.kwargs
    assert kwargs["device_id"] == _DEV
    assert kwargs["system_id"] == _SYS
    assert awaited.args[0] == DEVICE_PV_STAT_PATH


@pytest.mark.asyncio
async def test_system_pv_trends_uses_current_app_path() -> None:
    """The current App 2.4.0 system-PV endpoint is the primary request."""
    api = _api()
    get_json = AsyncMock(return_value={FIELD_DATA: {"x": [], "y": []}})

    with patch.object(api, "_get_json", get_json):
        await api.async_get_pv_trends(
            _SYS,
            date_type=DATE_TYPE_DAY,
            begin_date=_BEGIN,
            end_date=_END,
        )

    awaited = get_json.await_args
    assert awaited is not None
    assert awaited.args[0] == PV_TRENDS_PATH
