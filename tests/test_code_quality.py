"""Lightweight static checks for integration code hygiene."""

import ast
import importlib.util
import json
import pathlib
import re
import sys
import types

import yaml

CUSTOM_COMPONENT = pathlib.Path('custom_components/jackery_solarvault')
CLIENT_PACKAGE = pathlib.Path('custom_components/jackery_solarvault/client')
API_IMPLEMENTATION = CLIENT_PACKAGE / 'api.py'
MQTT_IMPLEMENTATION = CLIENT_PACKAGE / 'mqtt_push.py'
LOG_METHODS = {'debug', 'info', 'warning', 'error', 'exception'}
PERCENT_PLACEHOLDER = re.compile(
    r'(?<!%)%(?:\([^)]+\))?[#0 +\-]*(?:\d+|\*)?(?:\.\d+)?[hlL]?[diouxXeEfFgGcrs]'
)


def _load_util_module():
    package_dir = CUSTOM_COMPONENT
    sys.modules.setdefault('custom_components', types.ModuleType('custom_components'))
    package = types.ModuleType('custom_components.jackery_solarvault')
    package.__path__ = [str(package_dir)]
    sys.modules.setdefault('custom_components.jackery_solarvault', package)

    const_spec = importlib.util.spec_from_file_location(
        'custom_components.jackery_solarvault.const',
        package_dir / 'const.py',
    )
    assert const_spec is not None
    const_module = importlib.util.module_from_spec(const_spec)
    sys.modules[const_spec.name] = const_module
    assert const_spec.loader is not None
    const_spec.loader.exec_module(const_module)

    spec = importlib.util.spec_from_file_location(
        'custom_components.jackery_solarvault.util',
        package_dir / 'util.py',
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


util = _load_util_module()


def _python_sources() -> list[pathlib.Path]:
    return sorted(CUSTOM_COMPONENT.glob('*.py'))


def _translation_sources() -> dict[str, str]:
    """Return strings.json and every locale translation file."""
    paths = [
        CUSTOM_COMPONENT / 'strings.json',
        *sorted((CUSTOM_COMPONENT / 'translations').glob('*.json')),
    ]
    return {
        path.relative_to(CUSTOM_COMPONENT).as_posix(): path.read_text(encoding='utf-8')
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
            and node.func.attr == 'async_abort'
        ):
            continue
        for keyword in node.keywords:
            if (
                keyword.arg == 'reason'
                and isinstance(keyword.value, ast.Name)
                and keyword.value.id == 'FLOW_ABORT_REAUTH_ENTRY_MISSING'
            ):
                return True
    return False


def _reauth_entry_lookup_is_guarded(config_tree: ast.Module) -> bool:
    """Return whether _get_reauth_entry is guarded against missing entries."""
    for node in ast.walk(config_tree):
        if not (
            isinstance(node, ast.AsyncFunctionDef)
            and node.name == 'async_step_reauth_confirm'
        ):
            continue
        for try_node in (
            child for child in ast.walk(node) if isinstance(child, ast.Try)
        ):
            calls_reauth_entry = any(
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and child.func.attr == '_get_reauth_entry'
                for child in ast.walk(ast.Module(body=try_node.body, type_ignores=[]))
            )
            if not calls_reauth_entry:
                continue
            for handler in try_node.handlers:
                if {'KeyError', 'RuntimeError'} <= _exception_names(
                    handler.type
                ) and _handler_aborts_reauth_entry_missing(handler):
                    return True
    return False


def test_manifest_treats_recorder_as_optional_after_dependency() -> None:
    """HA fixture collection should not hard-require recorder setup."""
    manifest = json.loads(
        (CUSTOM_COMPONENT / 'manifest.json').read_text(encoding='utf-8')
    )

    assert 'recorder' not in manifest.get('dependencies', [])
    assert 'recorder' in manifest.get('after_dependencies', [])
    assert manifest['iot_class'] == 'cloud_polling'


def test_no_duplicate_literal_dict_keys() -> None:
    """Catch accidental duplicate payload keys such as login registerAppId."""
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding='utf-8'))
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
        tree = ast.parse(path.read_text(encoding='utf-8'))
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
            fmt = node.args[0].value.replace('%%', '')
            expected = len(PERCENT_PLACEHOLDER.findall(fmt))
            actual = len(node.args) - 1
            assert actual == expected, (
                f"{path}:{node.lineno} logging args mismatch: "
                f"expected {expected}, got {actual}, format={node.args[0].value!r}"
            )


def test_mqtt_wire_message_literals_are_centralized() -> None:
    """Keep documented MQTT messageType strings in const.py only."""
    forbidden = {
        'DevicePropertyChange',
        'ControlCombine',
        'QueryCombineData',
        'UploadCombineData',
        'UploadIncrementalCombineData',
        'UploadWeatherPlan',
        'QueryWeatherPlan',
        'SendWeatherAlert',
        'CancelWeatherAlert',
        'DownloadDeviceSchedule',
        'QuerySubDeviceGroupProperty',
    }
    for path in _python_sources():
        if path.name == 'const.py':
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in forbidden:
                raise AssertionError(
                    f"{path}:{node.lineno} uses raw MQTT messageType {node.value!r}; "
                    'use const.py instead'
                )


def test_period_reset_descriptions_use_date_type_constants() -> None:
    """Prevent drift between period sensors and documented dateType constants."""
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            for keyword in node.keywords:
                if keyword.arg != 'reset_period':
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
    path = CUSTOM_COMPONENT / 'const.py'
    tree = ast.parse(path.read_text(encoding='utf-8'))
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
        'aPhasePw',
        'bPhasePw',
        'cPhasePw',
        'tPhasePw',
        'anPhasePw',
        'bnPhasePw',
        'cnPhasePw',
        'tnPhasePw',
        'power1',
        'power2',
        'power3',
    }
    for path in _python_sources():
        if path.name == 'const.py':
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in forbidden:
                raise AssertionError(
                    f"{path}:{node.lineno} uses raw CT wire key {node.value!r}; "
                    'use const.py instead'
                )


def test_mqtt_credential_keys_are_centralized() -> None:
    """Keep login/MQTT credential dict keys centralized in const.py."""
    forbidden = {'mqttPassWord', 'userId', 'client_id', 'user_id'}
    for path in _python_sources():
        if path.name == 'const.py':
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in forbidden:
                raise AssertionError(
                    f"{path}:{node.lineno} uses raw MQTT credential key {node.value!r}; "
                    'use const.py instead'
                )


def test_mqtt_topic_literals_are_centralized() -> None:
    """Keep documented MQTT topic layout in const.py only."""
    forbidden = {'hb/app', 'hb/app/'}
    for path in _python_sources():
        if path.name == 'const.py':
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in forbidden:
                raise AssertionError(
                    f"{path}:{node.lineno} uses raw MQTT topic prefix {node.value!r}; "
                    'use MQTT_TOPIC_PREFIX from const.py instead'
                )


def test_app_period_stat_keys_are_centralized() -> None:
    """Keep app period/trend stat-key strings centralized in const.py."""
    forbidden = {
        'totalInCtEnergy',
        'totalOutCtEnergy',
        'totalChgEgy',
        'totalDisChgEgy',
    }
    for path in _python_sources():
        if path.name == 'const.py':
            continue
        tree = ast.parse(path.read_text(encoding='utf-8'))
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and node.value in forbidden:
                raise AssertionError(
                    f"{path}:{node.lineno} uses raw app stat key {node.value!r}; "
                    'use APP_STAT_* constants from const.py instead'
                )


def _const_string_values(name: str) -> tuple[str, ...]:
    """Read a tuple/frozenset/list constant of strings from const.py via AST."""
    const_tree = ast.parse((CUSTOM_COMPONENT / 'const.py').read_text(encoding='utf-8'))
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
            and node.func.id == 'frozenset'
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
    values = _const_string_values('PRESERVED_FAST_PAYLOAD_KEYS')
    assert values == (
        'ct_meter',
        'meter_heads',
        'smart_plugs',
        'weather_plan',
        'task_plan',
        'notice',
        'mqtt_last',
        'third_party_mqtt_config',
    )
    forbidden_fragments = ('Query', 'Upload', 'Control', 'DevicePropertyChange')
    for value in values:
        assert not any(fragment in value for fragment in forbidden_fragments)


