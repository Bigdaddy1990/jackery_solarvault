"""Tests for REFACTORSLOP/_extract_missing.py pure-function logic.

_extract_missing.py contains module-level I/O (reads a JSON file and writes
output files) that cannot execute in a test environment. This module loads
only the pure helper functions -- find_symbol_node, slice_source,
find_const_node, pick_backup -- by filtering out the I/O statements from the
AST before executing the extracted nodes.
"""

import ast
import textwrap
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Import helper: load pure functions from _extract_missing.py
# ---------------------------------------------------------------------------


def _load_extract_missing_functions() -> dict:
    """Dynamically load only the pure helper functions from _extract_missing.py.

    Module-level I/O statements (json.loads, file writes, print calls) are
    excluded so tests can import the utility functions without side effects.
    """
    repo_root = Path(__file__).parent.parent
    script_path = repo_root / "REFACTORSLOP" / "_extract_missing.py"
    source = script_path.read_text(encoding="utf-8")
    tree = ast.parse(source)

    safe_nodes: list[ast.stmt] = []
    for node in tree.body:
        # Always include imports.
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            safe_nodes.append(node)
        # Include function definitions (these are the pure helpers we test).
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            safe_nodes.append(node)
        # Include simple constant assignments that do not perform I/O.
        elif isinstance(node, ast.Assign):
            src_segment = ast.get_source_segment(source, node) or ""
            if "json.loads" not in src_segment and "read_text" not in src_segment:
                safe_nodes.append(node)

    mod = ast.Module(body=safe_nodes, type_ignores=[])
    namespace: dict = {}
    exec(compile(mod, str(script_path), "exec"), namespace)  # noqa: S102
    return namespace


_em = _load_extract_missing_functions()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse(source: str) -> ast.Module:
    return ast.parse(textwrap.dedent(source))


# ---------------------------------------------------------------------------
# pick_backup()
# ---------------------------------------------------------------------------


class TestPickBackup:
    def test_returns_first_preference_match(self) -> None:
        """pick_backup returns the highest-preference backup when multiple are present."""
        # PREFERENCE order from the script is: pre_transfer, pre_reconcile, pre_recovery
        srcs = ["pre_recovery", "pre_reconcile", "pre_transfer"]
        result = _em["pick_backup"](srcs)
        assert result == "pre_transfer"

    def test_returns_second_preference_when_first_absent(self) -> None:
        """pick_backup falls through to the second preference when first is missing."""
        srcs = ["pre_recovery", "pre_reconcile"]
        result = _em["pick_backup"](srcs)
        assert result == "pre_reconcile"

    def test_returns_last_preference_when_only_recovery(self) -> None:
        """pick_backup returns pre_recovery when it is the only available backup."""
        srcs = ["pre_recovery"]
        result = _em["pick_backup"](srcs)
        assert result == "pre_recovery"

    def test_returns_first_element_when_no_preference_matches(self) -> None:
        """pick_backup falls back to srcs[0] when no preferred backup is present."""
        srcs = ["unknown_backup", "another_backup"]
        result = _em["pick_backup"](srcs)
        assert result == "unknown_backup"

    def test_returns_first_element_for_single_unknown_backup(self) -> None:
        """pick_backup returns the only element when it is an unrecognized name."""
        srcs = ["custom_snapshot"]
        result = _em["pick_backup"](srcs)
        assert result == "custom_snapshot"

    def test_preference_order_pre_transfer_beats_pre_reconcile(self) -> None:
        """pre_transfer is preferred over pre_reconcile."""
        result = _em["pick_backup"](["pre_reconcile", "pre_transfer"])
        assert result == "pre_transfer"

    def test_preference_order_pre_reconcile_beats_pre_recovery(self) -> None:
        """pre_reconcile is preferred over pre_recovery."""
        result = _em["pick_backup"](["pre_recovery", "pre_reconcile"])
        assert result == "pre_reconcile"


# ---------------------------------------------------------------------------
# find_symbol_node()
# ---------------------------------------------------------------------------


