#!/usr/bin/env python3
"""Unified quality gate for the jackery_solarvault Home-Assistant integration.

This script is the single entry point for running every check that the project
considers blocking. It deliberately orchestrates the existing tooling instead of
re-implementing it, so behavior stays consistent between local runs, pre-commit
and CI.

Usage
-----
    python scripts/gate.py                 # full check (read-only)
    python scripts/gate.py --fast          # skip slow steps (no full pytest)
    python scripts/gate.py --fix           # run autofixers, then re-check
    python scripts/gate.py --only ruff,mypy
    python scripts/gate.py --skip pytest
    python scripts/gate.py --list          # print the step list and exit

Modes
-----
    --check (default)  read-only verification; non-zero exit means "blocking"
    --fix              apply autofixers (ruff --fix, ruff format, pre-commit
                       fixers); after fixing, re-runs the same checks
    --fast             omit full pytest + coverage; useful for inner-loop
    --full             include all optional steps (slow / scheduled-only)

Exit code
---------
    0 = all selected steps passed
    N = number of failed steps

Conventions
-----------
* Every step is a `GateStep`. New scripts under `scripts/check_*.py`,
  `scripts/enforce_*.py` or `scripts/hass_enforce_*.py` are auto-discovered.
* Pre-commit ist *die* Quelle für Lint/Format/Type/Yaml/Json/Typos-Hooks. Der
  Gate ruft `pre-commit run --all-files` und ergänzt nur HA-spezifische
  Prüfungen, die pre-commit nicht abdeckt (pytest, Manifest, Übersetzungen,
  HA-Quality-Scale, Custom-Enforcer-Skripte).

The gate is intentionally cross-platform (Linux / macOS / Windows) and uses
only the Python stdlib. External tools are invoked via `subprocess`.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import os
import shutil
import subprocess
import sys
import time
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Final

ROOT: Final[Path] = Path(__file__).resolve().parent.parent
INTEGRATION_DIR: Final[Path] = ROOT / "custom_components" / "jackery_solarvault"
SCRIPTS_DIR: Final[Path] = ROOT / "scripts"

# ANSI colors are supported on Windows 10+, macOS, Linux. Fall back to plain.
_USE_COLOR: Final[bool] = sys.stdout.isatty() and os.environ.get("NO_COLOR") is None


def _c(code: str, text: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text


def _green(text: str) -> str:
    return _c("32", text)


def _red(text: str) -> str:
    return _c("31", text)


def _yellow(text: str) -> str:
    return _c("33", text)


def _bold(text: str) -> str:
    return _c("1", text)


def _gray(text: str) -> str:
    return _c("90", text)


@dataclasses.dataclass(slots=True)
class StepResult:
    """Result of running a single gate step."""

    name: str
    passed: bool
    skipped: bool
    duration_s: float
    note: str = ""

    @property
    def status(self) -> str:
        if self.skipped:
            return _yellow("SKIP")
        return _green("PASS") if self.passed else _red("FAIL")


@dataclasses.dataclass(slots=True)
class GateStep:
    """A single gate step.

    Either ``check_cmd`` or ``check_fn`` must be supplied. If both are given,
    ``check_fn`` wins. The same applies to ``fix_cmd`` / ``fix_fn``.

    ``fast_skip`` marks steps that are too slow for inner-loop runs.
    ``full_only`` marks steps that only run with ``--full`` (e.g. CI nightly).
    """

    name: str
    description: str
    check_cmd: Sequence[str] | None = None
    check_fn: Callable[[], tuple[bool, str]] | None = None
    fix_cmd: Sequence[str] | None = None
    fix_fn: Callable[[], tuple[bool, str]] | None = None
    fast_skip: bool = False
    full_only: bool = False
    optional: bool = False  # if missing tool: SKIP instead of FAIL


# ---------------------------------------------------------------------------
# Custom check functions
# ---------------------------------------------------------------------------


def _check_manifest_required_fields() -> tuple[bool, str]:
    """Validate that manifest.json carries every HA-required key."""
    manifest_path = INTEGRATION_DIR / "manifest.json"
    required = {
        "domain",
        "name",
        "version",
        "documentation",
        "requirements",
        "codeowners",
        "iot_class",
        "integration_type",
    }
    try:
        data = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False, f"missing file: {manifest_path}"
    except json.JSONDecodeError as exc:
        return False, f"invalid JSON in {manifest_path}: {exc}"
    missing = sorted(required - data.keys())
    if missing:
        return False, f"manifest.json missing required keys: {missing}"
    # Cross-check with hacs.json
    hacs_path = ROOT / "hacs.json"
    if hacs_path.exists():
        try:
            hacs = json.loads(hacs_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return False, f"invalid JSON in hacs.json: {exc}"
        if "homeassistant" in hacs and "homeassistant" in data:
            if hacs["homeassistant"] != data["homeassistant"]:
                return False, (
                    "HA minimum version mismatch: "
                    f"manifest={data['homeassistant']!r} vs "
                    f"hacs={hacs['homeassistant']!r}"
                )
    return True, "manifest.json OK"


def _check_translations_coverage() -> tuple[bool, str]:
    """Every key in strings.json must appear in every translations/*.json."""
    strings = INTEGRATION_DIR / "strings.json"
    translations_dir = INTEGRATION_DIR / "translations"
    if not strings.exists():
        return False, f"missing {strings}"
    if not translations_dir.exists():
        return False, f"missing {translations_dir}"
    try:
        base = json.loads(strings.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return False, f"invalid JSON in strings.json: {exc}"

    def _keys(obj: object, prefix: str = "") -> set[str]:
        keys: set[str] = set()
        if isinstance(obj, dict):
            for k, v in obj.items():
                p = f"{prefix}.{k}" if prefix else k
                keys.add(p)
                keys |= _keys(v, p)
        return keys

    base_keys = _keys(base)
    missing_total: dict[str, list[str]] = {}
    for trans in sorted(translations_dir.glob("*.json")):
        try:
            translated = json.loads(trans.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            return False, f"invalid JSON in {trans.name}: {exc}"
        translated_keys = _keys(translated)
        missing = sorted(base_keys - translated_keys)
        if missing:
            missing_total[trans.name] = missing
    if missing_total:
        summary = ", ".join(
            f"{name}: {len(keys)} keys" for name, keys in missing_total.items()
        )
        return False, f"translations missing keys → {summary}"
    return True, f"translations cover {len(base_keys)} keys"


_PIN_SPLIT_CHARS: Final[str] = "<>=!~ "


def _normalize_pkg(spec: str) -> str:
    """Strip version specifier / extras from a pip requirement string."""
    spec = spec.strip()
    if not spec or spec.startswith("#"):
        return ""
    # Drop URL-style markers
    if "@" in spec:
        spec = spec.split("@", 1)[0]
    # Drop extras like ``coverage[toml]`` -> ``coverage``
    if "[" in spec:
        spec = spec.split("[", 1)[0]
    # Find first version-pin char and slice
    for i, ch in enumerate(spec):
        if ch in _PIN_SPLIT_CHARS:
            spec = spec[:i]
            break
    return spec.strip().lower().replace("_", "-")


def _check_requirements_mirror() -> tuple[bool, str]:
    """Check that root requirements.txt covers every manifest dep (by base name)."""
    manifest = INTEGRATION_DIR / "manifest.json"
    req = ROOT / "requirements.txt"
    if not req.exists():
        return True, "no root requirements.txt (manifest is sole source)"
    try:
        manifest_specs = json.loads(manifest.read_text(encoding="utf-8")).get(
            "requirements", []
        )
    except json.JSONDecodeError as exc:
        return False, f"invalid manifest.json: {exc}"
    text = req.read_text(encoding="utf-8")
    manifest_names = {_normalize_pkg(s) for s in manifest_specs} - {""}
    root_names = {_normalize_pkg(line) for line in text.splitlines()} - {""}
    missing = sorted(manifest_names - root_names)
    if missing:
        return False, f"requirements.txt is missing manifest deps: {missing}"
    return True, f"requirements.txt covers {len(manifest_names)} manifest deps"


def _check_workflow_consistency() -> tuple[bool, str]:
    """All workflow files should share the same extension (.yml or .yaml)."""
    wf_dir = ROOT / ".github" / "workflows"
    if not wf_dir.exists():
        return True, "no workflows directory"
    yml = sorted(p.name for p in wf_dir.glob("*.yml"))
    yaml = sorted(p.name for p in wf_dir.glob("*.yaml"))
    if yml and yaml:
        return False, (
            f"workflow extension drift: .yml={len(yml)}, .yaml={len(yaml)} "
            f"(yaml={yaml})"
        )
    return True, f"{len(yml or yaml)} workflows, consistent extension"


def _discover_enforcers() -> list[GateStep]:
    """Auto-pick up scripts/check_*.py, scripts/enforce_*.py, scripts/hass_enforce_*.py."""
    found: list[GateStep] = []
    patterns = ("check_*.py", "enforce_*.py", "hass_enforce_*.py")
    skip = {"gate.py"}
    for pattern in patterns:
        for script in sorted(SCRIPTS_DIR.glob(pattern)):
            if script.name in skip:
                continue
            found.append(
                GateStep(
                    name=script.stem,
                    description=f"custom enforcer: scripts/{script.name}",
                    check_cmd=[sys.executable, str(script)],
                    fast_skip=False,
                    optional=True,
                )
            )
    return found


# ---------------------------------------------------------------------------
# Step registry
# ---------------------------------------------------------------------------


def _core_steps() -> list[GateStep]:
    return [
        GateStep(
            name="precommit",
            description="pre-commit hooks (lint, format, mypy, yaml/json, typos)",
            check_cmd=["pre-commit", "run", "--all-files", "--show-diff-on-failure"],
            fix_cmd=["pre-commit", "run", "--all-files"],
            optional=True,
        ),
        GateStep(
            name="ruff-lint",
            description="ruff lint (extra direct invocation)",
            check_cmd=["ruff", "check", "."],
            fix_cmd=["ruff", "check", "--fix", "--unsafe-fixes", "."],
        ),
        GateStep(
            name="ruff-format",
            description="ruff formatter (check mode)",
            check_cmd=["ruff", "format", "--check", "."],
            fix_cmd=["ruff", "format", "."],
        ),
        GateStep(
            name="mypy",
            description="mypy strict (custom_components + tests)",
            check_cmd=[
                "mypy",
                str(INTEGRATION_DIR),
                str(ROOT / "tests"),
            ],
        ),
        GateStep(
            name="typos",
            description="typo check via crate-ci typos",
            check_cmd=["typos"],
            optional=True,
        ),
        GateStep(
            name="manifest",
            description="HA manifest.json required fields + hacs.json cross-check",
            check_fn=_check_manifest_required_fields,
        ),
        GateStep(
            name="translations",
            description="strings.json key coverage across translations/",
            check_fn=_check_translations_coverage,
        ),
        GateStep(
            name="requirements",
            description="root requirements.txt mirrors manifest deps",
            check_fn=_check_requirements_mirror,
        ),
        GateStep(
            name="workflows",
            description="workflow extension consistency (.yml vs .yaml)",
            check_fn=_check_workflow_consistency,
        ),
        GateStep(
            name="pytest",
            description="pytest with coverage gate (fail_under=85)",
            check_cmd=[
                "pytest",
                "-q",
                "--no-header",
                str(ROOT / "tests"),
            ],
            fast_skip=True,
        ),
        GateStep(
            name="pytest-ha",
            description="HA-specific pytest profile (tests/ha)",
            check_cmd=[
                "pytest",
                "-q",
                "-c",
                str(ROOT / "pytest-ha.ini"),
            ],
            fast_skip=True,
            full_only=True,
        ),
    ]


def _all_steps() -> list[GateStep]:
    return _core_steps() + _discover_enforcers()


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


def _have_tool(tool: str) -> bool:
    """True if ``tool`` is on PATH or matches an importable Python module."""
    if shutil.which(tool):
        return True
    # If invoked as `python -m foo`, check importability.
    if tool == sys.executable:
        return True
    return False


def _run_cmd(cmd: Sequence[str]) -> tuple[bool, str]:
    """Run a subprocess, return (ok, short note)."""
    try:
        completed = subprocess.run(  # noqa: S603 - intentional dispatch
            list(cmd),
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
    except FileNotFoundError as exc:
        return False, f"tool not found: {exc.filename}"
    if completed.returncode == 0:
        return True, "ok"
    # Trim noisy output to a short tail.
    out = (completed.stdout or "") + (completed.stderr or "")
    lines = [line for line in out.splitlines() if line.strip()]
    tail = " | ".join(lines[-3:])[:240]
    return False, f"exit={completed.returncode}: {tail}" if tail else f"exit={completed.returncode}"


def _execute(step: GateStep, mode_fix: bool) -> StepResult:
    start = time.monotonic()

    def _wrap(passed: bool, note: str) -> StepResult:
        return StepResult(
            name=step.name,
            passed=passed,
            skipped=False,
            duration_s=time.monotonic() - start,
            note=note,
        )

    if mode_fix:
        if step.fix_fn is not None:
            passed, note = step.fix_fn()
            return _wrap(passed, note)
        if step.fix_cmd is not None:
            if not _have_tool(step.fix_cmd[0]):
                if step.optional:
                    return StepResult(
                        step.name, True, True, time.monotonic() - start,
                        f"tool not installed: {step.fix_cmd[0]}",
                    )
                return _wrap(False, f"tool not installed: {step.fix_cmd[0]}")
            passed, note = _run_cmd(step.fix_cmd)
            return _wrap(passed, note)
        # No fix command; fall through to check command.

    if step.check_fn is not None:
        passed, note = step.check_fn()
        return _wrap(passed, note)
    if step.check_cmd is not None:
        if not _have_tool(step.check_cmd[0]):
            if step.optional:
                return StepResult(
                    step.name, True, True, time.monotonic() - start,
                    f"tool not installed: {step.check_cmd[0]}",
                )
            return _wrap(False, f"tool not installed: {step.check_cmd[0]}")
        return _wrap(*_run_cmd(step.check_cmd))
    return _wrap(False, "step has no check_cmd or check_fn")


def _print_header(label: str) -> None:
    # ASCII-only divider for Windows cp1252 consoles.
    print(f"\n{_bold('=' * 78)}")
    print(_bold(f"  {label}"))
    print(_bold("=" * 78))


def _print_step_summary(result: StepResult, description: str) -> None:
    dur = f"{result.duration_s:5.2f}s"
    line = f"  {result.status}  {result.name:<22} {_gray(dur)}  {description}"
    print(line)
    if result.note and (not result.passed or result.skipped):
        print(f"        {_gray(result.note)}")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Unified quality gate for jackery_solarvault."
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        help="apply autofixers, then re-check.",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="skip slow steps (pytest, full coverage).",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="include scheduled-only / nightly steps.",
    )
    parser.add_argument(
        "--only",
        type=str,
        default="",
        help="comma-separated step names to run exclusively.",
    )
    parser.add_argument(
        "--skip",
        type=str,
        default="",
        help="comma-separated step names to omit.",
    )
    parser.add_argument(
        "--list",
        action="store_true",
        help="print the step list and exit.",
    )
    return parser.parse_args(argv)


def _filter_steps(
    steps: list[GateStep],
    *,
    only: set[str],
    skip: set[str],
    fast: bool,
    full: bool,
) -> list[GateStep]:
    out: list[GateStep] = []
    for s in steps:
        if only and s.name not in only:
            continue
        if s.name in skip:
            continue
        if fast and s.fast_skip:
            continue
        if s.full_only and not full:
            continue
        out.append(s)
    return out


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    steps = _all_steps()
    only = {s.strip() for s in args.only.split(",") if s.strip()}
    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    selected = _filter_steps(
        steps, only=only, skip=skip, fast=args.fast, full=args.full
    )

    if args.list:
        _print_header("Available gate steps")
        for s in steps:
            tags = []
            if s.fast_skip:
                tags.append("slow")
            if s.full_only:
                tags.append("full-only")
            if s.optional:
                tags.append("optional")
            tag_str = f" [{','.join(tags)}]" if tags else ""
            print(f"  {s.name:<24}{tag_str}  {s.description}")
        return 0

    mode = "FIX → CHECK" if args.fix else "CHECK"
    title = f"jackery_solarvault gate · mode={mode} · {len(selected)} step(s)"
    _print_header(title)

    if args.fix:
        # Run fixers first; collect noise but don't aggregate as failures.
        _print_header("Phase 1 · apply autofixers")
        for step in selected:
            if step.fix_cmd is None and step.fix_fn is None:
                continue
            result = _execute(step, mode_fix=True)
            _print_step_summary(result, step.description)
        _print_header("Phase 2 · verify (read-only)")

    failures: list[StepResult] = []
    skipped: list[StepResult] = []
    for step in selected:
        result = _execute(step, mode_fix=False)
        _print_step_summary(result, step.description)
        if result.skipped:
            skipped.append(result)
        elif not result.passed:
            failures.append(result)

    _print_header("Summary")
    total = len(selected)
    passed = total - len(failures) - len(skipped)
    print(
        f"  {_green(str(passed))} passed · "
        f"{_red(str(len(failures)))} failed · "
        f"{_yellow(str(len(skipped)))} skipped · "
        f"{total} total"
    )
    if failures:
        print()
        print(_red("Failing steps:"))
        for f in failures:
            print(f"  - {f.name}: {f.note}")
    return len(failures)


if __name__ == "__main__":
    raise SystemExit(main())
