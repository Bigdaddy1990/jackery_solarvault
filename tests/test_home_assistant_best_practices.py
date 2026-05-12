"""Regression checks for Home Assistant best-practice alignment."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "jackery_solarvault"


def _read(relative_path: str) -> str:
    return (COMPONENT / relative_path).read_text(encoding="utf-8")


def test_read_only_platforms_define_parallel_updates_zero() -> None:
    """Coordinator-backed read-only platforms should disable entity update jobs."""
    for relative_path in ("sensor.py", "binary_sensor.py"):
        source = _read(relative_path)
        assert "PARALLEL_UPDATES = 0" in source, relative_path


def test_config_flow_uses_reconfigure_specific_missing_entry_abort() -> None:
    """Reconfigure should not reuse the reauth-specific missing-entry abort."""
    source = _read("config_flow.py")
    assert "FLOW_ABORT_RECONFIGURE_ENTRY_MISSING" in source
    assert (
        "return self.async_abort(reason=FLOW_ABORT_RECONFIGURE_ENTRY_MISSING)" in source
    )


def test_abort_translations_cover_reconfigure_entry_missing() -> None:
    """Every locale should expose the new reconfigure abort reason."""
    for relative_path in ("strings.json",):
        data = json.loads(_read(relative_path))
        abort = data.get("config", {}).get("abort", {})
        assert "reconfigure_entry_missing" in abort, relative_path

    translations_dir = COMPONENT / "translations"
    for locale_file in sorted(translations_dir.glob("*.json")):
        data = json.loads(locale_file.read_text(encoding="utf-8"))
        abort = data.get("config", {}).get("abort", {})
        assert "reconfigure_entry_missing" in abort, locale_file.name
