"""Regression checks for Home Assistant best-practice alignment."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMPONENT = ROOT / "custom_components" / "jackery_solarvault"


def _read(relative_path: str) -> str:
    """Read a file from the component directory and return its contents.

    Parameters:
        relative_path (str): Path to the file relative to the component root (e.g., "sensor.py" or "translations/en.json").

    Returns:
        str: The file contents decoded as UTF-8.
    """  # noqa: E501
    return (COMPONENT / relative_path).read_text(encoding="utf-8")


def test_read_only_platforms_define_parallel_updates_zero() -> None:
    """Ensure coordinator-backed read-only platform modules disable parallel entity update jobs.

    Checks that the component's sensor.py and binary_sensor.py contain the exact declaration "PARALLEL_UPDATES = 0".
    """  # noqa: E501
    for relative_path in ("sensor.py", "binary_sensor.py"):
        source = _read(relative_path)
        assert "PARALLEL_UPDATES = 0" in source, relative_path


def test_config_flow_uses_reconfigure_specific_missing_entry_abort() -> None:
    """Ensure config_flow defines and uses the reconfigure-specific missing-entry abort reason.

    Asserts that `config_flow.py` contains the `FLOW_ABORT_RECONFIGURE_ENTRY_MISSING` identifier and that the code calls `self.async_abort(reason=FLOW_ABORT_RECONFIGURE_ENTRY_MISSING)`.
    """  # noqa: E501
    source = _read("config_flow.py")
    assert "FLOW_ABORT_RECONFIGURE_ENTRY_MISSING" in source
    assert (
        "return self.async_abort(reason=FLOW_ABORT_RECONFIGURE_ENTRY_MISSING)" in source
    )


def test_abort_translations_cover_reconfigure_entry_missing() -> None:
    """Verify that the `config.abort.reconfigure_entry_missing` key exists in the component's root `strings.json` and in every locale JSON under `translations/`.

    This ensures the reconfigure-specific abort reason is present in the default strings and all translations.
    """  # noqa: E501
    for relative_path in ("strings.json",):
        data = json.loads(_read(relative_path))
        abort = data.get("config", {}).get("abort", {})
        assert "reconfigure_entry_missing" in abort, relative_path

    translations_dir = COMPONENT / "translations"
    for locale_file in sorted(translations_dir.glob("*.json")):
        data = json.loads(locale_file.read_text(encoding="utf-8"))
        abort = data.get("config", {}).get("abort", {})
        assert "reconfigure_entry_missing" in abort, locale_file.name
