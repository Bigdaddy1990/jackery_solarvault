"""Guard and repair Python 3.14 exception formatting after Ruff baseline.

Python 3.14 allows multi-exception ``except`` and ``except*`` headers without
parentheses when no ``as`` binding is present. Ruff formats those headers to the
Python 3.14 style only when its target version and formatter support are aligned.
This guard keeps the web-only Ruff baseline workflow from silently reverting to
the older parenthesized form, and ``--fix`` repairs the common Ruff output when
the formatter leaves it behind.
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

_EXCEPT_PAREN_START = re.compile(r"^(?P<indent>\s*)(?P<keyword>except\*?)\s*\(")


def _python_files(root: Path = ROOT) -> Iterable[Path]:
    """Yield repository Python files that should be checked."""
    for path in root.rglob("*.py"):
        if any(part in EXCLUDED_PARTS for part in path.parts):
            continue
        yield path


def _header_until_colon(lines: list[str], start_index: int) -> tuple[int, str]:
    """Return the final line index and ``except`` header from ``start_index``."""
    header_lines = [lines[start_index]]
    paren_depth = lines[start_index].count("(") - lines[start_index].count(")")
    index = start_index
    while paren_depth > 0 and index + 1 < len(lines):
        index += 1
        header_lines.append(lines[index])
        paren_depth += lines[index].count("(") - lines[index].count(")")
        if ":" in lines[index] and paren_depth <= 0:
            break
    return index, "\n".join(header_lines)


def _header_before_colon_without_comments(header: str) -> str:
    """Return an ``except`` header without comments or the trailing colon/body."""
    header_without_comments = "\n".join(
        line.split("#", 1)[0] for line in header.splitlines()
    )
    return header_without_comments.split(":", 1)[0]


def _is_parenthesized_multi_exception_without_as(header: str) -> bool:
    """Return True for old-style multi-exception headers Ruff should modernize."""
    before_colon = _header_before_colon_without_comments(header)
    if " as " in before_colon:
        return False
    return "," in before_colon


def _exception_items_from_header(header: str) -> list[str]:
    """Extract exception expressions from a parenthesized multi-exception header."""
    before_colon = _header_before_colon_without_comments(header).strip()
    match = re.match(r"^(except\*?)\s*\((?P<body>.*)\)\s*$", before_colon, re.S)
    if match is None:
        return []
    body = match.group("body")
    return [item.strip() for item in body.split(",") if item.strip()]


def _fixed_header(header: str) -> str | None:
    """Return a Python 3.14-style header for a fixable old-style header."""
    if not _is_parenthesized_multi_exception_without_as(header):
        return None
    first_line = header.splitlines()[0]
    match = _EXCEPT_PAREN_START.match(first_line)
    if match is None:
        return None
    items = _exception_items_from_header(header)
    if len(items) < 2:
        return None
    return f"{match.group('indent')}{match.group('keyword')} {', '.join(items)}:"


def violations_in_text(source: str) -> list[tuple[int, str]]:
    """Return old-style Python 3.14 exception formatting violations."""
    lines = source.splitlines()
    violations: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if not _EXCEPT_PAREN_START.match(line):
            continue
        _, header = _header_until_colon(lines, index)
        if _is_parenthesized_multi_exception_without_as(header):
            violations.append((index + 1, header.strip()))
    return violations


def fix_text(source: str) -> str:
    """Rewrite old-style multi-exception headers to Python 3.14 style."""
    lines = source.splitlines(keepends=True)
    fixed: list[str] = []
    index = 0
    while index < len(lines):
        line_without_newline = lines[index].rstrip("\r\n")
        if not _EXCEPT_PAREN_START.match(line_without_newline):
            fixed.append(lines[index])
            index += 1
            continue

        plain_lines = [line.rstrip("\r\n") for line in lines]
        end_index, header = _header_until_colon(plain_lines, index)
        replacement = _fixed_header(header)
        if replacement is None:
            fixed.append(lines[index])
            index += 1
            continue

        newline = "\n"
        if lines[end_index].endswith("\r\n"):
            newline = "\r\n"
        fixed.append(f"{replacement}{newline}")
        index = end_index + 1

    return "".join(fixed)


def _fix_file(path: Path) -> bool:
    """Rewrite ``path`` when Python 3.14 exception formatting drift is present."""
    source = path.read_text(encoding="utf-8")
    fixed = fix_text(source)
    if fixed == source:
        return False
    path.write_text(fixed, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    """Check or fix all repository Python files."""
    args = sys.argv[1:] if argv is None else argv
    fix = args == ["--fix"]
    if args and not fix:
        print("Usage: verify_py314_exception_style.py [--fix]", file=sys.stderr)
        return 2

    changed: list[str] = []
    if fix:
        for path in _python_files():
            if _fix_file(path):
                changed.append(str(path.relative_to(ROOT)))
        if changed:
            print("Rewrote Python 3.14 exception headers:")
            for item in changed:
                print(f"  {item}")

    violations: list[str] = []
    for path in _python_files():
        for line_number, header in violations_in_text(path.read_text(encoding="utf-8")):
            rel_path = path.relative_to(ROOT)
            violations.append(f"{rel_path}:{line_number}: {header}")

    if violations:
        print(
            "Python 3.14 exception style drift detected. "
            "Run Ruff with --target-version py314 and the local --fix guard so "
            "multi-exception headers without an 'as' binding stay unparenthesized.",
            file=sys.stderr,
        )
        for item in violations:
            print(item, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
