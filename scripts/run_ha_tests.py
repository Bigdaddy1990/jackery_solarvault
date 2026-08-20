"""Run the Home Assistant test harness with predictable coverage behavior."""

from __future__ import annotations

import sys

import pytest


def _pytest_args(arguments: list[str]) -> list[str]:
    """Disable configured coverage unless the caller selected a coverage mode."""
    if any(
        argument == "--no-cov" or argument.startswith("--cov") for argument in arguments
    ):
        return arguments
    return [*arguments, "--no-cov"]


def main() -> int:
    """Run pytest."""
    return pytest.main(_pytest_args(sys.argv[1:]))


if __name__ == "__main__":
    raise SystemExit(main())
