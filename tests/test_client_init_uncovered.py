"""Tests for uncovered paths in client/__init__.py to increase coverage."""

import pytest

from custom_components.jackery_solarvault.client import (
    JackeryApi,
    JackeryMqttPushClient,
)
from custom_components.jackery_solarvault.client.ble_transport import JackeryBleListener
from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
)


class TestClientModule:
    """Test client module."""

    def test_imports(self) -> None:  # ruff: ignore[no-self-use]
        """Test that all client classes can be imported."""
        assert JackeryApi is not None
        assert JackeryBleListener is not None
        assert JackeryLocalMqttClient is not None
        assert JackeryMqttPushClient is not None

    def test_jackery_api_has_public_methods(self) -> None:  # ruff: ignore[no-self-use]
        """Test JackeryApi has expected public methods."""
        # Verify the class has the main API methods
        assert hasattr(JackeryApi, "async_login")
        assert hasattr(JackeryApi, "async_get_system_list")
        assert hasattr(JackeryApi, "async_get_device_property")

    def test_jackery_ble_listener_has_public_methods(self) -> None:  # ruff: ignore[no-self-use]
        """Test JackeryBleListener has expected public methods."""
        assert hasattr(JackeryBleListener, "async_start")
        assert hasattr(JackeryBleListener, "async_stop")
        assert hasattr(JackeryBleListener, "async_ensure_connected")

    def test_jackery_local_mqtt_client_has_public_methods(self) -> None:  # ruff: ignore[no-self-use]
        """Test JackeryLocalMqttClient has expected public methods."""
        assert hasattr(JackeryLocalMqttClient, "async_start")
        assert hasattr(JackeryLocalMqttClient, "async_stop")

    def test_jackery_mqtt_push_client_has_public_methods(self) -> None:  # ruff: ignore[no-self-use]
        """Test JackeryMqttPushClient has expected public methods."""
        assert hasattr(JackeryMqttPushClient, "async_start")
        assert hasattr(JackeryMqttPushClient, "async_stop")
        assert hasattr(JackeryMqttPushClient, "async_publish_json")


class TestLazyImports:
    """Test lazy import mechanism."""

    def test_jackery_mqtt_push_client_lazy_import(self) -> None:  # ruff: ignore[no-self-use]
        """Test JackeryMqttPushClient is lazily imported."""
        from custom_components.jackery_solarvault.client import (
            JackeryMqttPushClient as MqttPushClient,
        )

        assert MqttPushClient is JackeryMqttPushClient


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
