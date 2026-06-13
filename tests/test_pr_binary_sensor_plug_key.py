"""Tests for the JackerySmartPlugStateBinarySensor changes introduced in this PR.

The PR changes the entity unique_id from
    "<device_id>_smart_plug_<index>_switch_state"
to
    "<device_id>_<plug_key>_switch_state"
where plug_key is derived from stable_subdevice_key("smart_plug", plug_sn, index).

Covers:
- unique_id uses plug_key, not plug_index
- plug_key is stored on the instance
- is_on reads FIELD_SWITCH_STATE then falls back to FIELD_SYS_SWITCH
- extra_state_attributes always contains plug_index
- _plug lookup by captured serial number (serial-stable binding)
"""

from types import SimpleNamespace
from typing import Any

from custom_components.jackery_solarvault.binary_sensor import (
    JackerySmartPlugStateBinarySensor,
)
from custom_components.jackery_solarvault.const import (
    FIELD_COMM_MODE,
    FIELD_COMM_STATE,
    FIELD_DEVICE_NAME,
    FIELD_SCAN_NAME,
    FIELD_SWITCH_STATE,
    FIELD_SYS_SWITCH,
    FIELD_VERSION,
    PAYLOAD_SMART_PLUGS,
)
from custom_components.jackery_solarvault.util import stable_subdevice_key

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _coordinator(
    dev_id: str = "123456",
    smart_plugs: list[dict[str, Any]] | None = None,
) -> Any:  # noqa: ANN401
    """Create a minimal coordinator stub with smart_plugs in the device payload."""
    payload: dict[str, Any] = {}
    if smart_plugs is not None:
        payload[PAYLOAD_SMART_PLUGS] = smart_plugs
    return SimpleNamespace(data={dev_id: payload})


def _plug_sensor(
    plug_sn: str = "SN001",
    plug_index: int = 1,
    plug_key: str | None = None,
    dev_id: str = "123456",
    smart_plugs: list[dict[str, Any]] | None = None,
) -> JackerySmartPlugStateBinarySensor:
    """Construct a JackerySmartPlugStateBinarySensor test instance."""
    if plug_key is None:
        plug_key = stable_subdevice_key("smart_plug", plug_sn, plug_index)
    if smart_plugs is None:
        smart_plugs = [{"sn": plug_sn, FIELD_SWITCH_STATE: 1}]
    coordinator = _coordinator(dev_id=dev_id, smart_plugs=smart_plugs)
    return JackerySmartPlugStateBinarySensor(
        coordinator,
        dev_id,
        plug_index=plug_index,
        plug_sn=plug_sn,
        plug_key=plug_key,
    )


# ---------------------------------------------------------------------------
# unique_id now uses plug_key
# ---------------------------------------------------------------------------


def test_unique_id_contains_plug_key() -> None:
    """unique_id must be '<device_id>_<plug_key>_switch_state'."""
    dev_id = "123456"
    plug_sn = "SN-ABC-001"
    plug_index = 1
    plug_key = stable_subdevice_key("smart_plug", plug_sn, plug_index)

    sensor = _plug_sensor(
        plug_sn=plug_sn, plug_index=plug_index, dev_id=dev_id, plug_key=plug_key
    )

    assert sensor.unique_id == f"{dev_id}_{plug_key}_switch_state"


def test_unique_id_does_not_use_raw_index() -> None:
    """unique_id must NOT simply contain 'smart_plug_1_switch_state' (old format)."""
    dev_id = "999"
    plug_sn = "SERIAL-X"
    plug_index = 1
    plug_key = stable_subdevice_key("smart_plug", plug_sn, plug_index)

    sensor = _plug_sensor(
        plug_sn=plug_sn, plug_index=plug_index, dev_id=dev_id, plug_key=plug_key
    )

    # Old format would be "999_smart_plug_1_switch_state" — must not exist.
    assert sensor.unique_id != f"{dev_id}_smart_plug_{plug_index}_switch_state"


