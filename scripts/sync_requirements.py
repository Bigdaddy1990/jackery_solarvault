"""Synchronize integration and test requirement files."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALWAYS_TEST = ["pytest-homeassistant-custom-component==0.13.356"]


def _normalized(lines: list[str]) -> set[str]:
    return {
        line.strip()
        for line in lines
        if line.strip() and not line.lstrip().startswith("#")
    }


def show_diff(name: str, current: list[str], expected: list[str]) -> bool:
    """Print deterministic requirement differences and report whether they differ."""
    current_set = _normalized(current)
    expected_set = _normalized(expected)
    for requirement in sorted(expected_set - current_set):
        print(f"{name}: + {requirement}")
    for requirement in sorted(current_set - expected_set):
        print(f"{name}: - {requirement}")
    return current_set != expected_set


def main(argv: list[str] | None = None) -> int:
    """Check or rewrite requirements-test.txt."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    manifest = json.loads(
        (ROOT / "custom_components/jackery_solarvault/manifest.json").read_text(
            encoding="utf-8"
        )
    )
    expected = sorted({*ALWAYS_TEST, *manifest.get("requirements", [])})
    path = ROOT / "requirements-test.txt"
    current = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    differs = show_diff(path.name, current, expected)
    if differs and not args.check:
        path.write_text("\n".join(expected) + "\n", encoding="utf-8")
    return int(differs and args.check)


if __name__ == "__main__":
    raise SystemExit(main())
