"""Contract tests for Jackery app endpoint wrappers kept for catalog parity."""

from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.jackery_solarvault.client.api import HttpProfile, JackeryApi
from custom_components.jackery_solarvault.const import (
    ACCESSORIES_BIND_PATH,
    ACCESSORIES_EXIST_PATH,
    ACCESSORIES_JACKERY_EXIST_PATH,
    ACCESSORIES_PATH,
    ACCESSORIES_SCANNABLE_PATH,
    APP_REQUEST_BEGIN_DATE,
    APP_REQUEST_DATE_TYPE,
    APP_REQUEST_END_DATE,
    APP_REQUEST_META,
    BATTERY_PACK_PATH,
    BLE_OTA_LINK_PATH,
    BOX_STAT_PATH,
    CARBON_STAT_PATH,
    CHECK_VERIFY_CODE_PATH,
    DEVICE_PROPERTY_PATH,
    DEVICE_PV_STAT_PATH,
    DEVICE_QR_CODE_PATH,
    DEVICE_SHARED_MANAGER_PATH,
    DEVICE_TODAY_ENERGY_PATH,
    FIELD_DATA,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_DEVICE_SN_LIST,
    FIELD_REGION_CODE,
    FIELD_REGISTER_APP_ID,
    FIELD_SUB_DEVICE_SN,
    FIELD_SYSTEM_ID,
    FIELD_TARGET_FIRMWARE_IDS,
    FIELD_TARGET_VERSION_ID,
    LOGOUT_PATH,
    OTA_LIST_PATH,
    REGISTER_APP_ID,
    REGISTER_PATH,
    SYSTEM_EXIST_PATH,
    VERIFY_CODE_PATH,
)


def _make_api() -> JackeryApi:
    """Build an API client whose transport boundary is patched per test."""
    return JackeryApi(Mock(), "tester@example.com", "secret")


@pytest.mark.asyncio
async def test_shelly_control_rejects_false_accepted_payload() -> None:
    """A successful HTTP envelope must not hide a rejected Shelly write."""
    api = _make_api()
    post_json = AsyncMock(return_value={FIELD_DATA: {"accepted": False}})

    with patch.object(api, "_post_json", post_json):
        accepted = await api.async_control_shelly_device(
            "shelly-1",
            action="switch",
            function="on",
        )

    assert accepted is False


@pytest.mark.asyncio
async def test_shelly_unbind_accepts_legacy_scalar_payload() -> None:
    """The older scalar acceptance response remains supported."""
    api = _make_api()
    post_form = AsyncMock(return_value={FIELD_DATA: "ok"})

    with patch.object(api, "_post_form", post_form):
        accepted = await api.async_unbind_shelly_device("binding-1", "shelly-1")

    assert accepted is True


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_async_get_device_property_uses_get_with_device_id_query() -> None:
    """DeviceDetailApi (/device/property) is a GET with a deviceId query param.

    A regression to POST-with-body made the Jackery cloud reject every
    /device/property call with code=10600, which emptied the entire live
    property surface (Home systems never recovered, blocking Layer 5 setup).
    The 0.1.0 baseline and the app both use GET; pin that contract here.
    """
    api = _make_api()
    payload = {"device": {"deviceSn": "SN-1"}, "properties": {"soc": 55}}
    get_json = AsyncMock(return_value={FIELD_DATA: payload})
    post_json = AsyncMock()

    with (
        patch.object(api, "_get_json", get_json),
        patch.object(api, "_post_json", post_json),
    ):
        result = await api.async_get_device_property(573702884982521856)

    get_json.assert_awaited_once_with(
        DEVICE_PROPERTY_PATH,
        params={FIELD_DEVICE_ID: "573702884982521856"},
        profile=HttpProfile.FAST,
        retry_transport_once=True,
    )
    post_json.assert_not_awaited()
    assert result == payload


