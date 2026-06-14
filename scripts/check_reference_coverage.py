"""Validate Jackery protocol/reference coverage against integration code."""


import argparse
import ast
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

DOMAIN_DIR = Path("custom_components/jackery_solarvault")
ENDPOINT_DIR = DOMAIN_DIR / "client" / "_endpoints"
REFERENCE_JSON = Path("docs/jackery_complete_reference.json")
MQTT_DOC = Path("docs/MQTT_PROTOCOL.md")
SERVICES_YAML = DOMAIN_DIR / "services.yaml"
STRINGS_JSON = DOMAIN_DIR / "strings.json"
SERVICES_PY = DOMAIN_DIR / "services.py"
QUALITY_SCALE = DOMAIN_DIR / "quality_scale.yaml"
MANIFEST = DOMAIN_DIR / "manifest.json"

_HTTP_RE = re.compile(r"/v1/[A-Za-z0-9_./{}:-]+")
_MQTT_CONSTANT_RE = re.compile(r'^MQTT_MESSAGE_[A-Z0-9_]+:\s*Final\s*=\s*"([^"]+)"', re.M)
_BACKTICK_RE = re.compile(r"`([A-Z][A-Za-z0-9]+(?:[A-Z][A-Za-z0-9]+)+)`")
_SERVICE_CONST_RE = re.compile(r'^SERVICE_[A-Z0-9_]+:\s*Final\s*=\s*"([a-z0-9_]+)"', re.M)
_REGISTER_RE = re.compile(r"async_register\(\s*\n\s*DOMAIN,\s*\n\s*([A-Z0-9_]+|['\"]([^'\"]+)['\"])", re.M)


@dataclass(frozen=True)
class CoverageReport:
    """Reference coverage check result."""

    errors: tuple[str, ...]
    warnings: tuple[str, ...]

    @property
    def ok(self) -> bool:
        """Return true when no blocking coverage errors were found."""
        return not self.errors


def _top_level_yaml_keys(path: Path) -> set[str]:
    """Return first-level mapping keys from the repository's simple YAML files."""
    keys: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line or line.startswith((" ", "#", "-")) or ":" not in line:
            continue
        key = line.split(":", 1)[0].strip()
        if key:
            keys.add(key)
    return keys


def _quality_rule_statuses(path: Path) -> set[str]:
    """Return status values from quality_scale.yaml without external YAML deps."""
    statuses: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("status:"):
            statuses.add(line.split(":", 1)[1].strip())
            continue
        if raw_line.startswith("  ") and not raw_line.startswith("    ") and ":" in line:
            value = line.split(":", 1)[1].strip()
            if value in {"done", "todo", "exempt"}:
                statuses.add(value)
    return statuses


