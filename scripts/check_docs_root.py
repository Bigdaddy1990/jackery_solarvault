"""Check that active project documentation lives as root HTML files."""

from __future__ import annotations

from argparse import ArgumentParser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs"
REQUIRED_HTML = (
    "Agents.html",
    "Claude.html",
    "FehlerLOG.html",
    "PROTOCOL.html",
    "TODO.html",
)


def main() -> int:
    """Validate the required root-level HTML docs."""
    parser = ArgumentParser()
    parser.add_argument("--watch", action="store_true")
    parser.parse_args()

    missing = [name for name in REQUIRED_HTML if not (DOCS / name).is_file()]
    if missing:
        print("Missing required docs:")
        for name in missing:
            print(f"- docs/{name}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
