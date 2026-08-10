"""Behavioral coverage for the Jackery SolarVault service-action dispatchers.

Each domain-scoped action shares one routing contract: resolve the device to its
owning coordinator, reject portable devices for Home-family commands, forward to a
coordinator method, and surface failures as ``ServiceValidationError`` (or
``ConfigEntryAuthFailed`` when credentials are rejected). These tests drive that
contract for every registered action through Home Assistant's real service call
path, asserting the business outcome (the coordinator boundary is invoked and the
documented response envelope is returned) rather than internal call order.
"""

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.jackery_solarvault import services
from custom_components.jackery_solarvault.client.api import (
    JackeryAuthError,
    JackeryError,
)
from custom_components.jackery_solarvault.const import (
    APP_PERIOD_DATE_TYPES,
    DISCOVERY_SOURCE_LEGACY_BIND_LIST,
    MQTT_ACTION_IDS_SCHEDULE,
    PAYLOAD_DEVICE,
    PAYLOAD_DISCOVERY_SOURCE,
    PAYLOAD_SYSTEM,
    SERVICE_ACCEPT_SHARED_DEVICE,
    SERVICE_AGREE_PRIVACY_CONSENT,
    SERVICE_BIND_CURRENCY,
    SERVICE_BIND_DEVICE,
    SERVICE_BIND_SMART_PART,
    SERVICE_CANCEL_DYNAMIC_PRICE_CONTRACT_AUTH,
    SERVICE_CHECK_ACCESSORIES_EXIST,
    SERVICE_CHECK_APP_VERSION,
    SERVICE_CHECK_JACKERY_ACCESSORIES_EXIST,
    SERVICE_CHECK_PRIVACY_UPDATE,
    SERVICE_CHECK_SYSTEM_BOUND,
    SERVICE_DELETE_ELECTRICITY_STRATEGY,
    SERVICE_DELETE_STORM_ALERT,
    SERVICE_FIELD_ACCESSORY_ID,
    SERVICE_FIELD_ACCESSORY_SN,
    SERVICE_FIELD_ACCOUNT_NICKNAME,
    SERVICE_FIELD_ACTION_ID,
    SERVICE_FIELD_AC_PORT,
    SERVICE_FIELD_ALARM_KEY,
    SERVICE_FIELD_ALERT_ID,
    SERVICE_FIELD_BEGIN_DATE,
    SERVICE_FIELD_BINDING_ID,
    SERVICE_FIELD_BIND_IDS,
    SERVICE_FIELD_BIND_KEY,
    SERVICE_FIELD_BIND_USER_ID,
    SERVICE_FIELD_BODY,
    SERVICE_FIELD_CMD,
    SERVICE_FIELD_CONNECT_TOKEN,
    SERVICE_FIELD_CONTACT_INFO,
    SERVICE_FIELD_CONTENT,
    SERVICE_FIELD_CONTRACT_ID,
    SERVICE_FIELD_COUNTRY,
    SERVICE_FIELD_CURRENCY,
    SERVICE_FIELD_CUSTOMER_NUMBER,
    SERVICE_FIELD_CUSTOM_ID,
    SERVICE_FIELD_DATE_TYPE,
    SERVICE_FIELD_DEVICES,
    SERVICE_FIELD_DEVICE_ID,
    SERVICE_FIELD_DEVICE_SN,
    SERVICE_FIELD_DEVICE_SN_INFOS,
    SERVICE_FIELD_ENABLE,
    SERVICE_FIELD_END_DATE,
    SERVICE_FIELD_GUID,
    SERVICE_FIELD_ID,
    SERVICE_FIELD_IP,
    SERVICE_FIELD_LATITUDE,
    SERVICE_FIELD_LONGITUDE,
    SERVICE_FIELD_MAX_POWER,
    SERVICE_FIELD_NEW_NAME,
    SERVICE_FIELD_NICKNAME,
    SERVICE_FIELD_PENDING_AGREE_VERSION_IDS,
    SERVICE_FIELD_PLATFORM_COMPANY_ID,
    SERVICE_FIELD_PORT,
    SERVICE_FIELD_QR_CODE_ID,
    SERVICE_FIELD_SET,
    SERVICE_FIELD_SHELLY_DEVICE_ID,
    SERVICE_FIELD_SYSTEM_ID,
    SERVICE_FIELD_TARGET_DEV_ID,
    SERVICE_FIELD_TIMEZONE_OFFSET,
    SERVICE_FIELD_ZONE_ID,
    SERVICE_GET_ALARM_DETAIL,
    SERVICE_GET_DEVICE_CURRENCY,
    SERVICE_GET_DYNAMIC_PRICE_LOGIN_URL,
    SERVICE_GET_OFFLINE_STATISTICS,
    SERVICE_GET_PRODUCT_INSTRUCTION,
    SERVICE_GET_PUSH_CONFIG,
    SERVICE_GET_SHARE_QR_CODE,
    SERVICE_GET_SHELLY_AUTH_URL,
    SERVICE_GET_SMART_SCHEDULE_PREDICTION,
    SERVICE_GET_UNREAD_COUNT,
    SERVICE_GET_USER_INFO,
    SERVICE_INSERT_ELECTRICITY_STRATEGY,
    SERVICE_LIST_ACCESSORIES,
    SERVICE_LIST_BANNERS,
    SERVICE_LIST_COUNTRY_ZONES,
    SERVICE_LIST_CURRENCIES,
    SERVICE_LIST_DYNAMIC_PRICE_CONTRACTS,
    SERVICE_LIST_FAQS,
    SERVICE_LIST_FAQ_ANSWERS,
    SERVICE_LIST_GRID_STANDARDS,
    SERVICE_LIST_NOTIFICATIONS,
    SERVICE_LIST_SHARED_DEVICES,
    SERVICE_LIST_SHARED_MANAGERS,
    SERVICE_LIST_SHELLY_BINDING_FAILURES,
    SERVICE_LIST_SHELLY_DEVICES,
    SERVICE_QUERY_BOX_STAT,
    SERVICE_QUERY_CARBON_STAT,
    SERVICE_QUERY_CHARGE_REPORT,
    SERVICE_QUERY_CUTOFF_STAT,
    SERVICE_QUERY_ELECTRICITY_STRATEGY,
    SERVICE_QUERY_PROFIT_STAT,
    SERVICE_QUERY_SOCKET_STAT,
    SERVICE_QUERY_SOC_STAT,
    SERVICE_QUERY_THIRD_PARTY_MQTT_CONFIG,
    SERVICE_QUERY_TOU_PLAN,
    SERVICE_REFRESH_SUBDEVICES,
    SERVICE_REFRESH_WEATHER_PLAN,
    SERVICE_REMOVE_ALL_SHARED_ACCESS,
    SERVICE_REMOVE_SHARED_ACCESS,
    SERVICE_RENAME_SYSTEM,
    SERVICE_REPORT_DEVICE_TIMEZONE,
    SERVICE_SAVE_DEVICE_MAX_POWER,
    SERVICE_SAVE_DYNAMIC_PRICE_CONTRACT_AUTH,
    SERVICE_SAVE_DYNAMIC_PRICE_LOCATION_ID,
    SERVICE_SAVE_TOU_PLAN,
    SERVICE_SEND_BLE_COMMAND,
    SERVICE_SEND_DEVICE_SCHEDULE,
    SERVICE_SET_ACCESSORY_NAME,
    SERVICE_SET_ACCOUNT_NICKNAME,
    SERVICE_SET_AC_NICKNAME,
    SERVICE_SET_DEVICE_NICKNAME,
    SERVICE_SET_PUSH_CONFIG,
    SERVICE_SET_STORM_ALERT_LOCATION,
    SERVICE_SET_THIRD_PARTY_MQTT_CONFIG,
    SERVICE_SUBMIT_FEEDBACK,
    SERVICE_SYNC_ALERTS,
    SERVICE_UNBIND_ACCESSORIES,
    SERVICE_UNBIND_DEVICE,
    SERVICE_UNBIND_SHELLY_ACCOUNT,
    SERVICE_UNBIND_SHELLY_DEVICE,
    SERVICE_UNBIND_SMART_PART,
    SERVICE_UPDATE_ELECTRICITY_STRATEGY,
)
from custom_components.jackery_solarvault.services import async_setup_services
from homeassistant.exceptions import ConfigEntryAuthFailed, ServiceValidationError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_DEVICE_ID = "dev-1"
_DATE_TYPE = APP_PERIOD_DATE_TYPES[0]
_ACTION_ID = next(iter(MQTT_ACTION_IDS_SCHEDULE))
_TEXT = "abc"
_NUM_ID = "123"
_SENTINEL: dict[str, object] = {"jackery": "sentinel"}


