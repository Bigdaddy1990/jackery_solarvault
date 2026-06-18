"""Regression tests for Jackery HTTP/BLE/MQTT command-routing contracts."""

import ast
import asyncio
import base64
import json
from pathlib import Path
import re
from typing import Any

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
import pytest

from custom_components.jackery_solarvault.client.mqtt_command import (
    command_body_for_transport,
    publish_mqtt_command,
)
from custom_components.jackery_solarvault.const import (
    ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG,
    FIELD_ACTION_ID,
    FIELD_BODY,
    FIELD_CMD,
    FIELD_MESSAGE_TYPE,
    FIELD_TIMESTAMP,
    FIELD_VERSION,
    MQTT_CREDENTIAL_USER_ID,
    MQTT_MESSAGE_THIRD_PARTY_MQTT_CONFIG,
    MQTT_TOPIC_COMMAND,
    MQTT_TOPIC_PREFIX,
)

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "jackery_solarvault"
COORDINATOR_PATH = INTEGRATION / "coordinator.py"
SERVICES_PATH = INTEGRATION / "services.py"
BUTTON_PATH = INTEGRATION / "button.py"
CONST_PATH = INTEGRATION / "const.py"


class _AcceptingMqtt:
    """MQTT fake capturing published payloads."""

    is_connected = True

    def __init__(self) -> None:
        self.diagnostics: dict[str, object] = {}
        self.calls: list[tuple[str, dict[str, Any], int, bool]] = []

    async def async_publish_json(
        self, topic: str, payload: dict[str, Any], *, qos: int, retain: bool
    ) -> None:
        """Capture the outgoing publish."""
        self.calls.append((topic, payload, qos, retain))


class _CredentialApi:
    """API fake serving MQTT credentials."""

    @staticmethod
    async def async_get_mqtt_credentials() -> dict[str, str]:
        """Return the user id required to build the command topic."""
        return {MQTT_CREDENTIAL_USER_ID: "user-1"}


def _read(path: Path) -> str:
    """Return UTF-8 source text."""
    return path.read_text(encoding="utf-8")


def _tree(path: Path) -> ast.Module:
    """Parse a Python file."""
    return ast.parse(_read(path))


def _maybe_function_node(
    path: Path, name: str
) -> ast.FunctionDef | ast.AsyncFunctionDef | None:
    """Return the named function node when present."""
    for node in ast.walk(_tree(path)):
        if (
            isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
            and node.name == name
        ):
            return node
    return None


