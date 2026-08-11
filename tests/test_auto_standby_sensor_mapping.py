"""Regression coverage for the Auto-Standby diagnostic mapping."""

from typing import Any

from custom_components.jackery_solarvault.const import (
    FIELD_AUTO_STANDBY,
    FIELD_IS_AUTO_STANDBY,
)
from custom_components.jackery_solarvault.sensor import SENSOR_DESCRIPTIONS


def test_auto_standby_diagnostic_reads_the_mode_field() -> None:
    """Auto-Standby mode reads ``autoStandby``, not the enable flag."""
    description = next(
        description
        for description in SENSOR_DESCRIPTIONS
        if description.key == "auto_standby"
    )
    payload: dict[str, Any] = {
        FIELD_AUTO_STANDBY: 2,
        FIELD_IS_AUTO_STANDBY: 1,
    }

    assert description.getter(payload) == 2
