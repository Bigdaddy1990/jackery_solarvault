"""Verify Python 3.14 multi-exception handler syntax."""

from __future__ import annotations

import ast
from pathlib import Path


def violations(paths: tuple[Path, ...]) -> tuple[str, ...]:
    """Return files that do not parse with the Python 3.14 grammar."""
    errors: list[str] = []
    for path in paths:
        try:
            ast.parse(
                path.read_text(encoding="utf-8"),
                filename=str(path),
                feature_version=(3, 14),
            )
        except SyntaxError as err:
            errors.append(f"{path}:{err.lineno}: {err.msg}")
    return tuple(errors)
