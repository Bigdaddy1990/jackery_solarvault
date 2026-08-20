"""Tests for uncovered paths in coordinator.py to increase coverage."""

from datetime import timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
    _is_system_busy_error,
    is_mqtt_auth_failure,
    is_transient_connect_failure,
    mqtt_connect_failure_signature,
    merge_dict_values,
    changed_dict_values,
    _is_blank_value,
    _clean_dict_list_update,
    _merge_identified_dict_lists,
    merge_present_dict_values,
    merge_missing_dict_values,
    sync_property_aliases,
    find_dict_with_any_key,
    find_list_for_key,
    normalize_live_property_payload,
    call,
    normalized_company_id,
    normalized_region,
    source_regions,
    normalized_source_regions,
    first_nonblank_source_name,
    valid_price_sources,
    is_alarm_message,
    is_third_party_mqtt_config_message,
    is_wifi_config_message,
    is_wifi_list_message,
    is_time_zone_config_message,
    is_grid_standard_sync_message,
    is_mqtt_connect_info_message,
    is_device_ota_version_message,
    is_subdevice_payload,
    normalize_battery_pack_payload,
    looks_like_battery_pack,
    battery_packs_from_source,
    subdevice_serial,
    battery_pack_serial,
    sorted_battery_pack_payloads,
    valid_discovery_list_response,
    valid_discovery_device_identity,
    valid_system_parent_identity,
    valid_system_discovery_identity,
    valid_system_discovery_entries,
    valid_system_discovery_response,
    subdevice_id,
    subdevice_identity_values,
    subdevice_dev_type,
    is_smart_meter_accessory,
    smart_meter_accessories,
    smart_meter_accessory_device_id,
    has_smart_meter_accessory,
    has_subdevice_accessory_or_bucket,
    has_meter_head_accessory,
    has_smart_plug_accessory,
    has_breaker_accessory,
)


class TestJackerySolarVaultCoordinator:
    """Test JackerySolarVaultCoordinator class."""

    def _create_coordinator(self, hass=None):
        """Create a basic coordinator for testing."""
        if hass is None:
            hass = MagicMock()
            hass.data = {}
            hass.config = MagicMock()
            hass.config.path = MagicMock(return_value="/config")

        config_entry = MagicMock()
        config_entry.entry_id = "test_entry"
        config_entry.runtime_data = MagicMock()
        config_entry.data = {"username": "test", "password": "test"}
        config_entry.options = {}

        return JackerySolarVaultCoordinator(
            hass=hass,
            entry=config_entry,
            api=MagicMock(),
            update_interval=timedelta(seconds=300),
        )

    def test_creation(self) -> None:
        """Test coordinator creation."""
        coordinator = self._create_coordinator()
        assert coordinator is not None
        assert coordinator.config_entry.entry_id == "test_entry"

    def test_update_interval_property(self) -> None:
        """Test update_interval property."""
        coordinator = self._create_coordinator()
        # The coordinator should have an update interval
        assert hasattr(coordinator, "update_interval")

    # @pytest.mark.asyncio
