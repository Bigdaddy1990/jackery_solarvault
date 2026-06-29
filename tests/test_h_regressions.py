"""Regression tests for high-impact Jackery runtime edge cases."""

import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text()


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
