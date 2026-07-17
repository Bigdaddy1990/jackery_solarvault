"""Unit coverage for Jackery config-flow option helpers."""

# ruff:file-ignore[private-member-access]

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault import config_flow
from custom_components.jackery_solarvault.const import (
    CONF_ENABLE_BLE_TRANSPORT,
    CONF_ENABLE_DERIVED_HOME_ENERGY_FALLBACK,
    CONF_ENABLE_WEEK_STATISTICS,
    CONF_LOCAL_MQTT_ENABLE,
    CONF_LOCAL_MQTT_HOST,
    CONF_LOCAL_MQTT_PASSWORD,
    CONF_LOCAL_MQTT_PORT,
    CONF_LOCAL_MQTT_TOPIC,
    CONF_LOCAL_MQTT_USERNAME,
    CONF_REGION_CODE,
    CONF_THIRD_PARTY_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_PORT,
    CONF_THIRD_PARTY_MQTT_TOKEN,
    CONF_THIRD_PARTY_MQTT_TOPIC_FILTER,
    CONF_THIRD_PARTY_MQTT_USERNAME,
    DEFAULT_LOCAL_MQTT_PORT,
    DOMAIN,
    ENTRY_BOOTSTRAP_MQTT_SESSION,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

if TYPE_CHECKING:
    from custom_components.jackery_solarvault.client import JackeryApi

_ACCOUNT = "owner@example.com"
_PASSWORD = "secret"
_REGION = "eu"
_LOCAL_PORT = 1884
_LEGACY_PORT = 1885
_SUBMITTED_PORT = 1886


def test_normalize_account_strips_whitespace() -> None:
    """Account ids are normalized before unique-id and reauth checks."""
    assert config_flow._normalize_account(" owner@example.com ") == _ACCOUNT


def test_flow_options_preserves_current_and_third_party_fields() -> None:
    """Option merging includes the full persistable option surface."""
    result = config_flow._flow_options(
        {CONF_ENABLE_WEEK_STATISTICS: True},
        {
            CONF_ENABLE_BLE_TRANSPORT: False,
            CONF_THIRD_PARTY_MQTT_IP: "broker.local",
            CONF_THIRD_PARTY_MQTT_PORT: _LOCAL_PORT,
        },
    )

    assert result[CONF_ENABLE_WEEK_STATISTICS] is True
    assert result[CONF_ENABLE_BLE_TRANSPORT] is False
    assert result[CONF_THIRD_PARTY_MQTT_IP] == "broker.local"
    assert result[CONF_THIRD_PARTY_MQTT_PORT] == _LOCAL_PORT


def test_entry_data_from_api_login_keeps_region_and_mqtt_bootstrap() -> None:
    """Successful login data carries credentials, region fallback, and MQTT snapshot."""
    api = SimpleNamespace(
        region_code="",
        mqtt_session_snapshot=lambda: {"user_id": "mqtt-user"},
    )
    existing_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_REGION_CODE: _REGION},
    )

    data = config_flow._entry_data_from_api_login(
        _ACCOUNT,
        _PASSWORD,
        cast("JackeryApi", api),
        existing_entry,
    )

    assert data == {
        CONF_USERNAME: _ACCOUNT,
        CONF_PASSWORD: _PASSWORD,
        CONF_REGION_CODE: _REGION,
        ENTRY_BOOTSTRAP_MQTT_SESSION: {"user_id": "mqtt-user"},
    }


def test_entry_data_from_api_login_prefers_api_region() -> None:
    """Fresh API region wins over an older config-entry region."""
    api = SimpleNamespace(
        region_code="us",
        mqtt_session_snapshot=lambda: None,
    )
    existing_entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_REGION_CODE: _REGION},
    )

    data = config_flow._entry_data_from_api_login(
        _ACCOUNT,
        _PASSWORD,
        cast("JackeryApi", api),
        existing_entry,
    )

    assert data[CONF_REGION_CODE] == "us"
    assert ENTRY_BOOTSTRAP_MQTT_SESSION not in data


def test_local_mqtt_port_coercion_falls_back_to_default() -> None:
    """Invalid stored Local-MQTT port values cannot poison config options."""
    assert config_flow._coerce_local_mqtt_port(None) == DEFAULT_LOCAL_MQTT_PORT
    assert config_flow._coerce_local_mqtt_port("") == DEFAULT_LOCAL_MQTT_PORT
    assert config_flow._coerce_local_mqtt_port(str(_LOCAL_PORT)) == _LOCAL_PORT
    assert config_flow._coerce_local_mqtt_port(object()) == DEFAULT_LOCAL_MQTT_PORT
    assert config_flow._coerce_local_mqtt_port("not-a-port") == DEFAULT_LOCAL_MQTT_PORT


