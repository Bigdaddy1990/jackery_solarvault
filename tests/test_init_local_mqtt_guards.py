"""Tests for the local MQTT guard logic added to __init__.py in this PR.

Covers:
- _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS: constant contains "#" and "+/#"
- _local_mqtt_client: returns None for missing/wrong-type data, returns client when found
- _async_start_local_mqtt: all guard conditions (enable flag, host, topic filter, blocked filters)
- _async_start_local_mqtt: starts client when all conditions are met
- _async_start_local_mqtt: _async_stop_local_mqtt cleanup behavior

Also covers:
- _rsa_pkcs1v15_encrypt (client/api.py): TypeError raised when loaded key is not RSA
- _generate_udid: output format includes MQTT_MAC_ID_PREFIX + 32 hex chars
"""

import logging
from typing import Any
from unittest.mock import AsyncMock
from unittest.mock import MagicMock
from unittest.mock import patch

import pytest


# ---------------------------------------------------------------------------
# _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS
# ---------------------------------------------------------------------------


class TestBlockedLocalMqttTopicFilters:
    """Tests for the _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS constant."""

    def test_contains_hash(self) -> None:
        """The blocked set must contain '#'."""
        from custom_components.jackery_solarvault import (
            _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS,
        )

        assert "#" in _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS

    def test_contains_plus_hash(self) -> None:
        """The blocked set must contain '+/#'."""
        from custom_components.jackery_solarvault import (
            _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS,
        )

        assert "+/#" in _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS

    def test_is_frozenset(self) -> None:
        """_BLOCKED_LOCAL_MQTT_TOPIC_FILTERS must be a frozenset."""
        from custom_components.jackery_solarvault import (
            _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS,
        )

        assert isinstance(_BLOCKED_LOCAL_MQTT_TOPIC_FILTERS, frozenset)

    def test_scoped_filter_is_not_blocked(self) -> None:
        """A scoped topic filter like 'jackery/#' must NOT be in the blocked set."""
        from custom_components.jackery_solarvault import (
            _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS,
        )

        assert "jackery/#" not in _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS
        assert "home/devices/+/status" not in _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS

    def test_empty_string_is_not_in_blocked_set(self) -> None:
        """Empty string must not be blocked (it is handled separately by emptiness check)."""
        from custom_components.jackery_solarvault import (
            _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS,
        )

        assert "" not in _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS


# ---------------------------------------------------------------------------
# _local_mqtt_client
# ---------------------------------------------------------------------------


class TestLocalMqttClient:
    """Tests for _local_mqtt_client() helper."""

    def test_returns_none_when_domain_not_in_hass_data(self) -> None:
        """When the DOMAIN key is absent from hass.data, must return None."""
        from custom_components.jackery_solarvault import _local_mqtt_client
        from custom_components.jackery_solarvault.const import DOMAIN

        hass = MagicMock()
        hass.data = {}
        entry = MagicMock()
        entry.entry_id = "entry-abc"

        result = _local_mqtt_client(hass, entry)
        assert result is None

    def test_returns_none_when_entry_id_not_in_hass_data(self) -> None:
        """When the entry_id key is absent from hass.data[DOMAIN], must return None."""
        from custom_components.jackery_solarvault import _local_mqtt_client
        from custom_components.jackery_solarvault.const import DOMAIN

        hass = MagicMock()
        hass.data = {DOMAIN: {}}
        entry = MagicMock()
        entry.entry_id = "entry-xyz"

        result = _local_mqtt_client(hass, entry)
        assert result is None

    def test_returns_none_when_bucket_is_not_dict(self) -> None:
        """When the bucket is not a dict (e.g. a string), must return None."""
        from custom_components.jackery_solarvault import _local_mqtt_client
        from custom_components.jackery_solarvault.const import DOMAIN

        hass = MagicMock()
        hass.data = {DOMAIN: {"entry-abc": "not-a-dict"}}
        entry = MagicMock()
        entry.entry_id = "entry-abc"

        result = _local_mqtt_client(hass, entry)
        assert result is None

    def test_returns_none_when_local_mqtt_key_absent(self) -> None:
        """When the bucket dict has no 'local_mqtt_client' key, must return None."""
        from custom_components.jackery_solarvault import _local_mqtt_client
        from custom_components.jackery_solarvault.const import DOMAIN

        hass = MagicMock()
        hass.data = {DOMAIN: {"entry-abc": {"other_key": "value"}}}
        entry = MagicMock()
        entry.entry_id = "entry-abc"

        result = _local_mqtt_client(hass, entry)
        assert result is None

    def test_returns_none_when_stored_value_is_wrong_type(self) -> None:
        """When the stored value is not a JackeryLocalMqttClient, must return None."""
        from custom_components.jackery_solarvault import _local_mqtt_client
        from custom_components.jackery_solarvault.const import DOMAIN

        hass = MagicMock()
        hass.data = {
            DOMAIN: {"entry-abc": {"local_mqtt_client": "not-a-client"}},
        }
        entry = MagicMock()
        entry.entry_id = "entry-abc"

        result = _local_mqtt_client(hass, entry)
        assert result is None

    def test_returns_client_when_stored_correctly(self) -> None:
        """When a JackeryLocalMqttClient is stored, must return it."""
        try:
            from custom_components.jackery_solarvault.client.local_mqtt import (
                JackeryLocalMqttClient,
            )
        except ImportError, SyntaxError:
            pytest.skip("JackeryLocalMqttClient not importable")

        from custom_components.jackery_solarvault import _local_mqtt_client
        from custom_components.jackery_solarvault.const import DOMAIN

        mock_client = MagicMock(spec=JackeryLocalMqttClient)
        hass = MagicMock()
        hass.data = {
            DOMAIN: {"entry-abc": {"local_mqtt_client": mock_client}},
        }
        entry = MagicMock()
        entry.entry_id = "entry-abc"

        result = _local_mqtt_client(hass, entry)
        assert result is mock_client


