"""Unit tests for the Jackery HTTP API client."""

import base64
from typing import Any

import pytest

from custom_components.jackery_solarvault.client.api import (
    JackeryApi,
    JackeryApiError,
    JackeryAuthError,
)
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
    MQTT_CREDENTIAL_CLIENT_ID,
    MQTT_CREDENTIAL_PASSWORD,
    MQTT_CREDENTIAL_USER_ID,
    MQTT_CREDENTIAL_USERNAME,
    MQTT_SESSION_MAC_ID,
    MQTT_SESSION_MAC_ID_SOURCE,
    MQTT_SESSION_SEED_B64,
    MQTT_SESSION_USER_ID,
    SAVE_DYNAMIC_MODE_PATH,
    SAVE_SINGLE_MODE_PATH,
    SYSTEM_NAME_PATH,
)


def test_extract_code_uses_shared_integer_parser() -> None:
    """API code parsing rejects bool/non-finite malformed response values."""
    assert JackeryApi._extract_code({FIELD_CODE: 200}) == 200
    assert JackeryApi._extract_code({FIELD_CODE: "200.0"}) == 200
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

    async def _put_json(path: str, payload: dict[str, Any]) -> dict[str, Any]:
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

    async def _post_form(path: str, payload: dict[str, Any]) -> dict[str, Any]:
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

    async def _post_form(_path: str, _payload: dict[str, Any]) -> dict[str, Any]:
        raise AssertionError("invalid tariff input must stop before HTTP post")

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


async def test_dynamic_tariff_writer_accepts_integral_company_id_text() -> None:
    """API writer should accept app-style integral text without truncation."""
    api = JackeryApi.__new__(JackeryApi)
    captured: list[tuple[str, dict[str, Any]]] = []

    async def _post_form(path: str, payload: dict[str, Any]) -> dict[str, Any]:
        captured.append((path, payload))
        return {FIELD_DATA: True}

    api._post_form = _post_form

    assert (
        await api.async_set_dynamic_mode(
            system_id="sys1",
            platform_company_id="7.0",
            system_region=" DE ",
        )
        is True
    )
    assert captured == [
        (
            SAVE_DYNAMIC_MODE_PATH,
            {
                FIELD_SYSTEM_ID: "sys1",
                FIELD_PLATFORM_COMPANY_ID: 7,
                FIELD_SYSTEM_REGION: "DE",
            },
        )
    ]


async def test_device_period_diagnostics_keep_request_context_for_null_payload() -> (
    None
):
    """Null chart responses must stay traceable in diagnostics."""
    api = JackeryApi.__new__(JackeryApi)
    api.last_device_period_stat_responses = {}

    async def _get_json(path: str, params: dict[str, str]) -> dict[str, Any]:
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

    async def _get_json(path: str, params: dict[str, str]) -> dict[str, Any]:
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


_FAKE_SEED_B64 = base64.b64encode(bytes(range(32))).decode("ascii")


def _bare_api() -> JackeryApi:
    """Return a JackeryApi instance bypassing the cloud-bound constructor."""
    api = JackeryApi.__new__(JackeryApi)
    api._mqtt_user_id = None
    api._mqtt_seed_b64 = None
    api._mqtt_mac_id = None
    api._mqtt_mac_id_source = "generated"
    api._token = None
    return api


def test_mqtt_session_snapshot_returns_none_until_hydrated() -> None:
    """The snapshot must stay None until all three mandatory fields are set."""
    api = _bare_api()
    assert api.mqtt_session_snapshot() is None

    api.hydrate_mqtt_session(
        user_id="user-1",
        seed_b64=_FAKE_SEED_B64,
        mac_id="2" + "a" * 32,
        mac_id_source="configured",
    )
    snapshot = api.mqtt_session_snapshot()
    assert snapshot == {
        MQTT_SESSION_USER_ID: "user-1",
        MQTT_SESSION_SEED_B64: _FAKE_SEED_B64,
        MQTT_SESSION_MAC_ID: "2" + "a" * 32,
        MQTT_SESSION_MAC_ID_SOURCE: "configured",
    }


async def test_get_mqtt_credentials_allow_stale_skips_login_when_hydrated() -> None:
    """Hydrated MQTT fields must let allow_stale=True build creds without login."""
    api = _bare_api()

    async def _fail_login() -> str:  # pragma: no cover — must not be called
        raise AssertionError("async_login must not be called when stale is allowed")

    api.async_login = _fail_login  # type: ignore[assignment]

    api.hydrate_mqtt_session(
        user_id="user-1",
        seed_b64=_FAKE_SEED_B64,
        mac_id="2" + "b" * 32,
    )
    creds = await api.async_get_mqtt_credentials(allow_stale=True)
    assert creds[MQTT_CREDENTIAL_USER_ID] == "user-1"
    assert creds[MQTT_CREDENTIAL_CLIENT_ID] == "user-1@APP"
    assert creds[MQTT_CREDENTIAL_USERNAME] == "user-1@" + "2" + "b" * 32
    # Password is base64 AES output — must be a non-empty ASCII string.
    assert creds[MQTT_CREDENTIAL_PASSWORD]
    assert isinstance(creds[MQTT_CREDENTIAL_PASSWORD], str)


async def test_get_mqtt_credentials_allow_stale_without_hydration_raises() -> None:
    """allow_stale=True still requires hydrate or a real login first."""
    api = _bare_api()

    async def _ensure_token() -> str:
        raise JackeryAuthError("simulated cloud outage")

    api._ensure_token = _ensure_token  # type: ignore[assignment]

    with pytest.raises(JackeryAuthError):
        await api.async_get_mqtt_credentials(allow_stale=True)
