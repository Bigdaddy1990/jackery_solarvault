"""Sync .HA_VERSION and its constraints fixture from installed pytest metadata.

Run after installing requirements-test.txt. --write records an intentional
pytest-plugin upgrade; --check prevents a stale or manually invented baseline.
"""

import argparse
from importlib.metadata import requires, version
from importlib.resources import files
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parents[1]


def plugin_baseline() -> str:
    """Read the exact Home Assistant pin from the installed pytest plugin."""
    candidates = []
    for value in requires("pytest-homeassistant-custom-component") or []:
        requirement = Requirement(value)
        if canonicalize_name(requirement.name) != "homeassistant":
            continue
        if requirement.marker is not None and not requirement.marker.evaluate():
            continue
        pins = list(requirement.specifier)
        if len(pins) != 1 or pins[0].operator != "==" or "*" in pins[0].version:
            raise ValueError("pytest plugin must declare one exact Home Assistant pin")
        candidates.append(pins[0].version)
    if len(candidates) != 1:
        raise ValueError("pytest plugin must declare exactly one active HA requirement")
    return candidates[0]


def baseline_files(root: Path) -> dict[Path, str]:
    """Build baseline artifacts from the plugin and its installed HA package."""
    baseline = plugin_baseline()
    if version("homeassistant") != baseline:
        raise ValueError("installed Home Assistant does not match pytest plugin pin")
    constraints = files("homeassistant").joinpath("package_constraints.txt").read_text(
        encoding="utf-8"
    )
    return {
        root / ".HA_VERSION": baseline + "\n",
        root / "tests/fixtures" / f"ha-{baseline}-package-constraints.txt": constraints,
    }


def constraint_values(content: str) -> list[str]:
    """Compare dependency semantics independently of formatter order/comments."""
    return sorted(
        str(Requirement(value))
        for line in content.splitlines()
        if (value := line.partition("#")[0].strip())
    )


def check_baseline(root: Path) -> list[str]:
    """Return missing or stale artifacts without changing the repository."""
    stale = []
    for path, expected in baseline_files(root).items():
        if not path.exists():
            stale.append(str(path.relative_to(root)))
            continue
        actual = path.read_text(encoding="utf-8")
        matches = (
            actual.strip() == expected.strip()
            if path.name == ".HA_VERSION"
            else constraint_values(actual) == constraint_values(expected)
        )
        if not matches:
            stale.append(str(path.relative_to(root)))
    return stale


def main() -> int:
    """Check the baseline or write artifacts for an intentional stack update."""
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--check", action="store_true")
    args = parser.parse_args()
    if args.write:
        for path, content in baseline_files(ROOT).items():
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
        return 0
    stale = check_baseline(ROOT)
    for path in stale:
        print(f"Stale pytest-plugin baseline: {path}; run --write after stack review")
    return int(bool(stale))


if __name__ == "__main__":
    raise SystemExit(main())
