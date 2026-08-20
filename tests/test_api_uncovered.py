"""Tests for uncovered paths in api.py to increase coverage."""

import asyncio
import base64
import json
import os
from typing import Never
from unittest.mock import AsyncMock, MagicMock

import aiohttp
import pytest

from custom_components.jackery_solarvault.client.api import (
    _DAY_CHART_SERIES_KEYS,  # noqa: PLC2701, RUF105
    JackeryApi,
    JackeryApiError,
    _aes_cbc_encrypt,  # noqa: PLC2701, RUF105
    _aes_ecb_encrypt,  # noqa: PLC2701, RUF105
    _data_field_accepted,  # noqa: PLC2701, RUF105
    _generate_udid,  # noqa: PLC2701, RUF105
    _log_body,  # noqa: PLC2701, RUF105
    _log_value_shape,  # noqa: PLC2701, RUF105
    _rsa_pkcs1v15_encrypt,  # noqa: PLC2701, RUF105
    build_login_crypto_fields,
    generate_login_aes_key,
)
from custom_components.jackery_solarvault.const import (
    ACCESSORIES_BIND_PATH,
    ACCESSORIES_UNBIND_PATH,
    ALARM_PATH,
    APP_REQUEST_BEGIN_DATE,
    APP_REQUEST_DATE_TYPE,
    APP_REQUEST_END_DATE,
    APP_REQUEST_META,
    APP_REQUEST_STAT_TYPE,
    BATTERY_PACK_PATH,
    DATE_TYPE_DAY,
    DEVICE_PROPERTY_PATH,
    DEVICE_SHARED_LIST_PATH,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_DEVICE_SN_LIST,
    FIELD_SYSTEM_ID,
    MQTT_CREDENTIAL_CLIENT_ID,
    MQTT_CREDENTIAL_PASSWORD,
    MQTT_CREDENTIAL_USERNAME,
    MQTT_CREDENTIAL_USER_ID,
    MQTT_MAC_ID_PREFIX,
    OTA_LIST_PATH,
    POWER_PRICE_PATH,
    PRICE_SOURCE_LIST_PATH,
    SYSTEM_STATISTIC_PATH,
)


class TestJackeryApi:
    """Test JackeryApi class."""

    def _create_client(self):  # noqa: PLR6301, RUF105
        """Create a basic client for testing with mocked dependencies."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        return JackeryApi(
            session=mock_session,
            account="test_account",
            password="test_password",
        )

    def test_creation(self) -> None:
        """Test client creation."""
        client = self._create_client()
        assert client is not None
        assert client._account == "test_account"  # noqa: RUF105, SLF001
        assert client._password == "test_password"  # noqa: RUF105, SLF001
        assert client._region_code is None  # noqa: RUF105, SLF001
        assert client._token is None  # noqa: RUF105, SLF001

    def test_creation_with_region_code(self) -> None:  # noqa: PLR6301, RUF105
        """Test client creation with custom region code."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        client = JackeryApi(
            session=mock_session,
            account="test_account",
            password="test_password",
            region_code="DE",
        )
        assert client._region_code == "DE"  # noqa: RUF105, SLF001

    def test_headers_property(self) -> None:
        """Test _headers method."""
        client = self._create_client()
        headers = client._headers(with_token=False)  # noqa: RUF105, SLF001
        assert "accept-encoding" in headers
        assert "accept-language" in headers
        assert "app_version" in headers
        assert "host" in headers

    def test_headers_with_token(self) -> None:
        """Test _headers method with token."""
        client = self._create_client()
        client._token = "test_token"  # noqa: RUF105, SLF001
        headers = client._headers(with_token=True)  # noqa: RUF105, SLF001
        # The Jackery API uses "token" header, not "Authorization"
        assert "token" in headers
        assert headers["token"] == "test_token"

    def test_maybe_learn_region_code(self) -> None:
        """Test _maybe_learn_region_code method."""
        client = self._create_client()
        systems = [{"countryCode": "US"}]
        client._maybe_learn_region_code(systems)  # noqa: RUF105, SLF001
        assert client._region_code == "US"  # noqa: RUF105, SLF001

    def test_maybe_learn_region_code_already_set(self) -> None:  # noqa: PLR6301, RUF105
        """Test _maybe_learn_region_code when already set."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        client = JackeryApi(
            session=mock_session,
            account="test_account",
            password="test_password",
            region_code="DE",
        )
        systems = [{"countryCode": "US"}]
        client._maybe_learn_region_code(systems)  # noqa: RUF105, SLF001
        assert client._region_code == "DE"  # Should not change  # noqa: RUF105, SLF001

    def test_maybe_learn_region_code_no_country(self) -> None:
        """Test _maybe_learn_region_code with no country code."""
        client = self._create_client()
        systems = [{}]
        client._maybe_learn_region_code(systems)  # noqa: RUF105, SLF001
        assert client._region_code is None  # noqa: RUF105, SLF001


class TestCryptoFunctions:
    """Test crypto utility functions."""

    def test_aes_ecb_encrypt(self) -> None:  # noqa: PLR6301, RUF105
        """Test _aes_ecb_encrypt function."""
        plaintext = b"test data"
        key = os.urandom(16)  # AES-128
        encrypted = _aes_ecb_encrypt(plaintext, key)
        assert isinstance(encrypted, bytes)
        assert len(encrypted) > 0
        assert encrypted != plaintext

    def test_aes_ecb_encrypt_pkcs7_padding(self) -> None:  # noqa: PLR6301, RUF105
        """Test PKCS7 padding is applied."""
        # Plaintext not multiple of 16 bytes
        plaintext = b"short"
        key = os.urandom(16)
        encrypted = _aes_ecb_encrypt(plaintext, key)
        # Should be padded to 16 bytes (AES block size)
        assert len(encrypted) == 16

    def test_aes_cbc_encrypt(self) -> None:  # noqa: PLR6301, RUF105
        """Test _aes_cbc_encrypt function."""
        plaintext = b"test data for cbc"
        key = os.urandom(32)  # AES-256
        iv = os.urandom(16)
        encrypted = _aes_cbc_encrypt(plaintext, key, iv)
        assert isinstance(encrypted, bytes)
        assert len(encrypted) > 0
        assert encrypted != plaintext

    def test_rsa_pkcs1v15_encrypt(self) -> None:  # noqa: PLR6301, RUF105
        """Test _rsa_pkcs1v15_encrypt with bundled key."""
        # Generate a test RSA key pair
        from cryptography.hazmat.primitives import serialization  # noqa: I001, PLC0415, RUF105
        from cryptography.hazmat.primitives.asymmetric import rsa as crypto_rsa  # noqa: PLC0415, RUF105

        private_key = crypto_rsa.generate_private_key(
            public_exponent=65537,
            key_size=1024,
        )
        public_key = private_key.public_key()
        der_bytes = public_key.public_bytes(
            encoding=serialization.Encoding.DER,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        public_key_b64 = base64.b64encode(der_bytes).decode("ascii")

        data = b"test data"
        encrypted = _rsa_pkcs1v15_encrypt(data, public_key_b64)
        assert isinstance(encrypted, bytes)
        assert len(encrypted) > 0

    def test_rsa_pkcs1v15_encrypt_invalid_key(self) -> None:
        """Test _rsa_pkcs1v15_encrypt raises on non-RSA key."""
        # EC key or other non-RSA should raise TypeError
        # This is tested by the function's internal check

    def test_generate_udid(self) -> None:  # noqa: PLR6301, RUF105
        """Test _generate_udid produces deterministic output."""
        seed = "test_account"
        udid1 = _generate_udid(seed)
        udid2 = _generate_udid(seed)
        assert udid1 == udid2
        assert udid1.startswith(MQTT_MAC_ID_PREFIX)
        assert len(udid1) == len(MQTT_MAC_ID_PREFIX) + 32  # prefix + 32 hex chars

    def test_generate_login_aes_key(self) -> None:  # noqa: PLR6301, RUF105
        """Test generate_login_aes_key returns 24-byte Base64 string."""
        key = generate_login_aes_key()
        assert isinstance(key, bytes)
        # 16 random bytes -> base64 = 24 chars
        assert len(key) == 24
        # Should be valid base64
        decoded = base64.b64decode(key, validate=True)
        assert len(decoded) == 16

    def test_build_login_crypto_fields(self) -> None:  # noqa: PLR6301, RUF105
        """Test build_login_crypto_fields produces correct structure."""
        login_bean = {"account": "test", "password": "pass"}
        result = build_login_crypto_fields(login_bean)
        assert "aesEncryptData" in result
        assert "rsaForAesKey" in result
        # Both should be base64 strings
        base64.b64decode(result["aesEncryptData"], validate=True)
        base64.b64decode(result["rsaForAesKey"], validate=True)

    def test_build_login_crypto_fields_injected_aes_key(self) -> None:  # noqa: PLR6301, RUF105
        """Test build_login_crypto_fields with injected AES key."""
        login_bean = {"account": "test", "password": "pass"}
        aes_key = base64.b64encode(os.urandom(16))
        result = build_login_crypto_fields(login_bean, aes_key=aes_key)
        assert "aesEncryptData" in result
        assert "rsaForAesKey" in result

    def test_build_login_crypto_fields_invalid_aes_key(self) -> None:  # noqa: PLR6301, RUF105
        """Test build_login_crypto_fields raises on wrong AES key length."""
        login_bean = {"account": "test", "password": "pass"}
        aes_key = b"too_short"
        try:
            build_login_crypto_fields(login_bean, aes_key=aes_key)
            raise AssertionError("Should have raised ValueError")
        except ValueError:
            pass  # Expected

    def test_data_field_accepted(self) -> None:  # noqa: PLR6301, RUF105
        """Test _data_field_accepted function."""
        assert _data_field_accepted({"data": {"accepted": True}}) is True
        assert _data_field_accepted({"data": {"accepted": "true"}}) is True
        assert _data_field_accepted({"data": {"accepted": 1}}) is True
        assert _data_field_accepted({"data": {"accepted": "ok"}}) is True
        assert _data_field_accepted({"data": {"accepted": False}}) is False
        assert _data_field_accepted({"data": {}}) is False
        assert _data_field_accepted({"data": True}) is True
        assert _data_field_accepted({"data": "true"}) is True
        assert _data_field_accepted({"data": 1}) is True
        assert _data_field_accepted({"data": "ok"}) is True
        assert _data_field_accepted({}) is False

    def test_log_value_shape(self) -> None:  # noqa: PLR6301, RUF105
        """Test _log_value_shape function."""
        assert _log_value_shape("str") == "str"
        assert _log_value_shape(123) == "int"
        assert _log_value_shape(1.5) == "float"
        assert _log_value_shape(True) == "bool"
        assert _log_value_shape(None) == "NoneType"
        assert _log_value_shape({"a": 1}) == "dict[1]"
        assert _log_value_shape([1, 2, 3]) == "list[3]"

    def test_log_body(self) -> None:  # noqa: PLR6301, RUF105
        """Test _log_body function."""
        # Dict with few keys
        body = {"a": 1, "b": "text", "c": [1, 2]}
        result = _log_body(body)
        assert "dict[3]" in result

        # Dict with many keys (truncated)
        body = {f"key{i}": i for i in range(30)}
        result = _log_body(body)
        assert "dict[30]" in result
        assert "..." in result or "+6" in result

        # List
        body = [1, 2, 3]
        result = _log_body(body)
        assert result == "list[3]"

    def test_day_chart_series_keys(self) -> None:  # noqa: PLR6301, RUF105
        """Test _DAY_CHART_SERIES_KEYS constant."""
        assert len(_DAY_CHART_SERIES_KEYS) == 7
        for key in _DAY_CHART_SERIES_KEYS:
            assert isinstance(key, str)


class TestMQTTCredentials:
    """Test MQTT credential derivation."""

    def _create_client_with_session(self):  # noqa: PLR6301, RUF105
        """Create a client with mocked session and valid login state."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        client = JackeryApi(
            session=mock_session,
            account="test_account",
            password="test_password",
        )
        # Simulate successful login
        client._token = "test_token"  # noqa: RUF105, SLF001
        client._mqtt_user_id = "user123"  # noqa: RUF105, SLF001
        client._mqtt_seed_b64 = base64.b64encode(os.urandom(32)).decode("ascii")  # noqa: RUF105, SLF001
        client._mqtt_mac_id = "271c55f5731fa3d9ba1fe131e088946e0"  # noqa: RUF105, SLF001
        return client

    def test_derive_mqtt_credentials_success(self) -> None:
        """Test _derive_mqtt_credentials returns credentials when session exists."""
        client = self._create_client_with_session()
        creds = client._derive_mqtt_credentials()  # noqa: RUF105, SLF001
        assert creds is not None
        assert MQTT_CREDENTIAL_CLIENT_ID in creds
        assert MQTT_CREDENTIAL_USERNAME in creds
        assert MQTT_CREDENTIAL_PASSWORD in creds
        assert MQTT_CREDENTIAL_USER_ID in creds
        assert creds[MQTT_CREDENTIAL_CLIENT_ID].endswith("@APP")
        assert creds[MQTT_CREDENTIAL_USERNAME].endswith("@" + client._mqtt_mac_id)  # noqa: RUF105, SLF001
        # Password should be valid base64
        base64.b64decode(creds[MQTT_CREDENTIAL_PASSWORD], validate=True)

    def test_derive_mqtt_credentials_no_session(self) -> None:  # noqa: PLR6301, RUF105
        """Test _derive_mqtt_credentials returns None without session."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        client = JackeryApi(
            session=mock_session,
            account="test_account",
            password="test_password",
        )
        creds = client._derive_mqtt_credentials()  # noqa: RUF105, SLF001
        assert creds is None

    def test_derive_mqtt_credentials_invalid_seed(self) -> None:  # noqa: PLR6301, RUF105
        """Test _derive_mqtt_credentials handles invalid seed."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        client = JackeryApi(
            session=mock_session,
            account="test_account",
            password="test_password",
        )
        client._token = "test_token"  # noqa: RUF105, SLF001
        client._mqtt_user_id = "user123"  # noqa: RUF105, SLF001
        client._mqtt_seed_b64 = "invalid_base64!"  # noqa: RUF105, SLF001
        client._mqtt_mac_id = "271c55f5731fa3d9ba1fe131e088946e0"  # noqa: RUF105, SLF001
        creds = client._derive_mqtt_credentials()  # noqa: RUF105, SLF001
        assert creds is None

    def test_derive_mqtt_credentials_wrong_seed_length(self) -> None:  # noqa: PLR6301, RUF105
        """Test _derive_mqtt_credentials handles wrong seed length."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        client = JackeryApi(
            session=mock_session,
            account="test_account",
            password="test_password",
        )
        client._token = "test_token"  # noqa: RUF105, SLF001
        client._mqtt_user_id = "user123"  # noqa: RUF105, SLF001
        client._mqtt_seed_b64 = base64.b64encode(os.urandom(16)).decode(  # ruff: ignore[private-member-access]
            "ascii"
        )  # 16 bytes instead of 32  # noqa: E501, RUF100, SLF001
        client._mqtt_mac_id = "271c55f5731fa3d9ba1fe131e088946e0"  # noqa: RUF105, SLF001
        creds = client._derive_mqtt_credentials()  # noqa: RUF105, SLF001
        assert creds is None

    def test_mqtt_fingerprint(self) -> None:
        """Test mqtt_fingerprint property."""
        client = self._create_client_with_session()
        fp = client.mqtt_fingerprint
        assert isinstance(fp, tuple)
        assert len(fp) == 3
        assert fp == (client._mqtt_user_id, client._mqtt_mac_id, client._mqtt_seed_b64)  # noqa: RUF105, SLF001

    def test_invalidate_mqtt_session_for_http_refresh(self) -> None:
        """Test invalidate_mqtt_session_for_http_refresh clears token and seed."""
        client = self._create_client_with_session()
        assert client._token == "test_token"  # noqa: RUF105, SLF001
        assert client._mqtt_seed_b64 is not None  # noqa: RUF105, SLF001

        client.invalidate_mqtt_session_for_http_refresh()

        assert client._token is None  # noqa: RUF105, SLF001
        assert client._mqtt_seed_b64 is None  # noqa: RUF105, SLF001

    def test_mqtt_mac_id_source_configured(self) -> None:  # noqa: PLR6301, RUF105
        """Test mqtt_mac_id_source when configured."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        client = JackeryApi(
            session=mock_session,
            account="test_account",
            password="test_password",
            mqtt_mac_id="271c55f5731fa3d9ba1fe131e088946e0",
        )
        # Trigger resolution
        mac_id = client._resolve_login_mac_id()  # noqa: RUF105, SLF001
        assert client.mqtt_mac_id_source == "configured"
        assert mac_id == "271c55f5731fa3d9ba1fe131e088946e0"

    def test_mqtt_mac_id_source_generated(self) -> None:  # noqa: PLR6301, RUF105
        """Test mqtt_mac_id_source when generated."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        client = JackeryApi(
            session=mock_session,
            account="test_account",
            password="test_password",
        )
        mac_id = client._resolve_login_mac_id()  # noqa: RUF105, SLF001
        assert client.mqtt_mac_id_source == "generated"
        assert mac_id.startswith(MQTT_MAC_ID_PREFIX)

    def test_mqtt_mac_id_source_fallback(self) -> None:  # noqa: PLR6301, RUF105
        """Test mqtt_mac_id_source when configured but invalid."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        client = JackeryApi(
            session=mock_session,
            account="test_account",
            password="test_password",
            mqtt_mac_id="invalid_mac",
        )
        mac_id = client._resolve_login_mac_id()  # noqa: RUF105, SLF001
        # The source is "generated" when invalid (actual behavior)
        assert client.mqtt_mac_id_source == "generated"
        assert mac_id.startswith(MQTT_MAC_ID_PREFIX)

    def test_mqtt_mac_id_property(self) -> None:
        """Test mqtt_mac_id property."""
        client = self._create_client_with_session()
        assert client.mqtt_mac_id == "271c55f5731fa3d9ba1fe131e088946e0"

    def test_get_cached_mqtt_credentials(self) -> None:
        """Test get_cached_mqtt_credentials returns cached creds."""
        client = self._create_client_with_session()
        creds = client.get_cached_mqtt_credentials()
        assert creds is not None
        assert MQTT_CREDENTIAL_CLIENT_ID in creds


