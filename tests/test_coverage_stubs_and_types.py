"""Test imports of client cache modules and DTO types."""

import custom_components.jackery_solarvault.client.api as client_api
import custom_components.jackery_solarvault.client.discovery_cache as client_dc
import custom_components.jackery_solarvault.client.local_daily_cache as client_ldc
import custom_components.jackery_solarvault.client.mqtt_session_cache as client_msc
import custom_components.jackery_solarvault.models as root_models


def test_client_modules_export_supported_surfaces() -> None:
    """Verify client cache modules expose expected functions and types."""
    assert client_api.JackeryApi is not None
    assert client_dc.async_load_discovery_cache is not None
    assert client_ldc.async_load_daily_cache is not None
    assert client_msc.async_load_mqtt_session is not None
    assert root_models.ApiBaseResponse is not None
