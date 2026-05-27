"""Unit tests for integration service helpers."""

from dataclasses import dataclass
import sys

from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
import pytest
import voluptuous as vol

from custom_components.jackery_solarvault import services
from custom_components.jackery_solarvault.api import JackeryAuthError
from custom_components.jackery_solarvault.const import (
    DOMAIN,
    SERVICE_FIELD_ACK_TIMEOUT,
    SERVICE_FIELD_ALERT_ID,
    SERVICE_FIELD_BODY,
    SERVICE_FIELD_CMD,
    SERVICE_FIELD_DEVICE_ID,
    SERVICE_FIELD_ENABLE,
    SERVICE_FIELD_FLAGS,
    SERVICE_FIELD_IP,
    SERVICE_FIELD_NEW_NAME,
    SERVICE_FIELD_PASSWORD,
    SERVICE_FIELD_PORT,
    SERVICE_FIELD_SYSTEM_ID,
    SERVICE_FIELD_TOKEN,
    SERVICE_FIELD_USERNAME,
    SERVICE_FIELD_WAIT_FOR_ACK,
)


@dataclass(slots=True)
class _Device:
    identifiers: set[tuple[str, str]]
    via_device_id: str | None = None


class _Registry:
    def __init__(self, devices: dict[str, _Device]) -> None:
        self._devices = devices

    def async_get(self, device_id: str) -> _Device | None:
        return self._devices.get(device_id)


@dataclass(slots=True)
class _Call:
    data: dict[str, object]


class _OverflowFloat:
    def __float__(self) -> float:
        raise OverflowError("too large")


def test_service_integer_parser_rejects_oversized_digit_strings() -> None:
    """Oversized digit strings must become HA validation errors."""
    old_limit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(640)
    try:
        with pytest.raises(vol.Invalid):
            services._coerce_service_int("9" * 700)
    finally:
        sys.set_int_max_str_digits(old_limit)


class _Api:
    def __init__(self, result: bool) -> None:
        self._result = result
        self.calls: list[tuple[str, str]] = []

    async def async_set_system_name(self, system_id: str, new_name: str) -> bool:
        self.calls.append((system_id, new_name))
        return self._result


class _Coordinator:
    def __init__(self, api_result: bool) -> None:
        self.api = _Api(api_result)
        self.refreshed = False

    async def async_request_refresh(self) -> None:
        self.refreshed = True


class _AuthApi:
    async def async_set_system_name(self, system_id: str, new_name: str) -> bool:
        raise JackeryAuthError("invalid token")


class _AuthCoordinator:
    api = _AuthApi()

    async def async_request_refresh(self) -> None:
        raise AssertionError("auth failures must not refresh")


