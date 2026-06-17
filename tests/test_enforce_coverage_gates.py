"""Tests for coverage gate enforcement."""

from decimal import Decimal
from typing import TYPE_CHECKING

from scripts.enforce_coverage_gates import enforce_coverage_gates

if TYPE_CHECKING:
    from pathlib import Path


def _write_xml(path: Path, *, branch_rate: str | None = "1") -> None:
    branch_attr = f' branch-rate="{branch_rate}"' if branch_rate is not None else ""
    classes = "\n".join(
        f'<class filename="{module}" line-rate="1" branch-rate="1" />'
        for module in (
            "custom_components/jackery_solarvault/__init__.py",
            "custom_components/jackery_solarvault/coordinator.py",
            "custom_components/jackery_solarvault/sensor.py",
            "custom_components/jackery_solarvault/util.py",
            "custom_components/jackery_solarvault/client/api.py",
            "custom_components/jackery_solarvault/client/mqtt_push.py",
            "custom_components/jackery_solarvault/client/ble_transport.py",
            "custom_components/jackery_solarvault/handlers/__init__.py",
            "custom_components/jackery_solarvault/handlers/mqtt_handlers.py",
            "custom_components/jackery_solarvault/stats/__init__.py",
            "custom_components/jackery_solarvault/client/_endpoints/__init__.py",
            "custom_components/jackery_solarvault/client/_endpoints/accessories.py",
            "custom_components/jackery_solarvault/client/_endpoints/auth.py",
            "custom_components/jackery_solarvault/client/_endpoints/device.py",
            "custom_components/jackery_solarvault/client/_endpoints/energy_price.py",
            "custom_components/jackery_solarvault/client/_endpoints/misc.py",
            "custom_components/jackery_solarvault/client/_endpoints/push.py",
            "custom_components/jackery_solarvault/client/_endpoints/shelly.py",
            "custom_components/jackery_solarvault/client/_endpoints/smart_mode.py",
            "custom_components/jackery_solarvault/client/_endpoints/statistics.py",
        )
    )
    path.write_text(
        f'<coverage line-rate="1"{branch_attr}>'
        f"<packages><package><classes>{classes}</classes></package></packages>"
        "</coverage>",
        encoding="utf-8",
    )


def test_enforce_coverage_gates_accepts_perfect_line_and_branch(tmp_path: Path) -> None:
    """A report with 100% line and branch coverage for target modules passes."""
    coverage_xml = tmp_path / "coverage.xml"
    _write_xml(coverage_xml)

    assert not enforce_coverage_gates(
        coverage_xml=coverage_xml,
        total_minimum=Decimal(85),
        legacy_module_minimum=Decimal(90),
        perfect_module_minimum=Decimal(100),
    )


def test_enforce_coverage_gates_requires_branch_coverage(tmp_path: Path) -> None:
    """CI must reject reports not produced with branch coverage enabled."""
    coverage_xml = tmp_path / "coverage.xml"
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
