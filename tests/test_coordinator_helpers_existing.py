"""Tests for coordinator helper functions that exist in coordinator.py."""

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.jackery_solarvault.client.api import JackeryError
from custom_components.jackery_solarvault.const import (
    DATE_TYPE_DAY,
    FIELD_BAT_SOC,
    FIELD_SUB_TYPE,
    FIELD_SYSTEM_REGION,
    SUBDEVICE_DEV_TYPE_BATTERY_PACK,
)
from custom_components.jackery_solarvault.coordinator import (
    _backfill_period_is_closed,  # noqa: PLC2701, RUF105
    _clean_dict_list_update,  # noqa: PLC2701, RUF105
    _dict_list_identity_values,  # noqa: PLC2701, RUF105
    _is_blank_value,  # noqa: PLC2701, RUF105
    _is_system_busy_error,  # noqa: PLC2701, RUF105
    _load_mqtt_push_client,  # noqa: PLC2701, RUF105
    _merge_identified_dict_lists,  # noqa: PLC2701, RUF105
    _normalize_backfill_status,  # noqa: PLC2701, RUF105
    _payload_debug_capture_enabled,  # noqa: PLC2701, RUF105
    _slow_fetch_failure_log_level,  # noqa: PLC2701, RUF105
    _stable_payload_debug_signature,  # noqa: PLC2701, RUF105
    battery_packs_from_source,
    changed_dict_values,
    find_dict_with_any_key,
    find_list_for_key,
    first_nonblank_source_name,
    is_alarm_message,
    is_device_ota_version_message,
    is_grid_standard_sync_message,
    is_mqtt_auth_failure,
    is_mqtt_connect_info_message,
    is_subdevice_payload,
    is_third_party_mqtt_config_message,
    is_time_zone_config_message,
    is_transient_connect_failure,
    is_wifi_config_message,
    is_wifi_list_message,
    looks_like_battery_pack,
    merge_dict_values,
    merge_missing_dict_values,
    merge_present_dict_values,
    mqtt_connect_failure_signature,
    normalize_battery_pack_payload,
    normalize_live_property_payload,
    normalized_company_id,
    normalized_region,
    normalized_source_regions,
    shelly_cloud_api_device_id,
    source_regions,
    valid_price_sources,
)


