"""Static integration contract checks."""

from __future__ import annotations

import ast
from pathlib import Path


def diagnostics_null_export_violations(*, root: Path) -> tuple[str, ...]:
    """Require diagnostics exports to pass through the null normalizer."""
    path = root / "custom_components/jackery_solarvault/diagnostics.py"
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.AsyncFunctionDef)
            and node.name == "async_get_config_entry_diagnostics"
        ):
            if any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Name)
                and child.func.id == "_diagnostic_json_null_free"
                for child in ast.walk(node)
            ):
                return ()
            return (f"{path}: diagnostics export bypasses _diagnostic_json_null_free",)
    return (f"{path}: async_get_config_entry_diagnostics is missing",)
