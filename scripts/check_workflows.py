"""Validate GitHub workflow and repository automation YAML files."""

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
    """
    Load and validate a YAML file, raising if the file is empty or contains invalid YAML.
    
    Raises:
        ValueError: if the YAML document is empty.
        OSError: if the file cannot be read.
        yaml.YAMLError: if the file contains invalid YAML.
    """
    with path.open(encoding="utf-8") as stream:
        document = yaml.safe_load(stream)
    if document is None:
        raise ValueError(f"{path.relative_to(ROOT)} is empty")


def main() -> int:
    """
    Validate all repository automation YAML files and report any validation failures.
    
    Prints validation failure messages to stderr when one or more files fail validation; prints a success message when all files validate.
    
    Returns:
        int: 0 if all files validated successfully, 1 if one or more files failed validation.
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
