"""Regression: CT/Smart-Meter period stats must use the accessory deviceId.

Background
----------
The CT/Smart-Meter is a sub-device (``devType=3``) with its own ``deviceId``
in the system ``accessories`` list. Per docs/Markdown/APP_POLLING_MQTT.md the
``/v1/device/stat/ct`` endpoint keys on that accessory id; calling it with the
main device id returns empty, leaving ``device_ct_stat_*`` (and the CT
statistic sensors) without values.

These tests lock down two things:

1. ``_smart_meter_accessory_device_id`` resolves the accessory id from a
   discovery-index entry (and falls back to the live ``ct_meter`` block).
2. A real guarded update cycle passes that resolved id to the CT-stat API.
"""

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from tests._update_cycle_fixture import (  # ruff:ignore[banned-api]
    DEVICE_ID,
    make_update_cycle_api,
    setup_update_cycle_coordinator,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_CT_DEVICE_ID = "2057219036232777730"


# ---------------------------------------------------------------------------
# Unit: accessory-id resolution (pure classmethod, no HA runtime needed)
# ---------------------------------------------------------------------------


def test_accessory_id_resolved_from_system_accessories() -> None:
    """A devType=3 accessory's deviceId is returned for the CT-stat call."""
    from custom_components.jackery_solarvault.const import (
        FIELD_ACCESSORIES,
        FIELD_SYSTEM_ID,
        PAYLOAD_SYSTEM_META,
    )
    from custom_components.jackery_solarvault.coordinator import (
        JackerySolarVaultCoordinator,
    )

    idx: dict[str, Any] = {
        FIELD_SYSTEM_ID: "595364183558991872",
        PAYLOAD_SYSTEM_META: {
            FIELD_ACCESSORIES: [
                {
                    "devType": 3,
                    "subType": 2,
                    "typeName": "Shelly Pro 3EM",
                    "deviceId": 2057219036232777730,
                    "deviceSn": "5c013b048e3c",
                }
            ]
        },
    }

    assert (
        JackerySolarVaultCoordinator._smart_meter_accessory_device_id(idx)
        == _CT_DEVICE_ID
    )


def test_accessory_id_none_without_smart_meter() -> None:
    """Return ``None`` when neither discovery nor live data has a CT accessory."""
    from custom_components.jackery_solarvault.const import (
        FIELD_ACCESSORIES,
        PAYLOAD_SYSTEM_META,
    )
    from custom_components.jackery_solarvault.coordinator import (
        JackerySolarVaultCoordinator,
    )

    idx: dict[str, Any] = {PAYLOAD_SYSTEM_META: {FIELD_ACCESSORIES: []}}
    assert JackerySolarVaultCoordinator._smart_meter_accessory_device_id(idx) is None


def test_accessory_id_falls_back_to_ct_meter_block() -> None:
    """When no accessory metadata exists, the live ct_meter id is used."""
    from custom_components.jackery_solarvault.const import PAYLOAD_CT_METER  # ruff:ignore[unsorted-imports]
    from custom_components.jackery_solarvault.coordinator import (
        JackerySolarVaultCoordinator,
    )

    source = {PAYLOAD_CT_METER: {"devType": 3, "deviceId": 2057219036232777730}}
    assert (
        JackerySolarVaultCoordinator._smart_meter_accessory_device_id(source)
        == _CT_DEVICE_ID
    )


@pytest.mark.asyncio
async def test_update_cycle_uses_accessory_id_for_ct_stats(
    hass: HomeAssistant,
) -> None:
    """Pass the discovered CT accessory id to every CT-stat request."""
    from custom_components.jackery_solarvault.const import FIELD_DEVICES  # ruff:ignore[unsorted-imports]

    api = make_update_cycle_api()
    systems = await api.async_get_system_list()
    systems[0][FIELD_DEVICES].append({
        "devType": 3,
        "subType": 2,
        "typeName": "Shelly Pro 3EM",
        "deviceId": _CT_DEVICE_ID,
        "deviceSn": "5c013b048e3c",
    })
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass, api=api)

    try:
        await coordinator._async_update_data_guarded()
        slow_metrics_task = coordinator._slow_metrics_bg_task
        assert slow_metrics_task is not None
        await slow_metrics_task
        await hass.async_block_till_done()

        called_device_ids = {
            str(call.args[0])
            for call in api.async_get_device_ct_stat.await_args_list
            if call.args
        }
        assert called_device_ids == {_CT_DEVICE_ID}
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()


