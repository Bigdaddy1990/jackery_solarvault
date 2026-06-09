"""Compatibility wrapper for the accidentally duplicated repository-root script.

Use ``scripts.check_vendor_pyyaml`` as the single maintained implementation.
"""

from scripts.check_vendor_pyyaml import main


if __name__ == "__main__":
    raise SystemExit(main())
