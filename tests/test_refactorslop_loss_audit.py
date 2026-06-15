"""Tests for REFACTORSLOP/_loss_audit.py pure-function logic.

_loss_audit.py is safely importable: its module-level code only defines
dataclasses, constants (Path assignments), and functions.  All filesystem
I/O lives inside main() which is guarded by ``if __name__ == "__main__"``.
"""

import ast
import importlib.util
import sys
import textwrap
from pathlib import Path

import pytest


# ---------------------------------------------------------------------------
# Import helper: load _loss_audit as a module without executing main()
# ---------------------------------------------------------------------------


def _load_loss_audit():  # noqa: ANN202
    """Dynamically load REFACTORSLOP/_loss_audit.py as a module object."""
    repo_root = Path(__file__).parent.parent
    script_path = repo_root / "REFACTORSLOP" / "_loss_audit.py"
    spec = importlib.util.spec_from_file_location("_loss_audit", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)  # type: ignore[union-attr]
    return module


_loss_audit = _load_loss_audit()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse(source: str) -> ast.Module:
    return ast.parse(textwrap.dedent(source))


# ---------------------------------------------------------------------------
# _count_stmts
# ---------------------------------------------------------------------------


class TestCountStmts:
    def test_simple_function_counts_body_statements(self) -> None:
        """A function with three statements returns at least 3."""
        tree = _parse("""
            def foo():
                x = 1
                y = 2
                return x + y
        """)
        func = tree.body[0]
        count = _loss_audit._count_stmts(func)
        assert count >= 3

    def test_empty_function_has_low_count(self) -> None:
        """A pass-only function has exactly 2 statements (def + pass)."""
        tree = _parse("""
            def foo():
                pass
        """)
        func = tree.body[0]
        count = _loss_audit._count_stmts(func)
        # ast.walk yields the FunctionDef itself + the Pass stmt = 2
        assert count == 2

    def test_nested_functions_are_counted(self) -> None:
        """Nested function bodies contribute to the parent's statement count."""
        outer = _parse("""
            def outer():
                def inner():
                    x = 1
                    y = 2
                    return x
                return inner
        """).body[0]
        count = _loss_audit._count_stmts(outer)
        # outer has: FunctionDef outer, FunctionDef inner, 3 stmts in inner, Return outer
        assert count >= 5

    def test_class_counts_methods(self) -> None:
        """A class with multiple methods returns a statement count covering them."""
        tree = _parse("""
            class Foo:
                def bar(self):
                    return 1
                def baz(self):
                    return 2
        """)
        cls = tree.body[0]
        count = _loss_audit._count_stmts(cls)
        assert count >= 4


# ---------------------------------------------------------------------------
# extract()
# ---------------------------------------------------------------------------


class TestExtract:
    def test_extracts_function_symbols(self, tmp_path: Path) -> None:
        """extract() populates ModuleInfo.symbols for top-level functions."""
        src = tmp_path / "mod.py"
        src.write_text("def foo():\n    return 1\n", encoding="utf-8")

        info = _loss_audit.extract(src)

        assert info.parse_error is None
        assert "foo" in info.symbols
        sym = info.symbols["foo"]
        assert sym.kind == "function"
        assert sym.qualname == "foo"

    def test_extracts_class_symbols(self, tmp_path: Path) -> None:
        """extract() records top-level classes and their methods."""
        src = tmp_path / "mod.py"
        src.write_text(
            "class MyClass:\n    def method(self):\n        pass\n",
            encoding="utf-8",
        )

        info = _loss_audit.extract(src)

        assert "MyClass" in info.symbols
        assert "MyClass.method" in info.symbols
        assert info.symbols["MyClass"].kind == "class"

    def test_extracts_async_functions(self, tmp_path: Path) -> None:
        """extract() treats async def the same as def for symbol collection."""
        src = tmp_path / "mod.py"
        src.write_text("async def async_setup():\n    pass\n", encoding="utf-8")

        info = _loss_audit.extract(src)

        assert "async_setup" in info.symbols

    def test_extracts_module_level_constants(self, tmp_path: Path) -> None:
        """extract() collects module-level constant names from Assign nodes."""
        src = tmp_path / "mod.py"
        src.write_text("MY_CONST = 42\nOTHER = 'x'\n", encoding="utf-8")

        info = _loss_audit.extract(src)

        assert "MY_CONST" in info.constants
        assert "OTHER" in info.constants

    def test_extracts_annotated_assignment_constants(self, tmp_path: Path) -> None:
        """extract() collects annotated assignments (e.g. x: int = 5)."""
        src = tmp_path / "mod.py"
        src.write_text("TYPED_CONST: int = 99\n", encoding="utf-8")

        info = _loss_audit.extract(src)

        assert "TYPED_CONST" in info.constants

    def test_parse_error_captured(self, tmp_path: Path) -> None:
        """extract() captures SyntaxError in parse_error instead of raising."""
        src = tmp_path / "bad.py"
        src.write_text("def foo(\n", encoding="utf-8")  # incomplete — SyntaxError

        info = _loss_audit.extract(src)

        assert info.parse_error is not None
        assert "SyntaxError" in info.parse_error
        assert info.symbols == {}
        assert info.constants == set()

    def test_empty_file(self, tmp_path: Path) -> None:
        """extract() handles an empty file without errors."""
        src = tmp_path / "empty.py"
        src.write_text("", encoding="utf-8")

        info = _loss_audit.extract(src)

        assert info.parse_error is None
        assert info.symbols == {}
        assert info.constants == set()

    def test_nested_qualname(self, tmp_path: Path) -> None:
        """Nested functions use dotted qualified names."""
        src = tmp_path / "mod.py"
        src.write_text(
            "def outer():\n    def inner():\n        pass\n",
            encoding="utf-8",
        )

        info = _loss_audit.extract(src)

        assert "outer" in info.symbols
        assert "outer.inner" in info.symbols

    def test_symbol_lineno_is_recorded(self, tmp_path: Path) -> None:
        """extract() stores the line number of each symbol."""
        src = tmp_path / "mod.py"
        src.write_text("\n\ndef foo():\n    pass\n", encoding="utf-8")

        info = _loss_audit.extract(src)

        assert info.symbols["foo"].lineno == 3

    def test_stmt_count_reflects_body_size(self, tmp_path: Path) -> None:
        """stmt_count for a function with three statements is at least 3."""
        src = tmp_path / "mod.py"
        src.write_text(
            "def foo():\n    a = 1\n    b = 2\n    return a + b\n",
            encoding="utf-8",
        )

        info = _loss_audit.extract(src)

        assert info.symbols["foo"].stmt_count >= 3


