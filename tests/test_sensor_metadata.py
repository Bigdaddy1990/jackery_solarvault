"""Regression tests for sensor period metadata."""

from collections import Counter

from custom_components.jackery_solarvault import sensor as sensor_module
from custom_components.jackery_solarvault.const import DATE_TYPE_DAY, DATE_TYPE_WEEK
from custom_components.jackery_solarvault.sensor import (
    PORTABLE_SENSOR_DESCRIPTIONS,
    SENSOR_DESCRIPTIONS,
    STAT_DESCRIPTIONS,
)
from homeassistant.helpers.entity import EntityCategory

_SPECIAL_DAY_SIBLINGS = {
    "pv_week_energy": "device_today_pv_energy",
    "home_week_energy": "today_home_load_energy",
    "device_ongrid_input_week_energy": "device_today_ongrid_input",
    "device_ongrid_output_week_energy": "device_today_ongrid_output",
    "battery_charge_week_energy": "device_today_battery_charge",
    "battery_discharge_week_energy": "device_today_battery_discharge",
}


def test_week_energy_descriptions_have_day_siblings() -> None:
    """Week energy metrics keep a day-period sibling without duplicate entities."""
    descriptions = {description.key: description for description in STAT_DESCRIPTIONS}
    week_keys = [
        description.key
        for description in STAT_DESCRIPTIONS
        if description.key.endswith("_week_energy")
    ]

    assert week_keys
    for week_key in week_keys:
        day_key = _SPECIAL_DAY_SIBLINGS.get(
            week_key,
            week_key.replace("_week_energy", "_day_energy"),
        )
        assert descriptions[week_key].reset_period == DATE_TYPE_WEEK
        assert day_key in descriptions
        assert descriptions[day_key].reset_period == DATE_TYPE_DAY


def test_stat_period_metadata_has_no_duplicate_source_period_pairs() -> None:
    """A section/stat/reset-period tuple may only produce one HA entity."""
    period_pairs = [
        (description.section, description.stat_key, description.reset_period)
        for description in STAT_DESCRIPTIONS
        if description.reset_period is not None
    ]
    duplicates = [pair for pair, count in Counter(period_pairs).items() if count > 1]

    assert duplicates == []


def test_default_power_is_a_disabled_diagnostic() -> None:
    """``default_power`` must not ship enabled while its field is not on the wire.

    ``defaultPw`` is a SystemBody field other models report; the SolarVault
    3 Pro Max never sends it, so an enabled-by-default entity would be
    permanently ``unknown`` (B4 2026-07-03). ``JackerySensor`` maps the
    DIAGNOSTIC category to ``entity_registry_enabled_default=False``.
    """
    description = next(
        description
        for description in SENSOR_DESCRIPTIONS
        if description.key == "default_power"
    )

    assert description.entity_category == EntityCategory.DIAGNOSTIC


def test_sensor_descriptions_never_use_config_category() -> None:
    """Home Assistant rejects ``EntityCategory.CONFIG`` on SensorEntity."""
    offenders: list[str] = []
    for group_name, descriptions in vars(sensor_module).items():
        if not group_name.endswith("_DESCRIPTIONS") or not isinstance(
            descriptions,
            tuple,
        ):
            continue
        offenders.extend(
            f"{group_name}:{description.key}"
            for description in descriptions
            if getattr(description, "entity_category", None) == EntityCategory.CONFIG
        )

    assert offenders == []


def test_portable_setting_mirror_sensors_are_diagnostics_without_statistics() -> None:
    """Read-only mirrors of settings must not become recorder statistic sensors."""
    descriptions = {
        description.key: description for description in PORTABLE_SENSOR_DESCRIPTIONS
    }

    for key in (
        "charge_limit",
        "discharge_limit",
        "power_mode",
        "power_source_selector",
        "ups_mode",
        "wifi_switch_status",
        "auto_standby_timer",
    ):
        description = descriptions[key]
        assert description.entity_category == EntityCategory.DIAGNOSTIC
        assert description.state_class is None
