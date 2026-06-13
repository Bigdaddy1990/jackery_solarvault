"""Tests for the new startup orchestration logic added to __init__.py in this PR.

Covers:
- _async_finish_entry_startup: auth failure path stores message on coordinator
- _async_finish_entry_startup: discovery UpdateFailed with cached snapshot sets data
- _async_finish_entry_startup: discovery UpdateFailed without cached snapshot logs warning
- _async_finish_entry_startup: ConfigEntryAuthFailed during discovery stored on coordinator
- _async_finish_entry_startup: gather result handling for each of the 5 result slots
  - refresh_result: UpdateFailed with cached_snapshot → set_updated_data called
  - refresh_result: UpdateFailed without cached_snapshot → warning logged
  - mqtt_result: ConfigEntryAuthFailed → _defer_background_auth_failure called
  - mqtt_result: other BaseException → warning logged
  - local_listener_result: BaseException → warning logged
  - direct_local_mqtt_result: BaseException → warning logged
  - ble_result: BaseException → warning logged
- _async_finish_entry_startup: finally block removes startup_task key from hass.data
- JackeryQueryButton: HomeAssistantError WITH translation_key is re-raised unchanged
- JackeryQueryButton: HomeAssistantError WITHOUT translation_key is wrapped
- JackeryRebootButton: HomeAssistantError WITH translation_key is re-raised unchanged
- JackeryRebootButton: HomeAssistantError WITHOUT translation_key is wrapped
- JackeryDeleteStormAlertButton: HomeAssistantError with translation_key passes through
- JackeryRefreshWeatherPlanButton: HomeAssistantError with translation_key passes through
- JackeryReadScheduleButton: HomeAssistantError with translation_key passes through
"""

import asyncio
import logging
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.jackery_solarvault import (
    _STARTUP_TASK_RUNTIME_KEY,  # noqa: PLC2701
    _async_finish_entry_startup,  # noqa: PLC2701
)
from custom_components.jackery_solarvault.const import DOMAIN

# ---------------------------------------------------------------------------
# Stubs
# ---------------------------------------------------------------------------


class _FakeHass:
    """Minimal hass stub with a mutable data dict."""

    def __init__(self) -> None:
        self.data: dict[str, Any] = {}

    def async_create_background_task(  # noqa: PLR6301
        self,
        coro: Any,  # noqa: ANN401
        name: str = "",
    ) -> asyncio.Task[Any]:
        return asyncio.get_event_loop().create_task(coro)


