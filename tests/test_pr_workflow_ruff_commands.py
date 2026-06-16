"""Tests for workflow YAML changes introduced in this PR.

This PR changes ruff commands in autofix.yml and pre-commit-ci-lite.yml to
explicitly target the current directory (``.``) and always suppress non-zero
exit codes with ``|| true``. It also removes the
``--critical-module-minimum-percent`` flag from the coverage-gates call in
reusable-python-tests.yml, which now relies on the per-module defaults
checked in to ``scripts/enforce_coverage_gates.py``.
"""

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent
AUTOFIX_YML = REPO_ROOT / ".github" / "workflows" / "autofix.yml"
PRE_COMMIT_LITE_YML = REPO_ROOT / ".github" / "workflows" / "pre-commit-ci-lite.yml"
REUSABLE_TESTS_YML = REPO_ROOT / ".github" / "workflows" / "reusable-python-tests.yml"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> object:
    """Parse a YAML file and return the document."""
    with path.open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def _collect_run_commands(node: object) -> list[str]:
    """Recursively collect all ``run`` command strings from a parsed YAML document."""
    if isinstance(node, dict):
        result: list[str] = []
        for key, value in node.items():
            if key == "run" and isinstance(value, str):
                result.append(value)
            else:
                result.extend(_collect_run_commands(value))
        return result
    if isinstance(node, list):
        result = []
        for item in node:
            result.extend(_collect_run_commands(item))
        return result
    return []


# ---------------------------------------------------------------------------
# autofix.yml – YAML validity
# ---------------------------------------------------------------------------


def test_autofix_yml_is_valid_yaml() -> None:
    """autofix.yml must be parseable as YAML and non-empty."""
    doc = _load_yaml(AUTOFIX_YML)
    assert doc is not None, "autofix.yml must not be empty"
    assert isinstance(doc, dict), "autofix.yml top-level must be a mapping"


def test_autofix_yml_has_expected_workflow_name() -> None:
    """autofix.yml must declare the pre-commit-autofix workflow name."""
    doc = _load_yaml(AUTOFIX_YML)
    assert isinstance(doc, dict)
    assert "name" in doc
    assert "autofix" in doc["name"].lower() or "pre-commit" in doc["name"].lower()


# ---------------------------------------------------------------------------
# autofix.yml – ruff command format (PR change)
# ---------------------------------------------------------------------------


def test_autofix_yml_initial_ruff_check_uses_dot_target() -> None:
    """The initial ruff check step must target '.' explicitly.

    The PR changed ``ruff check --fix`` to ``ruff check . --fix || true``
    so that ruff always receives an explicit target path and failures are
    non-fatal in the autofix context.
    """
    doc = _load_yaml(AUTOFIX_YML)
    commands = _collect_run_commands(doc)
    # Find single-line ruff check commands (not part of a multi-line block)
    ruff_check_lines = [
        line.strip()
        for cmd in commands
        for line in cmd.splitlines()
        if line.strip().startswith("ruff check")
    ]
    assert ruff_check_lines, "autofix.yml must contain at least one ruff check command"
    # Every ruff check command must include an explicit path argument.
    # The first occurrence is the initial check added by the PR.
    for line in ruff_check_lines:
        assert " . " in line or line.endswith(" ."), (
            f"ruff check command must include explicit '.' target: {line!r}"
        )


def test_autofix_yml_initial_ruff_format_uses_dot_target() -> None:
    """The initial ruff format step must target '.' explicitly.

    The PR changed ``ruff format`` to ``ruff format . || true``.
    """
    doc = _load_yaml(AUTOFIX_YML)
    commands = _collect_run_commands(doc)
    ruff_format_lines = [
        line.strip()
        for cmd in commands
        for line in cmd.splitlines()
        if line.strip().startswith("ruff format")
    ]
    assert ruff_format_lines, "autofix.yml must contain at least one ruff format command"
    for line in ruff_format_lines:
        assert " . " in line or line.endswith((" .", " . || true")), (
            f"ruff format command must include explicit '.' target: {line!r}"
        )


def test_autofix_yml_ruff_commands_use_or_true_fallback() -> None:
    """Ruff commands in autofix.yml must end with '|| true' to be non-fatal."""
    doc = _load_yaml(AUTOFIX_YML)
    commands = _collect_run_commands(doc)
    ruff_lines = [
        line.strip()
        for cmd in commands
        for line in cmd.splitlines()
        if line.strip().startswith("ruff ")
    ]
    assert ruff_lines, "autofix.yml must contain ruff commands"
    for line in ruff_lines:
        assert line.endswith("|| true"), (
            f"ruff command in autofix.yml must end with '|| true': {line!r}"
        )