def test_app_specific_subdevice_markers_are_centralized() -> None:
    """Avoid scattering magic devType/subType numbers through code."""
    forbidden_contexts = ('FIELD_DEV_TYPE', 'FIELD_DEVICE_TYPE', 'FIELD_SUB_TYPE')
    for path in _python_sources():
        if path.name == 'const.py':
            continue
        source = path.read_text(encoding='utf-8')
        tree = ast.parse(source)
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Constant) and node.value in {'2', '3'}):
                continue
            line = source.splitlines()[node.lineno - 1]
            if any(context in line for context in forbidden_contexts):
                raise AssertionError(
                    f"{path}:{node.lineno} uses raw subdevice marker {node.value!r}; "
                    'use SUBDEVICE_TYPE_* constants'
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
    const_source = (CUSTOM_COMPONENT / 'const.py').read_text(encoding='utf-8')
    init_source = (CUSTOM_COMPONENT / '__init__.py').read_text(encoding='utf-8')
    config_tree = ast.parse(
        (CUSTOM_COMPONENT / 'config_flow.py').read_text(encoding='utf-8')
    )

    assert 'CONFIG_ENTRY_VERSION' not in const_source
    assert 'CONF_DEVICE_ID' not in const_source
    assert 'CONF_SYSTEM_ID' not in const_source
    assert 'CONF_SCAN_INTERVAL' not in const_source
    assert 'async_migrate_entry' not in init_source
    assert '_async_clean_entry_config' not in init_source
    assert 'async_update_entry(entry' not in init_source
    assert 'version=' not in init_source
    assert _class_constant_int(config_tree, 'JackeryConfigFlow', 'VERSION') == 1


def test_ci_runs_all_pure_tests_and_compile_check() -> None:
    """Keep GitHub Actions aligned with the local validation command."""
    workflow = pathlib.Path('.github/workflows/validate.yml').read_text(
        encoding='utf-8'
    )
    package = pathlib.Path('package.json').read_text(encoding='utf-8')
    package_scripts = json.loads(package)['scripts']
    assert 'bun run ci' in workflow
    assert 'python scripts/check_compile.py' in package
    assert 'bun run typecheck' in package
    assert 'python -m pytest -q -c pyproject.toml' in package
    assert (
        'addopts=-q -ra --strict-markers --strict-config --tb=short --maxfail=20'
        in package
    )
    assert '-p pytest_asyncio.plugin -p no:cacheprovider' in package
    assert 'pytest-unit.ini' not in package
    assert 'docs:check' not in package_scripts['ha:best-practices']
    assert 'tests/test_power_math.py' not in workflow


def test_workflow_cache_paths_reference_existing_dependency_files() -> None:
    """Cache dependency paths in workflows should not silently point at typos."""
    missing: list[str] = []
    for workflow in pathlib.Path('.github/workflows').glob('*.y*ml'):
        source = workflow.read_text(encoding='utf-8')
        for match in re.finditer(r'^\s{12}([^\s#][^\n#]*)$', source, re.MULTILINE):
            candidate = match.group(1).strip()
            if not candidate.startswith('requirements') or not candidate.endswith(
                '.txt'
            ):
                continue
            if not pathlib.Path(candidate).exists():
                missing.append(f"{workflow}:{candidate}")
    assert not missing


def test_ruff_baseline_uses_pinned_python_314_exception_formatting() -> None:
    """Ruff baseline must not drift back to pre-3.14 exception formatting."""
    workflow = pathlib.Path('.github/workflows/ruff-baseline.yml').read_text(
        encoding='utf-8'
    )

    assert 'RUFF_VERSION:' in workflow
    assert 'RUFF_TARGET_VERSION: py314' in workflow
    assert 'bun run install:ruff-baseline' in workflow
    assert 'python -m pip install --upgrade pip ruff' not in workflow
    assert 'python -m ruff --version' in workflow
    assert workflow.count('--target-version "${RUFF_TARGET_VERSION}"') >= 3
    assert 'bun run format' in workflow
    assert 'python scripts/verify_py314_exception_style.py --fix' in workflow
    assert 'python scripts/verify_py314_exception_style.py' in workflow
    assert '--unsafe-fixes' not in workflow
    assert '--exit-zero' in workflow
    assert '--output-format=concise' in workflow


def test_ruff_autofix_uses_safe_fixes_only() -> None:
    """Local and workflow autofix paths must not enable Ruff unsafe fixes."""
    pyproject = pathlib.Path('pyproject.toml').read_text(encoding='utf-8')
    package = json.loads(pathlib.Path('package.json').read_text(encoding='utf-8'))
    workflow_sources = '\n'.join(
        path.read_text(encoding='utf-8')
        for path in (
            pathlib.Path('.github/workflows/autofix.yml'),
            pathlib.Path('.github/workflows/ruff-baseline.yml'),
        )
    )

    assert 'unsafe-fixes = false' in pyproject
    assert 'extend-unsafe-fixes' not in pyproject
    assert '--unsafe-fixes' not in package['scripts']['lint:fix']
    assert '--unsafe-fixes' not in package['scripts']['autofix']
    assert '--unsafe-fixes' not in workflow_sources


def test_py314_exception_style_guard_detects_reverted_multi_except_headers() -> None:
    """The workflow guard should fail old no-``as`` multi-exception headers only."""
    script = pathlib.Path('scripts/verify_py314_exception_style.py')
    spec = importlib.util.spec_from_file_location(
        'verify_py314_exception_style', script
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.violations_in_text(
        'try:\n    pass\nexcept (ValueError, TypeError):\n    pass\n'
    ) == [(3, 'except (ValueError, TypeError):')]
    assert module.violations_in_text(
        'try:\n    pass\nexcept (\n    ValueError,\n    TypeError,\n):\n    pass\n'
    ) == [
        (
            3,
            'except (\n    ValueError,\n    TypeError,\n):',
        )
    ]
    assert (
        module.violations_in_text(
            'try:\n    pass\nexcept ValueError, TypeError:\n    pass\n'
        )
        == []
    )
    assert (
        module.violations_in_text(
            'try:\n'
            '    pass\n'
            'except (ValueError, TypeError) as err:\n'
            '    raise RuntimeError from err\n'
        )
        == []
    )
    assert module.fix_text(
        'try:\n    pass\nexcept (ValueError, TypeError):\n    pass\n'
    ) == ('try:\n    pass\nexcept ValueError, TypeError:\n    pass\n')
    assert module.fix_text(
        'try:\n'
        '    pass\n'
        'except (ValueError, TypeError) as err:\n'
        '    raise RuntimeError from err\n'
    ) == (
        'try:\n'
        '    pass\n'
        'except (ValueError, TypeError) as err:\n'
        '    raise RuntimeError from err\n'
    )


def test_period_source_diagnostics_stay_minimal() -> None:
    """Period sensors should expose calculation facts, not redundant contracts."""
    const_source = (CUSTOM_COMPONENT / 'const.py').read_text(encoding='utf-8')
    sensor_source = (CUSTOM_COMPONENT / 'sensor.py').read_text(encoding='utf-8')
    (CUSTOM_COMPONENT / 'util.py').read_text(encoding='utf-8')
    # Period sensors expose only compact diagnostic facts. JSON-stringified
    # duplicates and cloud-shape heuristics belong in diagnostics /
    # payload_debug, not in entity attributes.
    for removed in (
        'SOURCE_CONTRACT_',
        'SOURCE_KIND_',
        'CHART_KIND_APP_BUCKET_SERIES',
        'attrs["source_contract"]',
        'attrs["source_kind"]',
        'attrs["chart_kind"]',
        'attrs["external_statistics_id"]',
        'attrs["external_statistics_bucket"]',
        'attrs["native_source"]',
        'attrs["period_total_method"]',
        'attrs["server_total_used"]',
        'attrs["period_labels"]',
        'attrs["period_labels_count"]',
        'attrs["period_labels_json"]',
        'attrs["period_values_count"]',
        'attrs["period_values_json"]',
        'attrs["period_values_by_label_json"]',
        'attrs["cloud_year_chart_nonzero_months"]',
        'attrs["cloud_year_chart_first_nonzero_month"]',
        'attrs["cloud_year_chart_last_nonzero_month"]',
        'attrs["cloud_year_appears_incomplete"]',
    ):
        assert removed not in const_source
        assert removed not in sensor_source
    for kept in (
        '"source_section"',
        '"source_key"',
        '"chart_series_key"',
        '"chart_series_sum"',
        '"server_total"',
        '"period_values"',
        '"request"',
    ):
        assert kept in sensor_source


def test_diagnostics_anonymize_outer_payload_keys() -> None:
    """Diagnostics must not expose device IDs or serials as raw map keys."""
    source = (CUSTOM_COMPONENT / 'diagnostics.py').read_text(encoding='utf-8')
    assert 'def _redacted_payload_map(' in source
    assert (
        'devices = _redacted_payload_map(coordinator.data or {}, "device", redact_keys)'
        in source
    )
    for forbidden in (
        'dev_id: async_redact_data',
        'key: async_redact_data',
        'sn: async_redact_data',
        'sn_or_id: async_redact_data',
    ):
        assert forbidden not in source
    for prefix in (
        'property_response',
        'device_statistic_response',
        'device_period_stat_response',
        'battery_pack_response',
        'ota_response',
        'location_response',
    ):
        assert f'"{prefix}"' in source


def test_polling_and_statistics_import_diagnostics_are_exported() -> None:
    """Diagnostics should show whether polling/import is live or cached."""
    diagnostics_source = (CUSTOM_COMPONENT / 'diagnostics.py').read_text(
        encoding='utf-8'
    )
    coordinator_source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(
        encoding='utf-8'
    )

    assert 'def polling_diagnostics(self) -> dict[str, Any]:' in coordinator_source
    assert 'def statistics_import_diagnostics(self) -> dict[str, Any]:' in (
        coordinator_source
    )
    assert '"polling": async_redact_data(' in diagnostics_source
    assert '"statistics_import": async_redact_data(' in diagnostics_source
    assert '"statistics_backfill"' not in diagnostics_source
    for key in (
        '"cache_hits"',
        '"fetches"',
        '"empty_fetches"',
        '"failures"',
        '"property_fetch_completed"',
        '"statistics_import_last_decision"',
    ):
        assert key in coordinator_source
    for key in (
        '"last_schedule_decision"',
        '"last_status"',
        '"last_current_entity_imported_rows"',
    ):
        assert key in coordinator_source


def test_polling_diagnostic_counter_uses_safe_int_parser() -> None:
    """Polling diagnostics must not raw-cast counter values."""
    source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(encoding='utf-8')
    block = source.split('def _bump_polling_diag', 1)[1].split(
        '\n\n        # Per-system calls',
        1,
    )[0]

    assert 'current = safe_int(values.get(key)) or 0' in block
    assert 'values[key] = current + 1' in block
    assert ' = int(values.get' not in block


def test_diagnostic_second_values_use_safe_int_parser() -> None:
    """Diagnostics second counters should avoid raw int(total_seconds()) casts."""
    coordinator_source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(
        encoding='utf-8'
    )
    diagnostics_source = (CUSTOM_COMPONENT / 'diagnostics.py').read_text(
        encoding='utf-8'
    )
    mqtt_diag = coordinator_source.split('def mqtt_diagnostics_snapshot', 1)[1].split(
        '\n    @property',
        1,
    )[0]

    assert 'safe_int(coordinator.configured_update_interval.total_seconds())' in (
        diagnostics_source
    )
    assert 'safe_int(self._configured_update_interval.total_seconds())' in mqtt_diag
    assert 'pause_remaining = safe_int(' in mqtt_diag
    assert ' int(coordinator.configured_update_interval.total_seconds())' not in (
        diagnostics_source
    )
    assert ' int(self._configured_update_interval.total_seconds())' not in mqtt_diag
    assert ' int(self._mqtt_paused_until_monotonic - now_mono)' not in mqtt_diag


def test_coordinator_interval_seconds_use_safe_int_parser() -> None:
    """Coordinator interval-derived seconds should avoid raw int casts."""
    source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(encoding='utf-8')
    init_block = source.split('def __init__', 1)[1].split(
        '\n        # Mapping deviceId',
        1,
    )[0]

    assert (
        'interval_sec = max(15, safe_int(update_interval.total_seconds()) or 15)'
        in (init_block)
    )
    assert 'interval_sec = max(15, int(' not in init_block


def test_diagnostics_redaction_keys_cover_sensitive_jackery_fields() -> None:
    """Keep diagnostics aligned with HA's share-safe diagnostics rule."""
    const_source = (CUSTOM_COMPONENT / 'const.py').read_text(encoding='utf-8')
    redaction_block = const_source.split('REDACT_KEYS: Final = {', 1)[1].split(
        '}\n\n# MQTT', 1
    )[0]
    for required in (
        'CONF_MQTT_MAC_ID',
        'CONF_REGION_CODE',
        'FIELD_TOKEN',
        'FIELD_MQTT_PASSWORD',
        'FIELD_DEVICE_ID',
        'FIELD_SYSTEM_ID',
        'FIELD_DEVICE_SN',
        'FIELD_SYSTEM_SN',
        'FIELD_LONGITUDE',
        'FIELD_LATITUDE',
        '"base64_encoded"',
        '"body_preview"',
        '"email"',
        '"phone"',
        '"raw_bytes"',
        '"raw_hex"',
        '"trailer_hex"',
    ):
        assert required in redaction_block


def test_ble_transport_debug_logs_do_not_expose_raw_payloads() -> None:
    """BLE debug logs must not expose raw encrypted or decoded frame payloads."""
    source = (CUSTOM_COMPONENT / 'client' / 'ble_transport.py').read_text(
        encoding='utf-8'
    )

    assert 'raw=%s' not in source
    assert 'base64=%s' not in source
    assert 'parsed.body[:200].decode("utf-8", errors="replace")' not in source
    assert 'Logs frame sizes and parse metadata' in source


def test_ble_transport_numeric_options_use_shared_integer_parser() -> None:
    """BLE diagnostic options must reject bool/non-finite numeric input."""
    source = (CUSTOM_COMPONENT / 'client' / 'ble_transport.py').read_text(
        encoding='utf-8'
    )

    assert 'from ..util import first_nonblank_int' in source
    assert 'def _coerce_ble_int(' in source
    assert 'mtu = _coerce_ble_int(mtu_override, "mtu_override")' in source
    assert 'frozenset(_coerce_ble_int(cmd, "ack_cmds") for cmd in ack_cmds)' in source
    assert 'mtu = int(mtu_override)' not in source
    assert 'frozenset(int(c) for c in ack_cmds)' not in source


def test_sensor_division_transform_uses_safe_float_parser() -> None:
    """Scaled sensor transforms must not expose NaN/Infinity as native values."""
    source = (CUSTOM_COMPONENT / 'sensor.py').read_text(encoding='utf-8')
    block = source.split('def _div(', 1)[1].split('\n\ndef _signed_diff', 1)[0]

    assert 'parsed = safe_float(value)' in block
    assert 'if parsed is None:' in block
    assert 'return round(parsed / divisor, 2)' in block
    assert 'return round(float(' not in block
    assert 'parsed = float(' not in block


def test_ble_transport_sensor_attributes_do_not_expose_raw_payloads() -> None:
    """BLE status entity attributes must keep debug-only data out of recorder."""
    source = (CUSTOM_COMPONENT / 'sensor.py').read_text(encoding='utf-8')
    cls = source.split('class JackeryBleTransportSensor', 1)[1].split(
        'class JackeryWeatherPlanSensor', 1
    )[0]

    assert 'attrs.pop("unrouted_frames_by_cmd", None)' in cls
    assert 'frame_attrs.pop("raw_hex", None)' in cls
    assert 'parsed_attrs.pop("body_preview", None)' in cls
    assert 'parsed_attrs.pop("trailer_hex", None)' in cls
    assert 'safe_int(self._observation().get("frames_decoded")) or 0' in cls
    assert 'return int(self._observation()' not in cls
    assert '= int(self._observation()' not in cls
    assert 'return self._observation()' not in cls


def test_subdevice_attributes_do_not_publish_serials_or_network_ids() -> None:
    """Subdevice entity attributes should avoid serial numbers and IP-like IDs."""
    sensitive_fields = ('FIELD_DEVICE_SN', 'FIELD_DEV_SN', 'FIELD_SN', 'FIELD_WIP')

    binary_source = (CUSTOM_COMPONENT / 'binary_sensor.py').read_text(encoding='utf-8')
    switch_source = (CUSTOM_COMPONENT / 'switch.py').read_text(encoding='utf-8')
    sensor_source = (CUSTOM_COMPONENT / 'sensor.py').read_text(encoding='utf-8')
    const_source = (CUSTOM_COMPONENT / 'const.py').read_text(encoding='utf-8')

    blocks = [
        binary_source
        .split('class JackerySmartPlugStateBinarySensor', 1)[1]
        .split(
            '# ---------------------------------------------------------------------------',
            1,
        )[0]
        .split('def extra_state_attributes', 1)[1]
        .split('return attrs', 1)[0],
        switch_source
        .split('class JackerySmartPlugSwitch', 1)[1]
        .split('class JackerySmartPlugPrioritySwitch', 1)[0]
        .split('def extra_state_attributes', 1)[1]
        .split('return attrs', 1)[0],
        sensor_source
        .split('class JackerySmartPlugSensor(JackeryEntity', 1)[1]
        .split('class JackeryMeterHeadSensor', 1)[0]
        .split('def extra_state_attributes', 1)[1]
        .split('return attrs', 1)[0],
        sensor_source
        .split('class JackeryMeterHeadSensor(JackeryEntity', 1)[1]
        .split('class JackerySmartMeterSensor', 1)[0]
        .split('def extra_state_attributes', 1)[1]
        .split('return attrs', 1)[0],
    ]
    ct_attr_fields = const_source.split('CT_ATTRIBUTE_FIELDS: Final = (', 1)[1].split(
        ')\n\nFIELD_TARGET_MODULE_VERSION', 1
    )[0]
    blocks.append(ct_attr_fields)

    for block in blocks:
        for field in sensitive_fields:
            assert field not in block


def test_unredacted_diagnostics_option_is_options_flow_only() -> None:
    """The unsafe raw-diagnostics switch belongs in options, not setup."""
    const_source = (CUSTOM_COMPONENT / 'const.py').read_text(encoding='utf-8')
    config_flow_source = (CUSTOM_COMPONENT / 'config_flow.py').read_text(
        encoding='utf-8'
    )

    assert (
        'CONF_ENABLE_UNREDACTED_DIAGNOSTICS: Final = "enable_unredacted_diagnostics"'
        in const_source
    )
    assert 'DEFAULT_ENABLE_UNREDACTED_DIAGNOSTICS: Final = False' in const_source
    assert (
        'CONF_ENABLE_UNREDACTED_DIAGNOSTICS: DEFAULT_ENABLE_UNREDACTED_DIAGNOSTICS'
    ) in config_flow_source

    user_schema = config_flow_source.split('USER_SCHEMA = vol.Schema', 1)[1].split(
        'class JackeryOptionsFlow', 1
    )[0]
    options_block = config_flow_source.split('class JackeryOptionsFlow', 1)[1].split(
        'class JackeryConfigFlow', 1
    )[0]
    assert 'CONF_ENABLE_UNREDACTED_DIAGNOSTICS' not in user_schema
    assert 'CONF_ENABLE_UNREDACTED_DIAGNOSTICS' in options_block


def test_unredacted_diagnostics_option_reaches_redaction_surfaces() -> None:
    """Diagnostics and JSONL payload debug must share the same raw-data toggle."""
    diagnostics_source = (CUSTOM_COMPONENT / 'diagnostics.py').read_text(
        encoding='utf-8'
    )
    coordinator_source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(
        encoding='utf-8'
    )
    mqtt_source = MQTT_IMPLEMENTATION.read_text(encoding='utf-8')

    assert 'active_redact_keys(entry)' in diagnostics_source
    assert 'diagnostic_redactions_disabled(entry)' in diagnostics_source
    assert 'mqtt_diagnostics_snapshot(' in diagnostics_source
    assert 'redact_topics=not redactions_disabled' in diagnostics_source
    assert 'diagnostic_redactions_disabled(self.entry)' in coordinator_source
    assert (
        'def diagnostics_snapshot(self, *, redact_topics: bool = True)' in mqtt_source
    )


def test_payload_debug_redaction_can_be_disabled_by_entry_option(monkeypatch) -> None:
    """The HAOS-friendly option should replace the env-var for local raw logs."""
    monkeypatch.delenv('JACKERY_DEV_MODE', raising=False)
    entry = types.SimpleNamespace(
        options={'enable_unredacted_diagnostics': True},
        data={},
    )

    assert util.diagnostic_redactions_disabled(entry) is True
    assert util.active_redact_keys(entry) == frozenset()

    event = {
        'password': 'account-secret',
        'nested': {'mqttPassWord': 'mqtt-seed'},
        'items': ({'bluetoothKey': 'ble-key'},),
    }
    redacted = util._payload_debug_redacted(event, False)
    raw = util._payload_debug_redacted(event, True)
    entity_attrs = util.redacted_json_safe_payload(event)

    assert redacted['password'] == '**REDACTED**'
    assert redacted['nested']['mqttPassWord'] == '**REDACTED**'
    assert raw['password'] == 'account-secret'
    assert raw['nested']['mqttPassWord'] == 'mqtt-seed'
    assert raw['items'][0]['bluetoothKey'] == 'ble-key'
    assert entity_attrs['password'] == '**REDACTED**'
    assert entity_attrs['nested']['mqttPassWord'] == '**REDACTED**'
    assert entity_attrs['items'][0]['bluetoothKey'] == '**REDACTED**'


def test_raw_properties_sensor_redacts_state_attributes() -> None:
    """Raw-properties sensor attributes can enter Recorder and must be redacted."""
    source = (CUSTOM_COMPONENT / 'sensor.py').read_text(encoding='utf-8')
    cls = source.split('class JackeryRawPropertiesSensor', 1)[1].split(
        'class JackeryBleTransportSensor', 1
    )[0]

    assert 'redacted_json_safe_payload(self._properties)' in cls
    assert 'json.dumps(v)' not in cls
    assert 'return redacted if isinstance(redacted, dict) else {}' in cls


def test_config_flow_connection_failures_are_not_error_logged() -> None:
    """Expected setup/reconfigure/reauth connection failures should stay quiet."""
    source = (CUSTOM_COMPONENT / 'config_flow.py').read_text(encoding='utf-8')

    assert '_LOGGER.error(' not in source
    assert 'errors[FLOW_ERROR_BASE] = FLOW_ERROR_CANNOT_CONNECT' in source
    assert 'Cannot connect to Jackery during setup' in source
    assert 'Cannot connect to Jackery during reconfigure' in source
    assert 'Cannot connect to Jackery during reauth' in source


def test_expected_entity_action_failures_are_not_error_logged() -> None:
    """User-triggered action failures should be returned to HA, not double-logged."""
    for module_name in ('number.py', 'text.py'):
        source = (CUSTOM_COMPONENT / module_name).read_text(encoding='utf-8')
        assert '_LOGGER.error(' not in source


def test_number_platform_has_no_unwired_experimental_setter() -> None:
    """Number platform should not keep dead experimental write paths."""
    source = (CUSTOM_COMPONENT / 'number.py').read_text(encoding='utf-8')

    assert '_set_max_power_experimental' not in source
    assert 'JackeryError' not in source


def test_mqtt_diagnostics_do_not_expose_mac_id_suffix() -> None:
    """Diagnostics may report credential source, but not device-correlating IDs."""
    coordinator_source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(
        encoding='utf-8'
    )

    assert 'diag["credential_mac_id_source"] = self.api.mqtt_mac_id_source' in (
        coordinator_source
    )
    assert 'credential_mac_id_suffix' not in coordinator_source


def test_entity_platforms_use_shared_unique_id_append_helper() -> None:
    """Keep duplicate unique_id filtering in one shared setup helper."""
    platform_files = {
        'binary_sensor.py',
        'button.py',
        'number.py',
        'select.py',
        'sensor.py',
        'switch.py',
        'text.py',
    }
    for name in platform_files:
        source = (CUSTOM_COMPONENT / name).read_text(encoding='utf-8')
        assert 'append_unique_entity(' in source, name
        assert 'seen_unique_ids.add' not in source, name
        assert 'Skip duplicate' not in source, name


def test_unique_id_helper_is_the_only_duplicate_entity_skip_logger() -> None:
    """Do not copy/paste platform-local duplicate entity logging again."""
    for path in _python_sources():
        source = path.read_text(encoding='utf-8')
        if path.name == 'util.py':
            assert 'Skip duplicate %s unique_id=%s' in source
            continue
        assert 'Skip duplicate' not in source, path


def test_unique_id_contract_is_documented_and_followed() -> None:
    """Unique IDs must stay independent from names/translations."""
    entity_source = (CUSTOM_COMPONENT / 'entity.py').read_text(encoding='utf-8')
    assert 'self._attr_unique_id = f"{device_id}_{key_suffix}"' in entity_source
    forbidden_fragments = {
        'FIELD_DEVICE_NAME',
        'FIELD_WNAME',
        'translation_key',
        'name=',
    }
    assignment_line = next(
        line.strip()
        for line in entity_source.splitlines()
        if 'self._attr_unique_id' in line
    )
    for fragment in forbidden_fragments:
        assert fragment not in assignment_line


def test_battery_pack_unique_ids_keep_stable_index_suffix() -> None:
    """Battery-pack entities must not use serial/name fields for unique_id."""
    sensor_source = (CUSTOM_COMPONENT / 'sensor.py').read_text(encoding='utf-8')
    assert 'f"battery_pack_{pack_index}_{description.key}"' in sensor_source
    pack_class = sensor_source.split(
        'class JackeryBatteryPackSensor(JackeryEntity, SensorEntity):', 1
    )[1].split('class JackerySmartMeterSensor', 1)[0]
    pack_init = pack_class.split('    def __init__(', 1)[1].split(
        '    @property\n    def _pack', 1
    )[0]
    assert 'FIELD_DEVICE_NAME' not in pack_init
    assert 'FIELD_WNAME' not in pack_init
    assert 'FIELD_SN' not in pack_init


def test_smart_plug_unique_ids_keep_stable_index_suffix() -> None:
    """Smart-plug entities keep names and serials out of unique IDs."""
    sensor_source = (CUSTOM_COMPONENT / 'sensor.py').read_text(encoding='utf-8')
    binary_source = (CUSTOM_COMPONENT / 'binary_sensor.py').read_text(encoding='utf-8')
    switch_source = (CUSTOM_COMPONENT / 'switch.py').read_text(encoding='utf-8')

    assert 'f"smart_plug_{plug_index}_{description.key}"' in sensor_source
    assert 'f"smart_plug_{plug_index}_switch_state"' in binary_source
    assert 'f"smart_plug_{plug_index}_switch"' in switch_source
    assert 'f"smart_plug_{plug_index}_priority_enabled"' in switch_source

    # Smart-plug entities must build device_info from plug metadata via the
    # shared helper in entity.py — name and serial are read from the payload
    # there, not embedded in unique IDs.
    for source in (sensor_source, binary_source, switch_source):
        assert '_build_smart_plug_device_info(' in source

    entity_source = (CUSTOM_COMPONENT / 'entity.py').read_text(encoding='utf-8')
    assert 'FIELD_DEVICE_SN)' in entity_source
    assert 'FIELD_SCAN_NAME' in entity_source


def test_meter_head_unique_ids_keep_stable_index_suffix() -> None:
    """Meter-head entities keep names and serials out of unique IDs."""
    sensor_source = (CUSTOM_COMPONENT / 'sensor.py').read_text(encoding='utf-8')

    assert 'f"meter_head_{meter_head_index}_{description.key}"' in sensor_source
    meter_head_class = sensor_source.split(
        'class JackeryMeterHeadSensor(JackeryEntity, SensorEntity):', 1
    )[1].split('class JackerySmartMeterSensor', 1)[0]
    meter_head_init = meter_head_class.split('    def __init__(', 1)[1].split(
        '    @property\n    def _meter_head', 1
    )[0]
    assert 'FIELD_DEVICE_SN' not in meter_head_init
    assert 'FIELD_DEVICE_NAME' not in meter_head_init
    assert 'FIELD_SCAN_NAME' not in meter_head_init


def test_data_quality_repair_issue_is_wired_with_guarded_year_backfill() -> None:
    """Contradictory app data still creates diagnostics around guarded states."""
    const_source = (CUSTOM_COMPONENT / 'const.py').read_text(encoding='utf-8')
    coordinator_source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(
        encoding='utf-8'
    )
    util_source = (CUSTOM_COMPONENT / 'util.py').read_text(encoding='utf-8')
    translation_sources = _translation_sources()

    assert 'PAYLOAD_DATA_QUALITY' in const_source
    assert 'REPAIR_ISSUE_APP_DATA_INCONSISTENCY' in const_source
    assert 'app_data_quality_warnings(entry, today=today)' in coordinator_source
    assert '_async_update_data_quality_issue' in coordinator_source
    assert 'normalized_data_quality_warnings(warnings)' in coordinator_source
    assert 'format_data_quality_warning(warning)' in coordinator_source
    assert 'DATA_QUALITY_REPAIR_EXAMPLE_LIMIT' in coordinator_source
    assert 'async_create_issue' in coordinator_source
    assert 'async_delete_issue' in coordinator_source
    assert 'issue_suffix = f"_{REPAIR_ISSUE_APP_DATA_INCONSISTENCY}"' in (
        coordinator_source
    )
    assert 'for domain, existing_issue_id in tuple(registry.issues):' in (
        coordinator_source
    )
    assert 'existing_issue_id != issue_id' in coordinator_source
    assert 'app_data_quality_warnings' in util_source
    assert 'normalized_data_quality_warnings' in util_source
    assert 'format_data_quality_warning' in util_source
    assert 'DATA_QUALITY_KEY_SOURCE_VALUE' in const_source
    for path, source in translation_sources.items():
        assert 'app_data_inconsistency' in source, path
        assert '{examples}' in source, path
        assert '{source_section}' not in source, path
        assert '{reference_section}' not in source, path


def test_services_yaml_matches_registered_services_and_validates_numeric_ids() -> None:
    """Keep services.yaml, const.py field constants, and setup schemas aligned."""
    services = yaml.safe_load(
        (CUSTOM_COMPONENT / 'services.yaml').read_text(encoding='utf-8')
    )
    const_source = (CUSTOM_COMPONENT / 'const.py').read_text(encoding='utf-8')
    services_source = (CUSTOM_COMPONENT / 'services.py').read_text(encoding='utf-8')
    init_source = (CUSTOM_COMPONENT / '__init__.py').read_text(encoding='utf-8')

    for service in (
        'rename_system',
        'refresh_weather_plan',
        'delete_storm_alert',
        'set_third_party_mqtt_config',
        'query_third_party_mqtt_config',
        'send_ble_command',
    ):
        assert service in services
        assert f'SERVICE_{service.upper()}: Final = "{service}"' in const_source

    assert set(services['rename_system']['fields']) == {'system_id', 'new_name'}
    assert set(services['refresh_weather_plan']['fields']) == {'device_id'}
    assert set(services['delete_storm_alert']['fields']) == {'device_id', 'alert_id'}
    assert set(services['set_third_party_mqtt_config']['fields']) == {
        'device_id',
        'enable',
        'ip',
        'port',
        'username',
        'password',
        'token',
    }
    assert set(services['query_third_party_mqtt_config']['fields']) == {'device_id'}
    assert set(services['send_ble_command']['fields']) == {
        'device_id',
        'cmd',
        'body',
        'flags',
        'wait_for_ack',
        'ack_timeout',
    }
    assert 'repair_statistics' not in services

    assert 'SERVICE_NUMERIC_ID_PATTERN: Final = r"^\\s*[0-9]+\\s*$"' in const_source
    # Schemas live in services.py alongside the handlers that consume them.
    assert 'SERVICE_FIELD_SYSTEM_ID): vol.All(' in services_source
    assert 'SERVICE_FIELD_DEVICE_ID): vol.All(' in services_source
    assert 'SERVICE_FIELD_CMD): vol.All(' in services_source
    assert 'SERVICE_FIELD_BODY): vol.Any(dict, cv.string)' in services_source
    assert 'SERVICE_FIELD_FLAGS, default=0' in services_source
    assert 'vol.Any(' in services_source
    assert 'ServiceResponse' not in services_source
    assert 'SupportsResponse' not in services_source
    assert 'async def _async_handle_repair_statistics' not in services_source
    # System rename keeps the strict numeric-id contract; device-id schemas
    # accept HA device-registry IDs (UUID-style) too, so the device selector
    # in services.yaml can hand them through unchanged.
    assert 'cv.string, vol.Match(SERVICE_NUMERIC_ID_PATTERN)' in services_source
    assert 'SERVICE_FIELD_ALERT_ID): vol.All(' in services_source
    assert 'SERVICE_NON_EMPTY_TEXT_PATTERN' in services_source
    assert 'str.strip' not in services_source
    assert 'str.strip' not in init_source


def test_user_visible_action_errors_have_translations() -> None:
    """Service/entity action error keys must be present in every translation file."""
    required_exception_keys = {
        'delete_storm_alert_failed',
        'dynamic_tariff_unavailable',
        'entity_action_failed',
        'invalid_number_allowed_values',
        'invalid_number_range',
        'invalid_select_option',
        'invalid_text_value',
        'missing_system_id',
        'mqtt_command_failed',
        'mqtt_missing_device_sn',
        'mqtt_missing_subdevice_sn',
        'query_third_party_mqtt_config_failed',
        'refresh_weather_plan_failed',
        'rename_system_failed',
        'send_ble_command_failed',
        'set_third_party_mqtt_config_failed',
    }
    required_service_keys = {
        'delete_storm_alert',
        'query_third_party_mqtt_config',
        'refresh_weather_plan',
        'rename_system',
        'send_ble_command',
        'set_third_party_mqtt_config',
    }

    for path in (
        CUSTOM_COMPONENT / 'strings.json',
        *sorted((CUSTOM_COMPONENT / 'translations').glob('*.json')),
    ):
        data = json.loads(path.read_text(encoding='utf-8'))
        assert required_exception_keys <= set(data['exceptions']), path
        assert required_service_keys <= set(data['services']), path


def test_refresh_auth_errors_trigger_reauth_not_update_failed() -> None:
    """Rejected credentials during refresh should start HA reauth instead of log-spamming."""
    coordinator_source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(
        encoding='utf-8'
    )
    assert 'ConfigEntryAuthFailed' in coordinator_source
    assert (
        'Jackery credentials were rejected during property refresh'
        in coordinator_source
    )
    assert (
        'Jackery credentials were rejected while fetching extended device data'
        in coordinator_source
    )
    assert (
        'Jackery credentials were rejected while fetching system data'
        in coordinator_source
    )
    assert 'Auth revoked (likely another session logged in)' not in coordinator_source


def test_reauth_flow_handles_missing_entry_without_assertion() -> None:
    """Malformed reauth contexts should abort cleanly instead of raising AssertionError."""
    config_flow_source = (CUSTOM_COMPONENT / 'config_flow.py').read_text(
        encoding='utf-8'
    )
    config_flow_tree = ast.parse(config_flow_source)
    translation_sources = _translation_sources()

    # Reauth uses HA's _get_reauth_entry() helper (HA 2024.6+) wrapped in a
    # try/except for (KeyError, RuntimeError) to abort cleanly when the
    # entry has gone away while the reauth flow was sitting on screen.
    assert 'self._get_reauth_entry()' in config_flow_source
    assert _reauth_entry_lookup_is_guarded(config_flow_tree)
    assert 'FLOW_ABORT_REAUTH_ENTRY_MISSING' in config_flow_source
    assert 'assert self._reauth_entry is not None' not in config_flow_source
    for path, source in translation_sources.items():
        assert 'reauth_entry_missing' in source, path


def test_data_quality_warnings_are_normalized_and_formatted_for_repairs() -> None:
    """Implement test data quality warnings are normalized and formatted for repairs."""
    warning_a = util.AppDataQualityWarning(
        level='warning',
        reason='year_less_than_week',
        metric_key='device_ongrid_output_energy',
        label='Device grid-side output energy',
        source_section='device_home_stat_year',
        source_value=30.28,
        reference_section='device_home_stat_week',
        reference_value=89.08,
    ).as_dict()
    warning_b = dict(warning_a)
    warning_c = util.AppDataQualityWarning(
        level='warning',
        reason='lifetime_less_than_year',
        metric_key='pv_energy',
        label='PV energy',
        source_section='statistic',
        source_value=41.31,
        reference_section='device_pv_stat_year',
        reference_value=126.97,
    ).as_dict()

    normalized = util.normalized_data_quality_warnings([
        warning_b,
        warning_c,
        warning_a,
    ])

    assert normalized == [warning_c, warning_a]
    assert util.format_data_quality_warning(normalized[0]) == (
        'PV energy: statistic=41.31 < device_pv_stat_year=126.97'
    )
    assert util.format_data_quality_warning(normalized[1]) == (
        'Device grid-side output energy: device_home_stat_year=30.28 '
        '< device_home_stat_week=89.08'
    )


def test_data_quality_diagnostics_include_request_context_keys() -> None:
    """Implement test data quality diagnostics include request context keys."""
    const_source = (CUSTOM_COMPONENT / 'const.py').read_text(encoding='utf-8')
    util_source = (CUSTOM_COMPONENT / 'util.py').read_text(encoding='utf-8')

    for key in (
        'DATA_QUALITY_KEY_SOURCE_REQUEST',
        'DATA_QUALITY_KEY_REFERENCE_REQUEST',
        'DATA_QUALITY_KEY_SOURCE_CHART_SERIES_KEY',
        'DATA_QUALITY_KEY_REFERENCE_CHART_SERIES_KEY',
        'DATA_QUALITY_KEY_TOTAL_METHOD',
    ):
        assert key in const_source
        assert key in util_source
    assert 'def _format_request_range' in util_source
    assert 'source_request=_request_for_section(source_section)' in util_source
    assert 'reference_request=_request_for_section(reference_section)' in util_source


def test_runtime_code_does_not_use_assert_for_auth_or_reauth_guards() -> None:
    """Runtime guard paths should raise HA/domain errors, not AssertionError."""
    api_source = API_IMPLEMENTATION.read_text(encoding='utf-8')
    config_flow_source = (CUSTOM_COMPONENT / 'config_flow.py').read_text(
        encoding='utf-8'
    )

    assert 'assert self._token is not None' not in api_source
    assert (
        'raise JackeryAuthError("Login succeeded without returning a token")'
        in api_source
    )
    assert 'assert self._reauth_entry is not None' not in config_flow_source


def test_system_discovery_auth_errors_trigger_reauth() -> None:
    """Auth failures in initial rediscovery are reauth problems, not generic UpdateFailed."""
    coordinator_source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(
        encoding='utf-8'
    )
    discover_block = coordinator_source.split('async def async_discover', 1)[1].split(
        'def _is_property_device_candidate', 1
    )[0]

    assert 'except JackeryAuthError as err' in discover_block
    assert 'Jackery credentials were rejected during system discovery' in discover_block
    assert 'Jackery credentials were rejected during legacy device discovery' in (
        discover_block
    )
    assert 'raise ConfigEntryAuthFailed' in discover_block


def test_system_discovery_does_not_keep_unpublished_manual_id_paths() -> None:
    """Unreleased manual device/system config paths are migration ballast."""
    coordinator_source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(
        encoding='utf-8'
    )
    init_source = (CUSTOM_COMPONENT / '__init__.py').read_text(encoding='utf-8')

    assert 'manual deviceId' not in coordinator_source
    assert 'Configured device_id=' not in coordinator_source
    assert 'self.entry.data.get(CONF_DEVICE_ID)' not in coordinator_source
    assert 'CONF_SYSTEM_ID' not in coordinator_source
    assert 'CONF_DEVICE_ID' not in coordinator_source
    assert 'CONF_SCAN_INTERVAL' not in init_source


def test_optional_number_setter_failures_are_logged_before_suppression() -> None:
    """Optional number setters may suppress cloud errors, but not silently."""
    number_source = (CUSTOM_COMPONENT / 'number.py').read_text(encoding='utf-8')
    assert 'Ignoring optional Jackery number setter failure' in number_source
    assert 'self.entity_description.raise_on_setter_error' in number_source


def test_number_setter_rejects_non_finite_values_before_transform() -> None:
    """Number service writes must not let NaN/Infinity reach int(round(...))."""
    number_source = (CUSTOM_COMPONENT / 'number.py').read_text(encoding='utf-8')
    block = number_source.split('async def async_set_native_value', 1)[1].split(
        '\n\n# ---------------------------------------------------------------------------\n'
        '# Setup',
        1,
    )[0]

    assert 'parsed_value = safe_float(value)' in block
    assert 'if parsed_value is None:' in block
    assert 'self._raise_action_error(\n                "invalid_number_range"' in block
    assert 'value = parsed_value' in block
    assert block.index('value = parsed_value') < block.index(
        'self.entity_description.value_transform(value)'
    )
    assert 'value_transform: Callable[[float], Any] = _rounded_int' in number_source
    assert 'value_transform: Callable[[float], Any] = lambda v: int(round(v))' not in (
        number_source
    )


def test_number_setter_helpers_use_shared_integer_parser() -> None:
    """Number setter callbacks must not raw-cast values before coordinator calls."""
    number_source = (CUSTOM_COMPONENT / 'number.py').read_text(encoding='utf-8')
    setter_block = number_source.split('# Setter helpers', 1)[1].split(
        '# Dynamic-value helpers',
        1,
    )[0]

    assert 'def _wire_int(value: Any) -> int:' in setter_block
    assert 'parsed = first_nonblank_int(value)' in setter_block
    assert 'raise HomeAssistantError("invalid number value")' in setter_block
    assert 'discharge_limit=_wire_int(value)' in setter_block
    assert 'parsed = _wire_int(value)' in setter_block
    assert 'async_set_max_output_power(dev_id, _wire_int(value))' in setter_block
    assert 'async_set_default_power(dev_id, _wire_int(value))' in setter_block
    assert 'discharge_limit=int(value)' not in setter_block
    assert 'if int(value)' not in setter_block
    assert 'async_set_max_output_power(dev_id, int(value))' not in setter_block
    assert 'async_set_default_power(dev_id, int(value))' not in setter_block


def test_number_float_transform_uses_shared_parser() -> None:
    """Float number writes should use the same finite-value parser as HA values."""
    number_source = (CUSTOM_COMPONENT / 'number.py').read_text(encoding='utf-8')

    assert 'def _wire_float(value: Any) -> float:' in number_source
    assert 'value_transform=_wire_float' in number_source
    assert 'value_transform=lambda v: float(v)' not in number_source


def test_number_allowed_value_checks_use_shared_rounding_helper() -> None:
    """Discrete number validation should centralize round/int conversion."""
    number_source = (CUSTOM_COMPONENT / 'number.py').read_text(encoding='utf-8')
    block = number_source.split('async def async_set_native_value', 1)[1].split(
        '\n\n# ---------------------------------------------------------------------------\n'
        '# Setup',
        1,
    )[0]

    assert 'def _rounded_int(value: Any) -> int:' in number_source
    assert 'parsed = safe_float(value)' in number_source
    assert 'if allowed and _rounded_int(value) not in' in block
    assert 'str(_rounded_int(v)) for v in allowed' in block
    assert 'int(round(value))' not in block
    assert 'int(round(v))' not in block


def test_number_setter_validates_all_entity_ranges() -> None:
    """Direct number writes must enforce min/max for every number entity."""
    number_source = (CUSTOM_COMPONENT / 'number.py').read_text(encoding='utf-8')
    block = number_source.split('async def async_set_native_value', 1)[1].split(
        '\n\n# ---------------------------------------------------------------------------\n'
        '# Setup',
        1,
    )[0]

    assert 'validate_range' not in number_source
    assert 'if value < self.native_min_value or value > self.native_max_value:' in block
    assert '"invalid_number_range"' in block
    assert block.index(
        'if value < self.native_min_value or value > self.native_max_value:'
    ) < block.index('allowed = self._allowed_values()')


def test_max_feed_grid_is_not_aliased_to_grid_standard_limit() -> None:
    """MaxGridStdPw is a fallback/readout, not the maxFeedGrid setting."""
    const_source = (CUSTOM_COMPONENT / 'const.py').read_text(encoding='utf-8')
    alias_block = const_source.split('MAIN_PROPERTY_ALIAS_PAIRS', 1)[1].split(
        'TASK_PLAN_BODY',
        1,
    )[0]

    assert '(FIELD_MAX_FEED_GRID, FIELD_MAX_GRID_STD_PW)' not in alias_block


def test_property_setters_keep_local_override_during_stale_refresh_window() -> None:
    """Fresh local writes should beat stale HTTP/MQTT snapshots briefly."""
    coordinator_source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(
        encoding='utf-8'
    )

    assert '_PROPERTY_OVERRIDE_TTL_SEC' in coordinator_source
    assert 'self._property_overrides' in coordinator_source
    assert 'def _merge_main_properties_for_device(' in coordinator_source
    assert 'return self._merge_main_properties(merged, overrides)' in coordinator_source


def test_config_flow_normalizes_account_and_uses_flow_constants() -> None:
    """Avoid duplicate entries caused by whitespace/case drift in usernames."""
    const_source = (CUSTOM_COMPONENT / 'const.py').read_text(encoding='utf-8')
    config_flow_source = (CUSTOM_COMPONENT / 'config_flow.py').read_text(
        encoding='utf-8'
    )

    assert 'FLOW_STEP_USER: Final = "user"' in const_source
    assert 'FLOW_ERROR_INVALID_AUTH: Final = "invalid_auth"' in const_source
    assert 'def _normalize_account(value: Any) -> str:' in config_flow_source
    assert 'return value.strip() if isinstance(value, str) else' in config_flow_source
    assert 'def _entry_text(entry: ConfigEntry, key: str) -> str:' in config_flow_source
    assert 'return value if isinstance(value, str) else' in config_flow_source
    assert (
        'account = _normalize_account(user_input.get(CONF_USERNAME))'
        in config_flow_source
    )
    assert 'await self.async_set_unique_id(account.lower())' in config_flow_source
    assert 'CONF_USERNAME: account' in config_flow_source
    assert (
        'vol.Required(CONF_USERNAME): vol.All(str, vol.Length(min=1))'
        in config_flow_source
    )
    assert 'FLOW_ERROR_ACCOUNT_REQUIRED' in config_flow_source
    assert 'errors[CONF_USERNAME] = FLOW_ERROR_ACCOUNT_REQUIRED' in config_flow_source
    assert (
        'vol.Required(CONF_PASSWORD): vol.All(str, vol.Length(min=1))'
        in config_flow_source
    )
    assert (
        'str.strip'
        not in config_flow_source.split('USER_SCHEMA =', 1)[1].split(
            'class JackeryConfigFlow', 1
        )[0]
    )
    assert 'str(entry.data.get(CONF_USERNAME' not in config_flow_source
    assert 'entry.data[CONF_USERNAME]' not in config_flow_source
    assert 'stored_username = _entry_text(entry, CONF_USERNAME)' in config_flow_source
    assert '"username": stored_username' in config_flow_source


def test_redact_keys_cover_mqtt_credential_aliases() -> None:
    """Diagnostics redaction must cover raw and normalized MQTT credential keys."""
    const_source = (CUSTOM_COMPONENT / 'const.py').read_text(encoding='utf-8')
    redact_block = const_source.split('REDACT_KEYS: Final =', 1)[1].split(')', 1)[0]

    for key_name in (
        'FIELD_MQTT_PASSWORD',
        'FIELD_USER_ID',
        'MQTT_CREDENTIAL_CLIENT_ID',
        'MQTT_CREDENTIAL_PASSWORD',
        'MQTT_CREDENTIAL_USER_ID',
        'MQTT_CREDENTIAL_USERNAME',
    ):
        assert key_name in redact_block


def test_diagnostics_do_not_expose_raw_mqtt_topic_user_ids() -> None:
    """MQTT topics contain the Jackery userId and must be redacted in diagnostics."""
    const_source = (CUSTOM_COMPONENT / 'const.py').read_text(encoding='utf-8')
    mqtt_source = MQTT_IMPLEMENTATION.read_text(encoding='utf-8')

    assert 'REDACTED_VALUE: Final = "**REDACTED**"' in const_source
    assert 'def _redact_topic(topic: str | None) -> str | None:' in mqtt_source
    assert 'parts[2] = REDACTED_VALUE' in mqtt_source
    assert (
        'def diagnostics_snapshot(self, *, redact_topics: bool = True)' in mqtt_source
    )
    assert 'return self._redact_topic(topic) if redact_topics else topic' in mqtt_source
    assert '"topics": [topic_value(topic) for topic in self._topics]' in mqtt_source
    assert (
        '"last_published_topic": topic_value(self._last_published_topic)' in mqtt_source
    )
    assert 'return self.diagnostics_snapshot()' in mqtt_source
    assert '"topic_count": len(self._topics)' in mqtt_source
    assert '"topics": list(self._topics)' not in mqtt_source
    assert '"last_published_topic": self._last_published_topic' not in mqtt_source


def test_mqtt_diagnostics_track_dropped_messages_and_timestamps() -> None:
    """Diagnostics need actionable MQTT health data without exposing credentials."""
    mqtt_source = MQTT_IMPLEMENTATION.read_text(encoding='utf-8')

    for fragment in (
        'self._messages_dropped = 0',
        'self._last_message_error: str | None = None',
        'self._last_connect_at: str | None = None',
        'self._last_disconnect_at: str | None = None',
        'self._last_message_at: str | None = None',
        'self._last_publish_at: str | None = None',
        '"messages_dropped": self._messages_dropped',
        '"last_message_error": self._last_message_error',
        '"last_connect_at": self._last_connect_at',
        '"last_disconnect_at": self._last_disconnect_at',
        '"last_message_at": self._last_message_at',
        '"last_publish_at": self._last_publish_at',
    ):
        assert fragment in mqtt_source

    assert 'invalid JSON payload' in mqtt_source
    assert 'non-object JSON payload' in mqtt_source


def test_mqtt_password_base64_validation_is_strict_and_redaction_constant_is_reused() -> (
    None
):
    """Reject malformed MQTT seeds and redact login diagnostics at export time."""
    api_source = API_IMPLEMENTATION.read_text(encoding='utf-8')

    assert 'base64.b64decode(self._mqtt_seed_b64, validate=True)' in api_source
    assert 'self.last_login_response = dict(data)' in api_source
    assert 'response=dict(data)' in api_source
    assert 'redacted[FIELD_TOKEN] = REDACTED_VALUE' not in api_source
    assert 'inner[FIELD_MQTT_PASSWORD] = REDACTED_VALUE' not in api_source
    assert '"**REDACTED**"' not in api_source


def test_api_trend_endpoints_use_shared_period_range_contract() -> None:
    """System trend helpers must not fall back to today..today for month/year."""
    api_source = API_IMPLEMENTATION.read_text(encoding='utf-8')

    assert 'app_period_date_bounds,' in api_source
    assert 'date.today().isoformat()' not in api_source
    for function_name in (
        'async_get_pv_trends',
        'async_get_home_trends',
        'async_get_battery_trends',
        '_async_get_device_period_stat',
    ):
        block = api_source.split(f"async def {function_name}", 1)[1]
        if function_name != '_async_get_device_period_stat':
            next_marker = 'async def '
            block = block.split(next_marker, 1)[0]
        else:
            block = block.split('async def async_get_device_pv_stat', 1)[0]
        assert 'app_period_date_bounds(' in block
        assert 'APP_REQUEST_BEGIN_DATE: str(begin_date)' in block
        assert 'APP_REQUEST_END_DATE: str(end_date)' in block


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
        walk_statement_lists(path, ast.parse(path.read_text(encoding='utf-8')))


def test_config_entry_bool_option_calls_use_config_key_and_default() -> None:
    """Optional entity cleanup must pass option key plus fallback default."""
    source = (CUSTOM_COMPONENT / '__init__.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == 'config_entry_bool_option'
    ]
    assert calls
    for call in calls:
        assert len(call.args) == 3, (
            f"config_entry_bool_option call at line {call.lineno} must pass entry, key, default"
        )

    assert 'CONF_CREATE_SMART_METER_DERIVED_SENSORS' in source
    assert 'CONF_CREATE_CALCULATED_POWER_SENSORS' in source
    assert 'DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS' in source
    assert 'DEFAULT_CREATE_CALCULATED_POWER_SENSORS' in source
    assert 'entry,\n            DEFAULT_CREATE_' not in source


def test_all_api_json_decode_paths_catch_value_error() -> None:
    """aiohttp/json stacks may raise ValueError for malformed JSON payloads.

    Each ``resp.json(...)`` call site must catch the relevant decode/format
    exceptions: ``aiohttp.ContentTypeError``, ``json.JSONDecodeError``,
    ``UnicodeDecodeError`` and ``ValueError``. The test accepts both the
    required parenthesized ``as err`` handler and Python 3.14's unparenthesized
    no-``as`` multi-exception headers.
    """
    api_source = API_IMPLEMENTATION.read_text(encoding='utf-8')
    decode_blocks: list[str] = []
    for match in re.finditer(
        r'except[^:\n]*(?:\n\s*[^:\n]*)*ContentTypeError', api_source
    ):
        block = api_source[match.start() : api_source.find(':', match.start()) + 1]
        decode_blocks.append(re.sub(r'\s+', ' ', block))
    assert len(decode_blocks) == 4, decode_blocks
    for block in decode_blocks:
        assert 'json.JSONDecodeError' in block, block
        assert 'UnicodeDecodeError' in block, block
        assert 'ValueError' in block, block


def test_login_invalid_json_is_reported_as_api_error_not_raw_exception() -> None:
    """Login should surface malformed cloud responses as JackeryApiError."""
    api_source = API_IMPLEMENTATION.read_text(encoding='utf-8')
    login_block = api_source.split('async def async_login', 1)[1].split(
        'async def async_get_mqtt_credentials', 1
    )[0]
    assert 'Login returned invalid JSON' in login_block
    assert 'HTTP_RAW_TEXT_LIMIT' in login_block
    assert 'raise JackeryApiError(' in login_block


def test_api_read_endpoints_normalize_unexpected_payload_shapes() -> None:
    """Dict/list API readers should not leak arbitrary data shapes to coordinator."""
    api_source = API_IMPLEMENTATION.read_text(encoding='utf-8')

    assert 'def _payload_dict(data: dict[str, Any], path: str)' in api_source
    assert 'def _payload_list(data: dict[str, Any], path: str)' in api_source
    assert 'returned unexpected data shape for dict payload' in api_source
    assert 'returned unexpected data shape for list payload' in api_source

    expected_fragments = (
        'return self._payload_dict(data, DEVICE_PROPERTY_PATH)',
        'return self._payload_dict(data, SYSTEM_STATISTIC_PATH)',
        'payload = self._payload_dict(data, PV_TRENDS_PATH)',
        'payload = self._payload_dict(data, HOME_TRENDS_PATH)',
        'payload = self._payload_dict(data, BATTERY_TRENDS_PATH)',
        'return self._payload_dict(data, POWER_PRICE_PATH)',
        'return self._payload_dict(data, PRICE_HISTORY_CONFIG_PATH)',
        'return self._payload_dict(data, DEVICE_STATISTIC_PATH)',
        'payload = self._payload_dict(data, path)',
        'return self._payload_dict(data, DEVICE_METER_STAT_PATH)',
        'return self._payload_dict(data, LOCATION_PATH)',
        'systems = self._payload_list(data, SYSTEM_LIST_PATH)',
        'return self._payload_list(data, PRICE_SOURCE_LIST_PATH)',
        'items = self._payload_list(data, OTA_LIST_PATH)',
        'return self._payload_list(data, DEVICE_LIST_PATH)',
    )
    for fragment in expected_fragments:
        assert fragment in api_source

    assert 'return data.get(FIELD_DATA) or {}' not in api_source


def test_ota_info_accepts_single_dict_payload_shapes() -> None:
    """OTA responses may be a list, a single dict, or a dict body wrapper."""
    api_source = API_IMPLEMENTATION.read_text(encoding='utf-8')
    block = api_source.split('async def async_get_ota_info', 1)[1].split(
        'async def async_get_location', 1
    )[0]

    assert 'items = self._payload_list(data, OTA_LIST_PATH)' in block
    assert 'raw = data.get(FIELD_DATA)' in block
    assert 'raw_body = raw.get(FIELD_BODY)' in block
    for field in (
        'FIELD_CURRENT_VERSION',
        'FIELD_VERSION',
        'FIELD_TARGET_VERSION',
        'FIELD_TARGET_MODULE_VERSION',
        'FIELD_UPDATE_STATUS',
        'FIELD_UPDATE_CONTENT',
        'FIELD_IS_FIRMWARE_UPGRADE',
        'FIELD_UPGRADE_TYPE',
    ):
        assert field in block


def test_ota_info_selects_requested_device_from_multi_item_response() -> None:
    """OTA list responses must not use the main-device item for a battery pack."""
    api_source = API_IMPLEMENTATION.read_text(encoding='utf-8')

    assert 'def _select_ota_item(' in api_source
    selector = api_source.split('def _select_ota_item(', 1)[1].split(
        '# --- generic GET with auto re-login', 1
    )[0]
    ota_block = api_source.split('async def async_get_ota_info', 1)[1].split(
        'async def async_get_location', 1
    )[0]

    assert 'requested_sn = str(device_sn)' in selector
    assert 'item.get(FIELD_DEVICE_SN)' in selector
    assert 'return item' in selector
    assert 'return items[0] if items else {}' in selector
    assert 'return self._select_ota_item(items, device_sn)' in ota_block
    assert 'return items[0]' not in ota_block


def test_battery_pack_single_object_detection_accepts_firmware_only_payloads() -> None:
    """Pack list fallback must keep BatteryPackSub firmware-only payloads."""
    api_source = API_IMPLEMENTATION.read_text(encoding='utf-8')
    block = api_source.split('async def async_get_battery_pack_list', 1)[1].split(
        'async def async_get_home_trends', 1
    )[0]

    for field in (
        'FIELD_VERSION',
        'FIELD_CURRENT_VERSION',
        'FIELD_IS_FIRMWARE_UPGRADE',
        'FIELD_UPDATE_STATUS',
    ):
        assert field in block


def test_api_payload_helper_paths_match_called_endpoint_constants() -> None:
    """Wrong helper path constants hide the real failing endpoint in diagnostics."""
    api_source = API_IMPLEMENTATION.read_text(encoding='utf-8')

    pv_block = api_source.split('async def async_get_pv_trends', 1)[1].split(
        'async def async_get_power_price', 1
    )[0]
    price_sources_block = api_source.split('async def async_get_price_sources', 1)[
        1
    ].split('async def async_get_price_history_config', 1)[0]
    assert 'payload = self._payload_dict(data, PV_TRENDS_PATH)' in pv_block
    assert 'HOME_TRENDS_PATH' not in pv_block
    assert (
        'return self._payload_list(data, PRICE_SOURCE_LIST_PATH)' in price_sources_block
    )
    assert 'DEVICE_LIST_PATH' not in price_sources_block


def test_home_assistant_ui_schemas_do_not_use_nonserializable_strip_callable() -> None:
    """HA voluptuous_serialize cannot convert custom callables in UI schemas."""
    config_flow_source = (CUSTOM_COMPONENT / 'config_flow.py').read_text(
        encoding='utf-8'
    )
    services_source = (CUSTOM_COMPONENT / 'services.py').read_text(encoding='utf-8')

    user_schema = config_flow_source.split('USER_SCHEMA =', 1)[1].split(
        'class JackeryConfigFlow', 1
    )[0]
    reauth_schema = config_flow_source.split('data_schema=vol.Schema', 1)[1].split(
        'description_placeholders', 1
    )[0]
    service_schema_block = services_source.split('RENAME_SCHEMA =', 1)[1].split(
        'def _loaded_coordinators', 1
    )[0]

    assert 'str.strip' not in user_schema
    assert 'str.strip' not in reauth_schema
    assert 'str.strip' not in service_schema_block
    assert '_coerce_service_int' not in service_schema_block
    assert '_coerce_service_float' not in service_schema_block
    assert 'SERVICE_NON_EMPTY_TEXT_PATTERN' in service_schema_block


def test_coordinator_imports_all_field_constants_it_references() -> None:
    """Catch runtime NameError regressions from missing FIELD_* imports."""
    source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    imported_from_const: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and node.module == 'const':
            imported_from_const.update(alias.name for alias in node.names)

    referenced_fields = {
        node.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Name) and node.id.startswith('FIELD_')
    }
    missing = sorted(referenced_fields - imported_from_const)
    assert not missing, f"Missing .const imports in coordinator.py: {missing}"


def test_coordinator_lazy_imports_mqtt_client_for_collection_without_aiomqtt() -> None:
    """HA config-flow collection and event loop must not import optional MQTT deps."""
    source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(encoding='utf-8')
    tree = ast.parse(source)

    module_mqtt_imports = [
        node
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        and node.module == 'mqtt_push'
        and any(alias.name == 'JackeryMqttPushClient' for alias in node.names)
    ]
    assert module_mqtt_imports == []
    assert 'if TYPE_CHECKING:' in source
    assert 'self._mqtt: JackeryMqttPushClient | None = None' in source
    assert 'def _load_mqtt_push_client() -> type[Any]:' in source
    assert 'importlib.import_module(".mqtt_push", __package__)' in source

    start_mqtt = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == 'async_start_mqtt'
    )
    event_loop_imports = [
        node
        for node in ast.walk(start_mqtt)
        if isinstance(node, ast.ImportFrom)
        and node.module == 'mqtt_push'
        and any(alias.name == 'JackeryMqttPushClient' for alias in node.names)
    ]
    assert event_loop_imports == []
    assert 'await self.hass.async_add_executor_job(' in source
    assert '_load_mqtt_push_client' in source
    assert 'except ModuleNotFoundError as err:' in source
    assert 'err.name != "aiomqtt"' in source
    assert 'Jackery MQTT push is unavailable because aiomqtt is not installed' in (
        source
    )


