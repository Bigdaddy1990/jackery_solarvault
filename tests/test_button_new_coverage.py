"""Tests for button.py new functions and entity classes added in this PR.

Covers:
- _storm_alert_id: alertId extraction from alert dicts
- _storm_alerts: filtering of storm alert lists from weather_plan payloads
- _smart_plug_device_sn: serial number extraction with field priority fallback
- JackeryQueryButtonDescription: frozen dataclass construction and fields
- QUERY_BUTTON_DESCRIPTIONS: content validation (length, dev_type presence)
- JackeryQueryButton.extra_state_attributes: messageType, actionId, cmd, optional devType
- JackeryDeleteStormAlertButton._alert: alert lookup by id
- JackeryDeleteStormAlertButton.available: False when alert is absent
- JackeryDeleteStormAlertButton.extra_state_attributes: alertId plus optional fields
- JackeryReadScheduleButton.extra_state_attributes: taskType, optional deviceSn
- JackeryRefreshWeatherPlanButton: translation_key and icon
- _raise_action_error: raises HomeAssistantError with correct fields
"""

import math
from unittest.mock import AsyncMock, MagicMock

import pytest

# ---------------------------------------------------------------------------
# Helpers — import the module-level functions under test
# ---------------------------------------------------------------------------


def _get_storm_alert_id():  # noqa: ANN202
    from custom_components.jackery_solarvault.button import (
        _storm_alert_id,  # noqa: PLC2701
    )

    return _storm_alert_id


def _get_storm_alerts():  # noqa: ANN202
    from custom_components.jackery_solarvault.button import (
        _storm_alerts,  # noqa: PLC2701
    )

    return _storm_alerts


def _get_smart_plug_device_sn():  # noqa: ANN202
    from custom_components.jackery_solarvault.button import (
        _smart_plug_device_sn,  # noqa: PLC2701
    )

    return _smart_plug_device_sn


# ---------------------------------------------------------------------------
# _storm_alert_id
# ---------------------------------------------------------------------------


class TestStormAlertId:
    """Tests for _storm_alert_id()."""

    def test_returns_string_from_alertid_key(self) -> None:  # noqa: PLR6301
        """AlertId present as string must be returned unchanged."""
        fn = _get_storm_alert_id()
        assert fn({"alertId": "abc123"}) == "abc123"

    def test_coerces_int_alertid_to_string(self) -> None:  # noqa: PLR6301
        """AlertId as integer must be coerced to string."""
        fn = _get_storm_alert_id()
        assert fn({"alertId": 42}) == "42"

    def test_returns_none_for_non_dict(self) -> None:  # noqa: PLR6301
        """Non-dict input must return None."""
        fn = _get_storm_alert_id()
        assert fn("not_a_dict") is None
        assert fn(None) is None
        assert fn(123) is None
        assert fn([]) is None

    def test_returns_none_when_alertid_missing(self) -> None:  # noqa: PLR6301
        """Missing alertId key must return None."""
        fn = _get_storm_alert_id()
        assert fn({}) is None
        assert fn({"other_key": "value"}) is None

    def test_returns_none_when_alertid_is_none(self) -> None:  # noqa: PLR6301
        """AlertId explicitly set to None must return None."""
        fn = _get_storm_alert_id()
        assert fn({"alertId": None}) is None

    def test_returns_none_when_alertid_is_empty_string(self) -> None:  # noqa: PLR6301
        """Empty string alertId must return None (blocked by the function)."""
        fn = _get_storm_alert_id()
        assert fn({"alertId": ""}) is None

    def test_returns_string_for_zero_alertid(self) -> None:  # noqa: PLR6301
        """Integer 0 is a valid alert id and must be returned as '0'."""
        fn = _get_storm_alert_id()
        # 0 is falsy but not in (None, ""), so it should be returned as "0"
        assert fn({"alertId": 0}) == "0"

    def test_returns_string_for_float_alertid(self) -> None:  # noqa: PLR6301
        """Float alertId must be coerced to string."""
        fn = _get_storm_alert_id()
        assert fn({"alertId": math.pi}) == "3.14"

    def test_other_keys_are_ignored(self) -> None:  # noqa: PLR6301
        """Extra keys in the dict must not affect the result."""
        fn = _get_storm_alert_id()
        result = fn({"alertId": "alert-99", "extra": "value", "status": 1})
        assert result == "alert-99"


# ---------------------------------------------------------------------------
# _storm_alerts
# ---------------------------------------------------------------------------


class TestStormAlerts:
    """Tests for _storm_alerts()."""

    def test_returns_empty_for_non_dict_weather_plan(self) -> None:  # noqa: PLR6301
        """Non-dict weather_plan must return an empty list."""
        fn = _get_storm_alerts()
        assert fn(None) == []
        assert fn("string") == []
        assert fn([]) == []
        assert fn(42) == []

    def test_returns_empty_when_storm_key_missing(self) -> None:  # noqa: PLR6301
        """Missing storm key in weather_plan must return an empty list."""
        fn = _get_storm_alerts()
        assert fn({}) == []
        assert fn({"other": "data"}) == []

    def test_returns_empty_when_storm_is_not_a_list(self) -> None:  # noqa: PLR6301
        """Storm value that is not a list must return an empty list."""
        fn = _get_storm_alerts()
        assert fn({"storm": None}) == []
        assert fn({"storm": "alert_string"}) == []
        assert fn({"storm": {}}) == []
        assert fn({"storm": 42}) == []

    def test_returns_empty_list_when_storm_list_empty(self) -> None:  # noqa: PLR6301
        """Empty storm list must return an empty list."""
        fn = _get_storm_alerts()
        assert fn({"storm": []}) == []

    def test_filters_out_alerts_without_alertid(self) -> None:  # noqa: PLR6301
        """Alerts without alertId must be excluded."""
        fn = _get_storm_alerts()
        weather_plan = {
            "storm": [
                {"status": 1},  # no alertId
                {"alertId": None},  # None alertId
                {"alertId": ""},  # empty alertId
                {"alertId": "valid-id"},  # valid alert
            ],
        }
        result = fn(weather_plan)
        assert len(result) == 1
        assert result[0]["alertId"] == "valid-id"

    def test_returns_all_valid_alerts(self) -> None:  # noqa: PLR6301
        """All alerts with valid alertIds must be included."""
        fn = _get_storm_alerts()
        weather_plan = {
            "storm": [
                {"alertId": "alert-1", "status": 1},
                {"alertId": "alert-2", "status": 0},
                {"alertId": "alert-3"},
            ],
        }
        result = fn(weather_plan)
        assert len(result) == 3  # noqa: PLR2004

    def test_filters_out_non_dict_entries_in_storm_list(self) -> None:  # noqa: PLR6301
        """Non-dict entries in the storm list must be excluded."""
        fn = _get_storm_alerts()
        weather_plan = {
            "storm": [
                "string_alert",
                None,
                42,
                {"alertId": "valid"},
            ],
        }
        result = fn(weather_plan)
        assert len(result) == 1
        assert result[0]["alertId"] == "valid"

    def test_preserves_alert_payload_contents(self) -> None:  # noqa: PLR6301
        """The original alert dict must be preserved in the result."""
        fn = _get_storm_alerts()
        alert = {"alertId": "a1", "startTs": 1000, "endTs": 2000, "status": 1}
        result = fn({"storm": [alert]})
        assert result[0] is alert


