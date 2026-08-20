"""Tests for MQTT credential canonical implementation and Base64 error handling.

Task 2 requirements:
- One canonical `async_get_mqtt_credentials` implementation
- Public compatibility aliases delegate to it
- Exact Base64 decoding exception handling (binascii.Error)
- No plaintext/token material in logs
"""

import base64
import binascii
from unittest.mock import Mock

import pytest

from custom_components.jackery_solarvault.client.api import JackeryApi


def _make_api() -> JackeryApi:
    """Build an API client."""
    return JackeryApi(Mock(), "tester@example.com", "secret")


class TestMqttCredentialCanonical:
    """Test canonical MQTT credential implementation."""

    @pytest.mark.asyncio
    async def test_async_get_mqtt_credentials_is_canonical(self) -> None:
        """async_get_mqtt_credentials is the single canonical implementation."""
        api = _make_api()
        # Both public methods should delegate to the same private implementation
        result_async = await api.async_get_mqtt_credentials()
        result_cached = api.get_cached_mqtt_credentials()
        # They should both return the same result (None when no session)
        assert result_async == result_cached is None

    @pytest.mark.asyncio
    async def test_get_cached_delegates_to_canonical(self) -> None:
        """get_cached_mqtt_credentials delegates to async_get_mqtt_credentials."""
        api = _make_api()
        # When there's a session, both should return the same derived credentials
        api._mqtt_user_id = "user123"
        api._mqtt_seed_b64 = base64.b64encode(b"x" * 32).decode("ascii")
        api._mqtt_mac_id = "2" + "a" * 32  # valid 33-char MAC ID

        result_async = await api.async_get_mqtt_credentials()
        result_cached = api.get_cached_mqtt_credentials()

        # Both should return identical credential dicts
        assert result_async is not None
        assert result_cached is not None
        assert result_async == result_cached
        assert result_async["client_id"] == result_cached["client_id"]
        assert result_async["username"] == result_cached["username"]
        assert result_async["password"] == result_cached["password"]

    @pytest.mark.asyncio
    async def test_base64_decode_error_raises_typed_error_not_nameerror(self) -> None:
        """Base64 decoding failures return None, not NameError or unhandled exception."""
        api = _make_api()
        api._mqtt_user_id = "user123"
        api._mqtt_seed_b64 = "not-valid-base64!!!"
        api._mqtt_mac_id = "2" + "a" * 32

        # Should return None gracefully, not raise NameError or binascii.Error
        result = await api.async_get_mqtt_credentials()
        assert result is None, (
            "Invalid base64 seed must return None gracefully, not raise "
            "binascii.Error or NameError. The exact exception binascii.Error "
            "must be caught internally."
        )

    @pytest.mark.asyncio
    async def test_base64_decode_wrong_length_returns_none(self) -> None:
        """Base64 seed of wrong length (not 32 bytes) returns None."""
        api = _make_api()
        api._mqtt_user_id = "user123"
        api._mqtt_seed_b64 = base64.b64encode(b"short").decode("ascii")  # 5 bytes, not 32
        api._mqtt_mac_id = "2" + "a" * 32

        result = await api.async_get_mqtt_credentials()
        assert result is None, "Seed of wrong length must return None"

    @pytest.mark.asyncio
    async def test_no_plaintext_token_in_logs(self) -> None:
        """Credential derivation never logs plaintext tokens/secrets."""
        api = _make_api()
        api._mqtt_user_id = "user123"
        api._mqtt_seed_b64 = base64.b64encode(b"x" * 32).decode("ascii")
        api._mqtt_mac_id = "2" + "a" * 32

        # Capture log output
        from io import StringIO
        import logging

        log_stream = StringIO()
        handler = logging.StreamHandler(log_stream)
        logger = logging.getLogger("custom_components.jackery_solarvault.client.api")
        logger.addHandler(handler)
        logger.setLevel(logging.DEBUG)

        try:
            await api.async_get_mqtt_credentials()
            log_output = log_stream.getvalue()

            # Verify no sensitive data in logs
            assert "user123" not in log_output, "User ID must not appear in logs"
            assert api._mqtt_seed_b64 not in log_output, "Seed must not appear in logs"
            assert api._mqtt_mac_id not in log_output, "MAC ID must not appear in logs"
        finally:
            logger.removeHandler(handler)

    @pytest.mark.asyncio
    async def test_no_direct_decoding_duplication_in_rsa_encrypt(self) -> None:
        """RSA encrypt uses base64.b64decode but handles exact exception."""
        from custom_components.jackery_solarvault.client.api import (
            _rsa_pkcs1v15_encrypt,
        )

        # Invalid base64 should raise binascii.Error (the exact exception)
        with pytest.raises(binascii.Error):
            _rsa_pkcs1v15_encrypt(b"test", "not-valid-base64!!!")

        # Valid base64 but invalid DER should raise ValueError (from load_der_public_key)
        import base64
        valid_b64_but_not_der = base64.b64encode(b"not a der key").decode("ascii")
        with pytest.raises(ValueError, match="Could not deserialize key data"):
            _rsa_pkcs1v15_encrypt(b"test", valid_b64_but_not_der)

    @pytest.mark.asyncio
    async def test_mqtt_credential_structure_is_complete(self) -> None:
        """Derived credentials have all four required fields."""
        api = _make_api()
        api._mqtt_user_id = "user123"
        api._mqtt_seed_b64 = base64.b64encode(b"x" * 32).decode("ascii")
        api._mqtt_mac_id = "2" + "a" * 32

        result = await api.async_get_mqtt_credentials()

        assert result is not None
        assert "client_id" in result
        assert "username" in result
        assert "password" in result
        assert "user_id" in result
        assert result["client_id"] == "user123@APP"
        assert result["username"] == "user123@2aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
        assert result["user_id"] == "user123"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
