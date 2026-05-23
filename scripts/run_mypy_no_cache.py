"""Run mypy without leaving a local cache directory."""

import os
import subprocess
import sys


def main() -> int:
    """Run mypy for the integration package."""
    return subprocess.call([
        sys.executable,
        "-m",
        "mypy",
        "--no-incremental",
        f"--cache-dir={os.devnull}",
        "custom_components/jackery_solarvault",
    ])


if __name__ == "__main__":
    raise SystemExit(main())
