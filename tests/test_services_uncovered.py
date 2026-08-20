"""Tests for uncovered paths in services.py to increase coverage."""

from dataclasses import dataclass
from typing import cast
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.jackery_solarvault.const import (
    DOMAIN,
    SERVICE_FIELD_AC_PORT,
    SERVICE_FIELD_BIND_IDS,
    SERVICE_FIELD_DEVICE_ID,
    SERVICE_FIELD_NICKNAME,
    SERVICE_FIELD_TIMEZONE_OFFSET,
    SERVICE_FIELD_ZONE_ID,
)
from custom_components.jackery_solarvault.services import (
    _async_handle_report_device_timezone,
    _async_handle_set_ac_nickname,
    _async_handle_unbind_accessories,
)
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, ServiceValidationError


@dataclass(slots=True)
class _Device:
    identifiers: set[tuple[str, str]]
    via_device_id: str | None = None


class _Registry:
    def __init__(self, devices: dict[str, _Device]) -> None:
        self._devices = devices

    def async_get(self, device_id: str) -> _Device | None:
        return self._devices.get(device_id)

    def async_get_or_create(self, **kwargs) -> Mock:  # noqa: PLR6301
        return Mock()


@dataclass(slots=True)
class _Call:
    data: dict[str, object]


def _test_hass() -> HomeAssistant:
    """Return the deliberately minimal Home Assistant test double."""
    return cast(HomeAssistant, object())


def _service_call(data: dict[str, object]) -> ServiceCall:
    """Type a minimal service call at the test boundary."""
    return cast(ServiceCall, _Call(data))


class _Coordinator:
    def __init__(self) -> None:
        self.async_unbind_accessories = AsyncMock(return_value={"success": True})
        self.async_set_ac_nickname = AsyncMock(return_value=None)
        self.async_report_device_timezone = AsyncMock(return_value=None)
        self.async_get_device_info = AsyncMock(return_value={})
        self.async_get_real_time_data = AsyncMock(return_value={})
        self.refreshed = False

    async def async_request_refresh(self) -> None:
        self.refreshed = True


