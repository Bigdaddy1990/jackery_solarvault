"""Regression coverage for project versus pre-commit environment selection."""

from typing import TYPE_CHECKING

from scripts import sync_dev_requirements

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_sync_targets_project_despite_hook_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An activated hook environment must never receive project dependencies."""
    environment = tmp_path / ".venv"
    environment.mkdir()
    (environment / "pyvenv.cfg").touch()
    monkeypatch.setenv("VIRTUAL_ENV", str(tmp_path / "hook-sandbox"))
    monkeypatch.setattr(sync_dev_requirements, "ROOT", tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        sync_dev_requirements.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command),
    )
    sync_dev_requirements.main()
    assert len(calls) == 1
    assert "--exact" in calls[0]
    assert calls[0][calls[0].index("--python") + 1] == str(environment)
    assert "requirements.txt" in calls[0]
    assert "requirements-test.txt" in calls[0]


def test_sync_creates_missing_project_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fresh checkout creates its own environment before installation."""
    monkeypatch.setattr(sync_dev_requirements, "ROOT", tmp_path)
    calls: list[list[str]] = []
    monkeypatch.setattr(
        sync_dev_requirements.subprocess,
        "run",
        lambda command, **kwargs: calls.append(command),
    )
    sync_dev_requirements.main()
    assert calls[0][3:] == ["venv", "--python", "3.14", str(tmp_path / ".venv")]
    assert calls[1][calls[1].index("--python") + 1] == str(tmp_path / ".venv")
