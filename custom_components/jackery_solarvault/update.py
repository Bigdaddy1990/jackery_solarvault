"""Update platform for Jackery SolarVault."""

import asyncio
from copy import deepcopy
import hashlib
import logging
import time
from typing import TYPE_CHECKING, Any, Final

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.const import EntityCategory
from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.event import async_call_later

from .client.mqtt_discovery import _record_mqtt_operation_error
from .client.mqtt_update_discovery import JackeryMqttUpdatePublisher
from .const import (
    DOMAIN,
    FIELD_CURRENT_VERSION,
    FIELD_TARGET_VERSION,
    FIELD_UPDATE_STATUS,
    FIELD_VERSION,
    PAYLOAD_BATTERY_PACKS,
    PAYLOAD_BLE_OTA_VERSIONS,
    SERVICE_FIELD_DEVICE_ID,
    SERVICE_START_BLE_OTA_UPDATE,
    SUBDEVICE_DEV_TYPE_BATTERY_PACK,
)
from .coordinator import (
    battery_pack_serial,
    sorted_battery_pack_payloads,
    subdevice_accessories,
)
from .entity import JackeryEntity, raise_entity_action_error
from .firmware import firmware_upgrade_available
from .util import (
    append_unique_entity,
    coordinator_entity_signature,
    first_nonblank_text,
    safe_int,
    stable_subdevice_key,
)

_MAX_PERCENTAGE: Final = 100
_BLE_OTA_CANDIDATE_RETRY_SECONDS: Final = 300.0
_LOGGER = logging.getLogger(__name__)


if TYPE_CHECKING:
    from collections.abc import Callable

    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.device_registry import ChildDeviceInfo
    from homeassistant.helpers.entity_platform import AddEntitiesCallback

    from . import JackeryConfigEntry
    from .coordinator import JackerySolarVaultCoordinator


PARALLEL_UPDATES = 1


def _battery_pack_registration_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    """Return the pack rows used to register add-on battery entities."""
    packs = payload.get(PAYLOAD_BATTERY_PACKS)
    valid_packs = (
        [pack for pack in packs if isinstance(pack, dict)]
        if isinstance(packs, list)
        else []
    )
    return valid_packs or sorted_battery_pack_payloads(
        subdevice_accessories(payload, dev_type=SUBDEVICE_DEV_TYPE_BATTERY_PACK)
    )


def _ble_ota_is_for_target(
    coordinator: JackerySolarVaultCoordinator, device_id: str, target_serial: str | None
) -> bool:
    """Identify the BLE transfer by its own selected physical target."""
    pending = getattr(coordinator, "_ble_ota_pending", None)
    record = pending.get(device_id) if isinstance(pending, dict) else None
    return bool(
        target_serial
        and isinstance(record, dict)
        and record.get("target_sn") == target_serial
    )


def _ota_coordinator_active(coordinator: JackerySolarVaultCoordinator) -> bool:
    """Reject receipts from a stopped or superseded config entry."""
    entry = getattr(coordinator, "entry", None)
    return not getattr(coordinator, "_shutdown_started", False) and (
        entry is None or getattr(entry, "runtime_data", coordinator) is coordinator
    )


def _cloud_ota_row(
    coordinator: JackerySolarVaultCoordinator, target_serial: str | None
) -> dict[str, Any]:
    """Return one exact target from its own latest HTTP OTA-list response."""
    if not isinstance(target_serial, str) or not target_serial.strip():
        return {}
    api = getattr(coordinator, "api", None)
    responses = getattr(api, "last_ota_responses", None)
    response = responses.get(target_serial) if isinstance(responses, dict) else None
    return _cloud_ota_row_from_response(response, target_serial)


def _cloud_ota_row_from_response(
    response: object, target_serial: str
) -> dict[str, Any]:
    """Select exactly one serial-owned item from an HTTP OTA response."""
    data = response.get("data") if isinstance(response, dict) else None
    rows = data if isinstance(data, list) else [data] if isinstance(data, dict) else []
    matches = [
        row
        for row in rows
        if isinstance(row, dict) and row.get("deviceSn") == target_serial
    ]
    return dict(matches[0]) if len(matches) == 1 else {}


def _cloud_ota_in_progress(
    coordinator: JackerySolarVaultCoordinator, target_serial: str | None
) -> bool | None:
    """Read Cloud OTA status only from the target's own HTTP list response."""
    status = safe_int(
        _cloud_ota_row(coordinator, target_serial).get(FIELD_UPDATE_STATUS)
    )
    if status in {1, 2}:
        return True
    # BaseMqttOTAActivity.updateView: 0 can update, 1 downloading,
    # 2 installing, 3 success, 4 failed, 6 no update required.
    return False if status in {0, 3, 4, 6} else None