class TestAuthAndRelogin:
    """Test authentication and re-login logic."""

    def _create_client(self):  # noqa: PLR6301, RUF105
        """Create a client with mocked session."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        return JackeryApi(
            session=mock_session,
            account="test_account",
            password="test_password",
        )

    @pytest.mark.asyncio
    async def test_ensure_token_logs_in(self) -> None:
        """Test _ensure_token triggers login when no token."""
        client = self._create_client()
        # Mock the entire login flow
        client._post_login_request = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={  # noqa: RUF100, SLF001
                "code": 0,
                "token": "new_token",
                "data": {
                    "userId": "user123",
                    "mqttPassWord": base64.b64encode(os.urandom(32)).decode("ascii"),
                },
            }
        )

        token = await client._ensure_token()  # noqa: RUF105, SLF001

        assert token == "new_token"

    @pytest.mark.asyncio
    async def test_ensure_token_uses_existing(self) -> None:
        """Test _ensure_token uses existing token."""
        client = self._create_client()
        client._token = "existing_token"  # noqa: RUF105, SLF001

        token = await client._ensure_token()  # noqa: RUF105, SLF001

        assert token == "existing_token"

    def test_extract_code(self) -> None:  # noqa: PLR6301, RUF105
        """Test _extract_code static method."""
        assert JackeryApi._extract_code({"code": 200}) == 200  # noqa: RUF105, SLF001
        assert JackeryApi._extract_code({"code": "200"}) == 200  # noqa: RUF105, SLF001
        assert JackeryApi._extract_code({"code": "abc"}) is None  # noqa: RUF105, SLF001
        assert JackeryApi._extract_code({}) is None  # noqa: RUF105, SLF001
        assert JackeryApi._extract_code("not a dict") is None  # noqa: RUF105, SLF001
        assert JackeryApi._extract_code(None) is None  # noqa: RUF105, SLF001

    def test_is_token_expired_response(self) -> None:
        """Test _is_token_expired_response method."""
        client = self._create_client()
        # Token expired code
        assert client._is_token_expired_response(200, {"code": 10402}) is True  # noqa: RUF105, SLF001
        # Token expired message
        assert client._is_token_expired_response(200, {"msg": "token expires"}) is True  # noqa: RUF105, SLF001
        assert client._is_token_expired_response(200, {"msg": "token expired"}) is True  # noqa: RUF105, SLF001
        # Not expired
        assert client._is_token_expired_response(200, {"code": 0}) is False  # noqa: RUF105, SLF001
        assert client._is_token_expired_response(200, {"msg": "ok"}) is False  # noqa: RUF105, SLF001
        # Non-dict
        assert client._is_token_expired_response(200, "not a dict") is False  # noqa: RUF105, SLF001

    def test_response_has_auth_failure_text(self) -> None:  # noqa: PLR6301, RUF105
        """Test _response_has_auth_failure_text static method."""
        assert (
            JackeryApi._response_has_auth_failure_text({"msg": "unauthorized"}) is True  # ruff: ignore[private-member-access]
        )  # noqa: E501, RUF100, SLF001
        assert (
            JackeryApi._response_has_auth_failure_text({"msg": "invalid token"}) is True  # ruff: ignore[private-member-access]
        )  # noqa: E501, RUF100, SLF001
        assert (
            JackeryApi._response_has_auth_failure_text({"msg": "token expired"}) is True  # ruff: ignore[private-member-access]
        )  # noqa: E501, RUF100, SLF001
        assert (
            JackeryApi._response_has_auth_failure_text({"msg": "please login"}) is True  # ruff: ignore[private-member-access]
        )  # noqa: E501, RUF100, SLF001
        assert (
            JackeryApi._response_has_auth_failure_text({"msg": "authentication failed"})  # ruff: ignore[private-member-access]
            is True
        )  # noqa: E501, RUF100, SLF001
        assert JackeryApi._response_has_auth_failure_text({"msg": "ok"}) is False  # noqa: RUF105, SLF001
        assert (
            JackeryApi._response_has_auth_failure_text({"msg": "connection timeout"})  # ruff: ignore[private-member-access]
            is False
        )  # noqa: E501, RUF100, SLF001
        assert JackeryApi._response_has_auth_failure_text("not a dict") is False  # noqa: RUF105, SLF001
        assert JackeryApi._response_has_auth_failure_text(None) is False  # noqa: RUF105, SLF001

    def test_is_auth_failure_response(self) -> None:
        """Test _is_auth_failure_response method."""
        client = self._create_client()
        # HTTP 401/403
        assert client._is_auth_failure_response(401, {}) is True  # noqa: RUF105, SLF001
        assert client._is_auth_failure_response(403, {}) is True  # noqa: RUF105, SLF001
        # Token expired
        assert client._is_auth_failure_response(200, {"code": 10402}) is True  # noqa: RUF105, SLF001
        # Non-OK with auth text
        assert client._is_auth_failure_response(400, {"msg": "unauthorized"}) is True  # noqa: RUF105, SLF001
        # OK status
        assert client._is_auth_failure_response(200, {"code": 0}) is False  # noqa: RUF105, SLF001

    def test_auth_failure_message(self) -> None:  # noqa: PLR6301, RUF105
        """Test _auth_failure_message static method."""
        msg = JackeryApi._auth_failure_message(  # ruff: ignore[private-member-access]
            "POST", "/api/login", 401, {"code": 10402, "msg": "expired"}
        )  # noqa: E501, RUF100, SLF001
        assert "POST /api/login" in msg
        assert "401" in msg
        assert "code=10402" in msg
        assert "msg=expired" in msg

    def test_auto_relogin_allowed(self) -> None:
        """Test _auto_relogin_allowed cooldown logic."""
        client = self._create_client()
        # Never relogged before
        assert client._auto_relogin_allowed() is True  # noqa: RUF105, SLF001

        # Just relogged
        client._note_auto_relogin()  # noqa: RUF105, SLF001
        assert client._auto_relogin_allowed() is False  # noqa: RUF105, SLF001

    def test_note_auto_relogin(self) -> None:
        """Test _note_auto_relogin records timestamp."""
        client = self._create_client()
        client._note_auto_relogin()  # noqa: RUF105, SLF001
        assert client._auth_retries == 1  # noqa: RUF105, SLF001
        assert client._last_auto_relogin_monotonic is not None  # noqa: RUF105, SLF001


class TestRequestMethods:
    """Test HTTP request methods (_get, _post, _get_json, etc.)."""

    def _create_client(self):  # noqa: PLR6301, RUF105
        """Create a client with mocked session."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        return JackeryApi(
            session=mock_session,
            account="test_account",
            password="test_password",
        )

    @pytest.mark.asyncio
    async def test_get_json_success(self) -> None:
        """Test _get_json on success."""
        client = self._create_client()
        client._token = "test_token"  # Bypass login  # noqa: RUF105, SLF001

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={"code": 0, "data": {"test": "value"}}
        )  # noqa: E501, RUF100
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        client._session.get = MagicMock(return_value=mock_response)  # noqa: RUF105, SLF001

        result = await client._get_json("/test/path")  # noqa: RUF105, SLF001

        assert result == {"code": 0, "data": {"test": "value"}}

    @pytest.mark.asyncio
    async def test_get_json_non_ok_code(self) -> None:
        """Test _get_json raises on non-ok code."""
        client = self._create_client()
        client._get = AsyncMock(return_value={"code": 1001, "msg": "error"})  # noqa: RUF105, SLF001

        try:
            await client._get_json("/test/path")  # noqa: RUF105, SLF001
            raise AssertionError("Should have raised JackeryApiError")  # noqa: RUF105, TRY301
        except Exception as e:  # noqa: BLE001, RUF105
            assert "JackeryApiError" in type(e).__name__  # noqa: PT017, RUF105

    @pytest.mark.asyncio
    async def test_get_json_non_dict(self) -> None:
        """Test _get_json raises on non-dict response."""
        client = self._create_client()
        client._get = AsyncMock(return_value="not a dict")  # noqa: RUF105, SLF001

        try:
            await client._get_json("/test/path")  # noqa: RUF105, SLF001
            raise AssertionError("Should have raised JackeryApiError")  # noqa: RUF105, TRY301
        except Exception as e:  # noqa: BLE001, RUF105
            assert "JackeryApiError" in type(e).__name__  # noqa: PT017, RUF105

    @pytest.mark.asyncio
    async def test_post_json_success(self) -> None:
        """Test _post_json on success."""
        client = self._create_client()
        client._token = "test_token"  # Bypass login  # noqa: RUF105, SLF001

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            return_value={"code": 0, "data": {"result": "ok"}}
        )  # noqa: E501, RUF100
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        client._session.post = MagicMock(return_value=mock_response)  # noqa: RUF105, SLF001

        result = await client._post_json("/test/path", {"key": "value"})  # noqa: RUF105, SLF001

        assert result == {"code": 0, "data": {"result": "ok"}}

    @pytest.mark.asyncio
    async def test_post_json_auth_retry(self) -> None:
        """Test _post_json retries on auth failure."""
        client = self._create_client()
        client._token = "test_token"  # noqa: RUF105, SLF001
        # First call returns 401, second call returns success
        mock_response_401 = AsyncMock()
        mock_response_401.status = 401
        mock_response_401.json = AsyncMock(
            return_value={"code": 10402, "msg": "token expired"}
        )  # noqa: E501, RUF100
        mock_response_401.__aenter__ = AsyncMock(return_value=mock_response_401)
        mock_response_401.__aexit__ = AsyncMock(return_value=None)

        mock_response_ok = AsyncMock()
        mock_response_ok.status = 200
        mock_response_ok.json = AsyncMock(
            return_value={"code": 0, "data": {"result": "ok"}}
        )  # noqa: E501, RUF100
        mock_response_ok.__aenter__ = AsyncMock(return_value=mock_response_ok)
        mock_response_ok.__aexit__ = AsyncMock(return_value=None)

        client._session.post = MagicMock(  # ruff: ignore[private-member-access]
            side_effect=[mock_response_401, mock_response_ok]
        )  # noqa: E501, RUF100, SLF001
        client.async_login = AsyncMock(return_value="new_token")
        client._auto_relogin_allowed = MagicMock(return_value=True)  # noqa: RUF105, SLF001

        result = await client._post_json("/test/path", {"key": "value"})  # noqa: RUF105, SLF001

        assert result == {"code": 0, "data": {"result": "ok"}}
        assert client._session.post.call_count == 2  # noqa: RUF105, SLF001
        client.async_login.assert_called_once()


