"""Lightweight static checks for integration code hygiene."""

import ast
import importlib.util
import json
import pathlib
import re
import sys
import types

import yaml

CUSTOM_COMPONENT = pathlib.Path("custom_components/jackery_solarvault")
CLIENT_PACKAGE = pathlib.Path("custom_components/jackery_solarvault/client")
API_IMPLEMENTATION = CLIENT_PACKAGE / "api.py"
MQTT_IMPLEMENTATION = CLIENT_PACKAGE / "mqtt_push.py"
LOG_METHODS = {"debug", "info", "warning", "error", "exception"}
PERCENT_PLACEHOLDER = re.compile(
    r"(?<!%)%(?:\([^)]+\))?[#0 +\-]*(?:\d+|\*)?(?:\.\d+)?[hlL]?[diouxXeEfFgGcrs]"
)


def _load_util_module():
    package_dir = CUSTOM_COMPONENT
    sys.modules.setdefault("custom_components", types.ModuleType("custom_components"))
    package = types.ModuleType("custom_components.jackery_solarvault")
    package.__path__ = [str(package_dir)]
    sys.modules.setdefault("custom_components.jackery_solarvault", package)

    const_spec = importlib.util.spec_from_file_location(
        "custom_components.jackery_solarvault.const",
        package_dir / "const.py",
    )
    assert const_spec is not None
    const_module = importlib.util.module_from_spec(const_spec)
    sys.modules[const_spec.name] = const_module
    assert const_spec.loader is not None
    const_spec.loader.exec_module(const_module)

    spec = importlib.util.spec_from_file_location(
        "custom_components.jackery_solarvault.util",
        package_dir / "util.py",
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


util = _load_util_module()


def _python_sources() -> list[pathlib.Path]:
    return sorted(CUSTOM_COMPONENT.glob("*.py"))


def _translation_sources() -> dict[str, str]:
    """Return strings.json and every locale translation file."""
    paths = [
        CUSTOM_COMPONENT / "strings.json",
        *sorted((CUSTOM_COMPONENT / "translations").glob("*.json")),
    ]
    return {
        path.relative_to(CUSTOM_COMPONENT).as_posix(): path.read_text(encoding="utf-8")
        for path in paths
    }


def _exception_names(node: ast.AST | None) -> set[str]:
    """Return simple exception names from an except handler type expression."""
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Tuple):
        return {item.id for item in node.elts if isinstance(item, ast.Name)}
    return set()


def _handler_aborts_reauth_entry_missing(handler: ast.ExceptHandler) -> bool:
    """Return whether an except handler aborts with the missing reauth entry reason."""
    for node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
        if not (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "async_abort"
        ):
            continue
        for keyword in node.keywords:
            if (
                keyword.arg == "reason"
                and isinstance(keyword.value, ast.Name)
                and keyword.value.id == "FLOW_ABORT_REAUTH_ENTRY_MISSING"
            ):
                return True
    return False


def _reauth_entry_lookup_is_guarded(config_tree: ast.Module) -> bool:
    """Return whether _get_reauth_entry is guarded against missing entries."""
    for node in ast.walk(config_tree):
        if not (
            isinstance(node, ast.AsyncFunctionDef)
            and node.name == "async_step_reauth_confirm"
        ):
            continue
        for try_node in (
            child for child in ast.walk(node) if isinstance(child, ast.Try)
        ):
            calls_reauth_entry = any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == "_get_reauth_entry"
                for child in ast.walk(ast.Module(body=try_node.body, type_ignores=[]))
            )
            if not calls_reauth_entry:
                continue
            for handler in try_node.handlers:
                if {"KeyError", "RuntimeError"} <= _exception_names(
                    handler.type
                ) and _handler_aborts_reauth_entry_missing(handler):
                    return True
    return False


def test_manifest_treats_recorder_as_optional_after_dependency() -> None:
    """HA fixture collection should not hard-require recorder setup."""
    manifest = json.loads(
        (CUSTOM_COMPONENT / "manifest.json").read_text(encoding="utf-8")
    )

    assert "recorder" not in manifest.get("dependencies", [])
    assert "recorder" in manifest.get("after_dependencies", [])
    assert manifest["iot_class"] == "cloud_polling"


def test_no_duplicate_literal_dict_keys() -> None:
    """Catch accidental duplicate payload keys such as login registerAppId."""
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Dict):
                continue
            seen: set[str] = set()
            for key in node.keys:
                if isinstance(key, ast.Constant) and isinstance(key.value, str):
                    assert key.value not in seen, (
                        f"{path}:{node.lineno} duplicates {key.value!r}"
                    )
                    seen.add(key.value)


def test_logging_format_argument_counts_match() -> None:
    """Prevent lazy-logging format strings from receiving extra/missing args."""
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr in LOG_METHODS
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and isinstance(node.args[0].value, str)
            ):
                continue
            fmt = node.args[0].value.replace("%%", "")
            expected = len(PERCENT_PLACEHOLDER.findall(fmt))
            actual = len(node.args) - 1
            assert actual == expected, (
                f"{path}:{node.lineno} logging args mismatch: "
                f"expected {expected}, got {actual}, format={node.args[0].value!r}"
            )


def test_mqtt_wire_message_literals_are_centralized() -> None:
    """Keep documented MQTT messageType strings in const.py only."""
    forbidden = {
        "DevicePropertyChange",
        "ControlCombine",
        "QueryCombineData",
        "UploadCombineData",
        "UploadIncrementalCombineData",
        "UploadWeatherPlan",
        "QueryWeatherPlan",
        "SendWeatherAlert",
        "CancelWeatherAlert",
        "DownloadDeviceSchedule",
        "QuerySubDeviceGroupProperty",
    }
    for path in _python_sources():
        if path.name == "const.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in forbidden:
                raise AssertionError(
                    f"{path}:{node.lineno} uses raw MQTT messageType {node.value!r}; "
                    "use const.py instead"
                )


def test_period_reset_descriptions_use_date_type_constants() -> None:
    """Prevent drift between period sensors and documented dateType constants."""
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != "reset_period":
                    continue
                if isinstance(keyword.value, ast.Constant) and isinstance(
                    keyword.value.value, str
                ):
                    raise AssertionError(
                        f"{path}:{keyword.value.lineno} uses raw reset_period "
                        f"{keyword.value.value!r}; use DATE_TYPE_* constants"
                    )


def test_const_exports_are_not_reassigned() -> None:
    """Avoid accidental duplicate constant assignments in const.py."""
    path = CUSTOM_COMPONENT / "const.py"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    seen: dict[str, int] = {}
    for node in tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            name = node.target.id
        elif (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
        ):
            name = node.targets[0].id
        else:
            continue
        assert name not in seen, (
            f"{path}:{node.lineno} reassigns {name}; first assignment at line {seen[name]}"
        )
        seen[name] = node.lineno


def test_ct_wire_keys_are_centralized() -> None:
    """Keep CT/Smart-Meter wire keys centralized in const.py."""
    forbidden = {
        "aPhasePw",
        "bPhasePw",
        "cPhasePw",
        "tPhasePw",
        "anPhasePw",
        "bnPhasePw",
        "cnPhasePw",
        "tnPhasePw",
        "power1",
        "power2",
        "power3",
    }
    for path in _python_sources():
        if path.name == "const.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in forbidden:
                raise AssertionError(
                    f"{path}:{node.lineno} uses raw CT wire key {node.value!r}; "
                    "use const.py instead"
                )


def test_mqtt_credential_keys_are_centralized() -> None:
    """Keep login/MQTT credential dict keys centralized in const.py."""
    forbidden = {"mqttPassWord", "userId", "client_id", "user_id"}
    for path in _python_sources():
        if path.name == "const.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in forbidden:
                raise AssertionError(
                    f"{path}:{node.lineno} uses raw MQTT credential key {node.value!r}; "
                    "use const.py instead"
                )


def test_mqtt_topic_literals_are_centralized() -> None:
    """Keep documented MQTT topic layout in const.py only."""
    forbidden = {"hb/app", "hb/app/"}
    for path in _python_sources():
        if path.name == "const.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in forbidden:
                raise AssertionError(
                    f"{path}:{node.lineno} uses raw MQTT topic prefix {node.value!r}; "
                    "use MQTT_TOPIC_PREFIX from const.py instead"
                )


def test_app_period_stat_keys_are_centralized() -> None:
    """Keep app period/trend stat-key strings centralized in const.py."""
    forbidden = {
        "totalInCtEnergy",
        "totalOutCtEnergy",
        "totalChgEgy",
        "totalDisChgEgy",
    }
    for path in _python_sources():
        if path.name == "const.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in forbidden:
                raise AssertionError(
                    f"{path}:{node.lineno} uses raw app stat key {node.value!r}; "
                    "use APP_STAT_* constants from const.py instead"
                )


def _const_string_values(name: str) -> tuple[str, ...]:
    """Read a tuple/frozenset/list constant of strings from const.py via AST."""
    const_tree = ast.parse((CUSTOM_COMPONENT / "const.py").read_text(encoding="utf-8"))
    env: dict[str, str] = {}
    target_node: ast.AST | None = None
    for node in const_tree.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            if isinstance(node.value, ast.Constant) and isinstance(
                node.value.value, str
            ):
                env[node.target.id] = node.value.value
            if node.target.id == name:
                target_node = node.value
    assert target_node is not None, f"const.py is missing {name}"

    def eval_node(node: ast.AST) -> object:
        """Implement eval node."""
        if isinstance(node, ast.Constant):
            return node.value
        if isinstance(node, ast.Name):
            return env[node.id]
        if isinstance(node, ast.Tuple | ast.List | ast.Set):
            return tuple(eval_node(item) for item in node.elts)
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "frozenset"
        ):
            return tuple(eval_node(item) for item in node.args[0].elts)  # type: ignore[index,union-attr]
        raise AssertionError(
            f"Unsupported const expression in {name}: {ast.dump(node)}"
        )

    values = eval_node(target_node)
    assert isinstance(values, tuple)
    assert all(isinstance(value, str) for value in values)
    return values


def test_preserved_fast_payload_keys_do_not_include_mqtt_protocol_values() -> None:
    """Only payload sections should be carried over between HTTP refreshes."""
    values = _const_string_values("PRESERVED_FAST_PAYLOAD_KEYS")
    assert values == (
        "ct_meter",
        "weather_plan",
        "task_plan",
        "notice",
        "mqtt_last",
    )
    forbidden_fragments = ("Query", "Upload", "Control", "DevicePropertyChange")
    for value in values:
        assert not any(fragment in value for fragment in forbidden_fragments)


def test_app_specific_subdevice_markers_are_centralized() -> None:
    """Avoid scattering magic devType/subType numbers from MQTT_PROTOCOL.md."""
    forbidden_contexts = ("FIELD_DEV_TYPE", "FIELD_DEVICE_TYPE", "FIELD_SUB_TYPE")
    for path in _python_sources():
        if path.name == "const.py":
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and node.value in {"2", "3"}):
                continue
            line = source.splitlines()[node.lineno - 1]
            if any(context in line for context in forbidden_contexts):
                raise AssertionError(
                    f"{path}:{node.lineno} uses raw subdevice marker {node.value!r}; "
                    "use SUBDEVICE_TYPE_* constants"
                )


