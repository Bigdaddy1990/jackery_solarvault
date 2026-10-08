"""Regressions for the compile/import release gate from issue #278."""

import __future__

import ast
import shutil
import subprocess
import sys
from typing import TYPE_CHECKING

import pytest
from scripts.check_integration_imports import (
    INTEGRATION,
    ROOT,
    compile_sources,
    module_name,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@pytest.mark.parametrize(
    "handler",
    [
        "except TypeError, ValueError:",
        "except TypeError, ValueError, OverflowError:",
        "except* TypeError, ValueError, OverflowError:",
    ],
)
def test_compile_gate_rejects_bare_exception_tuples(
    tmp_path: Path, handler: str
) -> None:
    """Two, three and except-star forms must retain portable parentheses."""
    (tmp_path / "broken.py").write_text(
        f"try:\n    pass\n{handler}\n    pass\n", encoding="utf-8"
    )
    with pytest.raises(SyntaxError, match="parenthes"):
        compile_sources(tmp_path)


def test_compile_gate_checks_nested_sources_without_cached_bytecode(
    tmp_path: Path,
) -> None:
    """Every shipped submodule is checked even if a stale pyc exists."""
    nested = tmp_path / "client"
    nested.mkdir()
    source = nested / "broken.py"
    source.write_text("return 1\n", encoding="utf-8")
    (nested / "broken.pyc").write_bytes(b"stale bytecode")
    with pytest.raises(SyntaxError, match="outside function"):
        compile_sources(tmp_path)


def test_compile_gate_accepts_parenthesized_multiline_handlers(
    tmp_path: Path,
) -> None:
    """Valid multiline exception tuples must not trigger a regex false positive."""
    source = tmp_path / "valid.py"
    source.write_text(
        "try:\n    pass\nexcept (\n    TypeError,\n    ValueError,\n"
        "    OverflowError,\n):\n    pass\n",
        encoding="utf-8",
    )
    assert compile_sources(tmp_path) == [source]


def test_compile_gate_rejects_empty_source_tree(tmp_path: Path) -> None:
    """An incorrect path must fail instead of silently checking zero files."""
    with pytest.raises(ValueError, match="No integration Python sources"):
        compile_sources(tmp_path)


def test_select_helper_defers_forward_annotation() -> None:
    """The actual helper stays safe under eager annotation evaluation."""
    source = INTEGRATION / "select.py"
    tree = ast.parse(source.read_bytes())
    future_imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom) and node.module == "__future__"
    ]
    helper = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef)
        and node.name == "_raise_select_action_error"
    )
    isolated = ast.Module(body=[*future_imports, helper], type_ignores=[])
    code = compile(isolated, str(source), "exec", dont_inherit=True)
    assert code.co_flags & __future__.annotations.compiler_flag
    namespace: dict[str, Callable[..., object]] = {}
    exec(code, namespace)  # ruff: ignore[exec-builtin]
    assert namespace[helper.name].__annotations__["entity"] == "JackerySelect"


@pytest.mark.parametrize(
    ["relative_path", "expected"],
    [
        ["__init__.py", "custom_components.jackery_solarvault"],
        ["client/__init__.py", "custom_components.jackery_solarvault.client"],
        ["select.py", "custom_components.jackery_solarvault.select"],
    ],
)
def test_import_gate_resolves_packages(relative_path: str, expected: str) -> None:
    """Package initializers must be imported by package name."""
    assert module_name(INTEGRATION / relative_path) == expected


def test_all_shipped_modules_import_in_fresh_process() -> None:
    """Real HA imports must succeed outside pytest's cached or stubbed modules."""
    result = subprocess.run(
        [sys.executable, "-m", "scripts.check_integration_imports"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    count = len(list(INTEGRATION.rglob("*.py")))
    assert f"Compiled and imported all {count} integration modules" in result.stdout


@pytest.mark.parametrize(
    ["source", "error"],
    [
        ["raise NameError('undefined forward reference')\n", "NameError"],
        ["import missing_jackery_gate_dependency\n", "ModuleNotFoundError"],
    ],
)
def test_import_gate_fails_on_runtime_errors(
    tmp_path: Path, source: str, error: str
) -> None:
    """Syntactically valid import failures must stop the release command."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "__init__.py").touch()
    (scripts / "check_integration_imports.py").write_bytes(
        (ROOT / "scripts" / "check_integration_imports.py").read_bytes()
    )
    integration = tmp_path / "custom_components" / "jackery_solarvault"
    integration.mkdir(parents=True)
    (integration / "__init__.py").write_text(source, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, "-m", "scripts.check_integration_imports"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode != 0
    assert error in result.stderr


def test_release_gate_rejects_removed_select_future_import(tmp_path: Path) -> None:
    """A 3.14 import must not hide removal of the eager-runtime safeguard."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "__init__.py").touch()
    (scripts / "check_integration_imports.py").write_bytes(
        (ROOT / "scripts" / "check_integration_imports.py").read_bytes()
    )
    integration = tmp_path / "custom_components" / "jackery_solarvault"
    shutil.copytree(
        INTEGRATION,
        integration,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    select = integration / "select.py"
    select.write_text(
        select.read_text(encoding="utf-8").replace(
            "from __future__ import annotations\n", ""
        ),
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, "-m", "scripts.check_integration_imports"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode != 0
    assert "must preserve its deferred forward reference" in result.stderr


def test_release_gate_checks_unimported_repository_scripts(tmp_path: Path) -> None:
    """A legacy handler in an unused repository script must block publication."""
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    (scripts / "__init__.py").touch()
    (scripts / "check_integration_imports.py").write_bytes(
        (ROOT / "scripts" / "check_integration_imports.py").read_bytes()
    )
    (scripts / "unused.py").write_text(
        "try:\n    pass\nexcept ValueError, TypeError:\n    pass\n",
        encoding="utf-8",
    )
    integration = tmp_path / "custom_components" / "jackery_solarvault"
    integration.mkdir(parents=True)
    (integration / "__init__.py").touch()
    result = subprocess.run(
        [sys.executable, "-m", "scripts.check_integration_imports"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode != 0
    assert "unused.py" in result.stderr
    assert "SyntaxError" in result.stderr
