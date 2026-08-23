"""Unit tests for local_mqtt_opt_in function."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import custom_components.jackery_solarvault as integration
from custom_components.jackery_solarvault.config_flow import _current_local_mqtt_options
from custom_components.jackery_solarvault.const import (
    CONF_THIRD_PARTY_MQTT_ENABLE,
    DEFAULT_LOCAL_MQTT_ENABLE,
    DEFAULT_THIRD_PARTY_MQTT_ENABLE,
)
from custom_components.jackery_solarvault.util import local_mqtt_opt_in


class MockConfigEntry:
    """Mock ConfigEntry with options and data dicts."""

    def __init__(self, options: dict | None = None, data: dict | None = None) -> None:  # noqa: D107
        self.options = options or {}
        self.data = data or {}

    def __contains__(self, key) -> bool:  # noqa: D105
        return key in self.options or key in self.data

    def get(self, key, default=None):  # noqa: D102
        return self.options.get(key, self.data.get(key, default))


def test_local_mqtt_opt_in_legacy_true() -> None:
    """Explicit local_mqtt_enable=True should return True."""
    entry = MockConfigEntry(options={"local_mqtt_enable": True})
    assert local_mqtt_opt_in(entry) is True


def test_local_mqtt_opt_in_explicit_false_respected() -> None:
    """local_mqtt_enable=False (explicit) should be respected as user choice to disable."""
    entry = MockConfigEntry(
        options={"local_mqtt_enable": False}, data={CONF_THIRD_PARTY_MQTT_ENABLE: True}
    )
    # Explicit False in options means user chose to disable local MQTT
    assert local_mqtt_opt_in(entry) is False


def test_local_mqtt_opt_in_legacy_false_third_party_false() -> None:
    """Both False should return False."""
    entry = MockConfigEntry(
        options={"local_mqtt_enable": False}, data={CONF_THIRD_PARTY_MQTT_ENABLE: False}
    )
    assert local_mqtt_opt_in(entry) is False


def test_local_mqtt_opt_in_no_legacy_fallback_to_third_party_true() -> None:  # noqa: D103
    entry = MockConfigEntry(options={}, data={CONF_THIRD_PARTY_MQTT_ENABLE: True})
    assert local_mqtt_opt_in(entry) is True


def test_local_mqtt_opt_in_no_legacy_fallback_to_third_party_false() -> None:  # noqa: D103
    entry = MockConfigEntry(options={}, data={CONF_THIRD_PARTY_MQTT_ENABLE: False})
    assert local_mqtt_opt_in(entry) is False


def test_local_mqtt_defaults_have_one_canonical_value() -> None:
    """Runtime and OptionsFlow cannot disagree for an unconfigured entry."""
    assert DEFAULT_LOCAL_MQTT_ENABLE is DEFAULT_THIRD_PARTY_MQTT_ENABLE


def test_local_mqtt_opt_in_empty_entry_matches_options_flow() -> None:
    """An empty entry remains disabled in both UI state and runtime."""
    entry = MockConfigEntry(options={}, data={})
    flow_options = _current_local_mqtt_options(entry)  # type: ignore[arg-type]

    assert local_mqtt_opt_in(entry) is False
    assert local_mqtt_opt_in(entry) is flow_options[CONF_THIRD_PARTY_MQTT_ENABLE]


@pytest.mark.parametrize("enabled", [False, True])
def test_local_mqtt_explicit_canonical_option_matches_options_flow(
    enabled: bool,
) -> None:
    """Explicit canonical choices have identical Flow and runtime semantics."""
    entry = MockConfigEntry(options={CONF_THIRD_PARTY_MQTT_ENABLE: enabled})
    flow_options = _current_local_mqtt_options(entry)  # type: ignore[arg-type]

    assert local_mqtt_opt_in(entry) is enabled
    assert flow_options[CONF_THIRD_PARTY_MQTT_ENABLE] is enabled


def test_local_mqtt_opt_in_legacy_missing_fallbacks_to_third_party() -> None:
    """Missing legacy key should fall back to third_party_mqtt_enable."""
    entry = MockConfigEntry(
        options={},  # no local_mqtt_enable
        data={CONF_THIRD_PARTY_MQTT_ENABLE: True},
    )
    assert local_mqtt_opt_in(entry) is True


def test_canonical_data_overrides_stale_legacy_data() -> None:
    """Within entry data, the current canonical key beats its retired alias."""
    entry = MockConfigEntry(
        options={},
        data={"local_mqtt_enable": True, CONF_THIRD_PARTY_MQTT_ENABLE: False},
    )
    assert local_mqtt_opt_in(entry) is False


def test_local_mqtt_opt_in_options_override_data() -> None:
    """Options should override data for both keys."""
    entry = MockConfigEntry(
        options={"local_mqtt_enable": False},
        data={"local_mqtt_enable": True, CONF_THIRD_PARTY_MQTT_ENABLE: False},
    )
    # Options win
    assert local_mqtt_opt_in(entry) is False


def test_canonical_option_overrides_stale_legacy_data() -> None:
    """A current options-flow choice wins over obsolete config-entry data."""
    entry = MockConfigEntry(
        options={CONF_THIRD_PARTY_MQTT_ENABLE: False},
        data={"local_mqtt_enable": True},
    )

    assert local_mqtt_opt_in(entry) is False


def test_canonical_option_overrides_stale_legacy_option() -> None:
    """The current option must beat a conflicting key from the retired form."""
    entry = MockConfigEntry(
        options={
            CONF_THIRD_PARTY_MQTT_ENABLE: False,
            "local_mqtt_enable": True,
        },
    )

    assert local_mqtt_opt_in(entry) is False


def test_legacy_migration_preserves_existing_canonical_disable() -> None:
    """Migration removes the legacy key without re-enabling Local MQTT."""
    hass = MagicMock()
    entry = SimpleNamespace(
        options={
            CONF_THIRD_PARTY_MQTT_ENABLE: False,
            "local_mqtt_enable": True,
        }
    )

    integration._async_migrate_legacy_local_mqtt_options(hass, entry)

    migrated = hass.config_entries.async_update_entry.call_args.kwargs["options"]
    assert migrated[CONF_THIRD_PARTY_MQTT_ENABLE] is False
    assert "local_mqtt_enable" not in migrated