def test_autofix_yml_first_ruff_check_command_matches_expected() -> None:
    """The first standalone ruff check command must match the PR-updated form."""
    doc = _load_yaml(AUTOFIX_YML)
    # The first step with a single-line ruff check is the one changed by the PR.
    jobs = doc.get("jobs", {})
    for job in jobs.values():
        for step in job.get("steps", []):
            run = step.get("run", "")
            if isinstance(run, str) and run.strip() == "ruff check . --fix || true":
                return  # found the expected command
    pytest.fail(
        "autofix.yml must contain a step with exactly: ruff check . --fix || true"
    )


def test_autofix_yml_first_ruff_format_command_matches_expected() -> None:
    """The first standalone ruff format command must match the PR-updated form."""
    doc = _load_yaml(AUTOFIX_YML)
    jobs = doc.get("jobs", {})
    for job in jobs.values():
        for step in job.get("steps", []):
            run = step.get("run", "")
            if isinstance(run, str) and run.strip() == "ruff format . || true":
                return  # found the expected command
    pytest.fail(
        "autofix.yml must contain a step with exactly: ruff format . || true"
    )


# ---------------------------------------------------------------------------
# pre-commit-ci-lite.yml – YAML validity
# ---------------------------------------------------------------------------


def test_pre_commit_lite_yml_is_valid_yaml() -> None:
    """pre-commit-ci-lite.yml must be parseable as YAML and non-empty."""
    doc = _load_yaml(PRE_COMMIT_LITE_YML)
    assert doc is not None, "pre-commit-ci-lite.yml must not be empty"
    assert isinstance(doc, dict)


# ---------------------------------------------------------------------------
# pre-commit-ci-lite.yml – ruff command format (PR change)
# ---------------------------------------------------------------------------


def test_pre_commit_lite_yml_ruff_check_uses_dot_target() -> None:
    """Ruff check in pre-commit-ci-lite.yml must target '.' explicitly.

    The PR changed ``ruff check --fix --preview --target-version py314 || true``
    to ``ruff check . --fix --preview --target-version py314 || true``.
    """
    doc = _load_yaml(PRE_COMMIT_LITE_YML)
    commands = _collect_run_commands(doc)
    ruff_check_lines = [
        line.strip()
        for cmd in commands
        for line in cmd.splitlines()
        if line.strip().startswith("ruff check")
    ]
    assert ruff_check_lines, "pre-commit-ci-lite.yml must contain a ruff check command"
    for line in ruff_check_lines:
        # The dot must come right after 'ruff check' as an explicit path argument.
        parts = line.split()
        assert len(parts) >= 3 and parts[2] == ".", (
            f"ruff check must have '.' as its path argument: {line!r}"
        )


def test_pre_commit_lite_yml_ruff_format_uses_dot_target() -> None:
    """Ruff format in pre-commit-ci-lite.yml must target '.' explicitly."""
    doc = _load_yaml(PRE_COMMIT_LITE_YML)
    commands = _collect_run_commands(doc)
    ruff_format_lines = [
        line.strip()
        for cmd in commands
        for line in cmd.splitlines()
        if line.strip().startswith("ruff format")
    ]
    assert ruff_format_lines, (
        "pre-commit-ci-lite.yml must contain a ruff format command"
    )
    for line in ruff_format_lines:
        parts = line.split()
        assert len(parts) >= 3 and parts[2] == ".", (
            f"ruff format must have '.' as its path argument: {line!r}"
        )


def test_pre_commit_lite_yml_ruff_check_command_matches_expected() -> None:
    """The ruff check command in pre-commit-ci-lite.yml matches the PR-updated form."""
    doc = _load_yaml(PRE_COMMIT_LITE_YML)
    jobs = doc.get("jobs", {})
    for job in jobs.values():
        for step in job.get("steps", []):
            run = step.get("run", "")
            if isinstance(run, str) and run.strip() == (
                "ruff check . --fix --preview --target-version py314 || true"
            ):
                return
    pytest.fail(
        "pre-commit-ci-lite.yml must contain a step with exactly: "
        "ruff check . --fix --preview --target-version py314 || true"
    )


def test_pre_commit_lite_yml_ruff_format_command_matches_expected() -> None:
    """The ruff format command in pre-commit-ci-lite.yml matches the PR-updated form."""
    doc = _load_yaml(PRE_COMMIT_LITE_YML)
    jobs = doc.get("jobs", {})
    for job in jobs.values():
        for step in job.get("steps", []):
            run = step.get("run", "")
            if isinstance(run, str) and run.strip() == "ruff format . || true":
                return
    pytest.fail(
        "pre-commit-ci-lite.yml must contain a step with exactly: ruff format . || true"
    )


# ---------------------------------------------------------------------------
# reusable-python-tests.yml – coverage gate change (PR change)
# ---------------------------------------------------------------------------


def test_reusable_tests_yml_is_valid_yaml() -> None:
    """reusable-python-tests.yml must be parseable as YAML and non-empty."""
    doc = _load_yaml(REUSABLE_TESTS_YML)
    assert doc is not None
    assert isinstance(doc, dict)