# ---------------------------------------------------------------------------
# _smart_plug_device_sn
# ---------------------------------------------------------------------------


class TestSmartPlugDeviceSn:
    """Tests for _smart_plug_device_sn()."""

    def test_returns_none_for_non_dict(self) -> None:  # noqa: PLR6301
        """Non-dict input must return None."""
        fn = _get_smart_plug_device_sn()
        assert fn(None) is None
        assert fn("string") is None
        assert fn(42) is None
        assert fn([]) is None

    def test_returns_device_sn_when_present(self) -> None:  # noqa: PLR6301
        """DeviceSn has highest priority."""
        fn = _get_smart_plug_device_sn()
        plug = {"deviceSn": "SN001", "devSn": "SN002", "sn": "SN003"}
        assert fn(plug) == "SN001"

    def test_falls_back_to_dev_sn(self) -> None:  # noqa: PLR6301
        """DevSn is used when deviceSn is missing."""
        fn = _get_smart_plug_device_sn()
        plug = {"devSn": "SN002", "sn": "SN003"}
        assert fn(plug) == "SN002"

    def test_falls_back_to_sn(self) -> None:  # noqa: PLR6301
        """Sn is used when deviceSn and devSn are missing."""
        fn = _get_smart_plug_device_sn()
        plug = {"sn": "SN003"}
        assert fn(plug) == "SN003"

    def test_returns_none_when_no_sn_fields(self) -> None:  # noqa: PLR6301
        """Missing all SN fields must return None."""
        fn = _get_smart_plug_device_sn()
        assert fn({}) is None
        assert fn({"name": "plug"}) is None

    def test_returns_none_when_device_sn_is_none(self) -> None:  # noqa: PLR6301
        """None deviceSn falls back to devSn."""
        fn = _get_smart_plug_device_sn()
        plug = {"deviceSn": None, "devSn": "SN002"}
        assert fn(plug) == "SN002"

    def test_returns_none_when_device_sn_is_empty_string(self) -> None:  # noqa: PLR6301
        """Empty deviceSn falls back to devSn because `or` treats empty string as falsy."""
        fn = _get_smart_plug_device_sn()
        plug = {"deviceSn": "", "devSn": "SN002"}
        assert fn(plug) == "SN002"

    def test_returns_none_when_all_sn_fields_are_empty(self) -> None:  # noqa: PLR6301
        """All SN fields empty must return None."""
        fn = _get_smart_plug_device_sn()
        plug = {"deviceSn": "", "devSn": "", "sn": ""}
        assert fn(plug) is None

    def test_returns_none_when_all_sn_fields_are_none(self) -> None:  # noqa: PLR6301
        """All SN fields None must return None."""
        fn = _get_smart_plug_device_sn()
        plug = {"deviceSn": None, "devSn": None, "sn": None}
        assert fn(plug) is None

    def test_coerces_non_string_sn_to_string(self) -> None:  # noqa: PLR6301
        """Non-string SN values must be coerced to string."""
        fn = _get_smart_plug_device_sn()
        plug = {"deviceSn": 12345}
        assert fn(plug) == "12345"

    def test_uses_raw_truthiness_for_sn_priority(self) -> None:  # noqa: PLR6301
        """Priority uses `or` so the first truthy value wins."""
        fn = _get_smart_plug_device_sn()
        plug = {"deviceSn": 0, "devSn": "SN002"}
        # 0 is falsy, so devSn is used
        assert fn(plug) == "SN002"


# ---------------------------------------------------------------------------
# JackeryQueryButtonDescription
# ---------------------------------------------------------------------------


class TestJackeryQueryButtonDescription:
    """Tests for the JackeryQueryButtonDescription dataclass."""

    def test_is_frozen_dataclass(self) -> None:  # noqa: PLR6301
        """JackeryQueryButtonDescription must be immutable (frozen dataclass)."""
        from custom_components.jackery_solarvault.button import (
            JackeryQueryButtonDescription,
        )

        desc = JackeryQueryButtonDescription(
            key="test_key",
            translation_key="test_key",
            icon="mdi:test",
            action=AsyncMock(),
            message_type="TestMessageType",
            action_id=100,
            cmd=200,
        )
        with pytest.raises((AttributeError, TypeError)):
            desc.key = "new_key"  # type: ignore[misc]

    def test_dev_type_defaults_to_none(self) -> None:  # noqa: PLR6301
        """dev_type defaults to None when not specified."""
        from custom_components.jackery_solarvault.button import (
            JackeryQueryButtonDescription,
        )

        desc = JackeryQueryButtonDescription(
            key="k",
            translation_key="k",
            icon="mdi:k",
            action=AsyncMock(),
            message_type="MT",
            action_id=1,
            cmd=2,
        )
        assert desc.dev_type is None

    def test_dev_type_can_be_set(self) -> None:  # noqa: PLR6301
        """dev_type can be set to an integer value."""
        from custom_components.jackery_solarvault.button import (
            JackeryQueryButtonDescription,
        )

        desc = JackeryQueryButtonDescription(
            key="k",
            translation_key="k",
            icon="mdi:k",
            action=AsyncMock(),
            message_type="MT",
            action_id=1,
            cmd=2,
            dev_type=99,
        )
        assert desc.dev_type == 99  # noqa: PLR2004