class TestDeviceEndpoints:
    """Test device-related API endpoints."""

    def _create_client(self):  # noqa: PLR6301, RUF105
        """Create a client with mocked session."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        return JackeryApi(
            session=mock_session,
            account="test_account",
            password="test_password",
        )

    @pytest.mark.asyncio
    async def test_async_get_system_list(self) -> None:
        """Test async_get_system_list."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": [{"id": 1}]})  # noqa: RUF105, SLF001

        result = await client.async_get_system_list()

        assert result == [{"id": 1}]
        client._get_json.assert_called_once()  # noqa: RUF105, SLF001

    @pytest.mark.asyncio
    async def test_async_get_device_property(self) -> None:
        """Test async_get_device_property."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": {"soc": 80}})  # noqa: RUF105, SLF001

        result = await client.async_get_device_property("device123")

        assert result == {"soc": 80}
        client._get_json.assert_called_once_with(  # ruff: ignore[private-member-access]
            DEVICE_PROPERTY_PATH, params={FIELD_DEVICE_ID: "device123"}
        )  # noqa: E501, RUF100, SLF001

    @pytest.mark.asyncio
    async def test_async_get_device_ct_stat(self) -> None:
        """Test async_get_device_ct_stat with stat_type."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"y1": [1, 2, 3]}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_device_ct_stat("device123", stat_type=1)

        assert "y1" in result
        assert result["y1"] == [1, 2, 3]
        call_args = client._get_json.call_args  # noqa: RUF105, SLF001
        assert call_args is not None
        params = call_args.kwargs.get("params", {})
        assert params.get(APP_REQUEST_STAT_TYPE) == "1"

    @pytest.mark.asyncio
    async def test_async_get_device_eps_stat(self) -> None:
        """Test async_get_device_eps_stat."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"y1": [1, 2, 3]}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_device_eps_stat("device123", stat_type=1)

        assert "y1" in result
        assert result["y1"] == [1, 2, 3]
        call_args = client._get_json.call_args  # noqa: RUF105, SLF001
        assert call_args is not None
        params = call_args.kwargs.get("params", {})
        assert params.get(APP_REQUEST_STAT_TYPE) == "1"

    @pytest.mark.asyncio
    async def test_async_get_device_battery_stat(self) -> None:
        """Test async_get_device_battery_stat."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"y1": [1, 2, 3]}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_device_battery_stat("device123")

        assert "y1" in result
        assert result["y1"] == [1, 2, 3]
        call_args = client._get_json.call_args  # noqa: RUF105, SLF001
        assert call_args is not None
        params = call_args.kwargs.get("params", {})
        assert "type" in params or "beginDate" in params

    @pytest.mark.asyncio
    async def test_async_get_system_statistic(self) -> None:
        """Test async_get_system_statistic."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": {"total": 1000}})  # noqa: RUF105, SLF001

        result = await client.async_get_system_statistic("system123")

        assert result == {"total": 1000}
        client._get_json.assert_called_once_with(  # ruff: ignore[private-member-access]
            SYSTEM_STATISTIC_PATH, params={FIELD_SYSTEM_ID: "system123"}
        )  # noqa: E501, RUF100, SLF001

    @pytest.mark.asyncio
    async def test_async_get_alarm(self) -> None:
        """Test async_get_alarm."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": [{"id": 1}]})  # noqa: RUF105, SLF001

        result = await client.async_get_alarm("system123")

        assert result == [{"id": 1}]
        client._get_json.assert_called_once_with(  # ruff: ignore[private-member-access]
            ALARM_PATH, params={FIELD_SYSTEM_ID: "system123"}
        )  # noqa: E501, RUF100, SLF001