def _function_node(path: Path, name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    """Return the named function node or fail the test."""
    node = _maybe_function_node(path, name)
    if node is not None:
        return node
    pytest.fail(f"{name} not found in {path}")


def _function_source(path: Path, name: str) -> str:
    """Return the named function's source."""
    source = _read(path)
    lines = source.splitlines()
    node = _function_node(path, name)
    assert node.end_lineno is not None
    return "\n".join(lines[node.lineno - 1 : node.end_lineno])


def _called_attribute_names(node: ast.AST) -> set[str]:
    """Return attribute call names inside an AST node."""
    names: set[str] = set()
    for child in ast.walk(node):
        if isinstance(child, ast.Call) and isinstance(child.func, ast.Attribute):
            names.add(child.func.attr)
    return names


def _decrypt_layer_c(ciphertext_b64: str, key: bytes) -> dict[str, Any]:
    """Decrypt a Layer-C body with AES-128-CBC/PKCS7 and IV=key."""
    cipher = Cipher(algorithms.AES(key), modes.CBC(key))
    decryptor = cipher.decryptor()
    padded = decryptor.update(base64.b64decode(ciphertext_b64)) + decryptor.finalize()
    unpadder = PKCS7(algorithms.AES.block_size).unpadder()
    plaintext = unpadder.update(padded) + unpadder.finalize()
    decoded = json.loads(plaintext.decode("utf-8"))
    assert isinstance(decoded, dict)
    return decoded


@pytest.mark.parametrize(
    ["method", "api_call"],
    [
        ["async_set_single_price", "async_set_single_mode"],
        ["async_set_price_source", "async_set_dynamic_mode"],
        ["async_set_price_mode_dynamic", "async_set_dynamic_mode"],
    ],
)
def test_http_price_setters_do_not_bypass_api_primary_path(
    method: str, api_call: str
) -> None:
    """HTTP-backed setters must use the API client before local state patches."""
    source = _function_source(COORDINATOR_PATH, method)
    assert f"self.api.{api_call}" in source
    assert source.index(f"self.api.{api_call}") < source.index(
        "_apply_local_price_patch"
    )
    assert "_async_publish_command(" not in source


@pytest.mark.parametrize(
    "method",
    [
        "async_set_eps",
        "async_set_soc_limits",
        "async_set_max_feed_grid",
        "async_set_third_party_mqtt_config",
        "async_send_device_schedule",
    ],
)
def test_ble_mqtt_setters_route_through_ble_first_fallback(method: str) -> None:
    """Non-HTTP command writers must keep BLE before MQTT fallback."""
    source = _function_source(COORDINATOR_PATH, method)
    assert "_async_publish_command_ble_first" in source
    assert "_async_publish_command(" not in source.replace(
        "_async_publish_command_ble_first", ""
    )


@pytest.mark.parametrize(
    ["handler", "coordinator_call"],
    [
        [
            "_async_handle_set_third_party_mqtt_config",
            "async_set_third_party_mqtt_config",
        ],
        [
            "_async_handle_query_third_party_mqtt_config",
            "async_query_third_party_mqtt_config",
        ],
        ["_async_handle_send_device_schedule", "async_send_device_schedule"],
    ],
)
def test_services_delegate_to_coordinator_routes(
    handler: str, coordinator_call: str
) -> None:
    """HA services must delegate into coordinator routers, not MQTT directly."""
    source = _function_source(SERVICES_PATH, handler)
    assert f"coordinator.{coordinator_call}" in source
    assert "_async_publish_command(" not in source
    assert "publish_mqtt_command" not in source


def test_button_descriptions_keep_coordinator_ble_first_actions() -> None:
    """Button action wrappers must call coordinator methods that preserve BLE-first."""
    button_source = _read(BUTTON_PATH)
    for description_name in (
        "refresh_wifi_list",
        "refresh_time_zone",
        "sync_time_zone",
        "sync_cloud_mqtt_info",
        "refresh_device_ota_version",
        "refresh_third_party_mqtt_config",
        "refresh_wifi_config",
        "portable_restart",
    ):
        assert description_name in button_source
    for coordinator_method in (
        "async_query_wifi_list",
        "async_get_time_zone",
        "async_send_time_zone",
        "async_sync_mqtt_connect_info",
        "async_query_device_ota_version",
        "async_query_third_party_mqtt_config",
        "async_query_wifi_config",
        "async_send_portable_command",
    ):
        assert "_async_publish_command_ble_first" in _function_source(
            COORDINATOR_PATH, coordinator_method
        )


async def test_mqtt_command_encrypts_layer_c_body_without_http_payload_shape() -> None:
    """MQTT command bodies must be Layer-C encrypted and keep MQTT subcommands."""
    mqtt = _AcceptingMqtt()
    key = b"0123456789abcdef"

    async def _noop() -> None:
        await asyncio.sleep(0)

    await publish_mqtt_command(
        mqtt=mqtt,
        api=_CredentialApi(),
        device_id="dev1",
        device_sn="SN123",
        bt_key=key,
        message_type=MQTT_MESSAGE_THIRD_PARTY_MQTT_CONFIG,
        action_id=ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG,
        cmd=113,
        body_fields={"enable": 1, "ip": "broker.local", "port": 1883},
        ensure_mqtt_cb=_noop,
        relogin_cb=_noop,
        stop_mqtt_cb=_noop,
    )

    topic, payload, qos, retain = mqtt.calls[0]
    assert topic == f"{MQTT_TOPIC_PREFIX}/user-1/{MQTT_TOPIC_COMMAND}"
    assert qos == 0
    assert retain is False
    assert payload[FIELD_VERSION] == 0
    assert payload[FIELD_MESSAGE_TYPE] == MQTT_MESSAGE_THIRD_PARTY_MQTT_CONFIG
    assert payload[FIELD_ACTION_ID] == ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG
    assert isinstance(payload[FIELD_TIMESTAMP], int)
    assert isinstance(payload[FIELD_BODY], str)
    assert "broker.local" not in payload[FIELD_BODY]
    assert _decrypt_layer_c(payload[FIELD_BODY], key) == {
        "enable": 1,
        "ip": "broker.local",
        "port": 1883,
        FIELD_CMD: 113,
    }
    assert "system_id" not in _decrypt_layer_c(payload[FIELD_BODY], key)
    assert "single_price" not in _decrypt_layer_c(payload[FIELD_BODY], key)


@pytest.mark.parametrize(
    "bad_cmd",
    [0, -1],
)
def test_transport_body_does_not_inject_invalid_mqtt_subcommands(bad_cmd: int) -> None:
    """MQTT subcommand formatting must never fake HTTP payload fields."""
    body = command_body_for_transport({"enable": 1}, cmd=bad_cmd)
    assert FIELD_CMD not in body
    assert "actionId" not in body
    assert "systemId" not in body


def test_documented_action_ids_have_dedicated_writer_or_router() -> None:
    """Every imported ACTION_ID used for HA buttons must route via coordinator."""
    const_source = _read(CONST_PATH)
    coordinator_source = _read(COORDINATOR_PATH)
    button_source = _read(BUTTON_PATH)
    for action_name in sorted(set(re.findall(r"ACTION_ID_[A-Z0-9_]+", button_source))):
        assert f"{action_name}: Final" in const_source
        assert action_name in coordinator_source or action_name.startswith(
            "ACTION_ID_PORTABLE_"
        )
    assert "async_send_portable_command" in coordinator_source


def test_no_new_ha_control_path_publishes_mqtt_only_without_fallback() -> None:
    """HA-facing service/control routers must not introduce MQTT-only writes."""
    forbidden_functions = {
        "_async_handle_set_third_party_mqtt_config",
        "_async_handle_query_third_party_mqtt_config",
        "_async_handle_send_device_schedule",
        "async_set_eps",
        "async_set_soc_limits",
        "async_set_max_feed_grid",
        "async_set_third_party_mqtt_config",
        "async_send_device_schedule",
    }
    for path in (SERVICES_PATH, COORDINATOR_PATH):
        for name in forbidden_functions:
            node = _maybe_function_node(path, name)
            if node is None:
                continue
            calls = _called_attribute_names(node)
            assert "publish_mqtt_command" not in calls
            assert "_async_publish_command" not in calls
