"""New services tests targeting the ~90% coverage gap.

Focus on the uncovered lines from services.py coverage report.
"""

from datetime import UTC
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, Mock, patch

import pytest

from custom_components.jackery_solarvault import services
from custom_components.jackery_solarvault.client.api import (
    JackeryAuthError,
    JackeryError,
)
from custom_components.jackery_solarvault.const import (
    DOMAIN,
    SERVICE_BIND_CURRENCY,
    SERVICE_CHECK_SYSTEM_BOUND,
    SERVICE_REPORT_DEVICE_TIMEZONE,
    SERVICE_SET_AC_NICKNAME,
    SERVICE_UNBIND_ACCESSORIES,
)
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

if TYPE_CHECKING:
    from homeassistant.core import ServiceResponse

# Disable pytest_homeassistant_custom_component auto-setup for these tests
pytestmark = pytest.mark.unit


def _make_coordinator() -> AsyncMock:
    coordinator = AsyncMock()
    coordinator.config_entry.async_start_reauth = Mock()
    coordinator.data = {}  # keeps portable-device guards from firing
    return coordinator


def _make_service_call(data: dict[str, Any]) -> SimpleNamespace:
    call = SimpleNamespace()
    call.data = data
    return call


async def _registered_handler(hass: SimpleNamespace, service_name: str):  # ruff: ignore[missing-return-type-private-function]
    await services.async_setup_services(hass)
    for call in hass.services.async_register.call_args_list:
        if call[0][0] == DOMAIN and call[0][1] == service_name:
            return call[0][2]
    return None


def _make_hass() -> SimpleNamespace:
    """Create a mock HomeAssistant with minimal required setup."""
    hass = SimpleNamespace()
    hass.data = {}
    # Device registry mock - needed for _resolve_jackery_device_id
    from homeassistant.helpers import device_registry as dr  # ruff: ignore[import-outside-top-level]

    mock_registry = SimpleNamespace()
    mock_registry.async_get = Mock(
        return_value=None
    )  # registry.async_get(device_id) returns None
    mock_registry.devices = {}
    mock_registry.async_load = (
        AsyncMock()
    )  # needed by pytest-homeassistant-custom-component
    mock_registry.async_wait_loaded = (
        AsyncMock()
    )  # needed by entity_registry async_load
    # ``dr.async_get(hass)`` reads the registry from Home Assistant data.
    hass.data[dr.DATA_REGISTRY] = mock_registry
    hass.services = SimpleNamespace()
    hass.services.async_register = Mock()
    hass.services.has_service = Mock(return_value=False)
    hass.bus = SimpleNamespace()
    hass.bus.async_listen = Mock()
    # Add time zone for dt_util
    hass.config = SimpleNamespace()
    hass.config.time_zone = UTC
    return hass