class _HandlerCase(SimpleNamespace):
    """One declarative service-dispatch scenario for the shared contract tests."""

    name: str
    method: str
    extra: dict[str, object]
    is_response: bool
    portable: bool
    has_auth: bool
    backend_error: BaseException


def _case(  # ruff:ignore[too-many-arguments]
    name: str,
    method: str,
    *,
    extra: dict[str, object] | None = None,
    is_response: bool = False,
    portable: bool = False,
    has_auth: bool = True,
    backend_error: BaseException | None = None,
) -> _HandlerCase:
    return _HandlerCase(
        name=name,
        method=method,
        extra=extra or {},
        is_response=is_response,
        portable=portable,
        has_auth=has_auth,
        backend_error=backend_error or JackeryError("boom"),
    )


_CASES: tuple[_HandlerCase, ...] = (
    _case(
        SERVICE_REFRESH_WEATHER_PLAN,
        "async_query_weather_plan",
        portable=True,
    ),
    _case(
        SERVICE_REFRESH_SUBDEVICES,
        "async_refresh_subdevices",
        portable=True,
    ),
    _case(
        SERVICE_DELETE_STORM_ALERT,
        "async_delete_storm_alert",
        extra={SERVICE_FIELD_ALERT_ID: _TEXT},
        portable=True,
    ),
    _case(
        SERVICE_SET_STORM_ALERT_LOCATION,
        "async_update_storm_alert_location",
        extra={SERVICE_FIELD_LATITUDE: 52.5, SERVICE_FIELD_LONGITUDE: 13.4},
        portable=True,
    ),
    _case(
        SERVICE_SET_THIRD_PARTY_MQTT_CONFIG,
        "async_set_third_party_mqtt_config",
        extra={
            SERVICE_FIELD_ENABLE: True,
            SERVICE_FIELD_IP: "10.0.0.1",
            SERVICE_FIELD_PORT: 1883,
        },
        portable=True,
    ),
    _case(
        SERVICE_QUERY_THIRD_PARTY_MQTT_CONFIG,
        "async_query_third_party_mqtt_config",
        portable=True,
    ),
    _case(
        SERVICE_SEND_BLE_COMMAND,
        "async_send_ble_command",
        extra={SERVICE_FIELD_CMD: 1, SERVICE_FIELD_BODY: {"k": 1}},
        has_auth=False,
        backend_error=RuntimeError("boom"),
    ),
    _case(
        SERVICE_SEND_DEVICE_SCHEDULE,
        "async_send_device_schedule",
        extra={SERVICE_FIELD_ACTION_ID: _ACTION_ID, SERVICE_FIELD_BODY: {"k": 1}},
        portable=True,
    ),
    _case(
        SERVICE_QUERY_TOU_PLAN,
        "async_query_tou_plan",
        is_response=True,
        portable=True,
    ),
    _case(
        SERVICE_SAVE_TOU_PLAN,
        "async_save_tou_plan",
        extra={SERVICE_FIELD_BODY: [{"k": 1}]},
        portable=True,
    ),
    _case(SERVICE_LIST_CURRENCIES, "async_list_currencies", is_response=True),
    _case(
        SERVICE_GET_DEVICE_CURRENCY,
        "async_get_device_currency",
        is_response=True,
        portable=True,
    ),
    _case(
        SERVICE_BIND_CURRENCY,
        "async_bind_currency",
        extra={SERVICE_FIELD_CURRENCY: "EUR"},
        portable=True,
    ),
    _case(
        SERVICE_LIST_ACCESSORIES,
        "async_list_accessories",
        is_response=True,
        portable=True,
    ),
    _case(
        SERVICE_CHECK_ACCESSORIES_EXIST,
        "async_check_accessories_exist",
        extra={SERVICE_FIELD_DEVICES: _TEXT},
        is_response=True,
        portable=True,
    ),
    _case(
        SERVICE_CHECK_JACKERY_ACCESSORIES_EXIST,
        "async_check_jackery_accessories_exist",
        extra={SERVICE_FIELD_DEVICE_SN_INFOS: _TEXT},
        is_response=True,
        portable=True,
    ),
    _case(
        SERVICE_SET_ACCESSORY_NAME,
        "async_set_accessory_name",
        extra={SERVICE_FIELD_ACCESSORY_ID: _TEXT, SERVICE_FIELD_NICKNAME: _TEXT},
        portable=True,
    ),
    _case(
        SERVICE_GET_DYNAMIC_PRICE_LOGIN_URL,
        "async_get_dynamic_price_login_url",
        extra={SERVICE_FIELD_PLATFORM_COMPANY_ID: 7},
        is_response=True,
        portable=True,
    ),
    _case(
        SERVICE_LIST_DYNAMIC_PRICE_CONTRACTS,
        "async_list_dynamic_price_contracts",
        extra={
            SERVICE_FIELD_CUSTOMER_NUMBER: _TEXT,
            SERVICE_FIELD_PLATFORM_COMPANY_ID: 7,
        },
        is_response=True,
    ),
    _case(
        SERVICE_SAVE_DYNAMIC_PRICE_CONTRACT_AUTH,
        "async_save_dynamic_price_contract_auth",
        extra={
            SERVICE_FIELD_CONTRACT_ID: _TEXT,
            SERVICE_FIELD_CUSTOM_ID: _TEXT,
            SERVICE_FIELD_PLATFORM_COMPANY_ID: 7,
        },
        portable=True,
    ),
    _case(
        SERVICE_CANCEL_DYNAMIC_PRICE_CONTRACT_AUTH,
        "async_cancel_dynamic_price_contract_auth",
        extra={SERVICE_FIELD_PLATFORM_COMPANY_ID: 7},
        portable=True,
    ),
    _case(
        SERVICE_SAVE_DYNAMIC_PRICE_LOCATION_ID,
        "async_save_dynamic_price_location_id",
        extra={SERVICE_FIELD_CONNECT_TOKEN: _TEXT},
    ),
    _case(
        SERVICE_QUERY_SOCKET_STAT,
        "async_query_socket_stat",
        extra={
            SERVICE_FIELD_TARGET_DEV_ID: _TEXT,
            SERVICE_FIELD_DATE_TYPE: _DATE_TYPE,
            SERVICE_FIELD_BEGIN_DATE: "2026-01-01",
            SERVICE_FIELD_END_DATE: "2026-01-02",
        },
        is_response=True,
        portable=True,
    ),
    _case(SERVICE_LIST_COUNTRY_ZONES, "async_list_country_zones", is_response=True),
    _case(
        SERVICE_LIST_GRID_STANDARDS,
        "async_list_grid_standards",
        extra={SERVICE_FIELD_COUNTRY: "DE"},
        is_response=True,
    ),
    _case(
        SERVICE_CHECK_SYSTEM_BOUND,
        "async_check_system_bound",
        extra={
            # bind_key is a string end-to-end (handler _service_required_text
            # + api sends it verbatim as the bindKey query param).
            SERVICE_FIELD_BIND_KEY: _TEXT,
            SERVICE_FIELD_DEVICE_SN: _TEXT,
            SERVICE_FIELD_GUID: _TEXT,
        },
        is_response=True,
    ),
    _case(
        SERVICE_SAVE_DEVICE_MAX_POWER,
        "async_save_device_max_power",
        extra={SERVICE_FIELD_MAX_POWER: 500},
        portable=True,
    ),
    _case(
        SERVICE_GET_ALARM_DETAIL,
        "async_get_alarm_detail",
        extra={SERVICE_FIELD_ALARM_KEY: _TEXT},
        is_response=True,
        portable=True,
    ),
    _case(SERVICE_LIST_NOTIFICATIONS, "async_list_notifications", is_response=True),
    _case(SERVICE_GET_UNREAD_COUNT, "async_get_unread_count", is_response=True),
    _case(SERVICE_GET_PUSH_CONFIG, "async_get_push_config", is_response=True),
    _case(
        SERVICE_SET_PUSH_CONFIG,
        "async_set_push_config",
        extra={SERVICE_FIELD_SET: 1},
    ),
    _case(
        SERVICE_SYNC_ALERTS,
        "async_sync_alerts",
        extra={SERVICE_FIELD_CONTENT: _TEXT, SERVICE_FIELD_ID: _TEXT},
        portable=True,
    ),
    _case(
        SERVICE_GET_OFFLINE_STATISTICS,
        "async_get_offline_statistics",
        is_response=True,
        portable=True,
    ),
    _case(
        SERVICE_QUERY_CHARGE_REPORT,
        "async_query_charge_report",
        extra={SERVICE_FIELD_DEVICE_SN: _TEXT},
        is_response=True,
    ),
    _case(
        SERVICE_QUERY_CUTOFF_STAT,
        "async_query_cutoff_stat",
        extra={
            SERVICE_FIELD_DEVICE_SN: _TEXT,
            SERVICE_FIELD_BEGIN_DATE: "2026-01-01",
            SERVICE_FIELD_END_DATE: "2026-01-02",
        },
        is_response=True,
    ),
    _case(SERVICE_QUERY_SOC_STAT, "async_query_soc_stat", is_response=True),
    _case(
        SERVICE_QUERY_CARBON_STAT,
        "async_query_carbon_stat",
        extra={SERVICE_FIELD_DEVICE_SN: _TEXT},
        is_response=True,
    ),
    _case(SERVICE_QUERY_PROFIT_STAT, "async_query_profit_stat", is_response=True),
    _case(
        SERVICE_QUERY_BOX_STAT,
        "async_query_box_stat",
        extra={
            SERVICE_FIELD_DEVICE_SN: _TEXT,
            SERVICE_FIELD_DATE_TYPE: _DATE_TYPE,
            SERVICE_FIELD_BEGIN_DATE: "2026-01-01",
            SERVICE_FIELD_END_DATE: "2026-01-02",
        },
        is_response=True,
    ),
    _case(SERVICE_CHECK_APP_VERSION, "async_check_app_version", is_response=True),
    _case(SERVICE_LIST_BANNERS, "async_list_banners", is_response=True),
    _case(
        SERVICE_SUBMIT_FEEDBACK,
        "async_submit_feedback",
        extra={SERVICE_FIELD_CONTACT_INFO: _TEXT, SERVICE_FIELD_CONTENT: _TEXT},
    ),
    _case(SERVICE_LIST_FAQS, "async_list_faqs", is_response=True),
    _case(SERVICE_LIST_FAQ_ANSWERS, "async_list_faq_answers", is_response=True),
    _case(
        SERVICE_CHECK_PRIVACY_UPDATE,
        "async_check_privacy_update",
        is_response=True,
    ),
    _case(
        SERVICE_AGREE_PRIVACY_CONSENT,
        "async_agree_privacy_consent",
        extra={SERVICE_FIELD_PENDING_AGREE_VERSION_IDS: [1]},
    ),
    _case(
        SERVICE_GET_PRODUCT_INSTRUCTION,
        "async_get_product_instruction",
        extra={SERVICE_FIELD_DEVICE_SN: _TEXT},
        is_response=True,
    ),
    _case(SERVICE_GET_USER_INFO, "async_get_user_info", is_response=True),
    _case(
        SERVICE_INSERT_ELECTRICITY_STRATEGY,
        "async_insert_electricity_strategy",
        extra={SERVICE_FIELD_BODY: {"k": 1}},
        has_auth=False,
    ),
    _case(
        SERVICE_UPDATE_ELECTRICITY_STRATEGY,
        "async_update_electricity_strategy",
        extra={SERVICE_FIELD_BODY: {"k": 1}},
        has_auth=False,
    ),
    _case(
        SERVICE_DELETE_ELECTRICITY_STRATEGY,
        "async_delete_electricity_strategy",
        extra={SERVICE_FIELD_BODY: {"k": 1}},
        has_auth=False,
    ),
    _case(
        SERVICE_QUERY_ELECTRICITY_STRATEGY,
        "async_query_electricity_strategy",
        has_auth=False,
    ),
    _case(
        SERVICE_SET_DEVICE_NICKNAME,
        "async_set_device_nickname",
        extra={SERVICE_FIELD_NICKNAME: _TEXT},
    ),
    _case(
        SERVICE_SET_ACCOUNT_NICKNAME,
        "async_update_user_info",
        extra={SERVICE_FIELD_ACCOUNT_NICKNAME: _TEXT},
    ),
    _case(SERVICE_UNBIND_DEVICE, "async_unbind_device"),
    _case(
        SERVICE_BIND_DEVICE,
        "async_bind_device",
        extra={
            SERVICE_FIELD_TARGET_DEV_ID: _NUM_ID,
            SERVICE_FIELD_BIND_KEY: _TEXT,
            SERVICE_FIELD_GUID: _TEXT,
            SERVICE_FIELD_TIMEZONE_OFFSET: 0,
        },
    ),
    _case(
        SERVICE_BIND_SMART_PART,
        "async_bind_smart_part",
        extra={SERVICE_FIELD_ACCESSORY_SN: _TEXT},
        portable=True,
    ),
    _case(
        SERVICE_UNBIND_SMART_PART,
        "async_unbind_smart_part",
        extra={SERVICE_FIELD_ACCESSORY_SN: _TEXT},
        portable=True,
    ),
    _case(
        SERVICE_GET_SMART_SCHEDULE_PREDICTION,
        "async_get_smart_schedule_prediction",
        is_response=True,
        portable=True,
    ),
    _case(
        SERVICE_UNBIND_ACCESSORIES,
        "async_unbind_accessories",
        extra={SERVICE_FIELD_BIND_IDS: [_NUM_ID]},
        is_response=True,
    ),
    _case(
        SERVICE_SET_AC_NICKNAME,
        "async_set_ac_nickname",
        extra={SERVICE_FIELD_AC_PORT: 1, SERVICE_FIELD_NICKNAME: _TEXT},
    ),
    _case(
        SERVICE_REPORT_DEVICE_TIMEZONE,
        "async_report_device_timezone",
        extra={
            SERVICE_FIELD_ZONE_ID: "Europe/Berlin",
            SERVICE_FIELD_TIMEZONE_OFFSET: 120,
        },
    ),
    _case(
        SERVICE_ACCEPT_SHARED_DEVICE,
        "async_accept_shared_device",
        extra={
            SERVICE_FIELD_TARGET_DEV_ID: _NUM_ID,
            SERVICE_FIELD_QR_CODE_ID: _TEXT,
        },
    ),
    _case(SERVICE_GET_SHELLY_AUTH_URL, "async_get_shelly_auth_url", is_response=True),
    _case(SERVICE_LIST_SHELLY_DEVICES, "async_get_shelly_devices", is_response=True),
    _case(
        SERVICE_UNBIND_SHELLY_DEVICE,
        "async_unbind_shelly_device",
        extra={
            SERVICE_FIELD_BINDING_ID: _TEXT,
            SERVICE_FIELD_SHELLY_DEVICE_ID: _TEXT,
        },
    ),
    _case(SERVICE_UNBIND_SHELLY_ACCOUNT, "async_unbind_shelly_account"),
    _case(
        SERVICE_LIST_SHELLY_BINDING_FAILURES,
        "async_get_shelly_binding_failures",
        is_response=True,
    ),
    _case(
        SERVICE_LIST_SHARED_DEVICES,
        "async_list_shared_devices",
        is_response=True,
    ),
    _case(
        SERVICE_LIST_SHARED_MANAGERS,
        "async_list_shared_managers",
        extra={SERVICE_FIELD_BIND_USER_ID: _TEXT},
        is_response=True,
    ),
    _case(
        SERVICE_REMOVE_SHARED_ACCESS,
        "async_remove_shared_access",
        extra={SERVICE_FIELD_BIND_USER_ID: _TEXT},
    ),
    _case(
        SERVICE_REMOVE_ALL_SHARED_ACCESS,
        "async_remove_all_shared_access",
        extra={SERVICE_FIELD_BIND_USER_ID: _TEXT},
    ),
)

