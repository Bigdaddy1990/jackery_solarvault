"""Contracts for the repository's offline hassfest shim."""

import json
from pathlib import Path
from typing import Any

from scripts.hassfest import (
    _validate_manifest,  # ruff: ignore[import-private-name] — offline-validator ist bewusst privat; nur der Test nutzt ihn
    run,
)

_INTEGRATION_PATH = (
    Path(__file__).resolve().parents[1] / "custom_components" / "jackery_solarvault"
)


def _manifest(**overrides: Any) -> dict[str, Any]:
    """Return the smallest manifest accepted by the offline validator."""
    manifest: dict[str, Any] = {
        "codeowners": ["@owner"],
        "config_flow": True,
        "documentation": "https://example.invalid/docs",
        "domain": "jackery_solarvault",
        "integration_type": "hub",
        "iot_class": "cloud_polling",
        "issue_tracker": "https://example.invalid/issues",
        "loggers": ["aiomqtt"],
        "name": "Jackery SolarVault",
        "quality_scale": "custom",
        "requirements": ["aiomqtt>=2.5.0"],
        "version": "0.1.1",
    }
    manifest.update(overrides)
    return manifest


def test_manifest_loggers_accept_external_dependency_loggers(tmp_path: Path) -> None:
    """HA's loggers field must not require the integration's own module logger."""
    integration_path = tmp_path / "jackery_solarvault"
    integration_path.mkdir()
    manifest_path = integration_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest()), encoding="utf-8")

    assert _validate_manifest(manifest_path) == []


def test_project_manifest_passes_offline_validation() -> None:
    """The checked-in manifest must satisfy the repository's offline validator."""
    assert _validate_manifest(_INTEGRATION_PATH / "manifest.json") == []


def test_hassfest_run_reports_each_validation_error(
    tmp_path: Path,
    capsys: Any,
) -> None:
    """A failing shim must expose actionable errors instead of exiting silently."""
    integration_path = tmp_path / "missing_integration"

    assert run(["--integration-path", str(integration_path)]) == 1

    stderr = capsys.readouterr().err
    assert "manifest.json is missing" in stderr
    assert "translations directory is missing" in stderr
    assert "strings.json is missing" in stderr