def test_service_numeric_ids_are_schema_serializable_but_trimmed_by_handlers() -> None:
    """Service IDs may include whitespace in UI/API input, then handlers strip them."""
    const_source = (CUSTOM_COMPONENT / 'const.py').read_text(encoding='utf-8')
    services_source = (CUSTOM_COMPONENT / 'services.py').read_text(encoding='utf-8')

    assert 'SERVICE_NUMERIC_ID_PATTERN: Final = r"^\\s*[0-9]+\\s*$"' in const_source
    # System rename keeps the strict numeric-id pattern; device-id handlers
    # tolerate HA device-registry UUIDs that the device selector emits.
    assert 'vol.Match(SERVICE_NUMERIC_ID_PATTERN)' in services_source
    assert 'str.strip' not in services_source


def test_rename_service_system_id_validates_direct_call_values() -> None:
    """Rename system_id constraints must not rely only on HA schema validation."""
    services_source = (CUSTOM_COMPONENT / 'services.py').read_text(encoding='utf-8')

    assert 'def _rename_system_id_from_service(' in services_source
    helper_block = services_source.split('def _rename_system_id_from_service(', 1)[
        1
    ].split('\n\n\ndef _rename_name_from_service', 1)[0]
    assert 'isinstance(raw, str)' in helper_block
    assert 'system_id = raw.strip()' in helper_block
    assert 'system_id.isascii() and system_id.isdecimal()' in helper_block
    assert 'rename_system_failed' in helper_block

    assert 'system_id = call.data[SERVICE_FIELD_SYSTEM_ID].strip()' not in (
        services_source
    )
    assert 'system_id = _rename_system_id_from_service(' in services_source


