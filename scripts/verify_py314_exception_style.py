"""Guard Python 3.14 exception formatting after Ruff baseline.

Python 3.14 allows multi-exception ``except`` and ``except*`` headers without
parentheses when no ``as`` binding is present. Ruff 0.15+ formats those headers
to the Python 3.14 style when its target version is ``py314``. This guard keeps
the web-only Ruff baseline workflow from silently reverting to the older
parenthesized form if the workflow target or Ruff version drifts.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_PARTS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "__pycache__",
    "venv",
}

_EXCEPT_PAREN_START = re.compile(r"^\s*except\*?\s*\(")


def _python_files(root: Path = ROOT) -> Iterable[Path]:
    """Yield repository Python files that should be checked."""
    for path in root.rglob("*.py"):
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        yield path


def _header_until_colon(lines: list[str], start_index: int) -> str:
    """Return an ``except`` header, including continuation lines when needed."""
    header_lines = [lines[start_index]]
    paren_depth = (
        lines[start_index].count("(")
        - lines[start_index].count(")")
    )
    index = start_index
    while paren_depth > 0 and index + 1 < len(lines):
        index += 1
        header_lines.append(lines[index])
        paren_depth += lines[index].count("(") - lines[index].count(")")
        if ":" in lines[index] and paren_depth <= 0:
            break
    return "\n".join(header_lines)


def _is_parenthesized_multi_exception_without_as(header: str) -> bool:
    """Return True for old-style multi-exception headers Ruff should modernize."""
    # Strip inline comments to avoid comment-only commas or ``as`` false positives.
    header_without_comments = "\n".join(
        line.split("#", 1)[0] for line in header.splitlines()
    )
    before_colon = header_without_comments.split(":", 1)[0]
    if " as " in before_colon:
        return False
    return "," in before_colon


def violations_in_text(source: str) -> list[tuple[int, str]]:
    """Return old-style Python 3.14 exception formatting violations."""
    lines = source.splitlines()
    violations: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if not _EXCEPT_PAREN_START.match(line):
            continue
        header = _header_until_colon(lines, index)
        if _is_parenthesized_multi_exception_without_as(header):
            violations.append((index + 1, header.strip()))
    return violations


def main() -> int:
    """Check all repository Python files."""
    violations: list[str] = []
    for path in _python_files():
        for line_number, header in violations_in_text(path.read_text(encoding="utf-8")):
            rel_path = path.relative_to(ROOT)
            violations.append(f"{rel_path}:{line_number}: {header}")

    if violations:
        print(
            "Python 3.14 exception style drift detected. "
            "Run Ruff with --target-version py314 so multi-exception headers "
            "without an 'as' binding stay unparenthesized.",
            file=sys.stderr,
        )
        for item in violations:
            print(item, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
