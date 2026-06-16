"""Tests for binary_sensor.py async_setup_entry and class-level attributes.

Covers code changed/added for this integration:
- PARALLEL_UPDATES constant
- async_setup_entry: entity discovery from coordinator data
- async_setup_entry: deduplication (same unique_id not added twice)
- async_setup_entry: signature-based re-collection skips unchanged data
- async_setup_entry: plugs without a serial number are skipped
- async_setup_entry: listener is registered via entry.async_on_unload
- async_setup_entry: async_add_entities not called when no entities found
- JackerySmartPlugStateBinarySensor class-level attribute values
- JackerySmartPlugStateBinarySensor._plug returns {} when coordinator.data is None
- JackerySmartPlugStateBinarySensor._plug returns {} when PAYLOAD_SMART_PLUGS absent
- JackerySmartPlugStateBinarySensor extra_state_attributes with FIELD_SWITCH_STATE=None
- JackerySmartPlugStateBinarySensor extra_state_attributes includes FIELD_SYS_SWITCH
- JackerySmartPlugStateBinarySensor is_on: FIELD_SWITCH_STATE None falls back to
FIELD_SYS_SWITCH
"""

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock

import pytest

from custom_components.jackery_solarvault.binary_sensor import (
    BINARY_DESCRIPTIONS,
    PARALLEL_UPDATES,
    JackeryBinarySensor,
    JackerySmartPlugStateBinarySensor,
    async_setup_entry,
)
from custom_components.jackery_solarvault.const import (
    FIELD_COMM_MODE,
    FIELD_COMM_STATE,
    FIELD_DEVICE_NAME,
    FIELD_SCAN_NAME,
    FIELD_SWITCH_STATE,
    FIELD_SYS_SWITCH,
    FIELD_VERSION,
    PAYLOAD_DEVICE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SMART_PLUGS,
)
from custom_components.jackery_solarvault.util import stable_subdevice_key
from homeassistant.components.binary_sensor import BinarySensorDeviceClass

if TYPE_CHECKING:
    from collections.abc import Callable

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(
    data: dict[str, object] | None = None,
) -> SimpleNamespace:
    """Build a minimal coordinator stub."""
    return SimpleNamespace(
        data=data,
        async_add_listener=MagicMock(return_value=lambda: None),
    )


def _make_entry(coordinator: SimpleNamespace) -> SimpleNamespace:
    """Build a minimal config-entry stub."""
    return SimpleNamespace(
        runtime_data=coordinator,
        async_on_unload=MagicMock(),
    )


