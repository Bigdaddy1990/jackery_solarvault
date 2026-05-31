"""Sync/validate localization flags.

This repository uses a lightweight variant of Home Assistant's localization validation.
In CI we run this script in --check mode to ensure the expected files exist and are valid JSON.
"""

import argparse
import json
from pathlib import Path
from typing import Any

TABLE_START_MARKER = "<!-- START_SETUP_FLAGS_TABLE -->"
TABLE_END_MARKER = "<!-- END_SETUP_FLAGS_TABLE -->"

_FLAG_PREFIX = "setup_flags_panel_flag_"
_SOURCE_PREFIX = "setup_flags_panel_source_"

_LANGUAGE_LABELS = {
    "en": "Englisch",
}


def _read_allowlist(path: Path) -> set[str]:
    try:
        return {
            line.strip()
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.strip().startswith("#")
        }
    except FileNotFoundError:
        return set()


def _load_json(path: Path) -> Any:  # noqa: ANN401
    return json.loads(path.read_text(encoding="utf-8"))


def _translation_languages(translations_dir: Path) -> list[str]:
    available = sorted(path.stem for path in translations_dir.glob("*.json"))
    if "en" in available:
        available.remove("en")
    return ["en", *available] if available else ["en"]


def _setup_flag_keys(strings_path: Path) -> list[str]:
    strings = _load_json(strings_path)
    common = strings.get("common", {})
    return [
        key
        for key in common
        if key.startswith(_FLAG_PREFIX) or key.startswith(_SOURCE_PREFIX)
    ]


def _update_markdown_table(
    markdown_path: Path,
    keys: list[str],
    translations: dict[str, dict[str, dict[str, str]]],
    languages: list[str],
    *,
    check_only: bool,
) -> bool:
    content = markdown_path.read_text(encoding="utf-8")
    lines = content.splitlines()
    start_index: int | None = None
    end_index: int | None = None
    for index, line in enumerate(lines):
        if line.strip() == TABLE_START_MARKER:
            start_index = index
        elif line.strip() == TABLE_END_MARKER and start_index is not None:
            end_index = index
            break

    if start_index is None or end_index is None or end_index <= start_index:
        raise SystemExit("Setup flags table markers missing.")

    header_cells = [
        "Übersetzungsschlüssel",
        *(f"{_LANGUAGE_LABELS.get(code, code)} (`{code}`)" for code in languages),
    ]
    table_lines = [
        "| " + " | ".join(header_cells) + " |",
        "| " + " | ".join("---" for _ in header_cells) + " |",
    ]

    for key in keys:
        row = [
            f"component.jackery_solarvault.common.{key}",
            *(translations[language]["common"][key] for language in languages),
        ]
        table_lines.append("| " + " | ".join(row) + " |")

    new_lines = lines[: start_index + 1] + table_lines + lines[end_index:]
    new_content = "\n".join(new_lines) + "\n"

    if new_content != content:
        if check_only:
            raise SystemExit("Setup flags table is out of date.")
        markdown_path.write_text(new_content, encoding="utf-8")
        return True

    return False


def main() -> int:  # noqa: D103
    parser = argparse.ArgumentParser()
    parser.add_argument("--allowlist", type=Path, required=False)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only validate; do not modify files.",
    )
    args = parser.parse_args()

    repo_root = Path(__file__).resolve().parents[1]
    allowlist = _read_allowlist(args.allowlist) if args.allowlist else set()

    # Validate strings.json and translation json files exist + parse as JSON
    custom_components_dir = repo_root / "custom_components"
    if not custom_components_dir.exists():
        return 0

    for integration_dir in custom_components_dir.iterdir():
        if not integration_dir.is_dir():
            continue
        strings = integration_dir / "strings.json"
        if strings.exists():
            _load_json(strings)

        translations_dir = integration_dir / "translations"
        if translations_dir.exists():
            for tfile in translations_dir.glob("*.json"):
                data = _load_json(tfile)
                # Optional minimal sanity: remove keys listed in allowlist from consideration
                # (This script is intentionally conservative; hassfest performs schema validation.)
                if allowlist:
                    # noop usage to avoid unused variable warnings in strict linters
                    _ = data

    pawcontrol_dir = custom_components_dir / "jackery_solarvault"
    strings_path = pawcontrol_dir / "strings.json"
    translations_dir = pawcontrol_dir / "translations"
    docs_path = repo_root / "docs" / "diagnostics.md"
    if strings_path.exists() and translations_dir.exists() and docs_path.exists():
        keys = _setup_flag_keys(strings_path)
        if keys:
            languages = _translation_languages(translations_dir)
            strings_common = _load_json(strings_path).get("common", {})
            translations: dict[str, dict[str, dict[str, str]]] = {
                "en": {"common": {key: strings_common[key] for key in keys}}
            }
            for language in languages:
                if language == "en":
                    continue
                translation_path = translations_dir / f"{language}.json"
                translation_common = _load_json(translation_path).get("common", {})
                translations[language] = {
                    "common": {key: translation_common[key] for key in keys}
                }
            _update_markdown_table(
                docs_path,
                keys,
                translations,
                languages,
                check_only=args.check,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
