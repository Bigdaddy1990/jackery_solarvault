"""Lightweight hassfest shim for offline validation.

The real Home Assistant hassfest utility validates integration manifests,
translations, and metadata. This shim provides a minimal subset so the
repository can run guard checks without pulling in the full Home Assistant
package. It validates manifest structure, required keys, and translation
presence for the jackery_solarvault integration.
"""

from argparse import ArgumentParser
from collections.abc import Iterable
import json
from pathlib import Path
import sys
from typing import Any

# ``custom`` is the value HA Core's hassfest accepts for integrations
# distributed outside core (HACS / manual installs). Our shim must
# mirror that to avoid rejecting valid custom integrations.
QUALITY_SCALE_LEVELS = {"internal", "bronze", "silver", "gold", "platinum", "custom"}
REQUIRED_KEYS = {
    "domain",
    "name",
    "version",
    "documentation",
    "issue_tracker",
    "requirements",
    "codeowners",
    "config_flow",
    "integration_type",
    "quality_scale",
    "loggers",
    "iot_class",
}
REQUIRED_LIST_KEYS = {"requirements", "codeowners", "loggers"}
IOT_CLASS_VALUES = {
    "assumed_state",
    "cloud_polling",
    "cloud_push",
    "local_polling",
    "local_push",
}


def _load_manifest(manifest_path: Path) -> dict[str, Any]:
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest = json.loads(manifest_text)
    if not isinstance(manifest, dict):
        msg = "manifest.json must contain a JSON object"
        raise ValueError(msg)
    return manifest


def _validate_manifest(manifest_path: Path) -> list[str]:
    errors: list[str] = []
    if not manifest_path.is_file():
        return ["manifest.json is missing"]

    try:
        manifest = _load_manifest(manifest_path)
    except (json.JSONDecodeError, ValueError) as exc:
        errors.append(f"invalid manifest.json: {exc}")
        return errors

    missing_keys = sorted(REQUIRED_KEYS - manifest.keys())
    if missing_keys:
        errors.append(
            f"manifest.json is missing required keys: {', '.join(missing_keys)}",
        )

    for key in REQUIRED_LIST_KEYS:
        value = manifest.get(key)
        if not isinstance(value, list) or not value:
            errors.append(f"manifest.{key} must be a non-empty list")

    if not isinstance(manifest.get("config_flow"), bool):
        errors.append("manifest.config_flow must be a boolean")

    domain = manifest.get("domain")
    expected_domain = manifest_path.parent.name
    if domain != expected_domain:
        errors.append(
            f"manifest.domain '{domain}' does not match integration folder '{expected_domain}'",
        )

    quality_scale = manifest.get("quality_scale")
    if quality_scale not in QUALITY_SCALE_LEVELS:
        errors.append(
            "manifest.quality_scale must be one of: "
            + ", ".join(sorted(QUALITY_SCALE_LEVELS)),
        )

    loggers = manifest.get("loggers")
    if isinstance(loggers, list):
        expected_logger = f"custom_components.{expected_domain}"
        if expected_logger not in loggers:
            errors.append(
                f"manifest.loggers must include 'custom_components.{expected_domain}'",
            )

    supported_by = manifest.get("supported_by")
    if supported_by is not None and (
        not isinstance(supported_by, str) or not supported_by
    ):
        errors.append("manifest.supported_by must be null or a non-empty string")

    iot_class = manifest.get("iot_class")
    if iot_class not in IOT_CLASS_VALUES:
        errors.append(
            "manifest.iot_class must be one of: " + ", ".join(sorted(IOT_CLASS_VALUES)),
        )

    return errors


def _validate_translations(integration_path: Path) -> list[str]:
    errors: list[str] = []
    translations_dir = integration_path / "translations"
    strings_path = integration_path / "strings.json"

    if not translations_dir.is_dir():
        errors.append("translations directory is missing")
    if not strings_path.is_file():
        errors.append("strings.json is missing")

    def _load_object(path: Path) -> dict[str, Any] | None:
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            errors.append(f"{path.name} is not valid JSON")
            return None
        if not isinstance(loaded, dict):
            errors.append(f"{path.name} must contain a JSON object")
            return None
        return loaded

    if strings_path.is_file():
        _load_object(strings_path)

    if translations_dir.is_dir():
        english_translation = translations_dir / "en.json"
        if not english_translation.is_file():
            errors.append("translations/en.json is missing")
        else:
            _load_object(english_translation)

    return errors


def run(argv: Iterable[str] | None = None) -> int:  # noqa: D103
    parser = ArgumentParser(description="Validate Home Assistant integration metadata")
    parser.add_argument(
        "--integration-path",
        type=Path,
        required=True,
        help="Path to the integration directory (e.g. custom_components/jackery_solarvault)",
    )
    args = parser.parse_args(list(argv) if argv is not None else None)

    integration_path = args.integration_path
    manifest_path = integration_path / "manifest.json"

    errors = _validate_manifest(manifest_path)
    errors.extend(_validate_translations(integration_path))

    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1

    print(f"hassfest stub: validated {integration_path}")
    return 0


def main() -> int:  # noqa: D103
    return run()


if __name__ == "__main__":
    raise SystemExit(main())
