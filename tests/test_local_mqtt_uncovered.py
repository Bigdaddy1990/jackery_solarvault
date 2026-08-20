"""Tests for uncovered paths in local_mqtt.py to increase coverage."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
    _local_mqtt_client,
)


class TestJackeryLocalMqttClient:
    """Test JackeryLocalMqttClient class."""

    def _create_client(self) -> JackeryLocalMqttClient:  # noqa: PLR6301
        """Create a basic client for testing."""
        hass = SimpleNamespace()
        hass.data = {}
        hass.config = SimpleNamespace()
        hass.config.path = MagicMock(return_value="/config")

        def mock_create_task(coro, name=None):
            return MagicMock()

        hass.async_create_task = mock_create_task

        def mock_schedule(coro_factory, name, eager_start=False):
            return MagicMock()

        hass.async_create_background_task = mock_schedule

        return JackeryLocalMqttClient(
            hass=hass,
            host="localhost",
            port=1883,
            username="user",
            password="pass",
            client_id="test_client",
            sink=None,
            topic_filter="test/topic",
        )

    def test_creation(self) -> None:
        """Test client creation."""
        client = self._create_client()
        assert client is not None
        assert client._connected is False
        assert client._messages_received == 0
        assert client._messages_dropped == 0
        assert client._last_error is None

    def test_is_connected_false_initially(self) -> None:
        """Test is_connected property returns False initially."""
        client = self._create_client()
        assert client.is_connected is False

    def test_is_started_false_initially(self) -> None:
        """Test is_started property returns False initially."""
        client = self._create_client()
        assert client.is_started is False

    def test_diagnostics_snapshot(self) -> None:
        """Test diagnostics_snapshot method."""
        client = self._create_client()
        snapshot = client.diagnostics_snapshot()
        assert isinstance(snapshot, dict)
        assert "connected" in snapshot
        assert "started" in snapshot
        assert "messages_received" in snapshot
        assert "messages_dropped" in snapshot

    def test_diagnostics_snapshot_redact(self) -> None:
        """Test diagnostics_snapshot with redact=False."""
        client = self._create_client()
        snapshot = client.diagnostics_snapshot(redact=False)
        assert isinstance(snapshot, dict)

    @pytest.mark.asyncio
    async def test_async_start_success(self) -> None:
        """Test async_start method success."""
        client = self._create_client()

        with patch(
            "custom_components.jackery_solarvault.client.local_mqtt.aiomqtt"
        ) as mock_aiomqtt:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_aiomqtt.Client.return_value = mock_client

            mock_client.subscribe = AsyncMock()
            # Create an async iterator for messages

            async def mock_messages():  # noqa: RUF029
                return
                yield  # pragma: no cover - make it an async generator

            mock_client.messages = mock_messages()

            await client.async_start()
            # Note: is_connected may not be True immediately due to async nature
            # but the runner task should be started
            assert client.is_started is True

    @pytest.mark.asyncio
    async def test_async_stop(self) -> None:
        """Test async_stop method."""
        client = self._create_client()
        client._runner_task = MagicMock()
        client._runner_task.done = MagicMock(return_value=False)

        with patch(
            "custom_components.jackery_solarvault.client.local_mqtt.aiomqtt"
        ) as mock_aiomqtt:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_aiomqtt.Client.return_value = mock_client

            await client.async_stop()
            assert client._connected is False

    def test_handle_message(self) -> None:
        """Test _handle_message method."""
        client = self._create_client()
        client._sink = AsyncMock()

        # Call _handle_message directly
        client._handle_message("test/topic", b'{"key": "value"}')

        # Since sink is async, it gets scheduled as a task
        # We can verify messages_received was incremented
        assert client._messages_received == 1
        assert client._last_topic == "test/topic"

    def test_handle_message_with_sink(self) -> None:
        """Test _handle_message forwards to sink."""
        client = self._create_client()
        sink_mock = AsyncMock()
        client._sink = sink_mock

        client._handle_message("test/topic", b'{"key": "value"}')

        # The sink should have been scheduled
        # Since we can't easily await the scheduled task in test,
        # verify the message was counted
        assert client._messages_received == 1
        assert client._messages_forwarded == 1

    def test_handle_message_non_json(self) -> None:
        """Test _handle_message with non-JSON payload."""
        client = self._create_client()
        client._sink = AsyncMock()

        client._handle_message("test/topic", b"not json")

        assert client._messages_received == 1
        assert client._messages_forwarded == 1
        # data should be None for non-JSON
        # We can't easily test the sink call, but forward count should increment

    def test_handle_message_oversized_payload(self) -> None:
        """Test _handle_message with oversized payload."""
        client = self._create_client()
        client._sink = AsyncMock()

        # Create payload larger than LOCAL_MQTT_MAX_PAYLOAD_BYTES (128KB)
        large_payload = b"x" * (128 * 1024 + 1)
        client._handle_message("test/topic", large_payload)

        assert client._payload_too_large_count == 1
        assert client._messages_received == 1

    def test_topic_tracking(self) -> None:
        """Test topic tracking in _handle_message."""
        client = self._create_client()
        client._sink = AsyncMock()

        # First message on topic
        client._handle_message("topic1", b"{}")
        assert client._topics_seen == ["topic1"]
        assert len(client._topics_seen) == 1

        # Second message on same topic
        client._handle_message("topic1", b"{}")
        assert client._topics_seen == ["topic1"]
        assert len(client._topics_seen) == 1

        # Message on new topic
        client._handle_message("topic2", b"{}")
        assert client._topics_seen == ["topic1", "topic2"]
        assert len(client._topics_seen) == 2

    def test_utc_now_iso(self) -> None:  # noqa: PLR6301
        """Test _utc_now_iso static method."""
        result = JackeryLocalMqttClient._utc_now_iso()
        assert isinstance(result, str)
        assert "T" in result
        assert "+" in result or result.endswith("Z")

    def test_extract_mqtt_code(self) -> None:  # noqa: PLR6301
        """Test _extract_mqtt_code static method."""

        class MockError:
            rc = 5

        err = MockError()
        code = JackeryLocalMqttClient._extract_mqtt_code(err)
        assert code == 5

    def test_local_mqtt_client_retrieval(self) -> None:  # noqa: PLR6301
        """Test _local_mqtt_client function."""
        hass = MagicMock()
        entry = MagicMock()
        entry.entry_id = "test_entry"

        # No client
        result = _local_mqtt_client(hass, entry)
        assert result is None

        # With client
        client = MagicMock(spec=JackeryLocalMqttClient)
        hass.data = {
            "jackery_solarvault": {"test_entry": {"local_mqtt_client": client}}
        }  # noqa: E501, RUF100
        result = _local_mqtt_client(hass, entry)
        assert result is client

        # Wrong type
        hass.data = {
            "jackery_solarvault": {"test_entry": {"local_mqtt_client": "not_a_client"}}
        }  # noqa: E501, RUF100
        result = _local_mqtt_client(hass, entry)
        assert result is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