class TestServices:
    """Test services module handler functions directly."""

    @pytest.mark.asyncio
    async def test_service_unbind_accessories(  # ruff: ignore[no-self-use]
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: E501, PLR6301, RUF100
        """Test _async_handle_unbind_accessories handler."""
        hass = _test_hass()
        coordinator = _Coordinator()

        # Mock device registry
        registry = _Registry({
            "test_device": _Device({(DOMAIN, "test_serial")}),
        })
        monkeypatch.setattr(
            "homeassistant.helpers.device_registry.async_get", lambda h: registry
        )

        # Mock coordinator lookup
        monkeypatch.setattr(
            "custom_components.jackery_solarvault.services._coordinator_for_device",
            lambda h, d: coordinator,
        )

        # Call the handler
        call = _service_call({
            SERVICE_FIELD_DEVICE_ID: "test_device",
            SERVICE_FIELD_BIND_IDS: ["bind1", "bind2"],
        })
        result = await _async_handle_unbind_accessories(hass, call)

        coordinator.async_unbind_accessories.assert_called_once_with(["bind1", "bind2"])
        assert result == {"result": {"success": True}}

    @pytest.mark.asyncio
    async def test_service_unbind_accessories_no_coordinator(  # ruff: ignore[no-self-use]
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: E501, PLR6301, RUF100
        """Test _async_handle_unbind_accessories when no coordinator found."""
        hass = _test_hass()

        # Mock device registry
        registry = _Registry({
            "test_device": _Device({(DOMAIN, "test_serial")}),
        })
        monkeypatch.setattr(
            "homeassistant.helpers.device_registry.async_get", lambda h: registry
        )

        # Mock coordinator lookup to return None
        monkeypatch.setattr(
            "custom_components.jackery_solarvault.services._coordinator_for_device",
            lambda h, d: None,
        )

        # Call the handler - should raise ServiceValidationError
        call = _service_call({
            SERVICE_FIELD_DEVICE_ID: "test_device",
            SERVICE_FIELD_BIND_IDS: ["bind1", "bind2"],
        })
        with pytest.raises(ServiceValidationError) as exc:
            await _async_handle_unbind_accessories(hass, call)
        assert "unbind_accessories_failed" in exc.value.translation_key

    @pytest.mark.asyncio
    async def test_service_unbind_accessories_auth_error(  # ruff: ignore[no-self-use]
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: E501, PLR6301, RUF100
        """Test _async_handle_unbind_accessories with auth error."""
        hass = _test_hass()
        coordinator = _Coordinator()
        coordinator.async_unbind_accessories = AsyncMock(
            side_effect=ConfigEntryAuthFailed("auth failed")
        )  # noqa: E501, RUF100

        # Mock device registry
        registry = _Registry({
            "test_device": _Device({(DOMAIN, "test_serial")}),
        })
        monkeypatch.setattr(
            "homeassistant.helpers.device_registry.async_get", lambda h: registry
        )

        # Mock coordinator lookup
        monkeypatch.setattr(
            "custom_components.jackery_solarvault.services._coordinator_for_device",
            lambda h, d: coordinator,
        )

        # Call the handler - should raise ConfigEntryAuthFailed
        call = _service_call({
            SERVICE_FIELD_DEVICE_ID: "test_device",
            SERVICE_FIELD_BIND_IDS: ["bind1", "bind2"],
        })
        with pytest.raises(ConfigEntryAuthFailed):
            await _async_handle_unbind_accessories(hass, call)

    @pytest.mark.asyncio
    async def test_service_set_ac_nickname(  # ruff: ignore[no-self-use]
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: E501, PLR6301, RUF100
        """Test _async_handle_set_ac_nickname handler."""
        hass = _test_hass()
        coordinator = _Coordinator()

        # Mock device registry
        registry = _Registry({
            "test_device": _Device({(DOMAIN, "test_serial")}),
        })
        monkeypatch.setattr(
            "homeassistant.helpers.device_registry.async_get", lambda h: registry
        )

        # Mock coordinator lookup
        monkeypatch.setattr(
            "custom_components.jackery_solarvault.services._coordinator_for_device",
            lambda h, d: coordinator,
        )

        # Call the handler
        call = _service_call({
            SERVICE_FIELD_DEVICE_ID: "test_device",
            SERVICE_FIELD_NICKNAME: "My AC",
            SERVICE_FIELD_AC_PORT: 1,
        })
        await _async_handle_set_ac_nickname(hass, call)

        coordinator.async_set_ac_nickname.assert_called_once_with(
            "test_serial", ac_port=1, name="My AC"
        )  # noqa: E501, RUF100

    @pytest.mark.asyncio
    async def test_service_set_ac_nickname_no_coordinator(  # ruff: ignore[no-self-use]
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: E501, PLR6301, RUF100
        """Test _async_handle_set_ac_nickname when no coordinator found."""
        hass = _test_hass()

        # Mock device registry
        registry = _Registry({
            "test_device": _Device({(DOMAIN, "test_serial")}),
        })
        monkeypatch.setattr(
            "homeassistant.helpers.device_registry.async_get", lambda h: registry
        )

        # Mock coordinator lookup to return None
        monkeypatch.setattr(
            "custom_components.jackery_solarvault.services._coordinator_for_device",
            lambda h, d: None,
        )

        # Call the handler - should raise ServiceValidationError
        call = _service_call({
            SERVICE_FIELD_DEVICE_ID: "test_device",
            SERVICE_FIELD_NICKNAME: "My AC",
            SERVICE_FIELD_AC_PORT: 1,
        })
        with pytest.raises(ServiceValidationError) as exc:
            await _async_handle_set_ac_nickname(hass, call)
        assert "set_ac_nickname_failed" in exc.value.translation_key

    @pytest.mark.asyncio
    async def test_service_set_ac_nickname_auth_error(  # ruff: ignore[no-self-use]
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: E501, PLR6301, RUF100
        """Test _async_handle_set_ac_nickname with auth error."""
        from custom_components.jackery_solarvault.client import JackeryAuthError  # noqa: I001

        hass = _test_hass()
        coordinator = _Coordinator()
        coordinator.async_set_ac_nickname = AsyncMock(
            side_effect=JackeryAuthError("auth failed")
        )  # noqa: E501, RUF100

        # Mock device registry
        registry = _Registry({
            "test_device": _Device({(DOMAIN, "test_serial")}),
        })
        monkeypatch.setattr(
            "homeassistant.helpers.device_registry.async_get", lambda h: registry
        )

        # Mock coordinator lookup
        monkeypatch.setattr(
            "custom_components.jackery_solarvault.services._coordinator_for_device",
            lambda h, d: coordinator,
        )

        # Call the handler - should raise ConfigEntryAuthFailed (wrapped from JackeryAuthError)
        call = _service_call({
            SERVICE_FIELD_DEVICE_ID: "test_device",
            SERVICE_FIELD_NICKNAME: "My AC",
            SERVICE_FIELD_AC_PORT: 1,
        })
        with pytest.raises(ConfigEntryAuthFailed):
            await _async_handle_set_ac_nickname(hass, call)

    @pytest.mark.asyncio
    async def test_service_report_device_timezone(  # ruff: ignore[no-self-use]
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: E501, PLR6301, RUF100
        """Test _async_handle_report_device_timezone handler."""
        hass = _test_hass()
        coordinator = _Coordinator()

        # Mock device registry
        registry = _Registry({
            "test_device": _Device({(DOMAIN, "test_serial")}),
        })
        monkeypatch.setattr(
            "homeassistant.helpers.device_registry.async_get", lambda h: registry
        )

        # Mock coordinator lookup
        monkeypatch.setattr(
            "custom_components.jackery_solarvault.services._coordinator_for_device",
            lambda h, d: coordinator,
        )

        # Call the handler
        call = _service_call({
            SERVICE_FIELD_DEVICE_ID: "test_device",
            SERVICE_FIELD_TIMEZONE_OFFSET: 3600,
            SERVICE_FIELD_ZONE_ID: "zone1",
        })
        await _async_handle_report_device_timezone(hass, call)

        coordinator.async_report_device_timezone.assert_called_once_with(
            "test_serial", zone_id="zone1", time_offset=3600
        )  # noqa: E501, RUF100

    @pytest.mark.asyncio
    async def test_service_report_device_timezone_no_coordinator(  # ruff: ignore[no-self-use]
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: E501, PLR6301, RUF100
        """Test _async_handle_report_device_timezone when no coordinator found."""
        hass = _test_hass()

        # Mock device registry
        registry = _Registry({
            "test_device": _Device({(DOMAIN, "test_serial")}),
        })
        monkeypatch.setattr(
            "homeassistant.helpers.device_registry.async_get", lambda h: registry
        )

        # Mock coordinator lookup to return None
        monkeypatch.setattr(
            "custom_components.jackery_solarvault.services._coordinator_for_device",
            lambda h, d: None,
        )

        # Call the handler - should raise ServiceValidationError
        call = _service_call({
            SERVICE_FIELD_DEVICE_ID: "test_device",
            SERVICE_FIELD_TIMEZONE_OFFSET: 3600,
            SERVICE_FIELD_ZONE_ID: "zone1",
        })
        with pytest.raises(ServiceValidationError) as exc:
            await _async_handle_report_device_timezone(hass, call)
        assert "report_device_timezone_failed" in exc.value.translation_key

    @pytest.mark.asyncio
    async def test_service_report_device_timezone_auth_error(  # ruff: ignore[no-self-use]
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:  # noqa: E501, PLR6301, RUF100
        """Test _async_handle_report_device_timezone with auth error."""
        hass = _test_hass()
        coordinator = _Coordinator()
        coordinator.async_report_device_timezone = AsyncMock(
            side_effect=ConfigEntryAuthFailed("auth failed")
        )  # noqa: E501, RUF100

        # Mock device registry
        registry = _Registry({
            "test_device": _Device({(DOMAIN, "test_serial")}),
        })
        monkeypatch.setattr(
            "homeassistant.helpers.device_registry.async_get", lambda h: registry
        )

        # Mock coordinator lookup
        monkeypatch.setattr(
            "custom_components.jackery_solarvault.services._coordinator_for_device",
            lambda h, d: coordinator,
        )

        # Call the handler - should raise ConfigEntryAuthFailed
        call = _service_call({
            SERVICE_FIELD_DEVICE_ID: "test_device",
            SERVICE_FIELD_TIMEZONE_OFFSET: 3600,
            SERVICE_FIELD_ZONE_ID: "zone1",
        })
        with pytest.raises(ConfigEntryAuthFailed):
            await _async_handle_report_device_timezone(hass, call)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
