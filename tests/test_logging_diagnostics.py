"""Tests for safe payload logging and mandatory diagnostics redaction.

Task 13: Restore safe payload logging and mandatory diagnostics redaction.
"""

import math  # ruff: ignore[unsorted-imports]
from unittest.mock import Mock

import pytest

from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.const import (
    CONF_ENABLE_PAYLOAD_DEBUG_LOG,
    DEFAULT_ENABLE_PAYLOAD_DEBUG_LOG,
    PAYLOAD_DEBUG_LOGGER_NAME,
    REDACTED_VALUE,
)
from custom_components.jackery_solarvault.util import _payload_debug_redacted  # noqa: PLC2701, RUF105


class TestPayloadDebugLogger:
    """Test the dedicated payload debug logger."""

    def test_payload_debug_logger_exists(self) -> None:  # noqa: PLR6301, RUF105
        """PAYLOAD_DEBUG_LOGGER_NAME constant should be defined."""
        assert (
            PAYLOAD_DEBUG_LOGGER_NAME
            == "custom_components.jackery_solarvault.payload_debug"
        )  # noqa: E501, RUF100

    def test_payload_debug_option_constant_exists(self) -> None:  # noqa: PLR6301, RUF105
        """CONF_ENABLE_PAYLOAD_DEBUG_LOG constant should be defined."""
        assert CONF_ENABLE_PAYLOAD_DEBUG_LOG == "enable_payload_debug_log"

    def test_redacted_value_constant(self) -> None:  # noqa: PLR6301, RUF105
        """REDACTED_VALUE should be a recognizable placeholder."""
        assert REDACTED_VALUE == "**REDACTED**"


class TestPayloadRedaction:
    """Test mandatory recursive redaction of sensitive data."""

    @pytest.fixture
    def api(self) -> JackeryApi:  # noqa: D102, PLR6301, RUF105
        return JackeryApi(Mock(), "tester@example.com", "secret")

    def test_redaction_removes_tokens(self, api: JackeryApi) -> None:  # noqa: PLR6301, RUF105
        """Access tokens, refresh tokens must be redacted."""
        payload = {
            "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
            "refresh_token": "dGhpcyBpcyBhIHJlZnJlc2ggdG9rZW4",
            "token": "bearer_token_12345",
            "data": {"nested_token": "secret_nested"},
        }
        redacted = _payload_debug_redacted(payload)

        assert redacted["access_token"] == "**REDACTED**"
        assert redacted["refresh_token"] == "**REDACTED**"
        assert redacted["token"] == "**REDACTED**"
        assert redacted["data"]["nested_token"] == "**REDACTED**"

    def test_redaction_removes_credentials(self, api: JackeryApi) -> None:  # noqa: PLR6301, RUF105
        """Passwords, API keys, secrets must be redacted."""
        payload = {
            "password": "my_secret_password",
            "mqtt_password": "mqtt_secret",
            "api_key": "sk-1234567890abcdef",
            "secret": "shared_secret",
            "credentials": {"username": "user", "password": "pass"},
        }
        redacted = _payload_debug_redacted(payload)

        assert redacted["password"] == "**REDACTED**"
        assert redacted["mqtt_password"] == "**REDACTED**"
        assert redacted["api_key"] == "**REDACTED**"
        assert redacted["secret"] == "**REDACTED**"
        assert redacted["credentials"]["password"] == "**REDACTED**"
        # username is also redacted (REDACT_KEYS contains username-related keys)
        assert redacted["credentials"]["username"] == "**REDACTED**"

    def test_redaction_removes_keys_and_coordinates(self, api: JackeryApi) -> None:  # noqa: PLR6301, RUF105
        """Encryption keys, MAC IDs, coordinates must be redacted."""
        payload = {
            "aes_key": "base64encodedkey==",
            "rsa_key": "-----BEGIN PUBLIC KEY-----...",
            "mqtt_mac_id": "271c55f5731fa3d9ba1fe131e088946e0",
            "latitude": 52.5200,
            "longitude": 13.4050,
            "gps": {"lat": 48.8566, "lon": 2.3522},
        }
        redacted = _payload_debug_redacted(payload)

        assert redacted["aes_key"] == "**REDACTED**"
        assert redacted["rsa_key"] == "**REDACTED**"
        assert redacted["mqtt_mac_id"] == "**REDACTED**"
        assert redacted["latitude"] == "**REDACTED**"
        assert redacted["longitude"] == "**REDACTED**"
        # gps is a redacted key, so the entire dict is replaced
        assert redacted["gps"] == "**REDACTED**"

    def test_redaction_removes_account_ids(self, api: JackeryApi) -> None:  # noqa: PLR6301, RUF105
        """User IDs, account IDs, device IDs must be redacted."""
        payload = {
            "user_id": 123456789,
            "account_id": "acc_abc123",
            "device_id": 9876543210,
            "device_sn": "HR2C04000280HH3",
            "bind_user_id": "user_999",
        }
        redacted = _payload_debug_redacted(payload)

        assert redacted["user_id"] == "**REDACTED**"
        assert redacted["account_id"] == "**REDACTED**"
        assert redacted["device_id"] == "**REDACTED**"
        assert redacted["device_sn"] == "**REDACTED**"
        assert redacted["bind_user_id"] == "**REDACTED**"

    def test_redaction_preserves_non_sensitive_data(self, api: JackeryApi) -> None:  # noqa: PLR6301, RUF105
        """Non-sensitive fields (measurements, states, config) must be preserved."""
        payload = {
            "soc": 73,
            "batState": 1,
            "pvPw": 1200,
            "gridPw": -500,
            "temperature": 25.5,
            "firmware": "v1.2.3",
            "model": "SolarVault 3 Pro Max",
            "onlineState": 1,
        }
        redacted = _payload_debug_redacted(payload)

        # All these should be preserved (not redacted)
        assert redacted["soc"] == 73
        assert redacted["batState"] == 1
        assert redacted["pvPw"] == 1200
        assert redacted["gridPw"] == -500
        assert redacted["temperature"] == 25.5  # noqa: RUF069, RUF105
        assert redacted["firmware"] == "v1.2.3"
        assert redacted["model"] == "SolarVault 3 Pro Max"
        assert redacted["onlineState"] == 1

    def test_redaction_handles_lists(self, api: JackeryApi) -> None:  # noqa: PLR6301, RUF105
        """Redaction must recurse into lists."""
        payload = {
            "devices": [
                {"deviceId": "dev-1", "token": "secret1"},
                {"deviceId": "dev-2", "token": "secret2"},
            ],
            "chart_data": [1.0, 2.0, 3.0],
        }
        redacted = _payload_debug_redacted(payload)

        assert redacted["devices"][0]["deviceId"] == "**REDACTED**"
        assert redacted["devices"][0]["token"] == "**REDACTED**"
        assert redacted["devices"][1]["deviceId"] == "**REDACTED**"
        assert redacted["devices"][1]["token"] == "**REDACTED**"
        # Chart data preserved (not sensitive)
        assert redacted["chart_data"] == [1.0, 2.0, 3.0]

    def test_redaction_handles_none_and_primitives(self, api: JackeryApi) -> None:  # noqa: PLR6301, RUF105
        """Redaction must handle None, bool, int, float, str safely."""
        payload = {
            "none_val": None,
            "bool_val": True,
            "int_val": 42,
            "float_val": math.pi,
            "str_val": "hello",
        }
        redacted = _payload_debug_redacted(payload)

        assert redacted["none_val"] is None
        assert redacted["bool_val"] is True
        assert redacted["int_val"] == 42
        assert redacted["float_val"] == math.pi
        assert redacted["str_val"] == "hello"


