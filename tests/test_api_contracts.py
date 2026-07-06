"""Contract tests for Jackery app endpoint wrappers kept for catalog parity."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.const import (
    ACCESSORIES_EXIST_PATH,
    ACCESSORIES_JACKERY_EXIST_PATH,
    ACCESSORIES_PATH,
    APP_REQUEST_BEGIN_DATE,
    APP_REQUEST_DATE_TYPE,
    APP_REQUEST_END_DATE,
    APP_REQUEST_META,
    BATTERY_PACK_PATH,
    BOX_STAT_PATH,
    CHECK_VERIFY_CODE_PATH,
    DEVICE_PV_STAT_PATH,
    DEVICE_QR_CODE_PATH,
    FIELD_DATA,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_REGION_CODE,
    FIELD_REGISTER_APP_ID,
    FIELD_SYSTEM_ID,
    LOGOUT_PATH,
    REGISTER_APP_ID,
    REGISTER_PATH,
    VERIFY_CODE_PATH,
)


def _make_api() -> JackeryApi:
    """Build an API client whose transport boundary is patched per test."""
    return JackeryApi(Mock(), "tester@example.com", "secret")


@pytest.mark.asyncio()
async def test_async_get_box_stat_contract_includes_explicit_period() -> None:
    """The catalog-only box-stat endpoint keeps the app request shape stable."""
    api = _make_api()
    payload = {"total": 12.3, "unit": "kWh", "x": ["00:00"], "y": [12.3]}
    get_json = AsyncMock(return_value={FIELD_DATA: payload})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_get_box_stat(
            device_sn="SN-123",
            date_type="day",
            begin_date="2026-07-03",
            end_date="2026-07-03",
            key="pv",
        )

    assert result == payload
    get_json.assert_awaited_once_with(
        BOX_STAT_PATH,
        params={
            "deviceSn": "SN-123",
            APP_REQUEST_DATE_TYPE: "day",
            APP_REQUEST_BEGIN_DATE: "2026-07-03",
            APP_REQUEST_END_DATE: "2026-07-03",
            "key": "pv",
        },
    )


@pytest.mark.asyncio()
async def test_async_get_accessories_contract_stringifies_ids() -> None:
    """Accessory-detail catalog endpoint sends the same query keys as the app."""
    api = _make_api()
    payload = {"accessories": [{"id": "child-1"}]}
    get_json = AsyncMock(return_value={FIELD_DATA: payload})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_get_accessories(
            devices="dev-a,dev-b",
            id=123,
            parent_device_id=456,
        )

    assert result == payload
    get_json.assert_awaited_once_with(
        ACCESSORIES_PATH,
        {
            "devices": "dev-a,dev-b",
            "id": "123",
            "parentDeviceId": "456",
        },
    )


@pytest.mark.asyncio()
async def test_async_check_verification_code_contract_sends_code() -> None:
    """Verification-code checks send the app catalog's code field."""
    api = _make_api()
    post_json = AsyncMock(return_value={FIELD_DATA: {"valid": True}})

    with patch.object(api, "_post_json", post_json):
        result = await api.async_check_verification_code(
            email="owner@example.com",
            code="123456",
        )

    assert result == {FIELD_DATA: {"valid": True}}
    post_json.assert_awaited_once_with(
        CHECK_VERIFY_CODE_PATH,
        {"email": "owner@example.com", "method": "email", "code": "123456"},
    )


@pytest.mark.asyncio()
async def test_async_register_contract_sends_app_identity_and_region() -> None:
    """Registration wrapper preserves source-of-truth request fields."""
    api = JackeryApi(Mock(), "tester@example.com", "secret", region_code="de")
    payload = {"userId": "user-1"}
    post_json = AsyncMock(return_value={FIELD_DATA: payload})

    with patch.object(api, "_post_json", post_json):
        result = await api.async_register(
            email="owner@example.com",
            password="secret",
            verification_code="123456",
        )

    assert result == payload
    post_json.assert_awaited_once_with(
        REGISTER_PATH,
        {
            "email": "owner@example.com",
            "password": "secret",
            "verificationCode": "123456",
            FIELD_REGISTER_APP_ID: REGISTER_APP_ID,
            FIELD_REGION_CODE: "DE",
        },
    )


