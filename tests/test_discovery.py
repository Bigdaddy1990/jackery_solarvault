"""Unit tests for Jackery device discovery filters."""

from __future__ import annotations

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
    """String false markers must not be treated as cloud-only accessories."""
    assert JackerySolarVaultCoordinator._is_property_device_candidate({
        FIELD_BIND_KEY: 1,
        FIELD_DEV_TYPE: 3,
        FIELD_IS_CLOUD: "false",
        FIELD_MODEL_CODE: 3002,
    })
    assert not JackerySolarVaultCoordinator._is_property_device_candidate({
        FIELD_BIND_KEY: 1,
        FIELD_DEV_TYPE: 3,
        FIELD_IS_CLOUD: "true",
        FIELD_MODEL_CODE: 3002,
    })
    assert not JackerySolarVaultCoordinator._is_property_device_candidate({
        FIELD_BIND_KEY: 1,
        FIELD_DEV_TYPE: "3",
        FIELD_IS_CLOUD: "true",
        FIELD_MODEL_CODE: 3002,
    })


def test_property_device_candidate_parses_bind_key_false_markers() -> None:
    """String false bindKey markers must filter unsupported accessories."""
    assert not JackerySolarVaultCoordinator._is_property_device_candidate({
        FIELD_BIND_KEY: "false",
        FIELD_DEV_TYPE: 1,
        FIELD_MODEL_CODE: 3002,
    })
    assert JackerySolarVaultCoordinator._is_property_device_candidate({
        FIELD_BIND_KEY: "true",
        FIELD_DEV_TYPE: 1,
        FIELD_MODEL_CODE: 3002,
    })


def test_property_device_candidate_treats_empty_model_code_as_missing() -> None:
    """Empty modelCode without devModel is not enough for device/property."""
    assert not JackerySolarVaultCoordinator._is_property_device_candidate({
        FIELD_BIND_KEY: 1,
        FIELD_DEV_TYPE: 1,
        FIELD_MODEL_CODE: "",
    })
    assert JackerySolarVaultCoordinator._is_property_device_candidate({
        FIELD_BIND_KEY: 1,
        FIELD_DEV_TYPE: 1,
        FIELD_MODEL_CODE: "",
        FIELD_DEV_MODEL: "SolarVault",
    })