def test_unique_id_stable_across_index_changes() -> None:
    """The unique_id must be driven by plug_key (serial-based), not by position."""
    dev_id = "123456"
    plug_sn = "STABLE-SN"
    # Same serial but different index (plug reordered in cloud response)
    plug_key_a = stable_subdevice_key("smart_plug", plug_sn, 1)
    plug_key_b = stable_subdevice_key("smart_plug", plug_sn, 2)

    sensor_a = _plug_sensor(
        plug_sn=plug_sn, plug_index=1, dev_id=dev_id, plug_key=plug_key_a
    )
    sensor_b = _plug_sensor(
        plug_sn=plug_sn, plug_index=2, dev_id=dev_id, plug_key=plug_key_b
    )

    # Because same SN drives both keys, both unique_ids contain the same SN-derived part.
    # The stable key for the same SN must be identical (index falls back only if SN is empty).
    assert plug_key_a == plug_key_b  # both use serial "STABLE-SN"
    assert sensor_a.unique_id == sensor_b.unique_id


def test_plug_key_attribute_stored(self: None = None) -> None:  # noqa: PT028
    """The plug_key must be stored as _plug_key on the instance."""
    plug_sn = "SN-KEY-TEST"
    plug_key = stable_subdevice_key("smart_plug", plug_sn, 1)
    sensor = _plug_sensor(plug_sn=plug_sn, plug_index=1, plug_key=plug_key)
    assert sensor._plug_key == plug_key  # noqa: SLF001


# ---------------------------------------------------------------------------
# is_on behaviour (unchanged from pre-PR, verify it still works)
# ---------------------------------------------------------------------------


def test_is_on_returns_true_when_switch_state_is_1() -> None:
    """is_on is True when FIELD_SWITCH_STATE is 1."""
    sensor = _plug_sensor(smart_plugs=[{"sn": "SN001", FIELD_SWITCH_STATE: 1}])
    assert sensor.is_on is True


def test_is_on_returns_false_when_switch_state_is_0() -> None:
    """is_on is False when FIELD_SWITCH_STATE is 0."""
    sensor = _plug_sensor(smart_plugs=[{"sn": "SN001", FIELD_SWITCH_STATE: 0}])
    assert sensor.is_on is False


def test_is_on_falls_back_to_sys_switch_when_switch_state_absent() -> None:
    """is_on uses FIELD_SYS_SWITCH when FIELD_SWITCH_STATE is absent."""
    sensor = _plug_sensor(smart_plugs=[{"sn": "SN001", FIELD_SYS_SWITCH: 1}])
    assert sensor.is_on is True


def test_is_on_returns_none_when_no_state_fields() -> None:
    """is_on is None when neither FIELD_SWITCH_STATE nor FIELD_SYS_SWITCH is present."""
    sensor = _plug_sensor(
        smart_plugs=[{"sn": "SN001"}]  # no state fields
    )
    assert sensor.is_on is None


def test_is_on_returns_none_when_plug_not_found() -> None:
    """is_on is None when the plug serial is not found in the coordinator data."""
    sensor = _plug_sensor(
        plug_sn="SN001",
        smart_plugs=[{"sn": "SN999", FIELD_SWITCH_STATE: 1}],  # different SN
    )
    assert sensor.is_on is None


# ---------------------------------------------------------------------------
# extra_state_attributes
# ---------------------------------------------------------------------------


def test_extra_state_attributes_always_contains_plug_index() -> None:
    """extra_state_attributes must always include the captured plug_index."""
    sensor = _plug_sensor(plug_index=2)
    attrs = sensor.extra_state_attributes
    assert "plug_index" in attrs
    assert attrs["plug_index"] == 2  # noqa: PLR2004


def test_extra_state_attributes_includes_device_name_when_present() -> None:
    """FIELD_DEVICE_NAME is included when the plug payload contains it."""
    sensor = _plug_sensor(
        smart_plugs=[{"sn": "SN001", FIELD_DEVICE_NAME: "Living Room Plug"}]
    )
    attrs = sensor.extra_state_attributes
    assert attrs[FIELD_DEVICE_NAME] == "Living Room Plug"


def test_extra_state_attributes_omits_absent_fields() -> None:
    """Optional diagnostic fields are omitted when absent from the plug payload."""
    sensor = _plug_sensor(
        smart_plugs=[{"sn": "SN001"}]  # Only sn, no diagnostic fields
    )
    attrs = sensor.extra_state_attributes
    assert FIELD_SCAN_NAME not in attrs
    assert FIELD_COMM_STATE not in attrs
    assert FIELD_COMM_MODE not in attrs
    assert FIELD_VERSION not in attrs


