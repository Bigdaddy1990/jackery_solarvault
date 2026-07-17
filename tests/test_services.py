"""Behavior tests for Jackery SolarVault Home Assistant service actions."""

# ruff:file-ignore[private-member-access]

import math
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, patch

import pytest
import voluptuous as vol

from custom_components.jackery_solarvault import services
from custom_components.jackery_solarvault.const import (
    DOMAIN,
    FIELD_QR_CODE_ID,
    FIELD_USER_ID,
    SERVICE_FIELD_ACK_TIMEOUT,
    SERVICE_FIELD_BIND_USER_ID,
    SERVICE_FIELD_DEVICE_ID,
    SERVICE_FIELD_ENABLE,
    SERVICE_FIELD_LEVEL,
    SERVICE_FIELD_PORT,
    SERVICE_GET_SHARE_QR_CODE,
    SERVICE_LIST_SHARED_DEVICES,
    SERVICE_LIST_SHARED_MANAGERS,
    SERVICE_REMOVE_ALL_SHARED_ACCESS,
    SERVICE_REMOVE_SHARED_ACCESS,
    SERVICE_RESPONSE_QR_CODE_ID,
    SERVICE_RESPONSE_USER_ID,
)
from custom_components.jackery_solarvault.services import async_setup_services
from homeassistant.exceptions import ServiceValidationError

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_DEVICE_ID = "dev-1"
_FLOAT_VALUE = 2.5
_WHOLE_FLOAT = 7.0
_INT_VALUE = 7
_MAX_PORT = 65535
_OVER_MAX_PORT = 65536
_MAX_NAME_LENGTH = 64
_SHARE_LEVEL = 2


def _coordinator_for(device_id: str, api: object) -> SimpleNamespace:
    """Build the minimum coordinator surface consumed by service routing."""
    return SimpleNamespace(
        api=api,
        data={device_id: {}},
        async_request_refresh=AsyncMock(),
    )


@pytest.mark.asyncio()
async def test_get_share_qr_code_service_returns_account_payload(
    hass: HomeAssistant,
) -> None:
    """The QR service returns the app QR fields through HA's response path."""
    api = SimpleNamespace(
        async_get_qr_code=AsyncMock(
            return_value={FIELD_QR_CODE_ID: "qr-123", FIELD_USER_ID: "user-123"}
        )
    )
    coordinator = _coordinator_for("dev-1", api)
    async_setup_services(hass)

    with (
        patch(
            "custom_components.jackery_solarvault.services._loaded_coordinators",
            return_value=[coordinator],
        ),
        patch(
            "custom_components.jackery_solarvault.services._notify_share_qr_code",
        ) as notify_qr,
    ):
        response = await hass.services.async_call(
            DOMAIN,
            SERVICE_GET_SHARE_QR_CODE,
            {SERVICE_FIELD_DEVICE_ID: "dev-1"},
            blocking=True,
            return_response=True,
        )

    assert response == {
        SERVICE_RESPONSE_QR_CODE_ID: "qr-123",
        SERVICE_RESPONSE_USER_ID: "user-123",
    }
    api.async_get_qr_code.assert_awaited_once_with()
    notify_qr.assert_called_once_with(
        hass,
        qr_code_id="qr-123",
        user_id="user-123",
    )


@pytest.mark.asyncio()
async def test_list_shared_devices_service_routes_to_owning_coordinator(
    hass: HomeAssistant,
) -> None:
    """Account-scoped sharing reads still dispatch by selected device id."""
    api_a = SimpleNamespace(async_get_device_shared_list=AsyncMock(return_value=[]))
    api_b = SimpleNamespace(
        async_get_device_shared_list=AsyncMock(
            return_value=[{"deviceId": "shared-1", "role": "manager"}]
        )
    )
    async_setup_services(hass)

    with patch(
        "custom_components.jackery_solarvault.services._loaded_coordinators",
        return_value=[
            _coordinator_for("dev-a", api_a),
            _coordinator_for("dev-b", api_b),
        ],
    ):
        response = await hass.services.async_call(
            DOMAIN,
            SERVICE_LIST_SHARED_DEVICES,
            {SERVICE_FIELD_DEVICE_ID: "dev-b"},
            blocking=True,
            return_response=True,
        )

    assert response == {"devices": [{"deviceId": "shared-1", "role": "manager"}]}
    api_a.async_get_device_shared_list.assert_not_called()
    api_b.async_get_device_shared_list.assert_awaited_once_with()