_PORTABLE_CASES = tuple(case for case in _CASES if case.portable)
_AUTH_CASES = tuple(case for case in _CASES if case.has_auth)


def _payload(case: _HandlerCase) -> dict[str, object]:
    return {SERVICE_FIELD_DEVICE_ID: _DEVICE_ID, **case.extra}


def _coordinator(
    method: str,
    *,
    result: object = _SENTINEL,
    error: BaseException | None = None,
    portable: bool = False,
) -> SimpleNamespace:
    payload = (
        {PAYLOAD_DEVICE: {PAYLOAD_DISCOVERY_SOURCE: DISCOVERY_SOURCE_LEGACY_BIND_LIST}}
        if portable
        else {}
    )
    coordinator = SimpleNamespace(data={_DEVICE_ID: payload})
    method_mock = (
        AsyncMock(side_effect=error)
        if error is not None
        else AsyncMock(return_value=result)
    )
    setattr(coordinator, method, method_mock)
    return coordinator


async def _call(
    hass: HomeAssistant,
    case: _HandlerCase,
    coordinator: SimpleNamespace,
) -> object:
    async_setup_services(hass)
    with (
        patch(
            "custom_components.jackery_solarvault.services._loaded_coordinators",
            return_value=[coordinator],
        ),
        patch(
            "custom_components.jackery_solarvault.services._resolve_jackery_device_id",
            return_value=_DEVICE_ID,
        ),
    ):
        return await hass.services.async_call(
            services.DOMAIN,
            case.name,
            _payload(case),
            blocking=True,
            return_response=case.is_response,
        )


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.name)
@pytest.mark.asyncio()
async def test_service_forwards_to_owning_coordinator(
    hass: HomeAssistant,
    case: _HandlerCase,
) -> None:
    """Each action forwards to its coordinator method and returns its envelope."""
    coordinator = _coordinator(case.method)

    response = await _call(hass, case, coordinator)

    getattr(coordinator, case.method).assert_awaited_once()
    if case.is_response:
        assert isinstance(response, dict)
        assert _SENTINEL in response.values()
    else:
        assert response is None


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.name)
@pytest.mark.asyncio()
async def test_service_rejects_unowned_device(
    hass: HomeAssistant,
    case: _HandlerCase,
) -> None:
    """A device no loaded account owns surfaces a translated validation error."""
    async_setup_services(hass)
    with (
        patch(
            "custom_components.jackery_solarvault.services._loaded_coordinators",
            return_value=[],
        ),
        patch(
            "custom_components.jackery_solarvault.services._resolve_jackery_device_id",
            return_value=_DEVICE_ID,
        ),
        pytest.raises(ServiceValidationError),
    ):
        await hass.services.async_call(
            services.DOMAIN,
            case.name,
            _payload(case),
            blocking=True,
            return_response=case.is_response,
        )


