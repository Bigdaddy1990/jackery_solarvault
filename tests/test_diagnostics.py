"""Tests for mandatory diagnostics credential redaction."""

from types import SimpleNamespace

from custom_components.jackery_solarvault.const import (
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_IP as CONF_LOCAL_MQTT_HOST,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_PASSWORD as CONF_LOCAL_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_TOKEN,
    CONF_THIRD_PARTY_MQTT_USERNAME,
    CONF_THIRD_PARTY_MQTT_USERNAME as CONF_LOCAL_MQTT_USERNAME,
    REDACT_KEYS,
)
from custom_components.jackery_solarvault.util import active_redact_keys
from homeassistant.components.diagnostics import REDACTED, async_redact_data


def test_local_and_third_party_mqtt_credentials_redacted_from_options() -> None:
    """Local- and third-party-MQTT broker credentials never leave the export.

    `async_get_config_entry_diagnostics` redacts `entry.options` with
    `async_redact_data(dict(entry.options), active_redact_keys(entry))`
    (diagnostics.py). Every credential-bearing MQTT broker option key —
    both the canonical local-broker keys and their legacy
    ``third_party_mqtt_*`` aliases, which are also real persisted option
    keys for the separate third-party-broker feature — must be listed in
    `REDACT_KEYS`, or a diagnostics export leaks broker passwords/tokens
    in cleartext.
    """
    entry = SimpleNamespace(
        options={
            CONF_LOCAL_MQTT_HOST: "192.168.1.50",
            CONF_LOCAL_MQTT_USERNAME: "mqtt-user",
            CONF_LOCAL_MQTT_PASSWORD: "super-secret",
            CONF_THIRD_PARTY_MQTT_IP: "192.168.1.60",
            CONF_THIRD_PARTY_MQTT_USERNAME: "third-party-user",
            CONF_THIRD_PARTY_MQTT_PASSWORD: "third-party-secret",
            CONF_THIRD_PARTY_MQTT_TOKEN: "third-party-token",
        },
    )

    redacted = async_redact_data(dict(entry.options), active_redact_keys())

    assert redacted[CONF_LOCAL_MQTT_HOST] == REDACTED
    assert redacted[CONF_LOCAL_MQTT_USERNAME] == REDACTED
    assert redacted[CONF_LOCAL_MQTT_PASSWORD] == REDACTED
    assert redacted[CONF_THIRD_PARTY_MQTT_IP] == REDACTED
    assert redacted[CONF_THIRD_PARTY_MQTT_USERNAME] == REDACTED
    assert redacted[CONF_THIRD_PARTY_MQTT_PASSWORD] == REDACTED
    assert redacted[CONF_THIRD_PARTY_MQTT_TOKEN] == REDACTED


def test_redaction_key_contract_is_immutable_and_mandatory() -> None:
    """The share-safe redaction key set cannot be cleared per config entry."""
    assert isinstance(REDACT_KEYS, frozenset)
    assert active_redact_keys() == REDACT_KEYS
    assert REDACT_KEYS
