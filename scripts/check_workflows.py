"""Validate GitHub workflow and repository automation YAML files."""

from __future__ import annotations

from pathlib import Path
import sys

import yaml

ROOT = Path(__file__).resolve().parents[1]
YAML_FILES = [
    *sorted((ROOT / ".github" / "workflows").glob("*.*")),
    ROOT / ".github" / "dependabot.yml",
    ROOT / ".github" / "labeler.yml",
]


def validate_yaml(path: Path) -> None:
    """Load a YAML file and fail if it is empty or invalid."""
    with path.open(encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if document is None:
        raise ValueError(f"{path.relative_to(ROOT)} is empty")


def main() -> int:
    """Validate all repository automation YAML files."""
    failures: list[str] = []
    for path in YAML_FILES:
        try:
            validate_yaml(path)
        except (OSError, ValueError, yaml.YAMLError) as err:
            failures.append(f"{path.relative_to(ROOT)}: {err}")
    if failures:
        print("\n".join(failures), file=sys.stderr)
        return 1
    print(f"Validated {len(YAML_FILES)} automation YAML files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
