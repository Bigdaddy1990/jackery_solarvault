"""Guard and repair Python 3.14 exception formatting after Ruff autofix.

Python 3.14 allows multi-exception ``except`` and ``except*`` headers without
parentheses when no ``as`` binding is present. Ruff formats those headers to the
Python 3.14 style only when the target version, formatter support, and line
length all allow a single-line header.

This guard prevents unnecessary drift back to ``except (A, B):`` while staying
compatible with ``ruff format --check``: parenthesized multi-line headers are
accepted when the Python 3.14 one-line form would exceed Ruff's configured line
length.
"""

import argparse
from collections.abc import Iterable
from pathlib import Path
import re
import sys
import tomllib

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_LINE_LENGTH = 88
DEFAULT_PATHS = ("custom_components",)
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


def _configured_line_length() -> int:
    """
    Determine the Ruff `tool.ruff.line-length` value from pyproject.toml.
    
    Reads pyproject.toml at the repository root and returns the configured `tool.ruff.line-length` when it exists and is greater than zero. If the file cannot be read, is invalid TOML, or the setting is missing or not a positive integer, returns DEFAULT_LINE_LENGTH.
    """
    pyproject = ROOT / "pyproject.toml"
    try:
        config = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except OSError, tomllib.TOMLDecodeError:
        return DEFAULT_LINE_LENGTH
    value = config.get("tool", {}).get("ruff", {}).get("line-length")
    return value if isinstance(value, int) and value > 0 else DEFAULT_LINE_LENGTH


def _iter_roots(paths: Iterable[str]) -> Iterable[Path]:
    """Yield existing file or directory roots from CLI path arguments."""
    for raw_path in paths:
        path = Path(raw_path)
        if not path.is_absolute():
            path = ROOT / path
        if path.exists():
            yield path


def _python_files(paths: Iterable[str] = DEFAULT_PATHS) -> Iterable[Path]:
    """
    Iterate Python (.py) files under the given paths, skipping excluded directory parts.
    
    Parameters:
    	paths (Iterable[str]): Files or directories to search. Relative paths are resolved under the repository root; defaults to DEFAULT_PATHS.
    
    Returns:
    	Iterator[Path]: Paths to discovered `.py` files that do not contain any component from EXCLUDED_PARTS.
    """
    for root in _iter_roots(paths):
        candidates = [root] if root.is_file() else root.rglob("*.py")
        for path in candidates:
            if path.suffix != ".py":
                continue
            if any(part in EXCLUDED_PARTS for part in path.parts):
                continue
            yield path


def _header_until_colon(lines: list[str], start_index: int) -> tuple[int, str]:
    """
    Collect the except/except* header block beginning at start_index.
    
    Tracks parentheses depth across following lines until the header's terminating colon is reached (a line containing ':' when parentheses depth is zero or less) or the end of the input is reached. Returns a tuple (last_index, header_text) where last_index is the index of the final line included and header_text is the header lines joined with '\n'.
    """
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
    """
    Extract the portion of an `except`/`except*` header before the first colon, with inline comments removed.
    
    Parameters:
        header (str): Raw header text (possibly multi-line) that may include comments and a trailing colon/body.
    
    Returns:
        str: The header content up to (but not including) the first `:`, with any `#`-started comments removed.
    """
    header_without_comments = "\n".join(
        line.split("#", 1)[0] for line in header.splitlines()
    )
    return header_without_comments.split(":", 1)[0]


def _is_parenthesized_multi_exception_without_as(header: str) -> bool:
    """
    Detect whether an except header uses a parenthesized comma-separated exception list without an `as` clause.
    
    Returns:
        True if the header contains multiple exceptions separated by commas and does not include an `as` clause, False otherwise.
    """
    before_colon = _header_before_colon_without_comments(header)
    if " as " in before_colon:
        return False
    return "," in before_colon


def _exception_items_from_header(header: str) -> list[str]:
    """
    Extract individual exception expressions from a parenthesized multi-exception except header.
    
    Returns:
        list[str]: Stripped exception expressions if `header` is of the form `except(...)` or `except*(...)`; otherwise an empty list.
    """
    before_colon = _header_before_colon_without_comments(header).strip()
    match = re.match(r"^(except\*?)\s*\((?P<body>.*)\)\s*$", before_colon, re.S)
    if match is None:
        return []
    body = match.group("body")
    return [item.strip() for item in body.split(",") if item.strip()]


