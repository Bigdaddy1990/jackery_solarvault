"""Static contract checks for Home Assistant config-entry unload behavior."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "custom_components" / "jackery_solarvault" / "__init__.py"


def _async_function(name: str) -> ast.AsyncFunctionDef:
    """Locate one integration async-function AST node.

    Parses INIT and returns the first matching async function. Raises an
    assertion if the function is absent.

    Returns:
        ast.AsyncFunctionDef: The AST node representing `async_unload_entry`.
    """
    tree = ast.parse(INIT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == name:
            return node
    raise AssertionError(f"{name} not found")  # ruff: ignore[raise-vanilla-args]


def _call_line(function: ast.AsyncFunctionDef, attr: str) -> int:
    """Find the first line that calls an attribute or name.

    Parameters:
        function: Async function AST node to search.
        attr: Attribute or function name to locate.

    Returns:
        int: The line number where the first matching attribute call occurs.

    Raises:
        AssertionError: If no call to the specified attribute is found.
    """
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == attr:
            return node.lineno
        if isinstance(func, ast.Name) and func.id == attr:
            return node.lineno
    raise AssertionError(f"{attr} call not found")  # ruff: ignore[raise-vanilla-args]


def test_unload_platforms_before_coordinator_shutdown() -> None:
    """Verify platforms are unloaded before the coordinator is shut down.

    The platform unload must appear before bounded coordinator shutdown, so
    the coordinator remains alive while the entry can still be loaded.
    """
    function = _async_function("async_unload_entry")

    assert _call_line(function, "async_unload_platforms") < _call_line(
        function, "_async_teardown_unloaded_entry"
    )
    _call_line(
        _async_function("_async_teardown_unloaded_entry"),
        "_async_shutdown_coordinator_bounded",
    )


def test_coordinator_shutdown_is_success_gated() -> None:
    """Require successful platform unload before coordinator shutdown.

    An `if not unload_ok: ... return` block must precede bounded shutdown.
    """
    function = _async_function("async_unload_entry")
    teardown_line = _call_line(function, "_async_teardown_unloaded_entry")

    failure_blocks = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.UnaryOp)
        and isinstance(node.test.op, ast.Not)
        and isinstance(node.test.operand, ast.Name)
        and node.test.operand.id == "unload_ok"
        and node.lineno < teardown_line
        and any(isinstance(stmt, ast.Return) for stmt in node.body)
    ]
    assert failure_blocks, "runtime teardown must be after if not unload_ok return"
