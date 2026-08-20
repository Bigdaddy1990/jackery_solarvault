"""Tests for uncovered paths in third_party_mqtt_codec.py to increase coverage."""

import pytest

from custom_components.jackery_solarvault.client.third_party_mqtt_codec import (
    BLE_AES_IV_LEN,
    decode_third_party_mqtt_config_body,
    decode_third_party_mqtt_field,
    encode_third_party_mqtt_field,
    generate_third_party_mqtt_token,
    stable_third_party_mqtt_token,
    third_party_mqtt_config_from_options,
    third_party_mqtt_config_plaintext,
)


class TestThirdPartyMqttCodec:  # noqa: PLR0904
    """Test third_party_mqtt_codec module."""

    def _create_bluetooth_key(self) -> bytes:  # noqa: PLR6301
        """Create a valid 16-byte bluetooth key."""
        return b"0123456789abcdef"

    def test_ble_aes_iv_len_constant(self) -> None:  # noqa: PLR6301
        """Test BLE_AES_IV_LEN constant value."""
        assert BLE_AES_IV_LEN == 16

    def test_encode_decode_basic(self) -> None:
        """Test basic encode/decode roundtrip."""
        bluetooth_key = self._create_bluetooth_key()
        value = "test_value"

        encoded = encode_third_party_mqtt_field(value, bluetooth_key)
        assert isinstance(encoded, str)

        decoded = decode_third_party_mqtt_field(encoded, bluetooth_key)
        assert decoded == value

    def test_encode_decode_empty_string(self) -> None:
        """Test encode/decode with empty string."""
        bluetooth_key = self._create_bluetooth_key()
        value = ""

        encoded = encode_third_party_mqtt_field(value, bluetooth_key)
        decoded = decode_third_party_mqtt_field(encoded, bluetooth_key)
        assert decoded == value

    def test_encode_decode_special_chars(self) -> None:
        """Test encode/decode with special characters."""
        bluetooth_key = self._create_bluetooth_key()
        value = "!@#$%^&*()_+-=[]{}|;':\",./<>?"

        encoded = encode_third_party_mqtt_field(value, bluetooth_key)
        decoded = decode_third_party_mqtt_field(encoded, bluetooth_key)
        assert decoded == value

    def test_encode_decode_unicode(self) -> None:
        """Test encode/decode with unicode."""
        bluetooth_key = self._create_bluetooth_key()
        value = "测试🔋🌞"

        encoded = encode_third_party_mqtt_field(value, bluetooth_key)
        decoded = decode_third_party_mqtt_field(encoded, bluetooth_key)
        assert decoded == value

    def test_encode_decode_long_string(self) -> None:
        """Test encode/decode with long string."""
        bluetooth_key = self._create_bluetooth_key()
        value = "x" * 10000

        encoded = encode_third_party_mqtt_field(value, bluetooth_key)
        decoded = decode_third_party_mqtt_field(encoded, bluetooth_key)
        assert decoded == value

    def test_generate_token_format(self) -> None:  # noqa: PLR6301
        """Test token generation format."""
        for _ in range(100):
            token = generate_third_party_mqtt_token()
            assert len(token) == 9
            assert token.isdigit()

    def test_generate_token_uniqueness(self) -> None:  # noqa: PLR6301
        """Test that generated tokens are unique."""
        tokens = set()
        for _ in range(1000):
            token = generate_third_party_mqtt_token()
            tokens.add(token)
        # Should have very high uniqueness
        assert len(tokens) > 950

    def test_stable_token_user_provided_valid(self) -> None:  # noqa: PLR6301
        """Test stable_third_party_mqtt_token with user token."""
        result_token, use_generated, new_generated = stable_third_party_mqtt_token(
            "123456789", "987654321"
        )
        assert result_token == "123456789"
        assert use_generated is False
        assert new_generated is None

    def test_stable_token_user_matches_generated(self) -> None:  # noqa: PLR6301
        """Test stable_third_party_mqtt_token when user token matches generated."""
        result_token, use_generated, new_generated = stable_third_party_mqtt_token(
            "123456789", "123456789"
        )
        assert result_token == "123456789"
        assert use_generated is True
        assert new_generated is None

    def test_stable_token_no_user_no_generated(self) -> None:  # noqa: PLR6301
        """Test stable_third_party_mqtt_token with no tokens."""
        result_token, use_generated, new_generated = stable_third_party_mqtt_token(
            None, None
        )
        assert len(result_token) == 9
        assert result_token.isdigit()
        assert use_generated is True
        assert new_generated == result_token

    def test_stable_token_no_user_has_generated(self) -> None:  # noqa: PLR6301
        """Test stable_third_party_mqtt_token with generated but no user."""
        result_token, use_generated, new_generated = stable_third_party_mqtt_token(
            None, "987654321"
        )
        assert result_token == "987654321"
        assert use_generated is True
        assert new_generated is None

    def test_config_from_options_all_defaults(self) -> None:  # noqa: PLR6301
        """Test config from options with all defaults."""
        options = {}
        generated_token = "123456789"
        config = third_party_mqtt_config_from_options(options, generated_token)
        assert config["enable"] == 0
        assert config["ip"] == ""
        assert config["port"] == 1883
        assert config["userName"] == ""
        assert config["password"] == ""
        assert config["token"] == "123456789"

    def test_config_from_options_custom_values(self) -> None:  # noqa: PLR6301
        """Test config from options with custom values."""
        options = {
            "third_party_mqtt_enable": True,
            "third_party_mqtt_ip": "192.168.1.100",
            "third_party_mqtt_port": 8883,
            "third_party_mqtt_username": "user",
            "third_party_mqtt_password": "pass",
            "third_party_mqtt_token": "123456789",
        }
        generated_token = "987654321"
        config = third_party_mqtt_config_from_options(options, generated_token)
        assert config["enable"] == 1
        assert config["ip"] == "192.168.1.100"
        assert config["port"] == 8883
        assert config["userName"] == "user"
        assert config["password"] == "pass"
        assert config["token"] == "123456789"

    def test_config_plaintext_without_device_data(self) -> None:  # noqa: PLR6301
        """Test config plaintext without device data."""
        options = {
            "third_party_mqtt_enable": True,
            "third_party_mqtt_ip": "192.168.1.100",
            "third_party_mqtt_port": 8883,
            "third_party_mqtt_username": "user",
            "third_party_mqtt_password": "pass",
            "third_party_mqtt_token": "123456789",
        }
        generated_token = "987654321"

        result = third_party_mqtt_config_plaintext(options, generated_token, None)
        assert result["enable"] == 1
        assert result["ip"] == "192.168.1.100"
        assert result["port"] == 8883
        assert result["userName"] == "user"
        assert result["password"] == "pass"
        assert result["token"] == "123456789"

    def test_config_plaintext_with_device_data(self) -> None:  # noqa: PLR6301
        """Test config plaintext with device data (device overwrites)."""
        options = {
            "third_party_mqtt_enable": True,
            "third_party_mqtt_ip": "192.168.1.100",
            "third_party_mqtt_port": 8883,
        }
        generated_token = "987654321"

        device_data = {
            "third_party_mqtt_config": {
                "enable": 0,
                "ip": "10.0.0.1",
                "port": 8883,
            }
        }
        result = third_party_mqtt_config_plaintext(
            options, generated_token, device_data
        )
        assert result["enable"] == 0  # Device overwrites
        assert result["ip"] == "10.0.0.1"  # Device overwrites
        assert result["port"] == 8883

    def test_decode_config_body_missing_bluetooth_key(self) -> None:  # noqa: PLR6301
        """Test decode_config_body with missing bluetooth key."""
        body = {"userName": "dGVzdA==", "password": "cGFzcw=="}
        result = decode_third_party_mqtt_config_body(body, None)
        assert result["_ha_plaintext"] is False
        assert result["_decode_error"] == "missing_bluetooth_key"

    def test_decode_config_body_empty(self) -> None:
        """Test decode_config_body with empty body."""
        bluetooth_key = self._create_bluetooth_key()
        body = {}
        result = decode_third_party_mqtt_config_body(body, bluetooth_key)
        assert result["_ha_plaintext"] is False
        assert "_decode_failed_fields" not in result

    def test_decode_config_body_valid(self) -> None:
        """Test decode_config_body with valid fields."""
        bluetooth_key = self._create_bluetooth_key()
        userName = encode_third_party_mqtt_field("user", bluetooth_key)  # noqa: N806
        password = encode_third_party_mqtt_field("pass", bluetooth_key)
        body = {"userName": userName, "password": password}
        result = decode_third_party_mqtt_config_body(body, bluetooth_key)
        assert result["userName"] == "user"
        assert result["password"] == "pass"
        assert result["_ha_plaintext"] is True

    def test_decode_config_body_invalid_field(self) -> None:
        """Test decode_config_body with invalid field."""
        bluetooth_key = self._create_bluetooth_key()
        userName = encode_third_party_mqtt_field("user", bluetooth_key)  # noqa: N806
        body = {"userName": userName, "password": "invalid_base64"}
        result = decode_third_party_mqtt_config_body(body, bluetooth_key)
        assert result["userName"] == "user"
        assert result["_ha_plaintext"] is False
        assert "password" in result["_decode_failed_fields"]

    def test_decode_config_body_non_string_values(self) -> None:
        """Test decode_config_body with non-string values."""
        bluetooth_key = self._create_bluetooth_key()
        body = {"userName": 123, "password": None, "token": []}
        result = decode_third_party_mqtt_config_body(body, bluetooth_key)
        assert result["_ha_plaintext"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