@pytest.mark.asyncio
async def test_update_cycle_uses_l2_for_every_empty_l1_ct_period(
    hass: HomeAssistant,
) -> None:
    """Current CT sections and month repair pair every empty L1 with App L2."""
    from custom_components.jackery_solarvault.const import (  # ruff:ignore[unsorted-imports]
        APP_PERIOD_DATE_TYPES,
        APP_REQUEST_META,
        APP_SECTION_CT_STAT,
        APP_STAT_TOTAL_CT_INPUT_ENERGY,
        APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
        CT_STAT_TYPE_L1,
        CT_STAT_TYPE_L2,
        DATE_TYPE_YEAR,
        FIELD_DEVICES,
    )

    async def _ct_stat(
        _device_id: str,
        *,
        date_type: str,
        stat_type: int,
        **_kwargs: Any,
    ) -> dict[str, Any]:
        if stat_type == CT_STAT_TYPE_L1 or date_type == DATE_TYPE_YEAR:
            return {
                "unit": "kWh",
                "x": [],
                "y1": [],
                "y2": [],
                APP_STAT_TOTAL_CT_INPUT_ENERGY: 0,
                APP_STAT_TOTAL_CT_OUTPUT_ENERGY: 0,
                APP_REQUEST_META: {"dateType": date_type, "type": "0"},
            }
        assert stat_type == CT_STAT_TYPE_L2
        return {
            "unit": "kWh",
            "x": ["observed"],
            "y1": [1.0],
            "y2": [0.25],
            APP_STAT_TOTAL_CT_INPUT_ENERGY: 1.0,
            APP_STAT_TOTAL_CT_OUTPUT_ENERGY: 0.25,
            APP_REQUEST_META: {"dateType": date_type, "type": "1"},
        }

    api = make_update_cycle_api(
        async_get_device_ct_stat=AsyncMock(side_effect=_ct_stat),
    )
    systems = await api.async_get_system_list()
    systems[0][FIELD_DEVICES].append({
        "devType": 3,
        "subType": 2,
        "typeName": "Shelly Pro 3EM",
        "deviceId": _CT_DEVICE_ID,
        "deviceSn": "5c013b048e3c",
    })
    coordinator, entry, _api = await setup_update_cycle_coordinator(hass, api=api)

    try:
        result = await coordinator._async_update_data_guarded()
        slow_metrics_task = coordinator._slow_metrics_bg_task
        assert slow_metrics_task is not None
        await slow_metrics_task
        await hass.async_block_till_done()
        result = await coordinator._async_update_data_guarded()

        calls = api.async_get_device_ct_stat.await_args_list
        assert calls
        l1_calls = [call for call in calls if call.kwargs["stat_type"] == 0]
        l2_calls = [call for call in calls if call.kwargs["stat_type"] == 1]
        assert len(l1_calls) == len(l2_calls)
        payload = result[DEVICE_ID]
        for date_type in APP_PERIOD_DATE_TYPES:
            if date_type == DATE_TYPE_YEAR:
                continue
            source = payload[f"{APP_SECTION_CT_STAT}_{date_type}"]
            assert source[APP_STAT_TOTAL_CT_INPUT_ENERGY] == pytest.approx(1.0)
            assert source[APP_REQUEST_META]["type"] == "1"
    finally:
        await hass.config_entries.async_unload(entry.entry_id)
        await hass.async_block_till_done()
