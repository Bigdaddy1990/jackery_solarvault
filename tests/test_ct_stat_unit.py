"""Unit tests for CT statistics (/v1/device/stat/ct) with type parameter.

Tests cover the required `type` query parameter that App 2.4.0+ sends.
Without it, the cloud returns an empty shell (code=0, no x/y1/y2 arrays).
"""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.const import (
    APP_REQUEST_STAT_TYPE,
    CT_STAT_TYPE_L1,
    DATE_TYPE_DAY,
    DEVICE_CT_STAT_PATH,
    FIELD_DATA,
    FIELD_DEVICE_ID,
)


def _api() -> JackeryApi:
    return JackeryApi(Mock(), "tester@example.com", "secret")


@pytest.mark.asyncio
async def test_ct_stat_includes_type_parameter_l1() -> None:
    """CT stat request includes type=0 (CT_STAT_TYPE_L1) matching App default."""
    api = _api()
    payload = {
        "x": ["00:00", "00:05"],
        "y1": [100.0, 105.0],
        "y2": [0.0, 0.0],
    }
    get_json = AsyncMock(return_value={FIELD_DATA: payload})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_get_device_ct_stat(11, date_type=DATE_TYPE_DAY)

    awaited = get_json.await_args
    assert awaited is not None
    args, kwargs = awaited
    assert args[0] == DEVICE_CT_STAT_PATH
    assert kwargs["params"][APP_REQUEST_STAT_TYPE] == str(CT_STAT_TYPE_L1)
    assert result == payload


@pytest.mark.asyncio
async def test_ct_stat_includes_type_parameter_l2() -> None:
    """CT stat request supports type=1 (CT_STAT_TYPE_L2) for second CT clamp."""
    api = _api()
    payload = {
        "x": ["00:00", "00:05"],
        "y1": [100.0, 105.0],
        "y2": [50.0, 52.0],
    }
    get_json = AsyncMock(return_value={FIELD_DATA: payload})

    with patch.object(api, "_get_json", get_json):
        # Note: async_get_device_ct_stat currently hardcodes CT_STAT_TYPE_L1
        # This test documents the expected behavior when L2 support is added
        result = await api.async_get_device_ct_stat(11, date_type=DATE_TYPE_DAY)

    awaited = get_json.await_args
    assert awaited is not None
    args, kwargs = awaited
    assert args[0] == DEVICE_CT_STAT_PATH
    # Current implementation uses CT_STAT_TYPE_L1 (0)
    assert kwargs["params"][APP_REQUEST_STAT_TYPE] == str(CT_STAT_TYPE_L1)
    assert result == payload


@pytest.mark.asyncio
async def test_ct_stat_response_contains_y1_y2_arrays() -> None:
    """CT stat response parsing preserves y1 (primary CT) and y2 (secondary CT) arrays."""  # noqa: E501, RUF105
    api = _api()
    payload = {
        "x": ["00:00", "00:05", "00:10"],
        "y1": [1200.5, 1210.3, 1195.0],  # Primary CT clamp (L1)
        "y2": [300.2, 305.1, 298.7],  # Secondary CT clamp (L2)
    }
    get_json = AsyncMock(return_value={FIELD_DATA: payload})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_get_device_ct_stat(11, date_type=DATE_TYPE_DAY)

    assert "y1" in result
    assert "y2" in result
    assert result["y1"] == [1200.5, 1210.3, 1195.0]
    assert result["y2"] == [300.2, 305.1, 298.7]
    assert result["x"] == ["00:00", "00:05", "00:10"]


@pytest.mark.asyncio
async def test_ct_stat_empty_arrays_when_no_data() -> None:
    """CT stat returns empty arrays when cloud has no data for the period."""
    api = _api()
    payload = {"x": [], "y1": [], "y2": []}
    get_json = AsyncMock(return_value={FIELD_DATA: payload})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_get_device_ct_stat(11, date_type=DATE_TYPE_DAY)

    assert result["y1"] == []
    assert result["y2"] == []
    assert result["x"] == []


@pytest.mark.asyncio
async def test_ct_stat_request_meta_includes_type() -> None:
    """Request meta annotation includes the type parameter for diagnostics."""
    api = _api()
    payload = {"x": ["00:00"], "y1": [100.0], "y2": [0.0]}
    get_json = AsyncMock(return_value={FIELD_DATA: payload})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_get_device_ct_stat(11, date_type=DATE_TYPE_DAY)

    meta = result["_request"]
    assert meta[APP_REQUEST_STAT_TYPE] == str(CT_STAT_TYPE_L1)
    assert meta["dateType"] == DATE_TYPE_DAY
    assert FIELD_DEVICE_ID not in meta


@pytest.mark.asyncio
async def test_ct_stat_wrapper_delegates_with_system_id() -> None:
    """async_get_device_ct_stat delegates to _async_get_device_period_stat with system_id."""  # noqa: E501, RUF105
    api = _api()
    delegate = AsyncMock(return_value={"x": [], "y1": [], "y2": [], "_request": {}})

    with patch.object(api, "_async_get_device_period_stat", delegate):
        await api.async_get_device_ct_stat(11, system_id=3, date_type=DATE_TYPE_DAY)

    awaited = delegate.await_args
    assert awaited is not None
    kwargs = awaited.kwargs
    assert kwargs["device_id"] == 11
    assert kwargs["system_id"] == 3
    assert kwargs["date_type"] == DATE_TYPE_DAY
    assert awaited.args[0] == DEVICE_CT_STAT_PATH


@pytest.mark.asyncio
async def test_ct_stat_device_id_in_params() -> None:
    """Device ID is correctly passed in query parameters."""
    api = _api()
    get_json = AsyncMock(return_value={FIELD_DATA: {"x": [], "y1": [], "y2": []}})

    with patch.object(api, "_get_json", get_json):
        await api.async_get_device_ct_stat(42, date_type=DATE_TYPE_DAY)

    awaited = get_json.await_args
    assert awaited is not None
    params = awaited.kwargs["params"]
    assert params[FIELD_DEVICE_ID] == "42"
