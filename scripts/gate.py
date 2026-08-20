"""Run deterministic local repository gates."""

from __future__ import annotations

from subprocess import run
from typing import Final

RUFF_TARGETS: Final = (
    "custom_components/jackery_solarvault",
    "tests",
)


def main() -> int:
    """Run Ruff against shipped integration and test sources only."""
    checks = (
        ("ruff", "check", *RUFF_TARGETS),
        ("ruff", "format", "--check", *RUFF_TARGETS),
    )
    return next(
        (
            result.returncode
            for command in checks
            if (result := run(command, check=False)).returncode
        ),
        0,
    )


if __name__ == "__main__":
    raise SystemExit(main())