def _literal_string_assignments(path: Path) -> dict[str, str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    values: dict[str, str] = {}
    for node in tree.body:
        target: ast.expr | None = None
        value: ast.expr | None = None
        if isinstance(node, ast.AnnAssign):
            target = node.target
            value = node.value
        elif isinstance(node, ast.Assign) and len(node.targets) == 1:
            target = node.targets[0]
            value = node.value
        if isinstance(target, ast.Name) and isinstance(value, ast.Constant):
            if isinstance(value.value, str):
                values[target.id] = value.value
    return values


def _iter_strings(value: Any) -> set[str]:
    strings: set[str] = set()
    if isinstance(value, str):
        strings.add(value)
    elif isinstance(value, dict):
        for key, item in value.items():
            strings.update(_iter_strings(key))
            strings.update(_iter_strings(item))
    elif isinstance(value, list | tuple | set):
        for item in value:
            strings.update(_iter_strings(item))
    return strings


def _normalize_paths(values: set[str]) -> set[str]:
    return {match.group(0).rstrip(".,`)]") for value in values for match in _HTTP_RE.finditer(value)}


def reference_http_endpoints(root: Path = Path.cwd()) -> set[str]:
    """Return documented /v1 endpoints from the structured reference JSON."""
    reference_path = root / REFERENCE_JSON
    if not reference_path.exists():
        return set()
    return _normalize_paths(_iter_strings(json.loads(reference_path.read_text(encoding="utf-8"))))


def implemented_http_endpoints(root: Path = Path.cwd()) -> set[str]:
    """Return /v1 endpoints used by domain endpoint mixins."""
    constants = _literal_string_assignments(root / DOMAIN_DIR / "const.py")
    values: set[str] = set()
    for path in (root / ENDPOINT_DIR).glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                values.add(node.value)
            elif isinstance(node, ast.Name) and node.id in constants:
                values.add(constants[node.id])
    return _normalize_paths(values)


def reference_mqtt_message_types(root: Path = Path.cwd()) -> set[str]:
    """Return documented MQTT messageType values."""
    values: set[str] = set()
    reference_path = root / REFERENCE_JSON
    if reference_path.exists():
        for item in _iter_strings(json.loads(reference_path.read_text(encoding="utf-8"))):
            if item and item[:1].isupper() and " " not in item:
                values.add(item)
    doc_path = root / MQTT_DOC
    if doc_path.exists():
        values.update(_BACKTICK_RE.findall(doc_path.read_text(encoding="utf-8")))
    return {value for value in values if not value.startswith("Jackery")}


def implemented_mqtt_message_types(root: Path = Path.cwd()) -> set[str]:
    """Return MQTT messageType values declared for runtime routing."""
    const_text = (root / DOMAIN_DIR / "const.py").read_text(encoding="utf-8")
    return set(_MQTT_CONSTANT_RE.findall(const_text))


def _service_constants(root: Path) -> dict[str, str]:
    return {f"SERVICE_{name.upper()}": name for name in _SERVICE_CONST_RE.findall((root / DOMAIN_DIR / "const.py").read_text(encoding="utf-8"))}


def registered_services(root: Path = Path.cwd()) -> set[str]:
    """Return services registered in services.py."""
    constants = _service_constants(root)
    found: set[str] = set()
    for const_name, literal in _REGISTER_RE.findall((root / SERVICES_PY).read_text(encoding="utf-8")):
        if literal:
            found.add(literal)
        else:
            found.add(constants.get(const_name, const_name))
    return found


def service_yaml_names(root: Path = Path.cwd()) -> set[str]:
    """Return service names from services.yaml."""
    return _top_level_yaml_keys(root / SERVICES_YAML)


def strings_service_names(root: Path = Path.cwd()) -> set[str]:
    """Return service names from strings.json."""
    data = json.loads((root / STRINGS_JSON).read_text(encoding="utf-8"))
    services = data.get("services", {})
    return set(services) if isinstance(services, dict) else set()


def quality_scale_statuses(root: Path = Path.cwd()) -> tuple[str, set[str]]:
    """Return manifest quality_scale plus internal quality rule statuses."""
    manifest = json.loads((root / MANIFEST).read_text(encoding="utf-8"))
    return str(manifest.get("quality_scale", "")), _quality_rule_statuses(root / QUALITY_SCALE)


def check_reference_coverage(root: Path = Path.cwd()) -> CoverageReport:
    """Run all reference-coverage checks."""
    errors: list[str] = []
    warnings: list[str] = []

    reference_endpoints = reference_http_endpoints(root)
    implemented_endpoints = implemented_http_endpoints(root)
    missing_endpoints = sorted(reference_endpoints - implemented_endpoints)
    if missing_endpoints:
        errors.append("HTTP endpoints missing from client/_endpoints: " + ", ".join(missing_endpoints))
    if not (root / REFERENCE_JSON).exists():
        warnings.append(f"{REFERENCE_JSON} not found; structured HTTP endpoint check skipped")

    reference_mqtt = reference_mqtt_message_types(root)
    implemented_mqtt = implemented_mqtt_message_types(root)
    missing_mqtt = sorted(reference_mqtt - implemented_mqtt)
    if missing_mqtt:
        errors.append("MQTT message types missing from runtime router/constants: " + ", ".join(missing_mqtt))

    services_yaml = service_yaml_names(root)
    services_strings = strings_service_names(root)
    services_registered = registered_services(root)
    if services_yaml != services_strings:
        errors.append(
            "services.yaml and strings.json services differ: "
            f"missing in strings={sorted(services_yaml - services_strings)}, "
            f"extra in strings={sorted(services_strings - services_yaml)}"
        )
    if services_yaml != services_registered:
        errors.append(
            "services.yaml and services.py registrations differ: "
            f"missing registrations={sorted(services_yaml - services_registered)}, "
            f"extra registrations={sorted(services_registered - services_yaml)}"
        )

    manifest_quality, internal_statuses = quality_scale_statuses(root)
    if manifest_quality != "custom":
        errors.append(f"manifest quality_scale must remain custom, got {manifest_quality!r}")
    if "done" not in internal_statuses:
        errors.append("quality_scale.yaml must keep internal completed rule statuses")

    return CoverageReport(tuple(errors), tuple(warnings))


def main() -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    args = parser.parse_args()
    report = check_reference_coverage(args.root)
    for warning in report.warnings:
        print(f"WARNING: {warning}", file=sys.stderr)
    if report.ok:
        print("Reference coverage checks passed.")
        return 0
    for error in report.errors:
        print(f"ERROR: {error}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
