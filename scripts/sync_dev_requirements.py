"""Install development requirements into the repository environment."""

import subprocess
import sys
from pathlib import Path
import os

ROOT = Path(__file__).resolve().parents[1]


def compatible_environment(environment: Path) -> bool:
    """Check the project interpreter, including broken environment links."""
    interpreter = environment / (
        "Scripts/python.exe" if os.name == "nt" else "bin/python"
    )
    try:
        result = subprocess.run(
            [
                str(interpreter),
                "-c",
                "import sys; sys.exit(not ((3, 14, 2) <= sys.version_info < (3, 15)))",
            ],
            check=False,
        )
    except OSError:
        return False
    return result.returncode == 0


def main() -> None:
    """Create the project environment if needed and synchronize requirements."""
    environment = ROOT / ".venv"
    uv = [sys.executable, "-m", "uv"]
    if not compatible_environment(environment):
        subprocess.run(
            [*uv, "venv", "--python", ">=3.14.2,<3.15", str(environment)],
            check=True,
            cwd=ROOT,
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