def test_resolve_jackery_device_id_follows_subdevice_parent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Device-picker accessory selections resolve to the parent SolarVault."""
    registry = _Registry({
        "solarvault-ha-id": _Device({(DOMAIN, "573702884982521856")}),
        "smart-plug-ha-id": _Device(
            {(DOMAIN, "573702884982521856_smart_plug_1")},
            via_device_id="solarvault-ha-id",
        ),
    })

    monkeypatch.setattr(services.dr, "async_get", lambda _hass: registry)

    assert (
        services._resolve_jackery_device_id(object(), "smart-plug-ha-id")
        == "573702884982521856"
    )
    assert (
        services._resolve_jackery_device_id(object(), "solarvault-ha-id")
        == "573702884982521856"
    )
    assert (
        services._resolve_jackery_device_id(object(), "573702884982521856")
        == "573702884982521856"
    )


async def test_rename_service_rejects_false_api_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rename service must not report success when the API returns false."""
    coordinator = _Coordinator(api_result=False)
    monkeypatch.setattr(
        services,
        "_coordinator_for_system",
        lambda _hass, _system_id: coordinator,
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_rename(
            object(),
            _Call({
                SERVICE_FIELD_SYSTEM_ID: "123",
                SERVICE_FIELD_NEW_NAME: "SolarVault",
            }),
        )

    assert err.value.translation_key == "rename_system_failed"
    assert coordinator.api.calls == [("123", "SolarVault")]
    assert coordinator.refreshed is False


async def test_rename_service_reauth_on_auth_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Rename service must preserve auth failures so HA can open reauth."""
    monkeypatch.setattr(
        services,
        "_coordinator_for_system",
        lambda _hass, _system_id: _AuthCoordinator(),
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await services._async_handle_rename(
            object(),
            _Call({
                SERVICE_FIELD_SYSTEM_ID: "123",
                SERVICE_FIELD_NEW_NAME: "SolarVault",
            }),
        )


@pytest.mark.parametrize(
    ("system_id", "expected_system_id", "expected_error"),
    [
        ("  ", "", "system_id must not be empty"),
        ("abc", "abc", "system_id must be numeric"),
        (None, "", "system_id must be text"),
    ],
)
async def test_rename_service_rejects_direct_invalid_system_id(
    monkeypatch: pytest.MonkeyPatch,
    system_id: object,
    expected_system_id: str,
    expected_error: str,
) -> None:
    """Direct rename handler calls must keep the schema system_id constraint."""

    def _fail_coordinator_lookup(_hass: object, _system_id: str) -> object:
        raise AssertionError("invalid system_id must stop before coordinator lookup")

    monkeypatch.setattr(services, "_coordinator_for_system", _fail_coordinator_lookup)

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_rename(
            object(),
            _Call({
                SERVICE_FIELD_SYSTEM_ID: system_id,
                SERVICE_FIELD_NEW_NAME: "SolarVault",
            }),
        )

    assert err.value.translation_key == "rename_system_failed"
    assert err.value.translation_placeholders == {
        "system_id": expected_system_id,
        "error": expected_error,
    }


@pytest.mark.parametrize(
    ("new_name", "expected_error"),
    [
        ("  ", "new_name must not be empty"),
        ("x" * 65, "new_name must be at most 64 characters"),
    ],
)
async def test_rename_service_rejects_direct_invalid_new_name(
    monkeypatch: pytest.MonkeyPatch,
    new_name: str,
    expected_error: str,
) -> None:
    """Direct rename handler calls must keep the schema name constraints."""
    coordinator = _Coordinator(api_result=True)
    monkeypatch.setattr(
        services,
        "_coordinator_for_system",
        lambda _hass, _system_id: coordinator,
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_rename(
            object(),
            _Call({
                SERVICE_FIELD_SYSTEM_ID: "123",
                SERVICE_FIELD_NEW_NAME: new_name,
            }),
        )

    assert err.value.translation_key == "rename_system_failed"
    assert err.value.translation_placeholders == {
        "system_id": "123",
        "error": expected_error,
    }
    assert coordinator.api.calls == []
    assert coordinator.refreshed is False


async def test_refresh_weather_plan_service_translates_home_assistant_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MQTT command errors are surfaced through the service translation key."""

    class _FailingCoordinator:
        data = {"dev1": {}}

        async def async_query_weather_plan(self, device_id: str) -> None:
            raise HomeAssistantError(f"MQTT command failed for {device_id}")

    monkeypatch.setattr(
        services,
        "_resolve_jackery_device_id",
        lambda _hass, raw: str(raw),
    )
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _FailingCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_refresh_weather_plan(
            object(),
            _Call({SERVICE_FIELD_DEVICE_ID: "dev1"}),
        )

    assert err.value.translation_key == "refresh_weather_plan_failed"
    assert err.value.translation_placeholders == {
        "device_id": "dev1",
        "error": "MQTT command failed for dev1",
    }


@pytest.mark.parametrize(
    ("device_id", "expected_error"),
    [
        ("  ", "device_id must not be empty"),
        (None, "device_id must be text"),
    ],
)
async def test_refresh_weather_plan_service_rejects_direct_invalid_device_id(
    monkeypatch: pytest.MonkeyPatch,
    device_id: object,
    expected_error: str,
) -> None:
    """Direct device service calls must keep the device_id text constraint."""

    def _fail_resolve(_hass: object, _raw: str) -> str:
        raise AssertionError("invalid device_id must stop before registry lookup")

    monkeypatch.setattr(services, "_resolve_jackery_device_id", _fail_resolve)

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_refresh_weather_plan(
            object(),
            _Call({SERVICE_FIELD_DEVICE_ID: device_id}),
        )

    assert err.value.translation_key == "refresh_weather_plan_failed"
    assert err.value.translation_placeholders == {
        "device_id": "",
        "error": expected_error,
    }


async def test_delete_storm_alert_service_rejects_direct_blank_alert_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct delete handler calls must keep the alert_id non-empty constraint."""

    class _StormAlertCoordinator:
        async def async_delete_storm_alert(self, *args: object) -> None:
            raise AssertionError("blank alert_id must stop before coordinator call")

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _StormAlertCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_delete_storm_alert(
            object(),
            _Call({
                SERVICE_FIELD_DEVICE_ID: "dev1",
                SERVICE_FIELD_ALERT_ID: "  ",
            }),
        )

    assert err.value.translation_key == "delete_storm_alert_failed"
    assert err.value.translation_placeholders == {
        "device_id": "dev1",
        "alert_id": "",
        "error": "alert_id must not be empty",
    }


async def test_set_third_party_mqtt_service_parses_boolean_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct service handler calls must not treat string 'false' as true."""

    class _ThirdPartyCoordinator:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def async_set_third_party_mqtt_config(
            self,
            device_id: str,
            *,
            enable: bool,
            ip: str,
            port: int,
            username: str,
            password: str,
            token: str,
        ) -> None:
            self.calls.append({
                "device_id": device_id,
                "enable": enable,
                "ip": ip,
                "port": port,
                "username": username,
                "password": password,
                "token": token,
            })

    coordinator = _ThirdPartyCoordinator()
    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services, "_coordinator_for_device", lambda _hass, _device_id: coordinator
    )

    await services._async_handle_set_third_party_mqtt_config(
        object(),
        _Call({
            SERVICE_FIELD_DEVICE_ID: "dev1",
            SERVICE_FIELD_ENABLE: "false",
            SERVICE_FIELD_IP: " 192.0.2.10 ",
            SERVICE_FIELD_PORT: "1883",
            SERVICE_FIELD_USERNAME: "user",
            SERVICE_FIELD_PASSWORD: "pass",
            SERVICE_FIELD_TOKEN: "token",
        }),
    )

    assert coordinator.calls == [
        {
            "device_id": "dev1",
            "enable": False,
            "ip": "192.0.2.10",
            "port": 1883,
            "username": "user",
            "password": "pass",
            "token": "token",
        }
    ]


