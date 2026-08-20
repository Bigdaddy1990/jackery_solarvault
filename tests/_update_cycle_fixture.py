"""Reusable fixture for driving the coordinator's guarded update cycle.

This is a **helper module, not a test file** (the leading underscore keeps
pytest from collecting it). It builds a fully-wired
:class:`JackerySolarVaultCoordinator` whose ``api`` is a mock returning
source-of-truth-shaped payloads for every endpoint the
``_async_update_data_guarded`` cycle touches, so behavioral tests can exercise
the fetch / merge / stat-schedule helpers for real while mocking only the
Jackery cloud boundary, the HA recorder statistics import, and the transport
wiring the cycle would otherwise start.

Design goals:

* **One reusable factory.** :func:`make_update_cycle_api` builds the mock
  ``api``; :func:`setup_update_cycle_coordinator` returns a live coordinator
  with a populated device index. Both are reused by multiple test modules.
* **Individually overridable endpoints.** Every endpoint is an
  :class:`~unittest.mock.AsyncMock` attribute on the returned ``api`` mock, so
  a test can force one endpoint to raise, return empty, or return a custom
  payload without rebuilding the whole stub.
* **Boundary-only mocking.** The mock stands in for the Jackery cloud API
  (HTTP path) only. The recorder statistics import is left disabled
  (``_statistics_import_ready = False``) unless a test opts in, and Layer-5
  transport startup is patched out during config-entry setup. All internal
  coordinator merge/fetch/gate logic runs unmocked.

Field names are grounded in ``docs/source-of-truth`` (the Jackery HTTP model
field catalog) and the ``FIELD_*`` / ``PAYLOAD_*`` constants.
"""

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault.const import (
    CODE_OK,
    CONF_MQTT_MAC_ID,
    CONF_REGION_CODE,
    DOMAIN,
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
    FIELD_ID,
    FIELD_IN_PW,
    FIELD_MODEL_CODE,
    FIELD_ONLINE,
    FIELD_OUT_PW,
    FIELD_SOC,
    FIELD_SYSTEM_ID,
    FIELD_WNAME,
    PAYLOAD_DEVICE,
    PAYLOAD_PROPERTIES,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME

if TYPE_CHECKING:
    from custom_components.jackery_solarvault.coordinator import (
        JackerySolarVaultCoordinator,
    )
    from homeassistant.core import HomeAssistant

# Stable identifiers reused across the fixture and its consuming tests.
DEVICE_ID = "573702884982521856"
DEVICE_SN = "HTB000000000001"
SYSTEM_ID = "512000000000000001"
MODEL_CODE = "HTB2000"


def _default_property_payload(dev_id: str, dev_sn: str) -> dict[str, Any]:
    """Return a realistic ``/v1/device/property`` response.

    The shape mirrors the app's HomeBody property snapshot: a ``device`` meta
    block plus a ``properties`` block carrying live power/SOC telemetry. Values
    are plausible (a battery at 62% discharging into the home).
    """
    return {
        PAYLOAD_DEVICE: {
            FIELD_DEVICE_ID: dev_id,
            FIELD_DEVICE_SN: dev_sn,
            FIELD_MODEL_CODE: MODEL_CODE,
            FIELD_ONLINE: 1,
            "activated": 1,
        },
        PAYLOAD_PROPERTIES: {
            FIELD_WNAME: "SolarVault",
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


def _default_system_list() -> list[dict[str, Any]]:
    """Return a ``/v1/device/system/list`` response with one property device.

    The device dict carries ``modelCode`` and a truthy ``bindKey`` so it passes
    ``_is_property_device_candidate`` and enters the property poll loop.
    """
    return [
        {
            FIELD_ID: SYSTEM_ID,
            FIELD_SYSTEM_ID: SYSTEM_ID,
            "systemName": "Home",
            FIELD_DEVICES: [
                {
                    FIELD_DEVICE_ID: DEVICE_ID,
                    FIELD_DEVICE_SN: DEVICE_SN,
                    FIELD_MODEL_CODE: MODEL_CODE,
                    FIELD_BIND_KEY: 1,
                    "devModel": "SolarVault HTB2000",
                },
            ],
        },
    ]


def make_update_cycle_api(**overrides: Any) -> MagicMock:
    """Build a mock Jackery ``api`` wired for the guarded update cycle.

    Every endpoint the cycle calls is an :class:`AsyncMock` with a
    source-of-truth-shaped default return. Pass ``endpoint_name=value`` to swap
    any single default (e.g. ``async_get_device_property=AsyncMock(...)`` to
    force a failure), keeping the rest realistic.

    Args:
        **overrides: Attribute names on the mock to replace after the defaults
            are installed. Each value is assigned verbatim, so pass an
            ``AsyncMock`` / ``MagicMock`` for coroutine / sync surfaces.

    Returns:
        A ``MagicMock`` exposing the coordinator's ``api`` surface.
    """
    api = MagicMock(name="JackeryApi")

    # --- config-entry setup + __init__ surface -------------------------------
    api.async_login = AsyncMock(return_value=None)
    api.async_get_mqtt_credentials = AsyncMock(return_value={"user_id": "user-1"})
    api.mqtt_session_snapshot = MagicMock(return_value=None)
    api.hydrate_mqtt_session = MagicMock(return_value=None)
    api.async_close = AsyncMock(return_value=None)
    api.payload_debug_callback = None
    api.auth_rejection_callback = None
    api.mqtt_fingerprint = None

    # --- discovery -----------------------------------------------------------
    api.async_get_system_list = AsyncMock(return_value=_default_system_list())
    api.async_list_devices_legacy = AsyncMock(return_value=[])
    api.async_sync_smart_accessories = AsyncMock(return_value=None)
    api.async_get_accessories_list = AsyncMock(return_value=[])

    # --- authoritative per-device property fetch (fast L3 critical path) ------
    api.async_get_device_property = AsyncMock(
        side_effect=lambda dev_id: _default_property_payload(dev_id, DEVICE_SN),
    )

    # --- per-device slow extras (stat/OTA/location/packs) --------------------
    api.async_get_device_statistic = AsyncMock(return_value={})
    api.async_get_device_pv_stat = AsyncMock(return_value={})
    api.async_get_device_battery_stat = AsyncMock(return_value={})
    api.async_get_device_home_stat = AsyncMock(return_value={})
    api.async_get_device_ct_stat = AsyncMock(return_value={})
    api.async_get_device_eps_stat = AsyncMock(return_value={})
    api.async_get_device_meter_stat = AsyncMock(return_value={})
    api.async_get_symmetry_stat = AsyncMock(return_value={})
    api.async_get_device_socket_statistic = AsyncMock(return_value={})
    api.async_get_today_energy = AsyncMock(return_value={})
    api.async_get_ota_info = AsyncMock(return_value={})
    api.async_get_location = AsyncMock(return_value={})
    api.async_get_battery_pack_list = AsyncMock(return_value=[])

    # --- per-system slow metrics (stats / trends / price) --------------------
    api.async_get_system_statistic = AsyncMock(return_value={})
    api.async_get_alarm = AsyncMock(return_value=None)
    api.async_get_pv_trends = AsyncMock(return_value={})
    api.async_get_home_trends = AsyncMock(return_value={})
    api.async_get_battery_trends = AsyncMock(return_value={})
    api.async_get_dynamic_price = AsyncMock(return_value={})
    api.async_get_power_price = AsyncMock(return_value={})
    api.async_get_price_sources = AsyncMock(return_value=[])
    api.async_get_price_history_config = AsyncMock(return_value={})

    # --- Shelly Cloud + third-party enrichment (L5) --------------------------
    api.async_get_shelly_devices = AsyncMock(return_value=[])
    api.async_get_shelly_realtime_power = AsyncMock(return_value={})

    for name, value in overrides.items():
        setattr(api, name, value)

    system_list_fetcher = api.async_get_system_list

    async def _get_system_list_with_raw_response() -> list[dict[str, Any]]:
        """Mirror the API client's raw-response side effect for discovery."""
        systems: list[dict[str, Any]] = await system_list_fetcher()
        api.last_system_list_response = {
            FIELD_CODE: CODE_OK,
            FIELD_DATA: systems,
        }
        return systems

    api.async_get_system_list = AsyncMock(
        side_effect=_get_system_list_with_raw_response,
    )
    return api


async def setup_update_cycle_coordinator(
    hass: HomeAssistant,
    *,
    api: MagicMock | None = None,
    entry_id: str = "update-cycle-entry",
    discover: bool = True,
) -> tuple[JackerySolarVaultCoordinator, MockConfigEntry, MagicMock]:
    """Set up the integration and return a coordinator ready for the cycle.

    The config entry is set up through the real ``async_setup_entry`` so the
    coordinator is fully wired (poll watchdog, stores, caches, MQTT manager),
    while the mandatory first HTTP refresh and deferred Layer-5 startup are
    isolated at their explicit setup boundaries. Tests then drive discovery
    and the guarded HTTP update cycle themselves. Recorder statistics imports
    remain disabled unless a test opts in.

    Args:
        hass: The Home Assistant test instance.
        api: Optional pre-built mock ``api``; a default one is created when
            omitted.
        entry_id: Config-entry id (override when a test needs more than one).
        discover: When ``True`` (default) run real discovery so the returned
            coordinator has a populated ``_device_index``.

    Returns:
        A ``(coordinator, entry, api)`` tuple.
    """
    if api is None:
        api = make_update_cycle_api()

    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_USERNAME: "tester@example.com",
            CONF_PASSWORD: "secret",
            CONF_MQTT_MAC_ID: None,
            CONF_REGION_CODE: None,
        },
        title="Jackery Home",
        entry_id=entry_id,
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.jackery_solarvault.JackeryApi",
            return_value=api,
        ),
        patch(
            "custom_components.jackery_solarvault._async_prepare_primary_http",
            AsyncMock(return_value=None),
        ),
        patch(
            "custom_components.jackery_solarvault.coordinator."
            "JackerySolarVaultCoordinator.async_start_statistics_imports",
            return_value=None,
        ),
        patch(
            "custom_components.jackery_solarvault._schedule_layer5_start_if_ready",
            return_value=None,
        ),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        await hass.async_block_till_done()

    coordinator = cast("JackerySolarVaultCoordinator", entry.runtime_data)
    # The setup path builds its own real ``JackeryApi`` mock via the patch
    # above; make the stat/property surface the mock we control.
    coordinator.api = cast("Any", api)
    if discover:
        await coordinator.async_discover()
    return coordinator, entry, api