@pytest.mark.asyncio()
async def test_async_send_verification_code_contract_uses_query_params() -> None:
    """Verification-code issuance uses the app catalog query fields."""
    api = _make_api()
    get_json = AsyncMock(return_value={FIELD_DATA: {"sent": True}})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_send_verification_code(
            email="owner@example.com",
            phone="+491234",
        )

    assert result == {"sent": True}
    get_json.assert_awaited_once_with(
        VERIFY_CODE_PATH,
        params={"email": "owner@example.com", "method": "email", "phone": "+491234"},
    )


@pytest.mark.asyncio()
async def test_async_logout_contract_has_empty_body() -> None:
    """Logout wrapper sends the app catalog's body-less POST."""
    api = _make_api()
    post_json = AsyncMock(return_value={FIELD_DATA: {"ok": True}})

    with patch.object(api, "_post_json", post_json):
        result = await api.async_logout()

    assert result == {"ok": True}
    post_json.assert_awaited_once_with(LOGOUT_PATH, {})


@pytest.mark.asyncio()
async def test_async_get_qr_code_contract_has_no_query_params() -> None:
    """QR-code wrapper requests the account share code without parameters."""
    api = _make_api()
    payload = {"qrCodeId": "qr-1", "userId": "user-1"}
    get_json = AsyncMock(return_value={FIELD_DATA: payload})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_get_qr_code()

    assert result == payload
    get_json.assert_awaited_once_with(DEVICE_QR_CODE_PATH)


@pytest.mark.asyncio()
async def test_async_check_accessories_exist_contract() -> None:
    """Accessory existence check remains available without wiring an entity."""
    api = _make_api()
    payload = {"dev-a": True}
    get_json = AsyncMock(return_value={FIELD_DATA: payload})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_check_accessories_exist(devices="dev-a")

    assert result == payload
    get_json.assert_awaited_once_with(
        ACCESSORIES_EXIST_PATH,
        params={"devices": "dev-a"},
    )


@pytest.mark.asyncio()
async def test_async_check_jackery_accessories_exist_contract() -> None:
    """Jackery accessory existence check keeps the app's serial-info parameter."""
    api = _make_api()
    payload = {"SN-123": {"exists": True}}
    get_json = AsyncMock(return_value={FIELD_DATA: payload})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_check_jackery_accessories_exist(
            device_sn_infos="SN-123",
        )

    assert result == payload
    get_json.assert_awaited_once_with(
        ACCESSORIES_JACKERY_EXIST_PATH,
        params={"deviceSnInfos": "SN-123"},
    )


@pytest.mark.asyncio()
async def test_orphan_endpoint_contracts_normalize_non_dict_payloads() -> None:
    """Catalog-only dict wrappers do not leak unexpected payload shapes."""
    api = _make_api()
    get_json = AsyncMock(return_value={FIELD_DATA: [{"unexpected": "list"}]})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_check_accessories_exist(devices="dev-a")

    assert result == {}


@pytest.mark.asyncio()
async def test_device_period_diagnostics_keep_context_for_null_payload() -> None:
    """Diagnostics keep request metadata even when the backend sends data:null."""
    api = _make_api()
    get_json = AsyncMock(return_value={FIELD_DATA: None})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_get_device_pv_stat(
            device_id="dev-1",
            system_id="sys-1",
            date_type="day",
            begin_date="2026-07-03",
            end_date="2026-07-03",
        )

    assert result == {
        APP_REQUEST_META: {
            APP_REQUEST_DATE_TYPE: "day",
            APP_REQUEST_BEGIN_DATE: "2026-07-03",
            APP_REQUEST_END_DATE: "2026-07-03",
        },
    }
    stored = api.last_device_period_stat_responses[f"{DEVICE_PV_STAT_PATH}:dev-1:day"]
    assert stored[APP_REQUEST_META] == {
        "path": DEVICE_PV_STAT_PATH,
        "params": {
            FIELD_DEVICE_ID: "dev-1",
            FIELD_SYSTEM_ID: "sys-1",
            APP_REQUEST_DATE_TYPE: "day",
            APP_REQUEST_BEGIN_DATE: "2026-07-03",
            APP_REQUEST_END_DATE: "2026-07-03",
        },
    }


@pytest.mark.asyncio()
async def test_battery_pack_diagnostics_keep_request_context_for_null_payload() -> None:
    """Battery-pack diagnostics keep request metadata for empty app responses."""
    api = _make_api()
    get_json = AsyncMock(return_value={FIELD_DATA: None})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_get_battery_pack_list(device_sn="SN-123")

    assert result == []
    assert api.last_battery_pack_responses["SN-123"][APP_REQUEST_META] == {
        "path": BATTERY_PACK_PATH,
        "params": {FIELD_DEVICE_SN: "SN-123"},
    }