# ---------------------------------------------------------------------------
# collect_tree()
# ---------------------------------------------------------------------------


class TestCollectTree:
    def test_returns_empty_dict_for_nonexistent_path(self, tmp_path: Path) -> None:
        """collect_tree() returns {} for a path that does not exist."""
        result = _loss_audit.collect_tree(tmp_path / "does_not_exist")
        assert result == {}

    def test_collects_all_py_files(self, tmp_path: Path) -> None:
        """collect_tree() collects every .py file under the given directory."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "a.py").write_text("def a(): pass\n", encoding="utf-8")
        (pkg / "b.py").write_text("def b(): pass\n", encoding="utf-8")
        sub = pkg / "sub"
        sub.mkdir()
        (sub / "c.py").write_text("def c(): pass\n", encoding="utf-8")

        result = _loss_audit.collect_tree(pkg)

        assert "a.py" in result
        assert "b.py" in result
        assert "sub/c.py" in result

    def test_ignores_non_py_files(self, tmp_path: Path) -> None:
        """collect_tree() does not include non-Python files."""
        pkg = tmp_path / "pkg"
        pkg.mkdir()
        (pkg / "readme.txt").write_text("hello", encoding="utf-8")
        (pkg / "mod.py").write_text("x = 1\n", encoding="utf-8")

        result = _loss_audit.collect_tree(pkg)

        assert "readme.txt" not in result
        assert "mod.py" in result

    def test_paths_use_forward_slashes(self, tmp_path: Path) -> None:
        """Relative paths in the result dict use forward-slash separators."""
        pkg = tmp_path / "pkg"
        sub = pkg / "sub"
        sub.mkdir(parents=True)
        (sub / "mod.py").write_text("pass\n", encoding="utf-8")

        result = _loss_audit.collect_tree(pkg)

        # Keys must use POSIX path separators regardless of OS.
        assert all("/" in k or k.count(".") >= 1 for k in result)
        assert "sub/mod.py" in result

    def test_empty_directory(self, tmp_path: Path) -> None:
        """collect_tree() on an empty directory returns an empty dict."""
        pkg = tmp_path / "empty_pkg"
        pkg.mkdir()

        result = _loss_audit.collect_tree(pkg)

        assert result == {}


# ---------------------------------------------------------------------------
# SymbolInfo / ModuleInfo dataclasses
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_symbol_info_fields(self) -> None:
        """SymbolInfo stores all expected fields."""
        sym = _loss_audit.SymbolInfo(
            qualname="Foo.bar", kind="function", lineno=10, stmt_count=5
        )
        assert sym.qualname == "Foo.bar"
        assert sym.kind == "function"
        assert sym.lineno == 10
        assert sym.stmt_count == 5

    def test_module_info_defaults(self) -> None:
        """ModuleInfo initialises with empty containers and no parse_error."""
        info = _loss_audit.ModuleInfo()
        assert info.symbols == {}
        assert info.constants == set()
        assert info.parse_error is None

    def test_module_info_independent_defaults(self) -> None:
        """Each ModuleInfo instance gets its own mutable default containers."""
        a = _loss_audit.ModuleInfo()
        b = _loss_audit.ModuleInfo()
        a.symbols["x"] = _loss_audit.SymbolInfo("x", "function", 1, 1)
        assert "x" not in b.symbols