def _class_constant_int(tree: ast.Module, class_name: str, attr_name: str) -> int:
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef) or node.name != class_name:
            continue
        for stmt in node.body:
            if not isinstance(stmt, ast.Assign):
                continue
            if not any(
                isinstance(target, ast.Name) and target.id == attr_name
                for target in stmt.targets
            ):
                continue
            if isinstance(stmt.value, ast.Constant) and isinstance(
                stmt.value.value, int
            ):
                return stmt.value.value
    raise AssertionError(f"Missing {class_name}.{attr_name} integer constant")


def test_config_entries_do_not_use_internal_version_ladder() -> None:
    """The first public package must not carry internal entry-version history."""
    const_source = (CUSTOM_COMPONENT / "const.py").read_text(encoding="utf-8")
    init_source = (CUSTOM_COMPONENT / "__init__.py").read_text(encoding="utf-8")
    config_tree = ast.parse(
        (CUSTOM_COMPONENT / "config_flow.py").read_text(encoding="utf-8")
    )

    assert "CONFIG_ENTRY_VERSION" not in const_source
    assert "CONF_DEVICE_ID" not in const_source
    assert "CONF_SYSTEM_ID" not in const_source
    assert "CONF_SCAN_INTERVAL" not in const_source
    assert "async_migrate_entry" not in init_source
    assert "_async_clean_entry_config" not in init_source
    assert "async_update_entry(entry" not in init_source
    assert "version=" not in init_source
    assert _class_constant_int(config_tree, "JackeryConfigFlow", "VERSION") == 1


def test_ci_runs_all_pure_tests_and_compileall() -> None:
    """Keep GitHub Actions aligned with the local validation command."""
    workflow = pathlib.Path(".github/workflows/validate.yml").read_text(
        encoding="utf-8"
    )
    assert "python -m compileall -q custom_components tests" in workflow
    assert "pytest -q -p no:cacheprovider" in workflow
    assert "tests/test_power_math.py" not in workflow


def test_standalone_client_util_mirror_stays_in_sync() -> None:
    """The bundled standalone client must expose the same pure helpers."""
    assert (CUSTOM_COMPONENT / "util.py").read_text(encoding="utf-8") == (
        CLIENT_PACKAGE / "util.py"
    ).read_text(encoding="utf-8")


def test_workflow_cache_paths_reference_existing_dependency_files() -> None:
    """Cache dependency paths in workflows should not silently point at typos."""
    missing: list[str] = []
    for workflow in pathlib.Path(".github/workflows").glob("*.y*ml"):
        source = workflow.read_text(encoding="utf-8")
        for match in re.finditer(r"^\s{12}([^\s#][^\n#]*)$", source, re.MULTILINE):
            candidate = match.group(1).strip()
            if not candidate.startswith("requirements") or not candidate.endswith(".txt"):
                continue
            if not pathlib.Path(candidate).exists():
                missing.append(f"{workflow}:{candidate}")
    assert not missing


def test_ruff_baseline_uses_pinned_python_314_exception_formatting() -> None:
    """Ruff baseline must not drift back to pre-3.14 exception formatting."""
    workflow = pathlib.Path(".github/workflows/ruff-baseline.yml").read_text(
        encoding="utf-8"
    )

    assert "RUFF_VERSION:" in workflow
    assert "RUFF_TARGET_VERSION: py314" in workflow
    assert '"ruff==${RUFF_VERSION}"' in workflow
    assert "python -m pip install --upgrade pip ruff" not in workflow
    assert "python -m ruff --version" in workflow
    assert workflow.count('--target-version "${RUFF_TARGET_VERSION}"') == 6
    assert (
        'python -m ruff format . --target-version "${RUFF_TARGET_VERSION}"'
        in workflow
    )
    assert "python scripts/verify_py314_exception_style.py --fix" in workflow
    assert "python scripts/verify_py314_exception_style.py" in workflow
    assert "--unsafe-fixes" in workflow
    assert "--exit-zero" in workflow
    assert "--output-format=concise" in workflow


