"""Compatibility wrapper for the accidentally duplicated repository-root script.

Use ``scripts.check_docs_root`` as the single maintained implementation.
"""

from scripts.check_docs_root import main


if __name__ == "__main__":
    raise SystemExit(main())
