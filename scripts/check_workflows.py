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
    """Collects all workflow `run` command strings from a parsed YAML document.

    Traverses the given YAML node (which may be a mapping, sequence, or scalar) and returns every string value found under a `run` key anywhere in the structure.

    Parameters:
        node (Any): A YAML-parsed value (e.g., dict, list, or scalar) to search for `run` command strings.

    Returns:
        list[str]: A list of `run` command strings found in the document (empty if none).
    """
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
    r"""Convert a script reference from a workflow `run` command into a filesystem path under the repository root.

    Parameters:
        ref (str): Script reference extracted from a `run` command. Expected forms are either dot-separated module style like `scripts.module.submodule` or path-like strings such as `scripts/path/to/script.py` (may use `\` or `/`).

    Returns:
        Path: Filesystem path under the repository root pointing to the referenced Python script.
    """
    if ref.startswith("scripts."):
        return ROOT / (ref.replace(".", "/") + ".py")
    return ROOT / ref.replace("\\", "/")


def validate_yaml(path: Path) -> None:
    """Validate a workflow YAML file and ensure any referenced scripts exist.

    Parses the YAML document at `path`, fails if the document is empty, and for every `run` command found in the document verifies that any referenced script (matching the configured `SCRIPT_REF_RE`) resolves to an existing file under the repository root.

    Parameters:
        path (Path): Filesystem path to the YAML file to validate.

    Raises:
        ValueError: If the YAML document is empty or if a referenced script file does not exist.
        OSError: Propagates I/O errors raised while opening the file.
        yaml.YAMLError: Propagates YAML parsing errors.
    """
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
    """Validate all repository automation YAML files and report any validation failures.

    Runs validation for each path in `YAML_FILES`, collects errors, and prints each failure to stderr when present. If no failures are found, prints a success summary to stdout.

    Returns:
        int: Exit code — `0` when all files validate successfully, `1` when one or more files failed validation.
    """
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
