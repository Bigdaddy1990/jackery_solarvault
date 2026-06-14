"""Tests for repository coverage gate enforcement."""

from pathlib import Path  # noqa: TC003

import pytest
from scripts import enforce_coverage_gates


def _write_coverage_xml(
    tmp_path: Path, rates: dict[str, str], total: str = "1"
) -> Path:
    """Write a minimal Cobertura XML file with class line rates."""
    classes = "\n".join(
        f'<class filename="{filename}" line-rate="{rate}" />'
        for filename, rate in rates.items()
    )
    coverage_xml = tmp_path / "coverage.xml"
    coverage_xml.write_text(
        f'<coverage line-rate="{total}"><packages><package><classes>'
        f"{classes}"
        "</classes></package></packages></coverage>",
        encoding="utf-8",
    )
    return coverage_xml


def _full_coverage_rates() -> dict[str, str]:
    """Return passing coverage rates for every repository critical module."""
    return dict.fromkeys(enforce_coverage_gates.CRITICAL_MODULE_COVERAGE_MINIMUMS, "1")


def test_enforce_reports_missing_critical_module(tmp_path: Path) -> None:
    """A critical module absent from coverage.xml must fail loudly."""
    rates = _full_coverage_rates()
    rates.pop("custom_components/jackery_solarvault/ingest.py")
    coverage_xml = _write_coverage_xml(tmp_path, rates)

    with pytest.raises(ValueError, match=r"critical module missing.*ingest\.py"):
        enforce_coverage_gates.enforce(coverage_xml=coverage_xml)


def test_enforce_rejects_underfilled_critical_module(tmp_path: Path) -> None:
    """A critical module below its configured gate must fail."""
    rates = _full_coverage_rates()
    rates["custom_components/jackery_solarvault/services.py"] = "0.99"
    coverage_xml = _write_coverage_xml(tmp_path, rates)

    with pytest.raises(ValueError, match=r"services\.py coverage 99\.00% < 100\.00%"):
        enforce_coverage_gates.enforce(coverage_xml=coverage_xml)


def test_enforce_accepts_default_100_percent_modules(tmp_path: Path) -> None:
    """Default repo gates pass when every 100% critical module is fully covered."""
    coverage_xml = _write_coverage_xml(tmp_path, _full_coverage_rates())

    enforce_coverage_gates.enforce(coverage_xml=coverage_xml)


def test_legacy_coordinator_uses_documented_transition_gate(tmp_path: Path) -> None:
    """The legacy coordinator keeps a temporary 90% gate during extraction."""
    rates = _full_coverage_rates()
    rates["custom_components/jackery_solarvault/coordinator.py"] = "0.90"
    coverage_xml = _write_coverage_xml(tmp_path, rates)

    enforce_coverage_gates.enforce(coverage_xml=coverage_xml)
