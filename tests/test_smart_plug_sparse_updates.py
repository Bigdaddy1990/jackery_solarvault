"""Optimistic and statistic updates never freeze socket discovery metadata."""

from copy import deepcopy
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.const import (
    FIELD_ACCESSORIES,
    FIELD_CONTROL_ALLOWED,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_SN,
    FIELD_DEV_TYPE,
    FIELD_IN_PW,
    FIELD_IP,
    FIELD_IS_CLOUD,
    FIELD_OP,
    FIELD_OUT_PW,
    FIELD_SWITCH_STATE,
    FIELD_TODAY_ENERGY,
    FIELD_TOTAL_ENERGY,
    PAYLOAD_SMART_PLUGS,
    SUBDEVICE_DEV_TYPE_SOCKET,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
    smart_plug_payload,
)
from custom_components.jackery_solarvault.sensor import (
    SMART_PLUG_SENSOR_DESCRIPTIONS,
    JackerySmartPlugSensor,
)
from custom_components.jackery_solarvault.switch import JackerySmartPlugSwitch
from homeassistant.exceptions import HomeAssistantError


def _coordinator(payload: dict[str, Any]) -> Any:
    """Run production patching with an isolated coordinator data boundary."""
    coordinator = cast(
        "Any", JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    )
    coordinator.data = {"parent": payload}
    coordinator._push_partial_update = MagicMock(  # ruff: ignore[private-member-access]
        side_effect=lambda data: setattr(coordinator, "data", data)
    )
    return coordinator


@pytest.mark.parametrize("existing_telemetry", [False, True])
def test_optimistic_state_never_copies_discovery_or_neighbours(
    existing_telemetry: bool,
) -> None:
    """A control acknowledgement keeps later permission/name/power changes visible."""
    accessory = {
        FIELD_DEVICE_SN: "serial",
        FIELD_DEVICE_ID: "cloud",
        FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET,
        FIELD_CONTROL_ALLOWED: 1,
        FIELD_DEVICE_NAME: "old name",
        FIELD_IN_PW: 100,
    }
    neighbour = {FIELD_DEVICE_SN: "other", FIELD_IN_PW: 55}
    payload: dict[str, Any] = {FIELD_ACCESSORIES: [accessory, neighbour]}
    if existing_telemetry:
        payload[PAYLOAD_SMART_PLUGS] = [
            {FIELD_DEVICE_SN: "serial", FIELD_OUT_PW: 9},
            {FIELD_DEVICE_SN: "telemetry-only", FIELD_OUT_PW: 8},
        ]
    before = deepcopy(payload)
    coordinator = _coordinator(payload)

    coordinator._apply_local_smart_plug_switch_patch("parent", "cloud", True)  # ruff: ignore[private-member-access]

    current = coordinator.data["parent"]
    assert current[FIELD_ACCESSORIES] == before[FIELD_ACCESSORIES]
    assert payload == before
    for telemetry in current[PAYLOAD_SMART_PLUGS]:
        assert FIELD_CONTROL_ALLOWED not in telemetry
        assert FIELD_DEVICE_NAME not in telemetry
        assert FIELD_IN_PW not in telemetry
        assert FIELD_DEVICE_ID not in telemetry
    assert all(
        item[FIELD_DEVICE_SN] != "other" for item in current[PAYLOAD_SMART_PLUGS]
    )
    if existing_telemetry:
        assert current[PAYLOAD_SMART_PLUGS][1] == before[PAYLOAD_SMART_PLUGS][1]
    current[FIELD_ACCESSORIES] = [
        {
            **accessory,
            FIELD_CONTROL_ALLOWED: 0,
            FIELD_DEVICE_NAME: "new name",
            FIELD_DEVICE_ID: "new-cloud-binding",
            FIELD_IN_PW: 20,
        },
        neighbour,
    ]
    resolved = smart_plug_payload(current, "serial")
    assert resolved[FIELD_SWITCH_STATE] == 1
    assert resolved[FIELD_CONTROL_ALLOWED] == 0
    assert resolved[FIELD_DEVICE_NAME] == "new name"
    assert resolved[FIELD_DEVICE_ID] == "new-cloud-binding"
    assert resolved[FIELD_IN_PW] == 20  # ruff: ignore[magic-value-comparison]


