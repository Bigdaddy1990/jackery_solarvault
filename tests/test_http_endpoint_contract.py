"""Contract tests for the reverse-engineered Jackery HTTP endpoint CSV."""

import ast
import importlib.util
from pathlib import Path
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
REGISTRY_PATH = (
    ROOT
    / "custom_components"
    / "jackery_solarvault"
    / "client"
    / "endpoint_registry.py"
)
DOC_PATH = ROOT / "docs" / "HTTP_ENDPOINT_MAPPING.md"
API_PATH = ROOT / "custom_components" / "jackery_solarvault" / "client" / "api.py"
ENDPOINTS_PATH = (
    ROOT / "custom_components" / "jackery_solarvault" / "client" / "_endpoints"
)


def _load_registry() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "endpoint_registry_contract", REGISTRY_PATH
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _client_methods() -> set[str]:
    methods: set[str] = set()
    for path in [API_PATH, *ENDPOINTS_PATH.glob("*.py")]:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        methods.update(
            node.name
            for node in ast.walk(tree)
            if isinstance(node, ast.AsyncFunctionDef)
        )
    return methods


def test_all_non_exempt_csv_endpoints_have_client_methods() -> None:
    """Ensure every required CSV endpoint is implemented."""
    registry = _load_registry()
    mapping = registry.load_csv_endpoint_mapping()

    missing = sorted(
        path
        for path, endpoint in mapping.items()
        if not endpoint.exempted and not endpoint.implemented
    )

    assert not missing


def test_endpoint_mapping_references_existing_client_methods() -> None:
    """Ensure registry method names stay in sync with the client."""
    registry = _load_registry()
    mapping = registry.load_csv_endpoint_mapping()
    client_methods = _client_methods()

    stale = sorted(
        (path, endpoint.client_method)
        for path, endpoint in mapping.items()
        if endpoint.client_method is not None
        and endpoint.client_method not in client_methods
    )

    assert not stale


def test_mobile_only_endpoints_are_explicit_exemptions() -> None:
    """Ensure mobile-only gaps are deliberate exemptions."""
    registry = _load_registry()
    mapping = registry.load_csv_endpoint_mapping()

    assert registry.EXEMPT_ENDPOINTS == {
        "auth/generatedJwt": (
            "mobile app push JWT; HA does not register mobile push identity"
        ),
        "device/bind/qrcode": (
            "mobile QR pairing; HA config flow uses bindKey/manual credentials"
        ),
        "device/bluetoothKey": (
            "mobile BLE key fetch; HA captures bluetoothKey from MQTT/discovery"
        ),
    }
    assert all(mapping[path].exempted for path in registry.EXEMPT_ENDPOINTS)


def test_documentation_is_generated_from_endpoint_mapping() -> None:
    """Ensure endpoint documentation is generated from the registry."""
    registry = _load_registry()
    expected = registry.render_endpoint_mapping_markdown(
        registry.load_csv_endpoint_mapping()
    )

    assert DOC_PATH.read_text(encoding="utf-8") == expected
