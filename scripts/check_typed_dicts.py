"""TypedDict guard for jackery_solarvault.

The Home Assistant core project has a variety of checks around TypedDicts.
This repo uses a lightweight audit to catch the most common refactor mistakes:

- A class inherits from TypedDict but has no annotations
- A TypedDict field is missing a type annotation

The goal is not to be overly strict, but to fail fast on broken typing.

This script is executed in CI via:

    python -m scripts.check_typed_dicts

Exit codes:
- 0: OK
- 1: Errors found
"""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "custom_components" / "jackery_solarvault"


def _is_typeddict_base(base: ast.expr) -> bool:
    """Return True if the base class expression looks like TypedDict."""
    if isinstance(base, ast.Name):
        return base.id == "TypedDict"
    if isinstance(base, ast.Attribute):
        return base.attr == "TypedDict"
    if isinstance(base, ast.Subscript):
        return _is_typeddict_base(base.value)
    return False


def _iter_py_files(path: Path) -> list[Path]:
    return [
        p for p in path.rglob("*.py") if p.is_file() and "__pycache__" not in p.parts
    ]


def _audit_file(path: Path) -> list[str]:
    errors: list[str] = []

    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as err:
        return [f"{path}: SyntaxError: {err}"]

    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue

        if not any(_is_typeddict_base(b) for b in node.bases):
            continue

        # TypedDict must contain at least one AnnAssign.
        ann_assigns = [n for n in node.body if isinstance(n, ast.AnnAssign)]
        if not ann_assigns:
            errors.append(
                f"{path}:{node.lineno} TypedDict '{node.name}' has no annotated fields"
            )
            continue

        for ann in ann_assigns:
            # AnnAssign without an annotation should not happen, but guard anyway.
            if ann.annotation is None:
                errors.append(
                    f"{path}:{ann.lineno} TypedDict '{node.name}' field missing annotation"
                )

    return errors


def main() -> int:  # noqa: D103
    if not TARGET.exists():
        print(f"TypedDict audit: target path not found: {TARGET}")
        return 1

    all_errors: list[str] = []

    for py_file in _iter_py_files(TARGET):
        # Skip generated/vendor files if ever added.
        if py_file.name.startswith("_") and py_file.name not in {"__init__.py"}:
            continue

        all_errors.extend(_audit_file(py_file))

    if all_errors:
        print("\n".join(all_errors))
        print(f"\nTypedDict audit failed: {len(all_errors)} error(s) found")
        return 1

    print("TypedDict audit passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