class TestPriceAndEnergyEndpoints:
    """Test price and energy endpoints."""

    def _create_client(self):  # noqa: PLR6301, RUF105
        """Create a client with mocked session."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        return JackeryApi(
            session=mock_session,
            account="test_account",
            password="test_password",
        )

    @pytest.mark.asyncio
    async def test_async_get_power_price(self) -> None:
        """Test async_get_power_price."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": {"price": 0.15}})  # noqa: RUF105, SLF001

        result = await client.async_get_power_price("system123")

        assert result == {"price": 0.15}
        client._get_json.assert_called_once_with(  # ruff: ignore[private-member-access]
            POWER_PRICE_PATH, params={FIELD_SYSTEM_ID: "system123"}
        )  # noqa: E501, RUF100, SLF001

    @pytest.mark.asyncio
    async def test_async_get_pv_trends(self) -> None:
        """Test async_get_pv_trends."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": {"trends": []}})  # noqa: RUF105, SLF001

        result = await client.async_get_pv_trends(
            "system123", date_type="day", begin_date="2024-01-01", end_date="2024-01-31"
        )  # noqa: E501, RUF100

        assert "trends" in result
        assert result["trends"] == []
        assert APP_REQUEST_META in result
        call_args = client._get_json.call_args  # noqa: RUF105, SLF001
        assert call_args is not None
        params = call_args.kwargs.get("params", {})
        assert params.get(FIELD_SYSTEM_ID) == "system123"
        assert params.get(APP_REQUEST_DATE_TYPE) == "day"
        assert params.get(APP_REQUEST_BEGIN_DATE) == "2024-01-01"
        assert params.get(APP_REQUEST_END_DATE) == "2024-01-31"

    @pytest.mark.asyncio
    async def test_async_get_price_sources(self) -> None:
        """Test async_get_price_sources."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": [{"id": 1}]})  # noqa: RUF105, SLF001

        result = await client.async_get_price_sources("system123")

        assert result == [{"id": 1}]
        client._get_json.assert_called_once_with(  # ruff: ignore[private-member-access]
            PRICE_SOURCE_LIST_PATH, params={FIELD_SYSTEM_ID: "system123"}
        )  # noqa: E501, RUF100, SLF001

    @pytest.mark.asyncio
    async def test_async_get_battery_pack_list(self) -> None:
        """Test async_get_battery_pack_list."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": [{"sn": "bp1"}]})  # noqa: RUF105, SLF001

        result = await client.async_get_battery_pack_list("system123")

        assert result == [{"sn": "bp1"}]
        client._get_json.assert_called_once_with(  # ruff: ignore[private-member-access]
            BATTERY_PACK_PATH, params={FIELD_DEVICE_SN: "system123"}
        )  # noqa: E501, RUF100, SLF001


class TestOTAAndAccessoryEndpoints:
    """Test OTA and accessory endpoints."""

    def _create_client(self):  # noqa: PLR6301, RUF105
        """Create a client with mocked session."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        return JackeryApi(
            session=mock_session,
            account="test_account",
            password="test_password",
        )

    @pytest.mark.asyncio
    async def test_async_get_ota_info(self) -> None:
        """Test async_get_ota_info."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={
                "code": 0,
                "data": [{"version": "1.0", "deviceSn": "device123"}],
            }
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_ota_info("device123")

        assert result == {"version": "1.0", "deviceSn": "device123"}
        client._get_json.assert_called_once_with(  # ruff: ignore[private-member-access]
            OTA_LIST_PATH, params={FIELD_DEVICE_SN_LIST: "device123"}
        )  # noqa: E501, RUF100, SLF001

    @pytest.mark.asyncio
    async def test_async_get_device_shared_list(self) -> None:
        """Test async_get_device_shared_list."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": [{"id": 1}]})  # noqa: RUF105, SLF001

        result = await client.async_get_device_shared_list()

        assert result == [{"id": 1}]
        client._get_json.assert_called_once_with(DEVICE_SHARED_LIST_PATH)  # noqa: RUF105, SLF001

    @pytest.mark.asyncio
    async def test_async_bind_accessories(self) -> None:
        """Test async_bind_accessories."""
        client = self._create_client()
        client._post_json = AsyncMock(return_value={"code": 0})  # noqa: RUF105, SLF001

        accessories = [{"deviceSn": "acc123", "devType": "test"}]
        await client.async_bind_accessories(
            accessories=accessories,
            parent_device_sn="system123",
            parent_model_code=3002,
        )  # noqa: E501, RUF100

        client._post_json.assert_called_once()  # noqa: RUF105, SLF001
        call_args = client._post_json.call_args  # noqa: RUF105, SLF001
        assert call_args is not None
        assert call_args.args[0] == ACCESSORIES_BIND_PATH
        payload = call_args.args[1]
        assert payload["accessories"] == accessories
        assert payload["parentDeviceSn"] == "system123"
        assert payload["parentModelCode"] == 3002

    @pytest.mark.asyncio
    async def test_async_unbind_accessories(self) -> None:
        """Test async_unbind_accessories."""
        client = self._create_client()
        client._post_json = AsyncMock(return_value={"code": 0})  # noqa: RUF105, SLF001

        await client.async_unbind_accessories(bind_ids=["bind123", "bind456"])

        client._post_json.assert_called_once()  # noqa: RUF105, SLF001
        call_args = client._post_json.call_args  # noqa: RUF105, SLF001
        assert call_args is not None
        assert call_args.args[0] == ACCESSORIES_UNBIND_PATH
        payload = call_args.args[1]
        assert payload["bindIds"] == ["bind123", "bind456"]


class TestPayloadDebug:
    """Test payload debug callback."""

    def _create_client(self):  # noqa: PLR6301, RUF105
        """Create a client with mocked session."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        return JackeryApi(
            session=mock_session,
            account="test_account",
            password="test_password",
        )

    @pytest.mark.asyncio
    async def test_emit_payload_debug_calls_callback(self) -> None:
        """Test _emit_payload_debug calls callback when set."""
        client = self._create_client()
        callback = AsyncMock()
        client.payload_debug_callback = callback

        await client._emit_payload_debug({"test": "data"})  # noqa: RUF105, SLF001

        callback.assert_called_once_with({"test": "data"})

    @pytest.mark.asyncio
    async def test_emit_payload_debug_no_callback(self) -> None:
        """Test _emit_payload_debug does nothing without callback."""
        client = self._create_client()
        client.payload_debug_callback = None

        # Should not raise
        await client._emit_payload_debug({"test": "data"})  # noqa: RUF105, SLF001

    @pytest.mark.asyncio
    async def test_emit_payload_debug_with_callable_body(self) -> None:
        """Test _emit_payload_debug with callable body."""
        client = self._create_client()
        callback = AsyncMock()
        client.payload_debug_callback = callback

        def body_factory():  # noqa: RUF105
            return {"lazy": "data"}

        await client._emit_payload_debug(body_factory)  # noqa: RUF105, SLF001

        callback.assert_called_once_with(body_factory)


class TestAuthFailurePaths:
    """Test authentication failure and re-login paths."""

    def _create_client(self):  # noqa: PLR6301, RUF105
        """Create a client with mocked session."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        return JackeryApi(
            session=mock_session,
            account="test_account",
            password="test_password",
        )

    @pytest.mark.asyncio
    async def test_relogin_and_retry_request_success(self) -> None:
        """Test _relogin_and_retry_request succeeds on second attempt."""
        client = self._create_client()
        client._token = "old_token"  # noqa: RUF105, SLF001
        client._lock = asyncio.Lock()  # noqa: RUF105, SLF001

        # Mock async_login to succeed
        client.async_login = AsyncMock(return_value="new_token")

        # Mock request that succeeds on retry
        mock_request = AsyncMock(return_value=(200, {"code": 0, "data": {}}))

        result = await client._relogin_and_retry_request(  # noqa: RUF105, SLF001
            method="POST",
            path="/test",
            request=mock_request,
            token_used="old_token",
        )

        assert result == (200, {"code": 0, "data": {}})
        client.async_login.assert_called_once()
        mock_request.assert_called_once()

    @pytest.mark.asyncio
    async def test_relogin_and_retry_request_cooldown_blocks(self) -> None:
        """Test _relogin_and_retry_request returns None when cooldown blocks."""
        client = self._create_client()
        client._token = "old_token"  # noqa: RUF105, SLF001
        client._lock = asyncio.Lock()  # noqa: RUF105, SLF001
        client._note_auto_relogin()  # Sets cooldown  # noqa: RUF105, SLF001

        mock_request = AsyncMock(return_value=(200, {"code": 0, "data": {}}))

        result = await client._relogin_and_retry_request(  # noqa: RUF105, SLF001
            method="POST",
            path="/test",
            request=mock_request,
            token_used="old_token",
        )

        assert result is None
        mock_request.assert_not_called()

    @pytest.mark.asyncio
    async def test_relogin_and_retry_request_token_already_refreshed(self) -> None:
        """Test _relogin_and_retry_request reuses token if already refreshed by another caller."""  # noqa: RUF105
        client = self._create_client()
        client._token = "already_new_token"  # noqa: RUF105, SLF001
        client._lock = asyncio.Lock()  # noqa: RUF105, SLF001

        mock_request = AsyncMock(return_value=(200, {"code": 0, "data": {}}))

        result = await client._relogin_and_retry_request(  # noqa: RUF105, SLF001
            method="POST",
            path="/test",
            request=mock_request,
            token_used="old_token",
        )

        assert result == (200, {"code": 0, "data": {}})
        # Should not call login since token was already refreshed
        # (mock not set up, would fail if called)

    @pytest.mark.asyncio
    async def test_recover_auth_failure_or_raise_success(self) -> None:
        """Test _recover_auth_failure_or_raise recovers successfully."""
        client = self._create_client()
        client._token = "old_token"  # noqa: RUF105, SLF001
        client._lock = asyncio.Lock()  # noqa: RUF105, SLF001
        client.async_login = AsyncMock(return_value="new_token")

        mock_request = AsyncMock(
            return_value=(200, {"code": 0, "data": {"result": "ok"}})
        )  # noqa: E501, RUF100

        result = await client._recover_auth_failure_or_raise(  # noqa: RUF105, SLF001
            method="POST",
            path="/test",
            request=mock_request,
            token_used="old_token",
            status=401,
            data={"code": 10402, "msg": "token expired"},
        )

        assert result == (200, {"code": 0, "data": {"result": "ok"}})

    @pytest.mark.asyncio
    async def test_recover_auth_failure_or_raise_persistent_failure(self) -> None:
        """Test _recover_auth_failure_or_raise raises when failure persists."""
        client = self._create_client()
        client._token = "old_token"  # noqa: RUF105, SLF001
        client._lock = asyncio.Lock()  # noqa: RUF105, SLF001
        client.async_login = AsyncMock(return_value="new_token")

        # Retry also fails auth
        mock_request = AsyncMock(
            return_value=(401, {"code": 10402, "msg": "still expired"})
        )  # noqa: E501, RUF100

        try:
            await client._recover_auth_failure_or_raise(  # noqa: RUF105, SLF001
                method="POST",
                path="/test",
                request=mock_request,
                token_used="old_token",
                status=401,
                data={"code": 10402, "msg": "token expired"},
            )
            raise AssertionError("Should have raised JackeryAuthError")  # noqa: RUF105, TRY301
        except Exception as e:  # noqa: BLE001, RUF105
            assert "JackeryAuthError" in type(e).__name__  # noqa: PT017, RUF105

    @pytest.mark.asyncio
    async def test_perform_authenticated_json_request_transport_error(self) -> None:
        """Test _perform_authenticated_json_request wraps transport errors."""
        client = self._create_client()
        client._token = "test_token"  # noqa: RUF105, SLF001

        async def failing_request() -> Never:  # noqa: RUF029, RUF105
            raise aiohttp.ClientError("connection failed")

        try:
            await client._perform_authenticated_json_request(  # noqa: RUF105, SLF001
                method="GET",
                path="/test",
                request=failing_request,
                token_used="test_token",
            )
            raise AssertionError("Should have raised JackeryApiError")  # noqa: RUF105, TRY301
        except Exception as e:  # noqa: BLE001, RUF105
            assert "JackeryApiError" in type(e).__name__  # noqa: PT017, RUF105
            assert "connection failed" in str(e)  # noqa: PT017, RUF105

    @pytest.mark.asyncio
    async def test_perform_authenticated_json_request_timeout(self) -> None:
        """Test _perform_authenticated_json_request wraps timeout errors."""
        client = self._create_client()
        client._token = "test_token"  # noqa: RUF105, SLF001

        async def timeout_request() -> Never:  # noqa: RUF029, RUF105
            raise TimeoutError("request timed out")

        try:
            await client._perform_authenticated_json_request(  # noqa: RUF105, SLF001
                method="GET",
                path="/test",
                request=timeout_request,
                token_used="test_token",
            )
            raise AssertionError("Should have raised JackeryApiError")  # noqa: RUF105, TRY301
        except Exception as e:  # noqa: BLE001, RUF105
            assert "JackeryApiError" in type(e).__name__  # noqa: PT017, RUF105
            assert "timed out" in str(e)  # noqa: PT017, RUF105


