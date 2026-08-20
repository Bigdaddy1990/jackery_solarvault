"""Regression: HomePower devices dispatch through the identical Home seam as SolarVault.

Locks in the verified fact that the discovery / classification / entity seam
never special-cases a SolarVault-specific ``modelCode`` or ``devModel``
literal. Every Home-family SKU (docs/source-of-truth/APP/smali_classes4/cb/
b.smali: 3001=HOME_011, 3002=HOME_013 [SolarVault reference model
HTH0132500A], 3003=HOME_014, 3004=HOME_010 — the constant-NAME suffix is not
the modelCode, the literal integer is) must be:

1. admitted by ``_is_property_device_candidate`` (coordinator.py) — a
   presence-only gate with no modelCode/devModel comparison;
2. discovered into ``_device_index`` with the same shape ``async_discover``
   produces for any system/list device;
3. classified as a Home (non-portable) payload by ``_has_home_payload_evidence``
   / ``_is_portable_payload`` (sensor.py);
4. eligible for the same Home-only sensor description
   (``SMART_MODE_SENSOR_DESCRIPTIONS``) SolarVault gets — proven by
   constructing the real ``JackerySensor`` entity class against it.

This is a TEST-ONLY regression lock: no production code changes accompany it.
"""

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault.const import (
    DISCOVERY_SOURCE_SYSTEM_LIST,
    FIELD_BAT_IN_PW,
    FIELD_BAT_OUT_PW,
    FIELD_BAT_SOC,
    FIELD_BIND_KEY,
    FIELD_CELL_TEMP,
    FIELD_CODE,
    FIELD_DATA,
    FIELD_DEVICES,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_SN,
    FIELD_DEV_MODEL,
    FIELD_ID,
    FIELD_IN_PW,
    FIELD_MODEL_CODE,
    FIELD_ONLINE,
    FIELD_OUT_PW,
    FIELD_SOC,
    FIELD_SYSTEM_ID,
    FIELD_WNAME,
    PAYLOAD_DEVICE,
    PAYLOAD_DEVICE_META,
    PAYLOAD_DISCOVERY_SOURCE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_SYSTEM_META,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from custom_components.jackery_solarvault.entity import payload_properties_for_sources
from custom_components.jackery_solarvault.sensor import (
    SMART_MODE_SENSOR_DESCRIPTIONS,
    JackerySensor,
    _has_home_payload_evidence,  # test drives the module-private Home/Portable classifier
    _is_portable_payload,  # test drives the module-private Home/Portable classifier
)

if TYPE_CHECKING:
    from custom_components.jackery_solarvault.sensor import JackerySensorDescription

# Home device family SKUs (docs/source-of-truth/APP/smali_classes4/cb/b.smali).
HOME_MODEL_CODES = (3001, 3002, 3003, 3004)

_SYSTEM_ID = "512000000000099001"
_DEVICE_SN = "HTH000000000099"


def _home_power_dev_id(model_code: int) -> str:
    """Return a stable per-SKU device id for test isolation."""
    return f"home-power-{model_code}"


def _home_power_dev_entry(model_code: int) -> dict[str, Any]:
    """Return a HomePower device dict shaped like a system/list entry."""
    return {
        FIELD_DEVICE_ID: _home_power_dev_id(model_code),
        FIELD_DEVICE_SN: _DEVICE_SN,
        FIELD_MODEL_CODE: model_code,
        FIELD_DEV_MODEL: "HomePower HTH0132500A",
        FIELD_BIND_KEY: 1,
    }


def _home_power_property_payload(dev_id: str, model_code: int) -> dict[str, Any]:
    """Return a ``/v1/device/property``-shaped payload for one HomePower SKU.

    Mirrors ``_default_property_payload`` in ``_update_cycle_fixture.py`` (same
    Home-body evidence fields: batSoc/batInPw/batOutPw), swapping only the
    device identity so the same telemetry shape resolves for every Home SKU.
    """
    return {
        PAYLOAD_DEVICE: {
            FIELD_DEVICE_ID: dev_id,
            FIELD_DEVICE_SN: _DEVICE_SN,
            FIELD_MODEL_CODE: model_code,
            FIELD_ONLINE: 1,
            "activated": 1,
        },
        PAYLOAD_PROPERTIES: {
            FIELD_WNAME: "HomePower",
            FIELD_ONLINE: 1,
            FIELD_SOC: 62,
            FIELD_BAT_SOC: 62,
            FIELD_IN_PW: 0,
            FIELD_OUT_PW: 350,
            FIELD_BAT_IN_PW: 0,
            FIELD_BAT_OUT_PW: 350,
            FIELD_CELL_TEMP: 24,
        },
    }


def _discovery_coordinator(*, systems: list[Any]) -> JackerySolarVaultCoordinator:
    """Build a bare coordinator whose discovery boundaries are mocked.

    Mirrors the pattern in ``test_coordinator_discovery_filter.py`` — a
    ``JackerySolarVaultCoordinator.__new__`` instance with only the discovery
    I/O boundary mocked, so ``async_discover`` runs its real admission and
    classification logic unmocked. ``_async_enumerate_http_accessories`` is
    also stubbed: it is an HTTP-accessories overlay step orthogonal to
    admission/index-population, and otherwise reads ``_endpoint_backoff``
    state that only the real ``__init__`` (skipped here) initialises.
    """
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    coordinator._device_index = {}
    coordinator._pending_discovery_parent_removals = set()
    mutable = cast("Any", coordinator)
    mutable.api = SimpleNamespace(
        async_get_system_list=AsyncMock(return_value=systems),
        async_list_devices_legacy=AsyncMock(return_value=[]),
        async_sync_smart_accessories=AsyncMock(return_value=None),
        async_get_accessories_list=AsyncMock(return_value=[]),
        last_system_list_response={FIELD_CODE: 0, FIELD_DATA: systems},
        last_legacy_device_list_response={FIELD_CODE: 0, FIELD_DATA: []},
    )
    mutable._async_save_discovery_cache = AsyncMock()
    mutable._schedule_background_once = lambda *_args, **_kwargs: None
    return coordinator


def _home_power_sensor(
    dev_id: str,
    payload: dict[str, Any],
    description: JackerySensorDescription,
) -> JackerySensor:
    """Build a bare ``JackerySensor`` bound to a single-device coordinator snapshot.

    Mirrors the ``.__new__`` + manual-attribute pattern in
    ``test_stat_entity_values.py`` — bypasses ``__init__`` so the entity class
    exercises only its real ``native_value``/``unique_id`` production logic,
    without hass wiring.
    """
    sensor = JackerySensor.__new__(JackerySensor)
    mutable = cast("Any", sensor)
    mutable.coordinator = SimpleNamespace(data={dev_id: payload})
    mutable._device_id = dev_id
    mutable._attr_unique_id = f"{dev_id}_{description.key}"
    mutable.entity_description = description
    return sensor


@pytest.mark.parametrize("model_code", HOME_MODEL_CODES)
def test_home_modelcode_is_admitted_as_property_device_candidate(
    model_code: int,
) -> None:
    """Every Home-family SKU passes the presence-only admission gate.

    ``_is_property_device_candidate`` never compares modelCode/devModel
    against a SolarVault-specific literal — it only checks that bindKey is
    truthy, the entry is not a cloud-devType-3 accessory, and either
    modelCode or devModel is present. HomePower satisfies all three exactly
    like SolarVault does.
    """
    dev = _home_power_dev_entry(model_code)

    assert JackerySolarVaultCoordinator._is_property_device_candidate(dev) is True


@pytest.mark.parametrize("model_code", HOME_MODEL_CODES)
@pytest.mark.asyncio
async def test_home_modelcode_is_discovered_into_device_index(
    model_code: int,
) -> None:
    """Every Home-family SKU lands in ``_device_index`` with the standard shape.

    Same three-key shape ``async_discover`` produces for any accepted
    system/list device (coordinator.py:3907-3911), tagged with the
    system/list discovery source.
    """
    dev_id = _home_power_dev_id(model_code)
    system_entry = {
        FIELD_ID: _SYSTEM_ID,
        FIELD_SYSTEM_ID: _SYSTEM_ID,
        "systemName": "Home",
        FIELD_DEVICES: [_home_power_dev_entry(model_code)],
    }
    coordinator = _discovery_coordinator(systems=[system_entry])

    await coordinator.async_discover()

    assert dev_id in coordinator._device_index
    indexed = coordinator._device_index[dev_id]
    assert set(indexed) == {FIELD_SYSTEM_ID, PAYLOAD_SYSTEM_META, PAYLOAD_DEVICE_META}
    assert indexed[FIELD_SYSTEM_ID] == _SYSTEM_ID
    assert indexed[PAYLOAD_DEVICE_META][FIELD_MODEL_CODE] == model_code
    assert (
        indexed[PAYLOAD_DEVICE_META][PAYLOAD_DISCOVERY_SOURCE]
        == DISCOVERY_SOURCE_SYSTEM_LIST
    )


@pytest.mark.asyncio
async def test_empty_outer_system_list_is_not_parent_removal_evidence() -> None:
    """A transient explicit ``data=[]`` keeps a known system parent."""
    system_entry = {
        FIELD_ID: _SYSTEM_ID,
        FIELD_SYSTEM_ID: _SYSTEM_ID,
        FIELD_DEVICES: [_home_power_dev_entry(3002)],
    }
    coordinator = _discovery_coordinator(systems=[system_entry])
    await coordinator.async_discover()

    mutable = cast("Any", coordinator)
    mutable.api.async_get_system_list = AsyncMock(return_value=[])
    mutable.api.last_system_list_response = {FIELD_CODE: 0, FIELD_DATA: []}
    await coordinator.async_discover()

    assert _home_power_dev_id(3002) in coordinator._device_index


@pytest.mark.asyncio
async def test_identified_system_with_empty_devices_confirms_parent_removal() -> None:
    """Two complete system omissions remove the previously indexed parent."""
    populated = {
        FIELD_ID: _SYSTEM_ID,
        FIELD_SYSTEM_ID: _SYSTEM_ID,
        FIELD_DEVICES: [_home_power_dev_entry(3002)],
    }
    empty_system = {
        FIELD_ID: _SYSTEM_ID,
        FIELD_SYSTEM_ID: _SYSTEM_ID,
        FIELD_DEVICES: [],
    }
    coordinator = _discovery_coordinator(systems=[populated])
    await coordinator.async_discover()

    mutable = cast("Any", coordinator)
    mutable.api.async_get_system_list = AsyncMock(return_value=[empty_system])
    mutable.api.last_system_list_response = {
        FIELD_CODE: 0,
        FIELD_DATA: [empty_system],
    }
    await coordinator.async_discover()
    assert _home_power_dev_id(3002) in coordinator._device_index

    await coordinator.async_discover()
    assert _home_power_dev_id(3002) not in coordinator._device_index


@pytest.mark.parametrize("model_code", HOME_MODEL_CODES)
def test_home_modelcode_payload_classified_as_home(model_code: int) -> None:
    """Every Home-family SKU's property payload classifies as Home, not Portable."""
    dev_id = _home_power_dev_id(model_code)
    payload = _home_power_property_payload(dev_id, model_code)
    props = payload_properties_for_sources(payload)

    assert _has_home_payload_evidence(props) is True
    assert _is_portable_payload(payload, props) is False


@pytest.mark.parametrize("model_code", HOME_MODEL_CODES)
def test_home_modelcode_surfaces_home_only_sensor_description(
    model_code: int,
) -> None:
    """A Home-only sensor description constructs against every Home SKU.

    ``_collect_entities`` (sensor.py:4715-4717) only appends
    ``SMART_MODE_SENSOR_DESCRIPTIONS`` entities when ``_is_portable_payload``
    is False — this builds the same ``JackerySensor`` entity class that loop
    constructs and proves it resolves against a HomePower payload exactly as
    it would for SolarVault.
    """
    dev_id = _home_power_dev_id(model_code)
    payload = _home_power_property_payload(dev_id, model_code)
    props = payload_properties_for_sources(payload)
    assert _is_portable_payload(payload, props) is False

    description = SMART_MODE_SENSOR_DESCRIPTIONS[0]
    entity = _home_power_sensor(dev_id, payload, description)

    assert entity.unique_id == f"{dev_id}_{description.key}"
    assert entity.entity_description is description
    # No smart_mode section in this payload: the getter/fallback chain must
    # resolve to None cleanly rather than raise.
    assert entity.native_value is None
