"""Source-level MQTT protocol contract checks.

These tests avoid importing Home Assistant while guarding the app-captured
MQTT details documented in MQTT_PROTOCOL.md and APP_POLLING_MQTT.md.
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COORDINATOR_PATH = ROOT / "custom_components" / "jackery_solarvault" / "coordinator.py"
MQTT_PUSH_PATH = ROOT / "custom_components" / "jackery_solarvault" / "mqtt_push.py"
CONST_PATH = ROOT / "custom_components" / "jackery_solarvault" / "const.py"


def _read(path: Path) -> str:
    """Read source files as UTF-8 regardless of host locale.

    Windows defaults ``Path.read_text()`` to cp1252, which crashes on the
    UTF-8 docstrings used throughout the integration sources. Always
    pin UTF-8 here so tests are platform-agnostic.
    """
    return path.read_text(encoding="utf-8")


def _function_source(path: Path, name: str) -> str:
    source = _read(path)
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            assert node.end_lineno is not None
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    raise AssertionError(f"{name} not found in {path}")


def test_mqtt_setter_commands_match_app_protocol() -> None:
    """Implement test mqtt setter commands match app protocol."""
    eps = _function_source(COORDINATOR_PATH, "async_set_eps")
    assert "action_id=ACTION_ID_EPS_ENABLED" in eps
    assert "ACTION_ID_SOC_CHARGE_LIMIT" not in eps

    max_output = _function_source(COORDINATOR_PATH, "async_set_max_output_power")
    assert "message_type=MQTT_MESSAGE_CONTROL_COMBINE" in max_output
    assert "cmd=MQTT_CMD_CONTROL_COMBINE" in max_output

    soc_limits = _function_source(COORDINATOR_PATH, "async_set_soc_limits")
    assert "ACTION_ID_SOC_CHARGE_LIMIT" in soc_limits
    assert "ACTION_ID_SOC_DISCHARGE_LIMIT" in soc_limits
    assert "FIELD_SOC_CHARGE_LIMIT" in soc_limits
    assert "FIELD_SOC_DISCHARGE_LIMIT" in soc_limits

    query_combine = _function_source(COORDINATOR_PATH, "async_query_system_info")
    assert "ACTION_ID_QUERY_COMBINE_DATA" in query_combine
    assert "cmd=MQTT_CMD_QUERY_COMBINE_DATA" in query_combine


def test_mqtt_uses_captured_qos_zero() -> None:
    """Implement test mqtt uses captured qos zero."""
    mqtt_source = _read(MQTT_PUSH_PATH)
    coordinator_source = _read(COORDINATOR_PATH)

    assert "qos: int = 0" in mqtt_source
    assert "subscribe(topic, qos=0)" in mqtt_source
    assert (
        "async_publish_json(topic, payload, qos=0, retain=False)" in coordinator_source
    )


def test_mqtt_payload_data_field_is_normalized_to_body() -> None:
    """Implement test mqtt payload data field is normalized to body."""
    mqtt_source = _read(MQTT_PUSH_PATH)
    coordinator_source = _read(COORDINATOR_PATH)
    const_source = _read(CONST_PATH)

    assert 'FIELD_DATA: Final = "data"' in const_source
    assert 'FIELD_BODY: Final = "body"' in const_source
    assert 'MQTT_MESSAGE_CONTROL_COMBINE: Final = "ControlCombine"' in const_source
    assert "MQTT_CMD_CONTROL_COMBINE: Final = 121" in const_source
    assert "alt_body = data.get(FIELD_DATA)" in mqtt_source
    assert "data[FIELD_BODY] = alt_body" in mqtt_source
    assert "alt_body = payload.get(FIELD_DATA)" in coordinator_source


def test_mqtt_topics_follow_documented_app_layout() -> None:
    """Guard the hb/app/<userId>/... topics documented in MQTT_PROTOCOL.md."""
    const_source = _read(CONST_PATH)
    mqtt_source = _read(MQTT_PUSH_PATH)
    coordinator_source = _read(COORDINATOR_PATH)

    assert 'MQTT_TOPIC_PREFIX: Final = "hb/app"' in const_source
    for name, suffix in {
        "MQTT_TOPIC_DEVICE": "device",
        "MQTT_TOPIC_ALERT": "alert",
        "MQTT_TOPIC_CONFIG": "config",
        "MQTT_TOPIC_NOTICE": "notice",
        "MQTT_TOPIC_COMMAND": "command",
        "MQTT_TOPIC_ACTION": "action",
    }.items():
        assert f'{name}: Final = "{suffix}"' in const_source
    for name in (
        "MQTT_TOPIC_DEVICE",
        "MQTT_TOPIC_ALERT",
        "MQTT_TOPIC_CONFIG",
        "MQTT_TOPIC_NOTICE",
    ):
        assert name in const_source
    assert "MQTT_TOPIC_PREFIX" in mqtt_source
    assert "MQTT_TOPIC_SUFFIXES" in mqtt_source
    assert "MQTT_TOPIC_COMMAND" in coordinator_source


def test_mqtt_connect_requests_full_app_snapshot() -> None:
    """On reconnect the integration asks the app protocol for a fresh snapshot."""
    connected = _function_source(COORDINATOR_PATH, "_async_mqtt_connected")
    assert "_async_query_system_info_for_missing" in connected
    assert "_async_query_weather_plan_for_missing" in connected
    assert "_async_query_subdevices_for_missing" in connected
    assert "force=True" in connected
    assert "ensure_mqtt=False" in connected


def test_mqtt_credentials_are_derived_from_active_login_session() -> None:
    """The MQTT password must use the REST login userId/mqttPassWord/macId triple."""
    api_source = _read(ROOT / "custom_components" / "jackery_solarvault" / "api.py")
    login = _function_source(
        ROOT / "custom_components" / "jackery_solarvault" / "api.py", "async_login"
    )
    credentials = _function_source(
        ROOT / "custom_components" / "jackery_solarvault" / "api.py",
        "async_get_mqtt_credentials",
    )

    assert "self._mqtt_user_id" in login
    assert "FIELD_USER_ID" in login
    assert "self._mqtt_seed_b64" in login
    assert "FIELD_MQTT_PASSWORD" in login
    assert "self._mqtt_mac_id = mac_id" in login
    assert "base64.b64decode(self._mqtt_seed_b64, validate=True)" in credentials
    assert "_aes_cbc_encrypt" in credentials
    assert "MQTT_CLIENT_ID_SUFFIX" in api_source
    assert "MQTT_USERNAME_SEPARATOR" in api_source


def test_mqtt_protocol_md_is_pure_api_reference() -> None:
    """MQTT_PROTOCOL.md must remain a pure reverse-engineering reference.

    Per docs/STRICT_WORK_INSTRUCTIONS.md the protocol file documents
    paths, fields, frame formats and action IDs ONLY. Privacy policy,
    redaction rules, changelog notes and similar meta-content belong
    in STRICT_WORK_INSTRUCTIONS.md or DATA_SOURCE_PRIORITY.md. This
    test prevents drift back into the file.
    """
    protocol = (ROOT / "docs" / "MQTT_PROTOCOL.md").read_text(encoding="utf-8")
    forbidden_sections = (
        "## Diagnostics privacy",
        "## Topic redaction in diagnostics",
        "## Changelog",
        "## Migration",
        "## Release notes",
    )
    for section in forbidden_sections:
        assert section not in protocol, (
            f"MQTT_PROTOCOL.md must not contain section {section!r}; "
            "policy/process content belongs elsewhere"
        )
    # Sanity: the API-reference content must still be there
    assert "hb/app" in protocol, "topic structure missing"
    assert "DevicePropertyChange" in protocol, "command reference missing"