@pytest.mark.parametrize("case", _CASES, ids=lambda case: case.name)
@pytest.mark.asyncio()
async def test_service_maps_backend_error_to_validation_error(
    hass: HomeAssistant,
    case: _HandlerCase,
) -> None:
    """A backend ``JackeryError`` becomes a translated ServiceValidationError."""
    coordinator = _coordinator(case.method, error=case.backend_error)

    with pytest.raises(ServiceValidationError):
        await _call(hass, case, coordinator)


@pytest.mark.parametrize("case", _AUTH_CASES, ids=lambda case: case.name)
@pytest.mark.asyncio()
async def test_service_maps_auth_error_to_reauth(
    hass: HomeAssistant,
    case: _HandlerCase,
) -> None:
    """Rejected credentials raise ConfigEntryAuthFailed to trigger reauth."""
    coordinator = _coordinator(case.method, error=JackeryAuthError("nope"))

    with pytest.raises(ConfigEntryAuthFailed):
        await _call(hass, case, coordinator)


_RERAISE_NAMES = frozenset({
    SERVICE_REFRESH_WEATHER_PLAN,
    SERVICE_REFRESH_SUBDEVICES,
    SERVICE_DELETE_STORM_ALERT,
    SERVICE_SET_STORM_ALERT_LOCATION,
    SERVICE_SET_THIRD_PARTY_MQTT_CONFIG,
    SERVICE_QUERY_THIRD_PARTY_MQTT_CONFIG,
    SERVICE_SEND_BLE_COMMAND,
    SERVICE_BIND_SMART_PART,
    SERVICE_UNBIND_SMART_PART,
})
_RERAISE_CASES = tuple(case for case in _CASES if case.name in _RERAISE_NAMES)


