"""Tests for module-level functions in coordinator.py for coverage."""

from datetime import date

import pytest

from custom_components.jackery_solarvault.const import (
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    FIELD_SYSTEM_REGION,
)
from custom_components.jackery_solarvault.coordinator import (
    BackfillStatus,
    _backfill_period_is_closed,
    _dict_list_identity_values,
    _load_mqtt_push_client,
    _normalize_backfill_status,
    _slow_fetch_failure_log_level,
    _stable_payload_debug_signature,
    changed_dict_values,
    find_dict_with_any_key,
    find_list_for_key,
    first_nonblank_source_name,
    is_alarm_message,
    is_device_ota_version_message,
    is_grid_standard_sync_message,
    is_mqtt_auth_failure,
    is_subdevice_payload,
    is_third_party_mqtt_config_message,
    is_time_zone_config_message,
    is_transient_connect_failure,
    merge_dict_values,
    merge_missing_dict_values,
    merge_present_dict_values,
    mqtt_connect_failure_signature,
    normalize_battery_pack_payload,
    normalize_live_property_payload,
    normalized_company_id,
    normalized_region,
    normalized_source_regions,
    source_regions,
    valid_price_sources,
)


class TestBackfillPeriodIsClosed:
    """Test _backfill_period_is_closed for all date types."""

    def test_day_type(self) -> None:
        today = date(2026, 8, 16)
        assert (
            _backfill_period_is_closed(DATE_TYPE_DAY, date(2026, 8, 15), today=today)
            is True
        )
        assert _backfill_period_is_closed(DATE_TYPE_DAY, today, today=today) is False

    def test_week_type(self) -> None:
        today = date(2026, 8, 16)  # Sunday
        assert (
            _backfill_period_is_closed(DATE_TYPE_WEEK, date(2026, 8, 3), today=today)
            is True
        )
        assert (
            _backfill_period_is_closed(DATE_TYPE_WEEK, date(2026, 8, 10), today=today)
            is False
        )

    def test_month_type_december_rollover(self) -> None:
        """December -> January year rollover (line 864)."""
        today = date(2026, 1, 15)
        assert (
            _backfill_period_is_closed(DATE_TYPE_MONTH, date(2025, 12, 1), today=today)
            is True
        )

    def test_month_type_current(self) -> None:
        today = date(2026, 8, 16)
        assert (
            _backfill_period_is_closed(DATE_TYPE_MONTH, date(2026, 8, 1), today=today)
            is False
        )

    def test_year_type(self) -> None:
        today = date(2026, 8, 16)
        assert (
            _backfill_period_is_closed(DATE_TYPE_YEAR, date(2025, 1, 1), today=today)
            is True
        )
        assert (
            _backfill_period_is_closed(DATE_TYPE_YEAR, date(2026, 1, 1), today=today)
            is False
        )

    def test_unknown_type_returns_false(self) -> None:
        today = date(2026, 8, 16)
        assert _backfill_period_is_closed("UNKNOWN", today, today=today) is False


class TestNormalizeBackfillStatus:
    """Test _normalize_backfill_status with BackfillStatus enum."""

    def test_known_status_returns_enum(self) -> None:
        result = _normalize_backfill_status(BackfillStatus.IMPORTED, closed=True)
        assert result == BackfillStatus.IMPORTED

    def test_auth_error_maps_to_retryable(self) -> None:
        result = _normalize_backfill_status("auth_error", closed=True)
        assert result == BackfillStatus.RETRYABLE

    def test_unknown_open_maps_to_pending(self) -> None:
        result = _normalize_backfill_status("unknown", closed=False)
        assert result == BackfillStatus.PENDING

    def test_unknown_closed_maps_to_pending(self) -> None:
        result = _normalize_backfill_status("unknown", closed=True)
        assert result == BackfillStatus.PENDING

    def test_invalid_type_returns_pending(self) -> None:
        result = _normalize_backfill_status(123, closed=True)
        assert result == BackfillStatus.PENDING


class TestLoadMqttPushClient:
    """Test _load_mqtt_push_client."""

    def test_returns_client_class(self) -> None:
        client_class = _load_mqtt_push_client()
        assert client_class is not None
        assert client_class.__name__ == "JackeryMqttPushClient"


class TestSlowFetchFailureLogLevel:
    """Test _slow_fetch_failure_log_level."""

    def test_returns_log_level(self) -> None:
        from custom_components.jackery_solarvault.client.api import JackeryError

        err = JackeryError("test error")
        level = _slow_fetch_failure_log_level(err, suppressed=False)
        assert level in {10, 20, 30, 40}  # DEBUG, INFO, WARNING, ERROR


class TestStablePayloadDebugSignature:
    """Test _stable_payload_debug_signature."""

    def test_returns_signature(self) -> None:
        event = {"key": "value"}
        sig = _stable_payload_debug_signature(event)
        assert isinstance(sig, str)


