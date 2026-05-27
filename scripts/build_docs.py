"""Build the Markdown files in docs/ into standalone HTML pages."""

import argparse
from collections.abc import Iterable
import difflib
import html
from pathlib import Path
import re
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
DOCS_DIR = ROOT / 'docs'
OUTPUT_DIR = DOCS_DIR / 'html'
_TITLE_RE = re.compile(r'^#\s+(.+?)\s*$')
_LINK_RE = re.compile(r'(?<!!)\[([^\]]+)\]\(([^)]+)\)')
_STRONG_RE = re.compile(r'\*\*([^*]+)\*\*')
_EM_RE = re.compile(r'(?<!\*)\*([^*]+)\*(?!\*)')
_CODE_RE = re.compile(r'`([^`]+)`')


def markdown_files() -> list[Path]:
    """List Markdown source files in the docs directory excluding generated output files.

    Searches DOCS_DIR for files matching "*.md" and returns only regular files that are not located in OUTPUT_DIR.

    Returns:
        list[Path]: Sorted list of Path objects pointing to Markdown source files.
    """
    return sorted(
        path
        for path in DOCS_DIR.glob('*.md')
        if path.is_file() and path.parent != OUTPUT_DIR
    )


def inline_markdown(value: str) -> str:
    """Render the small inline Markdown subset used by this repository."""
    escaped = html.escape(value, quote=False)
    escaped = _CODE_RE.sub(lambda match: f"<code>{match.group(1)}</code>", escaped)
    escaped = _STRONG_RE.sub(
        lambda match: f"<strong>{match.group(1)}</strong>", escaped
    )
    escaped = _EM_RE.sub(lambda match: f"<em>{match.group(1)}</em>", escaped)

    def link(match: re.Match[str]) -> str:
        label = match.group(1)
        href = html.escape(match.group(2), quote=True)
        return f'<a href="{href}">{label}</a>'

    return _LINK_RE.sub(link, escaped)


def close_list(parts: list[str], in_list: bool) -> bool:
    """Close an open unordered list in the HTML parts buffer.

    Parameters:
        parts (list[str]): Mutable list collecting HTML fragments; may be appended with a closing `</ul>` tag.
        in_list (bool): Whether an unordered list is currently open.

    Returns:
        `False`: Indicates the list is now closed.
    """
    if in_list:
        parts.append('</ul>')
    return False


def render_body(markdown: str) -> str:
    """Convert a Markdown document into a compact, deterministic HTML fragment.

    The renderer supports ATX headings (H1–H6), fenced code blocks using ``` (emitted as
    HTML-escaped `<pre><code>` blocks), unordered lists (`-` or `*`), paragraphs, and a
    small inline Markdown subset (inline code, bold, emphasis, and links). Output is a
    stable, minimal HTML fragment with elements separated by newline characters.

    Returns:
        html_fragment (str): The rendered HTML fragment.
    """
    parts: list[str] = []
    paragraph: list[str] = []
    in_list = False
    in_code = False
    code_lines: list[str] = []

    def flush_paragraph() -> None:
        nonlocal paragraph
        if paragraph:
            parts.append(f"<p>{inline_markdown(' '.join(paragraph))}</p>")
            paragraph = []

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        if line.startswith('```'):
            flush_paragraph()
            in_list = close_list(parts, in_list)
            if in_code:
                parts.append(
                    f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>"
                )
                code_lines = []
                in_code = False
            else:
                in_code = True
            continue
        if in_code:
            code_lines.append(raw_line)
            continue
        if not line.strip():
            flush_paragraph()
            in_list = close_list(parts, in_list)
            continue
        heading = re.match(r'^(#{1,6})\s+(.+)$', line)
        if heading:
            flush_paragraph()
            in_list = close_list(parts, in_list)
            level = len(heading.group(1))
            parts.append(
                f"<h{level}>{inline_markdown(heading.group(2).strip())}</h{level}>"
            )
            continue
        item = re.match(r'^[-*]\s+(.+)$', line)
        if item:
            flush_paragraph()
            if not in_list:
                parts.append('<ul>')
                in_list = True
            parts.append(f"<li>{inline_markdown(item.group(1).strip())}</li>")
            continue
        paragraph.append(line.strip())

    flush_paragraph()
    close_list(parts, in_list)
    if in_code:
        parts.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
    return '\n'.join(parts)


def title_for(path: Path, markdown: str) -> str:
    """Choose the document title from the first H1 in the markdown or, if none is present, the file stem.

    Parameters:
        path (Path): Source file path used as a fallback when no H1 is found.
        markdown (str): Markdown document text to scan for a first-level heading.

    Returns:
        title (str): The text of the first H1, HTML-unescaped and stripped of surrounding whitespace, or `path.stem` if no H1 is found.
    """
    for line in markdown.splitlines():
        match = _TITLE_RE.match(line)
        if match:
            return html.unescape(match.group(1)).strip()
    return path.stem


