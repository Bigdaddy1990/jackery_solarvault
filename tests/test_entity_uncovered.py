"""Tests for uncovered paths in entity.py to increase coverage."""

from unittest.mock import MagicMock

import pytest

from custom_components.jackery_solarvault.entity import JackeryEntity
from homeassistant.helpers.entity import EntityDescription


class TestJackeryEntity:
    """Test JackeryEntity class."""

    def _create_coordinator(self, data=None):  # noqa: PLR6301
        """Create a mock coordinator."""
        coordinator = MagicMock()
        coordinator.data = data or {}
        coordinator.config_entry = MagicMock()
        coordinator.config_entry.entry_id = "test_entry"
        coordinator.config_entry.runtime_data = MagicMock()
        coordinator.is_device_reachable = MagicMock(return_value=True)
        coordinator.is_entity_source_available = MagicMock(return_value=True)
        return coordinator

    def _create_entity(self, coordinator, key_suffix="test_key"):  # noqa: PLR6301
        """Create an entity instance for testing."""
        # Use a simple EntityDescription for testing
        description = EntityDescription(key=key_suffix, name="Test Entity")
        entity = JackeryEntity(
            coordinator=coordinator, device_id="test_device", key_suffix=key_suffix
        )
        entity.entity_description = description
        return entity

    def test_creation(self) -> None:
        """Test entity creation."""
        coordinator = self._create_coordinator()
        entity = self._create_entity(coordinator)
        assert entity is not None
        assert entity._device_id == "test_device"

    def test_unique_id(self) -> None:
        """Test unique_id property."""
        coordinator = self._create_coordinator()
        entity = self._create_entity(coordinator, "test_key")
        assert entity.unique_id == "test_device_test_key"

    def test_device_info(self) -> None:
        """Test device_info property."""
        coordinator = self._create_coordinator({
            "test_device": {
                "system": {"deviceName": "Test Device"},
                "discovery": {},
                "device": {},
                "ota": {"currentVersion": "1.0.0"},
            }
        })
        entity = self._create_entity(coordinator)
        device_info = entity.device_info
        assert device_info is not None
        assert "identifiers" in device_info
        assert "manufacturer" in device_info
        assert "model" in device_info

    def test_available_property(self) -> None:
        """Test available property."""
        coordinator = self._create_coordinator({"test_device": {}})
        entity = self._create_entity(coordinator)
        assert entity.available is True

    def test_available_property_no_data(self) -> None:
        """Test available property when no data."""
        coordinator = self._create_coordinator({})
        entity = self._create_entity(coordinator)
        # Should not be available if device not in coordinator data
        assert entity.available is False

    def test_payload_property(self) -> None:
        """Test _payload property."""
        coordinator = self._create_coordinator({
            "test_device": {"properties": {"test": "value"}}
        })
        entity = self._create_entity(coordinator)
        payload = entity._payload
        assert payload == {"properties": {"test": "value"}}

    def test_properties_property(self) -> None:
        """Test _properties property."""
        coordinator = self._create_coordinator({
            "test_device": {"properties": {"test": "value"}}
        })
        entity = self._create_entity(coordinator)
        props = entity._properties
        assert props == {"test": "value"}

    def test_device_meta_property(self) -> None:
        """Test _device_meta property."""
        coordinator = self._create_coordinator({
            "test_device": {"device": {"model": "Test Model"}}
        })
        entity = self._create_entity(coordinator)
        meta = entity._device_meta
        assert meta == {"model": "Test Model"}

    def test_discovery_property(self) -> None:
        """Test _discovery property."""
        coordinator = self._create_coordinator({
            "test_device": {"discovery": {"name": "Test"}}
        })
        entity = self._create_entity(coordinator)
        disc = entity._discovery
        assert disc == {"name": "Test"}

    def test_system_property(self) -> None:
        """Test _system property."""
        coordinator = self._create_coordinator({
            "test_device": {"system": {"online": True}}
        })
        entity = self._create_entity(coordinator)
        sys = entity._system
        assert sys == {"online": True}

    def test_online_marker_available(self) -> None:
        """Test _online_marker_available method."""
        coordinator = self._create_coordinator({
            "test_device": {"device": {"onlineStatus": 1}}
        })
        entity = self._create_entity(coordinator)
        result = entity._online_marker_available(False)
        assert result is True

    def test_source_capability_contract(self) -> None:
        """Test _source_capability_contract method."""
        coordinator = self._create_coordinator({"test_device": {}})
        entity = self._create_entity(coordinator)
        supported, data_sources, command_sources, _fields, _supervisor_only = (
            entity._source_capability_contract()
        )
        assert supported is True
        assert isinstance(data_sources, tuple)
        assert isinstance(command_sources, tuple)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
