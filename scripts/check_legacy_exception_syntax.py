"""Detect legacy Python 2 multi-exception syntax.

This guard prevents regressions like ``except TypeError, ValueError:`` that break
Python 3 AST parsing and hassfest validation.
"""

import argparse
from pathlib import Path
import re

LEGACY_EXCEPT_PATTERN = re.compile(
    r"^(?P<indent>\s*)except\s+(?!\()"
    r"(?P<exceptions>[^:\n]+?,\s*[^:\n]+?)"
    r"\s*:\s*(?P<comment>#.*)?$",
)


def _iter_python_files(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*.py") if path.is_file())


def _find_legacy_handlers(path: Path) -> list[int]:
    line_numbers: list[int] = []
    for index, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if LEGACY_EXCEPT_PATTERN.match(line):
            line_numbers.append(index)
    return line_numbers


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "paths",
        nargs="*",
        default=["custom_components/jackery_solarvault"],
        help="Directories to scan for Python files (default: custom_components/jackery_solarvault)",
    )
    return parser.parse_args()


def main() -> int:  # noqa: D103
    args = _parse_args()
    findings: list[tuple[Path, int]] = []

    for raw_path in args.paths:
        root = Path(raw_path)
        if root.is_file() and root.suffix == ".py":
            for line_no in _find_legacy_handlers(root):
                findings.append((root, line_no))
            continue

        if not root.exists():
            print(f"[check_legacy_exception_syntax] Skipping missing path: {root}")
            continue

        for path in _iter_python_files(root):
            for line_no in _find_legacy_handlers(path):
                findings.append((path, line_no))

    if not findings:
        print("No legacy multi-exception syntax found.")
        return 0

    print("Legacy Python 2 multi-exception syntax detected:")
    for path, line_no in findings:
        print(f" - {path}:{line_no}")
    print("Use Python 3 tuple syntax, e.g. `except (TypeError, ValueError):`.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