async def test_set_third_party_mqtt_service_keeps_none_credentials_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct service handler calls must not turn optional None fields into text."""

    class _ThirdPartyCoordinator:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def async_set_third_party_mqtt_config(
            self,
            device_id: str,
            *,
            enable: bool,
            ip: str,
            port: int,
            username: str,
            password: str,
            token: str,
        ) -> None:
            self.calls.append({
                "device_id": device_id,
                "enable": enable,
                "ip": ip,
                "port": port,
                "username": username,
                "password": password,
                "token": token,
            })

    coordinator = _ThirdPartyCoordinator()
    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services, "_coordinator_for_device", lambda _hass, _device_id: coordinator
    )

    await services._async_handle_set_third_party_mqtt_config(
        object(),
        _Call({
            SERVICE_FIELD_DEVICE_ID: "dev1",
            SERVICE_FIELD_ENABLE: True,
            SERVICE_FIELD_IP: "192.0.2.10",
            SERVICE_FIELD_PORT: 1883,
            SERVICE_FIELD_USERNAME: None,
            SERVICE_FIELD_PASSWORD: None,
            SERVICE_FIELD_TOKEN: None,
        }),
    )

    assert coordinator.calls == [
        {
            "device_id": "dev1",
            "enable": True,
            "ip": "192.0.2.10",
            "port": 1883,
            "username": "",
            "password": "",
            "token": "",
        }
    ]


async def test_set_third_party_mqtt_service_rejects_direct_non_text_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct service handler calls must not stringify credential objects."""

    class _ThirdPartyCoordinator:
        async def async_set_third_party_mqtt_config(self, *args: object) -> None:
            raise AssertionError(
                "non-text credentials must stop before coordinator call"
            )

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _ThirdPartyCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_set_third_party_mqtt_config(
            object(),
            _Call({
                SERVICE_FIELD_DEVICE_ID: "dev1",
                SERVICE_FIELD_ENABLE: True,
                SERVICE_FIELD_IP: "192.0.2.10",
                SERVICE_FIELD_PORT: 1883,
                SERVICE_FIELD_USERNAME: {"name": "user"},
            }),
        )

    assert err.value.translation_key == "set_third_party_mqtt_config_failed"
    assert err.value.translation_placeholders == {
        "device_id": "dev1",
        "error": "username must be text",
    }


