"""Compatibility wrapper for the accidentally duplicated repository-root script.

Use ``scripts.check_compile`` as the single maintained implementation.
"""

from scripts.check_compile import main


if __name__ == "__main__":
    raise SystemExit(main())
