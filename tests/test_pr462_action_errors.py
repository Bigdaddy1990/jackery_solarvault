"""Behavioral regressions for action-error paths highlighted by Codecov."""

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.button import (
    QUERY_BUTTON_DESCRIPTIONS,
    JackeryDeleteStormAlertButton,
    JackeryQueryButton,
    JackeryReadScheduleButton,
    JackeryRebootButton,
    JackeryRefreshWeatherPlanButton,
)
from custom_components.jackery_solarvault.client import JackeryAuthError
from custom_components.jackery_solarvault.const import PAYLOAD_SMART_PLUGS
from custom_components.jackery_solarvault.switch import (
    SWITCH_DESCRIPTIONS,
    JackerySmartPlugPrioritySwitch,
    JackerySmartPlugSwitch,
    JackerySwitch,
)
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

if TYPE_CHECKING:
    from homeassistant.components.button import ButtonEntity
    from homeassistant.components.switch import SwitchEntity

_DEVICE = "test-device"


def _coordinator(method: str, error: Exception) -> Any:
    """Expose a reachable device whose requested transport action fails."""
    coordinator = MagicMock()
    coordinator.config_entry.entry_id = "test-entry"
    coordinator.entry = coordinator.config_entry
    coordinator.data = {
        _DEVICE: {"properties": {}, PAYLOAD_SMART_PLUGS: [{"deviceSn": "plug-1"}]}
    }
    coordinator.get_device_data.side_effect = coordinator.data.get
    coordinator.last_update_success = True
    setattr(coordinator, method, AsyncMock(side_effect=error))
    return coordinator


def _button(kind: str, coordinator: Any) -> ButtonEntity:
    """Build the public action entity with an available cached snapshot."""
    entity: ButtonEntity
    if kind == "query":
        entity = JackeryQueryButton(
            coordinator,
            _DEVICE,
            description=next(
                item
                for item in QUERY_BUTTON_DESCRIPTIONS
                if item.key == "refresh_wifi_list"
            ),
        )
    elif kind == "reboot":
        entity = JackeryRebootButton(coordinator, _DEVICE)
    elif kind == "weather":
        entity = JackeryRefreshWeatherPlanButton(coordinator, _DEVICE)
    elif kind == "schedule":
        entity = JackeryReadScheduleButton(
            coordinator, _DEVICE, config=(1, "read_schedule", "read_schedule")
        )
    else:
        entity = JackeryDeleteStormAlertButton(coordinator, _DEVICE, alert_id="alert-1")
    # Availability is tested separately; exercise the transport failure itself.
    shell = cast("Any", entity)
    shell._availability_cache_active = True  # ruff: ignore[private-member-access]
    shell._cached_available = True  # ruff: ignore[private-member-access]
    return entity


@pytest.mark.parametrize(
    ["kind", "method"],
    [
        ["query", "async_query_wifi_list"],
        ["reboot", "async_reboot_device"],
        ["weather", "async_query_weather_plan"],
        ["schedule", "async_read_device_schedule"],
        ["storm", "async_delete_storm_alert"],
    ],
)
@pytest.mark.parametrize("failure", ["plain", "write", "translated", "auth"])
async def test_button_transport_failures_preserve_user_error_contract(
    kind: str, method: str, failure: str
) -> None:
    """Convert raw failures while preserving translated/authentication failures."""
    error = _error(failure)
    coordinator = _coordinator(method, error)
    with pytest.raises(HomeAssistantError) as raised:
        await _button(kind, coordinator).async_press()
    _assert_error(raised.value, error, failure)
    cast("AsyncMock", getattr(coordinator, method)).assert_awaited_once()


def _error(failure: str) -> Exception:
    """Create each error category using its real Home Assistant exception type."""
    if failure == "translated":
        return HomeAssistantError(
            translation_domain="jackery_solarvault", translation_key="known_error"
        )
    if failure == "auth":
        return JackeryAuthError("session expired")
    if failure == "write":
        return RuntimeError("connection lost")
    return HomeAssistantError("connection lost")


def _assert_error(actual: HomeAssistantError, original: Exception, kind: str) -> None:
    """Check the user-facing error and its original cause, not just that it raised."""
    if kind == "auth":
        assert isinstance(actual, ConfigEntryAuthFailed)
        assert actual.__cause__ is original
    elif kind == "translated":
        assert actual is original
    else:
        assert actual.translation_key == "entity_action_failed"
        assert actual.translation_placeholders is not None
        assert actual.translation_placeholders["device_id"] == _DEVICE
        assert actual.translation_placeholders["error"] == str(original)


@pytest.mark.parametrize("kind", ["description", "plug", "priority"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("failure", ["plain", "write", "translated", "auth"])
async def test_switch_transport_failures_preserve_user_error_contract(
    kind: str, enabled: bool, failure: str
) -> None:
    """Both switch directions retain action context and authentication failures."""
    method = {
        "description": "async_set_eps",
        "plug": "async_set_smart_plug_switch",
        "priority": "async_set_smart_plug_priority",
    }[kind]
    error = _error(failure)
    coordinator = _coordinator(method, error)
    entity: SwitchEntity
    if kind == "description":
        entity = JackerySwitch(
            coordinator,
            _DEVICE,
            next(item for item in SWITCH_DESCRIPTIONS if item.key == "eps_output"),
        )
    else:
        entity_type = (
            JackerySmartPlugPrioritySwitch
            if kind == "priority"
            else JackerySmartPlugSwitch
        )
        entity = entity_type(
            coordinator,
            _DEVICE,
            plug_index=1,
            plug_sn="plug-1",
            plug_key="smart_plug_1",
        )
    action = entity.async_turn_on if enabled else entity.async_turn_off
    with pytest.raises(HomeAssistantError) as raised:
        await action()
    _assert_error(raised.value, error, failure)
    cast("AsyncMock", getattr(coordinator, method)).assert_awaited_once()
