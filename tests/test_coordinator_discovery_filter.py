"""Regression: third-party devices must never enter the property loop.

A bound Shelly appears in the Jackery legacy ``bind/list`` with ``bindKey=0``
and no Jackery model metadata. It must not become a ``/v1/device/property``
candidate — that endpoint keys on a Jackery ``deviceId`` and rejects a Shelly
native id (``5c...``) with cloud ``code=10600`` on every poll cycle. The
``system/list`` path already filters such entries; this pins the same filter
on the legacy path.
"""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault.const import (
    FIELD_BIND_KEY,
    FIELD_CODE,
    FIELD_DATA,
    FIELD_DEV_ID,
    FIELD_DEV_SN,
    FIELD_MODEL_CODE,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
    subdevice_dev_type,
)


def _discovery_coordinator(
    *,
    systems: list[Any],
    legacy: list[Any],
) -> JackerySolarVaultCoordinator:
    """Build a bare coordinator whose discovery boundaries are mocked."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._device_index = {}  # ruff: ignore[private-member-access]
    coordinator._pending_discovery_parent_removals = set()  # ruff: ignore[private-member-access]
    cast("Any", coordinator).api = SimpleNamespace(
        async_get_system_list=AsyncMock(return_value=systems),
        async_list_devices_legacy=AsyncMock(return_value=legacy),
        async_sync_smart_accessories=AsyncMock(return_value=None),
        async_get_accessories_list=AsyncMock(return_value=[]),
        last_system_list_response={FIELD_CODE: 0, FIELD_DATA: systems},
        last_legacy_device_list_response={FIELD_CODE: 0, FIELD_DATA: legacy},
    )
    mutable = cast("Any", coordinator)
    mutable._async_save_discovery_cache = AsyncMock()  # ruff: ignore[private-member-access]
    mutable._schedule_background_once = lambda *_args, **_kwargs: None  # ruff: ignore[private-member-access]
    return coordinator


def test_shelly_textual_device_type_uses_scan_name_without_schema_rejection() -> None:
    """A documented Shelly deviceType may fall back to its proven scanName."""
    rejection_reasons: list[str] = []

    dev_type = subdevice_dev_type(
        {"deviceType": "emeter", "scanName": "shellypro3em"},
        rejection_callback=rejection_reasons.append,
    )

    assert dev_type == 3
    assert rejection_reasons == []


def test_unknown_textual_device_type_records_schema_rejection() -> None:
    """An unresolved textual type remains visible in rejection diagnostics."""
    rejection_reasons: list[str] = []

    dev_type = subdevice_dev_type(
        {"deviceType": "unknown-device-family", "scanName": "unknown"},
        rejection_callback=rejection_reasons.append,
    )

    assert dev_type is None
    assert rejection_reasons == ["subdevice_dev_type_value_error"]


@pytest.mark.asyncio
async def test_legacy_shelly_is_not_a_property_device() -> None:
    """A bound Shelly (bindKey=0, no model metadata) stays out of the index."""
    shelly = {FIELD_BIND_KEY: 0, FIELD_DEV_SN: "5c013b048e3c"}
    portable = {
        FIELD_BIND_KEY: 1,
        FIELD_MODEL_CODE: "3002",
        FIELD_DEV_ID: "portable-1",
    }
    coordinator = _discovery_coordinator(systems=[], legacy=[shelly, portable])

    await coordinator.async_discover()

    assert "5c013b048e3c" not in coordinator._device_index  # ruff: ignore[private-member-access]
    assert "portable-1" in coordinator._device_index  # ruff: ignore[private-member-access]


@pytest.mark.asyncio
async def test_legacy_real_portable_still_discovered() -> None:
    """A genuine Jackery portable from bind/list is still indexed as a device."""
    portable = {
        FIELD_BIND_KEY: 1,
        FIELD_MODEL_CODE: "E1000",
        FIELD_DEV_ID: "explorer-9",
    }
    coordinator = _discovery_coordinator(systems=[], legacy=[portable])

    await coordinator.async_discover()

    assert list(coordinator._device_index) == ["explorer-9"]  # ruff: ignore[private-member-access]


@pytest.mark.asyncio
async def test_empty_discovery_cycle_keeps_previous_populated_index() -> None:
    """Explicit empty outer lists are valid fallback, not removal evidence."""
    portable = {
        FIELD_BIND_KEY: 1,
        FIELD_MODEL_CODE: "E1000",
        FIELD_DEV_ID: "explorer-9",
    }
    coordinator = _discovery_coordinator(systems=[], legacy=[portable])
    await coordinator.async_discover()
    assert list(coordinator._device_index) == ["explorer-9"]  # ruff: ignore[private-member-access]

    cast("Any", coordinator).api.async_get_system_list = AsyncMock(return_value=[])
    cast("Any", coordinator).api.async_list_devices_legacy = AsyncMock(return_value=[])
    cast("Any", coordinator).api.last_system_list_response = {
        FIELD_CODE: 0,
        FIELD_DATA: [],
    }
    cast("Any", coordinator).api.last_legacy_device_list_response = {
        FIELD_CODE: 0,
        FIELD_DATA: [],
    }

    await coordinator.async_discover()

    assert list(coordinator._device_index) == ["explorer-9"]  # ruff: ignore[private-member-access]
