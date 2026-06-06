"""Sync jackery_solarvault translation files with strings.json.

Modes
-----
default        Sync existing translation files only.
--all-languages  Sync every language declared in scripts/languages.py.
--seed-missing   Create stub files for languages not yet present (implies
                 --all-languages).  New files get English strings as
                 placeholders; HA falls back to 'en' automatically until a
                 human or machine translation is provided.
--check        Validate only; exit non-zero on any mismatch or missing file.
--list-missing   Print languages that have no translation file and exit.
"""

import argparse
import json
from pathlib import Path
import sys
from typing import Any

# Resolve project root independent of CWD
ROOT = Path(__file__).resolve().parents[1]


def _load_json(path: Path) -> Any:  # noqa: ANN401
    return json.loads(path.read_text(encoding="utf-8"))


def _dump_json(data: Any) -> str:  # noqa: ANN401
    return json.dumps(data, ensure_ascii=False, indent=2) + "\n"


def _sync_tree(source: Any, existing: Any) -> Any:  # noqa: ANN401
    """Recursively merge *source* structure into *existing*.

    Preserve existing translations and back-fill missing keys with English
    strings.
    """
    if isinstance(source, dict):
        existing_map = existing if isinstance(existing, dict) else {}
        return {
            key: _sync_tree(value, existing_map.get(key))
            for key, value in source.items()
        }

    # Non-string leaf (number, bool, null) → immutable, keep source value.
    if not isinstance(source, str):
        return source

    # String leaf: keep existing translation when present.
    if isinstance(existing, str):
        return existing

    # No translation yet → fall back to English source string.
    return source


def _sync_translation(
    language_file: Path,
    strings_data: dict[str, Any],
    *,
    check_only: bool,
    seed: bool,
) -> bool:
    """Sync one language file.  Returns True when the file was modified."""
    if language_file.exists():
        original_content = language_file.read_text(encoding="utf-8")
        existing_data = json.loads(original_content)
    else:
        if check_only:
            raise SystemExit(f"Missing translation file: {language_file}")
        if not seed:
            # Skip files that don't exist unless seeding is requested.
            return False
        # Seed: write full English strings as placeholder.
        language_file.write_text(_dump_json(strings_data), encoding="utf-8")
        print(f"  SEEDED  {language_file.name}")
        return True

    synced = _sync_tree(strings_data, existing_data)
    new_content = _dump_json(synced)

    if new_content != original_content:
        if check_only:
            raise SystemExit(f"Translation file out of date: {language_file}")
        language_file.write_text(new_content, encoding="utf-8")
        print(f"  UPDATED {language_file.name}")
        return True

    return False


def _get_ha_languages() -> set[str]:
    """Import LANGUAGES set from scripts/languages.py."""
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "languages", ROOT / "scripts" / "languages.py"
    )
    mod = importlib.util.module_from_spec(spec)  # type: ignore[arg-type]
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod.LANGUAGES


def _resolve_language_files(
    translations_dir: Path,
    languages: list[str] | None,
    *,
    all_languages: bool,
    seed: bool,
) -> list[Path]:
    if languages:
        return [translations_dir / f"{lang}.json" for lang in languages]
    if all_languages or seed:
        ha_langs = _get_ha_languages()
        return sorted(translations_dir / f"{lang}.json" for lang in ha_langs)
    # Default: only files that already exist.
    return sorted(translations_dir.glob("*.json"))


def _list_missing(translations_dir: Path) -> list[str]:
    ha_langs = _get_ha_languages()
    existing = {p.stem for p in translations_dir.glob("*.json")}
    return sorted(ha_langs - existing)


def main(argv: list[str] | None = None) -> int:  # noqa: D103
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--integration-path",
        type=Path,
        default=ROOT / "custom_components" / "jackery_solarvault",
        help="Path to the jackery_solarvault integration directory.",
    )
    parser.add_argument(
        "--languages",
        nargs="*",
        metavar="LANG",
        help="Explicit language codes to sync (e.g. de fr ja).",
    )
    parser.add_argument(
        "--all-languages",
        action="store_true",
        help="Sync every language declared in languages.py (existing files only).",
    )
    parser.add_argument(
        "--seed-missing",
        action="store_true",
        help="Create stub files for every missing HA language (English fallback).",
    )
    parser.add_argument(
        "--list-missing",
        action="store_true",
        help="Print languages without a translation file and exit.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Validate only; exit 1 on any mismatch or missing file.",
    )
    args = parser.parse_args(argv)

    integration_path = args.integration_path
    strings_path = integration_path / "strings.json"
    translations_dir = integration_path / "translations"

    if not strings_path.exists():
        raise SystemExit(f"Missing strings.json at {strings_path}")

    translations_dir.mkdir(parents=True, exist_ok=True)

    if args.list_missing:
        missing = _list_missing(translations_dir)
        if missing:
            print(f"{len(missing)} languages missing:")
            for lang in missing:
                print(f"  {lang}")
        else:
            print("All HA languages present.")
        return 0

    strings_data = _load_json(strings_path)

    language_files = _resolve_language_files(
        translations_dir,
        args.languages,
        all_languages=args.all_languages,
        seed=args.seed_missing,
    )
    if not language_files:
        raise SystemExit("No translation files found to sync.")

    changed = 0
    for language_file in language_files:
        if _sync_translation(
            language_file,
            strings_data,
            check_only=args.check,
            seed=args.seed_missing,
        ):
            changed += 1

    total = len(language_files)
    print(f"Synced {total} language(s), {changed} updated/created.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