class TestServiceBindCurrency:
    """Test SERVICE_BIND_CURRENCY handler."""

    @pytest.mark.asyncio()
    async def test_bind_currency_success(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        hass = _make_hass()
        coordinator = _make_coordinator()
        coordinator.async_bind_currency = AsyncMock(return_value=None)
        with patch.object(
            services, "_coordinator_for_device", return_value=coordinator
        ):
            await services.async_setup_services(hass)
            handler = await _registered_handler(hass, SERVICE_BIND_CURRENCY)
            assert handler is not None

            await handler(
                _make_service_call({"device_id": "test_device", "currency": "EUR"})
            )

            coordinator.async_bind_currency.assert_called_once_with(
                "test_device", "EUR"
            )

    @pytest.mark.asyncio()
    async def test_bind_currency_auth_error(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        hass = _make_hass()
        coordinator = _make_coordinator()
        coordinator.async_bind_currency = AsyncMock(
            side_effect=JackeryAuthError("auth failed")
        )
        with patch.object(
            services, "_coordinator_for_device", return_value=coordinator
        ):
            await services.async_setup_services(hass)
            handler = await _registered_handler(hass, SERVICE_BIND_CURRENCY)
            assert handler is not None

            with pytest.raises(ConfigEntryAuthFailed):
                await handler(
                    _make_service_call({"device_id": "test_device", "currency": "EUR"})
                )

    @pytest.mark.asyncio()
    async def test_bind_currency_error(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        hass = _make_hass()
        coordinator = _make_coordinator()
        coordinator.async_bind_currency = AsyncMock(side_effect=JackeryError("error"))
        with patch.object(
            services, "_coordinator_for_device", return_value=coordinator
        ):
            await services.async_setup_services(hass)
            handler = await _registered_handler(hass, SERVICE_BIND_CURRENCY)
            assert handler is not None

            with pytest.raises(HomeAssistantError):
                await handler(
                    _make_service_call({"device_id": "test_device", "currency": "EUR"})
                )

    @pytest.mark.asyncio()
    async def test_bind_currency_no_coordinator(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        hass = _make_hass()
        with patch.object(services, "_coordinator_for_device", return_value=None):
            await services.async_setup_services(hass)
            handler = await _registered_handler(hass, SERVICE_BIND_CURRENCY)
            assert handler is not None

            with pytest.raises(HomeAssistantError):
                await handler(
                    _make_service_call({"device_id": "test_device", "currency": "EUR"})
                )


class TestServiceCheckSystemBound:
    """Test SERVICE_CHECK_SYSTEM_BOUND handler."""

    @pytest.mark.asyncio()
    async def test_check_system_bound_true(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        hass = _make_hass()
        coordinator = _make_coordinator()
        coordinator.async_check_system_bound = AsyncMock(return_value=True)
        with patch.object(
            services, "_coordinator_for_device", return_value=coordinator
        ):
            await services.async_setup_services(hass)
            handler = await _registered_handler(hass, SERVICE_CHECK_SYSTEM_BOUND)
            assert handler is not None

            result: ServiceResponse = await handler(
                _make_service_call({
                    "device_id": "test_device",
                    "bind_key": "bk",
                    "device_sn": "sn",
                    "guid": "guid",
                })
            )

            coordinator.async_check_system_bound.assert_called_once_with(
                bind_key="bk", device_sn="sn", guid="guid"
            )
            assert result == {"exists": True}

    @pytest.mark.asyncio()
    async def test_check_system_bound_false(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        hass = _make_hass()
        coordinator = _make_coordinator()
        coordinator.async_check_system_bound = AsyncMock(return_value=False)
        with patch.object(
            services, "_coordinator_for_device", return_value=coordinator
        ):
            await services.async_setup_services(hass)
            handler = await _registered_handler(hass, SERVICE_CHECK_SYSTEM_BOUND)
            assert handler is not None

            result: ServiceResponse = await handler(
                _make_service_call({
                    "device_id": "test_device",
                    "bind_key": "bk",
                    "device_sn": "sn",
                    "guid": "guid",
                })
            )

            coordinator.async_check_system_bound.assert_called_once_with(
                bind_key="bk", device_sn="sn", guid="guid"
            )
            assert result == {"exists": False}

    @pytest.mark.asyncio()
    async def test_check_system_bound_auth_error(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        hass = _make_hass()
        coordinator = _make_coordinator()
        coordinator.async_check_system_bound = AsyncMock(
            side_effect=JackeryAuthError("auth failed")
        )
        with patch.object(
            services, "_coordinator_for_device", return_value=coordinator
        ):
            await services.async_setup_services(hass)
            handler = await _registered_handler(hass, SERVICE_CHECK_SYSTEM_BOUND)
            assert handler is not None

            with pytest.raises(ConfigEntryAuthFailed):
                await handler(
                    _make_service_call({
                        "device_id": "test_device",
                        "bind_key": "bk",
                        "device_sn": "sn",
                        "guid": "guid",
                    })
                )

    @pytest.mark.asyncio()
    async def test_check_system_bound_error(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        hass = _make_hass()
        coordinator = _make_coordinator()
        coordinator.async_check_system_bound = AsyncMock(
            side_effect=JackeryError("error")
        )
        with patch.object(
            services, "_coordinator_for_device", return_value=coordinator
        ):
            await services.async_setup_services(hass)
            handler = await _registered_handler(hass, SERVICE_CHECK_SYSTEM_BOUND)
            assert handler is not None

            with pytest.raises(HomeAssistantError):
                await handler(
                    _make_service_call({
                        "device_id": "test_device",
                        "bind_key": "bk",
                        "device_sn": "sn",
                        "guid": "guid",
                    })
                )


class TestServiceUnbindAccessories:
    """Test SERVICE_UNBIND_ACCESSORIES handler."""

    @pytest.mark.asyncio()
    async def test_unbind_accessories_success(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        hass = _make_hass()
        coordinator = _make_coordinator()
        coordinator.async_unbind_accessories = AsyncMock(return_value={"ok": True})
        with patch.object(
            services, "_coordinator_for_device", return_value=coordinator
        ):
            await services.async_setup_services(hass)
            handler = await _registered_handler(hass, SERVICE_UNBIND_ACCESSORIES)
            assert handler is not None

            result: ServiceResponse = await handler(
                _make_service_call({"device_id": "test_device", "bind_ids": ["1", "2"]})
            )

            coordinator.async_unbind_accessories.assert_called_once_with(["1", "2"])
            assert result == {"result": {"ok": True}}

    @pytest.mark.asyncio()
    async def test_unbind_accessories_auth_error(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        hass = _make_hass()
        coordinator = _make_coordinator()
        coordinator.async_unbind_accessories = AsyncMock(
            side_effect=JackeryAuthError("auth failed")
        )
        with patch.object(
            services, "_coordinator_for_device", return_value=coordinator
        ):
            await services.async_setup_services(hass)
            handler = await _registered_handler(hass, SERVICE_UNBIND_ACCESSORIES)
            assert handler is not None

            with pytest.raises(ConfigEntryAuthFailed):
                await handler(
                    _make_service_call({
                        "device_id": "test_device",
                        "bind_ids": ["1", "2"],
                    })
                )

    @pytest.mark.asyncio()
    async def test_unbind_accessories_error(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        hass = _make_hass()
        coordinator = _make_coordinator()
        coordinator.async_unbind_accessories = AsyncMock(
            side_effect=JackeryError("error")
        )
        with patch.object(
            services, "_coordinator_for_device", return_value=coordinator
        ):
            await services.async_setup_services(hass)
            handler = await _registered_handler(hass, SERVICE_UNBIND_ACCESSORIES)
            assert handler is not None

            with pytest.raises(HomeAssistantError):
                await handler(
                    _make_service_call({
                        "device_id": "test_device",
                        "bind_ids": ["1", "2"],
                    })
                )


class TestServiceSetAcNickname:
    """Test SERVICE_SET_AC_NICKNAME handler."""

    @pytest.mark.asyncio()
    async def test_set_ac_nickname_success(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        hass = _make_hass()
        coordinator = _make_coordinator()
        coordinator.async_set_ac_nickname = AsyncMock(return_value=None)
        with patch.object(
            services, "_coordinator_for_device", return_value=coordinator
        ):
            await services.async_setup_services(hass)
            handler = await _registered_handler(hass, SERVICE_SET_AC_NICKNAME)
            assert handler is not None

            await handler(
                _make_service_call({
                    "device_id": "test_device",
                    "ac_port": 1,
                    "nickname": "Kueche",
                })
            )

            coordinator.async_set_ac_nickname.assert_called_once_with(
                "test_device", ac_port=1, name="Kueche"
            )

    @pytest.mark.asyncio()
    async def test_set_ac_nickname_auth_error(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        hass = _make_hass()
        coordinator = _make_coordinator()
        coordinator.async_set_ac_nickname = AsyncMock(
            side_effect=JackeryAuthError("auth failed")
        )
        with patch.object(
            services, "_coordinator_for_device", return_value=coordinator
        ):
            await services.async_setup_services(hass)
            handler = await _registered_handler(hass, SERVICE_SET_AC_NICKNAME)
            assert handler is not None

            with pytest.raises(ConfigEntryAuthFailed):
                await handler(
                    _make_service_call({
                        "device_id": "test_device",
                        "ac_port": 1,
                        "nickname": "Kueche",
                    })
                )

    @pytest.mark.asyncio()
    async def test_set_ac_nickname_error(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        hass = _make_hass()
        coordinator = _make_coordinator()
        coordinator.async_set_ac_nickname = AsyncMock(side_effect=JackeryError("error"))
        with patch.object(
            services, "_coordinator_for_device", return_value=coordinator
        ):
            await services.async_setup_services(hass)
            handler = await _registered_handler(hass, SERVICE_SET_AC_NICKNAME)
            assert handler is not None

            with pytest.raises(HomeAssistantError):
                await handler(
                    _make_service_call({
                        "device_id": "test_device",
                        "ac_port": 1,
                        "nickname": "Kueche",
                    })
                )


class TestServiceReportDeviceTimezone:
    """Test SERVICE_REPORT_DEVICE_TIMEZONE handler."""

    @pytest.mark.asyncio()
    async def test_report_device_timezone_success(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        hass = _make_hass()
        coordinator = _make_coordinator()
        coordinator.async_report_device_timezone = AsyncMock(return_value=None)
        with patch.object(
            services, "_coordinator_for_device", return_value=coordinator
        ):
            await services.async_setup_services(hass)
            handler = await _registered_handler(hass, SERVICE_REPORT_DEVICE_TIMEZONE)
            assert handler is not None

            await handler(
                _make_service_call({
                    "device_id": "test_device",
                    "zone_id": "Europe/Berlin",
                    "timezone_offset": 7200,
                })
            )

            coordinator.async_report_device_timezone.assert_called_once_with(
                "test_device", zone_id="Europe/Berlin", time_offset=7200
            )

    @pytest.mark.asyncio()
    async def test_report_device_timezone_auth_error(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        hass = _make_hass()
        coordinator = _make_coordinator()
        coordinator.async_report_device_timezone = AsyncMock(
            side_effect=JackeryAuthError("auth failed")
        )
        with patch.object(
            services, "_coordinator_for_device", return_value=coordinator
        ):
            await services.async_setup_services(hass)
            handler = await _registered_handler(hass, SERVICE_REPORT_DEVICE_TIMEZONE)
            assert handler is not None

            with pytest.raises(ConfigEntryAuthFailed):
                await handler(
                    _make_service_call({
                        "device_id": "test_device",
                        "zone_id": "Europe/Berlin",
                        "timezone_offset": 7200,
                    })
                )

    @pytest.mark.asyncio()
    async def test_report_device_timezone_error(self) -> None:  # ruff: ignore[undocumented-public-method, no-self-use]
        hass = _make_hass()
        coordinator = _make_coordinator()
        coordinator.async_report_device_timezone = AsyncMock(
            side_effect=JackeryError("error")
        )
        with patch.object(
            services, "_coordinator_for_device", return_value=coordinator
        ):
            await services.async_setup_services(hass)
            handler = await _registered_handler(hass, SERVICE_REPORT_DEVICE_TIMEZONE)
            assert handler is not None

            with pytest.raises(HomeAssistantError):
                await handler(
                    _make_service_call({
                        "device_id": "test_device",
                        "zone_id": "Europe/Berlin",
                        "timezone_offset": 7200,
                    })
                )