def test_py314_exception_style_guard_detects_reverted_multi_except_headers() -> None:
    """The workflow guard should fail old no-``as`` multi-exception headers only."""
    script = pathlib.Path("scripts/verify_py314_exception_style.py")
    spec = importlib.util.spec_from_file_location(
        "verify_py314_exception_style", script
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.violations_in_text(
        "try:\n"
        "    pass\n"
        "except (ValueError, TypeError):\n"
        "    pass\n"
    ) == [(3, "except (ValueError, TypeError):")]
    assert module.violations_in_text(
        "try:\n"
        "    pass\n"
        "except (\n"
        "    ValueError,\n"
        "    TypeError,\n"
        "):\n"
        "    pass\n"
    ) == [
        (
            3,
            "except (\n"
            "    ValueError,\n"
            "    TypeError,\n"
            "):",
        )
    ]
    assert (
        module.violations_in_text(
            "try:\n"
            "    pass\n"
            "except ValueError, TypeError:\n"
            "    pass\n"
        )
        == []
    )
    assert (
        module.violations_in_text(
            "try:\n"
            "    pass\n"
            "except (ValueError, TypeError) as err:\n"
            "    raise RuntimeError from err\n"
        )
        == []
    )
    assert module.fix_text(
        "try:\n"
        "    pass\n"
        "except (ValueError, TypeError):\n"
        "    pass\n"
    ) == (
        "try:\n"
        "    pass\n"
        "except ValueError, TypeError:\n"
        "    pass\n"
    )
    assert module.fix_text(
        "try:\n"
        "    pass\n"
        "except (ValueError, TypeError) as err:\n"
        "    raise RuntimeError from err\n"
    ) == (
        "try:\n"
        "    pass\n"
        "except (ValueError, TypeError) as err:\n"
        "    raise RuntimeError from err\n"
    )


def test_period_source_diagnostics_stay_minimal() -> None:
    """Period sensors should expose calculation facts, not redundant contracts."""
    const_source = (CUSTOM_COMPONENT / "const.py").read_text(encoding="utf-8")
    sensor_source = (CUSTOM_COMPONENT / "sensor.py").read_text(encoding="utf-8")
    (CUSTOM_COMPONENT / "util.py").read_text(encoding="utf-8")
    for removed in (
        "SOURCE_CONTRACT_",
        "SOURCE_KIND_",
        "CHART_KIND_APP_BUCKET_SERIES",
        'attrs["source_contract"]',
        'attrs["source_kind"]',
        'attrs["chart_kind"]',
        'attrs["external_statistics_id"]',
        'attrs["external_statistics_bucket"]',
        'attrs["native_source"]',
        'attrs["period_total_method"]',
        'attrs["server_total_used"]',
    ):
        assert removed not in const_source
        assert removed not in sensor_source
    for kept in (
        '"source_section"',
        '"source_key"',
        'attrs["chart_series_key"]',
        'attrs["chart_series_sum"]',
        'attrs["server_total"]',
        'attrs["period_values_json"]',
        'attrs["period_values_by_label_json"]',
        'attrs["request"]',
    ):
        assert kept in sensor_source


def test_data_source_priority_contract_exists() -> None:
    """Keep the guarded month-backfill rule documented next to the code."""
    contract = pathlib.Path("docs/DATA_SOURCE_PRIORITY.md").read_text(encoding="utf-8")
    assert "Do not use week values to repair month/year/lifetime totals." in contract
    assert "Month values may only guard year totals" in contract
    assert "same endpoint family" in contract
    assert "Do not use year values to repair lifetime totals." in contract
    assert (
        "month` and `year` must never fall back to `beginDate=endDate=today`"
        in contract
    )


def test_diagnostics_anonymize_outer_payload_keys() -> None:
    """Diagnostics must not expose device IDs or serials as raw map keys."""
    source = (CUSTOM_COMPONENT / "diagnostics.py").read_text(encoding="utf-8")
    assert "def _redacted_payload_map(" in source
    assert 'devices = _redacted_payload_map(coordinator.data or {}, "device")' in source
    for forbidden in (
        "dev_id: async_redact_data",
        "key: async_redact_data",
        "sn: async_redact_data",
        "sn_or_id: async_redact_data",
    ):
        assert forbidden not in source
    for prefix in (
        "property_response",
        "device_statistic_response",
        "device_period_stat_response",
        "battery_pack_response",
        "ota_response",
        "location_response",
    ):
        assert f'"{prefix}"' in source


def test_diagnostics_redaction_keys_cover_sensitive_jackery_fields() -> None:
    """Keep diagnostics aligned with HA's share-safe diagnostics rule."""
    const_source = (CUSTOM_COMPONENT / "const.py").read_text(encoding="utf-8")
    redaction_block = const_source.split("REDACT_KEYS: Final = {", 1)[1].split(
        "}\n\n# MQTT", 1
    )[0]
    for required in (
        "CONF_MQTT_MAC_ID",
        "CONF_REGION_CODE",
        "FIELD_TOKEN",
        "FIELD_MQTT_PASSWORD",
        "FIELD_DEVICE_ID",
        "FIELD_SYSTEM_ID",
        "FIELD_DEVICE_SN",
        "FIELD_SYSTEM_SN",
        "FIELD_LONGITUDE",
        "FIELD_LATITUDE",
        '"email"',
        '"phone"',
    ):
        assert required in redaction_block


def test_mqtt_diagnostics_do_not_expose_mac_id_suffix() -> None:
    """Diagnostics may report credential source, but not device-correlating IDs."""
    coordinator_source = (CUSTOM_COMPONENT / "coordinator.py").read_text(
        encoding="utf-8"
    )

    assert 'diag["credential_mac_id_source"] = self.api.mqtt_mac_id_source' in (
        coordinator_source
    )
    assert "credential_mac_id_suffix" not in coordinator_source


def test_data_source_priority_documents_minimal_attributes_and_payload_debug_log() -> (
    None
):
    """Source diagnostics must be lean; raw parser proof belongs in debug JSONL."""
    contract = pathlib.Path("docs/DATA_SOURCE_PRIORITY.md").read_text(encoding="utf-8")
    assert "Minimal entity diagnostic attributes" in contract
    assert "jackery_solarvault_payload_debug.jsonl" in contract
    assert "source_contract=" not in contract
    assert "source_kind=" not in contract


def test_entity_platforms_use_shared_unique_id_append_helper() -> None:
    """Keep duplicate unique_id filtering in one shared setup helper."""
    platform_files = {
        "binary_sensor.py",
        "button.py",
        "number.py",
        "select.py",
        "sensor.py",
        "switch.py",
        "text.py",
    }
    for name in platform_files:
        source = (CUSTOM_COMPONENT / name).read_text(encoding="utf-8")
        assert "append_unique_entity(" in source, name
        assert "seen_unique_ids.add" not in source, name
        assert "Skip duplicate" not in source, name


def test_unique_id_helper_is_the_only_duplicate_entity_skip_logger() -> None:
    """Do not copy/paste platform-local duplicate entity logging again."""
    for path in _python_sources():
        source = path.read_text(encoding="utf-8")
        if path.name == "util.py":
            assert "Skip duplicate %s unique_id=%s" in source
            continue
        assert "Skip duplicate" not in source, path


def test_unique_id_contract_is_documented_and_followed() -> None:
    """Unique IDs must stay independent from names/translations."""
    contract = pathlib.Path("docs/UNIQUE_ID_CONTRACT.md").read_text(encoding="utf-8")
    assert "<device_id>_<stable_key_suffix>" in contract
    assert "deviceName" in contract
    assert "translation keys" in contract

    entity_source = (CUSTOM_COMPONENT / "entity.py").read_text(encoding="utf-8")
    assert 'self._attr_unique_id = f"{device_id}_{key_suffix}"' in entity_source
    forbidden_fragments = {
        "FIELD_DEVICE_NAME",
        "FIELD_WNAME",
        "translation_key",
        "name=",
    }
    assignment_line = next(
        line.strip()
        for line in entity_source.splitlines()
        if "self._attr_unique_id" in line
    )
    for fragment in forbidden_fragments:
        assert fragment not in assignment_line


def test_battery_pack_unique_ids_keep_stable_index_suffix() -> None:
    """Battery-pack entities must not use serial/name fields for unique_id."""
    sensor_source = (CUSTOM_COMPONENT / "sensor.py").read_text(encoding="utf-8")
    assert 'f"battery_pack_{pack_index}_{description.key}"' in sensor_source
    pack_class = sensor_source.split(
        "class JackeryBatteryPackSensor(JackeryEntity, SensorEntity):", 1
    )[1].split("class JackerySmartMeterSensor", 1)[0]
    pack_init = pack_class.split("    def __init__(", 1)[1].split(
        "    @property\n    def _pack", 1
    )[0]
    assert "FIELD_DEVICE_NAME" not in pack_init
    assert "FIELD_WNAME" not in pack_init
    assert "FIELD_SN" not in pack_init


def test_data_quality_repair_issue_is_wired_with_guarded_year_backfill() -> None:
    """Contradictory app data still creates diagnostics around guarded states."""
    const_source = (CUSTOM_COMPONENT / "const.py").read_text(encoding="utf-8")
    coordinator_source = (CUSTOM_COMPONENT / "coordinator.py").read_text(
        encoding="utf-8"
    )
    util_source = (CUSTOM_COMPONENT / "util.py").read_text(encoding="utf-8")
    translation_sources = _translation_sources()
    data_priority = pathlib.Path("docs/DATA_SOURCE_PRIORITY.md").read_text(
        encoding="utf-8"
    )

    assert "PAYLOAD_DATA_QUALITY" in const_source
    assert "REPAIR_ISSUE_APP_DATA_INCONSISTENCY" in const_source
    assert "app_data_quality_warnings(entry, today=today)" in coordinator_source
    assert "_async_update_data_quality_issue" in coordinator_source
    assert "normalized_data_quality_warnings(warnings)" in coordinator_source
    assert "format_data_quality_warning(warning)" in coordinator_source
    assert "DATA_QUALITY_REPAIR_EXAMPLE_LIMIT" in coordinator_source
    assert "async_create_issue" in coordinator_source
    assert "async_delete_issue" in coordinator_source
    assert "app_data_quality_warnings" in util_source
    assert "normalized_data_quality_warnings" in util_source
    assert "format_data_quality_warning" in util_source
    assert "DATA_QUALITY_KEY_SOURCE_VALUE" in const_source
    for path, source in translation_sources.items():
        assert "app_data_inconsistency" in source, path
        assert "{examples}" in source, path
        assert "{source_section}" not in source, path
        assert "{reference_section}" not in source, path
    assert "same-endpoint month backfill" in data_priority
    assert "data_quality" in data_priority


def test_services_yaml_matches_registered_services_and_validates_numeric_ids() -> None:
    """Keep services.yaml, const.py field constants, and setup schemas aligned."""
    services = yaml.safe_load(
        (CUSTOM_COMPONENT / "services.yaml").read_text(encoding="utf-8")
    )
    const_source = (CUSTOM_COMPONENT / "const.py").read_text(encoding="utf-8")
    services_source = (CUSTOM_COMPONENT / "services.py").read_text(encoding="utf-8")
    init_source = (CUSTOM_COMPONENT / "__init__.py").read_text(encoding="utf-8")

    for service in (
        "rename_system",
        "refresh_weather_plan",
        "delete_storm_alert",
    ):
        assert service in services
        assert f'SERVICE_{service.upper()}: Final = "{service}"' in const_source

    assert set(services["rename_system"]["fields"]) == {"system_id", "new_name"}
    assert set(services["refresh_weather_plan"]["fields"]) == {"device_id"}
    assert set(services["delete_storm_alert"]["fields"]) == {"device_id", "alert_id"}

    assert 'SERVICE_NUMERIC_ID_PATTERN: Final = r"^\\s*[0-9]+\\s*$"' in const_source
    # Schemas live in services.py alongside the handlers that consume them.
    assert "SERVICE_FIELD_SYSTEM_ID): vol.All(" in services_source
    assert "SERVICE_FIELD_DEVICE_ID): vol.All(" in services_source
    # System rename keeps the strict numeric-id contract; device-id schemas
    # accept HA device-registry IDs (UUID-style) too, so the device selector
    # in services.yaml can hand them through unchanged.
    assert "cv.string, vol.Match(SERVICE_NUMERIC_ID_PATTERN)" in services_source
    assert "SERVICE_FIELD_ALERT_ID): vol.All(" in services_source
    assert "SERVICE_NON_EMPTY_TEXT_PATTERN" in services_source
    assert "str.strip" not in services_source
    assert "str.strip" not in init_source


def test_readme_documents_period_and_data_quality_behavior() -> None:
    """Users should get the same period/source rules that the code enforces."""
    readme = pathlib.Path("README.md").read_text(encoding="utf-8")
    assert "Week: Monday to Sunday" in readme
    assert "Month: calendar month" in readme
    assert "Year: calendar year" in readme
    assert "Weekly values are not used to repair monthly" in readme
    assert "same-endpoint monthly values" in readme
    assert "repair issue" in readme and "diagnostics export" in readme
    assert "hb/app/**REDACTED**/" in readme
    assert "dropped payloads" in readme


def test_refresh_auth_errors_trigger_reauth_not_update_failed() -> None:
    """Rejected credentials during refresh should start HA reauth instead of log-spamming."""
    coordinator_source = (CUSTOM_COMPONENT / "coordinator.py").read_text(
        encoding="utf-8"
    )
    assert "ConfigEntryAuthFailed" in coordinator_source
    assert (
        "Jackery credentials were rejected during property refresh"
        in coordinator_source
    )
    assert (
        "Jackery credentials were rejected while fetching extended device data"
        in coordinator_source
    )
    assert (
        "Jackery credentials were rejected while fetching system data"
        in coordinator_source
    )
    assert "Auth revoked (likely another session logged in)" not in coordinator_source


def test_reauth_flow_handles_missing_entry_without_assertion() -> None:
    """Malformed reauth contexts should abort cleanly instead of raising AssertionError."""
    config_flow_source = (CUSTOM_COMPONENT / "config_flow.py").read_text(
        encoding="utf-8"
    )
    config_flow_tree = ast.parse(config_flow_source)
    translation_sources = _translation_sources()

    # Reauth uses HA's _get_reauth_entry() helper (HA 2024.6+) wrapped in a
    # try/except for (KeyError, RuntimeError) to abort cleanly when the
    # entry has gone away while the reauth flow was sitting on screen.
    assert "self._get_reauth_entry()" in config_flow_source
    assert _reauth_entry_lookup_is_guarded(config_flow_tree)
    assert "FLOW_ABORT_REAUTH_ENTRY_MISSING" in config_flow_source
    assert "assert self._reauth_entry is not None" not in config_flow_source
    for path, source in translation_sources.items():
        assert "reauth_entry_missing" in source, path


def test_data_quality_warnings_are_normalized_and_formatted_for_repairs() -> None:
    """Implement test data quality warnings are normalized and formatted for repairs."""
    warning_a = util.AppDataQualityWarning(
        level="warning",
        reason="year_less_than_week",
        metric_key="device_ongrid_output_energy",
        label="Device grid-side output energy",
        source_section="device_home_stat_year",
        source_value=30.28,
        reference_section="device_home_stat_week",
        reference_value=89.08,
    ).as_dict()
    warning_b = dict(warning_a)
    warning_c = util.AppDataQualityWarning(
        level="warning",
        reason="lifetime_less_than_year",
        metric_key="pv_energy",
        label="PV energy",
        source_section="statistic",
        source_value=41.31,
        reference_section="device_pv_stat_year",
        reference_value=126.97,
    ).as_dict()

    normalized = util.normalized_data_quality_warnings([
        warning_b,
        warning_c,
        warning_a,
    ])

    assert normalized == [warning_c, warning_a]
    assert util.format_data_quality_warning(normalized[0]) == (
        "PV energy: statistic=41.31 < device_pv_stat_year=126.97"
    )
    assert util.format_data_quality_warning(normalized[1]) == (
        "Device grid-side output energy: device_home_stat_year=30.28 "
        "< device_home_stat_week=89.08"
    )


def test_data_quality_diagnostics_include_request_context_keys() -> None:
    """Implement test data quality diagnostics include request context keys."""
    const_source = (CUSTOM_COMPONENT / "const.py").read_text(encoding="utf-8")
    util_source = (CUSTOM_COMPONENT / "util.py").read_text(encoding="utf-8")

    for key in (
        "DATA_QUALITY_KEY_SOURCE_REQUEST",
        "DATA_QUALITY_KEY_REFERENCE_REQUEST",
        "DATA_QUALITY_KEY_SOURCE_CHART_SERIES_KEY",
        "DATA_QUALITY_KEY_REFERENCE_CHART_SERIES_KEY",
        "DATA_QUALITY_KEY_TOTAL_METHOD",
    ):
        assert key in const_source
        assert key in util_source
    assert "def _format_request_range" in util_source
    assert "source_request=_request_for_section(source_section)" in util_source
    assert "reference_request=_request_for_section(reference_section)" in util_source


def test_runtime_code_does_not_use_assert_for_auth_or_reauth_guards() -> None:
    """Runtime guard paths should raise HA/domain errors, not AssertionError."""
    api_source = API_IMPLEMENTATION.read_text(encoding="utf-8")
    config_flow_source = (CUSTOM_COMPONENT / "config_flow.py").read_text(
        encoding="utf-8"
    )

    assert "assert self._token is not None" not in api_source
    assert (
        'raise JackeryAuthError("Login succeeded without returning a token")'
        in api_source
    )
    assert "assert self._reauth_entry is not None" not in config_flow_source


def test_system_discovery_auth_errors_trigger_reauth() -> None:
    """Auth failures in initial rediscovery are reauth problems, not generic UpdateFailed."""
    coordinator_source = (CUSTOM_COMPONENT / "coordinator.py").read_text(
        encoding="utf-8"
    )
    discover_block = coordinator_source.split("async def async_discover", 1)[1].split(
        "def _is_property_device_candidate", 1
    )[0]

    assert "except JackeryAuthError as err" in discover_block
    assert "Jackery credentials were rejected during system discovery" in discover_block
    assert "raise ConfigEntryAuthFailed" in discover_block


def test_system_discovery_does_not_keep_unpublished_manual_id_paths() -> None:
    """Unreleased manual device/system config paths are migration ballast."""
    coordinator_source = (CUSTOM_COMPONENT / "coordinator.py").read_text(
        encoding="utf-8"
    )
    init_source = (CUSTOM_COMPONENT / "__init__.py").read_text(encoding="utf-8")

    assert "manual deviceId" not in coordinator_source
    assert "Configured device_id=" not in coordinator_source
    assert "self.entry.data.get(CONF_DEVICE_ID)" not in coordinator_source
    assert "CONF_SYSTEM_ID" not in coordinator_source
    assert "CONF_DEVICE_ID" not in coordinator_source
    assert "CONF_SCAN_INTERVAL" not in init_source


def test_optional_number_setter_failures_are_logged_before_suppression() -> None:
    """Optional number setters may suppress cloud errors, but not silently."""
    number_source = (CUSTOM_COMPONENT / "number.py").read_text(encoding="utf-8")
    assert "Ignoring optional Jackery number setter failure" in number_source
    assert "self.entity_description.raise_on_setter_error" in number_source


def test_max_feed_grid_is_not_aliased_to_grid_standard_limit() -> None:
    """MaxGridStdPw is a fallback/readout, not the maxFeedGrid setting."""
    const_source = (CUSTOM_COMPONENT / "const.py").read_text(encoding="utf-8")
    alias_block = const_source.split("MAIN_PROPERTY_ALIAS_PAIRS", 1)[1].split(
        "TASK_PLAN_BODY",
        1,
    )[0]

    assert "(FIELD_MAX_FEED_GRID, FIELD_MAX_GRID_STD_PW)" not in alias_block


def test_property_setters_keep_local_override_during_stale_refresh_window() -> None:
    """Fresh local writes should beat stale HTTP/MQTT snapshots briefly."""
    coordinator_source = (CUSTOM_COMPONENT / "coordinator.py").read_text(
        encoding="utf-8"
    )

    assert "_PROPERTY_OVERRIDE_TTL_SEC" in coordinator_source
    assert "self._property_overrides" in coordinator_source
    assert "def _merge_main_properties_for_device(" in coordinator_source
    assert "return self._merge_main_properties(merged, overrides)" in coordinator_source


def test_config_flow_normalizes_account_and_uses_flow_constants() -> None:
    """Avoid duplicate entries caused by whitespace/case drift in usernames."""
    const_source = (CUSTOM_COMPONENT / "const.py").read_text(encoding="utf-8")
    config_flow_source = (CUSTOM_COMPONENT / "config_flow.py").read_text(
        encoding="utf-8"
    )

    assert 'FLOW_STEP_USER: Final = "user"' in const_source
    assert 'FLOW_ERROR_INVALID_AUTH: Final = "invalid_auth"' in const_source
    assert "def _normalize_account(value: str) -> str:" in config_flow_source
    assert "return value.strip()" in config_flow_source
    assert (
        "account = _normalize_account(user_input[CONF_USERNAME])" in config_flow_source
    )
    assert "await self.async_set_unique_id(account.lower())" in config_flow_source
    assert "CONF_USERNAME: account" in config_flow_source
    assert (
        "vol.Required(CONF_USERNAME): vol.All(str, vol.Length(min=1))"
        in config_flow_source
    )
    assert "FLOW_ERROR_ACCOUNT_REQUIRED" in config_flow_source
    assert "errors[CONF_USERNAME] = FLOW_ERROR_ACCOUNT_REQUIRED" in config_flow_source
    assert (
        "vol.Required(CONF_PASSWORD): vol.All(str, vol.Length(min=1))"
        in config_flow_source
    )
    assert (
        "str.strip"
        not in config_flow_source.split("USER_SCHEMA =", 1)[1].split(
            "class JackeryConfigFlow", 1
        )[0]
    )


def test_redact_keys_cover_mqtt_credential_aliases() -> None:
    """Diagnostics redaction must cover raw and normalized MQTT credential keys."""
    const_source = (CUSTOM_COMPONENT / "const.py").read_text(encoding="utf-8")
    redact_block = const_source.split("REDACT_KEYS: Final =", 1)[1].split(")", 1)[0]

    for key_name in (
        "FIELD_MQTT_PASSWORD",
        "FIELD_USER_ID",
        "MQTT_CREDENTIAL_CLIENT_ID",
        "MQTT_CREDENTIAL_PASSWORD",
        "MQTT_CREDENTIAL_USER_ID",
        "MQTT_CREDENTIAL_USERNAME",
    ):
        assert key_name in redact_block


def test_diagnostics_do_not_expose_raw_mqtt_topic_user_ids() -> None:
    """MQTT topics contain the Jackery userId and must be redacted in diagnostics."""
    const_source = (CUSTOM_COMPONENT / "const.py").read_text(encoding="utf-8")
    mqtt_source = MQTT_IMPLEMENTATION.read_text(encoding="utf-8")

    assert 'REDACTED_VALUE: Final = "**REDACTED**"' in const_source
    assert "def _redact_topic(topic: str | None) -> str | None:" in mqtt_source
    assert "parts[2] = REDACTED_VALUE" in mqtt_source
    assert (
        '"topics": [self._redact_topic(topic) for topic in self._topics]' in mqtt_source
    )
    assert (
        '"last_published_topic": self._redact_topic(self._last_published_topic)'
        in mqtt_source
    )
    assert '"topic_count": len(self._topics)' in mqtt_source
    assert '"topics": list(self._topics)' not in mqtt_source
    assert '"last_published_topic": self._last_published_topic' not in mqtt_source


def test_mqtt_diagnostics_track_dropped_messages_and_timestamps() -> None:
    """Diagnostics need actionable MQTT health data without exposing credentials."""
    mqtt_source = MQTT_IMPLEMENTATION.read_text(encoding="utf-8")

    for fragment in (
        "self._messages_dropped = 0",
        "self._last_message_error: str | None = None",
        "self._last_connect_at: str | None = None",
        "self._last_disconnect_at: str | None = None",
        "self._last_message_at: str | None = None",
        "self._last_publish_at: str | None = None",
        '"messages_dropped": self._messages_dropped',
        '"last_message_error": self._last_message_error',
        '"last_connect_at": self._last_connect_at',
        '"last_disconnect_at": self._last_disconnect_at',
        '"last_message_at": self._last_message_at',
        '"last_publish_at": self._last_publish_at',
    ):
        assert fragment in mqtt_source

    assert "invalid JSON payload" in mqtt_source
    assert "non-object JSON payload" in mqtt_source


def test_mqtt_password_base64_validation_is_strict_and_redaction_constant_is_reused() -> (
    None
):
    """Reject malformed MQTT seeds and avoid copy/pasted redaction literals."""
    api_source = API_IMPLEMENTATION.read_text(encoding="utf-8")

    assert "base64.b64decode(self._mqtt_seed_b64, validate=True)" in api_source
    assert "redacted[FIELD_TOKEN] = REDACTED_VALUE" in api_source
    assert "inner[FIELD_MQTT_PASSWORD] = REDACTED_VALUE" in api_source
    assert '"**REDACTED**"' not in api_source


def test_api_trend_endpoints_use_shared_period_range_contract() -> None:
    """System trend helpers must not fall back to today..today for month/year."""
    api_source = API_IMPLEMENTATION.read_text(encoding="utf-8")

    assert "from .util import app_period_date_bounds" in api_source
    assert "date.today().isoformat()" not in api_source
    for function_name in (
        "async_get_pv_trends",
        "async_get_home_trends",
        "async_get_battery_trends",
        "_async_get_device_period_stat",
    ):
        block = api_source.split(f"async def {function_name}", 1)[1]
        if function_name != "_async_get_device_period_stat":
            next_marker = "async def "
            block = block.split(next_marker, 1)[0]
        else:
            block = block.split("async def async_get_device_pv_stat", 1)[0]
        assert "app_period_date_bounds(" in block
        assert "APP_REQUEST_BEGIN_DATE: str(begin_date)" in block
        assert "APP_REQUEST_END_DATE: str(end_date)" in block


def test_runtime_code_has_no_unreachable_statements_after_terminal_nodes() -> None:
    """Catch dead code such as duplicate return statements in helpers."""
    terminal = (ast.Return, ast.Raise, ast.Break, ast.Continue)

    def check_body(path: pathlib.Path, body: list[ast.stmt]) -> None:
        """Implement check body."""
        terminal_node: ast.stmt | None = None
        for stmt in body:
            if terminal_node is not None:
                raise AssertionError(
                    f"{path}:{stmt.lineno} unreachable statement after "
                    f"line {terminal_node.lineno}"
                )
            if isinstance(stmt, terminal):
                terminal_node = stmt

    def walk_statement_lists(path: pathlib.Path, node: ast.AST) -> None:
        """Implement walk statement lists."""
        for _field, value in ast.iter_fields(node):
            if (
                isinstance(value, list)
                and value
                and all(isinstance(item, ast.stmt) for item in value)
            ):
                check_body(path, value)
            for child in value if isinstance(value, list) else [value]:
                if isinstance(child, ast.AST):
                    walk_statement_lists(path, child)

    for path in _python_sources():
        walk_statement_lists(path, ast.parse(path.read_text(encoding="utf-8")))


def test_config_entry_bool_option_calls_use_config_key_and_default() -> None:
    """Optional entity cleanup must pass option key plus fallback default."""
    source = (CUSTOM_COMPONENT / "__init__.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "config_entry_bool_option"
    ]
    assert calls
    for call in calls:
        assert len(call.args) == 3, (
            f"config_entry_bool_option call at line {call.lineno} must pass entry, key, default"
        )

    assert "CONF_CREATE_SMART_METER_DERIVED_SENSORS" in source
    assert "CONF_CREATE_CALCULATED_POWER_SENSORS" in source
    assert "DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS" in source
    assert "DEFAULT_CREATE_CALCULATED_POWER_SENSORS" in source
    assert "entry,\n            DEFAULT_CREATE_" not in source


def test_all_api_json_decode_paths_catch_value_error() -> None:
    """aiohttp/json stacks may raise ValueError for malformed JSON payloads.

    Each ``resp.json(...)`` call site must catch the relevant decode/format
    exceptions: ``aiohttp.ContentTypeError``, ``json.JSONDecodeError``,
    ``UnicodeDecodeError`` and ``ValueError``. The test accepts both the
    required parenthesized ``as err`` handler and Python 3.14's unparenthesized
    no-``as`` multi-exception headers.
    """
    api_source = API_IMPLEMENTATION.read_text(encoding="utf-8")
    decode_blocks: list[str] = []
    for match in re.finditer(r"except[^:\n]*(?:\n\s*[^:\n]*)*ContentTypeError", api_source):
        block = api_source[match.start() : api_source.find(":", match.start()) + 1]
        decode_blocks.append(re.sub(r"\s+", " ", block))
    assert len(decode_blocks) == 4, decode_blocks
    for block in decode_blocks:
        assert "json.JSONDecodeError" in block, block
        assert "UnicodeDecodeError" in block, block
        assert "ValueError" in block, block


def test_login_invalid_json_is_reported_as_api_error_not_raw_exception() -> None:
    """Login should surface malformed cloud responses as JackeryApiError."""
    api_source = API_IMPLEMENTATION.read_text(encoding="utf-8")
    login_block = api_source.split("async def async_login", 1)[1].split(
        "async def async_get_mqtt_credentials", 1
    )[0]
    assert "Login returned invalid JSON" in login_block
    assert "HTTP_RAW_TEXT_LIMIT" in login_block
    assert "raise JackeryApiError(" in login_block


def test_api_read_endpoints_normalize_unexpected_payload_shapes() -> None:
    """Dict/list API readers should not leak arbitrary data shapes to coordinator."""
    api_source = API_IMPLEMENTATION.read_text(encoding="utf-8")

    assert "def _payload_dict(data: dict[str, Any], path: str)" in api_source
    assert "def _payload_list(data: dict[str, Any], path: str)" in api_source
    assert "returned unexpected data shape for dict payload" in api_source
    assert "returned unexpected data shape for list payload" in api_source

    expected_fragments = (
        "return self._payload_dict(data, DEVICE_PROPERTY_PATH)",
        "return self._payload_dict(data, SYSTEM_STATISTIC_PATH)",
        "payload = self._payload_dict(data, PV_TRENDS_PATH)",
        "payload = self._payload_dict(data, HOME_TRENDS_PATH)",
        "payload = self._payload_dict(data, BATTERY_TRENDS_PATH)",
        "return self._payload_dict(data, POWER_PRICE_PATH)",
        "return self._payload_dict(data, PRICE_HISTORY_CONFIG_PATH)",
        "return self._payload_dict(data, DEVICE_STATISTIC_PATH)",
        "payload = self._payload_dict(data, path)",
        "return self._payload_dict(data, DEVICE_METER_STAT_PATH)",
        "return self._payload_dict(data, LOCATION_PATH)",
        "systems = self._payload_list(data, SYSTEM_LIST_PATH)",
        "return self._payload_list(data, PRICE_SOURCE_LIST_PATH)",
        "items = self._payload_list(data, OTA_LIST_PATH)",
        "return self._payload_list(data, DEVICE_LIST_PATH)",
    )
    for fragment in expected_fragments:
        assert fragment in api_source

    assert "return data.get(FIELD_DATA) or {}" not in api_source


def test_ota_info_accepts_single_dict_payload_shapes() -> None:
    """OTA responses may be a list, a single dict, or a dict body wrapper."""
    api_source = API_IMPLEMENTATION.read_text(encoding="utf-8")
    block = api_source.split("async def async_get_ota_info", 1)[1].split(
        "async def async_get_location", 1
    )[0]

    assert "items = self._payload_list(data, OTA_LIST_PATH)" in block
    assert "raw = data.get(FIELD_DATA)" in block
    assert "raw_body = raw.get(FIELD_BODY)" in block
    for field in (
        "FIELD_CURRENT_VERSION",
        "FIELD_VERSION",
        "FIELD_TARGET_VERSION",
        "FIELD_TARGET_MODULE_VERSION",
        "FIELD_UPDATE_STATUS",
        "FIELD_UPDATE_CONTENT",
        "FIELD_IS_FIRMWARE_UPGRADE",
        "FIELD_UPGRADE_TYPE",
    ):
        assert field in block


def test_ota_info_selects_requested_device_from_multi_item_response() -> None:
    """OTA list responses must not use the main-device item for a battery pack."""
    api_source = API_IMPLEMENTATION.read_text(encoding="utf-8")

    assert "def _select_ota_item(" in api_source
    selector = api_source.split("def _select_ota_item(", 1)[1].split(
        "# --- generic GET with auto re-login", 1
    )[0]
    ota_block = api_source.split("async def async_get_ota_info", 1)[1].split(
        "async def async_get_location", 1
    )[0]

    assert "requested_sn = str(device_sn)" in selector
    assert "item.get(FIELD_DEVICE_SN)" in selector
    assert "return item" in selector
    assert "return items[0] if items else {}" in selector
    assert "return self._select_ota_item(items, device_sn)" in ota_block
    assert "return items[0]" not in ota_block


def test_battery_pack_single_object_detection_accepts_firmware_only_payloads() -> None:
    """Pack list fallback must keep BatteryPackSub firmware-only payloads."""
    api_source = API_IMPLEMENTATION.read_text(encoding="utf-8")
    block = api_source.split("async def async_get_battery_pack_list", 1)[1].split(
        "async def async_get_home_trends", 1
    )[0]

    for field in (
        "FIELD_VERSION",
        "FIELD_CURRENT_VERSION",
        "FIELD_IS_FIRMWARE_UPGRADE",
        "FIELD_UPDATE_STATUS",
    ):
        assert field in block


def test_api_payload_helper_paths_match_called_endpoint_constants() -> None:
    """Wrong helper path constants hide the real failing endpoint in diagnostics."""
    api_source = API_IMPLEMENTATION.read_text(encoding="utf-8")

    pv_block = api_source.split("async def async_get_pv_trends", 1)[1].split(
        "async def async_get_power_price", 1
    )[0]
    price_sources_block = api_source.split("async def async_get_price_sources", 1)[
        1
    ].split("async def async_get_price_history_config", 1)[0]
    assert "payload = self._payload_dict(data, PV_TRENDS_PATH)" in pv_block
    assert "HOME_TRENDS_PATH" not in pv_block
    assert (
        "return self._payload_list(data, PRICE_SOURCE_LIST_PATH)" in price_sources_block
    )
    assert "DEVICE_LIST_PATH" not in price_sources_block


def test_home_assistant_ui_schemas_do_not_use_nonserializable_strip_callable() -> None:
    """HA voluptuous_serialize cannot convert raw str.strip callables in UI schemas."""
    config_flow_source = (CUSTOM_COMPONENT / "config_flow.py").read_text(
        encoding="utf-8"
    )
    services_source = (CUSTOM_COMPONENT / "services.py").read_text(encoding="utf-8")

    user_schema = config_flow_source.split("USER_SCHEMA =", 1)[1].split(
        "class JackeryConfigFlow", 1
    )[0]
    reauth_schema = config_flow_source.split("data_schema=vol.Schema", 1)[1].split(
        "description_placeholders", 1
    )[0]
    service_schema_block = services_source.split("RENAME_SCHEMA =", 1)[1].split(
        "def _loaded_coordinators", 1
    )[0]

    assert "str.strip" not in user_schema
    assert "str.strip" not in reauth_schema
    assert "str.strip" not in service_schema_block
    assert "SERVICE_NON_EMPTY_TEXT_PATTERN" in service_schema_block


def test_coordinator_imports_all_field_constants_it_references() -> None:
    """Catch runtime NameError regressions from missing FIELD_* imports."""
    source = (CUSTOM_COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported_from_const: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == "const":
            imported_from_const.update(alias.name for alias in node.names)

    referenced_fields = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id.startswith("FIELD_")
    }
    missing = sorted(referenced_fields - imported_from_const)
    assert not missing, f"Missing .const imports in coordinator.py: {missing}"


def test_coordinator_lazy_imports_mqtt_client_for_collection_without_aiomqtt() -> None:
    """HA config-flow collection should not require optional MQTT deps."""
    source = (CUSTOM_COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    tree = ast.parse(source)

    module_mqtt_imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == "mqtt_push"
        and any(alias.name == "JackeryMqttPushClient" for alias in node.names)
    ]
    assert module_mqtt_imports == []
    assert "if TYPE_CHECKING:" in source
    assert "self._mqtt: JackeryMqttPushClient | None = None" in source

    start_mqtt = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "async_start_mqtt"
    )
    lazy_imports = [
        node
        for node in ast.walk(start_mqtt)
        if isinstance(node, ast.ImportFrom)
        and node.module == "mqtt_push"
        and any(alias.name == "JackeryMqttPushClient" for alias in node.names)
    ]
    assert len(lazy_imports) == 1
    assert "except ModuleNotFoundError as err:" in source
    assert 'err.name != "aiomqtt"' in source
    assert "Jackery MQTT push is unavailable because aiomqtt is not installed" in (
        source
    )


