"""Enforce total and critical-module coverage gates from coverage.py XML."""

from __future__ import annotations

import argparse
from collections.abc import Iterable
from decimal import Decimal, InvalidOperation
from pathlib import Path
from xml.etree.ElementTree import Element

from defusedxml import ElementTree as ET

CRITICAL_MODULES = (
    "custom_components/jackery_solarvault/coordinator.py",
    "custom_components/jackery_solarvault/config_flow.py",
    "custom_components/jackery_solarvault/services.py",
    "custom_components/jackery_solarvault/data_manager.py",
)


def _percent(raw_rate: str | None, *, context: str) -> Decimal:
    """Convert a coverage.py line-rate string to a percentage."""
    if raw_rate is None:
        raise SystemExit(f"missing line-rate attribute for {context}")

    try:
        return Decimal(raw_rate) * Decimal(100)
    except InvalidOperation as exc:
        raise SystemExit(f"invalid line-rate for {context}: {raw_rate!r}") from exc


def _normalized_path(raw_path: str) -> str:
    """Normalize coverage paths so XML from different runners matches gates."""
    return raw_path.replace("\\", "/").lstrip("./")


def _class_line_rates(root: Element) -> dict[str, Decimal]:
    """Return the highest line coverage percentage reported for each class file."""
    rates: dict[str, Decimal] = {}
    for class_node in root.findall(".//class"):
        filename = class_node.attrib.get("filename")
        if not filename:
            continue

        path = _normalized_path(filename)
        rate = _percent(class_node.attrib.get("line-rate"), context=path)
        rates[path] = max(rate, rates.get(path, Decimal("-1")))

    return rates


def _matching_rate(class_rates: dict[str, Decimal], module: str) -> Decimal | None:
    """Find coverage for a critical module regardless of relative path prefix."""
    normalized_module = _normalized_path(module)
    for path, rate in class_rates.items():
        if path == normalized_module or path.endswith(f"/{normalized_module}"):
            return rate
    return None


def _failed_module_gates(
    class_rates: dict[str, Decimal],
    modules: Iterable[str],
    minimum_percent: Decimal,
) -> list[str]:
    """Build failure messages for critical modules below the gate."""
    failures: list[str] = []
    for module in modules:
        rate = _matching_rate(class_rates, module)
        if rate is None:
            failures.append(f"{module}: missing from coverage.xml")
            continue
        if rate < minimum_percent:
            failures.append(f"{module}: {rate:.2f}% < {minimum_percent:.2f}%")
    return failures


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--coverage-xml", type=Path, default=Path("coverage.xml"))
    parser.add_argument("--total-minimum-percent", type=Decimal, default=Decimal("85"))
    parser.add_argument(
        "--critical-module-minimum-percent", type=Decimal, default=Decimal("90")
    )
    return parser.parse_args()


def main() -> int:
    """Run coverage gate checks."""
    args = _parse_args()
    if not args.coverage_xml.is_file():
        raise SystemExit(f"coverage XML not found: {args.coverage_xml}")

    root = ET.parse(args.coverage_xml).getroot()
    total_percent = _percent(root.attrib.get("line-rate"), context="total coverage")
    failures: list[str] = []
    if total_percent < args.total_minimum_percent:
        failures.append(
            f"total coverage: {total_percent:.2f}% < {args.total_minimum_percent:.2f}%"
        )

    failures.extend(
        _failed_module_gates(
            _class_line_rates(root),
            CRITICAL_MODULES,
            args.critical_module_minimum_percent,
        )
    )
    if failures:
        raise SystemExit("coverage gate failed:\n" + "\n".join(failures))

    print(
        "coverage gate passed: "
        f"total {total_percent:.2f}% >= {args.total_minimum_percent:.2f}%"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