# ---------------------------------------------------------------------------
# QUERY_BUTTON_DESCRIPTIONS
# ---------------------------------------------------------------------------


class TestQueryButtonDescriptions:
    """Tests for QUERY_BUTTON_DESCRIPTIONS constant."""

    def test_is_a_tuple(self) -> None:  # noqa: PLR6301
        """QUERY_BUTTON_DESCRIPTIONS must be a tuple."""
        from custom_components.jackery_solarvault.button import (
            QUERY_BUTTON_DESCRIPTIONS,
        )

        assert isinstance(QUERY_BUTTON_DESCRIPTIONS, tuple)

    def test_has_expected_count(self) -> None:  # noqa: PLR6301
        """Must have exactly 28 descriptions (14 SolarVault + 14 portable)."""
        from custom_components.jackery_solarvault.button import (
            QUERY_BUTTON_DESCRIPTIONS,
        )

        assert len(QUERY_BUTTON_DESCRIPTIONS) == 28  # noqa: PLR2004

    def test_all_entries_are_query_button_descriptions(self) -> None:  # noqa: PLR6301
        """All entries must be JackeryQueryButtonDescription instances."""
        from custom_components.jackery_solarvault.button import (
            QUERY_BUTTON_DESCRIPTIONS,
            JackeryQueryButtonDescription,
        )

        for desc in QUERY_BUTTON_DESCRIPTIONS:
            assert isinstance(desc, JackeryQueryButtonDescription)

    def test_subdevice_entries_have_dev_type(self) -> None:  # noqa: PLR6301
        """All subdevice query descriptions must have a non-None dev_type."""
        from custom_components.jackery_solarvault.button import (
            QUERY_BUTTON_DESCRIPTIONS,
        )

        subdevice_keys = {
            "refresh_battery_packs",
            "refresh_smart_meter",
            "refresh_meter_heads",
            "refresh_smart_plugs",
            "refresh_subdevice_combo",
        }
        for desc in QUERY_BUTTON_DESCRIPTIONS:
            if desc.key in subdevice_keys:
                assert desc.dev_type is not None, f"{desc.key} should have dev_type"

    def test_non_subdevice_entries_have_no_dev_type(self) -> None:  # noqa: PLR6301
        """Non-subdevice query descriptions must have dev_type=None."""
        from custom_components.jackery_solarvault.button import (
            QUERY_BUTTON_DESCRIPTIONS,
        )

        non_subdevice_keys = {
            "refresh_system_info",
            "refresh_device_info",
            "refresh_wifi_list",
            "refresh_time_zone",
            "sync_time_zone",
            "sync_cloud_mqtt_info",
            "refresh_device_ota_version",
            "refresh_third_party_mqtt_config",
            "refresh_wifi_config",
        }
        for desc in QUERY_BUTTON_DESCRIPTIONS:
            if desc.key in non_subdevice_keys:
                assert desc.dev_type is None, f"{desc.key} should not have dev_type"

    def test_all_entries_have_unique_keys(self) -> None:  # noqa: PLR6301
        """All description keys must be unique."""
        from custom_components.jackery_solarvault.button import (
            QUERY_BUTTON_DESCRIPTIONS,
        )

        keys = [desc.key for desc in QUERY_BUTTON_DESCRIPTIONS]
        assert len(keys) == len(set(keys))

    def test_all_entries_have_mdi_icons(self) -> None:  # noqa: PLR6301
        """All descriptions must have an mdi: icon."""
        from custom_components.jackery_solarvault.button import (
            QUERY_BUTTON_DESCRIPTIONS,
        )

        for desc in QUERY_BUTTON_DESCRIPTIONS:
            assert desc.icon.startswith("mdi:"), (
                f"{desc.key} icon should start with mdi:"
            )


# ---------------------------------------------------------------------------
# JackeryQueryButton entity
# ---------------------------------------------------------------------------


def _make_mock_coordinator(
    device_id: str = "12345", payload: dict | None = None
) -> MagicMock:
    """Create a mock coordinator with given device data."""
    coordinator = MagicMock()
    coordinator.data = {device_id: payload or {}}
    coordinator.async_add_listener = MagicMock(return_value=MagicMock())
    return coordinator


def _make_query_button(  # noqa: ANN202
    description=None,  # noqa: ANN001
    device_id: str = "12345",
    coordinator=None,  # noqa: ANN001
):
    """Construct a JackeryQueryButton for testing."""
    from custom_components.jackery_solarvault.button import (
        JackeryQueryButton,
        JackeryQueryButtonDescription,
    )

    if coordinator is None:
        coordinator = _make_mock_coordinator(device_id)

    if description is None:
        description = JackeryQueryButtonDescription(
            key="test_query",
            translation_key="test_query",
            icon="mdi:test",
            action=AsyncMock(),
            message_type="TestMessageType",
            action_id=1001,
            cmd=2002,
        )
    return JackeryQueryButton(coordinator, device_id, description=description)


