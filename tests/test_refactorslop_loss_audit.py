"""Tests for REFACTORSLOP/_loss_audit.py.

These scripts were added in this PR as new files.  The functions under test
are:
  * SymbolInfo / ModuleInfo  – dataclasses
  * _count_stmts             – statement counter for an AST node
  * extract                  – parse a .py file and return a ModuleInfo
  * collect_tree             – gather ModuleInfo for every .py in a directory
"""

from __future__ import annotations

import ast
import sys
from dataclasses import fields
from pathlib import Path

import pytest

# ---------------------------------------------------------------------------
# Import the module under test (no file I/O at module level for this script)
# ---------------------------------------------------------------------------
sys.path.insert(0, str(Path(__file__).parent.parent))
from REFACTORSLOP._loss_audit import (  # noqa: E402
    ModuleInfo,
    SymbolInfo,
    _count_stmts,
    collect_tree,
    extract,
)


# ---------------------------------------------------------------------------
# SymbolInfo dataclass
# ---------------------------------------------------------------------------


def test_symbol_info_fields() -> None:
    """SymbolInfo stores qualname, kind, lineno, and stmt_count."""
    sym = SymbolInfo(qualname="foo.bar", kind="function", lineno=10, stmt_count=5)
    assert sym.qualname == "foo.bar"
    assert sym.kind == "function"
    assert sym.lineno == 10
    assert sym.stmt_count == 5


def test_symbol_info_field_names() -> None:
    """SymbolInfo has exactly the four expected fields."""
    names = {f.name for f in fields(SymbolInfo)}
    assert names == {"qualname", "kind", "lineno", "stmt_count"}


def test_symbol_info_class_kind() -> None:
    """SymbolInfo accepts 'class' as the kind value."""
    sym = SymbolInfo(qualname="MyClass", kind="class", lineno=1, stmt_count=20)
    assert sym.kind == "class"


# ---------------------------------------------------------------------------
# ModuleInfo dataclass
# ---------------------------------------------------------------------------


def test_module_info_defaults() -> None:
    """ModuleInfo initialises with empty symbols, constants, and no parse error."""
    info = ModuleInfo()
    assert info.symbols == {}
    assert info.constants == set()
    assert info.parse_error is None


def test_module_info_instances_are_independent() -> None:
    """Two ModuleInfo instances do not share the same mutable containers."""
    a = ModuleInfo()
    b = ModuleInfo()
    a.constants.add("FOO")
    assert "FOO" not in b.constants


def test_module_info_parse_error_is_stored() -> None:
    """A parse error string is stored verbatim."""
    info = ModuleInfo(parse_error="SyntaxError: unexpected token")
    assert info.parse_error == "SyntaxError: unexpected token"


# ---------------------------------------------------------------------------
# _count_stmts
# ---------------------------------------------------------------------------


def test_count_stmts_single_pass() -> None:
    """A one-pass function counts the function node plus pass."""
    src = "def f():\n    pass\n"
    tree = ast.parse(src)
    func = tree.body[0]
    assert _count_stmts(func) == 2


def test_count_stmts_multiple_statements() -> None:
    """Statement count includes all nested statements recursively."""
    src = (
        "def f():\n"
        "    x = 1\n"
        "    y = 2\n"
        "    return x + y\n"
    )
    tree = ast.parse(src)
    func = tree.body[0]
    count = _count_stmts(func)
    # function node + 3 body statements
    assert count == 4


def test_count_stmts_nested_function() -> None:
    """Nested function bodies are counted recursively."""
    src = (
        "def outer():\n"
        "    def inner():\n"
        "        return 1\n"
        "    return inner()\n"
    )
    tree = ast.parse(src)
    outer = tree.body[0]
    count = _count_stmts(outer)
    # outer has: def inner(...) stmt + return stmt = 2 top-level
    # inner has: return 1 = 1 stmt
    # total recursive plus outer function node = 4
    assert count == 4


def test_count_stmts_if_block() -> None:
    """If statement body is counted."""
    src = (
        "def f(x):\n"
        "    if x:\n"
        "        y = 1\n"
        "        return y\n"
        "    return 0\n"
    )
    tree = ast.parse(src)
    func = tree.body[0]
    count = _count_stmts(func)
    # function node, if stmt, y=1, return y, return 0 = 5
    assert count == 5


