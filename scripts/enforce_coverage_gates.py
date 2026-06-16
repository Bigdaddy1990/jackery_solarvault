#!/usr/bin/env python3
"""Enforce coverage gates from a coverage.py XML report."""


import argparse
import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

SOURCE_ROOT = Path("custom_components/jackery_solarvault")

# Migration policy: refactored/new packages must stay at 100% line + branch coverage.
PERFECT_COVERAGE_GLOBS = (
    "handlers/*.py",
    "setters/*.py",
    "stats/*.py",
    "client/_endpoints/*.py",
)

# Legacy monolith areas are raised incrementally while migration continues.
LEGACY_CRITICAL_MODULES = frozenset(
    {
        "custom_components/jackery_solarvault/__init__.py",
        "custom_components/jackery_solarvault/coordinator.py",
        "custom_components/jackery_solarvault/sensor.py",
        "custom_components/jackery_solarvault/util.py",
        "custom_components/jackery_solarvault/client/api.py",
        "custom_components/jackery_solarvault/client/mqtt_push.py",
        "custom_components/jackery_solarvault/client/ble_transport.py",
    }
)

HUNDRED = Decimal("100")
JUSTIFIED_NO_COVER = re.compile(
    r"#\s*pragma:\s*no cover\s*(?:[-—:]|because\b|for\b).+",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class CoverageRates:
    """Line and branch coverage percentages for one coverage XML node."""

    line: Decimal
    branch: Decimal | None


def _decimal_percent(raw: str, *, label: str) -> Decimal:
    try:
        value = Decimal(raw)
    except InvalidOperation as err:
        raise ValueError(f"invalid {label}: {raw!r}") from err
    if value < 0 or value > 100:
        raise ValueError(f"{label} must be between 0 and 100: {raw!r}")
    return value


def _rate_to_percent(
    raw: str | None,
    *,
    label: str,
    required: bool = True,
) -> Decimal | None:
    if raw is None:
        if required:
            raise ValueError(f"coverage XML missing {label} rate")
        return None
    try:
        rate = Decimal(raw)
    except InvalidOperation as err:
        raise ValueError(f"invalid {label} rate: {raw!r}") from err
    if rate < 0 or rate > 1:
        raise ValueError(f"{label} rate must be between 0 and 1: {raw!r}")
    return rate * HUNDRED


def _normalized_filename(raw: str | None) -> str | None:
    if not raw:
        return None
    filename = raw.replace("\\", "/").lstrip("./")
    if filename.startswith(str(SOURCE_ROOT) + "/"):
        return filename
    return str(SOURCE_ROOT / filename)


def _repo_modules(patterns: tuple[str, ...]) -> frozenset[str]:
    modules: set[str] = set()
    for pattern in patterns:
        modules.update(
            path.as_posix()
            for path in SOURCE_ROOT.glob(pattern)
            if path.is_file() and path.suffix == ".py"
        )
    return frozenset(modules)


def _coverage_rates(root: ET.Element) -> dict[str, CoverageRates]:
    rates: dict[str, CoverageRates] = {}
    for class_node in root.findall(".//class"):
        filename = _normalized_filename(class_node.get("filename"))
        if filename is None:
            continue
        line = _rate_to_percent(class_node.get("line-rate"), label=f"{filename} line")
        branch = _rate_to_percent(
            class_node.get("branch-rate"),
            label=f"{filename} branch",
            required=False,
        )
        assert line is not None
        rates[filename] = CoverageRates(line=line, branch=branch)
    return rates


def _format_percent(value: Decimal | None) -> str:
    if value is None:
        return "missing"
    return f"{value:.2f}%"


def _pragma_failures() -> list[str]:
    failures: list[str] = []
    for path in SOURCE_ROOT.rglob("*.py"):
        lines = path.read_text(encoding="utf-8").splitlines()
        for line_number, line in enumerate(lines, 1):
            if "pragma: no cover" not in line:
                continue
            if JUSTIFIED_NO_COVER.search(line) is None:
                failures.append(
                    f"{path.as_posix()}:{line_number} has unjustified "
                    "# pragma: no cover"
                )
    return failures


def enforce_coverage_gates(
    *,
    coverage_xml: Path,
    total_minimum: Decimal,
    legacy_module_minimum: Decimal,
    perfect_module_minimum: Decimal,
) -> list[str]:
    """Return gate failures for the given coverage XML report."""
    if not coverage_xml.is_file():
        return [f"coverage xml not found: {coverage_xml}"]

    root = ET.parse(coverage_xml).getroot()
    total_line = _rate_to_percent(root.get("line-rate"), label="total line")
    total_branch = _rate_to_percent(
        root.get("branch-rate"),
        label="total branch",
        required=False,
    )
    assert total_line is not None

    failures: list[str] = _pragma_failures()
    if total_line < total_minimum:
        failures.append(
            "total line coverage "
            f"{_format_percent(total_line)} < {total_minimum:.2f}%"
        )
    if total_branch is None:
        failures.append(
            "coverage XML missing total branch-rate; run pytest with --cov-branch"
        )

    rates = _coverage_rates(root)
    perfect_modules = _repo_modules(PERFECT_COVERAGE_GLOBS)

    for module in sorted(perfect_modules):
        coverage = rates.get(module)
        if coverage is None:
            failures.append(
                f"perfect-coverage module missing from coverage XML: {module}"
            )
            continue
        if coverage.line < perfect_module_minimum:
            failures.append(
                f"{module} line coverage {_format_percent(coverage.line)} "
                f"< {perfect_module_minimum:.2f}%"
            )
        if coverage.branch is None:
            failures.append(
                f"{module} missing branch-rate; run pytest with --cov-branch"
            )
        elif coverage.branch < perfect_module_minimum:
            failures.append(
                f"{module} branch coverage {_format_percent(coverage.branch)} "
                f"< {perfect_module_minimum:.2f}%"
            )

    for module in sorted(LEGACY_CRITICAL_MODULES):
        coverage = rates.get(module)
        if coverage is None:
            failures.append(
                f"legacy critical module missing from coverage XML: {module}"
            )
        elif coverage.line < legacy_module_minimum:
            failures.append(
                f"{module} line coverage {_format_percent(coverage.line)} "
                f"< {legacy_module_minimum:.2f}%"
            )

    return failures


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-xml", type=Path, default=Path("coverage.xml"))
    parser.add_argument("--total-minimum", default="85")
    parser.add_argument("--legacy-module-minimum", default="90")
    parser.add_argument("--perfect-module-minimum", default="100")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        failures = enforce_coverage_gates(
            coverage_xml=args.coverage_xml,
            total_minimum=_decimal_percent(
                args.total_minimum,
                label="total minimum percent",
            ),
            legacy_module_minimum=_decimal_percent(
                args.legacy_module_minimum,
                label="legacy module minimum percent",
            ),
            perfect_module_minimum=_decimal_percent(
                args.perfect_module_minimum,
                label="perfect module minimum percent",
            ),
        )
    except (OSError, ET.ParseError, ValueError) as err:
        print(str(err), file=sys.stderr)
        return 1

    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1

    print("coverage gates passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