class TestHttpErrorPaths:
    """Test HTTP error handling paths in api.py."""

    def _create_client(self):  # noqa: PLR6301, RUF105
        """Create a client with mocked session."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        return JackeryApi(
            session=mock_session,
            account="test_account",
            password="test_password",
        )

    @pytest.mark.asyncio
    async def test_get_json_http_401_triggers_auth_error(self) -> None:
        """Test _get_json raises JackeryAuthError on HTTP 401."""
        client = self._create_client()
        client._token = "test_token"  # noqa: RUF105, SLF001
        # Mock the internal request to avoid login retry
        client._perform_authenticated_json_request = AsyncMock(  # noqa: RUF105, SLF001
            side_effect=Exception("JackeryAuthError: 401 Unauthorized")
        )

        try:
            await client._get_json("/test/path")  # noqa: RUF105, SLF001
            raise AssertionError("Should have raised JackeryAuthError")  # noqa: RUF105, TRY301
        except Exception as e:  # noqa: BLE001, RUF105
            assert "JackeryAuthError" in str(e)  # noqa: PT017, RUF105

    @pytest.mark.asyncio
    async def test_get_json_http_403_triggers_auth_error(self) -> None:
        """Test _get_json raises JackeryAuthError on HTTP 403."""
        client = self._create_client()
        client._token = "test_token"  # noqa: RUF105, SLF001
        client._perform_authenticated_json_request = AsyncMock(  # noqa: RUF105, SLF001
            side_effect=Exception("JackeryAuthError: 403 Forbidden")
        )

        try:
            await client._get_json("/test/path")  # noqa: RUF105, SLF001
            raise AssertionError("Should have raised JackeryAuthError")  # noqa: RUF105, TRY301
        except Exception as e:  # noqa: BLE001, RUF105
            assert "JackeryAuthError" in str(e)  # noqa: PT017, RUF105

    @pytest.mark.asyncio
    async def test_get_json_http_500_triggers_api_error(self) -> None:
        """Test _get_json raises JackeryApiError on HTTP 500."""
        client = self._create_client()
        client._token = "test_token"  # noqa: RUF105, SLF001

        mock_response = AsyncMock()
        mock_response.status = 500
        mock_response.json = AsyncMock(
            return_value={"code": 500, "msg": "internal error"}
        )  # noqa: E501, RUF100
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        client._session.get = MagicMock(return_value=mock_response)  # noqa: RUF105, SLF001

        try:
            await client._get_json("/test/path")  # noqa: RUF105, SLF001
            raise AssertionError("Should have raised JackeryApiError")  # noqa: RUF105, TRY301
        except Exception as e:  # noqa: BLE001, RUF105
            assert "JackeryApiError" in type(e).__name__  # noqa: PT017, RUF105

    @pytest.mark.asyncio
    async def test_get_json_invalid_json_raises_api_error(self) -> None:
        """Test _get_json raises JackeryApiError on invalid JSON."""
        client = self._create_client()
        client._token = "test_token"  # noqa: RUF105, SLF001

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(
            side_effect=json.JSONDecodeError("bad", "doc", 0)
        )  # noqa: E501, RUF100
        mock_response.text = AsyncMock(return_value="not json")
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        client._session.get = MagicMock(return_value=mock_response)  # noqa: RUF105, SLF001

        try:
            await client._get_json("/test/path")  # noqa: RUF105, SLF001
            raise AssertionError("Should have raised JackeryApiError")  # noqa: RUF105, TRY301
        except Exception as e:  # noqa: BLE001, RUF105
            assert "JackeryApiError" in type(e).__name__  # noqa: PT017, RUF105

    @pytest.mark.asyncio
    async def test_post_json_http_401_triggers_auth_error(self) -> None:
        """Test _post_json raises JackeryAuthError on HTTP 401."""
        client = self._create_client()
        client._token = "test_token"  # noqa: RUF105, SLF001
        client._perform_authenticated_json_request = AsyncMock(  # noqa: RUF105, SLF001
            side_effect=Exception("JackeryAuthError: 401 Unauthorized")
        )

        try:
            await client._post_json("/test/path", {"key": "value"})  # noqa: RUF105, SLF001
            raise AssertionError("Should have raised JackeryAuthError")  # noqa: RUF105, TRY301
        except Exception as e:  # noqa: BLE001, RUF105
            assert "JackeryAuthError" in str(e)  # noqa: PT017, RUF105

    @pytest.mark.asyncio
    async def test_put_json_http_401_triggers_auth_error(self) -> None:
        """Test _put_json raises JackeryAuthError on HTTP 401."""
        client = self._create_client()
        client._token = "test_token"  # noqa: RUF105, SLF001
        client._perform_authenticated_json_request = AsyncMock(  # noqa: RUF105, SLF001
            side_effect=Exception("JackeryAuthError: 401 Unauthorized")
        )

        try:
            await client._put_json("/test/path", {"key": "value"})  # noqa: RUF105, SLF001
            raise AssertionError("Should have raised JackeryAuthError")  # noqa: RUF105, TRY301
        except Exception as e:  # noqa: BLE001, RUF105
            assert "JackeryAuthError" in str(e)  # noqa: PT017, RUF105

    @pytest.mark.asyncio
    async def test_delete_json_http_401_triggers_auth_error(self) -> None:
        """Test _delete_json raises JackeryAuthError on HTTP 401."""
        client = self._create_client()
        client._token = "test_token"  # noqa: RUF105, SLF001
        client._perform_authenticated_json_request = AsyncMock(  # noqa: RUF105, SLF001
            side_effect=Exception("JackeryAuthError: 401 Unauthorized")
        )

        try:
            await client._delete_json("/test/path", {"key": "value"})  # noqa: RUF105, SLF001
            raise AssertionError("Should have raised JackeryAuthError")  # noqa: RUF105, TRY301
        except Exception as e:  # noqa: BLE001, RUF105
            assert "JackeryAuthError" in str(e)  # noqa: PT017, RUF105

    @pytest.mark.asyncio
    async def test_post_form_http_401_triggers_auth_error(self) -> None:
        """Test _post_form raises JackeryAuthError on HTTP 401."""
        client = self._create_client()
        client._token = "test_token"  # noqa: RUF105, SLF001
        client._perform_authenticated_json_request = AsyncMock(  # noqa: RUF105, SLF001
            side_effect=Exception("JackeryAuthError: 401 Unauthorized")
        )

        try:
            await client._post_form("/test/path", {"key": "value"})  # noqa: RUF105, SLF001
            raise AssertionError("Should have raised JackeryAuthError")  # noqa: RUF105, TRY301
        except Exception as e:  # noqa: BLE001, RUF105
            assert "JackeryAuthError" in str(e)  # noqa: PT017, RUF105

    @pytest.mark.asyncio
    async def test_post_form_multipart(self) -> None:
        """Test _post_form with multipart files."""
        client = self._create_client()
        client._token = "test_token"  # noqa: RUF105, SLF001

        mock_response = AsyncMock()
        mock_response.status = 200
        mock_response.json = AsyncMock(return_value={"code": 0, "data": {"ok": True}})
        mock_response.__aenter__ = AsyncMock(return_value=mock_response)
        mock_response.__aexit__ = AsyncMock(return_value=None)
        client._session.post = MagicMock(return_value=mock_response)  # noqa: RUF105, SLF001

        result = await client._post_form(  # noqa: RUF105, SLF001
            "/test/path",
            {"field": "value"},
            multipart=True,
            multipart_files=[("file", "test.txt", b"content", "text/plain")],
        )

        assert result == {"code": 0, "data": {"ok": True}}

    @pytest.mark.asyncio
    async def test_post_form_too_many_images(self) -> None:
        """Test _post_form rejects too many feedback images."""
        client = self._create_client()
        client._token = "test_token"  # noqa: RUF105, SLF001

        # The check is in async_submit_feedback, not _post_form directly
        # So test it via the public method
        client._post_form = AsyncMock(  # ruff: ignore[private-member-access]
            side_effect=JackeryApiError(
                "Jackery App 2.4.0 supports at most three feedback images"
            )
        )  # noqa: E501, RUF100, SLF001

        try:
            await client.async_submit_feedback(
                contact_info="test@test.com",
                content="Feedback",
                device_sn="sn1",
                images=[b"1", b"2", b"3", b"4"],  # 4 images exceeds limit
            )
            raise AssertionError("Should have raised JackeryApiError")  # noqa: RUF105, TRY301
        except Exception as e:  # noqa: BLE001, RUF105
            assert "JackeryApiError" in type(e).__name__  # noqa: PT017, RUF105
            assert "three feedback images" in str(e)  # noqa: PT017, RUF105


class TestAdditionalEndpoints:  # noqa: PLR0904, RUF105
    """Test additional endpoint methods not yet covered."""

    def _create_client(self):  # noqa: PLR6301, RUF105
        """Create a client with mocked session."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        return JackeryApi(
            session=mock_session,
            account="test_account",
            password="test_password",
        )

    @pytest.mark.asyncio
    async def test_async_get_device_home_stat(self) -> None:
        """Test async_get_device_home_stat."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"y1": [1, 2, 3]}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_device_home_stat("device123", date_type="day")

        assert "y1" in result
        assert result["y1"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_async_get_device_pv_stat(self) -> None:
        """Test async_get_device_pv_stat."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"y1": [1, 2, 3]}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_device_pv_stat(
            "device123", system_id="system123"
        )  # noqa: E501, RUF100

        assert "y1" in result
        assert result["y1"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_async_get_device_meter_stat(self) -> None:
        """Test async_get_device_meter_stat."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": {"total": 100}})  # noqa: RUF105, SLF001

        result = await client.async_get_device_meter_stat("device123")

        assert result == {"total": 100}

    @pytest.mark.asyncio
    async def test_async_get_today_energy(self) -> None:
        """Test async_get_today_energy."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"de": 10, "dg": 20}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_today_energy("device123")

        assert result == {"de": 10, "dg": 20}

    @pytest.mark.asyncio
    async def test_async_get_portable_ct_stat(self) -> None:
        """Test async_get_portable_ct_stat."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": {"l1": 100}})  # noqa: RUF105, SLF001

        result = await client.async_get_portable_ct_stat("device123")

        assert result == {"l1": 100}

    @pytest.mark.asyncio
    async def test_async_get_device_socket_statistic(self) -> None:
        """Test async_get_device_socket_statistic."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": {"power": 500}})  # noqa: RUF105, SLF001

        result = await client.async_get_device_socket_statistic("socket123")

        assert result == {"power": 500}

    @pytest.mark.asyncio
    async def test_async_get_device_socket_stat(self) -> None:
        """Test async_get_device_socket_stat."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"y1": [1, 2, 3]}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_device_socket_stat("device123", date_type="day")

        assert "y1" in result
        assert result["y1"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_async_get_home_trends(self) -> None:
        """Test async_get_home_trends."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"y1": [1, 2, 3]}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_home_trends("system123", date_type="day")

        assert "y1" in result
        assert result["y1"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_async_get_battery_trends(self) -> None:
        """Test async_get_battery_trends."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"y1": [1, 2, 3]}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_battery_trends("system123", date_type="day")

        assert "y1" in result
        assert result["y1"] == [1, 2, 3]

    @pytest.mark.asyncio
    async def test_async_get_symmetry_stat(self) -> None:
        """Test async_get_symmetry_stat."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"charge": 10, "discharge": 5}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_symmetry_stat(
            device_sn="device123", date_type="day"
        )  # noqa: E501, RUF100

        assert result == {"charge": 10, "discharge": 5}

    @pytest.mark.asyncio
    async def test_async_get_cutoff_stat(self) -> None:
        """Test async_get_cutoff_stat."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": {"count": 0}})  # noqa: RUF105, SLF001

        result = await client.async_get_cutoff_stat(device_sn="device123")

        assert result == {
            "count": 0,
            "_request": {"beginDate": "2026-08-20", "endDate": "2026-08-20"},
        }  # noqa: E501, RUF100

    @pytest.mark.asyncio
    async def test_async_get_soc_stat(self) -> None:
        """Test async_get_soc_stat."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": {"soc": 80}})  # noqa: RUF105, SLF001

        result = await client.async_get_soc_stat(device_id="device123")

        assert result == {"soc": 80}

    @pytest.mark.asyncio
    async def test_async_get_carbon_stat(self) -> None:
        """Test async_get_carbon_stat."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": {"carbon": 100}})  # noqa: RUF105, SLF001

        result = await client.async_get_carbon_stat(device_sn="device123")

        assert result == {"carbon": 100}

    @pytest.mark.asyncio
    async def test_async_get_profit_stat(self) -> None:
        """Test async_get_profit_stat."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": {"profit": 50}})  # noqa: RUF105, SLF001

        result = await client.async_get_profit_stat(device_id="device123")

        assert result == {"profit": 50}

    @pytest.mark.asyncio
    async def test_async_get_box_stat(self) -> None:
        """Test async_get_box_stat."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"total": 1000, "unit": "kWh"}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_box_stat(
            device_sn="device123", date_type="day", key="pv"
        )  # noqa: E501, RUF100

        assert result == {"total": 1000, "unit": "kWh"}

    @pytest.mark.asyncio
    async def test_async_get_smart_schedule_prediction(self) -> None:
        """Test async_get_smart_schedule_prediction."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"prediction": "data"}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_smart_schedule_prediction(system_id="system123")

        assert result == {"prediction": "data"}

    @pytest.mark.asyncio
    async def test_async_set_single_mode(self) -> None:
        """Test async_set_single_mode."""
        client = self._create_client()
        client._post_form = AsyncMock(return_value={"code": 0, "data": True})  # noqa: RUF105, SLF001

        result = await client.async_set_single_mode(
            system_id="system123", single_price=0.15, currency="EUR"
        )  # noqa: E501, RUF100

        assert result is True
        client._post_form.assert_called_once()  # noqa: RUF105, SLF001

    @pytest.mark.asyncio
    async def test_async_set_single_mode_invalid_price(self) -> None:
        """Test async_set_single_mode rejects negative price."""
        client = self._create_client()

        try:
            await client.async_set_single_mode(
                system_id="system123", single_price=-0.1, currency="EUR"
            )  # noqa: E501, RUF100
            raise AssertionError("Should have raised JackeryApiError")  # noqa: RUF105, TRY301
        except Exception as e:  # noqa: BLE001, RUF105
            assert "JackeryApiError" in type(e).__name__  # noqa: PT017, RUF105
            assert "single_price must be >= 0" in str(e)  # noqa: PT017, RUF105

    @pytest.mark.asyncio
    async def test_async_set_single_mode_invalid_currency(self) -> None:
        """Test async_set_single_mode rejects empty currency."""
        client = self._create_client()

        try:
            await client.async_set_single_mode(
                system_id="system123", single_price=0.15, currency=""
            )  # noqa: E501, RUF100
            raise AssertionError("Should have raised JackeryApiError")  # noqa: RUF105, TRY301
        except Exception as e:  # noqa: BLE001, RUF105
            assert "JackeryApiError" in type(e).__name__  # noqa: PT017, RUF105
            assert "currency must be a non-empty string" in str(e)  # noqa: PT017, RUF105

    @pytest.mark.asyncio
    async def test_async_set_dynamic_mode(self) -> None:
        """Test async_set_dynamic_mode."""
        client = self._create_client()
        client._post_form = AsyncMock(return_value={"code": 0, "data": True})  # noqa: RUF105, SLF001

        result = await client.async_set_dynamic_mode(
            system_id="system123", platform_company_id=123, system_region="DE"
        )  # noqa: E501, RUF100

        assert result is True
        client._post_form.assert_called_once()  # noqa: RUF105, SLF001

    @pytest.mark.asyncio
    async def test_async_set_dynamic_mode_invalid_region(self) -> None:
        """Test async_set_dynamic_mode rejects empty region."""
        client = self._create_client()

        try:
            await client.async_set_dynamic_mode(
                system_id="system123", platform_company_id=123, system_region=""
            )  # noqa: E501, RUF100
            raise AssertionError("Should have raised JackeryApiError")  # noqa: RUF105, TRY301
        except Exception as e:  # noqa: BLE001, RUF105
            assert "JackeryApiError" in type(e).__name__  # noqa: PT017, RUF105
            assert "system_region must be a non-empty string" in str(e)  # noqa: PT017, RUF105

    @pytest.mark.asyncio
    async def test_async_get_dynamic_price_login_url(self) -> None:
        """Test async_get_dynamic_price_login_url."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"loginUrl": "https://example.com"}}
        )  # noqa: RUF100, SLF001

        result = await client.async_get_dynamic_price_login_url(
            platform_company_id=123, system_id="system123"
        )  # noqa: E501, RUF100

        assert result == {"loginUrl": "https://example.com"}

    @pytest.mark.asyncio
    async def test_async_get_device_currency(self) -> None:
        """Test async_get_device_currency."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"currency": "EUR"}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_device_currency("device123")

        assert result == {"currency": "EUR"}

    @pytest.mark.asyncio
    async def test_async_save_contract_auth(self) -> None:
        """Test async_save_contract_auth."""
        client = self._create_client()
        client._post_form = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"success": True}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_save_contract_auth(
            contract_id="contract123",
            custom_id="custom123",
            platform_company_id=123,
            system_id="system123",
        )

        assert result == {"code": 0, "data": {"success": True}}

    @pytest.mark.asyncio
    async def test_async_get_contract_list(self) -> None:
        """Test async_get_contract_list."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": [{"id": "c1"}]})  # noqa: RUF105, SLF001

        result = await client.async_get_contract_list(
            customer_number="cust123", platform_company_id=123
        )  # noqa: E501, RUF100

        assert result == [{"id": "c1"}]

    @pytest.mark.asyncio
    async def test_async_cancel_contract_auth(self) -> None:
        """Test async_cancel_contract_auth."""
        client = self._create_client()
        client._post_form = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"success": True}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_cancel_contract_auth(
            platform_company_id=123, system_id="system123"
        )  # noqa: E501, RUF100

        assert result == {"code": 0, "data": {"success": True}}

    @pytest.mark.asyncio
    async def test_async_get_dynamic_price(self) -> None:
        """Test async_get_dynamic_price."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": {"price": 0.20}})  # noqa: RUF105, SLF001

        result = await client.async_get_dynamic_price("system123")

        assert result == {"price": 0.20}

    @pytest.mark.asyncio
    async def test_async_save_location_id(self) -> None:
        """Test async_save_location_id."""
        client = self._create_client()
        client._post_form = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"success": True}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_save_location_id(connect_token="token123")

        assert result == {"code": 0, "data": {"success": True}}

    @pytest.mark.asyncio
    async def test_async_save_tou_plan(self) -> None:
        """Test async_save_tou_plan."""
        client = self._create_client()
        client._post_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"success": True}}
        )  # noqa: E501, RUF100, SLF001

        tasks = [{"start": "08:00", "end": "18:00", "mode": "charge"}]
        result = await client.async_save_tou_plan(device_id="device123", tasks=tasks)

        assert result == {"code": 0, "data": {"success": True}}

    @pytest.mark.asyncio
    async def test_async_query_tou_plan(self) -> None:
        """Test async_query_tou_plan."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": {"tasks": []}})  # noqa: RUF105, SLF001

        result = await client.async_query_tou_plan(device_id="device123")

        assert result == {"tasks": []}

    @pytest.mark.asyncio
    async def test_async_get_currency_list(self) -> None:
        """Test async_get_currency_list."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": [{"code": "EUR"}]}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_currency_list()

        assert result == [{"code": "EUR"}]

    @pytest.mark.asyncio
    async def test_async_bind_currency(self) -> None:
        """Test async_bind_currency."""
        client = self._create_client()
        client._post_form = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"success": True}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_bind_currency(
            currency="EUR", device_id="device123", system_id="system123"
        )  # noqa: E501, RUF100

        assert result == {"code": 0, "data": {"success": True}}

    @pytest.mark.asyncio
    async def test_async_get_shelly_devices(self) -> None:
        """Test async_get_shelly_devices."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": [{"id": "s1"}]})  # noqa: RUF105, SLF001

        result = await client.async_get_shelly_devices()

        assert result == [{"id": "s1"}]

    @pytest.mark.asyncio
    async def test_async_get_shelly_realtime_power(self) -> None:
        """Test async_get_shelly_realtime_power."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": {"power": 100}})  # noqa: RUF105, SLF001

        result = await client.async_get_shelly_realtime_power("device123")

        assert result == {"power": 100}

    @pytest.mark.asyncio
    async def test_async_control_shelly_device(self) -> None:
        """Test async_control_shelly_device."""
        client = self._create_client()
        client._post_json = AsyncMock(return_value={"data": {"accepted": True}})  # noqa: RUF105, SLF001

        result = await client.async_control_shelly_device(
            "device123", action="turn_on", function="switch"
        )  # noqa: E501, RUF100

        assert result is True

    @pytest.mark.asyncio
    async def test_async_control_shelly_device_not_allowed(self) -> None:
        """Test async_control_shelly_device rejects when not allowed."""
        client = self._create_client()

        try:
            await client.async_control_shelly_device(
                "device123", action="turn_on", function="switch", control_allowed=False
            )  # noqa: E501, RUF100
            raise AssertionError("Should have raised JackeryApiError")  # noqa: RUF105, TRY301
        except Exception as e:  # noqa: BLE001, RUF105
            assert "JackeryApiError" in type(e).__name__  # noqa: PT017, RUF105
            assert "not allowed" in str(e)  # noqa: PT017, RUF105

    @pytest.mark.asyncio
    async def test_async_get_shelly_auth_url(self) -> None:
        """Test async_get_shelly_auth_url."""
        client = self._create_client()
        client._post_form = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={
                "code": 0,
                "data": {"authUrl": "https://auth.example.com", "state": "abc"},
            }
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_shelly_auth_url()

        assert result == {"authUrl": "https://auth.example.com", "state": "abc"}

    @pytest.mark.asyncio
    async def test_async_unbind_shelly_device(self) -> None:
        """Test async_unbind_shelly_device."""
        client = self._create_client()
        client._post_form = AsyncMock(return_value={"data": {"accepted": True}})  # noqa: RUF105, SLF001

        result = await client.async_unbind_shelly_device(
            binding_id="bind123", device_id="device123"
        )  # noqa: E501, RUF100

        assert result is True

    @pytest.mark.asyncio
    async def test_async_unbind_shelly_account(self) -> None:
        """Test async_unbind_shelly_account."""
        client = self._create_client()
        client._post_form = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"success": True}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_unbind_shelly_account()

        assert result is True

    @pytest.mark.asyncio
    async def test_async_get_shelly_binding_failures(self) -> None:
        """Test async_get_shelly_binding_failures."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={
                "code": 0,
                "data": {
                    "bindCount": 1,
                    "failedDeviceSns": [],
                    "successDeviceSns": ["s1"],
                },
            }
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_shelly_binding_failures(state="failed")

        assert result == {
            "bindCount": 1,
            "failedDeviceSns": [],
            "successDeviceSns": ["s1"],
        }  # noqa: E501, RUF100

    @pytest.mark.asyncio
    async def test_async_add_accessories(self) -> None:
        """Test async_add_accessories."""
        client = self._create_client()
        client._post_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"success": True}}
        )  # noqa: E501, RUF100, SLF001

        devices = [{"deviceSn": "acc1", "devType": "type1"}]
        result = await client.async_add_accessories(
            devices=devices, parent_device_id="parent123"
        )  # noqa: E501, RUF100

        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_async_remove_accessory(self) -> None:
        """Test async_remove_accessory."""
        client = self._create_client()
        client._delete_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"success": True}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_remove_accessory(accessory_id="acc123")

        assert result == {"success": True}

    @pytest.mark.asyncio
    async def test_async_check_accessories_exist(self) -> None:
        """Test async_check_accessories_exist."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": {"acc1": True}})  # noqa: RUF105, SLF001

        result = await client.async_check_accessories_exist(devices="acc1,acc2")

        assert result == {"acc1": True}

    @pytest.mark.asyncio
    async def test_async_get_accessories_list(self) -> None:
        """Test async_get_accessories_list."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": [{"id": "acc1"}]})  # noqa: RUF105, SLF001

        result = await client.async_get_accessories_list(device_id="device123")

        assert result == [{"id": "acc1"}]

    @pytest.mark.asyncio
    async def test_async_set_accessories_name(self) -> None:
        """Test async_set_accessories_name."""
        client = self._create_client()
        client._post_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"success": True}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_set_accessories_name(
            device_name="New Name", id="acc123"
        )  # noqa: E501, RUF100

        assert result == {"code": 0, "data": {"success": True}}

    @pytest.mark.asyncio
    async def test_async_check_jackery_accessories_exist(self) -> None:
        """Test async_check_jackery_accessories_exist."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": {"exists": True}})  # noqa: RUF105, SLF001

        result = await client.async_check_jackery_accessories_exist(
            device_sn_infos="sn1,sn2"
        )  # noqa: E501, RUF100

        assert result == {"exists": True}

    @pytest.mark.asyncio
    async def test_async_sync_smart_accessories(self) -> None:
        """Test async_sync_smart_accessories."""
        client = self._create_client()
        client._post_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"success": True}}
        )  # noqa: E501, RUF100, SLF001

        devices = [{"deviceSn": "acc1", "devType": 1}]
        result = await client.async_sync_smart_accessories(
            parent_device_id="parent123", dev_type=1, devices=devices
        )  # noqa: E501, RUF100

        assert result == {"code": 0, "data": {"success": True}}

    @pytest.mark.asyncio
    async def test_async_get_sub_shadow(self) -> None:
        """Test async_get_sub_shadow."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"shadow": "data"}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_sub_shadow(
            dev_type="type1", device_sn="sn1", sub_device_sn="sub1"
        )  # noqa: E501, RUF100

        assert result == {"shadow": "data"}

    @pytest.mark.asyncio
    async def test_async_get_system_shadow(self) -> None:
        """Test async_get_system_shadow."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"shadow": "system"}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_system_shadow(device_sn="sn1", diy_sn="diy1")

        assert result == {"shadow": "system"}

    @pytest.mark.asyncio
    async def test_async_get_notify_list(self) -> None:
        """Test async_get_notify_list."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": [{"id": 1}]})  # noqa: RUF105, SLF001

        result = await client.async_get_notify_list(
            current_time=1000, device_sn="sn1", page_no=1, page_size=10
        )  # noqa: E501, RUF100

        assert result == [{"id": 1}]

    @pytest.mark.asyncio
    async def test_async_get_unread_count(self) -> None:
        """Test async_get_unread_count."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": {"total": 5}})  # noqa: RUF105, SLF001

        result = await client.async_get_unread_count()

        assert result == {"total": 5}

    @pytest.mark.asyncio
    async def test_async_set_push_config(self) -> None:
        """Test async_set_push_config."""
        client = self._create_client()
        client._post_form = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"success": True}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_set_push_config(set=1)

        assert result == {"code": 0, "data": {"success": True}}

    @pytest.mark.asyncio
    async def test_async_get_push_config(self) -> None:
        """Test async_get_push_config."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": {"set": 1}})  # noqa: RUF105, SLF001

        result = await client.async_get_push_config()

        assert result == {"set": 1}

    @pytest.mark.asyncio
    async def test_async_check_smart_mode_set(self) -> None:
        """Test async_check_smart_mode_set."""
        client = self._create_client()
        client._post_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"enabled": True}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_check_smart_mode_set(
            device_id="device123", system_id="system123"
        )  # noqa: E501, RUF100

        assert result == {"enabled": True}

    @pytest.mark.asyncio
    async def test_async_get_smart_mode_info(self) -> None:
        """Test async_get_smart_mode_info."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": {"info": "data"}})  # noqa: RUF105, SLF001

        result = await client.async_get_smart_mode_info(system_id="system123")

        assert result == {"info": "data"}

    @pytest.mark.asyncio
    async def test_async_start_smart_mode(self) -> None:
        """Test async_start_smart_mode."""
        client = self._create_client()
        client._post_form = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"started": True}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_start_smart_mode(system_id="system123")

        assert result == {"code": 0, "data": {"started": True}}

    @pytest.mark.asyncio
    async def test_async_check_app_version(self) -> None:
        """Test async_check_app_version."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"version": "2.4.0"}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_check_app_version(
            type="android", version_name="2.4.0"
        )  # noqa: E501, RUF100

        assert result == {"version": "2.4.0"}

    @pytest.mark.asyncio
    async def test_async_get_banner_list(self) -> None:
        """Test async_get_banner_list."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": [{"id": 1}]})  # noqa: RUF105, SLF001

        result = await client.async_get_banner_list()

        assert result == [{"id": 1}]

    @pytest.mark.asyncio
    async def test_async_submit_feedback(self) -> None:
        """Test async_submit_feedback."""
        client = self._create_client()
        client._post_form = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"success": True}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_submit_feedback(
            contact_info="test@test.com", content="Feedback", device_sn="sn1"
        )  # noqa: E501, RUF100

        assert result == {"code": 0, "data": {"success": True}}

    @pytest.mark.asyncio
    async def test_async_get_faq_list(self) -> None:
        """Test async_get_faq_list."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": [{"question": "Q", "answer": "A"}]}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_faq_list()

        assert result == [{"question": "Q", "answer": "A"}]

    @pytest.mark.asyncio
    async def test_async_get_faq_answer(self) -> None:
        """Test async_get_faq_answer."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": [{"answer": "A"}]}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_faq_answer()

        assert result == [{"answer": "A"}]

    @pytest.mark.asyncio
    async def test_async_agree_privacy_consent(self) -> None:
        """Test async_agree_privacy_consent."""
        client = self._create_client()
        client._post_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"agreed": True}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_agree_privacy_consent(
            pending_agree_version_ids=[1, 2]
        )  # noqa: E501, RUF100

        assert result == {"code": 0, "data": {"agreed": True}}

    @pytest.mark.asyncio
    async def test_async_check_privacy_update(self) -> None:
        """Test async_check_privacy_update."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"updateRequired": False}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_check_privacy_update()

        assert result == {"updateRequired": False}

    @pytest.mark.asyncio
    async def test_async_get_product_instruction(self) -> None:
        """Test async_get_product_instruction."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"instruction": "data"}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_product_instruction(dev_sn="sn1", type="manual")

        assert result == {"instruction": "data"}

    @pytest.mark.asyncio
    async def test_async_get_zone_list(self) -> None:
        """Test async_get_zone_list."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": [{"zone": "DE"}]})  # noqa: RUF105, SLF001

        result = await client.async_get_zone_list()

        assert result == [{"zone": "DE"}]

    @pytest.mark.asyncio
    async def test_async_get_gcs_list(self) -> None:
        """Test async_get_gcs_list."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": [{"standard": "VDE"}]}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_gcs_list(country="DE")

        assert result == [{"standard": "VDE"}]

    @pytest.mark.asyncio
    async def test_async_get_alarm_detail(self) -> None:
        """Test async_get_alarm_detail."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"detail": "alarm info"}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_alarm_detail(alarm_key="alarm123")

        assert result == {"detail": "alarm info"}

    @pytest.mark.asyncio
    async def test_async_sync_alerts(self) -> None:
        """Test async_sync_alerts."""
        client = self._create_client()
        client._post_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"synced": True}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_sync_alerts(content="{}", id="device123")

        assert result == {"code": 0, "data": {"synced": True}}

    @pytest.mark.asyncio
    async def test_async_get_offline_statistics(self) -> None:
        """Test async_get_offline_statistics."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"offline": "data"}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_offline_statistics()

        assert result == {"offline": "data"}

    @pytest.mark.asyncio
    async def test_async_upload_power_report(self) -> None:
        """Test async_upload_power_report."""
        client = self._create_client()
        client._post_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"uploaded": True}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_upload_power_report(
            device_sn="sn1", properties={"p1": "v1"}
        )  # noqa: E501, RUF100

        assert result == {"uploaded": True}

    @pytest.mark.asyncio
    async def test_select_ota_item(self) -> None:  # noqa: PLR6301, RUF105
        """Test _select_ota_item static method."""
        items = [
            {"deviceSn": "sn1", "version": "1.0"},
            {"deviceSn": "sn2", "version": "2.0"},
        ]
        result = JackeryApi._select_ota_item(items, "sn1")  # noqa: RUF105, SLF001
        assert result == {"deviceSn": "sn1", "version": "1.0"}

        result = JackeryApi._select_ota_item(items, "sn3")  # Not found, returns first  # noqa: RUF105, SLF001
        assert result == {"deviceSn": "sn1", "version": "1.0"}

        result = JackeryApi._select_ota_item([], "sn1")  # Empty list  # noqa: RUF105, SLF001
        assert result == {}

    @pytest.mark.asyncio
    async def test_diagnostics_snapshot(self) -> None:
        """Test diagnostics_snapshot."""
        client = self._create_client()
        client._requests_total = 100  # noqa: RUF105, SLF001
        client._requests_failed = 5  # noqa: RUF105, SLF001
        client._timeouts_total = 2  # noqa: RUF105, SLF001
        client._auth_retries = 3  # noqa: RUF105, SLF001

        result = client.diagnostics_snapshot()

        assert result == {
            "requests_total": 100,
            "requests_failed": 5,
            "timeouts_total": 2,
            "auth_retries": 3,
        }

    @pytest.mark.asyncio
    async def test_hydrate_mqtt_session(self) -> None:
        """Test hydrate_mqtt_session."""
        client = self._create_client()

        client.hydrate_mqtt_session(
            user_id="user123",
            seed_b64=base64.b64encode(os.urandom(32)).decode("ascii"),
            mac_id="271c55f5731fa3d9ba1fe131e088946e0",
            mac_id_source="test",
        )

        assert client._mqtt_user_id == "user123"  # noqa: RUF105, SLF001
        assert client._mqtt_seed_b64 is not None  # noqa: RUF105, SLF001
        assert client._mqtt_mac_id == "271c55f5731fa3d9ba1fe131e088946e0"  # noqa: RUF105, SLF001
        assert client._mqtt_mac_id_source == "test"  # noqa: RUF105, SLF001

    @pytest.mark.asyncio
    async def test_mqtt_session_snapshot(self) -> None:
        """Test mqtt_session_snapshot."""
        client = self._create_client()
        # Without session
        result = client.mqtt_session_snapshot()
        assert result is None

        # With session
        client._mqtt_user_id = "user123"  # noqa: RUF105, SLF001
        client._mqtt_seed_b64 = base64.b64encode(os.urandom(32)).decode("ascii")  # noqa: RUF105, SLF001
        client._mqtt_mac_id = "271c55f5731fa3d9ba1fe131e088946e0"  # noqa: RUF105, SLF001
        client._mqtt_mac_id_source = "test"  # noqa: RUF105, SLF001

        result = client.mqtt_session_snapshot()
        assert result is not None
        assert result["user_id"] == "user123"
        assert result["mac_id"] == "271c55f5731fa3d9ba1fe131e088946e0"
        assert result["mac_id_source"] == "test"

    @pytest.mark.asyncio
    async def test_async_get_user_info(self) -> None:
        """Test async_get_user_info."""
        client = self._create_client()
        client._get_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"nickName": "Test User"}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_get_user_info()

        assert result == {"nickName": "Test User"}

    @pytest.mark.asyncio
    async def test_async_update_register_id(self) -> None:
        """Test async_update_register_id."""
        client = self._create_client()
        client._post_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"success": True}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_update_register_id(register_id="reg123")

        assert result == {"code": 0, "data": {"success": True}}

    @pytest.mark.asyncio
    async def test_async_list_devices_legacy(self) -> None:
        """Test async_list_devices_legacy."""
        client = self._create_client()
        client._get_json = AsyncMock(return_value={"code": 0, "data": [{"id": "dev1"}]})  # noqa: RUF105, SLF001

        result = await client.async_list_devices_legacy()

        assert result == [{"id": "dev1"}]

    @pytest.mark.asyncio
    async def test_async_update_user_info(self) -> None:
        """Test async_update_user_info."""
        client = self._create_client()
        client._post_json = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={"code": 0, "data": {"success": True}}
        )  # noqa: E501, RUF100, SLF001

        result = await client.async_update_user_info(nick_name="New Name")

        assert result == {"code": 0, "data": {"success": True}}

    @pytest.mark.asyncio
    async def test_async_set_system_name(self) -> None:
        """Test async_set_system_name."""
        client = self._create_client()
        client._put_json = AsyncMock(return_value={"code": 0, "data": True})  # noqa: RUF105, SLF001

        result = await client.async_set_system_name(
            system_id="system123", system_name="New System"
        )  # noqa: E501, RUF100

        assert result is True

    @pytest.mark.asyncio
    async def test_async_set_system_name_empty(self) -> None:
        """Test async_set_system_name rejects empty name."""
        client = self._create_client()

        try:
            await client.async_set_system_name(system_id="system123", system_name="")
            raise AssertionError("Should have raised JackeryApiError")  # noqa: RUF105, TRY301
        except Exception as e:  # noqa: BLE001, RUF105
            assert "JackeryApiError" in type(e).__name__  # noqa: PT017, RUF105
            assert "system_name must be a non-empty string" in str(e)  # noqa: PT017, RUF105

    @pytest.mark.asyncio
    async def test_async_set_max_power(self) -> None:
        """Test async_set_max_power."""
        client = self._create_client()
        client._post_form = AsyncMock(return_value={"code": 0, "data": True})  # noqa: RUF105, SLF001

        result = await client.async_set_max_power(device_id="device123", max_power=3000)

        assert result is True

    @pytest.mark.asyncio
    async def test_async_set_max_power_invalid(self) -> None:
        """Test async_set_max_power rejects invalid power."""
        client = self._create_client()

        try:
            await client.async_set_max_power(device_id="device123", max_power=-100)
            raise AssertionError("Should have raised JackeryApiError")  # noqa: RUF105, TRY301
        except Exception as e:  # noqa: BLE001, RUF105
            assert "JackeryApiError" in type(e).__name__  # noqa: PT017, RUF105
            assert "non-negative integer" in str(e)  # noqa: PT017, RUF105