def test_reusable_tests_yml_no_critical_module_minimum_percent_flag() -> None:
    """reusable-python-tests.yml must NOT pass --critical-module-minimum-percent.

    The PR removed this flag so that the enforce_coverage_gates script uses
    the per-module defaults from CRITICAL_MODULE_COVERAGE_MINIMUMS instead of
    a single uniform override.
    """
    source = REUSABLE_TESTS_YML.read_text(encoding="utf-8")
    assert "--critical-module-minimum-percent" not in source, (
        "reusable-python-tests.yml must not pass --critical-module-minimum-percent "
        "to enforce_coverage_gates (removed in this PR to use per-module defaults)"
    )


def test_reusable_tests_yml_coverage_gate_step_uses_total_minimum_flag() -> None:
    """The coverage gate invocation must still pass --total-minimum-percent."""
    source = REUSABLE_TESTS_YML.read_text(encoding="utf-8")
    assert "--total-minimum-percent" in source, (
        "reusable-python-tests.yml must still pass --total-minimum-percent "
        "to enforce_coverage_gates"
    )


def test_reusable_tests_yml_coverage_gate_calls_enforce_coverage_gates() -> None:
    """enforce_coverage_gates must still be invoked in the reusable workflow."""
    source = REUSABLE_TESTS_YML.read_text(encoding="utf-8")
    assert "enforce_coverage_gates" in source or "scripts.enforce_coverage_gates" in source, (
        "reusable-python-tests.yml must invoke scripts.enforce_coverage_gates"
    )


# ---------------------------------------------------------------------------
# Cross-workflow regression: neither workflow re-introduces the removed flag
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "workflow_path",
    [AUTOFIX_YML, PRE_COMMIT_LITE_YML, REUSABLE_TESTS_YML],
    ids=["autofix.yml", "pre-commit-ci-lite.yml", "reusable-python-tests.yml"],
)
def test_workflow_is_parseable_yaml(workflow_path: Path) -> None:
    """Every changed workflow file must remain valid YAML after this PR."""
    doc = _load_yaml(workflow_path)
    assert doc is not None


@pytest.mark.parametrize(
    "workflow_path",
    [AUTOFIX_YML, PRE_COMMIT_LITE_YML, REUSABLE_TESTS_YML],
    ids=["autofix.yml", "pre-commit-ci-lite.yml", "reusable-python-tests.yml"],
)
def test_workflow_does_not_contain_critical_module_minimum_percent(
    workflow_path: Path,
) -> None:
    """No changed workflow should pass --critical-module-minimum-percent."""
    source = workflow_path.read_text(encoding="utf-8")
    assert "--critical-module-minimum-percent" not in source, (
        f"{workflow_path.name} must not contain --critical-module-minimum-percent "
        "(removed from CI in this PR)"
    )


# ---------------------------------------------------------------------------
# Regression: old ruff commands without explicit '.' must not be present
# ---------------------------------------------------------------------------


def test_autofix_yml_no_bare_ruff_check_without_dot() -> None:
    """autofix.yml must not contain bare 'ruff check --fix' (without '.' target).

    The old form 'ruff check --fix' is replaced by 'ruff check . --fix || true'.
    This test prevents regression to the old bare form.
    """
    source = AUTOFIX_YML.read_text(encoding="utf-8")
    lines = source.splitlines()
    for line in lines:
        stripped = line.strip()
        # Match bare 'ruff check --fix' without a path token before '--'
        if stripped.startswith("ruff check --fix"):
            pytest.fail(
                f"autofix.yml contains bare 'ruff check --fix' without '.': {line!r}"
            )


def test_pre_commit_lite_yml_no_bare_ruff_check_without_dot() -> None:
    """pre-commit-ci-lite.yml must not use bare 'ruff check' without '.' target."""
    source = PRE_COMMIT_LITE_YML.read_text(encoding="utf-8")
    lines = source.splitlines()
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("ruff check --fix"):
            pytest.fail(
                f"pre-commit-ci-lite.yml contains bare 'ruff check' without '.': {line!r}"
            )


def test_autofix_yml_no_bare_ruff_format_without_dot() -> None:
    """autofix.yml must not contain bare 'ruff format' without '.' target."""
    source = AUTOFIX_YML.read_text(encoding="utf-8")
    lines = source.splitlines()
    for line in lines:
        stripped = line.strip()
        # 'ruff format' followed immediately by end-of-line or || (no path arg)
        if stripped in {"ruff format", "ruff format || true"}:
            pytest.fail(
                f"autofix.yml contains bare 'ruff format' without '.': {line!r}"
            )


def test_pre_commit_lite_yml_no_bare_ruff_format_without_dot() -> None:
    """pre-commit-ci-lite.yml must not contain bare 'ruff format' without '.' target."""
    source = PRE_COMMIT_LITE_YML.read_text(encoding="utf-8")
    lines = source.splitlines()
    for line in lines:
        stripped = line.strip()
        if stripped in {"ruff format", "ruff format || true"}:
            pytest.fail(
                f"pre-commit-ci-lite.yml contains bare 'ruff format' without '.': {line!r}"
            )
