"""Regression tests for migrated storm-warning sensor descriptions."""

from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from custom_components.jackery_solarvault.const import (
    FIELD_MINS_INTERVAL,
    FIELD_WPC,
    FIELD_WPS,
    PAYLOAD_TASK_PLAN,
    PAYLOAD_WEATHER_PLAN,
)
from custom_components.jackery_solarvault.descriptions.sensor import SENSOR_DESCRIPTIONS

if TYPE_CHECKING:
    from custom_components.jackery_solarvault.sensor import JackerySensor
    from homeassistant.helpers.typing import StateType

_ROOT_WARNING_MINUTES = 90
_ROW_WARNING_MINUTES = 45


def _description_value(
    key: str,
    *,
    merged_properties: dict[str, object] | None = None,
    payload: dict[str, object] | None = None,
) -> StateType:
    """Evaluate one migrated sensor description against a minimal entity stub."""
    description = next(item for item in SENSOR_DESCRIPTIONS if item.key == key)
    assert description.value_fn is not None
    entity = cast(
        "JackerySensor",
        SimpleNamespace(
            merged_properties=merged_properties or {},
            payload=payload or {},
        ),
    )
    return description.value_fn(entity)


def test_storm_warning_minutes_reads_weather_plan_root_value() -> None:
    """Root-level weather-plan wpc is a real warning lead time."""
    value = _description_value(
        "storm_warning_minutes",
        payload={PAYLOAD_WEATHER_PLAN: {FIELD_WPC: _ROOT_WARNING_MINUTES}},
    )

    assert value == _ROOT_WARNING_MINUTES


def test_storm_warning_minutes_reads_weather_plan_row_value() -> None:
    """Per-storm minsInterval is used when the plan has no root lead time."""
    value = _description_value(
        "storm_warning_minutes",
        payload={
            PAYLOAD_WEATHER_PLAN: {
                "storm": [{FIELD_MINS_INTERVAL: _ROW_WARNING_MINUTES}],
            }
        },
    )

    assert value == _ROW_WARNING_MINUTES


def test_storm_warning_enabled_preserves_zero() -> None:
    """A merged disabled flag must not fall through to a stale enabled plan flag."""
    value = _description_value(
        "storm_warning_enabled",
        merged_properties={FIELD_WPS: 0},
        payload={PAYLOAD_WEATHER_PLAN: {FIELD_WPS: 1}},
    )

    assert value == 0


def test_storm_warning_minutes_preserves_zero() -> None:
    """An explicitly empty storm plan must not fall through to stale task minutes."""
    value = _description_value(
        "storm_warning_minutes",
        payload={
            PAYLOAD_WEATHER_PLAN: {"storm": []},
            PAYLOAD_TASK_PLAN: {FIELD_WPC: 120},
        },
    )

    assert value == 0
