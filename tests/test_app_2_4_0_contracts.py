"""Regression contracts pinned to the authoritative Jackery 2.4.0 evidence."""

import ast
import base64
from pathlib import Path
from typing import Any, cast
from unittest.mock import AsyncMock, Mock

import pytest

from custom_components.jackery_solarvault import (
    const as const_module,
    services as services_module,
)
from custom_components.jackery_solarvault.client import api as api_module
from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.const import (
    ACTION_ID_FAULT_ALARM_REPORT,
    ACTION_ID_PORTABLE_ENERGY_SAVING,
    ACTION_ID_PORTABLE_SET_PEAKS_TROUGHS,
    ACTION_ID_PORTABLE_WRITE_WIFI_INFO,
    ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
    ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG,
    ACTION_ID_WRITE_WIFI_INFO,
    DYNAMIC_PRICE_PATH,
    FIELD_BAT_STATE,
    FIELD_SOC,
    MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG,
    MQTT_CMD_THIRD_PARTY_MQTT_CONFIG,
    MQTT_CMD_UPLOAD_DEVICE_ALERT,
    MQTT_CMD_WRITE_WIFI_INFO,
    PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID,
    PV_TRENDS_PATH,
    SYSTEM_INFO_KEYS,
)
from tests.fixtures.jackery_app_2_4_0_contracts import (  # ruff: ignore[banned-api]
    HOME_COMMANDS,
    HTTP_SETTER_FAMILIES,
    PORTABLE_COMMANDS,
    REALTIME_CONTROL_TRANSPORTS,
    REST_ENDPOINTS,
    STAT_FIELD_OWNERS,
    SYSTEM_BODY_FIELDS,
)


def test_app_240_uses_exact_pv_trends_path() -> None:
    """The PV-trends constant matches the path used by the current App."""
    assert REST_ENDPOINTS["pv_trends"] == PV_TRENDS_PATH


def test_app_240_uses_exact_dynamic_price_path() -> None:
    """The dynamic-price constant has no unproven version segment."""
    assert REST_ENDPOINTS["dynamic_price"] == DYNAMIC_PRICE_PATH


def test_app_240_does_not_expose_unproven_aiems_endpoint() -> None:
    """The unproven report endpoint is absent from API and service surfaces."""
    assert not hasattr(JackeryApi, "async_get_aiems_energy_prediction")
    assert not hasattr(const_module, "AIEMS_ENERGY_PREDICTION_PATH")
    assert not hasattr(const_module, "SERVICE_GET_AIEMS_ENERGY_PREDICTION")
    assert "get_aiems_energy_prediction" not in {
        registration.name
        for registration in services_module._service_registrations()  # ruff: ignore[private-member-access]
    }


def test_app_240_has_no_universal_http_or_websocket_control() -> None:
    """Only App-proven real-time transports are part of the control contract."""
    assert {"ble", "cloud_mqtt"} == REALTIME_CONTROL_TRANSPORTS
    assert "http" not in REALTIME_CONTROL_TRANSPORTS
    assert "websocket" not in REALTIME_CONTROL_TRANSPORTS
    assert {"system_name", "dynamic_price", "tariff"} == HTTP_SETTER_FAMILIES


def test_app_240_system_body_fields_survive_http_normalization() -> None:
    """HTTP-only mode keeps both battery fields owned by SystemBody."""
    assert {"soc", "batState"} == SYSTEM_BODY_FIELDS
    assert {FIELD_SOC, FIELD_BAT_STATE} <= SYSTEM_INFO_KEYS


def test_app_240_statistics_fields_have_distinct_dto_owners() -> None:
    """Similar energy fields remain tied to their exact App DTO."""
    assert STAT_FIELD_OWNERS == {
        "home_grid_import": ("HomeStat", "totalInGridEnergy"),
        "home_grid_export": ("HomeStat", "totalOutGridEnergy"),
        "home_energy": ("SysHomeStat", "totalHomeEgy"),
    }


def test_app_240_home_high_risk_command_pairs() -> None:
    """Home command identifiers match HomeCmdAction evidence."""
    assert HOME_COMMANDS["write_wifi_info"].action_id == ACTION_ID_WRITE_WIFI_INFO
    assert HOME_COMMANDS["write_wifi_info"].mqtt_command == MQTT_CMD_WRITE_WIFI_INFO
    assert HOME_COMMANDS["fault_alarm_report"].action_id == ACTION_ID_FAULT_ALARM_REPORT
    assert (
        HOME_COMMANDS["fault_alarm_report"].mqtt_command == MQTT_CMD_UPLOAD_DEVICE_ALERT
    )
    assert (
        HOME_COMMANDS["set_third_party_mqtt"].action_id
        == ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG
    )
    assert (
        HOME_COMMANDS["set_third_party_mqtt"].ble_message_type
        == MQTT_CMD_THIRD_PARTY_MQTT_CONFIG
    )
    assert (
        HOME_COMMANDS["query_third_party_mqtt"].action_id
        == ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG
    )
    assert (
        HOME_COMMANDS["query_third_party_mqtt"].ble_message_type
        == MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG
    )


def test_app_240_portable_high_risk_command_pairs() -> None:
    """Portable commands use the Kotlin-default BLE type where applicable."""
    expected_actions = {
        "write_wifi_info": ACTION_ID_PORTABLE_WRITE_WIFI_INFO,
        "setting_energy_saving": ACTION_ID_PORTABLE_ENERGY_SAVING,
        "set_peaks_troughs": ACTION_ID_PORTABLE_SET_PEAKS_TROUGHS,
    }
    for name, action_id in expected_actions.items():
        contract = PORTABLE_COMMANDS[name]
        assert action_id == contract.action_id
        assert (
            PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[action_id] == contract.ble_message_type
        )


def _hydrated_api(seed_b64: str) -> JackeryApi:
    """Return an API client with a cached MQTT session and no HTTP token."""
    api = JackeryApi(cast("Any", Mock()), "tester@example.com", "secret")
    api.hydrate_mqtt_session(
        user_id="user-1",
        seed_b64=seed_b64,
        mac_id="aabbccddeeff",
    )
    return api


@pytest.mark.asyncio
async def test_mqtt_credential_alias_is_cache_only() -> None:
    """The compatibility alias never turns a transport read into HTTP login."""
    api = _hydrated_api(base64.b64encode(bytes(range(32))).decode("ascii"))
    login = AsyncMock(side_effect=AssertionError("credential read must not log in"))
    api.async_login = login

    assert await api.async_get_mqtt_credentials() == api.get_cached_mqtt_credentials()
    login.assert_not_awaited()


def test_malformed_cached_mqtt_seed_is_rejected_without_exception() -> None:
    """Invalid cached session material degrades to unavailable credentials."""
    api = _hydrated_api("not-valid-base64***")

    assert api.get_cached_mqtt_credentials() is None


def test_wrong_length_cached_mqtt_seed_is_rejected_without_exception() -> None:
    """A decodable non-AES-256 seed is still unusable cache material."""
    api = _hydrated_api(base64.b64encode(b"too-short").decode("ascii"))

    assert api.get_cached_mqtt_credentials() is None


def test_mqtt_credential_alias_has_one_implementation() -> None:
    """A later duplicate definition cannot silently replace cache-only behavior."""
    module = ast.parse(Path(api_module.__file__).read_text(encoding="utf-8"))
    jackery_api = next(
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "JackeryApi"
    )

    definitions = [
        node
        for node in jackery_api.body
        if isinstance(node, ast.AsyncFunctionDef)
        and node.name == "async_get_mqtt_credentials"
    ]
    assert len(definitions) == 1