@pytest.mark.parametrize("case", _RERAISE_CASES, ids=lambda case: case.name)
@pytest.mark.asyncio()
async def test_service_reraises_config_entry_auth_failed(
    hass: HomeAssistant,
    case: _HandlerCase,
) -> None:
    """A ConfigEntryAuthFailed from the coordinator propagates unwrapped."""
    coordinator = _coordinator(case.method, error=ConfigEntryAuthFailed("reauth"))

    with pytest.raises(ConfigEntryAuthFailed):
        await _call(hass, case, coordinator)


@pytest.mark.parametrize("case", _PORTABLE_CASES, ids=lambda case: case.name)
@pytest.mark.asyncio()
async def test_home_family_service_rejects_portable_device(
    hass: HomeAssistant,
    case: _HandlerCase,
) -> None:
    """Home-family commands refuse Explorer/portable devices before the API call."""
    coordinator = _coordinator(case.method, portable=True)

    with pytest.raises(ServiceValidationError):
        await _call(hass, case, coordinator)

    getattr(coordinator, case.method).assert_not_awaited()


@pytest.mark.asyncio()
async def test_unbind_shelly_device_rejects_unaccepted_request(
    hass: HomeAssistant,
) -> None:
    """A falsy cloud acknowledgement is surfaced as a validation error."""
    case = _case(
        SERVICE_UNBIND_SHELLY_DEVICE,
        "async_unbind_shelly_device",
        extra={
            SERVICE_FIELD_BINDING_ID: _TEXT,
            SERVICE_FIELD_SHELLY_DEVICE_ID: _TEXT,
        },
    )
    coordinator = _coordinator(case.method, result=False)

    with pytest.raises(ServiceValidationError):
        await _call(hass, case, coordinator)