@pytest.mark.asyncio()
async def test_remove_shared_access_service_refreshes_after_cloud_success(
    hass: HomeAssistant,
) -> None:
    """Removing shared access refreshes HA only after the cloud call succeeds."""
    api = SimpleNamespace(async_remove_shared_access=AsyncMock(return_value={}))
    coordinator = _coordinator_for("dev-1", api)
    async_setup_services(hass)

    with patch(
        "custom_components.jackery_solarvault.services._loaded_coordinators",
        return_value=[coordinator],
    ):
        response = await hass.services.async_call(
            DOMAIN,
            SERVICE_REMOVE_SHARED_ACCESS,
            {
                SERVICE_FIELD_DEVICE_ID: "dev-1",
                SERVICE_FIELD_BIND_USER_ID: "user-456",
            },
            blocking=True,
        )

    assert response is None
    api.async_remove_shared_access.assert_awaited_once_with(
        bind_user_id="user-456",
        device_id="dev-1",
    )
    coordinator.async_request_refresh.assert_awaited_once_with()


@pytest.mark.asyncio()
async def test_list_shared_managers_service_routes_to_owning_coordinator(
    hass: HomeAssistant,
) -> None:
    """Shared manager reads use the selected device account and parsed level."""
    api = SimpleNamespace(
        async_get_device_shared_managers=AsyncMock(
            return_value=[{"userId": "manager-1"}]
        )
    )
    async_setup_services(hass)

    with patch(
        "custom_components.jackery_solarvault.services._loaded_coordinators",
        return_value=[_coordinator_for("dev-1", api)],
    ):
        response = await hass.services.async_call(
            DOMAIN,
            SERVICE_LIST_SHARED_MANAGERS,
            {
                SERVICE_FIELD_DEVICE_ID: "dev-1",
                SERVICE_FIELD_BIND_USER_ID: "user-456",
                SERVICE_FIELD_LEVEL: _SHARE_LEVEL,
            },
            blocking=True,
            return_response=True,
        )

    assert response == {"managers": [{"userId": "manager-1"}]}
    api.async_get_device_shared_managers.assert_awaited_once_with(
        bind_user_id="user-456",
        level=_SHARE_LEVEL,
    )


@pytest.mark.asyncio()
async def test_remove_all_shared_access_service_refreshes_after_cloud_success(
    hass: HomeAssistant,
) -> None:
    """Bulk shared-access removal refreshes only after the cloud call succeeds."""
    api = SimpleNamespace(async_remove_all_shared_access=AsyncMock(return_value={}))
    coordinator = _coordinator_for("dev-1", api)
    async_setup_services(hass)

    with patch(
        "custom_components.jackery_solarvault.services._loaded_coordinators",
        return_value=[coordinator],
    ):
        response = await hass.services.async_call(
            DOMAIN,
            SERVICE_REMOVE_ALL_SHARED_ACCESS,
            {
                SERVICE_FIELD_DEVICE_ID: "dev-1",
                SERVICE_FIELD_BIND_USER_ID: "user-456",
                SERVICE_FIELD_LEVEL: _SHARE_LEVEL,
            },
            blocking=True,
        )

    assert response is None
    api.async_remove_all_shared_access.assert_awaited_once_with(
        bind_user_id="user-456",
        level=_SHARE_LEVEL,
    )
    coordinator.async_request_refresh.assert_awaited_once_with()


def test_coerce_service_int_accepts_whole_numbers() -> None:
    """Service integer coercion keeps whole numbers but rejects bools/fractions."""
    assert services._coerce_service_int(_INT_VALUE) == _INT_VALUE
    assert services._coerce_service_int(_WHOLE_FLOAT) == _INT_VALUE
    assert services._coerce_service_int(" +7 ") == _INT_VALUE

    for raw in (True, _FLOAT_VALUE, "", "7.1", object()):
        with pytest.raises(vol.Invalid):
            services._coerce_service_int(raw)


def test_coerce_service_float_requires_finite_number() -> None:
    """Service float coercion rejects bools and non-finite values."""
    assert services._coerce_service_float("2.5") == _FLOAT_VALUE

    for raw in (False, "nan", math.inf, object()):
        with pytest.raises(vol.Invalid):
            services._coerce_service_float(raw)


def test_service_validation_error_includes_common_placeholders() -> None:
    """Translated service errors keep device id, raw error, and extra fields."""
    err = services._service_validation_error(
        "test_failed",
        device_id=_DEVICE_ID,
        error="bad",
        extra_placeholders={"extra": "value"},
    )

    assert err.translation_domain == DOMAIN
    assert err.translation_key == "test_failed"
    assert err.translation_placeholders == {
        "device_id": _DEVICE_ID,
        "error": "bad",
        "extra": "value",
    }


