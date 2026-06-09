"""Regression tests for high-impact Jackery runtime edge cases."""

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text()


def test_number_native_value_falls_back_after_unparseable_primary_key() -> None:
    """Number entities must continue through source keys when one value is invalid."""
    source = _source("custom_components/jackery_solarvault/number.py")

    assert "parsed = safe_float(val)" in source
    assert "if parsed is not None:" in source
    assert "return parsed" in source
    assert "return safe_float(val)" not in source


def test_local_mqtt_diagnostics_uses_live_client_snapshot() -> None:
    """Diagnostics must report the running local MQTT client when it exists."""
    source = _source("custom_components/jackery_solarvault/diagnostics.py")

    assert "bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)" in source
    assert "client = bucket.get(_LOCAL_MQTT_RUNTIME_KEY)" in source
    assert 'snapshot = getattr(client, "diagnostics_snapshot", None)' in source
    assert 'return {"enabled": False}' in source


def test_diagnostics_export_includes_rejection_metrics_and_schema_version() -> None:
    """Diagnostics payload must include Platinum-mandated rejection_metrics + schema_version."""  # noqa: E501
    source = _source("custom_components/jackery_solarvault/diagnostics.py")
    const_source = _source("custom_components/jackery_solarvault/const.py")

    assert "DIAGNOSTICS_SCHEMA_VERSION" in const_source
    assert '"schema_version": DIAGNOSTICS_SCHEMA_VERSION' in source
    assert '"rejection_metrics":' in source
    assert '"http_auth_rejections": 0' in source
    assert '"mqtt_broker_rejections": 0' in source
    assert '"payload_validation_rejections": 0' in source
    assert '"schema_rejections": 0' in source
    assert '"timestamp_skew_rejections": 0' in source
    assert '"auth_token_expiry_rejections": 0' in source
    assert '"last_rejection": None' in source


def test_diagnostics_schema_version_constant_is_final_int() -> None:
    """DIAGNOSTICS_SCHEMA_VERSION must be a Final[int] for type-strict consumers."""
    source = _source("custom_components/jackery_solarvault/const.py")
    match = re.search(
        r"^DIAGNOSTICS_SCHEMA_VERSION:\s*Final\s*=\s*(\d+)\s*$",
        source,
        re.MULTILINE,
    )
    assert match is not None, "DIAGNOSTICS_SCHEMA_VERSION must be Final[int]"


def test_api_last_login_response_is_assigned_after_success_validation() -> None:
    """Login diagnostics must only store successful login responses."""
    tree = ast.parse(_source("custom_components/jackery_solarvault/client/api.py"))
    login = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_login"
    )

    line_extract_code = next(
        node.lineno
        for node in ast.walk(login)
        if isinstance(node, ast.Attribute) and node.attr == "_extract_code"
    )
    line_assignment = next(
        node.lineno
        for node in ast.walk(login)
        if isinstance(node, ast.Attribute) and node.attr == "last_login_response"
    )

    assert line_assignment > line_extract_code


def test_get_json_rejects_invalid_json_instead_of_returning_raw_text_success() -> None:
    """Unparseable 200 bodies must raise, not become successful raw-text payloads."""
    tree = ast.parse(_source("custom_components/jackery_solarvault/client/api.py"))
    get_json = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_get_json"
    )

    assert any(
        isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and "returned invalid JSON" in node.value
        for node in ast.walk(get_json)
    )
    assert not any(
        isinstance(node, ast.Name) and node.id == "FIELD_RAW_TEXT"
        for node in ast.walk(get_json)
    )


def test_background_task_loggers_preserve_tracebacks() -> None:
    """MQTT background task callbacks must use exception logging."""
    local_mqtt = _source("custom_components/jackery_solarvault/client/local_mqtt.py")
    mqtt_push = _source("custom_components/jackery_solarvault/client/mqtt_push.py")

    assert (
        '_LOGGER.exception("Jackery local MQTT %s handler failed", label)' in local_mqtt
    )
    assert '_LOGGER.exception("Jackery MQTT %s handler failed", label)' in mqtt_push