@pytest.mark.asyncio()
async def test_unbind_shelly_account_rejects_unaccepted_request(
    hass: HomeAssistant,
) -> None:
    """A falsy account-unbind acknowledgement raises a validation error."""
    case = _case(SERVICE_UNBIND_SHELLY_ACCOUNT, "async_unbind_shelly_account")
    coordinator = _coordinator(case.method, result=False)

    with pytest.raises(ServiceValidationError):
        await _call(hass, case, coordinator)


@pytest.mark.asyncio()
async def test_send_ble_command_rejects_when_no_session(
    hass: HomeAssistant,
) -> None:
    """A BLE write that reports no active session surfaces a validation error."""
    case = _case(
        SERVICE_SEND_BLE_COMMAND,
        "async_send_ble_command",
        extra={SERVICE_FIELD_CMD: 1, SERVICE_FIELD_BODY: {"k": 1}},
    )
    coordinator = _coordinator(case.method, result=False)

    with pytest.raises(ServiceValidationError):
        await _call(hass, case, coordinator)


# ---------------------------------------------------------------------------
# Rename routes by system id rather than device id.
# ---------------------------------------------------------------------------


def _rename_coordinator(
    *,
    error: BaseException | None = None,
) -> SimpleNamespace:
    method = AsyncMock(side_effect=error) if error is not None else AsyncMock()
    return SimpleNamespace(
        data={_DEVICE_ID: {PAYLOAD_SYSTEM: {"id": _NUM_ID}}},
        async_set_system_name=method,
    )


def _rename_payload() -> dict[str, object]:
    return {SERVICE_FIELD_SYSTEM_ID: _NUM_ID, SERVICE_FIELD_NEW_NAME: "New Name"}


async def _call_rename(
    hass: HomeAssistant,
    coordinator: SimpleNamespace,
) -> None:
    async_setup_services(hass)
    with patch(
        "custom_components.jackery_solarvault.services._loaded_coordinators",
        return_value=[coordinator],
    ):
        await hass.services.async_call(
            services.DOMAIN,
            SERVICE_RENAME_SYSTEM,
            _rename_payload(),
            blocking=True,
        )


@pytest.mark.asyncio()
async def test_rename_forwards_to_system_owner(hass: HomeAssistant) -> None:
    """Rename resolves the owning coordinator by system id and forwards the name."""
    coordinator = _rename_coordinator()

    await _call_rename(hass, coordinator)

    coordinator.async_set_system_name.assert_awaited_once_with(_NUM_ID, "New Name")


@pytest.mark.asyncio()
async def test_rename_rejects_unowned_system(hass: HomeAssistant) -> None:
    """A system id no account owns surfaces a translated validation error."""
    async_setup_services(hass)
    with (
        patch(
            "custom_components.jackery_solarvault.services._loaded_coordinators",
            return_value=[],
        ),
        pytest.raises(ServiceValidationError),
    ):
        await hass.services.async_call(
            services.DOMAIN,
            SERVICE_RENAME_SYSTEM,
            _rename_payload(),
            blocking=True,
        )


@pytest.mark.asyncio()
async def test_rename_maps_backend_error_to_validation_error(
    hass: HomeAssistant,
) -> None:
    """A backend rename failure is surfaced as ServiceValidationError."""
    coordinator = _rename_coordinator(error=JackeryError("boom"))

    with pytest.raises(ServiceValidationError):
        await _call_rename(hass, coordinator)


@pytest.mark.asyncio()
async def test_rename_maps_auth_error_to_reauth(hass: HomeAssistant) -> None:
    """Rejected credentials during rename trigger reauth."""
    coordinator = _rename_coordinator(error=JackeryAuthError("nope"))

    with pytest.raises(ConfigEntryAuthFailed):
        await _call_rename(hass, coordinator)


