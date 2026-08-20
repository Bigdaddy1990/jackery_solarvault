"""Tests for uncovered paths in __init__.py to increase coverage."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.jackery_solarvault import (
    async_migrate_entry,
    async_remove_config_entry_device,
    async_setup,
    async_setup_entry,
    async_unload_entry,
)


class TestInitModule:
    """Test __init__.py module functions."""

    def _create_hass(self):
        """Create a mock hass."""
        hass = MagicMock()
        hass.data = {}
        hass.config_entries = MagicMock()
        hass.config = MagicMock()
        hass.config.config_dir = "/config"
        hass.config.path = MagicMock(return_value="/config")
        hass.states = MagicMock()
        return hass

    def _create_config_entry(self, data=None, options=None):
        """Create a mock config entry."""
        entry = MagicMock()
        entry.entry_id = "test_entry"
        entry.data = data or {}
        entry.options = options or {}
        entry.version = 1
        entry.minor_version = 0
        return entry

    def _create_coordinator(self):
        """Create a mock coordinator."""
        coordinator = AsyncMock()
        coordinator.async_setup = AsyncMock()
        coordinator.async_shutdown = AsyncMock()
        coordinator.async_load_cached_discovery = AsyncMock(return_value=False)
        coordinator.async_load_local_daily_snapshots = AsyncMock()
        coordinator.cached_discovery_snapshot = MagicMock(return_value=None)
        coordinator.api = MagicMock()
        coordinator.api.mqtt_session_snapshot = MagicMock(return_value=None)
        coordinator.api.hydrate_mqtt_session = MagicMock()
        coordinator.async_persist_http_mqtt_session = AsyncMock()
        coordinator.async_discover = AsyncMock()
        coordinator.async_config_entry_first_refresh = AsyncMock()
        coordinator.data = {}
        coordinator.async_set_scan_interval = MagicMock()
        coordinator.async_start_statistics_imports = MagicMock()
        coordinator.async_update_listeners = MagicMock()
        coordinator.has_pending_supplemental_transport_cleanup = False
        coordinator.set_local_mqtt_config_observer = MagicMock()
        coordinator.async_stop_supplemental_transports = AsyncMock()
        coordinator.local_mqtt_client = None
        coordinator.set_local_mqtt_client = MagicMock()
        coordinator.async_handle_local_mqtt_message = AsyncMock()
        coordinator.async_schedule_local_mqtt_device_config = MagicMock()
        coordinator.async_start_mqtt = AsyncMock()
        coordinator.async_start_ble_transport = AsyncMock()
        coordinator.async_reconcile_ble_transport = AsyncMock()
        coordinator.async_query_weather_plan = AsyncMock()
        coordinator.async_read_device_schedule = AsyncMock()
        coordinator.async_reboot_device = AsyncMock()
        coordinator.async_delete_storm_alert = AsyncMock()
        coordinator.async_send_portable_command = AsyncMock()
        coordinator.async_query_system_info = AsyncMock()
        coordinator.async_query_device_info = AsyncMock()
        coordinator.async_query_wifi_list = AsyncMock()
        coordinator.async_get_time_zone = AsyncMock()
        coordinator.async_send_time_zone = AsyncMock()
        coordinator.async_sync_mqtt_connect_info = AsyncMock()
        coordinator.async_query_device_ota_version = AsyncMock()
        coordinator.async_query_third_party_mqtt_config = AsyncMock()
        coordinator.async_query_wifi_config = AsyncMock()
        coordinator.async_query_battery_packs = AsyncMock()
        coordinator.async_query_smart_meter = AsyncMock()
        coordinator.async_query_meter_heads = AsyncMock()
        coordinator.async_query_smart_plugs = AsyncMock()
        coordinator.async_query_subdevice_combo = AsyncMock()
        coordinator.device_supports_advanced = MagicMock(return_value=False)
        coordinator.battery_pack_observed_serial = MagicMock(return_value=None)
        coordinator.battery_pack_identity_serial = MagicMock(return_value=None)
        coordinator.set_battery_pack_identity_override = MagicMock()
        coordinator._defer_background_auth_failure = MagicMock()
        coordinator.mark_mqtt_session_cache_loaded = MagicMock()
        return coordinator

    @pytest.mark.asyncio
    async def test_async_setup(self) -> None:
        """Test async_setup function."""
        hass = self._create_hass()
        with patch("custom_components.jackery_solarvault.async_setup_services"):
            result = await async_setup(hass, {})
            assert result is True

    @pytest.mark.asyncio
    async def test_async_setup_entry_success(self) -> None:
        """Test async_setup_entry success."""
        hass = self._create_hass()
        config_entry = self._create_config_entry(
            data={
                "username": "test",
                "password": "test",
            }
        )

        coordinator = self._create_coordinator()

        with patch(
            "custom_components.jackery_solarvault.JackerySolarVaultCoordinator",
            return_value=coordinator,
        ):
            with patch("custom_components.jackery_solarvault.async_get_clientsession"):
                with patch(
                    "custom_components.jackery_solarvault._async_release_fenced_coordinator",
                    return_value=True,
                ):
                    with patch(
                        "custom_components.jackery_solarvault._async_migrate_legacy_local_mqtt_options"
                    ):
                        with patch(
                            "custom_components.jackery_solarvault._async_prune_removed_local_mqtt_tls_options"
                        ):
                            with patch(
                                "custom_components.jackery_solarvault._cancel_layer5_start_task"
                            ):
                                with patch(
                                    "custom_components.jackery_solarvault._async_load_entry_caches",
                                    return_value=False,
                                ):
                                    with patch(
                                        "custom_components.jackery_solarvault._async_prepare_primary_http"
                                    ):
                                        with patch(
                                            "custom_components.jackery_solarvault._async_clean_legacy_entities"
                                        ):
                                            with patch(
                                                "custom_components.jackery_solarvault._async_remove_legacy_system_parent_devices"
                                            ):
                                                with patch.object(
                                                    hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock
                                                ):
                                                    with patch(
                                                        "custom_components.jackery_solarvault._schedule_layer5_start_if_ready"
                                                    ):
                                                        with patch(
                                                            "custom_components.jackery_solarvault._entry_runtime_bucket"
                                                        ):
                                                            result = (
                                                                await async_setup_entry(
                                                                    hass, config_entry
                                                                )
                                                            )
                                                            assert result is True

    @pytest.mark.asyncio
    async def test_async_unload_entry(self) -> None:
        """Test async_unload_entry."""
        hass = self._create_hass()
        config_entry = self._create_config_entry()

        coordinator = self._create_coordinator()

        # Set up coordinator in hass.data
        hass.data["jackery_solarvault"] = {config_entry.entry_id: coordinator}

        with patch(
            "custom_components.jackery_solarvault._entry_runtime_bucket",
            return_value={},
        ):
            with patch(
                "custom_components.jackery_solarvault._entry_runtime_task",
                return_value=None,
            ):
                with patch(
                    "custom_components.jackery_solarvault._local_mqtt_client",
                    return_value=None,
                ):
                    with patch(
                        "custom_components.jackery_solarvault._async_stop_local_mqtt_client"
                    ):
                        with patch.object(
                            hass.config_entries, "async_unload_platforms", new_callable=AsyncMock, return_value=True
                        ):
                            with patch(
                                "custom_components.jackery_solarvault._async_shutdown_coordinator_bounded",
                                return_value=True,
                            ):
                                result = await async_unload_entry(hass, config_entry)
                                assert result is True

    def test_async_migrate_entry(self) -> None:
        """Test async_migrate_entry."""
        hass = self._create_hass()
        config_entry = self._create_config_entry()
        config_entry.version = 1
        config_entry.minor_version = 0

        result = async_migrate_entry(hass, config_entry)
        assert result is True

    @pytest.mark.asyncio
    async def test_async_remove_config_entry_device(self) -> None:
        """Test async_remove_config_entry_device."""
        hass = self._create_hass()
        config_entry = self._create_config_entry()
        device = MagicMock()

        # Should return True (device can be removed)
        result = await async_remove_config_entry_device(hass, config_entry, device)
        assert result is True


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