def render_page(path: Path) -> str:
    """Builds a standalone HTML document from a Markdown source file.

    Reads the Markdown at `path`, derives the document title and body, and returns a complete HTML string suitable for writing to disk or serving.

    Returns:
        html (str): Complete HTML document containing an escaped <title> and the rendered body fragment.
    """
    markdown = path.read_text(encoding='utf-8')
    title = title_for(path, markdown)
    body = render_body(markdown)
    return f"""<!doctype html>
<html lang=\"de\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>{html.escape(title)}</title>
  <style>
    :root {{ color-scheme: light dark; }}
    body {{ font-family: system-ui, sans-serif; line-height: 1.6; margin: 2rem auto; max-width: 58rem; padding: 0 1rem; }}
    code, pre {{ font-family: ui-monospace, SFMono-Regular, Consolas, monospace; }}
    pre {{ overflow-x: auto; padding: 1rem; }}
  </style>
</head>
<body>
{body}
</body>
</html>
"""


def write_index(outputs: Iterable[Path], *, check: bool) -> bool:
    """Generate an index HTML file listing the given output paths and either write it to the output directory or verify it against the existing file.

    Each entry uses the output file's name for the link href and the file stem for the link text.

    Parameters:
        outputs (Iterable[Path]): Paths to output files to include in the index.
        check (bool): If `True`, compare the generated content with the existing file and do not modify disk; if `False`, write the file.

    Returns:
        `True` if the generated index was written or matched the existing file, `False` if a difference was detected when `check` is `True`.
    """
    links = '\n'.join(
        f'    <li><a href="{html.escape(path.name, quote=True)}">{html.escape(path.stem)}</a></li>'
        for path in sorted(outputs)
    )
    content = f"""<!doctype html>
<html lang=\"de\">
<head>
  <meta charset=\"utf-8\">
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">
  <title>Jackery SolarVault Dokumentation</title>
</head>
<body>
  <h1>Jackery SolarVault Dokumentation</h1>
  <ul>
{links}
  </ul>
</body>
</html>
"""
    return write_or_check(OUTPUT_DIR / 'index.html', content, check=check)


def write_or_check(path: Path, content: str, *, check: bool) -> bool:
    """Write `content` to `path`, or when `check` is true, report a unified diff if the existing file differs.

    Parameters:
        path (Path): Destination file path.
        content (str): New file content to write or compare.
        check (bool): If `True`, do not write; compare existing content and print a unified diff to stderr when different.

    Returns:
        bool: `True` if the file was written or the existing content matches `content`; `False` if `check` is `True` and the contents differ.
    """
    if check:
        old = path.read_text(encoding='utf-8') if path.exists() else ''
        if old != content:
            diff = difflib.unified_diff(
                old.splitlines(),
                content.splitlines(),
                fromfile=str(path),
                tofile=f"{path} (generated)",
                lineterm='',
            )
            print('\n'.join(diff), file=sys.stderr)
            return False
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')
    return True


def build(*, check: bool) -> int:
    """Build all docs/*.md files and return a process exit code."""
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    outputs: list[Path] = []
    ok = True
    for source in markdown_files():
        target = OUTPUT_DIR / f"{source.stem}.html"
        outputs.append(target)
        ok = write_or_check(target, render_page(source), check=check) and ok
    ok = write_index(outputs, check=check) and ok
    return 0 if ok else 1


def watch(interval: float) -> int:
    """Rebuild docs whenever a source Markdown file changes."""
    mtimes: dict[Path, int] = {}
    while True:
        sources = markdown_files()
        current = {path: path.stat().st_mtime_ns for path in sources}
        if current != mtimes:
            mtimes = current
            build(check=False)
            print(f"Built {len(sources)} docs into {OUTPUT_DIR.relative_to(ROOT)}")
        time.sleep(interval)


def parse_args() -> argparse.Namespace:
    """Parse command-line arguments for the docs builder.

    Returns:
        argparse.Namespace: Parsed arguments with attributes:
            check (bool): True to fail when generated HTML is stale.
            watch (bool): True to rebuild docs after changes.
            watch_interval (float): Seconds between source scans in watch mode.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--check', action='store_true', help='fail when generated HTML is stale'
    )
    parser.add_argument(
        '--watch', action='store_true', help='rebuild docs after changes'
    )
    parser.add_argument(
        '--watch-interval',
        type=float,
        default=1.0,
        help='seconds between source scans in watch mode',
    )
    return parser.parse_args()


def main() -> int:
    """Run the documentation builder according to CLI arguments.

    Parses command-line arguments and either starts watch mode or performs a single build, returning the chosen mode's exit code.

    Returns:
        int: Exit code (0 on success, non-zero on failure).

    Raises:
        SystemExit: If both `--watch` and `--check` are provided.
    """
    args = parse_args()
    if args.watch:
        if args.check:
            raise SystemExit('--watch and --check are mutually exclusive')
        return watch(interval=args.watch_interval)
    return build(check=args.check)


if __name__ == '__main__':
    raise SystemExit(main())