def test_device_id_services_validate_direct_call_values() -> None:
    """Device-id service constraints must not rely only on HA schema validation."""
    services_source = (CUSTOM_COMPONENT / 'services.py').read_text(encoding='utf-8')
    service_schema_block = services_source.split('REFRESH_WEATHER_PLAN_SCHEMA =', 1)[
        1
    ].split(
        '# ---------------------------------------------------------------------------',
        1,
    )[0]

    assert 'def _device_id_from_service(' in services_source
    helper_block = services_source.split('def _device_id_from_service(', 1)[1].split(
        '\n\n\ndef _rename_name_from_service', 1
    )[0]
    assert 'isinstance(raw, str)' in helper_block
    assert 'device_id = raw.strip()' in helper_block
    assert '_resolve_jackery_device_id(hass, device_id)' in helper_block
    assert 'extra_placeholders=extra_placeholders' in helper_block

    assert 'call.data[SERVICE_FIELD_DEVICE_ID].strip()' not in services_source
    assert services_source.count('_device_id_from_service(') >= 6
    assert 'vol.Length(min=1)' not in service_schema_block
    assert service_schema_block.count('vol.Match(SERVICE_NON_EMPTY_TEXT_PATTERN)') >= 7


def test_rename_service_name_validates_direct_call_values() -> None:
    """Rename service name constraints must not rely only on HA schema validation."""
    services_source = (CUSTOM_COMPONENT / 'services.py').read_text(encoding='utf-8')

    assert 'def _rename_name_from_service(' in services_source
    helper_block = services_source.split('def _rename_name_from_service(', 1)[1].split(
        '\n\n\ndef _ble_body_from_service', 1
    )[0]
    assert 'not isinstance(raw, str)' in helper_block
    assert 'parsed = raw.strip()' in helper_block
    assert 'if not parsed:' in helper_block
    assert 'len(parsed) > 64' in helper_block

    assert 'new_name = call.data[SERVICE_FIELD_NEW_NAME].strip()' not in services_source
    assert 'new_name = _rename_name_from_service(' in services_source


