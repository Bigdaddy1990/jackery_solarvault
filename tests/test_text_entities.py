"""Behavioural tests for the Jackery text platform.

Real text entities are constructed against a mocked coordinator boundary (no
HA runtime). They assert the native value read from coordinator data and that
setting a value forwards the sanitised payload to the correct coordinator
method, plus validation and portable-vs-home family gating in
``async_setup_entry``.
"""

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault import text as text_mod
from custom_components.jackery_solarvault.const import (
    DISCOVERY_SOURCE_LEGACY_BIND_LIST,
    FIELD_DEVICE_NAME,
    FIELD_GRID_STANDARD,
    FIELD_ID,
    FIELD_PV1,
    FIELD_PV2,
    FIELD_PV3,
    FIELD_PV4,
    FIELD_PV_NAME,
    FIELD_SYSTEM_NAME,
    FIELD_THIRD_PARTY_MQTT_IP,
    PAYLOAD_DEVICE,
    PAYLOAD_DISCOVERY,
    PAYLOAD_DISCOVERY_SOURCE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SYSTEM,
)
from custom_components.jackery_solarvault.text import (
    JackeryDeviceNameText,
    JackeryPvNameText,
    JackerySystemNameText,
    JackeryThirdPartyMqttText,
    async_setup_entry,
)
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

_DEVICE_ID = "dev-1"

_ASYNC_METHODS = (
    "async_set_device_name",
    "async_set_device_nickname",
    "async_set_pv_name",
    "async_set_system_name",
    "async_update_third_party_mqtt_config",
)

_PV_FIELDS = (FIELD_PV1, FIELD_PV2, FIELD_PV3, FIELD_PV4)


def test_grid_standard_text_has_one_class_definition() -> None:
    """A duplicate class cannot silently replace the stable entity contract."""
    module = ast.parse(Path(text_mod.__file__).read_text(encoding="utf-8"))

    definitions = [
        node
        for node in module.body
        if isinstance(node, ast.ClassDef) and node.name == "JackeryGridStandardText"
    ]

    assert len(definitions) == 1


def _coordinator(data: dict[str, Any]) -> MagicMock:
    coord = MagicMock(name="coordinator")
    coord.data = data
    coord.last_update_success = True
    coord.is_device_locally_reachable = MagicMock(return_value=False)
    coord.device_supports_advanced = MagicMock(return_value=True)
    coord.device_bluetooth_key = MagicMock(return_value=None)
    coord.third_party_mqtt_config_plaintext = MagicMock(return_value={})
    for name in _ASYNC_METHODS:
        setattr(coord, name, AsyncMock(return_value=None))
    return coord


def _system_name(data: dict[str, Any]) -> JackerySystemNameText:
    entity = JackerySystemNameText.__new__(JackerySystemNameText)
    mutable = cast("Any", entity)
    mutable.coordinator = _coordinator(data)
    mutable._device_id = _DEVICE_ID
    mutable.async_write_ha_state = MagicMock()
    return entity


def _device_name(data: dict[str, Any]) -> JackeryDeviceNameText:
    entity = JackeryDeviceNameText.__new__(JackeryDeviceNameText)
    mutable = cast("Any", entity)
    mutable.coordinator = _coordinator(data)
    mutable._device_id = _DEVICE_ID
    mutable.async_write_ha_state = MagicMock()
    return entity


def test_device_name_native_value_prefers_http_device_metadata() -> None:
    """The editable device label reads the HTTP property/device response."""
    entity = _device_name({
        _DEVICE_ID: {
            PAYLOAD_DEVICE: {FIELD_DEVICE_NAME: "SolarVault"},
            PAYLOAD_DISCOVERY: {FIELD_DEVICE_NAME: "Discovery fallback"},
        },
    })

    assert entity.native_value == "SolarVault"


async def test_home_device_name_uses_explicit_diy_http_setter() -> None:
    """A Home/D-I-Y device uses device/system/deviceName without Layer 5."""
    entity = _device_name({
        _DEVICE_ID: {
            PAYLOAD_DEVICE: {FIELD_DEVICE_NAME: "Old"},
            PAYLOAD_SYSTEM: {FIELD_ID: "sys-1"},
        },
    })

    await entity.async_set_value("  New Device  ")

    entity.coordinator.async_set_device_name.assert_awaited_once_with(
        _DEVICE_ID,
        "New Device",
    )
    entity.coordinator.async_set_device_nickname.assert_not_awaited()
    cast("MagicMock", entity.async_write_ha_state).assert_called_once()


