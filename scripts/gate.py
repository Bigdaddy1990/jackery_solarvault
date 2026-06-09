"""Unified quality gate for the jackery_solarvault integration.

This single entry point orchestrates the repository's verification
scripts so they run the same way locally and in CI. Each gate runs in
a subprocess so failures stay isolated and individual gate output is
captured for the final report.

The CLI mirrors the contract documented in ``.github/workflows/gate.yml``
and ``.pre-commit-config.yaml``:

* ``--fast``   run the syntactic checks only
* ``--full``   run the default set plus pytest
* ``--fix``    run autofixers where available
* ``--check``  forbid autofixers (mutually exclusive with ``--fix``)
* ``--only X,Y,Z``  run only the listed gate names

Exit code is 0 when every selected gate passes, 1 if any required gate
fails, and 2 for invalid invocations.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
PY: Final = sys.executable

# Tag semantics used by ``--fast``/``--full``/``--fix``:
#   "fast"    purely syntactic checks, no network, no HA core.
#   "default" everything in fast plus the project-wide enforcers.
#   "full"    everything in default plus pytest.
#   "fixable" the gate has a fix variant that ``--fix`` can opt in to.
_FAST: Final = frozenset({"fast", "default", "full"})
_DEFAULT: Final = frozenset({"default", "full"})
_FULL: Final = frozenset({"full"})


@dataclass(frozen=True, slots=True)
class Gate:
    """Definition of a single quality gate."""

    name: str
    cmd: tuple[str, ...]
    tags: frozenset[str]
    fix_cmd: tuple[str, ...] | None = None
    optional: bool = False


@dataclass(slots=True)
class GateResult:
    """Outcome of running a single gate."""

    name: str
    status: str  # "PASS", "FAIL", "SKIP"
    duration_s: float
    detail: str = ""


GATES: Final[tuple[Gate, ...]] = (
    Gate(
        name="compile",
        cmd=(PY, "scripts/check_compile.py"),
        tags=_FAST,
    ),
    Gate(
        name="ruff_format",
        cmd=(PY, "-m", "ruff", "format", "--check", "."),
        fix_cmd=(PY, "-m", "ruff", "format", "."),
        tags=_FAST | {"fixable"},
    ),
    Gate(
        name="ruff_check",
        cmd=(PY, "-m", "ruff", "check", "."),
        fix_cmd=(PY, "-m", "ruff", "check", "--fix", "."),
        tags=_FAST | {"fixable"},
    ),
    Gate(
        name="mypy",
        cmd=(PY, "scripts/run_mypy_no_cache.py"),
        tags=_DEFAULT,
    ),
    Gate(
        name="manifest",
        cmd=(
            PY,
            "scripts/hassfest.py",
            "--integration-path",
            "custom_components/jackery_solarvault",
        ),
        tags=_DEFAULT,
    ),
    Gate(
        name="translations",
        cmd=(PY, "scripts/sync_translations.py", "--check"),
        tags=_DEFAULT,
    ),
    Gate(
        name="localization_flags",
        cmd=(PY, "scripts/sync_localization_flags.py"),
        tags=_DEFAULT,
    ),
    Gate(
        name="workflows",
        cmd=(PY, "scripts/check_workflows.py"),
        tags=_DEFAULT,
    ),
    Gate(
        name="requirements",
        cmd=(PY, "scripts/sync_requirements.py", "--check"),
        tags=_DEFAULT,
    ),
    Gate(
        name="docs",
        cmd=(PY, "scripts/check_docs_root.py"),
        tags=_DEFAULT,
    ),
    Gate(
        name="typed_dicts",
        cmd=(PY, "scripts/check_typed_dicts.py"),
        tags=_DEFAULT,
    ),
    # ``check_vendor_pyyaml.py`` audits the PyYAML build vendored under
    # ``annotatedyaml/_vendor/yaml``; that vendor tree ships with Home
    # Assistant Core, not with this integration, so the gate is dropped
    # from the default profile and only available via ``--only``.
    Gate(
        name="vendor_pyyaml",
        cmd=(PY, "scripts/check_vendor_pyyaml.py"),
        tags=frozenset({"vendor_audit"}),
    ),
    # ``check_legacy_exception_syntax.py`` was written before PEP 758 (Python
    # 3.14) and treats unparenthesized multi-exception headers as Python 2
    # legacy syntax. PEP 758 explicitly made ``except A, B:`` valid in 3.14 and
    # ``verify_py314_exception_style.py`` (the ``py314_exceptions`` gate)
    # actively rewrites parenthesized headers TO that form. Running both as
    # default gates makes convergence impossible. The legacy guard is reachable
    # via ``--only`` for callers that need it on a non-3.14 branch.
    Gate(
        name="legacy_exceptions",
        cmd=(PY, "scripts/check_legacy_exception_syntax.py"),
        tags=frozenset({"pre_pep758"}),
    ),
    Gate(
        name="py314_exceptions",
        cmd=(PY, "scripts/verify_py314_exception_style.py"),
        tags=_DEFAULT,
    ),
    Gate(
        name="push_guard",
        cmd=(PY, "scripts/homeassistant_push_guard.py", "--check"),
        tags=_DEFAULT,
    ),
    Gate(
        name="pre_commit",
        cmd=("pre-commit", "run", "--all-files"),
        fix_cmd=("pre-commit", "run", "--all-files"),
        tags=_DEFAULT | {"fixable"},
        optional=True,
    ),
    Gate(
        name="pytest",
        cmd=(PY, "-m", "pytest"),
        tags=_FULL,
    ),
)

_KNOWN_NAMES: Final = frozenset(g.name for g in GATES)


def _select_gates(profile: str, only: frozenset[str] | None) -> list[Gate]:
    """Return the gates that match the current profile or ``--only`` filter."""
    if only is not None:
        return [g for g in GATES if g.name in only]
    return [g for g in GATES if profile in g.tags]


def _resolve_cmd(gate: Gate, fix: bool) -> tuple[str, ...]:
    """Return the command to execute for ``gate`` honouring ``--fix``."""
    if fix and gate.fix_cmd is not None:
        return gate.fix_cmd
    return gate.cmd


def _tool_available(cmd: tuple[str, ...]) -> bool:
    """Return True when the executable or script referenced by ``cmd`` exists."""
    head = cmd[0]
    if head == PY:
        # ``python script.py`` or ``python -m module``. For file form, verify
        # the script exists under the repo root so a missing file FAILS loudly
        # instead of producing a confusing Python traceback.
        if len(cmd) >= 2 and cmd[1].endswith(".py"):
            return (ROOT / cmd[1]).is_file()
        return True
    return shutil.which(head) is not None


def _subprocess_env() -> dict[str, str]:
    """Return the environment for gate subprocesses.

    ``PYTHONDONTWRITEBYTECODE=1`` matches ``scripts/check_compile.py`` and
    ``scripts/run_ha_tests.py`` — the project deliberately runs Python
    sources without leaving ``__pycache__`` directories behind.
    """
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def _run_gate(gate: Gate, *, fix: bool) -> GateResult:
    """Execute ``gate`` as a subprocess and capture its outcome."""
    cmd = _resolve_cmd(gate, fix)
    if not _tool_available(cmd):
        if gate.optional:
            return GateResult(gate.name, "SKIP", 0.0, detail=f"{cmd[0]} not on PATH")
        return GateResult(gate.name, "FAIL", 0.0, detail=f"missing tool: {cmd[0]}")
    start = time.perf_counter()
    try:
        proc = subprocess.run(
          cmd,
          cwd=ROOT,
          capture_output=True,
          text=True,
          check=False,
          env=_subprocess_env(),
          timeout=600,
      )
      duration = time.perf_counter() - start
      if proc.returncode == 0:
          return GateResult(gate.name, "PASS", duration)
      output = (proc.stdout + proc.stderr).strip().splitlines()
      detail = "\n".join(output[-20:]) if output else f"exit {proc.returncode}"
      return GateResult(gate.name, "FAIL", duration, detail=detail)
  except subprocess.TimeoutExpired:
      duration = time.perf_counter() - start
      return GateResult(gate.name, "FAIL", duration, detail=f"timeout after {duration:.1f}s")


def _print_results(results: Sequence[GateResult]) -> None:
    """Print a one-line summary per gate plus failing-gate detail blocks."""
    if not results:
        return
    width = max(len(r.name) for r in results)
    for result in results:
        print(
            f"[{result.status}] {result.name.ljust(width)}  {result.duration_s:6.2f}s"
        )
        if result.status == "FAIL" and result.detail:
            for line in result.detail.splitlines():
                print(f"        {line}")
    counts = {status: 0 for status in ("PASS", "FAIL", "SKIP")}
    for result in results:
        counts[result.status] = counts.get(result.status, 0) + 1
    print(
        f"gate: total={len(results)} "
        f"pass={counts['PASS']} fail={counts['FAIL']} skip={counts['SKIP']}"
    )


def _exit_code(results: Sequence[GateResult]) -> int:
    """Return 1 if any gate failed, otherwise 0."""
    return 1 if any(r.status == "FAIL" for r in results) else 0


def _parse_only(raw: str | None) -> frozenset[str] | None:
    """Parse the ``--only`` argument and validate against known gate names."""
    if raw is None:
        return None
    names = frozenset(part.strip() for part in raw.split(",") if part.strip())
    unknown = names - _KNOWN_NAMES
    if unknown:
        joined = ", ".join(sorted(unknown))
        raise SystemExit(f"gate: unknown gate(s) in --only: {joined}")
    return names


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    """Return the parsed CLI namespace."""
    parser = argparse.ArgumentParser(
        prog="gate",
        description="Run the jackery_solarvault quality gates.",
    )
    profile = parser.add_mutually_exclusive_group()
    profile.add_argument("--fast", action="store_true", help="syntactic checks only")
    profile.add_argument("--full", action="store_true", help="include pytest")
    parser.add_argument(
        "--fix", action="store_true", help="run autofixers where available"
    )
    parser.add_argument(
        "--check", action="store_true", help="forbid autofixers (read-only)"
    )
    parser.add_argument(
        "--only",
        default=None,
        help="comma-separated subset of gate names to run",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point used by ``python scripts/gate.py``."""
    args = _parse_args(argv)
    if args.fix and args.check:
        print("gate: --fix and --check are mutually exclusive", file=sys.stderr)
        return 2
    profile = "fast" if args.fast else "full" if args.full else "default"
    only = _parse_only(args.only)
    selected = _select_gates(profile, only)
    if not selected:
        print("gate: no matching gates", file=sys.stderr)
        return 2
    print(
        f"gate: profile={profile} gates={len(selected)} "
        f"fix={args.fix} check={args.check}"
    )
    results = [_run_gate(g, fix=args.fix) for g in selected]
    _print_results(results)
    return _exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