def test_current_local_mqtt_options_reads_new_and_legacy_keys() -> None:
    """Stored Local-MQTT options accept the current keys and legacy UI keys."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        options={
            CONF_THIRD_PARTY_MQTT_ENABLE: True,
            CONF_THIRD_PARTY_MQTT_IP: " broker.local ",
            CONF_THIRD_PARTY_MQTT_PORT: str(_LEGACY_PORT),
            CONF_THIRD_PARTY_MQTT_USERNAME: " user ",
            CONF_THIRD_PARTY_MQTT_PASSWORD: " pass ",
            CONF_LOCAL_MQTT_TOPIC: " jackery/# ",
        },
    )

    result = config_flow._current_local_mqtt_options(entry)

    assert result == {
        CONF_LOCAL_MQTT_ENABLE: True,
        CONF_LOCAL_MQTT_HOST: "broker.local",
        CONF_LOCAL_MQTT_PORT: _LEGACY_PORT,
        CONF_LOCAL_MQTT_USERNAME: "user",
        CONF_LOCAL_MQTT_PASSWORD: " pass ",
        CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: "jackery/#",
    }


def test_merge_local_mqtt_options_prefers_submitted_local_keys() -> None:
    """Direct local keys beat legacy form keys while omitted values are preserved."""
    current: dict[str, Any] = {
        CONF_LOCAL_MQTT_ENABLE: False,
        CONF_LOCAL_MQTT_HOST: "old.local",
        CONF_LOCAL_MQTT_PORT: _LOCAL_PORT,
        CONF_LOCAL_MQTT_USERNAME: "old-user",
        CONF_LOCAL_MQTT_PASSWORD: "old-pass",
        CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: "old/#",
    }

    result = config_flow._merge_local_mqtt_options(
        {
            CONF_LOCAL_MQTT_ENABLE: True,
            CONF_LOCAL_MQTT_HOST: " local.new ",
            CONF_THIRD_PARTY_MQTT_IP: "legacy.new",
            CONF_THIRD_PARTY_MQTT_PORT: str(_SUBMITTED_PORT),
            CONF_THIRD_PARTY_MQTT_USERNAME: "new-user",
            CONF_THIRD_PARTY_MQTT_PASSWORD: "new-pass",
            CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: " new/# ",
        },
        current,
    )

    assert result == {
        CONF_LOCAL_MQTT_ENABLE: True,
        CONF_LOCAL_MQTT_HOST: "local.new",
        CONF_LOCAL_MQTT_PORT: _SUBMITTED_PORT,
        CONF_LOCAL_MQTT_USERNAME: "new-user",
        CONF_LOCAL_MQTT_PASSWORD: "new-pass",
        CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: "new/#",
    }


def test_reconfigure_options_preserves_unexposed_existing_options() -> None:
    """Credential reconfigure keeps existing options while applying safe form fields."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={},
        options={
            "unexposed": "keep-me",
            CONF_LOCAL_MQTT_ENABLE: False,
            CONF_LOCAL_MQTT_HOST: "old.local",
            CONF_LOCAL_MQTT_PORT: _LOCAL_PORT,
            CONF_LOCAL_MQTT_USERNAME: "old-user",
            CONF_LOCAL_MQTT_PASSWORD: "old-pass",
            CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: "old/#",
        },
    )

    result = config_flow._reconfigure_options(
        entry,
        {
            CONF_ENABLE_DERIVED_HOME_ENERGY_FALLBACK: True,
            CONF_THIRD_PARTY_MQTT_ENABLE: True,
            CONF_THIRD_PARTY_MQTT_IP: "new.local",
            CONF_THIRD_PARTY_MQTT_TOKEN: "token",
        },
    )

    assert result["unexposed"] == "keep-me"
    assert result[CONF_ENABLE_DERIVED_HOME_ENERGY_FALLBACK] is True
    assert result[CONF_LOCAL_MQTT_ENABLE] is True
    assert result[CONF_LOCAL_MQTT_HOST] == "new.local"
    assert result[CONF_LOCAL_MQTT_USERNAME] == "old-user"
    assert result[CONF_LOCAL_MQTT_PASSWORD] == "old-pass"
    assert result[CONF_THIRD_PARTY_MQTT_TOKEN] == "token"