def test_delete_storm_alert_validates_direct_alert_id() -> None:
    """Delete service alert_id constraints must not rely only on HA schema validation."""
    services_source = (CUSTOM_COMPONENT / 'services.py').read_text(encoding='utf-8')

    assert 'def _storm_alert_id_from_service(' in services_source
    helper_block = services_source.split('def _storm_alert_id_from_service(', 1)[
        1
    ].split('\n\n\ndef _ble_body_from_service', 1)[0]
    assert 'isinstance(raw, str)' in helper_block
    assert 'alert_id = raw.strip()' in helper_block
    assert 'delete_storm_alert_failed' in helper_block
    assert '"alert_id": alert_id' in helper_block

    assert 'alert_id = call.data[SERVICE_FIELD_ALERT_ID].strip()' not in services_source
    assert 'alert_id = _storm_alert_id_from_service(' in services_source


def test_service_boolean_fields_use_safe_bool_parser() -> None:
    """Service booleans must not regress to truthiness casts."""
    services_source = (CUSTOM_COMPONENT / 'services.py').read_text(encoding='utf-8')

    assert 'from .util import safe_bool' in services_source
    assert 'def _service_bool(' in services_source
    helper_block = services_source.split('def _service_bool(', 1)[1].split('\n\n# ', 1)[
        0
    ]
    assert 'parsed = safe_bool(raw)' in helper_block
    assert 'raise _service_validation_error(' in helper_block

    assert 'bool(call.data' not in services_source
    assert 'enable=_service_bool(' in services_source
    assert 'field_name=SERVICE_FIELD_ENABLE' in services_source
    assert 'wait_for_ack=_service_bool(' in services_source
    assert 'field_name=SERVICE_FIELD_WAIT_FOR_ACK' in services_source
    # _service_bool raises ServiceValidationError itself; handlers must preserve it
    # so the field-specific translated error does not get wrapped again.
    assert services_source.count('except ServiceValidationError:\n        raise') >= 2


def test_standby_switch_uses_strict_numeric_mode_parser() -> None:
    """Manual standby must parse enum mode 1/2 without raw int casts."""
    switch_source = (CUSTOM_COMPONENT / 'switch.py').read_text(encoding='utf-8')
    helper_block = switch_source.split('def _standby_is_on(', 1)[1].split(
        '\n\n@dataclass', 1
    )[0]

    assert 'first_nonblank_int' in switch_source
    assert 'parsed = first_nonblank_int(raw)' in helper_block
    assert 'return parsed == 1' in helper_block
    assert 'return safe_bool(raw)' in helper_block
    assert 'return int(raw)' not in helper_block
    assert '= int(raw)' not in helper_block


def test_service_optional_text_fields_do_not_stringify_none() -> None:
    """Optional service text fields must keep direct-call None values empty."""
    services_source = (CUSTOM_COMPONENT / 'services.py').read_text(encoding='utf-8')

    assert 'def _service_optional_text(' in services_source
    helper_block = services_source.split('def _service_optional_text(', 1)[1].split(
        '\n\n# ', 1
    )[0]
    assert 'return ""' in helper_block
    assert 'not isinstance(raw, str)' in helper_block
    assert 'parsed = raw' in helper_block
    assert 'len(parsed) > max_length' in helper_block
    assert 'return parsed' in helper_block

    assert 'str(raw)' not in helper_block
    assert 'str(call.data.get(SERVICE_FIELD_USERNAME' not in services_source
    assert 'str(call.data.get(SERVICE_FIELD_PASSWORD' not in services_source
    assert 'str(call.data.get(SERVICE_FIELD_TOKEN' not in services_source
    assert 'username=_service_optional_text(' in services_source
    assert 'password=_service_optional_text(' in services_source
    assert 'token=_service_optional_text(' in services_source


def test_service_required_text_fields_validate_direct_call_values() -> None:
    """Required service text fields must not bypass schema checks in direct calls."""
    services_source = (CUSTOM_COMPONENT / 'services.py').read_text(encoding='utf-8')

    assert 'def _service_required_text(' in services_source
    helper_block = services_source.split('def _service_required_text(', 1)[1].split(
        '\n\n\ndef _service_optional_text', 1
    )[0]
    assert 'not isinstance(raw, str)' in helper_block
    assert 'parsed = raw.strip()' in helper_block
    assert 'if not parsed:' in helper_block
    assert 'len(parsed) > max_length' in helper_block

    assert 'ip=str(call.data[SERVICE_FIELD_IP]).strip()' not in services_source
    assert 'ip=_service_required_text(' in services_source
    assert 'field_name=SERVICE_FIELD_IP' in services_source


def test_ble_service_body_validates_json_native_values() -> None:
    """BLE service body dicts must not reach json.dumps with altered semantics."""
    services_source = (CUSTOM_COMPONENT / 'services.py').read_text(encoding='utf-8')

    assert 'def _json_native_value(' in services_source
    assert 'def _json_native_body(' in services_source
    assert 'parse_constant=_reject_json_constant' in services_source
    assert 'math.isfinite(value)' in services_source
    assert 'body object keys must be strings' in services_source
    assert 'body must contain only JSON-compatible values' in services_source
    assert 'return dict(raw_body)' not in services_source
    assert 'return _json_native_body(raw_body, device_id)' in services_source


def test_service_numeric_fields_validate_direct_call_ranges() -> None:
    """Service numeric fields must not bypass schema ranges in direct calls."""
    services_source = (CUSTOM_COMPONENT / 'services.py').read_text(encoding='utf-8')

    assert 'def _coerce_service_int(' in services_source
    assert 'def _coerce_service_float(' in services_source
    assert 'def _service_int(' in services_source
    assert 'def _service_float(' in services_source
    assert 'parsed = _coerce_service_int(raw)' in services_source
    assert 'parsed = _coerce_service_float(raw)' in services_source
    assert 'except (TypeError, ValueError, OverflowError)' in services_source
    assert 'math.isfinite(parsed)' in services_source
    assert 'int(call.data' not in services_source
    assert 'float(call.data' not in services_source
    assert 'vol.Coerce(int), vol.Range(min=1, max=65535)' in services_source
    assert 'vol.Coerce(int), vol.Range(min=0, max=65535)' in services_source
    assert 'vol.Coerce(float), vol.Range(min=0.5, max=60.0)' in services_source

    assert 'port=_service_int(' in services_source
    assert 'field_name=SERVICE_FIELD_PORT' in services_source
    assert 'cmd=_service_int(' in services_source
    assert 'field_name=SERVICE_FIELD_CMD' in services_source
    assert 'flags=_service_int(' in services_source
    assert 'field_name=SERVICE_FIELD_FLAGS' in services_source
    assert 'ack_timeout_sec=_service_float(' in services_source
    assert 'field_name=SERVICE_FIELD_ACK_TIMEOUT' in services_source


def test_services_yaml_uses_device_selector_for_device_id_fields() -> None:
    """services.yaml must use HA's device selector for jackery_solarvault devices.

    The picker filters by ``integration: jackery_solarvault`` so users
    cannot pick a device from another integration. system_id stays a text
    selector because a Jackery system maps to multiple HA devices and the
    selector cannot represent that scope.
    """
    services = yaml.safe_load(
        (CUSTOM_COMPONENT / 'services.yaml').read_text(encoding='utf-8')
    )

    refresh_field = services['refresh_weather_plan']['fields']['device_id']
    delete_field = services['delete_storm_alert']['fields']['device_id']
    ble_field = services['send_ble_command']['fields']['device_id']
    rename_field = services['rename_system']['fields']['system_id']

    for field in (refresh_field, delete_field, ble_field):
        assert field.get('required') is True
        device_selector = (field.get('selector') or {}).get('device') or {}
        assert device_selector.get('integration') == 'jackery_solarvault', (
            'device_id field must filter the picker to this integration'
        )

    # System rename uses a text selector by design.
    assert 'text' in (rename_field.get('selector') or {})


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
    services_source = (CUSTOM_COMPONENT / 'services.py').read_text(encoding='utf-8')

    # Lookup helpers exist and are typed against the runtime coordinator.
    assert (
        'def _coordinator_for_device(\n'
        '    hass: HomeAssistant, device_id: str\n'
        ') -> JackerySolarVaultCoordinator | None:' in services_source
    )
    assert (
        'def _coordinator_for_system(\n'
        '    hass: HomeAssistant, system_id: str\n'
        ') -> JackerySolarVaultCoordinator | None:' in services_source
    )

    # System lookup walks the system payload section and matches FIELD_ID
    # or FIELD_SYSTEM_ID — both are needed because the cloud surfaces the
    # same id under either key depending on endpoint.
    system_block = services_source.split('def _coordinator_for_system', 1)[1].split(
        '\n\n# ', 1
    )[0]
    assert 'PAYLOAD_SYSTEM' in system_block
    assert 'FIELD_ID' in system_block
    assert 'FIELD_SYSTEM_ID' in system_block

    # Device lookup uses the coordinator.data dict membership check.
    device_block = services_source.split('def _coordinator_for_device', 1)[1].split(
        '\ndef _coordinator_for_system', 1
    )[0]
    assert 'device_id in (coordinator.data or {})' in device_block

    # Each handler resolves the coordinator before invoking the cloud call;
    # missing-entry paths must raise a translated ServiceValidationError.
    for handler in (
        '_async_handle_rename',
        '_async_handle_refresh_weather_plan',
        '_async_handle_delete_storm_alert',
    ):
        block = services_source.split(f"async def {handler}", 1)[1].split(
            '\n\nasync def ', 1
        )[0]
        assert 'coordinator is None' in block, handler
        assert 'raise ServiceValidationError(' in block, handler
        assert 'translation_domain=DOMAIN' in block, handler

    send_ble_block = services_source.split(
        'async def _async_handle_send_ble_command', 1
    )[1].split('\n\n# ', 1)[0]
    assert 'coordinator is None' in send_ble_block
    assert '_service_validation_error(\n            "send_ble_command_failed"' in (
        send_ble_block
    )
    assert 'coordinator.async_send_ble_command(' in send_ble_block