def test_service_numeric_ids_are_schema_serializable_but_trimmed_by_handlers() -> None:
    """Service IDs may include whitespace in UI/API input, then handlers strip them."""
    const_source = (CUSTOM_COMPONENT / "const.py").read_text(encoding="utf-8")
    services_source = (CUSTOM_COMPONENT / "services.py").read_text(encoding="utf-8")

    assert 'SERVICE_NUMERIC_ID_PATTERN: Final = r"^\\s*[0-9]+\\s*$"' in const_source
    # System rename keeps the strict numeric-id pattern; device-id handlers
    # tolerate HA device-registry UUIDs that the device selector emits.
    assert "vol.Match(SERVICE_NUMERIC_ID_PATTERN)" in services_source
    assert "call.data[SERVICE_FIELD_SYSTEM_ID].strip()" in services_source
    assert "call.data[SERVICE_FIELD_DEVICE_ID].strip()" in services_source
    assert "call.data[SERVICE_FIELD_ALERT_ID].strip()" in services_source
    assert "str.strip" not in services_source


def test_services_yaml_uses_device_selector_for_device_id_fields() -> None:
    """services.yaml must use HA's device selector for jackery_solarvault devices.

    The picker filters by ``integration: jackery_solarvault`` so users
    cannot pick a device from another integration. system_id stays a text
    selector because a Jackery system maps to multiple HA devices and the
    selector cannot represent that scope.
    """
    services = yaml.safe_load(
        (CUSTOM_COMPONENT / "services.yaml").read_text(encoding="utf-8")
    )

    refresh_field = services["refresh_weather_plan"]["fields"]["device_id"]
    delete_field = services["delete_storm_alert"]["fields"]["device_id"]
    rename_field = services["rename_system"]["fields"]["system_id"]

    for field in (refresh_field, delete_field):
        assert field.get("required") is True
        device_selector = (field.get("selector") or {}).get("device") or {}
        assert device_selector.get("integration") == "jackery_solarvault", (
            "device_id field must filter the picker to this integration"
        )

    # System rename uses a text selector by design.
    assert "text" in (rename_field.get("selector") or {})


