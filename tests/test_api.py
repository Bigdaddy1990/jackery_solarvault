"""Unit tests for the Jackery HTTP API client."""

from typing import Any

import pytest

from custom_components.jackery_solarvault.client.api import JackeryApi, JackeryApiError
from custom_components.jackery_solarvault.const import (
    APP_REQUEST_META,
    BATTERY_PACK_PATH,
    DEVICE_PV_STAT_PATH,
    FIELD_CODE,
    FIELD_CURRENCY,
    FIELD_DATA,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_ID,
    FIELD_PLATFORM_COMPANY_ID,
    FIELD_SINGLE_PRICE,
    FIELD_SYSTEM_ID,
    FIELD_SYSTEM_NAME,
    FIELD_SYSTEM_REGION,
    SAVE_DYNAMIC_MODE_PATH,
    SAVE_SINGLE_MODE_PATH,
    SYSTEM_NAME_PATH,
)


def test_extract_code_uses_shared_integer_parser() -> None:
    """API code parsing rejects bool/non-finite malformed response values."""
    assert JackeryApi._extract_code({FIELD_CODE: 200}) == 200  # noqa: PLR2004
    assert JackeryApi._extract_code({FIELD_CODE: "200.0"}) == 200  # noqa: PLR2004
    assert JackeryApi._extract_code({FIELD_CODE: True}) is None
    assert JackeryApi._extract_code({FIELD_CODE: float("nan")}) is None
    assert JackeryApi._extract_code({FIELD_CODE: "200.5"}) is None


async def test_set_system_name_accepts_only_boolean_true_response() -> None:
    """Rename success must require the API's documented boolean true payload."""
    api = JackeryApi.__new__(JackeryApi)
    captured: list[tuple[str, dict[str, Any]]] = []
    responses = iter((
        {FIELD_DATA: True},
        {FIELD_DATA: False},
        {FIELD_DATA: "true"},
        {FIELD_DATA: "false"},
        {},
    ))

    async def _put_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:  # noqa: RUF029
        captured.append((path, payload))
        return next(responses)

    api._put_json = _put_json

    assert await api.async_set_system_name("123", " SolarVault ") is True
    assert await api.async_set_system_name("123", "SolarVault") is False
    assert await api.async_set_system_name("123", "SolarVault") is False
    assert await api.async_set_system_name("123", "SolarVault") is False
    assert await api.async_set_system_name("123", "SolarVault") is False
    assert captured[0] == (
        SYSTEM_NAME_PATH,
        {FIELD_SYSTEM_NAME: "SolarVault", FIELD_ID: "123"},
    )


async def test_tariff_writers_reject_only_explicit_false_markers() -> None:
    """Tariff writes accept null-like success payloads but reject false markers."""
    api = JackeryApi.__new__(JackeryApi)
    captured: list[tuple[str, dict[str, Any]]] = []
    responses = iter((
        {FIELD_DATA: True},
        {FIELD_DATA: None},
        {},
        {FIELD_DATA: False},
        {FIELD_DATA: "false"},
        {FIELD_DATA: 0},
    ))

    async def _post_form(path: str, payload: dict[str, Any]) -> dict[str, Any]:  # noqa: RUF029
        captured.append((path, payload))
        return next(responses)

    api._post_form = _post_form

    assert (
        await api.async_set_single_mode(
            system_id="sys1",
            single_price=0.25,
            currency="EUR",
        )
        is True
    )
    assert (
        await api.async_set_single_mode(
            system_id="sys1",
            single_price=0.25,
            currency="EUR",
        )
        is True
    )
    assert (
        await api.async_set_dynamic_mode(
            system_id="sys1",
            platform_company_id=7,
            system_region="DE",
        )
        is True
    )
    assert (
        await api.async_set_dynamic_mode(
            system_id="sys1",
            platform_company_id=7,
            system_region="DE",
        )
        is False
    )
    assert (
        await api.async_set_dynamic_mode(
            system_id="sys1",
            platform_company_id=7,
            system_region="DE",
        )
        is False
    )
    assert (
        await api.async_set_dynamic_mode(
            system_id="sys1",
            platform_company_id=7,
            system_region="DE",
        )
        is False
    )
    assert captured[0] == (
        SAVE_SINGLE_MODE_PATH,
        {
            FIELD_SYSTEM_ID: "sys1",
            FIELD_SINGLE_PRICE: "0.25",
            FIELD_CURRENCY: "EUR",
        },
    )
    assert captured[2] == (
        SAVE_DYNAMIC_MODE_PATH,
        {
            FIELD_SYSTEM_ID: "sys1",
            FIELD_PLATFORM_COMPANY_ID: 7,
            FIELD_SYSTEM_REGION: "DE",
        },
    )