def test_services_resolves_ha_device_uuid_back_to_jackery_device_id() -> None:
    """The device selector hands the handler an HA device-registry UUID.

    Translate it back to the Jackery numeric id by reading the matching
    ``(DOMAIN, jackery_device_id)`` identifier off the DeviceEntry. Accessory
    device rows point at the parent SolarVault through ``via_device_id``.
    Legacy automations that still pass a raw Jackery numeric id must keep
    working too — the resolver returns the input unchanged if the
    device-registry miss tells us we're not looking at an HA UUID.
    """
    services_source = (CUSTOM_COMPONENT / 'services.py').read_text(encoding='utf-8')

    assert 'from homeassistant.helpers import' in services_source
    assert 'device_registry as dr' in services_source

    block = services_source.split('def _resolve_jackery_device_id', 1)[1].split(
        '\ndef _coordinator_for_device', 1
    )[0]
    assert 'registry = dr.async_get(hass)' in block
    assert 'device = registry.async_get(raw)' in block
    assert 'device.via_device_id' in block
    assert 'via_device = registry.async_get(device.via_device_id)' in block
    assert 'device.identifiers' in block
    assert 'DOMAIN' in block
    # Legacy fallback: when the registry has no matching DeviceEntry,
    # treat the input as a raw Jackery id.
    assert 'return raw' in block


def test_services_setup_is_idempotent_and_callback_typed() -> None:
    """async_setup_services must be a sync @callback and skip already-registered services."""
    services_source = (CUSTOM_COMPONENT / 'services.py').read_text(encoding='utf-8')
    setup_block = services_source.split('def async_setup_services', 1)[1]

    assert '@callback' in services_source.split('async_setup_services', 1)[0][-200:]
    # Re-entry safe: HA fires async_setup multiple times in some test setups,
    # so each registration is gated on has_service.
    for service_const in (
        'SERVICE_RENAME_SYSTEM',
        'SERVICE_REFRESH_WEATHER_PLAN',
        'SERVICE_DELETE_STORM_ALERT',
        'SERVICE_SEND_BLE_COMMAND',
    ):
        assert f"hass.services.has_service(DOMAIN, {service_const})" in setup_block, (
            service_const
        )
        assert (
            f"hass.services.async_register(\n            DOMAIN,\n            {service_const}"
            in setup_block
        ), service_const


def test_ble_first_setter_routing_is_scoped_to_positive_cmd_commands() -> None:
    """Only user-driven positive-cmd commands use BLE-first routing."""
    source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(encoding='utf-8')

    def _block(name: str) -> str:
        return source.split(f"async def {name}", 1)[1].split('\n    async def ', 1)[0]

    for name in (
        'async_set_eps',
        'async_set_soc_limits',
        'async_set_max_feed_grid',
        'async_set_max_output_power',
        'async_set_auto_standby_hours',
        'async_set_standby',
        'async_set_work_model',
        'async_set_off_grid_shutdown',
        'async_set_off_grid_time',
        'async_set_default_power',
        'async_set_follow_meter',
        'async_set_temp_unit',
        'async_set_third_party_mqtt_config',
        'async_query_third_party_mqtt_config',
        'async_set_smart_plug_switch',
        'async_set_smart_plug_priority',
        'async_set_ct_phase',
    ):
        block = _block(name)
        assert '_async_publish_command_ble_first(' in block, name

    for name in (
        'async_set_storm_warning',
        'async_set_storm_minutes',
        'async_delete_storm_alert',
    ):
        block = _block(name)
        assert '_async_publish_command_ble_first(' not in block, name
        assert '_async_publish_command(' in block, name


def test_command_transport_cmd_uses_shared_integer_parser() -> None:
    """MQTT/BLE command routing must not raw-cast transport cmd values."""
    source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(encoding='utf-8')
    body_block = source.split('def _command_body_for_transport', 1)[1].split(
        '\n    async def _async_publish_command_ble_first',
        1,
    )[0]
    ble_block = source.split('async def _async_publish_command_ble_first', 1)[1].split(
        '\n    async def _async_publish_command',
        1,
    )[0]

    assert 'def _transport_cmd(value: Any) -> int:' in source
    assert 'parsed = first_nonblank_int(value)' in source
    assert 'cmd_int = _transport_cmd(cmd)' in body_block
    assert 'cmd_int = _transport_cmd(cmd)' in ble_block
    assert 'body[FIELD_CMD] = cmd_int' in body_block
    assert 'cmd=cmd_int' in ble_block
    assert 'int(cmd)' not in body_block
    assert 'int(cmd)' not in ble_block


def test_soc_limit_setter_uses_safe_int_payload_fallbacks() -> None:
    """SOC writes must tolerate one corrupt cached limit while setting the other."""
    source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(encoding='utf-8')
    block = source.split('async def async_set_soc_limits', 1)[1].split(
        '\n    async def async_set_max_feed_grid',
        1,
    )[0]

    assert 'safe_int,' in source
    assert 'def _soc_limit(value: Any) -> int | None:' in block
    assert 'if parsed is None or parsed < 0 or parsed > 100:' in block
    assert 'def _current_soc_limit(primary: str, legacy: str, default: int)' in block
    assert 'for raw in (current.get(primary), current.get(legacy)):' in block
    assert 'parsed = _soc_limit(raw)' in block
    assert '_soc_limit(charge_limit)' in block
    assert '_soc_limit(discharge_limit)' in block
    assert 'raise UpdateFailed("Invalid SOC limit")' in block
    assert 'int(\n            charge_limit' not in block
    assert 'int(\n            discharge_limit' not in block


def test_numeric_control_setters_use_shared_safe_int_parser() -> None:
    """Public numeric control setters must not expose raw int-cast errors."""
    source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(encoding='utf-8')

    assert 'def _control_int(value: Any, field_name: str) -> int:' in source
    assert 'parsed = None if isinstance(value, bool) else safe_int(value)' in source
    assert 'raise UpdateFailed(f"Invalid {field_name}")' in source

    def _block(name: str) -> str:
        return source.split(f"async def {name}", 1)[1].split('\n    async def ', 1)[0]

    expected_fields = {
        'async_set_max_feed_grid': 'FIELD_MAX_FEED_GRID',
        'async_set_max_output_power': 'FIELD_MAX_OUT_PW',
        'async_set_auto_standby_hours': 'FIELD_IS_AUTO_STANDBY',
        'async_set_work_model': 'FIELD_WORK_MODEL',
        'async_set_off_grid_time': 'FIELD_OFF_GRID_TIME',
        'async_set_default_power': 'FIELD_DEFAULT_PW',
        'async_set_storm_minutes': 'FIELD_MINS_INTERVAL',
        'async_set_temp_unit': 'FIELD_TEMP_UNIT',
    }
    for name, field_name in expected_fields.items():
        block = _block(name)
        assert '_control_int(' in block, name
        assert field_name in block, name
        assert ' = int(' not in block, name
        assert ' if int(' not in block, name


def test_third_party_mqtt_config_validates_port_in_coordinator() -> None:
    """Coordinator-level MQTT config writes must not raw-cast the port."""
    source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(encoding='utf-8')
    block = source.split('async def async_set_third_party_mqtt_config', 1)[1].split(
        '\n    async def async_query_third_party_mqtt_config',
        1,
    )[0]

    assert 'parsed_port = _control_int(port, FIELD_THIRD_PARTY_MQTT_PORT)' in block
    assert 'if parsed_port < 1 or parsed_port > 65535:' in block
    assert 'FIELD_THIRD_PARTY_MQTT_PORT: parsed_port' in block
    assert 'FIELD_THIRD_PARTY_MQTT_PORT: int(port)' not in block


def test_third_party_mqtt_logs_do_not_emit_warning_noise() -> None:
    """Explicit experimental service calls should not pollute HA system warnings."""
    source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(encoding='utf-8')
    set_block = source.split('async def async_set_third_party_mqtt_config', 1)[1].split(
        '\n    async def async_query_third_party_mqtt_config',
        1,
    )[0]
    query_block = source.split('async def async_query_third_party_mqtt_config', 1)[
        1
    ].split(
        '\n    async def ',
        1,
    )[0]

    assert '_LOGGER.info(' in set_block
    assert '_LOGGER.warning(' not in set_block
    assert 'user=%r' not in set_block
    assert '_LOGGER.info(' in query_block
    assert '_LOGGER.warning(' not in query_block


def test_ct_phase_setter_uses_safe_int_before_range_check() -> None:
    """CT phase assignment must reject bad input without raw int-cast errors."""
    source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(encoding='utf-8')
    block = source.split('async def async_set_ct_phase', 1)[1].split(
        '\n    async def _async_query_subdevices_for_missing',
        1,
    )[0]

    assert 'phase_int = safe_int(phase)' in block
    assert 'if phase_int not in (1, 2, 3, 4):' in block
    assert 'FIELD_SCHE_PHASE: phase_int' in block
    assert 'phase_int = int(phase)' not in block


def test_single_price_setters_use_safe_float_parser() -> None:
    """Single-tariff writers must not expose raw float-cast errors."""
    source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(encoding='utf-8')
    set_block = source.split('async def async_set_single_price', 1)[1].split(
        '\n    async def async_set_price_mode_single',
        1,
    )[0]
    mode_block = source.split('async def async_set_price_mode_single', 1)[1].split(
        '\n    @staticmethod',
        1,
    )[0]

    assert 'price = safe_float(price_value)' in set_block
    assert 'if price is None or price < 0:' in set_block
    assert 'single_price=price' in set_block
    assert 'FIELD_SINGLE_PRICE: round(price, 4)' in set_block
    assert 'single_price=float(price_value)' not in set_block
    assert 'round(float(price_value)' not in set_block

    assert 'price = safe_float(single_price)' in mode_block
    assert 'await self.async_set_single_price(device_id, price)' in mode_block
    assert 'async_set_single_price(device_id, float(single_price))' not in mode_block


def test_coordinator_sets_http_properties_from_fresh_sanitized_property_payload() -> (
    None
):
    """HTTP source payload must be defined before entry assembly."""
    source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(encoding='utf-8')
    refresh_block = source.split('async def _async_update_data', 1)[1].split(
        '@property\n    def update_interval', 1
    )[0]

    assert 'new_props' not in refresh_block
    assert 'http_props = self._sanitize_main_properties(' in refresh_block
    assert 'payload.get(PAYLOAD_PROPERTIES) or {}' in refresh_block
    assert 'PAYLOAD_HTTP_PROPERTIES: http_props' in refresh_block
    assert 'http_props,' in refresh_block


def test_component_modules_import_all_referenced_const_names() -> None:
    """Catch runtime NameError regressions from missing .const imports in any module."""
    const_tree = ast.parse((CUSTOM_COMPONENT / 'const.py').read_text(encoding='utf-8'))
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

    modules_to_check = [path for path in _python_sources() if path.name != 'const.py']
    failures: dict[str, list[str]] = {}
    for path in modules_to_check:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        imported_from_const: set[str] = set()
        assigned: set[str] = set()
        used: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == 'const':
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
    util_tree = ast.parse((CUSTOM_COMPONENT / 'util.py').read_text(encoding='utf-8'))
    util_helpers = {
        node.name
        for node in ast.walk(util_tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
        and not node.name.startswith('_')
    }

    modules_to_check = [path for path in _python_sources() if path.name != 'util.py']
    failures: dict[str, list[str]] = {}
    for path in modules_to_check:
        tree = ast.parse(path.read_text(encoding='utf-8'))
        imported_from_util: set[str] = set()
        assigned: set[str] = set()
        used: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == 'util':
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
    sensor_source = (CUSTOM_COMPONENT / 'sensor.py').read_text(encoding='utf-8')
    for class_name in (
        'JackeryBatteryNetPowerSensor',
        'JackeryBatteryStackNetPowerSensor',
        'JackeryGridNetPowerSensor',
        'JackeryHomeConsumptionPowerSensor',
    ):
        block = sensor_source.split(f"class {class_name}", 1)[1].split('\nclass ', 1)[0]
        assert '_attr_device_class = SensorDeviceClass.POWER' in block
        assert '_attr_native_unit_of_measurement = UnitOfPower.WATT' in block
        assert '_attr_state_class' not in block

    assert 'historically existed without a compatible recorder unit' in sensor_source


def test_smart_meter_entities_cache_state_before_ha_state_write() -> None:
    """Smart-meter sensors must not recompute values during every HA state read."""
    sensor_source = (CUSTOM_COMPONENT / 'sensor.py').read_text(encoding='utf-8')
    block = sensor_source.split(
        'class JackerySmartMeterSensor(JackeryEntity, SensorEntity):', 1
    )[1].split('class JackeryRawPropertiesSensor', 1)[0]

    assert 'self._cached_native_value: Any = None' in block
    assert 'self._cached_attrs: dict[str, Any] = {}' in block
    assert 'def _refresh_cache(self) -> None:' in block
    assert 'def _handle_coordinator_update(self) -> None:' in block
    assert 'async def async_added_to_hass(self) -> None:' in block
    assert 'return self._cached_native_value' in block
    assert 'return self._cached_attrs' in block


def test_setup_removes_stale_energy_net_power_helpers_without_unit() -> None:
    """Broken Energy helper sensors should be cleaned before HA records them again."""
    const_source = (CUSTOM_COMPONENT / 'const.py').read_text(encoding='utf-8')
    init_source = (CUSTOM_COMPONENT / '__init__.py').read_text(encoding='utf-8')

    for name in (
        'STALE_ENERGY_HELPER_PREFIX',
        'STALE_NET_POWER_SUFFIX',
        'STALE_HELPER_VENDOR_TOKENS',
    ):
        assert name in const_source
        assert name in init_source
    assert '_async_remove_stale_energy_helpers(hass)' in init_source
    assert 'unit not in (None, "")' in init_source
    assert 'explicitly reference this integration' in init_source
    assert 'please recreate with Jackery battery_net_power' in init_source


def test_sensor_source_has_no_duplicate_battery_pack_ot_attribute_entry() -> None:
    """Battery-pack diagnostics should not expose the same raw key twice."""
    sensor_source = (CUSTOM_COMPONENT / 'sensor.py').read_text(encoding='utf-8')
    block = sensor_source.split(
        'class JackeryBatteryPackSensor(JackeryEntity, SensorEntity):', 1
    )[1].split('class JackerySmartMeterSensor', 1)[0]

    assert block.count('FIELD_OT,') == 1


def test_battery_pack_sensor_uses_ota_fallback_fields() -> None:
    """Pack firmware/update diagnostics must read the OTA-enriched fields."""
    sensor_source = (CUSTOM_COMPONENT / 'sensor.py').read_text(encoding='utf-8')
    block = sensor_source.split(
        'class JackeryBatteryPackSensor(JackeryEntity, SensorEntity):', 1
    )[1].split('class JackerySmartMeterSensor', 1)[0]

    assert 'raw = pack.get(FIELD_CURRENT_VERSION)' in block
    assert 'raw = pack.get(FIELD_IS_FIRMWARE_UPGRADE)' in block
    assert 'def _refresh_cache(self) -> None:' in block
    for field in (
        'FIELD_VERSION',
        'FIELD_CURRENT_VERSION',
        'FIELD_UPDATE_STATUS',
        'FIELD_TARGET_VERSION',
        'FIELD_TARGET_MODULE_VERSION',
        'FIELD_UPDATE_CONTENT',
        'FIELD_UPGRADE_TYPE',
    ):
        assert field in block


def test_data_quality_warnings_do_not_hide_sensor_states() -> None:
    """Repairs diagnose contradictions; entity states keep their documented source."""
    sensor_source = (CUSTOM_COMPONENT / 'sensor.py').read_text(encoding='utf-8')
    stat_block = sensor_source.split(
        'class JackeryStatSensor(JackeryEntity, SensorEntity):', 1
    )[1].split('class JackeryBatteryPackSensor', 1)[0]

    assert 'def _data_quality_warning_for_own_source' not in stat_block
    assert 'def _is_source_untrusted_by_data_quality' not in stat_block
    assert 'PAYLOAD_DATA_QUALITY' not in stat_block
    assert 'data_quality_untrusted' not in stat_block


def test_payload_debug_log_records_raw_types_parsed_floats_and_rotation() -> None:
    """Implement test payload debug log records raw types parsed floats and rotation."""
    util_source = (CUSTOM_COMPONENT / 'util.py').read_text(encoding='utf-8')
    api_source = API_IMPLEMENTATION.read_text(encoding='utf-8')
    coordinator_source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(
        encoding='utf-8'
    )

    for fragment in (
        'PAYLOAD_DEBUG_LOG_FILENAME',
        'PAYLOAD_DEBUG_LOG_MAX_BYTES',
        'def chart_series_debug',
        '"raw_type": type(raw).__name__',
        '"parsed_float": parsed',
        '"parsed_sum": round(total, 5)',
        'def append_payload_debug_line',
    ):
        assert (
            fragment in util_source
            or fragment in api_source
            or fragment in coordinator_source
        )

    assert 'self.payload_debug_callback' in api_source
    assert 'chart_series_debug(payload)' in api_source
    assert '"kind": "http"' in api_source
    assert '"kind": "mqtt"' in coordinator_source
    assert 'append_payload_debug_line' in coordinator_source


def test_local_helper_calls_match_their_declared_arity() -> None:
    """Catch nested helper call mistakes before they create runtime coroutine leaks."""
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding='utf-8'))
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
    source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    update_func = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == '_async_update_data'
    )
    ttl_calls = [
        node
        for node in ast.walk(update_func)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == '_get_with_ttl'
    ]
    assert ttl_calls
    assert all(len(call.args) == 5 for call in ttl_calls)

    bad_fragment = 'PAYLOAD_ALARM,\n    PAYLOAD_DEBUG_LOG_FILENAME,\n                    self._slow_metrics_interval_sec'
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
    allowed_dunder_globals = {'__file__', '__conditional_annotations__'}
    for path in _python_sources():
        table = symtable.symtable(path.read_text(encoding='utf-8'), str(path), 'exec')
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
    config_flow_source = (CUSTOM_COMPONENT / 'config_flow.py').read_text(
        encoding='utf-8'
    )
    options_block = config_flow_source.split('class JackeryOptionsFlow', 1)[1].split(
        'class JackeryConfigFlow', 1
    )[0]

    assert 'from .util import config_entry_bool_option' in config_flow_source
    assert 'def _entry_bool_option(' not in config_flow_source
    assert (
        'current_options = _current_option_values(self.config_entry)' in options_block
    )
    assert 'def _current_option_values(entry: ConfigEntry)' in config_flow_source
    assert 'config_entry_bool_option(entry, key, default)' in config_flow_source
    assert '.options.get(' not in options_block


