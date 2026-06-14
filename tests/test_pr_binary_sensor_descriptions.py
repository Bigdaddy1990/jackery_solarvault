"""Tests for the binary_sensor.py platform updated in this PR.

Covers:
- BINARY_DESCRIPTIONS count, structure and required fields
- JackeryBinarySensor.is_on property for each description
- JackeryBinarySensor entity_registry_enabled_default behavior (DIAGNOSTIC = disabled)
- Getter delegation to _properties vs _device_meta per description
- safe_bool coercion through the getter path
- Boundary conditions: None state, truthy/falsy values

All tests use lightweight stubs so no Home Assistant fixtures are required.
"""

from typing import Any
from unittest.mock import MagicMock

from custom_components.jackery_solarvault.binary_sensor import (
    BINARY_DESCRIPTIONS,
    JackeryBinaryDescription,
    JackeryBinarySensor,
)
from custom_components.jackery_solarvault.const import (
    FIELD_ETH_PORT,
    FIELD_ONLINE_STATUS,
    FIELD_SW_EPS_STATE,
    PAYLOAD_DEVICE,
    PAYLOAD_PROPERTIES,
)
from homeassistant.components.binary_sensor import BinarySensorDeviceClass
from homeassistant.const import EntityCategory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_coordinator(
    device_id: str = "dev_abc",
    properties: dict[str, Any] | None = None,
    device_meta: dict[str, Any] | None = None,
) -> MagicMock:
    """Build a minimal coordinator mock with data for the given device."""
    coordinator = MagicMock()
    coordinator.data = {
        device_id: {
            PAYLOAD_PROPERTIES: properties or {},
            PAYLOAD_DEVICE: device_meta or {},
        }
    }
    return coordinator


def _make_sensor(
    description: JackeryBinaryDescription,
    device_id: str = "dev_abc",
    properties: dict[str, Any] | None = None,
    device_meta: dict[str, Any] | None = None,
) -> JackeryBinarySensor:
    """Construct a JackeryBinarySensor with a mock coordinator."""
    coordinator = _make_coordinator(device_id, properties, device_meta)
    return JackeryBinarySensor(coordinator, device_id, description)


# ---------------------------------------------------------------------------
# BINARY_DESCRIPTIONS structure
# ---------------------------------------------------------------------------