def test_rename_service_field_parsers_trim_and_validate() -> None:
    """Rename helpers accept canonical text and reject invalid service data."""
    assert services._rename_system_id_from_service(" 123 ") == "123"
    assert services._rename_name_from_service(" SolarVault ", "123") == "SolarVault"

    for raw in ("", "abc", 123):
        with pytest.raises(ServiceValidationError):
            services._rename_system_id_from_service(raw)

    for raw in ("", 123, "x" * (_MAX_NAME_LENGTH + 1)):
        with pytest.raises(ServiceValidationError):
            services._rename_name_from_service(raw, "123")


def test_storm_alert_parser_requires_non_empty_text() -> None:
    """Storm alert ids are stripped text values, not arbitrary objects."""
    assert services._storm_alert_id_from_service(" alert-1 ", _DEVICE_ID) == "alert-1"

    for raw in ("", 123):
        with pytest.raises(ServiceValidationError):
            services._storm_alert_id_from_service(raw, _DEVICE_ID)


def test_ble_body_parser_accepts_json_objects_and_rejects_unsafe_values() -> None:
    """BLE service bodies stay strict JSON-native objects."""
    assert services._ble_body_from_service('{"cmd": 1}', _DEVICE_ID) == {"cmd": 1}
    assert services._ble_body_from_service({"items": [None, True]}, _DEVICE_ID) == {
        "items": [None, True]
    }

    invalid_bodies = (
        "[1]",
        "{bad-json",
        "NaN",
        {"bad": math.inf},
        {"bad": object()},
        {1: "not-string-key"},
        object(),
    )
    for raw in invalid_bodies:
        with pytest.raises(ServiceValidationError):
            services._ble_body_from_service(raw, _DEVICE_ID)


def test_service_scalar_parsers_raise_translated_errors() -> None:
    """Shared service scalar parsers report field-specific validation failures."""
    assert (
        services._service_bool(
            "true",
            field_name=SERVICE_FIELD_ENABLE,
            translation_key="set_third_party_mqtt_config_failed",
            device_id=_DEVICE_ID,
        )
        is True
    )
    assert (
        services._service_required_text(
            " broker ",
            field_name="ip",
            translation_key="set_third_party_mqtt_config_failed",
            device_id=_DEVICE_ID,
            max_length=_MAX_NAME_LENGTH,
        )
        == "broker"
    )
    assert not services._service_optional_text(
        None,
        field_name="token",
        translation_key="set_third_party_mqtt_config_failed",
        device_id=_DEVICE_ID,
        max_length=_MAX_NAME_LENGTH,
    )
    assert (
        services._service_optional_text(
            "user",
            field_name="username",
            translation_key="set_third_party_mqtt_config_failed",
            device_id=_DEVICE_ID,
            max_length=_MAX_NAME_LENGTH,
        )
        == "user"
    )
    assert (
        services._service_int(
            _MAX_PORT,
            field_name=SERVICE_FIELD_PORT,
            translation_key="set_third_party_mqtt_config_failed",
            device_id=_DEVICE_ID,
            min_value=1,
            max_value=_MAX_PORT,
        )
        == _MAX_PORT
    )
    assert (
        services._service_float(
            "2.5",
            field_name=SERVICE_FIELD_ACK_TIMEOUT,
            translation_key="send_ble_command_failed",
            device_id=_DEVICE_ID,
            min_value=0.5,
            max_value=60.0,
        )
        == _FLOAT_VALUE
    )

    invalid_calls = (
        lambda: services._service_bool(
            "maybe",
            field_name=SERVICE_FIELD_ENABLE,
            translation_key="set_third_party_mqtt_config_failed",
            device_id=_DEVICE_ID,
        ),
        lambda: services._service_required_text(
            "",
            field_name="ip",
            translation_key="set_third_party_mqtt_config_failed",
            device_id=_DEVICE_ID,
            max_length=_MAX_NAME_LENGTH,
        ),
        lambda: services._service_required_text(
            object(),
            field_name="ip",
            translation_key="set_third_party_mqtt_config_failed",
            device_id=_DEVICE_ID,
            max_length=_MAX_NAME_LENGTH,
        ),
        lambda: services._service_required_text(
            "x" * (_MAX_NAME_LENGTH + 1),
            field_name="ip",
            translation_key="set_third_party_mqtt_config_failed",
            device_id=_DEVICE_ID,
            max_length=_MAX_NAME_LENGTH,
        ),
        lambda: services._service_optional_text(
            object(),
            field_name="token",
            translation_key="set_third_party_mqtt_config_failed",
            device_id=_DEVICE_ID,
            max_length=_MAX_NAME_LENGTH,
        ),
        lambda: services._service_optional_text(
            "x" * (_MAX_NAME_LENGTH + 1),
            field_name="token",
            translation_key="set_third_party_mqtt_config_failed",
            device_id=_DEVICE_ID,
            max_length=_MAX_NAME_LENGTH,
        ),
        lambda: services._service_int(
            "bad",
            field_name=SERVICE_FIELD_PORT,
            translation_key="set_third_party_mqtt_config_failed",
            device_id=_DEVICE_ID,
            min_value=1,
            max_value=_MAX_PORT,
        ),
        lambda: services._service_int(
            _OVER_MAX_PORT,
            field_name=SERVICE_FIELD_PORT,
            translation_key="set_third_party_mqtt_config_failed",
            device_id=_DEVICE_ID,
            min_value=1,
            max_value=_MAX_PORT,
        ),
        lambda: services._service_float(
            "nan",
            field_name=SERVICE_FIELD_ACK_TIMEOUT,
            translation_key="send_ble_command_failed",
            device_id=_DEVICE_ID,
            min_value=0.5,
            max_value=60.0,
        ),
        lambda: services._service_float(
            100.0,
            field_name=SERVICE_FIELD_ACK_TIMEOUT,
            translation_key="send_ble_command_failed",
            device_id=_DEVICE_ID,
            min_value=0.5,
            max_value=60.0,
        ),
    )
    for call in invalid_calls:
        with pytest.raises(ServiceValidationError):
            call()


