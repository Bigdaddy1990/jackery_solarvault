#!/usr/bin/env python3
"""Compile project Python files without writing bytecode."""

from pathlib import Path
import py_compile
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
TARGETS = (ROOT / "custom_components", ROOT / "tests", ROOT / "scripts")


def _python_files() -> tuple[Path, ...]:
    """Return Python files that should compile."""
    return tuple(
        path for target in TARGETS if target.exists() for path in target.rglob("*.py")
    )


def main() -> int:
    """Compile configured project directories."""
    failed = False
    with tempfile.TemporaryDirectory() as temp_dir:
        for path in _python_files():
            cfile = str(Path(temp_dir, f"{path.stem}.pyc"))
            try:
                py_compile.compile(str(path), cfile=cfile, doraise=True)
            except py_compile.PyCompileError:
                failed = True
    return int(failed)


if __name__ == "__main__":
    sys.exit(main())
