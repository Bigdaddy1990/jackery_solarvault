"""Test imports of client cache modules and DTO types."""

import custom_components.jackery_solarvault.client.api as root_api
import custom_components.jackery_solarvault.client.discovery_cache as client_dc
import custom_components.jackery_solarvault.client.local_daily_cache as client_ldc
import custom_components.jackery_solarvault.client.mqtt_session_cache as client_msc
import custom_components.jackery_solarvault.types as root_types


def test_root_facade_re_exports() -> None:
    """Verify client cache modules expose expected functions and types."""
    assert root_api.JackeryApi is not None
    assert client_dc.async_load_discovery_cache is not None
    assert client_ldc.async_load_daily_cache is not None
    assert client_msc.async_load_mqtt_session is not None
    assert root_types.ApiBaseResponse is not None
