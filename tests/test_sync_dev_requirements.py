"""Regression coverage for project versus pre-commit environment selection."""

import subprocess
from typing import TYPE_CHECKING

import pytest
from scripts import sync_dev_requirements

if TYPE_CHECKING:
    from pathlib import Path


def test_sync_targets_project_despite_hook_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An activated hook environment must never receive project dependencies."""
    environment = tmp_path / ".venv"
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "hook-sandbox"))
    monkeypatch.setattr(sync_dev_requirements, "ROOT", tmp_path)
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(sync_dev_requirements.subprocess, "run", run)
    sync_dev_requirements.main()
    expected_call_count = 2
    assert len(calls) == expected_call_count
    install = calls[-1]
    assert "--exact" in install
    assert install[install.index("--python") + 1] == str(environment)
    assert "requirements.txt" in install
    assert "requirements-test.txt" in install


@pytest.mark.parametrize("broken", [False, True])
def test_sync_recreates_unsupported_or_missing_interpreter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, broken: bool
) -> None:
    """Unsupported and missing interpreters must trigger environment replacement."""
    monkeypatch.setattr(sync_dev_requirements, "ROOT", tmp_path)
    calls: list[list[str]] = []

    def run(command: list[str], **kwargs: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        if "-c" in command:
            if broken:
                raise FileNotFoundError
            return subprocess.CompletedProcess(command, 1)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(sync_dev_requirements.subprocess, "run", run)
    sync_dev_requirements.main()
    assert calls[1][3:] == [
        "venv",
        "--python",
        ">=3.14.2,<3.15",
        str(tmp_path / ".venv"),
    ]
    assert calls[2][calls[2].index("--python") + 1] == str(tmp_path / ".venv")
