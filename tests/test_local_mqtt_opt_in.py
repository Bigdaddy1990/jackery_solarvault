"""Unit tests for local_mqtt_opt_in function."""

import pytest
from unittest.mock import MagicMock

from custom_components.jackery_solarvault.const import (
    CONF_THIRD_PARTY_MQTT_ENABLE,
    DEFAULT_THIRD_PARTY_MQTT_ENABLE,
    CONF_LOCAL_MQTT_ENABLE,
    DEFAULT_LOCAL_MQTT_ENABLE,
)
from custom_components.jackery_solarvault.util import local_mqtt_opt_in


class MockConfigEntry:
    """Mock ConfigEntry with options and data dicts."""
    def __init__(self, options: dict = None, data: dict = None):
        self.options = options or {}
        self.data = data or {}

    def __contains__(self, key):
        return key in self.options or key in self.data

    def get(self, key, default=None):
        return self.options.get(key, self.data.get(key, default))


def test_local_mqtt_opt_in_legacy_true() -> None:
    """Explicit local_mqtt_enable=True should return True."""
    entry = MockConfigEntry(options={"local_mqtt_enable": True})
    assert local_mqtt_opt_in(entry) is True


def test_local_mqtt_opt_in_explicit_false_respected() -> None:
    """local_mqtt_enable=False (explicit) should be respected as user choice to disable."""
    entry = MockConfigEntry(
        options={"local_mqtt_enable": False},
        data={CONF_THIRD_PARTY_MQTT_ENABLE: True}
    )
    # Explicit False in options means user chose to disable local MQTT
    assert local_mqtt_opt_in(entry) is False


def test_local_mqtt_opt_in_legacy_false_third_party_false() -> None:
    """Both False should return False."""
    entry = MockConfigEntry(
        options={"local_mqtt_enable": False},
        data={CONF_THIRD_PARTY_MQTT_ENABLE: False}
    )
    assert local_mqtt_opt_in(entry) is False


def test_local_mqtt_opt_in_no_legacy_fallback_to_third_party_true() -> None:
    entry = MockConfigEntry(
        options={},
        data={CONF_THIRD_PARTY_MQTT_ENABLE: True}
    )
    assert local_mqtt_opt_in(entry) is True


def test_local_mqtt_opt_in_no_legacy_fallback_to_third_party_false() -> None:
    entry = MockConfigEntry(
        options={},
        data={CONF_THIRD_PARTY_MQTT_ENABLE: False}
    )
    assert local_mqtt_opt_in(entry) is False


def test_local_mqtt_opt_in_defaults_match_123_baseline() -> None:
    """Default constants: local_mqtt enabled by default, third_party_mqtt opt-in (disabled)."""
    assert DEFAULT_LOCAL_MQTT_ENABLE is True
    assert DEFAULT_THIRD_PARTY_MQTT_ENABLE is False


def test_local_mqtt_opt_in_empty_entry_defaults_to_enabled() -> None:
    """Empty entry should default to enabled (via DEFAULT_LOCAL_MQTT_ENABLE=True)."""
    entry = MockConfigEntry(options={}, data={})
    # Falls back to DEFAULT_LOCAL_MQTT_ENABLE which is True
    assert local_mqtt_opt_in(entry) is True


def test_local_mqtt_opt_in_legacy_missing_fallbacks_to_third_party() -> None:
    """Missing legacy key should fall back to third_party_mqtt_enable."""
    entry = MockConfigEntry(
        options={},  # no local_mqtt_enable
        data={CONF_THIRD_PARTY_MQTT_ENABLE: True}
    )
    assert local_mqtt_opt_in(entry) is True


def test_local_mqtt_opt_in_data_takes_precedence_when_no_options() -> None:
    """When no options, data should be used for both keys."""
    entry = MockConfigEntry(
        options={},
        data={"local_mqtt_enable": True, CONF_THIRD_PARTY_MQTT_ENABLE: False}
    )
    # Legacy in data wins
    assert local_mqtt_opt_in(entry) is True


def test_local_mqtt_opt_in_options_override_data() -> None:
    """Options should override data for both keys."""
    entry = MockConfigEntry(
        options={"local_mqtt_enable": False},
        data={"local_mqtt_enable": True, CONF_THIRD_PARTY_MQTT_ENABLE: False}
    )
    # Options win
    assert local_mqtt_opt_in(entry) is False