def test_services_routes_actions_to_owning_coordinator_for_multi_account() -> None:
    """Service-action handlers must route to the coordinator that owns the id.

    The previous implementation iterated over every loaded coordinator and
    accepted the first non-failing call, which sent service-action requests
    to the wrong account whenever two Jackery accounts were configured at
    once. The current implementation maps the request to the owning entry by
    looking the id up inside ``coordinator.data`` (devices) or
    ``coordinator.data[device_id][PAYLOAD_SYSTEM]`` (systems) before
    forwarding to the API.
    """
    services_source = (CUSTOM_COMPONENT / "services.py").read_text(encoding="utf-8")

    # Lookup helpers exist and are typed against the runtime coordinator.
    assert (
        "def _coordinator_for_device(\n"
        "    hass: HomeAssistant, device_id: str\n"
        ") -> JackerySolarVaultCoordinator | None:" in services_source
    )
    assert (
        "def _coordinator_for_system(\n"
        "    hass: HomeAssistant, system_id: str\n"
        ") -> JackerySolarVaultCoordinator | None:" in services_source
    )

    # System lookup walks the system payload section and matches FIELD_ID
    # or FIELD_SYSTEM_ID — both are needed because the cloud surfaces the
    # same id under either key depending on endpoint.
    system_block = services_source.split("def _coordinator_for_system", 1)[1].split(
        "\n\n# ", 1
    )[0]
    assert "PAYLOAD_SYSTEM" in system_block
    assert "FIELD_ID" in system_block
    assert "FIELD_SYSTEM_ID" in system_block

    # Device lookup uses the coordinator.data dict membership check.
    device_block = services_source.split("def _coordinator_for_device", 1)[1].split(
        "\ndef _coordinator_for_system", 1
    )[0]
    assert "device_id in (coordinator.data or {})" in device_block

    # Each handler resolves the coordinator before invoking the cloud call;
    # missing-entry paths must raise a translated ServiceValidationError.
    for handler in (
        "_async_handle_rename",
        "_async_handle_refresh_weather_plan",
        "_async_handle_delete_storm_alert",
    ):
        block = services_source.split(f"async def {handler}", 1)[1].split(
            "\n\nasync def ", 1
        )[0]
        assert "coordinator is None" in block, handler
        assert "raise ServiceValidationError(" in block, handler
        assert "translation_domain=DOMAIN" in block, handler


