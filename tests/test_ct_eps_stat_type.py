"""Unit tests for CT/EPS stat type parameter handling."""

from typing import Any

from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.const import (
    APP_REQUEST_META,
    APP_REQUEST_STAT_TYPE,
    CT_STAT_TYPE_L1,
    CT_STAT_TYPE_L2,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    DEVICE_CT_STAT_PATH,
    DEVICE_EPS_STAT_PATH,
    EPS_STAT_TYPE_L1,
    FIELD_DEVICE_ID,
    FIELD_SYSTEM_ID,
)


class MockJackeryApi(JackeryApi):
    """Mock JackeryApi with captured parameters."""

    def __init__(self) -> None:  # noqa: D107, RUF105
        from unittest.mock import AsyncMock  # noqa: PLC0415, RUF105

        super().__init__(
            session=AsyncMock(),
            account="test_account",
            password="test_password",
        )
        self.captured_params: dict[str, dict[str, str]] = {}

    async def _get_json(self, path: str, params: dict[str, str]) -> dict[str, Any]:
        self.captured_params[path] = params
        return {"code": 0, "data": {"x": [], "y": [], "y1": [], "y2": []}}


async def test_async_get_device_ct_stat_defaults_to_l1() -> None:
    """async_get_device_ct_stat defaults to CT_STAT_TYPE_L1 (0) when stat_type not provided."""  # noqa: E501, RUF105
    api = MockJackeryApi()

    await api.async_get_device_ct_stat(
        device_id="dev1",
        system_id="sys1",
        date_type=DATE_TYPE_DAY,
        begin_date="2026-01-01",
        end_date="2026-01-01",
    )

    params = api.captured_params[DEVICE_CT_STAT_PATH]
    assert params[APP_REQUEST_STAT_TYPE] == str(CT_STAT_TYPE_L1)
    assert params[FIELD_DEVICE_ID] == "dev1"
    assert params[FIELD_SYSTEM_ID] == "sys1"


async def test_async_get_device_ct_stat_explicit_l1() -> None:
    """async_get_device_ct_stat accepts explicit CT_STAT_TYPE_L1."""
    api = MockJackeryApi()

    await api.async_get_device_ct_stat(
        device_id="dev1",
        system_id="sys1",
        date_type=DATE_TYPE_DAY,
        begin_date="2026-01-01",
        end_date="2026-01-01",
        stat_type=CT_STAT_TYPE_L1,
    )

    params = api.captured_params[DEVICE_CT_STAT_PATH]
    assert params[APP_REQUEST_STAT_TYPE] == str(CT_STAT_TYPE_L1)


async def test_async_get_device_ct_stat_explicit_l2() -> None:
    """async_get_device_ct_stat accepts explicit CT_STAT_TYPE_L2."""
    api = MockJackeryApi()

    await api.async_get_device_ct_stat(
        device_id="dev1",
        system_id="sys1",
        date_type=DATE_TYPE_DAY,
        begin_date="2026-01-01",
        end_date="2026-01-01",
        stat_type=CT_STAT_TYPE_L2,
    )

    params = api.captured_params[DEVICE_CT_STAT_PATH]
    assert params[APP_REQUEST_STAT_TYPE] == str(CT_STAT_TYPE_L2)


async def test_async_get_device_ct_stat_week_type() -> None:
    """async_get_device_ct_stat works with week date_type and stat_type."""
    api = MockJackeryApi()

    await api.async_get_device_ct_stat(
        device_id="dev1",
        system_id="sys1",
        date_type=DATE_TYPE_WEEK,
        begin_date="2026-01-05",
        end_date="2026-01-11",
        stat_type=CT_STAT_TYPE_L1,
    )

    params = api.captured_params[DEVICE_CT_STAT_PATH]
    assert params[APP_REQUEST_STAT_TYPE] == str(CT_STAT_TYPE_L1)
    assert params["dateType"] == DATE_TYPE_WEEK


async def test_async_get_device_ct_stat_month_type() -> None:
    """async_get_device_ct_stat works with month date_type and stat_type."""
    api = MockJackeryApi()

    await api.async_get_device_ct_stat(
        device_id="dev1",
        system_id="sys1",
        date_type=DATE_TYPE_MONTH,
        begin_date="2026-01-01",
        end_date="2026-01-31",
        stat_type=CT_STAT_TYPE_L1,
    )

    params = api.captured_params[DEVICE_CT_STAT_PATH]
    assert params[APP_REQUEST_STAT_TYPE] == str(CT_STAT_TYPE_L1)
    assert params["dateType"] == DATE_TYPE_MONTH


async def test_async_get_device_ct_stat_year_type() -> None:
    """async_get_device_ct_stat works with year date_type and stat_type."""
    api = MockJackeryApi()

    await api.async_get_device_ct_stat(
        device_id="dev1",
        system_id="sys1",
        date_type=DATE_TYPE_YEAR,
        begin_date="2026-01-01",
        end_date="2026-12-31",
        stat_type=CT_STAT_TYPE_L1,
    )

    params = api.captured_params[DEVICE_CT_STAT_PATH]
    assert params[APP_REQUEST_STAT_TYPE] == str(CT_STAT_TYPE_L1)
    assert params["dateType"] == DATE_TYPE_YEAR


