"""Contracts proven by the extracted 2.4.2 App and its GET request caller."""

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.const import PAYLOAD_AIEMS_ENERGY_PREDICTION
from tests._update_cycle_fixture import (  # ruff: ignore[banned-api]
    DEVICE_ID,
    SYSTEM_ID,
    make_update_cycle_api,
    setup_update_cycle_coordinator,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


async def test_aiems_prediction_uses_system_get_and_preserves_full_body() -> None:
    """Forecast arrays and operation plans remain together in their own body."""
    body = {
        "pvData": [1.0, 2.0],
        "loadData": [3.0, 4.0],
        "socData": [50.0, 51.0],
        "priceData": [0.28, 0.29],
        "timestamps": ["2026-10-01T00:00:00", "2026-10-01T00:05:00"],
        "operationPlan": [{"start": 1790805600, "end": 1790805900, "power": 200}],
        "currency": "€",
    }
    api = JackeryApi.__new__(JackeryApi)
    with patch.object(JackeryApi, "_get_json", new_callable=AsyncMock) as request:
        request.return_value = {"data": body}
        result = await api.async_get_aiems_energy_prediction(system_id=123)

    request.assert_awaited_once_with(
        "/v1/api/aiems/report/energy/prediction", params={"systemId": "123"}
    )
    assert result == body


async def test_prediction_shadow_fills_its_bucket_without_changing_energy(
    hass: HomeAssistant,
) -> None:
    """The real shadow handler keeps forecasts separate from measured totals."""
    body = {"pvData": [3.0], "operationPlan": [{"power": 200}]}
    api = make_update_cycle_api(
        async_get_aiems_energy_prediction=AsyncMock(return_value=body)
    )
    coordinator, entry, _ = await setup_update_cycle_coordinator(hass, api=api)
    working: dict[str, Any] = {
        "device": {
            "deviceId": DEVICE_ID,
            "deviceSn": "HTB000000000001",
            "modelCode": "HTB2000",
            "bindKey": 1,
        },
        "system": {"systemId": SYSTEM_ID},
    }
    working["device_today_energy"] = {"ds": 4.2}
    api.async_get_aiems_energy_prediction.reset_mock()

    changed = await coordinator._async_apply_aiems_prediction(  # ruff: ignore[private-member-access]
        DEVICE_ID, working
    )

    assert changed
    api.async_get_aiems_energy_prediction.assert_awaited_once_with(system_id=SYSTEM_ID)
    assert working[PAYLOAD_AIEMS_ENERGY_PREDICTION] == body
    assert working["device_today_energy"]["ds"] == pytest.approx(4.2)
    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