async def test_set_third_party_mqtt_service_preserves_invalid_boolean_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid service booleans should keep their translated field error."""

    class _ThirdPartyCoordinator:
        async def async_set_third_party_mqtt_config(self, *args: object) -> None:
            raise AssertionError("invalid boolean must stop before coordinator call")

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _ThirdPartyCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_set_third_party_mqtt_config(
            object(),
            _Call({
                SERVICE_FIELD_DEVICE_ID: "dev1",
                SERVICE_FIELD_ENABLE: "maybe",
                SERVICE_FIELD_IP: "192.0.2.10",
                SERVICE_FIELD_PORT: 1883,
            }),
        )

    assert err.value.translation_key == "set_third_party_mqtt_config_failed"
    assert err.value.translation_placeholders == {
        "device_id": "dev1",
        "error": "enable must be a boolean",
    }


@pytest.mark.parametrize(
    ("port", "expected_error"),
    [
        (0, "port must be between 1 and 65535"),
        (1883.9, "port must be an integer"),
    ],
)
async def test_set_third_party_mqtt_service_rejects_direct_invalid_port(
    monkeypatch: pytest.MonkeyPatch,
    port: object,
    expected_error: str,
) -> None:
    """Direct service handler calls must keep the schema port constraints."""

    class _ThirdPartyCoordinator:
        async def async_set_third_party_mqtt_config(self, *args: object) -> None:
            raise AssertionError("invalid port must stop before coordinator call")

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _ThirdPartyCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_set_third_party_mqtt_config(
            object(),
            _Call({
                SERVICE_FIELD_DEVICE_ID: "dev1",
                SERVICE_FIELD_ENABLE: True,
                SERVICE_FIELD_IP: "192.0.2.10",
                SERVICE_FIELD_PORT: port,
            }),
        )

    assert err.value.translation_key == "set_third_party_mqtt_config_failed"
    assert err.value.translation_placeholders == {
        "device_id": "dev1",
        "error": expected_error,
    }


@pytest.mark.parametrize(
    ("schema", "extra_data"),
    [
        (services.REFRESH_WEATHER_PLAN_SCHEMA, {}),
        (services.DELETE_STORM_ALERT_SCHEMA, {SERVICE_FIELD_ALERT_ID: "alert-1"}),
        (
            services.SET_THIRD_PARTY_MQTT_SCHEMA,
            {
                SERVICE_FIELD_ENABLE: True,
                SERVICE_FIELD_IP: "192.0.2.10",
                SERVICE_FIELD_PORT: 1883,
            },
        ),
        (services.QUERY_THIRD_PARTY_MQTT_SCHEMA, {}),
        (
            services.SEND_BLE_COMMAND_SCHEMA,
            {
                SERVICE_FIELD_CMD: 107,
                SERVICE_FIELD_BODY: {"cmd": 107},
            },
        ),
    ],
)
def test_device_id_service_schemas_reject_whitespace_only_values(
    schema: vol.Schema,
    extra_data: dict[str, object],
) -> None:
    """Device-id service schemas must reject values the handlers trim to empty."""
    with pytest.raises(vol.Invalid):
        schema({
            SERVICE_FIELD_DEVICE_ID: "  ",
            **extra_data,
        })


async def test_set_third_party_mqtt_service_rejects_direct_blank_ip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct service handler calls must keep the required IP text constraint."""

    class _ThirdPartyCoordinator:
        async def async_set_third_party_mqtt_config(self, *args: object) -> None:
            raise AssertionError("blank IP must stop before coordinator call")

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _ThirdPartyCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_set_third_party_mqtt_config(
            object(),
            _Call({
                SERVICE_FIELD_DEVICE_ID: "dev1",
                SERVICE_FIELD_ENABLE: True,
                SERVICE_FIELD_IP: "  ",
                SERVICE_FIELD_PORT: 1883,
            }),
        )

    assert err.value.translation_key == "set_third_party_mqtt_config_failed"
    assert err.value.translation_placeholders == {
        "device_id": "dev1",
        "error": "ip must not be empty",
    }