def _ble_ota_report_attributes(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
    target_serial: str | None,
) -> dict[str, Any]:
    """Expose only a uniquely owned BLE version report, without reinterpreting it."""
    if not target_serial or not _ota_coordinator_active(coordinator):
        return {}
    key_getter = getattr(coordinator, "device_bluetooth_key", None)
    key = key_getter(device_id) if callable(key_getter) else None
    fingerprints = getattr(coordinator, "_ble_ota_versions_fingerprint", {})
    generations = getattr(coordinator, "_ble_ota_versions_generation", {})
    sequences = getattr(coordinator, "_ble_ota_versions_first_sequence", {})
    generation = generations.get(device_id) if isinstance(generations, dict) else None
    if (
        not isinstance(key, bytes)  # ruff: ignore[too-many-boolean-expressions]
        or not isinstance(fingerprints, dict)
        or fingerprints.get(device_id) != hashlib.sha256(key).digest()
        or not isinstance(generation, int)
        or isinstance(generation, bool)
        or not isinstance(sequences, dict)
        or sequences.get(device_id) is None
    ):
        return {}
    listener = getattr(coordinator, "_ble_listener", None)
    current_generation = (
        listener.current_session_generation(device_id) if listener is not None else None
    )
    if current_generation is not None and current_generation != generation:
        return {}
    payload = (getattr(coordinator, "data", None) or {}).get(device_id, {})
    rows = payload.get(PAYLOAD_BLE_OTA_VERSIONS) if isinstance(payload, dict) else None
    matches = (
        [
            row
            for row in rows
            if isinstance(row, dict) and row.get("deviceSn") == target_serial
        ]
        if isinstance(rows, list)
        else []
    )
    if len(matches) != 1:
        return {}
    result: dict[str, Any] = {
        "ble_ota_report": deepcopy(matches[0]),
        "ble_ota_report_source": "ble",
        "ble_ota_report_session": generation,
    }
    observed_times = getattr(coordinator, "_ble_ota_versions_observed_at", {})
    observed = (
        observed_times.get(device_id) if isinstance(observed_times, dict) else None
    )
    if observed is not None and callable(getattr(observed, "isoformat", None)):
        result["ble_ota_report_received_at"] = observed.isoformat()
    return result


def _ble_ota_candidate(
    coordinator: JackerySolarVaultCoordinator,
    device_id: str,
    target_serial: str | None,
) -> dict[str, Any]:
    """Return only a candidate bound to the current BLE report and target."""
    if not target_serial or not _ble_ota_report_attributes(
        coordinator, device_id, target_serial
    ):
        return {}
    receipts = getattr(coordinator, "_ble_ota_candidate_receipts", {})
    receipt = (
        receipts.get((device_id, target_serial)) if isinstance(receipts, dict) else None
    )
    if not isinstance(receipt, dict):
        return {}
    payload = (getattr(coordinator, "data", None) or {}).get(device_id, {})
    current_report = (
        payload.get(PAYLOAD_BLE_OTA_VERSIONS) if isinstance(payload, dict) else None
    )
    resolver = getattr(coordinator, "_resolve_device_sn", None)
    parent = resolver(device_id) if callable(resolver) else None
    generations = getattr(coordinator, "_ble_ota_versions_generation", {})
    fingerprints = getattr(coordinator, "_ble_ota_versions_fingerprint", {})
    listener = getattr(coordinator, "_ble_listener", None)
    candidate = receipt.get("candidate")
    if (
        not parent  # ruff: ignore[too-many-boolean-expressions]
        or receipt.get("parent_sn") != parent
        or not isinstance(generations, dict)
        or receipt.get("generation") != generations.get(device_id)
        or listener is None
        or listener.current_session_generation(device_id) != receipt.get("generation")
        or not isinstance(fingerprints, dict)
        or receipt.get("fingerprint") != fingerprints.get(device_id)
        or receipt.get("report") != current_report
        or not isinstance(candidate, dict)
        or candidate.get("deviceSn") != target_serial
    ):
        return {}
    return deepcopy(candidate)