class TestJackeryQueryButton:
    """Tests for JackeryQueryButton entity."""

    def test_extra_state_attributes_has_message_type(self) -> None:  # noqa: PLR6301
        """extra_state_attributes must contain messageType."""
        from custom_components.jackery_solarvault.button import (
            JackeryQueryButtonDescription,
        )
        from custom_components.jackery_solarvault.const import FIELD_MESSAGE_TYPE

        desc = JackeryQueryButtonDescription(
            key="test",
            translation_key="test",
            icon="mdi:test",
            action=AsyncMock(),
            message_type="SomeMessageType",
            action_id=1,
            cmd=2,
        )
        btn = _make_query_button(description=desc)
        attrs = btn.extra_state_attributes
        assert FIELD_MESSAGE_TYPE in attrs
        assert attrs[FIELD_MESSAGE_TYPE] == "SomeMessageType"

    def test_extra_state_attributes_has_action_id(self) -> None:  # noqa: PLR6301
        """extra_state_attributes must contain actionId."""
        from custom_components.jackery_solarvault.button import (
            JackeryQueryButtonDescription,
        )

        desc = JackeryQueryButtonDescription(
            key="test",
            translation_key="test",
            icon="mdi:test",
            action=AsyncMock(),
            message_type="MT",
            action_id=999,
            cmd=1,
        )
        btn = _make_query_button(description=desc)
        attrs = btn.extra_state_attributes
        assert attrs["actionId"] == 999  # noqa: PLR2004

    def test_extra_state_attributes_has_cmd(self) -> None:  # noqa: PLR6301
        """extra_state_attributes must contain cmd."""
        from custom_components.jackery_solarvault.button import (
            JackeryQueryButtonDescription,
        )
        from custom_components.jackery_solarvault.const import FIELD_CMD

        desc = JackeryQueryButtonDescription(
            key="test",
            translation_key="test",
            icon="mdi:test",
            action=AsyncMock(),
            message_type="MT",
            action_id=1,
            cmd=888,
        )
        btn = _make_query_button(description=desc)
        attrs = btn.extra_state_attributes
        assert FIELD_CMD in attrs
        assert attrs[FIELD_CMD] == 888  # noqa: PLR2004

    def test_extra_state_attributes_excludes_dev_type_when_none(self) -> None:  # noqa: PLR6301
        """When dev_type is None, devType must not appear in extra_state_attributes."""
        from custom_components.jackery_solarvault.button import (
            JackeryQueryButtonDescription,
        )
        from custom_components.jackery_solarvault.const import FIELD_DEV_TYPE

        desc = JackeryQueryButtonDescription(
            key="test",
            translation_key="test",
            icon="mdi:test",
            action=AsyncMock(),
            message_type="MT",
            action_id=1,
            cmd=2,
            dev_type=None,
        )
        btn = _make_query_button(description=desc)
        attrs = btn.extra_state_attributes
        assert FIELD_DEV_TYPE not in attrs

    def test_extra_state_attributes_includes_dev_type_when_set(self) -> None:  # noqa: PLR6301
        """When dev_type is set, devType must appear in extra_state_attributes."""
        from custom_components.jackery_solarvault.button import (
            JackeryQueryButtonDescription,
        )
        from custom_components.jackery_solarvault.const import FIELD_DEV_TYPE

        desc = JackeryQueryButtonDescription(
            key="test",
            translation_key="test",
            icon="mdi:test",
            action=AsyncMock(),
            message_type="MT",
            action_id=1,
            cmd=2,
            dev_type=42,
        )
        btn = _make_query_button(description=desc)
        attrs = btn.extra_state_attributes
        assert FIELD_DEV_TYPE in attrs
        assert attrs[FIELD_DEV_TYPE] == 42  # noqa: PLR2004

    def test_translation_key_matches_description(self) -> None:  # noqa: PLR6301
        """translation_key must be set from the description."""
        from custom_components.jackery_solarvault.button import (
            JackeryQueryButtonDescription,
        )

        desc = JackeryQueryButtonDescription(
            key="refresh_wifi_config",
            translation_key="refresh_wifi_config",
            icon="mdi:wifi-cog",
            action=AsyncMock(),
            message_type="MT",
            action_id=1,
            cmd=2,
        )
        btn = _make_query_button(description=desc)
        assert btn._attr_translation_key == "refresh_wifi_config"  # noqa: SLF001

    def test_icon_matches_description(self) -> None:  # noqa: PLR6301
        """Icon must be set from the description."""
        from custom_components.jackery_solarvault.button import (
            JackeryQueryButtonDescription,
        )

        desc = JackeryQueryButtonDescription(
            key="k",
            translation_key="k",
            icon="mdi:custom-icon",
            action=AsyncMock(),
            message_type="MT",
            action_id=1,
            cmd=2,
        )
        btn = _make_query_button(description=desc)
        assert btn._attr_icon == "mdi:custom-icon"  # noqa: SLF001

    def test_raise_action_error_raises_homeassistant_error(self) -> None:  # noqa: PLR6301
        """_raise_action_error must raise HomeAssistantError with entity_action_failed."""
        from homeassistant.exceptions import HomeAssistantError

        btn = _make_query_button()
        with pytest.raises(HomeAssistantError) as exc_info:
            btn._raise_action_error(RuntimeError("test error"))  # noqa: SLF001
        assert exc_info.value.translation_key == "entity_action_failed"

    def test_raise_action_error_includes_error_in_placeholders(self) -> None:  # noqa: PLR6301
        """_raise_action_error must include the error string in translation_placeholders."""
        from homeassistant.exceptions import HomeAssistantError

        btn = _make_query_button()
        with pytest.raises(HomeAssistantError) as exc_info:
            btn._raise_action_error(ValueError("some detail"))  # noqa: SLF001
        placeholders = exc_info.value.translation_placeholders or {}
        assert "some detail" in placeholders.get("error", "")

    async def test_async_press_calls_action(self) -> None:  # noqa: PLR6301
        """async_press must call the description's action."""
        from custom_components.jackery_solarvault.button import (
            JackeryQueryButtonDescription,
        )

        action_mock = AsyncMock()
        desc = JackeryQueryButtonDescription(
            key="k",
            translation_key="k",
            icon="mdi:k",
            action=action_mock,
            message_type="MT",
            action_id=1,
            cmd=2,
        )
        btn = _make_query_button(description=desc)
        await btn.async_press()
        action_mock.assert_called_once()

    async def test_async_press_reraises_config_entry_auth_failed(self) -> None:  # noqa: PLR6301
        """ConfigEntryAuthFailed must propagate unchanged from async_press."""
        from custom_components.jackery_solarvault.button import (
            JackeryQueryButtonDescription,
        )
        from homeassistant.exceptions import ConfigEntryAuthFailed

        async def _auth_fail(coord, dev_id):  # noqa: ANN001, ANN202, RUF029
            raise ConfigEntryAuthFailed("bad creds")  # noqa: TRY003

        desc = JackeryQueryButtonDescription(
            key="k",
            translation_key="k",
            icon="mdi:k",
            action=_auth_fail,
            message_type="MT",
            action_id=1,
            cmd=2,
        )
        btn = _make_query_button(description=desc)
        with pytest.raises(ConfigEntryAuthFailed):
            await btn.async_press()

    async def test_async_press_wraps_generic_exception(self) -> None:  # noqa: PLR6301
        """Generic exceptions must be wrapped into HomeAssistantError."""
        from custom_components.jackery_solarvault.button import (
            JackeryQueryButtonDescription,
        )
        from homeassistant.exceptions import HomeAssistantError

        async def _fail(coord, dev_id):  # noqa: ANN001, ANN202, RUF029
            raise RuntimeError("unexpected")

        desc = JackeryQueryButtonDescription(
            key="k",
            translation_key="k",
            icon="mdi:k",
            action=_fail,
            message_type="MT",
            action_id=1,
            cmd=2,
        )
        btn = _make_query_button(description=desc)
        with pytest.raises(HomeAssistantError) as exc_info:
            await btn.async_press()
        assert exc_info.value.translation_key == "entity_action_failed"


