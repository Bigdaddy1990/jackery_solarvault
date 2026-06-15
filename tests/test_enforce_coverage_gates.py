"""Tests for repository coverage gate enforcement."""

from decimal import Decimal
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


# ---------------------------------------------------------------------------
# Tests for the PR change: CI no longer passes --critical-module-minimum-percent
# so the per-module defaults from CRITICAL_MODULE_COVERAGE_MINIMUMS are used.
# ---------------------------------------------------------------------------


def test_enforce_without_override_uses_per_module_defaults(tmp_path: Path) -> None:
    """Omitting critical_module_minimum_percent uses repo-specific defaults, not 100%.

    After the PR change, CI calls the script without --critical-module-minimum-percent,
    so coordinator.py keeps its 90% gate while other modules stay at 100%.
    """
    rates = _full_coverage_rates()
    # coordinator at exactly its 90% transition gate — must still pass.
    rates["custom_components/jackery_solarvault/coordinator.py"] = "0.90"
    coverage_xml = _write_coverage_xml(tmp_path, rates)

    # No critical_module_minimum_percent override → per-module defaults apply.
    enforce_coverage_gates.enforce(coverage_xml=coverage_xml)


def test_enforce_coordinator_below_transition_gate_fails(tmp_path: Path) -> None:
    """coordinator.py below 90% fails even with the relaxed transition gate."""
    rates = _full_coverage_rates()
    rates["custom_components/jackery_solarvault/coordinator.py"] = "0.89"
    coverage_xml = _write_coverage_xml(tmp_path, rates)

    with pytest.raises(ValueError, match=r"coordinator\.py coverage 89\.00% < 90\.00%"):
        enforce_coverage_gates.enforce(coverage_xml=coverage_xml)


def test_enforce_with_global_override_applies_uniform_minimum(tmp_path: Path) -> None:
    """Passing critical_module_minimum_percent overrides ALL per-module gates.

    The --critical-module-minimum-percent flag (still in the script, removed from CI)
    replaces all per-module minimums with a single uniform percent.
    """
    rates = _full_coverage_rates()
    # coordinator at 0.95 — above 90% default but we override every gate to 95%.
    rates["custom_components/jackery_solarvault/coordinator.py"] = "0.95"
    coverage_xml = _write_coverage_xml(tmp_path, rates)

    # Override: demand 95% for every critical module → coordinator passes.
    enforce_coverage_gates.enforce(
        coverage_xml=coverage_xml,
        critical_module_minimum_percent=Decimal("95"),
    )


def test_enforce_global_override_rejects_coordinator_at_default_gate(
    tmp_path: Path,
) -> None:
    """A global 95% override rejects coordinator.py that would pass the 90% default."""
    rates = _full_coverage_rates()
    rates["custom_components/jackery_solarvault/coordinator.py"] = "0.90"
    coverage_xml = _write_coverage_xml(tmp_path, rates)

    with pytest.raises(ValueError, match=r"coordinator\.py coverage 90\.00% < 95\.00%"):
        enforce_coverage_gates.enforce(
            coverage_xml=coverage_xml,
            critical_module_minimum_percent=Decimal("95"),
        )


# ---------------------------------------------------------------------------
# main() CLI entry point tests
# ---------------------------------------------------------------------------


def test_main_returns_zero_on_success(tmp_path: Path) -> None:
    """main() returns 0 when all gates pass."""
    coverage_xml = _write_coverage_xml(tmp_path, _full_coverage_rates())

    result = enforce_coverage_gates.main(
        ["--coverage-xml", str(coverage_xml), "--total-minimum-percent", "85"]
    )

    assert result == 0


def test_main_returns_one_on_failure(tmp_path: Path) -> None:
    """main() returns 1 when total coverage is below the minimum."""
    rates = _full_coverage_rates()
    coverage_xml = _write_coverage_xml(tmp_path, rates, total="0.50")

    result = enforce_coverage_gates.main(
        ["--coverage-xml", str(coverage_xml), "--total-minimum-percent", "85"]
    )

    assert result == 1