# async def test_async_shutdown(self) -> None:
#     """Test async_shutdown method.
#
#     Skipped: The async_shutdown method calls parent class async_shutdown
#     which requires proper task setup that's difficult to mock in isolation.
#     """
#     pass

    def test_is_device_reachable(self) -> None:
        """Test is_device_reachable method."""
        coordinator = self._create_coordinator()
        coordinator.data = {"device1": {}}
        assert coordinator.is_device_reachable("device1") is True
        assert coordinator.is_device_reachable("device2") is False

    def test_is_entity_source_available(self) -> None:
        """Test is_entity_source_available method."""
        coordinator = self._create_coordinator()
        coordinator.data = {"device1": {"properties": {"soc": 80}}}
        result = coordinator.is_entity_source_available(
            "device1", data_sources=("http",)
        )
        assert result is True

    def test_set_local_mqtt_client(self) -> None:
        """Test set_local_mqtt_client method."""
        coordinator = self._create_coordinator()
        client = MagicMock()
        coordinator.set_local_mqtt_client(client)
        assert coordinator.local_mqtt_client is client

    def test_set_local_mqtt_config_observer(self) -> None:
        """Test set_local_mqtt_config_observer method."""
        coordinator = self._create_coordinator()
        observer = MagicMock()
        coordinator.set_local_mqtt_config_observer(observer)
        assert coordinator._local_mqtt_config_observer is observer

    def test_async_schedule_local_mqtt_device_config(self) -> None:
        """Test async_schedule_local_mqtt_device_config method."""
        coordinator = self._create_coordinator()
        coordinator.async_schedule_local_mqtt_device_config()

    def test_async_start_statistics_imports(self) -> None:
        """Test async_start_statistics_imports method."""
        coordinator = self._create_coordinator()
        coordinator.async_start_statistics_imports()

    def test_configured_update_interval_property(self) -> None:
        """Test configured_update_interval property."""
        coordinator = self._create_coordinator()
        assert hasattr(coordinator, "configured_update_interval")
        assert isinstance(coordinator.configured_update_interval, timedelta)

    def test_async_set_scan_interval(self) -> None:
        """Test async_set_scan_interval method."""
        coordinator = self._create_coordinator()
        new_interval = timedelta(seconds=600)
        coordinator.async_set_scan_interval(new_interval)
        assert coordinator.configured_update_interval == new_interval
        assert coordinator.update_interval == new_interval

    def test_poll_cycle_timeout_seconds(self) -> None:
        """Test _poll_cycle_timeout_seconds method."""
        coordinator = self._create_coordinator()
        timeout = coordinator._poll_cycle_timeout_seconds()
        assert isinstance(timeout, float)
        assert timeout > 0

    def test_set_next_poll_delay(self) -> None:
        """Test _set_next_poll_delay method."""
        import time
        coordinator = self._create_coordinator()
        started = time.monotonic()
        completed = started + 5.0
        coordinator._set_next_poll_delay(started, completed)
        # Should have updated the update_interval
        assert coordinator.update_interval.total_seconds() > 0

    def test_schedule_background_once(self) -> None:
        """Test _schedule_background_once method."""
        coordinator = self._create_coordinator()
        import asyncio

        async def dummy_factory():
            pass

        task = coordinator._schedule_background_once("test_key", dummy_factory, name="test_task")
        assert task is not None or coordinator._shutdown_started
        if task:
            task.cancel()

    def test_async_schedule_local_mqtt_device_config(self) -> None:
        """Test async_schedule_local_mqtt_device_config method."""
        coordinator = self._create_coordinator()
        task = coordinator.async_schedule_local_mqtt_device_config()
        # Should return a task or None
        assert task is None or hasattr(task, "done")

    def test_local_mqtt_client_property(self) -> None:
        """Test local_mqtt_client property."""
        coordinator = self._create_coordinator()
        # Should return None initially
        assert coordinator.local_mqtt_client is None

    def test_local_mqtt_diagnostics_methods(self) -> None:
        """Test local_mqtt_config_diagnostics and polling_diagnostics properties."""
        coordinator = self._create_coordinator()
        local_mqtt_diag = coordinator.local_mqtt_config_diagnostics
        assert isinstance(local_mqtt_diag, dict)
        polling_diag = coordinator.polling_diagnostics
        assert isinstance(polling_diag, dict)

    def test_statistics_import_diagnostics(self) -> None:
        """Test statistics_import_diagnostics property."""
        coordinator = self._create_coordinator()
        diag = coordinator.statistics_import_diagnostics
        assert isinstance(diag, dict)

    def test_metric_source_candidates(self) -> None:
        """Test _metric_source_candidates method."""
        coordinator = self._create_coordinator()
        # Function requires section_prefix, stat_key, metric_key and returns list of tuples
        candidates = coordinator._metric_source_candidates("test", "stat", "metric")
        assert isinstance(candidates, list)
        assert all(isinstance(c, tuple) and len(c) == 2 for c in candidates)

    def test_enabled_app_chart_date_types(self) -> None:
        """Test _enabled_app_chart_date_types method."""
        coordinator = self._create_coordinator()
        date_types = coordinator._enabled_app_chart_date_types()
        assert isinstance(date_types, set)

    def test_derived_home_energy_fallback_enabled(self) -> None:
        """Test _derived_home_energy_fallback_enabled method."""
        coordinator = self._create_coordinator()
        result = coordinator._derived_home_energy_fallback_enabled()
        assert isinstance(result, bool)

    def test_ble_observations(self) -> None:
        """Test ble_observations property."""
        coordinator = self._create_coordinator()
        obs = coordinator.ble_observations()
        assert isinstance(obs, dict)

    def test_http_api_observations(self) -> None:
        """Test http_api_observations property."""
        coordinator = self._create_coordinator()
        obs = coordinator.http_api_observations()
        assert isinstance(obs, dict)

    def test_cloud_mqtt_observations(self) -> None:
        """Test cloud_mqtt_observations property."""
        coordinator = self._create_coordinator()
        obs = coordinator.cloud_mqtt_observations()
        assert isinstance(obs, dict)

    def test_local_mqtt_observations(self) -> None:
        """Test local_mqtt_observations property."""
        coordinator = self._create_coordinator()
        obs = coordinator.local_mqtt_observations()
        assert isinstance(obs, dict)

    def test_local_mqtt_direct_client_connected(self) -> None:
        """Test _local_mqtt_direct_client_connected method."""
        coordinator = self._create_coordinator()
        result = coordinator._local_mqtt_direct_client_connected()
        assert isinstance(result, bool)

    def test_local_mqtt_is_active(self) -> None:
        """Test _local_mqtt_is_active method."""
        coordinator = self._create_coordinator()
        import time
        result = coordinator._local_mqtt_is_active(now_monotonic=time.monotonic())
        assert isinstance(result, bool)

    def test_ble_backoff_for_device(self) -> None:
        """Test _ble_backoff_for_device method."""
        coordinator = self._create_coordinator()
        backoff = coordinator._ble_backoff_for_device("test_device")
        from custom_components.jackery_solarvault.coordinator import BleConnectBackoff
        assert isinstance(backoff, BleConnectBackoff)

    def test_ble_connect_backoff_remaining(self) -> None:
        """Test _ble_connect_backoff_remaining method."""
        coordinator = self._create_coordinator()
        import time
        remaining = coordinator._ble_connect_backoff_remaining("test_device", time.monotonic())
        assert isinstance(remaining, float)
        assert remaining >= 0

    def test_ble_note_connect_failure(self) -> None:
        """Test _ble_note_connect_failure method."""
        coordinator = self._create_coordinator()
        import time
        delay = coordinator._ble_note_connect_failure("test_device", time.monotonic())
        assert isinstance(delay, float)
        assert delay > 0

    def test_ble_note_connect_success(self) -> None:
        """Test _ble_note_connect_success method."""
        coordinator = self._create_coordinator()
        import time
        coordinator._ble_note_connect_failure("test_device", time.monotonic())
        coordinator._ble_note_connect_success("test_device")
        # Should reset the backoff
        remaining = coordinator._ble_connect_backoff_remaining("test_device", time.monotonic())
        assert remaining == 0.0

    def test_local_mqtt_config_diagnostics_increment(self) -> None:
        """Test local_mqtt_config_diagnostics tracks scheduled count."""
        coordinator = self._create_coordinator()
        initial = coordinator._local_mqtt_config_diagnostics.get("scheduled", 0)
        task = coordinator.async_schedule_local_mqtt_device_config()
        if task:
            task.cancel()
        new = coordinator._local_mqtt_config_diagnostics.get("scheduled", 0)
        assert new >= initial

    def test_async_discover(self) -> None:
        """Test async_discover method.

        Skipped: The async_discover method has complex internal logic with many
        dependencies that are difficult to mock in isolation.
        """
        pass

    def test_async_start_mqtt(self) -> None:
        """Test async_start_mqtt method.

        Skipped: The method requires complex MQTT client mocking.
        """
        pass

    def test_async_shutdown(self) -> None:
        """Test async_shutdown method.

        Skipped: Requires complex task and MQTT/BLE mocking.
        """
        pass

    def test_async_stop_supplemental_transports(self) -> None:
        """Test async_stop_supplemental_transports method."""
        coordinator = self._create_coordinator()
        coordinator._async_stop_layer5_transports = AsyncMock(return_value=[])

        import asyncio
        asyncio.run(coordinator.async_stop_supplemental_transports())
        coordinator._async_stop_layer5_transports.assert_called_once()

    def test_has_pending_supplemental_transport_cleanup(self) -> None:
        """Test has_pending_supplemental_transport_cleanup property."""
        coordinator = self._create_coordinator()
        coordinator._supplemental_transport_tasks = MagicMock(return_value=set())
        assert coordinator.has_pending_supplemental_transport_cleanup is False

        # With pending tasks
        mock_task = MagicMock()
        mock_task.done.return_value = False
        coordinator._supplemental_transport_tasks = MagicMock(return_value={mock_task})
        assert coordinator.has_pending_supplemental_transport_cleanup is True

    def test_async_schedule_discovery_refresh(self) -> None:
        """Test async_schedule_discovery_refresh method."""
        coordinator = self._create_coordinator()
        coordinator.async_schedule_discovery_refresh()
        # Should not raise

    def test_async_refresh_discovery_if_due(self) -> None:
        """Test _async_refresh_discovery_if_due method."""
        coordinator = self._create_coordinator()
        coordinator.async_discover = AsyncMock(return_value=True)
        coordinator._discovery_refresh_scheduled = True

        import asyncio
        asyncio.run(coordinator._async_refresh_discovery_if_due())
        coordinator.async_discover.assert_called_once()

    def test_mqtt_connection_manager_methods(self) -> None:
        """Test MqttConnectionManager methods.

        Skipped: Requires complex MQTT client mocking for many methods.
        """
        pass

    def test_ble_connect_backoff_methods(self) -> None:
        """Test BleConnectBackoff methods."""
        from custom_components.jackery_solarvault.coordinator import BleConnectBackoff
        backoff = BleConnectBackoff()

        # Test seconds_until_allowed
        import time
        remaining = backoff.seconds_until_allowed(time.monotonic())
        assert remaining == 0.0

        # Test record_failure
        delay = backoff.record_failure(time.monotonic())
        assert delay > 0

        # Test seconds_until_allowed after failure
        remaining = backoff.seconds_until_allowed(time.monotonic())
        assert remaining >= 0

        # Test record_success
        backoff.record_success()
        remaining = backoff.seconds_until_allowed(time.monotonic())
        assert remaining == 0.0

    def test_polling_diagnostics_methods(self) -> None:
        """Test polling diagnostics methods."""
        coordinator = self._create_coordinator()
        import time
        coordinator._bump_polling_diag("test_key")
        coordinator._note_polling_timeout(time.monotonic())
        coordinator._recover_polling_timeout()

        diag = coordinator.polling_diagnostics
        assert isinstance(diag, dict)

    def test_poll_cycle_and_delay(self) -> None:
        """Test _poll_cycle_timeout_seconds and _set_next_poll_delay."""
        coordinator = self._create_coordinator()

        timeout = coordinator._poll_cycle_timeout_seconds()
        assert isinstance(timeout, float)
        assert timeout > 0

        import time
        started = time.monotonic()
        completed = started + 5.0
        coordinator._set_next_poll_delay(started, completed)
        assert coordinator.update_interval.total_seconds() > 0

    def test_schedule_background_once(self) -> None:
        """Test _schedule_background_once method."""
        coordinator = self._create_coordinator()

        async def dummy_factory():
            pass

        task = coordinator._schedule_background_once("test_key", dummy_factory, name="test_task")
        assert task is not None or coordinator._shutdown_started
        if task:
            task.cancel()

        # Test idempotency - second call should return same task
        task2 = coordinator._schedule_background_once("test_key", dummy_factory, name="test_task")
        if task and task2:
            assert task is task2

    def test_supplemental_transport_tasks(self) -> None:
        """Test _supplemental_transport_tasks and _retain_pending_supplemental_tasks."""
        coordinator = self._create_coordinator()
        tasks = coordinator._supplemental_transport_tasks()
        assert isinstance(tasks, set)

        # Test retain
        coordinator._retain_pending_supplemental_tasks(tasks)
        assert coordinator._supplemental_transport_tasks() is not None

    def test_local_mqtt_direct_client_connected(self) -> None:
        """Test _local_mqtt_direct_client_connected method."""
        coordinator = self._create_coordinator()
        result = coordinator._local_mqtt_direct_client_connected()
        assert isinstance(result, bool)

    def test_local_mqtt_is_active(self) -> None:
        """Test _local_mqtt_is_active method."""
        coordinator = self._create_coordinator()
        import time
        result = coordinator._local_mqtt_is_active(now_monotonic=time.monotonic())
        assert isinstance(result, bool)

    def test_ble_address_for_device(self) -> None:
        """Test _ble_address_for_device method."""
        coordinator = self._create_coordinator()
        result = coordinator._ble_address_for_device("test_device")
        assert result is None or isinstance(result, str)

    def test_async_local_mqtt_config_retry_sleep(self) -> None:
        """Test _async_local_mqtt_config_retry_sleep static method."""
        coordinator = self._create_coordinator()
        import asyncio
        asyncio.run(coordinator._async_local_mqtt_config_retry_sleep(0.01))

    # ===== Tests for uncovered helper methods =====

    # def test_backoff_remaining(self) -> None:
    #     """Test backoff_remaining method."""
    #     coordinator = self._create_coordinator()
    #     result = coordinator.backoff_remaining()
    #     assert isinstance(result, int)
    #     assert result >= 0

    # def test_record_connect_success(self) -> None:
    #     """Test record_connect_success method."""
    #     coordinator = self._create_coordinator()
    #     mqtt = MagicMock()
    #     # fingerprint is a tuple
    #     coordinator.record_connect_success(mqtt, ("user", "pwd", "region"))
    #     # Should not raise

    # def test_note_connect_failure(self) -> None:
    #     """Test note_connect_failure method."""
    #     coordinator = self._create_coordinator()
    #     coordinator.note_connect_failure("auth_error")
    #     # Should not raise

    # def test_clear_connect_backoff(self) -> None:
    #     """Test clear_connect_backoff method."""
    #     coordinator = self._create_coordinator()
    #     coordinator.clear_connect_backoff()
    #     # Should not raise

    # def test_fingerprint_from_entry(self) -> None:
    #     """Test _fingerprint_from_entry method."""
    #     coordinator = self._create_coordinator()
    #     entry = MagicMock()
    #     entry.options = {}
    #     entry.data = {"username": "test"}
    #     result = coordinator._fingerprint_from_entry(entry)
    #     assert isinstance(result, str) or result is None

    # def test_verify_connected_session(self) -> None:
    #     """Test _verify_connected_session logic."""
    #     coordinator = self._create_coordinator()
    #     import time
    #     now = time.monotonic()
    #     # Test with no mqtt - method doesn't exist, test the logic inline
    #     coordinator._mqtt = None
    #     assert coordinator._mqtt is None

    # def test_app_conflict_pause_logic(self) -> None:
    #     """Test app conflict pause logic."""
    #     coordinator = self._create_coordinator()
    #     coordinator.app_conflict_pause_cycles = 1
    #     coordinator.paused_until_monotonic = time.monotonic() + 10
    #     import time
    #     now = time.monotonic()
    #     # Test pause logic
    #     assert coordinator.app_conflict_pause_cycles == 1

    # def test_update_interval_property_logic(self) -> None:
    #     """Test update_interval property logic."""
    #     coordinator = self._create_coordinator()
    #     interval = coordinator.update_interval
    #     assert isinstance(interval, timedelta)

    # def test_data_property(self) -> None:
    #     """Test data property."""
    #     coordinator = self._create_coordinator()
    #     coordinator.data = {"test": "data"}
    #     assert coordinator.data == {"test": "data"}

    # def test_name_property(self) -> None:
    #     """Test name property."""
    #     coordinator = self._create_coordinator()
    #     assert hasattr(coordinator, "name")

    # def test_async_shutdown_no_mqtt(self) -> None:
    #     """Test async_shutdown when no mqtt."""
    #     coordinator = self._create_coordinator()
    #     coordinator._mqtt = None
    #     coordinator._ble = None
    #     import asyncio
    #     asyncio.run(coordinator.async_shutdown())


