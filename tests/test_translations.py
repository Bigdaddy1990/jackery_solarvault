"""Translation contract tests for generated Home Assistant entity keys."""

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


def test_service_actions_use_translation_files() -> None:
    """Service action labels belong in translations, not services.yaml."""
    services_yaml = (TRANSLATION_ROOT / "services.yaml").read_text(encoding="utf-8")
    strings = json.loads(
        (TRANSLATION_ROOT / "strings.json").read_text(encoding="utf-8")
    )
    icons = json.loads((TRANSLATION_ROOT / "icons.json").read_text(encoding="utf-8"))

    for hardcoded_key in ("  name:", "  description:"):
        assert hardcoded_key not in services_yaml

    expected_services = {
        "rename_system",
        "refresh_weather_plan",
        "delete_storm_alert",
    }
    assert set(strings["services"]) == expected_services
    assert set(icons["services"]) == expected_services
    for service_id in expected_services:
        assert "service" in icons["services"][service_id]


def test_battery_power_labels_keep_main_battery_and_stack_distinct() -> None:
    """Battery power labels must not hide batOutPw vs stackOutPw semantics."""
    de = json.loads(
        (TRANSLATION_ROOT / "translations" / "de.json").read_text(encoding="utf-8")
    )
    en = json.loads(
        (TRANSLATION_ROOT / "translations" / "en.json").read_text(encoding="utf-8")
    )

    assert de["entity"]["sensor"]["battery_discharge_power"]["name"] == (
        "Hauptbatterie Entladeleistung"
    )
    assert de["entity"]["sensor"]["stack_out_power"]["name"] == (
        "Batteriesystem Entladeleistung"
    )
    assert en["entity"]["sensor"]["battery_discharge_power"]["name"] == (
        "Main battery discharge power"
    )
    assert en["entity"]["sensor"]["stack_out_power"]["name"] == (
        "Battery system discharge power"
    )


def test_repair_issue_translations_are_fixable_or_descriptive() -> None:
    """Repair issues must define exactly one of description or fix_flow."""
    for path in (
        TRANSLATION_ROOT / "strings.json",
        TRANSLATION_ROOT / "translations" / "en.json",
        TRANSLATION_ROOT / "translations" / "de.json",
    ):
        strings = json.loads(path.read_text(encoding="utf-8"))
        for issue in strings.get("issues", {}).values():
            assert ("description" in issue) ^ ("fix_flow" in issue), path
