"""Payload filters for the Jackery SolarVault integration.

Filters keep fields out of a payload section they do not belong to (for
example accessory-only or third-party MQTT fields in the main-device
properties). They never judge values; value rules live in :mod:`.guards` and
statistics safety in :mod:`.ingest`. Filters hold no Home Assistant state and
perform no I/O.
"""

from typing import Any, Final

from .const import (
    APP_SECTION_TODAY_ENERGY,
    FIELD_PV1,
    FIELD_PV2,
    FIELD_PV3,
    FIELD_PV4,
    FIELD_PV_PW,
    FIELD_THIRD_PARTY_MQTT_ENABLE,
    FIELD_THIRD_PARTY_MQTT_IP,
    FIELD_THIRD_PARTY_MQTT_PASSWORD,
    FIELD_THIRD_PARTY_MQTT_PORT,
    FIELD_THIRD_PARTY_MQTT_TOKEN,
    FIELD_THIRD_PARTY_MQTT_USERNAME,
    SUBDEVICE_ONLY_PROPERTY_KEYS,
)
from .ingest import is_periodic_section
from .util import safe_float

LIVE_PROPERTY_ALIAS_PAIRS: Final = (
    ("inPw", "inPower"),
    ("outPw", "outPower"),
    ("elecFreq", "frequency"),
    ("soc", "batterySoc"),
    # No ("batSoc", "soc"): batSoc is the main battery's internal SOC, soc the
    # system SOC (WIRING_REFERENCE.md). Aliasing them made soc flip 78 <-> 92 %.
    ("standbyPw", "standbyw"),
)
_MAIN_PROPERTY_EXCLUDE_KEYS: Final = SUBDEVICE_ONLY_PROPERTY_KEYS | {
    FIELD_THIRD_PARTY_MQTT_ENABLE,
    FIELD_THIRD_PARTY_MQTT_IP,
    FIELD_THIRD_PARTY_MQTT_PORT,
    FIELD_THIRD_PARTY_MQTT_USERNAME,
    FIELD_THIRD_PARTY_MQTT_PASSWORD,
    FIELD_THIRD_PARTY_MQTT_TOKEN,
}
_PV_CHANNEL_PROPERTY_KEYS: Final = (FIELD_PV1, FIELD_PV2, FIELD_PV3, FIELD_PV4)


def sync_property_aliases(
    values: dict[str, Any],
    alias_pairs: tuple[tuple[str, str], ...] | list[tuple[str, str]],
) -> dict[str, Any]:
    """Mirror equivalent app property names after merge operations."""
    synced = dict(values)
    for left, right in alias_pairs:
        left_value = synced.get(left)
        right_value = synced.get(right)
        if left_value is None and right_value is not None:
            synced[left] = right_value
        elif right_value is None and left_value is not None:
            synced[right] = left_value
    return synced


def _is_section_key(key: str) -> bool:
    """Return True for stats/trends/today sections that are not properties."""
    return is_periodic_section(key) or (
        key == APP_SECTION_TODAY_ENERGY
        or key.startswith(f"{APP_SECTION_TODAY_ENERGY}_")
    )


def sanitize_main_properties(props: dict[str, Any]) -> dict[str, Any]:
    """Remove accessory-only properties and normalize main-device PV fields.

    Accessory-only, third-party MQTT config, and stats/trends section keys are
    removed. Numeric PV channel scalars are normalized to PV dictionaries.
    """
    clean = {
        key: value
        for key, value in dict(props).items()
        if key not in _MAIN_PROPERTY_EXCLUDE_KEYS and not _is_section_key(key)
    }
    for channel_key in _PV_CHANNEL_PROPERTY_KEYS:
        channel_value = clean.get(channel_key)
        if (
            isinstance(channel_value, dict)
            or channel_value is None
            or isinstance(channel_value, bool)
            or safe_float(channel_value) is None
        ):
            continue
        clean[channel_key] = {FIELD_PV_PW: channel_value}
    return sync_property_aliases(clean, LIVE_PROPERTY_ALIAS_PAIRS)