async def test_portable_device_name_uses_explicit_bind_nickname_setter() -> None:
    """A legacy portable device keeps using the App's bind/nickname endpoint."""
    entity = _device_name({
        _DEVICE_ID: {
            PAYLOAD_DEVICE: {
                FIELD_DEVICE_NAME: "Old",
                PAYLOAD_DISCOVERY_SOURCE: DISCOVERY_SOURCE_LEGACY_BIND_LIST,
            },
        },
    })

    await entity.async_set_value("Portable")

    entity.coordinator.async_set_device_nickname.assert_awaited_once_with(
        _DEVICE_ID,
        "Portable",
    )
    entity.coordinator.async_set_device_name.assert_not_awaited()


def test_system_name_native_value_prefers_system_name() -> None:
    """The editable label reads the stored systemName field."""
    entity = _system_name(
        {_DEVICE_ID: {PAYLOAD_SYSTEM: {FIELD_SYSTEM_NAME: "Vault", FIELD_ID: "sys-1"}}},
    )

    assert entity.native_value == "Vault"


async def test_system_name_set_trims_and_forwards() -> None:
    """Renaming trims whitespace and forwards the system id and new name."""
    entity = _system_name(
        {_DEVICE_ID: {PAYLOAD_SYSTEM: {FIELD_SYSTEM_NAME: "Old", FIELD_ID: "sys-1"}}},
    )

    await entity.async_set_value("  New Name  ")

    entity.coordinator.async_set_system_name.assert_awaited_once_with(
        "sys-1",
        "New Name",
    )
    cast("MagicMock", entity.async_write_ha_state).assert_called_once()


async def test_system_name_missing_id_raises() -> None:
    """Renaming a system with no id raises a translated error before any write."""
    entity = _system_name(
        {_DEVICE_ID: {PAYLOAD_SYSTEM: {FIELD_SYSTEM_NAME: "Old"}}},
    )

    with pytest.raises(HomeAssistantError) as err:
        await entity.async_set_value("New")

    assert err.value.translation_key == "missing_system_id"
    entity.coordinator.async_set_system_name.assert_not_awaited()


async def test_system_name_empty_value_raises() -> None:
    """A whitespace-only name is rejected as an invalid text value."""
    entity = _system_name(
        {_DEVICE_ID: {PAYLOAD_SYSTEM: {FIELD_SYSTEM_NAME: "Old", FIELD_ID: "sys-1"}}},
    )

    with pytest.raises(HomeAssistantError) as err:
        await entity.async_set_value("   ")

    assert err.value.translation_key == "invalid_text_value"