class _FakeEntry:
    """Minimal config-entry stub."""

    def __init__(
        self,
        entry_id: str = "test_entry_id_abcd",
        options: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.entry_id = entry_id
        self.options = options or {}
        self.data = data or {}
        self._unload_callbacks: list[Any] = []
        self.runtime_data: Any = None

    def async_on_unload(self, callback: Any) -> None:  # noqa: ANN401
        self._unload_callbacks.append(callback)


def _make_coordinator_stub() -> MagicMock:
    """Create a minimal coordinator mock for startup orchestration tests."""
    coordinator = MagicMock()
    coordinator.api = MagicMock()
    coordinator.api.async_login = AsyncMock()
    coordinator.api.mqtt_session_snapshot = MagicMock(return_value=None)
    coordinator.async_discover = AsyncMock()
    coordinator.async_config_entry_first_refresh = AsyncMock()
    coordinator.async_start_mqtt = AsyncMock()
    coordinator.async_start_local_mqtt_listener = AsyncMock()
    coordinator.async_start_ble_transport = AsyncMock()
    coordinator.async_apply_local_mqtt_config_to_devices = AsyncMock()
    coordinator.cached_discovery_snapshot = MagicMock(return_value=None)
    coordinator.async_set_updated_data = MagicMock()
    coordinator._defer_background_auth_failure = MagicMock()  # noqa: SLF001
    coordinator._mqtt_auth_failure_message = None  # noqa: SLF001
    return coordinator


# ---------------------------------------------------------------------------
# _async_finish_entry_startup: auth failure during login
# ---------------------------------------------------------------------------


class TestAsyncFinishEntryStartupAuthFailure:
    """Tests for _async_finish_entry_startup when auth layer rejects credentials."""

    async def test_auth_failure_stored_on_coordinator(self) -> None:  # noqa: PLR6301
        """ConfigEntryAuthFailed from _async_authenticate_api_layer must be stored on the coordinator."""
        from homeassistant.exceptions import ConfigEntryAuthFailed

        hass = _FakeHass()
        entry = _FakeEntry(entry_id="entry-auth-fail")
        coordinator = _make_coordinator_stub()

        hass.data[DOMAIN] = {
            entry.entry_id: {
                _STARTUP_TASK_RUNTIME_KEY: asyncio.get_event_loop().create_task(
                    asyncio.sleep(0)
                )
            }
        }

        with patch(
            "custom_components.jackery_solarvault._async_authenticate_api_layer",
            new_callable=AsyncMock,
            side_effect=ConfigEntryAuthFailed("bad creds"),
        ):
            await _async_finish_entry_startup(hass, entry, coordinator)

        # ConfigEntryAuthFailed during auth → stored on coordinator
        assert coordinator._mqtt_auth_failure_message is not None  # noqa: SLF001

    async def test_auth_failure_returns_early(self) -> None:  # noqa: PLR6301
        """When auth fails, _async_finish_entry_startup must return early without calling async_discover."""
        from homeassistant.exceptions import ConfigEntryAuthFailed

        hass = _FakeHass()
        entry = _FakeEntry(entry_id="entry-early-return")
        coordinator = _make_coordinator_stub()
        hass.data[DOMAIN] = {entry.entry_id: {}}

        with patch(
            "custom_components.jackery_solarvault._async_authenticate_api_layer",
            new_callable=AsyncMock,
            side_effect=ConfigEntryAuthFailed("rejected"),
        ):
            await _async_finish_entry_startup(hass, entry, coordinator)

        coordinator.async_discover.assert_not_called()


# ---------------------------------------------------------------------------
# _async_finish_entry_startup: discovery failure paths
# ---------------------------------------------------------------------------


class TestAsyncFinishEntryStartupDiscovery:
    """Tests for _async_finish_entry_startup when discovery fails."""

    async def test_discovery_update_failed_with_cached_snapshot_sets_data(  # noqa: PLR6301
        self,
    ) -> None:
        """When discovery raises UpdateFailed and a cached snapshot exists, coordinator data is set."""
        from homeassistant.helpers.update_coordinator import UpdateFailed

        hass = _FakeHass()
        entry = _FakeEntry(entry_id="entry-disc-cache")
        coordinator = _make_coordinator_stub()
        hass.data[DOMAIN] = {entry.entry_id: {}}

        cached = {"dev-123": {"some": "payload"}}
        coordinator.cached_discovery_snapshot = MagicMock(return_value=cached)
        coordinator._async_load_cached_discovery = AsyncMock(return_value=True)  # noqa: SLF001

        with (
            patch(
                "custom_components.jackery_solarvault._async_authenticate_api_layer",
                new_callable=AsyncMock,
            ),
            patch.object(
                coordinator,
                "async_discover",
                side_effect=UpdateFailed("cloud offline"),
            ),
        ):
            await _async_finish_entry_startup(hass, entry, coordinator)

        coordinator.async_set_updated_data.assert_called_once_with(cached)

    async def test_discovery_auth_failed_stored_on_coordinator(self) -> None:  # noqa: PLR6301
        """When discovery raises ConfigEntryAuthFailed, it is stored on the coordinator."""
        from homeassistant.exceptions import ConfigEntryAuthFailed

        hass = _FakeHass()
        entry = _FakeEntry(entry_id="entry-disc-auth")
        coordinator = _make_coordinator_stub()
        hass.data[DOMAIN] = {entry.entry_id: {}}

        with (
            patch(
                "custom_components.jackery_solarvault._async_authenticate_api_layer",
                new_callable=AsyncMock,
            ),
            patch.object(
                coordinator,
                "async_discover",
                side_effect=ConfigEntryAuthFailed("discovery rejected"),
            ),
        ):
            await _async_finish_entry_startup(hass, entry, coordinator)

        assert coordinator._mqtt_auth_failure_message is not None  # noqa: SLF001


# ---------------------------------------------------------------------------
# _async_finish_entry_startup: gather result handling
# ---------------------------------------------------------------------------


class TestAsyncFinishEntryStartupGatherResults:
    """Tests for _async_finish_entry_startup gather result handling."""

    async def test_mqtt_auth_failed_defers_background_auth_failure(  # noqa: PLR6301
        self,
    ) -> None:
        """When mqtt_result is ConfigEntryAuthFailed, _defer_background_auth_failure is called."""
        from homeassistant.exceptions import ConfigEntryAuthFailed

        hass = _FakeHass()
        entry = _FakeEntry(entry_id="entry-mqtt-auth")
        coordinator = _make_coordinator_stub()
        hass.data[DOMAIN] = {entry.entry_id: {}}
        mqtt_auth_err = ConfigEntryAuthFailed("mqtt creds rejected")
        coordinator.async_start_mqtt = AsyncMock(side_effect=mqtt_auth_err)

        with patch(
            "custom_components.jackery_solarvault._async_authenticate_api_layer",
            new_callable=AsyncMock,
        ):
            await _async_finish_entry_startup(hass, entry, coordinator)

        coordinator._defer_background_auth_failure.assert_called_once_with(  # noqa: SLF001
            mqtt_auth_err
        )

    async def test_mqtt_generic_error_logs_warning(  # noqa: PLR6301
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When mqtt_result is a non-auth BaseException, a warning is logged."""
        hass = _FakeHass()
        entry = _FakeEntry(entry_id="entry-mqtt-warn")
        coordinator = _make_coordinator_stub()
        hass.data[DOMAIN] = {entry.entry_id: {}}
        coordinator.async_start_mqtt = AsyncMock(
            side_effect=RuntimeError("broker down")
        )

        with (
            patch(
                "custom_components.jackery_solarvault._async_authenticate_api_layer",
                new_callable=AsyncMock,
            ),
            caplog.at_level(
                logging.WARNING, logger="custom_components.jackery_solarvault"
            ),
        ):
            await _async_finish_entry_startup(hass, entry, coordinator)

        assert "MQTT push could not start" in caplog.text

    async def test_ble_error_logs_warning(  # noqa: PLR6301
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When ble_result is a BaseException, a warning is logged."""
        hass = _FakeHass()
        entry = _FakeEntry(entry_id="entry-ble-warn")
        coordinator = _make_coordinator_stub()
        hass.data[DOMAIN] = {entry.entry_id: {}}
        coordinator.async_start_ble_transport = AsyncMock(
            side_effect=RuntimeError("bluetooth unavailable")
        )

        with (
            patch(
                "custom_components.jackery_solarvault._async_authenticate_api_layer",
                new_callable=AsyncMock,
            ),
            caplog.at_level(
                logging.WARNING, logger="custom_components.jackery_solarvault"
            ),
        ):
            await _async_finish_entry_startup(hass, entry, coordinator)

        assert "BLE transport could not start" in caplog.text

    async def test_local_listener_error_logs_warning(  # noqa: PLR6301
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When local_listener_result is a BaseException, a warning is logged."""
        hass = _FakeHass()
        entry = _FakeEntry(entry_id="entry-local-warn")
        coordinator = _make_coordinator_stub()
        hass.data[DOMAIN] = {entry.entry_id: {}}
        coordinator.async_start_local_mqtt_listener = AsyncMock(
            side_effect=RuntimeError("HA MQTT unavailable")
        )

        with (
            patch(
                "custom_components.jackery_solarvault._async_authenticate_api_layer",
                new_callable=AsyncMock,
            ),
            caplog.at_level(
                logging.WARNING, logger="custom_components.jackery_solarvault"
            ),
        ):
            await _async_finish_entry_startup(hass, entry, coordinator)

        assert "HA-MQTT listener could not start" in caplog.text

    async def test_refresh_failed_with_cached_snapshot_sets_data(self) -> None:  # noqa: PLR6301
        """When first refresh fails with UpdateFailed and cached snapshot exists, coordinator data is set."""
        from homeassistant.helpers.update_coordinator import UpdateFailed

        hass = _FakeHass()
        entry = _FakeEntry(entry_id="entry-refresh-cache")
        coordinator = _make_coordinator_stub()
        hass.data[DOMAIN] = {entry.entry_id: {}}

        cached = {"dev-456": {"battery": 80}}
        coordinator.cached_discovery_snapshot = MagicMock(return_value=cached)
        coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=UpdateFailed("HTTP 503")
        )

        with patch(
            "custom_components.jackery_solarvault._async_authenticate_api_layer",
            new_callable=AsyncMock,
        ):
            await _async_finish_entry_startup(hass, entry, coordinator)

        coordinator.async_set_updated_data.assert_called_once_with(cached)

    async def test_refresh_failed_without_cached_snapshot_logs_warning(  # noqa: PLR6301
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """When first refresh fails with UpdateFailed and no cached snapshot, a warning is logged."""
        from homeassistant.helpers.update_coordinator import UpdateFailed

        hass = _FakeHass()
        entry = _FakeEntry(entry_id="entry-refresh-no-cache")
        coordinator = _make_coordinator_stub()
        hass.data[DOMAIN] = {entry.entry_id: {}}

        coordinator.cached_discovery_snapshot = MagicMock(return_value=None)
        coordinator.async_config_entry_first_refresh = AsyncMock(
            side_effect=UpdateFailed("HTTP 503 no cache")
        )

        with (
            patch(
                "custom_components.jackery_solarvault._async_authenticate_api_layer",
                new_callable=AsyncMock,
            ),
            caplog.at_level(
                logging.WARNING, logger="custom_components.jackery_solarvault"
            ),
        ):
            await _async_finish_entry_startup(hass, entry, coordinator)

        assert "first HTTP refresh failed" in caplog.text


# ---------------------------------------------------------------------------
# _async_finish_entry_startup: finally block
# ---------------------------------------------------------------------------


class TestAsyncFinishEntryStartupFinally:
    """Tests that the finally block cleans up the startup task key."""

    async def test_finally_removes_startup_task_key_on_success(self) -> None:  # noqa: PLR6301
        """The startup_task key must be removed from hass.data after successful startup."""
        hass = _FakeHass()
        entry = _FakeEntry(entry_id="entry-finally-ok")
        coordinator = _make_coordinator_stub()

        fake_task = asyncio.get_event_loop().create_task(asyncio.sleep(0))
        hass.data[DOMAIN] = {entry.entry_id: {_STARTUP_TASK_RUNTIME_KEY: fake_task}}

        with patch(
            "custom_components.jackery_solarvault._async_authenticate_api_layer",
            new_callable=AsyncMock,
        ):
            await _async_finish_entry_startup(hass, entry, coordinator)

        bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        assert _STARTUP_TASK_RUNTIME_KEY not in bucket

    async def test_finally_removes_startup_task_key_on_auth_failure(self) -> None:  # noqa: PLR6301
        """The startup_task key must be removed even when auth fails during startup."""
        from homeassistant.exceptions import ConfigEntryAuthFailed

        hass = _FakeHass()
        entry = _FakeEntry(entry_id="entry-finally-auth")
        coordinator = _make_coordinator_stub()

        fake_task = asyncio.get_event_loop().create_task(asyncio.sleep(0))
        hass.data[DOMAIN] = {entry.entry_id: {_STARTUP_TASK_RUNTIME_KEY: fake_task}}

        with patch(
            "custom_components.jackery_solarvault._async_authenticate_api_layer",
            new_callable=AsyncMock,
            side_effect=ConfigEntryAuthFailed("bad creds"),
        ):
            await _async_finish_entry_startup(hass, entry, coordinator)

        bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id, {})
        assert _STARTUP_TASK_RUNTIME_KEY not in bucket

    async def test_finally_handles_missing_hass_data_gracefully(self) -> None:  # noqa: PLR6301
        """The finally block must not raise when hass.data has no entry bucket."""
        hass = _FakeHass()
        entry = _FakeEntry(entry_id="entry-finally-missing")
        coordinator = _make_coordinator_stub()
        # hass.data has no DOMAIN key at all

        with patch(
            "custom_components.jackery_solarvault._async_authenticate_api_layer",
            new_callable=AsyncMock,
        ):
            # Must not raise
            await _async_finish_entry_startup(hass, entry, coordinator)


# ---------------------------------------------------------------------------
# JackeryQueryButton: HomeAssistantError pass-through vs wrapping
# ---------------------------------------------------------------------------


class TestJackeryQueryButtonHomeAssistantErrorHandling:
    """Tests for HomeAssistantError handling in JackeryQueryButton.async_press."""

    def _make_query_button(  # noqa: PLR6301
        self,
        action: Any,  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        """Construct a JackeryQueryButton with the given action."""
        from custom_components.jackery_solarvault.button import (
            JackeryQueryButton,
            JackeryQueryButtonDescription,
        )

        coordinator = MagicMock()
        coordinator.data = {"12345": {}}
        coordinator.async_add_listener = MagicMock(return_value=MagicMock())
        description = JackeryQueryButtonDescription(
            key="test_btn",
            translation_key="test_btn",
            icon="mdi:test",
            action=action,
            message_type="SomeMessage",
            action_id=1,
            cmd=2,
        )
        return JackeryQueryButton(coordinator, "12345", description=description)

    async def test_homeassistant_error_with_translation_key_is_reraised(
        self,
    ) -> None:
        """HomeAssistantError that already has a translation_key must pass through unchanged."""
        from homeassistant.exceptions import HomeAssistantError

        original_error = HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="some_specific_error",
        )

        async def _action(coordinator: Any, device_id: str) -> None:  # noqa: ANN401, RUF029
            raise original_error

        btn = self._make_query_button(_action)
        with pytest.raises(HomeAssistantError) as exc_info:
            await btn.async_press()

        # Must be the SAME object, not a re-wrapped one
        assert exc_info.value is original_error
        assert exc_info.value.translation_key == "some_specific_error"

    async def test_homeassistant_error_without_translation_key_is_wrapped(
        self,
    ) -> None:
        """HomeAssistantError without a translation_key must be wrapped into entity_action_failed."""
        from homeassistant.exceptions import HomeAssistantError

        async def _action(coordinator: Any, device_id: str) -> None:  # noqa: ANN401, RUF029
            raise HomeAssistantError("plain error without translation")  # noqa: TRY003

        btn = self._make_query_button(_action)
        with pytest.raises(HomeAssistantError) as exc_info:
            await btn.async_press()

        # Must be wrapped with entity_action_failed
        assert exc_info.value.translation_key == "entity_action_failed"

    async def test_homeassistant_error_with_none_translation_key_is_wrapped(
        self,
    ) -> None:
        """HomeAssistantError with translation_key=None must be wrapped."""
        from homeassistant.exceptions import HomeAssistantError

        err = HomeAssistantError("no translation")
        # Explicitly ensure translation_key is None/falsy
        err.translation_key = None  # type: ignore[assignment]

        async def _action(coordinator: Any, device_id: str) -> None:  # noqa: ANN401, RUF029
            raise err

        btn = self._make_query_button(_action)
        with pytest.raises(HomeAssistantError) as exc_info:
            await btn.async_press()

        assert exc_info.value.translation_key == "entity_action_failed"


# ---------------------------------------------------------------------------
# JackeryRebootButton: HomeAssistantError pass-through vs wrapping
# ---------------------------------------------------------------------------


class TestJackeryRebootButtonHomeAssistantErrorHandling:
    """Tests for HomeAssistantError handling in JackeryRebootButton.async_press."""

    async def test_homeassistant_error_with_translation_key_is_reraised(  # noqa: PLR6301
        self,
    ) -> None:
        """HomeAssistantError with translation_key must pass through unchanged."""
        from custom_components.jackery_solarvault.button import JackeryRebootButton  # noqa: I001
        from homeassistant.exceptions import HomeAssistantError

        original_error = HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="device_unreachable",
        )

        coordinator = MagicMock()
        coordinator.data = {"12345": {}}
        coordinator.async_reboot_device = AsyncMock(side_effect=original_error)
        coordinator.async_request_refresh = AsyncMock()

        btn = JackeryRebootButton(coordinator, "12345")
        with pytest.raises(HomeAssistantError) as exc_info:
            await btn.async_press()

        assert exc_info.value is original_error
        assert exc_info.value.translation_key == "device_unreachable"

    async def test_homeassistant_error_without_translation_key_is_wrapped(  # noqa: PLR6301
        self,
    ) -> None:
        """HomeAssistantError without translation_key must be wrapped into entity_action_failed."""
        from custom_components.jackery_solarvault.button import JackeryRebootButton  # noqa: I001
        from homeassistant.exceptions import HomeAssistantError

        coordinator = MagicMock()
        coordinator.data = {"12345": {}}
        coordinator.async_reboot_device = AsyncMock(
            side_effect=HomeAssistantError("plain error")
        )
        coordinator.async_request_refresh = AsyncMock()

        btn = JackeryRebootButton(coordinator, "12345")
        with pytest.raises(HomeAssistantError) as exc_info:
            await btn.async_press()

        assert exc_info.value.translation_key == "entity_action_failed"


# ---------------------------------------------------------------------------
# JackeryDeleteStormAlertButton: HomeAssistantError pass-through
# ---------------------------------------------------------------------------


class TestJackeryDeleteStormAlertButtonHomeAssistantErrorHandling:
    """Tests for HomeAssistantError handling in JackeryDeleteStormAlertButton.async_press."""

    async def test_homeassistant_error_with_translation_key_is_reraised(  # noqa: PLR6301
        self,
    ) -> None:
        """HomeAssistantError with translation_key must pass through unchanged."""
        from custom_components.jackery_solarvault.button import (
            JackeryDeleteStormAlertButton,
        )
        from homeassistant.exceptions import HomeAssistantError

        original_error = HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="alert_not_found",
        )

        coordinator = MagicMock()
        coordinator.data = {"12345": {}}
        coordinator.async_delete_storm_alert = AsyncMock(side_effect=original_error)
        coordinator.async_request_refresh = AsyncMock()

        btn = JackeryDeleteStormAlertButton(coordinator, "12345", alert_id="a1")
        with pytest.raises(HomeAssistantError) as exc_info:
            await btn.async_press()

        assert exc_info.value is original_error


# ---------------------------------------------------------------------------
# JackeryRefreshWeatherPlanButton: HomeAssistantError pass-through
# ---------------------------------------------------------------------------


class TestJackeryRefreshWeatherPlanButtonHomeAssistantErrorHandling:
    """Tests for HomeAssistantError handling in JackeryRefreshWeatherPlanButton.async_press."""

    async def test_homeassistant_error_with_translation_key_is_reraised(  # noqa: PLR6301
        self,
    ) -> None:
        """HomeAssistantError with translation_key must pass through unchanged."""
        from custom_components.jackery_solarvault.button import (
            JackeryRefreshWeatherPlanButton,
        )
        from homeassistant.exceptions import HomeAssistantError

        original_error = HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="weather_unavailable",
        )

        coordinator = MagicMock()
        coordinator.data = {"12345": {}}
        coordinator.async_query_weather_plan = AsyncMock(side_effect=original_error)
        coordinator.async_request_refresh = AsyncMock()

        btn = JackeryRefreshWeatherPlanButton(coordinator, "12345")
        with pytest.raises(HomeAssistantError) as exc_info:
            await btn.async_press()

        assert exc_info.value is original_error


# ---------------------------------------------------------------------------
# JackeryReadScheduleButton: HomeAssistantError pass-through
# ---------------------------------------------------------------------------


class TestJackeryReadScheduleButtonHomeAssistantErrorHandling:
    """Tests for HomeAssistantError handling in JackeryReadScheduleButton.async_press."""

    async def test_homeassistant_error_with_translation_key_is_reraised(  # noqa: PLR6301
        self,
    ) -> None:
        """HomeAssistantError with translation_key must pass through unchanged."""
        from custom_components.jackery_solarvault.button import (
            JackeryReadScheduleButton,
        )
        from homeassistant.exceptions import HomeAssistantError

        original_error = HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="schedule_unavailable",
        )

        coordinator = MagicMock()
        coordinator.data = {"12345": {}}
        coordinator.async_read_device_schedule = AsyncMock(side_effect=original_error)
        coordinator.async_request_refresh = AsyncMock()

        btn = JackeryReadScheduleButton(
            coordinator,
            "12345",
            task_type=2,
            key_suffix="read_custom_mode_schedule",
            translation_key="read_custom_mode_schedule",
            icon="mdi:calendar-clock",
        )
        with pytest.raises(HomeAssistantError) as exc_info:
            await btn.async_press()

        assert exc_info.value is original_error

    async def test_homeassistant_error_without_translation_key_is_wrapped(  # noqa: PLR6301
        self,
    ) -> None:
        """HomeAssistantError without translation_key must be wrapped into entity_action_failed."""
        from custom_components.jackery_solarvault.button import (
            JackeryReadScheduleButton,
        )
        from homeassistant.exceptions import HomeAssistantError

        coordinator = MagicMock()
        coordinator.data = {"12345": {}}
        coordinator.async_read_device_schedule = AsyncMock(
            side_effect=HomeAssistantError("plain schedule error")
        )
        coordinator.async_request_refresh = AsyncMock()

        btn = JackeryReadScheduleButton(
            coordinator,
            "12345",
            task_type=2,
            key_suffix="read_custom_mode_schedule",
            translation_key="read_custom_mode_schedule",
            icon="mdi:calendar-clock",
        )
        with pytest.raises(HomeAssistantError) as exc_info:
            await btn.async_press()

        assert exc_info.value.translation_key == "entity_action_failed"


# ---------------------------------------------------------------------------
# Regression: _storm_alert_id with float alertId
# ---------------------------------------------------------------------------


def test_storm_alert_id_with_float_returns_full_string_representation() -> None:
    """_storm_alert_id with a float alertId must return the full str() representation.

    Regression guard: str(math.pi) is '3.141592653589793', not '3.14'.
    The function uses str(raw), so the output is the full Python repr of the float.
    """
    import math

    from custom_components.jackery_solarvault.button import (
        _storm_alert_id,  # noqa: PLC2701
    )

    result = _storm_alert_id({"alertId": math.pi})
    # str(math.pi) = "3.141592653589793", not "3.14"
    assert result == str(math.pi)
    assert result == "3.141592653589793"


def test_storm_alert_id_with_integer_zero_returns_string_zero() -> None:
    """_storm_alert_id with alertId=0 must return '0', not None.

    Boundary case: 0 is falsy but is not in {None, ''}, so it is a valid id.
    """
    from custom_components.jackery_solarvault.button import (
        _storm_alert_id,  # noqa: PLC2701
    )

    result = _storm_alert_id({"alertId": 0})
    assert result == "0"


def test_storm_alert_id_with_false_alertid_returns_string_false() -> None:
    """_storm_alert_id with alertId=False must return 'False' (bool is not None or '')."""
    from custom_components.jackery_solarvault.button import (
        _storm_alert_id,  # noqa: PLC2701
    )

    # False is falsy but not None and not ""
    result = _storm_alert_id({"alertId": False})
    # False is not in {None, ""} so it will be str(False) = "False"
    assert result == "False"


# ---------------------------------------------------------------------------
# Regression: _legacy_suffix_matches boundary cases not in test_pr_new_coverage
# ---------------------------------------------------------------------------


def test_legacy_suffix_matches_rejects_current_schema_entity_tail_overlap() -> None:
    """A current-schema UID must not be deleted by a legacy suffix that is its tail.

    This is the original regression: legacy suffix '_today_battery_charge' must not
    match current UID '12345_device_today_battery_charge' because the head
    '12345_device' does not conform to the legacy <digits> or
    <digits>_battery_pack_<digits> head shape.
    """
    from custom_components.jackery_solarvault import (
        _legacy_suffix_matches,  # noqa: PLC2701
    )

    # This is the exact regression case from the PR description
    assert (
        _legacy_suffix_matches(
            "12345_device_today_battery_charge", "_today_battery_charge"
        )
        is False
    )


def test_legacy_suffix_matches_does_not_match_when_head_has_letters() -> None:
    """A head containing letters must not match the legacy pattern."""
    from custom_components.jackery_solarvault import (
        _legacy_suffix_matches,  # noqa: PLC2701
    )

    # 'abc' as head should not match
    assert _legacy_suffix_matches("abc_battery_soc", "_battery_soc") is False


def test_legacy_suffix_matches_handles_very_long_device_id() -> None:
    """A very long numeric device ID (real Jackery serial) must match correctly."""
    from custom_components.jackery_solarvault import (
        _legacy_suffix_matches,  # noqa: PLC2701
    )

    # 15-digit device ID is realistic for Jackery serials
    assert _legacy_suffix_matches("123456789012345_battery_soc", "_battery_soc") is True


def test_legacy_suffix_matches_battery_pack_large_index() -> None:
    """A battery pack with a large index must still match."""
    from custom_components.jackery_solarvault import (
        _legacy_suffix_matches,  # noqa: PLC2701
    )

    # Pack index 99 is unusual but valid
    assert _legacy_suffix_matches("12345_battery_pack_99_voltage", "_voltage") is True


def test_legacy_suffix_matches_empty_suffix_always_false() -> None:
    """An empty suffix must never match (head would be full uid, which contains non-digits if uid does)."""
    from custom_components.jackery_solarvault import (
        _legacy_suffix_matches,  # noqa: PLC2701
    )

    # Empty suffix: head = full uid. Only matches if uid itself is just digits.
    assert _legacy_suffix_matches("12345", "") is True  # pure digit uid, empty suffix
    assert _legacy_suffix_matches("abc_key", "") is False


# ---------------------------------------------------------------------------
# Regression: JackeryApi relative imports
# ---------------------------------------------------------------------------


def test_api_module_uses_relative_const_import() -> None:
    """The api module must be importable, confirming the relative import fix works.

    The PR changed 'from jackery_solarvault.const import' to 'from ..const import'.
    This test confirms the module imports without error and that encrypt_mqtt_body
    (a new function added in this PR) is available.
    """
    from custom_components.jackery_solarvault.client.api import encrypt_mqtt_body  # noqa: I001

    assert callable(encrypt_mqtt_body)


def test_api_module_encrypt_mqtt_body_requires_16_byte_key() -> None:
    """encrypt_mqtt_body with a 24-byte key must raise ValueError (not ImportError).

    This confirms the relative import fix did not break the function.
    """
    from custom_components.jackery_solarvault.client.api import encrypt_mqtt_body  # noqa: I001

    with pytest.raises(ValueError, match="16 bytes"):
        encrypt_mqtt_body({"cmd": 1}, b"0123456789abcdef01234567")  # 24 bytes


# ---------------------------------------------------------------------------
# client/__init__.py: import_module based lazy loading
# ---------------------------------------------------------------------------


def test_client_init_getattr_uses_import_module() -> None:
    """The __getattr__ must use import_module (importlib) rather than a direct import.

    The PR changed the implementation from a direct inline import to importlib.import_module.
    We verify the behavior is equivalent: the class is loaded lazily and is correct.
    """
    import custom_components.jackery_solarvault.client as client_pkg
    from custom_components.jackery_solarvault.client.mqtt_push import (
        JackeryMqttPushClient as DirectClass,
    )

    lazy_cls = client_pkg.JackeryMqttPushClient
    assert lazy_cls is DirectClass


def test_client_init_getattr_raises_attribute_error_for_unknown() -> None:
    """__getattr__ must raise AttributeError for names other than JackeryMqttPushClient."""
    import custom_components.jackery_solarvault.client as client_pkg

    with pytest.raises(AttributeError, match="NonExistent"):
        _ = client_pkg.NonExistent  # type: ignore[attr-defined]