def test_count_stmts_class_body() -> None:
    """Class body statements are counted."""
    src = (
        "class Foo:\n"
        "    x: int = 0\n"
        "    def method(self) -> None:\n"
        "        pass\n"
    )
    tree = ast.parse(src)
    cls = tree.body[0]
    count = _count_stmts(cls)
    # class node + annotated assignment + def method + pass = 4
    assert count == 4


def test_count_stmts_empty_function() -> None:
    """A docstring-only function counts the function node plus Expr."""
    src = 'def f():\n    """Docstring."""\n'
    tree = ast.parse(src)
    func = tree.body[0]
    # function node + docstring Expr statement
    assert _count_stmts(func) == 2


def test_count_stmts_returns_int() -> None:
    """_count_stmts always returns an integer."""
    src = "def f(): pass\n"
    tree = ast.parse(src)
    result = _count_stmts(tree.body[0])
    assert isinstance(result, int)


# ---------------------------------------------------------------------------
# extract
# ---------------------------------------------------------------------------


def test_extract_simple_function(tmp_path: Path) -> None:
    """extract() recognises a module-level function."""
    src = "def hello():\n    pass\n"
    py = tmp_path / "mod.py"
    py.write_text(src, encoding="utf-8")

    info = extract(py)

    assert info.parse_error is None
    assert "hello" in info.symbols
    sym = info.symbols["hello"]
    assert sym.kind == "function"
    assert sym.qualname == "hello"


def test_extract_class_and_method(tmp_path: Path) -> None:
    """extract() records both the class and its method with dotted qualname."""
    src = (
        "class Foo:\n"
        "    def bar(self):\n"
        "        return 42\n"
    )
    py = tmp_path / "mod.py"
    py.write_text(src, encoding="utf-8")

    info = extract(py)

    assert "Foo" in info.symbols
    assert "Foo.bar" in info.symbols
    assert info.symbols["Foo"].kind == "class"
    assert info.symbols["Foo.bar"].kind == "function"


def test_extract_async_function(tmp_path: Path) -> None:
    """extract() handles async function definitions."""
    src = "async def fetch():\n    pass\n"
    py = tmp_path / "mod.py"
    py.write_text(src, encoding="utf-8")

    info = extract(py)

    assert "fetch" in info.symbols
    assert info.symbols["fetch"].kind == "function"


def test_extract_module_level_constants(tmp_path: Path) -> None:
    """extract() collects module-level Assign and AnnAssign names."""
    src = (
        "FOO = 1\n"
        "BAR: int = 2\n"
        "baz = 3\n"  # lowercase – still collected as a constant name
    )
    py = tmp_path / "const.py"
    py.write_text(src, encoding="utf-8")

    info = extract(py)

    assert "FOO" in info.constants
    assert "BAR" in info.constants
    assert "baz" in info.constants


def test_extract_no_constants_when_only_functions(tmp_path: Path) -> None:
    """Module with only function defs has empty constants set."""
    src = "def f():\n    x = 1\n    return x\n"
    py = tmp_path / "mod.py"
    py.write_text(src, encoding="utf-8")

    info = extract(py)

    assert info.constants == set()


def test_extract_syntax_error_returns_parse_error(tmp_path: Path) -> None:
    """A file with a syntax error sets parse_error and returns no symbols."""
    py = tmp_path / "bad.py"
    py.write_text("def f(\n    broken {\n", encoding="utf-8")

    info = extract(py)

    assert info.parse_error is not None
    assert "SyntaxError" in info.parse_error
    assert info.symbols == {}


def test_extract_nested_class(tmp_path: Path) -> None:
    """Nested classes and their methods receive dotted qualnames."""
    src = (
        "class Outer:\n"
        "    class Inner:\n"
        "        def method(self):\n"
        "            pass\n"
    )
    py = tmp_path / "mod.py"
    py.write_text(src, encoding="utf-8")

    info = extract(py)

    assert "Outer" in info.symbols
    assert "Outer.Inner" in info.symbols
    assert "Outer.Inner.method" in info.symbols


def test_extract_stmt_count_stored_in_symbol(tmp_path: Path) -> None:
    """Symbol statement counts are persisted correctly."""
    src = "def f():\n    a = 1\n    b = 2\n    return a + b\n"
    py = tmp_path / "mod.py"
    py.write_text(src, encoding="utf-8")

    info = extract(py)

    assert info.symbols["f"].stmt_count == 4


def test_extract_lineno_is_function_start(tmp_path: Path) -> None:
    """SymbolInfo.lineno reflects the actual start line in the source."""
    src = "\n\n\ndef f():\n    pass\n"
    py = tmp_path / "mod.py"
    py.write_text(src, encoding="utf-8")

    info = extract(py)

    assert info.symbols["f"].lineno == 4


