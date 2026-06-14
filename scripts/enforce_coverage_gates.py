"""Enforce total and critical-module coverage gates from coverage.py XML."""

import argparse
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

DEFAULT_TOTAL_MINIMUM_PERCENT = Decimal(85)
DEFAULT_CRITICAL_MODULE_MINIMUM_PERCENT = Decimal(100)

# Repository-specific module gates. Newly extracted critical runtime modules must
# stay at 100%; the legacy coordinator is lower only while refactoring moves
# branches into covered helper modules.
CRITICAL_MODULE_COVERAGE_MINIMUMS: Mapping[str, Decimal] = {
    "custom_components/jackery_solarvault/config_flow.py": (
        DEFAULT_CRITICAL_MODULE_MINIMUM_PERCENT
    ),
    "custom_components/jackery_solarvault/coordinator.py": Decimal(90),
    "custom_components/jackery_solarvault/ingest.py": (
        DEFAULT_CRITICAL_MODULE_MINIMUM_PERCENT
    ),
    "custom_components/jackery_solarvault/services.py": (
        DEFAULT_CRITICAL_MODULE_MINIMUM_PERCENT
    ),
}

CRITICAL_MODULES: frozenset[str] = frozenset(CRITICAL_MODULE_COVERAGE_MINIMUMS)


def _decimal_percent(raw: str, *, label: str) -> Decimal:
    """Parse a percent CLI value."""
    try:
        value = Decimal(raw)
    except InvalidOperation as err:
        raise ValueError(f"invalid {label}: {raw!r}") from err
    if value < 0 or value > 100:
        raise ValueError(f"{label} must be between 0 and 100: {raw!r}")
    return value


def _rate_to_percent(raw: str | None, *, label: str) -> Decimal:
    """Convert a Cobertura line-rate attribute to percent."""
    if raw is None:
        raise ValueError(f"coverage XML missing {label} line-rate")
    try:
        rate = Decimal(raw)
    except InvalidOperation as err:
        raise ValueError(f"invalid {label} line-rate: {raw!r}") from err
    return rate * Decimal(100)


def _normalized_filename(raw: str | None) -> str | None:
    """Normalize coverage.py filenames to repository-style paths."""
    if not raw:
        return None
    return raw.replace("\\", "/").lstrip("./")


def _class_line_rates(root: ET.Element) -> dict[str, Decimal]:
    """Return coverage percent by normalized filename."""
    rates: dict[str, Decimal] = {}
    for class_node in root.findall(".//class"):
        filename = _normalized_filename(class_node.get("filename"))
        if filename is None:
            continue
        rates[filename] = _rate_to_percent(
            class_node.get("line-rate"),
            label=filename,
        )
    return rates


def _critical_module_minimums(
    override_percent: Decimal | None,
) -> Mapping[str, Decimal]:
    """Return critical-module thresholds, applying a global CLI override if set."""
    if override_percent is None:
        return CRITICAL_MODULE_COVERAGE_MINIMUMS
    return dict.fromkeys(CRITICAL_MODULE_COVERAGE_MINIMUMS, override_percent)


def enforce(
    *,
    coverage_xml: Path,
    total_minimum_percent: Decimal = DEFAULT_TOTAL_MINIMUM_PERCENT,
    critical_module_minimum_percent: Decimal | None = None,
) -> None:
    """Raise ValueError when a coverage gate fails."""
    if not coverage_xml.is_file():
        raise ValueError(f"coverage xml not found: {coverage_xml}")
    root = ET.parse(coverage_xml).getroot()
    total = _rate_to_percent(root.get("line-rate"), label="total")
    failures: list[str] = []
    if total < total_minimum_percent:
        failures.append(f"total coverage {total:.2f}% < {total_minimum_percent:.2f}%")

    rates = _class_line_rates(root)
    critical_minimums = _critical_module_minimums(critical_module_minimum_percent)
    for module, minimum_percent in sorted(critical_minimums.items()):
        coverage = rates.get(module)
        if coverage is None:
            failures.append(f"critical module missing from coverage XML: {module}")
        elif coverage < minimum_percent:
            failures.append(
                f"{module} coverage {coverage:.2f}% < {minimum_percent:.2f}%"
            )
    if failures:
        raise ValueError("\n".join(failures))
    critical_summary = ", ".join(
        f"{module}>={minimum:.2f}%"
        for module, minimum in sorted(critical_minimums.items())
    )
    print(
        "coverage gates passed: "
        f"total={total:.2f}% >= {total_minimum_percent:.2f}%, "
        f"critical_modules={critical_summary}"
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-xml", type=Path, required=True)
    parser.add_argument(
        "--total-minimum-percent",
        default=str(DEFAULT_TOTAL_MINIMUM_PERCENT),
    )
    parser.add_argument(
        "--critical-module-minimum-percent",
        help=(
            "Override every repo-specific critical-module gate. Omit to use "
            "the checked-in per-module defaults."
        ),
    )
    args = parser.parse_args(argv)
    try:
        critical_minimum = None
        if args.critical_module_minimum_percent is not None:
            critical_minimum = _decimal_percent(
                args.critical_module_minimum_percent,
                label="critical module minimum percent",
            )
        enforce(
            coverage_xml=args.coverage_xml,
            total_minimum_percent=_decimal_percent(
                args.total_minimum_percent,
                label="total minimum percent",
            ),
            critical_module_minimum_percent=critical_minimum,
        )
    except (OSError, ET.ParseError, ValueError) as err:
        print(err, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