async def test_statistics_keep_discovery_permissions_live() -> None:
    """Read-only energy updates leave current discovery metadata authoritative."""
    accessory = {
        FIELD_DEVICE_SN: "serial",
        FIELD_DEVICE_ID: "cloud",
        FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET,
        FIELD_CONTROL_ALLOWED: 1,
        FIELD_IN_PW: 100,
    }
    entry: dict[str, Any] = {FIELD_ACCESSORIES: [accessory]}
    coordinator = _coordinator(entry)
    coordinator._slow_cache = {}  # ruff: ignore[private-member-access]
    coordinator._slow_metrics_interval_sec = 60  # ruff: ignore[private-member-access]
    coordinator.api = MagicMock()
    coordinator._async_get_with_ttl_for = AsyncMock(  # ruff: ignore[private-member-access]
        return_value={FIELD_TODAY_ENERGY: 2.5, FIELD_TOTAL_ENERGY: 9.5}
    )

    await coordinator._async_enrich_smart_plug_statistics("parent", entry)  # ruff: ignore[private-member-access]

    telemetry = entry[PAYLOAD_SMART_PLUGS][0]
    assert FIELD_CONTROL_ALLOWED not in telemetry
    assert FIELD_IN_PW not in telemetry
    assert FIELD_DEVICE_ID not in telemetry
    entry[FIELD_ACCESSORIES] = [
        {**accessory, FIELD_CONTROL_ALLOWED: 0, FIELD_IN_PW: 20}
    ]
    resolved = smart_plug_payload(entry, "serial")
    assert resolved[FIELD_CONTROL_ALLOWED] == 0
    assert resolved[FIELD_IN_PW] == 20  # ruff: ignore[magic-value-comparison]
    assert resolved[FIELD_TOTAL_ENERGY] == pytest.approx(9.5)


@pytest.mark.parametrize("update_kind", ["optimistic", "statistics"])
async def test_discovery_revocation_blocks_writes_after_sparse_update(
    update_kind: str,
) -> None:
    """Actual relay writes reject revoked access after optimistic or energy updates."""
    accessory = {
        FIELD_DEVICE_SN: "serial",
        FIELD_DEVICE_ID: "cloud",
        FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET,
        FIELD_IS_CLOUD: 1,
        FIELD_CONTROL_ALLOWED: 1,
    }
    entry: dict[str, Any] = {FIELD_ACCESSORIES: [accessory]}
    coordinator = _coordinator(entry)
    boundary = MagicMock()
    boundary.data = coordinator.data
    boundary.config_entry = None
    boundary.device_supports_advanced.return_value = False
    boundary.async_set_shelly_cloud_switch = AsyncMock()
    relay = JackerySmartPlugSwitch(
        boundary, "parent", plug_index=1, plug_sn="serial", plug_key="smart_plug_serial"
    )
    identity = relay.unique_id
    await relay.async_turn_on()
    boundary.async_set_shelly_cloud_switch.assert_awaited_once_with(
        "parent", shelly_device_id="cloud", on=True
    )
    boundary.async_set_shelly_cloud_switch.reset_mock()
    if update_kind == "optimistic":
        coordinator._apply_local_smart_plug_switch_patch("parent", "cloud", True)  # ruff: ignore[private-member-access]
    else:
        coordinator._slow_cache = {}  # ruff: ignore[private-member-access]
        coordinator._slow_metrics_interval_sec = 60  # ruff: ignore[private-member-access]
        coordinator.api = MagicMock()
        coordinator._async_get_with_ttl_for = AsyncMock(  # ruff: ignore[private-member-access]
            return_value={FIELD_TOTAL_ENERGY: 9.5}
        )
        await coordinator._async_enrich_smart_plug_statistics("parent", entry)  # ruff: ignore[private-member-access]
    boundary.data = coordinator.data
    boundary.data["parent"][FIELD_ACCESSORIES] = [
        {**accessory, FIELD_CONTROL_ALLOWED: 0}
    ]

    with pytest.raises(HomeAssistantError) as err:
        await relay.async_turn_off()

    assert err.value.translation_placeholders is not None
    assert (
        err.value.translation_placeholders["error"] == "Shelly control is not allowed"
    )
    boundary.async_set_shelly_cloud_switch.assert_not_awaited()
    assert relay.unique_id == identity


async def test_shared_cloud_statistic_id_has_no_unambiguous_owner() -> None:
    """A statistic endpoint cannot assign one cloud device's energy to two sockets."""
    entry: dict[str, Any] = {
        FIELD_ACCESSORIES: [
            {
                FIELD_DEVICE_SN: serial,
                FIELD_DEVICE_ID: "shared-cloud",
                FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET,
            }
            for serial in ("first", "second")
        ]
    }
    coordinator = _coordinator(entry)
    coordinator._slow_cache = {}  # ruff: ignore[private-member-access]
    coordinator._slow_metrics_interval_sec = 60  # ruff: ignore[private-member-access]
    coordinator.api = MagicMock()
    coordinator._async_get_with_ttl_for = AsyncMock(  # ruff: ignore[private-member-access]
        return_value={FIELD_TOTAL_ENERGY: 9.5}
    )
    before = deepcopy(entry)

    await coordinator._async_enrich_smart_plug_statistics("parent", entry)  # ruff: ignore[private-member-access]

    assert entry == before
    coordinator._async_get_with_ttl_for.assert_not_awaited()  # ruff: ignore[private-member-access]


