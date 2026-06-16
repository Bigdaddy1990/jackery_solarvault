"""Unit tests for Jackery device discovery filters."""

import json
from pathlib import Path
import re
from typing import Any

from custom_components.jackery_solarvault.const import (
    FIELD_BIND_KEY,
    FIELD_DEV_MODEL,
    FIELD_DEV_TYPE,
    FIELD_IS_CLOUD,
    FIELD_MODEL_CODE,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)


def test_property_device_candidate_parses_cloud_marker_strings() -> None:
    """String false markers must not be treated as cloud-marked accessories."""
    assert JackerySolarVaultCoordinator._is_property_device_candidate({  # noqa: S101, SLF001
        FIELD_BIND_KEY: 1,
        FIELD_DEV_TYPE: 3,
        FIELD_IS_CLOUD: "false",
        FIELD_MODEL_CODE: 3002,
    })
    assert not JackerySolarVaultCoordinator._is_property_device_candidate({  # noqa: S101, SLF001
        FIELD_BIND_KEY: 1,
        FIELD_DEV_TYPE: 3,
        FIELD_IS_CLOUD: "true",
        FIELD_MODEL_CODE: 3002,
    })
    assert not JackerySolarVaultCoordinator._is_property_device_candidate({  # noqa: S101, SLF001
        FIELD_BIND_KEY: 1,
        FIELD_DEV_TYPE: "3",
        FIELD_IS_CLOUD: "true",
        FIELD_MODEL_CODE: 3002,
    })


def test_property_device_candidate_parses_bind_key_false_markers() -> None:
    """String false bindKey markers must filter unsupported accessories."""
    assert not JackerySolarVaultCoordinator._is_property_device_candidate({  # noqa: S101, SLF001
        FIELD_BIND_KEY: "false",
        FIELD_DEV_TYPE: 1,
        FIELD_MODEL_CODE: 3002,
    })
    assert JackerySolarVaultCoordinator._is_property_device_candidate({  # noqa: S101, SLF001
        FIELD_BIND_KEY: "true",
        FIELD_DEV_TYPE: 1,
        FIELD_MODEL_CODE: 3002,
    })


def test_property_device_candidate_treats_empty_model_code_as_missing() -> None:
    """Empty modelCode without devModel is not enough for device/property."""
    assert not JackerySolarVaultCoordinator._is_property_device_candidate({  # noqa: S101, SLF001
        FIELD_BIND_KEY: 1,
        FIELD_DEV_TYPE: 1,
        FIELD_MODEL_CODE: "",
    })
    assert JackerySolarVaultCoordinator._is_property_device_candidate({  # noqa: S101, SLF001
        FIELD_BIND_KEY: 1,
        FIELD_DEV_TYPE: 1,
        FIELD_MODEL_CODE: "",
        FIELD_DEV_MODEL: "SolarVault",
    })


ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = ROOT / "custom_components/jackery_solarvault/manifest.json"
QUALITY_SCALE_PATH = ROOT / "custom_components/jackery_solarvault/quality_scale.yaml"
DISCOVERY_DOC_PATHS = (
    ROOT / "README.md",
    ROOT / "custom_components/jackery_solarvault/README.md",
    ROOT / "docs/README.de.md",
    ROOT / "docs/README.es.md",
    ROOT / "docs/README.fr.md",
)
DISCOVERY_RULES = {
    "discovery",
    "discovery-update-info",
    "docs-data-update",
    "dynamic-devices",
}
LOCAL_DISCOVERY_KEYS = {"bluetooth", "dhcp", "mqtt", "zeroconf"}


def _manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text())


def _quality_rule_block(rule: str) -> str:
    text = QUALITY_SCALE_PATH.read_text()
    match = re.search(
        rf"^  {re.escape(rule)}:\n(?P<body>(?:    .+\n)+)",
        text,
        re.MULTILINE,
    )
    assert match is not None, f"missing quality-scale rule {rule}"  # noqa: S101
    return match.group("body")


def _quality_rule_status(rule: str) -> str:
    block = _quality_rule_block(rule)
    status_match = re.search(r"status: (\w+)", block)
    if status_match is not None:
        return status_match.group(1)
    value_match = re.search(r"^    (\w+)\s*$", block, re.MULTILINE)
    assert value_match is not None, f"missing quality-scale status for {rule}"  # noqa: S101
    return value_match.group(1)


def _manifest_discovery_tokens(manifest: dict[str, Any]) -> set[str]:
    tokens: set[str] = set()
    for item in manifest["bluetooth"]:
        tokens.add(item["service_uuid"])
        tokens.add(str(item["manufacturer_id"]))
    for item in manifest["dhcp"]:
        tokens.add(item["hostname"])
        tokens.add(item["macaddress"])
    tokens.update(manifest["zeroconf"])
    return tokens


def test_manifest_discovery_surfaces_are_inventory_complete() -> None:
    """Manifest must keep every active local discovery surface explicit."""
    manifest = _manifest()

    assert LOCAL_DISCOVERY_KEYS.issubset(manifest)  # noqa: S101
    assert manifest["bluetooth"] == [  # noqa: S101
        {
            "service_uuid": "0000bdee-0000-1000-8000-00805f9b34fb",
            "manufacturer_id": 18434,
            "connectable": True,
        },
    ]
    assert manifest["dhcp"] == [  # noqa: S101
        {"hostname": "solarvault*", "macaddress": "80F1B2*"},
        {"hostname": "jackery*", "macaddress": "80F1B2*"},
    ]
    assert manifest["mqtt"] == []  # noqa: S101
    assert manifest["zeroconf"] == ["_jackery-solarvault._tcp.local."]  # noqa: S101


def test_quality_scale_discovery_rules_match_manifest_surfaces() -> None:
    """Quality-scale evidence must not exempt discovery while manifest advertises it."""
    manifest = _manifest()
    active_discovery = LOCAL_DISCOVERY_KEYS.intersection(manifest)

    assert active_discovery == LOCAL_DISCOVERY_KEYS  # noqa: S101
    for rule in DISCOVERY_RULES:
        block = _quality_rule_block(rule).lower()
        assert _quality_rule_status(rule) == "done"  # noqa: S101
        assert "cloud" + "-only" not in block  # noqa: S101
        assert "no lan" + " discovery" not in block  # noqa: S101

    discovery_block = _quality_rule_block("discovery")
    for surface in LOCAL_DISCOVERY_KEYS:
        assert surface in discovery_block.lower()  # noqa: S101
    for token in _manifest_discovery_tokens(manifest):
        assert token in discovery_block  # noqa: S101


def test_discovery_documentation_matches_manifest_and_avoids_cloud_only() -> None:
    """User docs must describe local setup discovery when manifest enables it."""
    tokens = _manifest_discovery_tokens(_manifest())

    for path in DISCOVERY_DOC_PATHS:
        text = path.read_text()
        text_lower = text.lower()
        assert "cloud" + "-only" not in text_lower  # noqa: S101
        assert "cloud" + " only" not in text_lower  # noqa: S101
        for surface in LOCAL_DISCOVERY_KEYS:
            assert surface in text_lower, f"{path} does not mention {surface}"  # noqa: S101
        for token in tokens:
            assert token in text, f"{path} does not mention {token}"  # noqa: S101