class TestDictListIdentityValues:
    """Test _dict_list_identity_values."""

    def test_returns_serial_keys(self) -> None:
        item = {"deviceSn": "pack-1", "sn": "serial-123"}
        result = _dict_list_identity_values(item)
        assert "serial:pack-1" in result
        assert "serial:serial-123" in result

    def test_returns_id_keys(self) -> None:
        item = {"devId": "id-1", "deviceId": "id-2", "id": "id-3", "idx": 5}
        result = _dict_list_identity_values(item)
        assert "devId:id-1" in result
        assert "deviceId:id-2" in result
        assert "id:id-3" in result
        assert "idx:5" in result

    def test_skips_blank_values(self) -> None:
        item = {"deviceSn": "", "devId": None, "id": "valid"}
        result = _dict_list_identity_values(item)
        assert "id:valid" in result


class TestMergePresentDictValues:
    """Test merge_present_dict_values."""

    def test_preserves_base_when_update_blank(self) -> None:
        base = {"key": "value"}
        updates = {"key": None}
        result = merge_present_dict_values(base, updates)
        assert result["key"] == "value"

    def test_update_overwrites_when_base_blank(self) -> None:
        base = {"key": None}
        updates = {"key": "new_value"}
        result = merge_present_dict_values(base, updates)
        assert result["key"] == "new_value"


class TestMergeMissingDictValues:
    """Test merge_missing_dict_values."""

    def test_fills_missing_nested(self) -> None:
        base = {"device": {"soc": 50}}
        updates = {"device": {"temp": 25}}
        result = merge_missing_dict_values(base, updates)
        assert result["device"]["soc"] == 50
        assert result["device"]["temp"] == 25

    def test_does_not_overwrite_existing(self) -> None:
        base = {"device": {"soc": 50}}
        updates = {"device": {"soc": 60, "temp": 25}}
        result = merge_missing_dict_values(base, updates)
        assert result["device"]["soc"] == 50
        assert result["device"]["temp"] == 25


class TestFindDictWithAnyKey:
    """Test find_dict_with_any_key."""

    def test_returns_none_for_none_input(self) -> None:
        result = find_dict_with_any_key(None, {"deviceSn"})
        assert result is None

    def test_returns_none_for_number_input(self) -> None:
        result = find_dict_with_any_key(123, {"deviceSn"})
        assert result is None


class TestFindListForKey:
    """Test find_list_for_key."""

    def test_returns_none_for_none_input(self) -> None:
        result = find_list_for_key(None, "batteryPacks")
        assert result is None

    def test_returns_none_for_number_input(self) -> None:
        result = find_list_for_key(123, "batteryPacks")
        assert result is None


class TestMergeDictValues:
    """Test merge_dict_values."""

    def test_merges_updates_into_base(self) -> None:
        base = {"a": 1, "b": 2}
        updates = {"b": 3, "c": 4}
        result = merge_dict_values(base, updates)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_overwrites_base_with_none(self) -> None:
        base = {"a": 1}
        updates = {"a": None}
        result = merge_dict_values(base, updates)
        assert result == {"a": None}

    def test_handles_nested_dicts(self) -> None:
        base = {"device": {"soc": 50}}
        updates = {"device": {"temp": 25}}
        result = merge_dict_values(base, updates)
        assert result["device"]["soc"] == 50
        assert result["device"]["temp"] == 25


class TestChangedDictValues:
    """Test changed_dict_values."""

    def test_detects_new_keys(self) -> None:
        before = {"a": 1}
        after = {"a": 1, "b": 2}
        result = changed_dict_values(before, after)
        assert result == {"b": 2}

    def test_detects_changed_values(self) -> None:
        before = {"a": 1}
        after = {"a": 2}
        result = changed_dict_values(before, after)
        assert result == {"a": 2}

    def test_handles_nested_dicts(self) -> None:
        before = {"device": {"soc": 50}}
        after = {"device": {"soc": 60}}
        result = changed_dict_values(before, after)
        assert result == {"device": {"soc": 60}}


class TestNormalizeBatteryPackPayload:
    """Test normalize_battery_pack_payload."""

    def test_flattens_updates_object(self) -> None:
        item = {"deviceSn": "pack-1", "updates": {"soc": 50}}
        result = normalize_battery_pack_payload(item)
        assert result["deviceSn"] == "pack-1"
        assert result["soc"] == 50


class TestNormalizeLivePropertyPayload:
    """Test normalize_live_property_payload."""

    def test_returns_shallow_copy(self) -> None:
        source = {"soc": 50, "temp": 25}
        result = normalize_live_property_payload(source)
        assert result == source
        assert result is not source


