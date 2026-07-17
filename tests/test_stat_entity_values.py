"""Regression tests for statistic entity value passthrough."""

from types import SimpleNamespace
from typing import Any, cast

import pytest

from custom_components.jackery_solarvault.sensor import (
    STAT_DESCRIPTIONS,
    JackeryStatSensor,
)

_DEVICE_ID = "dev-1"
_STAT_KEY = "device_today_pv_energy"
_NEGATIVE_KWH = -1.5


def _stat_sensor() -> JackeryStatSensor:
    description = next(desc for desc in STAT_DESCRIPTIONS if desc.key == _STAT_KEY)
    sensor = JackeryStatSensor.__new__(JackeryStatSensor)
    mutable = cast("Any", sensor)
    mutable.coordinator = SimpleNamespace(
        data={
            _DEVICE_ID: {
                description.section: {
                    description.stat_key: _NEGATIVE_KWH,
                },
            },
        },
    )
    mutable.hass = SimpleNamespace(config=SimpleNamespace(time_zone="UTC"))
    mutable._device_id = _DEVICE_ID  # ruff:ignore[private-member-access]
    mutable.entity_description = description
    mutable._reset_period = description.reset_period  # ruff:ignore[private-member-access]
    mutable._cached_native_value = None  # ruff:ignore[private-member-access]
    mutable._cached_attrs = {}  # ruff:ignore[private-member-access]
    mutable._cached_source_section = description.section  # ruff:ignore[private-member-access]
    return sensor


def test_stat_entity_does_not_clamp_negative_period_values() -> None:
    """Stats/trends quality decisions belong upstream, not in the entity."""
    sensor = _stat_sensor()

    sensor._refresh_cache()  # ruff:ignore[private-member-access]

    assert sensor.native_value == pytest.approx(_NEGATIVE_KWH)