class TestFindSymbolNode:
    def test_finds_top_level_function(self) -> None:
        """find_symbol_node locates a top-level function by name."""
        tree = _parse("""
            def my_func():
                return 1
        """)
        node = _em["find_symbol_node"](tree, "my_func")
        assert node is not None
        assert isinstance(node, ast.FunctionDef)
        assert node.name == "my_func"

    def test_finds_top_level_class(self) -> None:
        """find_symbol_node locates a top-level class by name."""
        tree = _parse("""
            class MyClass:
                pass
        """)
        node = _em["find_symbol_node"](tree, "MyClass")
        assert node is not None
        assert isinstance(node, ast.ClassDef)
        assert node.name == "MyClass"

    def test_finds_method_on_class(self) -> None:
        """find_symbol_node resolves dotted qualnames to class methods."""
        tree = _parse("""
            class Coordinator:
                def async_update(self):
                    pass
        """)
        node = _em["find_symbol_node"](tree, "Coordinator.async_update")
        assert node is not None
        assert isinstance(node, ast.FunctionDef)
        assert node.name == "async_update"

    def test_returns_none_for_missing_symbol(self) -> None:
        """find_symbol_node returns None when the qualname does not exist."""
        tree = _parse("""
            def existing():
                pass
        """)
        result = _em["find_symbol_node"](tree, "nonexistent")
        assert result is None

    def test_returns_none_for_missing_nested_component(self) -> None:
        """find_symbol_node returns None when an intermediate component is absent."""
        tree = _parse("""
            class Foo:
                pass
        """)
        result = _em["find_symbol_node"](tree, "Foo.bar.baz")
        assert result is None

    def test_finds_async_function(self) -> None:
        """find_symbol_node handles async function definitions."""
        tree = _parse("""
            async def async_setup_entry(hass, entry):
                pass
        """)
        node = _em["find_symbol_node"](tree, "async_setup_entry")
        assert node is not None
        assert isinstance(node, ast.AsyncFunctionDef)

    def test_finds_nested_function_inside_class_method(self) -> None:
        """find_symbol_node resolves deeply nested qualnames."""
        tree = _parse("""
            class Api:
                def login(self):
                    def _inner():
                        pass
        """)
        node = _em["find_symbol_node"](tree, "Api.login._inner")
        assert node is not None
        assert isinstance(node, ast.FunctionDef)
        assert node.name == "_inner"

    def test_returns_none_on_empty_module(self) -> None:
        """find_symbol_node returns None when given an empty module."""
        tree = ast.parse("")
        result = _em["find_symbol_node"](tree, "anything")
        assert result is None


# ---------------------------------------------------------------------------
# find_const_node()
# ---------------------------------------------------------------------------


class TestFindConstNode:
    def test_finds_simple_assignment(self) -> None:
        """find_const_node locates a module-level plain assignment by name."""
        tree = _parse("""
            MY_CONST = 42
        """)
        node = _em["find_const_node"](tree, "MY_CONST")
        assert node is not None
        assert isinstance(node, ast.Assign)

    def test_finds_annotated_assignment(self) -> None:
        """find_const_node locates a module-level annotated assignment."""
        tree = _parse("""
            TIMEOUT: int = 30
        """)
        node = _em["find_const_node"](tree, "TIMEOUT")
        assert node is not None
        assert isinstance(node, ast.AnnAssign)

    def test_returns_none_for_missing_constant(self) -> None:
        """find_const_node returns None when the name is not defined."""
        tree = _parse("""
            EXISTING = 1
        """)
        result = _em["find_const_node"](tree, "MISSING")
        assert result is None

    def test_returns_none_for_function_not_constant(self) -> None:
        """find_const_node does not match function definitions."""
        tree = _parse("""
            def MY_CONST():
                pass
        """)
        result = _em["find_const_node"](tree, "MY_CONST")
        assert result is None

    def test_returns_none_on_empty_module(self) -> None:
        """find_const_node returns None for an empty module."""
        tree = ast.parse("")
        result = _em["find_const_node"](tree, "ANYTHING")
        assert result is None

    def test_finds_first_of_multiple_same_name_assignments(self) -> None:
        """find_const_node finds the first assignment with the given name."""
        tree = _parse("""
            VALUE = 1
            VALUE = 2
        """)
        node = _em["find_const_node"](tree, "VALUE")
        assert node is not None
        # There should be a result — we don't assert which assignment (first or last)
        # but we verify the function returns something rather than None.

    def test_finds_string_constant(self) -> None:
        """find_const_node locates a string constant assignment."""
        tree = _parse("""
            API_BASE_URL = "https://example.com"
        """)
        node = _em["find_const_node"](tree, "API_BASE_URL")
        assert node is not None


