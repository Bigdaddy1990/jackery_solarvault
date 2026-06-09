"""Ensure test imports declare the third-party packages they rely on."""

import argparse
import ast
from collections.abc import Iterable
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
TESTS_ROOT = REPO_ROOT / "tests"
REQUIREMENT_FILES = [
    REPO_ROOT / "requirements-test.txt",
    REPO_ROOT / "requirements.txt",
]
STANDARD_LIB = {name.replace(".", "_") for name in sys.stdlib_module_names}
INTERNAL_PREFIXES = ("tests", "custom_components", "scripts")
INTERNAL_MODULES: set[str] = set()
REQUIREMENT_EQUIVALENTS: dict[str, set[str]] = {
    # The pytest plugin installs Home Assistant as a transitive dependency, so
    # tests importing `homeassistant` are satisfied when only the plugin is
    # listed in requirements_test.txt.
    "homeassistant": {"pytest_homeassistant_custom_component"},
    "yaml": {"pyyaml"},
}


def _extract_requirement_name(line: str) -> str | None:
    """Return the normalized package name from a requirement line."""
    stripped = line.split("#", 1)[0].strip()
    if not stripped or stripped.startswith("-"):
        return None

    delimiters = frozenset("[] <>!=~();")
    for i, char in enumerate(stripped):
        if char in delimiters:
            name = stripped[:i].strip()
            return name or None

    return stripped or None


def _parse_requirements() -> set[str]:
    modules: set[str] = set()

    for requirement_file in REQUIREMENT_FILES:
        content = requirement_file.read_text(encoding="utf-8")
        for raw_line in content.splitlines():
            name = _extract_requirement_name(raw_line)
            if not name:
                continue
            normalized = name.lower()
            modules.add(normalized)
            modules.add(normalized.replace("-", "_"))

    return modules


def _iter_test_files() -> Iterable[Path]:
    return TESTS_ROOT.rglob("*.py")


def _collect_imports(path: Path) -> set[str]:
    imports: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module is None or node.level:
                continue
            imports.add(node.module.split(".")[0])

    return imports


def _is_third_party(module: str) -> bool:
    module_lower = module.lower()
    if module_lower in STANDARD_LIB:
        return False
    if module_lower in INTERNAL_MODULES:
        return False
    return not any(module_lower.startswith(prefix) for prefix in INTERNAL_PREFIXES)


def main() -> int:  # noqa: D103
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="print all discovered imports for debugging",
    )
    args = parser.parse_args()

    requirements = _parse_requirements()
    missing: dict[str, set[str]] = {}

    for file in _iter_test_files():
        imports = _collect_imports(file)
        if args.verbose:
            print(f"{file}: {sorted(imports)}")
        for module in imports:
            if not _is_third_party(module):
                continue
            normalized = module.lower().replace("-", "_")
            if normalized in requirements:
                continue
            equivalents = REQUIREMENT_EQUIVALENTS.get(normalized, set())
            if equivalents.intersection(requirements):
                continue
            missing.setdefault(file.as_posix(), set()).add(module)

    if missing:
        print("Missing test requirement declarations detected:")
        for file, modules in sorted(missing.items()):
            formatted = ", ".join(sorted(modules))
            print(f"  - {file}: {formatted}")
        print("Add the packages to requirements_test.txt to fix the violation.")
        return 1

    print("All third-party test imports map to requirements_test.txt entries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
