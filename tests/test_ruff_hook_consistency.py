"""Regression coverage for destructive mixed-version Ruff fixing passes."""

import json
from pathlib import Path
import subprocess
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_autofix_uses_one_ruff_hook_environment() -> None:
    """Fixing and verification must share Ruff without a stale version pin."""
    config = yaml.safe_load((ROOT / ".pre-commit-config.yaml").read_text())
    hooks = {
        hook["id"]: (repository, hook)
        for repository in config["repos"]
        for hook in repository["hooks"]
        if hook["id"] in {"ruff-check", "ruff-format"}
    }
    assert set(hooks) == {"ruff-check", "ruff-format"}
    dependencies = []
    for repository, hook in hooks.values():
        assert repository["repo"] == "local"
        assert hook["language"] == "python"
        dependencies.append(hook["additional_dependencies"])
    assert dependencies == [["ruff>=0.17.0"], ["ruff>=0.17.0"]]
    workflow = yaml.safe_load((ROOT / ".github/workflows/autofix.yml").read_text())
    steps = workflow["jobs"]["autofix"]["steps"]
    commands = "\n".join(step.get("run", "") for step in steps)
    assert "ruff check" not in commands
    assert "pyrefly infer" not in commands
    assert not any("ruff-action" in step.get("uses", "") for step in steps)
    assert "pre-commit run --all-files || true" in commands
    assert "python -m pre_commit run --all-files" in commands
    assert "python -m pre_commit clean" in commands
    for filename in ("autofix.yml", "pre-commit-ci-lite.yml"):
        other = yaml.safe_load((ROOT / ".github/workflows" / filename).read_text())
        for job in other["jobs"].values():
            assert not any(
                "pre-commit/action" in step.get("uses", "") for step in job["steps"]
            )
            hook_commands = "\n".join(step.get("run", "") for step in job["steps"])
            assert hook_commands.index("python -m pre_commit clean") < (
                hook_commands.index("run --all-files")
            )
    validate = (ROOT / ".github/workflows/validate.yml").read_text()
    assert "ruff check --no-fix custom_components/ tests/" in validate
    verify = next(
        index
        for index, step in enumerate(steps)
        if step.get("run") == "python -m pre_commit run --all-files"
    )
    publish = next(
        index
        for index, step in enumerate(steps)
        if "pre-commit-ci/lite-action" in step.get("uses", "")
    )
    assert verify < publish
    assert "if" not in steps[publish]


def test_ruff_fixing_preserves_needed_suppression_and_private_access_check() -> None:
    """RUF100 stays enabled without erasing required private access ignores."""
    source = (
        "def access(obj):\n"
        "    return obj._value  # ruff: ignore[private-member-access]\n"
    )
    command = [
        sys.executable,
        "-m",
        "ruff",
        "check",
        "--isolated",
        "--preview",
        "--select=SLF001,RUF100",
        "--fix",
        "--output-format=json",
        "-",
    ]
    for _ in range(2):
        result = subprocess.run(
            command,
            input=source,
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert result.stdout == source
    unguarded = subprocess.run(
        [*command[:-1], "--no-fix", "-"],
        input=source.split("  # ruff:", 1)[0] + "\n",
        capture_output=True,
        text=True,
        check=False,
    )
    assert unguarded.returncode == 1
    assert json.loads(unguarded.stdout)[0]["code"] == "SLF001"


def test_ble_regressions_have_no_unused_ruff_suppressions() -> None:
    """The original failing file passes the real repository lint configuration."""
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "--no-fix", "tests/test_ble_frame.py"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
