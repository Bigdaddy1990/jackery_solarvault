"""Unit tests for integration service helpers."""

from dataclasses import dataclass
import sys
from typing import TYPE_CHECKING, ClassVar, cast

import pytest
import voluptuous as vol

from custom_components.jackery_solarvault import services
from custom_components.jackery_solarvault.client.api import JackeryAuthError
from custom_components.jackery_solarvault.const import (
    DOMAIN,
    SERVICE_FIELD_ACK_TIMEOUT,
    SERVICE_FIELD_ACTION_ID,
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
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant, ServiceCall


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


def _hass() -> HomeAssistant:
    """Return a lightweight object typed as HomeAssistant for service unit tests."""
    return cast("HomeAssistant", object())


def _call(data: dict[str, object]) -> ServiceCall:
    """Return a lightweight service-call object for direct handler tests."""
    return cast("ServiceCall", _Call(data))


class _OverflowFloat:
    def __float__(self) -> float:
        """Raise an OverflowError when converting the object to a float.

        This method always raises OverflowError with the message "too large".

        Raises:
            OverflowError: always raised with the message "too large".
        """
        msg = "too large"
        raise OverflowError(msg)


def test_service_integer_parser_rejects_oversized_digit_strings() -> None:
    """Oversized digit strings must become HA validation errors."""
    old_limit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(640)
    try:
        with pytest.raises(vol.Invalid):
            services.SET_THIRD_PARTY_MQTT_SCHEMA({
                SERVICE_FIELD_DEVICE_ID: "dev1",
                SERVICE_FIELD_ENABLE: True,
                SERVICE_FIELD_IP: "192.168.2.212",
                SERVICE_FIELD_PORT: "9" * 700,
            })
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
    async def async_set_system_name(self, system_id: str, new_name: str) -> bool:  # noqa: PLR6301
        """Set the display name for the specified system.

        Parameters:
            system_id (str): The identifier of the system to rename.
            new_name (str): The new display name to assign to the system.

        Returns:
            bool: `True` if the rename succeeded, `False` otherwise.

        Raises:
            JackeryAuthError: If the request fails due to authentication (invalid or
            expired credentials).
        """
        msg = "invalid token"
        raise JackeryAuthError(msg)


class _AuthCoordinator:
    api = _AuthApi()

    async def async_request_refresh(self) -> None:  # noqa: PLR6301
        """Ensure a refresh is not performed during authentication failure handling.

        Raises:
            AssertionError: Always raised to fail the test if a refresh is attempted.
        """
        msg = "auth failures must not refresh"
        raise AssertionError(msg)


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

    assert (  # noqa: S101
        services._resolve_jackery_device_id(_hass(), "smart-plug-ha-id")  # noqa: SLF001
        == "573702884982521856"
    )
    assert (  # noqa: S101
        services._resolve_jackery_device_id(_hass(), "solarvault-ha-id")  # noqa: SLF001
        == "573702884982521856"
    )
    assert (  # noqa: S101
        services._resolve_jackery_device_id(_hass(), "573702884982521856")  # noqa: SLF001
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
        await services._async_handle_rename(  # noqa: SLF001
            _hass(),
            _call({
                SERVICE_FIELD_SYSTEM_ID: "123",
                SERVICE_FIELD_NEW_NAME: "SolarVault",
            }),
        )

    assert err.value.translation_key == "rename_system_failed"  # noqa: S101
    assert coordinator.api.calls == [("123", "SolarVault")]  # noqa: S101
    assert coordinator.refreshed is False  # noqa: S101


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
        await services._async_handle_rename(  # noqa: SLF001
            _hass(),
            _call({
                SERVICE_FIELD_SYSTEM_ID: "123",
                SERVICE_FIELD_NEW_NAME: "SolarVault",
            }),
        )


@pytest.mark.parametrize(
    ["handler_name", "call_data"],
    [
        [
            "_async_handle_refresh_weather_plan",
            {SERVICE_FIELD_DEVICE_ID: "dev1"},
        ],
        [
            "_async_handle_delete_storm_alert",
            {
                SERVICE_FIELD_DEVICE_ID: "dev1",
                SERVICE_FIELD_ALERT_ID: "alert1",
            },
        ],
        [
            "_async_handle_set_third_party_mqtt_config",
            {
                SERVICE_FIELD_DEVICE_ID: "dev1",
                SERVICE_FIELD_ENABLE: True,
                SERVICE_FIELD_IP: "192.0.2.10",
                SERVICE_FIELD_PORT: 1883,
            },
        ],
        [
            "_async_handle_query_third_party_mqtt_config",
            {SERVICE_FIELD_DEVICE_ID: "dev1"},
        ],
        [
            "_async_handle_send_device_schedule",
            {
                SERVICE_FIELD_DEVICE_ID: "dev1",
                SERVICE_FIELD_ACTION_ID: 3015,
                SERVICE_FIELD_BODY: {"enabled": True},
            },
        ],
    ],
)
async def test_device_services_preserve_auth_failures_for_reauth(
    monkeypatch: pytest.MonkeyPatch,
    handler_name: str,
    call_data: dict[str, object],
) -> None:
    """Device-scoped service handlers must let HA open reauth on auth failure."""

    class _AuthDeviceCoordinator:
        async def async_query_weather_plan(self, *args: object) -> None:  # noqa: PLR6301
            msg = "invalid token"
            raise JackeryAuthError(msg)

        async def async_delete_storm_alert(self, *args: object) -> None:  # noqa: PLR6301
            msg = "invalid token"
            raise JackeryAuthError(msg)

        async def async_set_third_party_mqtt_config(  # noqa: PLR6301
            self,
            *args: object,
            **kwargs: object,
        ) -> None:
            msg = "invalid token"
            raise JackeryAuthError(msg)

        async def async_query_third_party_mqtt_config(self, *args: object) -> None:  # noqa: PLR6301
            msg = "invalid token"
            raise JackeryAuthError(msg)

        async def async_send_device_schedule(  # noqa: PLR6301
            self,
            *args: object,
            **kwargs: object,
        ) -> None:
            msg = "invalid token"
            raise JackeryAuthError(msg)

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _AuthDeviceCoordinator(),
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await getattr(services, handler_name)(_hass(), _call(call_data))


@pytest.mark.parametrize(
    ["system_id", "expected_system_id", "expected_error"],
    [
        ["  ", "", "system_id must not be empty"],
        ["abc", "abc", "system_id must be numeric"],
        [None, "", "system_id must be text"],
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
        """Prevent coordinator lookup by always raising an AssertionError.

        This test helper raises AssertionError with the message
        "invalid system_id must stop before coordinator lookup" to ensure callers
        validate `system_id` before attempting to retrieve a coordinator.

        Parameters:
            _hass: Home Assistant instance (unused).
            _system_id: The system identifier that should have been validated
            beforehand.

        Raises:
            AssertionError: Always raised with message "invalid system_id must stop
            before coordinator lookup".
        """
        msg = "invalid system_id must stop before coordinator lookup"
        raise AssertionError(msg)

    monkeypatch.setattr(services, "_coordinator_for_system", _fail_coordinator_lookup)

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_rename(  # noqa: SLF001
            _hass(),
            _call({
                SERVICE_FIELD_SYSTEM_ID: system_id,
                SERVICE_FIELD_NEW_NAME: "SolarVault",
            }),
        )

    assert err.value.translation_key == "rename_system_failed"  # noqa: S101
    assert err.value.translation_placeholders == {  # noqa: S101
        "system_id": expected_system_id,
        "error": expected_error,
    }


@pytest.mark.parametrize(
    ["new_name", "expected_error"],
    [
        ["  ", "new_name must not be empty"],
        ["x" * 65, "new_name must be at most 64 characters"],
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
        await services._async_handle_rename(  # noqa: SLF001
            _hass(),
            _call({
                SERVICE_FIELD_SYSTEM_ID: "123",
                SERVICE_FIELD_NEW_NAME: new_name,
            }),
        )

    assert err.value.translation_key == "rename_system_failed"  # noqa: S101
    assert err.value.translation_placeholders == {  # noqa: S101
        "system_id": "123",
        "error": expected_error,
    }
    assert coordinator.api.calls == []  # noqa: S101
    assert coordinator.refreshed is False  # noqa: S101


async def test_refresh_weather_plan_service_translates_home_assistant_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """MQTT command errors are surfaced through the service translation key."""

    class _FailingCoordinator:
        data: ClassVar[dict[str, dict[str, object]]] = {"dev1": {}}

        async def async_query_weather_plan(self, device_id: str) -> None:  # noqa: PLR6301
            """Request a weather plan update for the given device.

            Parameters:
                device_id (str): Identifier of the target device.

            Raises:
                HomeAssistantError: If the MQTT command fails for the device (message
                includes the device_id).
            """
            msg = f"MQTT command failed for {device_id}"
            raise HomeAssistantError(msg)

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
        await services._async_handle_refresh_weather_plan(  # noqa: SLF001
            _hass(),
            _call({SERVICE_FIELD_DEVICE_ID: "dev1"}),
        )

    assert err.value.translation_key == "refresh_weather_plan_failed"  # noqa: S101
    assert err.value.translation_placeholders == {  # noqa: S101
        "device_id": "dev1",
        "error": "MQTT command failed for dev1",
    }


@pytest.mark.parametrize(
    ["device_id", "expected_error"],
    [
        ["  ", "device_id must not be empty"],
        [None, "device_id must be text"],
    ],
)
async def test_refresh_weather_plan_service_rejects_direct_invalid_device_id(
    monkeypatch: pytest.MonkeyPatch,
    device_id: object,
    expected_error: str,
) -> None:
    """Direct device service calls must keep the device_id text constraint."""

    def _fail_resolve(_hass: object, _raw: str) -> str:
        """Stub resolver used in tests to ensure device-id validation halts before.

        registry lookup.

        Always raises an AssertionError with the message "invalid device_id must stop
        before registry lookup" when invoked.
        """
        msg = "invalid device_id must stop before registry lookup"
        raise AssertionError(msg)

    monkeypatch.setattr(services, "_resolve_jackery_device_id", _fail_resolve)

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_refresh_weather_plan(  # noqa: SLF001
            _hass(),
            _call({SERVICE_FIELD_DEVICE_ID: device_id}),
        )

    assert err.value.translation_key == "refresh_weather_plan_failed"  # noqa: S101
    assert err.value.translation_placeholders == {  # noqa: S101
        "device_id": "",
        "error": expected_error,
    }


async def test_delete_storm_alert_service_rejects_direct_blank_alert_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct delete handler calls must keep the alert_id non-empty constraint."""

    class _StormAlertCoordinator:
        async def async_delete_storm_alert(self, *args: object) -> None:  # noqa: PLR6301
            """Test stub for deleting a storm alert that fails if invoked.

            Used by tests to assert that input validation prevents coordinator calls;
            if this method is ever called it raises an AssertionError with the message
            "blank alert_id must stop before coordinator call".

            Raises:
                AssertionError: Always raised to indicate the coordinator should not be
                reached for invalid input.
            """
            msg = "blank alert_id must stop before coordinator call"
            raise AssertionError(msg)

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _StormAlertCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_delete_storm_alert(  # noqa: SLF001
            _hass(),
            _call({
                SERVICE_FIELD_DEVICE_ID: "dev1",
                SERVICE_FIELD_ALERT_ID: "  ",
            }),
        )

    assert err.value.translation_key == "delete_storm_alert_failed"  # noqa: S101
    assert err.value.translation_placeholders == {  # noqa: S101
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

        async def async_set_third_party_mqtt_config(  # noqa: PLR0913
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
            """Record a third-party MQTT configuration call for the given device.

            Appends a dictionary with keys "device_id", "enable", "ip", "port",
            "username", "password", and "token" to self.calls.
            """
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
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: coordinator,
    )

    await services._async_handle_set_third_party_mqtt_config(  # noqa: SLF001
        _hass(),
        _call({
            SERVICE_FIELD_DEVICE_ID: "dev1",
            SERVICE_FIELD_ENABLE: "false",
            SERVICE_FIELD_IP: " 192.0.2.10 ",
            SERVICE_FIELD_PORT: "1883",
            SERVICE_FIELD_USERNAME: "user",
            SERVICE_FIELD_PASSWORD: "pass",
            SERVICE_FIELD_TOKEN: "token",
        }),
    )

    assert coordinator.calls == [  # noqa: S101
        {
            "device_id": "dev1",
            "enable": False,
            "ip": "192.0.2.10",
            "port": 1883,
            "username": "user",
            "password": "pass",
            "token": "token",
        },
    ]


async def test_set_third_party_mqtt_service_keeps_none_credentials_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct service handler calls must not turn optional None fields into text."""

    class _ThirdPartyCoordinator:
        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def async_set_third_party_mqtt_config(  # noqa: PLR0913
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
            """Record a third-party MQTT configuration call for the given device.

            Appends a dictionary with keys "device_id", "enable", "ip", "port",
            "username", "password", and "token" to self.calls.
            """
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
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: coordinator,
    )

    await services._async_handle_set_third_party_mqtt_config(  # noqa: SLF001
        _hass(),
        _call({
            SERVICE_FIELD_DEVICE_ID: "dev1",
            SERVICE_FIELD_ENABLE: True,
            SERVICE_FIELD_IP: "192.0.2.10",
            SERVICE_FIELD_PORT: 1883,
            SERVICE_FIELD_USERNAME: None,
            SERVICE_FIELD_PASSWORD: None,
            SERVICE_FIELD_TOKEN: None,
        }),
    )

    assert coordinator.calls == [  # noqa: S101
        {
            "device_id": "dev1",
            "enable": True,
            "ip": "192.0.2.10",
            "port": 1883,
            "username": "",
            "password": "",
            "token": "",
        },
    ]


async def test_set_third_party_mqtt_service_rejects_direct_non_text_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct service handler calls must not stringify credential objects."""

    class _ThirdPartyCoordinator:
        async def async_set_third_party_mqtt_config(self, *args: object) -> None:  # noqa: PLR6301
            """Test sentinel that fails if the coordinator API is invoked.

            This method always raises an AssertionError to ensure the coordinator is
            not called
            during tests when input validation should have failed earlier.

            Raises:
                AssertionError: with message "non-text credentials must stop before
                coordinator call"
            """
            msg = "non-text credentials must stop before coordinator call"
            raise AssertionError(
                msg,
            )

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _ThirdPartyCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_set_third_party_mqtt_config(  # noqa: SLF001
            _hass(),
            _call({
                SERVICE_FIELD_DEVICE_ID: "dev1",
                SERVICE_FIELD_ENABLE: True,
                SERVICE_FIELD_IP: "192.0.2.10",
                SERVICE_FIELD_PORT: 1883,
                SERVICE_FIELD_USERNAME: {"name": "user"},
            }),
        )

    assert err.value.translation_key == "set_third_party_mqtt_config_failed"  # noqa: S101
    assert err.value.translation_placeholders == {  # noqa: S101
        "device_id": "dev1",
        "error": "username must be text",
    }


async def test_set_third_party_mqtt_service_preserves_invalid_boolean_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid service booleans should keep their translated field error."""

    class _ThirdPartyCoordinator:
        async def async_set_third_party_mqtt_config(self, *args: object) -> None:  # noqa: PLR6301
            """Apply third-party MQTT configuration to the coordinator.

            Test-only stub: raises AssertionError if invoked to assert that input
            validation prevented the coordinator from being called.
            """
            msg = "invalid boolean must stop before coordinator call"
            raise AssertionError(msg)

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _ThirdPartyCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_set_third_party_mqtt_config(  # noqa: SLF001
            _hass(),
            _call({
                SERVICE_FIELD_DEVICE_ID: "dev1",
                SERVICE_FIELD_ENABLE: "maybe",
                SERVICE_FIELD_IP: "192.0.2.10",
                SERVICE_FIELD_PORT: 1883,
            }),
        )

    assert err.value.translation_key == "set_third_party_mqtt_config_failed"  # noqa: S101
    assert err.value.translation_placeholders == {  # noqa: S101
        "device_id": "dev1",
        "error": "enable must be a boolean",
    }


@pytest.mark.parametrize(
    ["port", "expected_error"],
    [
        [0, "port must be between 1 and 65535"],
        [1883.9, "port must be an integer"],
    ],
)
async def test_set_third_party_mqtt_service_rejects_direct_invalid_port(
    monkeypatch: pytest.MonkeyPatch,
    port: object,
    expected_error: str,
) -> None:
    """Direct service handler calls must keep the schema port constraints."""

    class _ThirdPartyCoordinator:
        async def async_set_third_party_mqtt_config(self, *args: object) -> None:  # noqa: PLR6301
            """Apply third-party MQTT configuration to the target device.

            This method persists the provided MQTT settings (enable, ip, port,
            username, password, token, etc.) for the device handled by this
            coordinator. In this test stub the method raises AssertionError to signal
            it must not be invoked by handlers when validation fails.

            Raises:
                AssertionError: In the test stub, always raised to indicate the
                coordinator should not be called.
            """
            msg = "invalid port must stop before coordinator call"
            raise AssertionError(msg)

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _ThirdPartyCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_set_third_party_mqtt_config(  # noqa: SLF001
            _hass(),
            _call({
                SERVICE_FIELD_DEVICE_ID: "dev1",
                SERVICE_FIELD_ENABLE: True,
                SERVICE_FIELD_IP: "192.0.2.10",
                SERVICE_FIELD_PORT: port,
            }),
        )

    assert err.value.translation_key == "set_third_party_mqtt_config_failed"  # noqa: S101
    assert err.value.translation_placeholders == {  # noqa: S101
        "device_id": "dev1",
        "error": expected_error,
    }


@pytest.mark.parametrize(
    ["schema", "extra_data"],
    [
        [services.REFRESH_WEATHER_PLAN_SCHEMA, {}],
        [services.DELETE_STORM_ALERT_SCHEMA, {SERVICE_FIELD_ALERT_ID: "alert-1"}],
        [
            services.SET_THIRD_PARTY_MQTT_SCHEMA,
            {
                SERVICE_FIELD_ENABLE: True,
                SERVICE_FIELD_IP: "192.0.2.10",
                SERVICE_FIELD_PORT: 1883,
            },
        ],
        [services.QUERY_THIRD_PARTY_MQTT_SCHEMA, {}],
        [
            services.SEND_BLE_COMMAND_SCHEMA,
            {
                SERVICE_FIELD_CMD: 107,
                SERVICE_FIELD_BODY: {"cmd": 107},
            },
        ],
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
        async def async_set_third_party_mqtt_config(self, *args: object) -> None:  # noqa: PLR6301
            """Apply a third-party MQTT configuration for the associated device.

            This handler accepts the normalized fields for third-party MQTT (enable,
            ip, port,
            username, password, token, and any other optional credentials) and applies
            them
            to the device's configuration.

            Returns:
                None
            """
            msg = "blank IP must stop before coordinator call"
            raise AssertionError(msg)

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _ThirdPartyCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_set_third_party_mqtt_config(  # noqa: SLF001
            _hass(),
            _call({
                SERVICE_FIELD_DEVICE_ID: "dev1",
                SERVICE_FIELD_ENABLE: True,
                SERVICE_FIELD_IP: "  ",
                SERVICE_FIELD_PORT: 1883,
            }),
        )

    assert err.value.translation_key == "set_third_party_mqtt_config_failed"  # noqa: S101
    assert err.value.translation_placeholders == {  # noqa: S101
        "device_id": "dev1",
        "error": "ip must not be empty",
    }


async def test_set_third_party_mqtt_service_rejects_direct_long_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Direct service handler calls must keep optional text length limits."""

    class _ThirdPartyCoordinator:
        async def async_set_third_party_mqtt_config(self, *args: object) -> None:  # noqa: PLR6301
            msg = "long token must stop before coordinator call"
            raise AssertionError(msg)

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _ThirdPartyCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_set_third_party_mqtt_config(  # noqa: SLF001
            _hass(),
            _call({
                SERVICE_FIELD_DEVICE_ID: "dev1",
                SERVICE_FIELD_ENABLE: True,
                SERVICE_FIELD_IP: "192.0.2.10",
                SERVICE_FIELD_PORT: 1883,
                SERVICE_FIELD_TOKEN: "x" * 513,
            }),
        )

    assert err.value.translation_key == "set_third_party_mqtt_config_failed"  # noqa: S101
    assert err.value.translation_placeholders == {  # noqa: S101
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

        async def async_send_ble_command(  # noqa: PLR0913
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
            """Record a BLE command invocation for the given device and indicate.

            success.

            Parameters:
                device_id (str): The target device identifier.
                cmd (int): BLE command code to send.
                body (dict[str, object]): JSON-compatible command payload.
                flags (int): Bitmask of command flags.
                wait_for_ack (bool): Whether to wait for an acknowledgement.
                ack_timeout_sec (float): Timeout in seconds to wait for an
                acknowledgement.
                connect_timeout_sec (float): Timeout in seconds for establishing the
                BLE connection.

            Returns:
                bool: `True` if the command was accepted, `False` otherwise.
            """
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
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: coordinator,
    )

    await services._async_handle_send_ble_command(  # noqa: SLF001
        _hass(),
        _call({
            SERVICE_FIELD_DEVICE_ID: "dev1",
            SERVICE_FIELD_CMD: "107",
            SERVICE_FIELD_BODY: {"cmd": 107},
            SERVICE_FIELD_WAIT_FOR_ACK: "false",
        }),
    )

    assert coordinator.calls[0]["wait_for_ack"] is False  # noqa: S101


async def test_send_ble_command_service_preserves_invalid_wait_for_ack_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Invalid BLE wait_for_ack should keep the direct translated field error."""

    class _BleCoordinator:
        async def async_send_ble_command(self, *args: object) -> bool:  # noqa: PLR6301
            """Send a BLE command to the target device and indicate whether the command.

            was acknowledged.

            Parameters:
                *args (object): Command parameters (implementation-specific).

            Returns:
                bool: `True` if the device acknowledged the command, `False` otherwise.
            """
            msg = "invalid boolean must stop before coordinator call"
            raise AssertionError(msg)

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _BleCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_send_ble_command(  # noqa: SLF001
            _hass(),
            _call({
                SERVICE_FIELD_DEVICE_ID: "dev1",
                SERVICE_FIELD_CMD: 107,
                SERVICE_FIELD_BODY: {"cmd": 107},
                SERVICE_FIELD_WAIT_FOR_ACK: "maybe",
            }),
        )

    assert err.value.translation_key == "send_ble_command_failed"  # noqa: S101
    assert err.value.translation_placeholders == {  # noqa: S101
        "device_id": "dev1",
        "error": "wait_for_ack must be a boolean",
    }


@pytest.mark.parametrize(
    ["body", "expected_error"],
    [
        [{1: "x"}, "body object keys must be strings"],
        [{"cmd": object()}, "body must contain only JSON-compatible values"],
        ['{"cmd": NaN}', "body is not valid JSON: invalid JSON constant: NaN"],
    ],
)
async def test_send_ble_command_service_rejects_non_json_native_body(
    monkeypatch: pytest.MonkeyPatch,
    body: object,
    expected_error: str,
) -> None:
    """Validate that the BLE send-command handler rejects request bodies that are not.

    JSON-native.

    This test calls the BLE service handler with a `body` value that cannot be safely
    serialized to JSON
    (or would be altered by json.dumps) and asserts the handler raises
    ServiceValidationError with
    translation_key "send_ble_command_failed" and translation_placeholders containing
    the provided
    `expected_error`.

    Parameters:
        body (object): The raw `body` value passed to the service; must be a
        non-JSON-native case to trigger validation.
        expected_error (str): The exact error message expected in the service error
        translation placeholders.
    """

    class _BleCoordinator:
        async def async_send_ble_command(self, *args: object) -> bool:  # noqa: PLR6301
            """Send a BLE command to the target device via the coordinator.

            Parameters:
                *args (object): Variable arguments forwarded from the service handler;
                expected to include the target device identifier and the command
                payload (body) along with optional flags such as `cmd`, `flags`,
                `ack_timeout`, and `wait_for_ack`.

            Returns:
                bool: `True` if the BLE command succeeded, `False` otherwise.
            """
            msg = "invalid body must stop before coordinator call"
            raise AssertionError(msg)

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _BleCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_send_ble_command(  # noqa: SLF001
            _hass(),
            _call({
                SERVICE_FIELD_DEVICE_ID: "dev1",
                SERVICE_FIELD_CMD: 107,
                SERVICE_FIELD_BODY: body,
            }),
        )

    assert err.value.translation_key == "send_ble_command_failed"  # noqa: S101
    assert err.value.translation_placeholders == {  # noqa: S101
        "device_id": "dev1",
        "error": expected_error,
    }


@pytest.mark.parametrize(
    ["call_data", "expected_error"],
    [
        [
            {
                SERVICE_FIELD_CMD: 107,
                SERVICE_FIELD_BODY: {"cmd": 107},
                SERVICE_FIELD_FLAGS: -1,
                SERVICE_FIELD_ACK_TIMEOUT: 5.0,
            },
            "flags must be between 0 and 65535",
        ],
        [
            {
                SERVICE_FIELD_CMD: 107.5,
                SERVICE_FIELD_BODY: {"cmd": 107},
            },
            "cmd must be an integer",
        ],
    ],
)
async def test_send_ble_command_service_rejects_direct_invalid_numeric_fields(
    monkeypatch: pytest.MonkeyPatch,
    call_data: dict[str, object],
    expected_error: str,
) -> None:
    """Direct BLE service calls must keep schema numeric ranges."""

    class _BleCoordinator:
        async def async_send_ble_command(self, *args: object) -> bool:  # noqa: PLR6301
            """Sentinel coordinator method that must not be called by service handlers.

            Raises:
                AssertionError: Always raised with the message
                "invalid numeric field must stop before coordinator call" to signal that
                input validation should have prevented invocation.
            """
            msg = "invalid numeric field must stop before coordinator call"
            raise AssertionError(
                msg,
            )

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _BleCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_send_ble_command(  # noqa: SLF001
            _hass(),
            _call({SERVICE_FIELD_DEVICE_ID: "dev1", **call_data}),
        )

    assert err.value.translation_key == "send_ble_command_failed"  # noqa: S101
    assert err.value.translation_placeholders == {  # noqa: S101
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
        async def async_send_ble_command(self, *args: object) -> bool:  # noqa: PLR6301
            """Test-only stub for sending a BLE command that must not be invoked.

            Raises:
                AssertionError: Always raised to indicate the coordinator method should
                not be called during validation tests.
            """
            msg = "invalid ack_timeout must stop before coordinator call"
            raise AssertionError(
                msg,
            )

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _BleCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_send_ble_command(  # noqa: SLF001
            _hass(),
            _call({
                SERVICE_FIELD_DEVICE_ID: "dev1",
                SERVICE_FIELD_CMD: 107,
                SERVICE_FIELD_BODY: {"cmd": 107},
                SERVICE_FIELD_ACK_TIMEOUT: ack_timeout,
            }),
        )

    assert err.value.translation_key == "send_ble_command_failed"  # noqa: S101
    assert err.value.translation_placeholders == {  # noqa: S101
        "device_id": "dev1",
        "error": "ack_timeout must be a number",
    }
