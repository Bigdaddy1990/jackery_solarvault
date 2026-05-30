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
    """Detects whether an AST expression represents a TypedDict base class.

    Recognizes unqualified `TypedDict`, attribute-qualified forms (e.g., `typing.TypedDict`),
    and subscripted forms (e.g., `TypedDict[...]`).

    Parameters:
        base (ast.expr): AST node representing a base class expression.

    Returns:
        True if the expression represents a `TypedDict` base, False otherwise.
    """
    if isinstance(base, ast.Name):
        return base.id == "TypedDict"
    if isinstance(base, ast.Attribute):
        return base.attr == "TypedDict"
    if isinstance(base, ast.Subscript):
        return _is_typeddict_base(base.value)
    return False


def _iter_py_files(path: Path) -> list[Path]:
    """Collect Python files under the given directory, excluding any located in `__pycache__` directories.

    Parameters:
        path (Path): Root directory to search.

    Returns:
        list[Path]: List of `.py` file paths found under `path`, excluding files in `__pycache__` path components.
    """
    return [
        p for p in path.rglob("*.py") if p.is_file() and "__pycache__" not in p.parts
    ]


def _audit_file(path: Path) -> list[str]:
    """Audit a Python file for TypedDict structure issues.

    Parse the file at `path` and report TypedDict-related problems found in its AST:
    - classes that inherit from `TypedDict` but contain no annotated fields,
    - annotated assignments within those classes that lack an annotation.

    Parameters:
        path (Path): Path to the Python source file to audit.

    Returns:
        list[str]: A list of error messages found in the file (empty if none).
            - On a syntax error while parsing, returns a single string formatted as
              "{path}: SyntaxError: {err}".
            - For TypedDict issues, messages are formatted like
              "{path}:{lineno} TypedDict '<Name>' has no annotated fields" or
              "{path}:{lineno} TypedDict '<Name>' field missing annotation".
    """
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


def main() -> int:
    """Run the TypedDict audit for the configured target directory and return a process exit code.

    Prints detailed error lines and a failure summary when issues are found, or a success message when none are found. If the target path does not exist, prints a message and treats that as a failure.

    Returns:
        int: Exit code — `0` when the audit passes, `1` when the target is missing or any errors are detected.
    """
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
