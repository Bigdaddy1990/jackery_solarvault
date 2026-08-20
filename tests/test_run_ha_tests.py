"""Contracts for the Home Assistant pytest wrapper."""

from scripts.run_ha_tests import _pytest_args


def test_default_harness_run_disables_coverage() -> None:
    """The HA harness must not consume the separately scheduled coverage gate."""
    assert _pytest_args([]) == ["--no-cov"]
    assert _pytest_args(["tests"]) == ["tests", "--no-cov"]


def test_explicit_coverage_choice_is_preserved() -> None:
    """An explicit caller coverage option must not be overridden."""
    assert _pytest_args(["--cov=custom_components"]) == [
        "--cov=custom_components",
    ]
    assert _pytest_args(["--no-cov"]) == ["--no-cov"]
