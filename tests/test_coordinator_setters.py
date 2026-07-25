"""Behavioral tests for device-setter command contracts.

Each setter must emit the correct command frame (action id + coerced body
fields) and apply an optimistic local patch. The command-dispatch layer
(BLE/MQTT/HTTP fallback) is a separately-tested seam and is mocked here so the
tests assert the setter's own contract: value coercion and the command it
requests, plus the resulting local state — not the transport call order.
"""

from typing import Any, cast  # ruff:ignore[unsorted-imports]
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault import coordinator as coord_mod
from custom_components.jackery_solarvault.const import (
    PAYLOAD_PROPERTIES,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from homeassistant.helpers.update_coordinator import UpdateFailed

_DEVICE = "dev-1"


def _coordinator(props: dict[str, Any] | None = None) -> Any:  # ruff:ignore[any-type]
    """Bare coordinator with a mocked command-dispatch seam."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    shell = cast("Any", coordinator)
    shell.data = {_DEVICE: {PAYLOAD_PROPERTIES: dict(props or {})}}
    shell._shutdown_started = False
    shell._property_overrides = {}
    shell._live_property_key_monotonic = {}
    shell._listeners = {}
    shell._async_publish_command_ble_first = AsyncMock()
    return shell


def _sent_body(coordinator: Any) -> dict[str, Any]:  # ruff:ignore[any-type]
    """Return the body_fields the setter asked the dispatcher to send."""
    return coordinator._async_publish_command_ble_first.await_args.kwargs["body_fields"]


@pytest.mark.asyncio()
async def test_set_eps_encodes_boolean_as_int() -> None:
    """EPS enable is transmitted as 1 and mirrored into local properties."""
    coordinator = _coordinator()

    await coordinator.async_set_eps(_DEVICE, enabled=True)

    assert _sent_body(coordinator) == {coord_mod.FIELD_SW_EPS: 1}


@pytest.mark.asyncio()
async def test_set_eps_disabled_sends_zero() -> None:
    """Disabling EPS transmits 0."""
    coordinator = _coordinator()

    await coordinator.async_set_eps(_DEVICE, enabled=False)

    assert _sent_body(coordinator) == {coord_mod.FIELD_SW_EPS: 0}


@pytest.mark.asyncio()
async def test_set_work_model_coerces_and_patches_local_state() -> None:
    """Work-model writes coerce to int and land in local properties."""
    coordinator = _coordinator()

    await coordinator.async_set_work_model(_DEVICE, mode=2)

    assert _sent_body(coordinator) == {coord_mod.FIELD_WORK_MODEL: 2}
    assert (
        coordinator.data[_DEVICE][PAYLOAD_PROPERTIES][coord_mod.FIELD_WORK_MODEL] == 2
    )


@pytest.mark.asyncio()
async def test_set_standby_maps_enabled_to_sleep_code() -> None:
    """Standby maps enabled->SLEEP(1) and disabled->POWER_ON(2)."""
    coordinator = _coordinator()

    await coordinator.async_set_standby(_DEVICE, enabled=True)
    assert _sent_body(coordinator) == {coord_mod.FIELD_AUTO_STANDBY: 1}

    await coordinator.async_set_standby(_DEVICE, enabled=False)
    assert _sent_body(coordinator) == {coord_mod.FIELD_AUTO_STANDBY: 2}


@pytest.mark.asyncio()
async def test_set_auto_standby_hours_sends_boolean_flag() -> None:
    """Any positive hour count becomes the boolean auto-standby flag 1."""
    coordinator = _coordinator()

    await coordinator.async_set_auto_standby_hours(_DEVICE, hours=5)

    body = _sent_body(coordinator)
    assert body[coord_mod.FIELD_IS_AUTO_STANDBY] == 1


@pytest.mark.asyncio()
async def test_set_off_grid_shutdown_encodes_boolean() -> None:
    """Off-grid shutdown enable is encoded as 1."""
    coordinator = _coordinator()

    await coordinator.async_set_off_grid_shutdown(_DEVICE, enabled=True)

    assert _sent_body(coordinator) == {coord_mod.FIELD_OFF_GRID_DOWN: 1}


@pytest.mark.asyncio()
async def test_set_max_feed_grid_mirrors_grid_standard_field() -> None:
    """Max-feed-grid writes carry both the feed and grid-standard fields."""
    coordinator = _coordinator()

    await coordinator.async_set_max_feed_grid(_DEVICE, watts=250)

    assert _sent_body(coordinator) == {coord_mod.FIELD_MAX_FEED_GRID: 250}


@pytest.mark.asyncio()
async def test_set_soc_limits_requires_at_least_one_side() -> None:
    """Calling with neither limit is rejected before any command is sent."""
    coordinator = _coordinator()

    with pytest.raises(UpdateFailed):
        await coordinator.async_set_soc_limits(_DEVICE)

    coordinator._async_publish_command_ble_first.assert_not_awaited()


@pytest.mark.asyncio()
async def test_set_soc_limits_fills_missing_side_from_current_state() -> None:
    """Only the charge side supplied -> discharge is filled from current props."""
    coordinator = _coordinator(
        {coord_mod.FIELD_SOC_DISCHG_LIMIT: 20},
    )

    await coordinator.async_set_soc_limits(_DEVICE, charge_limit=90)

    body = _sent_body(coordinator)
    assert body[coord_mod.FIELD_SOC_CHG_LIMIT] == 90
    assert body[coord_mod.FIELD_SOC_DISCHG_LIMIT] == 20


@pytest.mark.asyncio()
async def test_set_soc_limits_rejects_out_of_range_charge() -> None:
    """An explicit charge limit above 100 is invalid and blocks the frame."""
    coordinator = _coordinator()

    with pytest.raises(UpdateFailed):
        await coordinator.async_set_soc_limits(_DEVICE, charge_limit=150)

    coordinator._async_publish_command_ble_first.assert_not_awaited()


@pytest.mark.asyncio()
async def test_custom_use_battery_derives_back_off_and_fills_lower() -> None:
    """Setting only the upper bound fills dl from state and derives bc = cl - 5."""
    coordinator = _coordinator({"dl": 10})

    await coordinator.async_portable_set_custom_use_battery(_DEVICE, charge_limit=80)

    assert _sent_body(coordinator) == {"dl": 10, "cl": 80, "bc": 75}


@pytest.mark.asyncio()
async def test_custom_use_battery_fills_upper_from_state() -> None:
    """Setting only the lower bound fills cl from state; bc tracks cl."""
    coordinator = _coordinator({"cl": 90})

    await coordinator.async_portable_set_custom_use_battery(_DEVICE, discharge_limit=20)

    assert _sent_body(coordinator) == {"dl": 20, "cl": 90, "bc": 85}


@pytest.mark.asyncio()
async def test_custom_use_battery_requires_a_bound() -> None:
    """Calling with neither bound is rejected before any frame is sent."""
    coordinator = _coordinator()

    with pytest.raises(UpdateFailed):
        await coordinator.async_portable_set_custom_use_battery(_DEVICE)

    coordinator._async_publish_command_ble_first.assert_not_awaited()


@pytest.mark.asyncio()
async def test_custom_use_battery_clamps_out_of_range_upper() -> None:
    """An explicit upper bound above 100 is clamped to the safe default (100)."""
    coordinator = _coordinator({"dl": 10})

    await coordinator.async_portable_set_custom_use_battery(_DEVICE, charge_limit=150)

    assert _sent_body(coordinator) == {"dl": 10, "cl": 100, "bc": 95}


@pytest.mark.asyncio()
async def test_custom_use_battery_ignores_unparseable_stored_side() -> None:
    """A non-numeric stored partner value falls back to the bound default."""
    coordinator = _coordinator({"cl": "nope"})

    await coordinator.async_portable_set_custom_use_battery(_DEVICE, discharge_limit=20)

    assert _sent_body(coordinator) == {"dl": 20, "cl": 100, "bc": 95}
