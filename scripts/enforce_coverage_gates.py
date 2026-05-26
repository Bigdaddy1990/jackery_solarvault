"""Enforce total and critical-module coverage gates from coverage.py XML."""

from __future__ import annotations

import argparse
from decimal import Decimal, InvalidOperation
from pathlib import Path
import sys
import xml.etree.ElementTree as ET

CRITICAL_MODULES: frozenset[str] = frozenset({
    "custom_components/jackery_solarvault/__init__.py",
    "custom_components/jackery_solarvault/coordinator.py",
    "custom_components/jackery_solarvault/sensor.py",
    "custom_components/jackery_solarvault/util.py",
    "custom_components/jackery_solarvault/client/api.py",
    "custom_components/jackery_solarvault/client/mqtt_push.py",
    "custom_components/jackery_solarvault/client/ble_transport.py",
})


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


def enforce(
    *,
    coverage_xml: Path,
    total_minimum_percent: Decimal,
    critical_module_minimum_percent: Decimal,
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
    for module in sorted(CRITICAL_MODULES):
        coverage = rates.get(module)
        if coverage is None:
            failures.append(f"critical module missing from coverage XML: {module}")
        elif coverage < critical_module_minimum_percent:
            failures.append(
                f"{module} coverage {coverage:.2f}% "
                f"< {critical_module_minimum_percent:.2f}%"
            )
    if failures:
        raise ValueError("\n".join(failures))
    print(
        "coverage gates passed: "
        f"total={total:.2f}% >= {total_minimum_percent:.2f}%, "
        f"critical_modules>={critical_module_minimum_percent:.2f}%"
    )


def main(argv: list[str] | None = None) -> int:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-xml", type=Path, required=True)
    parser.add_argument("--total-minimum-percent", required=True)
    parser.add_argument("--critical-module-minimum-percent", required=True)
    args = parser.parse_args(argv)
    try:
        enforce(
            coverage_xml=args.coverage_xml,
            total_minimum_percent=_decimal_percent(
                args.total_minimum_percent,
                label="total minimum percent",
            ),
            critical_module_minimum_percent=_decimal_percent(
                args.critical_module_minimum_percent,
                label="critical module minimum percent",
            ),
        )
    except (OSError, ET.ParseError, ValueError) as err:
        print(err, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
