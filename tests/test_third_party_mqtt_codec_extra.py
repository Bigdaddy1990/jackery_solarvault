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


class TestThirdPartyMqttCodecExtra:
    """Additional tests for third_party_mqtt_codec to increase coverage."""

    def _create_bluetooth_key(self) -> bytes:
        """Create a valid 16-byte bluetooth key."""
        return b"0123456789abcdef"

    def test_ble_aes_iv_len_constant(self) -> None:
        """Test BLE_AES_IV_LEN constant value."""
        assert BLE_AES_IV_LEN == 16

    def test_encode_decode_edge_cases(self) -> None:
        """Test encode/decode with edge cases."""
        bluetooth_key = self._create_bluetooth_key()

        # Very long string
        value = "x" * 1000
        encoded = encode_third_party_mqtt_field(value, bluetooth_key)
        decoded = decode_third_party_mqtt_field(encoded, bluetooth_key)
        assert decoded == value

        # String with newlines
        value = "line1\nline2\r\nline3"
        encoded = encode_third_party_mqtt_field(value, bluetooth_key)
        decoded = decode_third_party_mqtt_field(encoded, bluetooth_key)
        assert decoded == value

    def test_generate_token_format(self) -> None:
        """Test token generation format."""
        for _ in range(100):
            token = generate_third_party_mqtt_token()
            assert len(token) == 9
            assert token.isdigit()

    def test_stable_token_all_cases(self) -> None:
        """Test stable_third_party_mqtt_token all branches."""
        # Case 1: user token provided, valid length
        result_token, use_generated, new_generated = stable_third_party_mqtt_token(
            "123456789", "987654321"
        )
        assert result_token == "123456789"
        assert use_generated is False
        assert new_generated is None

        # Case 2: user token matches generated
        result_token, use_generated, new_generated = stable_third_party_mqtt_token(
            "123456789", "123456789"
        )
        assert result_token == "123456789"
        assert use_generated is True
        assert new_generated is None

        # Case 3: no user token, no generated
        result_token, use_generated, new_generated = stable_third_party_mqtt_token(
            None, None
        )
        assert len(result_token) == 9
        assert result_token.isdigit()
        assert use_generated is True
        assert new_generated == result_token

        # Case 4: no user token, generated exists
        result_token, use_generated, new_generated = stable_third_party_mqtt_token(
            None, "987654321"
        )
        assert result_token == "987654321"
        assert use_generated is True
        assert new_generated is None

    def test_config_from_options_all_defaults(self) -> None:
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

    def test_config_plaintext_all_paths(self) -> None:
        """Test config plaintext with various scenarios."""
        options = {
            "third_party_mqtt_enable": True,
            "third_party_mqtt_ip": "192.168.1.100",
            "third_party_mqtt_port": 8883,
            "third_party_mqtt_username": "user",
            "third_party_mqtt_password": "pass",
            "third_party_mqtt_token": "123456789",
        }
        generated_token = "987654321"

        # Without device data
        result = third_party_mqtt_config_plaintext(options, generated_token, None)
        assert result["enable"] == 1
        assert result["ip"] == "192.168.1.100"

        # With device data (device overwrites)
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
        assert result["enable"] == 0
        assert result["ip"] == "10.0.0.1"

    def test_decode_config_body_all_cases(self) -> None:
        """Test decode_third_party_mqtt_config_body all branches."""
        bluetooth_key = self._create_bluetooth_key()

        # Case: missing bluetooth key
        body = {"userName": "dGVzdA==", "password": "cGFzcw=="}
        result = decode_third_party_mqtt_config_body(body, None)
        assert result["_ha_plaintext"] is False
        assert result["_decode_error"] == "missing_bluetooth_key"

        # Case: empty body
        body = {}
        result = decode_third_party_mqtt_config_body(body, bluetooth_key)
        assert result["_ha_plaintext"] is False
        assert "_decode_failed_fields" not in result

        # Case: valid fields
        userName = encode_third_party_mqtt_field("user", bluetooth_key)
        password = encode_third_party_mqtt_field("pass", bluetooth_key)
        body = {"userName": userName, "password": password}
        result = decode_third_party_mqtt_config_body(body, bluetooth_key)
        assert result["userName"] == "user"
        assert result["password"] == "pass"
        assert result["_ha_plaintext"] is True

        # Case: invalid field
        body = {"userName": userName, "password": "invalid_base64"}
        result = decode_third_party_mqtt_config_body(body, bluetooth_key)
        assert result["userName"] == "user"
        assert result["_ha_plaintext"] is False
        assert "password" in result["_decode_failed_fields"]

        # Case: non-string values
        body = {"userName": 123, "password": None, "token": []}
        result = decode_third_party_mqtt_config_body(body, bluetooth_key)
        assert result["_ha_plaintext"] is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
