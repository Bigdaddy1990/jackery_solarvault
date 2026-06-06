"""Synchronise assistant-specific contributor guides with the canonical copy."""

import argparse
from pathlib import Path

SYNC_START = "<!-- SYNC:START -->"
SYNC_END = "<!-- SYNC:END -->"
REPO_ROOT = Path(__file__).resolve().parents[1]
CANONICAL_SOURCE = REPO_ROOT / ".github" / "copilot-instructions.md"
TARGETS = [
    REPO_ROOT / ".claude" / "agents" / "copilot-instructions.md",
    REPO_ROOT / ".gemini" / "styleguide.md",
]


def _load_canonical_block() -> str:
    content = CANONICAL_SOURCE.read_text(encoding="utf-8")
    if SYNC_START in content and SYNC_END in content:
        start = content.index(SYNC_START) + len(SYNC_START)
        end = content.index(SYNC_END, start)
        block = content[start:end].strip("\n")
    else:
        block = content.strip("\n")
    return f"{SYNC_START}\n{block}\n{SYNC_END}"


def _apply_sync_block(target: Path, block: str) -> tuple[str, str]:
    text = target.read_text(encoding="utf-8")
    start = text.index(SYNC_START)
    end = text.index(SYNC_END, start) + len(SYNC_END)
    return text, text[:start] + block + text[end:]


def main() -> int:  # noqa: D103
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="only verify the files are synced",
    )
    args = parser.parse_args()

    block = _load_canonical_block()
    had_changes = False

    for target in TARGETS:
        original, updated = _apply_sync_block(target, block)
        if updated != original:
            if args.check:
                print(f"{target} is out of date with {CANONICAL_SOURCE}")
            else:
                target.write_text(updated, encoding="utf-8")
                print(f"Synced {target} with {CANONICAL_SOURCE}")
            had_changes = True

    if args.check and had_changes:
        return 1
    if not had_changes:
        print("Contributor guides already match the canonical instructions.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
