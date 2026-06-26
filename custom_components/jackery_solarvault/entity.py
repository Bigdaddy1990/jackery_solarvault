"""Shared entity base class."""

import logging
from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    FIELD_CURRENT_VERSION,
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_SN,
    FIELD_DEV_MODEL,
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
    PAYLOAD_SOCKET_STAT,
    PAYLOAD_STATISTIC,
    PAYLOAD_SYMMETRY_STAT,
    PAYLOAD_SYSTEM,
    PAYLOAD_TASK_PLAN,
    PAYLOAD_WEATHER_PLAN,
)
from .coordinator import JackerySolarVaultCoordinator
from .util import (
    first_nonblank_text,
    jackery_online_state,
    nonblank_text,
    smart_plug_serial,
    stable_subdevice_key,
    subdevice_branding,
)

_LOGGER = logging.getLogger(__name__)


class JackeryEntity(CoordinatorEntity[JackerySolarVaultCoordinator]):
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
        """Fetch the photovoltaic (PV) trends section from the device payload.

        Returns:
            dict[str, Any]: PV trends data from the device payload, or an empty dict if
            not present.
        """  # noqa: D421
        return self._payload.get(PAYLOAD_PV_TRENDS) or {}

    @property
    def _alarm(self) -> object:
        """Return the alarm payload for the device.

        Returns:
            The alarm payload object from the device payload, or None if no alarm data
            is present.
        """  # noqa: D421
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
    def _symmetry_stat(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_SYMMETRY_STAT) or {}

    @property
    def _socket_stat(self) -> dict[str, Any]:
        return self._payload.get(PAYLOAD_SOCKET_STAT) or {}

    @property
    def device_info(self) -> DeviceInfo:
        """Build DeviceInfo for the parent SolarVault device.

        Chooses the display name from `system.device_name`, then
        `discovery.device_name`, then `properties.wname`, and falls back to `"Jackery
        {device_id}"`. Chooses the model from `discovery.dev_model`, then
        `device_meta.model_name`, and falls back to `"SolarVault"`. Includes
        `serial_number` from `device_meta.device_sn` or `discovery.device_sn` when
        present, and `sw_version` from `ota.current_version` when present.

        Returns:
            DeviceInfo: DeviceInfo containing identifiers, manufacturer, name, model,
            and optional `serial_number` and `sw_version`.
        """
        sys_name = self._system.get(FIELD_DEVICE_NAME)
        disc_name = self._discovery.get(FIELD_DEVICE_NAME)
        props_wname = self._properties.get(FIELD_WNAME)
        name = sys_name or disc_name or props_wname or f"Jackery {self._device_id}"
        )

        model = first_nonblank_text(
            self._discovery.get(FIELD_DEV_MODEL),
            self._device_meta.get(FIELD_MODEL_NAME),
            fallback="SolarVault",
        )
        sw_version = nonblank_text(self._ota.get(FIELD_CURRENT_VERSION))
        sn = first_nonblank_text(
            self._device_meta.get(FIELD_DEVICE_SN),
            self._discovery.get(
            FIELD_DEVICE_SN
            ),
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
        self,
        plug_index: int,
        plug: dict[str, Any],
        self, plug_index: int, plug: dict[str, Any], plug_key: str | None = None
    ) -> DeviceInfo:
        """Builds DeviceInfo metadata for a smart-plug subdevice attached to the parent.

        SolarVault.

        Parameters:
            plug_index (int): 1-based index used for fallback display name and for
            constructing a stable subdevice identifier when `plug_key` is not provided.
            plug (dict[str, Any]): Smart-plug payload containing fields such as scan
            name, device name, model/type names, serial/version fields and other
            discovery data.
            plug_key (str | None): Optional stable key to use for the subdevice
            identifier; if omitted a stable key is derived from the plug data and index.

        Returns:
            DeviceInfo: Device registry metadata for the smart-plug including
            identifiers, manufacturer, name, model, serial_number, sw_version, and
            via_device.
        """
        base_name = first_nonblank_text(
            self._system.get(FIELD_DEVICE_NAME),
            self._discovery.get(FIELD_DEVICE_NAME),
            self._properties.get(FIELD_WNAME),
            fallback="SolarVault",
        )
        sn = smart_plug_serial(plug)
        stable_key = plug_key or stable_subdevice_key("smart_plug", sn, plug_index)
        # Branding lookup against the documented accessory catalog so the
        # UI shows "Shelly Plus Plug S" instead of the raw "shellyplusplugs"
        # wire identifier (PROTOCOL §3 + source-of-truth scanName table).
        manufacturer_brand, model_label = subdevice_branding(plug.get(FIELD_SCAN_NAME))
        display_name = first_nonblank_text(
            plug.get(FIELD_DEVICE_NAME),
            model_label,
            plug.get(FIELD_SCAN_NAME),
            fallback=f"Smart Plug {plug_index}",
        )
        model = first_nonblank_text(
            model_label,
            plug.get(FIELD_MODEL),
            plug.get(FIELD_MODEL_NAME),
            plug.get(FIELD_TYPE_NAME),
            fallback="Smart Plug",
        )
        version = first_nonblank_text(
            plug.get(FIELD_VERSION),
            plug.get(FIELD_CURRENT_VERSION),
        )
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

        Prefers an explicit device online indicator from device metadata or system
        state; if that indicator parses to `False` the entity is still considered
        available when the coordinator reports the device as locally reachable. If no
        explicit online indicator is present, availability falls back to whether the
        device ID exists in the coordinator data.

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
                if not parsed_online and self.coordinator.is_device_locally_reachable(
                    self._device_id,
                ):
                    return True
                return parsed_online
        return self._device_id in (self.coordinator.data or {})

    async def async_added_to_hass(self) -> None:
        """Run when entity is added to Home Assistant."""
        await super().async_added_to_hass()

    async def async_will_remove_from_hass(self) -> None:
        """Run when entity is about to be removed from Home Assistant."""
        await super().async_will_remove_from_hass()
