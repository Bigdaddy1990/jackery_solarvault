"""Behavioral tests for the coordinator's thin HTTP-API passthrough methods.

These methods forward a Home Assistant service call to the Jackery cloud client
and return its payload verbatim (some additionally schedule a refresh, resolve a
systemId, or gate on Home/System config context). The Jackery ``api`` client is
the integration boundary and is mocked; the tests assert the delegation
contract: the right client method with the right arguments, value passthrough,
and — where the code does it — the refresh / guard / systemId behavior.
"""

import time
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import pytest

from custom_components.jackery_solarvault.client.api import JackeryApiError
from custom_components.jackery_solarvault.const import PAYLOAD_SYSTEM
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.update_coordinator import UpdateFailed

_DEVICE = "573702884982521856"
_SENTINEL: dict[str, Any] = {"ok": True, "value": 42}
# Mirror of coordinator._ACCESSORIES_SYNC_BACKOFF_KEY. Duplicated as a literal
# rather than imported to avoid a private-name import; drift is caught because
# the assertion below would then fail.
_ACCESSORIES_SYNC_BACKOFF_KEY = "accessories_sync"


def _coordinator(*, home_config: bool = False) -> JackerySolarVaultCoordinator:
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    obj = cast("Any", coordinator)
    obj._device_index = {}  # ruff: ignore[private-member-access]
    obj.data = (
        {_DEVICE: {PAYLOAD_SYSTEM: {"id": "sys-1"}}} if home_config else {_DEVICE: {}}
    )
    obj.api = MagicMock()
    obj.async_request_refresh = AsyncMock()
    return coordinator


def _api(coordinator: JackerySolarVaultCoordinator) -> Any:
    return cast("Any", coordinator).api


# --- ungated passthroughs -------------------------------------------------


@pytest.mark.asyncio
async def test_get_smart_mode_info_passthrough() -> None:
    """Smart-mode info forwards the system id and returns the payload."""
    coordinator = _coordinator()
    _api(coordinator).async_get_smart_mode_info = AsyncMock(return_value=_SENTINEL)

    result = await coordinator.async_get_smart_mode_info("sys-1")

    assert result is _SENTINEL
    _api(coordinator).async_get_smart_mode_info.assert_awaited_once_with("sys-1")


@pytest.mark.asyncio
async def test_start_smart_mode_passthrough() -> None:
    """Start smart mode forwards the system id."""
    coordinator = _coordinator()
    _api(coordinator).async_start_smart_mode = AsyncMock()

    await coordinator.async_start_smart_mode("sys-1")

    _api(coordinator).async_start_smart_mode.assert_awaited_once_with("sys-1")


@pytest.mark.asyncio
async def test_query_tou_plan_passthrough() -> None:
    """TOU plan query forwards the device id and returns the payload."""
    coordinator = _coordinator()
    _api(coordinator).async_query_tou_plan = AsyncMock(return_value=_SENTINEL)

    result = await coordinator.async_query_tou_plan(_DEVICE)

    assert result is _SENTINEL
    _api(coordinator).async_query_tou_plan.assert_awaited_once_with(device_id=_DEVICE)


@pytest.mark.asyncio
async def test_save_tou_plan_refreshes() -> None:
    """Saving a TOU plan forwards tasks and then requests a refresh."""
    coordinator = _coordinator()
    _api(coordinator).async_save_tou_plan = AsyncMock()
    tasks = [{"start": "00:00"}]

    await coordinator.async_save_tou_plan(_DEVICE, tasks)

    _api(coordinator).async_save_tou_plan.assert_awaited_once_with(
        device_id=_DEVICE,
        tasks=tasks,
    )
    cast("Any", coordinator).async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_list_currencies_passthrough() -> None:
    """Currency list forwards to the client currency-list call."""
    coordinator = _coordinator()
    _api(coordinator).async_get_currency_list = AsyncMock(return_value=[{"c": "EUR"}])

    result = await coordinator.async_list_currencies()

    assert result == [{"c": "EUR"}]