class TestBinaryDescriptionsStructure:
    """Structural tests for BINARY_DESCRIPTIONS."""

    def test_is_a_tuple(self) -> None:
        """BINARY_DESCRIPTIONS must be a tuple."""
        assert isinstance(BINARY_DESCRIPTIONS, tuple)

    def test_count_is_three(self) -> None:
        """BINARY_DESCRIPTIONS must have exactly 3 entries (online, eps_active, eth_connected)."""
        assert len(BINARY_DESCRIPTIONS) == 3, (  # noqa: PLR2004
            f"Expected 3 binary descriptions, got {len(BINARY_DESCRIPTIONS)}: "
            f"{[d.key for d in BINARY_DESCRIPTIONS]}"
        )

    def test_all_entries_are_jackery_binary_description(self) -> None:
        """All entries must be JackeryBinaryDescription instances."""
        for desc in BINARY_DESCRIPTIONS:
            assert isinstance(desc, JackeryBinaryDescription), (
                f"Entry {desc!r} is not a JackeryBinaryDescription"
            )

    def test_all_entries_have_getter(self) -> None:
        """All entries must have a callable getter."""
        for desc in BINARY_DESCRIPTIONS:
            assert callable(desc.getter), (
                f"Description '{desc.key}' has non-callable getter: {desc.getter!r}"
            )

    def test_all_entries_have_unique_keys(self) -> None:
        """All description keys must be unique."""
        keys = [d.key for d in BINARY_DESCRIPTIONS]
        assert len(keys) == len(set(keys)), f"Duplicate keys: {keys}"

    def test_all_entries_have_translation_key(self) -> None:
        """All entries must have a non-empty translation_key."""
        for desc in BINARY_DESCRIPTIONS:
            assert desc.translation_key, (
                f"Description '{desc.key}' has invalid translation_key: {desc.translation_key!r}"
            )
            assert isinstance(desc.translation_key, str), (
                f"Description '{desc.key}' has invalid translation_key: {desc.translation_key!r}"
            )

    def test_all_entries_have_diagnostic_category(self) -> None:
        """All binary descriptions must be diagnostic entities."""
        for desc in BINARY_DESCRIPTIONS:
            assert desc.entity_category == EntityCategory.DIAGNOSTIC, (
                f"Description '{desc.key}' has entity_category={desc.entity_category!r}, expected DIAGNOSTIC"
            )

    def test_all_entries_have_device_class(self) -> None:
        """All entries must have a device_class set."""
        for desc in BINARY_DESCRIPTIONS:
            assert desc.device_class is not None, (
                f"Description '{desc.key}' is missing device_class"
            )

    # --- Per-entry validation ---

    def test_online_key_present_with_connectivity_class(self) -> None:
        """'online' must have device_class CONNECTIVITY."""
        online = next(d for d in BINARY_DESCRIPTIONS if d.key == "online")
        assert online.device_class == BinarySensorDeviceClass.CONNECTIVITY

    def test_eps_active_key_present_with_running_class(self) -> None:
        """'eps_active' must have device_class RUNNING."""
        eps = next(d for d in BINARY_DESCRIPTIONS if d.key == "eps_active")
        assert eps.device_class == BinarySensorDeviceClass.RUNNING

    def test_eth_connected_key_present_with_connectivity_class(self) -> None:
        """'eth_connected' must have device_class CONNECTIVITY."""
        eth = next(d for d in BINARY_DESCRIPTIONS if d.key == "eth_connected")
        assert eth.device_class == BinarySensorDeviceClass.CONNECTIVITY


# ---------------------------------------------------------------------------
# JackeryBinarySensor — entity registry flags
# ---------------------------------------------------------------------------


class TestJackeryBinarySensorEntityFlags:
    """Tests for JackeryBinarySensor entity meta-properties."""

    def test_diagnostic_sensor_is_disabled_by_default(self) -> None:
        """All DIAGNOSTIC binary sensors must be disabled in the entity registry by default."""
        for desc in BINARY_DESCRIPTIONS:
            sensor = _make_sensor(desc)
            assert sensor._attr_entity_registry_enabled_default is False, (
                f"Sensor '{desc.key}' should be disabled by default (DIAGNOSTIC)"
            )

    def test_unique_id_is_device_id_and_key(self) -> None:
        """Unique ID must be '<device_id>_<key>'."""
        for desc in BINARY_DESCRIPTIONS:
            sensor = _make_sensor(desc, device_id="device_123")
            assert sensor._attr_unique_id == f"device_123_{desc.key}", (
                f"Sensor '{desc.key}' has unexpected unique_id: {sensor._attr_unique_id!r}"
            )

    def test_entity_description_is_set(self) -> None:
        """The entity_description attribute must be the same object passed to __init__."""
        for desc in BINARY_DESCRIPTIONS:
            sensor = _make_sensor(desc)
            assert sensor.entity_description is desc


# ---------------------------------------------------------------------------
# JackeryBinarySensor.is_on — getter delegation
# ---------------------------------------------------------------------------


