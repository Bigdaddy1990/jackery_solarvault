"""Shared entity base class."""
from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import JackerySolarVaultCoordinator


class JackeryEntity(CoordinatorEntity[JackerySolarVaultCoordinator]):
    _attr_has_entity_name = True

    def __init__(
        self,
        coordinator: JackerySolarVaultCoordinator,
        device_id: str,
        key_suffix: str,
    ) -> None:
        super().__init__(coordinator)
        self._device_id = device_id
        self._attr_unique_id = f"{device_id}_{key_suffix}"

    @property
    def _payload(self) -> dict[str, Any]:
        return (self.coordinator.data or {}).get(self._device_id, {}) or {}

    @property
    def _properties(self) -> dict[str, Any]:
        return self._payload.get("properties") or {}

    @property
    def _device_meta(self) -> dict[str, Any]:
        return self._payload.get("device") or {}

    @property
    def _discovery(self) -> dict[str, Any]:
        return self._payload.get("discovery") or {}

    @property
    def _system(self) -> dict[str, Any]:
        return self._payload.get("system") or {}

    @property
    def _statistic(self) -> dict[str, Any]:
        return self._payload.get("statistic") or {}

    @property
    def _price(self) -> dict[str, Any]:
        return self._payload.get("price") or {}

    @property
    def _pv_trends(self) -> dict[str, Any]:
        return self._payload.get("pv_trends") or {}

    @property
    def _alarm(self) -> Any:
        return self._payload.get("alarm")

    @property
    def _device_statistic(self) -> dict[str, Any]:
        return self._payload.get("device_statistic") or {}

    @property
    def _ota(self) -> dict[str, Any]:
        return self._payload.get("ota") or {}

    @property
    def _location(self) -> dict[str, Any]:
        return self._payload.get("location") or {}

    @property
    def _home_trends(self) -> dict[str, Any]:
        return self._payload.get("home_trends") or {}

    @property
    def _battery_trends(self) -> dict[str, Any]:
        return self._payload.get("battery_trends") or {}

    @property
    def device_info(self) -> DeviceInfo:
        sys_name = self._system.get("deviceName")   # e.g. "SolarVault 3 Pro Max"
        disc_name = self._discovery.get("deviceName")
        props_wname = self._properties.get("wname")
        name = sys_name or disc_name or props_wname or f"Jackery {self._device_id}"

        model = (
            self._discovery.get("devModel")
            or self._device_meta.get("modelName")
            or "SolarVault"
        )
        sw_version = self._ota.get("currentVersion") or None
        sn = (
            self._device_meta.get("deviceSn")
            or self._discovery.get("deviceSn")
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
        if not super().available:
            return False
        online = self._device_meta.get("onlineStatus")
        if online is None:
            online = self._system.get("onlineState")
        if online is not None:
            return bool(online)
        return self._device_id in (self.coordinator.data or {})
