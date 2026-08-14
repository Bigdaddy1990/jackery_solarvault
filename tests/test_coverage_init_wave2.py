"""Lifecycle coverage for the Jackery config-entry integration boundary."""

from datetime import timedelta
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components import jackery_solarvault as integration
from custom_components.jackery_solarvault.const import (
    CONF_CREATE_CALCULATED_POWER_SENSORS,
    CONF_ENABLE_BLE_TRANSPORT,
    CONF_LOCAL_MQTT_ENABLE,
    CONF_LOCAL_MQTT_HOST,
    CONF_LOCAL_MQTT_PASSWORD,
    CONF_LOCAL_MQTT_PORT,
    CONF_LOCAL_MQTT_TOPIC,
    CONF_LOCAL_MQTT_USERNAME,
    CONF_SCAN_INTERVAL,
    CONF_THIRD_PARTY_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_PORT,
    CONF_THIRD_PARTY_MQTT_TOKEN,
    CONF_THIRD_PARTY_MQTT_USERNAME,
    DOMAIN,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from homeassistant.const import CONF_PASSWORD, CONF_USERNAME
from homeassistant.helpers import device_registry as dr

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _entry(
    hass: HomeAssistant,
    *,
    entry_id: str,
    options: dict[str, Any] | None = None,
) -> MockConfigEntry:
    """Create and register one integration entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_USERNAME: "owner@example.com", CONF_PASSWORD: "secret"},
        options=options or {},
        entry_id=entry_id,
    )
    entry.add_to_hass(hass)
    return entry


async def test_setup_adopts_confirmed_device_mqtt_config_in_place(
    hass: HomeAssistant,
) -> None:
    """A valid 3047 readback updates options without reloading primary HTTP."""
    entry = _entry(
        hass,
        entry_id="adopt-device-mqtt",
        options={CONF_LOCAL_MQTT_TOPIC: "jackery/local/device"},
    )
    api = MagicMock(name="api")
    coordinator = MagicMock(name="coordinator")
    coordinator.data = {}
    forward = AsyncMock(return_value=None)

    with (
        patch.object(integration, "async_get_clientsession", return_value=MagicMock()),
        patch.object(integration, "JackeryApi", return_value=api),
        patch.object(
            integration,
            "JackerySolarVaultCoordinator",
            return_value=coordinator,
        ),
        patch.object(
            integration,
            "_async_release_fenced_coordinator",
            AsyncMock(return_value=True),
        ),
        patch.object(integration, "_async_prune_removed_local_mqtt_tls_options"),
        patch.object(
            integration,
            "_async_load_entry_caches",
            AsyncMock(return_value=False),
        ),
        patch.object(
            integration,
            "_async_prepare_primary_http",
            AsyncMock(return_value=None),
        ),
        patch.object(integration, "_async_clean_legacy_entities"),
        patch.object(integration, "_async_remove_legacy_system_parent_devices"),
        patch.object(hass.config_entries, "async_forward_entry_setups", forward),
        patch.object(integration, "_schedule_layer5_start_if_ready"),
    ):
        assert await integration.async_setup_entry(hass, entry) is True

    observer = coordinator.set_local_mqtt_config_observer.call_args.args[0]
    assert callable(observer)
    schedule = MagicMock()
    with (
        patch.object(integration, "_schedule_options_reconcile", schedule),
        patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload_entry,
    ):
        observer({
            "enable": 1,
            "ip": "192.168.2.212",
            "port": 1883,
            "userName": "bridge-user",
            "password": "bridge-pass",
            "token": "device-token",
        })
        await hass.async_block_till_done()

    assert entry.options == {
        CONF_LOCAL_MQTT_TOPIC: "jackery/local/device",
        CONF_LOCAL_MQTT_ENABLE: True,
        CONF_THIRD_PARTY_MQTT_ENABLE: True,
        CONF_LOCAL_MQTT_HOST: "192.168.2.212",
        CONF_LOCAL_MQTT_PORT: 1883,
        CONF_LOCAL_MQTT_USERNAME: "bridge-user",
        CONF_LOCAL_MQTT_PASSWORD: "bridge-pass",
        CONF_THIRD_PARTY_MQTT_IP: "192.168.2.212",
        CONF_THIRD_PARTY_MQTT_PORT: 1883,
        CONF_THIRD_PARTY_MQTT_USERNAME: "bridge-user",
        CONF_THIRD_PARTY_MQTT_PASSWORD: "bridge-pass",
        CONF_THIRD_PARTY_MQTT_TOKEN: "device-token",
    }
    reload_entry.assert_not_awaited()
    assert schedule.call_count >= 1
    assert all(
        call.args[:3] == (hass, entry, coordinator) for call in schedule.call_args_list
    )
    assert all(
        CONF_LOCAL_MQTT_ENABLE in call.args[3] for call in schedule.call_args_list
    )
    forward.assert_awaited_once()


async def test_setup_ignores_incomplete_enabled_device_mqtt_config(
    hass: HomeAssistant,
) -> None:
    """An enabled 3047 payload without a valid broker cannot overwrite options."""
    entry = _entry(hass, entry_id="reject-device-mqtt")
    coordinator = MagicMock(name="coordinator")
    coordinator.data = {}

    with (
        patch.object(integration, "async_get_clientsession", return_value=MagicMock()),
        patch.object(integration, "JackeryApi", return_value=MagicMock()),
        patch.object(
            integration,
            "JackerySolarVaultCoordinator",
            return_value=coordinator,
        ),
        patch.object(
            integration,
            "_async_release_fenced_coordinator",
            AsyncMock(return_value=True),
        ),
        patch.object(integration, "_async_prune_removed_local_mqtt_tls_options"),
        patch.object(
            integration,
            "_async_load_entry_caches",
            AsyncMock(return_value=False),
        ),
        patch.object(
            integration,
            "_async_prepare_primary_http",
            AsyncMock(return_value=None),
        ),
        patch.object(integration, "_async_clean_legacy_entities"),
        patch.object(integration, "_async_remove_legacy_system_parent_devices"),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            AsyncMock(return_value=None),
        ),
        patch.object(integration, "_schedule_layer5_start_if_ready"),
    ):
        assert await integration.async_setup_entry(hass, entry) is True

    observer = coordinator.set_local_mqtt_config_observer.call_args.args[0]
    with (
        patch.object(hass.config_entries, "async_update_entry") as update_entry,
        patch.object(integration, "_schedule_options_reconcile") as reconcile,
    ):
        observer({"enable": True, "ip": "", "port": 1883})

    update_entry.assert_not_called()
    reconcile.assert_not_called()


async def test_entry_data_change_reloads_instead_of_mutating_transports(
    hass: HomeAssistant,
) -> None:
    """Credential/data changes reload, while no in-place transport action runs."""
    entry = _entry(hass, entry_id="data-change")
    coordinator = MagicMock(name="coordinator")
    entry.runtime_data = coordinator
    bucket = integration._entry_runtime_bucket(  # ruff: ignore[private-member-access]
        hass, entry
    )
    bucket[integration._ENTRY_DATA_SNAPSHOT_RUNTIME_KEY] = {  # ruff: ignore[private-member-access]
        CONF_USERNAME: "old@example.com",
        CONF_PASSWORD: "secret",
    }
    bucket[integration._OPTIONS_SNAPSHOT_RUNTIME_KEY] = {}  # ruff: ignore[private-member-access]

    with (
        patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload_entry,
        patch.object(integration, "_schedule_options_reconcile") as reconcile,
    ):
        await integration._async_entry_updated(  # ruff: ignore[private-member-access]
            hass, entry
        )

    reload_entry.assert_awaited_once_with(entry.entry_id)
    reconcile.assert_not_called()
    coordinator.async_set_scan_interval.assert_not_called()


async def test_entry_options_apply_polling_entities_and_layer5_in_place(
    hass: HomeAssistant,
) -> None:
    """Ordinary options update HTTP scheduling and Layer 5 without a reload."""
    entry = _entry(
        hass,
        entry_id="options-in-place",
        options={
            CONF_SCAN_INTERVAL: 37,
            CONF_CREATE_CALCULATED_POWER_SENSORS: True,
            CONF_ENABLE_BLE_TRANSPORT: True,
        },
    )
    coordinator = MagicMock(name="coordinator")
    entry.runtime_data = coordinator
    bucket = integration._entry_runtime_bucket(  # ruff: ignore[private-member-access]
        hass, entry
    )
    bucket[integration._ENTRY_DATA_SNAPSHOT_RUNTIME_KEY] = dict(  # ruff: ignore[private-member-access]
        entry.data
    )
    bucket[integration._OPTIONS_SNAPSHOT_RUNTIME_KEY] = {}  # ruff: ignore[private-member-access]

    with (
        patch.object(hass.config_entries, "async_reload", AsyncMock()) as reload_entry,
        patch.object(integration, "_async_clean_legacy_entities") as clean_entities,
        patch.object(integration, "_schedule_options_reconcile") as reconcile,
    ):
        await integration._async_entry_updated(  # ruff: ignore[private-member-access]
            hass, entry
        )

    reload_entry.assert_not_awaited()
    coordinator.async_set_scan_interval.assert_called_once_with(timedelta(seconds=37))
    coordinator.async_update_listeners.assert_called_once_with()
    clean_entities.assert_called_once_with(hass, entry)
    reconcile.assert_called_once()
    assert CONF_ENABLE_BLE_TRANSPORT in reconcile.call_args.args[3]


async def test_options_reconcile_keeps_ble_running_when_local_mqtt_fails(
    hass: HomeAssistant,
) -> None:
    """One optional transport failure does not stop the independent peer."""
    entry = _entry(hass, entry_id="independent-options-reconcile")
    coordinator = MagicMock(name="coordinator")
    coordinator.async_reconcile_ble_transport = AsyncMock(return_value=None)
    entry.runtime_data = coordinator
    bucket = integration._entry_runtime_bucket(  # ruff: ignore[private-member-access]
        hass, entry
    )
    bucket[integration._OPTIONS_RECONCILE_PENDING_RUNTIME_KEY] = {  # ruff: ignore[private-member-access]
        CONF_LOCAL_MQTT_TOPIC,
        CONF_ENABLE_BLE_TRANSPORT,
    }
    local_failure = RuntimeError("local listener unavailable")

    with patch.object(
        integration,
        "_async_start_local_mqtt",
        AsyncMock(side_effect=local_failure),
    ) as start_local:
        await integration._async_reconcile_entry_options(  # ruff: ignore[private-member-access]
            hass,
            entry,
            coordinator,
        )

    start_local.assert_awaited_once_with(hass, entry, coordinator)
    coordinator.async_reconcile_ble_transport.assert_awaited_once_with()
    coordinator.async_schedule_local_mqtt_device_config.assert_called_once_with()


async def test_release_fenced_coordinator_clears_only_after_http_shutdown(
    hass: HomeAssistant,
) -> None:
    """A previous HTTP owner remains fenced until its bounded shutdown succeeds."""
    entry = _entry(hass, entry_id="fenced-owner")
    coordinator = MagicMock(spec=JackerySolarVaultCoordinator)
    entry.runtime_data = coordinator
    bucket = integration._entry_runtime_bucket(  # ruff: ignore[private-member-access]
        hass, entry
    )
    bucket[integration._UNLOADING_COORDINATOR_RUNTIME_KEY] = coordinator  # ruff: ignore[private-member-access]
    bucket[integration._PRIMARY_SETUP_COORDINATOR_RUNTIME_KEY] = coordinator  # ruff: ignore[private-member-access]

    with (
        patch.object(
            integration,
            "_async_shutdown_coordinator_bounded",
            AsyncMock(side_effect=[False, True]),
        ) as shutdown,
        patch.object(integration, "_defer_supplemental_transports") as defer,
        patch.object(integration, "_schedule_supplemental_cleanup") as cleanup,
    ):
        assert not await integration._async_release_fenced_coordinator(  # ruff: ignore[private-member-access]
            hass, entry
        )
        assert entry.runtime_data is coordinator
        assert (
            bucket[integration._UNLOADING_COORDINATOR_RUNTIME_KEY]  # ruff: ignore[private-member-access]
            is coordinator
        )

        assert await integration._async_release_fenced_coordinator(  # ruff: ignore[private-member-access]
            hass, entry
        )

    assert shutdown.await_count == 2
    assert entry.runtime_data is None
    assert integration._UNLOADING_COORDINATOR_RUNTIME_KEY not in bucket  # ruff: ignore[private-member-access]
    assert integration._PRIMARY_SETUP_COORDINATOR_RUNTIME_KEY not in bucket  # ruff: ignore[private-member-access]
    defer.assert_called_once_with(hass, entry, coordinator)
    cleanup.assert_called_once_with(hass, entry)


async def test_failed_platform_unload_preserves_http_runtime_fence(
    hass: HomeAssistant,
) -> None:
    """A failed platform unload cannot clear or shut down the active HTTP owner."""
    entry = _entry(hass, entry_id="failed-unload")
    coordinator = MagicMock(spec=JackerySolarVaultCoordinator)
    entry.runtime_data = coordinator
    shutdown = AsyncMock(return_value=True)

    with (
        patch.object(
            hass.config_entries,
            "async_unload_platforms",
            AsyncMock(return_value=False),
        ),
        patch.object(integration, "_async_shutdown_coordinator_bounded", shutdown),
        patch.object(integration, "_schedule_supplemental_cleanup"),
    ):
        assert await integration.async_unload_entry(hass, entry) is False

    shutdown.assert_not_awaited()
    assert entry.runtime_data is coordinator
    bucket = hass.data[DOMAIN][entry.entry_id]
    assert (
        bucket[integration._UNLOADING_COORDINATOR_RUNTIME_KEY]  # ruff: ignore[private-member-access]
        is coordinator
    )


def test_battery_pack_registry_identity_requires_one_parent_scoped_id(
    hass: HomeAssistant,
) -> None:
    """Battery-pack migration accepts exactly one identity scoped to its parent."""
    entry = _entry(hass, entry_id="pack-registry-identity")
    registry = dr.async_get(hass)
    parent = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "head-1")},
        name="SolarVault",
    )
    child = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "head-1_battery_pack_PACK-1")},
        name="Pack",
        via_device=(DOMAIN, "head-1"),
    )

    assert integration._battery_pack_registry_identity(  # ruff: ignore[private-member-access]
        registry,
        child,
    ) == ("head-1", "head-1_battery_pack_PACK-1", "PACK-1")
    assert child.via_device_id == parent.id

    ambiguous = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={
            (DOMAIN, "head-1_battery_pack_PACK-2"),
            (DOMAIN, "head-1_battery_pack_PACK-3"),
        },
        name="Ambiguous pack",
        via_device=(DOMAIN, "head-1"),
    )
    assert (
        integration._battery_pack_registry_identity(  # ruff: ignore[private-member-access]
            registry,
            ambiguous,
        )
        is None
    )


def test_phantom_cleanup_removes_head_unit_duplicate_pack(
    hass: HomeAssistant,
) -> None:
    """A head serial masquerading as a pack is detached without payload filtering."""
    entry = _entry(hass, entry_id="head-duplicate-pack")
    coordinator = MagicMock(name="coordinator")
    coordinator.data = {}
    entry.runtime_data = coordinator
    registry = dr.async_get(hass)
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "head-2")},
        name="SolarVault",
        serial_number="HEAD-SERIAL",
    )
    child = registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, "head-2_battery_pack_HEAD-SERIAL")},
        name="False pack",
        serial_number="HEAD-SERIAL",
        via_device=(DOMAIN, "head-2"),
    )

    integration._async_remove_phantom_battery_pack_devices(  # ruff: ignore[private-member-access]
        hass,
        entry,
    )

    remaining = registry.async_get(child.id)
    assert remaining is None or entry.entry_id not in remaining.config_entries


async def test_config_entry_device_removal_is_allowed(
    hass: HomeAssistant,
) -> None:
    """Registry removal is never blocked by transient discovery state."""
    entry = _entry(hass, entry_id="allow-device-remove")
    device = MagicMock(spec=dr.DeviceEntry)

    assert await integration.async_remove_config_entry_device(hass, entry, device)
