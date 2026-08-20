"""Tests for uncovered paths in discovery_cache.py to increase coverage."""

import pytest
import asyncio
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import UTC, datetime, timedelta
import copy

from custom_components.jackery_solarvault.client.discovery_cache import (
    async_load_discovery_cache,
    async_save_discovery_cache,
)


class TestAsyncLoadDiscoveryCache:
    """Test async_load_discovery_cache function."""

    @pytest.mark.asyncio
    async def test_returns_empty_for_missing_store(self) -> None:
        """Test returns empty dict when store has no data."""
        hass = MagicMock()
        hass.data = {}

        with patch("custom_components.jackery_solarvault.client.discovery_cache._store") as mock_store:
            mock_store_instance = AsyncMock()
            mock_store_instance.async_load = AsyncMock(return_value=None)
            mock_store.return_value = mock_store_instance

            result = await async_load_discovery_cache(hass, "test_entry")
            assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_for_invalid_data(self) -> None:
        """Test returns empty dict when store data is invalid."""
        hass = MagicMock()
        hass.data = {}

        with patch("custom_components.jackery_solarvault.client.discovery_cache._store") as mock_store:
            mock_store_instance = AsyncMock()
            mock_store_instance.async_load = AsyncMock(return_value="not a dict")
            mock_store.return_value = mock_store_instance

            result = await async_load_discovery_cache(hass, "test_entry")
            assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_for_missing_entries(self) -> None:
        """Test returns empty dict when entries key is missing."""
        hass = MagicMock()
        hass.data = {}

        with patch("custom_components.jackery_solarvault.client.discovery_cache._store") as mock_store:
            mock_store_instance = AsyncMock()
            mock_store_instance.async_load = AsyncMock(return_value={"other": "data"})
            mock_store.return_value = mock_store_instance

            result = await async_load_discovery_cache(hass, "test_entry")
            assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_for_missing_entry_id(self) -> None:
        """Test returns empty dict when entry_id is missing from entries."""
        hass = MagicMock()
        hass.data = {}

        with patch("custom_components.jackery_solarvault.client.discovery_cache._store") as mock_store:
            mock_store_instance = AsyncMock()
            mock_store_instance.async_load = AsyncMock(return_value={"entries": {}})
            mock_store.return_value = mock_store_instance

            result = await async_load_discovery_cache(hass, "test_entry")
            assert result == {}

    @pytest.mark.asyncio
    async def test_returns_empty_for_invalid_device_index(self) -> None:
        """Test returns empty dict when device_index is invalid."""
        hass = MagicMock()
        hass.data = {}

        with patch("custom_components.jackery_solarvault.client.discovery_cache._store") as mock_store:
            mock_store_instance = AsyncMock()
            mock_store_instance.async_load = AsyncMock(return_value={"entries": {"test_entry": {}}})
            mock_store.return_value = mock_store_instance

            result = await async_load_discovery_cache(hass, "test_entry")
            assert result == {}

    @pytest.mark.asyncio
    async def test_loads_valid_cache(self) -> None:
        """Test loads valid cache data."""
        hass = MagicMock()
        hass.data = {}

        test_data = {
            "entries": {
                "test_entry": {
                    "device_index": {
                        "device1": {"ip": "192.168.1.1", "name": "Device 1"},
                        "device2": {"ip": "192.168.1.2", "name": "Device 2"},
                    }
                }
            }
        }

        with patch("custom_components.jackery_solarvault.client.discovery_cache._store") as mock_store:
            mock_store_instance = AsyncMock()
            mock_store_instance.async_load = AsyncMock(return_value=test_data)
            mock_store.return_value = mock_store_instance

            result = await async_load_discovery_cache(hass, "test_entry")
            assert len(result) == 2
            assert "device1" in result
            assert "device2" in result
            assert result["device1"]["ip"] == "192.168.1.1"
            assert result["device2"]["ip"] == "192.168.1.2"

    @pytest.mark.asyncio
    async def test_normalizes_device_ids(self) -> None:
        """Test normalizes device IDs to strings."""
        hass = MagicMock()
        hass.data = {}

        test_data = {
            "entries": {
                "test_entry": {
                    "device_index": {
                        123: {"ip": "192.168.1.1"},
                        "456": {"ip": "192.168.1.2"},
                    }
                }
            }
        }

        with patch("custom_components.jackery_solarvault.client.discovery_cache._store") as mock_store:
            mock_store_instance = AsyncMock()
            mock_store_instance.async_load = AsyncMock(return_value=test_data)
            mock_store.return_value = mock_store_instance

            result = await async_load_discovery_cache(hass, "test_entry")
            assert "123" in result
            assert "456" in result

    @pytest.mark.asyncio
    async def test_filters_empty_values(self) -> None:
        """Test filters out empty device metadata."""
        hass = MagicMock()
        hass.data = {}

        test_data = {
            "entries": {
                "test_entry": {
                    "device_index": {
                        "device1": {"ip": "192.168.1.1"},
                        "device2": {},
                        "device3": None,
                    }
                }
            }
        }

        with patch("custom_components.jackery_solarvault.client.discovery_cache._store") as mock_store:
            mock_store_instance = AsyncMock()
            mock_store_instance.async_load = AsyncMock(return_value=test_data)
            mock_store.return_value = mock_store_instance

            result = await async_load_discovery_cache(hass, "test_entry")
            assert "device1" in result
            assert "device2" not in result
            assert "device3" not in result