class _BleOtaCandidateRefresh:
    """Refresh candidates only for current reports, independently of cloud OTA."""

    def __init__(self, coordinator: JackerySolarVaultCoordinator) -> None:
        self.coordinator = coordinator
        self.attempted: dict[tuple[str, str, int], float] = {}

    def schedule(self, entities: list[UpdateEntity]) -> None:  # ruff: ignore[too-many-locals]
        coordinator = self.coordinator
        scheduler = getattr(coordinator, "_schedule_background_once", None)
        ble_enabled = getattr(coordinator, "_ble_writes_enabled", None)
        if (
            not callable(scheduler)
            or not _ota_coordinator_active(coordinator)
            or not callable(ble_enabled)
            or not ble_enabled()
        ):
            return
        now = time.monotonic()
        for entity in entities:
            if "ble" not in getattr(entity, "command_sources", ()):
                continue
            device_id = getattr(entity, "_device_id", None)
            if not isinstance(device_id, str) or not device_id:
                continue
            resolver = getattr(coordinator, "_resolve_device_sn", None)
            parent = resolver(device_id) if callable(resolver) else None
            target = getattr(entity, "_pack_sn", None) or parent
            if not parent or not target:
                continue
            if _ble_ota_candidate(coordinator, device_id, target):
                continue
            listener = getattr(coordinator, "_ble_listener", None)
            generation = (
                listener.current_session_generation(device_id) if listener else None
            )
            report_matches = getattr(
                coordinator, "_ble_ota_report_matches_target", None
            )
            if generation is None or not callable(report_matches):
                continue
            has_report = bool(
                _ble_ota_report_attributes(coordinator, device_id, target)
            )
            report_current = has_report and report_matches(
                device_id, generation, target
            )
            is_head = getattr(entity, "_pack_index", None) is None
            writes_enabled = getattr(coordinator, "_ble_writes_enabled", None)
            if not report_current and (
                not is_head or not callable(writes_enabled) or not writes_enabled()
            ):
                continue
            key = (device_id, target, generation)
            previous = self.attempted.get(key)
            if (
                previous is not None
                and now - previous < _BLE_OTA_CANDIDATE_RETRY_SECONDS
            ):
                continue
            self.attempted[key] = now
            scheduler(
                f"ble_ota_candidate:{device_id}:{target}",
                lambda entity=entity, device_id=device_id, parent=parent, target=target, generation=generation, need_report=not report_current: (  # ruff: ignore[line-too-long]
                    self._refresh(
                        entity, device_id, parent, target, generation, need_report
                    )
                ),
                name="jackery_solarvault_ble_ota_candidate",
            )

    async def _refresh(  # ruff: ignore[too-many-arguments, too-many-positional-arguments]
        self,
        entity: UpdateEntity,
        device_id: str,
        parent: str,
        target: str,
        generation: int,
        need_report: bool,
    ) -> None:
        coordinator = self.coordinator
        pack_index = getattr(entity, "_pack_index", None)
        revision_fn = getattr(coordinator, "battery_pack_identity_revision", None)
        revision = (
            revision_fn(device_id, target)
            if pack_index is not None and callable(revision_fn)
            else None
        )

        def identity_is_current() -> bool:
            listener = getattr(coordinator, "_ble_listener", None)
            if (
                not _ota_coordinator_active(coordinator)
                or not callable(getattr(coordinator, "_ble_writes_enabled", None))
                or not coordinator._ble_writes_enabled()  # ruff: ignore[private-member-access]
            ):
                return False
            if (
                listener is None
                or listener.current_session_generation(device_id) != generation
                or coordinator._resolve_device_sn(device_id) != parent  # ruff: ignore[private-member-access]
            ):
                return False
            if pack_index is None:
                return target == parent
            return (
                coordinator.battery_pack_identity_serial(device_id, pack_index)
                == target
                and callable(revision_fn)
                and revision_fn(device_id, target) == revision
            )

        if not identity_is_current():
            return
        try:
            await coordinator.async_check_ble_ota_versions(
                device_id,
                target_sn=target,
                parent_sn=parent,
                recovery_query=need_report,
                identity_is_current=identity_is_current,
            )
        except asyncio.CancelledError:
            raise
        except Exception as err:  # ruff: ignore[blind-except]
            _LOGGER.warning(
                "BLE OTA candidate refresh failed for %s: %s",
                device_id,
                type(err).__name__,
            )
            return
        if identity_is_current():
            # An empty candidate result still counts as a completed check.
            coordinator.async_update_listeners()


class JackeryDeviceUpdateEntity(JackeryEntity, UpdateEntity):
    """Firmware update entity for the Jackery SolarVault main device."""

    _attr_has_entity_name = True
    _attr_translation_key = "firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.CONFIG
    command_sources: tuple[str, ...] = ("ble",)
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL
        | UpdateEntityFeature.PROGRESS
        | UpdateEntityFeature.RELEASE_NOTES
    )

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
    ) -> None:
        """Initialize the device firmware update entity."""
        super().__init__(coordinator, device_id, "device_firmware_update")

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Keep the actual BLE module report separate from OTA cloud metadata."""
        if self.command_sources != ("ble",):
            return {}
        resolver = getattr(self.coordinator, "_resolve_device_sn", None)
        serial = resolver(self._device_id) if callable(resolver) else None
        return _ble_ota_report_attributes(self.coordinator, self._device_id, serial)

    @property
    def _ota(self) -> dict[str, Any]:
        """Use only the version candidate derived from this device's BLE report."""
        resolver = getattr(self.coordinator, "_resolve_device_sn", None)
        serial = resolver(self._device_id) if callable(resolver) else None
        return _ble_ota_candidate(self.coordinator, self._device_id, serial)

    @property
    def installed_version(self) -> str | None:
        """Version currently installed on the device."""
        return first_nonblank_text(self._ota.get(FIELD_CURRENT_VERSION))

    @property
    def latest_version(self) -> str | None:
        """Latest version available for install."""
        ota = self._ota
        # An explicit device/cloud flag is stronger than a possibly retained
        # target from an earlier update.
        upgrade_available = firmware_upgrade_available(ota)
        if upgrade_available is False:
            return self.installed_version
        new_version = first_nonblank_text(
            ota.get("newVersion"),
            ota.get("latestVersion"),
            ota.get("new_version"),
            ota.get("latest_version"),
            ota.get(FIELD_TARGET_VERSION),
        )
        if new_version is not None:
            return new_version

        # If the firmware upgrade flag is set by cloud/device, signal update available
        if upgrade_available is True:
            installed = self.installed_version
            return f"{installed} (update available)" if installed else "available"

        return None

    @property
    def in_progress(self) -> bool | None:
        """Report only a BLE transfer targeting this main device."""
        resolver = getattr(self.coordinator, "_resolve_device_sn", None)
        serial = resolver(self._device_id) if callable(resolver) else None
        return _ble_ota_is_for_target(self.coordinator, self._device_id, serial)

    @property
    def update_percentage(self) -> int | None:
        """Return BLE progress only for this device's transfer."""  # ruff: ignore[property-docstring-starts-with-verb]
        if not self.in_progress:
            return None
        progress_map = getattr(self.coordinator, "_ble_ota_progress", None)
        if isinstance(progress_map, dict):
            progress = progress_map.get(self._device_id)
            if isinstance(progress, (int, float)) and 0 <= progress <= _MAX_PERCENTAGE:
                return int(progress)
        return None

    @property
    def release_url(self) -> str | None:
        """URL to the full release notes or firmware description."""
        ota = self._ota
        return first_nonblank_text(
            ota.get("downloadUrl"),
            ota.get("url"),
            ota.get("releaseUrl"),
        )

    async def async_release_notes(self) -> str | None:
        """Return the selected device firmware's notes, not mobile-app notes."""
        ota = self._ota
        return first_nonblank_text(
            ota.get("updateContent"),
            ota.get("releaseNotes"),
            ota.get("description"),
            ota.get("notes"),
        )

    async def async_install(
        self,
        version: str | None = None,
        backup: bool = False,
        **kwargs: object,
    ) -> None:
        """Trigger firmware update installation via coordinator."""
        del version, backup, kwargs
        entry = self.coordinator.config_entry
        device = (
            dr.async_get(self.hass).async_get_device_by_identifier(
                (DOMAIN, self._device_id), entry.entry_id
            )
            if entry is not None
            else None
        )
        if device is None:
            raise_entity_action_error(
                "firmware_update",
                self._device_id,
                "The firmware target is not registered",
            )
        try:
            await self.hass.services.async_call(
                DOMAIN,
                SERVICE_START_BLE_OTA_UPDATE,
                {SERVICE_FIELD_DEVICE_ID: device.id},
                blocking=True,
                return_response=True,
            )
        except Exception as err:  # ruff: ignore[blind-except]
            raise_entity_action_error("firmware_update", self._device_id, err)