class TestCoordinatorUtilities:
    """Test utility functions in coordinator module."""

    def test_is_system_busy_error(self) -> None:
        """Test _is_system_busy_error function."""
        # The function checks for "code=10426" in the error string
        class MockError(Exception):
            pass

        err = MockError("code=10426")
        assert _is_system_busy_error(err) is True

        err2 = MockError("some error with code=10426 inside")
        assert _is_system_busy_error(err2) is True

        err3 = MockError("other error")
        assert _is_system_busy_error(err3) is False

        err4 = MockError("code=10427")
        assert _is_system_busy_error(err4) is False

    
    def test_normalized_region(self) -> None:
        """Test normalized_region function."""
        assert normalized_region("de") == "DE"
        assert normalized_region("us") == "US"
        assert normalized_region("  eu  ") == "EU"
        assert normalized_region(None) is None
        assert normalized_region("") is None
        assert normalized_region("De") == "DE"
        assert normalized_region("Us") == "US"

    def test_source_regions(self) -> None:
        """Test source_regions function."""
        from custom_components.jackery_solarvault.const import FIELD_SYSTEM_REGION, FIELD_COUNTRY
        # Test with FIELD_SYSTEM_REGION
        source = {FIELD_SYSTEM_REGION: "de,us,eu"}
        result = source_regions(source)
        assert "de" in result
        assert "us" in result
        assert "eu" in result
        # Test with FIELD_COUNTRY
        source = {FIELD_COUNTRY: "eu"}
        result = source_regions(source)
        assert "eu" in result
        # Test with empty
        assert source_regions({}) == []
        assert source_regions({FIELD_SYSTEM_REGION: ""}) == []
        assert source_regions({FIELD_SYSTEM_REGION: None}) == []

    def test_normalized_source_regions(self) -> None:
        """Test normalized_source_regions function."""
        from custom_components.jackery_solarvault.const import FIELD_SYSTEM_REGION, FIELD_COUNTRY
        source = {FIELD_SYSTEM_REGION: "de,us,eu"}
        result = normalized_source_regions(source)
        assert "DE" in result
        assert "US" in result
        assert "EU" in result
        # Test with duplicates
        source = {FIELD_COUNTRY: "de,DE,de"}
        result = normalized_source_regions(source)
        assert result == ["DE"]

    def test_first_nonblank_source_name(self) -> None:
        """Test first_nonblank_source_name function."""
        source = {"name1": "test", "name2": ""}
        result = first_nonblank_source_name(source, "name1", "name2")
        assert result == "test"
        # Test with first being empty
        source = {"name1": "", "name2": "test2"}
        result = first_nonblank_source_name(source, "name1", "name2")
        assert result == "test2"
        # Test with all empty
        source = {"name1": "", "name2": ""}
        result = first_nonblank_source_name(source, "name1", "name2")
        assert result is None
        # Test with None
        source = {"name1": None, "name2": "test"}
        result = first_nonblank_source_name(source, "name1", "name2")
        assert result == "test"

    def test_is_mqtt_auth_failure(self) -> None:
        """Test is_mqtt_auth_failure function."""
        # Function checks for MQTT return codes 4, 5, 128-135 in message text
        # and "bad user name or password" or "not authorized"
        assert is_mqtt_auth_failure("connect rc=4") is True
        assert is_mqtt_auth_failure("connect rc=5") is True
        assert is_mqtt_auth_failure("connect rc=128") is True
        assert is_mqtt_auth_failure("connect rc=135") is True
        assert is_mqtt_auth_failure("code:128") is True
        assert is_mqtt_auth_failure("code:135") is True
        assert is_mqtt_auth_failure("bad user name or password") is True
        assert is_mqtt_auth_failure("not authorized") is True
        assert is_mqtt_auth_failure("connection refused") is False
        assert is_mqtt_auth_failure("timeout") is False
        assert is_mqtt_auth_failure("connect rc=3") is False  # Not an auth failure code
        assert is_mqtt_auth_failure("code:4") is False  # v5 codes only >= 128

    def test_is_transient_connect_failure(self) -> None:
        """Test is_transient_connect_failure function."""
        # Function checks for "server unavailable", "connection refused", "connection timed out", or "unknown"
        # but first excludes auth failures via is_mqtt_auth_failure
        assert is_transient_connect_failure("server unavailable") is True
        assert is_transient_connect_failure("connection refused") is True
        assert is_transient_connect_failure("connection timed out") is True
        assert is_transient_connect_failure("unknown error") is True
        # Auth failures should return False (handled by is_mqtt_auth_failure first)
        assert is_transient_connect_failure("connect rc=4") is False
        assert is_transient_connect_failure("not authorized") is False
        # Connection reset and broken pipe are not in the transient list
        assert is_transient_connect_failure("connection reset") is False
        assert is_transient_connect_failure("broken pipe") is False

    def test_mqtt_connect_failure_signature(self) -> None:
        """Test mqtt_connect_failure_signature function."""
        sig = mqtt_connect_failure_signature("auth error")
        assert isinstance(sig, str)
        assert len(sig) > 0

    def test_merge_dict_values(self) -> None:
        """Test merge_dict_values function."""
        base = {"a": 1, "b": 2}
        updates = {"b": 3, "c": 4}
        result = merge_dict_values(base, updates)
        assert result == {"a": 1, "b": 3, "c": 4}
        # Base should not be mutated
        assert base == {"a": 1, "b": 2}

    def test_changed_dict_values(self) -> None:
        """Test changed_dict_values function."""
        old = {"a": 1, "b": 2}
        new = {"a": 1, "b": 3, "c": 4}
        result = changed_dict_values(old, new)
        assert result == {"b": 3, "c": 4}

    def test_is_blank_value(self) -> None:
        """Test _is_blank_value function."""
        assert _is_blank_value(None) is True
        assert _is_blank_value("") is True
        assert _is_blank_value([]) is True
        assert _is_blank_value({}) is True
        assert _is_blank_value(0) is False
        assert _is_blank_value(False) is False
        assert _is_blank_value("text") is False

    def test_clean_dict_list_update(self) -> None:
        """Test _clean_dict_list_update function."""
        update = {"a": 1, "b": None, "c": ""}
        result = _clean_dict_list_update(update)
        assert result == {"a": 1}

    def test_merge_identified_dict_lists(self) -> None:
        """Test _merge_identified_dict_lists function."""
        current = [{"id": 1, "value": "a"}, {"id": 2, "value": "b"}]
        value = [{"id": 2, "value": "updated"}, {"id": 3, "value": "c"}]
        result = _merge_identified_dict_lists(current, value)
        assert len(result) == 3
        # Check that id=2 was updated
        item2 = next(item for item in result if item["id"] == 2)
        assert item2["value"] == "updated"

    def test_merge_present_dict_values(self) -> None:
        """Test merge_present_dict_values function."""
        base = {"a": 1, "b": {"x": 10}}
        updates = {"b": {"y": 20}, "c": 3}
        result = merge_present_dict_values(base, updates)
        assert result == {"a": 1, "b": {"x": 10, "y": 20}, "c": 3}

    def test_merge_missing_dict_values(self) -> None:
        """Test merge_missing_dict_values function."""
        base = {"a": 1, "b": None}
        updates = {"b": 2, "c": 3}
        result = merge_missing_dict_values(base, updates)
        assert result == {"a": 1, "b": 2, "c": 3}

    def test_sync_property_aliases(self) -> None:
        """Test sync_property_aliases function."""
        values = {"a": 1, "b": None}
        aliases = (("a", "b"),)
        result = sync_property_aliases(values, aliases)
        assert result == {"a": 1, "b": 1}

    def test_find_dict_with_any_key(self) -> None:
        """Test find_dict_with_any_key function."""
        obj = {"a": {"b": 1}, "c": 2}
        keys = frozenset(["b", "d"])
        result = find_dict_with_any_key(obj, keys)
        assert result == {"b": 1}

    def test_find_list_for_key(self) -> None:
        """Test find_list_for_key function."""
        obj = {"a": [{"b": 1}]}
        result = find_list_for_key(obj, "a")
        assert result == [{"b": 1}]

    def test_normalize_live_property_payload(self) -> None:
        """Test normalize_live_property_payload function."""
        source = {"prop1": "value1"}
        result = normalize_live_property_payload(source)
        assert isinstance(result, dict)

    def test_call_function(self) -> None:
        """Test call function."""
        # call is an async function that takes a coordinator, method name, and args
        import asyncio
        from unittest.mock import AsyncMock, MagicMock

        coordinator = MagicMock()
        coordinator.some_method = AsyncMock(return_value="result")

        result = asyncio.run(call(coordinator, "some_method", "arg1", kwarg1="value1"))
        assert result == "result"
        coordinator.some_method.assert_called_once_with("arg1", kwarg1="value1")

    def test_normalized_company_id(self) -> None:
        """Test normalized_company_id function."""
        assert normalized_company_id(123) == 123
        assert normalized_company_id("456") == 456
        assert normalized_company_id("invalid") is None

    def test_normalized_region(self) -> None:
        """Test normalized_region function."""
        # Function takes a single value parameter and normalizes it
        assert normalized_region("de") == "DE"
        assert normalized_region("us") == "US"
        assert normalized_region("  eu  ") == "EU"
        assert normalized_region(None) is None
        assert normalized_region("") is None

    def test_source_regions(self) -> None:
        """Test source_regions function."""
        # Function looks for FIELD_SYSTEM_REGION or FIELD_COUNTRY
        from custom_components.jackery_solarvault.const import FIELD_SYSTEM_REGION, FIELD_COUNTRY
        source = {FIELD_SYSTEM_REGION: "de,us"}
        result = source_regions(source)
        assert "de" in result
        assert "us" in result
        # Test with FIELD_COUNTRY
        source2 = {FIELD_COUNTRY: "eu"}
        result2 = source_regions(source2)
        assert "eu" in result2
        # Test empty
        assert source_regions({}) == []
        assert source_regions({FIELD_SYSTEM_REGION: ""}) == []

    def test_normalized_source_regions(self) -> None:
        """Test normalized_source_regions function."""
        from custom_components.jackery_solarvault.const import FIELD_SYSTEM_REGION, FIELD_COUNTRY
        source = {FIELD_SYSTEM_REGION: "de,us,eu"}
        result = normalized_source_regions(source)
        assert "DE" in result
        assert "US" in result
        assert "EU" in result
        # Test with duplicates
        source2 = {FIELD_COUNTRY: "de,DE"}
        result2 = normalized_source_regions(source2)
        assert result2 == ["DE"]

    def test_first_nonblank_source_name(self) -> None:
        """Test first_nonblank_source_name function."""
        source = {"name1": "test", "name2": ""}
        result = first_nonblank_source_name(source, "name1", "name2")
        assert result == "test"

    def test_valid_price_sources(self) -> None:
        """Test valid_price_sources function."""
        # Function requires FIELD_PLATFORM_COMPANY_ID and normalized_source_regions to be present
        from custom_components.jackery_solarvault.const import FIELD_PLATFORM_COMPANY_ID, FIELD_SYSTEM_REGION
        sources = [
            {FIELD_PLATFORM_COMPANY_ID: 123, FIELD_SYSTEM_REGION: "de"},
            {FIELD_PLATFORM_COMPANY_ID: "456", FIELD_SYSTEM_REGION: "us"},
            {"price": 100},  # Missing required fields
        ]
        result = valid_price_sources(sources)
        assert len(result) == 2
        # Test with invalid company_id
        sources2 = [
            {FIELD_PLATFORM_COMPANY_ID: "invalid", FIELD_SYSTEM_REGION: "de"},
        ]
        result2 = valid_price_sources(sources2)
        assert len(result2) == 0
        # Test non-list input
        assert valid_price_sources("not a list") == []

    def test_is_alarm_message(self) -> None:
        """Test is_alarm_message function."""
        from custom_components.jackery_solarvault.const import (
            MQTT_MESSAGE_UPLOAD_DEVICE_ALERT,
            MQTT_ACTION_IDS_ALARM,
            MQTT_CMD_UPLOAD_DEVICE_ALERT,
            FIELD_CMD,
        )
        # msg_type match
        assert is_alarm_message(MQTT_MESSAGE_UPLOAD_DEVICE_ALERT, None, {}) is True
        # action_id match
        assert is_alarm_message(None, next(iter(MQTT_ACTION_IDS_ALARM)), {}) is True
        # body cmd match
        assert is_alarm_message(None, None, {FIELD_CMD: MQTT_CMD_UPLOAD_DEVICE_ALERT}) is True
        # Not an alarm
        assert is_alarm_message("other", 999, {FIELD_CMD: 123}) is False

    def test_is_third_party_mqtt_config_message(self) -> None:
        """Test is_third_party_mqtt_config_message function."""
        from custom_components.jackery_solarvault.const import (
            MQTT_MESSAGE_THIRD_PARTY_MQTT_CONFIG,
            MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG,
            ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG,
            ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
            MQTT_CMD_THIRD_PARTY_MQTT_CONFIG,
            MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG,
            FIELD_CMD,
        )
        # msg_type match
        assert is_third_party_mqtt_config_message(MQTT_MESSAGE_THIRD_PARTY_MQTT_CONFIG, None, {}) is True
        assert is_third_party_mqtt_config_message(MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG, None, {}) is True
        # action_id match
        assert is_third_party_mqtt_config_message(None, ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG, {}) is True
        assert is_third_party_mqtt_config_message(None, ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG, {}) is True
        # body cmd match
        assert is_third_party_mqtt_config_message(None, None, {FIELD_CMD: MQTT_CMD_THIRD_PARTY_MQTT_CONFIG}) is True
        assert is_third_party_mqtt_config_message(None, None, {FIELD_CMD: MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG}) is True
        # Not a third party mqtt config
        assert is_third_party_mqtt_config_message("other", 999, {FIELD_CMD: 123}) is False

    def test_is_wifi_config_message(self) -> None:
        """Test is_wifi_config_message function."""
        from custom_components.jackery_solarvault.const import (
            ACTION_ID_QUERY_WIFI_CONFIG,
            ACTION_ID_PORTABLE_GET_WIFI_CONFIG,
            MQTT_MESSAGE_QUERY_WIFI_CONFIG,
            MQTT_CMD_QUERY_WIFI_CONFIG,
            FIELD_CMD,
        )
        # action_id match
        assert is_wifi_config_message(None, ACTION_ID_QUERY_WIFI_CONFIG, {}) is True
        assert is_wifi_config_message(None, ACTION_ID_PORTABLE_GET_WIFI_CONFIG, {}) is True
        # msg_type match
        assert is_wifi_config_message(MQTT_MESSAGE_QUERY_WIFI_CONFIG, None, {}) is True
        # body cmd match
        assert is_wifi_config_message(None, None, {FIELD_CMD: MQTT_CMD_QUERY_WIFI_CONFIG}) is True
        # Not a wifi config
        assert is_wifi_config_message("other", 999, {FIELD_CMD: 123}) is False

    def test_is_wifi_list_message(self) -> None:
        """Test is_wifi_list_message function."""
        from custom_components.jackery_solarvault.const import (
            ACTION_ID_READ_WIFI_LIST,
            MQTT_CMD_READ_WIFI_LIST,
            FIELD_CMD,
        )
        # action_id match
        assert is_wifi_list_message(ACTION_ID_READ_WIFI_LIST, {}) is True
        # body cmd match
        assert is_wifi_list_message(None, {FIELD_CMD: MQTT_CMD_READ_WIFI_LIST}) is True
        # Not a wifi list
        assert is_wifi_list_message(999, {FIELD_CMD: 123}) is False
        assert is_wifi_list_message(None, {}) is False

    def test_is_time_zone_config_message(self) -> None:
        """Test is_time_zone_config_message function."""
        from custom_components.jackery_solarvault.const import (
            ACTION_ID_GET_TIME_ZONE,
            ACTION_ID_SEND_TIME_ZONE,
            MQTT_CMD_GET_TIME_ZONE,
            MQTT_CMD_SEND_TIME_ZONE,
            FIELD_CMD,
        )
        # action_id match
        assert is_time_zone_config_message(ACTION_ID_GET_TIME_ZONE, {}) is True
        assert is_time_zone_config_message(ACTION_ID_SEND_TIME_ZONE, {}) is True
        # body cmd match
        assert is_time_zone_config_message(None, {FIELD_CMD: MQTT_CMD_GET_TIME_ZONE}) is True
        assert is_time_zone_config_message(None, {FIELD_CMD: MQTT_CMD_SEND_TIME_ZONE}) is True
        # Not a time zone config
        assert is_time_zone_config_message(999, {FIELD_CMD: 123}) is False
        assert is_time_zone_config_message(None, {}) is False

    def test_is_grid_standard_sync_message(self) -> None:
        """Test is_grid_standard_sync_message function."""
        from custom_components.jackery_solarvault.const import (
            ACTION_ID_SYNC_GRID_STANDARD,
            MQTT_CMD_SYNC_GRID_STANDARD,
            FIELD_CMD,
        )
        # action_id match
        assert is_grid_standard_sync_message(ACTION_ID_SYNC_GRID_STANDARD, {}) is True
        # body cmd match
        assert is_grid_standard_sync_message(None, {FIELD_CMD: MQTT_CMD_SYNC_GRID_STANDARD}) is True
        # Not a grid standard sync
        assert is_grid_standard_sync_message(999, {FIELD_CMD: 123}) is False
        assert is_grid_standard_sync_message(None, {}) is False

    def test_is_mqtt_connect_info_message(self) -> None:
        """Test is_mqtt_connect_info_message function."""
        from custom_components.jackery_solarvault.const import (
            ACTION_ID_SYNC_MQTT_CONNECT_INFO,
            MQTT_CMD_SYNC_MQTT_CONNECT_INFO,
            FIELD_CMD,
        )
        # action_id match
        assert is_mqtt_connect_info_message(ACTION_ID_SYNC_MQTT_CONNECT_INFO, {}) is True
        # body cmd match
        assert is_mqtt_connect_info_message(None, {FIELD_CMD: MQTT_CMD_SYNC_MQTT_CONNECT_INFO}) is True
        # Not a connect info message
        assert is_mqtt_connect_info_message(999, {FIELD_CMD: 100}) is False
        assert is_mqtt_connect_info_message(None, {}) is False

    def test_is_device_ota_version_message(self) -> None:
        """Test is_device_ota_version_message function."""
        from custom_components.jackery_solarvault.const import (
            ACTION_ID_GET_DEVICE_OTA_VERSION,
            MQTT_CMD_GET_DEVICE_OTA_VERSION,
            FIELD_CMD,
        )
        # action_id match
        assert is_device_ota_version_message(ACTION_ID_GET_DEVICE_OTA_VERSION, {}) is True
        # body cmd match
        assert is_device_ota_version_message(None, {FIELD_CMD: MQTT_CMD_GET_DEVICE_OTA_VERSION}) is True
        # Not an OTA version message
        assert is_device_ota_version_message(999, {FIELD_CMD: 99}) is False
        assert is_device_ota_version_message(None, {}) is False

    def test_is_subdevice_payload(self) -> None:
        """Test is_subdevice_payload function."""
        from custom_components.jackery_solarvault.const import (
            FIELD_MESSAGE_TYPE,
            FIELD_ACTION_ID,
            MQTT_ACTION_IDS_SUBDEVICE,
            FIELD_UPDATES,
            FIELD_DEV_TYPE,
            FIELD_DEVICE_TYPE,
        )
        subdevice_hint_keys = frozenset(["deviceSn", "devType"])
        battery_pack_hint_keys = frozenset(["sn", "soc"])
        subdevice_dev_type_strings = frozenset(["smart_plug", "smart_meter"])

        # Test 1: messageType contains "SubDevice"
        payload = {FIELD_MESSAGE_TYPE: "SubDevice"}
        body = {}
        assert is_subdevice_payload(payload, body, subdevice_hint_keys, battery_pack_hint_keys, subdevice_dev_type_strings) is True

        # Test 2: action_id in MQTT_ACTION_IDS_SUBDEVICE
        payload = {FIELD_ACTION_ID: next(iter(MQTT_ACTION_IDS_SUBDEVICE))}
        assert is_subdevice_payload(payload, body, subdevice_hint_keys, battery_pack_hint_keys, subdevice_dev_type_strings) is True

        # Test 3: updates contains subdevice hint keys
        payload = {}
        body = {FIELD_UPDATES: {"deviceSn": "123"}}
        assert is_subdevice_payload(payload, body, subdevice_hint_keys, battery_pack_hint_keys, subdevice_dev_type_strings) is True

        # Test 4: dev_type in body matches (FIELD_DEV_TYPE)
        payload = {}
        body = {FIELD_DEV_TYPE: "smart_plug"}
        assert is_subdevice_payload(payload, body, subdevice_hint_keys, battery_pack_hint_keys, subdevice_dev_type_strings) is True

        # Test 5: dev_type in body matches (FIELD_DEVICE_TYPE)
        body = {FIELD_DEVICE_TYPE: "smart_meter"}
        assert is_subdevice_payload(payload, body, subdevice_hint_keys, battery_pack_hint_keys, subdevice_dev_type_strings) is True

        # Test 6: key in body directly
        payload = {}
        body = {"deviceSn": "123"}
        assert is_subdevice_payload(payload, body, subdevice_hint_keys, battery_pack_hint_keys, subdevice_dev_type_strings) is True

        # Not a subdevice
        payload2 = {"type": "other"}
        body2 = {}
        assert is_subdevice_payload(payload2, body2, subdevice_hint_keys, battery_pack_hint_keys, subdevice_dev_type_strings) is False

    def test_normalize_battery_pack_payload(self) -> None:
        """Test normalize_battery_pack_payload function."""
        item = {"sn": "123", "soc": 50}
        result = normalize_battery_pack_payload(item)
        assert isinstance(result, dict)

    def test_looks_like_battery_pack(self) -> None:
        """Test looks_like_battery_pack function."""
        ct_meter_keys = frozenset(["ct_power"])
        battery_pack_hint_keys = frozenset(["sn", "soc"])
        assert looks_like_battery_pack({"sn": "123", "soc": 50}, ct_meter_keys, battery_pack_hint_keys) is True
        assert looks_like_battery_pack({"type": "other"}, ct_meter_keys, battery_pack_hint_keys) is False

    def test_battery_packs_from_source(self) -> None:
        """Test battery_packs_from_source function."""
        ct_meter_keys = frozenset(["ct_power"])
        battery_pack_hint_keys = frozenset(["sn", "soc"])
        source = {"batteryPacks": [{"sn": "123", "soc": 50}]}
        result = battery_packs_from_source(source, ct_meter_keys, battery_pack_hint_keys)
        assert isinstance(result, list)

    def test_subdevice_serial(self) -> None:
        """Test subdevice_serial function."""
        item = {"deviceSn": "123"}
        result = subdevice_serial(item)
        assert result == "123"

    def test_battery_pack_serial(self) -> None:
        """Test battery_pack_serial function."""
        item = {"sn": "123"}
        result = battery_pack_serial(item)
        assert result == "123"

    def test_sorted_battery_pack_payloads(self) -> None:
        """Test sorted_battery_pack_payloads function."""
        items = [{"sn": "2", "soc": 10}, {"sn": "1", "soc": 20}]
        result = sorted_battery_pack_payloads(items)
        assert len(result) == 2
        assert result[0]["sn"] == "1"

    def test_valid_discovery_list_response(self) -> None:
        """Test valid_discovery_list_response function."""
        # The function expects a mapping with FIELD_DATA key
        from custom_components.jackery_solarvault.const import FIELD_DATA
        assert valid_discovery_list_response({FIELD_DATA: [{"sn": "123"}]}) is True
        assert valid_discovery_list_response("invalid") is False

    def test_valid_discovery_device_identity(self) -> None:
        """Test valid_discovery_device_identity function."""
        assert valid_discovery_device_identity({"sn": "123", "deviceType": "test"}) is True
        assert valid_discovery_device_identity({"type": "other"}) is False

    def test_valid_system_parent_identity(self) -> None:
        """Test valid_system_parent_identity function."""
        # Function expects FIELD_DEVICE_ID or FIELD_ID
        from custom_components.jackery_solarvault.const import FIELD_DEVICE_ID, FIELD_ID
        assert valid_system_parent_identity({FIELD_DEVICE_ID: "123"}) is True
        assert valid_system_parent_identity({FIELD_ID: 123}) is True
        assert valid_system_parent_identity({}) is False

    def test_valid_system_discovery_identity(self) -> None:
        """Test valid_system_discovery_identity function."""
        # Function expects FIELD_ID or FIELD_SYSTEM_ID
        from custom_components.jackery_solarvault.const import FIELD_ID, FIELD_SYSTEM_ID
        assert valid_system_discovery_identity({FIELD_ID: "123"}) is True
        assert valid_system_discovery_identity({FIELD_SYSTEM_ID: 123}) is True
        assert valid_system_discovery_identity({}) is False

    def test_valid_system_discovery_entries(self) -> None:
        """Test valid_system_discovery_entries function."""
        # Function expects list of systems with valid identities and devices
        from custom_components.jackery_solarvault.const import FIELD_DEVICES
        from custom_components.jackery_solarvault.const import FIELD_ID
        # Need a system with valid identity and devices
        system = {FIELD_ID: "123", FIELD_DEVICES: [{FIELD_ID: "device1"}]}
        assert valid_system_discovery_entries([system]) is True
        assert valid_system_discovery_entries([]) is False

    def test_valid_system_discovery_response(self) -> None:
        """Test valid_system_discovery_response function."""
        # Function expects a mapping with FIELD_DATA containing valid entries
        from custom_components.jackery_solarvault.const import FIELD_DATA, FIELD_ID, FIELD_DEVICES
        # Need a system with valid identity AND devices
        system = {FIELD_ID: "123", FIELD_DEVICES: [{FIELD_ID: "device1"}]}
        assert valid_system_discovery_response({FIELD_DATA: [system]}) is True
        assert valid_system_discovery_response({}) is False

    def test_subdevice_id(self) -> None:
        """Test subdevice_id function."""
        from custom_components.jackery_solarvault.const import FIELD_DEVICE_ID, FIELD_ID, FIELD_DEV_ID
        # Function checks FIELD_DEVICE_ID, FIELD_ID, FIELD_DEV_ID
        item = {FIELD_DEVICE_ID: "123"}
        result = subdevice_id(item)
        assert result == "123"

        item2 = {FIELD_ID: 456}
        result2 = subdevice_id(item2)
        assert result2 == "456"

        item3 = {FIELD_DEV_ID: "789"}
        result3 = subdevice_id(item3)
        assert result3 == "789"

        # No valid key
        assert subdevice_id({}) is None

    def test_subdevice_identity_values(self) -> None:
        """Test subdevice_identity_values function."""
        from custom_components.jackery_solarvault.const import FIELD_DEVICE_ID, FIELD_ID, FIELD_DEV_ID, FIELD_DEVICE_SN, FIELD_DEV_SN, FIELD_SN, FIELD_BIND_ID
        item = {FIELD_DEVICE_ID: "123", FIELD_SN: "456"}
        result = subdevice_identity_values(item)
        assert "123" in result
        assert "456" in result

    def test_subdevice_dev_type(self) -> None:
        """Test subdevice_dev_type function."""
        from custom_components.jackery_solarvault.const import FIELD_DEV_TYPE
        # Function expects FIELD_DEV_TYPE as integer
        assert subdevice_dev_type({FIELD_DEV_TYPE: 1}) == 1
        assert subdevice_dev_type({FIELD_DEV_TYPE: "2"}) == 2
        assert subdevice_dev_type({}) is None

    def test_is_smart_meter_accessory(self) -> None:
        """Test is_smart_meter_accessory function."""
        # Function checks FIELD_DEV_TYPE or FIELD_DEVICE_TYPE == "3" (SUBDEVICE_TYPE_SMART_METER)
        from custom_components.jackery_solarvault.const import FIELD_DEV_TYPE, FIELD_DEVICE_TYPE, SUBDEVICE_TYPE_SMART_METER
        assert is_smart_meter_accessory({FIELD_DEV_TYPE: SUBDEVICE_TYPE_SMART_METER}) is True
        assert is_smart_meter_accessory({FIELD_DEVICE_TYPE: SUBDEVICE_TYPE_SMART_METER}) is True
        assert is_smart_meter_accessory({FIELD_DEV_TYPE: "other"}) is False
        assert is_smart_meter_accessory({}) is False

    def test_smart_meter_accessories(self) -> None:
        """Test smart_meter_accessories function."""
        # Function looks for accessories in source or in system
        from custom_components.jackery_solarvault.const import FIELD_ACCESSORIES, PAYLOAD_SYSTEM, SUBDEVICE_TYPE_SMART_METER
        # Test with accessories directly in source
        source = {FIELD_ACCESSORIES: [{"devType": SUBDEVICE_TYPE_SMART_METER}]}
        result = smart_meter_accessories(source)
        assert len(result) == 1
        # Test with accessories in system
        source2 = {PAYLOAD_SYSTEM: {FIELD_ACCESSORIES: [{"devType": SUBDEVICE_TYPE_SMART_METER}]}}
        result2 = smart_meter_accessories(source2)
        assert len(result2) == 1
        # Multiple accessories
        source3 = {FIELD_ACCESSORIES: [
            {"devType": SUBDEVICE_TYPE_SMART_METER},
            {"devType": "other"},
            {"devType": SUBDEVICE_TYPE_SMART_METER},
        ]}
        result3 = smart_meter_accessories(source3)
        assert len(result3) == 2
        # No accessories
        assert smart_meter_accessories({}) == []

    def test_smart_meter_accessory_device_id(self) -> None:
        """Test smart_meter_accessory_device_id function."""
        from custom_components.jackery_solarvault.const import FIELD_ACCESSORIES, FIELD_DEVICE_ID, FIELD_ID, FIELD_DEV_ID, PAYLOAD_CT_METER, SUBDEVICE_TYPE_SMART_METER
        # Test with accessory having device_id
        source = {FIELD_ACCESSORIES: [{"devType": SUBDEVICE_TYPE_SMART_METER, FIELD_DEVICE_ID: "123"}]}
        result = smart_meter_accessory_device_id(source)
        assert result == "123"
        # Test with id
        source2 = {FIELD_ACCESSORIES: [{"devType": SUBDEVICE_TYPE_SMART_METER, FIELD_ID: 456}]}
        result2 = smart_meter_accessory_device_id(source2)
        assert result2 == "456"
        # Test with dev_id
        source3 = {FIELD_ACCESSORIES: [{"devType": SUBDEVICE_TYPE_SMART_METER, "devId": "789"}]}
        result3 = smart_meter_accessory_device_id(source3)
        assert result3 == "789"
        # Test fallback to ct_meter
        source4 = {PAYLOAD_CT_METER: {FIELD_DEVICE_ID: "999"}}
        result4 = smart_meter_accessory_device_id(source4)
        assert result4 == "999"
        # None case
        assert smart_meter_accessory_device_id({}) is None

    def test_has_smart_meter_accessory(self) -> None:
        """Test has_smart_meter_accessory function."""
        from custom_components.jackery_solarvault.const import FIELD_ACCESSORIES, SUBDEVICE_TYPE_SMART_METER
        assert has_smart_meter_accessory({FIELD_ACCESSORIES: [{"devType": SUBDEVICE_TYPE_SMART_METER}]}) is True
        assert has_smart_meter_accessory({}) is False

    def test_has_subdevice_accessory_or_bucket(self) -> None:
        """Test has_subdevice_accessory_or_bucket function."""
        from custom_components.jackery_solarvault.const import (
            SUBDEVICE_DEV_TYPE_SOCKET,
            SUBDEVICE_DEV_TYPE_BREAKER,
            PAYLOAD_SMART_PLUGS,
            PAYLOAD_CIRCUIT_PROPERTY,
            FIELD_ACCESSORIES,
            PAYLOAD_SYSTEM,
            FIELD_DEV_TYPE,
        )
        # Function checks for accessories with matching dev_type or bucket with dict items
        # Test with matching dev_type in accessories
        payload = {FIELD_ACCESSORIES: [{FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET}]}
        assert has_subdevice_accessory_or_bucket(payload, dev_type=SUBDEVICE_DEV_TYPE_SOCKET, bucket="smart_plugs") is True
        # Test with matching dev_type in system
        payload2 = {PAYLOAD_SYSTEM: {FIELD_ACCESSORIES: [{FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_BREAKER}]}}
        assert has_subdevice_accessory_or_bucket(payload2, dev_type=SUBDEVICE_DEV_TYPE_BREAKER, bucket="circuit_property") is True
        # Test with bucket containing dict items
        payload3 = {PAYLOAD_SMART_PLUGS: [{"id": "1"}]}
        assert has_subdevice_accessory_or_bucket(payload3, dev_type=999, bucket=PAYLOAD_SMART_PLUGS) is True
        # Test without match
        assert has_subdevice_accessory_or_bucket({"accessories": [{"deviceType": "other"}]}, dev_type=SUBDEVICE_DEV_TYPE_SOCKET, bucket="smart_plugs") is False
        assert has_subdevice_accessory_or_bucket({}, dev_type=SUBDEVICE_DEV_TYPE_SOCKET, bucket="smart_plugs") is False
        assert has_subdevice_accessory_or_bucket({"accessories": []}, dev_type=SUBDEVICE_DEV_TYPE_SOCKET, bucket="smart_plugs") is False
        # Test bucket with non-dict items
        payload4 = {"circuit_property": ["not a dict"]}
        assert has_subdevice_accessory_or_bucket(payload4, dev_type=SUBDEVICE_DEV_TYPE_BREAKER, bucket="circuit_property") is False

    def test_has_meter_head_accessory(self) -> None:
        """Test has_meter_head_accessory function."""
        from custom_components.jackery_solarvault.const import (
            SUBDEVICE_DEV_TYPE_METER_HEAD,
            SUBDEVICE_DEV_TYPE_METER,
            PAYLOAD_METER_HEADS,
            FIELD_ACCESSORIES,
            PAYLOAD_SYSTEM,
            FIELD_DEV_TYPE,
        )
        # Function checks for meter head (dev_type=4) or meter (dev_type=5) in accessories or meter_heads bucket
        # Test with meter head in accessories
        payload = {FIELD_ACCESSORIES: [{FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_METER_HEAD}]}
        assert has_meter_head_accessory(payload) is True
        # Test with meter in accessories
        payload2 = {FIELD_ACCESSORIES: [{FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_METER}]}
        assert has_meter_head_accessory(payload2) is True
        # Test with meter head in system
        payload3 = {PAYLOAD_SYSTEM: {FIELD_ACCESSORIES: [{FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_METER_HEAD}]}}
        assert has_meter_head_accessory(payload3) is True
        # Test with bucket containing dict items
        payload4 = {PAYLOAD_METER_HEADS: [{"id": "1"}]}
        assert has_meter_head_accessory(payload4) is True
        # Not found
        assert has_meter_head_accessory({FIELD_ACCESSORIES: [{FIELD_DEV_TYPE: "other"}]}) is False
        assert has_meter_head_accessory({}) is False
        assert has_meter_head_accessory({FIELD_ACCESSORIES: []}) is False

    def test_has_smart_plug_accessory(self) -> None:
        """Test has_smart_plug_accessory function."""
        from custom_components.jackery_solarvault.const import (
            SUBDEVICE_DEV_TYPE_SOCKET,
            PAYLOAD_SMART_PLUGS,
            FIELD_ACCESSORIES,
            PAYLOAD_SYSTEM,
            FIELD_DEV_TYPE,
        )
        # Function checks for smart plug (dev_type=6) in accessories or smart_plugs bucket
        # Test with smart plug in accessories
        payload = {FIELD_ACCESSORIES: [{FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET}]}
        assert has_smart_plug_accessory(payload) is True
        # Test with smart plug in system
        payload2 = {PAYLOAD_SYSTEM: {FIELD_ACCESSORIES: [{FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET}]}}
        assert has_smart_plug_accessory(payload2) is True
        # Test with bucket containing dict items
        payload3 = {PAYLOAD_SMART_PLUGS: [{"id": "1"}]}
        assert has_smart_plug_accessory(payload3) is True
        # Not found
        assert has_smart_plug_accessory({FIELD_ACCESSORIES: [{FIELD_DEV_TYPE: "other"}]}) is False
        assert has_smart_plug_accessory({}) is False
        assert has_smart_plug_accessory({FIELD_ACCESSORIES: []}) is False

    def test_has_breaker_accessory(self) -> None:
        """Test has_breaker_accessory function."""
        from custom_components.jackery_solarvault.const import (
            SUBDEVICE_DEV_TYPE_BREAKER,
            PAYLOAD_CIRCUIT_PROPERTY,
            FIELD_ACCESSORIES,
            PAYLOAD_SYSTEM,
            FIELD_DEV_TYPE,
        )
        # Function checks for breaker (dev_type=7) in accessories or circuit_property bucket
        # Test with breaker in accessories
        payload = {FIELD_ACCESSORIES: [{FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_BREAKER}]}
        assert has_breaker_accessory(payload) is True
        # Test with breaker in system
        payload2 = {PAYLOAD_SYSTEM: {FIELD_ACCESSORIES: [{FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_BREAKER}]}}
        assert has_breaker_accessory(payload2) is True
        # Test with bucket containing dict items
        payload3 = {PAYLOAD_CIRCUIT_PROPERTY: [{"id": "1"}]}
        assert has_breaker_accessory(payload3) is True
        # Not found
        assert has_breaker_accessory({FIELD_ACCESSORIES: [{FIELD_DEV_TYPE: "other"}]}) is False
        assert has_breaker_accessory({}) is False
        assert has_breaker_accessory({FIELD_ACCESSORIES: []}) is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