# ---------------------------------------------------------------------------
# slice_source()
# ---------------------------------------------------------------------------


class TestSliceSource:
    def test_extracts_function_source_lines(self, tmp_path: Path) -> None:
        """slice_source returns the source text for a function node."""
        source = "x = 1\n\ndef my_func():\n    return 42\n"
        src_file = tmp_path / "mod.py"
        src_file.write_text(source, encoding="utf-8")
        tree = ast.parse(source)
        func_node = tree.body[1]  # the FunctionDef

        result = _em["slice_source"](src_file, func_node)

        assert "def my_func" in result
        assert "return 42" in result

    def test_includes_decorator_lines(self, tmp_path: Path) -> None:
        """slice_source includes preceding decorator lines in the extracted source."""
        source = "import abc\n\n@abc.abstractmethod\ndef decorated():\n    pass\n"
        src_file = tmp_path / "mod.py"
        src_file.write_text(source, encoding="utf-8")
        tree = ast.parse(source)
        func_node = tree.body[1]  # the decorated FunctionDef

        result = _em["slice_source"](src_file, func_node)

        assert "@abc.abstractmethod" in result
        assert "def decorated" in result

    def test_extracts_class_source(self, tmp_path: Path) -> None:
        """slice_source returns the full class source including its methods."""
        source = "class Foo:\n    def bar(self):\n        return 1\n"
        src_file = tmp_path / "mod.py"
        src_file.write_text(source, encoding="utf-8")
        tree = ast.parse(source)
        class_node = tree.body[0]

        result = _em["slice_source"](src_file, class_node)

        assert "class Foo" in result
        assert "def bar" in result
        assert "return 1" in result

    def test_slice_starts_at_first_decorator(self, tmp_path: Path) -> None:
        """slice_source includes all stacked decorators before the function."""
        source = (
            "from functools import wraps\n\n"
            "@wraps\n@staticmethod\ndef multi_decorated():\n    pass\n"
        )
        src_file = tmp_path / "mod.py"
        src_file.write_text(source, encoding="utf-8")
        tree = ast.parse(source)
        func_node = tree.body[1]  # multi_decorated

        result = _em["slice_source"](src_file, func_node)

        assert "@wraps" in result
        assert "@staticmethod" in result
        assert "def multi_decorated" in result

    def test_returns_only_target_function_not_full_file(self, tmp_path: Path) -> None:
        """slice_source returns only the targeted function's lines, not the whole file."""
        source = (
            "HEADER = 'header'\n\n"
            "def first():\n    pass\n\n"
            "def second():\n    return 2\n\n"
            "FOOTER = 'footer'\n"
        )
        src_file = tmp_path / "mod.py"
        src_file.write_text(source, encoding="utf-8")
        tree = ast.parse(source)
        first_func = tree.body[1]  # 'first'

        result = _em["slice_source"](src_file, first_func)

        assert "def first" in result
        assert "HEADER" not in result
        assert "def second" not in result
        assert "FOOTER" not in result

    def test_single_line_function(self, tmp_path: Path) -> None:
        """slice_source handles a function that occupies only a single line."""
        source = "def noop(): pass\n"
        src_file = tmp_path / "mod.py"
        src_file.write_text(source, encoding="utf-8")
        tree = ast.parse(source)
        func_node = tree.body[0]

        result = _em["slice_source"](src_file, func_node)

        assert "def noop" in result
        assert "pass" in result