# ---------------------------------------------------------------------------
# Portable-device classification helpers.
# ---------------------------------------------------------------------------


def test_is_portable_device_detects_legacy_bind_list_source() -> None:
    """A legacy-bind-list discovery source marks the payload portable."""
    coordinator = cast(
        "Any",
        SimpleNamespace(
            data={
                _DEVICE_ID: {
                    PAYLOAD_DEVICE: {
                        PAYLOAD_DISCOVERY_SOURCE: DISCOVERY_SOURCE_LEGACY_BIND_LIST,
                    }
                }
            }
        ),
    )

    assert services._is_portable_device(coordinator, _DEVICE_ID) is True  # ruff: ignore[private-member-access]


def test_is_portable_device_rejects_home_payload_evidence() -> None:
    """Home/System-body fields keep a device out of the portable class."""
    coordinator = cast(
        "Any",
        SimpleNamespace(
            data={
                _DEVICE_ID: {
                    PAYLOAD_DEVICE: {
                        PAYLOAD_DISCOVERY_SOURCE: DISCOVERY_SOURCE_LEGACY_BIND_LIST,
                    },
                    "properties": {"batSoc": 80},
                }
            }
        ),
    )

    assert services._is_portable_device(coordinator, _DEVICE_ID) is False  # ruff: ignore[private-member-access]


def test_is_portable_device_defaults_to_false_without_evidence() -> None:
    """An empty payload is treated as a non-portable Home device."""
    coordinator = cast("Any", SimpleNamespace(data={_DEVICE_ID: {}}))

    assert services._is_portable_device(coordinator, _DEVICE_ID) is False  # ruff: ignore[private-member-access]


def test_payload_home_evidence_recognizes_system_body() -> None:
    """A populated system body is recognized as Home-payload evidence."""
    assert services._payload_has_home_payload_evidence(  # ruff: ignore[private-member-access]
        {PAYLOAD_SYSTEM: {"id": "1"}},
    )
    assert not services._payload_has_home_payload_evidence({})  # ruff: ignore[private-member-access]


# ---------------------------------------------------------------------------
# Share QR notification rendering.
# ---------------------------------------------------------------------------


def test_notify_share_qr_code_publishes_scannable_image(hass: HomeAssistant) -> None:
    """A valid qrCodeId renders a data-URI image into a persistent notification."""
    with (
        patch(
            "custom_components.jackery_solarvault.services."
            "_render_share_qr_png_data_uri",
            return_value="data:image/png;base64,AAAA",
        ),
        patch(
            "homeassistant.components.persistent_notification.async_create",
        ) as create,
    ):
        services._notify_share_qr_code(hass, qr_code_id="qr-1", user_id="user-1")  # ruff: ignore[private-member-access]

    create.assert_called_once()


def test_notify_share_qr_code_skips_when_qr_code_missing(
    hass: HomeAssistant,
) -> None:
    """A non-string qrCodeId short-circuits without touching notifications."""
    with patch(
        "homeassistant.components.persistent_notification.async_create",
    ) as create:
        services._notify_share_qr_code(hass, qr_code_id=None, user_id="user-1")  # ruff: ignore[private-member-access]

    create.assert_not_called()


def test_notify_share_qr_code_swallows_render_failure(hass: HomeAssistant) -> None:
    """A rendering failure never propagates out of the best-effort notifier."""
    with patch(
        "custom_components.jackery_solarvault.services._render_share_qr_png_data_uri",
        side_effect=RuntimeError("render boom"),
    ):
        services._notify_share_qr_code(hass, qr_code_id="qr-1", user_id="user-1")  # ruff: ignore[private-member-access]


def test_render_share_qr_png_data_uri_encodes_png() -> None:
    """The QR renderer returns a base64 PNG data URI for the qrCodeId."""
    data_uri = services._render_share_qr_png_data_uri("qr-code-1")  # ruff: ignore[private-member-access]

    assert data_uri.startswith("data:image/png;base64,")


# ---------------------------------------------------------------------------
# get_share_qr_code inspects its coordinator payload, so it is exercised apart
# from the generic table.
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_get_share_qr_code_rejects_unowned_device(
    hass: HomeAssistant,
) -> None:
    """The QR read surfaces a validation error when no account owns the device."""
    async_setup_services(hass)
    with (
        patch(
            "custom_components.jackery_solarvault.services._loaded_coordinators",
            return_value=[],
        ),
        patch(
            "custom_components.jackery_solarvault.services._resolve_jackery_device_id",
            return_value=_DEVICE_ID,
        ),
        pytest.raises(ServiceValidationError),
    ):
        await hass.services.async_call(
            services.DOMAIN,
            SERVICE_GET_SHARE_QR_CODE,
            {SERVICE_FIELD_DEVICE_ID: _DEVICE_ID},
            blocking=True,
            return_response=True,
        )


@pytest.mark.asyncio()
async def test_get_share_qr_code_maps_backend_error_to_validation_error(
    hass: HomeAssistant,
) -> None:
    """A backend failure while reading the QR payload is a validation error."""
    coordinator = SimpleNamespace(
        data={_DEVICE_ID: {}},
        async_get_share_qr_code=AsyncMock(side_effect=JackeryError("boom")),
    )
    async_setup_services(hass)
    with (
        patch(
            "custom_components.jackery_solarvault.services._loaded_coordinators",
            return_value=[coordinator],
        ),
        patch(
            "custom_components.jackery_solarvault.services._resolve_jackery_device_id",
            return_value=_DEVICE_ID,
        ),
        pytest.raises(ServiceValidationError),
    ):
        await hass.services.async_call(
            services.DOMAIN,
            SERVICE_GET_SHARE_QR_CODE,
            {SERVICE_FIELD_DEVICE_ID: _DEVICE_ID},
            blocking=True,
            return_response=True,
        )