class TestAsyncSaveDiscoveryCache:
    """Test async_save_discovery_cache function."""

    @pytest.mark.asyncio
    async def test_saves_cache(self) -> None:
        """Test saves cache data to store."""
        hass = MagicMock()
        hass.data = {}

        # Track the created task so we can await it
        created_tasks = []

        async def run_coro(coro, name=None, eager_start=False):
            task = asyncio.create_task(coro)
            created_tasks.append(task)
            return task
        hass.async_create_task = MagicMock(side_effect=run_coro)

        device_index = {
            "device1": {"ip": "192.168.1.1", "name": "Device 1"},
            "device2": {"ip": "192.168.1.2", "name": "Device 2"},
        }

        with patch("custom_components.jackery_solarvault.client.discovery_cache._store") as mock_store:
            with patch("custom_components.jackery_solarvault.client.discovery_cache.asyncio.shield", new=lambda x: x):
                mock_store_instance = AsyncMock()
                mock_store_instance.async_load = AsyncMock(return_value={})
                mock_store_instance.async_save = AsyncMock()
                mock_store.return_value = mock_store_instance

                await async_save_discovery_cache(hass, "test_entry", device_index)

                # Wait for the persist task to complete
                for task in created_tasks:
                    await task

                # Verify async_save was called
                mock_store_instance.async_save.assert_called_once()
                saved_data = mock_store_instance.async_save.call_args[0][0]
                assert "entries" in saved_data
                assert "test_entry" in saved_data["entries"]
                assert "device_index" in saved_data["entries"]["test_entry"]

    @pytest.mark.asyncio
    async def test_overwrites_existing_entry(self) -> None:
        """Test overwrites existing entry for same entry_id."""
        hass = MagicMock()
        hass.data = {}

        created_tasks = []

        async def run_coro(coro, name=None, eager_start=False):
            task = asyncio.create_task(coro)
            created_tasks.append(task)
            return task
        hass.async_create_task = MagicMock(side_effect=run_coro)

        device_index = {"device1": {"ip": "192.168.1.1"}}

        with patch("custom_components.jackery_solarvault.client.discovery_cache._store") as mock_store:
            with patch("custom_components.jackery_solarvault.client.discovery_cache.asyncio.shield", new=lambda x: x):
                mock_store_instance = AsyncMock()
                # Existing data with different entry
                mock_store_instance.async_load = AsyncMock(return_value={
                    "entries": {
                        "other_entry": {
                            "device_index": {"old_device": {"ip": "10.0.0.1"}}
                        }
                    }
                })
                mock_store_instance.async_save = AsyncMock()
                mock_store.return_value = mock_store_instance

                await async_save_discovery_cache(hass, "test_entry", device_index)

                # Wait for the persist task to complete
                for task in created_tasks:
                    await task

                saved_data = mock_store_instance.async_save.call_args[0][0]
                assert "test_entry" in saved_data["entries"]
                assert "other_entry" in saved_data["entries"]  # Preserved
                assert "device1" in saved_data["entries"]["test_entry"]["device_index"]

    @pytest.mark.asyncio
    async def test_normalizes_device_ids(self) -> None:
        """Test normalizes device IDs to strings on save."""
        hass = MagicMock()
        hass.data = {}

        created_tasks = []

        async def run_coro(coro, name=None, eager_start=False):
            task = asyncio.create_task(coro)
            created_tasks.append(task)
            return task
        hass.async_create_task = MagicMock(side_effect=run_coro)

        device_index = {
            123: {"ip": "192.168.1.1"},
            "456": {"ip": "192.168.1.2"},
        }

        with patch("custom_components.jackery_solarvault.client.discovery_cache._store") as mock_store:
            with patch("custom_components.jackery_solarvault.client.discovery_cache.asyncio.shield", new=lambda x: x):
                mock_store_instance = AsyncMock()
                mock_store_instance.async_load = AsyncMock(return_value={})
                mock_store_instance.async_save = AsyncMock()
                mock_store.return_value = mock_store_instance

                await async_save_discovery_cache(hass, "test_entry", device_index)

                # Wait for the persist task to complete
                for task in created_tasks:
                    await task

                saved_data = mock_store_instance.async_save.call_args[0][0]
                assert "123" in saved_data["entries"]["test_entry"]["device_index"]
                assert "456" in saved_data["entries"]["test_entry"]["device_index"]

    @pytest.mark.asyncio
    async def test_deep_copies_metadata(self) -> None:
        """Test deep copies device metadata."""
        hass = MagicMock()
        hass.data = {}

        created_tasks = []

        async def run_coro(coro, name=None, eager_start=False):
            task = asyncio.create_task(coro)
            created_tasks.append(task)
            return task
        hass.async_create_task = MagicMock(side_effect=run_coro)

        original = {"ip": "192.168.1.1"}
        device_index = {"device1": original}

        with patch("custom_components.jackery_solarvault.client.discovery_cache._store") as mock_store:
            with patch("custom_components.jackery_solarvault.client.discovery_cache.asyncio.shield", new=lambda x: x):
                mock_store_instance = AsyncMock()
                mock_store_instance.async_load = AsyncMock(return_value={})
                mock_store_instance.async_save = AsyncMock()
                mock_store.return_value = mock_store_instance

                await async_save_discovery_cache(hass, "test_entry", device_index)

                # Wait for the persist task to complete
                for task in created_tasks:
                    await task

                # Modify original - should not affect saved data
                original["ip"] = "modified"

                saved_data = mock_store_instance.async_save.call_args[0][0]
                assert saved_data["entries"]["test_entry"]["device_index"]["device1"]["ip"] == "192.168.1.1"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
