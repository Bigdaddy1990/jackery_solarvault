"""DataUpdateCoordinator for Jackery SolarVault."""
from __future__ import annotations

import logging
import time
from datetime import date, timedelta
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import JackeryApi, JackeryAuthError, JackeryError
from .const import (
    CONF_DEVICE_ID,
    CONF_SYSTEM_ID,
    DOMAIN,
    PRICE_CONFIG_INTERVAL_SEC,
    SLOW_METRICS_INTERVAL_SEC,
)

_LOGGER = logging.getLogger(__name__)


class JackerySolarVaultCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Polls all known Jackery devices.

    `data` shape:
        {
          "<deviceId>": {
            "device":     {id, deviceSn, deviceName, onlineStatus, ...},
            "properties": {...},
            "system":     {...},        # system metadata (name, gridStandard, ...)
            "statistic":  {...},        # today/total KPIs (optional)
            "price":      {...},        # power price config (optional)
            "alarm":      ...,          # alarm list
          },
          ...
        }
    """

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: JackeryApi,
        update_interval: timedelta,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({entry.title})",
            update_interval=update_interval,
        )
        self.api = api
        self.entry = entry

        # Mapping deviceId -> {systemId, system_meta, device_meta}
        self._device_index: dict[str, dict[str, Any]] = {}

        # Slow-metric caches: per-systemId -> (last_fetch_monotonic, payload)
        # Entries stay valid for SLOW_METRICS_INTERVAL_SEC / PRICE_CONFIG_INTERVAL_SEC
        self._slow_cache: dict[str, dict[str, tuple[float, Any]]] = {}
        # Track the calendar day of the last refresh so we can invalidate
        # day-bounded metrics (statistic, pv_trends) at local midnight.
        self._cached_date: date | None = None

    async def async_discover(self) -> None:
        """Populate _device_index from config or /v1/device/system/list."""
        manual_dev = self.entry.data.get(CONF_DEVICE_ID)
        manual_sys = self.entry.data.get(CONF_SYSTEM_ID)
        if manual_dev:
            self._device_index[str(manual_dev)] = {
                "systemId": str(manual_sys) if manual_sys else None,
                "system_meta": {},
                "device_meta": {},
            }
            _LOGGER.info(
                "Jackery: using manual deviceId=%s systemId=%s",
                manual_dev, manual_sys,
            )
            return

        # Primary: confirmed system/list endpoint (SolarVault + friends)
        try:
            systems = await self.api.async_get_system_list()
        except JackeryError as err:
            raise UpdateFailed(f"system/list failed: {err}") from err

        for sys_entry in systems:
            sys_id = sys_entry.get("id") or sys_entry.get("systemId")
            system_meta = {k: v for k, v in sys_entry.items() if k != "devices"}
            for dev in (sys_entry.get("devices") or []):
                dev_id = dev.get("deviceId") or dev.get("id")
                if not dev_id:
                    continue
                self._device_index[str(dev_id)] = {
                    "systemId": str(sys_id) if sys_id else None,
                    "system_meta": system_meta,
                    "device_meta": dict(dev),
                }

        if self._device_index:
            _LOGGER.info(
                "Jackery: discovered %d device(s) from /v1/device/system/list",
                len(self._device_index),
            )
            return

        # Fallback: legacy bind/list (Explorer portables)
        legacy = await self.api.async_list_devices_legacy()
        for dev in legacy:
            dev_id = (
                dev.get("devId") or dev.get("deviceId")
                or dev.get("id") or dev.get("devSn") or dev.get("deviceSn")
            )
            if dev_id:
                self._device_index[str(dev_id)] = {
                    "systemId": None,
                    "system_meta": {},
                    "device_meta": dict(dev),
                }

        if not self._device_index:
            _LOGGER.error(
                "Jackery: no devices found on either /v1/device/system/list "
                "or /v1/device/bind/list. Set device_id manually in options."
            )

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        if not self._device_index:
            await self.async_discover()
            if not self._device_index:
                raise UpdateFailed("No Jackery devices found.")

        # Per-system calls honour their own refresh intervals. Inside a
        # single update cycle we call each endpoint at most once; across
        # cycles the cache only refreshes when its TTL expired.
        system_cache: dict[str, dict[str, Any]] = {}

        # At the start of each cycle: if the local date rolled over, wipe
        # the day-bounded caches so we don't keep serving yesterday's
        # final values for up to SLOW_METRICS_INTERVAL_SEC.
        today = date.today()
        if self._cached_date is not None and self._cached_date != today:
            _LOGGER.debug(
                "Jackery: day rollover (%s -> %s), clearing day-bounded caches",
                self._cached_date, today,
            )
            for per_system in self._slow_cache.values():
                per_system.pop("statistic", None)
                per_system.pop("pv_trends", None)
                per_system.pop("home_trends", None)
                per_system.pop("battery_trends", None)
        self._cached_date = today

        async def _get_with_ttl_for(
            cache: dict[str, tuple[float, Any]],
            cache_key: str,
            ttl_sec: int,
            fetcher,  # zero-arg async callable
            default: Any,
        ) -> Any:
            """Generic TTL cache helper operating on any dict."""
            now = time.monotonic()
            entry = cache.get(cache_key)
            if entry is not None:
                last_ts, last_value = entry
                if now - last_ts < ttl_sec:
                    return last_value
            try:
                value = await fetcher()
            except JackeryError as err:
                _LOGGER.debug("%s failed: %s", cache_key, err)
                if entry is not None:
                    return entry[1]
                return default
            cache[cache_key] = (now, value)
            return value

        async def _get_with_ttl(
            sys_id: str,
            cache_key: str,
            ttl_sec: int,
            fetcher,  # callable(sys_id) -> awaitable
            default: Any,
        ) -> Any:
            """System-scoped TTL cache wrapper."""
            per_system = self._slow_cache.setdefault(sys_id, {})
            return await _get_with_ttl_for(
                per_system, cache_key, ttl_sec,
                lambda: fetcher(sys_id), default,
            )

        async def _fetch_system(sys_id: str) -> dict[str, Any]:
            if sys_id in system_cache:
                return system_cache[sys_id]
            bundle: dict[str, Any] = {
                "statistic": await _get_with_ttl(
                    sys_id, "statistic", SLOW_METRICS_INTERVAL_SEC,
                    self.api.async_get_system_statistic, {},
                ),
                "alarm": await _get_with_ttl(
                    sys_id, "alarm", SLOW_METRICS_INTERVAL_SEC,
                    self.api.async_get_alarm, None,
                ),
                "pv_trends": await _get_with_ttl(
                    sys_id, "pv_trends", SLOW_METRICS_INTERVAL_SEC,
                    self.api.async_get_pv_trends, {},
                ),
                "home_trends": await _get_with_ttl(
                    sys_id, "home_trends", SLOW_METRICS_INTERVAL_SEC,
                    self.api.async_get_home_trends, {},
                ),
                "battery_trends": await _get_with_ttl(
                    sys_id, "battery_trends", SLOW_METRICS_INTERVAL_SEC,
                    self.api.async_get_battery_trends, {},
                ),
                "price": await _get_with_ttl(
                    sys_id, "price", PRICE_CONFIG_INTERVAL_SEC,
                    self.api.async_get_power_price, {},
                ),
            }
            system_cache[sys_id] = bundle
            return bundle

        async def _fetch_device_extras(
            dev_id: str, dev_sn: str | None
        ) -> dict[str, Any]:
            """Device-level slow metrics (deviceStatistic, OTA, location).

            deviceStatistic: changes on ~5 min boundary, like system stats.
            OTA + location: change practically never → hourly TTL.
            """
            per_dev_key = f"dev:{dev_id}"
            per_dev = self._slow_cache.setdefault(per_dev_key, {})

            out: dict[str, Any] = {}
            out["device_statistic"] = await _get_with_ttl_for(
                per_dev, "device_statistic", SLOW_METRICS_INTERVAL_SEC,
                lambda: self.api.async_get_device_statistic(dev_id), {},
            )
            out["location"] = await _get_with_ttl_for(
                per_dev, "location", PRICE_CONFIG_INTERVAL_SEC,
                lambda: self.api.async_get_location(dev_id), {},
            )
            if dev_sn:
                out["ota"] = await _get_with_ttl_for(
                    per_dev, "ota", PRICE_CONFIG_INTERVAL_SEC,
                    lambda: self.api.async_get_ota_info(dev_sn), {},
                )
            else:
                out["ota"] = {}
            return out

        result: dict[str, dict[str, Any]] = {}
        for dev_id, idx in self._device_index.items():
            try:
                payload = await self.api.async_get_device_property(dev_id)
            except JackeryAuthError as err:
                raise UpdateFailed(
                    "Auth revoked (likely another session logged in). "
                    f"{err}"
                ) from err
            except JackeryError as err:
                _LOGGER.warning("property fetch failed for %s: %s", dev_id, err)
                if self.data and dev_id in self.data:
                    result[dev_id] = self.data[dev_id]
                continue

            # Pull SN from either the fresh property payload or the discovery
            # metadata — needed for the OTA endpoint (which keys on SN).
            dev_sn = (
                (payload.get("device") or {}).get("deviceSn")
                or (idx.get("device_meta") or {}).get("deviceSn")
            )
            extras = await _fetch_device_extras(dev_id, dev_sn)

            entry: dict[str, Any] = {
                "device": payload.get("device") or {},
                "properties": payload.get("properties") or {},
                "system": idx.get("system_meta") or {},
                "discovery": idx.get("device_meta") or {},
                "device_statistic": extras.get("device_statistic") or {},
                "ota": extras.get("ota") or {},
                "location": extras.get("location") or {},
            }
            sys_id = idx.get("systemId")
            if sys_id:
                sys_data = await _fetch_system(sys_id)
                entry.update(sys_data)
            result[dev_id] = entry

        return result
