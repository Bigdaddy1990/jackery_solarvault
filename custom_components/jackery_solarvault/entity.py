"""Shared entity base class."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    FIELD_CURRENT_VERSION,
    FIELD_DEV_MODEL,
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_SN,
    FIELD_MODEL_NAME,
    FIELD_ONLINE_STATE,
    FIELD_ONLINE_STATUS,
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
        return self._payload.get(PAYLOAD_PV_TRENDS) or {}

    @property
    def _alarm(self) -> Any:
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
        """Return device-registry metadata for this entity."""
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

    @property
    def available(self) -> bool:
        """Return whether the entity is currently available."""
        if not super().available:
            return False
        online = self._device_meta.get(FIELD_ONLINE_STATUS)
        if online is None:
            online = self._system.get(FIELD_ONLINE_STATE)
        if online is not None:
            return bool(online)
        return self._device_id in (self.coordinator.data or {})
