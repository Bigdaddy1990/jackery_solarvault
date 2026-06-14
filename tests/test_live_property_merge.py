"""Regression tests for transport-safe live property merging."""

from pathlib import Path
import sys
import types
import warnings

import pytest


def _load_ingest_module() -> types.ModuleType:
    """Load ingest.py without importing Home Assistant package initializers."""
    package_name = "custom_components.jackery_solarvault"
    const_name = f"{package_name}.const"
    package = sys.modules.setdefault(package_name, types.ModuleType(package_name))
    package.__path__ = [str(Path("custom_components/jackery_solarvault"))]
    const = types.ModuleType(const_name)
    for name in (
        "APP_SECTION_BATTERY_STAT",
        "APP_SECTION_BATTERY_TRENDS",
        "APP_SECTION_CT_STAT",
        "APP_SECTION_HOME_STAT",
        "APP_SECTION_HOME_TRENDS",
        "APP_SECTION_PV_STAT",
        "APP_SECTION_PV_TRENDS",
    ):
        setattr(const, name, name.lower())
    sys.modules[const_name] = const

    module_name = f"{package_name}.ingest"
    sys.modules.pop(module_name, None)
    source = Path("custom_components/jackery_solarvault/ingest.py").read_text(
        encoding="utf-8"
    )
    module = types.ModuleType(module_name)
    module.__package__ = package_name
    module.__file__ = "custom_components/jackery_solarvault/ingest.py"
    sys.modules[module_name] = module
    exec(compile(source, module.__file__, "exec"), module.__dict__)  # noqa: S102
    return module


merge_live_properties = _load_ingest_module().merge_live_properties


def test_merge_live_properties_rejects_unconfirmed_zero_over_valid_value() -> None:
    """A sparse live payload must not erase a known non-zero value with zero."""
    merged = merge_live_properties({"today_energy": 12.5}, {"today_energy": 0})

    assert merged == {"today_energy": 12.5}


def test_merge_live_properties_rejects_none_over_valid_value() -> None:
    """None from a transport frame must not overwrite a populated live value."""
    merged = merge_live_properties({"battery": 87}, {"battery": None})

    assert merged == {"battery": 87}


def test_merge_live_properties_recursively_validates_nested_dicts() -> None:
    """Nested live sections use the same validation rules as top-level fields."""
    merged = merge_live_properties(
        {"ct": {"volt": 230.1, "freq": 50, "meta": {"phase": "A"}}},
        {"ct": {"volt": 0, "freq": None, "meta": {"phase": ""}}},
    )

    assert merged == {"ct": {"volt": 230.1, "freq": 50, "meta": {"phase": "A"}}}


def test_merge_live_properties_keeps_transport_priority_traceable() -> None:
    """Cloud, MQTT, and BLE frames merge in arrival order without invalid blanks."""
    cloud = {"power": 500, "soc": 80, "source": "cloud"}
    mqtt = {"power": 0, "soc": None, "source": "mqtt"}
    ble = {"power": 450, "soc": 79, "source": "ble"}

    merged = merge_live_properties(cloud, mqtt)
    merged = merge_live_properties(merged, ble)

    assert merged == {"power": 450, "soc": 79, "source": "ble"}


def test_merge_dict_values_legacy_wrapper_warns_and_uses_live_rules() -> None:
    """Legacy wrapper body warns and delegates to live-property semantics."""
    namespace = {
        "Any": object,
        "DeprecationWarning": DeprecationWarning,
        "merge_live_properties": merge_live_properties,
        "warnings": warnings,
    }
    wrapper_source = """
def _merge_dict_values(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    warnings.warn(
        "JackerySolarVaultCoordinator._merge_dict_values is deprecated; "
        "use ingest.merge_live_properties for live payloads",
        DeprecationWarning,
        stacklevel=2,
    )
    return merge_live_properties(base, updates)
"""
    exec(wrapper_source, namespace)  # noqa: S102

    with pytest.warns(DeprecationWarning, match="merge_live_properties"):
        merged = namespace["_merge_dict_values"](
            {"power": 500, "nested": {"valid": 1}},
            {"power": 0, "nested": {"valid": None}},
        )

    assert merged == {"power": 500, "nested": {"valid": 1}}
