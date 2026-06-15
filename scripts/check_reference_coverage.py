"""Validate Jackery reference coverage against implementation files."""

from __future__ import annotations

import ast
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REFERENCE_PATH = ROOT / "docs" / "jackery_complete_reference.json"
ENDPOINTS_DIR = (
    ROOT / "custom_components" / "jackery_solarvault" / "client" / "_endpoints"
)
CONST_PATH = ROOT / "custom_components" / "jackery_solarvault" / "const.py"
SERVICES_PATH = ROOT / "custom_components" / "jackery_solarvault" / "services.yaml"


def _literal_assignments() -> dict[str, object]:
    """Return literal module-level assignments from const.py."""
    tree = ast.parse(CONST_PATH.read_text(encoding="utf-8"))
    values: dict[str, object] = {}
    for node in tree.body:
        target: ast.expr | None = None
        value_node: ast.expr | None = None
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            target = node.target
            value_node = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value_node = node.value
        if not isinstance(target, ast.Name) or value_node is None:
            continue
        try:
            values[target.id] = ast.literal_eval(value_node)
        except ValueError:
            continue
        except TypeError:
            continue
    return values


def _endpoint_module_text() -> str:
    """Return concatenated endpoint module source."""
    return "\n".join(
        path.read_text(encoding="utf-8") for path in sorted(ENDPOINTS_DIR.glob("*.py"))
    )


def const_http_endpoints() -> dict[str, str]:
    """Return /v1 endpoint constants from const.py without the /v1 prefix."""
    return {
        name: value.removeprefix("/v1/")
        for name, value in _literal_assignments().items()
        if name.endswith("_PATH")
        and isinstance(value, str)
        and value.startswith("/v1/")
    }


def implemented_http_endpoints() -> set[str]:
    """Return endpoints whose constants are used by endpoint mixins."""
    module_text = _endpoint_module_text()
    return {
        endpoint
        for name, endpoint in const_http_endpoints().items()
        if name in module_text
    }


def action_ids() -> tuple[dict[str, int], dict[str, int]]:
    """Return home and portable action constants keyed by name."""
    actions = {
        name: value
        for name, value in _literal_assignments().items()
        if name.startswith("ACTION_ID") and isinstance(value, int)
    }
    return {name: value for name, value in actions.items() if "PORTABLE" not in name}, {
        name: value for name, value in actions.items() if "PORTABLE" in name
    }


def service_names() -> set[str]:
    """Return top-level Home Assistant service names from services.yaml."""
    return {
        line[:-1]
        for line in SERVICES_PATH.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith(" ") and line.endswith(":")
    }


def validate_reference_coverage() -> list[str]:
    """Return drift messages between the reference matrix and implementation."""
    reference = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    errors: list[str] = []

    reference_http = reference["http_endpoints"]
    expected_implemented = set(reference_http["implemented"])
    expected_skipped = set(reference_http["intentionally_skipped"])
    actual_implemented = implemented_http_endpoints()
    const_endpoints = set(const_http_endpoints().values())

    if actual_implemented != expected_implemented:
        errors.append(
            "HTTP implemented drift: "
            f"missing={sorted(expected_implemented - actual_implemented)} "
            f"unexpected={sorted(actual_implemented - expected_implemented)}"
        )
    if expected_skipped & const_endpoints:
        errors.append(
            "Skipped endpoints are implemented in const.py: "
            f"{sorted(expected_skipped & const_endpoints)}"
        )

    home_actions, portable_actions = action_ids()
    expected_home = {
        item["name"]: item["action_id"] for item in reference["commands"]["home"]
    }
    expected_portable = {
        item["name"]: item["action_id"] for item in reference["commands"]["portable"]
    }
    if home_actions != expected_home:
        errors.append(
            "Home command drift: "
            f"missing={sorted(expected_home.keys() - home_actions.keys())} "
            f"unexpected={sorted(home_actions.keys() - expected_home.keys())} "
            f"changed={sorted(name for name in expected_home.keys() & home_actions.keys() if expected_home[name] != home_actions[name])}"
        )
    if portable_actions != expected_portable:
        errors.append(
            "Portable command drift: "
            f"missing={sorted(expected_portable.keys() - portable_actions.keys())} "
            f"unexpected={sorted(portable_actions.keys() - expected_portable.keys())} "
            f"changed={sorted(name for name in expected_portable.keys() & portable_actions.keys() if expected_portable[name] != portable_actions[name])}"
        )

    expected_services = set(reference["home_assistant_services"])
    actual_services = service_names()
    if actual_services != expected_services:
        errors.append(
            "Service drift: "
            f"missing={sorted(expected_services - actual_services)} "
            f"unexpected={sorted(actual_services - expected_services)}"
        )

    return errors


def main() -> int:
    """Run the reference coverage gate."""
    errors = validate_reference_coverage()
    if errors:
        for error in errors:
            print(error)
        return 1
    reference = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    implemented = len(reference["http_endpoints"]["implemented"])
    skipped = len(reference["http_endpoints"]["intentionally_skipped"])
    print(
        "Reference coverage OK: "
        f"HTTP {implemented}/{implemented + skipped} implemented, "
        f"{skipped} intentionally skipped; "
        f"commands {len(reference['commands']['home'])} home + "
        f"{len(reference['commands']['portable'])} portable; "
        f"services {len(reference['home_assistant_services'])}."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