def test_main_without_critical_module_flag_uses_per_module_defaults(
    tmp_path: Path,
) -> None:
    """main() without --critical-module-minimum-percent uses repo defaults.

    This directly validates the PR change: the CI workflow no longer passes
    --critical-module-minimum-percent, relying on checked-in per-module gates.
    """
    rates = _full_coverage_rates()
    # coordinator at exactly its 90% threshold.
    rates["custom_components/jackery_solarvault/coordinator.py"] = "0.90"
    coverage_xml = _write_coverage_xml(tmp_path, rates)

    result = enforce_coverage_gates.main(["--coverage-xml", str(coverage_xml)])

    assert result == 0


def test_main_with_critical_module_override_flag(tmp_path: Path) -> None:
    """main() accepts and applies --critical-module-minimum-percent."""
    rates = _full_coverage_rates()
    # coordinator at 0.95 — satisfies both default 90% and a 95% override.
    rates["custom_components/jackery_solarvault/coordinator.py"] = "0.95"
    coverage_xml = _write_coverage_xml(tmp_path, rates)

    result = enforce_coverage_gates.main(
        [
            "--coverage-xml",
            str(coverage_xml),
            "--critical-module-minimum-percent",
            "95",
        ]
    )

    assert result == 0


def test_main_critical_override_flag_fails_below_override_threshold(
    tmp_path: Path,
) -> None:
    """--critical-module-minimum-percent at 95 rejects coordinator at 90%."""
    rates = _full_coverage_rates()
    rates["custom_components/jackery_solarvault/coordinator.py"] = "0.90"
    coverage_xml = _write_coverage_xml(tmp_path, rates)

    result = enforce_coverage_gates.main(
        [
            "--coverage-xml",
            str(coverage_xml),
            "--critical-module-minimum-percent",
            "95",
        ]
    )

    assert result == 1


def test_main_missing_coverage_xml_returns_one(tmp_path: Path) -> None:
    """main() returns 1 when the coverage XML file does not exist."""
    result = enforce_coverage_gates.main(
        ["--coverage-xml", str(tmp_path / "nonexistent.xml")]
    )

    assert result == 1


def test_main_invalid_total_minimum_returns_one(tmp_path: Path) -> None:
    """main() returns 1 when --total-minimum-percent is not a valid number."""
    coverage_xml = _write_coverage_xml(tmp_path, _full_coverage_rates())

    result = enforce_coverage_gates.main(
        ["--coverage-xml", str(coverage_xml), "--total-minimum-percent", "notanumber"]
    )

    assert result == 1


def test_main_invalid_critical_minimum_returns_one(tmp_path: Path) -> None:
    """main() returns 1 when --critical-module-minimum-percent is not a valid number."""
    coverage_xml = _write_coverage_xml(tmp_path, _full_coverage_rates())

    result = enforce_coverage_gates.main(
        [
            "--coverage-xml",
            str(coverage_xml),
            "--critical-module-minimum-percent",
            "notanumber",
        ]
    )

    assert result == 1


# ---------------------------------------------------------------------------
# _decimal_percent helper tests
# ---------------------------------------------------------------------------


def test_decimal_percent_valid_values() -> None:
    """_decimal_percent accepts valid 0-100 values."""
    assert enforce_coverage_gates._decimal_percent("85", label="x") == Decimal("85")
    assert enforce_coverage_gates._decimal_percent("0", label="x") == Decimal("0")
    assert enforce_coverage_gates._decimal_percent("100", label="x") == Decimal("100")


def test_decimal_percent_rejects_non_numeric() -> None:
    """_decimal_percent raises ValueError for non-numeric input."""
    with pytest.raises(ValueError, match="invalid"):
        enforce_coverage_gates._decimal_percent("abc", label="total")


def test_decimal_percent_rejects_negative() -> None:
    """_decimal_percent raises ValueError for values below 0."""
    with pytest.raises(ValueError, match="must be between 0 and 100"):
        enforce_coverage_gates._decimal_percent("-1", label="total")


def test_decimal_percent_rejects_above_100() -> None:
    """_decimal_percent raises ValueError for values above 100."""
    with pytest.raises(ValueError, match="must be between 0 and 100"):
        enforce_coverage_gates._decimal_percent("101", label="total")


# ---------------------------------------------------------------------------
# _normalized_filename helper tests
# ---------------------------------------------------------------------------


def test_normalized_filename_converts_backslashes() -> None:
    """Windows-style backslash paths are normalized to forward slashes."""
    result = enforce_coverage_gates._normalized_filename(
        r"custom_components\jackery_solarvault\coordinator.py"
    )
    assert result == "custom_components/jackery_solarvault/coordinator.py"


