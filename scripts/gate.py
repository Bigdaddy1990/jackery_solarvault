#!/usr/bin/env python3
"""Repository quality gate runner."""

import argparse
from dataclasses import dataclass
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

PY = sys.executable
ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True, slots=True)
class Gate:
    """A single quality gate command."""

    name: str
    cmd: tuple[str, ...]
    profiles: frozenset[str]
    fix_cmd: tuple[str, ...] | None = None


@dataclass(frozen=True, slots=True)
class GateResult:
    """A gate execution result."""

    name: str
    status: str
    seconds: float


GATES: tuple[Gate, ...] = (
    Gate(
        "compile",
        (PY, "scripts/check_compile.py"),
        frozenset({"fast", "default", "full"}),
    ),
    Gate(
        "ruff_format",
        ("ruff", "format", "--check", "."),
        frozenset({"fast", "default", "full"}),
        ("ruff", "format", "."),
    ),
    Gate(
        "ruff_check",
        ("ruff", "check", "."),
        frozenset({"fast", "default", "full"}),
        ("ruff", "check", "--fix", "."),
    ),
    Gate(
        "mypy",
        ("mypy", "custom_components/jackery_solarvault"),
        frozenset({"default", "full"}),
    ),
    Gate(
        "manifest",
        (
            "python",
            "-m",
            "hassfest",
            "--integration-path",
            "custom_components/jackery_solarvault",
        ),
        frozenset({"default", "full"}),
    ),
    Gate(
        "localization_flags",
        (PY, "scripts/sync_localization_flags.py", "--check"),
        frozenset({"default", "full"}),
    ),
    Gate("pytest", (PY, "-m", "pytest"), frozenset({"full"})),
    Gate("vendor_pyyaml", (PY, "scripts/check_vendor_pyyaml.py"), frozenset()),
    Gate("py314_exceptions", (PY, "scripts/py314_exceptions.py"), frozenset()),
    Gate(
        "legacy_exceptions",
        (PY, "scripts/check_legacy_exception_syntax.py"),
        frozenset(),
    ),
)


def _parse_only(value: str | None) -> frozenset[str] | None:
    """Parse a comma-separated gate allowlist."""
    if value is None:
        return None
    requested = frozenset(part.strip() for part in value.split(",") if part.strip())
    known = {gate.name for gate in GATES}
    unknown = requested - known
    if unknown:
        raise SystemExit(2)
    return requested


def _select_gates(profile: str, only: frozenset[str] | None) -> tuple[Gate, ...]:
    """Select gates by profile, optionally overridden by ``--only``."""
    if only is not None:
        return tuple(gate for gate in GATES if gate.name in only)
    return tuple(gate for gate in GATES if profile in gate.profiles)


def _resolve_cmd(gate: Gate, *, fix: bool) -> tuple[str, ...]:
    """Return the command to run for a gate."""
    if fix and gate.fix_cmd is not None:
        return gate.fix_cmd
    return gate.cmd


def _tool_available(cmd: tuple[str, ...]) -> bool:
    """Return whether a command executable or project script exists."""
    if not cmd:
        return False
    executable = cmd[0]
    if executable in {PY, sys.executable, "python"} and len(cmd) > 1:
        script = ROOT / cmd[1]
        if cmd[1].endswith(".py"):
            return script.is_file()
    return shutil.which(executable) is not None


def _subprocess_env() -> dict[str, str]:
    """Build a stable child-process environment."""
    return {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}


def _run_gate(gate: Gate, *, fix: bool) -> GateResult:
    """Run a single gate."""
    cmd = _resolve_cmd(gate, fix=fix)
    started = time.monotonic()
    if not _tool_available(cmd):
        return GateResult(gate.name, "SKIP", 0.0)
    completed = subprocess.run(cmd, cwd=ROOT, env=_subprocess_env(), check=False)
    status = "PASS" if completed.returncode == 0 else "FAIL"
    return GateResult(gate.name, status, time.monotonic() - started)


def _exit_code(results: list[GateResult]) -> int:
    """Return a process exit code for gate results."""
    return 1 if any(result.status == "FAIL" for result in results) else 0


def main(argv: list[str] | None = None) -> int:
    """Run selected quality gates."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--profile", choices=("fast", "default", "full"), default="default"
    )
    parser.add_argument("--fast", action="store_true")
    parser.add_argument("--full", action="store_true")
    parser.add_argument("--only")
    parser.add_argument("--fix", action="store_true")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    if args.fix and args.check:
        return 2
    profile = "fast" if args.fast else "full" if args.full else args.profile
    gates = _select_gates(profile, _parse_only(args.only))
    results = [_run_gate(gate, fix=args.fix and not args.check) for gate in gates]
    for _result in results:
        pass
    return _exit_code(results)


if __name__ == "__main__":
    raise SystemExit(main())
