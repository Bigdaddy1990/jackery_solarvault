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
DOCS_DIR = ROOT / "docs"
OUTPUT_DIR = DOCS_DIR / "html"
_TITLE_RE = re.compile(r"^#\s+(.+?)\s*$")
_LINK_RE = re.compile(r"(?<!!)\[([^\]]+)\]\(([^)]+)\)")
_STRONG_RE = re.compile(r"\*\*([^*]+)\*\*")
_EM_RE = re.compile(r"(?<!\*)\*([^*]+)\*(?!\*)")
_CODE_RE = re.compile(r"`([^`]+)`")


def markdown_files() -> list[Path]:
    """Return source Markdown files that belong to the docs contract."""
    return sorted(
        path
        for path in DOCS_DIR.glob("*.md")
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
    """Close an open unordered list if needed."""
    if in_list:
        parts.append("</ul>")
    return False


def render_body(markdown: str) -> str:
    """Render Markdown to a compact, deterministic HTML fragment."""
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
        if line.startswith("```"):
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
        heading = re.match(r"^(#{1,6})\s+(.+)$", line)
        if heading:
            flush_paragraph()
            in_list = close_list(parts, in_list)
            level = len(heading.group(1))
            parts.append(
                f"<h{level}>{inline_markdown(heading.group(2).strip())}</h{level}>"
            )
            continue
        item = re.match(r"^[-*]\s+(.+)$", line)
        if item:
            flush_paragraph()
            if not in_list:
                parts.append("<ul>")
                in_list = True
            parts.append(f"<li>{inline_markdown(item.group(1).strip())}</li>")
            continue
        paragraph.append(line.strip())

    flush_paragraph()
    close_list(parts, in_list)
    if in_code:
        parts.append(f"<pre><code>{html.escape(chr(10).join(code_lines))}</code></pre>")
    return "\n".join(parts)


def title_for(path: Path, markdown: str) -> str:
    """Return the first H1 or the file stem as the document title."""
    for line in markdown.splitlines():
        match = _TITLE_RE.match(line)
        if match:
            return html.unescape(match.group(1)).strip()
    return path.stem


def render_page(path: Path) -> str:
    """Render a source Markdown file as a standalone HTML document."""
    markdown = path.read_text(encoding="utf-8")
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
    """Write or verify a generated HTML index."""
    links = "\n".join(
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
    return write_or_check(OUTPUT_DIR / "index.html", content, check=check)


def write_or_check(path: Path, content: str, *, check: bool) -> bool:
    """Write content or report a diff if the file is stale."""
    if check:
        old = path.read_text(encoding="utf-8") if path.exists() else ""
        if old != content:
            diff = difflib.unified_diff(
                old.splitlines(),
                content.splitlines(),
                fromfile=str(path),
                tofile=f"{path} (generated)",
                lineterm="",
            )
            print("\n".join(diff), file=sys.stderr)
            return False
        return True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
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
    """Parse command-line arguments."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check", action="store_true", help="fail when generated HTML is stale"
    )
    parser.add_argument(
        "--watch", action="store_true", help="rebuild docs after changes"
    )
    parser.add_argument(
        "--watch-interval",
        type=float,
        default=1.0,
        help="seconds between source scans in watch mode",
    )
    return parser.parse_args()


def main() -> int:
    """Run the docs builder."""
    args = parse_args()
    if args.watch:
        if args.check:
            raise SystemExit("--watch and --check are mutually exclusive")
        return watch(interval=args.watch_interval)
    return build(check=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