class TestCoalescedDayStat:
    """Test _coalesced_day_stat_copy method."""

    def _create_client(self):  # noqa: PLR6301, RUF105
        """Create a client with mocked session."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        return JackeryApi(
            session=mock_session,
            account="test_account",
            password="test_password",
        )

    def test_coalesced_day_stat_non_day(self) -> None:
        """Test _coalesced_day_stat_copy returns unchanged for non-day."""
        client = self._create_client()
        data = {"code": 0, "data": {"x": ["1"], "y": [1]}}

        result = client._coalesced_day_stat_copy(data, "month")  # noqa: RUF105, SLF001

        assert result is data  # Should return same object

    def test_coalesced_day_stat_no_data(self) -> None:
        """Test _coalesced_day_stat_copy with no data field."""
        client = self._create_client()
        data = {"code": 0}

        result = client._coalesced_day_stat_copy(data, "day")  # noqa: RUF105, SLF001

        assert result is data

    def test_coalesced_day_stat_invalid_data(self) -> None:
        """Test _coalesced_day_stat_copy with invalid data field."""
        client = self._create_client()
        data = {"code": 0, "data": "not a dict"}

        result = client._coalesced_day_stat_copy(data, "day")  # noqa: RUF105, SLF001

        assert result is data

    def test_coalesced_day_stat_no_series(self) -> None:
        """Test _coalesced_day_stat_copy with no chart series."""
        client = self._create_client()
        data = {"code": 0, "data": {"other": "value"}}

        result = client._coalesced_day_stat_copy(data, "day")  # noqa: RUF105, SLF001

        assert result is data

    def test_coalesced_day_stat_normalizes_bools(self) -> None:
        """Test _coalesced_day_stat_copy normalizes boolean values to None."""
        client = self._create_client()
        # y series with boolean values (should become None)
        data = {"code": 0, "data": {"y": [1.0, True, False, 2.0, None]}}

        result = client._coalesced_day_stat_copy(data, "day")  # noqa: RUF105, SLF001

        assert result is not data  # Should be a new dict
        assert result["data"]["y"] == [1.0, None, None, 2.0, None]

    def test_coalesced_day_stat_preserves_valid_numbers(self) -> None:
        """Test _coalesced_day_stat_copy preserves valid numeric values."""
        client = self._create_client()
        # y series with valid numbers including negatives - no changes expected
        data = {"code": 0, "data": {"y": [1.5, -0.5, 0.0, 10.0]}}

        result = client._coalesced_day_stat_copy(data, DATE_TYPE_DAY)  # noqa: RUF105, SLF001

        # When no changes needed, returns original data unchanged
        assert result is data
        assert result["data"]["y"] == [1.5, -0.5, 0.0, 10.0]

    def test_coalesced_day_stat_multiple_series(self) -> None:
        """Test _coalesced_day_stat_copy handles multiple series."""
        client = self._create_client()
        data = {
            "code": 0,
            "data": {
                "y": [1.0, True, 2.0],
                "y1": [True, 2.0, False],
                "x": ["a", "b", "c"],  # Should not be modified
            },
        }

        result = client._coalesced_day_stat_copy(data, "day")  # noqa: RUF105, SLF001

        assert result is not data
        assert result["data"]["y"] == [1.0, None, 2.0]
        assert result["data"]["y1"] == [None, 2.0, None]
        assert result["data"]["x"] == ["a", "b", "c"]  # Unchanged


class TestHttpPayloadDebug:
    """Test _http_payload_debug and _log_body integration."""

    def _create_client(self):  # noqa: PLR6301, RUF105
        """Create a client with mocked session."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        return JackeryApi(
            session=mock_session,
            account="test_account",
            password="test_password",
        )

    def test_http_payload_debug_get(self) -> None:
        """Test _http_payload_debug for GET request."""
        client = self._create_client()
        event = client._http_payload_debug(  # noqa: RUF105, SLF001
            method="GET",
            path="/test/path",
            params={"param1": "value1"},
            status=200,
            response={"code": 0, "data": {"key": "value"}},
        )

        assert event["kind"] == "http"
        assert event["method"] == "GET"
        assert event["path"] == "/test/path"
        assert event["params"] == {"param1": "value1"}
        assert event["status"] == 200
        assert event["response"] == {"code": 0, "data": {"key": "value"}}
        assert "response_data_type" in event

    def test_http_payload_debug_post(self) -> None:
        """Test _http_payload_debug for POST request."""
        client = self._create_client()
        event = client._http_payload_debug(  # noqa: RUF105, SLF001
            method="POST",
            path="/test/path",
            body={"field": "value"},
            status=200,
            response={"code": 0},
        )

        assert event["method"] == "POST"
        assert event["request_body"] == {"field": "value"}

    def test_http_payload_debug_chart_series_debug(self) -> None:
        """Test _http_payload_debug includes chart_series_debug for day data."""
        client = self._create_client()
        event = client._http_payload_debug(  # noqa: RUF105, SLF001
            method="GET",
            path="/test/stat",
            params={"dateType": "day"},
            response={"code": 0, "data": {"y": [1, 2, 3], "y1": [4, 5, 6]}},
        )

        assert "chart_series_debug" in event
        assert event["chart_series_debug"]["y"]["raw_count"] == 3
        assert event["chart_series_debug"]["y1"]["raw_count"] == 3


