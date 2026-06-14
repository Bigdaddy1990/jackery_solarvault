"""Regression tests for the Jackery protocol coverage matrix."""

from scripts.check_reference_coverage import validate_reference_coverage


def test_reference_coverage_matrix_has_no_drift() -> None:
    """Fail when reference endpoints, commands, or services drift from code."""
    assert validate_reference_coverage() == []