async def test_set_third_party_mqtt_service_rejects_direct_long_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct service handler calls must keep optional text length limits."""

    class _ThirdPartyCoordinator:
        async def async_set_third_party_mqtt_config(self, *args: object) -> None:
            raise AssertionError("long token must stop before coordinator call")

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _ThirdPartyCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_set_third_party_mqtt_config(
            object(),
            _Call({
                SERVICE_FIELD_DEVICE_ID: "dev1",
                SERVICE_FIELD_ENABLE: True,
                SERVICE_FIELD_IP: "192.0.2.10",
                SERVICE_FIELD_PORT: 1883,
                SERVICE_FIELD_TOKEN: "x" * 513,
            }),
        )

    assert err.value.translation_key == "set_third_party_mqtt_config_failed"
    assert err.value.translation_placeholders == {
        "device_id": "dev1",
        "error": "token must be at most 512 characters",
    }


async def test_send_ble_command_service_parses_wait_for_ack_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct BLE service handler calls must parse wait_for_ack strings."""

    class _BleCoordinator:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def async_send_ble_command(
            self,
            device_id: str,
            *,
            cmd: int,
            body: dict[str, object],
            flags: int,
            wait_for_ack: bool,
            ack_timeout_sec: float,
            connect_timeout_sec: float,
        ) -> bool:
            self.calls.append({
                "device_id": device_id,
                "cmd": cmd,
                "body": body,
                "flags": flags,
                "wait_for_ack": wait_for_ack,
                "ack_timeout_sec": ack_timeout_sec,
                "connect_timeout_sec": connect_timeout_sec,
            })
            return True

    coordinator = _BleCoordinator()
    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services, "_coordinator_for_device", lambda _hass, _device_id: coordinator
    )

    await services._async_handle_send_ble_command(
        object(),
        _Call({
            SERVICE_FIELD_DEVICE_ID: "dev1",
            SERVICE_FIELD_CMD: "107",
            SERVICE_FIELD_BODY: {"cmd": 107},
            SERVICE_FIELD_WAIT_FOR_ACK: "false",
        }),
    )

    assert coordinator.calls[0]["wait_for_ack"] is False