def _fixed_header(header: str, *, line_length: int) -> str | None:
    """
    Produce a Python 3.14-style except header when the header can be represented unparenthesized within the given line length.
    
    Only returns a replacement when the original header is a parenthesized multi-exception form without `as` and the rewritten single-line header length is less than or equal to `line_length`.
    
    Parameters:
        line_length (int): Maximum allowed length for the rewritten header.
    
    Returns:
        str | None: The rewritten header string (including the trailing ':'), or `None` if no valid replacement is applicable.
    """
    if not _is_parenthesized_multi_exception_without_as(header):
        return None
    first_line = header.splitlines()[0]
    match = _EXCEPT_PAREN_START.match(first_line)
    if match is None:
        return None
    items = _exception_items_from_header(header)
    if len(items) < 2:
        return None

    replacement = f"{match.group('indent')}{match.group('keyword')} {', '.join(items)}:"
    if len(replacement) > line_length:
        return None
    return replacement


def violations_in_text(
    source: str,
    *,
    line_length: int | None = None,
) -> list[tuple[int, str]]:
    """Return unnecessary old-style Python 3.14 exception formatting violations."""
    if line_length is None:
        line_length = _configured_line_length()
    lines = source.splitlines()
    violations: list[tuple[int, str]] = []
    for index, line in enumerate(lines):
        if not _EXCEPT_PAREN_START.match(line):
            continue
        _, header = _header_until_colon(lines, index)
        if _fixed_header(header, line_length=line_length) is not None:
            violations.append((index + 1, header.strip()))
    return violations


def fix_text(
    source: str,
    *,
    line_length: int | None = None,
) -> str:
    """
    Rewrite old-style parenthesized multi-exception `except` / `except*` headers to the Python 3.14 single-line unparenthesized form where a safe, line-length-compliant replacement exists.
    
    Parameters:
    	source (str): The original file text to process.
    	line_length (int | None): Maximum allowed line length for replacements. If None, reads Ruff's configured `tool.ruff.line-length` (fallback to module default) and uses that value to decide whether a single-line replacement is allowed.
    
    Returns:
    	str: The resulting source text with fixable exception headers replaced. Original line endings (CRLF vs LF) are preserved for each replaced header; text with no applicable changes is returned unchanged.
    """
    if line_length is None:
        line_length = _configured_line_length()
    lines = source.splitlines(keepends=True)
    plain_lines = [line.rstrip("\r\n") for line in lines]
    fixed: list[str] = []
    index = 0
    while index < len(lines):
        line_without_newline = plain_lines[index]
        if not _EXCEPT_PAREN_START.match(line_without_newline):
            fixed.append(lines[index])
            index += 1
            continue

        end_index, header = _header_until_colon(plain_lines, index)
        replacement = _fixed_header(header, line_length=line_length)
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


def _fix_file(path: Path, *, line_length: int) -> bool:
    """
    Rewrite the file at `path` if any fixable Python 3.14 exception-header drifts are found.
    
    Reads the file, attempts to rewrite fixable parenthesized multi-exception `except`/`except*` headers using `fix_text`, and overwrites the file only when changes are produced.
    
    Parameters:
        path (Path): Path to the Python file to inspect and potentially modify.
        line_length (int): Maximum allowed line length used to decide whether a header can be rewritten.
    
    Returns:
        bool: `True` if the file was modified and written back, `False` if no changes were necessary.
    """
    source = path.read_text(encoding="utf-8")
    fixed = fix_text(source, line_length=line_length)
    if fixed == source:
        return False
    path.write_text(fixed, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    """
    Run the CLI that detects and optionally rewrites Python 3.14 multi-exception header style drift in repository files.
    
    Parameters:
        argv (list[str] | None): Optional list of command-line arguments to parse; when None, sys.argv[1:] is used.
    
    Returns:
        exit_code (int): Process exit code: `0` if no violations remain, `1` if any violations were found.
    """
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix", action="store_true")
    parser.add_argument(
        "--line-length",
        type=int,
        default=_configured_line_length(),
        help="Maximum formatted line length. Defaults to Ruff's pyproject setting.",
    )
    parser.add_argument(
        "paths",
        nargs="*",
        default=list(DEFAULT_PATHS),
        help="Files or directories to check. Defaults to custom_components/.",
    )
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)

    changed: list[str] = []
    if args.fix:
        for path in _python_files(args.paths):
            if _fix_file(path, line_length=args.line_length):
                changed.append(str(path.relative_to(ROOT)))
        if changed:
            print("Rewrote Python 3.14 exception headers:")
            for item in changed:
                print(f"  {item}")

    violations: list[str] = []
    for path in _python_files(args.paths):
        source = path.read_text(encoding="utf-8")
        for line_number, header in violations_in_text(
            source,
            line_length=args.line_length,
        ):
            rel_path = path.relative_to(ROOT)
            violations.append(f"{rel_path}:{line_number}: {header}")

    if violations:
        print(
            "Python 3.14 exception style drift detected. "
            "Run Ruff with --target-version py314 and the local --fix guard so "
            "multi-exception headers without an 'as' binding stay unparenthesized "
            "when they fit within Ruff's line length.",
            file=sys.stderr,
        )
        for item in violations:
            print(item, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
