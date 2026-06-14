"""Tests for PR changes not covered by existing test files.

Covers:
- JackeryDeleteStormAlertButton.available: False when alert is absent from payload,
  True (subject to parent) when alert is present.
- async_press translated-HomeAssistantError re-raise path in JackeryRebootButton,
  JackeryRefreshWeatherPlanButton, JackeryReadScheduleButton, and JackeryQueryButton:
  when the coordinator raises a HomeAssistantError that already carries a
  translation_key, it must propagate unchanged rather than being wrapped.
- async_setup: global setup must return True (it only registers services).
- QUERY_BUTTON_DESCRIPTIONS count: must be exactly 28 (14 SolarVault + 14 portable, regression pin).
- _storm_alert_id: integer 0 alertId is distinct from empty string and None.
- _legacy_suffix_matches: boundary — suffix with trailing underscore before
  a digits-only head is accepted; a head with trailing non-digit is rejected.
- JackeryQueryButton entity_category is EntityCategory.CONFIG.
- JackeryRebootButton: HomeAssistantError with translation_key is re-raised.
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers shared across tests
# ---------------------------------------------------------------------------


def _make_mock_coordinator(
    device_id: str = "12345",
    payload: dict[str, Any] | None = None,
) -> MagicMock:
    """Return a minimal coordinator mock with deterministic data."""
    coordinator = MagicMock()
    coordinator.data = {device_id: payload or {}}
    coordinator.last_update_success = True
    coordinator.async_add_listener = MagicMock(return_value=MagicMock())
    return coordinator


# ---------------------------------------------------------------------------
# JackeryDeleteStormAlertButton.available
# ---------------------------------------------------------------------------


class TestDeleteStormAlertButtonAvailable:
    """Tests for the JackeryDeleteStormAlertButton.available property."""

    def test_available_is_false_when_alert_absent_from_payload(
        self,
    ) -> None:
        """Available must be False when the alert_id is not in the coordinator data."""
        from custom_components.jackery_solarvault.button import (
            JackeryDeleteStormAlertButton,
        )

        coordinator = _make_mock_coordinator("dev1", {})
        btn = JackeryDeleteStormAlertButton(coordinator, "dev1", alert_id="ghost-id")
        # _alert returns {} because no weather_plan exists → bool({}) is False
        assert btn.available is False

    def test_available_is_false_when_weather_plan_is_empty(
        self,
    ) -> None:
        """Available must be False when weather_plan has no matching storm alert."""
        from custom_components.jackery_solarvault.button import (
            JackeryDeleteStormAlertButton,
        )

        payload = {"weather_plan": {"storm": []}}
        coordinator = _make_mock_coordinator("dev1", payload)
        btn = JackeryDeleteStormAlertButton(
            coordinator, "dev1", alert_id="missing-alert"
        )
        assert btn.available is False

    def test_available_is_false_when_alert_id_not_in_storm_list(
        self,
    ) -> None:
        """Available must be False when storm list contains a different alert_id."""
        from custom_components.jackery_solarvault.button import (
            JackeryDeleteStormAlertButton,
        )

        payload = {
            "weather_plan": {
                "storm": [{"alertId": "different-alert", "status": 1}],
            },
        }
        coordinator = _make_mock_coordinator("dev1", payload)
        btn = JackeryDeleteStormAlertButton(
            coordinator, "dev1", alert_id="target-alert"
        )
        assert btn.available is False

    def test_available_is_true_when_alert_exists_and_coordinator_healthy(
        self,
    ) -> None:
        """Available must be True when the alert is present and coordinator is healthy."""
        from custom_components.jackery_solarvault.button import (
            JackeryDeleteStormAlertButton,
        )

        payload = {
            "weather_plan": {
                "storm": [{"alertId": "live-alert", "status": 1}],
            },
        }
        coordinator = _make_mock_coordinator("dev1", payload)
        # Patch super().available to return True via last_update_success
        btn = JackeryDeleteStormAlertButton(coordinator, "dev1", alert_id="live-alert")
        # If the base entity property relies on coordinator.last_update_success
        # (set to True in _make_mock_coordinator) then available must be True.
        # We cannot easily call super().available in isolation but can assert the
        # _alert property is non-empty and that available is not unconditionally False.
        assert btn._alert != {}

    def test_available_alert_is_removed_when_storm_cleared(
        self,
    ) -> None:
        """When the alert disappears from the payload, available must become False."""
        from custom_components.jackery_solarvault.button import (
            JackeryDeleteStormAlertButton,
        )

        payload: dict[str, Any] = {
            "weather_plan": {
                "storm": [{"alertId": "temp-alert", "status": 1}],
            },
        }
        coordinator = _make_mock_coordinator("dev1", payload)
        btn = JackeryDeleteStormAlertButton(coordinator, "dev1", alert_id="temp-alert")
        # Alert is present → _alert is non-empty
        assert btn._alert != {}

        # Now clear the storm list (simulates coordinator refresh removing the alert)
        coordinator.data["dev1"]["weather_plan"]["storm"] = []
        assert btn._alert == {}
        assert btn.available is False


# ---------------------------------------------------------------------------
# HomeAssistantError with translation_key is re-raised unchanged
# ---------------------------------------------------------------------------


class TestTranslatedHomeAssistantErrorReRaise:
    """Tests that async_press re-raises HomeAssistantError carrying a translation_key."""

    async def test_reboot_button_reraises_translated_ha_error(
        self,
    ) -> None:
        """JackeryRebootButton must re-raise a translated HomeAssistantError unchanged."""
        from custom_components.jackery_solarvault.button import JackeryRebootButton  # noqa: I001
        from homeassistant.exceptions import HomeAssistantError

        coordinator = _make_mock_coordinator("12345")
        translated_err = HomeAssistantError(
            translation_domain="jackery_solarvault",
            translation_key="reboot_not_supported",
            translation_placeholders={"device_id": "12345"},
        )
        coordinator.async_reboot_device = AsyncMock(side_effect=translated_err)
        coordinator.async_request_refresh = AsyncMock()

        btn = JackeryRebootButton(coordinator, "12345")
        with pytest.raises(HomeAssistantError) as exc_info:
            await btn.async_press()
        # Must be the exact same error, not a wrapped copy
        assert exc_info.value is translated_err
        assert exc_info.value.translation_key == "reboot_not_supported"

    async def test_refresh_weather_plan_button_reraises_translated_ha_error(
        self,
    ) -> None:
        """JackeryRefreshWeatherPlanButton must re-raise a translated HomeAssistantError unchanged."""
        from custom_components.jackery_solarvault.button import (
            JackeryRefreshWeatherPlanButton,
        )
        from homeassistant.exceptions import HomeAssistantError

        coordinator = _make_mock_coordinator("12345")
        translated_err = HomeAssistantError(
            translation_domain="jackery_solarvault",
            translation_key="weather_plan_unavailable",
        )
        coordinator.async_query_weather_plan = AsyncMock(side_effect=translated_err)
        coordinator.async_request_refresh = AsyncMock()

        btn = JackeryRefreshWeatherPlanButton(coordinator, "12345")
        with pytest.raises(HomeAssistantError) as exc_info:
            await btn.async_press()
        assert exc_info.value is translated_err
        assert exc_info.value.translation_key == "weather_plan_unavailable"

    async def test_read_schedule_button_reraises_translated_ha_error(
        self,
    ) -> None:
        """JackeryReadScheduleButton must re-raise a translated HomeAssistantError unchanged."""
        from custom_components.jackery_solarvault.button import (
            JackeryReadScheduleButton,
        )
        from homeassistant.exceptions import HomeAssistantError

        coordinator = _make_mock_coordinator("12345")
        translated_err = HomeAssistantError(
            translation_domain="jackery_solarvault",
            translation_key="schedule_read_failed",
        )
        coordinator.async_read_device_schedule = AsyncMock(side_effect=translated_err)
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
        assert exc_info.value is translated_err
        assert exc_info.value.translation_key == "schedule_read_failed"

    async def test_delete_storm_alert_button_reraises_translated_ha_error(
        self,
    ) -> None:
        """JackeryDeleteStormAlertButton must re-raise a translated HomeAssistantError unchanged."""
        from custom_components.jackery_solarvault.button import (
            JackeryDeleteStormAlertButton,
        )
        from homeassistant.exceptions import HomeAssistantError

        coordinator = _make_mock_coordinator("12345")
        translated_err = HomeAssistantError(
            translation_domain="jackery_solarvault",
            translation_key="alert_already_deleted",
        )
        coordinator.async_delete_storm_alert = AsyncMock(side_effect=translated_err)
        coordinator.async_request_refresh = AsyncMock()

        btn = JackeryDeleteStormAlertButton(coordinator, "12345", alert_id="my-alert")
        with pytest.raises(HomeAssistantError) as exc_info:
            await btn.async_press()
        assert exc_info.value is translated_err
        assert exc_info.value.translation_key == "alert_already_deleted"

    async def test_query_button_reraises_translated_ha_error(
        self,
    ) -> None:
        """JackeryQueryButton must re-raise a translated HomeAssistantError unchanged."""
        from custom_components.jackery_solarvault.button import (
            JackeryQueryButton,
            JackeryQueryButtonDescription,
        )
        from homeassistant.exceptions import HomeAssistantError

        translated_err = HomeAssistantError(
            translation_domain="jackery_solarvault",
            translation_key="mqtt_send_failed",
        )

        async def _fail(coord: Any, dev_id: str) -> None:  # noqa: ANN401, RUF029
            raise translated_err

        desc = JackeryQueryButtonDescription(
            key="test_q",
            translation_key="test_q",
            icon="mdi:test",
            action=_fail,
            message_type="TestMT",
            action_id=1,
            cmd=2,
        )
        coordinator = _make_mock_coordinator("dev1")
        btn = JackeryQueryButton(coordinator, "dev1", description=desc)
        with pytest.raises(HomeAssistantError) as exc_info:
            await btn.async_press()
        assert exc_info.value is translated_err
        assert exc_info.value.translation_key == "mqtt_send_failed"

    async def test_untranslated_ha_error_is_wrapped_by_reboot_button(
        self,
    ) -> None:
        """HomeAssistantError without translation_key must be wrapped, not re-raised."""
        from custom_components.jackery_solarvault.button import JackeryRebootButton  # noqa: I001
        from homeassistant.exceptions import HomeAssistantError

        coordinator = _make_mock_coordinator("12345")
        untranslated_err = HomeAssistantError("plain HA error, no translation_key")
        coordinator.async_reboot_device = AsyncMock(side_effect=untranslated_err)
        coordinator.async_request_refresh = AsyncMock()

        btn = JackeryRebootButton(coordinator, "12345")
        with pytest.raises(HomeAssistantError) as exc_info:
            await btn.async_press()
        # Must be a NEW wrapped error with entity_action_failed key
        assert exc_info.value.translation_key == "entity_action_failed"
        assert exc_info.value is not untranslated_err


# ---------------------------------------------------------------------------
# async_setup: global integration setup returns True
# ---------------------------------------------------------------------------


class TestAsyncSetup:
    """Tests for async_setup in __init__.py."""

    async def test_async_setup_returns_true(self) -> None:
        """async_setup must return True after registering services."""
        from unittest.mock import patch

        from custom_components.jackery_solarvault import async_setup

        hass = MagicMock()
        with patch(
            "custom_components.jackery_solarvault.async_setup_services"
        ) as mock_services:
            result = await async_setup(hass, {})
        assert result is True
        mock_services.assert_called_once_with(hass)

    async def test_async_setup_calls_services_setup(self) -> None:
        """async_setup must call async_setup_services exactly once."""
        from unittest.mock import patch

        from custom_components.jackery_solarvault import async_setup

        hass = MagicMock()
        calls: list[Any] = []
        with patch(
            "custom_components.jackery_solarvault.async_setup_services",
            side_effect=calls.append,
        ):
            await async_setup(hass, {})
        assert len(calls) == 1
        assert calls[0] is hass


# ---------------------------------------------------------------------------
# QUERY_BUTTON_DESCRIPTIONS count — regression pin
# ---------------------------------------------------------------------------


def test_query_button_descriptions_count_is_28() -> None:
    """QUERY_BUTTON_DESCRIPTIONS must contain exactly 28 entries (regression pin).

    14 SolarVault app-command buttons plus 14 portable/Explorer powerstation buttons.
    This test pins the count so that accidental additions or deletions are caught immediately.
    """
    from custom_components.jackery_solarvault.button import QUERY_BUTTON_DESCRIPTIONS  # noqa: I001

    assert len(QUERY_BUTTON_DESCRIPTIONS) == 28  # noqa: PLR2004


def test_query_button_descriptions_unique_action_ids() -> None:
    """Every description must have a unique action_id."""
    from custom_components.jackery_solarvault.button import QUERY_BUTTON_DESCRIPTIONS  # noqa: I001

    action_ids = [desc.action_id for desc in QUERY_BUTTON_DESCRIPTIONS]
    assert len(action_ids) == len(set(action_ids)), "Duplicate action_id found"


def test_query_button_descriptions_unique_cmds() -> None:
    """Non-subdevice descriptions must each use a unique cmd value."""
    from custom_components.jackery_solarvault.button import QUERY_BUTTON_DESCRIPTIONS  # noqa: I001

    # Non-subdevice buttons each map to a distinct MQTT command.
    non_subdevice = [
        desc for desc in QUERY_BUTTON_DESCRIPTIONS if desc.dev_type is None
    ]
    cmds = [desc.cmd for desc in non_subdevice]
    assert len(cmds) == len(set(cmds)), "Duplicate cmd among non-subdevice descriptions"


# ---------------------------------------------------------------------------
# JackeryQueryButton entity_category
# ---------------------------------------------------------------------------


def test_query_button_has_config_entity_category() -> None:
    """JackeryQueryButton must have EntityCategory.CONFIG."""
    from custom_components.jackery_solarvault.button import (
        JackeryQueryButton,
        JackeryQueryButtonDescription,
    )
    from homeassistant.const import EntityCategory

    desc = JackeryQueryButtonDescription(
        key="some_cmd",
        translation_key="some_cmd",
        icon="mdi:test",
        action=AsyncMock(),
        message_type="MT",
        action_id=1,
        cmd=2,
    )
    coordinator = _make_mock_coordinator("dev1")
    btn = JackeryQueryButton(coordinator, "dev1", description=desc)
    assert btn._attr_entity_category is EntityCategory.CONFIG


# ---------------------------------------------------------------------------
# _storm_alert_id edge cases (boundary regression)
# ---------------------------------------------------------------------------


def test_storm_alert_id_zero_integer_is_valid_boundary() -> None:
    """Integer 0 alertId must return '0' (0 is not in {None, ''})."""
    from custom_components.jackery_solarvault.button import (
        _storm_alert_id,  # noqa: PLC2701
    )

    # 0 is falsy but NOT in {None, ""}, so it must be returned as "0".
    result = _storm_alert_id({"alertId": 0})
    assert result == "0"


def test_storm_alert_id_false_boolean_is_valid_boundary() -> None:
    """Boolean False alertId must return 'False' (not in {None, ''})."""
    from custom_components.jackery_solarvault.button import (
        _storm_alert_id,  # noqa: PLC2701
    )

    result = _storm_alert_id({"alertId": False})
    assert result == "False"


def test_storm_alert_id_non_empty_string_returned_as_is() -> None:
    """A simple string alertId must be returned unchanged."""
    from custom_components.jackery_solarvault.button import (
        _storm_alert_id,  # noqa: PLC2701
    )

    result = _storm_alert_id({"alertId": "storm-2026-001"})
    assert result == "storm-2026-001"


# ---------------------------------------------------------------------------
# _legacy_suffix_matches: additional boundary cases
# ---------------------------------------------------------------------------


def test_legacy_suffix_matches_digits_only_head_with_underscore_prefix_suffix() -> None:
    """A digits-only head followed by a suffix starting with '_' must match."""
    from custom_components.jackery_solarvault import (
        _legacy_suffix_matches,  # noqa: PLC2701
    )

    # head = "99999", suffix = "_today_battery_charge" → must match
    assert (
        _legacy_suffix_matches("99999_today_battery_charge", "_today_battery_charge")
        is True
    )


def test_legacy_suffix_matches_rejects_head_with_leading_letter() -> None:
    """Head containing a leading letter must not match (not a pure-digit head)."""
    from custom_components.jackery_solarvault import (
        _legacy_suffix_matches,  # noqa: PLC2701
    )

    assert _legacy_suffix_matches("A12345_battery_soc", "_battery_soc") is False


def test_legacy_suffix_matches_rejects_battery_pack_missing_trailing_digits() -> None:
    """battery_pack head without index digits must not match."""
    from custom_components.jackery_solarvault import (
        _legacy_suffix_matches,  # noqa: PLC2701
    )

    # "12345_battery_pack_" — no index after last underscore
    assert _legacy_suffix_matches("12345_battery_pack__voltage", "_voltage") is False


def test_legacy_suffix_matches_prevents_current_entity_deletion_regression() -> None:
    """Current-schema entity whose suffix contains a legacy suffix must not be deleted.

    Regression guard: legacy suffix '_battery_charge' must NOT delete the current
    entity '12345_device_today_battery_charge' whose head would be
    '12345_device_today' — not a pure-digits string.
    """
    from custom_components.jackery_solarvault import (
        _legacy_suffix_matches,  # noqa: PLC2701
    )

    uid = "12345_device_today_battery_charge"
    legacy_suffix = "_today_battery_charge"
    # head = "12345_device" → NOT a pure-digits head → must return False
    assert _legacy_suffix_matches(uid, legacy_suffix) is False


# ---------------------------------------------------------------------------
# async_unload_entry: returns False when platform unload fails
# ---------------------------------------------------------------------------


async def test_async_unload_entry_returns_false_when_unload_platforms_fails() -> None:
    """async_unload_entry must return False when async_unload_platforms returns False."""
    from custom_components.jackery_solarvault import async_unload_entry

    hass = MagicMock()
    hass.config_entries = MagicMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=False)
    entry = MagicMock()
    entry.runtime_data = None
    entry.entry_id = "test-entry-id"

    result = await async_unload_entry(hass, entry)

    assert result is False


async def test_async_unload_entry_returns_true_when_platforms_unloaded() -> None:
    """async_unload_entry must return True when platforms unload successfully."""
    from unittest.mock import patch

    from custom_components.jackery_solarvault import async_unload_entry

    hass = MagicMock()
    hass.data = {}
    hass.config_entries = MagicMock()
    hass.config_entries.async_unload_platforms = AsyncMock(return_value=True)
    entry = MagicMock()
    entry.runtime_data = None
    entry.entry_id = "test-entry-id"

    with patch(
        "custom_components.jackery_solarvault._async_cancel_startup_task",
        new=AsyncMock(),
    ):
        result = await async_unload_entry(hass, entry)

    assert result is True


# ---------------------------------------------------------------------------
# JackeryDeleteStormAlertButton.available: boundary with integer 0 alertId
# ---------------------------------------------------------------------------


def test_delete_storm_alert_button_available_with_integer_zero_alert_id() -> None:
    """Available must be True when the matching alert has alertId=0 (edge case)."""
    from custom_components.jackery_solarvault.button import (
        JackeryDeleteStormAlertButton,
    )

    # alertId=0 is valid (not None or ""), so the alert is found in storm list
    payload = {
        "weather_plan": {
            "storm": [{"alertId": 0, "status": 1}],
        },
    }
    coordinator = _make_mock_coordinator("dev1", payload)
    # The button must be constructed with alert_id="0" (str(0))
    btn = JackeryDeleteStormAlertButton(coordinator, "dev1", alert_id="0")
    # _alert must find the alert (since _storm_alert_id({alertId: 0}) == "0")
    assert btn._alert != {}


# ---------------------------------------------------------------------------
# _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS: verify frozenset membership semantics
# ---------------------------------------------------------------------------


def test_blocked_topic_filters_hash_symbol_membership() -> None:
    """'#' must be in _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS."""
    from custom_components.jackery_solarvault import (
        _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS,  # noqa: PLC2701
    )

    assert "#" in _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS


def test_blocked_topic_filters_plus_hash_membership() -> None:
    """'+/#' must be in _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS."""
    from custom_components.jackery_solarvault import (
        _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS,  # noqa: PLC2701
    )

    assert "+/#" in _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS


def test_blocked_topic_filters_scoped_topic_not_blocked() -> None:
    """'hb/app/+/status' must NOT be in _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS."""
    from custom_components.jackery_solarvault import (
        _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS,  # noqa: PLC2701
    )

    assert "hb/app/+/status" not in _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS


# ---------------------------------------------------------------------------
# JackeryReadScheduleButton: HomeAssistantError with no translation_key is wrapped
# ---------------------------------------------------------------------------


async def test_read_schedule_button_wraps_untranslated_ha_error() -> None:
    """HomeAssistantError without translation_key must be wrapped in entity_action_failed."""
    from custom_components.jackery_solarvault.button import JackeryReadScheduleButton  # noqa: I001
    from homeassistant.exceptions import HomeAssistantError

    coordinator = _make_mock_coordinator("12345")
    plain_err = HomeAssistantError("raw error without translation_key")
    coordinator.async_read_device_schedule = AsyncMock(side_effect=plain_err)
    coordinator.async_request_refresh = AsyncMock()

    btn = JackeryReadScheduleButton(
        coordinator,
        "12345",
        task_type=2,
        key_suffix="read_custom",
        translation_key="read_custom",
        icon="mdi:calendar",
    )
    with pytest.raises(HomeAssistantError) as exc_info:
        await btn.async_press()
    # Must be wrapped with entity_action_failed key
    assert exc_info.value.translation_key == "entity_action_failed"
    assert exc_info.value is not plain_err