class TestJackeryBinarySensorIsOn:
    """Tests for JackeryBinarySensor.is_on property."""

    # --- 'online' sensor (reads from device_meta) ---

    def test_online_is_on_when_online_status_truthy(self) -> None:
        """'online' sensor must return True when FIELD_ONLINE_STATUS is truthy."""
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "online")
        sensor = _make_sensor(desc, device_meta={FIELD_ONLINE_STATUS: 1})
        assert sensor.is_on is True

    def test_online_is_off_when_online_status_falsy(self) -> None:
        """'online' sensor must return False when FIELD_ONLINE_STATUS is 0."""
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "online")
        sensor = _make_sensor(desc, device_meta={FIELD_ONLINE_STATUS: 0})
        assert sensor.is_on is False

    def test_online_is_none_when_online_status_missing(self) -> None:
        """'online' sensor must return None when FIELD_ONLINE_STATUS is absent."""
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "online")
        sensor = _make_sensor(desc, device_meta={})
        assert sensor.is_on is None

    def test_online_reads_from_device_meta_not_properties(self) -> None:
        """'online' getter reads FIELD_ONLINE_STATUS from device_meta, ignoring properties."""
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "online")
        # Put value in properties but NOT in device_meta — must return None
        sensor = _make_sensor(
            desc,
            properties={FIELD_ONLINE_STATUS: 1},  # in wrong payload section
            device_meta={},  # correct section is empty
        )
        assert sensor.is_on is None

    # --- 'eps_active' sensor (reads from properties) ---

    def test_eps_active_is_on_when_eps_state_truthy(self) -> None:
        """'eps_active' sensor must return True when FIELD_SW_EPS_STATE is truthy."""
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "eps_active")
        sensor = _make_sensor(desc, properties={FIELD_SW_EPS_STATE: 1})
        assert sensor.is_on is True

    def test_eps_active_is_off_when_eps_state_zero(self) -> None:
        """'eps_active' sensor must return False when FIELD_SW_EPS_STATE is 0."""
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "eps_active")
        sensor = _make_sensor(desc, properties={FIELD_SW_EPS_STATE: 0})
        assert sensor.is_on is False

    def test_eps_active_is_none_when_eps_state_missing(self) -> None:
        """'eps_active' sensor must return None when FIELD_SW_EPS_STATE is absent."""
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "eps_active")
        sensor = _make_sensor(desc, properties={})
        assert sensor.is_on is None

    def test_eps_active_reads_from_properties_not_device_meta(self) -> None:
        """'eps_active' getter reads FIELD_SW_EPS_STATE from properties, not device_meta."""
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "eps_active")
        sensor = _make_sensor(
            desc,
            properties={},
            device_meta={FIELD_SW_EPS_STATE: 1},  # in wrong section
        )
        assert sensor.is_on is None

    # --- 'eth_connected' sensor (reads from properties) ---

    def test_eth_connected_is_on_when_eth_port_truthy(self) -> None:
        """'eth_connected' sensor must return True when FIELD_ETH_PORT is truthy."""
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "eth_connected")
        sensor = _make_sensor(desc, properties={FIELD_ETH_PORT: 1})
        assert sensor.is_on is True

    def test_eth_connected_is_off_when_eth_port_zero(self) -> None:
        """'eth_connected' sensor must return False when FIELD_ETH_PORT is 0."""
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "eth_connected")
        sensor = _make_sensor(desc, properties={FIELD_ETH_PORT: 0})
        assert sensor.is_on is False

    def test_eth_connected_is_none_when_eth_port_absent(self) -> None:
        """'eth_connected' sensor must return None when FIELD_ETH_PORT is absent."""
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "eth_connected")
        sensor = _make_sensor(desc, properties={})
        assert sensor.is_on is None

    # --- safe_bool coercion through the getter ---

    def test_is_on_returns_true_for_integer_one(self) -> None:
        """Getter value of 1 (int) must map to True through safe_bool."""
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "eps_active")
        sensor = _make_sensor(desc, properties={FIELD_SW_EPS_STATE: 1})
        assert sensor.is_on is True

    def test_is_on_returns_false_for_integer_zero(self) -> None:
        """Getter value of 0 (int) must map to False through safe_bool."""
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "eps_active")
        sensor = _make_sensor(desc, properties={FIELD_SW_EPS_STATE: 0})
        assert sensor.is_on is False

    def test_is_on_returns_none_for_none_getter_value(self) -> None:
        """Getter value of None must produce None through safe_bool."""
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "eps_active")
        sensor = _make_sensor(desc, properties={FIELD_SW_EPS_STATE: None})
        assert sensor.is_on is None

    def test_is_on_returns_true_for_bool_true(self) -> None:
        """Getter value True (bool) must produce True through safe_bool."""
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "eth_connected")
        sensor = _make_sensor(desc, properties={FIELD_ETH_PORT: True})
        assert sensor.is_on is True

    def test_is_on_returns_false_for_bool_false(self) -> None:
        """Getter value False (bool) must produce False through safe_bool."""
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "eth_connected")
        sensor = _make_sensor(desc, properties={FIELD_ETH_PORT: False})
        assert sensor.is_on is False