async def test_tariff_writers_validate_numeric_inputs_before_post() -> None:
    """Invalid tariff writer inputs must fail before the HTTP request."""
    api = JackeryApi.__new__(JackeryApi)

    async def _post_form(_path: str, _payload: dict[str, Any]) -> dict[str, Any]:  # noqa: RUF029
        """Test stub that prevents performing an HTTP form POST for tariff-related.

        operations.

        Used in unit tests to ensure input validation stops execution before any
        network request is made.

        Parameters:
            _path (str): The request path that would have been posted to.
            _payload (dict[str, Any]): The form payload that would have been sent.

        Raises:
            AssertionError: Always raised with message "invalid tariff input must stop
            before HTTP post".
        """
        msg = "invalid tariff input must stop before HTTP post"
        raise AssertionError(msg)

    api._post_form = _post_form

    with pytest.raises(JackeryApiError, match="single_price"):
        await api.async_set_single_mode(
            system_id="sys1",
            single_price=float("nan"),
            currency="EUR",
        )
    with pytest.raises(JackeryApiError, match="platform_company_id"):
        await api.async_set_dynamic_mode(
            system_id="sys1",
            platform_company_id="7.5",
            system_region="DE",
        )


@pytest.mark.parametrize(
    ["company_id", "expected_company_id"],
    [["7.0", 7], [7.0, 7], [9007199254740993, 9007199254740993]],
)
async def test_dynamic_tariff_writer_accepts_integral_company_id_values(
    company_id: str | float,
    expected_company_id: int,
) -> None:
    """API writer should accept app-style integral values without truncation."""
    api = JackeryApi.__new__(JackeryApi)
    captured: list[tuple[str, dict[str, Any]]] = []

    async def _post_form(path: str, payload: dict[str, Any]) -> dict[str, Any]:  # noqa: RUF029
        captured.append((path, payload))
        return {FIELD_DATA: True}

    api._post_form = _post_form

    assert (
        await api.async_set_dynamic_mode(
            system_id="sys1",
            platform_company_id=company_id,
            system_region=" DE ",
        )
        is True
    )
    assert captured == [
        (
            SAVE_DYNAMIC_MODE_PATH,
            {
                FIELD_SYSTEM_ID: "sys1",
                FIELD_PLATFORM_COMPANY_ID: expected_company_id,
                FIELD_SYSTEM_REGION: "DE",
            },
        ),
    ]


async def test_dynamic_tariff_writer_rejects_bool_company_id() -> None:
    """API writer should not treat bool as an integer company id."""
    api = JackeryApi.__new__(JackeryApi)

    async def _post_form(_path: str, _payload: dict[str, Any]) -> dict[str, Any]:  # noqa: RUF029
        raise AssertionError("invalid tariff input must stop before HTTP post")  # noqa: TRY003

    api._post_form = _post_form

    with pytest.raises(JackeryApiError, match="platform_company_id"):
        await api.async_set_dynamic_mode(
            system_id="sys1",
            platform_company_id=True,
            system_region="DE",
        )


async def test_device_period_diagnostics_keep_request_context_for_null_payload() -> (
    None
):
    """Null chart responses must stay traceable in diagnostics."""
    api = JackeryApi.__new__(JackeryApi)
    api.last_device_period_stat_responses = {}

    async def _get_json(path: str, params: dict[str, str]) -> dict[str, Any]:  # noqa: RUF029
        assert path == DEVICE_PV_STAT_PATH
        return {FIELD_CODE: 0, FIELD_DATA: None}

    api._get_json = _get_json

    payload = await api.async_get_device_pv_stat(
        "dev1",
        "sys1",
        date_type="day",
        begin_date="2026-05-23",
        end_date="2026-05-23",
    )

    request_meta = {
        "dateType": "day",
        "beginDate": "2026-05-23",
        "endDate": "2026-05-23",
    }
    stored = api.last_device_period_stat_responses[f"{DEVICE_PV_STAT_PATH}:dev1:day"]

    assert payload == {APP_REQUEST_META: request_meta}
    assert stored[FIELD_DATA] is None
    assert stored[APP_REQUEST_META] == {
        "path": DEVICE_PV_STAT_PATH,
        "params": {
            FIELD_DEVICE_ID: "dev1",
            FIELD_SYSTEM_ID: "sys1",
            **request_meta,
        },
    }


async def test_battery_pack_diagnostics_keep_request_context_for_null_payload() -> None:
    """SolarVault pack-list null responses must still identify the request."""
    api = JackeryApi.__new__(JackeryApi)
    api.last_battery_pack_responses = {}

    async def _get_json(path: str, params: dict[str, str]) -> dict[str, Any]:  # noqa: RUF029
        assert path == BATTERY_PACK_PATH
        return {FIELD_CODE: 0, FIELD_DATA: None}

    api._get_json = _get_json

    assert await api.async_get_battery_pack_list("sn1") == []

    stored = api.last_battery_pack_responses["sn1"]
    assert stored[FIELD_DATA] is None
    assert stored[APP_REQUEST_META] == {
        "path": BATTERY_PACK_PATH,
        "params": {FIELD_DEVICE_SN: "sn1"},
    }