async def test_async_get_device_eps_stat_defaults_to_l1() -> None:
    """async_get_device_eps_stat defaults to EPS_STAT_TYPE_L1 (0) when stat_type not provided."""  # noqa: E501, RUF105
    api = MockJackeryApi()

    await api.async_get_device_eps_stat(
        device_id="dev1",
        date_type=DATE_TYPE_DAY,
        begin_date="2026-01-01",
        end_date="2026-01-01",
    )

    params = api.captured_params[DEVICE_EPS_STAT_PATH]
    assert params[APP_REQUEST_STAT_TYPE] == str(EPS_STAT_TYPE_L1)
    assert params[FIELD_DEVICE_ID] == "dev1"


async def test_async_get_device_eps_stat_explicit_l1() -> None:
    """async_get_device_eps_stat accepts explicit EPS_STAT_TYPE_L1."""
    api = MockJackeryApi()

    await api.async_get_device_eps_stat(
        device_id="dev1",
        date_type=DATE_TYPE_DAY,
        begin_date="2026-01-01",
        end_date="2026-01-01",
        stat_type=EPS_STAT_TYPE_L1,
    )

    params = api.captured_params[DEVICE_EPS_STAT_PATH]
    assert params[APP_REQUEST_STAT_TYPE] == str(EPS_STAT_TYPE_L1)


async def test_async_get_device_eps_stat_week_type() -> None:
    """async_get_device_eps_stat works with week date_type and stat_type."""
    api = MockJackeryApi()

    await api.async_get_device_eps_stat(
        device_id="dev1",
        date_type=DATE_TYPE_WEEK,
        begin_date="2026-01-05",
        end_date="2026-01-11",
        stat_type=EPS_STAT_TYPE_L1,
    )

    params = api.captured_params[DEVICE_EPS_STAT_PATH]
    assert params[APP_REQUEST_STAT_TYPE] == str(EPS_STAT_TYPE_L1)
    assert params["dateType"] == DATE_TYPE_WEEK


async def test_async_get_device_eps_stat_month_type() -> None:
    """async_get_device_eps_stat works with month date_type and stat_type."""
    api = MockJackeryApi()

    await api.async_get_device_eps_stat(
        device_id="dev1",
        date_type=DATE_TYPE_MONTH,
        begin_date="2026-01-01",
        end_date="2026-01-31",
        stat_type=EPS_STAT_TYPE_L1,
    )

    params = api.captured_params[DEVICE_EPS_STAT_PATH]
    assert params[APP_REQUEST_STAT_TYPE] == str(EPS_STAT_TYPE_L1)
    assert params["dateType"] == DATE_TYPE_MONTH


async def test_async_get_device_eps_stat_year_type() -> None:
    """async_get_device_eps_stat works with year date_type and stat_type."""
    api = MockJackeryApi()

    await api.async_get_device_eps_stat(
        device_id="dev1",
        date_type=DATE_TYPE_YEAR,
        begin_date="2026-01-01",
        end_date="2026-12-31",
        stat_type=EPS_STAT_TYPE_L1,
    )

    params = api.captured_params[DEVICE_EPS_STAT_PATH]
    assert params[APP_REQUEST_STAT_TYPE] == str(EPS_STAT_TYPE_L1)
    assert params["dateType"] == DATE_TYPE_YEAR


async def test_ct_stat_type_parameter_in_request_meta() -> None:
    """CT stat request metadata includes the stat_type parameter."""
    api = MockJackeryApi()

    await api.async_get_device_ct_stat(
        device_id="dev1",
        system_id="sys1",
        date_type=DATE_TYPE_DAY,
        begin_date="2026-01-01",
        end_date="2026-01-01",
        stat_type=CT_STAT_TYPE_L2,
    )

    # Check that request metadata in stored response includes stat_type
    stored = api.last_device_period_stat_responses[f"{DEVICE_CT_STAT_PATH}:dev1:day"]
    assert stored[APP_REQUEST_META]["params"][APP_REQUEST_STAT_TYPE] == str(
        CT_STAT_TYPE_L2
    )  # noqa: E501, RUF100


async def test_eps_stat_type_parameter_in_request_meta() -> None:
    """EPS stat request metadata includes the stat_type parameter."""
    api = MockJackeryApi()

    await api.async_get_device_eps_stat(
        device_id="dev1",
        date_type=DATE_TYPE_DAY,
        begin_date="2026-01-01",
        end_date="2026-01-01",
        stat_type=EPS_STAT_TYPE_L1,
    )

    # Check that request metadata in stored response includes stat_type
    stored = api.last_device_period_stat_responses[f"{DEVICE_EPS_STAT_PATH}:dev1:day"]
    assert stored[APP_REQUEST_META]["params"][APP_REQUEST_STAT_TYPE] == str(
        EPS_STAT_TYPE_L1
    )  # noqa: E501, RUF100