# ---------------------------------------------------------------------------
# JackeryBinarySensor — no payload scenario
# ---------------------------------------------------------------------------


class TestJackeryBinarySensorNoPayload:
    """Tests for JackeryBinarySensor when coordinator has no data."""

    def test_is_on_returns_none_when_coordinator_data_is_none(self) -> None:
        """is_on must return None when coordinator.data is None."""
        coordinator = MagicMock()
        coordinator.data = None
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "online")
        sensor = JackeryBinarySensor(coordinator, "dev1", desc)
        assert sensor.is_on is None

    def test_is_on_returns_none_when_device_not_in_coordinator_data(self) -> None:
        """is_on must return None when the device_id is absent from coordinator.data."""
        coordinator = MagicMock()
        coordinator.data = {"other_device": {}}
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "online")
        sensor = JackeryBinarySensor(coordinator, "missing_device", desc)
        assert sensor.is_on is None

    def test_is_on_returns_none_when_properties_section_missing(self) -> None:
        """is_on must return None when PAYLOAD_PROPERTIES is absent from device payload."""
        coordinator = MagicMock()
        coordinator.data = {"dev1": {}}  # no properties key
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "eps_active")
        sensor = JackeryBinarySensor(coordinator, "dev1", desc)
        assert sensor.is_on is None


# ---------------------------------------------------------------------------
# Getter isolation tests — verify each getter only reads from the right section
# ---------------------------------------------------------------------------


class TestGetterIsolation:
    """Verify getter functions only read from their designated payload section."""

    def test_online_getter_reads_only_device_meta(self) -> None:
        """'online' getter must use the second argument (device_meta), not properties."""
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "online")
        # Confirm calling getter directly: (properties, device_meta)
        result = desc.getter({}, {FIELD_ONLINE_STATUS: 1})
        assert result == 1

    def test_online_getter_ignores_properties(self) -> None:
        """'online' getter must ignore the first argument (properties)."""
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "online")
        result = desc.getter({FIELD_ONLINE_STATUS: 99}, {})
        assert result is None  # online reads from device_meta (second arg), not properties

    def test_eps_active_getter_reads_only_properties(self) -> None:
        """'eps_active' getter must use the first argument (properties), not device_meta."""
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "eps_active")
        result = desc.getter({FIELD_SW_EPS_STATE: 1}, {})
        assert result == 1

    def test_eps_active_getter_ignores_device_meta(self) -> None:
        """'eps_active' getter must ignore device_meta (second arg)."""
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "eps_active")
        result = desc.getter({}, {FIELD_SW_EPS_STATE: 1})
        assert result is None

    def test_eth_connected_getter_reads_only_properties(self) -> None:
        """'eth_connected' getter must use properties (first arg)."""
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "eth_connected")
        result = desc.getter({FIELD_ETH_PORT: 1}, {})
        assert result == 1

    def test_eth_connected_getter_ignores_device_meta(self) -> None:
        """'eth_connected' getter must ignore device_meta (second arg)."""
        desc = next(d for d in BINARY_DESCRIPTIONS if d.key == "eth_connected")
        result = desc.getter({}, {FIELD_ETH_PORT: 1})
        assert result is None
