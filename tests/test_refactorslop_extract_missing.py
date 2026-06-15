"""Tests for pure functions in REFACTORSLOP/_extract_missing.py.

The script contains module-level I/O that runs unconditionally at import time
(reads _loss_audit_current.json, writes _missing_sources.md/.json), so we load
it through importlib with mocked filesystem operations.

Functions under test:
  * pick_backup       – choose the preferred backup name from a list
  * find_symbol_node  – walk an AST to locate a qualified symbol
  * find_const_node   – locate a module-level constant assignment by name
  * slice_source      – slice source lines for a given AST node
"""

from __future__ import annotations

import ast
import importlib.util
import json
import sys
import types
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

# ---------------------------------------------------------------------------
# Load the module without triggering any real file I/O
# ---------------------------------------------------------------------------

_EMPTY_AUDIT = json.dumps({"TRULY_MISSING_SYMBOLS": {}, "TRULY_MISSING_CONSTS": {}})
_MODULE_PATH = str(Path(__file__).parent.parent / "REFACTORSLOP" / "_extract_missing.py")


def _load_extract_missing() -> types.ModuleType:
    """Import _extract_missing with all filesystem I/O suppressed."""
    with (
        patch("pathlib.Path.read_text", return_value=_EMPTY_AUDIT),
        patch("pathlib.Path.write_text", return_value=None),
    ):
        spec = importlib.util.spec_from_file_location("_extract_missing", _MODULE_PATH)
        assert spec is not None and spec.loader is not None
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


@pytest.fixture(scope="module")
def em() -> types.ModuleType:
    """Module fixture – loaded once per test-module session."""
    return _load_extract_missing()


# ---------------------------------------------------------------------------
# pick_backup
# ---------------------------------------------------------------------------


def test_pick_backup_chooses_pre_transfer_first(em: types.ModuleType) -> None:
    """pre_transfer is the highest-priority backup."""
    result = em.pick_backup(["pre_recovery", "pre_transfer", "pre_reconcile"])
    assert result == "pre_transfer"


def test_pick_backup_falls_back_to_pre_reconcile(em: types.ModuleType) -> None:
    """When pre_transfer is absent, pre_reconcile is preferred over pre_recovery."""
    result = em.pick_backup(["pre_recovery", "pre_reconcile"])
    assert result == "pre_reconcile"


def test_pick_backup_falls_back_to_pre_recovery(em: types.ModuleType) -> None:
    """When only pre_recovery is available it is returned."""
    result = em.pick_backup(["pre_recovery"])
    assert result == "pre_recovery"


def test_pick_backup_returns_first_element_for_unknown_names(em: types.ModuleType) -> None:
    """An unrecognised backup name falls through to the first element of srcs."""
    result = em.pick_backup(["unknown_backup"])
    assert result == "unknown_backup"


def test_pick_backup_single_pre_transfer(em: types.ModuleType) -> None:
    """Single-element list is returned as-is when it matches the preference."""
    assert em.pick_backup(["pre_transfer"]) == "pre_transfer"


def test_pick_backup_preference_order_not_position(em: types.ModuleType) -> None:
    """Preference is determined by PREFERENCE list order, not the input list order."""
    # Even if pre_reconcile appears first in input, pre_transfer wins.
    result = em.pick_backup(["pre_reconcile", "pre_transfer"])
    assert result == "pre_transfer"


# ---------------------------------------------------------------------------
# find_symbol_node – locate a qualified symbol in an AST
# ---------------------------------------------------------------------------


def _parse(src: str) -> ast.Module:
    return ast.parse(src)


def test_find_symbol_node_top_level_function(em: types.ModuleType) -> None:
    """A top-level function is found by its unqualified name."""
    tree = _parse("def hello():\n    pass\n")
    node = em.find_symbol_node(tree, "hello")
    assert node is not None
    assert isinstance(node, ast.FunctionDef)
    assert node.name == "hello"


def test_find_symbol_node_top_level_class(em: types.ModuleType) -> None:
    """A top-level class is found by its unqualified name."""
    tree = _parse("class Foo:\n    pass\n")
    node = em.find_symbol_node(tree, "Foo")
    assert node is not None
    assert isinstance(node, ast.ClassDef)
    assert node.name == "Foo"


def test_find_symbol_node_method(em: types.ModuleType) -> None:
    """A class method is found via dotted qualname."""
    src = "class Foo:\n    def bar(self):\n        return 42\n"
    tree = _parse(src)
    node = em.find_symbol_node(tree, "Foo.bar")
    assert node is not None
    assert isinstance(node, ast.FunctionDef)
    assert node.name == "bar"


def test_find_symbol_node_nested_class(em: types.ModuleType) -> None:
    """Deeply nested class is found via multi-part dotted qualname."""
    src = (
        "class Outer:\n"
        "    class Inner:\n"
        "        def method(self): pass\n"
    )
    tree = _parse(src)
    node = em.find_symbol_node(tree, "Outer.Inner.method")
    assert node is not None
    assert isinstance(node, ast.FunctionDef)
    assert node.name == "method"


def test_find_symbol_node_not_found_returns_none(em: types.ModuleType) -> None:
    """A missing symbol returns None without raising."""
    tree = _parse("x = 1\n")
    result = em.find_symbol_node(tree, "nonexistent")
    assert result is None


