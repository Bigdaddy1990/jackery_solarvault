"""Unit tests for ``scripts/gate.py``.

These tests exercise the pure helpers — gate selection, ``--only``
parsing, fix-mode resolution, and exit-code aggregation — without
shelling out to the real subprocess. The few cases that do invoke
``main`` use ``--only`` to keep the run small and rely on the existing
project scripts being on disk.
"""

import importlib.util
from pathlib import Path
import sys
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_gate_module() -> ModuleType:
    """Import ``scripts/gate.py`` as a module without a ``scripts`` package."""
    gate_path = _REPO_ROOT / "scripts" / "gate.py"
    spec = importlib.util.spec_from_file_location("scripts_gate", gate_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["scripts_gate"] = module
    spec.loader.exec_module(module)
    return module


gate = _load_gate_module()


def test_fast_profile_selects_syntactic_gates_only() -> None:
    """``--fast`` must keep pytest and the heavy enforcers out of the run."""
    selected = gate._select_gates("fast", only=None)
    names = {g.name for g in selected}
    assert {"compile", "ruff_format", "ruff_check"} <= names
    assert "pytest" not in names
    assert "manifest" not in names


def test_full_profile_includes_pytest() -> None:
    """``--full`` must add pytest on top of the default set."""
    selected = gate._select_gates("full", only=None)
    names = {g.name for g in selected}
    assert "pytest" in names
    assert "manifest" in names


def test_mypy_runs_in_default_and_full_but_not_fast() -> None:
    """Strict mypy is mandatory per pyproject.toml; ``--fast`` is the only escape."""
    fast = {g.name for g in gate._select_gates("fast", only=None)}
    default = {g.name for g in gate._select_gates("default", only=None)}
    full = {g.name for g in gate._select_gates("full", only=None)}
    assert "mypy" not in fast
    assert "mypy" in default
    assert "mypy" in full


def test_hassfest_passes_integration_path_argument() -> None:
    """``hassfest.py`` is mandatory and rejects bare invocation (issue surfaced 2026-05-28)."""
    hassfest = next(g for g in gate.GATES if g.name == "manifest")
    assert "--integration-path" in hassfest.cmd
    assert "custom_components/jackery_solarvault" in hassfest.cmd


def test_vendor_pyyaml_is_not_in_default_profile() -> None:
    """Reject ``check_vendor_pyyaml`` from default/full profiles.

    The script audits HA Core's ``annotatedyaml/_vendor`` tree, which is not
    present in this integration repo; including it as a default gate would
    FAIL every local run for the wrong reason. Reachable only via
    ``--only vendor_pyyaml``.
    """
    default = {g.name for g in gate._select_gates("default", only=None)}
    full = {g.name for g in gate._select_gates("full", only=None)}
    assert "vendor_pyyaml" not in default
    assert "vendor_pyyaml" not in full
    only = {
        g.name for g in gate._select_gates("default", only=frozenset({"vendor_pyyaml"}))
    }
    assert only == {"vendor_pyyaml"}


def test_localization_flags_runs_in_default() -> None:
    """``sync_localization_flags.py`` must be part of the default gate set."""
    default = {g.name for g in gate._select_gates("default", only=None)}
    assert "localization_flags" in default


def test_legacy_exceptions_is_out_of_default_profile() -> None:
    """Reject pre-PEP-758 legacy guard from default/full.

    ``check_legacy_exception_syntax.py`` flags ``except A, B:`` as Python 2
    legacy, but PEP 758 made that form valid in Python 3.14 and the
    ``py314_exceptions`` gate actively rewrites parenthesized headers to it.
    Running both gates as default makes convergence impossible.
    """
    default = {g.name for g in gate._select_gates("default", only=None)}
    full = {g.name for g in gate._select_gates("full", only=None)}
    assert "legacy_exceptions" not in default
    assert "legacy_exceptions" not in full
    py314 = {
        g.name
        for g in gate._select_gates("default", only=frozenset({"py314_exceptions"}))
    }
    assert py314 == {"py314_exceptions"}


def test_only_filter_overrides_profile() -> None:
    """``--only`` must select exactly the requested gates regardless of profile."""
    selected = gate._select_gates("fast", only=frozenset({"manifest", "pytest"}))
    assert {g.name for g in selected} == {"manifest", "pytest"}


def test_parse_only_rejects_unknown_gate() -> None:
    """``--only`` with an unknown gate must SystemExit so CI fails loudly."""
    with pytest.raises(SystemExit):
        gate._parse_only("compile,nonexistent_gate")


def test_resolve_cmd_returns_fix_cmd_when_fix_true() -> None:
    """Fix-mode must use the fix variant when one is defined."""
    fixable = next(g for g in gate.GATES if g.fix_cmd is not None)
    assert gate._resolve_cmd(fixable, fix=True) == fixable.fix_cmd
    assert gate._resolve_cmd(fixable, fix=False) == fixable.cmd


def test_resolve_cmd_falls_back_when_no_fix_cmd() -> None:
    """Fix-mode must keep the read-only command when no fix variant exists."""
    plain = next(g for g in gate.GATES if g.fix_cmd is None)
    assert gate._resolve_cmd(plain, fix=True) == plain.cmd


def test_tool_available_detects_missing_script() -> None:
    """Missing project scripts must be reported as unavailable, not crash."""
    missing = (gate.PY, "scripts/does_not_exist.py")
    assert gate._tool_available(missing) is False


def test_tool_available_detects_present_script() -> None:
    """An existing project script must register as available."""
    present = (gate.PY, "scripts/check_compile.py")
    assert gate._tool_available(present) is True


def test_exit_code_zero_when_all_pass() -> None:
    """Aggregation returns 0 only when every result passed."""
    results = [gate.GateResult("a", "PASS", 0.0), gate.GateResult("b", "SKIP", 0.0)]
    assert gate._exit_code(results) == 0


def test_exit_code_one_when_any_fail() -> None:
    """Any FAIL in the result list must bubble up to a non-zero exit code."""
    results = [gate.GateResult("a", "PASS", 0.0), gate.GateResult("b", "FAIL", 0.0)]
    assert gate._exit_code(results) == 1


def test_main_rejects_fix_and_check_together() -> None:
    """``--fix`` plus ``--check`` is an invalid combination (exit 2)."""
    assert gate.main(["--fix", "--check"]) == 2


def test_subprocess_env_disables_bytecode_writes() -> None:
    """Gate subprocesses must inherit ``PYTHONDONTWRITEBYTECODE=1``.

    The repo policy is documented by ``scripts/check_compile.py`` and
    ``scripts/run_ha_tests.py``; if a gate child process forgets this
    flag, it litters ``__pycache__`` directories across the repo.
    """
    env = gate._subprocess_env()
    assert env["PYTHONDONTWRITEBYTECODE"] == "1"
