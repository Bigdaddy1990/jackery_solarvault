"""Validate the Jackery protocol reference is covered by protocol_map."""

from __future__ import annotations

import ast
import importlib.util
import json
from pathlib import Path

spec = importlib.util.spec_from_file_location(
    "protocol_map",
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "jackery_solarvault"
    / "protocol_map.py",
)
assert spec is not None
protocol_map = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(protocol_map)

ROOT = Path(__file__).resolve().parents[1]
REFERENCE = ROOT / "docs" / "jackery_complete_reference.json"
IMPLEMENTATION_ROOT = ROOT / "custom_components" / "jackery_solarvault"


def _reference() -> dict:
    return json.loads(REFERENCE.read_text(encoding="utf-8"))


def _assert_target_exists(target: str) -> None:
    file_name, _, symbol = target.partition(":")
    path = IMPLEMENTATION_ROOT / file_name
    assert path.exists(), target
    assert symbol, target
    tree = ast.parse(path.read_text(encoding="utf-8"))
    symbols = {
        node.name
        for node in tree.body
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef)
    }
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            symbols.update(
                child.name
                for child in node.body
                if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef)
            )
    assert symbol in symbols, target


def test_http_reference_endpoints_are_mapped_or_exempted() -> None:
    missing: list[str] = []
    for entries in _reference()["http_api"].values():
        for entry in entries:
            path = entry["path"]
            if path in protocol_map.HTTP_ENDPOINTS:
                _assert_target_exists(protocol_map.HTTP_ENDPOINTS[path])
            elif path not in protocol_map.HTTP_ENDPOINT_EXEMPTIONS:
                missing.append(path)
    assert not missing


def test_mqtt_message_types_are_routed() -> None:
    missing = [
        message_type
        for message_type in _reference()["mqtt"]["message_types"]
        if message_type not in protocol_map.MQTT_MESSAGE_HANDLERS
    ]
    assert not missing
    for target in protocol_map.MQTT_MESSAGE_HANDLERS.values():
        _assert_target_exists(target)


def test_command_ids_are_built_or_exempted() -> None:
    reference = _reference()["commands"]
    missing: list[str] = []
    for entry in reference["home"] + reference["portable"]:
        msg_id = str(entry["msg_id"])
        if msg_id in protocol_map.COMMAND_BUILDERS:
            _assert_target_exists(protocol_map.COMMAND_BUILDERS[msg_id])
        elif msg_id not in protocol_map.COMMAND_EXEMPTIONS:
            missing.append(msg_id)
    assert not missing


def test_command_names_are_built_or_exempted() -> None:
    reference = _reference()["commands"]
    exempt_names = {entry["command"] for entry in reference["portable"]}
    missing = [
        entry["command"]
        for entry in reference["home"] + reference["portable"]
        if entry["command"] not in protocol_map.COMMAND_NAME_BUILDERS
        and entry["command"] not in exempt_names
    ]
    assert not missing


def test_reference_dto_fields_are_consumed_or_exempted() -> None:
    reference = _reference()
    missing: list[str] = []
    for class_name, meta in reference["models"]["smali"].items():
        fields = meta.get("fields")
        if not isinstance(fields, list):
            continue
        for field in fields:
            field_name = field["name"] if isinstance(field, dict) else field
            key = f"{class_name}.{field_name}"
            wildcard = f"{class_name}.*"
            if key in protocol_map.DTO_FIELD_CONSUMERS:
                continue
            if wildcard in protocol_map.DTO_FIELD_EXEMPTIONS:
                continue
            missing.append(key)
    assert not missing
