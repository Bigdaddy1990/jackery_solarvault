"""Security regressions for isolated release imports and immutable ZIP contents."""

import os
from pathlib import Path
import subprocess
from typing import Any
from zipfile import ZipFile

import pytest
import yaml

_WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


@pytest.mark.parametrize("workflow_name", ["release.yml", "release-please.yml"])
def test_release_import_gate_is_isolated_from_write_privileged_publisher(
    workflow_name: str,
) -> None:
    """Dependency and integration execution finish in a separate read-only job."""
    workflow: dict[str, Any] = yaml.safe_load(
        (_WORKFLOWS / workflow_name).read_text(encoding="utf-8")
    )
    jobs: dict[str, Any] = workflow["jobs"]
    gate_name, gate = next(
        (name, job)
        for name, job in jobs.items()
        if any(
            "python -m scripts.check_integration_imports" in step.get("run", "")
            for step in job["steps"]
        )
    )
    publisher_name, publisher = next(
        (name, job)
        for name, job in jobs.items()
        if any(
            step.get("uses", "").startswith((
                "softprops/action-gh-release@",
                "googleapis/release-please-action@",
            ))
            for step in job["steps"]
        )
    )

    assert gate["permissions"] == {"contents": "read"}
    assert "if" not in gate
    assert gate.get("continue-on-error", False) is False
    gate_checkout = next(
        step
        for step in gate["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    assert gate_checkout["with"]["persist-credentials"] is False
    assert gate_checkout["with"]["ref"] == "${{ github.sha }}"
    assert any(
        "pip install -r requirements.txt -r requirements-test.txt"
        in step.get("run", "")
        for step in gate["steps"]
    )
    compile_step = next(
        step
        for step in gate["steps"]
        if "python -m scripts.check_integration_imports" in step.get("run", "")
    )
    assert "if" not in compile_step
    assert compile_step.get("continue-on-error", False) is False
    assert publisher_name != gate_name
    assert "if" not in publisher
    assert publisher.get("continue-on-error", False) is False
    needs = publisher["needs"]
    assert gate_name in ([needs] if isinstance(needs, str) else needs)
    publisher_checkout = next(
        step
        for step in publisher["steps"]
        if step.get("uses", "").startswith("actions/checkout@")
    )
    assert publisher_checkout["with"]["ref"] == "${{ github.sha }}"
    assert publisher["steps"][0] is publisher_checkout
    for step in publisher["steps"]:
        command = step.get("run", "")
        assert "pip install" not in command
        assert "uv sync" not in command
        assert "scripts.check_integration_imports" not in command
        assert "scripts.sync_ha_test_baseline" not in command
        assert "custom_components.jackery_solarvault" not in command
        assert not step.get("uses", "").startswith("actions/setup-python@")


def test_release_zip_contains_only_committed_integration_bytes(tmp_path: Path) -> None:
    """The real packaging step ignores modified and untracked workspace files."""
    workflow: dict[str, Any] = yaml.safe_load(
        (_WORKFLOWS / "release.yml").read_text(encoding="utf-8")
    )
    build_command = next(
        step["run"]
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if step.get("name") == "Build release zip"
    )
    environment = {
        **os.environ,
        "GIT_CONFIG_SYSTEM": os.devnull,
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GITHUB_WORKSPACE": str(tmp_path),
    }
    integration = tmp_path / "custom_components" / "jackery_solarvault"
    integration.mkdir(parents=True)
    source = integration / "__init__.py"
    committed_bytes = b'"""Committed integration source."""\n'
    source.write_bytes(committed_bytes)
    (tmp_path / "README.md").write_text("Outside integration\n", encoding="utf-8")
    for command in (
        ["git", "init", "--initial-branch=main", "--quiet"],
        ["git", "add", "."],
        [
            "git",
            "-c",
            "user.name=Release Regression",
            "-c",
            "user.email=release-regression@example.test",
            "commit",
            "--quiet",
            "-m",
            "Commit release input",
        ],
    ):
        subprocess.run(
            command,
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            check=True,
            timeout=10,
        )
    environment["GITHUB_SHA"] = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()
    source.write_bytes(b'raise RuntimeError("Mutated worktree")\n')
    (integration / "untracked.py").write_text(
        'raise RuntimeError("Untracked input")\n', encoding="utf-8"
    )

    subprocess.run(
        ["bash", "-e", "-c", build_command],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        check=True,
        timeout=10,
    )

    with ZipFile(tmp_path / "jackery_solarvault.zip") as archive:
        assert archive.namelist() == ["__init__.py"]
        assert archive.read("__init__.py") == committed_bytes
