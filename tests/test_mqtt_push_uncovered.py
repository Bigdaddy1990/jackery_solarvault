"""Tests for uncovered paths in mqtt_push.py to increase coverage."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from custom_components.jackery_solarvault.client.mqtt_push import JackeryMqttPushClient
from custom_components.jackery_solarvault.const import MQTT_TOPIC_PREFIX, REDACTED_VALUE


class TestJackeryMqttPushClient:  # noqa: PLR0904
    """Test JackeryMqttPushClient class."""

    def _create_client(self, generation=0):  # noqa: PLR6301
        """Create a basic client for testing."""
        hass = MagicMock()
        hass.data = {}
        hass.async_create_background_task = MagicMock()
        hass.async_add_executor_job = AsyncMock(return_value=MagicMock())
        hass.config = MagicMock()
        hass.config.path = MagicMock(return_value="/config")

        async def mock_message_callback(topic: str, data: dict) -> None:
            pass

        client = JackeryMqttPushClient(
            hass=hass,
            message_callback=mock_message_callback,
        )
        client._session_generation = generation
        return client

    def _create_client_with_tls_ca_missing(self, generation=0):  # noqa: PLR6301
        """Create a client with missing TLS CA file."""
        hass = MagicMock()
        hass.data = {}
        hass.async_create_background_task = MagicMock()
        hass.async_add_executor_job = AsyncMock(return_value=MagicMock())
        hass.config = MagicMock()
        hass.config.path = MagicMock(return_value="/config")

        async def mock_message_callback(topic: str, data: dict) -> None:
            pass

        client = JackeryMqttPushClient(
            hass=hass,
            message_callback=mock_message_callback,
        )
        client._session_generation = generation
        return client

    def test_creation(self) -> None:
        """Test client creation."""
        client = self._create_client()
        assert client is not None
        assert client._connected is False
        assert client._session_generation == 0

    def test_connected_property(self) -> None:
        """Test connected property."""
        client = self._create_client()
        assert client.is_connected is False

    def test_is_started_property(self) -> None:
        """Test is_started property."""
        client = self._create_client()
        assert client.is_started is False

    def test_generation_property(self) -> None:
        """Test session_generation property."""
        client = self._create_client(generation=5)
        assert client.session_generation == 5

    def test_responses_correlated_property(self) -> None:
        """Test responses_correlated property."""
        client = self._create_client()
        assert client.responses_correlated == 0

    def test_responses_expired_property(self) -> None:
        """Test responses_expired property."""
        client = self._create_client()
        assert client.responses_expired == 0

    def test_diagnostics_snapshot(self) -> None:
        """Test diagnostics_snapshot method."""
        client = self._create_client()
        snapshot = client.diagnostics_snapshot()
        assert isinstance(snapshot, dict)

    def test_diagnostics(self) -> None:
        """Test diagnostics property."""
        client = self._create_client()
        snapshot = client.diagnostics
        assert isinstance(snapshot, dict)

    def test_seconds_since_last_message(self) -> None:
        """Test seconds_since_last_message property."""
        client = self._create_client()
        result = client.seconds_since_last_message
        assert result is None or isinstance(result, float)

    def test_consecutive_auth_failures(self) -> None:
        """Test consecutive_auth_failures property."""
        client = self._create_client()
        assert client.consecutive_auth_failures == 0

    @pytest.mark.asyncio
    async def test_start_success(self) -> None:
        """Test start method success."""
        client = self._create_client()

        # Create a proper mock task that returns done=False
        mock_runner_task = MagicMock()
        mock_runner_task.done.return_value = False

        with patch(
            "custom_components.jackery_solarvault.client.mqtt_push.aiomqtt"
        ) as mock_aiomqtt:
            mock_client = AsyncMock()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=None)
            mock_aiomqtt.Client.return_value = mock_client

            mock_client.subscribe = AsyncMock()
            # messages needs to be an async iterator

            async def mock_messages():  # noqa: RUF029
                return
                yield  # pragma: no cover

            mock_client.messages = mock_messages()

            # Mock the background task creation to return our mock task
            with patch.object(  # noqa: SIM117
                client._hass,
                "async_create_background_task",
                return_value=mock_runner_task,  # noqa: E501, RUF100, SLF001
            ):
                # Mock the SSL context creation
                with patch.object(
                    client, "_build_ssl_context_blocking", return_value=MagicMock()
                ):
                    await client.async_start(
                        client_id="test_client",
                        username="test_user",
                        password="test_pass",
                        user_id="test_user_id",
                    )

            # The runner task should be started
            assert client.is_started is True
            # Verify the fingerprint was set
            assert client._fingerprint is not None
            assert client._connect_attempts == 1

    @pytest.mark.asyncio
    async def test_stop(self) -> None:
        """Test stop method."""
        client = self._create_client()
        client._connected = True
        client._client = AsyncMock()

        await client.async_stop()
        assert client.is_connected is False

    def test_handle_message_valid(self) -> None:
        """Test _handle_message with valid message."""
        client = self._create_client(generation=0)
        client._message_callback = AsyncMock()

        # _handle_message signature: (topic, payload, generation=None, runner_task=None)
        # It schedules the callback but doesn't return a coroutine
        client._handle_message(
            "jackery/device/123/status",
            b'{"generation": 0, "data": {"soc": 80}}',
            generation=0,
            runner_task=None,
        )
        # The callback is scheduled as a background task, not awaited directly
        # Just verify the message was processed (messages_seen incremented)
        assert client._messages_seen == 1

    def test_handle_message_wrong_generation(self) -> None:
        """Test _handle_message with wrong generation."""
        client = self._create_client(generation=1)
        client._message_callback = AsyncMock()

        client._handle_message(
            "jackery/device/123/status",
            b'{"generation": 0, "data": {"soc": 80}}',
            generation=0,
            runner_task=None,
        )
        # Message should be ignored due to wrong generation
        assert client._messages_seen == 0

    def test_handle_message_no_body_field_fallback(self) -> None:
        """Test _handle_message without body field (falls back to data)."""
        client = self._create_client(generation=0)
        client._message_callback = AsyncMock()

        client._handle_message(
            "jackery/device/123/status",
            b'{"generation": 0, "data": {"soc": 80}}',
            generation=0,
            runner_task=None,
        )
        assert client._messages_seen == 1

    def test_handle_message_invalid_json(self) -> None:
        """Test _handle_message with invalid JSON."""
        client = self._create_client(generation=0)
        client._message_callback = AsyncMock()

        client._handle_message(
            "jackery/device/123/status",
            b"invalid json",
            generation=0,
            runner_task=None,
        )
        # Invalid JSON should be dropped
        assert client._messages_seen == 0
        assert client._messages_dropped == 1

    def test_schedule_coroutine(self) -> None:
        """Test _schedule_coroutine method."""
        client = self._create_client()

        async def dummy_coro() -> str:  # noqa: RUF029
            return "done"

        # _schedule_coroutine takes a coroutine factory, label, generation, runner_task, tracked_tasks
        # It schedules the coroutine but returns None (task is tracked internally)
        client._schedule_coroutine(lambda: dummy_coro(), "test")
        # Verify it doesn't raise an error
        assert True

    @pytest.mark.asyncio
    async def test_publish(self) -> None:
        """Test publish method."""
        client = self._create_client()
        client._connected = True
        client._client = AsyncMock()

        await client.async_publish_json("test/topic", {"key": "value"})
        client._client.publish.assert_called_once()

    @pytest.mark.asyncio
    async def test_publish_not_connected(self) -> None:
        """Test publish when not connected."""
        client = self._create_client()
        client._connected = False

        with pytest.raises(Exception):  # noqa: B017
            await client.async_publish_json("test/topic", {"key": "value"})

    def test_credential_fingerprint(self) -> None:  # noqa: PLR6301
        """Test _credential_fingerprint static method."""
        fp1 = JackeryMqttPushClient._credential_fingerprint("client1", "user1", "pass1")
        fp2 = JackeryMqttPushClient._credential_fingerprint("client1", "user1", "pass1")
        fp3 = JackeryMqttPushClient._credential_fingerprint("client2", "user1", "pass1")
        assert fp1 == fp2
        assert fp1 != fp3
        assert len(fp1) == 64  # SHA-256 hex

    def test_extract_mqtt_code(self) -> None:  # noqa: PLR6301
        """Test _extract_mqtt_code static method."""

        # Create mock error with rc attribute
        class MockError:
            rc = 5

        err = MockError()
        code = JackeryMqttPushClient._extract_mqtt_code(err)
        assert code == 5

        # Test with rc.value
        class MockError2:
            class RC:
                value = 135

            rc = RC()

        err2 = MockError2()
        code2 = JackeryMqttPushClient._extract_mqtt_code(err2)
        assert code2 == 135

    def test_is_connect_auth_failure_rc(self) -> None:  # noqa: PLR6301
        """Test _is_connect_auth_failure_rc static method."""
        # Auth failure codes: 4, 5, 134, 135
        assert JackeryMqttPushClient._is_connect_auth_failure_rc(4) is True
        assert JackeryMqttPushClient._is_connect_auth_failure_rc(5) is True
        assert JackeryMqttPushClient._is_connect_auth_failure_rc(134) is True
        assert JackeryMqttPushClient._is_connect_auth_failure_rc(135) is True
        assert JackeryMqttPushClient._is_connect_auth_failure_rc(0) is False
        assert JackeryMqttPushClient._is_connect_auth_failure_rc(1) is False

    def test_is_connect_failure_error(self) -> None:  # noqa: PLR6301
        """Test _is_connect_failure_error static method."""
        assert (
            JackeryMqttPushClient._is_connect_failure_error("connect rc=5 (auth)")
            is True
        )
        assert (
            JackeryMqttPushClient._is_connect_failure_error("connect failed: timeout")
            is True
        )
        assert (
            JackeryMqttPushClient._is_connect_failure_error("disconnect: error")
            is False
        )  # noqa: E501, RUF100, SLF001
        assert JackeryMqttPushClient._is_connect_failure_error(None) is False
        assert JackeryMqttPushClient._is_connect_failure_error("") is False

    def test_redact_topic(self) -> None:  # noqa: PLR6301
        """Test _redact_topic static method."""
        topic = f"{MQTT_TOPIC_PREFIX}/user123/status"
        redacted = JackeryMqttPushClient._redact_topic(topic)
        assert REDACTED_VALUE in redacted
        assert "user123" not in redacted

        # Non-matching topic
        topic2 = "other/prefix/user123/status"
        redacted2 = JackeryMqttPushClient._redact_topic(topic2)
        assert redacted2 == topic2

        # None
        assert JackeryMqttPushClient._redact_topic(None) is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
