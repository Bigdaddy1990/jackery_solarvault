"""Tests for coverage gate enforcement."""

from contextlib import contextmanager
from decimal import Decimal
from pathlib import Path
import shutil
from typing import TYPE_CHECKING
import uuid

from scripts.enforce_coverage_gates import (
    LEGACY_CRITICAL_MODULES,
    PERFECT_COVERAGE_GLOBS,
    _repo_modules,  # noqa: PLC2701
    enforce_coverage_gates,
)

if TYPE_CHECKING:
    from collections.abc import Iterator

_TEMP_ROOT = Path(".pytest-runtime")


@contextmanager
def _temporary_directory() -> Iterator[str]:
    """Return a workspace-local temporary directory for Windows test runs."""
    _TEMP_ROOT.mkdir(exist_ok=True)
    path = _TEMP_ROOT / f"coverage-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield str(path)
    finally:
        shutil.rmtree(path, ignore_errors=True)


def _write_xml(path: Path, *, branch_rate: str | None = "1") -> None:
    branch_attr = f' branch-rate="{branch_rate}"' if branch_rate is not None else ""
    modules = sorted(_repo_modules(PERFECT_COVERAGE_GLOBS) | LEGACY_CRITICAL_MODULES)
    classes = "\n".join(
        f'<class filename="{module}" line-rate="1" branch-rate="1" />'
        for module in modules
    )
    path.write_text(
        f'<coverage line-rate="1"{branch_attr}>'
        f"<packages><package><classes>{classes}</classes></package></packages>"
        "</coverage>",
        encoding="utf-8",
    )


def test_enforce_coverage_gates_accepts_perfect_line_and_branch() -> None:
    """A report with 100% line and branch coverage for target modules passes."""
    with _temporary_directory() as tmpdir:
        coverage_xml = Path(tmpdir) / "coverage.xml"
        _write_xml(coverage_xml)

        assert not enforce_coverage_gates(
            coverage_xml=coverage_xml,
            total_minimum=Decimal(85),
            legacy_module_minimum=Decimal(90),
            perfect_module_minimum=Decimal(100),
        )


def test_enforce_coverage_gates_requires_branch_coverage() -> None:
    """CI must reject reports not produced with branch coverage enabled."""
    with _temporary_directory() as tmpdir:
        coverage_xml = Path(tmpdir) / "coverage.xml"
        _write_xml(coverage_xml, branch_rate=None)

        failures = enforce_coverage_gates(
            coverage_xml=coverage_xml,
            total_minimum=Decimal(85),
            legacy_module_minimum=Decimal(90),
            perfect_module_minimum=Decimal(100),
        )

        assert (
            "coverage XML missing total branch-rate; run pytest with --cov-branch"
            in failures
        )