def test_extract_empty_file(tmp_path: Path) -> None:
    """An empty file produces a ModuleInfo with empty symbols and constants."""
    py = tmp_path / "empty.py"
    py.write_text("", encoding="utf-8")

    info = extract(py)

    assert info.parse_error is None
    assert info.symbols == {}
    assert info.constants == set()


# ---------------------------------------------------------------------------
# collect_tree
# ---------------------------------------------------------------------------


def test_collect_tree_returns_empty_for_missing_dir(tmp_path: Path) -> None:
    """Non-existent base directory returns an empty dict."""
    result = collect_tree(tmp_path / "does_not_exist")
    assert result == {}


def test_collect_tree_single_file(tmp_path: Path) -> None:
    """A single .py file is reflected as a single entry with relative posix path."""
    py = tmp_path / "module.py"
    py.write_text("X = 1\n", encoding="utf-8")

    result = collect_tree(tmp_path)

    assert "module.py" in result
    assert "X" in result["module.py"].constants


def test_collect_tree_nested_directories(tmp_path: Path) -> None:
    """Python files in subdirectories produce posix-relative paths."""
    sub = tmp_path / "pkg"
    sub.mkdir()
    (sub / "__init__.py").write_text("", encoding="utf-8")
    (sub / "mod.py").write_text("def f(): pass\n", encoding="utf-8")

    result = collect_tree(tmp_path)

    assert "pkg/__init__.py" in result
    assert "pkg/mod.py" in result
    assert "f" in result["pkg/mod.py"].symbols


def test_collect_tree_ignores_non_py_files(tmp_path: Path) -> None:
    """Non-.py files are not collected."""
    (tmp_path / "notes.txt").write_text("hello\n")
    (tmp_path / "data.json").write_text("{}")
    (tmp_path / "script.py").write_text("pass\n")

    result = collect_tree(tmp_path)

    assert list(result.keys()) == ["script.py"]


def test_collect_tree_multiple_files(tmp_path: Path) -> None:
    """All .py files in the tree are collected."""
    (tmp_path / "a.py").write_text("A = 1\n")
    (tmp_path / "b.py").write_text("B = 2\n")
    (tmp_path / "c.py").write_text("C = 3\n")

    result = collect_tree(tmp_path)

    assert set(result.keys()) == {"a.py", "b.py", "c.py"}


def test_collect_tree_parse_error_recorded(tmp_path: Path) -> None:
    """A file that cannot be parsed has its error recorded in ModuleInfo."""
    bad = tmp_path / "bad.py"
    bad.write_text("def f(:\n    pass\n", encoding="utf-8")

    result = collect_tree(tmp_path)

    assert "bad.py" in result
    assert result["bad.py"].parse_error is not None


def test_collect_tree_symbol_info_accessible(tmp_path: Path) -> None:
    """Symbols collected by collect_tree are proper SymbolInfo instances."""
    py = tmp_path / "mod.py"
    py.write_text("def greet(name):\n    return name\n")

    result = collect_tree(tmp_path)

    sym = result["mod.py"].symbols["greet"]
    assert isinstance(sym, SymbolInfo)
    assert sym.kind == "function"


# ---------------------------------------------------------------------------
# Integration: extract + collect_tree consistency
# ---------------------------------------------------------------------------


def test_extract_and_collect_tree_agree(tmp_path: Path) -> None:
    """Direct extract() and collect_tree() return identical ModuleInfo for the same file."""
    src = (
        "CONSTANT = 42\n"
        "def alpha():\n"
        "    pass\n"
        "class Beta:\n"
        "    def method(self):\n"
        "        return 0\n"
    )
    py = tmp_path / "mod.py"
    py.write_text(src, encoding="utf-8")

    direct = extract(py)
    via_tree = collect_tree(tmp_path)["mod.py"]

    assert direct.symbols.keys() == via_tree.symbols.keys()
    assert direct.constants == via_tree.constants
    assert direct.parse_error == via_tree.parse_error


def test_extract_does_not_include_local_assignments_as_constants(tmp_path: Path) -> None:
    """Variables assigned inside functions are NOT added to module-level constants."""
    src = "def f():\n    LOCAL_VAR = 1\n    return LOCAL_VAR\n"
    py = tmp_path / "mod.py"
    py.write_text(src, encoding="utf-8")

    info = extract(py)

    assert "LOCAL_VAR" not in info.constants
