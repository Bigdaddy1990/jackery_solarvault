"""Translation contract tests for generated Home Assistant entity keys."""

import json
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
TRANSLATION_ROOT = ROOT / "custom_components" / "jackery_solarvault"
LANGUAGES = ("en", "de", "es", "fr")


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

    for lang in LANGUAGES:
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
    expected = {
        "de": (
            "Hauptbatterie Entladeleistung",
            "Batteriesystem Entladeleistung",
        ),
        "en": (
            "Main battery discharge power",
            "Battery system discharge power",
        ),
        "es": (
            "Potencia de descarga de la batería principal",
            "Potencia de descarga del sistema de baterías",
        ),
        "fr": (
            "Puissance de décharge de la batterie principale",
            "Puissance de décharge du système de batteries",
        ),
    }

    for lang, (main_battery, battery_system) in expected.items():
        translation = json.loads(
            (TRANSLATION_ROOT / "translations" / f"{lang}.json").read_text(
                encoding="utf-8"
            )
        )
        assert (
            translation["entity"]["sensor"]["battery_discharge_power"]["name"]
            == main_battery
        ), lang
        assert (
            translation["entity"]["sensor"]["stack_out_power"]["name"] == battery_system
        ), lang


def test_repair_issue_translations_are_fixable_or_descriptive() -> None:
    """Repair issues must define exactly one of description or fix_flow."""
    paths = [TRANSLATION_ROOT / "strings.json"]
    paths.extend(
        TRANSLATION_ROOT / "translations" / f"{lang}.json" for lang in LANGUAGES
    )

    for path in paths:
        strings = json.loads(path.read_text(encoding="utf-8"))
        for issue in strings.get("issues", {}).values():
            assert ("description" in issue) ^ ("fix_flow" in issue), path
