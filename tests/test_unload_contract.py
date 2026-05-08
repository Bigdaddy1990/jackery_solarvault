"""Static contract checks for Home Assistant config-entry unload behavior."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "custom_components" / "jackery_solarvault" / "__init__.py"


def _async_unload_entry() -> ast.AsyncFunctionDef:
    tree = ast.parse(INIT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_unload_entry":
            return node
    raise AssertionError("async_unload_entry not found")


def _call_line(function: ast.AsyncFunctionDef, attr: str) -> int:
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == attr
        ):
            return node.lineno
    raise AssertionError(f"{attr} call not found")


def test_unload_platforms_before_coordinator_shutdown() -> None:
    """Do not stop the coordinator while HA may keep the entry loaded."""
    function = _async_unload_entry()

    assert _call_line(function, "async_unload_platforms") < _call_line(
        function, "async_shutdown"
    )


def test_coordinator_shutdown_is_success_gated() -> None:
    """Coordinator shutdown must run only after async_unload_platforms succeeds."""
    function = _async_unload_entry()
    shutdown_line = _call_line(function, "async_shutdown")

    success_blocks = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.Name)
        and node.test.id == "unload_ok"
        and node.lineno < shutdown_line <= getattr(node, "end_lineno", node.lineno)
    ]
    assert success_blocks, "async_shutdown must be inside if unload_ok"
