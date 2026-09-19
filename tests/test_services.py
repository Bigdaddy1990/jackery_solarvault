"""Unit tests for integration service helpers."""

from contextlib import contextmanager
from dataclasses import dataclass
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING, ClassVar, cast
from unittest.mock import AsyncMock, Mock

import pytest
import voluptuous as vol

from custom_components.jackery_solarvault import services
from custom_components.jackery_solarvault.client.api import (
    JackeryAuthError,
    JackeryError,
)
from custom_components.jackery_solarvault.const import (
    DISCOVERY_SOURCE_LEGACY_BIND_LIST,
    DOMAIN,
    FIELD_QR_CODE_ID,
    FIELD_USER_ID,
    PAYLOAD_DEVICE,
    PAYLOAD_DISCOVERY,
    PAYLOAD_DISCOVERY_SOURCE,
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
    SERVICE_RESPONSE_QR_CODE_ID,
    SERVICE_RESPONSE_USER_ID,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

    from homeassistant.core import HomeAssistant, ServiceCall


@dataclass(slots=True)
class _Device:
    identifiers: set[tuple[str, str]]
    via_device_id: str | None = None


class _Registry:
    def __init__(self, devices: dict[str, _Device]) -> None:
        self._devices = devices

    def async_get(self, device_id: str, **_kwargs: object) -> _Device | None:
        return self._devices.get(device_id)


@dataclass(slots=True)
class _Call:
    data: dict[str, object]


def _test_hass() -> HomeAssistant:
    """Return the deliberately minimal Home Assistant test double."""
    return cast("HomeAssistant", object())


def _service_call(data: dict[str, object]) -> ServiceCall:
    """Type a minimal service call at the test boundary."""
    return cast("ServiceCall", _Call(data))


def _translation_placeholder(exc: HomeAssistantError, key: str = "error") -> str:
    """Read a required translation placeholder from a service error."""
    placeholders = exc.translation_placeholders
    assert placeholders is not None
    value = placeholders[key]
    assert isinstance(value, str)
    return value


# pyrefly: ignore [deprecated]
@contextmanager
def _ignore_private() -> Iterator[None]:
    """Mark deliberate private service-helper access in tests.

    The per-line ``# ruff: ignore[private-member-access]`` comments carry the
    actual suppression; this scope keeps the assertion body of rejection tests
    under one readable construct.
    """
    yield


class _OverflowFloat:
    def __float__(self) -> float:
        """Raise an OverflowError when converting the object to a float.

        This method always raises OverflowError with the message "too large".

        Raises:
            OverflowError: always raised with the message "too large".
        """
        raise OverflowError("too large")  # ruff: ignore[raise-vanilla-args]


def test_service_integer_parser_rejects_oversized_digit_strings() -> None:
    """Oversized digit strings must become HA validation errors."""
    old_limit = sys.get_int_max_str_digits()
    sys.set_int_max_str_digits(640)
    try:
        with pytest.raises(vol.Invalid):
            services._coerce_service_int("9" * 700)  # ruff: ignore[private-member-access]
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

    async def async_set_system_name(self, system_id: str, new_name: str) -> None:
        # Mirrors the coordinator wrapper the service now routes through
        # (coordinator.async_set_system_name), which raises on a false API
        # result instead of the service inspecting the raw boolean.
        ok = await self.api.async_set_system_name(system_id, new_name)
        if not ok:
            raise JackeryError("server returned false")  # ruff: ignore[raise-vanilla-args]
        await self.async_request_refresh()

    async def async_request_refresh(self) -> None:
        self.refreshed = True


class _AuthApi:
    async def async_set_system_name(self, system_id: str, new_name: str) -> bool:  # ruff: ignore[no-self-use]
        """Set the display name for the specified system.

        Parameters:
            system_id (str): The identifier of the system to rename.
            new_name (str): The new display name to assign to the system.

        Returns:
            bool: `True` if the rename succeeded, `False` otherwise.

        Raises:
            JackeryAuthError: If the request fails due to authentication (invalid or expired credentials).
        """  # ruff: ignore[line-too-long]
        raise JackeryAuthError("invalid token")  # ruff: ignore[raise-vanilla-args]


class _AuthCoordinator:
    api = _AuthApi()

    async def async_set_system_name(self, system_id: str, new_name: str) -> None:
        # Propagates the auth failure raised by the API without refreshing.
        await self.api.async_set_system_name(system_id, new_name)

    async def async_request_refresh(self) -> None:  # ruff: ignore[no-self-use]
        """Ensure a refresh is not performed during authentication failure handling.

        Raises:
            AssertionError: Always raised to fail the test if a refresh is attempted.
        """
        raise AssertionError("auth failures must not refresh")  # ruff: ignore[raise-vanilla-args]


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
        services._resolve_jackery_device_id(_test_hass(), "smart-plug-ha-id")  # ruff: ignore[private-member-access]
        == "573702884982521856"
    )
    assert (
        services._resolve_jackery_device_id(_test_hass(), "solarvault-ha-id")  # ruff: ignore[private-member-access]
        == "573702884982521856"
    )
    assert (
        services._resolve_jackery_device_id(_test_hass(), "573702884982521856")  # ruff: ignore[private-member-access]
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

    with pytest.raises(HomeAssistantError) as err:
        await services._async_handle_rename(  # ruff: ignore[private-member-access]
            _test_hass(),
            _service_call({
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
        await services._async_handle_rename(  # ruff: ignore[private-member-access]
            _test_hass(),
            _service_call({
                SERVICE_FIELD_SYSTEM_ID: "123",
                SERVICE_FIELD_NEW_NAME: "SolarVault",
            }),
        )


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
            _system_id: The system identifier that should have been validated beforehand.

        Raises:
            AssertionError: Always raised with message "invalid system_id must stop before coordinator lookup".
        """  # ruff: ignore[line-too-long]
        raise AssertionError("invalid system_id must stop before coordinator lookup")  # ruff: ignore[raise-vanilla-args]

    monkeypatch.setattr(services, "_coordinator_for_system", _fail_coordinator_lookup)

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_rename(  # ruff: ignore[private-member-access]
            _test_hass(),
            _service_call({
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
        await services._async_handle_rename(  # ruff: ignore[private-member-access]
            _test_hass(),
            _service_call({
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
        data: ClassVar[dict[str, dict[str, object]]] = {"dev1": {}}

        async def async_query_weather_plan(self, device_id: str) -> None:  # ruff: ignore[no-self-use]
            """Request a weather plan update for the given device.

            Parameters:
                device_id (str): Identifier of the target device.

            Raises:
                HomeAssistantError: If the MQTT command fails for the device (message includes the device_id).
            """  # ruff: ignore[line-too-long]
            raise HomeAssistantError(f"MQTT command failed for {device_id}")  # ruff: ignore[raise-vanilla-args]

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

    with pytest.raises(HomeAssistantError) as err:
        await services._async_handle_refresh_weather_plan(  # ruff: ignore[private-member-access]
            _test_hass(),
            _service_call({SERVICE_FIELD_DEVICE_ID: "dev1"}),
        )

    assert err.value.translation_key == "refresh_weather_plan_failed"
    assert err.value.translation_placeholders == {
        "device_id": "dev1",
        "error": "HomeAssistantError: **REDACTED**",
    }
    assert "MQTT command failed" not in str(err.value.translation_placeholders)


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
        """Stub resolver used in tests to ensure device-id validation halts before registry lookup.

        Always raises an AssertionError with the message "invalid device_id must stop before registry lookup" when invoked.
        """  # ruff: ignore[line-too-long]
        raise AssertionError("invalid device_id must stop before registry lookup")  # ruff: ignore[raise-vanilla-args]

    monkeypatch.setattr(services, "_resolve_jackery_device_id", _fail_resolve)

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_refresh_weather_plan(  # ruff: ignore[private-member-access]
            _test_hass(),
            _service_call({SERVICE_FIELD_DEVICE_ID: device_id}),
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
        async def async_delete_storm_alert(self, *args: object) -> None:  # ruff: ignore[no-self-use]
            """Test stub for deleting a storm alert that fails if invoked.

            Used by tests to assert that input validation prevents coordinator calls; if this method is ever called it raises an AssertionError with the message "blank alert_id must stop before coordinator call".

            Raises:
                AssertionError: Always raised to indicate the coordinator should not be reached for invalid input.
            """  # ruff: ignore[line-too-long]
            raise AssertionError("blank alert_id must stop before coordinator call")  # ruff: ignore[raise-vanilla-args]

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _StormAlertCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_delete_storm_alert(  # ruff: ignore[private-member-access]
            _test_hass(),
            _service_call({
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
        data: ClassVar[dict[str, object]] = {}

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def async_set_third_party_mqtt_config(  # ruff: ignore[too-many-arguments]
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

            Appends a dictionary with keys "device_id", "enable", "ip", "port", "username", "password", and "token" to self.calls.
            """  # ruff: ignore[line-too-long]
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

    await services._async_handle_set_third_party_mqtt_config(  # ruff: ignore[private-member-access]
        _test_hass(),
        _service_call({
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
        data: ClassVar[dict[str, object]] = {}

        def __init__(self) -> None:
            self.calls: list[dict[str, object]] = []

        async def async_set_third_party_mqtt_config(  # ruff: ignore[too-many-arguments]
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

            Appends a dictionary with keys "device_id", "enable", "ip", "port", "username", "password", and "token" to self.calls.
            """  # ruff: ignore[line-too-long]
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

    await services._async_handle_set_third_party_mqtt_config(  # ruff: ignore[private-member-access]
        _test_hass(),
        _service_call({
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
        data: ClassVar[dict[str, object]] = {}

        async def async_set_third_party_mqtt_config(self, *args: object) -> None:  # ruff: ignore[no-self-use]
            """Test sentinel that fails if the coordinator API is invoked.

            This method always raises an AssertionError to ensure the coordinator is not called
            during tests when input validation should have failed earlier.

            Raises:
                AssertionError: with message "non-text credentials must stop before coordinator call"
            """  # ruff: ignore[line-too-long]
            raise AssertionError(  # ruff: ignore[raise-vanilla-args]
                "non-text credentials must stop before coordinator call"
            )

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _ThirdPartyCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_set_third_party_mqtt_config(  # ruff: ignore[private-member-access]
            _test_hass(),
            _service_call({
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
        data: ClassVar[dict[str, object]] = {}

        async def async_set_third_party_mqtt_config(self, *args: object) -> None:  # ruff: ignore[no-self-use]
            """Apply third-party MQTT configuration to the coordinator.

            Test-only stub: raises AssertionError if invoked to assert that input validation prevented the coordinator from being called.
            """  # ruff: ignore[line-too-long]
            raise AssertionError("invalid boolean must stop before coordinator call")  # ruff: ignore[raise-vanilla-args]

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _ThirdPartyCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_set_third_party_mqtt_config(  # ruff: ignore[private-member-access]
            _test_hass(),
            _service_call({
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
        data: ClassVar[dict[str, object]] = {}

        async def async_set_third_party_mqtt_config(self, *args: object) -> None:  # ruff: ignore[no-self-use]
            """Apply third-party MQTT configuration to the target device.

            This method persists the provided MQTT settings (enable, ip, port, username, password, token, etc.) for the device handled by this coordinator. In this test stub the method raises AssertionError to signal it must not be invoked by handlers when validation fails.

            Raises:
                AssertionError: In the test stub, always raised to indicate the coordinator should not be called.
            """  # ruff: ignore[line-too-long]
            raise AssertionError("invalid port must stop before coordinator call")  # ruff: ignore[raise-vanilla-args]

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _ThirdPartyCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_set_third_party_mqtt_config(  # ruff: ignore[private-member-access]
            _test_hass(),
            _service_call({
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
        data: ClassVar[dict[str, object]] = {}

        async def async_set_third_party_mqtt_config(self, *args: object) -> None:  # ruff: ignore[no-self-use]
            """Apply a third-party MQTT configuration for the associated device.

            This handler accepts the normalized fields for third-party MQTT (enable, ip, port,
            username, password, token, and any other optional credentials) and applies them
            to the device's configuration.

            Returns:
                None
            """  # ruff: ignore[line-too-long]
            raise AssertionError("blank IP must stop before coordinator call")  # ruff: ignore[raise-vanilla-args]

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _ThirdPartyCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_set_third_party_mqtt_config(  # ruff: ignore[private-member-access]
            _test_hass(),
            _service_call({
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
        data: ClassVar[dict[str, object]] = {}

        async def async_set_third_party_mqtt_config(self, *args: object) -> None:  # ruff: ignore[no-self-use]
            raise AssertionError("long token must stop before coordinator call")  # ruff: ignore[raise-vanilla-args]

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services,
        "_coordinator_for_device",
        lambda _hass, _device_id: _ThirdPartyCoordinator(),
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_set_third_party_mqtt_config(  # ruff: ignore[private-member-access]
            _test_hass(),
            _service_call({
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

        async def async_send_ble_command(  # ruff: ignore[too-many-arguments]
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
            """Record a BLE command invocation for the given device and indicate success.

            Parameters:
                device_id (str): The target device identifier.
                cmd (int): BLE command code to send.
                body (dict[str, object]): JSON-compatible command payload.
                flags (int): Bitmask of command flags.
                wait_for_ack (bool): Whether to wait for an acknowledgement.
                ack_timeout_sec (float): Timeout in seconds to wait for an acknowledgement.
                connect_timeout_sec (float): Timeout in seconds for establishing the BLE connection.

            Returns:
                bool: `True` if the command was accepted, `False` otherwise.
            """  # ruff: ignore[line-too-long]
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

    await services._async_handle_send_ble_command(  # ruff: ignore[private-member-access]
        _test_hass(),
        _service_call({
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
        async def async_send_ble_command(self, *args: object) -> bool:  # ruff: ignore[no-self-use]
            """Send a BLE command to the target device and indicate whether the command was acknowledged.

            Parameters:
                *args (object): Command parameters (implementation-specific).

            Returns:
                bool: `True` if the device acknowledged the command, `False` otherwise.
            """  # ruff: ignore[line-too-long]
            raise AssertionError("invalid boolean must stop before coordinator call")  # ruff: ignore[raise-vanilla-args]

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services, "_coordinator_for_device", lambda _hass, _device_id: _BleCoordinator()
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_send_ble_command(  # ruff: ignore[private-member-access]
            _test_hass(),
            _service_call({
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
    ["body", "expected_error"],
    [
        [{1: "x"}, "TypeError: **REDACTED**"],
        [{"cmd": object()}, "ValueError: **REDACTED**"],
        ['{"cmd": NaN}', "body is not valid JSON: invalid JSON constant: NaN"],
    ],
)
async def test_send_ble_command_service_rejects_non_json_native_body(
    monkeypatch: pytest.MonkeyPatch,
    body: object,
    expected_error: str,
) -> None:
    """Validate that the BLE send-command handler rejects request bodies that are not JSON-native.

    This test calls the BLE service handler with a `body` value that cannot be safely serialized to JSON
    (or would be altered by json.dumps) and asserts the handler raises ServiceValidationError with
    translation_key "send_ble_command_failed" and translation_placeholders containing the provided
    `expected_error`.

    Parameters:
        body (object): The raw `body` value passed to the service; must be a non-JSON-native case to trigger validation.
        expected_error (str): The exact error message expected in the service error translation placeholders.
    """  # ruff: ignore[line-too-long]

    class _BleCoordinator:
        async def async_send_ble_command(self, *args: object) -> bool:  # ruff: ignore[no-self-use]
            """Send a BLE command to the target device via the coordinator.

            Parameters:
                *args (object): Variable arguments forwarded from the service handler; expected to include the target device identifier and the command payload (body) along with optional flags such as `cmd`, `flags`, `ack_timeout`, and `wait_for_ack`.

            Returns:
                bool: `True` if the BLE command succeeded, `False` otherwise.
            """  # ruff: ignore[line-too-long]
            raise AssertionError("invalid body must stop before coordinator call")  # ruff: ignore[raise-vanilla-args]

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services, "_coordinator_for_device", lambda _hass, _device_id: _BleCoordinator()
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_send_ble_command(  # ruff: ignore[private-member-access]
            _test_hass(),
            _service_call({
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
        async def async_send_ble_command(self, *args: object) -> bool:  # ruff: ignore[no-self-use]
            """Sentinel coordinator method that must not be called by service handlers.

            Raises:
                AssertionError: Always raised with the message
                "invalid numeric field must stop before coordinator call" to signal that
                input validation should have prevented invocation.
            """
            raise AssertionError(  # ruff: ignore[raise-vanilla-args]
                "invalid numeric field must stop before coordinator call"
            )

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services, "_coordinator_for_device", lambda _hass, _device_id: _BleCoordinator()
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_send_ble_command(  # ruff: ignore[private-member-access]
            _test_hass(),
            _service_call({SERVICE_FIELD_DEVICE_ID: "dev1", **call_data}),
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
        async def async_send_ble_command(self, *args: object) -> bool:  # ruff: ignore[no-self-use]
            """Test-only stub for sending a BLE command that must not be invoked.

            Raises:
                AssertionError: Always raised to indicate the coordinator method should not be called during validation tests.
            """  # ruff: ignore[line-too-long]
            raise AssertionError(  # ruff: ignore[raise-vanilla-args]
                "invalid ack_timeout must stop before coordinator call"
            )

    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _hass, raw: raw)
    monkeypatch.setattr(
        services, "_coordinator_for_device", lambda _hass, _device_id: _BleCoordinator()
    )

    with pytest.raises(ServiceValidationError) as err:
        await services._async_handle_send_ble_command(  # ruff: ignore[private-member-access]
            _test_hass(),
            _service_call({
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


# ---------------------------------------------------------------------------
# Pure coercion / validation helpers — exercise every branch
# ---------------------------------------------------------------------------


def _fake_coordinator(data: object) -> JackerySolarVaultCoordinator:
    """Return a typed coordinator-shaped stub carrying the given payload data."""
    return cast("JackerySolarVaultCoordinator", SimpleNamespace(data=data))


@pytest.mark.parametrize("raw", [True, False])
def test_coerce_service_int_rejects_bool(raw: object) -> None:
    """Booleans are never valid service integers."""
    with pytest.raises(vol.Invalid), _ignore_private():
        services._coerce_service_int(raw)  # ruff: ignore[private-member-access]


def test_coerce_service_int_returns_int() -> None:
    """Plain integers pass through unchanged."""
    expected = 42
    assert services._coerce_service_int(expected) == expected  # ruff: ignore[private-member-access]


def test_coerce_service_int_returns_whole_float() -> None:
    """Integral floats are converted without truncation."""
    expected = 3
    assert services._coerce_service_int(3.0) == expected  # ruff: ignore[private-member-access]


def test_coerce_service_int_rejects_fractional_float() -> None:
    """Fractional floats must not be silently truncated."""
    with pytest.raises(vol.Invalid), _ignore_private():
        services._coerce_service_int(3.5)  # ruff: ignore[private-member-access]


def test_coerce_service_int_rejects_infinite_float() -> None:
    """Infinite floats are not whole numbers."""
    with pytest.raises(vol.Invalid), _ignore_private():
        services._coerce_service_int(float("inf"))  # ruff: ignore[private-member-access]


@pytest.mark.parametrize(
    ["raw", "expected"],
    [[" 7 ", 7], ["+42", 42], ["-42", -42]],
)
def test_coerce_service_int_parses_digit_strings(raw: object, expected: int) -> None:
    """Signed and padded decimal strings parse to integers."""
    assert services._coerce_service_int(raw) == expected  # ruff: ignore[private-member-access]


def test_coerce_service_int_rejects_whitespace_string() -> None:
    """Whitespace-only strings are not integers."""
    with pytest.raises(vol.Invalid), _ignore_private():
        services._coerce_service_int("   ")  # ruff: ignore[private-member-access]


def test_coerce_service_int_rejects_non_decimal_string() -> None:
    """Non-ASCII or non-decimal digits are rejected."""
    with pytest.raises(vol.Invalid), _ignore_private():
        services._coerce_service_int("12a")  # ruff: ignore[private-member-access]


def test_coerce_service_int_rejects_unsupported_type() -> None:
    """Containers are never valid integers."""
    with pytest.raises(vol.Invalid), _ignore_private():
        services._coerce_service_int([1])  # ruff: ignore[private-member-access]


@pytest.mark.parametrize("raw", [True, False])
def test_coerce_service_float_rejects_bool(raw: object) -> None:
    """Booleans are never valid service floats."""
    with pytest.raises(vol.Invalid), _ignore_private():
        services._coerce_service_float(raw)  # ruff: ignore[private-member-access]


def test_coerce_service_float_rejects_unsupported_type() -> None:
    """Containers are never valid floats."""
    with pytest.raises(vol.Invalid), _ignore_private():
        services._coerce_service_float([])  # ruff: ignore[private-member-access]


def test_coerce_service_float_parses_values() -> None:
    """Ints and numeric strings convert to floats."""
    expected = 42.0
    assert services._coerce_service_float(42) == expected  # ruff: ignore[private-member-access]
    assert services._coerce_service_float("2.5") == 2.5  # ruff: ignore[private-member-access,float-equality-comparison,magic-value-comparison]


def test_coerce_service_float_rejects_overflow_object() -> None:
    """Objects whose __float__ overflows raise vol.Invalid."""
    with pytest.raises(vol.Invalid), _ignore_private():
        services._coerce_service_float(_OverflowFloat())  # ruff: ignore[private-member-access]


@pytest.mark.parametrize("raw", ["inf", "nan", "foo"])
def test_coerce_service_float_rejects_non_finite(raw: object) -> None:
    """Non-finite and non-numeric text is rejected."""
    with pytest.raises(vol.Invalid), _ignore_private():
        services._coerce_service_float(raw)  # ruff: ignore[private-member-access]


def test_json_native_value_passes_scalars() -> None:
    """JSON scalars round-trip unchanged."""
    assert services._json_native_value(None) is None  # ruff: ignore[private-member-access]
    assert services._json_native_value("hello") == "hello"  # ruff: ignore[private-member-access]
    assert services._json_native_value(True) is True  # ruff: ignore[private-member-access]
    assert services._json_native_value(1) == 1  # ruff: ignore[private-member-access]
    assert services._json_native_value(1.5) == 1.5  # ruff: ignore[private-member-access,float-equality-comparison,magic-value-comparison]


def test_json_native_value_rejects_non_finite_float() -> None:
    """Non-finite floats raise ValueError."""
    with pytest.raises(ValueError, match="finite numbers"), _ignore_private():
        services._json_native_value(float("inf"))  # ruff: ignore[private-member-access]


def test_json_native_value_normalizes_nested_containers() -> None:
    """Lists and dicts recurse into their children."""
    assert services._json_native_value([1, "x", None]) == [1, "x", None]  # ruff: ignore[private-member-access]
    assert services._json_native_value({"a": 1}) == {"a": 1}  # ruff: ignore[private-member-access]


def test_json_native_value_rejects_non_string_keys() -> None:
    """Object keys must be strings."""
    with pytest.raises(TypeError, match="keys must be strings"), _ignore_private():
        services._json_native_value({1: "a"})  # ruff: ignore[private-member-access]


def test_json_native_value_rejects_unsupported_values() -> None:
    """Arbitrary objects are not JSON-compatible."""
    with pytest.raises(ValueError, match="JSON-compatible values"), _ignore_private():
        services._json_native_value(object())  # ruff: ignore[private-member-access]


def test_json_native_body_returns_normalized_dict() -> None:
    """A JSON-native dict body passes through."""
    assert services._json_native_body({"a": 1}, "dev") == {"a": 1}  # ruff: ignore[private-member-access]


def test_json_native_body_rejects_non_string_keys() -> None:
    """Non-string keys surface as translated validation errors."""
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        services._json_native_body({1: "a"}, "dev")  # ruff: ignore[private-member-access]
    assert err.value.translation_key == "send_ble_command_failed"


def test_json_native_body_rejects_non_dict_body() -> None:
    """A list body is redacted and rejected."""
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        # pyrefly: ignore [bad-argument-type]
        services._json_native_body([1], "dev")  # ruff: ignore[private-member-access]
    assert err.value.translation_key == "send_ble_command_failed"
    assert "**REDACTED**" in _translation_placeholder(err.value)


def test_ble_body_accepts_mapping() -> None:
    """Dict bodies normalize directly."""
    assert services._ble_body_from_service({"cmd": 1}, "dev") == {"cmd": 1}  # ruff: ignore[private-member-access]


def test_ble_body_accepts_json_object_string() -> None:
    """JSON object strings parse to dicts."""
    assert services._ble_body_from_service('{"cmd": 1}', "dev") == {"cmd": 1}  # ruff: ignore[private-member-access]


def test_ble_body_rejects_json_array_string() -> None:
    """Non-object JSON text is rejected."""
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        services._ble_body_from_service("[1]", "dev")  # ruff: ignore[private-member-access]
    assert "body JSON must be an object" in _translation_placeholder(err.value)


def test_ble_body_rejects_malformed_json() -> None:
    """Unparseable JSON strings are rejected."""
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        services._ble_body_from_service("{", "dev")  # ruff: ignore[private-member-access]
    assert "body is not valid JSON" in _translation_placeholder(err.value)


def test_ble_body_rejects_non_container_value() -> None:
    """Integers are neither mapping nor JSON string."""
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        services._ble_body_from_service(42, "dev")  # ruff: ignore[private-member-access]
    assert "must be a mapping or JSON object string" in (
        _translation_placeholder(err.value)
    )


def test_tou_tasks_accepts_list_body() -> None:
    """A list of task dicts passes through."""
    assert len(services._tou_tasks_from_service([{"cmd": 1}], "dev")) == 1  # ruff: ignore[private-member-access]


def test_tou_tasks_extracts_tasks_from_dict() -> None:
    """Dict bodies with a tasks key yield the task list."""
    assert len(services._tou_tasks_from_service({"tasks": [{"a": 1}]}, "dev")) == 1  # ruff: ignore[private-member-access]


def test_tou_tasks_rejects_dict_without_tasks() -> None:
    """Dicts without a tasks key are rejected."""
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        services._tou_tasks_from_service({"foo": 1}, "dev")  # ruff: ignore[private-member-access]
    assert err.value.translation_key == "save_tou_plan_failed"
    assert "body must be a tasks list" in _translation_placeholder(err.value)


def test_tou_tasks_rejects_malformed_json() -> None:
    """Unparseable JSON strings are rejected."""
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        services._tou_tasks_from_service("{bad", "dev")  # ruff: ignore[private-member-access]
    assert "body is not valid JSON" in _translation_placeholder(err.value)


def test_tou_tasks_rejects_non_dict_task() -> None:
    """Tasks must be JSON objects."""
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        services._tou_tasks_from_service(["string"], "dev")  # ruff: ignore[private-member-access]
    assert "each TOU task must be a JSON object" in (
        _translation_placeholder(err.value)
    )


@pytest.mark.parametrize(
    ["raw", "expected"],
    [[True, True], [False, False], ["true", True], ["false", False], [1, True]],
)
def test_service_bool_parses_boolean_values(raw: object, expected: bool) -> None:
    """Boolean-ish service values parse to booleans."""
    assert (
        services._service_bool(  # ruff: ignore[private-member-access]
            raw, field_name="f", translation_key="k", device_id="d"
        )
        is expected
    )


@pytest.mark.parametrize("raw", [None, "maybe", []])
def test_service_bool_rejects_non_boolean(raw: object) -> None:
    """Non-boolean values raise a translated field error."""
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        services._service_bool(raw, field_name="f", translation_key="k", device_id="d")  # ruff: ignore[private-member-access]
    assert "f must be a boolean" in _translation_placeholder(err.value)


def test_service_required_text_strips_value() -> None:
    """Required text trims surrounding whitespace."""
    assert (
        services._service_required_text(  # ruff: ignore[private-member-access]
            "  hi  ",
            field_name="f",
            translation_key="k",
            device_id="d",
            max_length=10,
        )
        == "hi"
    )


def test_service_required_text_rejects_non_text() -> None:
    """Non-string required text raises a translated error."""
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        services._service_required_text(  # ruff: ignore[private-member-access]
            1, field_name="f", translation_key="k", device_id="d", max_length=10
        )
    assert "f must be text" in _translation_placeholder(err.value)


def test_service_required_text_rejects_blank() -> None:
    """Blank required text raises a translated error."""
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        services._service_required_text(  # ruff: ignore[private-member-access]
            "", field_name="f", translation_key="k", device_id="d", max_length=10
        )
    assert "f must not be empty" in _translation_placeholder(err.value)


def test_service_required_text_rejects_overlong() -> None:
    """Text beyond max_length raises a translated error."""
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        services._service_required_text(  # ruff: ignore[private-member-access]
            "x" * 11, field_name="f", translation_key="k", device_id="d", max_length=10
        )
    assert "f must be at most 10 characters" in (_translation_placeholder(err.value))


def test_service_optional_text_defaults_none_to_empty() -> None:
    """None optional text resolves to the empty string."""
    assert (
        services._service_optional_text(  # ruff: ignore[private-member-access]
            None, field_name="f", translation_key="k", device_id="d", max_length=10
        )
        == ""  # ruff: ignore[compare-to-empty-string]
    )


def test_service_optional_text_keeps_value() -> None:
    """Optional text is not stripped."""
    assert (
        services._service_optional_text(  # ruff: ignore[private-member-access]
            "  hi  ",
            field_name="f",
            translation_key="k",
            device_id="d",
            max_length=10,
        )
        == "  hi  "
    )


def test_service_optional_text_rejects_non_text() -> None:
    """Non-string optional text raises a translated error."""
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        services._service_optional_text(  # ruff: ignore[private-member-access]
            1, field_name="f", translation_key="k", device_id="d", max_length=10
        )
    assert "f must be text" in _translation_placeholder(err.value)


def test_service_optional_text_rejects_overlong() -> None:
    """Optional text beyond max_length raises."""
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        services._service_optional_text(  # ruff: ignore[private-member-access]
            "x" * 11, field_name="f", translation_key="k", device_id="d", max_length=10
        )
    assert "f must be at most 10 characters" in (_translation_placeholder(err.value))


def test_service_int_accepts_in_range() -> None:
    """In-range integers pass through."""
    expected = 5
    assert (
        services._service_int(  # ruff: ignore[private-member-access]
            expected,
            field_name="f",
            translation_key="k",
            device_id="d",
            bounds=(1, 10),
        )
        == expected
    )


@pytest.mark.parametrize("raw", [0, 11])
def test_service_int_rejects_out_of_range(raw: object) -> None:
    """Out-of-range integers raise a translated bound error."""
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        services._service_int(  # ruff: ignore[private-member-access]
            raw, field_name="f", translation_key="k", device_id="d", bounds=(1, 10)
        )
    assert "f must be between 1 and 10" in _translation_placeholder(err.value)


def test_service_int_rejects_non_integer() -> None:
    """Non-integer input raises a translated type error."""
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        services._service_int(  # ruff: ignore[private-member-access]
            "x", field_name="f", translation_key="k", device_id="d", bounds=(1, 10)
        )
    assert "f must be an integer" in _translation_placeholder(err.value)


def test_service_float_accepts_in_range() -> None:
    """In-range floats pass through."""
    assert (
        services._service_float(  # ruff: ignore[private-member-access,float-equality-comparison]
            5.5,
            field_name="f",
            translation_key="k",
            device_id="d",
            bounds=(1.0, 10.0),
        )
        == 5.5  # ruff: ignore[magic-value-comparison]
    )


@pytest.mark.parametrize("raw", [0.5, 11.0])
def test_service_float_rejects_out_of_range(raw: object) -> None:
    """Out-of-range floats raise a translated bound error."""
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        services._service_float(  # ruff: ignore[private-member-access]
            raw,
            field_name="f",
            translation_key="k",
            device_id="d",
            bounds=(1.0, 10.0),
        )
    assert "f must be between 1.0 and 10.0" in (_translation_placeholder(err.value))


def test_service_float_rejects_non_number() -> None:
    """Non-numeric input raises a translated type error."""
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        services._service_float(  # ruff: ignore[private-member-access]
            "x", field_name="f", translation_key="k", device_id="d", bounds=(1.0, 10.0)
        )
    assert "f must be a number" in _translation_placeholder(err.value)


def test_reject_json_constant_names_the_constant() -> None:
    """The constant name is part of the rejection message."""
    with (
        pytest.raises(ValueError, match="invalid JSON constant: NaN"),
        _ignore_private(),
    ):
        services._reject_json_constant("NaN")  # ruff: ignore[private-member-access]


@pytest.mark.parametrize(
    ["raw", "expected"],
    [["12345", "12345"], ["12345_smart_plug_1", "12345"], ["", ""]],
)
def test_strip_jackery_subdevice_suffix(raw: str, expected: str) -> None:
    """Only digit-rooted ids get their suffix removed."""
    assert services._strip_jackery_subdevice_suffix(raw) == expected  # ruff: ignore[private-member-access]


def test_service_validation_error_sets_placeholders() -> None:
    """The validation error carries domain, key and base placeholders."""
    err = services._service_validation_error("key", device_id="d", error="oops")  # ruff: ignore[private-member-access]
    assert err.translation_domain == DOMAIN
    assert err.translation_key == "key"
    assert err.translation_placeholders == {"device_id": "d", "error": "oops"}


def test_service_validation_error_merges_extra_placeholders() -> None:
    """Extra placeholders are merged into the error."""
    err = services._service_validation_error(  # ruff: ignore[private-member-access]
        "key", device_id="d", error="oops", extra_placeholders={"alert_id": "a1"}
    )
    assert _translation_placeholder(err, "alert_id") == "a1"


def test_service_action_error_wraps_home_assistant_error() -> None:
    """The action error is a translated HomeAssistantError."""
    err = services._service_action_error("key", device_id="d", error="oops")  # ruff: ignore[private-member-access]
    assert isinstance(err, HomeAssistantError)
    assert err.translation_domain == DOMAIN


def test_service_action_error_merges_extra_placeholders() -> None:
    """Extra placeholders are merged into action errors too."""
    err = services._service_action_error(  # ruff: ignore[private-member-access]
        "key", device_id="d", error="oops", extra_placeholders={"extra": "v"}
    )
    assert _translation_placeholder(err, "extra") == "v"


def test_device_id_from_service_rejects_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Whitespace-only device ids raise before registry lookup."""
    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _h, _r: "x")
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        services._device_id_from_service(_test_hass(), "  ", translation_key="k")  # ruff: ignore[private-member-access]
    assert "device_id must not be empty" in _translation_placeholder(err.value)


def test_device_id_from_service_rejects_non_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-string device ids raise a translated type error."""
    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _h, _r: "x")
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        services._device_id_from_service(_test_hass(), 42, translation_key="k")  # ruff: ignore[private-member-access]
    assert "device_id must be text" in _translation_placeholder(err.value)


def test_rename_name_rejects_non_text() -> None:
    """A non-string new_name raises a translated type error."""
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        services._rename_name_from_service(42, "123")  # ruff: ignore[private-member-access]
    assert "new_name must be text" in _translation_placeholder(err.value)


def test_storm_alert_id_rejects_non_text() -> None:
    """A non-string alert id raises a translated type error."""
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        services._storm_alert_id_from_service(42, "dev")  # ruff: ignore[private-member-access]
    assert "alert_id must be text" in _translation_placeholder(err.value)


def test_coordinator_for_device_finds_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The coordinator owning the device id is returned."""
    coordinator = _fake_coordinator({"123": {}})
    monkeypatch.setattr(services, "_loaded_coordinators", lambda _h: [coordinator])
    assert services._coordinator_for_device(_test_hass(), "123") is coordinator  # ruff: ignore[private-member-access]


def test_coordinator_for_device_skips_non_matching_then_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Coordinators without the device loop past; None when nobody owns it."""
    other = _fake_coordinator({"other": {}})
    missing_data = _fake_coordinator(None)
    monkeypatch.setattr(
        services, "_loaded_coordinators", lambda _h: [other, missing_data]
    )
    assert services._coordinator_for_device(_test_hass(), "123") is None  # ruff: ignore[private-member-access]


def test_coordinator_for_system_finds_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The coordinator whose payload declares the system id is returned."""
    coordinator = _fake_coordinator({"dev1": {"system": {"id": "sys1"}}})
    monkeypatch.setattr(services, "_loaded_coordinators", lambda _h: [coordinator])
    assert services._coordinator_for_system(_test_hass(), "sys1") is coordinator  # ruff: ignore[private-member-access]


def test_coordinator_for_system_checks_both_id_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SystemId matches via the second documented key."""
    coordinator = _fake_coordinator({"dev1": {"system": {"systemId": "sys1"}}})
    monkeypatch.setattr(services, "_loaded_coordinators", lambda _h: [coordinator])
    assert services._coordinator_for_system(_test_hass(), "sys1") is coordinator  # ruff: ignore[private-member-access]


def test_coordinator_for_system_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """No coordinator and no payload match yields None."""
    monkeypatch.setattr(services, "_loaded_coordinators", lambda _h: [])
    assert services._coordinator_for_system(_test_hass(), "sys1") is None  # ruff: ignore[private-member-access]


def test_is_portable_device_false_for_home_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Home-payload evidence always outranks portable classification."""
    monkeypatch.setattr(services, "_payload_has_home_payload_evidence", lambda _p: True)
    assert services._is_portable_device(_fake_coordinator({}), "dev1") is False  # ruff: ignore[private-member-access]


@pytest.mark.parametrize("section", [PAYLOAD_DEVICE, PAYLOAD_DISCOVERY])
def test_is_portable_device_true_for_legacy_bind_list(
    monkeypatch: pytest.MonkeyPatch, section: str
) -> None:
    """Either documented section may carry the legacy bind-list marker."""
    monkeypatch.setattr(
        services, "_payload_has_home_payload_evidence", lambda _p: False
    )
    coordinator = _fake_coordinator({
        "dev1": {
            section: {PAYLOAD_DISCOVERY_SOURCE: (DISCOVERY_SOURCE_LEGACY_BIND_LIST)}
        }
    })
    assert services._is_portable_device(coordinator, "dev1") is True  # ruff: ignore[private-member-access]


def test_is_portable_device_false_for_other_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A different discovery source does not mark the device portable."""
    monkeypatch.setattr(
        services, "_payload_has_home_payload_evidence", lambda _p: False
    )
    coordinator = _fake_coordinator({
        "dev1": {PAYLOAD_DEVICE: {PAYLOAD_DISCOVERY_SOURCE: "cloud_bind"}}
    })
    assert services._is_portable_device(coordinator, "dev1") is False  # ruff: ignore[private-member-access]


def test_is_portable_device_false_for_non_dict_section(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A non-dict section payload cannot carry the marker."""
    monkeypatch.setattr(
        services, "_payload_has_home_payload_evidence", lambda _p: False
    )
    coordinator = _fake_coordinator({"dev1": {PAYLOAD_DEVICE: "not-a-dict"}})
    assert services._is_portable_device(coordinator, "dev1") is False  # ruff: ignore[private-member-access]


def test_is_portable_device_false_for_unknown_device(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unknown device ids are not portable."""
    monkeypatch.setattr(
        services, "_payload_has_home_payload_evidence", lambda _p: False
    )
    assert services._is_portable_device(_fake_coordinator({}), "dev1") is False  # ruff: ignore[private-member-access]


def test_raise_if_portable_home_service_allows_home_devices(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Home-family devices pass the portable guard untouched."""
    monkeypatch.setattr(services, "_is_portable_device", lambda _c, _d: False)
    services._raise_if_portable_home_service(  # ruff: ignore[private-member-access]
        _fake_coordinator({}), "dev1", translation_key="k", service_name="s"
    )


def test_raise_if_portable_home_service_rejects_portables(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Portable devices hit the translated Home-family error."""
    monkeypatch.setattr(services, "_is_portable_device", lambda _c, _d: True)
    with pytest.raises(ServiceValidationError) as err, _ignore_private():
        services._raise_if_portable_home_service(  # ruff: ignore[private-member-access]
            _fake_coordinator({}),
            "dev1",
            translation_key="k",
            service_name="rename_system",
        )
    assert err.value.translation_key == "k"
    assert "rename_system" in _translation_placeholder(err.value)


def test_loaded_coordinators_keeps_only_typed_runtime_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Entries without a coordinator runtime_data are skipped."""
    coordinator = Mock(spec=JackerySolarVaultCoordinator)
    entries = [
        SimpleNamespace(runtime_data=coordinator),
        SimpleNamespace(runtime_data=object()),
        SimpleNamespace(),
    ]
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_loaded_entries=lambda _domain: entries)
    )
    with _ignore_private():
        result = services._loaded_coordinators(cast("HomeAssistant", hass))  # ruff: ignore[private-member-access]
    assert result == [coordinator]


async def test_get_share_qr_code_returns_response_envelope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The QR service returns the qrCodeId/userId envelope after notifying."""
    coordinator = _fake_coordinator({"dev1": {}})
    monkeypatch.setattr(services, "_resolve_jackery_device_id", lambda _h, raw: raw)
    monkeypatch.setattr(services, "_loaded_coordinators", lambda _h: [coordinator])
    # pyrefly: ignore [missing-attribute]
    coordinator.async_get_share_qr_code = AsyncMock(
        return_value={FIELD_QR_CODE_ID: "qr-1", FIELD_USER_ID: "user-1"}
    )
    notify = AsyncMock()
    monkeypatch.setattr(services, "_notify_share_qr_code", notify)

    response = await services._async_handle_get_share_qr_code(  # ruff: ignore[private-member-access]
        _test_hass(),
        _service_call({SERVICE_FIELD_DEVICE_ID: "dev1"}),
    )

    assert response == {
        SERVICE_RESPONSE_QR_CODE_ID: "qr-1",
        SERVICE_RESPONSE_USER_ID: "user-1",
    }
    notify.assert_awaited_once()


def test_coordinator_for_system_skips_payload_without_system(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Payloads lacking a system section cannot match any system id."""
    coordinator = _fake_coordinator({"dev1": {}})
    monkeypatch.setattr(services, "_loaded_coordinators", lambda _h: [coordinator])
    with _ignore_private():
        assert services._coordinator_for_system(_test_hass(), "sys1") is None  # ruff: ignore[private-member-access]


async def test_setup_services_is_idempotent(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A second setup keeps the first registration instead of re-adding it."""
    monkeypatch.setattr(services, "async_setup_services", services.async_setup_services)
    await services.async_setup_services(hass)
    registered = set(hass.services.async_services().get(DOMAIN, {}))
    await services.async_setup_services(hass)
    assert registered
    assert registered == set(hass.services.async_services().get(DOMAIN, {}))