class TestCoordinatorHelpersExisting:  # noqa: PLR0904, RUF105
    """Test coordinator helper functions that exist in coordinator.py."""

    def _bare_entry(self) -> Any:  # noqa: ANN401, PLR6301, RUF105
        entry = SimpleNamespace()
        entry.options = {}
        entry.data = {}
        return entry

    def test_payload_debug_capture_enabled(self) -> None:  # noqa: PLR6301, RUF105
        """Test _payload_debug_capture_enabled."""
        result = _payload_debug_capture_enabled(None)
        assert isinstance(result, bool)

    def test_stable_payload_debug_signature(self) -> None:  # noqa: PLR6301, RUF105
        """Test _stable_payload_debug_signature."""
        event = {"key": "value"}
        sig = _stable_payload_debug_signature(event)
        assert isinstance(sig, str)

    def test_slow_fetch_failure_log_level(self) -> None:  # noqa: PLR6301, RUF105
        """Test _slow_fetch_failure_log_level."""
        err = JackeryError("test error")
        level = _slow_fetch_failure_log_level(err, suppressed=False)
        assert level in {10, 20, 30, 40}

    def test_load_mqtt_push_client(self) -> None:  # noqa: PLR6301, RUF105
        """Test _load_mqtt_push_client."""
        client_class = _load_mqtt_push_client()
        assert client_class is not None

    def test_backfill_period_is_closed(self) -> None:  # noqa: PLR6301, RUF105
        """Test _backfill_period_is_closed."""
        today = date(2026, 8, 16)
        assert (
            _backfill_period_is_closed(DATE_TYPE_DAY, date(2026, 8, 15), today=today)
            is True
        )
        assert _backfill_period_is_closed(DATE_TYPE_DAY, today, today=today) is False

    def test_normalize_backfill_status(self) -> None:  # noqa: PLR6301, RUF105
        """Test _normalize_backfill_status."""
        from custom_components.jackery_solarvault.coordinator import BackfillStatus  # noqa: I001, PLC0415, RUF105

        result = _normalize_backfill_status(BackfillStatus.IMPORTED, closed=True)
        assert result == BackfillStatus.IMPORTED

    def test_is_system_busy_error(self) -> None:  # noqa: PLR6301, RUF105
        """Test _is_system_busy_error."""
        from custom_components.jackery_solarvault.client.api import JackeryError  # noqa: I001, PLC0415, RUF105

        err = JackeryError("system busy")
        result = _is_system_busy_error(err)
        assert isinstance(result, bool)

    def test_is_mqtt_auth_failure(self) -> None:  # noqa: PLR6301, RUF105
        """Test is_mqtt_auth_failure."""
        assert is_mqtt_auth_failure("connect rc=5") is True
        assert is_mqtt_auth_failure("bad user name or password") is True

    def test_is_transient_connect_failure(self) -> None:  # noqa: PLR6301, RUF105
        """Test is_transient_connect_failure."""
        assert is_transient_connect_failure("connection refused") is True
        assert is_transient_connect_failure("server unavailable") is True

    def test_mqtt_connect_failure_signature(self) -> None:  # noqa: PLR6301, RUF105
        """Test mqtt_connect_failure_signature."""
        result = mqtt_connect_failure_signature("connection refused")
        assert result == "connection refused"

    def test_merge_dict_values(self) -> None:  # noqa: PLR6301, RUF105
        """Test merge_dict_values."""
        base = {"a": 1, "b": 2}
        updates = {"b": 3, "c": 4}
        result = merge_dict_values(base, updates)
        assert result == {"a": 1, "b": 3, "c": 4}

    def test_changed_dict_values(self) -> None:  # noqa: PLR6301, RUF105
        """Test changed_dict_values."""
        before = {"a": 1}
        after = {"a": 1, "b": 2}
        result = changed_dict_values(before, after)
        assert result == {"b": 2}

    def test_is_blank_value(self) -> None:  # noqa: PLR6301, RUF105
        """Test _is_blank_value."""
        assert _is_blank_value(None) is True
        assert _is_blank_value("") is True
        assert _is_blank_value("   ") is True
        assert _is_blank_value([]) is True
        assert _is_blank_value({}) is True
        assert _is_blank_value("value") is False
        assert _is_blank_value(0) is False
        assert _is_blank_value(False) is False

    def test_dict_list_identity_values(self) -> None:  # noqa: PLR6301, RUF105
        """Test _dict_list_identity_values."""
        item = {"deviceSn": "pack-1", "sn": "serial-123"}
        result = _dict_list_identity_values(item)
        assert "serial:pack-1" in result
        assert "serial:serial-123" in result

    def test_clean_dict_list_update(self) -> None:  # noqa: PLR6301, RUF105
        """Test _clean_dict_list_update."""
        update = {"key1": "value", "key2": None, "key3": "", "key4": []}
        result = _clean_dict_list_update(update)
        assert result == {"key1": "value"}

    def test_merge_identified_dict_lists(self) -> None:  # noqa: PLR6301, RUF105
        """Test _merge_identified_dict_lists."""
        base = [{"deviceSn": "p1", "soc": 50}]
        updates = [{"deviceSn": "p1", "soc": 60}]
        result = _merge_identified_dict_lists(base, updates)
        assert result[0]["soc"] == 60

    def test_merge_present_dict_values(self) -> None:  # noqa: PLR6301, RUF105
        """Test merge_present_dict_values."""
        base = {"key": "value"}
        updates = {"key": None}
        result = merge_present_dict_values(base, updates)
        assert result["key"] == "value"

    def test_merge_missing_dict_values(self) -> None:  # noqa: PLR6301, RUF105
        """Test merge_missing_dict_values."""
        base = {"device": {"soc": 50}}
        updates = {"device": {"temp": 25}}
        result = merge_missing_dict_values(base, updates)
        assert result["device"]["soc"] == 50
        assert result["device"]["temp"] == 25

    def test_find_dict_with_any_key(self) -> None:  # noqa: PLR6301, RUF105
        """Test find_dict_with_any_key."""
        result = find_dict_with_any_key(None, {"deviceSn"})
        assert result is None

    def test_find_list_for_key(self) -> None:  # noqa: PLR6301, RUF105
        """Test find_list_for_key."""
        result = find_list_for_key(None, "batteryPacks")
        assert result is None

    def test_normalize_live_property_payload(self) -> None:  # noqa: PLR6301, RUF105
        """Test normalize_live_property_payload."""
        source = {"soc": 50, "temp": 25}
        result = normalize_live_property_payload(source)
        assert result == source
        assert result is not source

    def test_normalized_company_id(self) -> None:  # noqa: PLR6301, RUF105
        """Test normalized_company_id."""
        result = normalized_company_id("123")
        assert result == 123

    def test_normalized_region(self) -> None:  # noqa: PLR6301, RUF105
        """Test normalized_region."""
        result = normalized_region("  de  ")
        assert result == "DE"

    def test_source_regions(self) -> None:  # noqa: PLR6301, RUF105
        """Test source_regions."""
        source = {FIELD_SYSTEM_REGION: "DE"}
        result = source_regions(source)
        assert result == ["DE"]

    def test_normalized_source_regions(self) -> None:  # noqa: PLR6301, RUF105
        """Test normalized_source_regions."""
        source = {FIELD_SYSTEM_REGION: "de, DE, fr"}
        result = normalized_source_regions(source)
        assert "DE" in result
        assert "FR" in result

    def test_first_nonblank_source_name(self) -> None:  # noqa: PLR6301, RUF105
        """Test first_nonblank_source_name."""
        source = {"key1": "", "key2": "value"}
        result = first_nonblank_source_name(source, "key1", "key2")
        assert result == "value"

    def test_valid_price_sources(self) -> None:  # noqa: PLR6301, RUF105
        """Test valid_price_sources."""
        result = valid_price_sources("string")
        assert result == []

    def test_is_alarm_message(self) -> None:  # noqa: PLR6301, RUF105
        """Test is_alarm_message."""
        assert is_alarm_message(None, None, {}) is False

    def test_is_third_party_mqtt_config_message(self) -> None:  # noqa: PLR6301, RUF105
        """Test is_third_party_mqtt_config_message."""
        assert is_third_party_mqtt_config_message(None, None, {}) is False

    def test_is_wifi_config_message(self) -> None:  # noqa: PLR6301, RUF105
        """Test is_wifi_config_message."""
        assert is_wifi_config_message(None, None, {}) is False

    def test_is_wifi_list_message(self) -> None:  # noqa: PLR6301, RUF105
        """Test is_wifi_list_message."""
        assert is_wifi_list_message(None, {}) is False

    def test_is_time_zone_config_message(self) -> None:  # noqa: PLR6301, RUF105
        """Test is_time_zone_config_message."""
        assert is_time_zone_config_message(None, {}) is False

    def test_is_grid_standard_sync_message(self) -> None:  # noqa: PLR6301, RUF105
        """Test is_grid_standard_sync_message."""
        assert is_grid_standard_sync_message(None, {}) is False

    def test_is_mqtt_connect_info_message(self) -> None:  # noqa: PLR6301, RUF105
        """Test is_mqtt_connect_info_message."""
        assert is_mqtt_connect_info_message(None, {}) is False

    def test_is_device_ota_version_message(self) -> None:  # noqa: PLR6301, RUF105
        """Test is_device_ota_version_message."""
        assert is_device_ota_version_message(None, {}) is False

    def test_is_subdevice_payload(self) -> None:  # noqa: PLR6301, RUF105
        """Test is_subdevice_payload."""
        assert (
            is_subdevice_payload({}, {}, frozenset(), frozenset(), frozenset()) is False
        )

    def test_normalize_battery_pack_payload(self) -> None:  # noqa: PLR6301, RUF105
        """Test normalize_battery_pack_payload."""
        item = {"deviceSn": "pack-1", "updates": {"soc": 50}}
        result = normalize_battery_pack_payload(item)
        assert result["deviceSn"] == "pack-1"
        assert result["soc"] == 50

    def test_looks_like_battery_pack(self) -> None:  # noqa: PLR6301, RUF105
        """Test looks_like_battery_pack."""
        from custom_components.jackery_solarvault.const import (  # noqa: PLC0415, RUF105
            BATTERY_PACK_HINT_KEYS,
            CT_METER_KEYS,
        )

        item = {FIELD_BAT_SOC: 50, FIELD_SUB_TYPE: SUBDEVICE_DEV_TYPE_BATTERY_PACK}
        result = looks_like_battery_pack(item, CT_METER_KEYS, BATTERY_PACK_HINT_KEYS)
        assert isinstance(result, bool)

    def test_battery_packs_from_source(self) -> None:  # noqa: PLR6301, RUF105
        """Test battery_packs_from_source."""
        from custom_components.jackery_solarvault.const import (  # noqa: PLC0415, RUF105
            BATTERY_PACK_HINT_KEYS,
            CT_METER_KEYS,
            FIELD_BATTERY_PACKS,
        )

        source = {FIELD_BATTERY_PACKS: [{FIELD_BAT_SOC: 50}]}
        result = battery_packs_from_source(
            source, CT_METER_KEYS, BATTERY_PACK_HINT_KEYS
        )
        assert result is not None
        assert len(result) == 1

    def test_shelly_cloud_api_device_id(self) -> None:  # noqa: PLR6301, RUF105
        """Test shelly_cloud_api_device_id."""
        # Item needs to match Shelly Cloud criteria (scan_name starts with "shelly" or is_cloud)  # noqa: E501, RUF105
        item = {"scanName": "shelly_plug", "deviceId": "5c:cf:7f:12:34:56"}
        result = shelly_cloud_api_device_id(item)
        assert result == "5c:cf:7f:12:34:56"

    def test_shelly_cloud_api_device_id_returns_none_for_non_shelly(self) -> None:  # noqa: PLR6301, RUF105
        """Test shelly_cloud_api_device_id returns None for non-Shelly items."""
        item = {"scanName": "jackery_device", "deviceId": "123"}
        result = shelly_cloud_api_device_id(item)
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
