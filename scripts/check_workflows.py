"""Validate GitHub workflow and repository automation YAML files."""

from pathlib import Path
import re
import sys
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[1]
YAML_FILES = [
    *sorted((ROOT / ".github" / "workflows").glob("*.*")),
    ROOT / ".github" / "dependabot.yml",
    ROOT / ".github" / "labeler.yml",
]
SCRIPT_REF_RE = re.compile(
    r"(?P<ref>scripts[\\/][A-Za-z0-9_.-]+\.py|scripts\.[A-Za-z0-9_.]+)"
)


def _iter_run_commands(node: Any) -> list[str]:
    """Return all workflow ``run`` command strings from a YAML document."""
    if isinstance(node, dict):
        commands: list[str] = []
        for key, value in node.items():
            if key == "run" and isinstance(value, str):
                commands.append(value)
            else:
                commands.extend(_iter_run_commands(value))
        return commands
    if isinstance(node, list):
        commands = []
        for item in node:
            commands.extend(_iter_run_commands(item))
        return commands
    return []


def _script_path_from_ref(ref: str) -> Path:
    """Return the repository path for a script reference in a run command."""
    if ref.startswith("scripts."):
        return ROOT / (ref.replace(".", "/") + ".py")
    return ROOT / ref.replace("\\", "/")


def validate_yaml(path: Path) -> None:
    """Load a YAML file and fail if it is empty or invalid."""
    with path.open(encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if document is None:
        raise ValueError(f"{path.relative_to(ROOT)} is empty")
    for command in _iter_run_commands(document):
        for match in SCRIPT_REF_RE.finditer(command):
            script_path = _script_path_from_ref(match.group("ref"))
            if not script_path.exists():
                raise ValueError(
                    f"references missing script {script_path.relative_to(ROOT)}"
                )


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
