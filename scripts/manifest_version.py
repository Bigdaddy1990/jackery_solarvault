"""Read and validate the Home Assistant integration manifest version."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "custom_components" / "jackery_solarvault" / "manifest.json"
VERSION_RE = re.compile(r"^[0-9]+\.[0-9]+\.[0-9]+([-.+][0-9A-Za-z.-]+)?$")


def manifest_version() -> str:
    """Return the non-empty manifest version."""
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    version = manifest.get("version")
    if not isinstance(version, str) or not version.strip():
        raise SystemExit("manifest.json must contain a non-empty string version")
    version = version.strip()
    if not VERSION_RE.fullmatch(version):
        raise SystemExit(f"manifest version {version!r} is not a valid release version")
    return version


def parse_args() -> argparse.Namespace:
    """Parse CLI options."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--github-output", action="store_true")
    parser.add_argument("--verify-tag", action="store_true")
    parser.add_argument("--tag", default="")
    return parser.parse_args()


def main() -> int:
    """Run the manifest version helper."""
    args = parse_args()
    version = manifest_version()
    tag = args.tag.removeprefix("v")
    if args.verify_tag and tag != version:
        print(
            f"::error::Tag '{tag}' does not match manifest version '{version}'",
            file=sys.stderr,
        )
        return 1
    if args.github_output:
        output = Path(__import__("os").environ["GITHUB_OUTPUT"])
        with output.open("a", encoding="utf-8") as stream:
            stream.write(f"version={version}\n")
            stream.write(f"tag=v{version}\n")
    else:
        print(version)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