@pytest.mark.asyncio
async def test_list_country_zones_passthrough() -> None:
    """Country/zone list forwards to the zone-list call."""
    coordinator = _coordinator()
    _api(coordinator).async_get_zone_list = AsyncMock(return_value=[{"z": 1}])

    assert await coordinator.async_list_country_zones() == [{"z": 1}]


@pytest.mark.asyncio
async def test_list_grid_standards_forwards_country() -> None:
    """Grid-standard list forwards the country code."""
    coordinator = _coordinator()
    _api(coordinator).async_get_gcs_list = AsyncMock(return_value=[{"g": 1}])

    result = await coordinator.async_list_grid_standards(country="DE")

    assert result == [{"g": 1}]
    _api(coordinator).async_get_gcs_list.assert_awaited_once_with(country="DE")


@pytest.mark.asyncio
async def test_check_system_bound_forwards_identifiers() -> None:
    """System-bound check forwards all three identity fields."""
    coordinator = _coordinator()
    _api(coordinator).async_check_system_bound = AsyncMock(return_value=_SENTINEL)

    result = await coordinator.async_check_system_bound(
        bind_key="bk",
        device_sn="sn",
        guid="g",
    )

    assert result is _SENTINEL
    _api(coordinator).async_check_system_bound.assert_awaited_once_with(
        bind_key="bk",
        device_sn="sn",
        guid="g",
    )


@pytest.mark.asyncio
async def test_get_device_bluetooth_key_payload_passthrough() -> None:
    """Bluetooth-key payload forwards device_sn/guid and returns it verbatim."""
    coordinator = _coordinator()
    _api(coordinator).async_get_device_bluetooth_key = AsyncMock(
        return_value=_SENTINEL,
    )

    result = await coordinator.async_get_device_bluetooth_key_payload(
        device_sn="sn",
        guid="g",
    )

    assert result is _SENTINEL


@pytest.mark.asyncio
async def test_get_unread_count_passthrough() -> None:
    """Unread-count forwards to the client."""
    coordinator = _coordinator()
    _api(coordinator).async_get_unread_count = AsyncMock(return_value={"n": 3})

    assert await coordinator.async_get_unread_count() == {"n": 3}


@pytest.mark.asyncio
async def test_set_push_config_maps_argument_name() -> None:
    """Push config maps the public set_value onto the client's ``set`` kwarg."""
    coordinator = _coordinator()
    _api(coordinator).async_set_push_config = AsyncMock(return_value=_SENTINEL)

    result = await coordinator.async_set_push_config("all")

    assert result is _SENTINEL
    _api(coordinator).async_set_push_config.assert_awaited_once_with(set="all")


@pytest.mark.asyncio
async def test_query_soc_stat_passthrough() -> None:
    """SOC stat forwards the device id."""
    coordinator = _coordinator()
    _api(coordinator).async_get_soc_stat = AsyncMock(return_value=_SENTINEL)

    result = await coordinator.async_query_soc_stat(_DEVICE)

    assert result is _SENTINEL
    _api(coordinator).async_get_soc_stat.assert_awaited_once_with(device_id=_DEVICE)


@pytest.mark.asyncio
async def test_query_carbon_stat_passthrough() -> None:
    """Carbon stat forwards the device serial number."""
    coordinator = _coordinator()
    _api(coordinator).async_get_carbon_stat = AsyncMock(return_value=_SENTINEL)

    result = await coordinator.async_query_carbon_stat("SN-9")

    assert result is _SENTINEL
    _api(coordinator).async_get_carbon_stat.assert_awaited_once_with(device_sn="SN-9")


@pytest.mark.asyncio
async def test_query_box_stat_forwards_all_fields() -> None:
    """Box stat forwards every documented filter field."""
    coordinator = _coordinator()
    _api(coordinator).async_get_box_stat = AsyncMock(return_value=_SENTINEL)

    result = await coordinator.async_query_box_stat(
        device_sn="SN",
        date_type="day",
        begin_date="2026-01-01",
        end_date="2026-01-02",
        key="pv",
    )

    assert result is _SENTINEL
    _api(coordinator).async_get_box_stat.assert_awaited_once_with(
        device_sn="SN",
        date_type="day",
        begin_date="2026-01-01",
        end_date="2026-01-02",
        key="pv",
    )