class JackeryBatteryPackUpdateEntity(JackeryEntity, UpdateEntity):
    """Firmware update entity for an add-on battery pack."""

    _attr_has_entity_name = True
    _attr_translation_key = "battery_pack_firmware"
    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    )
    command_sources: tuple[str, ...] = ("ble",)

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        pack_index: int,
        pack_sn: str | None,
        pack_key: str,
    ) -> None:
        """Initialize the battery pack firmware update entity."""
        super().__init__(
            coordinator,
            device_id,
            f"{pack_key}_firmware_update",
        )
        self._pack_index = pack_index
        self._pack_sn = pack_sn
        self._pack_key = pack_key

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Expose only this pack's own BLE OTA report."""
        if self.command_sources != ("ble",) or self._pack_sn is None:
            return {}
        identity = getattr(self.coordinator, "battery_pack_identity_serial", None)
        if (
            not callable(identity)
            or identity(self._device_id, self._pack_index) != self._pack_sn
        ):
            return {}
        return _ble_ota_report_attributes(
            self.coordinator, self._device_id, self._pack_sn
        )

    @property
    def _pack(self) -> dict[str, Any]:
        """Current battery pack payload dictionary."""
        packs = self._payload.get(PAYLOAD_BATTERY_PACKS) or []
        pack_dicts = sorted_battery_pack_payloads(packs)
        if self._pack_sn is not None:
            matches = [
                pack
                for pack in pack_dicts
                if battery_pack_serial(pack) == self._pack_sn
            ]
            return matches[0] if len(matches) == 1 else {}
        if 1 <= self._pack_index <= len(pack_dicts):
            return pack_dicts[self._pack_index - 1]
        return {}

    @property
    def device_info(self) -> DeviceInfo | ChildDeviceInfo:
        """Link to the pack's registered child without duplicate full devices."""
        child = self._pack_child_device_info()
        if child is not None:
            return child
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._device_id}_{self._pack_key}")},
        )

    @property
    def installed_version(self) -> str | None:
        """Installed firmware version on the pack."""
        pack = self._firmware_source
        version = first_nonblank_text(
            pack.get(FIELD_VERSION),
            pack.get(FIELD_CURRENT_VERSION),
        )
        if version is not None:
            return version
        return None

    @property
    def latest_version(self) -> str | None:
        """Latest available version on the pack."""
        pack = self._firmware_source
        upgrade_available = firmware_upgrade_available(pack)
        if upgrade_available is False:
            return self.installed_version
        new_version = first_nonblank_text(
            pack.get("newVersion"),
            pack.get("latestVersion"),
            pack.get(FIELD_TARGET_VERSION),
        )
        if new_version is not None:
            return new_version

        if upgrade_available is True:
            installed = self.installed_version
            return f"{installed} (update available)" if installed else "available"

        return None

    @property
    def _firmware_source(self) -> dict[str, Any]:
        """Keep BLE candidates bound to the current pack; HTTP owns its list."""
        if self.command_sources != ("ble",):
            return self._pack
        identity = getattr(self.coordinator, "battery_pack_identity_serial", None)
        if (
            not callable(identity)
            or identity(self._device_id, self._pack_index) != self._pack_sn
        ):
            return {}
        return _ble_ota_candidate(self.coordinator, self._device_id, self._pack_sn)

    @property
    def in_progress(self) -> bool | None:
        """Report BLE transfer state only for this pack's serial."""
        return _ble_ota_is_for_target(self.coordinator, self._device_id, self._pack_sn)

    async def async_install(
        self, version: str | None = None, backup: bool = False, **kwargs: object
    ) -> None:
        """Start BLE OTA for this pack's registered physical device."""
        del version, backup, kwargs
        entry = self.coordinator.config_entry
        registry = dr.async_get(self.hass)
        identifier = (DOMAIN, f"{self._device_id}_{self._pack_key}")
        device = (
            registry.async_get_device_by_identifier(identifier, entry.entry_id)
            if entry is not None
            else None
        )
        child_lookup = getattr(registry, "async_get_child_device_by_identifier", None)
        if device is None and entry is not None and callable(child_lookup):
            device = child_lookup(identifier, entry.entry_id)
        if device is None:
            raise_entity_action_error(
                "firmware_update",
                self._device_id,
                "The battery pack firmware target is not registered",
            )
        try:
            await self.hass.services.async_call(
                DOMAIN,
                SERVICE_START_BLE_OTA_UPDATE,
                {SERVICE_FIELD_DEVICE_ID: device.id},
                blocking=True,
                return_response=True,
            )
        except Exception as err:  # ruff: ignore[blind-except]
            raise_entity_action_error("firmware_update", self._device_id, err)


