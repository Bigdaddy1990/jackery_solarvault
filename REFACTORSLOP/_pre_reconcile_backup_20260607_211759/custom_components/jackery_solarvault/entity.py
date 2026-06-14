"""Shared entity base class."""

import logging  # noqa: I001
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    FIELD_CURRENT_VERSION,
    FIELD_DEV_MODEL,
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_SN,
    FIELD_MODEL,
    FIELD_MODEL_NAME,
    FIELD_ONLINE_STATE,
    FIELD_ONLINE_STATUS,
    FIELD_SCAN_NAME,
    FIELD_TYPE_NAME,
    FIELD_VERSION,
    FIELD_WNAME,
    MANUFACTURER,
    PAYLOAD_ALARM,
    PAYLOAD_BATTERY_TRENDS,
    PAYLOAD_DEVICE,
    PAYLOAD_DEVICE_STATISTIC,
    PAYLOAD_DISCOVERY,
    PAYLOAD_HOME_TRENDS,
    PAYLOAD_HTTP_PROPERTIES,
    PAYLOAD_LOCATION,
    PAYLOAD_OTA,
    PAYLOAD_PRICE,
    PAYLOAD_PROPERTIES,
    PAYLOAD_PV_TRENDS,
    PAYLOAD_STATISTIC,
    PAYLOAD_SYSTEM,
    PAYLOAD_TASK_PLAN,
    PAYLOAD_WEATHER_PLAN,
)
from .coordinator import JackerySolarVaultCoordinator
from .util import (
    jackery_online_state,
    smart_plug_serial,
    stable_subdevice_key,
    subdevice_branding,
)


# --- restored from Clause/live lineage (backup) ---  # noqa: E303, RUF100
_LOGGER = logging.getLogger(__name__)


class JackeryEntity(CoordinatorEntity[JackerySolarVaultCoordinator]):  # noqa: E302, RUF100
    """Jackery entity."""

    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        key_suffix: str,
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}_{key_suffix}"

    @property
    def _payload(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get(self._device_id, {}) or {}

    @property
    def _properties(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_PROPERTIES) or {}

    @property
    def _http_properties(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_HTTP_PROPERTIES) or {}

    @property
    def _device_meta(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_DEVICE) or {}

    @property
    def _discovery(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_DISCOVERY) or {}

    @property
    def _system(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_SYSTEM) or {}

    @property
    def _statistic(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_STATISTIC) or {}

    @property
    def _price(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_PRICE) or {}

    @property
    def _pv_trends(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_PV_TRENDS) or {}

    @property
    def _alarm(self) -> object:
        return self._payload.get(PAYLOAD_ALARM)

    @property
    def _device_statistic(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_DEVICE_STATISTIC) or {}

    @property
    def _ota(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_OTA) or {}

    @property
    def _location(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_LOCATION) or {}

    @property
    def _home_trends(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_HOME_TRENDS) or {}

    @property
    def _battery_trends(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_BATTERY_TRENDS) or {}

    @property
    def _weather_plan(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_WEATHER_PLAN) or {}

    @property
    def _task_plan(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_TASK_PLAN) or {}

    @property
    def device_info(self) -> DeviceInfo:
        """Constructs the DeviceInfo for the parent SolarVault device.

        The returned DeviceInfo includes identifiers {(DOMAIN, device_id)}, manufacturer, name, model, and optional serial_number and sw_version. The display name is chosen from system.device_name, discovery.device_name, properties.wname, then falls back to "Jackery {device_id}". The model is chosen from discovery.dev_model, device_meta.model_name, then falls back to "SolarVault". Serial number and software version are included when present in device metadata/discovery and OTA data, respectively.

        Returns:
            DeviceInfo: DeviceInfo populated for the parent SolarVault device.
        """
        sys_name = self._system.get(FIELD_DEVICE_NAME)
        disc_name = self._discovery.get(FIELD_DEVICE_NAME)
        props_wname = self._properties.get(FIELD_WNAME)
        name = sys_name or disc_name or props_wname or f"Jackery {self._device_id}"

        model = (
            self._discovery.get(FIELD_DEV_MODEL)
            or self._device_meta.get(FIELD_MODEL_NAME)
            or "SolarVault"
        )
        sw_version = self._ota.get(FIELD_CURRENT_VERSION) or None
        sn = self._device_meta.get(FIELD_DEVICE_SN) or self._discovery.get(
            FIELD_DEVICE_SN
        )

        return DeviceInfo(
            identifiers={(DOMAIN, self._device_id)},
            manufacturer=MANUFACTURER,
            name=str(name),
            model=str(model),
            serial_number=sn,
            sw_version=sw_version,
        )

    def _build_smart_plug_device_info(
        self, plug_index: int, plug: dict[str, Any], plug_key: str | None = None
    ) -> DeviceInfo:
        """Construct DeviceInfo for a smart-plug subdevice attached to the parent SolarVault.

        Parameters:
            plug_index (int): 1-based index used to form the subdevice identifier and fallback display name.
            plug (dict[str, Any]): Smart-plug payload containing fields such as serial numbers, model/type names, scan name, device name, and version.

        Returns:
            DeviceInfo: Device registry metadata for the smart-plug including identifiers, manufacturer, name, model, serial_number, sw_version, and via_device.
        """
        base_name = (
            self._system.get(FIELD_DEVICE_NAME)
            or self._discovery.get(FIELD_DEVICE_NAME)
            or self._properties.get(FIELD_WNAME)
            or "SolarVault"
        )
        sn = smart_plug_serial(plug)
        stable_key = plug_key or stable_subdevice_key("smart_plug", sn, plug_index)
        # Branding lookup against the documented accessory catalog so the
        # UI shows "Shelly Plus Plug S" instead of the raw "shellyplusplugs"
        # wire identifier (PROTOCOL §3 + docs/html scanName table).
        manufacturer_brand, model_label = subdevice_branding(plug.get(FIELD_SCAN_NAME))
        display_name = (
            plug.get(FIELD_DEVICE_NAME)
            or model_label
            or plug.get(FIELD_SCAN_NAME)
            or f"Smart Plug {plug_index}"
        )
        model = (
            model_label
            or plug.get(FIELD_MODEL)
            or plug.get(FIELD_MODEL_NAME)
            or plug.get(FIELD_TYPE_NAME)
            or "Smart Plug"
        )
        version = plug.get(FIELD_VERSION) or plug.get(FIELD_CURRENT_VERSION)
        return DeviceInfo(
            identifiers={(DOMAIN, f"{self._device_id}_{stable_key}")},
            manufacturer=manufacturer_brand or MANUFACTURER,
            name=f"{base_name} {display_name}",
            model=str(model),
            serial_number=str(sn) if sn else None,
            sw_version=str(version) if version else None,
            via_device=(DOMAIN, self._device_id),
        )

    @property
    def available(self) -> bool:
        """Determine whether the entity is available.

        First verifies the parent coordinator's availability. If present, uses the device metadata `online_status` or the system `online_state` (parsed with `jackery_online_state`) to determine availability; if no explicit state is available, falls back to whether the device ID exists in the coordinator data.

        Returns:
            True if the entity is available, False otherwise.
        """
        if not super().available:
            return False
        online = self._device_meta.get(FIELD_ONLINE_STATUS)
        if online is None:
            online = self._system.get(FIELD_ONLINE_STATE)
        if online is not None:
            parsed_online = jackery_online_state(online)
            if parsed_online is not None:
                return parsed_online
        return self._device_id in (self.coordinator.data or {})