class TestNormalizedCompanyId:
    """Test normalized_company_id."""

    def test_returns_int_for_valid(self) -> None:
        result = normalized_company_id("123")
        assert result == 123

    def test_returns_none_for_invalid(self) -> None:
        result = normalized_company_id("abc")
        assert result is None


class TestNormalizedRegion:
    """Test normalized_region."""

    def test_uppercases_and_strips(self) -> None:
        result = normalized_region("  de  ")
        assert result == "DE"


class TestSourceRegions:
    """Test source_regions."""

    def test_extracts_from_system_region(self) -> None:
        source = {FIELD_SYSTEM_REGION: "DE"}
        result = source_regions(source)
        assert result == ["DE"]

    def test_splits_comma_separated(self) -> None:
        source = {FIELD_SYSTEM_REGION: "DE, FR"}
        result = source_regions(source)
        assert result == ["DE", "FR"]


class TestNormalizedSourceRegions:
    """Test normalized_source_regions."""

    def test_normalizes_and_dedupes(self) -> None:
        source = {FIELD_SYSTEM_REGION: "de, DE, fr"}
        result = normalized_source_regions(source)
        assert "DE" in result
        assert "FR" in result


class TestFirstNonblankSourceName:
    """Test first_nonblank_source_name."""

    def test_returns_first_nonblank(self) -> None:
        source = {"key1": "", "key2": "value"}
        result = first_nonblank_source_name(source, "key1", "key2")
        assert result == "value"


class TestIsMqttAuthFailure:
    """Test is_mqtt_auth_failure."""

    def test_recognizes_auth_failure(self) -> None:
        assert is_mqtt_auth_failure("connect rc=5") is True
        assert is_mqtt_auth_failure("bad user name or password") is True


class TestIsTransientConnectFailure:
    """Test is_transient_connect_failure."""

    def test_recognizes_transient(self) -> None:
        assert is_transient_connect_failure("connection refused") is True
        assert is_transient_connect_failure("server unavailable") is True


class TestMqttConnectFailureSignature:
    """Test mqtt_connect_failure_signature."""

    def test_returns_signature(self) -> None:
        result = mqtt_connect_failure_signature("connection refused")
        assert result == "connection refused"

    def test_handles_tls_errors(self) -> None:
        result = mqtt_connect_failure_signature("CERTIFICATE_VERIFY_FAILED")
        assert result == "tls_certificate_verify_failed"


class TestIsAlarmMessage:
    """Test is_alarm_message."""

    def test_returns_false_for_none_message_type(self) -> None:
        assert is_alarm_message(None, None, {}) is False

    def test_returns_false_for_empty_body(self) -> None:
        assert is_alarm_message("some_type", 123, {}) is False


class TestIsDeviceOtaVersionMessage:
    """Test is_device_ota_version_message."""

    def test_returns_false_for_none_action_id(self) -> None:
        assert is_device_ota_version_message(None, {}) is False

    def test_returns_false_for_empty_body(self) -> None:
        assert is_device_ota_version_message(123, {}) is False


class TestIsGridStandardSyncMessage:
    """Test is_grid_standard_sync_message."""

    def test_returns_false_for_none_action_id(self) -> None:
        assert is_grid_standard_sync_message(None, {}) is False

    def test_returns_false_for_empty_body(self) -> None:
        assert is_grid_standard_sync_message(123, {}) is False


class TestIsSubdevicePayload:
    """Test is_subdevice_payload."""

    def test_returns_false_for_none_payload(self) -> None:
        # Function expects dict for payload, test with empty dict
        assert (
            is_subdevice_payload({}, {}, frozenset(), frozenset(), frozenset()) is False
        )

    def test_returns_false_for_empty_payload(self) -> None:
        assert (
            is_subdevice_payload({}, {}, frozenset(), frozenset(), frozenset()) is False
        )

    def test_returns_true_for_subdevice_message_type(self) -> None:
        payload = {"messageType": "SubDeviceData"}
        body = {}
        result = is_subdevice_payload(
            payload, body, frozenset(), frozenset(), frozenset()
        )
        assert result is True


class TestIsThirdPartyMqttConfigMessage:
    """Test is_third_party_mqtt_config_message."""

    def test_returns_false_for_none_msg_type(self) -> None:
        assert is_third_party_mqtt_config_message(None, None, {}) is False

    def test_returns_false_for_empty_body(self) -> None:
        assert is_third_party_mqtt_config_message("some_type", 123, {}) is False


class TestIsTimeZoneConfigMessage:
    """Test is_time_zone_config_message."""

    def test_returns_false_for_none_action_id(self) -> None:
        assert is_time_zone_config_message(None, {}) is False

    def test_returns_false_for_empty_body(self) -> None:
        assert is_time_zone_config_message(123, {}) is False


class TestValidPriceSources:
    """Test valid_price_sources."""

    def test_returns_empty_for_invalid(self) -> None:
        result = valid_price_sources("string")
        assert result == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
