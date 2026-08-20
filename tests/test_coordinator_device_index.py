"""Behavioral tests for coordinator device-index resolvers.

These map cloud/BLE identifiers to internal device ids and answer capability
questions from the discovered device index plus the live ``coordinator.data``
snapshot. They are pure lookups — no I/O — so they exercise the resolution
precedence directly.
"""

import base64
from typing import Any, cast

from custom_components.jackery_solarvault.const import (
    DISCOVERY_SOURCE_LEGACY_BIND_LIST,
    FIELD_BLUETOOTH_KEY,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_ID,
    FIELD_MAX_OUT_PW,
    FIELD_MODEL_CODE,
    FIELD_SYSTEM_ID,
    PAYLOAD_DEVICE,
    PAYLOAD_DEVICE_META,
    PAYLOAD_DISCOVERY_SOURCE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SYSTEM,
    PAYLOAD_SYSTEM_META,
    PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

_HOME_MODEL_CODE = 3001


def _coordinator(
    *,
    index: dict[str, Any] | None = None,
    data: dict[str, Any] | None = None,
) -> JackerySolarVaultCoordinator:
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._device_index = index or {}
    cast("Any", coordinator).data = data or {}
    return coordinator


# ---------------------------------------------------------------------------
# _resolve_device_id_from_mqtt
# ---------------------------------------------------------------------------


def test_resolve_device_id_matches_known_id_in_body() -> None:
    """A device id nested in the body resolves against the index."""
    coordinator = _coordinator(index={"dev-1": {}})

    resolved = coordinator._resolve_device_id_from_mqtt(
        {"body": {FIELD_DEVICE_ID: "dev-1"}},
    )

    assert resolved == "dev-1"


def test_resolve_device_id_by_serial_from_index_meta() -> None:
    """When only a serial is present, it is matched through device-meta."""
    coordinator = _coordinator(
        index={
            "dev-1": {PAYLOAD_DEVICE_META: {FIELD_DEVICE_SN: "SN-A"}},
            "dev-2": {PAYLOAD_DEVICE_META: {FIELD_DEVICE_SN: "SN-B"}},
        },
    )

    resolved = coordinator._resolve_device_id_from_mqtt(
        {FIELD_DEVICE_SN: "SN-B"},
    )

    assert resolved == "dev-2"


def test_resolve_device_id_falls_back_to_sole_device() -> None:
    """An unidentifiable payload maps to the only known device."""
    coordinator = _coordinator(index={"only": {}})

    resolved = coordinator._resolve_device_id_from_mqtt({"body": {}})

    assert resolved == "only"


def test_resolve_device_id_returns_none_when_ambiguous() -> None:
    """With multiple devices and no identifier, resolution abstains."""
    coordinator = _coordinator(index={"a": {}, "b": {}})

    resolved = coordinator._resolve_device_id_from_mqtt({"body": {}})

    assert resolved is None


def test_resolve_device_id_rejects_unknown_explicit_serial_for_sole_device() -> None:
    """An explicit foreign serial never falls through to the sole-device shortcut."""
    coordinator = _coordinator(
        index={"only": {PAYLOAD_DEVICE_META: {FIELD_DEVICE_SN: "KNOWN"}}},
    )

    assert (
        coordinator._resolve_device_id_from_mqtt({FIELD_DEVICE_SN: "FOREIGN"}) is None
    )


def test_resolve_device_id_rejects_duplicate_serial_match() -> None:
    """Ambiguous cached serial identity fails closed instead of cross-wiring state."""
    coordinator = _coordinator(
        index={
            "dev-1": {PAYLOAD_DEVICE_META: {FIELD_DEVICE_SN: "DUPLICATE"}},
            "dev-2": {PAYLOAD_DEVICE_META: {FIELD_DEVICE_SN: "DUPLICATE"}},
        },
    )

    assert (
        coordinator._resolve_device_id_from_mqtt({FIELD_DEVICE_SN: "DUPLICATE"}) is None
    )


# ---------------------------------------------------------------------------
# _resolve_device_sn / _resolve_system_id
# ---------------------------------------------------------------------------


def test_resolve_device_sn_prefers_index_meta() -> None:
    """The device-meta serial wins over live data."""
    coordinator = _coordinator(
        index={"dev-1": {PAYLOAD_DEVICE_META: {FIELD_DEVICE_SN: "META-SN"}}},
        data={"dev-1": {PAYLOAD_DEVICE: {FIELD_DEVICE_SN: "DATA-SN"}}},
    )

    assert coordinator._resolve_device_sn("dev-1") == "META-SN"


def test_resolve_device_sn_falls_back_to_live_data() -> None:
    """Without index meta, the serial comes from the live device payload."""
    coordinator = _coordinator(
        index={"dev-1": {}},
        data={"dev-1": {PAYLOAD_DEVICE: {FIELD_DEVICE_SN: "DATA-SN"}}},
    )

    assert coordinator._resolve_device_sn("dev-1") == "DATA-SN"


def test_resolve_system_id_prefers_index() -> None:
    """The index systemId takes precedence."""
    coordinator = _coordinator(index={"dev-1": {FIELD_SYSTEM_ID: 42}})

    assert coordinator._resolve_system_id("dev-1") == "42"


def test_resolve_system_id_from_live_system_payload() -> None:
    """Without an index systemId, the live system payload supplies it."""
    coordinator = _coordinator(
        index={"dev-1": {}},
        data={"dev-1": {PAYLOAD_SYSTEM: {FIELD_ID: 7}}},
    )

    assert coordinator._resolve_system_id("dev-1") == "7"


def test_resolve_system_id_returns_none_when_absent() -> None:
    """A device with no system context yields None."""
    coordinator = _coordinator(index={"dev-1": {}}, data={"dev-1": {}})

    assert coordinator._resolve_system_id("dev-1") is None


# ---------------------------------------------------------------------------
# device_bluetooth_key
# ---------------------------------------------------------------------------


def test_bluetooth_key_decodes_aes128() -> None:
    """A valid 16-byte base64 key decodes to raw bytes."""
    raw = bytes(range(16))
    encoded = base64.b64encode(raw).decode()
    coordinator = _coordinator(
        index={"dev-1": {PAYLOAD_SYSTEM_META: {FIELD_BLUETOOTH_KEY: encoded}}},
    )

    assert coordinator.device_bluetooth_key("dev-1") == raw


def test_bluetooth_key_missing_returns_none() -> None:
    """No configured key yields None."""
    coordinator = _coordinator(index={"dev-1": {}})

    assert coordinator.device_bluetooth_key("dev-1") is None


def test_bluetooth_key_wrong_length_rejected() -> None:
    """A key that decodes to a non-AES length is rejected."""
    encoded = base64.b64encode(b"three").decode()
    coordinator = _coordinator(
        index={"dev-1": {PAYLOAD_DEVICE_META: {FIELD_BLUETOOTH_KEY: encoded}}},
    )

    assert coordinator.device_bluetooth_key("dev-1") is None


def test_bluetooth_key_invalid_base64_rejected() -> None:
    """A non-base64 key is rejected without raising."""
    coordinator = _coordinator(
        index={"dev-1": {PAYLOAD_DEVICE_META: {FIELD_BLUETOOTH_KEY: "!!!not-b64"}}},
    )

    assert coordinator.device_bluetooth_key("dev-1") is None


# ---------------------------------------------------------------------------
# device_supports_advanced / device_supports_third_party_mqtt
# ---------------------------------------------------------------------------


def test_supports_advanced_when_max_out_pw_present() -> None:
    """A device reporting maxOutPw supports advanced controls."""
    coordinator = _coordinator(
        data={"dev-1": {PAYLOAD_PROPERTIES: {FIELD_MAX_OUT_PW: 800}}},
    )

    assert coordinator.device_supports_advanced("dev-1") is True


def test_supports_advanced_false_without_evidence() -> None:
    """A stripped-down device without maxOutPw is not advanced."""
    coordinator = _coordinator(data={"dev-1": {PAYLOAD_PROPERTIES: {}}})

    assert coordinator.device_supports_advanced("dev-1") is False


def test_supports_advanced_from_stable_http_system_discovery() -> None:
    """System-list metadata keeps Home controls registered before live values."""
    coordinator = _coordinator(
        data={"dev-1": {PAYLOAD_SYSTEM: {FIELD_ID: "system-1"}}},
    )

    assert coordinator.device_supports_advanced("dev-1") is True


def test_supports_advanced_from_nonportable_model_metadata() -> None:
    """Home model presence is capability evidence without hardcoded SKU values."""
    coordinator = _coordinator(
        data={
            "dev-1": {
                PAYLOAD_DEVICE: {FIELD_MODEL_CODE: _HOME_MODEL_CODE},
            },
        },
    )

    assert coordinator.device_supports_advanced("dev-1") is True


def test_portable_legacy_model_metadata_is_not_advanced_home_evidence() -> None:
    """Legacy-bind portable models do not inherit Home-only controls."""
    coordinator = _coordinator(
        data={
            "dev-1": {
                PAYLOAD_DEVICE: {
                    FIELD_MODEL_CODE: _HOME_MODEL_CODE,
                    PAYLOAD_DISCOVERY_SOURCE: DISCOVERY_SOURCE_LEGACY_BIND_LIST,
                },
            },
        },
    )

    assert coordinator.device_supports_advanced("dev-1") is False


def test_supports_third_party_mqtt_when_config_present() -> None:
    """A cached third-party MQTT config implies support."""
    coordinator = _coordinator(
        data={"dev-1": {PAYLOAD_THIRD_PARTY_MQTT_CONFIG: {"host": "x"}}},
    )

    assert coordinator.device_supports_third_party_mqtt("dev-1") is True


def test_supports_third_party_mqtt_via_advanced_evidence() -> None:
    """Advanced hardware supports third-party MQTT even without a cached config."""
    coordinator = _coordinator(
        data={"dev-1": {PAYLOAD_PROPERTIES: {FIELD_MAX_OUT_PW: 800}}},
    )

    assert coordinator.device_supports_third_party_mqtt("dev-1") is True


# ---------------------------------------------------------------------------
# device_id_for_ble_serial
# ---------------------------------------------------------------------------


def test_ble_serial_matches_as_http_suffix() -> None:
    """The BLE serial is a suffix of the longer HTTP serial."""
    coordinator = _coordinator(
        index={"dev-1": {PAYLOAD_DEVICE_META: {FIELD_DEVICE_SN: "HR2C04000280HH3"}}},
    )

    assert coordinator.device_id_for_ble_serial("R2C04000280HH3") == "dev-1"


def test_ble_serial_exact_match() -> None:
    """An exact serial match also resolves."""
    coordinator = _coordinator(
        index={"dev-1": {PAYLOAD_DEVICE_META: {FIELD_DEVICE_SN: "R2C04000280HH3"}}},
    )

    assert coordinator.device_id_for_ble_serial("r2c04000280hh3") == "dev-1"


def test_ble_serial_empty_returns_none() -> None:
    """An empty BLE serial resolves to nothing."""
    coordinator = _coordinator(index={"dev-1": {}})

    assert coordinator.device_id_for_ble_serial("") is None


def test_ble_serial_no_match_returns_none() -> None:
    """A serial that matches no device yields None."""
    coordinator = _coordinator(
        index={"dev-1": {PAYLOAD_DEVICE_META: {FIELD_DEVICE_SN: "AAAA"}}},
    )

    assert coordinator.device_id_for_ble_serial("BBBB") is None


# ---------------------------------------------------------------------------
# _is_portable_device_id
# ---------------------------------------------------------------------------


def test_is_portable_true_for_legacy_bind_source() -> None:
    """A legacy-bind-list discovery source marks the device portable."""
    coordinator = _coordinator(
        data={
            "dev-1": {
                PAYLOAD_DEVICE: {
                    PAYLOAD_DISCOVERY_SOURCE: DISCOVERY_SOURCE_LEGACY_BIND_LIST,
                },
            },
        },
    )

    assert coordinator._is_portable_device_id("dev-1") is True


def test_is_portable_false_for_regular_device() -> None:
    """A device without the legacy marker is not portable."""
    coordinator = _coordinator(data={"dev-1": {PAYLOAD_DEVICE: {}}})

    assert coordinator._is_portable_device_id("dev-1") is False


def test_is_portable_false_when_unknown_device() -> None:
    """An unknown device id is not portable."""
    coordinator = _coordinator(data={})

    assert coordinator._is_portable_device_id("ghost") is False
