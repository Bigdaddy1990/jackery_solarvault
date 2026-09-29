"""Backfilled day curves reach every day sensor that documents them as source.

Live 2026-09-28: the Energy Dashboard grid import "Netzbezug heute" missed
12.-18.09. and every home_trends day stayed "unmapped" - the backfill only
wrote sensors whose *primary* section was ``<source>_day`` and ignored the
documented ``fallback_sources`` of the same day sensors.
"""

from typing import TYPE_CHECKING, Any, cast

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault.const import (
    APP_DEVICE_STAT_BATTERY_TO_GRID,
    APP_DEVICE_STAT_ONGRID_TO_BATTERY,
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_HOME_STAT,
    APP_SECTION_HOME_TRENDS,
    APP_STAT_TOTAL_HOME_ENERGY,
    APP_STAT_TOTAL_IN_GRID_ENERGY,
    DATE_TYPE_DAY,
    DOMAIN,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from homeassistant.helpers import entity_registry as er

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_DEVICE = "dev"
_KEYS = (
    "today_grid_import_energy",
    "device_today_ongrid_input",
    "today_home_load_energy",
    "device_today_ongrid_to_battery",
    "device_today_battery_to_ongrid",
)


def _coordinator(hass: HomeAssistant) -> Any:
    entry = MockConfigEntry(domain=DOMAIN, data={})
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for key in _KEYS:
        registry.async_get_or_create(
            "sensor",
            DOMAIN,
            f"{_DEVICE}_{key}",
            config_entry=entry,
            suggested_object_id=key,
        )
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    cast("Any", coordinator).hass = hass
    return coordinator


@pytest.mark.parametrize(
    ["section", "stat_key", "expected"],
    [
        [
            APP_SECTION_HOME_STAT,
            APP_STAT_TOTAL_IN_GRID_ENERGY,
            {"today_grid_import_energy", "device_today_ongrid_input"},
        ],
        [
            APP_SECTION_HOME_TRENDS,
            APP_STAT_TOTAL_HOME_ENERGY,
            {"today_home_load_energy"},
        ],
        [
            APP_SECTION_BATTERY_STAT,
            APP_DEVICE_STAT_ONGRID_TO_BATTERY,
            {"device_today_ongrid_to_battery"},
        ],
        [
            APP_SECTION_BATTERY_STAT,
            APP_DEVICE_STAT_BATTERY_TO_GRID,
            {"device_today_battery_to_ongrid"},
        ],
    ],
)
def test_day_curve_targets_include_documented_fallback_sensors(
    hass: HomeAssistant, section: str, stat_key: str, expected: set[str]
) -> None:
    """Each backfilled metric reaches the day sensors that read it."""
    coordinator = _coordinator(hass)

    targets = set(
        coordinator._energy_statistic_targets(_DEVICE, section, stat_key)  # ruff: ignore[private-member-access]
    )

    assert targets == {(f"sensor.{key}", DATE_TYPE_DAY) for key in expected}