@pytest.mark.asyncio
async def test_check_app_version_passthrough() -> None:
    """App-version check forwards its two fields."""
    coordinator = _coordinator()
    _api(coordinator).async_check_app_version = AsyncMock(return_value=_SENTINEL)

    result = await coordinator.async_check_app_version(type="android", version_name="1")

    assert result is _SENTINEL


@pytest.mark.asyncio
async def test_list_banners_passthrough() -> None:
    """Banner list forwards to the client."""
    coordinator = _coordinator()
    _api(coordinator).async_get_banner_list = AsyncMock(return_value=[1, 2])

    assert await coordinator.async_list_banners() == [1, 2]


@pytest.mark.asyncio
async def test_get_user_info_passthrough() -> None:
    """User-info forwards to the client."""
    coordinator = _coordinator()
    _api(coordinator).async_get_user_info = AsyncMock(return_value=_SENTINEL)

    assert await coordinator.async_get_user_info() is _SENTINEL


@pytest.mark.asyncio
async def test_get_product_instruction_maps_argument_name() -> None:
    """Product instruction maps device_sn onto the client's ``dev_sn`` kwarg."""
    coordinator = _coordinator()
    _api(coordinator).async_get_product_instruction = AsyncMock(return_value=_SENTINEL)

    await coordinator.async_get_product_instruction(device_sn="SN", type="pdf")

    _api(coordinator).async_get_product_instruction.assert_awaited_once_with(
        dev_sn="SN",
        type="pdf",
    )


@pytest.mark.asyncio
async def test_save_dynamic_price_location_id_refreshes() -> None:
    """Saving a location token returns the result and requests a refresh."""
    coordinator = _coordinator()
    _api(coordinator).async_save_location_id = AsyncMock(return_value=_SENTINEL)

    result = await coordinator.async_save_dynamic_price_location_id(connect_token="tok")

    assert result is _SENTINEL
    cast("Any", coordinator).async_request_refresh.assert_awaited_once()


# --- home-config-gated passthroughs --------------------------------------


@pytest.mark.asyncio
async def test_check_smart_mode_requires_home_context() -> None:
    """A non-Home device is rejected before any client call."""
    coordinator = _coordinator(home_config=False)
    _api(coordinator).async_check_smart_mode_set = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await coordinator.async_check_smart_mode(_DEVICE, "sys-1")

    _api(coordinator).async_check_smart_mode_set.assert_not_awaited()


@pytest.mark.asyncio
async def test_check_smart_mode_home_context_passes_through() -> None:
    """A Home device forwards both ids and returns the payload."""
    coordinator = _coordinator(home_config=True)
    _api(coordinator).async_check_smart_mode_set = AsyncMock(return_value=_SENTINEL)

    result = await coordinator.async_check_smart_mode(_DEVICE, "sys-1")

    assert result is _SENTINEL
    _api(coordinator).async_check_smart_mode_set.assert_awaited_once_with(
        device_id=_DEVICE,
        system_id="sys-1",
    )


@pytest.mark.asyncio
async def test_get_device_currency_gated_by_home_context() -> None:
    """Device currency is gated on Home/System context."""
    coordinator = _coordinator(home_config=False)
    _api(coordinator).async_get_device_currency = AsyncMock()

    with pytest.raises(HomeAssistantError):
        await coordinator.async_get_device_currency(_DEVICE)


@pytest.mark.asyncio
async def test_bind_currency_resolves_system_and_refreshes() -> None:
    """Binding a currency resolves the systemId, forwards it, then refreshes."""
    coordinator = _coordinator(home_config=True)
    _api(coordinator).async_bind_currency = AsyncMock()

    await coordinator.async_bind_currency(_DEVICE, "EUR")

    _api(coordinator).async_bind_currency.assert_awaited_once_with(
        currency="EUR",
        device_id=_DEVICE,
        system_id="sys-1",
    )
    cast("Any", coordinator).async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_save_device_max_power_rejected_value_raises() -> None:
    """A rejected max-power write raises and does not refresh."""
    coordinator = _coordinator(home_config=True)
    _api(coordinator).async_set_max_power = AsyncMock(return_value=False)

    with pytest.raises(HomeAssistantError):
        await coordinator.async_save_device_max_power(_DEVICE, 1000)

    cast("Any", coordinator).async_request_refresh.assert_not_awaited()


