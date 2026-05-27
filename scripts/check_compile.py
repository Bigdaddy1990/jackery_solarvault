"""Compile Python sources in memory without writing bytecode caches."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOTS = (
    ROOT / 'custom_components',
    ROOT / 'tests',
    ROOT / 'scripts',
)


def main() -> int:
    """Compile all Python source files found under the configured project source roots into bytecode in memory.

    This function scans each directory listed in SOURCE_ROOTS for `*.py` files, reads them as UTF-8 text, and compiles them without writing `.pyc` cache files.

    Returns:
        int: `0` if all discovered files were compiled successfully.
    """
    for source_root in SOURCE_ROOTS:
        for path in sorted(source_root.rglob('*.py')):
            source = path.read_text(encoding='utf-8')
            compile(source, str(path), 'exec')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