@pytest.mark.asyncio
async def test_async_get_today_energy_uses_get_with_device_sn_query() -> None:
    """device/stat/today is a GET with a deviceSn query param, not a POST body.

    Same regression class as /device/property and /device/ota/list: a
    POST-with-body request is rejected by the cloud with code=10600, which
    emptied the today-energy KPIs every cycle. The const catalog marks it
    ``# ?deviceSn=<...>`` (GET), matching every other device/stat/* reader.
    """
    api = _make_api()
    payload = {"de": 1.0, "dg": 2.0, "dh": 3.0, "ds": 4.0}
    get_json = AsyncMock(return_value={FIELD_DATA: payload})
    post_json = AsyncMock()

    with (
        patch.object(api, "_get_json", get_json),
        patch.object(api, "_post_json", post_json),
    ):
        result = await api.async_get_today_energy("HR2C04000280HH3")

    get_json.assert_awaited_once_with(
        DEVICE_TODAY_ENERGY_PATH,
        params={FIELD_DEVICE_SN: "HR2C04000280HH3"},
    )
    post_json.assert_not_awaited()
    assert result == payload


@pytest.mark.asyncio
async def test_async_get_ota_info_uses_get_with_device_sn_list_query() -> None:
    """DeviceMqttOTASelectApi (/device/ota/list) is a GET with a deviceSnList query.

    Same regression class as /device/property: a POST-with-body request is
    rejected by the cloud; the 0.1.0 baseline and the const catalog
    (``# ?deviceSnList=<sn>``) both use GET.
    """
    api = _make_api()
    get_json = AsyncMock(return_value={FIELD_DATA: []})
    post_json = AsyncMock()

    with (
        patch.object(api, "_get_json", get_json),
        patch.object(api, "_post_json", post_json),
    ):
        await api.async_get_ota_info("SN-1")

    get_json.assert_awaited_once_with(
        OTA_LIST_PATH,
        params={FIELD_DEVICE_SN_LIST: "SN-1"},
    )
    post_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_accessories_contract_stringifies_ids() -> None:
    """device/accessories stringifies its ids on each of its two real verbs.

    ``home.DeviceAccessoriesApi`` lists ``devices,id,parentDeviceId``, but that
    field set is the *union* of two distinct call sites rather than one GET
    query -- the same one-DTO-many-verbs shape as ``DiyDeviceSystemApi``
    (device/system = POST bind + DELETE unbind). The app POSTs to add
    (``devices`` + ``parentDeviceId``) and DELETEs to remove (``id``); there is
    no GET reader on this path. Pin both real verbs and their id stringifying.
    """
    api = _make_api()
    post_json = AsyncMock(return_value={FIELD_DATA: {"successCount": 1}})
    delete_json = AsyncMock(return_value={FIELD_DATA: {"ok": True}})

    with (
        patch.object(api, "_post_json", post_json),
        patch.object(api, "_delete_json", delete_json),
    ):
        added = await api.async_add_accessories(
            devices=[{"deviceSn": "dev-a"}],
            parent_device_id=456,
        )
        removed = await api.async_remove_accessory(accessory_id=123)

    assert added == {"successCount": 1}
    post_json.assert_awaited_once_with(
        ACCESSORIES_PATH,
        {"devices": [{"deviceSn": "dev-a"}], "parentDeviceId": "456"},
    )

    assert removed == {"ok": True}
    delete_json.assert_awaited_once_with(ACCESSORIES_PATH, {"id": "123"})


@pytest.mark.asyncio
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
        {
            "code": "123456",
            "email": "owner@example.com",
            "method": "email",
            "phone": "",
        },
    )


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_async_send_verification_code_contract_posts_app_fields() -> None:
    """Verification-code issuance POSTs the app catalog body fields.

    Source-of-truth (``auth/verificationCode`` / GetVerificationCodeApi) is a
    POST carrying ``email``, ``method`` and ``phone`` in the request body.
    """
    api = _make_api()
    post_json = AsyncMock(return_value={FIELD_DATA: {"sent": True}})

    with patch.object(api, "_post_json", post_json):
        result = await api.async_send_verification_code(
            email="owner@example.com",
            phone="+491234",
        )

    assert result == {"sent": True}
    post_json.assert_awaited_once_with(
        VERIFY_CODE_PATH,
        {"email": "owner@example.com", "method": "email", "phone": "+491234"},
    )