async def test_system_name_auth_error_propagates() -> None:
    """A rejected credential during rename surfaces as ConfigEntryAuthFailed."""
    entity = _system_name(
        {_DEVICE_ID: {PAYLOAD_SYSTEM: {FIELD_SYSTEM_NAME: "Old", FIELD_ID: "sys-1"}}},
    )
    entity.coordinator.async_set_system_name = AsyncMock(
        side_effect=text_mod.JackeryAuthError("nope"),
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await entity.async_set_value("New")


def _third_party(field: str, data: dict[str, Any]) -> JackeryThirdPartyMqttText:
    entity = JackeryThirdPartyMqttText.__new__(JackeryThirdPartyMqttText)
    mutable = cast("Any", entity)
    mutable.coordinator = _coordinator(data)
    mutable._device_id = _DEVICE_ID
    mutable._field = field
    mutable._attr_translation_key = "third_party_mqtt_ip"
    return entity


def test_third_party_native_value_from_plaintext_accessor() -> None:
    """The value comes from the coordinator's plaintext config accessor."""
    entity = _third_party(FIELD_THIRD_PARTY_MQTT_IP, {_DEVICE_ID: {}})
    entity.coordinator.third_party_mqtt_config_plaintext = MagicMock(
        return_value={FIELD_THIRD_PARTY_MQTT_IP: "10.0.0.5"},
    )

    assert entity.native_value == "10.0.0.5"


async def test_third_party_set_forwards_field_update() -> None:
    """Writing a field forwards a single-field config update to the coordinator."""
    entity = _third_party(FIELD_THIRD_PARTY_MQTT_IP, {_DEVICE_ID: {}})

    await entity.async_set_value("  10.0.0.9  ")

    entity.coordinator.async_update_third_party_mqtt_config.assert_awaited_once_with(
        _DEVICE_ID,
        {FIELD_THIRD_PARTY_MQTT_IP: "10.0.0.9"},
    )


async def test_setup_entry_creates_home_text_family() -> None:
    """Grid-standard metadata must not be exposed as an editable text field."""
    coordinator = _coordinator({
        _DEVICE_ID: {
            PAYLOAD_SYSTEM: {FIELD_ID: "sys-1", FIELD_GRID_STANDARD: 20},
            PAYLOAD_PROPERTIES: {"batSoc": 55},
        },
    })
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    entry = SimpleNamespace(runtime_data=coordinator, async_on_unload=MagicMock())
    added: list[Any] = []

    await cast("Any", async_setup_entry)(None, entry, added.extend)

    unique_ids = {ent.unique_id for ent in added}
    assert f"{_DEVICE_ID}_device_name" in unique_ids
    assert f"{_DEVICE_ID}_system_name" in unique_ids
    assert f"{_DEVICE_ID}_grid_standard" in unique_ids
    assert f"{_DEVICE_ID}_third_party_mqtt_ip" in unique_ids


async def test_setup_entry_without_advanced_skips_third_party_fields() -> None:
    """Without advanced support or a BLE key, the MQTT text fields are omitted."""
    coordinator = _coordinator({
        _DEVICE_ID: {
            PAYLOAD_SYSTEM: {FIELD_ID: "sys-1", FIELD_GRID_STANDARD: 20},
        },
    })
    coordinator.device_supports_advanced = MagicMock(return_value=False)
    coordinator.device_bluetooth_key = MagicMock(return_value=None)
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    entry = SimpleNamespace(runtime_data=coordinator, async_on_unload=MagicMock())
    added: list[Any] = []

    await cast("Any", async_setup_entry)(None, entry, added.extend)

    unique_ids = {ent.unique_id for ent in added}
    assert f"{_DEVICE_ID}_system_name" in unique_ids
    assert f"{_DEVICE_ID}_grid_standard" in unique_ids
    assert f"{_DEVICE_ID}_third_party_mqtt_ip" not in unique_ids


def _pv_name(data: dict[str, Any], index: int) -> JackeryPvNameText:
    entity = JackeryPvNameText.__new__(JackeryPvNameText)
    mutable = cast("Any", entity)
    mutable.coordinator = _coordinator(data)
    mutable._device_id = _DEVICE_ID
    mutable._index = index
    mutable._field = _PV_FIELDS[index]
    mutable._attr_translation_key = f"pv{index + 1}_name"
    channel = (
        data.get(_DEVICE_ID, {}).get(PAYLOAD_PROPERTIES, {}).get(_PV_FIELDS[index])
    )
    mutable._cached_native_value = (
        str(channel[FIELD_PV_NAME])
        if isinstance(channel, dict) and channel.get(FIELD_PV_NAME) is not None
        else None
    )
    mutable.async_write_ha_state = MagicMock()
    return entity


def test_pv_name_native_value_reads_channel_name() -> None:
    """The editable PV label reads the stored name from the channel dict."""
    entity = _pv_name(
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_PV1: {FIELD_PV_NAME: "Roof East"}}}},
        0,
    )

    assert entity.native_value == "Roof East"


def test_pv_name_native_value_absent_returns_none() -> None:
    """An absent channel or missing name yields no native value."""
    entity = _pv_name({_DEVICE_ID: {PAYLOAD_PROPERTIES: {}}}, 0)

    assert entity.native_value is None


def test_pv_name_state_calculation_uses_cached_value() -> None:
    """HA state serialization must not traverse the coordinator payload."""
    entity = _pv_name(
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_PV1: {FIELD_PV_NAME: "Roof"}}}},
        0,
    )
    entity.coordinator.data[_DEVICE_ID][PAYLOAD_PROPERTIES][FIELD_PV1][
        FIELD_PV_NAME
    ] = "Changed behind cache"

    assert entity.native_value == "Roof"