@pytest.mark.asyncio()
async def test_get_share_qr_code_maps_auth_error_to_reauth(
    hass: HomeAssistant,
) -> None:
    """Rejected credentials while reading the QR payload trigger reauth."""
    coordinator = SimpleNamespace(
        data={_DEVICE_ID: {}},
        async_get_share_qr_code=AsyncMock(side_effect=JackeryAuthError("nope")),
    )
    async_setup_services(hass)
    with (
        patch(
            "custom_components.jackery_solarvault.services._loaded_coordinators",
            return_value=[coordinator],
        ),
        patch(
            "custom_components.jackery_solarvault.services._resolve_jackery_device_id",
            return_value=_DEVICE_ID,
        ),
        pytest.raises(ConfigEntryAuthFailed),
    ):
        await hass.services.async_call(
            services.DOMAIN,
            SERVICE_GET_SHARE_QR_CODE,
            {SERVICE_FIELD_DEVICE_ID: _DEVICE_ID},
            blocking=True,
            return_response=True,
        )


# ---------------------------------------------------------------------------
# Parser helpers with branches the dispatch tests do not reach.
# ---------------------------------------------------------------------------


def test_tou_tasks_parser_unwraps_object_and_validates_items() -> None:
    """TOU parsing accepts a wrapping object and rejects non-object tasks."""
    assert services._tou_tasks_from_service(  # ruff: ignore[private-member-access]
        '{"tasks": [{"slot": 1}]}',
        _DEVICE_ID,
    ) == [{"slot": 1}]
    assert services._tou_tasks_from_service([{"slot": 2}], _DEVICE_ID) == [{"slot": 2}]  # ruff: ignore[private-member-access]

    for raw in ('{"tasks": 5}', "{not json", [5]):
        with pytest.raises(ServiceValidationError):
            services._tou_tasks_from_service(raw, _DEVICE_ID)  # ruff: ignore[private-member-access]


def test_reject_json_constant_rejects_non_standard_tokens() -> None:
    """Non-standard JSON constants (NaN/Infinity) are rejected during parsing."""
    with pytest.raises(ValueError, match="invalid JSON constant"):
        services._reject_json_constant("NaN")  # ruff: ignore[private-member-access]


def test_resolve_jackery_device_id_maps_registry_identifier() -> None:
    """A registry device with a Jackery identifier resolves to the cloud id."""
    device = SimpleNamespace(
        via_device_id=None,
        identifiers={(services.DOMAIN, "cloud-42")},
    )
    registry = SimpleNamespace(async_get=lambda raw: device)
    with patch(
        "custom_components.jackery_solarvault.services.dr.async_get",
        return_value=registry,
    ):
        resolved = services._resolve_jackery_device_id(  # ruff: ignore[private-member-access]
            cast("HomeAssistant", object()),
            "ha-uuid",
        )

    assert resolved == "cloud-42"


def test_resolve_jackery_device_id_follows_accessory_via_parent() -> None:
    """An accessory row resolves through its parent SolarVault device."""
    parent = SimpleNamespace(
        via_device_id=None,
        identifiers={(services.DOMAIN, "parent-7")},
    )
    accessory = SimpleNamespace(
        via_device_id="parent-id",
        identifiers={(services.DOMAIN, "accessory-9")},
    )

    def _async_get(raw: str) -> object:
        return accessory if raw == "ha-uuid" else parent

    registry = SimpleNamespace(async_get=_async_get)
    with patch(
        "custom_components.jackery_solarvault.services.dr.async_get",
        return_value=registry,
    ):
        resolved = services._resolve_jackery_device_id(  # ruff: ignore[private-member-access]
            cast("HomeAssistant", object()),
            "ha-uuid",
        )

    assert resolved == "parent-7"


def test_resolve_jackery_device_id_returns_raw_without_matching_identifier() -> None:
    """A known device lacking a Jackery identifier falls back to the raw id."""
    device = SimpleNamespace(
        via_device_id="missing-parent",
        identifiers={("other_domain", "x")},
    )

    def _async_get(raw: str) -> object | None:
        return device if raw == "ha-uuid" else None

    registry = SimpleNamespace(async_get=_async_get)
    with patch(
        "custom_components.jackery_solarvault.services.dr.async_get",
        return_value=registry,
    ):
        resolved = services._resolve_jackery_device_id(  # ruff: ignore[private-member-access]
            cast("HomeAssistant", object()),
            "ha-uuid",
        )

    assert resolved == "ha-uuid"


def test_payload_home_evidence_recognizes_http_properties() -> None:
    """Home-body fields inside the http_properties section count as evidence."""
    assert services._payload_has_home_payload_evidence(  # ruff: ignore[private-member-access]
        {"http_properties": {"pvPw": 120}},
    )


def test_json_native_value_normalizes_nested_containers() -> None:
    """JSON-native normalization passes finite scalars and nested containers."""
    assert services._json_native_value({"a": [1, 2.5, "x", None, True]}) == {  # ruff: ignore[private-member-access]
        "a": [1, 2.5, "x", None, True]
    }

    with pytest.raises(ValueError, match="finite"):
        services._json_native_value(float("inf"))  # ruff: ignore[private-member-access]


def test_resolve_jackery_device_id_returns_raw_when_unknown() -> None:
    """An id absent from the registry passes through unchanged (legacy path)."""
    registry = SimpleNamespace(async_get=lambda raw: None)
    with patch(
        "custom_components.jackery_solarvault.services.dr.async_get",
        return_value=registry,
    ):
        resolved = services._resolve_jackery_device_id(  # ruff: ignore[private-member-access]
            cast("HomeAssistant", object()),
            "legacy-123",
        )

    assert resolved == "legacy-123"
