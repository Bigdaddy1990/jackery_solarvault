"""Regression test for the conversion-loss power balance (2026-07-03).

Live finding: the sensor reported 1995 W "loss" while PV ran at 2884 W —
the formula subtracted only the grid-side EXPORT (``outGridSidePw`` =
469 W) instead of the inverter's total AC output (``gridOutPw`` = house
share + export, per the SystemBody identity ``otherLoadPw = gridOutPw -
outGridSidePw + inGridSidePw``). The "loss" therefore silently contained
the whole household consumption. The balance must close at the inverter
boundary: PV + battery discharge + inverter AC input - battery charge -
inverter AC output.
"""

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault.const import (
    CONF_CREATE_SAVINGS_DETAIL_SENSORS,
    DOMAIN,
    FIELD_BAT_IN_PW,
    FIELD_BAT_OUT_PW,
    FIELD_DEVICE_SN,
    FIELD_GRID_IN_PW,
    FIELD_GRID_OUT_PW,
    FIELD_IN_GRID_SIDE_PW,
    FIELD_OTHER_LOAD_PW,
    FIELD_OUT_GRID_SIDE_PW,
    FIELD_PV_PW,
    FIELD_STACK_IN_PW,
    FIELD_STACK_OUT_PW,
    PAYLOAD_DEVICE,
    PAYLOAD_DISCOVERY,
    PAYLOAD_PROPERTIES,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from homeassistant.core import HomeAssistant

_DEVICE_ID = "dev-home-1"
_DEVICE_SN = "SN-HOME-0001"

# Live frame 2026-07-03 14:16 (SystemBody), completed via the wire
# identity: house load 1900 W + export 469 W => inverter AC output 2369 W.
_PV_W = 2884
_STACK_CHARGE_W = 420
_EXPORT_W = 469
_HOUSE_LOAD_W = 1900
_INVERTER_AC_OUT_W = _HOUSE_LOAD_W + _EXPORT_W
# 2884 + 0 + 0 - 420 - 2369 = 95 W true conversion residual (the old
# export-only formula yielded the observed bogus 1995 W).
_EXPECTED_LOSS_W = 95.0

_LIVE_PROPS: dict[str, Any] = {
    FIELD_PV_PW: _PV_W,
    FIELD_STACK_IN_PW: _STACK_CHARGE_W,
    FIELD_STACK_OUT_PW: 0,
    FIELD_BAT_IN_PW: 221,
    FIELD_BAT_OUT_PW: 0,
    FIELD_GRID_IN_PW: 0,
    FIELD_GRID_OUT_PW: _INVERTER_AC_OUT_W,
    FIELD_IN_GRID_SIDE_PW: 0,
    FIELD_OUT_GRID_SIDE_PW: _EXPORT_W,
    FIELD_OTHER_LOAD_PW: _HOUSE_LOAD_W,
}


def _make_api_stub() -> MagicMock:
    """Build a ``JackeryApi`` stub that mocks only the network boundary.

    Returns:
        MagicMock: Stub exposing the coroutine surface the coordinator
        touches during setup, with no real IO.
    """
    api = MagicMock(name="JackeryApi")
    api.async_login = AsyncMock(return_value=None)
    api.async_get_mqtt_credentials = AsyncMock(return_value={"user_id": "user-1"})
    api.async_get_system_list = AsyncMock(return_value=[])
    api.async_list_devices_legacy = AsyncMock(return_value=[])
    api.mqtt_session_snapshot = MagicMock(return_value=None)
    api.hydrate_mqtt_session = MagicMock(return_value=None)
    api.async_close = AsyncMock(return_value=None)
    api.payload_debug_callback = None
    api.auth_rejection_callback = None
    return api


@pytest.fixture()
async def savings_setup(
    hass: HomeAssistant,
) -> AsyncGenerator[MockConfigEntry]:
    """Set up the integration with savings-detail sensors enabled.

    Yields:
        MockConfigEntry: The configured entry after entity discovery.
    """
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_USERNAME: "tester@example.com", CONF_PASSWORD: "secret"},
        options={CONF_CREATE_SAVINGS_DETAIL_SENSORS: True},
        title="Jackery Home",
        entry_id="home-entry",
    )
    entry.add_to_hass(hass)

    api = _make_api_stub()
    with (
        patch(
            "custom_components.jackery_solarvault.JackeryApi",
            return_value=api,
        ),
        patch(
            "custom_components.jackery_solarvault._async_finish_entry_startup",
            AsyncMock(return_value=None),
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = entry.runtime_data
    coordinator.async_set_updated_data({
        _DEVICE_ID: {
            PAYLOAD_DEVICE: {FIELD_DEVICE_SN: _DEVICE_SN},
            PAYLOAD_DISCOVERY: {FIELD_DEVICE_SN: _DEVICE_SN},
            PAYLOAD_PROPERTIES: dict(_LIVE_PROPS),
        },
    })
    await hass.async_block_till_done()

    yield entry

    await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()


async def test_conversion_loss_balances_at_the_inverter_boundary(
    hass: HomeAssistant,
    savings_setup: MockConfigEntry,
) -> None:
    """The loss is the inverter residual, not export-only accounting.

    With PV 2884 W, stack charge 420 W and inverter AC output 2369 W
    (house 1900 W + export 469 W) the true residual is 95 W. The
    historical export-only formula reported 1995 W — the household
    consumption disguised as "loss".
    """
    from homeassistant.helpers import entity_registry as er  # noqa: PLC0415

    await hass.async_block_till_done()
    registry = er.async_get(hass)
    entity_id = registry.async_get_entity_id(
        "sensor",
        DOMAIN,
        f"{_DEVICE_ID}_conversion_loss_power",
    )
    assert entity_id is not None, "conversion loss sensor was not registered"

    state = hass.states.get(entity_id)

    assert state is not None
    assert state.state == str(_EXPECTED_LOSS_W)
    assert state.attributes["inverter_ac_output_power"] == _INVERTER_AC_OUT_W
    assert "grid_side_output_power" not in state.attributes
