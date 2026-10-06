"""Dependency gates explain missing imports and cover direct test libraries."""

from typing import TYPE_CHECKING

from scripts import enforce_test_requirements as gate

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_missing_import_is_reported_with_its_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """An actionable failing gate names the source file and undeclared library."""
    tests = tmp_path / "tests"
    tests.mkdir()
    source = tests / "test_missing.py"
    source.write_text("import missing_library\n", encoding="utf-8")
    requirements = tmp_path / "requirements-test.txt"
    requirements.write_text("pytest\n", encoding="utf-8")
    monkeypatch.setattr(gate, "TESTS_ROOT", tests)
    monkeypatch.setattr(gate, "REQUIREMENT_FILES", [requirements])
    monkeypatch.setattr(gate.sys, "argv", ["enforce_test_requirements"])
    assert gate.main() == 1
    captured = capsys.readouterr()
    assert "missing_library" in captured.err
    assert str(source) in captured.err


def test_declared_import_passes_without_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The diagnostic repair does not turn correctly declared imports into errors."""
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_declared.py").write_text(
        "import example_library\n", encoding="utf-8"
    )
    requirements = tmp_path / "requirements-test.txt"
    requirements.write_text("example-library>=1\n", encoding="utf-8")
    monkeypatch.setattr(gate, "TESTS_ROOT", tests)
    monkeypatch.setattr(gate, "REQUIREMENT_FILES", [requirements])
    monkeypatch.setattr(gate.sys, "argv", ["enforce_test_requirements"])
    assert gate.main() == 0
    assert not capsys.readouterr().err


def test_direct_crypto_test_import_is_declared() -> None:
    """Core owns runtime crypto, but the API tests directly require it as well."""
    assert "cryptography" in gate._parse_requirements()  # ruff: ignore[private-member-access]
