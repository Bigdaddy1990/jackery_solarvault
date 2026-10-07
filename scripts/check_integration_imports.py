"""Compile and import every shipped integration module before publishing.

Run with the supported Python runtime and the real HA/test dependencies:
    python -m scripts.check_integration_imports

The Python 3.13 grammar check preserves portable exception syntax; it does not
claim that the current Home Assistant dependency stack supports Python 3.13.
Exception headers use ``# fmt: skip`` so Ruff's Python 3.14 formatter retains
their grouping parentheses without changing the project's runtime target.
Imports run outside pytest so test stubs cannot conceal import failures.
"""

import ast
import importlib
from pathlib import Path
from typing import get_type_hints

ROOT = Path(__file__).resolve().parents[1]
INTEGRATION = ROOT / "custom_components" / "jackery_solarvault"


def compile_sources(directory: Path) -> list[Path]:
    """Compile all sources and reject syntax unavailable to the portable parser."""
    sources = sorted(directory.rglob("*.py"))
    if not sources:
        raise ValueError(f"No integration Python sources found in {directory}")
    for path in sources:
        source = path.read_bytes()
        compile(source, str(path), "exec", dont_inherit=True)
        ast.parse(source, filename=str(path), feature_version=(3, 13))
    return sources


def module_name(path: Path) -> str:
    """Resolve a shipped Python file to its importable module or package name."""
    parts = list(path.relative_to(ROOT).with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def main() -> None:
    """Fail immediately on any compilation or real dependency import error."""
    sources = compile_sources(INTEGRATION)
    for path in sources:
        importlib.import_module(module_name(path))
    const = importlib.import_module("custom_components.jackery_solarvault.const")
    for platform in const.PLATFORMS:
        importlib.import_module(f"custom_components.jackery_solarvault.{platform}")
    select = importlib.import_module("custom_components.jackery_solarvault.select")
    # Native 3.14 import succeeds even after removing the future import. Keep
    # the deliberate eager-runtime safeguard observable in the release gate.
    if select._raise_select_action_error.__annotations__["entity"] != "JackerySelect":
        raise TypeError("Select helper must preserve its deferred forward reference")
    if (
        get_type_hints(select._raise_select_action_error)["entity"]
        is not select.JackerySelect
    ):
        raise TypeError("Select entity forward reference did not resolve")
    print(f"Compiled and imported all {len(sources)} integration modules")


if __name__ == "__main__":
    main()
