"""HTTP system-shadow surfaces SystemBody config into main properties.

Owner escalation 2026-07-05: diagnostic sensors (CT-Status, Systemstatus,
Netzstatus/-zustand, Max. System-Ein/Ausgangsleistung, Energieplan-Leistung,
Funktions-Flags) stayed "Unbekannt" whenever cloud MQTT was down. Root cause:
the HTTP system-shadow body IS ``SystemBody`` and carries these keys, but the
shadow merge routed it through ``_merge_subdevice_data``, which only mirrors
``SUBDEVICE_MAIN_MIRROR_KEYS`` into ``PAYLOAD_PROPERTIES`` — the
``SYSTEM_INFO_KEYS``-only fields were dropped. HTTP is the authoritative,
always-on source, so the system shadow must surface them like the MQTT
CombineData handler does.
"""

from typing import Any

from custom_components.jackery_solarvault.const import (
    FIELD_CT_STAT,
    FIELD_ENERGY_PLAN_PW,
    FIELD_FUNC_ENABLE,
    FIELD_GRID_STATE,
    FIELD_MAX_SYS_IN_PW,
    FIELD_MAX_SYS_OUT_PW,
    FIELD_ONGRID_STAT,
    FIELD_STAT,
    PAYLOAD_PROPERTIES,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

_DEVICE_ID = "dev-1"
_MAX_SYS_OUT_PW = 2500
_MAX_SYS_IN_PW = 2500
_ENERGY_PLAN_PW = 800
_FUNC_ENABLE = 768
_CT_STAT = 1
_STAT = 3
_ONGRID_STAT = 1
_GRID_STATE = 2
_EXISTING_SOC = 55


def _bare_coordinator() -> JackerySolarVaultCoordinator:
    """Create a coordinator shell for the property-merge path without HA setup."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._property_overrides = {}  # ruff:ignore[private-member-access]
    coordinator._system_info_cache = {}  # ruff:ignore[private-member-access]
    coordinator._system_info_cache_monotonic = {}  # ruff:ignore[private-member-access]
    return coordinator


def _system_body() -> dict[str, Any]:
    """A SystemBody shadow body carrying the SYSTEM_INFO-only fields."""
    return {
        FIELD_STAT: _STAT,
        FIELD_ONGRID_STAT: _ONGRID_STAT,
        FIELD_CT_STAT: _CT_STAT,
        FIELD_GRID_STATE: _GRID_STATE,
        FIELD_ENERGY_PLAN_PW: _ENERGY_PLAN_PW,
        FIELD_MAX_SYS_OUT_PW: _MAX_SYS_OUT_PW,
        FIELD_MAX_SYS_IN_PW: _MAX_SYS_IN_PW,
        FIELD_FUNC_ENABLE: _FUNC_ENABLE,
    }


def test_system_info_fields_reach_main_properties() -> None:
    """SystemBody-only shadow fields land in PAYLOAD_PROPERTIES and are cached."""
    coordinator = _bare_coordinator()
    working: dict[str, Any] = {}

    merged = coordinator._merge_system_info_fields(  # ruff:ignore[private-member-access]
        _DEVICE_ID,
        working,
        _system_body(),
    )

    assert merged is True
    props = working[PAYLOAD_PROPERTIES]
    assert props[FIELD_MAX_SYS_OUT_PW] == _MAX_SYS_OUT_PW
    assert props[FIELD_MAX_SYS_IN_PW] == _MAX_SYS_IN_PW
    assert props[FIELD_CT_STAT] == _CT_STAT
    assert props[FIELD_STAT] == _STAT
    assert props[FIELD_ONGRID_STAT] == _ONGRID_STAT
    assert props[FIELD_GRID_STATE] == _GRID_STATE
    assert props[FIELD_ENERGY_PLAN_PW] == _ENERGY_PLAN_PW
    assert props[FIELD_FUNC_ENABLE] == _FUNC_ENABLE
    # Cached so the fields survive a later MQTT-only cycle.
    assert coordinator._system_info_cache[_DEVICE_ID][FIELD_CT_STAT] == _CT_STAT  # ruff:ignore[private-member-access]


def test_system_info_merge_preserves_existing_properties() -> None:
    """The merge fills SystemBody keys without blanking existing main props."""
    coordinator = _bare_coordinator()
    working: dict[str, Any] = {PAYLOAD_PROPERTIES: {"soc": _EXISTING_SOC}}

    coordinator._merge_system_info_fields(_DEVICE_ID, working, _system_body())  # ruff:ignore[private-member-access]

    props = working[PAYLOAD_PROPERTIES]
    assert props["soc"] == _EXISTING_SOC
    assert props[FIELD_MAX_SYS_OUT_PW] == _MAX_SYS_OUT_PW


def test_system_info_merge_noop_without_fields() -> None:
    """A shadow body with no SystemBody info fields is a no-op."""
    coordinator = _bare_coordinator()
    working: dict[str, Any] = {}

    merged = coordinator._merge_system_info_fields(  # ruff:ignore[private-member-access]
        _DEVICE_ID,
        working,
        {"someUnrelatedKey": 1},
    )

    assert merged is False
    assert PAYLOAD_PROPERTIES not in working