@pytest.mark.asyncio
async def test_async_logout_contract_has_empty_body() -> None:
    """Logout wrapper sends the app catalog's body-less POST."""
    api = _make_api()
    post_json = AsyncMock(return_value={FIELD_DATA: {"ok": True}})

    with patch.object(api, "_post_json", post_json):
        result = await api.async_logout()

    assert result == {"ok": True}
    post_json.assert_awaited_once_with(LOGOUT_PATH, {})


@pytest.mark.asyncio
async def test_async_get_qr_code_contract_has_no_query_params() -> None:
    """QR-code wrapper requests the account share code without parameters."""
    api = _make_api()
    payload = {"qrCodeId": "qr-1", "userId": "user-1"}
    get_json = AsyncMock(return_value={FIELD_DATA: payload})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_get_qr_code()

    assert result == payload
    get_json.assert_awaited_once_with(DEVICE_QR_CODE_PATH)


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_async_bind_accessories_contract() -> None:
    """Accessory bind hits the bind path and passes the response through.

    Mirrors ``DeviceAccessoriesBindApi`` (source-of-truth
    ``device/accessories/bind``). ``AccessoryItem`` is not in any
    source-of-truth field catalog, so only the request path and pass-through
    response are pinned here -- the descriptor field names are inferred from
    the decompiled DTO, not catalog-confirmed.
    """
    api = _make_api()
    payload = {"successCount": 1, "failCount": 0}
    post_json = AsyncMock(return_value=payload)

    with patch.object(api, "_post_json", post_json):
        result = await api.async_bind_accessories(
            accessories=[{"deviceSn": "dev-a"}],
        )

    assert result == payload
    awaited = post_json.await_args
    assert awaited is not None
    assert awaited.args[0] == ACCESSORIES_BIND_PATH


@pytest.mark.asyncio
async def test_async_check_scannable_accessories_contract() -> None:
    """Scannable-accessory check hits the scannable path and passes data through.

    Mirrors ``DeviceAccessoriesScannableApi`` (source-of-truth
    ``device/accessories/scannable``). ``RequestBean`` is not in any
    source-of-truth field catalog, so only the request path and pass-through
    response are pinned here -- the descriptor field names are inferred from
    the decompiled DTO, not catalog-confirmed.
    """
    api = _make_api()
    payload = {"scannable": [{"deviceSn": "dev-a"}]}
    post_json = AsyncMock(return_value=payload)

    with patch.object(api, "_post_json", post_json):
        result = await api.async_check_scannable_accessories(
            devices=[{"deviceSn": "dev-a", "bindKey": 1}],
        )

    assert result == payload
    awaited = post_json.await_args
    assert awaited is not None
    assert awaited.args[0] == ACCESSORIES_SCANNABLE_PATH


@pytest.mark.asyncio
async def test_orphan_endpoint_contracts_normalize_non_dict_payloads() -> None:
    """Catalog-only dict wrappers do not leak unexpected payload shapes."""
    api = _make_api()
    get_json = AsyncMock(return_value={FIELD_DATA: [{"unexpected": "list"}]})

    with patch.object(api, "_get_json", get_json):
        result = await api.async_check_accessories_exist(devices="dev-a")

    assert result == {}


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
async def test_async_get_carbon_stat_uses_get_with_device_sn_query() -> None:
    """device/stat/carbon is a GET with a deviceSn query, not a POST body.

    Every device/stat/* sibling (soc, profit, symmetry) and the shipped 0.1.0
    baseline use GET; a POST-with-body regression is rejected by the cloud with
    code=10600 and empties the carbon KPIs. Pin the GET contract here.
    """
    api = _make_api()
    payload = {"carbon": 1.5}
    get_json = AsyncMock(return_value={FIELD_DATA: payload})
    post_json = AsyncMock()

    with (
        patch.object(api, "_get_json", get_json),
        patch.object(api, "_post_json", post_json),
    ):
        result = await api.async_get_carbon_stat(device_sn="SN-9")

    get_json.assert_awaited_once_with(
        CARBON_STAT_PATH,
        params={FIELD_DEVICE_SN: "SN-9"},
    )
    post_json.assert_not_awaited()
    assert result == payload


