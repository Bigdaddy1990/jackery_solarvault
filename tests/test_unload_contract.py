"""Static contract checks for Home Assistant config-entry unload behavior."""

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT = ROOT / "custom_components" / "jackery_solarvault" / "__init__.py"


def _async_unload_entry() -> ast.AsyncFunctionDef:
    """
    Locate the AST node for the `async_unload_entry` coroutine defined in the integration's __init__.py.
    
    Parses the file at INIT into an AST and returns the first ast.AsyncFunctionDef node whose name is "async_unload_entry". Raises an AssertionError if no such function is present.
    
    Returns:
        ast.AsyncFunctionDef: The AST node representing `async_unload_entry`.
    """
    tree = ast.parse(INIT.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_unload_entry":
            return node
    raise AssertionError("async_unload_entry not found")


def _call_line(function: ast.AsyncFunctionDef, attr: str) -> int:
    """
    Find the source-line number of the first attribute call with the given name inside an async function AST node.
    
    Parameters:
        function (ast.AsyncFunctionDef): The async function AST node to search.
        attr (str): The attribute name of the call to locate (e.g., "async_shutdown").
    
    Returns:
        int: The line number where the first matching attribute call occurs.
    
    Raises:
        AssertionError: If no call to the specified attribute is found.
    """
    for node in ast.walk(function):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == attr
        ):
            return node.lineno
    raise AssertionError(f"{attr} call not found")


def test_unload_platforms_before_coordinator_shutdown() -> None:
    """
    Verify platforms are unloaded before the coordinator is shut down.
    
    Asserts that the call to `async_unload_platforms` appears earlier in `async_unload_entry`
    than the call to `async_shutdown`, ensuring the coordinator is not stopped while the entry may still be considered loaded.
    """
    function = _async_unload_entry()

    assert _call_line(function, "async_unload_platforms") < _call_line(
        function, "async_shutdown"
    )


def test_coordinator_shutdown_is_success_gated() -> None:
    """
    Assert that coordinator shutdown is executed only after a successful platform unload.
    
    Checks the AST of `async_unload_entry` and fails unless an `if not unload_ok: ... return` block appears before the call to `async_shutdown`, ensuring shutdown is gated on unload success.
    """
    function = _async_unload_entry()
    shutdown_line = _call_line(function, "async_shutdown")

    failure_blocks = [
        node
        for node in ast.walk(function)
        if isinstance(node, ast.If)
        and isinstance(node.test, ast.UnaryOp)
        and isinstance(node.test.op, ast.Not)
        and isinstance(node.test.operand, ast.Name)
        and node.test.operand.id == "unload_ok"
        and node.lineno < shutdown_line
        and any(isinstance(stmt, ast.Return) for stmt in node.body)
    ]
    assert failure_blocks, "async_shutdown must be after if not unload_ok return"
