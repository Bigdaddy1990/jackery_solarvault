"""Install development requirements into the repository environment."""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    """Create the project environment if needed and synchronize requirements."""
    environment = ROOT / ".venv"
    uv = [sys.executable, "-m", "uv"]
    if not (environment / "pyvenv.cfg").exists():
        subprocess.run(
            [*uv, "venv", "--python", "3.14", str(environment)], check=True, cwd=ROOT
        )
    subprocess.run(
        [
            *uv,
            "pip",
            "install",
            "--exact",
            "--python",
            str(environment),
            "-r",
            "requirements.txt",
            "-r",
            "requirements-test.txt",
        ],
        check=True,
        cwd=ROOT,
    )


if __name__ == "__main__":
    main()