def test_button_handlers_guard_unavailable_entities_before_writes() -> None:
    """Write buttons must check availability before sending commands."""
    source = _source("custom_components/jackery_solarvault/button.py")

    for marker in (
        "async_query_weather_plan",
        "async_read_device_schedule",
        "async_delete_storm_alert",
    ):
        index = source.index(marker)
        prefix = source[max(0, index - 220) : index]
        assert "if not self.available:" in prefix


def test_schedule_schema_uses_central_action_id_set() -> None:
    """Schedule service validation must share the MQTT schedule action constants."""
    services = _source("custom_components/jackery_solarvault/services.py")
    const = _source("custom_components/jackery_solarvault/const.py")

    assert "vol.In(MQTT_ACTION_IDS_SCHEDULE)" in services
    assert "ACTION_ID_TIMER_TASK_ADD" in const
    assert "MQTT_ACTION_IDS_SCHEDULE: Final = frozenset({" in const
    assert "MQTT_ACTION_IDS_SCHEDULE: Final = frozenset({3015" not in const


def test_mqtt_push_failure_and_backpressure_paths_are_guarded() -> None:
    """MQTT push must mark all publish failures dead and bound message fan-out."""
    source = _source("custom_components/jackery_solarvault/client/mqtt_push.py")

    assert "_MAX_PENDING_MESSAGE_TASKS = 32" in source
    assert "self._stopping = False" in source
    assert "except Exception as err:" in source
    assert 'self._last_error = f"publish failed: {err}"' in source
    assert "and not self._stopping" in source
    assert "len(self._message_tasks) >= _MAX_PENDING_MESSAGE_TASKS" in source
    assert 'self._last_message_error = "message callback backlog full"' in source


def test_select_unknown_price_mode_and_match_narrowing_are_explicit() -> None:
    """Select handlers must not silently no-op when option maps grow."""
    source = _source("custom_components/jackery_solarvault/select.py")

    assert "assert match is not None" in source
    assert "elif mode == 2:" in source
    assert "else:\n        _raise_select_action_error" in source


def test_redact_keys_use_constants_for_shared_secret_fields() -> None:
    """Redaction keys must use shared constants and avoid duplicate literals."""
    source = _source("custom_components/jackery_solarvault/const.py")
    redact_keys = source[source.index("REDACT_KEYS: Final") : source.index("# MQTT")]

    assert 'FIELD_BLUETOOTH_KEY: Final = "bluetoothKey"' in source
    assert "FIELD_BLUETOOTH_KEY," in source
    assert '"bluetoothKey",' not in redact_keys
    assert "CONF_PASSWORD," in source
    assert "CONF_USERNAME," in source


def test_shelly_control_rejects_missing_action_or_function() -> None:
    """Shelly control must not stringify None into backend form values."""
    source = _source("custom_components/jackery_solarvault/client/api.py")

    assert "action: str | int | None" in source
    assert "function: str | int | None" in source
    assert "if action is None or function is None:" in source
    assert "str(action)" in source
    assert "str(function)" in source


def test_ble_frame_header_check_survives_optimized_python() -> None:
    """BLE frame construction must not rely on assert for runtime validation."""
    source = _source("custom_components/jackery_solarvault/client/ble.py")

    assert "assert len(header)" not in source
    assert "if len(header) != _BINARY_FRAME_HEADER_LEN:" in source
    assert "raise ValueError(" in source


def test_device_available_falls_back_for_unknown_online_state() -> None:
    """Unknown cloud online-state values must not hide devices present in data."""
    source = _source("custom_components/jackery_solarvault/entity.py")

    assert "unrecognized online state" in source
    assert "falling back to data membership" in source
    assert "return self._device_id in (self.coordinator.data or {})" in source


def test_coordinator_exception_logs_preserve_tracebacks() -> None:
    """Coordinator background exception paths should preserve tracebacks."""
    source = _source("custom_components/jackery_solarvault/coordinator.py")

    assert (
        '_LOGGER.debug("Jackery recorder-statistics import failed: %s", err)'
        not in source
    )
    assert '_LOGGER.exception("Jackery recorder-statistics import failed")' in source
    assert '_LOGGER.exception("%s failed", cache_key)' in source
