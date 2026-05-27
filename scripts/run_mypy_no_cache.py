"""Run mypy without leaving a local cache directory."""

import os
import subprocess
import sys


def main() -> int:
    """
    Run mypy for the integration package using os.devnull as the cache directory.
    
    Returns:
        int: Exit code returned by the mypy subprocess (`0` indicates success, non-zero indicates failure).
    """
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