def test_sensor_setup_uses_shared_bool_option_fallback_helper() -> None:
    """Sensor setup should share one fallback path from options/data/defaults."""
    sensor_source = (CUSTOM_COMPONENT / 'sensor.py').read_text(encoding='utf-8')
    setup_block = sensor_source.split('async def async_setup_entry', 1)[1].split(
        '# ---------------------------------------------------------------------------\n# Entities',
        1,
    )[0]

    assert 'config_entry_bool_option' in sensor_source
    assert 'def _entry_bool_option(' not in sensor_source
    assert setup_block.count('config_entry_bool_option(') == 3
    assert '.options.get(' not in setup_block


def test_price_provider_gate_uses_validated_price_sources() -> None:
    """Price-provider select gate must ignore malformed provider payloads."""
    select_source = (CUSTOM_COMPONENT / 'select.py').read_text(encoding='utf-8')
    provider_gate = select_source.split('if key == "electricity_price_provider":', 1)[
        1
    ].split('return False', 1)[0]

    assert '_price_sources_from_payload(payload)' in provider_gate
    assert 'bool(payload.get(PAYLOAD_PRICE_SOURCES))' not in provider_gate


def test_no_unresolved_git_merge_conflict_markers() -> None:
    """Catch real merge conflict markers without flagging reStructuredText tables."""
    marker_prefixes = ('<<<<<<< ', '>>>>>>> ')
    for path in pathlib.Path('.').rglob('*'):
        if not path.is_file() or '.git' in path.parts:
            continue
        try:
            lines = path.read_text(encoding='utf-8').splitlines()
        except UnicodeDecodeError:
            continue
        for line_number, line in enumerate(lines, start=1):
            assert not line.startswith(marker_prefixes), f"{path}:{line_number}: {line}"
            assert line != '=======', f"{path}:{line_number}: {line}"


def test_payload_debug_file_is_gated_by_dedicated_logger_not_options() -> None:
    """Raw payload logging must use HA logger controls without a stale option.

    The setup/options checkbox was removed, but the JSONL writer must still avoid
    inheriting DEBUG from the parent integration logger. Requiring DEBUG to be
    set directly on the dedicated payload-debug logger keeps the feature
    available for diagnostics without a hidden ``debug_payload_log`` option.
    """
    const_source = (CUSTOM_COMPONENT / 'const.py').read_text(encoding='utf-8')
    coordinator_source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(
        encoding='utf-8'
    )
    init_source = (CUSTOM_COMPONENT / '__init__.py').read_text(encoding='utf-8')

    assert 'CONF_DEBUG_PAYLOAD_LOG' not in const_source
    assert 'DEFAULT_DEBUG_PAYLOAD_LOG' not in const_source
    assert 'CONF_DEBUG_PAYLOAD_LOG' not in coordinator_source
    assert 'DEFAULT_DEBUG_PAYLOAD_LOG' not in coordinator_source
    assert 'debug_payload_log' not in init_source
    assert '_async_purge_stale_payload_debug_log' not in init_source

    assert '_PAYLOAD_DEBUG_LOGGER.level != logging.DEBUG' in coordinator_source
    assert (
        'if not _PAYLOAD_DEBUG_LOGGER.isEnabledFor(logging.DEBUG):'
        not in coordinator_source
    )


def test_no_direct_blocking_file_io_inside_async_functions() -> None:
    """HA runtime paths must not do disk IO directly in the event loop."""
    forbidden = {
        'open',
        'write_text',
        'read_text',
        'unlink',
        'mkdir',
        'stat',
    }
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding='utf-8'))
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
    api_tree = ast.parse(API_IMPLEMENTATION.read_text(encoding='utf-8'))
    method_arity: dict[str, tuple[int, int | None]] = {}
    for cls in [
        node
        for node in ast.walk(api_tree)
        if isinstance(node, ast.ClassDef) and node.name == 'JackeryApi'
    ]:
        for item in cls.body:
            if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = item.args
                positional = len(args.posonlyargs) + len(args.args)
                decorators = {
                    dec.id for dec in item.decorator_list if isinstance(dec, ast.Name)
                }
                if 'classmethod' in decorators:
                    positional = max(0, positional - 1)
                elif 'staticmethod' not in decorators and positional:
                    positional -= 1
                required = positional - len(args.defaults)
                max_count = None if args.vararg is not None else positional
                if not item.name.startswith('_'):
                    method_arity[item.name] = (required, max_count)

    assert method_arity
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding='utf-8'))
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
            if isinstance(receiver, ast.Name) and receiver.id not in {'api'}:
                continue
            if isinstance(receiver, ast.Attribute) and receiver.attr != 'api':
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
    init_source = (CUSTOM_COMPONENT / '__init__.py').read_text(encoding='utf-8')
    services_source = (CUSTOM_COMPONENT / 'services.py').read_text(encoding='utf-8')
    init_tree = ast.parse(init_source)

    assert isinstance(init_tree.body[0], ast.Expr)
    future_annotations = (
        isinstance(init_tree.body[1], ast.ImportFrom)
        and init_tree.body[1].module == '__future__'
        and any(alias.name == 'annotations' for alias in init_tree.body[1].names)
    )
    assert future_annotations or sys.version_info >= (3, 14)
    assert 'from typing import TYPE_CHECKING' not in init_source
    assert 'from .coordinator import JackerySolarVaultCoordinator' in init_source
    # Service-action routing lives in services.py; the helper is private
    # there but must keep its typed signature so multi-account lookups
    # remain mypy-clean.
    assert (
        'def _loaded_coordinators(hass: HomeAssistant) '
        '-> list[JackerySolarVaultCoordinator]' in services_source
    )
    assert 'from .coordinator import JackerySolarVaultCoordinator' in services_source


def test_all_python_sources_parse_with_current_and_ha_target_grammar() -> None:
    """Catch accidental syntax that only works on a different Python branch."""
    for path in _python_sources():
        source = path.read_text(encoding='utf-8')
        ast.parse(source, filename=str(path))
        # HA 2026 currently runs Python 3.14 in the user's diagnostics, but the
        # integration is intentionally packaged for Python 3.14+ only.
        ast.parse(source, filename=str(path), feature_version=(3, 14))


def test_pre_commit_python_target_matches_ha_minimum() -> None:
    """Keep pre-commit autofixes from rewriting code with newer-only syntax."""
    config = pathlib.Path('.pre-commit-config.yaml').read_text(encoding='utf-8')

    assert 'python: python3.14' in config
    assert '--py314-plus' in config
    assert 'python3.13' not in config
    assert '--py313-plus' not in config


def _load_py314_exception_guard_module():
    """Load the local Python 3.14 exception-style guard script."""
    spec = importlib.util.spec_from_file_location(
        'verify_py314_exception_style',
        pathlib.Path('scripts/verify_py314_exception_style.py'),
    )
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_autofix_workflow_keeps_ruff_python314_format_stable() -> None:
    """Autofix must use one pinned Ruff with an explicit Python 3.14 target."""
    workflow = pathlib.Path('.github/workflows/autofix.yml').read_text(encoding='utf-8')

    assert 'RUFF_VERSION:' in workflow
    assert 'RUFF_TARGET_VERSION: py314' in workflow
    assert 'bun run install:ci' in workflow
    assert 'astral-sh/ruff-action' not in workflow
    assert 'pre-commit-ci/lite-action' not in workflow
    assert '--target-version "${RUFF_TARGET_VERSION}"' in workflow
    assert '--unsafe-fixes' not in workflow
    assert '--add-noqa' in workflow
    assert (
        'python scripts/verify_py314_exception_style.py --fix custom_components/'
        in workflow
    )
    assert 'bun run format:check' in workflow
    assert 'ruff check --fix' not in workflow
    assert 'ref: ${{ github.head_ref || github.ref_name }}' in workflow
    assert 'target_branch="${GITHUB_HEAD_REF:-${GITHUB_REF_NAME}}"' in workflow
    assert 'git fetch origin "${target_branch}"' in workflow
    assert 'git rebase "origin/${target_branch}"' in workflow
    assert 'git push origin "HEAD:${target_branch}"' in workflow


def test_validate_ruff_job_uses_same_python314_formatter_target() -> None:
    """Validate must check exactly the formatter target that autofix writes."""
    workflow = pathlib.Path('.github/workflows/validate.yml').read_text(
        encoding='utf-8'
    )

    assert 'python -m pip install "ruff==0.15.12"' in workflow
    assert 'run: bun run lint' in workflow
    assert 'run: bun run format:check' in workflow
    assert 'run: ruff format --check custom_components/ tests/' not in workflow


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


def test_ha_test_workflow_is_wired() -> None:
    """HA test dependencies and bun test commands must stay wired."""
    workflow = pathlib.Path('.github/workflows/validate.yml').read_text(
        encoding='utf-8'
    )
    requirements = pathlib.Path('requirements-test.txt').read_text(encoding='utf-8')

    assert 'pytest-homeassistant-custom-component' in requirements
    assert 'requirements-test.txt' in workflow
    assert 'run: bun run ha:test' in workflow
    assert 'pytest-ha.ini' in pathlib.Path('package.json').read_text(encoding='utf-8')
    assert pathlib.Path('pytest-ha.ini').exists()
    assert 'asyncio_mode = auto' in pathlib.Path('pytest-ha.ini').read_text(
        encoding='utf-8'
    )


def test_generated_payload_debug_logs_are_ignored() -> None:
    """Implement test generated payload debug logs are ignored."""
    gitignore = pathlib.Path('.gitignore').read_text(encoding='utf-8')

    assert 'jackery_solarvault_payload_debug.jsonl' in gitignore
    assert 'jackery_solarvault_payload_debug.jsonl.1' in gitignore


def test_hacs_manifest_uses_current_supported_keys() -> None:
    """Keep hacs.json compatible with the current HACS action schema."""
    manifest = json.loads(pathlib.Path('hacs.json').read_text(encoding='utf-8'))
    supported_keys = {
        'content_in_root',
        'country',
        'filename',
        'hacs',
        'hide_default_branch',
        'homeassistant',
        'name',
        'persistent_directory',
        'zip_release',
    }

    assert 'name' in manifest
    assert not (set(manifest) - supported_keys)
    assert 'render_readme' not in manifest


def test_setup_entry_cleans_up_partially_initialized_coordinator() -> None:
    """Failed setup must not leak MQTT clients, timers or runtime_data."""
    init_source = (CUSTOM_COMPONENT / '__init__.py').read_text(encoding='utf-8')

    assert 'import contextlib' in init_source
    assert 'try:' in init_source
    assert '# Discovery must run first' in init_source
    assert 'except Exception:' in init_source
    assert 'with contextlib.suppress(Exception):' in init_source
    assert 'await coordinator.async_shutdown()' in init_source
    assert 'if entry.runtime_data is coordinator:' in init_source
    assert 'entry.runtime_data = cast(Any, None)' in init_source
    assert (
        'await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)'
        in init_source
    )
    assert 'coordinator.async_start_statistics_imports()' in init_source
    assert init_source.index(
        'await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)'
    ) < init_source.index('coordinator.async_start_statistics_imports()')


def test_brand_assets_are_packaged_without_runtime_sync() -> None:
    """Use packaged brand PNGs without mutating the custom component at runtime."""
    init_source = (CUSTOM_COMPONENT / '__init__.py').read_text(encoding='utf-8')
    brand_dir = CUSTOM_COMPONENT / 'brand'

    assert not (CUSTOM_COMPONENT / 'brand.py').exists()
    assert '_async_ensure_cached_brand_images' not in init_source
    assert 'async_add_executor_job' not in init_source
    assert (brand_dir / 'icon.png').is_file()
    assert (brand_dir / 'icon@2x.png').is_file()
    assert (brand_dir / 'dark_icon.png').is_file()
    assert (brand_dir / 'dark_icon@2x.png').is_file()
    assert not pathlib.Path('brands/icon.svg').exists()
    assert not pathlib.Path('brands/logo.svg').exists()


def test_mqtt_tls_uses_verified_jackery_ca_without_insecure_fallback() -> None:
    """MQTT TLS may ship a CA trust anchor, never keys or insecure TLS."""
    component_files = list(CUSTOM_COMPONENT.rglob('*'))
    bundled_sensitive_files = [
        path.as_posix()
        for path in component_files
        if path.is_file() and path.suffix.lower() in {'.cer', '.pem', '.key'}
    ]
    assert bundled_sensitive_files == []
    assert (CUSTOM_COMPONENT / 'jackery_ca.crt').is_file()
    assert 'BEGIN CERTIFICATE' in (CUSTOM_COMPONENT / 'jackery_ca.crt').read_text(
        encoding='utf-8'
    )

    mqtt_source = MQTT_IMPLEMENTATION.read_text(encoding='utf-8')
    coordinator_source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(
        encoding='utf-8'
    )
    combined = mqtt_source + coordinator_source

    assert 'ssl.create_default_context()' in mqtt_source
    assert 'ssl.CERT_REQUIRED' in mqtt_source
    assert 'ssl.CERT_NONE' not in combined
    assert 'tls_insecure=True' not in combined
    assert 'disabled_after_strict_tls_failure' not in combined
    assert 'ctx.load_verify_locations(cafile=str(ca_path))' in mqtt_source
    # The path call may be multi-line after ruff format; collapse whitespace
    # before checking. Equivalent to grepping "config.path(...jackery_ca.crt)".
    mqtt_source_collapsed = re.sub(r'\s+', ' ', mqtt_source)
    assert (
        'self._hass.config.path( "custom_components", "jackery_solarvault", "jackery_ca.crt" )'
        in mqtt_source_collapsed
        or 'self._hass.config.path("custom_components", "jackery_solarvault", "jackery_ca.crt")'
        in mqtt_source_collapsed
    )
    assert '"tls_custom_ca_loaded": self._tls_custom_ca_loaded' in mqtt_source
    assert 'diag["tls_certificate_verification"] = (' in coordinator_source
    assert 'if diag.get("tls_x509_strict_disabled")' in coordinator_source
    assert '"chain_hostname_enabled_x509_strict_disabled"' in coordinator_source
    assert '"chain_hostname_enabled"' in coordinator_source


def test_auth_failures_are_not_suppressed_by_control_or_background_paths() -> None:
    """Control writes/background helpers must propagate auth failures to HA reauth."""
    coordinator_source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(
        encoding='utf-8'
    )
    repairs_source = (CUSTOM_COMPONENT / 'repairs.py').read_text(encoding='utf-8')
    number_source = (CUSTOM_COMPONENT / 'number.py').read_text(encoding='utf-8')

    assert 'def _raise_config_entry_auth_failed(' in coordinator_source
    for context in (
        'while preparing MQTT credentials',
        'while fetching battery pack OTA metadata',
        'while preparing an MQTT command',
        'while refreshing MQTT command credentials',
        'while saving the single tariff',
        'while reading the current tariff',
        'while reading price sources',
        'while saving the dynamic tariff',
    ):
        assert context in coordinator_source
    assert 'async_track_time_interval' not in coordinator_source
    assert 'async def _async_periodic_refresh' not in coordinator_source
    assert 'update_interval=update_interval' in coordinator_source
    update_block = coordinator_source.split('async def _async_update_data', 1)[1].split(
        '# ------------------------------------------------------------------\n    # Diagnostics',
        1,
    )[0]
    assert '_raise_config_entry_auth_failed' in update_block
    assert 'def _defer_background_auth_failure(' in coordinator_source
    assert 'except ConfigEntryAuthFailed:' in coordinator_source
    assert 'except ConfigEntryAuthFailed as err:' in coordinator_source
    assert 'except ConfigEntryAuthFailed:' in repairs_source
    assert 'except JackeryAuthError as err:' in number_source
    assert 'raise ConfigEntryAuthFailed' in number_source