def test_extra_state_attributes_includes_optional_diagnostic_fields() -> None:
    """All optional diagnostic fields are included when the plug payload has them."""
    sensor = _plug_sensor(
        smart_plugs=[
            {
                "sn": "SN001",
                FIELD_SCAN_NAME: "shellyplusplugs",
                FIELD_COMM_STATE: 1,
                FIELD_COMM_MODE: 2,
                FIELD_SWITCH_STATE: 1,
                FIELD_SYS_SWITCH: 1,
                FIELD_VERSION: "1.0.3",
            }
        ]
    )
    attrs = sensor.extra_state_attributes
    assert attrs[FIELD_SCAN_NAME] == "shellyplusplugs"
    assert attrs[FIELD_COMM_STATE] == 1
    assert attrs[FIELD_COMM_MODE] == 2  # noqa: PLR2004
    assert attrs[FIELD_SWITCH_STATE] == 1
    assert attrs[FIELD_SYS_SWITCH] == 1
    assert attrs[FIELD_VERSION] == "1.0.3"


# ---------------------------------------------------------------------------
# Serial-based _plug lookup (stability under payload reordering)
# ---------------------------------------------------------------------------


def test_plug_lookup_by_serial_not_position() -> None:
    """_plug must find the plug by its captured serial even when list order changes."""
    dev_id = "123456"
    plug_sn = "SN-STABLE"
    # Two plugs; the desired one is second in the list
    smart_plugs = [
        {"sn": "SN-OTHER", FIELD_SWITCH_STATE: 0},
        {"sn": plug_sn, FIELD_SWITCH_STATE: 1},
    ]
    coordinator = _coordinator(dev_id=dev_id, smart_plugs=smart_plugs)
    plug_key = stable_subdevice_key("smart_plug", plug_sn, 2)
    sensor = JackerySmartPlugStateBinarySensor(
        coordinator, dev_id, plug_index=2, plug_sn=plug_sn, plug_key=plug_key
    )

    assert sensor.is_on is True


def test_plug_lookup_returns_empty_dict_when_serial_not_found() -> None:
    """_plug returns {} when no plug in the list matches the captured serial."""
    dev_id = "123456"
    plug_sn = "GONE-SN"
    smart_plugs = [{"sn": "DIFFERENT-SN", FIELD_SWITCH_STATE: 1}]
    coordinator = _coordinator(dev_id=dev_id, smart_plugs=smart_plugs)
    plug_key = stable_subdevice_key("smart_plug", plug_sn, 1)
    sensor = JackerySmartPlugStateBinarySensor(
        coordinator, dev_id, plug_index=1, plug_sn=plug_sn, plug_key=plug_key
    )

    assert sensor._plug == {}  # noqa: SLF001


# ---------------------------------------------------------------------------
# stable_subdevice_key helper (as used in binary_sensor.async_setup_entry)
# ---------------------------------------------------------------------------


def test_stable_subdevice_key_normalizes_serial_to_lowercase() -> None:
    """stable_subdevice_key normalises to lowercase."""
    key = stable_subdevice_key("smart_plug", "SN-ABC-001", 1)
    assert key == key.lower()


def test_stable_subdevice_key_uses_fallback_index_for_none_identity() -> None:
    """When identity is None, stable_subdevice_key falls back to the index."""
    key = stable_subdevice_key("smart_plug", None, 3)
    assert "3" in key


def test_stable_subdevice_key_uses_fallback_index_for_empty_identity() -> None:
    """When identity is an empty string, stable_subdevice_key falls back to the index."""
    key = stable_subdevice_key("smart_plug", "", 5)
    assert "5" in key


def test_stable_subdevice_key_replaces_special_chars_with_underscore() -> None:
    """Non-alphanumeric characters in the identity are replaced with underscores."""
    key = stable_subdevice_key("smart_plug", "AB:CD:EF-01", 1)
    assert ":" not in key
    assert "-" not in key


def test_stable_subdevice_key_is_deterministic() -> None:
    """Same inputs always produce the same key."""
    a = stable_subdevice_key("smart_plug", "SN123", 1)
    b = stable_subdevice_key("smart_plug", "SN123", 1)
    assert a == b


def test_stable_subdevice_key_includes_prefix() -> None:
    """The resulting key must start with the given prefix."""
    key = stable_subdevice_key("smart_plug", "SN123", 1)
    assert key.startswith("smart_plug_")
