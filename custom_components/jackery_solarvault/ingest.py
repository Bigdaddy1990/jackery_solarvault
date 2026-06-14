"""Central payload ingestion gate for the Jackery SolarVault integration.

Every transport — HTTP/REST polling, cloud MQTT push, local MQTT and BLE —
funnels its decoded payloads through this single module so the data path is
identical regardless of how a frame arrived. The gate enforces two rules:

* **Live device-property fields** are merged with :func:`merge_live_properties`,
  which never blanks a populated field with an empty/``None`` value. A sparse
  push frame (MQTT/BLE) can refresh or add fields but can never wipe the live
  values another transport (the HTTP poll) already delivered. This is what keeps
  live state stable when MQTT/BLE are active.
* **Periodic long-term values** (cumulative energy stat/trend sections) are
  identified by section prefix so the coordinator routes them to the HA recorder
  instead of mixing them into live state.

The gate holds no Home Assistant dependencies and performs no transport I/O; it
is pure data normalization so it stays unit-testable and reusable by every
transport layer.
"""

from enum import StrEnum
from typing import Any

from .const import (
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_BATTERY_TRENDS,
    APP_SECTION_CT_STAT,
    APP_SECTION_HOME_STAT,
    APP_SECTION_HOME_TRENDS,
    APP_SECTION_PV_STAT,
    APP_SECTION_PV_TRENDS,
)


class TransportSource(StrEnum):
    """Origin transport of an ingested payload."""

    HTTP = "http"
    CLOUD_MQTT = "cloud_mqtt"
    LOCAL_MQTT = "local_mqtt"
    BLE = "ble"


#: Section-key prefixes that carry periodic (long-term) statistics/trends.
#: Everything else in a device payload is treated as live property state.
PERIODIC_SECTION_PREFIXES: frozenset[str] = frozenset({
    APP_SECTION_PV_STAT,
    APP_SECTION_HOME_STAT,
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_CT_STAT,
    APP_SECTION_PV_TRENDS,
    APP_SECTION_HOME_TRENDS,
    APP_SECTION_BATTERY_TRENDS,
})


def is_periodic_section(section_key: str) -> bool:
    """Return whether a section contains periodic long-term data."""
    return any(
        section_key == prefix or section_key.startswith(f"{prefix}_")
        for prefix in PERIODIC_SECTION_PREFIXES
    )


def _is_unconfirmed_zero(current: object, value: object) -> bool:
    """Return true when an incoming zero would erase a valid live value."""
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and value == 0
        and isinstance(current, int | float)
        and not isinstance(current, bool)
        and current > 0
    )


def _is_blankable(value: object) -> bool:
    """Determine whether a value should be treated as blank.

    A value is considered blank if it is:
    - `None`
    - a string that is empty or contains only whitespace
    - an empty `list` or `dict`

    Returns:
        `True` if the value is blank as described above, `False` otherwise.
    """
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return isinstance(value, (list, dict)) and not value


def merge_live_properties(
    base: dict[str, Any],
    update: dict[str, Any],
) -> dict[str, Any]:
    """Merge live properties without blanking populated base values.

    Incoming dictionaries merge recursively. Incoming ``None``, empty strings,
    empty containers, and unconfirmed numeric ``0`` values do not replace
    populated values already accepted from another transport.
    """
    merged: dict[str, Any] = dict(base)
    for key, value in update.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = merge_live_properties(current, value)
        elif (
            _is_blankable(value) and not _is_blankable(current)
        ) or _is_unconfirmed_zero(current, value):
            continue
        else:
            merged[key] = value
    return merged