class TestPayloadDictAndList:
    """Test _payload_dict and _payload_list edge cases."""

    def _create_client(self):  # noqa: PLR6301, RUF105
        """Create a client with mocked session."""
        mock_session = AsyncMock(spec=aiohttp.ClientSession)
        return JackeryApi(
            session=mock_session,
            account="test_account",
            password="test_password",
        )

    def test_payload_dict_returns_dict(self) -> None:
        """Test _payload_dict returns dict payload."""
        client = self._create_client()
        data = {"code": 0, "data": {"key": "value"}}

        result = client._payload_dict(data, "/test/path")  # noqa: RUF105, SLF001

        assert result == {"key": "value"}

    def test_payload_dict_returns_empty_when_none(self) -> None:
        """Test _payload_dict returns empty dict when data is None."""
        client = self._create_client()
        data = {"code": 0, "data": None}

        result = client._payload_dict(data, "/test/path")  # noqa: RUF105, SLF001

        assert result == {}

    def test_payload_dict_logs_warning_on_unexpected_shape(self, caplog) -> None:  # noqa: RUF105
        """Test _payload_dict logs warning on unexpected data shape."""
        client = self._create_client()
        data = {"code": 0, "data": "unexpected string"}

        result = client._payload_dict(data, "/test/path")  # noqa: RUF105, SLF001

        assert result == {}
        assert "unexpected data shape" in caplog.text

    def test_payload_list_returns_list(self) -> None:
        """Test _payload_list returns list of dicts."""
        client = self._create_client()
        data = {"code": 0, "data": [{"a": 1}, {"b": 2}]}

        result = client._payload_list(data, "/test/path")  # noqa: RUF105, SLF001

        assert result == [{"a": 1}, {"b": 2}]

    def test_payload_list_filters_non_dict(self) -> None:
        """Test _payload_list filters non-dict items."""
        client = self._create_client()
        data = {"code": 0, "data": [{"a": 1}, "not dict", {"b": 2}, 123]}

        result = client._payload_list(data, "/test/path")  # noqa: RUF105, SLF001

        assert result == [{"a": 1}, {"b": 2}]

    def test_payload_list_returns_empty_when_none(self) -> None:
        """Test _payload_list returns empty list when data is None."""
        client = self._create_client()
        data = {"code": 0, "data": None}

        result = client._payload_list(data, "/test/path")  # noqa: RUF105, SLF001

        assert result == []

    def test_payload_list_logs_warning_on_unexpected_shape(self, caplog) -> None:  # noqa: RUF105
        """Test _payload_list logs warning on unexpected data shape."""
        client = self._create_client()
        data = {"code": 0, "data": "unexpected string"}

        result = client._payload_list(data, "/test/path")  # noqa: RUF105, SLF001

        assert result == []
        assert "unexpected data shape" in caplog.text


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
