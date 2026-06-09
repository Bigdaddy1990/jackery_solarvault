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
    """
    Determine whether a payload section contains periodic (long-term) data.
    
    Matches either a known periodic prefix exactly or keys that start with a recognized prefix followed by an underscore (for example, `device_pv_stat_day`).
    
    Parameters:
        section_key (str): Payload section identifier to test.
    
    Returns:
        `true` if the section_key represents a periodic section, `false` otherwise.
    """
    return any(
        section_key == prefix or section_key.startswith(f"{prefix}_")
        for prefix in PERIODIC_SECTION_PREFIXES
    )


def _is_blankable(value: object) -> bool:
    """
    Determine whether a value should be treated as blank and ignored when merging live properties.
    
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
    """
    Merge live device-property fields without ever blanking populated keys.
    
    Perform a recursive, update-wins merge where empty or blanking values in
    `update` do not overwrite populated values in `base`. Dictionary values are
    merged recursively; non-dictionary values from `update` replace those in
    `base` unless the `update` value is blank (None, empty/whitespace string, or
    empty list/dict) and the corresponding `base` value is populated.
    
    Parameters:
        base (dict[str, Any]): Original live properties to merge into.
        update (dict[str, Any]): Incoming update to apply; blanking values in this
            mapping will not replace populated values from `base`.
    
    Returns:
        dict[str, Any]: A new mapping containing the merged properties with the
        "never blank populated keys" rule enforced.
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