async def test_pv_name_set_trims_and_forwards_index_and_name() -> None:
    """Setting a PV name trims whitespace and forwards the channel index + name."""
    entity = _pv_name(
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_PV3: {FIELD_PV_NAME: "Old"}}}},
        2,
    )

    await entity.async_set_value("  Carport  ")

    entity.coordinator.async_set_pv_name.assert_awaited_once_with(
        device_id=_DEVICE_ID,
        index=2,
        name="Carport",
    )
    assert entity.native_value == "Carport"
    cast("MagicMock", entity.async_write_ha_state).assert_called_once()


async def test_pv_name_empty_value_raises() -> None:
    """A whitespace-only PV name is rejected before any coordinator write."""
    entity = _pv_name(
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_PV1: {FIELD_PV_NAME: "Old"}}}},
        0,
    )

    with pytest.raises(HomeAssistantError) as err:
        await entity.async_set_value("   ")

    assert err.value.translation_key == "invalid_text_value"
    entity.coordinator.async_set_pv_name.assert_not_awaited()


async def test_pv_name_auth_error_propagates() -> None:
    """A rejected credential during a PV rename surfaces as ConfigEntryAuthFailed."""
    entity = _pv_name(
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_PV1: {FIELD_PV_NAME: "Old"}}}},
        0,
    )
    entity.coordinator.async_set_pv_name = AsyncMock(
        side_effect=text_mod.JackeryAuthError("nope"),
    )

    with pytest.raises(ConfigEntryAuthFailed):
        await entity.async_set_value("New")


async def test_pv_name_failure_message_uses_one_based_index() -> None:
    """A rename failure reports the 1-based PV label (index 2 -> 'PV input 3')."""
    entity = _pv_name(
        {_DEVICE_ID: {PAYLOAD_PROPERTIES: {FIELD_PV3: {FIELD_PV_NAME: "Old"}}}},
        2,
    )
    entity.coordinator.async_set_pv_name = AsyncMock(
        side_effect=text_mod.JackeryError("nope"),
    )

    with pytest.raises(HomeAssistantError) as err:
        await entity.async_set_value("Carport")

    assert err.value.translation_key == "rename_pv_name_failed"
    assert err.value.translation_placeholders is not None
    assert err.value.translation_placeholders["index"] == "3"


async def test_setup_entry_creates_pv_name_only_for_present_channels() -> None:
    """A home device gets a PV-name entity per present channel and no others."""
    coordinator = _coordinator({
        _DEVICE_ID: {
            PAYLOAD_SYSTEM: {FIELD_ID: "sys-1"},
            PAYLOAD_PROPERTIES: {
                FIELD_PV1: {"pvPw": 100},
                FIELD_PV2: {"pvPw": 0},
            },
        },
    })
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    entry = SimpleNamespace(runtime_data=coordinator, async_on_unload=MagicMock())
    added: list[Any] = []

    await cast("Any", async_setup_entry)(None, entry, added.extend)

    unique_ids = {ent.unique_id for ent in added}
    assert f"{_DEVICE_ID}_pv1_name" in unique_ids
    assert f"{_DEVICE_ID}_pv2_name" in unique_ids
    assert f"{_DEVICE_ID}_pv3_name" not in unique_ids
    assert f"{_DEVICE_ID}_pv4_name" not in unique_ids


async def test_setup_entry_portable_skips_pv_name() -> None:
    """Portable devices never expose the Home-only PV-name entities."""
    coordinator = _coordinator({
        _DEVICE_ID: {
            PAYLOAD_DEVICE: {
                PAYLOAD_DISCOVERY_SOURCE: DISCOVERY_SOURCE_LEGACY_BIND_LIST,
            },
            PAYLOAD_PROPERTIES: {FIELD_PV1: {"pvPw": 100}},
        },
    })
    coordinator.async_add_listener = MagicMock(return_value=lambda: None)
    entry = SimpleNamespace(runtime_data=coordinator, async_on_unload=MagicMock())
    added: list[Any] = []

    await cast("Any", async_setup_entry)(None, entry, added.extend)

    unique_ids = {ent.unique_id for ent in added}
    assert f"{_DEVICE_ID}_device_name" in unique_ids
    assert f"{_DEVICE_ID}_pv1_name" not in unique_ids
