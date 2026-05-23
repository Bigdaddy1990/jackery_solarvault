"""Remove local checker caches after successful bun workflows."""

from __future__ import annotations

from pathlib import Path
import shutil

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR_NAMES = {
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
}
CACHE_FILES = {
    ".coverage",
}


def _remove_dir(path: Path) -> bool:
    if not path.exists():
        return False
    shutil.rmtree(path, ignore_errors=True)
    return not path.exists()


def _remove_file(path: Path) -> bool:
    if not path.exists():
        return False
    path.unlink(missing_ok=True)
    return not path.exists()


def main() -> int:
    """Remove known local cache directories and files."""
    removed = 0
    skipped: list[str] = []

    for path in sorted(ROOT.rglob("*")):
        if path.is_dir() and path.name in CACHE_DIR_NAMES:
            if _remove_dir(path):
                removed += 1
            elif path.exists():
                skipped.append(str(path.relative_to(ROOT)))
        elif path.is_file() and path.name in CACHE_FILES:
            if _remove_file(path):
                removed += 1
            elif path.exists():
                skipped.append(str(path.relative_to(ROOT)))

    print(f"removed {removed} cache item(s)")
    if skipped:
        print("skipped locked cache item(s):")
        for item in skipped:
            print(f"- {item}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
