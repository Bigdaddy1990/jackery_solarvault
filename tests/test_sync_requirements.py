"""Tests for the requirements synchronization diagnostics."""

from pathlib import Path

import pytest
from scripts import sync_requirements
from scripts.sync_requirements import show_diff


def test_show_diff_reports_added_and_removed_requirements(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """A failed requirements check explains every detected difference."""
    assert show_diff(
        "requirements.txt",
        ["old-package==1", "shared-package>=2"],
        ["new-package==3", "shared-package>=2"],
    )

    assert capsys.readouterr().out.splitlines() == [
        "requirements.txt: + new-package==3",
        "requirements.txt: - old-package==1",
    ]


def test_show_diff_is_silent_when_requirements_match(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """An in-sync requirements file produces no diagnostic noise."""
    assert not show_diff("requirements.txt", ["package>=1"], ["package>=1"])

    assert capsys.readouterr().out == ""


def test_show_diff_ignores_indented_comments(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Whitespace before a comment cannot create an empty requirement diff."""
    assert not show_diff("requirements.txt", ["  # pinned by HA"], [])

    assert capsys.readouterr().out == ""


def test_main_reports_the_existing_hyphenated_requirements_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """The check names the same requirements-test file it inspected."""
    manifest = tmp_path / "custom_components" / "jackery_solarvault" / "manifest.json"
    manifest.parent.mkdir(parents=True)
    manifest.write_text('{"requirements": []}', encoding="utf-8")
    (tmp_path / "requirements-test.txt").write_text("old-package\n", encoding="utf-8")
    monkeypatch.setattr(sync_requirements, "ROOT", tmp_path)
    monkeypatch.setattr(sync_requirements, "ALWAYS_TEST", ["new-package"])

    assert sync_requirements.main(["--check"]) == 1

    output = capsys.readouterr().out.splitlines()
    assert "requirements-test.txt: + new-package" in output
    assert "requirements-test.txt: - old-package" in output
    assert all(not line.startswith("requirements_test.txt:") for line in output)