def test_brand_runtime_sync_is_absent() -> None:
    """Read-only custom component mounts are safe because setup writes no brand files."""
    init_source = (CUSTOM_COMPONENT / '__init__.py').read_text(encoding='utf-8')
    component_sources = '\n'.join(
        path.read_text(encoding='utf-8')
        for path in CUSTOM_COMPONENT.glob('*.py')
        if path.name != '__pycache__'
    )

    assert not (CUSTOM_COMPONENT / 'brand.py').exists()
    assert '_async_ensure_cached_brand_images' not in component_sources
    assert 'shutil.copy2' not in component_sources
    assert 'Path(__file__).with_name("brand")' not in component_sources
    assert 'async_setup_services(hass)' in init_source


def test_legacy_suffix_match_is_boundary_anchored() -> None:
    """Removing legacy suffixes must not over-match current unique IDs.

    Regression for the diagnostics-reported gap: every reload deleted
    ``<device_id>_device_today_battery_charge`` because the legacy suffix
    ``_today_battery_charge`` matched its tail via a plain ``str.endswith``.
    The replacement helper anchors the head to the documented unique-id
    contract (numeric ``device_id`` optionally followed by ``_battery_pack_N``)
    so the legacy id is the only one that matches.
    """
    init_source = (CUSTOM_COMPONENT / '__init__.py').read_text(encoding='utf-8')
    assert '_legacy_suffix_matches' in init_source
    assert '_LEGACY_UID_HEAD_RE' in init_source
    # The naive ``any(uid.endswith(suffix) for ...)`` is now gone; the helper
    # delegates to the boundary-anchored matcher instead.
    assert 'uid.endswith(suffix) for suffix in suffix_tuple' not in init_source
    assert '_legacy_suffix_matches(uid, suffix) for suffix in suffix_tuple' in (
        init_source
    )

    # Mirror the regex behaviour so this test fails fast if the contract
    # drifts. Keep the pattern in lock-step with ``__init__._LEGACY_UID_HEAD_RE``.
    pattern = re.compile(r'\d+(?:_battery_pack_\d+)?')

    def matches(uid: str, suffix: str) -> bool:
        if not uid.endswith(suffix):
            return False
        return pattern.fullmatch(uid[: -len(suffix)]) is not None

    # Legacy ids that must be removed.
    assert matches('573702884982521856_today_battery_charge', '_today_battery_charge')
    assert matches(
        '573702884982521856_battery_pack_0_today_battery_charge',
        '_today_battery_charge',
    )
    # Current ids that must NOT be removed even though their tail contains
    # the legacy suffix.
    assert not matches(
        '573702884982521856_device_today_battery_charge', '_today_battery_charge'
    )
    assert not matches(
        '573702884982521856_device_today_battery_discharge',
        '_today_battery_discharge',
    )


# ---------------------------------------------------------------------------
# Drift-protection: CHANGELOG "Three-part fix"
# ---------------------------------------------------------------------------


def test_stale_period_guard_publishes_none_for_all_periods() -> None:
    """Pin CHANGELOG "Three-part fix" against DAY-carve-out regressions.

    The CHANGELOG explicitly states the stale-period guard must set
    ``native_value`` to ``None`` for ALL period sensors when the wall
    clock has crossed a period boundary but the source data still has
    the previous period's begin_date. A previous ``raw = 0 if DAY else
    None`` carve-out reintroduced the midnight delta spike the
    three-part fix was designed to prevent.
    """
    source = (CUSTOM_COMPONENT / 'sensor.py').read_text(encoding='utf-8')
    assert 'raw = 0 if self._reset_period == DATE_TYPE_DAY' not in source
    assert 'raw = 0 if self._reset_period' not in source


def test_total_revenue_uses_total_increasing_without_monetary_class() -> None:
    """Pin CHANGELOG "Three-part fix" for ``total_revenue``.

    Adding ``device_class=MONETARY`` forced ``state_class=TOTAL`` via the
    HA validator restriction (MONETARY -> {TOTAL} only), which lost the
    CHANGELOG fix that uses ``TOTAL_INCREASING`` so the Recorder treats the
    midnight cloud transient as a reset rather than a real loss.
    """
    source = (CUSTOM_COMPONENT / 'sensor.py').read_text(encoding='utf-8')
    block = re.search(
        r'key="total_revenue",.*?\),',
        source,
        re.DOTALL,
    )
    assert block is not None, 'total_revenue description not found in sensor.py'
    body = block.group(0)
    assert 'SensorStateClass.TOTAL_INCREASING' in body, (
        'total_revenue must use SensorStateClass.TOTAL_INCREASING per CHANGELOG '
        '"Three-part fix" / Midnight race condition.'
    )
    assert 'SensorDeviceClass.MONETARY' not in body, (
        'total_revenue must NOT carry device_class=MONETARY — it is not in any '
        'of the docs and it forces state_class back to TOTAL, undoing the '
        'three-part fix.'
    )


def test_no_entity_layer_cross_period_repair() -> None:
    """Entity code must not reintroduce ad-hoc cross-period repair.

    Block re-introduction of the ``_clamp_backwards_period_value``
    pattern that papered over the stale-period guard regression at the
    wrong layer.
    """
    source = (CUSTOM_COMPONENT / 'sensor.py').read_text(encoding='utf-8')
    assert '_clamp_backwards_period_value' not in source
    assert '_last_published_value' not in source
    assert '_last_published_anchor' not in source


def test_ble_sink_calls_merge_with_correct_signature() -> None:
    """Pin BLE sink against the 2-arg regression that froze live values.

    ``_merge_main_properties_for_device`` takes 3 positional arguments
    ``(device_id, base, updates)``. The previous 2-arg call inside the
    BLE listener sink raised ``TypeError`` for every decoded frame, the
    sink's try/except swallowed it as DEBUG, and BLE telemetry never
    reached coordinator.data. Whenever MQTT went quiet at the same time
    the user saw "no live values for 9+ minutes" (observed
    2026-05-16 17:41-17:44 production log).
    """
    source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(encoding='utf-8')
    # Scope to the BLE-sink body. The closing ``listener = …`` line
    # marks the end of ``_sink`` inside ``async_start_ble_transport``.
    sink_match = re.search(
        r'async def _sink\(device_id: str.*?(?=\n {0,8}listener = JackeryBleListener)',
        source,
        re.DOTALL,
    )
    assert sink_match is not None, 'BLE sink not found in coordinator.py'
    sink_body = sink_match.group(0)
    # Walk the actual call (``self._merge_…``) so we skip the
    # docstring's bare ``_merge_main_properties_for_device(device_id,
    # payload)`` mention. Parenthesis-balanced matching keeps nested
    # ``.get(PAYLOAD_PROPERTIES) or {}`` arguments from terminating the
    # regex early. Count commas at *depth 1* of the call's parentheses.
    call_idx = sink_body.find('self._merge_main_properties_for_device(')
    assert call_idx >= 0, (
        'BLE sink does not call self._merge_main_properties_for_device — wire it '
        'back so cmd=107/121 BLE bodies actually merge into coordinator.data.'
    )
    cursor = call_idx + len('self._merge_main_properties_for_device(')
    depth = 1
    arg_commas = 0
    while cursor < len(sink_body) and depth > 0:
        ch = sink_body[cursor]
        if ch == '(':
            depth += 1
        elif ch == ')':
            depth -= 1
        elif ch == ',' and depth == 1:
            arg_commas += 1
        cursor += 1
    # 3 args -> 2 commas at depth 1; 2 args (the broken signature) -> 1.
    assert arg_commas >= 2, (
        'BLE sink calls _merge_main_properties_for_device with fewer than 3 args. '
        'This is the regression that froze live values on 2026-05-16. The method '
        'needs (device_id, base, updates); a 2-arg call raises TypeError silently '
        'in the sink try/except and drops every decoded BLE frame.'
    )


def test_historical_statistics_backfill_is_http_only() -> None:
    """Historical statistic backfill must stay bounded to HTTP day curves."""
    source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(encoding='utf-8')
    for removed in (
        'def _iter_calendar_months',
        'def _iter_calendar_weeks',
        'def _iter_calendar_days',
        'async def _async_fetch_historical_app_chart_source',
        'async def _async_repair_missing_app_chart_statistics',
        'async def async_repair_statistics',
    ):
        assert removed not in source
    assert 'async def _async_fetch_historical_day_chart_sources' in source
    assert 'async def _async_http_backfill_recent_day_statistics' in source
    assert '_STATISTICS_HTTP_BACKFILL_WINDOW_DAYS = 7' in source
    assert '_STATISTICS_HTTP_BACKFILL_INTERVAL_SEC = 6 * 60 * 60' in source
    assert '_schedule_mqtt_backfill_queries' not in source


def test_listener_gate_is_present_in_all_entity_platforms() -> None:
    """All 7 entity platforms must gate their ``_add_new_entities`` listener.

    Without the gate, every MQTT push (~150/min when active) re-runs
    ``_collect_entities`` for every platform and emits a unique-id-dedup
    DEBUG entry for each already-registered entity, drowning the log
    in tens of thousands of repeats per hour. The gate compares the
    coordinator signature; live entity-state updates are unaffected
    because each entity registers its own CoordinatorEntity listener.
    """
    platforms = (
        'sensor.py',
        'binary_sensor.py',
        'button.py',
        'number.py',
        'select.py',
        'switch.py',
        'text.py',
    )
    for name in platforms:
        source = (CUSTOM_COMPONENT / name).read_text(encoding='utf-8')
        assert 'coordinator_entity_signature(' in source, (
            f"{name} is missing the coordinator_entity_signature listener gate"
        )
        assert 'if sig == last_signature:' in source, (
            f"{name} listener gate must short-circuit on unchanged signature"
        )


def test_ble_keep_alive_loop_is_wired_into_connection_runner() -> None:
    """BLE connection runner must spawn the keep-alive heartbeat.

    The SolarVault peripheral closes idle GATT sessions after roughly
    20 s (observed 2026-05-17 production log: disconnects every 6-20 s).
    A periodic ``cmd=106`` query write keeps the session warm and
    doubles as a property refresh.
    """
    source = (CUSTOM_COMPONENT / 'client' / 'ble_transport.py').read_text(
        encoding='utf-8'
    )
    assert '_KEEPALIVE_INTERVAL_SEC' in source, 'keep-alive interval constant missing'
    assert 'async def _async_keep_alive_loop' in source, 'keep-alive loop missing'
    # Loop must be spawned as a background task after start_notify and
    # cancelled in the connection runner's finally block.
    assert (
        'self._hass.async_create_background_task(\n'
        '                        self._async_keep_alive_loop(device_id)'
    ) in source, 'keep-alive task is not spawned in the connection runner'
    assert 'keep_alive_task.cancel()' in source, (
        'keep-alive task must be cancelled in the finally block on disconnect'
    )


def test_ble_service_waits_for_reconnect_before_failing() -> None:
    """Direct BLE service calls must not fail during a reconnect window."""
    transport = (CUSTOM_COMPONENT / 'client' / 'ble_transport.py').read_text(
        encoding='utf-8'
    )
    assert 'async def async_ensure_connected' in transport
    assert 'self._async_run_connection(device_id, address)' in transport

    coordinator = (CUSTOM_COMPONENT / 'coordinator.py').read_text(encoding='utf-8')
    assert 'connect_timeout_sec: float = 0.0' in coordinator
    assert 'async_ensure_connected(' in coordinator

    services = (CUSTOM_COMPONENT / 'services.py').read_text(encoding='utf-8')
    assert '_BLE_SERVICE_CONNECT_TIMEOUT_SEC = 35.0' in services
    assert 'connect_timeout_sec=_BLE_SERVICE_CONNECT_TIMEOUT_SEC' in services


def test_ble_cmd_120_battery_pack_routing_is_narrow() -> None:
    """cmd=120 BLE routing must only merge battery-pack lifetime.

    cmd=120 BLE bodies carry four variants (system / per-device / CT /
    battery-pack). Only the battery-pack variant has no HTTP authority
    (``/v1/device/battery/pack/list`` returns ``data: null`` for
    SolarVault). The other three variants conflict with HTTP authority
    and must stay "not routed" until firmware semantics are docs-
    confirmed.
    """
    source = (CUSTOM_COMPONENT / 'coordinator.py').read_text(encoding='utf-8')
    assert '_merge_battery_pack_lifetime_from_ble' in source, (
        'battery-pack lifetime merge helper missing'
    )
    # The sink must gate the cmd=120 branch on devType=BATTERY_PACK and
    # presence of deviceSn — never blindly merge any cmd=120 frame.
    import re

    sink_match = re.search(
        r'async def _sink\(device_id: str.*?(?=\n {0,8}listener = JackeryBleListener)',
        source,
        re.DOTALL,
    )
    assert sink_match is not None, 'BLE sink not found'
    sink = sink_match.group(0)
    assert 'MQTT_CMD_QUERY_COMBINE_DATA' in sink, (
        'cmd=120 branch must reference MQTT_CMD_QUERY_COMBINE_DATA'
    )
    assert 'SUBDEVICE_DEV_TYPE_BATTERY_PACK' in sink, (
        'cmd=120 branch must gate on SUBDEVICE_DEV_TYPE_BATTERY_PACK'
    )
    assert 'FIELD_DEVICE_SN' in sink, (
        'cmd=120 branch must require deviceSn before merging'
    )


def test_battery_pack_lifetime_entities_exist() -> None:
    """Pin Phase 8 — pack-lifetime entity descriptions.

    Without these two ``JackeryBatteryPackSensorDescription`` entries
    the BLE-sourced ``inEgy``/``outEgy`` lifetime counters stay buried
    in coordinator.data and are invisible to the Energy Dashboard.
    Both must be ``TOTAL_INCREASING`` (lifetime monotonic counters in
    kWh after the ``_div(1000)`` Wh-int transform) and
    ``entity_registry_enabled_default=False`` (BLE transport is opt-in).
    """
    source = (CUSTOM_COMPONENT / 'sensor.py').read_text(encoding='utf-8')
    assert 'translation_key="battery_pack_lifetime_charge_energy"' in source
    assert 'translation_key="battery_pack_lifetime_discharge_energy"' in source
    assert 'field=FIELD_IN_EGY' in source
    assert 'field=FIELD_OUT_EGY' in source
    # Both must use TOTAL_INCREASING and be disabled by default.
    # Scan a ~700-char window starting at the translation_key line —
    # that covers the entire description block including the closing
    # ``),`` regardless of nested ``_div()`` calls in the transform.
    for key in (
        'battery_pack_lifetime_charge_energy',
        'battery_pack_lifetime_discharge_energy',
    ):
        anchor = source.find(f'translation_key="{key}"')
        assert anchor >= 0, f"{key} entity description not found"
        window = source[anchor : anchor + 700]
        assert 'SensorStateClass.TOTAL_INCREASING' in window, (
            f"{key} must use TOTAL_INCREASING for lifetime counter semantics"
        )
        assert 'entity_registry_enabled_default=False' in window, (
            f"{key} must be opt-in (BLE transport is optional)"
        )
        assert '_div(1000)' in window, (
            f"{key} must transform Wh-int -> kWh via _div(1000)"
        )


def test_battery_pack_setup_honors_description_enabled_default() -> None:
    """Pack sensor creation must preserve per-description default enablement."""
    source = (CUSTOM_COMPONENT / 'sensor.py').read_text(encoding='utf-8')
    block = source.split('for pack_desc in BATTERY_PACK_SENSOR_DESCRIPTIONS:', 1)[
        1
    ].split('# Smart plugs', 1)[0]

    assert 'pack_desc.entity_registry_enabled_default' in block
    assert 'pack_desc.entity_category' in block
    assert '!= EntityCategory.DIAGNOSTIC' in block


def test_ble_listener_stats_track_unrouted_cmd_counter() -> None:
    """Pin Phase 9 — unrouted-frame counter on listener stats.

    The sink increments ``stats.unrouted_frames_by_cmd[cmd]`` for
    every cmd=120 system/per-device/CT variant it deliberately
    leaves unmerged. The counter must be exposed in
    ``ble_observations()`` so the maintainer can see at a glance
    what BLE telemetry currently flows past unused.
    """
    transport = (CUSTOM_COMPONENT / 'client' / 'ble_transport.py').read_text(
        encoding='utf-8'
    )
    assert 'unrouted_frames_by_cmd: dict[int, int]' in transport, (
        'BleListenerStats must declare unrouted_frames_by_cmd'
    )
    coord = (CUSTOM_COMPONENT / 'coordinator.py').read_text(encoding='utf-8')
    assert 'stats.unrouted_frames_by_cmd[cmd]' in coord, (
        'BLE sink must increment unrouted_frames_by_cmd'
    )
    assert '"unrouted_frames_by_cmd"' in coord, (
        'ble_observations() must expose unrouted_frames_by_cmd in diagnostics'
    )