def test_find_symbol_node_partial_path_not_found(em: types.ModuleType) -> None:
    """A valid class but wrong method name returns None."""
    src = "class Foo:\n    def bar(self): pass\n"
    tree = _parse(src)
    result = em.find_symbol_node(tree, "Foo.baz")
    assert result is None


def test_find_symbol_node_async_function(em: types.ModuleType) -> None:
    """Async function definitions are found like regular functions."""
    tree = _parse("async def fetch():\n    pass\n")
    node = em.find_symbol_node(tree, "fetch")
    assert node is not None
    assert isinstance(node, ast.AsyncFunctionDef)


def test_find_symbol_node_wrong_class_in_path(em: types.ModuleType) -> None:
    """If the first path component does not match, returns None."""
    src = "class Foo:\n    def bar(self): pass\n"
    tree = _parse(src)
    result = em.find_symbol_node(tree, "Bar.bar")
    assert result is None


# ---------------------------------------------------------------------------
# find_const_node – locate a module-level constant assignment
# ---------------------------------------------------------------------------


def test_find_const_node_simple_assign(em: types.ModuleType) -> None:
    """A plain assignment is found by its target name."""
    tree = _parse("FOO = 42\n")
    node = em.find_const_node(tree, "FOO")
    assert node is not None
    assert isinstance(node, ast.Assign)


def test_find_const_node_annotated_assign(em: types.ModuleType) -> None:
    """An annotated assignment is found by its target name."""
    tree = _parse("BAR: int = 99\n")
    node = em.find_const_node(tree, "BAR")
    assert node is not None
    assert isinstance(node, ast.AnnAssign)


def test_find_const_node_not_found_returns_none(em: types.ModuleType) -> None:
    """A missing constant name returns None."""
    tree = _parse("FOO = 1\n")
    result = em.find_const_node(tree, "MISSING")
    assert result is None


def test_find_const_node_does_not_find_local_variables(em: types.ModuleType) -> None:
    """Variables inside functions are NOT found at module scope."""
    src = "def f():\n    LOCAL = 1\n"
    tree = _parse(src)
    result = em.find_const_node(tree, "LOCAL")
    assert result is None


def test_find_const_node_multiple_targets_on_one_line(em: types.ModuleType) -> None:
    """Chained assignment returns the Assign node if either target matches."""
    # ast.Assign supports multiple targets via tuple unpacking; simple chained
    # assignments (A = B = 1) also produce a single Assign with two targets.
    src = "A = B = 1\n"
    tree = _parse(src)
    # Both A and B share the same Assign node.
    node_a = em.find_const_node(tree, "A")
    node_b = em.find_const_node(tree, "B")
    assert node_a is not None
    assert node_b is not None


def test_find_const_node_returns_first_occurrence(em: types.ModuleType) -> None:
    """When the same name appears twice, the first occurrence is returned."""
    src = "X = 1\nX = 2\n"
    tree = _parse(src)
    node = em.find_const_node(tree, "X")
    assert node is not None
    # The returned node should be the first Assign; its value should be 1.
    assert isinstance(node, ast.Assign)
    assert ast.literal_eval(node.value) == 1  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# slice_source – slice lines from a source file for a given AST node
# ---------------------------------------------------------------------------


def test_slice_source_extracts_correct_lines(em: types.ModuleType, tmp_path: Path) -> None:
    """slice_source returns exactly the lines spanned by the node."""
    src = "x = 1\ndef f():\n    return x\n"
    py = tmp_path / "mod.py"
    py.write_text(src, encoding="utf-8")

    tree = ast.parse(src)
    func_node = tree.body[1]  # `def f()`
    result = em.slice_source(py, func_node)

    assert "def f():" in result
    assert "return x" in result
    assert "x = 1" not in result


def test_slice_source_single_line_node(em: types.ModuleType, tmp_path: Path) -> None:
    """A single-line function is returned without extra lines."""
    src = "def g(): pass\n"
    py = tmp_path / "mod.py"
    py.write_text(src, encoding="utf-8")

    tree = ast.parse(src)
    func_node = tree.body[0]
    result = em.slice_source(py, func_node)

    assert result.strip() == "def g(): pass"


def test_slice_source_includes_decorators(em: types.ModuleType, tmp_path: Path) -> None:
    """When a node has decorators, the preceding decorator lines are included."""
    src = "@property\ndef value(self):\n    return 42\n"
    py = tmp_path / "mod.py"
    py.write_text(src, encoding="utf-8")

    tree = ast.parse(src)
    func_node = tree.body[0]
    result = em.slice_source(py, func_node)

    assert "@property" in result
    assert "def value" in result


def test_slice_source_multiline_function(em: types.ModuleType, tmp_path: Path) -> None:
    """A multi-line function body is fully included."""
    src = (
        "def compute(x, y):\n"
        "    a = x * 2\n"
        "    b = y + 1\n"
        "    return a + b\n"
    )
    py = tmp_path / "mod.py"
    py.write_text(src, encoding="utf-8")

    tree = ast.parse(src)
    func_node = tree.body[0]
    result = em.slice_source(py, func_node)

    assert "def compute" in result
    assert "a = x * 2" in result
    assert "b = y + 1" in result
    assert "return a + b" in result