"""Regression tests for the HTTP-primary live-property merge policy.

Live finding: both SOC sensors sat at 75 % for 8 hours while power values
stayed current. Root cause: ``_http_properties_with_live_overrides`` let
fresh MQTT/bridge stamps protect sparse live payload values from fresh HTTP.
HTTP/API is the authoritative live path; MQTT/BLE frames are incomplete
supplements and may only fill fields the HTTP snapshot omitted.
"""

from datetime import timedelta
from typing import TYPE_CHECKING

from custom_components.jackery_solarvault.const import (
    FIELD_BAT_SOC,
    FIELD_PV_PW,
    FIELD_SOC,
    PAYLOAD_PROPERTIES,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

if TYPE_CHECKING:
    import pytest

_NOW = 10_000.0
_STALE_AGE_SEC = 1_000.0
_FROZEN_SOC = 75
_FRESH_HTTP_SOC = 62
_LIVE_PV_W = 100
_HTTP_PV_W = 90

_ENTRY = {
    PAYLOAD_PROPERTIES: {
        FIELD_BAT_SOC: _FROZEN_SOC,
        FIELD_SOC: _FROZEN_SOC,
        FIELD_PV_PW: _LIVE_PV_W,
    },
}
_HTTP_PROPS = {
    FIELD_BAT_SOC: _FRESH_HTTP_SOC,
    FIELD_SOC: _FRESH_HTTP_SOC,
    FIELD_PV_PW: _HTTP_PV_W,
}


def _bare_coordinator(
    monkeypatch: pytest.MonkeyPatch,
) -> JackerySolarVaultCoordinator:
    """Create a coordinator shell for the override policy without HA setup."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._live_property_key_monotonic = {}  # ruff:ignore[private-member-access]
    coordinator._configured_update_interval = timedelta(seconds=15)  # ruff:ignore[private-member-access]
    monkeypatch.setattr(
        "custom_components.jackery_solarvault.coordinator.time.monotonic",
        lambda: _NOW,
    )
    return coordinator


def test_power_only_live_frames_do_not_override_http_properties(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fresh HTTP values win even when a supplemental pvPw frame is newer."""
    coordinator = _bare_coordinator(monkeypatch)
    coordinator._live_property_key_monotonic["dev-1"] = {FIELD_PV_PW: _NOW}  # ruff:ignore[private-member-access]

    guarded = coordinator._http_properties_with_live_overrides(  # ruff:ignore[private-member-access]
        "dev-1",
        _ENTRY,
        dict(_HTTP_PROPS),
    )

    assert guarded[FIELD_BAT_SOC] == _FRESH_HTTP_SOC
    assert guarded[FIELD_SOC] == _FRESH_HTTP_SOC
    assert guarded[FIELD_PV_PW] == _HTTP_PV_W


def test_recently_pushed_keys_do_not_override_present_http_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Recent live stamps never turn MQTT/BLE into the primary live source."""
    coordinator = _bare_coordinator(monkeypatch)
    coordinator._live_property_key_monotonic["dev-1"] = {  # ruff:ignore[private-member-access]
        FIELD_SOC: _NOW,
        FIELD_BAT_SOC: _NOW,
    }

    guarded = coordinator._http_properties_with_live_overrides(  # ruff:ignore[private-member-access]
        "dev-1",
        _ENTRY,
        dict(_HTTP_PROPS),
    )

    assert guarded == _HTTP_PROPS


def test_recent_live_fields_fill_http_omissions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Supplemental live values fill keys that HTTP did not provide."""
    coordinator = _bare_coordinator(monkeypatch)
    coordinator._live_property_key_monotonic["dev-1"] = {  # ruff:ignore[private-member-access]
        FIELD_SOC: _NOW,
        FIELD_PV_PW: _NOW,
    }
    http_props = dict(_HTTP_PROPS)
    del http_props[FIELD_PV_PW]

    guarded = coordinator._http_properties_with_live_overrides(  # ruff:ignore[private-member-access]
        "dev-1",
        _ENTRY,
        http_props,
    )

    assert guarded[FIELD_BAT_SOC] == _FRESH_HTTP_SOC
    assert guarded[FIELD_SOC] == _FRESH_HTTP_SOC
    assert guarded[FIELD_PV_PW] == _LIVE_PV_W


def test_expired_stamps_let_http_win_everywhere(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Stamps older than the freshness window stop shielding entirely."""
    coordinator = _bare_coordinator(monkeypatch)
    coordinator._live_property_key_monotonic["dev-1"] = {  # ruff:ignore[private-member-access]
        FIELD_SOC: _NOW - _STALE_AGE_SEC,
        FIELD_PV_PW: _NOW - _STALE_AGE_SEC,
    }

    guarded = coordinator._http_properties_with_live_overrides(  # ruff:ignore[private-member-access]
        "dev-1",
        _ENTRY,
        dict(_HTTP_PROPS),
    )

    assert guarded == _HTTP_PROPS


def test_note_live_property_keys_stamps_only_delivered_live_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Only live keys actually present (and non-None) in the frame get stamped."""
    coordinator = _bare_coordinator(monkeypatch)

    coordinator._note_live_property_keys(  # ruff:ignore[private-member-access]
        "dev-1",
        {FIELD_PV_PW: 123, FIELD_SOC: None, "notALiveKey": 1},
    )

    stamps = coordinator._live_property_key_monotonic["dev-1"]  # ruff:ignore[private-member-access]
    assert stamps == {FIELD_PV_PW: _NOW}