def test_normalized_filename_strips_leading_dot_slash() -> None:
    """Leading ./ prefix is stripped for repository-style paths."""
    result = enforce_coverage_gates._normalized_filename(
        "./custom_components/jackery_solarvault/api.py"
    )
    assert result == "custom_components/jackery_solarvault/api.py"


def test_normalized_filename_returns_none_for_empty() -> None:
    """Empty or None input returns None."""
    assert enforce_coverage_gates._normalized_filename(None) is None
    assert enforce_coverage_gates._normalized_filename("") is None


def test_normalized_filename_passes_through_clean_path() -> None:
    """A clean path without backslashes or ./ prefix is returned unchanged."""
    raw = "custom_components/jackery_solarvault/services.py"
    assert enforce_coverage_gates._normalized_filename(raw) == raw


# ---------------------------------------------------------------------------
# _rate_to_percent helper tests
# ---------------------------------------------------------------------------


def test_rate_to_percent_converts_correctly() -> None:
    """Line-rate 0.95 becomes 95.00 percent."""
    result = enforce_coverage_gates._rate_to_percent("0.95", label="x")
    assert result == Decimal("95.00")


def test_rate_to_percent_raises_for_none() -> None:
    """None line-rate raises ValueError (missing attribute in XML)."""
    with pytest.raises(ValueError, match="missing"):
        enforce_coverage_gates._rate_to_percent(None, label="total")


def test_rate_to_percent_raises_for_invalid_string() -> None:
    """Non-numeric line-rate raises ValueError."""
    with pytest.raises(ValueError, match="invalid"):
        enforce_coverage_gates._rate_to_percent("bad", label="total")


# ---------------------------------------------------------------------------
# enforce() edge case tests
# ---------------------------------------------------------------------------


def test_enforce_raises_for_missing_coverage_xml(tmp_path: Path) -> None:
    """enforce() raises ValueError when the coverage XML file does not exist."""
    with pytest.raises(ValueError, match="coverage xml not found"):
        enforce_coverage_gates.enforce(coverage_xml=tmp_path / "missing.xml")


def test_enforce_collects_multiple_failures_in_single_error(tmp_path: Path) -> None:
    """All gate failures are collected and reported together in one ValueError."""
    rates = _full_coverage_rates()
    # Total low + two critical modules failing simultaneously.
    rates["custom_components/jackery_solarvault/services.py"] = "0.50"
    rates["custom_components/jackery_solarvault/ingest.py"] = "0.70"
    coverage_xml = _write_coverage_xml(tmp_path, rates, total="0.50")

    with pytest.raises(ValueError) as exc_info:
        enforce_coverage_gates.enforce(
            coverage_xml=coverage_xml,
            total_minimum_percent=Decimal("85"),
        )

    message = str(exc_info.value)
    assert "total coverage" in message
    assert "services.py" in message
    assert "ingest.py" in message


def test_enforce_total_below_minimum_fails(tmp_path: Path) -> None:
    """Total coverage below the minimum triggers a ValueError."""
    coverage_xml = _write_coverage_xml(tmp_path, _full_coverage_rates(), total="0.84")

    with pytest.raises(ValueError, match=r"total coverage 84\.00% < 85\.00%"):
        enforce_coverage_gates.enforce(
            coverage_xml=coverage_xml,
            total_minimum_percent=Decimal("85"),
        )


def test_enforce_total_at_exact_minimum_passes(tmp_path: Path) -> None:
    """Total coverage exactly equal to the minimum is accepted (not below)."""
    coverage_xml = _write_coverage_xml(tmp_path, _full_coverage_rates(), total="0.85")

    # Must not raise.
    enforce_coverage_gates.enforce(
        coverage_xml=coverage_xml,
        total_minimum_percent=Decimal("85"),
    )


def test_enforce_filenames_normalized_from_backslash_paths(tmp_path: Path) -> None:
    """Coverage XML with Windows-style backslash filenames still matches critical modules."""
    rates = {
        k.replace("/", "\\"): v for k, v in _full_coverage_rates().items()
    }
    coverage_xml = _write_coverage_xml(tmp_path, rates)

    # Backslash paths should be normalized and still satisfy critical module gates.
    enforce_coverage_gates.enforce(coverage_xml=coverage_xml)
