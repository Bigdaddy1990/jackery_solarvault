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
    """Return True when a payload section holds periodic long-term values.

    Matches both the bare prefix and the ``{prefix}_{date_type}`` section keys
    (e.g. ``device_pv_stat_day``) the coordinator stores per period.
    """
    return any(
        section_key == prefix or section_key.startswith(f"{prefix}_")
        for prefix in PERIODIC_SECTION_PREFIXES
    )


def _is_blankable(value: object) -> bool:
    """Return True for values that must never overwrite a populated field."""
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return isinstance(value, (list, dict)) and not value


def merge_live_properties(
    base: dict[str, Any],
    update: dict[str, Any],
) -> dict[str, Any]:
    """Merge live device-property fields without ever blanking populated keys.

    Recursive, update-wins merge with one rule a plain dict merge lacks: an
    empty/``None`` value in ``update`` never replaces a populated value in
    ``base``. A sparse MQTT/BLE frame (omitting or nulling fields the device did
    not report this tick) therefore cannot wipe the live picture delivered by
    another transport such as the HTTP property poll.
    """
    merged: dict[str, Any] = dict(base)
    for key, value in update.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = merge_live_properties(current, value)
        elif _is_blankable(value) and not _is_blankable(current):
            continue
        else:
            merged[key] = value
    return merged
