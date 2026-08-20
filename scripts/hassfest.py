"""Small offline manifest validator used when the official action is unavailable."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REQUIRED_MANIFEST_KEYS = frozenset(
    {
        "codeowners",
        "config_flow",
        "documentation",
        "domain",
        "integration_type",
        "iot_class",
        "issue_tracker",
        "name",
        "requirements",
        "version",
    }
)


def _validate_manifest(path: Path) -> list[str]:
    """Return actionable errors for a manifest."""
    if not path.is_file():
        return [f"{path}: manifest.json is missing"]
    try:
        manifest: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as err:
        return [f"{path}: invalid JSON: {err}"]
    if not isinstance(manifest, dict):
        return [f"{path}: manifest root must be an object"]
    return [
        f"{path}: missing required key {key!r}"
        for key in sorted(REQUIRED_MANIFEST_KEYS - manifest.keys())
    ]


def run(argv: list[str] | None = None) -> int:
    """Validate the integration resources."""
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--integration-path",
        type=Path,
        default=Path("custom_components/jackery_solarvault"),
    )
    integration = parser.parse_args(argv).integration_path
    errors = _validate_manifest(integration / "manifest.json")
    for relative, label in (
        ("translations", "translations directory"),
        ("strings.json", "strings.json"),
    ):
        if not (integration / relative).exists():
            errors.append(f"{integration / relative}: {label} is missing")
    for error in errors:
        print(error, file=sys.stderr)
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(run())