def test_services_resolves_ha_device_uuid_back_to_jackery_device_id() -> None:
    """The device selector hands the handler an HA device-registry UUID.

    Translate it back to the Jackery numeric id by reading the matching
    ``(DOMAIN, jackery_device_id)`` identifier off the DeviceEntry. Legacy
    automations that still pass a raw Jackery numeric id must keep working
    too — the resolver returns the input unchanged if the device-registry
    miss tells us we're not looking at an HA UUID.
    """
    services_source = (CUSTOM_COMPONENT / "services.py").read_text(encoding="utf-8")

    assert "from homeassistant.helpers import" in services_source
    assert "device_registry as dr" in services_source

    block = services_source.split("def _resolve_jackery_device_id", 1)[1].split(
        "\ndef _coordinator_for_device", 1
    )[0]
    assert "dr.async_get(hass).async_get(raw)" in block
    assert "device.identifiers" in block
    assert "DOMAIN" in block
    # Legacy fallback: when the registry has no matching DeviceEntry,
    # treat the input as a raw Jackery id.
    assert "return raw" in block


def test_services_setup_is_idempotent_and_callback_typed() -> None:
    """async_setup_services must be a sync @callback and skip already-registered services."""
    services_source = (CUSTOM_COMPONENT / "services.py").read_text(encoding="utf-8")
    setup_block = services_source.split("def async_setup_services", 1)[1]

    assert "@callback" in services_source.split("async_setup_services", 1)[0][-200:]
    # Re-entry safe: HA fires async_setup multiple times in some test setups,
    # so each registration is gated on has_service.
    for service_const in (
        "SERVICE_RENAME_SYSTEM",
        "SERVICE_REFRESH_WEATHER_PLAN",
        "SERVICE_DELETE_STORM_ALERT",
    ):
        assert f"hass.services.has_service(DOMAIN, {service_const})" in setup_block, (
            service_const
        )
        assert (
            f"hass.services.async_register(\n            DOMAIN,\n            {service_const}"
            in setup_block
        ), service_const