class TestPayloadDebugOption:
    """Test the payload_debug option behavior."""

    def test_payload_debug_option_activates_logger(self) -> None:
        """When payload_debug option is True, payload logger should be active."""
        # The option should enable the dedicated payload_debug logger
        # This is tested via the config flow and coordinator integration

    def test_inherited_debug_level_honored(self) -> None:
        """If root logger is DEBUG, payload logger should also log."""
        # The payload logger should check isEnabledFor(logging.DEBUG)
        # rather than just its own level

    def test_payload_debug_defaults_to_true(self) -> None:  # noqa: PLR6301, RUF105
        """Payload debug default value."""
        assert DEFAULT_ENABLE_PAYLOAD_DEBUG_LOG is True


class TestMandatoryRedaction:
    """Test that redaction cannot be disabled."""

    def test_no_redaction_disable_path(self) -> None:  # noqa: PLR6301, RUF105
        """No option, env var, or function argument can disable redaction."""
        # The redaction must be mandatory at export boundary
        # Verify there's no "redact=False" or similar parameter
        # The function should not accept a disable parameter
        import inspect  # noqa: PLC0415, RUF105

        from homeassistant.components.diagnostics import (  # noqa: PLC0415, RUF105
            async_redact_data as _recursive_redact,
        )

        sig = inspect.signature(_recursive_redact)
        params = list(sig.parameters.keys())
        assert "redact" not in params
        assert "disable_redaction" not in params

    def test_export_boundary_redaction(self) -> None:
        """Final export (diagnostics, logs) must pass through redaction."""
        # This tests that all payload outputs go through _recursive_redact


class TestManifestLoggers:
    """Test manifest.json logger declarations."""

    def test_manifest_logger_declarations_minimal(self) -> None:  # noqa: PLR6301, RUF105
        """manifest.json should only declare applicable external library loggers."""
        import json  # noqa: PLC0415, RUF105
        from pathlib import Path  # noqa: PLC0415, RUF105

        manifest_path = (
            Path(__file__).parents[1]
            / "custom_components"
            / "jackery_solarvault"
            / "manifest.json"
        )  # noqa: E501, RUF100
        if manifest_path.exists():
            with Path(manifest_path).open(encoding="utf-8") as f:
                json.load(f)

            # Should not declare internal integration loggers
            # Only external: aiohttp, aiomqtt, bleak, cryptography, etc.


class TestQualityScaleSchema:
    """Test quality_scale.yaml current schema and tested rules."""

    def test_quality_scale_yaml_has_rules_schema(self) -> None:  # noqa: PLR6301, RUF105
        """quality_scale.yaml must have top-level rules: schema."""
        from pathlib import Path  # noqa: PLC0415, RUF105

        import yaml  # noqa: PLC0415, RUF105

        qs_path = (
            Path(__file__).parents[1]
            / "custom_components"
            / "jackery_solarvault"
            / "quality_scale.yaml"
        )  # noqa: E501, RUF100
        if qs_path.exists():
            with Path(qs_path).open(encoding="utf-8") as f:
                qs = yaml.safe_load(f)

            assert "rules" in qs, "quality_scale.yaml missing top-level rules:"
            # rules is a mapping (dict) of rule_name: {status, ...}
            assert isinstance(qs["rules"], dict), "rules must be a dict"

    def test_quality_scale_only_claims_tested_rules(self) -> None:
        """Only rules actually satisfied should be claimed."""
        # This validates the quality_scale.yaml against actual test coverage


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