def test_device_id_parser_strips_and_maps_registry_id() -> None:
    """Direct service ids are stripped before HA device-registry resolution."""
    hass_obj = cast("HomeAssistant", object())
    with patch(
        "custom_components.jackery_solarvault.services._resolve_jackery_device_id",
        return_value="mapped-device",
    ) as resolver:
        device_id = services._device_id_from_service(
            hass_obj,
            " dev-1 ",
            translation_key="refresh_weather_plan_failed",
        )

    assert device_id == "mapped-device"
    resolver.assert_called_once_with(hass_obj, "dev-1")

    for raw in ("", object()):
        with pytest.raises(ServiceValidationError):
            services._device_id_from_service(
                hass_obj,
                raw,
                translation_key="refresh_weather_plan_failed",
            )


def test_coordinator_lookup_helpers_route_by_device_and_system() -> None:
    """Coordinator routing selects the account that owns the requested object."""
    coordinator_a = SimpleNamespace(
        data={"dev-a": {"system": {"id": "sys-a", "systemId": "legacy-a"}}}
    )
    coordinator_b = SimpleNamespace(
        data={"dev-b": {"system": {"id": "sys-b", "systemId": "legacy-b"}}}
    )

    with patch(
        "custom_components.jackery_solarvault.services._loaded_coordinators",
        return_value=[coordinator_a, coordinator_b],
    ):
        hass_obj = cast("HomeAssistant", object())
        assert (
            cast("Any", services._coordinator_for_device(hass_obj, "dev-b"))
            is coordinator_b
        )
        assert services._coordinator_for_device(hass_obj, "missing") is None
        assert (
            cast("Any", services._coordinator_for_system(hass_obj, "sys-a"))
            is coordinator_a
        )
        assert (
            cast("Any", services._coordinator_for_system(hass_obj, "legacy-b"))
            is coordinator_b
        )
        assert services._coordinator_for_system(hass_obj, "missing") is None


def test_loaded_coordinators_filters_runtime_data_by_type() -> None:
    """Only Jackery coordinator runtime data is returned from loaded entries."""

    class FakeCoordinator:
        pass

    fake_coordinator = FakeCoordinator()
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(
            async_loaded_entries=lambda domain: [
                SimpleNamespace(runtime_data=fake_coordinator),
                SimpleNamespace(runtime_data=object()),
            ]
        )
    )

    with patch(
        "custom_components.jackery_solarvault.services.JackerySolarVaultCoordinator",
        FakeCoordinator,
    ):
        assert cast(
            "Any",
            services._loaded_coordinators(cast("HomeAssistant", hass)),
        ) == [fake_coordinator]


def test_strip_jackery_subdevice_suffix_keeps_parent_id() -> None:
    """Subdevice service ids are normalized back to the parent numeric id."""
    assert services._strip_jackery_subdevice_suffix("12345_socket_1") == "12345"
    assert services._strip_jackery_subdevice_suffix("abc_socket_1") == "abc_socket_1"
