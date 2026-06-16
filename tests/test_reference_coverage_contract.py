"""Static contract tests for Jackery reference coverage documentation."""

from __future__ import annotations  # noqa: TID251

import ast
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_PATH = ROOT / "docs" / "jackery_complete_reference.json"
COVERAGE_PATH = ROOT / "docs" / "REFERENCE_COVERAGE.md"
ENDPOINT_REGISTRY_PATH = (
    ROOT
    / "custom_components"
    / "jackery_solarvault"
    / "client"
    / "endpoint_registry.py"
)
CONST_PATH = ROOT / "custom_components" / "jackery_solarvault" / "const.py"


def _literal_assignment(tree: ast.Module, name: str) -> object:
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):  # noqa: SIM102
            if node.target.id == name and node.value is not None:
                return ast.literal_eval(node.value)
    msg = f"{name} assignment not found"
    raise AssertionError(msg)


def _frozenset_name_count(tree: ast.Module, name: str) -> int:
    for node in tree.body:
        if not (isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)):
            continue
        if node.target.id != name or not isinstance(node.value, ast.Call):
            continue
        [arg] = node.value.args
        assert isinstance(arg, ast.Set)  # noqa: S101
        return sum(isinstance(item, ast.Name) for item in arg.elts)
    msg = f"{name} frozenset assignment not found"
    raise AssertionError(msg)


def _summary_row(markdown: str, label: str) -> tuple[str, ...]:
    pattern = re.compile(rf"^\| {re.escape(label)}\s*\|(.+)\|$", re.MULTILINE)
    match = pattern.search(markdown)
    assert match is not None, f"{label} coverage row missing"  # noqa: S101
    return tuple(cell.strip() for cell in match.group(1).split("|"))


def test_reference_coverage_summary_matches_authoritative_json() -> None:
    """Docs must not drift from the authoritative reference counters."""
    reference = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    coverage = COVERAGE_PATH.read_text(encoding="utf-8")

    assert (  # noqa: S101
        "Single technical source of truth:"
        " `docs/jackery_complete_reference.json`" in coverage
    )
    assert _summary_row(coverage, "HTTP-Endpoints")[0] == str(  # noqa: S101
        reference["counts"]["http_endpoints"],
    )
    assert _summary_row(coverage, "MQTT-Msg-Types (home)")[0] == str(  # noqa: S101
        len(reference["mqtt"]["message_types"]),
    )
    assert _summary_row(coverage, "Commands (home)")[0] == str(  # noqa: S101
        reference["counts"]["commands_home"],
    )
    assert _summary_row(coverage, "Commands (portable)")[0] == str(  # noqa: S101
        reference["counts"]["commands_portable"],
    )
    assert _summary_row(coverage, "Accessories")[0] == str(  # noqa: S101
        reference["counts"]["accessory_types"],
    )


def test_reference_coverage_summary_matches_implementation_lists() -> None:
    """Central implementation counters must match documented coverage."""
    coverage = COVERAGE_PATH.read_text(encoding="utf-8")
    endpoint_tree = ast.parse(ENDPOINT_REGISTRY_PATH.read_text(encoding="utf-8"))
    const_tree = ast.parse(CONST_PATH.read_text(encoding="utf-8"))

    client_endpoints = _literal_assignment(endpoint_tree, "CLIENT_ENDPOINTS")
    exempt_endpoints = _literal_assignment(endpoint_tree, "EXEMPT_ENDPOINTS")
    portable_action_ids_count = _frozenset_name_count(const_tree, "PORTABLE_ACTION_IDS")

    mqtt_message_constants = {
        node.target.id
        for node in const_tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id.startswith("MQTT_MESSAGE_")
    }
    home_action_id_constants = {
        node.target.id
        for node in const_tree.body
        if isinstance(node, ast.AnnAssign)
        and isinstance(node.target, ast.Name)
        and node.target.id.startswith("ACTION_ID_")
        and not node.target.id.startswith("ACTION_ID_PORTABLE_")
    }

    reference = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    reference_endpoint_paths = {
        endpoint["path"]
        for endpoints in reference["http_api"].values()
        for endpoint in endpoints
    }
    implemented_reference_endpoints = set(client_endpoints) & reference_endpoint_paths
    exempt_reference_endpoints = set(exempt_endpoints) & reference_endpoint_paths

    assert _summary_row(coverage, "HTTP-Endpoints")[1] == str(  # noqa: S101
        len(implemented_reference_endpoints),
    )
    assert (  # noqa: S101
        f"{len(implemented_reference_endpoints) + len(exempt_reference_endpoints)}"
        "/112 covered" in coverage
    )
    assert _summary_row(coverage, "MQTT-Msg-Types (home)")[1] == str(  # noqa: S101
        len(mqtt_message_constants),
    )
    assert _summary_row(coverage, "Commands (home)")[1] == str(  # noqa: S101
        len(home_action_id_constants),
    )
    assert _summary_row(coverage, "Commands (portable)")[1] == str(  # noqa: S101
        portable_action_ids_count,
    )