async def test_send_ble_command_service_preserves_invalid_wait_for_ack_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid BLE wait_for_ack should keep the direct translated field error."""

    class _BleCoordinator:
        async def async_send_ble_command(self, *args: object) -> bool:
            raise AssertionError("invalid boolean must stop before coordinator call")

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services, "_coordinator_for_device", lambda _hass, _device_id: _BleCoordinator()
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_send_ble_command(
            object(),
            _Call({
                SERVICE_FIELD_DEVICE_ID: "dev1",
                SERVICE_FIELD_CMD: 107,
                SERVICE_FIELD_BODY: {"cmd": 107},
                SERVICE_FIELD_WAIT_FOR_ACK: "maybe",
            }),
        )

    assert err.value.translation_key == "send_ble_command_failed"
    assert err.value.translation_placeholders == {
        "device_id": "dev1",
        "error": "wait_for_ack must be a boolean",
    }


@pytest.mark.parametrize(
    ("body", "expected_error"),
    [
        ({1: "x"}, "body object keys must be strings"),
        ({"cmd": object()}, "body must contain only JSON-compatible values"),
        ('{"cmd": NaN}', "body is not valid JSON: invalid JSON constant: NaN"),
    ],
)
async def test_send_ble_command_service_rejects_non_json_native_body(
    monkeypatch: pytest.MonkeyPatch,
    body: object,
    expected_error: str,
) -> None:
    """Direct BLE service calls must reject bodies json.dumps would alter or fail."""

    class _BleCoordinator:
        async def async_send_ble_command(self, *args: object) -> bool:
            raise AssertionError("invalid body must stop before coordinator call")

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services, "_coordinator_for_device", lambda _hass, _device_id: _BleCoordinator()
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_send_ble_command(
            object(),
            _Call({
                SERVICE_FIELD_DEVICE_ID: "dev1",
                SERVICE_FIELD_CMD: 107,
                SERVICE_FIELD_BODY: body,
            }),
        )

    assert err.value.translation_key == "send_ble_command_failed"
    assert err.value.translation_placeholders == {
        "device_id": "dev1",
        "error": expected_error,
    }


@pytest.mark.parametrize(
    ("call_data", "expected_error"),
    [
        (
            {
                SERVICE_FIELD_CMD: 107,
                SERVICE_FIELD_BODY: {"cmd": 107},
                SERVICE_FIELD_FLAGS: -1,
                SERVICE_FIELD_ACK_TIMEOUT: 5.0,
            },
            "flags must be between 0 and 65535",
        ),
        (
            {
                SERVICE_FIELD_CMD: 107.5,
                SERVICE_FIELD_BODY: {"cmd": 107},
            },
            "cmd must be an integer",
        ),
    ],
)
async def test_send_ble_command_service_rejects_direct_invalid_numeric_fields(
    monkeypatch: pytest.MonkeyPatch,
    call_data: dict[str, object],
    expected_error: str,
) -> None:
    """Direct BLE service calls must keep schema numeric ranges."""

    class _BleCoordinator:
        async def async_send_ble_command(self, *args: object) -> bool:
            raise AssertionError(
                "invalid numeric field must stop before coordinator call"
            )

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services, "_coordinator_for_device", lambda _hass, _device_id: _BleCoordinator()
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_send_ble_command(
            object(),
            _Call({SERVICE_FIELD_DEVICE_ID: "dev1", **call_data}),
        )

    assert err.value.translation_key == "send_ble_command_failed"
    assert err.value.translation_placeholders == {
        "device_id": "dev1",
        "error": expected_error,
    }


@pytest.mark.parametrize("ack_timeout", ["nan", True, _OverflowFloat()])
async def test_send_ble_command_service_rejects_direct_invalid_ack_timeout(
    monkeypatch: pytest.MonkeyPatch,
    ack_timeout: object,
) -> None:
    """Direct BLE service calls must reject non-finite ack_timeout values."""

    class _BleCoordinator:
        async def async_send_ble_command(self, *args: object) -> bool:
            raise AssertionError(
                "invalid ack_timeout must stop before coordinator call"
            )

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services, "_coordinator_for_device", lambda _hass, _device_id: _BleCoordinator()
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_send_ble_command(
            object(),
            _Call({
                SERVICE_FIELD_DEVICE_ID: "dev1",
                SERVICE_FIELD_CMD: 107,
                SERVICE_FIELD_BODY: {"cmd": 107},
                SERVICE_FIELD_ACK_TIMEOUT: ack_timeout,
            }),
        )

    assert err.value.translation_key == "send_ble_command_failed"
    assert err.value.translation_placeholders == {
        "device_id": "dev1",
        "error": "ack_timeout must be a number",
    }