# ---------------------------------------------------------------------------
# JackeryDeleteStormAlertButton
# ---------------------------------------------------------------------------


def _make_delete_storm_alert_button(  # noqa: ANN202
    alert_id: str = "alert-1",
    device_id: str = "12345",
    coordinator_data: dict | None = None,
):
    """Construct a JackeryDeleteStormAlertButton with a mock coordinator."""
    from custom_components.jackery_solarvault.button import (
        JackeryDeleteStormAlertButton,
    )

    coordinator = _make_mock_coordinator(
        device_id,
        coordinator_data or {},
    )
    # Make coordinator.available return True
    coordinator.last_update_success = True
    return JackeryDeleteStormAlertButton(coordinator, device_id, alert_id=alert_id)


class TestJackeryDeleteStormAlertButton:
    """Tests for JackeryDeleteStormAlertButton."""

    def test_translation_key_is_delete_storm_alert(self) -> None:  # noqa: PLR6301
        """translation_key must be 'delete_storm_alert'."""
        btn = _make_delete_storm_alert_button()
        assert btn._attr_translation_key == "delete_storm_alert"  # noqa: SLF001

    def test_icon_is_correct(self) -> None:  # noqa: PLR6301
        """Icon must be the weather-lightning-rainy icon."""
        btn = _make_delete_storm_alert_button()
        assert btn._attr_icon == "mdi:weather-lightning-rainy"  # noqa: SLF001

    def test_unique_id_includes_alert_id(self) -> None:  # noqa: PLR6301
        """unique_id must include the alert_id."""
        btn = _make_delete_storm_alert_button(alert_id="test-alert-xyz")
        assert "test-alert-xyz" in (btn._attr_unique_id or "")  # noqa: SLF001

    def test_alert_returns_matching_alert(self) -> None:  # noqa: PLR6301
        """_alert must return the alert dict matching the stored alert_id."""
        payload = {
            "weather_plan": {
                "storm": [
                    {"alertId": "alert-1", "status": 1, "startTs": 100},
                    {"alertId": "alert-2", "status": 0},
                ],
            },
        }
        btn = _make_delete_storm_alert_button("alert-1", coordinator_data=payload)
        alert = btn._alert  # noqa: SLF001
        assert alert.get("alertId") == "alert-1"
        assert alert.get("startTs") == 100  # noqa: PLR2004

    def test_alert_returns_empty_dict_when_not_found(self) -> None:  # noqa: PLR6301
        """_alert must return an empty dict when the alert is not in the payload."""
        payload = {
            "weather_plan": {
                "storm": [
                    {"alertId": "alert-99"},
                ],
            },
        }
        btn = _make_delete_storm_alert_button("missing-alert", coordinator_data=payload)
        assert btn._alert == {}  # noqa: SLF001

    def test_alert_returns_empty_dict_when_no_weather_plan(self) -> None:  # noqa: PLR6301
        """_alert must return an empty dict when weather_plan is absent."""
        btn = _make_delete_storm_alert_button("alert-1", coordinator_data={})
        assert btn._alert == {}  # noqa: SLF001

    def test_extra_state_attributes_always_has_alert_id(self) -> None:  # noqa: PLR6301
        """extra_state_attributes must always include alertId."""
        from custom_components.jackery_solarvault.const import FIELD_ALERT_ID  # noqa: I001

        btn = _make_delete_storm_alert_button("my-alert-id")
        attrs = btn.extra_state_attributes
        assert FIELD_ALERT_ID in attrs
        assert attrs[FIELD_ALERT_ID] == "my-alert-id"

    def test_extra_state_attributes_includes_optional_fields_when_present(self) -> None:  # noqa: PLR6301
        """Optional fields (startTs, endTs, status, manual) must appear when in alert."""
        from custom_components.jackery_solarvault.const import (
            FIELD_END_TS,
            FIELD_MANUAL,
            FIELD_START_TS,
            FIELD_STATUS,
        )

        payload = {
            "weather_plan": {
                "storm": [
                    {
                        "alertId": "a1",
                        "startTs": 1000,
                        "endTs": 2000,
                        "status": 1,
                        "manual": True,
                    },
                ],
            },
        }
        btn = _make_delete_storm_alert_button("a1", coordinator_data=payload)
        attrs = btn.extra_state_attributes
        assert attrs.get(FIELD_START_TS) == 1000  # noqa: PLR2004
        assert attrs.get(FIELD_END_TS) == 2000  # noqa: PLR2004
        assert attrs.get(FIELD_STATUS) == 1
        assert attrs.get(FIELD_MANUAL) is True

    def test_extra_state_attributes_omits_optional_fields_when_absent(self) -> None:  # noqa: PLR6301
        """Optional fields must be absent from extra_state_attributes when not in alert."""
        from custom_components.jackery_solarvault.const import (
            FIELD_END_TS,
            FIELD_MANUAL,
            FIELD_START_TS,
            FIELD_STATUS,
        )

        payload = {"weather_plan": {"storm": [{"alertId": "a1"}]}}
        btn = _make_delete_storm_alert_button("a1", coordinator_data=payload)
        attrs = btn.extra_state_attributes
        assert FIELD_START_TS not in attrs
        assert FIELD_END_TS not in attrs
        assert FIELD_STATUS not in attrs
        assert FIELD_MANUAL not in attrs

    def test_raise_action_error_raises_homeassistant_error(self) -> None:  # noqa: PLR6301
        """_raise_action_error must raise HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError

        btn = _make_delete_storm_alert_button()
        with pytest.raises(HomeAssistantError) as exc_info:
            btn._raise_action_error("test error detail")  # noqa: SLF001
        assert exc_info.value.translation_key == "entity_action_failed"

    async def test_async_press_calls_delete_and_refresh(self) -> None:  # noqa: PLR6301
        """async_press must call async_delete_storm_alert and async_request_refresh."""
        from custom_components.jackery_solarvault.button import (
            JackeryDeleteStormAlertButton,
        )

        coordinator = _make_mock_coordinator("12345")
        coordinator.async_delete_storm_alert = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()

        btn = JackeryDeleteStormAlertButton(coordinator, "12345", alert_id="alert-x")
        await btn.async_press()
        coordinator.async_delete_storm_alert.assert_called_once_with("12345", "alert-x")
        coordinator.async_request_refresh.assert_called_once()

    async def test_async_press_reraises_config_entry_auth_failed(self) -> None:  # noqa: PLR6301
        """ConfigEntryAuthFailed must propagate from async_press."""
        from custom_components.jackery_solarvault.button import (
            JackeryDeleteStormAlertButton,
        )
        from homeassistant.exceptions import ConfigEntryAuthFailed

        coordinator = _make_mock_coordinator("12345")
        coordinator.async_delete_storm_alert = AsyncMock(
            side_effect=ConfigEntryAuthFailed("bad creds"),
        )
        coordinator.async_request_refresh = AsyncMock()

        btn = JackeryDeleteStormAlertButton(coordinator, "12345", alert_id="alert-x")
        with pytest.raises(ConfigEntryAuthFailed):
            await btn.async_press()

    async def test_async_press_wraps_runtime_error(self) -> None:  # noqa: PLR6301
        """Generic RuntimeError from async_press must be wrapped into HomeAssistantError."""
        from custom_components.jackery_solarvault.button import (
            JackeryDeleteStormAlertButton,
        )
        from homeassistant.exceptions import HomeAssistantError

        coordinator = _make_mock_coordinator("12345")
        coordinator.async_delete_storm_alert = AsyncMock(
            side_effect=RuntimeError("broker down"),
        )
        coordinator.async_request_refresh = AsyncMock()

        btn = JackeryDeleteStormAlertButton(coordinator, "12345", alert_id="alert-x")
        with pytest.raises(HomeAssistantError) as exc_info:
            await btn.async_press()
        assert exc_info.value.translation_key == "entity_action_failed"


# ---------------------------------------------------------------------------
# JackeryReadScheduleButton
# ---------------------------------------------------------------------------


def _make_read_schedule_button(  # noqa: ANN202
    task_type: int = 2,
    key_suffix: str = "read_custom_mode_schedule",
    plug_sn: str = "",
    device_id: str = "12345",
):
    """Construct a JackeryReadScheduleButton for testing."""
    from custom_components.jackery_solarvault.button import JackeryReadScheduleButton  # noqa: I001

    coordinator = _make_mock_coordinator(device_id)
    return JackeryReadScheduleButton(
        coordinator,
        device_id,
        task_type=task_type,
        key_suffix=key_suffix,
        translation_key=key_suffix,
        icon="mdi:calendar-clock",
        plug_sn=plug_sn,
    )


class TestJackeryReadScheduleButton:
    """Tests for JackeryReadScheduleButton entity."""

    def test_extra_state_attributes_has_task_type(self) -> None:  # noqa: PLR6301
        """extra_state_attributes must always include taskType."""
        btn = _make_read_schedule_button(task_type=2)
        attrs = btn.extra_state_attributes
        assert "taskType" in attrs
        assert attrs["taskType"] == 2  # noqa: PLR2004

    def test_extra_state_attributes_omits_device_sn_when_empty(self) -> None:  # noqa: PLR6301
        """DeviceSn must be absent from extra_state_attributes when plug_sn is empty."""
        from custom_components.jackery_solarvault.const import FIELD_DEVICE_SN  # noqa: I001

        btn = _make_read_schedule_button(plug_sn="")
        attrs = btn.extra_state_attributes
        assert FIELD_DEVICE_SN not in attrs

    def test_extra_state_attributes_includes_device_sn_when_set(self) -> None:  # noqa: PLR6301
        """DeviceSn must appear in extra_state_attributes when plug_sn is set."""
        from custom_components.jackery_solarvault.const import FIELD_DEVICE_SN  # noqa: I001

        btn = _make_read_schedule_button(plug_sn="PLUG-SN-001")
        attrs = btn.extra_state_attributes
        assert FIELD_DEVICE_SN in attrs
        assert attrs[FIELD_DEVICE_SN] == "PLUG-SN-001"

    def test_translation_key_is_set_from_constructor(self) -> None:  # noqa: PLR6301
        """translation_key must match the value passed in constructor."""
        btn = _make_read_schedule_button(key_suffix="read_time_electricity_schedule")
        assert btn._attr_translation_key == "read_time_electricity_schedule"  # noqa: SLF001

    def test_task_type_stored_correctly(self) -> None:  # noqa: PLR6301
        """task_type must be stored as an integer attribute."""
        btn = _make_read_schedule_button(task_type=3)
        assert btn._task_type == 3  # noqa: PLR2004, SLF001

    def test_plug_sn_stored_correctly(self) -> None:  # noqa: PLR6301
        """plug_sn must be stored correctly."""
        btn = _make_read_schedule_button(plug_sn="SN-XYZ")
        assert btn._plug_sn == "SN-XYZ"  # noqa: SLF001

    def test_custom_mode_task_type(self) -> None:  # noqa: PLR6301
        """TIMER_TASK_TYPE_CUSTOM_MODE should be task type 2."""
        from custom_components.jackery_solarvault.const import (
            TIMER_TASK_TYPE_CUSTOM_MODE,
        )

        assert TIMER_TASK_TYPE_CUSTOM_MODE == 2  # noqa: PLR2004
        btn = _make_read_schedule_button(task_type=TIMER_TASK_TYPE_CUSTOM_MODE)
        assert btn.extra_state_attributes["taskType"] == 2  # noqa: PLR2004

    def test_smart_plug_task_type(self) -> None:  # noqa: PLR6301
        """TIMER_TASK_TYPE_SMART_PLUG should be task type 1."""
        from custom_components.jackery_solarvault.const import (
            TIMER_TASK_TYPE_SMART_PLUG,
        )

        assert TIMER_TASK_TYPE_SMART_PLUG == 1
        btn = _make_read_schedule_button(task_type=TIMER_TASK_TYPE_SMART_PLUG)
        assert btn.extra_state_attributes["taskType"] == 1

    def test_time_elec_task_type(self) -> None:  # noqa: PLR6301
        """TIMER_TASK_TYPE_TIME_ELEC should be task type 3."""
        from custom_components.jackery_solarvault.const import TIMER_TASK_TYPE_TIME_ELEC  # noqa: I001

        assert TIMER_TASK_TYPE_TIME_ELEC == 3  # noqa: PLR2004
        btn = _make_read_schedule_button(task_type=TIMER_TASK_TYPE_TIME_ELEC)
        assert btn.extra_state_attributes["taskType"] == 3  # noqa: PLR2004

    def test_raise_action_error_raises_homeassistant_error(self) -> None:  # noqa: PLR6301
        """_raise_action_error must raise HomeAssistantError."""
        from homeassistant.exceptions import HomeAssistantError

        btn = _make_read_schedule_button()
        with pytest.raises(HomeAssistantError) as exc_info:
            btn._raise_action_error("schedule read failed")  # noqa: SLF001
        assert exc_info.value.translation_key == "entity_action_failed"

    async def test_async_press_calls_read_schedule_and_refresh(self) -> None:  # noqa: PLR6301
        """async_press must call async_read_device_schedule and async_request_refresh."""
        from custom_components.jackery_solarvault.button import (
            JackeryReadScheduleButton,
        )

        coordinator = _make_mock_coordinator("12345")
        coordinator.async_read_device_schedule = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()

        btn = JackeryReadScheduleButton(
            coordinator,
            "12345",
            task_type=2,
            key_suffix="read_custom_mode_schedule",
            translation_key="read_custom_mode_schedule",
            icon="mdi:calendar-clock",
        )
        await btn.async_press()
        coordinator.async_read_device_schedule.assert_called_once_with(
            "12345",
            task_type=2,
            plug_sn="",
        )
        coordinator.async_request_refresh.assert_called_once()

    async def test_async_press_with_plug_sn(self) -> None:  # noqa: PLR6301
        """async_press with a plug_sn must pass it to async_read_device_schedule."""
        from custom_components.jackery_solarvault.button import (
            JackeryReadScheduleButton,
        )

        coordinator = _make_mock_coordinator("12345")
        coordinator.async_read_device_schedule = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()

        btn = JackeryReadScheduleButton(
            coordinator,
            "12345",
            task_type=1,
            key_suffix="smart_plug_1_read_schedule",
            translation_key="read_smart_plug_schedule",
            icon="mdi:calendar-clock",
            plug_sn="PLUG-SN-001",
        )
        await btn.async_press()
        coordinator.async_read_device_schedule.assert_called_once_with(
            "12345",
            task_type=1,
            plug_sn="PLUG-SN-001",
        )


# ---------------------------------------------------------------------------
# JackeryRefreshWeatherPlanButton
# ---------------------------------------------------------------------------


class TestJackeryRefreshWeatherPlanButton:
    """Tests for JackeryRefreshWeatherPlanButton entity."""

    def test_translation_key_is_refresh_weather_plan(self) -> None:  # noqa: PLR6301
        """translation_key must be 'refresh_weather_plan'."""
        from custom_components.jackery_solarvault.button import (
            JackeryRefreshWeatherPlanButton,
        )

        coordinator = _make_mock_coordinator("12345")
        btn = JackeryRefreshWeatherPlanButton(coordinator, "12345")
        assert btn._attr_translation_key == "refresh_weather_plan"  # noqa: SLF001

    def test_icon_is_weather_cloudy_clock(self) -> None:  # noqa: PLR6301
        """Icon must be 'mdi:weather-cloudy-clock'."""
        from custom_components.jackery_solarvault.button import (
            JackeryRefreshWeatherPlanButton,
        )

        coordinator = _make_mock_coordinator("12345")
        btn = JackeryRefreshWeatherPlanButton(coordinator, "12345")
        assert btn._attr_icon == "mdi:weather-cloudy-clock"  # noqa: SLF001

    def test_unique_id_includes_refresh_weather_plan(self) -> None:  # noqa: PLR6301
        """unique_id must include 'refresh_weather_plan'."""
        from custom_components.jackery_solarvault.button import (
            JackeryRefreshWeatherPlanButton,
        )

        coordinator = _make_mock_coordinator("99999")
        btn = JackeryRefreshWeatherPlanButton(coordinator, "99999")
        assert "refresh_weather_plan" in (btn._attr_unique_id or "")  # noqa: SLF001

    async def test_async_press_calls_query_weather_plan_and_refresh(self) -> None:  # noqa: PLR6301
        """async_press must call async_query_weather_plan and async_request_refresh."""
        from custom_components.jackery_solarvault.button import (
            JackeryRefreshWeatherPlanButton,
        )

        coordinator = _make_mock_coordinator("12345")
        coordinator.async_query_weather_plan = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()

        btn = JackeryRefreshWeatherPlanButton(coordinator, "12345")
        await btn.async_press()
        coordinator.async_query_weather_plan.assert_called_once_with("12345")
        coordinator.async_request_refresh.assert_called_once()

    async def test_async_press_reraises_config_entry_auth_failed(self) -> None:  # noqa: PLR6301
        """ConfigEntryAuthFailed must propagate from async_press."""
        from custom_components.jackery_solarvault.button import (
            JackeryRefreshWeatherPlanButton,
        )
        from homeassistant.exceptions import ConfigEntryAuthFailed

        coordinator = _make_mock_coordinator("12345")
        coordinator.async_query_weather_plan = AsyncMock(
            side_effect=ConfigEntryAuthFailed("creds rejected"),
        )
        coordinator.async_request_refresh = AsyncMock()

        btn = JackeryRefreshWeatherPlanButton(coordinator, "12345")
        with pytest.raises(ConfigEntryAuthFailed):
            await btn.async_press()

    async def test_async_press_wraps_generic_exception(self) -> None:  # noqa: PLR6301
        """Generic exception from async_press must be wrapped into HomeAssistantError."""
        from custom_components.jackery_solarvault.button import (
            JackeryRefreshWeatherPlanButton,
        )
        from homeassistant.exceptions import HomeAssistantError

        coordinator = _make_mock_coordinator("12345")
        coordinator.async_query_weather_plan = AsyncMock(
            side_effect=RuntimeError("cloud unreachable"),
        )
        coordinator.async_request_refresh = AsyncMock()

        btn = JackeryRefreshWeatherPlanButton(coordinator, "12345")
        with pytest.raises(HomeAssistantError) as exc_info:
            await btn.async_press()
        assert exc_info.value.translation_key == "entity_action_failed"

    def test_raise_action_error_has_entity_in_placeholders(self) -> None:  # noqa: PLR6301
        """The error placeholder must include the entity key 'refresh_weather_plan'."""
        from custom_components.jackery_solarvault.button import (
            JackeryRefreshWeatherPlanButton,
        )
        from homeassistant.exceptions import HomeAssistantError

        coordinator = _make_mock_coordinator("12345")
        btn = JackeryRefreshWeatherPlanButton(coordinator, "12345")
        with pytest.raises(HomeAssistantError) as exc_info:
            btn._raise_action_error("detail")  # noqa: SLF001
        placeholders = exc_info.value.translation_placeholders or {}
        assert placeholders.get("entity") == "refresh_weather_plan"


# ---------------------------------------------------------------------------
# JackeryRebootButton
# ---------------------------------------------------------------------------


class TestJackeryRebootButton:
    """Tests for JackeryRebootButton (kept minimal since core logic unchanged)."""

    def test_translation_key_is_reboot_device(self) -> None:  # noqa: PLR6301
        """translation_key must be 'reboot_device'."""
        from custom_components.jackery_solarvault.button import JackeryRebootButton  # noqa: I001

        coordinator = _make_mock_coordinator("12345")
        btn = JackeryRebootButton(coordinator, "12345")
        assert btn._attr_translation_key == "reboot_device"  # noqa: SLF001

    def test_unique_id_includes_reboot_device(self) -> None:  # noqa: PLR6301
        """unique_id must include 'reboot_device'."""
        from custom_components.jackery_solarvault.button import JackeryRebootButton  # noqa: I001

        coordinator = _make_mock_coordinator("12345")
        btn = JackeryRebootButton(coordinator, "12345")
        assert "reboot_device" in (btn._attr_unique_id or "")  # noqa: SLF001

    def test_raise_action_error_has_reboot_device_entity(self) -> None:  # noqa: PLR6301
        """The error placeholder must include 'reboot_device' as entity."""
        from custom_components.jackery_solarvault.button import JackeryRebootButton  # noqa: I001
        from homeassistant.exceptions import HomeAssistantError

        coordinator = _make_mock_coordinator("12345")
        btn = JackeryRebootButton(coordinator, "12345")
        with pytest.raises(HomeAssistantError) as exc_info:
            btn._raise_action_error("details")  # noqa: SLF001
        placeholders = exc_info.value.translation_placeholders or {}
        assert placeholders.get("entity") == "reboot_device"

    async def test_async_press_calls_reboot_and_refresh(self) -> None:  # noqa: PLR6301
        """async_press must call async_reboot_device and async_request_refresh."""
        from custom_components.jackery_solarvault.button import JackeryRebootButton  # noqa: I001

        coordinator = _make_mock_coordinator("12345")
        coordinator.async_reboot_device = AsyncMock()
        coordinator.async_request_refresh = AsyncMock()

        btn = JackeryRebootButton(coordinator, "12345")
        await btn.async_press()
        coordinator.async_reboot_device.assert_called_once_with("12345")
        coordinator.async_request_refresh.assert_called_once()


# ---------------------------------------------------------------------------
# Regression: storm alert with integer alertId 0
# ---------------------------------------------------------------------------


def test_storm_alert_id_zero_is_valid() -> None:
    """AlertId of integer 0 must be accepted (boundary: 0 is falsy but valid)."""
    fn = _get_storm_alert_id()
    # 0 is in neither (None,) nor ("",) so should be returned as "0"
    result = fn({"alertId": 0})
    assert result == "0"


def test_storm_alerts_preserves_order() -> None:
    """_storm_alerts must preserve the order of alerts in the storm list."""
    fn = _get_storm_alerts()
    weather = {
        "storm": [
            {"alertId": "first"},
            {"alertId": "second"},
            {"alertId": "third"},
        ],
    }
    result = fn(weather)
    assert [a["alertId"] for a in result] == ["first", "second", "third"]


def test_smart_plug_device_sn_with_mixed_case_values() -> None:
    """DeviceSn with mixed-case values must be returned as-is (no lowercasing)."""
    fn = _get_smart_plug_device_sn()
    plug = {"deviceSn": "SN-MixedCase"}
    assert fn(plug) == "SN-MixedCase"
