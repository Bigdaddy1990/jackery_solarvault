"""Fail when test files contain TODO markers."""

import argparse
from pathlib import Path
import re


def _iter_test_files(tests_dir: Path) -> list[Path]:
    return sorted(path for path in tests_dir.rglob("*.py") if path.is_file())


def _find_todos(test_file: Path) -> list[tuple[int, str]]:
    findings: list[tuple[int, str]] = []
    todo_pattern = re.compile(r"\bTODO\b", flags=re.IGNORECASE)
    for line_number, line in enumerate(
        test_file.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if todo_pattern.search(line) is None:
            continue
        findings.append((line_number, line.strip()))
    return findings


def main() -> int:  # noqa: D103
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--tests-dir",
        default=Path("tests"),
        type=Path,
        help="directory that contains pytest files",
    )
    args = parser.parse_args()

    if not args.tests_dir.is_dir():
        raise SystemExit(f"tests directory not found: {args.tests_dir}")

    violations: list[str] = []
    for test_file in _iter_test_files(args.tests_dir):
        for line_number, line in _find_todos(test_file):
            violations.append(f"{test_file}:{line_number}: {line}")

    if violations:
        print("Found forbidden TODO markers in test files:")
        for violation in violations:
            print(f"- {violation}")
        return 1

    print(f"No TODO markers found in test files under {args.tests_dir}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