def _make_device_payload(
    properties: dict[str, Any] | None = None,
    device_meta: dict[str, Any] | None = None,
    smart_plugs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build a device payload dict."""
    payload: dict[str, Any] = {
        PAYLOAD_PROPERTIES: properties or {},
        PAYLOAD_DEVICE: device_meta or {},
    }
    if smart_plugs is not None:
        payload[PAYLOAD_SMART_PLUGS] = smart_plugs
    return payload


def _make_plug_sensor(
    plug_sn: str = "SN001",
    plug_index: int = 1,
    plug_key: str | None = None,
    dev_id: str = "dev123",
    smart_plugs: list[dict[str, Any]] | None = None,
) -> JackerySmartPlugStateBinarySensor:
    """Construct a JackerySmartPlugStateBinarySensor test instance."""
    if plug_key is None:
        plug_key = stable_subdevice_key("smart_plug", plug_sn, plug_index)
    if smart_plugs is None:
        smart_plugs = [{"sn": plug_sn}]
    payload = _make_device_payload(smart_plugs=smart_plugs)
    coordinator = _make_coordinator(data={dev_id: payload})
    return JackerySmartPlugStateBinarySensor(
        coordinator,
        dev_id,
        plug_index=plug_index,
        plug_sn=plug_sn,
        plug_key=plug_key,
    )


# ---------------------------------------------------------------------------
# PARALLEL_UPDATES constant
# ---------------------------------------------------------------------------


def test_parallel_updates_is_zero() -> None:
    """PARALLEL_UPDATES must be 0 to disable per-entity parallel scheduling."""
    assert PARALLEL_UPDATES == 0  # noqa: S101


# ---------------------------------------------------------------------------
# async_setup_entry — basic entity discovery
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_async_setup_entry_creates_binary_sensors_for_each_device() -> None:
    """async_setup_entry must create BINARY_DESCRIPTIONS-count entities per device."""
    dev_id = "device_001"
    coordinator = _make_coordinator(data={dev_id: _make_device_payload()})
    entry = _make_entry(coordinator)
    added_entities: list[Any] = []

    async_add_entities = MagicMock(side_effect=added_entities.extend)

    await async_setup_entry(None, entry, async_add_entities)

    # One call with BINARY_DESCRIPTIONS count of entities (no smart plugs)
    assert async_add_entities.call_count == 1  # noqa: S101
    entities = async_add_entities.call_args[0][0]
    assert len(entities) == len(BINARY_DESCRIPTIONS)  # noqa: S101
    assert all(isinstance(e, JackeryBinarySensor) for e in entities)  # noqa: S101


@pytest.mark.asyncio()
async def test_async_setup_entry_creates_multiple_devices() -> None:
    """async_setup_entry must create entities for each device in coordinator data."""
    coordinator = _make_coordinator(
        data={
            "dev_a": _make_device_payload(),
            "dev_b": _make_device_payload(),
        },
    )
    entry = _make_entry(coordinator)
    async_add_entities = MagicMock()

    await async_setup_entry(None, entry, async_add_entities)

    # Both devices get entities: 2 * len(BINARY_DESCRIPTIONS) entities
    entities = async_add_entities.call_args[0][0]
    assert len(entities) == 2 * len(BINARY_DESCRIPTIONS)  # noqa: S101


@pytest.mark.asyncio()
async def test_async_setup_entry_no_data_no_call() -> None:
    """async_setup_entry must not call async_add_entities when coordinator.data is.

    empty.
    """
    coordinator = _make_coordinator(data={})
    entry = _make_entry(coordinator)
    async_add_entities = MagicMock()

    await async_setup_entry(None, entry, async_add_entities)

    async_add_entities.assert_not_called()


@pytest.mark.asyncio()
async def test_async_setup_entry_none_data_no_call() -> None:
    """async_setup_entry must not call async_add_entities when coordinator.data is.

    None.
    """
    coordinator = _make_coordinator(data=None)
    entry = _make_entry(coordinator)
    async_add_entities = MagicMock()

    await async_setup_entry(None, entry, async_add_entities)

    async_add_entities.assert_not_called()


# ---------------------------------------------------------------------------
# async_setup_entry — smart plug entity creation
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_async_setup_entry_creates_smart_plug_entities() -> None:
    """async_setup_entry must create JackerySmartPlugStateBinarySensor for plugs with.

    serials.
    """
    dev_id = "dev_001"
    plugs = [
        {"sn": "SN-A"},
        {"sn": "SN-B"},
    ]
    coordinator = _make_coordinator(
        data={dev_id: _make_device_payload(smart_plugs=plugs)},
    )
    entry = _make_entry(coordinator)
    async_add_entities = MagicMock()

    await async_setup_entry(None, entry, async_add_entities)

    entities = async_add_entities.call_args[0][0]
    plug_entities = [
        e for e in entities if isinstance(e, JackerySmartPlugStateBinarySensor)
    ]
    assert len(plug_entities) == 2  # noqa: PLR2004, S101


@pytest.mark.asyncio()
async def test_async_setup_entry_skips_plugs_without_serial() -> None:
    """async_setup_entry must skip smart plugs that have no extractable serial.

    number.
    """
    dev_id = "dev_001"
    plugs = [
        {},  # no serial fields at all
        {"sn": ""},  # empty serial
        {"sn": "SN-X"},  # valid serial
    ]
    coordinator = _make_coordinator(
        data={dev_id: _make_device_payload(smart_plugs=plugs)},
    )
    entry = _make_entry(coordinator)
    async_add_entities = MagicMock()

    await async_setup_entry(None, entry, async_add_entities)

    entities = async_add_entities.call_args[0][0]
    plug_entities = [
        e for e in entities if isinstance(e, JackerySmartPlugStateBinarySensor)
    ]
    # Only the plug with "SN-X" should be included
    assert len(plug_entities) == 1  # noqa: S101


# ---------------------------------------------------------------------------
# async_setup_entry — signature-based deduplication
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_async_setup_entry_listener_registered() -> None:
    """async_setup_entry must register a listener via entry.async_on_unload."""
    dev_id = "dev_001"
    coordinator = _make_coordinator(data={dev_id: _make_device_payload()})
    entry = _make_entry(coordinator)
    async_add_entities = MagicMock()

    await async_setup_entry(None, entry, async_add_entities)

    # async_on_unload must have been called with a callable (the unsubscribe fn)
    assert entry.async_on_unload.called  # noqa: S101
    unsubscribe_fn = entry.async_on_unload.call_args[0][0]
    assert callable(unsubscribe_fn)  # noqa: S101


@pytest.mark.asyncio()
async def test_async_setup_entry_same_signature_no_double_add() -> None:
    """Re-invoking _add_new_entities with unchanged coordinator data must not add.

    entities again.
    """
    dev_id = "dev_001"
    coordinator = _make_coordinator(data={dev_id: _make_device_payload()})
    entry = _make_entry(coordinator)

    captured_listener: Callable[[], None] | None = None

    def capture_add_listener(fn: Callable[[], None]) -> Callable[[], None]:
        nonlocal captured_listener
        captured_listener = fn
        return lambda: None

    coordinator.async_add_listener = capture_add_listener
    async_add_entities = MagicMock()

    await async_setup_entry(None, entry, async_add_entities)

    # First call already added entities
    first_call_count = async_add_entities.call_count
    assert first_call_count == 1  # noqa: S101

    # Invoke the listener with unchanged data — signature is the same
    assert captured_listener is not None  # noqa: S101
    captured_listener()

    # async_add_entities must NOT be called again
    assert async_add_entities.call_count == first_call_count  # noqa: S101


@pytest.mark.asyncio()
async def test_async_setup_entry_new_device_triggers_add() -> None:
    """Listener invocation with new device data must call async_add_entities again."""
    dev_id_a = "dev_a"
    dev_id_b = "dev_b"
    coordinator = _make_coordinator(data={dev_id_a: _make_device_payload()})
    entry = _make_entry(coordinator)

    captured_listener: Callable[[], None] | None = None

    def capture_add_listener(fn: Callable[[], None]) -> Callable[[], None]:
        nonlocal captured_listener
        captured_listener = fn
        return lambda: None

    coordinator.async_add_listener = capture_add_listener
    async_add_entities = MagicMock()

    await async_setup_entry(None, entry, async_add_entities)
    assert async_add_entities.call_count == 1  # noqa: S101

    # Simulate a new device being added to coordinator data
    coordinator.data = {
        dev_id_a: _make_device_payload(),
        dev_id_b: _make_device_payload(),
    }
    assert captured_listener is not None  # noqa: S101
    captured_listener()

    # A second call must have been made with new entities for dev_b
    assert async_add_entities.call_count == 2  # noqa: PLR2004, S101


# ---------------------------------------------------------------------------
# JackerySmartPlugStateBinarySensor — class-level attributes
# ---------------------------------------------------------------------------


def test_smart_plug_sensor_translation_key() -> None:
    """_attr_translation_key must be 'smart_plug_switch_state'."""
    sensor = _make_plug_sensor()
    assert sensor.translation_key == "smart_plug_switch_state"  # noqa: S101


def test_smart_plug_sensor_device_class() -> None:
    """_attr_device_class must be BinarySensorDeviceClass.POWER."""
    sensor = _make_plug_sensor()
    assert sensor.device_class == BinarySensorDeviceClass.POWER  # noqa: S101


def test_smart_plug_sensor_icon() -> None:
    """_attr_icon must be 'mdi:power-socket-de'."""
    sensor = _make_plug_sensor()
    assert sensor.icon == "mdi:power-socket-de"  # noqa: S101


# ---------------------------------------------------------------------------
# JackerySmartPlugStateBinarySensor — _plug edge cases
# ---------------------------------------------------------------------------


def test_plug_property_returns_empty_when_coordinator_data_none() -> None:
    """_plug must return {} when coordinator.data is None."""
    coordinator = _make_coordinator(data=None)
    plug_key = stable_subdevice_key("smart_plug", "SN001", 1)
    sensor = JackerySmartPlugStateBinarySensor(
        coordinator,
        "dev1",
        plug_index=1,
        plug_sn="SN001",
        plug_key=plug_key,
    )
    assert sensor._plug == {}  # noqa: S101, SLF001


def test_plug_property_returns_empty_when_device_missing_from_data() -> None:
    """_plug must return {} when device_id is absent from coordinator data."""
    coordinator = _make_coordinator(data={"other_dev": {}})
    plug_key = stable_subdevice_key("smart_plug", "SN001", 1)
    sensor = JackerySmartPlugStateBinarySensor(
        coordinator,
        "dev1",
        plug_index=1,
        plug_sn="SN001",
        plug_key=plug_key,
    )
    assert sensor._plug == {}  # noqa: S101, SLF001


def test_plug_property_returns_empty_when_smart_plugs_key_absent() -> None:
    """_plug must return {} when PAYLOAD_SMART_PLUGS is not in the device payload."""
    coordinator = _make_coordinator(data={"dev1": {}})
    plug_key = stable_subdevice_key("smart_plug", "SN001", 1)
    sensor = JackerySmartPlugStateBinarySensor(
        coordinator,
        "dev1",
        plug_index=1,
        plug_sn="SN001",
        plug_key=plug_key,
    )
    assert sensor._plug == {}  # noqa: S101, SLF001


def test_plug_property_matches_by_serial_number() -> None:
    """_plug must return the dict matching the captured serial, not by position."""
    plug_sn = "TARGET-SN"
    coordinator = _make_coordinator(
        data={
            "dev1": {
                PAYLOAD_SMART_PLUGS: [
                    {"sn": "FIRST-SN", FIELD_SWITCH_STATE: 0},
                    {"sn": plug_sn, FIELD_SWITCH_STATE: 1},
                ],
            },
        },
    )
    plug_key = stable_subdevice_key("smart_plug", plug_sn, 2)
    sensor = JackerySmartPlugStateBinarySensor(
        coordinator,
        "dev1",
        plug_index=2,
        plug_sn=plug_sn,
        plug_key=plug_key,
    )
    assert sensor._plug == {"sn": plug_sn, FIELD_SWITCH_STATE: 1}  # noqa: S101, SLF001


# ---------------------------------------------------------------------------
# JackerySmartPlugStateBinarySensor — is_on edge cases
# ---------------------------------------------------------------------------


def test_is_on_switch_state_none_falls_back_to_sys_switch() -> None:
    """When FIELD_SWITCH_STATE is explicitly None, is_on uses FIELD_SYS_SWITCH."""
    sensor = _make_plug_sensor(
        smart_plugs=[{"sn": "SN001", FIELD_SWITCH_STATE: None, FIELD_SYS_SWITCH: 1}],
    )
    # FIELD_SWITCH_STATE is present but None; raw = None → falls back to
    # FIELD_SYS_SWITCH
    # Actually the code does: raw = plug.get(FIELD_SWITCH_STATE); if raw is None: raw =
    # plug.get(FIELD_SYS_SWITCH)
    assert sensor.is_on is True  # noqa: S101


def test_is_on_both_switch_state_and_sys_switch_zero() -> None:
    """When both FIELD_SWITCH_STATE and FIELD_SYS_SWITCH are 0, is_on is False."""
    sensor = _make_plug_sensor(
        smart_plugs=[{"sn": "SN001", FIELD_SWITCH_STATE: 0, FIELD_SYS_SWITCH: 0}],
    )
    assert sensor.is_on is False  # noqa: S101


def test_is_on_switch_state_zero_does_not_fallback_to_sys_switch() -> None:
    """When FIELD_SWITCH_STATE is 0 (falsy but not None), must use it, not.

    FIELD_SYS_SWITCH.
    """
    sensor = _make_plug_sensor(
        smart_plugs=[{"sn": "SN001", FIELD_SWITCH_STATE: 0, FIELD_SYS_SWITCH: 1}],
    )
    # FIELD_SWITCH_STATE = 0 is not None, so it is used directly (not 1 from
    # FIELD_SYS_SWITCH)
    assert sensor.is_on is False  # noqa: S101


# ---------------------------------------------------------------------------
# JackerySmartPlugStateBinarySensor — extra_state_attributes edge cases
# ---------------------------------------------------------------------------


def test_extra_state_attributes_plug_index_one() -> None:
    """extra_state_attributes must contain plug_index=1 for index-1 sensor."""
    sensor = _make_plug_sensor(plug_index=1)
    assert sensor.extra_state_attributes["plug_index"] == 1  # noqa: S101


def test_extra_state_attributes_plug_index_five() -> None:
    """extra_state_attributes must contain plug_index for any index."""
    sensor = _make_plug_sensor(plug_index=5)
    assert sensor.extra_state_attributes["plug_index"] == 5  # noqa: PLR2004, S101


def test_extra_state_attributes_none_not_present_when_key_absent() -> None:
    """extra_state_attributes must not include keys absent from the plug payload."""
    sensor = _make_plug_sensor(smart_plugs=[{"sn": "SN001"}])
    attrs = sensor.extra_state_attributes
    for key in (
        FIELD_DEVICE_NAME,
        FIELD_SCAN_NAME,
        FIELD_COMM_STATE,
        FIELD_COMM_MODE,
        FIELD_VERSION,
    ):
        assert key not in attrs, (  # noqa: S101
            f"Key {key!r} should not be in attrs when absent from plug"
        )


def test_extra_state_attributes_switch_state_included_when_none() -> None:
    """extra_state_attributes includes FIELD_SWITCH_STATE even when its value is.

    None.
    """
    sensor = _make_plug_sensor(smart_plugs=[{"sn": "SN001", FIELD_SWITCH_STATE: None}])
    attrs = sensor.extra_state_attributes
    # The key IS present in the plug dict (just with value None), so it must appear
    assert FIELD_SWITCH_STATE in attrs  # noqa: S101
    assert attrs[FIELD_SWITCH_STATE] is None  # noqa: S101


def test_extra_state_attributes_sys_switch_included() -> None:
    """extra_state_attributes includes FIELD_SYS_SWITCH when present in plug payload."""
    sensor = _make_plug_sensor(smart_plugs=[{"sn": "SN001", FIELD_SYS_SWITCH: 1}])
    attrs = sensor.extra_state_attributes
    assert FIELD_SYS_SWITCH in attrs  # noqa: S101
    assert attrs[FIELD_SYS_SWITCH] == 1  # noqa: S101


def test_extra_state_attributes_no_plug_returns_only_plug_index() -> None:
    """extra_state_attributes must only contain plug_index when plug cannot be found."""
    sensor = _make_plug_sensor(
        plug_sn="NOT-FOUND",
        smart_plugs=[{"sn": "OTHER-SN"}],  # different SN → _plug returns {}
    )
    attrs = sensor.extra_state_attributes
    assert attrs == {"plug_index": 1}  # noqa: S101


# ---------------------------------------------------------------------------
# JackerySmartPlugStateBinarySensor — unique_id format
# ---------------------------------------------------------------------------


def test_smart_plug_unique_id_format() -> None:
    """unique_id must be '<device_id>_<plug_key>_switch_state'."""
    dev_id = "device_xyz"
    plug_sn = "SN-TEST-001"
    plug_index = 3
    plug_key = stable_subdevice_key("smart_plug", plug_sn, plug_index)
    sensor = _make_plug_sensor(
        plug_sn=plug_sn,
        plug_index=plug_index,
        plug_key=plug_key,
        dev_id=dev_id,
    )
    expected = f"{dev_id}_{plug_key}_switch_state"
    assert sensor.unique_id == expected  # noqa: S101


def test_smart_plug_index_stored_correctly() -> None:
    """The plug_index passed at construction must be stored and accessible."""
    expected_plug_index = 7
    sensor = _make_plug_sensor(plug_index=expected_plug_index)
    assert sensor._plug_index == expected_plug_index  # noqa: S101, SLF001


def test_smart_plug_sn_stored_correctly() -> None:
    """The plug_sn passed at construction must be stored and accessible."""
    sensor = _make_plug_sensor(plug_sn="MY-SERIAL")
    assert sensor._plug_sn == "MY-SERIAL"  # noqa: S101, SLF001


# ---------------------------------------------------------------------------
# async_setup_entry — deduplication across multiple calls
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_async_setup_entry_deduplicates_same_unique_ids() -> None:
    """Duplicate entity unique_ids across rebuild calls must not produce duplicate.

    entities.
    """
    dev_id = "dev_dup"
    coordinator = _make_coordinator(data={dev_id: _make_device_payload()})
    entry = _make_entry(coordinator)

    captured_listener: Callable[[], None] | None = None

    def capture_add_listener(fn: Callable[[], None]) -> Callable[[], None]:
        nonlocal captured_listener
        captured_listener = fn
        return lambda: None

    coordinator.async_add_listener = capture_add_listener
    async_add_entities = MagicMock()

    await async_setup_entry(None, entry, async_add_entities)
    first_count = async_add_entities.call_count
    assert first_count == 1  # noqa: S101

    # Simulate a forced re-collection by clearing coordinator data and then restoring it
    # with a new device, so the signature changes and entities are collected again.
    # The entities with the same device_id should NOT be added again (deduplication).
    coordinator.data = {
        dev_id: _make_device_payload(),
        "new_dev": _make_device_payload(),
    }
    captured_listener()

    # Only new entities (for "new_dev") should be added
    assert async_add_entities.call_count == 2  # noqa: PLR2004, S101
    second_batch = async_add_entities.call_args_list[1][0][0]
    # The second batch must only contain entities for "new_dev", not for "dev_dup"
    second_device_ids = {e._device_id for e in second_batch}  # noqa: SLF001
    assert "new_dev" in second_device_ids  # noqa: S101
    assert "dev_dup" not in second_device_ids  # noqa: S101