@pytest.mark.asyncio
async def test_save_device_max_power_accepted_refreshes() -> None:
    """An accepted max-power write requests a refresh."""
    coordinator = _coordinator(home_config=True)
    _api(coordinator).async_set_max_power = AsyncMock(return_value=True)

    await coordinator.async_save_device_max_power(_DEVICE, 1000)

    _api(coordinator).async_set_max_power.assert_awaited_once_with(_DEVICE, 1000)
    cast("Any", coordinator).async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_dynamic_price_login_url_missing_system_raises() -> None:
    """A device with home context but no resolvable systemId raises UpdateFailed."""
    coordinator = _coordinator(home_config=True)
    # Home context is satisfied via properties, but no systemId is resolvable.
    cast("Any", coordinator).data = {_DEVICE: {"properties": {"maxOutPw": 1}}}
    _api(coordinator).async_get_dynamic_price_login_url = AsyncMock()

    with pytest.raises(UpdateFailed):
        await coordinator.async_get_dynamic_price_login_url(_DEVICE, 5)


@pytest.mark.asyncio
async def test_get_dynamic_price_login_url_success_forwards_and_returns() -> None:
    """With a resolvable systemId, the login URL call forwards it and returns.

    Complements ``test_get_dynamic_price_login_url_missing_system_raises``
    (negative path) with the happy path so a regression that stops forwarding
    the resolved ``system_id`` — or that stops returning the client payload —
    is caught too.
    """
    coordinator = _coordinator(home_config=True)
    _api(coordinator).async_get_dynamic_price_login_url = AsyncMock(
        return_value=_SENTINEL,
    )

    result = await coordinator.async_get_dynamic_price_login_url(_DEVICE, 5)

    assert result is _SENTINEL
    _api(coordinator).async_get_dynamic_price_login_url.assert_awaited_once_with(
        platform_company_id=5,
        system_id="sys-1",
    )


@pytest.mark.asyncio
async def test_sync_alerts_refreshes_and_returns() -> None:
    """Syncing alerts forwards content/id, refreshes, and returns the result."""
    coordinator = _coordinator()
    _api(coordinator).async_sync_alerts = AsyncMock(return_value=_SENTINEL)

    result = await coordinator.async_sync_alerts(content="c", id="i")

    assert result is _SENTINEL
    _api(coordinator).async_sync_alerts.assert_awaited_once_with(content="c", id="i")
    cast("Any", coordinator).async_request_refresh.assert_awaited_once()


@pytest.mark.asyncio
async def test_accessory_sync_backs_off_after_persistent_10600() -> None:
    """A persistent code=10600 stops the discovery-cadence accessory sync spam.

    ``synchronizeSmartAccessoriesData`` is a genuine POST, but accounts without
    smart accessories get code=10600 on every discovery cycle. The call is
    routed through the shared endpoint-backoff ladder so it fires once, opens a
    window, and is suppressed on the next cycle instead of re-firing (and
    re-logging) forever. The HTTP verb is unchanged.
    """
    coordinator = _coordinator()
    obj = cast("Any", coordinator)
    obj._endpoint_backoff = {}  # ruff: ignore[private-member-access]
    api = _api(coordinator)
    api.async_sync_smart_accessories = AsyncMock(
        side_effect=JackeryApiError("code=10600 msg=''"),
    )
    api.async_get_accessories_list = AsyncMock(return_value=[])
    obj._overlay_http_accessories = MagicMock()  # ruff: ignore[private-member-access]
    index = {_DEVICE: {PAYLOAD_SYSTEM: {}}}

    await coordinator._async_enumerate_http_accessories(index)  # ruff: ignore[private-member-access]
    await coordinator._async_enumerate_http_accessories(index)  # ruff: ignore[private-member-access]

    assert api.async_sync_smart_accessories.await_count == 1
    assert (
        coordinator._endpoint_backoff_active(  # ruff: ignore[private-member-access]
            _ACCESSORIES_SYNC_BACKOFF_KEY,
            time.monotonic(),
        )
        is True
    )