class JackeryCloudDeviceUpdateEntity(JackeryDeviceUpdateEntity):
    """Separate App-HTTP start for the device-side Cloud OTA job."""

    _attr_translation_key = "firmware_cloud"
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.RELEASE_NOTES
    )
    command_sources = ("http",)

    @property
    def _ota(self) -> dict[str, Any]:
        """Keep Cloud update metadata tied to the selected HTTP response."""
        if not _ota_coordinator_active(self.coordinator):
            return {}
        receipts = getattr(self.coordinator, "_accepted_head_cloud_ota", {})
        receipt = receipts.get(self._device_id) if isinstance(receipts, dict) else None
        if not isinstance(receipt, tuple) or len(receipt) != 4:  # ruff: ignore[magic-value-comparison]
            return {}
        binding, epoch, serial, response = receipt
        if (
            not self.coordinator._http_parent_binding_is_current(  # ruff: ignore[private-member-access]
                self._device_id, binding
            )
            or getattr(self.coordinator, "_http_data_identity_epochs", {}).get(
                self._device_id, 0
            )
            != epoch
            or self.coordinator._resolve_device_sn(self._device_id) != serial  # ruff: ignore[private-member-access]
        ):
            return {}
        return _cloud_ota_row_from_response(response, serial)

    @property
    def in_progress(self) -> bool | None:  # ruff: ignore[undocumented-public-method]
        status = safe_int(self._ota.get(FIELD_UPDATE_STATUS))
        if status in {1, 2}:
            return True
        return False if status in {0, 3, 4, 6} else None

    @property
    def update_percentage(self) -> None:  # ruff: ignore[undocumented-public-method]
        return None

    def __init__(  # ruff: ignore[undocumented-public-init]
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        super().__init__(coordinator, device_id)
        self._attr_unique_id = f"{device_id}_device_cloud_firmware_update"

    async def async_install(
        self, version: str | None = None, backup: bool = False, **kwargs: object
    ) -> None:
        """Start a freshly selected App Cloud OTA operation."""
        from .client.cloud_ota import async_start_cloud_ota  # ruff: ignore[import-outside-top-level]

        del backup, kwargs
        try:
            await async_start_cloud_ota(
                self.coordinator, self._device_id, requested_version=version
            )
        except Exception as err:  # ruff: ignore[blind-except]
            raise_entity_action_error("firmware_update", self._device_id, err)


class JackeryCloudBatteryPackUpdateEntity(JackeryBatteryPackUpdateEntity):
    """Separate App-HTTP OTA start for one serial-bound battery pack."""

    _attr_translation_key = "battery_pack_firmware_cloud"
    _attr_supported_features = UpdateEntityFeature.INSTALL
    command_sources = ("http",)

    @property
    def _pack(self) -> dict[str, Any]:
        """Require a current matching pack before exposing its HTTP response."""
        if not _ota_coordinator_active(self.coordinator) or not self._pack_sn:
            return {}
        if (
            not super()._pack
            or self.coordinator.battery_pack_identity_serial(
                self._device_id, self._pack_index
            )
            != self._pack_sn
        ):
            return {}
        receipts = getattr(self.coordinator, "_accepted_pack_cloud_ota", {})
        receipt = (
            receipts.get((self._device_id, self._pack_sn))
            if isinstance(receipts, dict)
            else None
        )
        if not isinstance(receipt, tuple) or len(receipt) != 5:  # ruff: ignore[magic-value-comparison]
            return {}
        binding, data_epoch, pack_epoch, revision, response = receipt
        if (
            not self.coordinator._http_parent_binding_is_current(  # ruff: ignore[private-member-access]
                self._device_id, binding
            )
            or getattr(self.coordinator, "_http_data_identity_epochs", {}).get(
                self._device_id, 0
            )
            != data_epoch
            or getattr(self.coordinator, "_http_pack_owner_epochs", {}).get(
                (self._device_id, self._pack_sn), 0
            )
            != pack_epoch
            or self.coordinator.battery_pack_identity_revision(
                self._device_id, self._pack_sn
            )
            != revision
        ):
            return {}
        return _cloud_ota_row_from_response(response, self._pack_sn)

    @property
    def in_progress(self) -> bool | None:  # ruff: ignore[undocumented-public-method]
        pack = self._pack
        if not pack:
            return None
        status = safe_int(pack.get(FIELD_UPDATE_STATUS))
        if status in {1, 2}:
            return True
        return False if status in {0, 3, 4, 6} else None

    def __init__(  # ruff: ignore[undocumented-public-init]
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        pack_index: int,
        pack_sn: str,
        pack_key: str,
    ) -> None:
        super().__init__(
            coordinator,
            device_id,
            pack_index=pack_index,
            pack_sn=pack_sn,
            pack_key=pack_key,
        )
        self._attr_unique_id = f"{device_id}_{pack_key}_cloud_firmware_update"

    async def async_install(
        self, version: str | None = None, backup: bool = False, **kwargs: object
    ) -> None:
        """Start the selected pack's App Cloud OTA operation."""
        from .client.cloud_ota import async_start_cloud_ota  # ruff: ignore[import-outside-top-level]

        del backup, kwargs
        try:
            await async_start_cloud_ota(
                self.coordinator,
                self._device_id,
                target_serial=self._pack_sn,
                requested_version=version,
            )
        except Exception as err:  # ruff: ignore[blind-except]
            raise_entity_action_error("firmware_update", self._device_id, err)


class _UnifiedFirmwareSources:
    """One registered update entity with separate, serial-owned OTA readers."""

    command_sources: tuple[str, ...] = ("ble", "http")
    _device_id: str
    _ble_source: JackeryDeviceUpdateEntity | JackeryBatteryPackUpdateEntity
    _http_source: JackeryCloudDeviceUpdateEntity | JackeryCloudBatteryPackUpdateEntity

    @property
    def _firmware_source_values(self) -> dict[str, dict[str, Any]]:
        values: dict[str, dict[str, Any]] = {}
        for layer, source in (("ble", self._ble_source), ("http", self._http_source)):
            payload = (
                source._ota  # ruff: ignore[private-member-access]
                if isinstance(source, JackeryDeviceUpdateEntity)
                and not isinstance(source, JackeryBatteryPackUpdateEntity)
                else source._firmware_source  # ruff: ignore[private-member-access]
            )
            values[layer] = {
                "installed_version": source.installed_version,
                "latest_version": source.latest_version,
                "in_progress": source.in_progress,
                "upgrade_available": firmware_upgrade_available(payload),
            }
        return values

    @property
    def _selected_firmware_source(self) -> str | None:  # ruff: ignore[too-many-return-statements]
        values = self._firmware_source_values
        ble, http = values["ble"], values["http"]
        if ble["in_progress"]:
            return "ble"
        if http["in_progress"]:
            return "http"
        # A verified update candidate outranks a no-update result. If both
        # transports can update, prefer local BLE, preserving the layer policy.
        if ble["upgrade_available"] is True and ble["latest_version"]:
            return "ble"
        if http["upgrade_available"] is True and http["latest_version"]:
            return "http"
        for layer, value in (("ble", ble), ("http", http)):
            if (
                value["upgrade_available"] is not False
                and value["installed_version"]
                and value["latest_version"]
                and value["installed_version"] != value["latest_version"]
            ):
                return layer
        if ble["installed_version"] or ble["latest_version"]:
            return "ble"
        return "http" if http["installed_version"] or http["latest_version"] else None

    @property
    def _active_firmware_reader(self) -> UpdateEntity:
        return (
            self._http_source
            if self._selected_firmware_source == "http"
            else self._ble_source
        )

    @property
    def installed_version(self) -> str | None:
        selected = self._active_firmware_reader
        other = self._http_source if selected is self._ble_source else self._ble_source
        return selected.installed_version or other.installed_version

    @property
    def latest_version(self) -> str | None:
        return self._active_firmware_reader.latest_version

    @property
    def in_progress(self) -> bool:
        return bool(self._ble_source.in_progress or self._http_source.in_progress)

    @property
    def update_percentage(self) -> int | None:
        return (
            safe_int(self._ble_source.update_percentage)
            if self._ble_source.in_progress
            else None
        )

    @property
    def release_url(self) -> str | None:
        return getattr(self._active_firmware_reader, "release_url", None)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        # Never mix two firmware candidates into a forged source payload.
        # Their actual per-layer versions are accessible on this same entity.
        attributes = dict(self._ble_source.extra_state_attributes or {})
        attributes["firmware_source_values"] = self._firmware_source_values
        attributes["selected_firmware_source"] = self._selected_firmware_source
        return attributes

    async def async_release_notes(self) -> str | None:
        reader = self._active_firmware_reader
        notes = getattr(reader, "async_release_notes", None)
        return await notes() if callable(notes) else None

    async def async_install(
        self,
        version: str | None = None,
        backup: bool = False,
        **kwargs: object,
    ) -> None:
        """Send the install request only to the source supplying the candidate."""
        selected_source = self._selected_firmware_source
        candidate = self._firmware_source_values.get(selected_source or "")
        has_current_candidate = bool(
            candidate
            and candidate["latest_version"]
            and (
                candidate["upgrade_available"] is True
                or (
                    candidate["upgrade_available"] is not False
                    and candidate["installed_version"]
                    and candidate["installed_version"] != candidate["latest_version"]
                )
            )
        )
        if not has_current_candidate:
            raise_entity_action_error(
                "firmware_update", self._device_id, "No current firmware candidate"
            )
        else:
            # A rejected candidate must never fall through to a transport write.
            selected = (
                self._http_source if selected_source == "http" else self._ble_source
            )
            if version is not None and version != selected.latest_version:
                raise_entity_action_error(
                    "firmware_update", self._device_id, "Firmware candidate changed"
                )
            elif selected is self._ble_source:
                # The source reader is never registered with HA and has no hass.
                # Dispatch BLE services from the one canonical registered entity.
                if isinstance(self, JackeryBatteryPackUpdateEntity):
                    await JackeryBatteryPackUpdateEntity.async_install(
                        self, version=version, backup=backup, **kwargs
                    )
                else:
                    assert isinstance(self, JackeryDeviceUpdateEntity)
                    await JackeryDeviceUpdateEntity.async_install(
                        self, version=version, backup=backup, **kwargs
                    )
            else:
                await selected.async_install(version=version, backup=backup, **kwargs)


class JackeryUnifiedDeviceUpdateEntity(
    _UnifiedFirmwareSources, JackeryDeviceUpdateEntity
):
    """One stable main-device update ID; BLE and HTTP feed its own state."""

    def __init__(  # ruff: ignore[undocumented-public-init]
        self, coordinator: JackerySolarVaultCoordinator, device_id: str
    ) -> None:
        super().__init__(coordinator, device_id)
        self._ble_source = JackeryDeviceUpdateEntity(coordinator, device_id)
        self._http_source = JackeryCloudDeviceUpdateEntity(coordinator, device_id)


class JackeryUnifiedBatteryPackUpdateEntity(
    _UnifiedFirmwareSources, JackeryBatteryPackUpdateEntity
):
    """One update ID for exactly this physical, serial-bound battery pack."""

    def __init__(  # ruff: ignore[undocumented-public-init]
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        *,
        pack_index: int,
        pack_sn: str,
        pack_key: str,
    ) -> None:
        super().__init__(
            coordinator,
            device_id,
            pack_index=pack_index,
            pack_sn=pack_sn,
            pack_key=pack_key,
        )
        self._ble_source = JackeryBatteryPackUpdateEntity(
            coordinator,
            device_id,
            pack_index=pack_index,
            pack_sn=pack_sn,
            pack_key=pack_key,
        )
        self._http_source = JackeryCloudBatteryPackUpdateEntity(
            coordinator,
            device_id,
            pack_index=pack_index,
            pack_sn=pack_sn,
            pack_key=pack_key,
        )


def _legacy_cloud_firmware_id(unique_id: str) -> str | None:
    """Map the old HTTP-only update identity to its canonical BLE-based key."""
    if unique_id.endswith("_device_firmware_update"):
        return (
            unique_id.removesuffix("_device_firmware_update")
            + "_device_cloud_firmware_update"
        )
    if unique_id.endswith("_firmware_update"):
        return unique_id.removesuffix("_firmware_update") + "_cloud_firmware_update"
    return None


def _async_remove_legacy_cloud_firmware_updates(  # ruff: ignore[too-many-branches]
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    entities: list[UpdateEntity],
) -> int:
    """Retire an obsolete native Cloud ID only after its single replacement exists.

    Never steal another live config entry or discard conflicting user metadata.
    """
    from homeassistant.helpers import entity_registry as er  # ruff: ignore[import-outside-top-level]

    registry = er.async_get(hass)
    count = 0
    for entity in entities:
        canonical_uid = getattr(entity, "unique_id", None)
        if not isinstance(canonical_uid, str):
            canonical_uid = getattr(entity, "_attr_unique_id", None)
        if not isinstance(canonical_uid, str):
            continue
        old_uid = _legacy_cloud_firmware_id(canonical_uid)
        if old_uid is None:
            continue
        new_id = registry.async_get_entity_id("update", DOMAIN, canonical_uid)
        old_id = registry.async_get_entity_id("update", DOMAIN, old_uid)
        if not new_id or not old_id or new_id == old_id:
            continue
        current, legacy = registry.async_get(new_id), registry.async_get(old_id)
        if (
            current is None
            or legacy is None
            or current.config_entry_id != entry.entry_id
            or current.platform != DOMAIN
            or legacy.platform != DOMAIN
        ):
            continue
        if (
            legacy.config_entry_id is not None
            and legacy.config_entry_id != entry.entry_id
        ):
            old_owner = hass.config_entries.async_get_entry(legacy.config_entry_id)
            if old_owner is not None:
                continue
        # Keep user naming and ownership settings where they are unambiguous.
        transfer: dict[str, Any] = {}
        conflict = False
        for field in ("name", "icon", "area_id"):
            old_value, current_value = (
                getattr(legacy, field, None),
                getattr(current, field, None),
            )
            if old_value is not None:
                if current_value is not None and current_value != old_value:
                    conflict = True
                    break
                if current_value is None:
                    transfer[field] = old_value
        if conflict:
            _LOGGER.warning(
                "Cannot merge customized Cloud update %s into %s", old_id, new_id
            )
            continue
        labels = set(getattr(current, "labels", ())) | set(
            getattr(legacy, "labels", ())
        )
        if labels != set(getattr(current, "labels", ())):
            transfer["labels"] = labels
        aliases = sorted(
            set(getattr(current, "aliases", ())) | set(getattr(legacy, "aliases", ()))
        )
        if set(aliases) != set(getattr(current, "aliases", ())):
            transfer["aliases"] = aliases
        if transfer:
            registry.async_update_entity(new_id, **transfer)
        registry.async_remove(old_id)
        _LOGGER.info(
            "Unified firmware registry identity %s into %s", old_uid, canonical_uid
        )
        count += 1
    return count


async def async_setup_entry(  # ruff: ignore[unused-async]  # ruff: ignore[complex-structure, too-many-statements]
    hass: HomeAssistant,
    entry: JackeryConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Jackery SolarVault update platform."""
    coordinator: JackerySolarVaultCoordinator = entry.runtime_data
    mqtt_publisher: JackeryMqttUpdatePublisher | None = None
    mqtt_retry_at = 0.0
    mqtt_retry_unsubscribe: Callable[[], None] | None = None
    all_entities: list[UpdateEntity] = []
    seen_unique_ids: set[str] = set()
    ble_candidate_refresh = _BleOtaCandidateRefresh(coordinator)

    def _cancel_mqtt_setup_retry() -> None:
        nonlocal mqtt_retry_unsubscribe
        if mqtt_retry_unsubscribe is not None:
            mqtt_retry_unsubscribe()
            mqtt_retry_unsubscribe = None

    def _retry_mqtt_setup(_now: object) -> None:
        nonlocal mqtt_retry_unsubscribe, mqtt_retry_at
        mqtt_retry_unsubscribe = None
        mqtt_retry_at = 0.0
        _ensure_mqtt_publisher()

    entry.async_on_unload(_cancel_mqtt_setup_retry)

    def _ensure_mqtt_publisher() -> None:
        """Retry mirror setup without blocking native update entities."""
        nonlocal mqtt_publisher, mqtt_retry_at, mqtt_retry_unsubscribe
        now = time.monotonic()
        if (
            mqtt_publisher is not None
            or "mqtt" not in hass.config.components
            or now < mqtt_retry_at
            or entry.runtime_data is not coordinator
            or getattr(coordinator, "_shutdown_started", False)
        ):
            return
        publisher: JackeryMqttUpdatePublisher | None = None
        try:
            # MQTT retains historical updates for cleanup, never registers
            # another firmware entity for the same physical target.
            publisher = JackeryMqttUpdatePublisher(
                hass, entry, coordinator, mirror_mode=False
            )
            entry.async_on_unload(publisher.retire)
            publisher.track(all_entities)
        except Exception as err:  # ruff: ignore[blind-except]
            if publisher is not None:
                publisher.retire()
            mqtt_retry_at = now + 30.0
            _record_mqtt_operation_error(
                hass, "update_mirror", "setup", entry.entry_id, err, entry=entry
            )
            if mqtt_retry_unsubscribe is None:
                try:
                    mqtt_retry_unsubscribe = async_call_later(
                        hass, 30.0, _retry_mqtt_setup
                    )
                except Exception as retry_err:  # ruff: ignore[blind-except]
                    _record_mqtt_operation_error(
                        hass,
                        "update_mirror",
                        "retry_schedule",
                        entry.entry_id,
                        retry_err,
                        entry=entry,
                    )
            return
        mqtt_publisher = publisher
        _cancel_mqtt_setup_retry()

    def _collect_entities() -> list[UpdateEntity]:
        entities: list[UpdateEntity] = []
        for dev_id, raw_payload in (coordinator.data or {}).items():
            payload: object = raw_payload
            if not isinstance(payload, dict):
                continue
            family = coordinator.device_family(dev_id)
            if family == "unknown":
                continue

            # Main SolarVault device update entity
            append_unique_entity(
                entities,
                seen_unique_ids,
                (
                    JackeryUnifiedDeviceUpdateEntity
                    if family == "home"
                    else JackeryDeviceUpdateEntity
                )(coordinator, dev_id),
            )

            # Battery pack update entities
            registration_packs = _battery_pack_registration_rows(payload)
            resolve_serial = getattr(coordinator, "_resolve_device_sn", None)
            head_serial = resolve_serial(dev_id) if callable(resolve_serial) else None
            head_key = (
                head_serial.strip().casefold() if isinstance(head_serial, str) else None
            )
            for index in range(1, len(registration_packs) + 1):
                serial = coordinator.battery_pack_identity_serial(dev_id, index)
                if serial is None or serial.strip().casefold() == head_key:
                    continue
                stable_key = stable_subdevice_key("battery_pack", serial, index)
                append_unique_entity(
                    entities,
                    seen_unique_ids,
                    (
                        JackeryUnifiedBatteryPackUpdateEntity
                        if family == "home"
                        else JackeryBatteryPackUpdateEntity
                    )(
                        coordinator,
                        dev_id,
                        pack_index=index,
                        pack_sn=serial,
                        pack_key=stable_key,
                    ),
                )
        return entities

    entities = _collect_entities()
    if entities:
        async_add_entities(entities)
        all_entities.extend(entities)

    # Entity registration can complete after async_add_entities returns. Watch
    # the registry once, then retire only the legacy HTTP-only OTA identities.
    if getattr(hass, "bus", None) is not None:
        from homeassistant.helpers import entity_registry as er  # ruff: ignore[import-outside-top-level]

        @callback
        def _on_update_registered(event: Any) -> None:  # ruff: ignore[any-type]
            if entry.runtime_data is coordinator and str(
                event.data.get("entity_id", "")
            ).startswith("update."):
                _async_remove_legacy_cloud_firmware_updates(hass, entry, all_entities)

        entry.async_on_unload(
            hass.bus.async_listen(
                er.EVENT_ENTITY_REGISTRY_UPDATED, _on_update_registered
            )
        )
        _async_remove_legacy_cloud_firmware_updates(hass, entry, all_entities)
    _ensure_mqtt_publisher()
    ble_candidate_refresh.schedule(all_entities)

    last_signature = coordinator_entity_signature(coordinator.data)

    def _on_coordinator_update() -> None:
        nonlocal last_signature
        current_signature = coordinator_entity_signature(coordinator.data)
        if current_signature != last_signature:
            last_signature = current_signature
            new_entities = _collect_entities()
            if new_entities:
                async_add_entities(new_entities)
                all_entities.extend(new_entities)
                if mqtt_publisher is not None:
                    mqtt_publisher.track(new_entities)
        _ensure_mqtt_publisher()
        ble_candidate_refresh.schedule(all_entities)

    entry.async_on_unload(coordinator.async_add_listener(_on_coordinator_update))
