"""Translation contract tests for generated Home Assistant entity keys."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TRANSLATION_ROOT = ROOT / "custom_components" / "jackery_solarvault"


def _leaf_paths(value: Any, prefix: str = "") -> set[str]:
    if not isinstance(value, dict):
        return {prefix}

    paths: set[str] = set()
    for key, child in value.items():
        child_prefix = f"{prefix}.{key}" if prefix else key
        paths.update(_leaf_paths(child, child_prefix))
    return paths


def test_language_files_cover_all_string_keys() -> None:
    """Implement test language files cover all string keys."""
    base = json.loads((TRANSLATION_ROOT / "strings.json").read_text(encoding="utf-8"))
    base_paths = _leaf_paths(base)

    for lang in ("en", "de"):
        translated = json.loads(
            (TRANSLATION_ROOT / "translations" / f"{lang}.json").read_text(
                encoding="utf-8"
            )
        )
        assert _leaf_paths(translated) == base_paths, lang
