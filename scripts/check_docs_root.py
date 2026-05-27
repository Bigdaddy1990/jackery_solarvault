"""Check that active project documentation lives as root HTML files."""

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
    """
    Check that each filename in REQUIRED_HTML exists as a root-level HTML file under DOCS.
    
    If any required files are missing, prints "Missing required docs:" followed by each missing path as "- docs/<name>" and returns 1. If all required files are present, returns 0.
    
    Returns:
        int: Exit code — 0 when all required docs are present, 1 when one or more are missing.
    """
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