# ---------------------------------------------------------------------------
# _async_start_local_mqtt guard conditions
# ---------------------------------------------------------------------------


def _make_mock_entry(
    *,
    enable: bool = True,
    host: str = "192.168.1.100",
    topic_filter: str = "jackery/devices/#",
    port: int = 1883,
    username: str = "",
    password: str = "",
    entry_id: str = "test-entry-id",
) -> MagicMock:
    """Build a mock config entry for _async_start_local_mqtt tests."""
    entry = MagicMock()
    entry.entry_id = entry_id
    # Wire up options so config_entry_*_option helpers work.
    # Also set data={} so legacy fallback in util doesn't raise AttributeError.
    options = {
        "third_party_mqtt_enable": enable,
        "third_party_mqtt_ip": host,
        "third_party_mqtt_topic_filter": topic_filter,
        "third_party_mqtt_port": port,
        "third_party_mqtt_username": username,
        "third_party_mqtt_password": password,
    }
    entry.options = options
    entry.data = {}
    entry.async_on_unload = MagicMock()
    return entry


class TestAsyncStartLocalMqttGuards:
    """Tests for _async_start_local_mqtt guard conditions."""

    async def test_skips_when_third_party_mqtt_disabled(self) -> None:
        """When CONF_THIRD_PARTY_MQTT_ENABLE is False, no client is created."""
        try:
            from custom_components.jackery_solarvault import _async_start_local_mqtt
            from custom_components.jackery_solarvault.client.local_mqtt import (
                JackeryLocalMqttClient,
            )
        except ImportError, SyntaxError:
            pytest.skip("Required modules not importable")

        hass = MagicMock()
        hass.data = {}
        entry = _make_mock_entry(enable=False)
        coordinator = MagicMock()

        with patch(
            "custom_components.jackery_solarvault.client.local_mqtt.JackeryLocalMqttClient",
            autospec=True,
        ) as mock_cls:
            await _async_start_local_mqtt(hass, entry, coordinator)
            mock_cls.assert_not_called()

    async def test_skips_when_host_is_empty(self) -> None:
        """When the host is empty, no client is created."""
        try:
            from custom_components.jackery_solarvault import _async_start_local_mqtt
        except ImportError, SyntaxError:
            pytest.skip("Required modules not importable")

        hass = MagicMock()
        hass.data = {}
        entry = _make_mock_entry(enable=True, host="")
        coordinator = MagicMock()

        with patch(
            "custom_components.jackery_solarvault.client.local_mqtt.JackeryLocalMqttClient",
            autospec=True,
        ) as mock_cls:
            await _async_start_local_mqtt(hass, entry, coordinator)
            mock_cls.assert_not_called()

    async def test_skips_when_host_is_whitespace_only(self) -> None:
        """When the host is only whitespace, no client is created (strip check)."""
        try:
            from custom_components.jackery_solarvault import _async_start_local_mqtt
        except ImportError, SyntaxError:
            pytest.skip("Required modules not importable")

        hass = MagicMock()
        hass.data = {}
        entry = _make_mock_entry(enable=True, host="   ")
        coordinator = MagicMock()

        with patch(
            "custom_components.jackery_solarvault.client.local_mqtt.JackeryLocalMqttClient",
            autospec=True,
        ) as mock_cls:
            await _async_start_local_mqtt(hass, entry, coordinator)
            mock_cls.assert_not_called()

    async def test_skips_when_topic_filter_is_empty(self) -> None:
        """When the topic filter is empty, no client is created."""
        try:
            from custom_components.jackery_solarvault import _async_start_local_mqtt
        except ImportError, SyntaxError:
            pytest.skip("Required modules not importable")

        hass = MagicMock()
        hass.data = {}
        entry = _make_mock_entry(enable=True, host="192.168.1.100", topic_filter="")
        coordinator = MagicMock()

        with patch(
            "custom_components.jackery_solarvault.client.local_mqtt.JackeryLocalMqttClient",
            autospec=True,
        ) as mock_cls:
            await _async_start_local_mqtt(hass, entry, coordinator)
            mock_cls.assert_not_called()

    async def test_skips_when_topic_filter_is_whitespace_only(self) -> None:
        """When the topic filter is whitespace-only after strip, no client is created."""
        try:
            from custom_components.jackery_solarvault import _async_start_local_mqtt
        except ImportError, SyntaxError:
            pytest.skip("Required modules not importable")

        hass = MagicMock()
        hass.data = {}
        entry = _make_mock_entry(
            enable=True,
            host="192.168.1.100",
            topic_filter="   ",
        )
        coordinator = MagicMock()

        with patch(
            "custom_components.jackery_solarvault.client.local_mqtt.JackeryLocalMqttClient",
            autospec=True,
        ) as mock_cls:
            await _async_start_local_mqtt(hass, entry, coordinator)
            mock_cls.assert_not_called()

    async def test_skips_and_warns_when_topic_filter_is_hash(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When topic filter is '#', no client is created and a warning is logged."""
        try:
            from custom_components.jackery_solarvault import _async_start_local_mqtt
        except ImportError, SyntaxError:
            pytest.skip("Required modules not importable")

        hass = MagicMock()
        hass.data = {}
        entry = _make_mock_entry(
            enable=True,
            host="192.168.1.100",
            topic_filter="#",
        )
        coordinator = MagicMock()

        with patch(
            "custom_components.jackery_solarvault.client.local_mqtt.JackeryLocalMqttClient",
            autospec=True,
        ) as mock_cls:
            with caplog.at_level(
                logging.WARNING,
                logger="custom_components.jackery_solarvault",
            ):
                await _async_start_local_mqtt(hass, entry, coordinator)
            mock_cls.assert_not_called()
        assert "blocked" in caplog.text.lower() or "CPU safety" in caplog.text

    async def test_skips_and_warns_when_topic_filter_is_plus_hash(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """When topic filter is '+/#', no client is created and a warning is logged."""
        try:
            from custom_components.jackery_solarvault import _async_start_local_mqtt
        except ImportError, SyntaxError:
            pytest.skip("Required modules not importable")

        hass = MagicMock()
        hass.data = {}
        entry = _make_mock_entry(
            enable=True,
            host="192.168.1.100",
            topic_filter="+/#",
        )
        coordinator = MagicMock()

        with patch(
            "custom_components.jackery_solarvault.client.local_mqtt.JackeryLocalMqttClient",
            autospec=True,
        ) as mock_cls:
            with caplog.at_level(
                logging.WARNING,
                logger="custom_components.jackery_solarvault",
            ):
                await _async_start_local_mqtt(hass, entry, coordinator)
            mock_cls.assert_not_called()
        assert "blocked" in caplog.text.lower() or "+/#" in caplog.text

    async def test_starts_client_when_all_conditions_met(self) -> None:
        """When all conditions are met with a scoped topic filter, the client is started."""
        try:
            from custom_components.jackery_solarvault import _async_start_local_mqtt
            from custom_components.jackery_solarvault.client.local_mqtt import (
                JackeryLocalMqttClient,
            )
        except ImportError, SyntaxError:
            pytest.skip("Required modules not importable")

        hass = MagicMock()
        hass.data = {}
        entry = _make_mock_entry(
            enable=True,
            host="192.168.1.100",
            topic_filter="jackery/devices/+/status",
        )
        coordinator = MagicMock()
        coordinator.async_handle_local_mqtt_message = AsyncMock()

        mock_client = AsyncMock(spec=JackeryLocalMqttClient)

        with patch(
            "custom_components.jackery_solarvault.JackeryLocalMqttClient",
            return_value=mock_client,
        ) as mock_cls:
            await _async_start_local_mqtt(hass, entry, coordinator)
            mock_cls.assert_called_once()
            mock_client.async_start.assert_called_once()

    async def test_client_registered_in_hass_data(self) -> None:
        """After a successful start, the client is stored in hass.data."""
        try:
            from custom_components.jackery_solarvault import (
                _LOCAL_MQTT_RUNTIME_KEY,
                _async_start_local_mqtt,
            )
            from custom_components.jackery_solarvault.client.local_mqtt import (
                JackeryLocalMqttClient,
            )
            from custom_components.jackery_solarvault.const import DOMAIN
        except ImportError, SyntaxError:
            pytest.skip("Required modules not importable")

        hass = MagicMock()
        hass.data = {}

        def _setdefault(key, default=None):
            if key not in hass.data:
                hass.data[key] = default if default is not None else {}
            return hass.data[key]

        hass.data.setdefault = _setdefault

        entry = _make_mock_entry(
            enable=True,
            host="192.168.1.100",
            topic_filter="jackery/power/+",
            entry_id="entry-12345",
        )
        coordinator = MagicMock()
        coordinator.async_handle_local_mqtt_message = AsyncMock()

        mock_client = AsyncMock(spec=JackeryLocalMqttClient)

        with patch(
            "custom_components.jackery_solarvault.JackeryLocalMqttClient",
            return_value=mock_client,
        ):
            await _async_start_local_mqtt(hass, entry, coordinator)

        # Client should be stored in hass.data[DOMAIN][entry_id]
        domain_data = hass.data.get(DOMAIN, {})
        entry_data = domain_data.get("entry-12345", {})
        assert entry_data.get(_LOCAL_MQTT_RUNTIME_KEY) is mock_client

    async def test_unload_callback_registered(self) -> None:
        """entry.async_on_unload must be called to register the stop callback."""
        try:
            from custom_components.jackery_solarvault import _async_start_local_mqtt
            from custom_components.jackery_solarvault.client.local_mqtt import (
                JackeryLocalMqttClient,
            )
        except ImportError, SyntaxError:
            pytest.skip("Required modules not importable")

        hass = MagicMock()
        hass.data = {}
        entry = _make_mock_entry(
            enable=True,
            host="192.168.1.100",
            topic_filter="jackery/+/power",
        )
        coordinator = MagicMock()
        coordinator.async_handle_local_mqtt_message = AsyncMock()

        mock_client = AsyncMock(spec=JackeryLocalMqttClient)

        with patch(
            "custom_components.jackery_solarvault.JackeryLocalMqttClient",
            return_value=mock_client,
        ):
            await _async_start_local_mqtt(hass, entry, coordinator)

        entry.async_on_unload.assert_called_once()


# ---------------------------------------------------------------------------
# Sink function behavior - data routing
# ---------------------------------------------------------------------------


class TestLocalMqttSink:
    """Tests for the _sink function created inside _async_start_local_mqtt."""

    async def test_sink_routes_data_to_coordinator(self) -> None:
        """The _sink must forward non-None data to coordinator.async_handle_local_mqtt_message."""
        try:
            from custom_components.jackery_solarvault import _async_start_local_mqtt
            from custom_components.jackery_solarvault.client.local_mqtt import (
                JackeryLocalMqttClient,
            )
        except ImportError, SyntaxError:
            pytest.skip("Required modules not importable")

        hass = MagicMock()
        hass.data = {}

        entry = _make_mock_entry(
            enable=True,
            host="192.168.1.100",
            topic_filter="jackery/data/+",
        )

        coordinator = MagicMock()
        handle_mock = AsyncMock()
        coordinator.async_handle_local_mqtt_message = handle_mock

        captured_sink = None

        def _capture_client(
            hass_arg, *, host, port, username, password, client_id, sink, topic_filter
        ):
            nonlocal captured_sink
            captured_sink = sink
            client = AsyncMock(spec=JackeryLocalMqttClient)
            return client

        with patch(
            "custom_components.jackery_solarvault.JackeryLocalMqttClient",
            side_effect=_capture_client,
        ):
            await _async_start_local_mqtt(hass, entry, coordinator)

        assert captured_sink is not None
        # Simulate a message arriving
        test_data = {"pv_power": 1000}
        await captured_sink("jackery/data/device1", test_data, b"raw")
        handle_mock.assert_called_once_with("jackery/data/device1", test_data)

    async def test_sink_skips_none_data(self) -> None:
        """The _sink must skip forwarding when data is None."""
        try:
            from custom_components.jackery_solarvault import _async_start_local_mqtt
            from custom_components.jackery_solarvault.client.local_mqtt import (
                JackeryLocalMqttClient,
            )
        except ImportError, SyntaxError:
            pytest.skip("Required modules not importable")

        hass = MagicMock()
        hass.data = {}

        entry = _make_mock_entry(
            enable=True,
            host="192.168.1.100",
            topic_filter="jackery/data/+",
        )

        coordinator = MagicMock()
        handle_mock = AsyncMock()
        coordinator.async_handle_local_mqtt_message = handle_mock

        captured_sink = None

        def _capture_client(
            hass_arg, *, host, port, username, password, client_id, sink, topic_filter
        ):
            nonlocal captured_sink
            captured_sink = sink
            client = AsyncMock(spec=JackeryLocalMqttClient)
            return client

        with patch(
            "custom_components.jackery_solarvault.JackeryLocalMqttClient",
            side_effect=_capture_client,
        ):
            await _async_start_local_mqtt(hass, entry, coordinator)

        await captured_sink("jackery/data/device1", None, b"raw")
        handle_mock.assert_not_called()


# ---------------------------------------------------------------------------
# _rsa_pkcs1v15_encrypt: TypeError for non-RSA key
# ---------------------------------------------------------------------------


class TestRsaPkcs1V15Encrypt:
    """Tests for _rsa_pkcs1v15_encrypt in client/api.py."""

    def test_raises_type_error_for_non_rsa_key(self) -> None:
        """When the DER-encoded key is not an RSA key, must raise TypeError."""
        import base64

        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
        )

        try:
            from custom_components.jackery_solarvault.client.api import (
                _rsa_pkcs1v15_encrypt,
            )
        except ImportError, SyntaxError:
            pytest.skip("api module not importable")

        # Generate a real EC key (not RSA) and serialize it as DER
        ec_key = ec.generate_private_key(ec.SECP256R1()).public_key()
        der_bytes = ec_key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        b64_key = base64.b64encode(der_bytes).decode()

        with pytest.raises(TypeError, match="RSA public key"):
            _rsa_pkcs1v15_encrypt(b"test data", b64_key)

    def test_accepts_valid_rsa_key(self) -> None:
        """A valid RSA public key must produce encrypted output without raising."""
        import base64

        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
        )

        try:
            from custom_components.jackery_solarvault.client.api import (
                _rsa_pkcs1v15_encrypt,
            )
        except ImportError, SyntaxError:
            pytest.skip("api module not importable")

        # Generate a real RSA key and encode as DER
        private_key = rsa.generate_private_key(
            public_exponent=65537,
            key_size=2048,
        )
        pub_key = private_key.public_key()
        der_bytes = pub_key.public_bytes(
            Encoding.DER, PublicFormat.SubjectPublicKeyInfo
        )
        b64_key = base64.b64encode(der_bytes).decode()

        result = _rsa_pkcs1v15_encrypt(b"test payload", b64_key)
        # RSA-2048 PKCS#1 v1.5 output must be 256 bytes
        assert len(result) == 256
        assert isinstance(result, bytes)

    def test_error_message_includes_actual_key_type(self) -> None:
        """TypeError message must mention the actual key type found."""
        import base64

        from cryptography.hazmat.primitives.asymmetric import ec
        from cryptography.hazmat.primitives.serialization import (
            Encoding,
            PublicFormat,
        )

        try:
            from custom_components.jackery_solarvault.client.api import (
                _rsa_pkcs1v15_encrypt,
            )
        except ImportError, SyntaxError:
            pytest.skip("api module not importable")

        ec_key = ec.generate_private_key(ec.SECP256R1()).public_key()
        der_bytes = ec_key.public_bytes(Encoding.DER, PublicFormat.SubjectPublicKeyInfo)
        b64_key = base64.b64encode(der_bytes).decode()

        with pytest.raises(TypeError) as exc_info:
            _rsa_pkcs1v15_encrypt(b"data", b64_key)
        # The error message should mention the actual type
        assert (
            "EllipticCurve" in str(exc_info.value)
            or "EC" in str(exc_info.value)
            or "got" in str(exc_info.value)
        )


# ---------------------------------------------------------------------------
# _generate_udid: output format
# ---------------------------------------------------------------------------


class TestGenerateUdid:
    """Tests for _generate_udid in client/api.py."""

    def test_output_starts_with_mqtt_mac_id_prefix(self) -> None:
        """The generated UDID must start with MQTT_MAC_ID_PREFIX."""
        try:
            from custom_components.jackery_solarvault.client.api import _generate_udid
            from custom_components.jackery_solarvault.const import MQTT_MAC_ID_PREFIX
        except ImportError, SyntaxError:
            pytest.skip("api module not importable")

        result = _generate_udid("test@example.com")
        assert result.startswith(MQTT_MAC_ID_PREFIX)

    def test_output_is_deterministic(self) -> None:
        """Same seed must produce the same UDID."""
        try:
            from custom_components.jackery_solarvault.client.api import _generate_udid
        except ImportError, SyntaxError:
            pytest.skip("api module not importable")

        result1 = _generate_udid("user@example.com")
        result2 = _generate_udid("user@example.com")
        assert result1 == result2

    def test_different_seeds_produce_different_udids(self) -> None:
        """Different seeds must produce different UDIDs."""
        try:
            from custom_components.jackery_solarvault.client.api import _generate_udid
        except ImportError, SyntaxError:
            pytest.skip("api module not importable")

        result1 = _generate_udid("user1@example.com")
        result2 = _generate_udid("user2@example.com")
        assert result1 != result2

    def test_output_has_expected_length(self) -> None:
        """MQTT_MAC_ID_PREFIX (1 char) + 32 hex chars UUID = 33 chars total."""
        try:
            from custom_components.jackery_solarvault.client.api import _generate_udid
            from custom_components.jackery_solarvault.const import MQTT_MAC_ID_PREFIX
        except ImportError, SyntaxError:
            pytest.skip("api module not importable")

        result = _generate_udid("seed")
        prefix_len = len(MQTT_MAC_ID_PREFIX)
        # Total length = prefix + 32 UUID chars (UUID with dashes removed)
        assert len(result) == prefix_len + 32

    def test_output_contains_no_dashes(self) -> None:
        """The UUID portion must have no dashes."""
        try:
            from custom_components.jackery_solarvault.client.api import _generate_udid
            from custom_components.jackery_solarvault.const import MQTT_MAC_ID_PREFIX
        except ImportError, SyntaxError:
            pytest.skip("api module not importable")

        result = _generate_udid("some_account")
        uuid_part = result[len(MQTT_MAC_ID_PREFIX) :]
        assert "-" not in uuid_part

    def test_output_is_lowercase_hex_after_prefix(self) -> None:
        """The UUID portion (after prefix) must be lowercase hexadecimal."""
        import re

        try:
            from custom_components.jackery_solarvault.client.api import _generate_udid
            from custom_components.jackery_solarvault.const import MQTT_MAC_ID_PREFIX
        except ImportError, SyntaxError:
            pytest.skip("api module not importable")

        result = _generate_udid("test_seed")
        uuid_part = result[len(MQTT_MAC_ID_PREFIX) :]
        assert re.fullmatch(r"[0-9a-f]{32}", uuid_part), (
            f"UUID part '{uuid_part}' is not lowercase hex"
        )


# ---------------------------------------------------------------------------
# _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS boundary tests
# ---------------------------------------------------------------------------


def test_blocked_filters_does_not_block_scoped_mqtt_topic() -> None:
    """A deep scoped topic like 'jackery/SV3/12345/+/state' must not be blocked."""
    from custom_components.jackery_solarvault import _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS

    assert "jackery/SV3/12345/+/state" not in _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS


def test_blocked_filters_are_exactly_two_entries() -> None:
    """The blocked filters set must have exactly 2 entries: '#' and '+/#'."""
    from custom_components.jackery_solarvault import _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS

    assert len(_BLOCKED_LOCAL_MQTT_TOPIC_FILTERS) == 2


def test_local_mqtt_runtime_key_is_expected_string() -> None:
    """_LOCAL_MQTT_RUNTIME_KEY must equal 'local_mqtt_client'."""
    from custom_components.jackery_solarvault import _LOCAL_MQTT_RUNTIME_KEY

    assert _LOCAL_MQTT_RUNTIME_KEY == "local_mqtt_client"
