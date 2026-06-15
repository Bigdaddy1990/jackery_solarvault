"""Regression tests for coverage gate enforcement."""

from __future__ import annotations

import importlib
from pathlib import Path
from unittest.mock import patch

import pytest
from scripts import enforce_coverage_gates

COVERED_CLASSES = """\
<class filename="custom_components/jackery_solarvault/coordinator.py" line-rate="0.91" />
<class filename="custom_components/jackery_solarvault/config_flow.py" line-rate="0.92" />
<class filename="custom_components/jackery_solarvault/services.py" line-rate="0.93" />
<class filename="custom_components/jackery_solarvault/data_manager.py" line-rate="0.94" />
"""


def _write_coverage_xml(path: Path, *, line_rate: str, classes: str) -> None:
    path.write_text(
        f"""<?xml version="1.0" ?>
<coverage line-rate="{line_rate}">
  <packages>
    <package name="jackery_solarvault">
      <classes>
        {classes}
      </classes>
    </package>
  </packages>
</coverage>
""",
        encoding="utf-8",
    )


def test_import_does_not_evaluate_defusedxml_element_annotation() -> None:
    """The module imports with defusedxml, which has no Element attribute."""
    assert not hasattr(enforce_coverage_gates.ET, "Element")
    importlib.reload(enforce_coverage_gates)


def test_main_passes_when_total_and_critical_modules_meet_gates(tmp_path: Path) -> None:
    """Valid coverage XML passing all configured gates exits cleanly."""
    coverage_xml = tmp_path / "coverage.xml"
    _write_coverage_xml(coverage_xml, line_rate="0.95", classes=COVERED_CLASSES)

    with patch(
        "sys.argv",
        [
            "enforce_coverage_gates",
            "--coverage-xml",
            str(coverage_xml),
            "--total-minimum-percent",
            "90",
            "--critical-module-minimum-percent",
            "90",
        ],
    ):
        assert enforce_coverage_gates.main() == 0


def test_main_fails_when_critical_module_is_below_gate(tmp_path: Path) -> None:
    """Critical modules below the configured gate are reported."""
    coverage_xml = tmp_path / "coverage.xml"
    _write_coverage_xml(
        coverage_xml,
        line_rate="0.95",
        classes=COVERED_CLASSES.replace(
            'coordinator.py" line-rate="0.91', 'coordinator.py" line-rate="0.89'
        ),
    )

    with (
        patch(
            "sys.argv",
            [
                "enforce_coverage_gates",
                "--coverage-xml",
                str(coverage_xml),
                "--critical-module-minimum-percent",
                "90",
            ],
        ),
        pytest.raises(SystemExit, match="coordinator.py: 89.00% < 90.00%"),
    ):
        enforce_coverage_gates.main()
