"""Focused behavioral coverage for sensor payload and registration boundaries."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault import sensor as sensor_module
from custom_components.jackery_solarvault.const import (
    DISCOVERY_SOURCE_LEGACY_BIND_LIST,
    FIELD_IS_CONTRACT_AUTH,
    PAYLOAD_DISCOVERY,
    PAYLOAD_DISCOVERY_SOURCE,
    PAYLOAD_DYNAMIC_PRICE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SYSTEM,
)
from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.const import UnitOfEnergy


@pytest.mark.parametrize(
    ["payload", "props", "expected_home", "expected_portable"],
    [
        [{PAYLOAD_SYSTEM: {"gridStandard": 20}}, None, True, False],
        [{PAYLOAD_PROPERTIES: {"pvPw": 20}}, None, True, False],
        [{"http_properties": {"workModel": 1}}, None, True, False],
        [{}, {"maxOutPw": 2_500}, True, False],
        [
            {
                PAYLOAD_DISCOVERY: {
                    PAYLOAD_DISCOVERY_SOURCE: DISCOVERY_SOURCE_LEGACY_BIND_LIST,
                }
            },
            None,
            False,
            True,
        ],
        [{PAYLOAD_DISCOVERY: []}, None, False, False],
    ],
)
def test_payload_family_detection_uses_positive_protocol_evidence(
    payload: dict[str, Any],
    props: dict[str, Any] | None,
    expected_home: bool,
    expected_portable: bool,
) -> None:
    """Home evidence wins; only legacy-bind metadata identifies portable payloads."""
    assert (
        sensor_module._payload_has_home_payload_evidence(payload, props)  # ruff: ignore[private-member-access]
        is expected_home
    )
    assert (
        sensor_module._is_portable_payload(payload, props)  # ruff: ignore[private-member-access]
        is expected_portable
    )


def test_payload_value_helpers_cover_invalid_and_fallback_shapes() -> None:
    """Getter helpers preserve zeros and reject malformed intermediate containers."""
    path = sensor_module._path  # ruff: ignore[private-member-access]
    assert path({"outer": {"value": 3}}, "outer", "value") == 3
    assert path({"outer": 3}, "outer", "value") is None

    divide = sensor_module._div(10)  # ruff: ignore[private-member-access]
    assert divide("12.34") == pytest.approx(1.23)
    assert divide(None) is None
    assert sensor_module._signed_diff("12", 7) == 5  # ruff: ignore[private-member-access]
    assert sensor_module._signed_diff("bad", 7) is None  # ruff: ignore[private-member-access]
    assert sensor_module._flag_int(True) == 1  # ruff: ignore[private-member-access]
    assert sensor_module._flag_int("2") == 2  # ruff: ignore[private-member-access]
    assert sensor_module._system_meta_scalar_value(False) is None  # ruff: ignore[private-member-access]
    assert sensor_module._system_meta_scalar_value("  ") is None  # ruff: ignore[private-member-access]
    assert sensor_module._system_meta_scalar_value(20) == "20"  # ruff: ignore[private-member-access]
    assert sensor_module._temp_unit_label("1") == "F"  # ruff: ignore[private-member-access]
    assert sensor_module._temp_unit_label(0) == "C"  # ruff: ignore[private-member-access]
    assert sensor_module._temp_unit_label("invalid") is None  # ruff: ignore[private-member-access]


def test_storm_plan_helpers_accept_all_documented_payload_variants() -> None:
    """Lead time may arrive at the root, in a storm row, or as switch-only state."""
    from_plan = sensor_module._storm_minutes_from_plan  # ruff: ignore[private-member-access]
    fallback = sensor_module._storm_minutes_fallback  # ruff: ignore[private-member-access]

    assert from_plan({"wpc": "45"}) == 45
    assert from_plan({"storm": [None, {"minsInterval": 30}]}) == 30
    assert from_plan({"storm": [{"wpc": 0}]}) is None
    assert fallback({"wps": "invalid"}, {}, {}) is None
    assert fallback({"wps": 1}, {}, {}) == sensor_module.DEFAULT_STORM_WARNING_MINUTES
    assert fallback({}, {"wps": 0}, {}) == 0
    assert (
        fallback({}, {"storm": [{}]}, {}) == sensor_module.DEFAULT_STORM_WARNING_MINUTES
    )
    assert fallback({}, {"storm": []}, {}) == 0
    assert fallback({}, {}, {}) is None


def test_getter_factories_preserve_metadata_and_source_semantics() -> None:
    """Generated getters expose their App fields and avoid truthiness data loss."""
    prop_any = sensor_module._prop_any("first", "second")  # ruff: ignore[private-member-access]
    assert prop_any({"first": 0, "second": 2}) == 0
    assert prop_any({"first": None, "second": 2}) == 2
    assert cast("Any", prop_any).app_fields == ("first", "second")

    power_any = sensor_module._prop_power_any("first", "second")  # ruff: ignore[private-member-access]
    assert power_any({"first": "bad", "second": 0}) == 0
    assert power_any({"first": 0, "second": "4.5"}) == pytest.approx(4.5)
    assert power_any({}) is None

    section_field = sensor_module._payload_section_field("section", "value")  # ruff: ignore[private-member-access]
    assert section_field({"section": {"value": 0}}) == 0
    assert section_field({"section": []}) is None

    list_count = sensor_module._payload_section_first_list_count(  # ruff: ignore[private-member-access]
        "section", "primary", "fallback"
    )
    assert list_count({"section": {"primary": "bad", "fallback": [1, 2]}}) == 2
    assert list_count({"section": []}) is None
    assert list_count({"section": {}}) is None

    http_prop = sensor_module._payload_http_prop("power")  # ruff: ignore[private-member-access]
    assert http_prop({"http_properties": {"power": 0}}) == 0
    assert http_prop({"http_properties": []}) is None

    pv_power = sensor_module._pv_channel_power("pv1")  # ruff: ignore[private-member-access]
    assert pv_power({"pv1": {"pvPw": 123}}) == 123
    assert pv_power({"pv1": []}) is None


def test_description_metadata_uses_getter_then_smali_and_explicit_sources() -> None:
    """Description provenance follows the getter, then the Smali field fallback."""
    getter = sensor_module._prop("soc")  # ruff: ignore[private-member-access]
    from_getter = sensor_module.JackerySensorDescription(key="getter", getter=getter)
    from_smali = sensor_module.JackerySensorDescription(
        key="smali", getter=lambda _props: None, smali_field="legacyField"
    )
    explicit = sensor_module.JackerySensorDescription(
        key="explicit",
        getter=getter,
        app_fields=("chosen",),
        data_sources=("manual",),
    )

    assert from_getter.app_fields == ("soc",)
    assert from_getter.data_sources
    assert from_smali.app_fields == ("legacyField",)
    assert from_smali.data_sources
    assert explicit.app_fields == ("chosen",)
    assert explicit.data_sources == ("manual",)


@pytest.mark.parametrize(
    ["previous", "current", "description", "expected"],
    [
        [
            10.0,
            9.99,
            SensorEntityDescription(
                key="energy",
                device_class=SensorDeviceClass.ENERGY,
                state_class=SensorStateClass.TOTAL_INCREASING,
                native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            ),
            10.0,
        ],
        [
            10.0,
            9.0,
            SensorEntityDescription(
                key="reset",
                device_class=SensorDeviceClass.ENERGY,
                state_class=SensorStateClass.TOTAL_INCREASING,
                native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            ),
            9.0,
        ],
        [
            10.0,
            None,
            SensorEntityDescription(
                key="missing",
                device_class=SensorDeviceClass.ENERGY,
                state_class=SensorStateClass.TOTAL_INCREASING,
                native_unit_of_measurement=UnitOfEnergy.WATT_HOUR,
            ),
            10.0,
        ],
        [
            10.0,
            9.99,
            SensorEntityDescription(
                key="measurement",
                device_class=SensorDeviceClass.ENERGY,
                state_class=SensorStateClass.MEASUREMENT,
                native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            ),
            9.99,
        ],
    ],
)
def test_total_increasing_jitter_guard_preserves_only_tiny_regressions(
    previous: object,
    current: object,
    description: SensorEntityDescription,
    expected: object,
) -> None:
    """Tiny source wobble is held, while resets and ordinary measurements pass."""
    assert (
        sensor_module._guard_total_increasing_jitter(  # ruff: ignore[private-member-access]
            previous, current, description
        )
        == expected
    )


@pytest.mark.parametrize(
    ["stored", "expected"],
    [
        [None, None],
        [SimpleNamespace(native_value=True, native_unit_of_measurement="kWh"), None],
        [SimpleNamespace(native_value="4", native_unit_of_measurement="Wh"), None],
        [SimpleNamespace(native_value="bad", native_unit_of_measurement="kWh"), None],
        [
            SimpleNamespace(
                native_value=float("inf"), native_unit_of_measurement="kWh"
            ),
            None,
        ],
        [SimpleNamespace(native_value=-1, native_unit_of_measurement="kWh"), None],
        [SimpleNamespace(native_value="4.5", native_unit_of_measurement=None), 4.5],
    ],
)
async def test_restored_lifetime_energy_rejects_invalid_recorder_state(
    stored: object,
    expected: float | None,
) -> None:
    """Only finite, non-negative lifetime values in a compatible unit restore."""
    entity = SimpleNamespace(async_get_last_sensor_data=AsyncMock(return_value=stored))

    result = await sensor_module._async_restored_lifetime_energy_value(  # ruff: ignore[private-member-access]
        cast("Any", entity), "kWh"
    )

    assert result == expected


def test_jackery_sensor_value_mapping_and_source_attributes() -> None:
    """Fallbacks, value maps, and live-over-HTTP diagnostics share one payload view."""
    description = sensor_module.JackerySensorDescription(
        key="mode",
        getter=sensor_module._prop("mode"),  # ruff: ignore[private-member-access]
        fallbacks=(lambda payload: payload.get("fallback"),),
        value_map={2: "self_consumption"},
    )
    coordinator = MagicMock(name="coordinator")
    coordinator.data = {
        "dev-1": {
            PAYLOAD_PROPERTIES: {"mode": 2},
            "http_properties": {"mode": 1},
        }
    }
    coordinator.last_update_success = True
    sensor = sensor_module.JackerySensor(coordinator, "dev-1", description)

    assert sensor.native_value == "self_consumption"
    assert sensor.extra_state_attributes == {
        "merged_raw_value": 2,
        "http_raw_value": 1,
        "live_source_overrides_http": True,
    }

    coordinator.data["dev-1"] = {
        PAYLOAD_PROPERTIES: {},
        "http_properties": {},
        "fallback": 3,
    }
    assert sensor.native_value == 3
    assert sensor.extra_state_attributes == {
        "merged_raw_value": None,
        "http_raw_value": None,
    }


async def test_sensor_registration_is_stable_and_adds_new_dynamic_family() -> None:
    """Unchanged callbacks add nothing; new authorized dynamic-price data adds once."""
    coordinator = MagicMock(name="coordinator")
    coordinator.data = {"dev-1": {PAYLOAD_PROPERTIES: {}, PAYLOAD_SYSTEM: {}}}
    coordinator.last_update_success = True
    listeners: list[Any] = []

    def _listen(callback: Any) -> Any:  # noqa: ANN401, RUF105
        listeners.append(callback)
        return lambda: None

    coordinator.async_add_listener.side_effect = _listen
    coordinator._has_smart_meter_accessory.return_value = False  # ruff: ignore[private-member-access]
    entry = SimpleNamespace(
        data={},
        options={},
        runtime_data=coordinator,
        async_on_unload=MagicMock(),
    )
    batches: list[list[Any]] = []

    await cast("Any", sensor_module.async_setup_entry)(
        None, entry, lambda entities: batches.append(list(entities))
    )
    assert len(batches) == 1
    assert listeners

    listeners[0]()
    assert len(batches) == 1

    coordinator.data["dev-1"][PAYLOAD_DYNAMIC_PRICE] = {FIELD_IS_CONTRACT_AUTH: True}
    listeners[0]()
    assert len(batches) == 2
    assert any("dynamic" in (entity.unique_id or "") for entity in batches[1])
