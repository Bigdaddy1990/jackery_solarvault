"""Compatibility wrapper for the accidentally duplicated repository-root script.

Use ``scripts.check_typed_dicts`` as the single maintained implementation.
"""

from scripts.check_typed_dicts import main


if __name__ == "__main__":
    raise SystemExit(main())