def test_coordinator_sets_http_properties_from_fresh_sanitized_property_payload() -> (
    None
):
    """HTTP source payload must be defined before entry assembly."""
    source = (CUSTOM_COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    refresh_block = source.split("async def _async_update_data", 1)[1].split(
        "@property\n    def update_interval", 1
    )[0]

    assert "new_props" not in refresh_block
    assert "http_props = self._sanitize_main_properties(" in refresh_block
    assert "payload.get(PAYLOAD_PROPERTIES) or {}" in refresh_block
    assert "PAYLOAD_HTTP_PROPERTIES: http_props" in refresh_block
    assert "http_props," in refresh_block


def test_component_modules_import_all_referenced_const_names() -> None:
    """Catch runtime NameError regressions from missing .const imports in any module."""
    const_tree = ast.parse((CUSTOM_COMPONENT / "const.py").read_text(encoding="utf-8"))
    const_names: set[str] = set()
    for node in ast.walk(const_tree):
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                const_names.add(target.id)

    modules_to_check = [path for path in _python_sources() if path.name != "const.py"]
    failures: dict[str, list[str]] = {}
    for path in modules_to_check:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_from_const: set[str] = set()
        assigned: set[str] = set()
        used: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "const":
                imported_from_const.update(
                    alias.asname or alias.name for alias in node.names
                )
            elif isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                assigned.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigned.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                assigned.add(node.target.id)
            elif isinstance(node, ast.Name):
                used.add(node.id)
        missing = sorted((used & const_names) - imported_from_const - assigned)
        if missing:
            failures[path.name] = missing

    assert not failures, f"Missing .const imports: {failures}"


def test_component_modules_import_all_referenced_util_helpers() -> None:
    """Catch runtime NameError regressions from missing .util helper imports."""
    util_tree = ast.parse((CUSTOM_COMPONENT / "util.py").read_text(encoding="utf-8"))
    util_helpers = {
        node.name
        for node in ast.walk(util_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not node.name.startswith("_")
    }

    modules_to_check = [path for path in _python_sources() if path.name != "util.py"]
    failures: dict[str, list[str]] = {}
    for path in modules_to_check:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        imported_from_util: set[str] = set()
        assigned: set[str] = set()
        used: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "util":
                imported_from_util.update(
                    alias.asname or alias.name for alias in node.names
                )
            elif isinstance(
                node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)
            ):
                assigned.add(node.name)
            elif isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        assigned.add(target.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                assigned.add(node.target.id)
            elif isinstance(node, ast.Name):
                used.add(node.id)
        missing = sorted((used & util_helpers) - imported_from_util - assigned)
        if missing:
            failures[path.name] = missing

    assert not failures, f"Missing .util helper imports: {failures}"


def test_derived_live_power_sensors_do_not_generate_long_term_statistics() -> None:
    """Calculated live-difference power sensors should not create LTS unit metadata."""
    sensor_source = (CUSTOM_COMPONENT / "sensor.py").read_text(encoding="utf-8")
    for class_name in (
        "JackeryBatteryNetPowerSensor",
        "JackeryBatteryStackNetPowerSensor",
        "JackeryGridNetPowerSensor",
        "JackeryHomeConsumptionPowerSensor",
    ):
        block = sensor_source.split(f"class {class_name}", 1)[1].split("\nclass ", 1)[0]
        assert "_attr_device_class = SensorDeviceClass.POWER" in block
        assert "_attr_native_unit_of_measurement = UnitOfPower.WATT" in block
        assert "_attr_state_class" not in block

    assert "historically existed without a compatible recorder unit" in sensor_source


def test_smart_meter_entities_cache_state_before_ha_state_write() -> None:
    """Smart-meter sensors must not recompute values during every HA state read."""
    sensor_source = (CUSTOM_COMPONENT / "sensor.py").read_text(encoding="utf-8")
    block = sensor_source.split(
        "class JackerySmartMeterSensor(JackeryEntity, SensorEntity):", 1
    )[1].split("class JackeryRawPropertiesSensor", 1)[0]

    assert "self._cached_native_value: Any = None" in block
    assert "self._cached_attrs: dict[str, Any] = {}" in block
    assert "def _refresh_cache(self) -> None:" in block
    assert "def _handle_coordinator_update(self) -> None:" in block
    assert "async def async_added_to_hass(self) -> None:" in block
    assert "return self._cached_native_value" in block
    assert "return self._cached_attrs" in block


def test_setup_removes_stale_energy_net_power_helpers_without_unit() -> None:
    """Broken Energy helper sensors should be cleaned before HA records them again."""
    const_source = (CUSTOM_COMPONENT / "const.py").read_text(encoding="utf-8")
    init_source = (CUSTOM_COMPONENT / "__init__.py").read_text(encoding="utf-8")

    for name in (
        "STALE_ENERGY_HELPER_PREFIX",
        "STALE_NET_POWER_SUFFIX",
        "STALE_HELPER_VENDOR_TOKENS",
    ):
        assert name in const_source
        assert name in init_source
    assert "_async_remove_stale_energy_helpers(hass)" in init_source
    assert 'unit not in (None, "")' in init_source
    assert "explicitly reference this integration" in init_source
    assert "please recreate with Jackery battery_net_power" in init_source


def test_sensor_source_has_no_duplicate_battery_pack_ot_attribute_entry() -> None:
    """Battery-pack diagnostics should not expose the same raw key twice."""
    sensor_source = (CUSTOM_COMPONENT / "sensor.py").read_text(encoding="utf-8")
    block = sensor_source.split(
        "class JackeryBatteryPackSensor(JackeryEntity, SensorEntity):", 1
    )[1].split("class JackerySmartMeterSensor", 1)[0]

    assert block.count("FIELD_OT,") == 1


def test_battery_pack_sensor_uses_ota_fallback_fields() -> None:
    """Pack firmware/update diagnostics must read the OTA-enriched fields."""
    sensor_source = (CUSTOM_COMPONENT / "sensor.py").read_text(encoding="utf-8")
    block = sensor_source.split(
        "class JackeryBatteryPackSensor(JackeryEntity, SensorEntity):", 1
    )[1].split("class JackerySmartMeterSensor", 1)[0]

    assert "raw = self._pack.get(FIELD_CURRENT_VERSION)" in block
    assert "raw = self._pack.get(FIELD_IS_FIRMWARE_UPGRADE)" in block
    for field in (
        "FIELD_VERSION",
        "FIELD_CURRENT_VERSION",
        "FIELD_UPDATE_STATUS",
        "FIELD_TARGET_VERSION",
        "FIELD_TARGET_MODULE_VERSION",
        "FIELD_UPDATE_CONTENT",
        "FIELD_UPGRADE_TYPE",
    ):
        assert field in block


def test_data_quality_warnings_do_not_hide_sensor_states() -> None:
    """Repairs diagnose contradictions; entity states keep their documented source."""
    sensor_source = (CUSTOM_COMPONENT / "sensor.py").read_text(encoding="utf-8")
    stat_block = sensor_source.split(
        "class JackeryStatSensor(JackeryEntity, SensorEntity):", 1
    )[1].split("class JackeryBatteryPackSensor", 1)[0]

    assert "def _data_quality_warning_for_own_source" not in stat_block
    assert "def _is_source_untrusted_by_data_quality" not in stat_block
    assert "PAYLOAD_DATA_QUALITY" not in stat_block
    assert "data_quality_untrusted" not in stat_block


def test_payload_debug_log_records_raw_types_parsed_floats_and_rotation() -> None:
    """Implement test payload debug log records raw types parsed floats and rotation."""
    util_source = (CUSTOM_COMPONENT / "util.py").read_text(encoding="utf-8")
    api_source = API_IMPLEMENTATION.read_text(encoding="utf-8")
    coordinator_source = (CUSTOM_COMPONENT / "coordinator.py").read_text(
        encoding="utf-8"
    )

    for fragment in (
        "PAYLOAD_DEBUG_LOG_FILENAME",
        "PAYLOAD_DEBUG_LOG_MAX_BYTES",
        "def chart_series_debug",
        '"raw_type": type(raw).__name__',
        '"parsed_float": parsed',
        '"parsed_sum": round(total, 5)',
        "def append_payload_debug_line",
    ):
        assert (
            fragment in util_source
            or fragment in api_source
            or fragment in coordinator_source
        )

    assert "self.payload_debug_callback" in api_source
    assert "chart_series_debug(payload)" in api_source
    assert '"kind": "http"' in api_source
    assert '"kind": "mqtt"' in coordinator_source
    assert "append_payload_debug_line" in coordinator_source
    assert "jackery_solarvault_payload_debug.jsonl" in (
        pathlib.Path("README.md").read_text(encoding="utf-8")
    )


def test_local_helper_calls_match_their_declared_arity() -> None:
    """Catch nested helper call mistakes before they create runtime coroutine leaks."""
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for owner in ast.walk(tree):
            if not isinstance(owner, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            local_defs: dict[str, tuple[int, int | None, int]] = {}
            for stmt in owner.body:
                if not isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    continue
                positional_count = len(stmt.args.posonlyargs) + len(stmt.args.args)
                required_count = positional_count - len(stmt.args.defaults)
                max_count = None if stmt.args.vararg is not None else positional_count
                local_defs[stmt.name] = (required_count, max_count, stmt.lineno)
            if not local_defs:
                continue
            for call in ast.walk(owner):
                if not isinstance(call, ast.Call) or not isinstance(
                    call.func, ast.Name
                ):
                    continue
                if call.func.id not in local_defs:
                    continue
                required_count, max_count, definition_line = local_defs[call.func.id]
                actual_count = len(call.args)
                too_few = actual_count < required_count
                too_many = max_count is not None and actual_count > max_count
                assert not (too_few or too_many), (
                    f"{path}:{call.lineno} calls local helper {call.func.id}() "
                    f"with {actual_count} positional args; definition at line "
                    f"{definition_line} expects {required_count}..{max_count}"
                )


def test_system_ttl_gather_calls_use_exact_helper_shape() -> None:
    """Protect the system bundle gather from accidental extra constants/arguments."""
    source = (CUSTOM_COMPONENT / "coordinator.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    update_func = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == "_async_update_data"
    )
    ttl_calls = [
        node
        for node in ast.walk(update_func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "_get_with_ttl"
    ]
    assert ttl_calls
    assert all(len(call.args) == 5 for call in ttl_calls)

    bad_fragment = "PAYLOAD_ALARM,\n    PAYLOAD_DEBUG_LOG_FILENAME,\n                    self._slow_metrics_interval_sec"
    assert bad_fragment not in source


def test_component_modules_have_no_unresolved_global_names() -> None:
    """Catch NameError-class bugs caused by missing imports or renamed locals."""
    import builtins
    import symtable

    builtins_names = set(dir(builtins))
    # Python's symtable surfaces compiler-generated module dunders as
    # "global references" that aren't statically assigned anywhere in the
    # source. Whitelist them so the unresolved-name guard does not produce
    # false positives across Python versions:
    #   * ``__file__``                — every module
    #   * ``__conditional_annotations__`` — Python 3.14+ (PEP 749, lazy
    #     evaluation of conditionally-emitted annotations).
    allowed_dunder_globals = {"__file__", "__conditional_annotations__"}
    for path in _python_sources():
        table = symtable.symtable(path.read_text(encoding="utf-8"), str(path), "exec")
        module_defs = {
            symbol.get_name()
            for symbol in table.get_symbols()
            if symbol.is_imported() or symbol.is_assigned() or symbol.is_namespace()
        }
        unresolved: set[str] = set()

        def walk(
            scope: symtable.SymbolTable,
            *,
            _module_defs: set[str] = module_defs,
            _unresolved: set[str] = unresolved,
        ) -> None:
            """Recursively collect unresolved global names from this scope."""
            for symbol in scope.get_symbols():
                name = symbol.get_name()
                if (
                    symbol.is_referenced()
                    and symbol.is_global()
                    and not symbol.is_imported()
                    and not symbol.is_assigned()
                    and name not in _module_defs
                    and name not in builtins_names
                    and name not in allowed_dunder_globals
                ):
                    _unresolved.add(name)
            for child in scope.get_children():
                walk(child, _module_defs=_module_defs, _unresolved=_unresolved)

        walk(table)
        assert not unresolved, (
            f"{path} has unresolved global names: {sorted(unresolved)}"
        )


def test_options_flow_uses_shared_bool_option_fallback_helper() -> None:
    """Options defaults should share one fallback path from options/data/defaults."""
    config_flow_source = (CUSTOM_COMPONENT / "config_flow.py").read_text(
        encoding="utf-8"
    )
    options_block = config_flow_source.split("class JackeryOptionsFlow", 1)[1].split(
        "class JackeryConfigFlow", 1
    )[0]

    assert "from .util import config_entry_bool_option" in config_flow_source
    assert "def _entry_bool_option(" not in config_flow_source
    assert (
        "current_options = _current_option_values(self.config_entry)" in options_block
    )
    assert "def _current_option_values(entry: ConfigEntry)" in config_flow_source
    assert "config_entry_bool_option(entry, key, default)" in config_flow_source
    assert ".options.get(" not in options_block


def test_sensor_setup_uses_shared_bool_option_fallback_helper() -> None:
    """Sensor setup should share one fallback path from options/data/defaults."""
    sensor_source = (CUSTOM_COMPONENT / "sensor.py").read_text(encoding="utf-8")
    setup_block = sensor_source.split("async def async_setup_entry", 1)[1].split(
        "# ---------------------------------------------------------------------------\n# Entities",
        1,
    )[0]

    assert "config_entry_bool_option" in sensor_source
    assert "def _entry_bool_option(" not in sensor_source
    assert setup_block.count("config_entry_bool_option(") == 3
    assert ".options.get(" not in setup_block


def test_no_unresolved_git_merge_conflict_markers() -> None:
    """Catch real merge conflict markers without flagging reStructuredText tables."""
    marker_prefixes = ("<<<<<<< ", ">>>>>>> ")
    for path in pathlib.Path(".").rglob("*"):
        if not path.is_file() or ".git" in path.parts:
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(lines, start=1):
            assert not line.startswith(marker_prefixes), f"{path}:{line_number}: {line}"
            assert line != "=======", f"{path}:{line_number}: {line}"


def test_payload_debug_file_is_gated_by_dedicated_logger_not_options() -> None:
    """Raw payload logging must use HA logger controls without a stale option.

    The setup/options checkbox was removed, but the JSONL writer must still avoid
    inheriting DEBUG from the parent integration logger. Requiring DEBUG to be
    set directly on the dedicated payload-debug logger keeps the feature
    available for diagnostics without a hidden ``debug_payload_log`` option.
    """
    const_source = (CUSTOM_COMPONENT / "const.py").read_text(encoding="utf-8")
    coordinator_source = (CUSTOM_COMPONENT / "coordinator.py").read_text(
        encoding="utf-8"
    )
    init_source = (CUSTOM_COMPONENT / "__init__.py").read_text(encoding="utf-8")

    assert "CONF_DEBUG_PAYLOAD_LOG" not in const_source
    assert "DEFAULT_DEBUG_PAYLOAD_LOG" not in const_source
    assert "CONF_DEBUG_PAYLOAD_LOG" not in coordinator_source
    assert "DEFAULT_DEBUG_PAYLOAD_LOG" not in coordinator_source
    assert "debug_payload_log" not in init_source
    assert "_async_purge_stale_payload_debug_log" not in init_source

    assert "_PAYLOAD_DEBUG_LOGGER.level != logging.DEBUG" in coordinator_source
    assert (
        "if not _PAYLOAD_DEBUG_LOGGER.isEnabledFor(logging.DEBUG):"
        not in coordinator_source
    )


def test_no_direct_blocking_file_io_inside_async_functions() -> None:
    """HA runtime paths must not do disk IO directly in the event loop."""
    forbidden = {
        "open",
        "write_text",
        "read_text",
        "replace",
        "unlink",
        "mkdir",
        "stat",
    }
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if not isinstance(node, ast.AsyncFunctionDef):
                continue
            for call in ast.walk(node):
                if not isinstance(call, ast.Call):
                    continue
                name = None
                if isinstance(call.func, ast.Name):
                    name = call.func.id
                elif isinstance(call.func, ast.Attribute):
                    name = call.func.attr
                if name in forbidden:
                    raise AssertionError(
                        f"{path}:{call.lineno} does blocking file IO in async function {node.name}()"
                    )


def test_api_method_calls_use_valid_positional_arity() -> None:
    """Coordinator/service paths must not call JackeryApi methods with impossible arity."""
    api_tree = ast.parse(API_IMPLEMENTATION.read_text(encoding="utf-8"))
    method_arity: dict[str, tuple[int, int | None]] = {}
    for cls in [
        node
        for node in ast.walk(api_tree)
        if isinstance(node, ast.ClassDef) and node.name == "JackeryApi"
    ]:
        for item in cls.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = item.args
                positional = len(args.posonlyargs) + len(args.args)
                decorators = {
                    dec.id for dec in item.decorator_list if isinstance(dec, ast.Name)
                }
                if "classmethod" in decorators:
                    positional = max(0, positional - 1)
                elif "staticmethod" not in decorators and positional:
                    positional -= 1
                required = positional - len(args.defaults)
                max_count = None if args.vararg is not None else positional
                if not item.name.startswith("_"):
                    method_arity[item.name] = (required, max_count)

    assert method_arity
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for call in ast.walk(tree):
            if not isinstance(call, ast.Call) or not isinstance(
                call.func, ast.Attribute
            ):
                continue
            name = call.func.attr
            if name not in method_arity:
                continue
            # Only check explicit JackeryApi usage. Other objects can have
            # methods with the same public name but unrelated signatures.
            receiver = call.func.value
            if isinstance(receiver, ast.Name) and receiver.id not in {"api"}:
                continue
            if isinstance(receiver, ast.Attribute) and receiver.attr != "api":
                continue
            required, max_count = method_arity[name]
            positional = len(call.args)
            assert positional >= required, (
                f"{path}:{call.lineno} calls {name}() with too few positional args"
            )
            assert max_count is None or positional <= max_count, (
                f"{path}:{call.lineno} calls {name}() with too many positional args"
            )


def test_init_annotations_are_safe_after_pre_commit_autofix() -> None:
    """Avoid HA collection NameError after pre-commit annotation rewrites."""
    init_source = (CUSTOM_COMPONENT / "__init__.py").read_text(encoding="utf-8")
    services_source = (CUSTOM_COMPONENT / "services.py").read_text(encoding="utf-8")
    init_tree = ast.parse(init_source)

    assert isinstance(init_tree.body[0], ast.Expr)
    future_annotations = (
        isinstance(init_tree.body[1], ast.ImportFrom)
        and init_tree.body[1].module == "__future__"
        and any(alias.name == "annotations" for alias in init_tree.body[1].names)
    )
    assert future_annotations or sys.version_info >= (3, 14)
    assert "from typing import TYPE_CHECKING" not in init_source
    assert "from .coordinator import JackerySolarVaultCoordinator" in init_source
    # Service-action routing lives in services.py; the helper is private
    # there but must keep its typed signature so multi-account lookups
    # remain mypy-clean.
    assert (
        "def _loaded_coordinators(hass: HomeAssistant) "
        "-> list[JackerySolarVaultCoordinator]" in services_source
    )
    assert "from .coordinator import JackerySolarVaultCoordinator" in services_source


def test_all_python_sources_parse_with_current_and_ha_target_grammar() -> None:
    """Catch accidental syntax that only works on a different Python branch."""
    for path in _python_sources():
        source = path.read_text(encoding="utf-8")
        ast.parse(source, filename=str(path))
        # HA 2026 currently runs Python 3.14 in the user's diagnostics, but the
        # integration is intentionally packaged for Python 3.14+ only.
        ast.parse(source, filename=str(path), feature_version=(3, 14))


def test_pre_commit_python_target_matches_ha_minimum() -> None:
    """Keep pre-commit autofixes from rewriting code with newer-only syntax."""
    config = pathlib.Path(".pre-commit-config.yaml").read_text(encoding="utf-8")

    assert "python: python3.14" in config
    assert "--py314-plus" in config
    assert "python3.13" not in config
    assert "--py313-plus" not in config






def _load_py314_exception_guard_module():
    """Load the local Python 3.14 exception-style guard script."""
    spec = importlib.util.spec_from_file_location(
        "verify_py314_exception_style",
        pathlib.Path("scripts/verify_py314_exception_style.py"),
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_autofix_workflow_keeps_ruff_python314_format_stable() -> None:
    """Autofix must use one pinned Ruff with an explicit Python 3.14 target."""
    workflow = pathlib.Path(".github/workflows/autofix.yml").read_text(
        encoding="utf-8"
    )

    assert "RUFF_VERSION:" in workflow
    assert "RUFF_TARGET_VERSION: py314" in workflow
    assert '"ruff==${RUFF_VERSION}"' in workflow
    assert "astral-sh/ruff-action" not in workflow
    assert "pre-commit-ci/lite-action" not in workflow
    assert "--target-version \"${RUFF_TARGET_VERSION}\"" in workflow
    assert "--unsafe-fixes" in workflow
    assert "--add-noqa" in workflow
    assert (
        "python scripts/verify_py314_exception_style.py --fix custom_components/"
        in workflow
    )
    assert "python -m ruff format --check ${RUFF_PATHS}" in workflow
    assert "ruff check --fix" not in workflow
    assert "ref: ${{ github.head_ref || github.ref_name }}" in workflow
    assert 'target_branch="${GITHUB_HEAD_REF:-${GITHUB_REF_NAME}}"' in workflow
    assert 'git fetch origin "${target_branch}"' in workflow
    assert 'git rebase "origin/${target_branch}"' in workflow
    assert 'git push origin "HEAD:${target_branch}"' in workflow


def test_validate_ruff_job_uses_same_python314_formatter_target() -> None:
    """Validate must check exactly the formatter target that autofix writes."""
    workflow = pathlib.Path(".github/workflows/validate.yml").read_text(
        encoding="utf-8"
    )

    assert 'python -m pip install "ruff==0.15.12"' in workflow
    assert "python -m ruff check custom_components/ tests/ --target-version py314" in workflow
    assert (
        "python -m ruff format --check custom_components/ tests/ --target-version py314"
        in workflow
    )
    assert "run: ruff format --check custom_components/ tests/" not in workflow


def test_py314_exception_guard_only_rewrites_headers_that_stay_formatted() -> None:
    """The local guard must not fight Ruff's line-length-driven wrapping."""
    guard = _load_py314_exception_guard_module()

    short_old_style = """try:
    value = parse()
except (
    TypeError,
    ValueError,
):
    value = None
"""
    short_new_style = """try:
    value = parse()
except TypeError, ValueError:
    value = None
"""
    assert guard.fix_text(short_old_style, line_length=88) == short_new_style
    assert guard.violations_in_text(short_old_style, line_length=88)
    assert not guard.violations_in_text(short_new_style, line_length=88)

    long_ruff_style = """            try:
                body = await resp.json(content_type=None)
            except (
                aiohttp.ContentTypeError,
                json.JSONDecodeError,
                UnicodeDecodeError,
                ValueError,
            ):
                body = {}
"""
    assert guard.fix_text(long_ruff_style, line_length=88) == long_ruff_style
    assert not guard.violations_in_text(long_ruff_style, line_length=88)


def test_strict_work_instructions_and_repair_roadmap_are_present() -> None:
    """Implement test strict work instructions and repair roadmap are present."""
    instructions = pathlib.Path("docs/STRICT_WORK_INSTRUCTIONS.md").read_text(
        encoding="utf-8"
    )
    roadmap = pathlib.Path("docs/REPAIR_ROADMAP.md").read_text(encoding="utf-8")
    workflow = pathlib.Path(".github/workflows/validate.yml").read_text(
        encoding="utf-8"
    )
    requirements = pathlib.Path("requirements-test.txt").read_text(encoding="utf-8")
    pr_template = pathlib.Path(".github/PULL_REQUEST_TEMPLATE.md").read_text(
        encoding="utf-8"
    )

    assert "Repair the foundation first" in instructions
    assert "raw HTTP/MQTT payload" in instructions
    assert "parser -> coordinator data -> entity native value" in instructions
    assert "Do not write tests that preserve broken behavior" in instructions
    assert "Phase 1: Establish real Home Assistant test infrastructure" in roadmap
    assert "pytest-homeassistant-custom-component" in requirements
    assert "requirements-test.txt" in workflow
    assert "pytest-ha.ini" in workflow
    assert "docs/STRICT_WORK_INSTRUCTIONS.md" in pr_template


def test_generated_payload_debug_logs_are_ignored() -> None:
    """Implement test generated payload debug logs are ignored."""
    gitignore = pathlib.Path(".gitignore").read_text(encoding="utf-8")

    assert "jackery_solarvault_payload_debug.jsonl" in gitignore
    assert "jackery_solarvault_payload_debug.jsonl.1" in gitignore


def test_hacs_manifest_uses_current_supported_keys() -> None:
    """Keep hacs.json compatible with the current HACS action schema."""
    manifest = json.loads(pathlib.Path("hacs.json").read_text(encoding="utf-8"))
    supported_keys = {
        "content_in_root",
        "country",
        "filename",
        "hacs",
        "hide_default_branch",
        "homeassistant",
        "name",
        "persistent_directory",
        "zip_release",
    }

    assert "name" in manifest
    assert not (set(manifest) - supported_keys)
    assert "render_readme" not in manifest


def test_setup_entry_cleans_up_partially_initialized_coordinator() -> None:
    """Failed setup must not leak MQTT clients, timers or runtime_data."""
    init_source = (CUSTOM_COMPONENT / "__init__.py").read_text(encoding="utf-8")

    assert "import contextlib" in init_source
    assert "try:" in init_source
    assert "# Discovery must run first" in init_source
    assert "except Exception:" in init_source
    assert "with contextlib.suppress(Exception):" in init_source
    assert "await coordinator.async_shutdown()" in init_source
    assert "if entry.runtime_data is coordinator:" in init_source
    assert "entry.runtime_data = None" in init_source
    assert (
        "await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)"
        in init_source
    )


def test_brand_assets_are_packaged_without_runtime_sync() -> None:
    """Use packaged brand PNGs without mutating the custom component at runtime."""
    init_source = (CUSTOM_COMPONENT / "__init__.py").read_text(encoding="utf-8")
    readme = pathlib.Path("README.md").read_text(encoding="utf-8")
    brand_dir = CUSTOM_COMPONENT / "brand"

    assert not (CUSTOM_COMPONENT / "brand.py").exists()
    assert "_async_ensure_cached_brand_images" not in init_source
    assert "async_add_executor_job" not in init_source
    assert (brand_dir / "icon.png").is_file()
    assert (brand_dir / "icon@2x.png").is_file()
    assert (brand_dir / "dark_icon.png").is_file()
    assert (brand_dir / "dark_icon@2x.png").is_file()
    assert not pathlib.Path("brands/icon.svg").exists()
    assert not pathlib.Path("brands/logo.svg").exists()
    assert "logo.svg" not in readme
    assert "custom_components/jackery_solarvault/brand/" in readme
    assert "/homeassistant/.cache/brands/integrations/jackery/" not in readme


def test_legacy_app_cloud_values_doc_path_is_preserved() -> None:
    """Keep the old German doc path available while README links use the new path."""
    legacy = pathlib.Path("docs/Werte aus APP-Cloud.md")
    canonical = pathlib.Path("docs/APP_CLOUD_VALUES.md")

    assert legacy.is_file()
    assert canonical.is_file()
    legacy_text = legacy.read_text(encoding="utf-8")
    assert "docs/APP_CLOUD_VALUES.md" in legacy_text
    assert "Berechnung fuer `_savings_calculation.calculated_total`" in legacy_text


def test_mqtt_tls_uses_verified_jackery_ca_without_insecure_fallback() -> None:
    """MQTT TLS may ship a CA trust anchor, never keys or insecure TLS."""
    component_files = list(CUSTOM_COMPONENT.rglob("*"))
    bundled_sensitive_files = [
        path.as_posix()
        for path in component_files
        if path.is_file() and path.suffix.lower() in {".cer", ".pem", ".key"}
    ]
    assert bundled_sensitive_files == []
    assert (CUSTOM_COMPONENT / "jackery_ca.crt").is_file()
    assert "BEGIN CERTIFICATE" in (CUSTOM_COMPONENT / "jackery_ca.crt").read_text(
        encoding="utf-8"
    )

    mqtt_source = MQTT_IMPLEMENTATION.read_text(encoding="utf-8")
    coordinator_source = (CUSTOM_COMPONENT / "coordinator.py").read_text(
        encoding="utf-8"
    )
    combined = mqtt_source + coordinator_source

    assert "ssl.create_default_context()" in mqtt_source
    assert "ssl.CERT_REQUIRED" in mqtt_source
    assert "ssl.CERT_NONE" not in combined
    assert "tls_insecure=True" not in combined
    assert "disabled_after_strict_tls_failure" not in combined
    assert "ctx.load_verify_locations(cafile=str(ca_path))" in mqtt_source
    # The path call may be multi-line after ruff format; collapse whitespace
    # before checking. Equivalent to grepping "config.path(...jackery_ca.crt)".
    mqtt_source_collapsed = re.sub(r"\s+", " ", mqtt_source)
    assert (
        'self._hass.config.path( "custom_components", "jackery_solarvault", "jackery_ca.crt" )'
        in mqtt_source_collapsed
        or 'self._hass.config.path("custom_components", "jackery_solarvault", "jackery_ca.crt")'
        in mqtt_source_collapsed
    )
    assert '"tls_custom_ca_loaded": self._tls_custom_ca_loaded' in mqtt_source
    assert 'diag["tls_certificate_verification"] = "enabled"' in coordinator_source


def test_auth_failures_are_not_suppressed_by_control_or_background_paths() -> None:
    """Control writes/background helpers must propagate auth failures to HA reauth."""
    coordinator_source = (CUSTOM_COMPONENT / "coordinator.py").read_text(
        encoding="utf-8"
    )
    repairs_source = (CUSTOM_COMPONENT / "repairs.py").read_text(encoding="utf-8")
    number_source = (CUSTOM_COMPONENT / "number.py").read_text(encoding="utf-8")

    assert "def _raise_config_entry_auth_failed(" in coordinator_source
    for context in (
        "while preparing an MQTT command",
        "while refreshing MQTT command credentials",
        "while saving the single tariff",
        "while reading the current tariff",
        "while reading price sources",
        "while saving the dynamic tariff",
    ):
        assert context in coordinator_source
    assert "async_track_time_interval" not in coordinator_source
    assert "async def _async_periodic_refresh" not in coordinator_source
    assert "update_interval=update_interval" in coordinator_source
    update_block = coordinator_source.split("async def _async_update_data", 1)[1].split(
        "# ------------------------------------------------------------------\n    # Diagnostics",
        1,
    )[0]
    assert "_raise_config_entry_auth_failed" in update_block
    assert "except ConfigEntryAuthFailed:" in repairs_source
    assert "except JackeryAuthError:" in number_source


def test_brand_runtime_sync_is_absent() -> None:
    """Read-only custom component mounts are safe because setup writes no brand files."""
    init_source = (CUSTOM_COMPONENT / "__init__.py").read_text(encoding="utf-8")
    component_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in CUSTOM_COMPONENT.glob("*.py")
        if path.name != "__pycache__"
    )

    assert not (CUSTOM_COMPONENT / "brand.py").exists()
    assert "_async_ensure_cached_brand_images" not in component_sources
    assert "shutil.copy2" not in component_sources
    assert 'Path(__file__).with_name("brand")' not in component_sources
    assert "async_setup_services(hass)" in init_source