@pytest.mark.parametrize(
    "value",
    ["192.168.1.2", "2001:db8::1", "fe80::1%eth0", "NaN", "inf", True, "bad", None],
)
@pytest.mark.parametrize("alias", [FIELD_IP, FIELD_OP])
def test_non_numeric_power_alias_does_not_mask_discovery(
    value: Any, alias: str
) -> None:
    """Network addresses and invalid values cannot replace valid socket power."""
    canonical = FIELD_IN_PW if alias == FIELD_IP else FIELD_OUT_PW
    payload = {
        FIELD_ACCESSORIES: [
            {
                FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET,
                FIELD_DEVICE_SN: "serial",
                canonical: 42,
            }
        ],
        PAYLOAD_SMART_PLUGS: [{FIELD_DEVICE_SN: "serial", alias: value}],
    }
    assert smart_plug_payload(payload, "serial")[canonical] == 42  # ruff: ignore[magic-value-comparison]


@pytest.mark.parametrize(
    ["value", "numeric"],
    [
        [0, 0],
        ["0", 0],
        [12, 12],
        ["12", 12],
        [12.5, 12.5],
        ["12,5", 12.5],
        ["12.5", 12.5],
    ],
)
@pytest.mark.parametrize("alias", [FIELD_IP, FIELD_OP])
def test_numeric_power_alias_preserves_accepted_source_priority(
    value: Any, numeric: float, alias: str
) -> None:
    """Finite numeric aliases, including zero, still override discovery values."""
    canonical = FIELD_IN_PW if alias == FIELD_IP else FIELD_OUT_PW
    payload = {
        FIELD_ACCESSORIES: [
            {
                FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET,
                FIELD_DEVICE_SN: "serial",
                canonical: 42,
            }
        ],
        PAYLOAD_SMART_PLUGS: [{FIELD_DEVICE_SN: "serial", alias: value}],
    }
    assert smart_plug_payload(payload, "serial")[canonical] == numeric


@pytest.mark.parametrize("alias", [FIELD_IP, FIELD_OP])
@pytest.mark.parametrize(
    ["value", "expected"],
    [["12,5", 12], ["12.5", 12], [12.5, 12], [0, 0], ["0", 0]],
)
def test_numeric_alias_reaches_real_sensor_state(
    alias: str, value: Any, expected: int
) -> None:
    """Validated numeric power reaches the actual HA sensor's native state."""
    boundary = MagicMock()
    boundary.config_entry = None
    boundary.data = {
        "parent": {PAYLOAD_SMART_PLUGS: [{FIELD_DEVICE_SN: "serial", alias: value}]}
    }
    key = "input_power" if alias == FIELD_IP else "output_power"
    description = next(
        item for item in SMART_PLUG_SENSOR_DESCRIPTIONS if item.key == key
    )
    entity = JackerySmartPlugSensor(
        boundary,
        "parent",
        identity=(1, "serial", "smart_plug_serial"),
        description=description,
    )

    entity._refresh_cache()  # ruff: ignore[private-member-access]

    assert entity.native_value == expected


@pytest.mark.parametrize("alias", [FIELD_IP, FIELD_OP])
@pytest.mark.parametrize("value", [True, float("inf"), float("nan"), "2001:db8::1"])
def test_invalid_alias_without_discovery_remains_unknown(
    alias: str, value: Any
) -> None:
    """Rejected power aliases never re-enter the sensor transform as raw values."""
    boundary = MagicMock()
    boundary.config_entry = None
    boundary.data = {
        "parent": {PAYLOAD_SMART_PLUGS: [{FIELD_DEVICE_SN: "serial", alias: value}]}
    }
    key = "input_power" if alias == FIELD_IP else "output_power"
    description = next(
        item for item in SMART_PLUG_SENSOR_DESCRIPTIONS if item.key == key
    )
    entity = JackerySmartPlugSensor(
        boundary,
        "parent",
        identity=(1, "serial", "smart_plug_serial"),
        description=description,
    )

    entity._refresh_cache()  # ruff: ignore[private-member-access]

    assert entity.native_value is None
