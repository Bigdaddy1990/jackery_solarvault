"""Regression tests for the Jackery reference coverage checker."""

import json
from pathlib import Path
import shutil

from scripts.check_reference_coverage import (
    check_reference_coverage,
    reference_mqtt_message_types,
    registered_services,
)


def _copy_reference_fixture(tmp_path: Path) -> Path:
    """Copy only files required by the reference coverage checker."""
    root = tmp_path / "repo"
    domain = root / "custom_components" / "jackery_solarvault"
    endpoint_dir = domain / "client" / "_endpoints"
    docs = root / "docs"
    endpoint_dir.mkdir(parents=True)
    docs.mkdir()

    for relative in (
        "custom_components/jackery_solarvault/const.py",
        "custom_components/jackery_solarvault/services.py",
        "custom_components/jackery_solarvault/services.yaml",
        "custom_components/jackery_solarvault/strings.json",
        "custom_components/jackery_solarvault/manifest.json",
        "custom_components/jackery_solarvault/quality_scale.yaml",
        "docs/MQTT_PROTOCOL.md",
    ):
        source = Path(relative)
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)

    shutil.copytree(
        Path("custom_components/jackery_solarvault/client/_endpoints"),
        endpoint_dir,
        dirs_exist_ok=True,
    )
    return root


def test_reference_coverage_passes_for_current_repository() -> None:
    """Current repository metadata stays internally aligned."""
    report = check_reference_coverage(Path.cwd())

    assert report.ok, report.errors


def test_reference_coverage_detects_missing_reference_endpoint(tmp_path: Path) -> None:
    """Structured reference JSON endpoints must exist in endpoint mixins."""
    root = _copy_reference_fixture(tmp_path)
    reference_path = root / "docs" / "jackery_complete_reference.json"
    reference_path.write_text(
        json.dumps({"endpoints": ["/v1/auth/login", "/v1/device/not/implemented"]}),
        encoding="utf-8",
    )

    report = check_reference_coverage(root)

    assert not report.ok
    assert any("/v1/device/not/implemented" in error for error in report.errors)


def test_reference_coverage_detects_missing_mqtt_message_type(tmp_path: Path) -> None:
    """MQTT protocol docs must stay aligned with declared message types."""
    root = _copy_reference_fixture(tmp_path)
    mqtt_path = root / "docs" / "MQTT_PROTOCOL.md"
    mqtt_path.write_text(
        "| messageType |\n|---|\n| `UnimplementedMessageType` |\n", encoding="utf-8"
    )

    report = check_reference_coverage(root)

    assert not report.ok
    assert any("UnimplementedMessageType" in error for error in report.errors)


def test_reference_mqtt_message_types_ignore_common_capitalized_words(
    tmp_path: Path,
) -> None:
    """Structured reference JSON only treats strict CamelCase as MQTT types."""
    root = _copy_reference_fixture(tmp_path)
    reference_path = root / "docs" / "jackery_complete_reference.json"
    (root / "docs" / "MQTT_PROTOCOL.md").write_text("", encoding="utf-8")
    reference_path.write_text(
        json.dumps({"status": ["Success", "Error", "OK", "BLE", "MQTT", "WiFi"]}),
        encoding="utf-8",
    )

    assert reference_mqtt_message_types(root).isdisjoint({
        "Success",
        "Error",
        "OK",
        "BLE",
        "MQTT",
        "WiFi",
    })


def test_registered_services_support_qualified_service_registry_calls(
    tmp_path: Path,
) -> None:
    """Registered services are parsed from hass.services.async_register calls."""
    root = _copy_reference_fixture(tmp_path)

    assert registered_services(root) == {
        "delete_storm_alert",
        "query_third_party_mqtt_config",
        "refresh_weather_plan",
        "rename_system",
        "send_ble_command",
        "send_device_schedule",
        "set_third_party_mqtt_config",
    }


def test_reference_coverage_detects_missing_service_translation(tmp_path: Path) -> None:
    """A service in services.yaml must also exist in strings.json."""
    root = _copy_reference_fixture(tmp_path)
    strings_path = root / "custom_components" / "jackery_solarvault" / "strings.json"
    strings = json.loads(strings_path.read_text(encoding="utf-8"))
    strings["services"].pop("rename_system")
    strings_path.write_text(json.dumps(strings), encoding="utf-8")

    report = check_reference_coverage(root)

    assert not report.ok
    assert len(report.errors) == 1, f"Unexpected errors: {report.errors}"
    assert any("services.yaml and strings.json" in error for error in report.errors)


def test_reference_coverage_detects_manifest_quality_regression(tmp_path: Path) -> None:
    """The manifest must keep custom while quality_scale.yaml tracks internal goals."""
    root = _copy_reference_fixture(tmp_path)
    manifest_path = root / "custom_components" / "jackery_solarvault" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["quality_scale"] = "platinum"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    report = check_reference_coverage(root)

    assert not report.ok
    assert "manifest quality_scale must remain custom" in report.errors[0]
