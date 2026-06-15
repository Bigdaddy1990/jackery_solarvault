"""Regression tests for the Jackery reference coverage checker."""

import ast
from io import StringIO
import json
from pathlib import Path
import shutil
from unittest.mock import patch

import pytest
from scripts.check_reference_coverage import (
    CoverageReport,
    _iter_strings,
    _literal_string_assignments,
    _normalize_paths,
    _quality_rule_statuses,
    _registered_service_name,
    _top_level_yaml_keys,
    check_reference_coverage,
    implemented_mqtt_message_types,
    main,
    reference_http_endpoints,
    reference_mqtt_message_types,
    registered_services,
    service_yaml_names,
    strings_service_names,
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


# ---------------------------------------------------------------------------
# CoverageReport dataclass
# ---------------------------------------------------------------------------


def test_coverage_report_ok_is_true_when_no_errors() -> None:
    """CoverageReport.ok returns True only when the errors tuple is empty."""
    report = CoverageReport(errors=(), warnings=())

    assert report.ok is True


def test_coverage_report_ok_is_false_when_errors_present() -> None:
    """CoverageReport.ok returns False when there is at least one error."""
    report = CoverageReport(errors=("something broke",), warnings=())

    assert report.ok is False


def test_coverage_report_ok_is_false_with_warnings_only() -> None:
    """Warnings alone do not count as errors; ok should still check only errors."""
    report = CoverageReport(errors=(), warnings=("heads-up",))

    assert report.ok is True


# ---------------------------------------------------------------------------
# _top_level_yaml_keys
# ---------------------------------------------------------------------------


def test_top_level_yaml_keys_extracts_first_level_keys(tmp_path: Path) -> None:
    """Top-level YAML keys are returned; comments and indented lines are ignored."""
    yaml_file = tmp_path / "services.yaml"
    yaml_file.write_text(
        "# This is a comment\n"
        "my_service:\n"
        "  description: does stuff\n"
        "another_service:\n"
        "- list item\n",
        encoding="utf-8",
    )

    keys = _top_level_yaml_keys(yaml_file)

    assert keys == {"my_service", "another_service"}


def test_top_level_yaml_keys_ignores_blank_lines(tmp_path: Path) -> None:
    """Blank lines in YAML do not produce spurious keys."""
    yaml_file = tmp_path / "svc.yaml"
    yaml_file.write_text("\n\nfoo:\n\nbar:\n", encoding="utf-8")

    keys = _top_level_yaml_keys(yaml_file)

    assert keys == {"foo", "bar"}


def test_top_level_yaml_keys_ignores_comment_lines(tmp_path: Path) -> None:
    """Hash-prefixed comment lines are not included in the key set."""
    yaml_file = tmp_path / "svc.yaml"
    yaml_file.write_text(
        "# comment: looks like a key but is not\nreal_key:\n", encoding="utf-8"
    )

    keys = _top_level_yaml_keys(yaml_file)

    assert "comment" not in keys
    assert "real_key" in keys


def test_top_level_yaml_keys_ignores_lines_without_colon(tmp_path: Path) -> None:
    """Lines without a colon are skipped even if they look like keys."""
    yaml_file = tmp_path / "svc.yaml"
    yaml_file.write_text("no_colon_here\nhas_colon: value\n", encoding="utf-8")

    keys = _top_level_yaml_keys(yaml_file)

    assert keys == {"has_colon"}


# ---------------------------------------------------------------------------
# _quality_rule_statuses
# ---------------------------------------------------------------------------


def test_quality_rule_statuses_parses_status_colon_lines(tmp_path: Path) -> None:
    """status: <value> lines are always included."""
    qfile = tmp_path / "quality_scale.yaml"
    qfile.write_text("status: done\nstatus: todo\n", encoding="utf-8")

    statuses = _quality_rule_statuses(qfile)

    assert "done" in statuses
    assert "todo" in statuses


def test_quality_rule_statuses_parses_two_space_indented_values(tmp_path: Path) -> None:
    """Two-space indented lines whose values are done/todo/exempt are captured."""
    qfile = tmp_path / "quality_scale.yaml"
    qfile.write_text(
        "some_rule:\n  status: done\nanother_rule:\n  status: exempt\n",
        encoding="utf-8",
    )

    statuses = _quality_rule_statuses(qfile)

    assert "done" in statuses
    assert "exempt" in statuses


def test_quality_rule_statuses_ignores_comment_lines(tmp_path: Path) -> None:
    """Hash-prefixed comment lines do not contribute statuses."""
    qfile = tmp_path / "quality_scale.yaml"
    qfile.write_text("# status: done\n", encoding="utf-8")

    statuses = _quality_rule_statuses(qfile)

    assert "done" not in statuses


def test_quality_rule_statuses_ignores_non_standard_values(tmp_path: Path) -> None:
    """Two-space indented values outside done/todo/exempt are not included."""
    qfile = tmp_path / "quality_scale.yaml"
    qfile.write_text("  custom_key: something_else\n", encoding="utf-8")

    statuses = _quality_rule_statuses(qfile)

    assert "something_else" not in statuses


def test_quality_rule_statuses_returns_all_three_standard_values(
    tmp_path: Path,
) -> None:
    """All three recognised status values can coexist."""
    qfile = tmp_path / "quality_scale.yaml"
    qfile.write_text("status: done\nstatus: todo\nstatus: exempt\n", encoding="utf-8")

    statuses = _quality_rule_statuses(qfile)

    assert statuses == {"done", "todo", "exempt"}


# ---------------------------------------------------------------------------
# _literal_string_assignments
# ---------------------------------------------------------------------------


def test_literal_string_assignments_finds_plain_assign(tmp_path: Path) -> None:
    """Simple NAME = 'value' assignments are extracted."""
    py_file = tmp_path / "const.py"
    py_file.write_text("FOO = 'bar'\n", encoding="utf-8")

    result = _literal_string_assignments(py_file)

    assert result == {"FOO": "bar"}


def test_literal_string_assignments_finds_annotated_assign(tmp_path: Path) -> None:
    """Annotated NAME: type = 'value' assignments are also extracted."""
    py_file = tmp_path / "const.py"
    py_file.write_text(
        "from typing import Final\nFOO: Final = 'baz'\n", encoding="utf-8"
    )

    result = _literal_string_assignments(py_file)

    assert result["FOO"] == "baz"


def test_literal_string_assignments_ignores_non_string_constants(
    tmp_path: Path,
) -> None:
    """Integer and None constants are not included."""
    py_file = tmp_path / "const.py"
    py_file.write_text("COUNT = 42\nFLAG = True\n", encoding="utf-8")

    result = _literal_string_assignments(py_file)

    assert result == {}


def test_literal_string_assignments_ignores_multi_target_assign(tmp_path: Path) -> None:
    """Tuple-unpacking assignments (a = b = 'x') are not extracted."""
    py_file = tmp_path / "const.py"
    py_file.write_text("A = B = 'x'\n", encoding="utf-8")

    result = _literal_string_assignments(py_file)

    assert result == {}


# ---------------------------------------------------------------------------
# _iter_strings
# ---------------------------------------------------------------------------


def test_iter_strings_returns_plain_string() -> None:
    """A bare string value yields itself."""
    assert _iter_strings("hello") == {"hello"}


def test_iter_strings_returns_dict_keys_and_values() -> None:
    """Both keys and values of a dict are included."""
    result = _iter_strings({"key": "value"})

    assert "key" in result
    assert "value" in result


def test_iter_strings_recurses_into_lists() -> None:
    """String items inside lists are extracted."""
    result = _iter_strings(["a", "b"])

    assert result == {"a", "b"}


def test_iter_strings_recurses_into_nested_structures() -> None:
    """Deeply nested strings in mixed dicts/lists are all returned."""
    data = {"outer": {"inner": ["deep"]}}
    result = _iter_strings(data)

    assert "deep" in result
    assert "outer" in result
    assert "inner" in result


def test_iter_strings_ignores_non_string_leaves() -> None:
    """Non-string leaf values such as integers are silently skipped."""
    result = _iter_strings({"count": 42, "flag": True, "name": "ok"})

    assert result == {"count", "flag", "name", "ok"}


def test_iter_strings_handles_empty_structures() -> None:
    """Empty containers return an empty set without raising."""
    assert _iter_strings({}) == set()
    assert _iter_strings([]) == set()


# ---------------------------------------------------------------------------
# _normalize_paths
# ---------------------------------------------------------------------------


def test_normalize_paths_extracts_v1_paths() -> None:
    """Valid /v1/... paths are extracted from the input strings."""
    result = _normalize_paths({"/v1/auth/login"})

    assert "/v1/auth/login" in result


def test_normalize_paths_strips_trailing_punctuation() -> None:
    """Trailing .,`)] characters are stripped from extracted paths."""
    result = _normalize_paths({"/v1/auth/login,", "/v1/device/info."})

    assert "/v1/auth/login" in result
    assert "/v1/device/info" in result


def test_normalize_paths_ignores_non_v1_strings() -> None:
    """Strings without /v1/ produce no output."""
    result = _normalize_paths({"no match here", "/v2/other"})

    assert result == set()


def test_normalize_paths_handles_multiple_paths_in_one_string() -> None:
    """Multiple /v1/... paths embedded in a single string are all captured."""
    result = _normalize_paths({"see /v1/auth/login and /v1/device/info here"})

    assert "/v1/auth/login" in result
    assert "/v1/device/info" in result


# ---------------------------------------------------------------------------
# reference_http_endpoints
# ---------------------------------------------------------------------------


def test_reference_http_endpoints_returns_empty_when_json_missing(
    tmp_path: Path,
) -> None:
    """When docs/jackery_complete_reference.json does not exist, return empty set."""
    root = tmp_path / "repo"
    root.mkdir()

    result = reference_http_endpoints(root)

    assert result == set()


def test_reference_http_endpoints_extracts_nested_paths(tmp_path: Path) -> None:
    """Paths nested inside reference JSON structures are extracted."""
    root = tmp_path / "repo"
    docs = root / "docs"
    docs.mkdir(parents=True)
    (docs / "jackery_complete_reference.json").write_text(
        json.dumps({"api": {"paths": ["/v1/auth/login", "/v1/device/list"]}}),
        encoding="utf-8",
    )

    result = reference_http_endpoints(root)

    assert "/v1/auth/login" in result
    assert "/v1/device/list" in result


# ---------------------------------------------------------------------------
# reference_mqtt_message_types
# ---------------------------------------------------------------------------


def test_reference_mqtt_message_types_excludes_jackery_prefix(tmp_path: Path) -> None:
    """Values that start with 'Jackery' are filtered out."""
    root = tmp_path / "repo"
    docs = root / "docs"
    docs.mkdir(parents=True)
    (docs / "jackery_complete_reference.json").write_text(
        json.dumps({"types": ["JackeryHeartbeat", "DeviceStatus"]}),
        encoding="utf-8",
    )
    (docs / "MQTT_PROTOCOL.md").write_text("", encoding="utf-8")

    result = reference_mqtt_message_types(root)

    assert "JackeryHeartbeat" not in result
    assert "DeviceStatus" in result


def test_reference_mqtt_message_types_excludes_short_camel_case(tmp_path: Path) -> None:
    """CamelCase strings with length <= 4 are not treated as message types."""
    root = tmp_path / "repo"
    docs = root / "docs"
    docs.mkdir(parents=True)
    (docs / "jackery_complete_reference.json").write_text(
        json.dumps({"types": ["DeviceStatus", "Ok"]}),
        encoding="utf-8",
    )
    (docs / "MQTT_PROTOCOL.md").write_text("", encoding="utf-8")

    result = reference_mqtt_message_types(root)

    assert "Ok" not in result
    assert "DeviceStatus" in result


def test_reference_mqtt_message_types_collects_backtick_types_from_doc(
    tmp_path: Path,
) -> None:
    """CamelCase identifiers in backticks from MQTT_PROTOCOL.md are included."""
    root = tmp_path / "repo"
    docs = root / "docs"
    docs.mkdir(parents=True)
    (docs / "jackery_complete_reference.json").write_text(
        json.dumps({}), encoding="utf-8"
    )
    (docs / "MQTT_PROTOCOL.md").write_text(
        "| messageType | description |\n| `DeviceHeartbeat` | periodic ping |\n",
        encoding="utf-8",
    )

    result = reference_mqtt_message_types(root)

    assert "DeviceHeartbeat" in result


def test_reference_mqtt_message_types_returns_empty_when_no_files(
    tmp_path: Path,
) -> None:
    """When neither reference JSON nor MQTT doc exists, return empty set."""
    root = tmp_path / "repo"
    root.mkdir()

    result = reference_mqtt_message_types(root)

    assert result == set()


# ---------------------------------------------------------------------------
# implemented_mqtt_message_types
# ---------------------------------------------------------------------------


def test_implemented_mqtt_message_types_only_includes_mqtt_message_prefix(
    tmp_path: Path,
) -> None:
    """Only constants whose names start with MQTT_MESSAGE_ are returned."""
    root = tmp_path / "repo"
    domain_dir = root / "custom_components" / "jackery_solarvault"
    domain_dir.mkdir(parents=True)
    (domain_dir / "const.py").write_text(
        "MQTT_MESSAGE_HEARTBEAT = 'DeviceHeartbeat'\n"
        "MQTT_TOPIC_PREFIX = 'jackery/'\n"
        "SERVICE_RENAME = 'rename_system'\n",
        encoding="utf-8",
    )

    result = implemented_mqtt_message_types(root)

    assert result == {"DeviceHeartbeat"}


def test_implemented_mqtt_message_types_returns_empty_without_mqtt_constants(
    tmp_path: Path,
) -> None:
    """No MQTT_MESSAGE_* constants results in an empty set."""
    root = tmp_path / "repo"
    domain_dir = root / "custom_components" / "jackery_solarvault"
    domain_dir.mkdir(parents=True)
    (domain_dir / "const.py").write_text(
        "DOMAIN = 'jackery_solarvault'\n", encoding="utf-8"
    )

    result = implemented_mqtt_message_types(root)

    assert result == set()


# ---------------------------------------------------------------------------
# _registered_service_name
# ---------------------------------------------------------------------------


def test_registered_service_name_returns_string_from_constant_node() -> None:
    """An ast.Constant holding a str value returns that string."""
    node = ast.Constant(value="my_service")

    result = _registered_service_name(node, {})

    assert result == "my_service"


def test_registered_service_name_resolves_name_from_constants_dict() -> None:
    """An ast.Name whose id exists in constants returns the mapped value."""
    node = ast.Name(id="SERVICE_FOO")

    result = _registered_service_name(node, {"SERVICE_FOO": "foo_service"})

    assert result == "foo_service"


def test_registered_service_name_returns_name_id_when_not_in_constants() -> None:
    """An ast.Name not found in constants falls back to the id itself."""
    node = ast.Name(id="UNKNOWN_CONSTANT")

    result = _registered_service_name(node, {})

    assert result == "UNKNOWN_CONSTANT"


def test_registered_service_name_returns_none_for_other_node_types() -> None:
    """Nodes that are neither Constant nor Name return None."""
    node = ast.Tuple(elts=[])

    result = _registered_service_name(node, {})

    assert result is None


# ---------------------------------------------------------------------------
# registered_services
# ---------------------------------------------------------------------------


def test_registered_services_returns_empty_when_services_py_missing(
    tmp_path: Path,
) -> None:
    """When services.py does not exist an empty set is returned without error."""
    root = tmp_path / "repo"
    domain_dir = root / "custom_components" / "jackery_solarvault"
    domain_dir.mkdir(parents=True)
    (domain_dir / "const.py").write_text(
        "DOMAIN = 'jackery_solarvault'\n", encoding="utf-8"
    )

    result = registered_services(root)

    assert result == set()


def test_registered_services_parses_literal_string_service_name(tmp_path: Path) -> None:
    """async_register calls with a literal second argument are recognised."""
    root = tmp_path / "repo"
    domain_dir = root / "custom_components" / "jackery_solarvault"
    domain_dir.mkdir(parents=True)
    (domain_dir / "const.py").write_text(
        "DOMAIN = 'jackery_solarvault'\n", encoding="utf-8"
    )
    (domain_dir / "services.py").write_text(
        "DOMAIN = 'jackery_solarvault'\n"
        "async def setup(hass):\n"
        "    hass.services.async_register(DOMAIN, 'literal_service', None)\n",
        encoding="utf-8",
    )

    result = registered_services(root)

    assert "literal_service" in result


def test_registered_services_ignores_non_domain_first_arg(tmp_path: Path) -> None:
    """Calls where the first arg is not the DOMAIN name are skipped."""
    root = tmp_path / "repo"
    domain_dir = root / "custom_components" / "jackery_solarvault"
    domain_dir.mkdir(parents=True)
    (domain_dir / "const.py").write_text("SERVICE_FOO = 'foo'\n", encoding="utf-8")
    (domain_dir / "services.py").write_text(
        "DOMAIN = 'jackery_solarvault'\n"
        "async def setup(hass):\n"
        "    hass.services.async_register('other_domain', 'other_service', None)\n",
        encoding="utf-8",
    )

    result = registered_services(root)

    assert result == set()


# ---------------------------------------------------------------------------
# service_yaml_names
# ---------------------------------------------------------------------------


def test_service_yaml_names_extracts_keys(tmp_path: Path) -> None:
    """service_yaml_names returns top-level YAML keys from services.yaml."""
    root = tmp_path / "repo"
    domain_dir = root / "custom_components" / "jackery_solarvault"
    domain_dir.mkdir(parents=True)
    (domain_dir / "services.yaml").write_text(
        "svc_one:\n  description: one\nsvc_two:\n  description: two\n",
        encoding="utf-8",
    )

    result = service_yaml_names(root)

    assert result == {"svc_one", "svc_two"}


# ---------------------------------------------------------------------------
# strings_service_names
# ---------------------------------------------------------------------------


def test_strings_service_names_extracts_service_keys(tmp_path: Path) -> None:
    """strings_service_names returns service names from strings.json."""
    root = tmp_path / "repo"
    domain_dir = root / "custom_components" / "jackery_solarvault"
    domain_dir.mkdir(parents=True)
    (domain_dir / "strings.json").write_text(
        json.dumps({"services": {"svc_one": {}, "svc_two": {}}}),
        encoding="utf-8",
    )

    result = strings_service_names(root)

    assert result == {"svc_one", "svc_two"}


def test_strings_service_names_returns_empty_when_no_services_key(
    tmp_path: Path,
) -> None:
    """When 'services' key is absent from strings.json, return empty set."""
    root = tmp_path / "repo"
    domain_dir = root / "custom_components" / "jackery_solarvault"
    domain_dir.mkdir(parents=True)
    (domain_dir / "strings.json").write_text(
        json.dumps({"title": "Jackery"}), encoding="utf-8"
    )

    result = strings_service_names(root)

    assert result == set()


def test_strings_service_names_returns_empty_when_services_is_not_dict(
    tmp_path: Path,
) -> None:
    """When 'services' is a list (not a dict), return empty set."""
    root = tmp_path / "repo"
    domain_dir = root / "custom_components" / "jackery_solarvault"
    domain_dir.mkdir(parents=True)
    (domain_dir / "strings.json").write_text(
        json.dumps({"services": ["svc_one", "svc_two"]}),
        encoding="utf-8",
    )

    result = strings_service_names(root)

    assert result == set()


# ---------------------------------------------------------------------------
# check_reference_coverage – additional scenarios
# ---------------------------------------------------------------------------


def test_check_reference_coverage_warns_when_reference_json_absent(
    tmp_path: Path,
) -> None:
    """A warning is emitted when the structured reference JSON does not exist."""
    root = _copy_reference_fixture(tmp_path)

    report = check_reference_coverage(root)

    assert any("not found" in warning for warning in report.warnings)


def test_check_reference_coverage_no_warning_when_reference_json_present(
    tmp_path: Path,
) -> None:
    """No warning is emitted for missing JSON when the file exists."""
    root = _copy_reference_fixture(tmp_path)
    reference_path = root / "docs" / "jackery_complete_reference.json"
    reference_path.write_text(json.dumps({}), encoding="utf-8")

    report = check_reference_coverage(root)

    assert not any("not found" in warning for warning in report.warnings)


def test_check_reference_coverage_detects_quality_scale_missing_done(
    tmp_path: Path,
) -> None:
    """An error is raised when quality_scale.yaml has no 'done' status."""
    root = _copy_reference_fixture(tmp_path)
    quality_path = (
        root / "custom_components" / "jackery_solarvault" / "quality_scale.yaml"
    )
    quality_path.write_text("some_rule:\n  status: todo\n", encoding="utf-8")

    report = check_reference_coverage(root)

    assert not report.ok
    assert any("must keep internal completed rule statuses" in e for e in report.errors)


def test_check_reference_coverage_detects_extra_registered_service(
    tmp_path: Path,
) -> None:
    """A service registered in services.py but absent from services.yaml is an error."""
    root = _copy_reference_fixture(tmp_path)
    services_path = root / "custom_components" / "jackery_solarvault" / "services.py"
    services_py = services_path.read_text(encoding="utf-8")
    services_path.write_text(
        services_py + "\n"
        "async def _extra(hass):\n"
        "    hass.services.async_register(DOMAIN, 'phantom_service', None)\n",
        encoding="utf-8",
    )

    report = check_reference_coverage(root)

    assert not report.ok
    assert any(
        "services.yaml and services.py registrations differ" in e for e in report.errors
    )
    assert any("phantom_service" in e for e in report.errors)


def test_check_reference_coverage_accumulates_multiple_errors(tmp_path: Path) -> None:
    """Multiple independent failures all appear in the errors tuple."""
    root = _copy_reference_fixture(tmp_path)
    manifest_path = root / "custom_components" / "jackery_solarvault" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["quality_scale"] = "bronze"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    mqtt_path = root / "docs" / "MQTT_PROTOCOL.md"
    mqtt_path.write_text("| `AnotherUnimplementedType` |\n", encoding="utf-8")

    report = check_reference_coverage(root)

    assert len(report.errors) >= 2


# ---------------------------------------------------------------------------
# main() CLI entry point
# ---------------------------------------------------------------------------


def test_main_returns_zero_on_success(tmp_path: Path) -> None:
    """main() returns 0 when all coverage checks pass."""
    root = _copy_reference_fixture(tmp_path)
    with patch("sys.argv", ["check_reference_coverage", "--root", str(root)]):
        return_code = main()

    assert return_code == 0


def test_main_returns_one_on_failure(tmp_path: Path) -> None:
    """main() returns 1 when at least one coverage error exists."""
    root = _copy_reference_fixture(tmp_path)
    manifest_path = root / "custom_components" / "jackery_solarvault" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["quality_scale"] = "gold"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with patch("sys.argv", ["check_reference_coverage", "--root", str(root)]):
        return_code = main()

    assert return_code == 1


def test_main_prints_errors_to_stderr(tmp_path: Path) -> None:
    """Errors are written to stderr prefixed with 'ERROR:'."""
    root = _copy_reference_fixture(tmp_path)
    manifest_path = root / "custom_components" / "jackery_solarvault" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["quality_scale"] = "silver"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    stderr_capture = StringIO()
    with (
        patch("sys.argv", ["check_reference_coverage", "--root", str(root)]),
        patch("sys.stderr", stderr_capture),
    ):
        main()

    stderr_output = stderr_capture.getvalue()
    assert "ERROR:" in stderr_output
    assert "manifest quality_scale must remain custom" in stderr_output


def test_main_prints_warnings_to_stderr(tmp_path: Path) -> None:
    """Warnings are written to stderr prefixed with 'WARNING:' even on success."""
    root = _copy_reference_fixture(tmp_path)
    # Fixture has no reference JSON so a warning about the missing file is emitted.

    stderr_capture = StringIO()
    with (
        patch("sys.argv", ["check_reference_coverage", "--root", str(root)]),
        patch("sys.stderr", stderr_capture),
    ):
        return_code = main()

    assert return_code == 0
    assert "WARNING:" in stderr_capture.getvalue()


def test_main_prints_success_to_stdout_when_passing(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    """On a passing run main() prints a success message to stdout."""
    root = _copy_reference_fixture(tmp_path)

    with patch("sys.argv", ["check_reference_coverage", "--root", str(root)]):
        return_code = main()

    assert return_code == 0
    captured = capsys.readouterr()
    assert "passed" in captured.out