@pytest.mark.asyncio
async def test_async_get_ble_ota_link_uses_get_with_query() -> None:
    """device/ota/bluetooth is a GET query (DeviceBleOTALinkQuiryApi).

    Sibling device/ota/list is a GET; the letter in the reference CSV is the
    Retrofit method id, not the HTTP verb. A POST-with-body regression is
    rejected with code=10600, so pin the GET query contract.
    """
    api = _make_api()
    get_json = AsyncMock(return_value={FIELD_DATA: {"url": "http://x"}})
    post_json = AsyncMock()

    with (
        patch.object(api, "_get_json", get_json),
        patch.object(api, "_post_json", post_json),
    ):
        await api.async_get_ble_ota_link(
            device_sn="SN-1",
            sub_device_sn="SUB-1",
            target_firmware_ids="fw1",
            target_version_id="v1",
        )

    get_json.assert_awaited_once_with(
        BLE_OTA_LINK_PATH,
        params={
            FIELD_DEVICE_SN: "SN-1",
            FIELD_SUB_DEVICE_SN: "SUB-1",
            FIELD_TARGET_FIRMWARE_IDS: "fw1",
            FIELD_TARGET_VERSION_ID: "v1",
        },
    )
    post_json.assert_not_awaited()


@pytest.mark.asyncio
async def test_async_check_system_bound_uses_get_with_query() -> None:
    """device/system/exist (SystemBindExistApi) is a read/existence GET check.

    It carries no request body — only bindKey/deviceSn/guid query params. A
    POST-with-body regression is rejected with code=10600.
    """
    api = _make_api()
    get_json = AsyncMock(return_value={FIELD_DATA: {"exist": True}})
    post_json = AsyncMock()

    with (
        patch.object(api, "_get_json", get_json),
        patch.object(api, "_post_json", post_json),
    ):
        result = await api.async_check_system_bound(
            bind_key="bk",
            device_sn="SN-1",
            guid="g1",
        )

    get_json.assert_awaited_once_with(
        SYSTEM_EXIST_PATH,
        params={"bindKey": "bk", "deviceSn": "SN-1", "guid": "g1"},
    )
    post_json.assert_not_awaited()
    assert result == {FIELD_DATA: {"exist": True}}


@pytest.mark.asyncio
async def test_async_get_device_shared_managers_posts_form_body() -> None:
    """device/bind/share/list is a POST form body, not a read-only GET query.

    The verb is chosen by the builder at the app call site, never by the
    endpoint catalog's method-id column. ``SharedManagerActivity.requestLists()``
    builds this request with ``jj/k`` (POST), whereas the proven-GET readers
    ``DeviceDetailApi`` (device/property, the endpoint whose GET->POST flip
    caused the live code=10600 outage) and this endpoint's sibling
    ``DeviceSharedListApi`` (device/bind/shared) both build with ``jj/g`` (GET).
    "It is a *list* endpoint, so it must be a GET" is exactly the unsound
    inference to guard against here: the sibling is a GET, this one is not.
    Pin the POST-form contract in both directions.
    """
    api = _make_api()
    managers = [{"bindUserId": "u1"}]
    post_form = AsyncMock(return_value={FIELD_DATA: managers})
    get_json = AsyncMock()

    with (
        patch.object(api, "_post_form", post_form),
        patch.object(api, "_get_json", get_json),
    ):
        result = await api.async_get_device_shared_managers(
            bind_user_id="u1",
            level=0,
        )

    post_form.assert_awaited_once_with(
        DEVICE_SHARED_MANAGER_PATH,
        {"bindUserId": "u1", "level": 0},
    )
    get_json.assert_not_awaited()
    assert result == managers
