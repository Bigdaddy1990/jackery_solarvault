"""DataUpdateCoordinator for Jackery SolarVault."""

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
from datetime import date, datetime, timedelta
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import JackeryApi, JackeryAuthError, JackeryError
from .const import (
    ACTION_ID_AUTO_STANDBY,
    ACTION_ID_DEFAULT_PW,
    ACTION_ID_DELETE_STORM_ALERT,
    ACTION_ID_EPS_ENABLED,
    ACTION_ID_FOLLOW_METER_PW,
    ACTION_ID_MAX_FEED_GRID,
    ACTION_ID_MAX_OUT_PW,
    ACTION_ID_OFF_GRID_DOWN,
    ACTION_ID_OFF_GRID_TIME,
    ACTION_ID_QUERY_COMBINE_DATA,
    ACTION_ID_QUERY_WEATHER_PLAN,
    ACTION_ID_REBOOT_DEVICE,
    ACTION_ID_SOC_CHARGE_LIMIT,
    ACTION_ID_SOC_DISCHARGE_LIMIT,
    ACTION_ID_STANDBY,
    ACTION_ID_STORM_MINUTES,
    ACTION_ID_STORM_WARNING,
    ACTION_ID_SUBDEVICE_3014,
    ACTION_ID_SUBDEVICE_3031,
    ACTION_ID_SUBDEVICE_3037,
    ACTION_ID_TEMP_UNIT,
    ACTION_ID_WORK_MODEL,
    APP_CHART_STAT_METRICS,
    APP_CHART_STAT_PERIODS,
    APP_PERIOD_DATE_TYPES,
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_BATTERY_TRENDS,
    APP_SECTION_HOME_STAT,
    APP_SECTION_HOME_TRENDS,
    APP_SECTION_PV_STAT,
    APP_SECTION_PV_TRENDS,
    APP_STAT_PV1_ENERGY,
    APP_STAT_PV2_ENERGY,
    APP_STAT_PV3_ENERGY,
    APP_STAT_PV4_ENERGY,
    APP_STAT_TOTAL_CHARGE,
    APP_STAT_TOTAL_DISCHARGE,
    APP_STAT_TOTAL_HOME_ENERGY,
    APP_STAT_TOTAL_IN_GRID_ENERGY,
    APP_STAT_TOTAL_OUT_GRID_ENERGY,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    BATTERY_PACK_HINT_KEYS,
    BATTERY_PACK_STALE_THRESHOLD_SEC,
    CT_METER_KEYS,
    DATA_QUALITY_KEY_LABEL,
    DATA_QUALITY_KEY_METRIC_KEY,
    DATA_QUALITY_REPAIR_EXAMPLE_LIMIT,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    DOMAIN,
    FIELD_ACCESSORIES,
    FIELD_ACTION_ID,
    FIELD_AUTO_STANDBY,
    FIELD_BAT_NUM,
    FIELD_BAT_SOC,
    FIELD_BATTERIES,
    FIELD_BATTERY_PACK,
    FIELD_BATTERY_PACK_LIST,
    FIELD_BATTERY_PACKS,
    FIELD_BIND_KEY,
    FIELD_BODY,
    FIELD_CELL_TEMP,
    FIELD_CID,
    FIELD_CMD,
    FIELD_COMM_STATE,
    FIELD_COMPANY_NAME,
    FIELD_COUNTRY,
    FIELD_COUNTRY_CODE,
    FIELD_CURRENCY,
    FIELD_CURRENCY_CODE,
    FIELD_CURRENT_VERSION,
    FIELD_DATA,
    FIELD_DEFAULT_PW,
    FIELD_DEV_ID,
    FIELD_DEV_MODEL,
    FIELD_DEV_SN,
    FIELD_DEV_TYPE,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_SN,
    FIELD_DEVICE_TYPE,
    FIELD_DEVICES,
    FIELD_DYNAMIC_OR_SINGLE,
    FIELD_ID,
    FIELD_IN_PW,
    FIELD_IP,
    FIELD_IS_AUTO_STANDBY,
    FIELD_IS_CLOUD,
    FIELD_IS_FOLLOW_METER_PW,
    FIELD_LOGIN_ALLOWED,
    FIELD_MAX_FEED_GRID,
    FIELD_MAX_GRID_STD_PW,
    FIELD_MAX_OUT_PW,
    FIELD_MESSAGE_TYPE,
    FIELD_MINS_INTERVAL,
    FIELD_MODEL_CODE,
    FIELD_NAME,
    FIELD_OFF_GRID_DOWN,
    FIELD_OFF_GRID_TIME,
    FIELD_OP,
    FIELD_OUT_PW,
    FIELD_PACK_LIST,
    FIELD_PLATFORM_COMPANY_ID,
    FIELD_POWER_PRICE_RESOURCE,
    FIELD_PRODUCT_MODEL,
    FIELD_RB,
    FIELD_REBOOT,
    FIELD_SCAN_NAME,
    FIELD_SINGLE_CURRENCY,
    FIELD_SINGLE_CURRENCY_CODE,
    FIELD_SINGLE_PRICE,
    FIELD_SN,
    FIELD_SOC_CHARGE_LIMIT,
    FIELD_SOC_CHG_LIMIT,
    FIELD_SOC_DISCHARGE_LIMIT,
    FIELD_SOC_DISCHG_LIMIT,
    FIELD_SUB_TYPE,
    FIELD_SW_EPS,
    FIELD_SYSTEM_ID,
    FIELD_SYSTEM_REGION,
    FIELD_TARGET_VERSION,
    FIELD_TEMP_UNIT,
    FIELD_TIMESTAMP,
    FIELD_TYPE_NAME,
    FIELD_UPDATE_CONTENT,
    FIELD_UPDATE_STATUS,
    FIELD_UPDATES,
    FIELD_VERSION,
    FIELD_WNAME,
    FIELD_WORK_MODEL,
    FIELD_WPC,
    FIELD_WPS,
    MAIN_PROPERTY_ALIAS_PAIRS,
    MQTT_ACTION_IDS_COMBINE,
    MQTT_ACTION_IDS_SCHEDULE,
    MQTT_ACTION_IDS_SUBDEVICE,
    MQTT_CMD_CONTROL_COMBINE,
    MQTT_CMD_DEVICE_PROPERTY_CHANGE,
    MQTT_CMD_NONE,
    MQTT_CMD_QUERY_COMBINE_DATA,
    MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
    MQTT_CMD_QUERY_WEATHER_PLAN,
    MQTT_CREDENTIAL_CLIENT_ID,
    MQTT_CREDENTIAL_PASSWORD,
    MQTT_CREDENTIAL_USER_ID,
    MQTT_CREDENTIAL_USERNAME,
    MQTT_MESSAGE_CANCEL_WEATHER_ALERT,
    MQTT_MESSAGE_CONTROL_COMBINE,
    MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
    MQTT_MESSAGE_DOWNLOAD_DEVICE_SCHEDULE,
    MQTT_MESSAGE_QUERY_COMBINE_DATA,
    MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
    MQTT_MESSAGE_QUERY_WEATHER_PLAN,
    MQTT_MESSAGE_SEND_WEATHER_ALERT,
    MQTT_MESSAGE_UPLOAD_COMBINE_DATA,
    MQTT_MESSAGE_UPLOAD_INCREMENTAL_COMBINE_DATA,
    MQTT_MESSAGE_UPLOAD_WEATHER_PLAN,
    MQTT_RECONNECT_THROTTLE_SEC,
    MQTT_TOPIC_COMMAND,
    MQTT_TOPIC_PREFIX,
    NON_BATTERY_SUBDEVICE_TYPES,
    PACK_FIELD_LAST_SEEN_AT,
    PAYLOAD_ALARM,
    PAYLOAD_BATTERY_PACKS,
    PAYLOAD_BATTERY_TRENDS,
    PAYLOAD_CT_METER,
    PAYLOAD_DATA_QUALITY,
    PAYLOAD_DEBUG_LOG_FILENAME,
    PAYLOAD_DEBUG_LOGGER_NAME,
    PAYLOAD_DEBUG_THROTTLE_SEC,
    PAYLOAD_DEVICE,
    PAYLOAD_DEVICE_META,
    PAYLOAD_DEVICE_STATISTIC,
    PAYLOAD_DISCOVERY,
    PAYLOAD_HOME_TRENDS,
    PAYLOAD_HTTP_PROPERTIES,
    PAYLOAD_LOCATION,
    PAYLOAD_MQTT_LAST,
    PAYLOAD_NOTICE,
    PAYLOAD_OTA,
    PAYLOAD_PRICE,
    PAYLOAD_PRICE_HISTORY_CONFIG,
    PAYLOAD_PRICE_SOURCES,
    PAYLOAD_PROPERTIES,
    PAYLOAD_PV_TRENDS,
    PAYLOAD_STATISTIC,
    PAYLOAD_SYSTEM,
    PAYLOAD_SYSTEM_META,
    PAYLOAD_TASK_PLAN,
    PAYLOAD_WEATHER_PLAN,
    PRESERVED_FAST_PAYLOAD_KEYS,
    PRICE_CONFIG_INTERVAL_SEC,
    REPAIR_ISSUE_APP_DATA_INCONSISTENCY,
    REPAIR_TRANSLATION_APP_DATA_INCONSISTENCY,
    SLOW_METRICS_INTERVAL_SEC,
    SMART_METER_SUBTYPE,
    SUBDEVICE_HINT_KEYS,
    SUBDEVICE_MAIN_MIRROR_KEYS,
    SUBDEVICE_ONLY_PROPERTY_KEYS,
    SUBDEVICE_TYPE_SMART_METER,
    SYSTEM_INFO_KEYS,
)

if TYPE_CHECKING:
    from .mqtt_push import JackeryMqttPushClient

from .util import (
    app_data_quality_warnings,
    app_month_request_kwargs,
    app_period_request_kwargs,
    append_payload_debug_line,
    apply_year_month_backfill,
    chart_series_debug,
    external_trend_statistic_id,
    format_data_quality_warning,
    guard_statistic_totals_from_year,
    normalized_data_quality_warnings,
    safe_float,
    trend_series_points,
    year_payload_appears_current_month_only,
)

_LOGGER = logging.getLogger(__name__)
_PAYLOAD_DEBUG_LOGGER = logging.getLogger(PAYLOAD_DEBUG_LOGGER_NAME)


def _stable_payload_debug_signature(event: dict[str, Any]) -> str:
    """Return a content-only signature for payload-debug dedup.

    Per-message identifiers (``id``, ``timestamp``, ``messageId``) and
    the optional ``entry_id`` annotation change for every record but
    do not represent new information about the device. They are
    excluded from the signature so a stream of identical telemetry
    payloads collapses into one log line per actually-changed value.
    """
    payload = event.get("payload") or {}
    body = payload.get("body") if isinstance(payload, dict) else None
    if isinstance(body, dict):
        body_sig: Any = {k: v for k, v in body.items() if k != "messageId"}
    else:
        body_sig = body
    response = (
        event.get("response") if isinstance(event.get("response"), dict) else None
    )
    if response is not None:
        response_data = (
            response.get("data")
            if isinstance(response.get("data"), dict)
            else response.get("data")
        )
    else:
        response_data = None
    return json.dumps(
        [
            event.get("kind"),
            event.get("topic") or event.get("path"),
            payload.get("messageType") if isinstance(payload, dict) else None,
            body_sig,
            event.get("body_type"),
            event.get("data_type"),
            event.get("response_data_type"),
            event.get("status"),
            response_data,
        ],
        sort_keys=True,
        default=str,
    )


class JackerySolarVaultCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):
    """Polls all known Jackery devices.

    `data` shape:
        {
          "<deviceId>": {
            PAYLOAD_DEVICE:     {id, deviceSn, deviceName, onlineStatus, ...},
            PAYLOAD_PROPERTIES: {...},
            PAYLOAD_SYSTEM:     {...},        # system metadata (name, gridStandard, ...)
            PAYLOAD_STATISTIC:  {...},        # today/total KPIs (optional)
            PAYLOAD_PRICE:      {...},        # power price config (optional)
            PAYLOAD_ALARM:      ...,          # alarm list
          },
          ...
        }
    """

    _PRICE_OVERRIDE_TTL_SEC = 600

    _CT_METER_KEYS = CT_METER_KEYS
    _SUBDEVICE_HINT_KEYS = SUBDEVICE_HINT_KEYS
    _SUBDEVICE_ONLY_PROPERTY_KEYS = SUBDEVICE_ONLY_PROPERTY_KEYS
    _SUBDEVICE_MAIN_MIRROR_KEYS = SUBDEVICE_MAIN_MIRROR_KEYS
    _SYSTEM_INFO_KEYS = SYSTEM_INFO_KEYS
    _BATTERY_PACK_HINT_KEYS = BATTERY_PACK_HINT_KEYS
    _MAIN_PROPERTY_ALIAS_PAIRS = MAIN_PROPERTY_ALIAS_PAIRS
    _BATTERY_PACK_LIVE_KEYS = frozenset({FIELD_BAT_SOC, FIELD_CELL_TEMP})
    _DEVICE_YEAR_BACKFILL_STAT_KEYS = {
        APP_SECTION_PV_STAT: (
            APP_STAT_TOTAL_SOLAR_ENERGY,
            APP_STAT_PV1_ENERGY,
            APP_STAT_PV2_ENERGY,
            APP_STAT_PV3_ENERGY,
            APP_STAT_PV4_ENERGY,
        ),
        APP_SECTION_BATTERY_STAT: (
            APP_STAT_TOTAL_CHARGE,
            APP_STAT_TOTAL_DISCHARGE,
        ),
        APP_SECTION_HOME_STAT: (
            APP_STAT_TOTAL_IN_GRID_ENERGY,
            APP_STAT_TOTAL_OUT_GRID_ENERGY,
        ),
    }
    _SYSTEM_YEAR_BACKFILL_STAT_KEYS = {
        APP_SECTION_HOME_TRENDS: (APP_STAT_TOTAL_HOME_ENERGY,),
    }

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        api: JackeryApi,
        update_interval: timedelta,
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN} ({entry.title})",
            update_interval=None,
        )
        self.api = api
        self.api.payload_debug_callback = self._async_payload_debug_event
        self.entry = entry
        self._configured_update_interval = update_interval
        interval_sec = max(15, int(update_interval.total_seconds()))
        # Fast property polling should follow the configured interval, but
        # server-side slow endpoints (stats/trends/price) should keep their
        # own cadence to avoid long update cycles.
        self._slow_metrics_interval_sec = max(SLOW_METRICS_INTERVAL_SEC, interval_sec)
        self._price_config_interval_sec = max(PRICE_CONFIG_INTERVAL_SEC, interval_sec)

        # Mapping deviceId -> {systemId, system_meta, device_meta}
        self._device_index: dict[str, dict[str, Any]] = {}

        # Slow-metric caches: per-systemId -> (last_fetch_monotonic, payload)
        # Entries stay valid for the configured polling interval.
        self._slow_cache: dict[str, dict[str, tuple[float, Any]]] = {}
        # Track the calendar day of the last refresh so we can invalidate
        # day-bounded metrics (statistic, pv_trends) at local midnight.
        self._cached_date: date | None = None
        self._mqtt: JackeryMqttPushClient | None = None
        self._mqtt_fingerprint: tuple[str | None, str | None, str | None] | None = None
        self._mqtt_generated_mac_warning_logged = False
        self._last_mqtt_connect_attempt: float = 0.0
        self._last_weather_plan_query: dict[str, float] = {}
        self._weather_plan_query_interval_sec = 180
        self._last_system_info_query: dict[str, float] = {}
        self._system_info_query_interval_sec = 180
        self._last_subdevice_query: dict[str, float] = {}
        # Battery packs and CT meters are app-side MQTT subdevices. Their live
        # values must follow the user's polling interval, not the slow
        # statistic cadence.
        self._subdevice_query_interval_sec = interval_sec
        self._price_overrides: dict[str, tuple[float, dict[str, Any]]] = {}
        self._periodic_refresh_unsub: Any | None = None
        self._periodic_refresh_task: asyncio.Task[None] | None = None
        self._mqtt_backfill_task: asyncio.Task[None] | None = None
        self._skipped_refresh_ticks = 0
        # Dedup cache for payload-debug records:
        # (kind, channel, message_type) -> (last_signature, last_emit_monotonic).
        # Bounded by the number of distinct topics the device publishes
        # (typically <10), so memory stays trivial.
        self._payload_debug_last_sig: dict[tuple[str, str, str], str] = {}
        self._payload_debug_last_emit_ts: dict[tuple[str, str, str], float] = {}
        # Counter for diagnostics: how many battery packs the cleanup has
        # removed for being silent past BATTERY_PACK_STALE_THRESHOLD_SEC.
        # Resets on integration reload.
        self._stale_battery_packs_dropped = 0
        # Identifiers for devices to remove from HA's device_registry on
        # the next async cleanup hook. Populated synchronously by
        # _merge_subdevice_data, drained asynchronously by
        # _async_cleanup_pending_device_removals.
        self._pending_device_removals: list[tuple[str, str]] = []
        # Statistic-import cache: avoid re-publishing identical chart buckets
        # to HA recorder when the cloud snapshot did not change. Keyed by
        # statistic_id, value is the JSON signature of the last published
        # (starts, states) tuple.
        self._stat_import_last_sig: dict[str, str] = {}

    async def _async_payload_debug_event(
        self, event_or_factory: dict[str, Any] | Callable[[], dict[str, Any]]
    ) -> None:
        """Append one redacted raw/parsed HTTP/MQTT diagnostic event when enabled.

        Raw payload diagnostics are deliberately gated behind a dedicated DEBUG
        logger. They are useful for parser/source bugs, but writing every
        HTTP/MQTT payload on normal installations is unnecessary disk churn.

        Two safety nets prevent log spam even when DEBUG is enabled:

        1. **Dedup**: a tiny per-coordinator signature cache. If the redacted
           event payload is identical to the most recent record for the same
           ``(kind, topic-or-path)`` channel, the line is dropped. Real
           Jackery devices repeat the same MQTT bodies for ~80% of polls
           (verified against captured ``payload_debug.jsonl`` traces).
        2. **Empty-chart-series suppression**: ``chart_series_debug`` returns
           an empty dict for non-trend payloads (smart-meter telemetry,
           CT phase frames, OTA/control etc). Empty debug fields are
           dropped rather than written as ``"body_chart_series_debug": {}``.

        ``event_or_factory`` may be either a pre-built event dict or a
        zero-arg callable that returns one. The callable form lets call
        sites avoid building the event when DEBUG is disabled — the most
        important hot-path optimization on the per-MQTT-message path.
        """
        # Hard gate on an explicitly configured payload-debug logger. Do not
        # use ``isEnabledFor(DEBUG)``: this child logger would inherit DEBUG
        # from the parent integration logger and unexpectedly create JSONL
        # files when users only enabled normal HA debug logging. Requiring a
        # concrete DEBUG level on this exact logger keeps diagnostics available
        # through HA logging controls without keeping a hidden options toggle.
        if _PAYLOAD_DEBUG_LOGGER.level != logging.DEBUG:
            return
        event = (
            event_or_factory() if callable(event_or_factory) else dict(event_or_factory)
        )
        # Drop empty chart-series-debug fields — they're noise on the
        # smart-meter / control / OTA paths where there are no chart series.
        for empty_key in (
            "body_chart_series_debug",
            "data_chart_series_debug",
            "chart_series_debug",
        ):
            if empty_key in event and not event[empty_key]:
                event.pop(empty_key)
        # Two-stage filter on the same logical channel:
        # 1. Dedup: drop records whose redacted body matches the previous
        #    record from the same (kind, channel, messageType). Drops ~93%
        #    of HTTP responses against real devices.
        # 2. Throttle: when the body keeps changing slowly (e.g. CT phase
        #    power flickering by 1 W every second), still emit at most one
        #    record per ``PAYLOAD_DEBUG_THROTTLE_SEC`` per channel. This
        #    keeps the file useful for debugging without filling the disk
        #    when a user forgot to switch the dedicated DEBUG logger off.
        signature = _stable_payload_debug_signature(event)
        kind = str(event.get("kind") or "?")
        channel = str(event.get("topic") or event.get("path") or "")
        message_type = ""
        payload_obj = event.get("payload")
        if isinstance(payload_obj, dict):
            message_type = str(payload_obj.get("messageType") or "")
        cache_key = (kind, channel, message_type)
        last_sig = self._payload_debug_last_sig.get(cache_key)
        last_ts = self._payload_debug_last_emit_ts.get(cache_key, 0.0)
        now_mono = time.monotonic()
        if last_sig == signature:
            return
        if last_sig is not None and (now_mono - last_ts) < PAYLOAD_DEBUG_THROTTLE_SEC:
            # Body changed but a record from this channel was emitted very
            # recently. Skip this one; the next genuinely-new record after
            # the throttle window will carry the difference.
            return
        self._payload_debug_last_sig[cache_key] = signature
        self._payload_debug_last_emit_ts[cache_key] = now_mono
        event.setdefault("timestamp", dt_util.now().isoformat())
        event.setdefault("entry_id", self.entry.entry_id)
        path = self.hass.config.path(PAYLOAD_DEBUG_LOG_FILENAME)
        await self.hass.async_add_executor_job(
            append_payload_debug_line,
            path,
            event,
        )

    async def async_discover(self) -> None:
        """Populate _device_index from config or /v1/device/system/list."""
        self._device_index.clear()

        # Primary: confirmed system/list endpoint (SolarVault + friends)
        try:
            systems = await self.api.async_get_system_list()
        except JackeryAuthError as err:
            raise ConfigEntryAuthFailed(
                "Jackery credentials were rejected during system discovery. "
                "Re-authentication is required."
            ) from err
        except JackeryError as err:
            raise UpdateFailed(f"system/list failed: {err}") from err

        for sys_entry in systems:
            sys_id = sys_entry.get(FIELD_ID) or sys_entry.get(FIELD_SYSTEM_ID)
            devices = sys_entry.get(FIELD_DEVICES) or []
            accessories = [
                dict(dev)
                for dev in devices
                if isinstance(dev, dict) and not self._is_property_device_candidate(dev)
            ]
            system_meta = {k: v for k, v in sys_entry.items() if k != FIELD_DEVICES}
            if accessories:
                system_meta[FIELD_ACCESSORIES] = accessories
            for dev in devices:
                if not isinstance(dev, dict):
                    continue
                if not self._is_property_device_candidate(dev):
                    continue
                dev_id = dev.get(FIELD_DEVICE_ID) or dev.get(FIELD_ID)
                if not dev_id:
                    continue
                self._device_index[str(dev_id)] = {
                    FIELD_SYSTEM_ID: str(sys_id) if sys_id else None,
                    PAYLOAD_SYSTEM_META: system_meta,
                    PAYLOAD_DEVICE_META: dict(dev),
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
                dev.get(FIELD_DEV_ID)
                or dev.get(FIELD_DEVICE_ID)
                or dev.get(FIELD_ID)
                or dev.get(FIELD_DEV_SN)
                or dev.get(FIELD_DEVICE_SN)
            )
            if dev_id:
                self._device_index[str(dev_id)] = {
                    FIELD_SYSTEM_ID: None,
                    PAYLOAD_SYSTEM_META: {},
                    PAYLOAD_DEVICE_META: dict(dev),
                }

        if not self._device_index:
            _LOGGER.error(
                "Jackery: no devices found on either /v1/device/system/list "
                "or /v1/device/bind/list."
            )

    @staticmethod
    def _is_property_device_candidate(dev: dict[str, Any]) -> bool:
        """Filter out accessory entries that do not support /device/property."""
        # Observed for third-party accessories (e.g., Shelly): bindKey=0 and
        # no Jackery model metadata. Those IDs return API code=20000.
        bind_key = dev.get(FIELD_BIND_KEY)
        if bind_key in (0, "0", False):
            return False
        if dev.get(FIELD_DEV_TYPE) == 3 and bool(dev.get(FIELD_IS_CLOUD)):
            return False
        return not (dev.get(FIELD_MODEL_CODE) is None and not dev.get(FIELD_DEV_MODEL))

    @staticmethod
    def _is_mqtt_auth_failure(message: object) -> bool:
        """Return True for broker-side MQTT credential rejection."""
        text = str(message or "").lower()
        return (
            "connect rc=4" in text
            or "connect rc=5" in text
            or "bad user name or password" in text
            or "not authorized" in text
        )

    async def async_start_mqtt(self) -> None:
        """Start (or reconfigure) MQTT push channel."""
        if self._mqtt is None:
            try:
                from .mqtt_push import JackeryMqttPushClient
            except ModuleNotFoundError as err:
                if err.name != "gmqtt":
                    raise
                _LOGGER.warning(
                    "Jackery MQTT push is unavailable because gmqtt is not installed"
                )
                return

            self._mqtt = JackeryMqttPushClient(
                self.hass,
                self._async_handle_mqtt_message,
                self._async_mqtt_connected,
            )
        try:
            await self._async_ensure_mqtt(force=True, wait_connected=True)
        except RuntimeError as err:
            _LOGGER.warning("Jackery MQTT initial connect did not complete: %s", err)
            return

    async def _async_mqtt_connected(self) -> None:
        """Request a full app-style MQTT snapshot after every broker connect."""
        await self._async_query_system_info_for_missing(force=True, ensure_mqtt=False)
        await self._async_query_weather_plan_for_missing(force=True, ensure_mqtt=False)
        await self._async_query_subdevices_for_missing(force=True, ensure_mqtt=False)

    @property
    def configured_update_interval(self) -> timedelta:
        """Return the integration's fixed polling interval."""
        return self._configured_update_interval

    def async_start_periodic_refresh(self) -> None:
        """Start fixed-rate polling independent of coordinator listener state."""
        if self._periodic_refresh_unsub is not None:
            return
        self._periodic_refresh_unsub = async_track_time_interval(
            self.hass,
            self._handle_periodic_refresh_tick,
            self._configured_update_interval,
        )
        _LOGGER.info(
            "Jackery: fixed-rate polling started every %ss",
            int(self._configured_update_interval.total_seconds()),
        )

    @callback
    def _handle_periodic_refresh_tick(self, _now: Any) -> None:
        """Run one refresh tick, skipping instead of piling up overlapping polls."""
        if (
            self._periodic_refresh_task is not None
            and not self._periodic_refresh_task.done()
        ):
            self._skipped_refresh_ticks += 1
            _LOGGER.debug(
                "Jackery: skipping polling tick because previous refresh is still running"
            )
            return
        self._periodic_refresh_task = self.hass.async_create_task(
            self._async_periodic_refresh(),
            name=f"{DOMAIN}_fixed_rate_refresh",
        )

    async def _async_periodic_refresh(self) -> None:
        """Perform one scheduled refresh and keep errors inside the coordinator."""
        started = time.monotonic()
        try:
            await self.async_request_refresh()
        except Exception as err:
            _LOGGER.debug("Jackery scheduled refresh failed: %s", err)
            return
        elapsed = time.monotonic() - started
        interval_sec = self._configured_update_interval.total_seconds()
        if elapsed > interval_sec:
            _LOGGER.debug(
                "Jackery polling cycle overran interval: %.2fs > %.2fs",
                elapsed,
                interval_sec,
            )

    async def async_shutdown(self) -> None:
        """Stop MQTT client on integration unload."""
        if self._periodic_refresh_unsub is not None:
            self._periodic_refresh_unsub()
            self._periodic_refresh_unsub = None
        for task in (self._periodic_refresh_task, self._mqtt_backfill_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._periodic_refresh_task = None
        self._mqtt_backfill_task = None
        if self._mqtt is not None:
            await self._mqtt.async_stop()
            self._mqtt = None

    async def _async_ensure_mqtt(
        self, *, force: bool = False, wait_connected: bool = False
    ) -> None:
        """Ensure MQTT is connected with credentials from current login session."""
        if self._mqtt is None:
            return

        # Fast path: current client is already configured for the current
        # session fingerprint, and no forced reconnect is requested.
        current_fp = self.api.mqtt_fingerprint
        if (
            not force
            and self._mqtt.is_started
            and self._mqtt_fingerprint is not None
            and self._mqtt_fingerprint == current_fp
            and self._mqtt.is_connected
        ):
            return

        # Avoid reconnect churn when another app session keeps rotating the
        # token/seed frequently.
        now = time.monotonic()
        if (
            not force
            and self._mqtt.is_started
            and (
                (
                    self._mqtt_fingerprint is not None
                    and self._mqtt_fingerprint != current_fp
                )
                or not self._mqtt.is_connected
            )
            and (now - self._last_mqtt_connect_attempt) < MQTT_RECONNECT_THROTTLE_SEC
        ):
            _LOGGER.debug(
                "Jackery MQTT: reconnect is throttled (%.1fs/%ss)",
                now - self._last_mqtt_connect_attempt,
                MQTT_RECONNECT_THROTTLE_SEC,
            )
            return

        try:
            creds = await self.api.async_get_mqtt_credentials()
        except JackeryError as err:
            _LOGGER.debug("Jackery MQTT credential build failed: %s", err)
            return

        if not self._mqtt_generated_mac_warning_logged and str(
            self.api.mqtt_mac_id_source
        ).startswith("generated"):
            _LOGGER.debug(
                "Jackery MQTT uses internally generated macId (%s)",
                self.api.mqtt_mac_id_source,
            )
            self._mqtt_generated_mac_warning_logged = True

        fingerprint = self.api.mqtt_fingerprint
        if self._mqtt_fingerprint is not None and fingerprint != self._mqtt_fingerprint:
            _LOGGER.info("Jackery MQTT: credential session changed, reconnecting")

        await self._mqtt.async_start(
            client_id=creds[MQTT_CREDENTIAL_CLIENT_ID],
            username=creds[MQTT_CREDENTIAL_USERNAME],
            password=creds[MQTT_CREDENTIAL_PASSWORD],
            user_id=creds[MQTT_CREDENTIAL_USER_ID],
        )
        if wait_connected:
            try:
                await self._mqtt.async_wait_until_connected(timeout_sec=15.0)
            except RuntimeError as err:
                mqtt_last_error = self._mqtt.diagnostics.get("last_error")
                if self._is_mqtt_auth_failure(err) or self._is_mqtt_auth_failure(
                    mqtt_last_error
                ):
                    raise
                _LOGGER.warning(
                    "Jackery MQTT TLS/connect check failed "
                    "(chain+hostname verified; strict AKID check suppressed if supported): %s",
                    err,
                )
                raise
        self._mqtt_fingerprint = fingerprint
        self._last_mqtt_connect_attempt = time.monotonic()

    async def _async_handle_mqtt_message(
        self,
        topic: str,
        payload: dict[str, Any],
    ) -> None:
        """Merge inbound MQTT payloads into coordinator data and push update."""
        # Lazy-built event: when the dedicated payload_debug logger is not
        # explicitly at DEBUG level, the factory is never called — saving the
        # ``chart_series_debug`` walk on the per-MQTT-message hot path.
        await self._async_payload_debug_event(
            lambda: {
                "kind": "mqtt",
                "topic": topic,
                "payload": payload,
                "body_type": type(payload.get(FIELD_BODY)).__name__,
                "data_type": type(payload.get(FIELD_DATA)).__name__,
                "body_chart_series_debug": chart_series_debug(payload.get(FIELD_BODY)),
                "data_chart_series_debug": chart_series_debug(payload.get(FIELD_DATA)),
            }
        )
        if not self.data:
            return

        device_id = self._resolve_device_id_from_mqtt(payload)
        if not device_id:
            return
        if device_id not in self.data:
            return

        current = self.data[device_id]
        updated = dict(current)
        touched = False
        body = payload.get(FIELD_BODY)
        if not isinstance(body, dict):
            alt_body = payload.get(FIELD_DATA)
            body = alt_body if isinstance(alt_body, dict) else {}
        msg_type = payload.get(FIELD_MESSAGE_TYPE)
        action_id = payload.get(FIELD_ACTION_ID)
        is_subdevice = self._is_subdevice_payload(payload, body)

        if topic.endswith("/device") or topic.endswith("/config"):
            if body:
                if is_subdevice:
                    touched = self._merge_subdevice_data(updated, body) or touched
                else:
                    props = self._merge_main_properties(
                        current.get(PAYLOAD_PROPERTIES) or {},
                        body,
                    )
                    updated[PAYLOAD_PROPERTIES] = props
                    touched = True

            # Keep known metadata in sync when the envelope includes it.
            if payload.get(FIELD_DEVICE_SN) and not is_subdevice:
                meta = dict(current.get(PAYLOAD_DEVICE) or {})
                if meta.get(FIELD_DEVICE_SN) != payload.get(FIELD_DEVICE_SN):
                    meta[FIELD_DEVICE_SN] = payload.get(FIELD_DEVICE_SN)
                    updated[PAYLOAD_DEVICE] = meta
                    touched = True

        elif topic.endswith("/alert"):
            updated[PAYLOAD_ALARM] = body if body else payload
            touched = True

        elif topic.endswith("/notice"):
            # Not entity-backed today; keep as diagnostic context.
            updated[PAYLOAD_NOTICE] = payload
            touched = True

        # Weather-plan and weather-alert related messages.
        # Explicit MessageType whitelist + dedicated cmd/actionId beats
        # substring matches like `"storm" in body`, which can false-positive
        # on unrelated payloads that happen to contain a "storm" key (e.g. a
        # future firmware adding storm-related telemetry to other messages).
        weather_action_ids = (
            ACTION_ID_QUERY_WEATHER_PLAN,
            ACTION_ID_STORM_MINUTES,
            ACTION_ID_DELETE_STORM_ALERT,
            ACTION_ID_STORM_WARNING,
        )
        if (
            msg_type
            in (
                MQTT_MESSAGE_UPLOAD_WEATHER_PLAN,
                MQTT_MESSAGE_QUERY_WEATHER_PLAN,
                MQTT_MESSAGE_SEND_WEATHER_ALERT,
                MQTT_MESSAGE_CANCEL_WEATHER_ALERT,
            )
            or body.get(FIELD_CMD) == MQTT_CMD_QUERY_WEATHER_PLAN
            or action_id in weather_action_ids
        ):
            updated[PAYLOAD_WEATHER_PLAN] = body if body else payload
            touched = True

        # User-configurable schedule payloads (custom mode / tariff mode /
        # smart-plug priority) are transported via DownloadDeviceSchedule.
        if (
            msg_type == MQTT_MESSAGE_DOWNLOAD_DEVICE_SCHEDULE
            or action_id in MQTT_ACTION_IDS_SCHEDULE
        ):
            updated[PAYLOAD_TASK_PLAN] = body if body else payload
            touched = True

        # System/config snapshots (work mode, temp unit, standby/off-grid,
        # max system power, storm lead time) are transported via
        # QueryCombineData/UploadCombineData, not the HTTP property endpoint.
        if (
            not is_subdevice
            and (
                msg_type
                in (
                    MQTT_MESSAGE_QUERY_COMBINE_DATA,
                    MQTT_MESSAGE_UPLOAD_COMBINE_DATA,
                    MQTT_MESSAGE_UPLOAD_INCREMENTAL_COMBINE_DATA,
                    MQTT_MESSAGE_CONTROL_COMBINE,
                )
                or action_id in MQTT_ACTION_IDS_COMBINE
                or body.get(FIELD_CMD)
                in (MQTT_CMD_QUERY_COMBINE_DATA, MQTT_CMD_CONTROL_COMBINE)
            )
            and body
        ):
            props = self._merge_main_properties(
                current.get(PAYLOAD_PROPERTIES) or {},
                body,
            )
            updated[PAYLOAD_PROPERTIES] = props
            touched = True

        # Sub-device status: battery packs and CT/smart meter values are
        # transported as QuerySubDeviceGroupProperty responses.
        if (
            is_subdevice
            or msg_type == MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY
            or action_id in MQTT_ACTION_IDS_SUBDEVICE
        ):
            source = body if body else payload
            if isinstance(source, dict):
                touched = self._merge_subdevice_data(updated, source) or touched

        if not touched:
            return

        updated[PAYLOAD_MQTT_LAST] = {
            "topic": topic,
            FIELD_MESSAGE_TYPE: payload.get(FIELD_MESSAGE_TYPE),
            FIELD_ACTION_ID: payload.get(FIELD_ACTION_ID),
            FIELD_TIMESTAMP: payload.get(FIELD_TIMESTAMP),
            FIELD_DEVICE_SN: payload.get(FIELD_DEVICE_SN),
        }

        new_data = dict(self.data)
        new_data[device_id] = updated
        self._push_partial_update(new_data)

    def _resolve_device_id_from_mqtt(self, payload: dict[str, Any]) -> str | None:
        body = payload.get(FIELD_BODY)
        if not isinstance(body, dict):
            alt_body = payload.get(FIELD_DATA)
            body = alt_body if isinstance(alt_body, dict) else {}

        for key in (FIELD_DEVICE_ID, FIELD_DEV_ID):
            value = payload.get(key)
            if value is None:
                value = body.get(key)
            if value is not None and str(value) in self._device_index:
                return str(value)

        device_sn = payload.get(FIELD_DEVICE_SN) or body.get(FIELD_DEVICE_SN)
        if device_sn:
            for dev_id, idx in self._device_index.items():
                candidates = [
                    (idx.get(PAYLOAD_DEVICE_META) or {}).get(FIELD_DEVICE_SN),
                    (idx.get(PAYLOAD_DEVICE_META) or {}).get(FIELD_DEV_SN),
                    ((self.data or {}).get(dev_id, {}).get(PAYLOAD_DEVICE) or {}).get(
                        FIELD_DEVICE_SN
                    ),
                    (
                        (self.data or {}).get(dev_id, {}).get(PAYLOAD_DISCOVERY) or {}
                    ).get(FIELD_DEVICE_SN),
                ]
                if device_sn in candidates:
                    return dev_id

        if len(self._device_index) == 1:
            return next(iter(self._device_index))
        return None

    def _resolve_device_sn(self, device_id: str) -> str | None:
        idx = self._device_index.get(device_id) or {}
        from_idx = (idx.get(PAYLOAD_DEVICE_META) or {}).get(FIELD_DEVICE_SN)
        if from_idx:
            return str(from_idx)
        data = (self.data or {}).get(device_id, {})
        for section in (PAYLOAD_DEVICE, PAYLOAD_DISCOVERY):
            sn = (data.get(section) or {}).get(FIELD_DEVICE_SN)
            if sn:
                return str(sn)
        return None

    def _resolve_system_id(self, device_id: str) -> str | None:
        idx = self._device_index.get(device_id) or {}
        sys_id = idx.get(FIELD_SYSTEM_ID)
        if sys_id:
            return str(sys_id)
        payload = (self.data or {}).get(device_id, {})
        sys_meta = payload.get(PAYLOAD_SYSTEM) or {}
        sys_id = sys_meta.get(FIELD_ID) or sys_meta.get(FIELD_SYSTEM_ID)
        if sys_id is not None:
            return str(sys_id)
        return None

    def device_supports_advanced(self, device_id: str) -> bool:
        """Return True if the device exposes advanced controls.

        SolarVault 3 Pro Max and similar hardware that reports `maxOutPw` in
        properties — or that has modelCode 3002 in metadata/discovery —
        supports the full set of advanced settings (work-mode, temp unit,
        auto-standby, off-grid timer, follow-meter, storm warning, etc.).
        Older or stripped-down models lack some of these.

        Centralized so that every platform asks the same question the same
        way; previously this 1-liner was duplicated across button/select/
        sensor/switch.
        """
        payload = (self.data or {}).get(device_id, {})
        props = payload.get(PAYLOAD_PROPERTIES) or {}
        meta = payload.get(PAYLOAD_DEVICE) or {}
        disc = payload.get(PAYLOAD_DISCOVERY) or {}
        model_code = meta.get(FIELD_MODEL_CODE) or disc.get(FIELD_MODEL_CODE)
        return FIELD_MAX_OUT_PW in props or str(model_code) == "3002"

    @staticmethod
    def _merge_dict_values(
        base: dict[str, Any],
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """Recursively merge nested dictionaries while preserving old keys."""
        merged: dict[str, Any] = dict(base)
        for key, value in updates.items():
            cur = merged.get(key)
            if isinstance(cur, dict) and isinstance(value, dict):
                merged[key] = JackerySolarVaultCoordinator._merge_dict_values(
                    cur, value
                )
            else:
                merged[key] = value
        return merged

    @classmethod
    def _sync_property_aliases(cls, props: dict[str, Any]) -> dict[str, Any]:
        """Mirror equivalent app property names after merge operations."""
        normalized = dict(props)
        for left, right in cls._MAIN_PROPERTY_ALIAS_PAIRS:
            if normalized.get(left) is not None and normalized.get(right) is None:
                normalized[right] = normalized[left]
            if normalized.get(right) is not None and normalized.get(left) is None:
                normalized[left] = normalized[right]
        return normalized

    @classmethod
    def _merge_main_properties(
        cls,
        base: dict[str, Any],
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """Sanitize, merge, and normalize main-device property payloads."""
        merged = cls._merge_dict_values(
            cls._sanitize_main_properties(base),
            cls._sanitize_main_properties(updates),
        )
        return cls._sync_property_aliases(merged)

    @staticmethod
    def _find_dict_with_any_key(
        obj: Any,
        keys: set[str] | frozenset[str],
    ) -> dict[str, Any] | None:
        """Find the first nested dict containing any of the requested keys."""
        if isinstance(obj, dict):
            if any(key in obj for key in keys):
                return obj
            for value in obj.values():
                found = JackerySolarVaultCoordinator._find_dict_with_any_key(
                    value, keys
                )
                if found is not None:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = JackerySolarVaultCoordinator._find_dict_with_any_key(item, keys)
                if found is not None:
                    return found
        return None

    @staticmethod
    def _find_list_for_key(obj: Any, key: str) -> list[dict[str, Any]] | None:
        """Find a nested list of dicts under a key such as batteryPacks."""
        if isinstance(obj, dict):
            value = obj.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
            for child in obj.values():
                found = JackerySolarVaultCoordinator._find_list_for_key(child, key)
                if found is not None:
                    return found
        elif isinstance(obj, list):
            for item in obj:
                found = JackerySolarVaultCoordinator._find_list_for_key(item, key)
                if found is not None:
                    return found
        return None

    @classmethod
    def _sanitize_main_properties(cls, props: dict[str, Any]) -> dict[str, Any]:
        """Remove accessory-only fields from main device properties."""
        clean = {
            key: value
            for key, value in dict(props).items()
            if key not in cls._SUBDEVICE_ONLY_PROPERTY_KEYS
        }
        return cls._sync_property_aliases(clean)

    @classmethod
    def _is_subdevice_payload(
        cls,
        payload: dict[str, Any],
        body: dict[str, Any],
    ) -> bool:
        """Identify MQTT accessory payloads mixed into the app device topic."""
        msg_type = str(payload.get(FIELD_MESSAGE_TYPE) or "")
        if "SubDevice" in msg_type:
            return True
        action_id = payload.get(FIELD_ACTION_ID)
        try:
            if int(action_id) in MQTT_ACTION_IDS_SUBDEVICE:
                return True
        except TypeError, ValueError:
            pass
        updates = body.get(FIELD_UPDATES)
        if isinstance(updates, dict) and any(
            key in updates
            for key in cls._SUBDEVICE_HINT_KEYS | cls._BATTERY_PACK_HINT_KEYS
        ):
            return True
        return any(key in body for key in cls._SUBDEVICE_HINT_KEYS)

    @classmethod
    def _normalize_battery_pack_payload(cls, item: Any) -> dict[str, Any]:
        """Flatten Jackery battery-pack payloads to BatteryPackSub fields.

        The Android app parses add-on battery updates from BatteryPackSub. In
        live MQTT frames the actual values can sit below an `updates` object,
        while the top level only carries deviceSn/inPw/outPw metadata. Flatten
        those shapes before merging so partial packets do not hide SOC/temp.
        """
        if not isinstance(item, dict):
            return {}
        normalized = dict(item)
        for nested_key in (FIELD_UPDATES, FIELD_BODY, PAYLOAD_PROPERTIES):
            nested = normalized.get(nested_key)
            if isinstance(nested, dict):
                normalized = cls._merge_dict_values(normalized, nested)
        aliases = {
            FIELD_RB: FIELD_BAT_SOC,
            FIELD_IP: FIELD_IN_PW,
            FIELD_OP: FIELD_OUT_PW,
        }
        for source_key, target_key in aliases.items():
            if (
                normalized.get(target_key) is None
                and normalized.get(source_key) is not None
            ):
                normalized[target_key] = normalized[source_key]
        return normalized

    @classmethod
    def _looks_like_battery_pack(cls, item: Any) -> bool:
        """Return True for add-on battery pack dicts, not CT/smart meters."""
        if not isinstance(item, dict):
            return False
        if any(key in item for key in cls._CT_METER_KEYS):
            return False
        if (
            str(item.get(FIELD_DEV_TYPE) or item.get(FIELD_DEVICE_TYPE) or "")
            in NON_BATTERY_SUBDEVICE_TYPES
        ):
            return False
        scan_name = str(item.get(FIELD_SCAN_NAME) or "").lower()
        if "shelly" in scan_name or "3em" in scan_name:
            return False
        if str(item.get(FIELD_SUB_TYPE) or "") == SMART_METER_SUBTYPE:
            return False
        return any(key in item for key in cls._BATTERY_PACK_HINT_KEYS)

    @classmethod
    def _battery_packs_from_source(cls, source: Any) -> list[dict[str, Any]] | None:
        """Extract up to five add-on battery pack payloads from known shapes."""
        for key in (
            FIELD_BATTERY_PACKS,
            FIELD_BATTERY_PACK,
            FIELD_BATTERY_PACK_LIST,
            FIELD_BATTERIES,
            FIELD_PACK_LIST,
        ):
            packs = cls._find_list_for_key(source, key)
            if packs:
                normalized = [
                    cls._normalize_battery_pack_payload(item) for item in packs
                ]
                filtered = [
                    item for item in normalized if cls._looks_like_battery_pack(item)
                ]
                return filtered[:5] if filtered else normalized[:5]
        if isinstance(source, list):
            normalized = [cls._normalize_battery_pack_payload(item) for item in source]
            packs = [item for item in normalized if cls._looks_like_battery_pack(item)]
            return packs[:5] if packs else None
        normalized_source = cls._normalize_battery_pack_payload(source)
        if cls._looks_like_battery_pack(normalized_source):
            return [normalized_source]
        return None

    @classmethod
    def _battery_packs_need_query(cls, payload: dict[str, Any]) -> bool:
        """Return True when add-on packs exist or are expected.

        The Android app polls BatteryPackSub over MQTT. The HTTP
        battery-pack endpoint can return data:null for this product/account,
        so stopping the MQTT query after the first SOC value leaves addon
        batteries stale.
        """
        props = payload.get(PAYLOAD_PROPERTIES) or {}
        try:
            expected = max(0, int(props.get(FIELD_BAT_NUM) or 0))
        except TypeError, ValueError:
            expected = 0
        packs = payload.get(PAYLOAD_BATTERY_PACKS)
        if not isinstance(packs, list):
            return expected > 0
        if expected > 0:
            return True
        return bool(packs)

    def _merge_subdevice_data(
        self,
        updated: dict[str, Any],
        source: dict[str, Any],
    ) -> bool:
        """Route accessory data to accessory sections instead of main props."""
        touched = False

        packs = self._battery_packs_from_source(source)
        if packs:
            merged_packs = self._merge_battery_pack_lists(
                updated.get(PAYLOAD_BATTERY_PACKS),
                packs,
            )
            cleaned, stale_count, dropped_indices = self._drop_stale_battery_packs(
                merged_packs
            )
            updated[PAYLOAD_BATTERY_PACKS] = cleaned
            if stale_count:
                self._stale_battery_packs_dropped += stale_count
                _LOGGER.info(
                    "Jackery: dropped %d stale battery pack(s) silent for >%d days",
                    stale_count,
                    BATTERY_PACK_STALE_THRESHOLD_SEC // 86400,
                )
                # Schedule HA device-registry cleanup for the dropped pack
                # indices. The merge runs synchronously; the actual
                # async_remove_device call happens in the post-update
                # cleanup hook below.
                device_id = self._resolve_device_id_from_payload(updated)
                if device_id:
                    for pack_index in dropped_indices:
                        identifier = (
                            DOMAIN,
                            f"{device_id}_battery_pack_{pack_index}",
                        )
                        self._pending_device_removals.append(identifier)
            touched = True

        ct = self._find_dict_with_any_key(source, self._CT_METER_KEYS)
        if ct:
            current_ct = updated.get(PAYLOAD_CT_METER)
            if isinstance(current_ct, dict):
                updated[PAYLOAD_CT_METER] = self._merge_dict_values(current_ct, ct)
            else:
                updated[PAYLOAD_CT_METER] = dict(ct)
            touched = True

        mirror = {
            key: value
            for key, value in source.items()
            if key in self._SUBDEVICE_MAIN_MIRROR_KEYS
        }
        if mirror:
            props = self._merge_main_properties(
                updated.get(PAYLOAD_PROPERTIES) or {},
                mirror,
            )
            updated[PAYLOAD_PROPERTIES] = props
            touched = True

        return touched

    @classmethod
    def _merge_battery_pack_lists(
        cls,
        current: Any,
        updates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge incremental pack telemetry without dropping static fields.

        Jackery's MQTT sub-device packets often contain only inPw/outPw plus
        deviceSn. Replacing the full pack list with those packets removes
        fields learned from HTTP/OTA (version, SOC, temperature). Keep known
        fields and overlay the latest non-null telemetry by SN, falling back
        to list position.
        """
        merged: list[dict[str, Any]] = [
            dict(item) for item in current or [] if isinstance(item, dict)
        ][:5]
        index_by_sn: dict[str, int] = {}
        for idx, item in enumerate(merged):
            sn = (
                item.get(FIELD_DEVICE_SN)
                or item.get(FIELD_DEV_SN)
                or item.get(FIELD_SN)
            )
            if sn:
                index_by_sn[str(sn)] = idx

        for update_idx, raw_update in enumerate(updates[:5]):
            if not isinstance(raw_update, dict):
                continue
            update = {
                key: value for key, value in raw_update.items() if value is not None
            }
            sn = (
                update.get(FIELD_DEVICE_SN)
                or update.get(FIELD_DEV_SN)
                or update.get(FIELD_SN)
            )
            target_idx = index_by_sn.get(str(sn)) if sn else None
            if target_idx is None and update_idx < len(merged):
                target_idx = update_idx

            if target_idx is None:
                merged.append(dict(update))
                if sn:
                    index_by_sn[str(sn)] = len(merged) - 1
            else:
                merged[target_idx] = cls._merge_dict_values(merged[target_idx], update)
                if sn:
                    index_by_sn[str(sn)] = target_idx

        # Tag each pack that is reporting as online with the current UTC
        # timestamp. Packs with commState=0 (offline) keep their previous
        # _last_seen_at so a brief outage does not look like a removal.
        # Packs that were never seen online (no commState) are also
        # tagged once on first discovery to give the stale-cleanup a
        # baseline.
        now_iso = dt_util.utcnow().isoformat()
        for pack in merged:
            comm_state = str(pack.get(FIELD_COMM_STATE) or "")
            if comm_state == "1" or PACK_FIELD_LAST_SEEN_AT not in pack:
                pack[PACK_FIELD_LAST_SEEN_AT] = now_iso

        return merged[:5]

    @classmethod
    def _drop_stale_battery_packs(
        cls,
        packs: list[dict[str, Any]],
        *,
        threshold_seconds: int = BATTERY_PACK_STALE_THRESHOLD_SEC,
    ) -> tuple[list[dict[str, Any]], int, list[int]]:
        """Remove packs that have been silent past the stale threshold.

        Returns a tuple of ``(kept_packs, stale_count, dropped_indices)``
        where ``dropped_indices`` is the list of original positions of
        the dropped packs (used by the coordinator to build the matching
        ``device_registry`` identifiers and call ``async_remove_device``).

        Cleanup is deliberately conservative: a pack must have been
        silent for the full threshold (default 7 days) before it is
        dropped, so daily WiFi blips or manual reboots do not trigger
        spurious removal.

        See ``BATTERY_PACK_STALE_THRESHOLD_SEC`` in const.py for the
        rationale and ``docs/STRICT_WORK_INSTRUCTIONS.md`` for the rule
        that we never invent device state, only document silence.
        """
        if not packs:
            return packs, 0, []
        now = dt_util.utcnow()
        kept: list[dict[str, Any]] = []
        stale = 0
        dropped_indices: list[int] = []
        for index, pack in enumerate(packs):
            last_seen = pack.get(PACK_FIELD_LAST_SEEN_AT)
            if not isinstance(last_seen, str):
                # No timestamp yet — keep, the next merge will tag it.
                kept.append(pack)
                continue
            try:
                seen_at = datetime.fromisoformat(last_seen)
            except ValueError:
                # Corrupt timestamp; keep but rewrite so future passes
                # have a clean baseline.
                kept.append(pack)
                continue
            elapsed = (now - seen_at).total_seconds()
            if elapsed > threshold_seconds:
                stale += 1
                dropped_indices.append(index)
                continue
            kept.append(pack)
        return kept, stale, dropped_indices

    @staticmethod
    def _resolve_device_id_from_payload(payload: dict[str, Any]) -> str | None:
        """Pick the parent device id from a coordinator payload slice.

        Used by the stale-pack cleanup to construct the ``device_registry``
        identifier. The coordinator data is keyed by ``device_id`` at the
        top level, but nested payload slices passed into the merge step
        do not carry that key. Best-effort fallback: read ``deviceId``,
        ``device_id`` or ``id`` from the merged props.
        """
        for key in ("deviceId", "device_id", "id"):
            value = payload.get(key)
            if isinstance(value, str | int) and str(value).strip():
                return str(value).strip()
        props = payload.get("properties")
        if isinstance(props, dict):
            for key in ("deviceId", "device_id"):
                value = props.get(key)
                if isinstance(value, str | int) and str(value).strip():
                    return str(value).strip()
        return None

    async def async_cleanup_pending_device_removals(self) -> int:
        """Remove queued battery-pack devices from HA's device registry.

        Called once per coordinator update from ``_async_update_data``
        after the merge has settled. Drains
        ``_pending_device_removals`` and asks HA to remove each
        device. Returns the number of devices actually removed.

        Implements the Gold-tier ``dynamic-devices`` rule: when a pack
        is permanently unplugged, both the integration's payload and
        HA's device registry must end up consistent without manual
        user intervention.
        """
        if not self._pending_device_removals:
            return 0
        # Local import keeps the registry stub-free for unit tests that
        # exercise the coordinator without HA helpers loaded.
        from homeassistant.helpers import device_registry as dr

        registry = dr.async_get(self.hass)
        removed = 0
        # Snapshot the queue and clear it before iterating so a concurrent
        # merge cannot lose new entries appended during the await.
        pending = list(self._pending_device_removals)
        self._pending_device_removals.clear()
        for identifier in pending:
            device = registry.async_get_device(identifiers={identifier})
            if device is None:
                continue
            registry.async_remove_device(device.id)
            removed += 1
        if removed:
            _LOGGER.info(
                "Jackery: removed %d stale battery-pack device(s) "
                "from HA device registry",
                removed,
            )
        return removed

    async def _async_enrich_battery_pack_ota(
        self,
        device_id: str,
        packs: list[dict[str, Any]],
        main_device_sn: str | None,
    ) -> None:
        """Attach per-pack OTA metadata for packs learned through MQTT.

        Jackery exposes addon battery live data via MQTT BatteryPackSub, but
        firmware versions are read through /v1/device/ota/list by deviceSn.
        """
        if not packs:
            return

        per_dev = self._slow_cache.setdefault(f"dev:{device_id}", {})
        now = time.monotonic()
        tasks: list[Any] = []
        task_meta: list[tuple[int, str, str]] = []

        for idx, pack in enumerate(packs[:5]):
            if not isinstance(pack, dict):
                continue
            pack_sn = (
                pack.get(FIELD_DEVICE_SN)
                or pack.get(FIELD_DEV_SN)
                or pack.get(FIELD_SN)
            )
            if not pack_sn:
                continue
            pack_sn = str(pack_sn)
            if main_device_sn and pack_sn == str(main_device_sn):
                continue

            cache_key = f"pack_ota:{pack_sn}"
            cached = per_dev.get(cache_key)
            if cached and now - cached[0] < self._price_config_interval_sec:
                cached_ota = cached[1]
                if isinstance(cached_ota, dict):
                    self._merge_pack_ota(packs[idx], cached_ota)
                continue

            tasks.append(self.api.async_get_ota_info(pack_sn))
            task_meta.append((idx, pack_sn, cache_key))

        if not tasks:
            return

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for (idx, pack_sn, cache_key), res in zip(task_meta, results, strict=False):
            if isinstance(res, Exception):
                _LOGGER.debug("Pack OTA fetch failed for %s: %s", pack_sn, res)
                continue
            if not isinstance(res, dict) or not res:
                continue
            per_dev[cache_key] = (now, res)
            self._merge_pack_ota(packs[idx], res)

    @staticmethod
    def _merge_pack_ota(pack: dict[str, Any], ota: dict[str, Any]) -> None:
        current_version = ota.get(FIELD_CURRENT_VERSION) or ota.get(FIELD_VERSION)
        if current_version is not None:
            pack[FIELD_VERSION] = current_version
            pack[FIELD_CURRENT_VERSION] = current_version
        for key in (FIELD_TARGET_VERSION, FIELD_UPDATE_STATUS, FIELD_UPDATE_CONTENT):
            if key in ota and ota.get(key) is not None:
                pack[key] = ota.get(key)

    @staticmethod
    def _is_smart_meter_accessory(item: dict[str, Any]) -> bool:
        """Return True for the CT/Smart-Meter accessory entry used by the app."""
        if (
            str(item.get(FIELD_DEV_TYPE) or item.get(FIELD_DEVICE_TYPE) or "")
            == SUBDEVICE_TYPE_SMART_METER
        ):
            return True
        text = " ".join(
            str(item.get(key) or "")
            for key in (
                FIELD_SCAN_NAME,
                FIELD_TYPE_NAME,
                FIELD_DEVICE_NAME,
                FIELD_PRODUCT_MODEL,
            )
        ).lower()
        if "shelly" in text or "3em" in text or "meter" in text or "ct" in text:
            return True
        return str(item.get(FIELD_SUB_TYPE) or "") == SMART_METER_SUBTYPE

    @classmethod
    def _smart_meter_accessories(cls, source: dict[str, Any]) -> list[dict[str, Any]]:
        """Return Smart-Meter accessory metadata from coordinator payload or index."""
        accessories: Any = source.get(FIELD_ACCESSORIES)
        if not isinstance(accessories, list):
            system = source.get(PAYLOAD_SYSTEM) or source.get(PAYLOAD_SYSTEM_META) or {}
            accessories = (
                system.get(FIELD_ACCESSORIES) if isinstance(system, dict) else []
            )
        if not isinstance(accessories, list):
            return []
        return [
            item
            for item in accessories
            if isinstance(item, dict) and cls._is_smart_meter_accessory(item)
        ]

    @classmethod
    def _smart_meter_accessory_device_id(cls, source: dict[str, Any]) -> str | None:
        """Return the app's subDeviceId for CT statistic endpoints."""
        for item in cls._smart_meter_accessories(source):
            dev_id = (
                item.get(FIELD_DEVICE_ID)
                or item.get(FIELD_ID)
                or item.get(FIELD_DEV_ID)
            )
            if dev_id is not None:
                return str(dev_id)

        ct = source.get(PAYLOAD_CT_METER) or {}
        if isinstance(ct, dict):
            dev_id = ct.get(FIELD_DEVICE_ID) or ct.get(FIELD_ID) or ct.get(FIELD_DEV_ID)
            if dev_id is not None:
                return str(dev_id)
        return None

    @classmethod
    def _has_smart_meter_accessory(cls, payload: dict[str, Any]) -> bool:
        """Return True when discovery metadata contains a CT/smart meter accessory."""
        return bool(cls._smart_meter_accessories(payload))

    @staticmethod
    def _trend_query_kwargs(date_type: str) -> dict[str, str]:
        """Return Jackery-app style trend query kwargs.

        APP_POLLING_MQTT.md requires explicit app ranges:
        day=today, week=Monday..Sunday, month=first..last, year=Jan 1..Dec 31.
        Using today..today with ``dateType=month/year`` can return partial
        day-like totals on some accounts.
        """
        return app_period_request_kwargs(date_type, today=dt_util.now().date())

    @staticmethod
    def _app_period_section(prefix: str, date_type: str) -> str:
        """Return the normalized payload key for documented app period sections."""
        return f"{prefix}_{date_type}"

    def _needs_year_month_backfill(
        self,
        payload: dict[str, Any],
        prefix: str,
        stat_keys: tuple[str, ...],
        *,
        today: date,
    ) -> bool:
        """Return whether a year section needs historical month fetches."""
        section = self._app_period_section(prefix, DATE_TYPE_YEAR)
        source = payload.get(section)
        if not isinstance(source, dict):
            return False
        return year_payload_appears_current_month_only(
            source,
            section,
            stat_keys,
            current_month=today.month,
        )

    def _apply_local_property_patch(
        self,
        device_id: str,
        updates: dict[str, Any],
    ) -> None:
        if not updates or not self.data or device_id not in self.data:
            return
        new_data = dict(self.data)
        entry = dict(new_data[device_id])
        props = self._sanitize_main_properties(entry.get(PAYLOAD_PROPERTIES) or {})
        props = self._merge_dict_values(props, self._sanitize_main_properties(updates))
        entry[PAYLOAD_PROPERTIES] = props
        new_data[device_id] = entry
        self._push_partial_update(new_data)

    def _invalidate_system_cache(self, system_id: str | None, *cache_keys: str) -> None:
        """Drop stale slow-cache entries after write endpoints."""
        if not system_id:
            return
        per_system = self._slow_cache.get(str(system_id))
        if not per_system:
            return
        for cache_key in cache_keys:
            per_system.pop(cache_key, None)

    def _apply_local_price_patch(
        self,
        device_id: str,
        updates: dict[str, Any],
    ) -> None:
        if not updates or not self.data or device_id not in self.data:
            return
        self._price_overrides[device_id] = (time.monotonic(), dict(updates))
        new_data = dict(self.data)
        entry = dict(new_data[device_id])
        price = dict(entry.get(PAYLOAD_PRICE) or {})
        price.update(updates)
        entry[PAYLOAD_PRICE] = price
        new_data[device_id] = entry
        self._push_partial_update(new_data)

    def _apply_local_weather_plan_patch(
        self,
        device_id: str,
        updates: dict[str, Any],
    ) -> None:
        if not updates or not self.data or device_id not in self.data:
            return
        new_data = dict(self.data)
        entry = dict(new_data[device_id])
        weather = dict(entry.get(PAYLOAD_WEATHER_PLAN) or {})
        weather.update(updates)
        entry[PAYLOAD_WEATHER_PLAN] = weather
        new_data[device_id] = entry
        self._push_partial_update(new_data)

    def _push_partial_update(self, new_data: dict[str, dict[str, Any]]) -> None:
        """Push updated coordinator data without rescheduling the poll timer.

        DataUpdateCoordinator.async_set_updated_data() resets the next scheduled
        refresh relative to "now". With frequent MQTT pushes, that can delay
        polling indefinitely and make the configured scan interval ineffective.
        """
        self.data = new_data
        self.last_update_success = True
        self.async_update_listeners()

    async def _async_query_system_info_for_missing(
        self,
        *,
        force: bool = False,
        ensure_mqtt: bool = True,
        snapshot: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Query app-style system config when HTTP properties omit it."""
        if self._mqtt is None or not self._mqtt.is_connected:
            return
        data = snapshot if snapshot is not None else (self.data or {})
        if not data:
            return

        now = time.monotonic()
        for device_id, payload in data.items():
            props = payload.get(PAYLOAD_PROPERTIES) or {}
            has_all = all(props.get(key) is not None for key in self._SYSTEM_INFO_KEYS)
            if has_all and not force:
                continue
            last_query = self._last_system_info_query.get(device_id, 0.0)
            if not force and (now - last_query) < self._system_info_query_interval_sec:
                continue
            self._last_system_info_query[device_id] = now
            try:
                await self.async_query_system_info(device_id, ensure_mqtt=ensure_mqtt)
            except (TimeoutError, HomeAssistantError, JackeryError) as err:
                _LOGGER.debug(
                    "Jackery system-info query failed for %s: %s", device_id, err
                )

    async def _async_query_weather_plan_for_missing(
        self,
        *,
        force: bool = False,
        ensure_mqtt: bool = True,
        snapshot: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Query weather/storm plan when lead-time fields are missing."""
        if self._mqtt is None or not self._mqtt.is_connected:
            return
        data = snapshot if snapshot is not None else (self.data or {})
        if not data:
            return

        now = time.monotonic()
        for device_id, payload in data.items():
            props = payload.get(PAYLOAD_PROPERTIES) or {}
            weather = payload.get(PAYLOAD_WEATHER_PLAN) or {}
            has_minutes = (
                props.get(FIELD_WPC) is not None
                or props.get(FIELD_MINS_INTERVAL) is not None
                or weather.get(FIELD_WPC) is not None
                or weather.get(FIELD_MINS_INTERVAL) is not None
            )
            if has_minutes and not force:
                continue
            last_query = self._last_weather_plan_query.get(device_id, 0.0)
            if not force and (now - last_query) < self._weather_plan_query_interval_sec:
                continue
            self._last_weather_plan_query[device_id] = now
            try:
                await self.async_query_weather_plan(device_id, ensure_mqtt=ensure_mqtt)
            except (TimeoutError, HomeAssistantError, JackeryError) as err:
                _LOGGER.debug(
                    "Jackery weather-plan query failed for %s: %s", device_id, err
                )

    async def _async_publish_command(
        self,
        device_id: str,
        *,
        message_type: str,
        action_id: int,
        cmd: int,
        body_fields: dict[str, Any],
        ensure_mqtt: bool = True,
    ) -> None:
        if self._mqtt is None:
            raise HomeAssistantError("MQTT client not initialized")

        try:
            creds = await self.api.async_get_mqtt_credentials()
        except JackeryError as err:
            raise HomeAssistantError(
                f"Could not build Jackery MQTT credentials: {err}"
            ) from err
        user_id = creds[MQTT_CREDENTIAL_USER_ID]
        topic = f"{MQTT_TOPIC_PREFIX}/{user_id}/{MQTT_TOPIC_COMMAND}"
        ts = int(time.time() * 1000)
        body: dict[str, Any] = dict(body_fields)
        # App formatter only injects `cmd` when bleMsgType > 0.
        # For actions like SendWeatherAlert/CancelWeatherAlert/Storm switch
        # (bleMsgType = 0), `cmd` is omitted.
        if int(cmd) > 0:
            body[FIELD_CMD] = int(cmd)
        payload: dict[str, Any] = {
            "id": ts,
            FIELD_VERSION: 0,
            FIELD_MESSAGE_TYPE: message_type,
            FIELD_ACTION_ID: action_id,
            FIELD_TIMESTAMP: ts,
            FIELD_BODY: body,
        }
        device_sn = self._resolve_device_sn(device_id)
        if not device_sn:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="mqtt_missing_device_sn",
                translation_placeholders={"device_id": str(device_id)},
            )
        payload[FIELD_DEVICE_SN] = device_sn

        last_err: Exception | None = None
        attempts = 2 if ensure_mqtt else 1
        for attempt in range(attempts):
            try:
                if ensure_mqtt:
                    await self._async_ensure_mqtt(
                        force=not self._mqtt.is_connected,
                        wait_connected=True,
                    )
                elif self._mqtt is None or not self._mqtt.is_connected:
                    raise RuntimeError("MQTT client is not connected")
                if self._mqtt is None:
                    raise RuntimeError("MQTT client is not running")
                await self._mqtt.async_publish_json(topic, payload, qos=0, retain=False)
                return
            except RuntimeError as err:
                last_err = err
                if ensure_mqtt and attempt == 0:
                    # Recover from stale MQTT sessions (e.g. app login on the
                    # same account rotated mqttPassWord). A fresh REST login
                    # rebuilds credentials before we reconnect.
                    try:
                        await self.api.async_login()
                    except JackeryError as relogin_err:
                        _LOGGER.debug(
                            "Jackery re-login before MQTT command retry failed: %s",
                            relogin_err,
                        )
                    # Force a clean MQTT client restart to avoid stale socket
                    # state races that can surface as publish rc=4.
                    if self._mqtt is not None:
                        await self._mqtt.async_stop()
                    continue
        mqtt_last_error = None
        if self._mqtt is not None:
            mqtt_last_error = self._mqtt.diagnostics.get("last_error")
        raise HomeAssistantError(
            translation_domain=DOMAIN,
            translation_key="mqtt_command_failed",
            translation_placeholders={
                "error": str(last_err) if last_err else "unknown",
                "mqtt_last_error": str(mqtt_last_error) if mqtt_last_error else "n/a",
            },
        ) from last_err

    async def async_set_eps(self, device_id: str, enabled: bool) -> None:
        """Set eps."""
        val = 1 if enabled else 0
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_EPS_ENABLED,
            cmd=MQTT_CMD_DEVICE_PROPERTY_CHANGE,
            body_fields={FIELD_SW_EPS: val},
        )
        self._apply_local_property_patch(device_id, {FIELD_SW_EPS: val})

    async def async_set_soc_limits(
        self,
        device_id: str,
        *,
        charge_limit: int | None = None,
        discharge_limit: int | None = None,
    ) -> None:
        """Set soc limits."""
        if charge_limit is None and discharge_limit is None:
            raise UpdateFailed(
                "Cannot set SOC limits without charge_limit or discharge_limit"
            )
        patch: dict[str, Any] = {}
        if charge_limit is not None:
            chg = int(charge_limit)
            await self._async_publish_command(
                device_id,
                message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
                action_id=ACTION_ID_SOC_CHARGE_LIMIT,
                cmd=MQTT_CMD_DEVICE_PROPERTY_CHANGE,
                body_fields={FIELD_SOC_CHARGE_LIMIT: chg, FIELD_SOC_CHG_LIMIT: chg},
            )
            patch.update({FIELD_SOC_CHARGE_LIMIT: chg, FIELD_SOC_CHG_LIMIT: chg})
        if discharge_limit is not None:
            dis = int(discharge_limit)
            await self._async_publish_command(
                device_id,
                message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
                action_id=ACTION_ID_SOC_DISCHARGE_LIMIT,
                cmd=MQTT_CMD_DEVICE_PROPERTY_CHANGE,
                body_fields={
                    FIELD_SOC_DISCHARGE_LIMIT: dis,
                    FIELD_SOC_DISCHG_LIMIT: dis,
                },
            )
            patch.update({FIELD_SOC_DISCHARGE_LIMIT: dis, FIELD_SOC_DISCHG_LIMIT: dis})
        self._apply_local_property_patch(device_id, patch)

    async def async_set_max_feed_grid(self, device_id: str, watts: int) -> None:
        """Set max feed grid."""
        value = int(watts)
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_COMBINE,
            action_id=ACTION_ID_MAX_FEED_GRID,
            cmd=MQTT_CMD_CONTROL_COMBINE,
            body_fields={FIELD_MAX_FEED_GRID: value},
        )
        self._apply_local_property_patch(
            device_id, {FIELD_MAX_FEED_GRID: value, FIELD_MAX_GRID_STD_PW: value}
        )

    async def async_set_max_output_power(self, device_id: str, watts: int) -> None:
        """Set max output power."""
        value = int(watts)
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_COMBINE,
            action_id=ACTION_ID_MAX_OUT_PW,
            cmd=MQTT_CMD_CONTROL_COMBINE,
            body_fields={FIELD_MAX_OUT_PW: value},
        )
        self._apply_local_property_patch(device_id, {FIELD_MAX_OUT_PW: value})

    async def async_set_auto_standby_hours(
        self,
        device_id: str,
        hours: int,
    ) -> None:
        # App-side setter uses a boolean payload key "isAutoStandby"
        # (0/1), not an hour value.
        """Set auto standby hours."""
        val = 1 if int(hours) > 0 else 0
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_COMBINE,
            action_id=ACTION_ID_AUTO_STANDBY,
            cmd=MQTT_CMD_CONTROL_COMBINE,
            body_fields={FIELD_IS_AUTO_STANDBY: val},
        )
        # Keep legacy mirror field consistent for read-side sensors that may
        # still report enum semantics (1=SLEEP/auto-off, 2=POWER_ON).
        self._apply_local_property_patch(
            device_id,
            {FIELD_IS_AUTO_STANDBY: val, FIELD_AUTO_STANDBY: 1 if val == 1 else 2},
        )

    async def async_set_auto_standby(self, device_id: str, enabled: bool) -> None:
        """Backward-compatible bool setter (legacy switch entity)."""
        val = 1 if enabled else 0
        await self.async_set_auto_standby_hours(device_id, val)

    async def async_set_standby(self, device_id: str, enabled: bool) -> None:
        """Put the unit into standby/sleep or power it back on.

        App mapping: HomeDeviceController.a.SLEEP=1, POWER_ON=2.
        """
        value = 1 if enabled else 2
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_STANDBY,
            cmd=MQTT_CMD_DEVICE_PROPERTY_CHANGE,
            body_fields={FIELD_AUTO_STANDBY: value},
        )
        self._apply_local_property_patch(device_id, {FIELD_AUTO_STANDBY: value})

    async def async_set_work_model(self, device_id: str, mode: int) -> None:
        """Set work model."""
        value = int(mode)
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_COMBINE,
            action_id=ACTION_ID_WORK_MODEL,
            cmd=MQTT_CMD_CONTROL_COMBINE,
            body_fields={FIELD_WORK_MODEL: value},
        )
        self._apply_local_property_patch(device_id, {FIELD_WORK_MODEL: value})

    async def async_set_off_grid_shutdown(self, device_id: str, enabled: bool) -> None:
        """Set off grid shutdown."""
        val = 1 if enabled else 0
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_COMBINE,
            action_id=ACTION_ID_OFF_GRID_DOWN,
            cmd=MQTT_CMD_CONTROL_COMBINE,
            body_fields={FIELD_OFF_GRID_DOWN: val},
        )
        self._apply_local_property_patch(device_id, {FIELD_OFF_GRID_DOWN: val})

    async def async_set_off_grid_time(self, device_id: str, minutes: int) -> None:
        """Set off grid time."""
        value = int(minutes)
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_COMBINE,
            action_id=ACTION_ID_OFF_GRID_TIME,
            cmd=MQTT_CMD_CONTROL_COMBINE,
            body_fields={FIELD_OFF_GRID_TIME: value},
        )
        self._apply_local_property_patch(device_id, {FIELD_OFF_GRID_TIME: value})

    async def async_set_default_power(self, device_id: str, watts: int) -> None:
        """Set default power."""
        value = int(watts)
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_COMBINE,
            action_id=ACTION_ID_DEFAULT_PW,
            cmd=MQTT_CMD_CONTROL_COMBINE,
            body_fields={FIELD_DEFAULT_PW: value},
        )
        self._apply_local_property_patch(device_id, {FIELD_DEFAULT_PW: value})

    async def async_set_follow_meter(self, device_id: str, enabled: bool) -> None:
        """Set follow meter."""
        val = 1 if enabled else 0
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_COMBINE,
            action_id=ACTION_ID_FOLLOW_METER_PW,
            cmd=MQTT_CMD_CONTROL_COMBINE,
            body_fields={FIELD_IS_FOLLOW_METER_PW: val},
        )
        self._apply_local_property_patch(device_id, {FIELD_IS_FOLLOW_METER_PW: val})

    async def async_set_storm_warning(self, device_id: str, enabled: bool) -> None:
        """Set storm warning."""
        val = 1 if enabled else 0
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_COMBINE,
            action_id=ACTION_ID_STORM_WARNING,
            cmd=MQTT_CMD_NONE,
            body_fields={FIELD_WPS: val},
        )
        self._apply_local_property_patch(device_id, {FIELD_WPS: val})
        self._apply_local_weather_plan_patch(device_id, {FIELD_WPS: val})

    async def async_set_storm_minutes(self, device_id: str, minutes: int) -> None:
        """Set storm minutes."""
        value = int(minutes)
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_SEND_WEATHER_ALERT,
            action_id=ACTION_ID_STORM_MINUTES,
            cmd=MQTT_CMD_NONE,
            body_fields={FIELD_MINS_INTERVAL: value},
        )
        # Some payloads expose this value as wpc in system/config snapshots.
        self._apply_local_property_patch(
            device_id, {FIELD_WPC: value, FIELD_MINS_INTERVAL: value}
        )
        self._apply_local_weather_plan_patch(
            device_id, {FIELD_WPC: value, FIELD_MINS_INTERVAL: value}
        )

    async def async_delete_storm_alert(self, device_id: str, alert_id: str) -> None:
        """Delete storm alert."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_CANCEL_WEATHER_ALERT,
            action_id=ACTION_ID_DELETE_STORM_ALERT,
            cmd=MQTT_CMD_NONE,
            body_fields={"alertId": alert_id},
        )

    async def async_set_temp_unit(self, device_id: str, unit: int) -> None:
        """Set temp unit."""
        value = int(unit)
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_COMBINE,
            action_id=ACTION_ID_TEMP_UNIT,
            cmd=MQTT_CMD_CONTROL_COMBINE,
            body_fields={FIELD_TEMP_UNIT: value},
        )
        self._apply_local_property_patch(device_id, {FIELD_TEMP_UNIT: value})

    async def async_set_single_price(self, device_id: str, price_value: float) -> None:
        """Set single price."""
        system_id = self._resolve_system_id(device_id)
        if not system_id:
            raise UpdateFailed(
                f"Cannot set single tariff for {device_id}: missing systemId"
            )
        current = ((self.data or {}).get(device_id, {}) or {}).get(PAYLOAD_PRICE) or {}
        currency = (
            current.get(FIELD_SINGLE_CURRENCY)
            or current.get(FIELD_CURRENCY)
            or current.get(FIELD_SINGLE_CURRENCY_CODE)
            or current.get(FIELD_CURRENCY_CODE)
            or "€"
        )
        await self.api.async_set_single_mode(
            system_id=system_id,
            single_price=float(price_value),
            currency=str(currency),
        )
        self._invalidate_system_cache(system_id, PAYLOAD_PRICE)
        self._apply_local_price_patch(
            device_id,
            {
                FIELD_DYNAMIC_OR_SINGLE: 2,
                FIELD_SINGLE_PRICE: round(float(price_value), 4),
            },
        )

    async def async_set_price_mode_single(self, device_id: str) -> None:
        """Set price mode single."""
        current = ((self.data or {}).get(device_id, {}) or {}).get(PAYLOAD_PRICE) or {}
        single_price = current.get(FIELD_SINGLE_PRICE)
        if single_price is None:
            system_id = self._resolve_system_id(device_id)
            if not system_id:
                raise HomeAssistantError(
                    f"Cannot switch to single tariff for {device_id}: missing systemId"
                )
            try:
                latest = await self.api.async_get_power_price(system_id)
            except JackeryError as err:
                raise HomeAssistantError(
                    f"Cannot switch to single tariff for {device_id}: {err}"
                ) from err
            if isinstance(latest, dict):
                self._apply_local_price_patch(device_id, latest)
                single_price = latest.get(FIELD_SINGLE_PRICE)
        if single_price is None:
            raise HomeAssistantError(
                f"Cannot switch to single tariff for {device_id}: missing singlePrice"
            )
        await self.async_set_single_price(device_id, float(single_price))

    @staticmethod
    def _valid_price_sources(sources: Any) -> list[dict[str, Any]]:
        if not isinstance(sources, list):
            return []
        valid: list[dict[str, Any]] = []
        for item in sources:
            if not isinstance(item, dict):
                continue
            company_id = item.get(FIELD_PLATFORM_COMPANY_ID)
            region = item.get(FIELD_COUNTRY) or item.get(FIELD_SYSTEM_REGION)
            if company_id in (None, "") or not region:
                continue
            valid.append(item)
        return valid

    async def _async_price_sources_for_device(
        self, device_id: str
    ) -> list[dict[str, Any]]:
        payload = (self.data or {}).get(device_id, {}) or {}
        sources = self._valid_price_sources(payload.get(PAYLOAD_PRICE_SOURCES))
        if sources:
            return sources

        system_id = self._resolve_system_id(device_id)
        if not system_id:
            return []
        try:
            sources = self._valid_price_sources(
                await self.api.async_get_price_sources(system_id)
            )
        except JackeryError as err:
            _LOGGER.debug("price source fetch failed for %s: %s", device_id, err)
            return []

        if self.data and device_id in self.data:
            new_data = dict(self.data)
            entry = dict(new_data[device_id])
            entry[PAYLOAD_PRICE_SOURCES] = sources
            new_data[device_id] = entry
            self._push_partial_update(new_data)
        return sources

    @staticmethod
    def _source_regions(source: dict[str, Any]) -> list[str]:
        raw = source.get(FIELD_SYSTEM_REGION) or source.get(FIELD_COUNTRY)
        if raw in (None, ""):
            return []
        return [part.strip() for part in str(raw).split(",") if part.strip()]

    def _device_country_code(self, device_id: str) -> str | None:
        payload = (self.data or {}).get(device_id, {}) or {}
        for section_name in (PAYLOAD_SYSTEM, PAYLOAD_DEVICE, PAYLOAD_DISCOVERY):
            section = payload.get(section_name) or {}
            if not isinstance(section, dict):
                continue
            raw = (
                section.get(FIELD_COUNTRY_CODE)
                or section.get(FIELD_COUNTRY)
                or section.get(FIELD_SYSTEM_REGION)
            )
            if raw not in (None, ""):
                return str(raw).strip().upper()
        return None

    def _source_region_for_device(
        self, device_id: str, source: dict[str, Any]
    ) -> str | None:
        regions = self._source_regions(source)
        if not regions:
            return None
        country = self._device_country_code(device_id)
        if country:
            for region in regions:
                if region.upper() == country:
                    return region
        return regions[0]

    def _find_matching_price_source(
        self,
        device_id: str,
        sources: list[dict[str, Any]],
        current: dict[str, Any],
    ) -> dict[str, Any] | None:
        company_id = current.get(FIELD_PLATFORM_COMPANY_ID)
        if company_id in (None, ""):
            return None
        region = current.get(FIELD_SYSTEM_REGION)
        country = self._device_country_code(device_id)
        matches = [
            source
            for source in sources
            if str(source.get(FIELD_PLATFORM_COMPANY_ID)) == str(company_id)
        ]
        if not matches:
            return None
        if region not in (None, ""):
            for source in matches:
                if str(region) in self._source_regions(source):
                    return source
        if country:
            for source in matches:
                if country in {item.upper() for item in self._source_regions(source)}:
                    return source
        return matches[0] if len(matches) == 1 else None

    async def async_set_price_source(
        self,
        device_id: str,
        source: dict[str, Any],
    ) -> None:
        """Select a dynamic-price provider via the app's saveDynamicMode API."""
        system_id = self._resolve_system_id(device_id)
        if not system_id:
            raise HomeAssistantError(
                f"Cannot set dynamic tariff for {device_id}: missing systemId"
            )

        company_id = source.get(FIELD_PLATFORM_COMPANY_ID)
        region = self._source_region_for_device(device_id, source)
        if company_id in (None, "") or not region:
            raise HomeAssistantError(
                "Cannot set dynamic tariff: selected provider is missing "
                "platformCompanyId/country."
            )

        await self.api.async_set_dynamic_mode(
            system_id=system_id,
            platform_company_id=int(company_id),
            system_region=str(region),
        )
        self._invalidate_system_cache(system_id, PAYLOAD_PRICE)
        self._apply_local_price_patch(
            device_id,
            {
                FIELD_DYNAMIC_OR_SINGLE: 1,
                FIELD_PLATFORM_COMPANY_ID: int(company_id),
                FIELD_SYSTEM_REGION: str(region),
                FIELD_COMPANY_NAME: source.get(FIELD_COMPANY_NAME)
                or source.get(FIELD_NAME),
                FIELD_POWER_PRICE_RESOURCE: source.get(FIELD_CID),
                FIELD_LOGIN_ALLOWED: source.get(FIELD_LOGIN_ALLOWED),
            },
        )

    async def async_set_price_mode_dynamic(self, device_id: str) -> None:
        """Set price mode dynamic."""
        system_id = self._resolve_system_id(device_id)
        if not system_id:
            raise HomeAssistantError(
                f"Cannot set dynamic tariff for {device_id}: missing systemId"
            )
        current = ((self.data or {}).get(device_id, {}) or {}).get(PAYLOAD_PRICE) or {}
        company_id = current.get(FIELD_PLATFORM_COMPANY_ID)
        region = current.get(FIELD_SYSTEM_REGION)
        if company_id in (None, "") or not region:
            sources = await self._async_price_sources_for_device(device_id)
            source = self._find_matching_price_source(device_id, sources, current)
            if source is not None:
                await self.async_set_price_source(device_id, source)
                return
            if len(sources) == 1:
                await self.async_set_price_source(device_id, sources[0])
                return
            raise HomeAssistantError(
                "Dynamic tariff requires provider selection. Use the "
                "'Electricity price provider' select entity first."
            )
        await self.api.async_set_dynamic_mode(
            system_id=system_id,
            platform_company_id=int(company_id),
            system_region=str(region),
        )
        self._invalidate_system_cache(system_id, PAYLOAD_PRICE)
        self._apply_local_price_patch(device_id, {FIELD_DYNAMIC_OR_SINGLE: 1})

    async def async_query_system_info(
        self, device_id: str, *, ensure_mqtt: bool = True
    ) -> None:
        """Query system info."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_COMBINE_DATA,
            action_id=ACTION_ID_QUERY_COMBINE_DATA,
            cmd=MQTT_CMD_QUERY_COMBINE_DATA,
            body_fields={},
            ensure_mqtt=ensure_mqtt,
        )

    async def async_query_weather_plan(
        self, device_id: str, *, ensure_mqtt: bool = True
    ) -> None:
        """Query weather plan."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_WEATHER_PLAN,
            action_id=ACTION_ID_QUERY_WEATHER_PLAN,
            cmd=MQTT_CMD_QUERY_WEATHER_PLAN,
            body_fields={},
            ensure_mqtt=ensure_mqtt,
        )

    async def async_query_battery_packs(
        self, device_id: str, *, ensure_mqtt: bool = True
    ) -> None:
        """Query battery packs."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
            action_id=ACTION_ID_SUBDEVICE_3014,
            cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
            body_fields={FIELD_DEV_TYPE: 1},
            ensure_mqtt=ensure_mqtt,
        )

    async def async_query_smart_meter(
        self, device_id: str, *, ensure_mqtt: bool = True
    ) -> None:
        """Query smart meter."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
            action_id=ACTION_ID_SUBDEVICE_3031,
            cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
            body_fields={FIELD_DEV_TYPE: 3},
            ensure_mqtt=ensure_mqtt,
        )

    async def async_query_subdevice_combo(
        self, device_id: str, *, ensure_mqtt: bool = True
    ) -> None:
        """Query subdevice combo."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
            action_id=ACTION_ID_SUBDEVICE_3037,
            cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
            body_fields={FIELD_DEV_TYPE: 2},
            ensure_mqtt=ensure_mqtt,
        )

    async def async_reboot_device(self, device_id: str) -> None:
        """Reboot device."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_REBOOT_DEVICE,
            cmd=MQTT_CMD_DEVICE_PROPERTY_CHANGE,
            body_fields={FIELD_REBOOT: 1},
        )
        self._apply_local_property_patch(device_id, {FIELD_REBOOT: 1})

    async def _async_query_subdevices_for_missing(
        self,
        *,
        force: bool = False,
        ensure_mqtt: bool = True,
        snapshot: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Query MQTT sub-device status for smart meter and pack payloads."""
        if self._mqtt is None or not self._mqtt.is_connected:
            return
        data = snapshot if snapshot is not None else self.data
        if not data:
            return
        now = time.monotonic()
        for device_id, payload in data.items():
            should_query_meter = (
                force
                or self._has_smart_meter_accessory(payload)
                or isinstance(payload.get(PAYLOAD_CT_METER), dict)
            )
            should_query_packs = self._battery_packs_need_query(payload)
            if not should_query_meter and not should_query_packs:
                continue
            if (
                not force
                and now - self._last_subdevice_query.get(device_id, 0)
                < self._subdevice_query_interval_sec
            ):
                continue
            self._last_subdevice_query[device_id] = now
            if should_query_packs:
                try:
                    await self.async_query_battery_packs(
                        device_id, ensure_mqtt=ensure_mqtt
                    )
                except (TimeoutError, HomeAssistantError, JackeryError) as err:
                    _LOGGER.debug(
                        "Jackery battery-pack query failed for %s: %s", device_id, err
                    )
                try:
                    await self.async_query_subdevice_combo(
                        device_id, ensure_mqtt=ensure_mqtt
                    )
                except (TimeoutError, HomeAssistantError, JackeryError) as err:
                    _LOGGER.debug(
                        "Jackery subdevice-combo query failed for %s: %s",
                        device_id,
                        err,
                    )
            if should_query_meter:
                try:
                    await self.async_query_smart_meter(
                        device_id, ensure_mqtt=ensure_mqtt
                    )
                except (TimeoutError, HomeAssistantError, JackeryError) as err:
                    _LOGGER.debug(
                        "Jackery smart-meter query failed for %s: %s", device_id, err
                    )

    def _schedule_mqtt_backfill_queries(
        self, snapshot: dict[str, dict[str, Any]]
    ) -> None:
        """Queue MQTT query commands without blocking the HTTP poll result."""
        if self._mqtt is None or not self._mqtt.is_connected:
            return
        if self._mqtt_backfill_task is not None and not self._mqtt_backfill_task.done():
            return
        self._mqtt_backfill_task = self.hass.async_create_task(
            self._async_mqtt_backfill_queries(dict(snapshot)),
            name=f"{DOMAIN}_mqtt_backfill_queries",
        )

    async def _async_mqtt_backfill_queries(
        self, snapshot: dict[str, dict[str, Any]]
    ) -> None:
        """Refresh app-side MQTT-only data after the HTTP poll has completed."""
        try:
            await self._async_query_system_info_for_missing(snapshot=snapshot)
            await self._async_query_weather_plan_for_missing(snapshot=snapshot)
            await self._async_query_subdevices_for_missing(snapshot=snapshot)
        except Exception as err:
            _LOGGER.debug("Jackery MQTT backfill query failed: %s", err)

    def _local_statistic_start(self, bucket_date: date) -> datetime:
        """Return a UTC timestamp for a local app-statistic bucket date."""
        timezone = dt_util.get_time_zone(self.hass.config.time_zone)
        if timezone is None:
            timezone = dt_util.DEFAULT_TIME_ZONE
        local_start = datetime.combine(
            bucket_date,
            datetime.min.time(),
            tzinfo=timezone,
        )
        return dt_util.as_utc(local_start)

    @staticmethod
    def _stat_row_start(row: dict[str, Any]) -> float | None:
        """Return a statistics row start timestamp in seconds."""
        start = row.get("start")
        if isinstance(start, datetime):
            return start.timestamp()
        return safe_float(start)

    async def _async_statistic_sum_offset(
        self,
        statistic_id: str,
        starts: list[datetime],
        states: list[float],
    ) -> float:
        """Return the cumulative sum offset for rewritten app chart statistics.

        App-period endpoints return the full documented range on every refresh.
        Rewriting the same external statistic rows lets HA reflect corrected
        app chart buckets without resetting the long-term ``sum``.
        """
        if not starts or not states:
            return 0.0
        try:
            from homeassistant.components.recorder import get_instance
            from homeassistant.components.recorder.statistics import (
                statistics_during_period,
            )
        except (ImportError, RuntimeError) as err:
            _LOGGER.debug("Recorder statistics API unavailable: %s", err)
            return 0.0

        try:
            recorder = get_instance(self.hass)
        except Exception as err:
            _LOGGER.debug("Recorder instance unavailable: %s", err)
            return 0.0

        # Only rows before the rewritten range can be used as lifetime offset.
        # Rows inside the current app range are rewritten when Jackery backfills
        # or corrects the documented period chart.
        prior_start = starts[0] - timedelta(days=370)
        try:
            existing = await recorder.async_add_executor_job(
                statistics_during_period,
                self.hass,
                prior_start,
                starts[0],
                {statistic_id},
                "hour",
                None,
                {"start", "sum"},
            )
        except Exception as err:
            _LOGGER.debug(
                "Could not read previous statistics for %s: %s",
                statistic_id,
                err,
            )
            return 0.0

        rows = existing.get(statistic_id, []) if isinstance(existing, dict) else []
        previous: tuple[float, float] | None = None
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_start = self._stat_row_start(row)
            row_sum = safe_float(row.get("sum"))
            if row_start is None or row_sum is None:
                continue
            if row_start >= starts[0].timestamp():
                continue
            if previous is None or row_start > previous[0]:
                previous = (row_start, row_sum)
        if previous is None:
            return 0.0
        return round(previous[1], 5)

    async def _async_update_data_quality_issue(
        self,
        snapshot: dict[str, dict[str, Any]],
    ) -> None:
        """Surface contradictory app statistics as a HA repair issue.

        The integration must not silently repair period or lifetime totals with
        other periods. Instead it keeps every entity on its documented source
        and creates a repair issue when the app/cloud data contradicts itself.
        """
        warnings: list[dict[str, Any]] = []
        for dev_id in sorted(snapshot):
            source = snapshot[dev_id].get(PAYLOAD_DATA_QUALITY)
            if isinstance(source, list):
                warnings.extend(item for item in source if isinstance(item, dict))
        warnings = normalized_data_quality_warnings(warnings)

        try:
            from homeassistant.helpers import issue_registry as ir
        except ImportError, RuntimeError:
            if warnings:
                examples = "; ".join(
                    format_data_quality_warning(warning)
                    for warning in warnings[:DATA_QUALITY_REPAIR_EXAMPLE_LIMIT]
                )
                _LOGGER.warning(
                    "Jackery app/cloud statistics are inconsistent; diagnostics contain %d warning(s): %s",
                    len(warnings),
                    examples,
                )
            return

        issue_id = f"{self.entry.entry_id}_{REPAIR_ISSUE_APP_DATA_INCONSISTENCY}"
        if not warnings:
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return

        first = warnings[0]
        examples = "; ".join(
            format_data_quality_warning(warning)
            for warning in warnings[:DATA_QUALITY_REPAIR_EXAMPLE_LIMIT]
        )
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=REPAIR_TRANSLATION_APP_DATA_INCONSISTENCY,
            translation_placeholders={
                "count": str(len(warnings)),
                "metric": str(
                    first.get(DATA_QUALITY_KEY_LABEL)
                    or first.get(DATA_QUALITY_KEY_METRIC_KEY)
                    or "unknown"
                ),
                "examples": examples or "unknown",
            },
        )

    async def _async_import_app_chart_statistics(
        self,
        snapshot: dict[str, dict[str, Any]],
    ) -> None:
        """Import Jackery app chart arrays as real HA external statistics.

        APP_POLLING_MQTT.md defines the source endpoints and period ranges.
        Normal week/month/year entities remain app period totals; the app chart
        arrays are imported separately as HA external statistics so recorder
        graphs receive real dated buckets instead of one flat total state.
        """
        if not snapshot:
            return
        try:
            from homeassistant.components.recorder.models import (
                StatisticData,
                StatisticMeanType,
                StatisticMetaData,
            )
            from homeassistant.components.recorder.statistics import (
                async_add_external_statistics,
            )
            from homeassistant.const import UnitOfEnergy
            from homeassistant.util.unit_conversion import EnergyConverter
        except (ImportError, RuntimeError) as err:
            _LOGGER.debug("Recorder statistics import unavailable: %s", err)
            return

        today = dt_util.now().date()
        for device_id, payload in snapshot.items():
            if not isinstance(payload, dict):
                continue
            name_prefix = (
                (payload.get(PAYLOAD_SYSTEM) or {}).get(FIELD_DEVICE_NAME)
                or (payload.get(PAYLOAD_DISCOVERY) or {}).get(FIELD_DEVICE_NAME)
                or (payload.get(PAYLOAD_PROPERTIES) or {}).get(FIELD_WNAME)
                or f"Jackery {device_id}"
            )
            for section_prefix, stat_key, metric_key, label in APP_CHART_STAT_METRICS:
                # CT chart imports are intentionally excluded; see
                # APP_POLLING_MQTT.md Smart-Meter/CT rules.
                for date_type, bucket, bucket_label in APP_CHART_STAT_PERIODS:
                    section = f"{section_prefix}_{date_type}"
                    source = payload.get(section)
                    if not isinstance(source, dict):
                        continue
                    points = trend_series_points(
                        source,
                        section,
                        stat_key,
                        today=today,
                    )
                    if not points:
                        continue
                    starts = [
                        self._local_statistic_start(point.start_date)
                        for point in points
                    ]
                    states = [max(0.0, round(point.value, 5)) for point in points]
                    if not starts or not states:
                        continue
                    statistic_id = external_trend_statistic_id(
                        DOMAIN,
                        device_id,
                        metric_key,
                        bucket,
                    )
                    # Skip the (recorder read + recorder write) round-trip
                    # when the chart arrays have not changed since the last
                    # successful import. Jackery's app chart arrays update
                    # at most once per ~hour for week/month/year buckets,
                    # so the previous coordinator refresh almost always
                    # produces an identical (starts, states) tuple. The
                    # signature includes ``starts`` so a day-rollover or
                    # month-rollover invalidates the cache automatically.
                    series_signature = json.dumps(
                        [
                            [
                                s.isoformat() if hasattr(s, "isoformat") else s
                                for s in starts
                            ],
                            states,
                        ],
                        sort_keys=True,
                        default=str,
                    )
                    if self._stat_import_last_sig.get(statistic_id) == series_signature:
                        continue
                    offset = await self._async_statistic_sum_offset(
                        statistic_id,
                        starts,
                        states,
                    )
                    statistics: list[StatisticData] = []
                    cumulative = offset
                    for start, state in zip(starts, states, strict=False):
                        cumulative = round(cumulative + state, 5)
                        statistics.append(
                            StatisticData(start=start, state=state, sum=cumulative)
                        )
                    metadata = StatisticMetaData(
                        mean_type=StatisticMeanType.NONE,
                        has_sum=True,
                        name=f"{name_prefix} {label} ({bucket_label})",
                        source=DOMAIN,
                        statistic_id=statistic_id,
                        unit_class=EnergyConverter.UNIT_CLASS,
                        unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
                    )
                    try:
                        async_add_external_statistics(
                            self.hass,
                            metadata,
                            statistics,
                        )
                    except Exception as err:
                        _LOGGER.debug(
                            "Could not import %d app chart statistics for %s: %s",
                            len(statistics),
                            statistic_id,
                            err,
                        )
                        continue
                    self._stat_import_last_sig[statistic_id] = series_signature
                    _LOGGER.debug(
                        "Imported %d Jackery app chart statistic bucket(s) for %s",
                        len(statistics),
                        statistic_id,
                    )

    async def _async_update_data(
        self, _retry_discovery_once: bool = True
    ) -> dict[str, dict[str, Any]]:
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
        # final values for up to self._slow_metrics_interval_sec.
        today = dt_util.now().date()
        if self._cached_date is not None and self._cached_date != today:
            _LOGGER.debug(
                "Jackery: day rollover (%s -> %s), clearing day-bounded caches",
                self._cached_date,
                today,
            )
            cache_keys_to_clear = (
                PAYLOAD_STATISTIC,
                PAYLOAD_PV_TRENDS,
                self._app_period_section(APP_SECTION_PV_TRENDS, DATE_TYPE_WEEK),
                self._app_period_section(APP_SECTION_PV_TRENDS, DATE_TYPE_MONTH),
                self._app_period_section(APP_SECTION_PV_TRENDS, DATE_TYPE_YEAR),
                PAYLOAD_HOME_TRENDS,
                self._app_period_section(APP_SECTION_HOME_TRENDS, DATE_TYPE_WEEK),
                self._app_period_section(APP_SECTION_HOME_TRENDS, DATE_TYPE_MONTH),
                self._app_period_section(APP_SECTION_HOME_TRENDS, DATE_TYPE_YEAR),
                PAYLOAD_BATTERY_TRENDS,
                self._app_period_section(APP_SECTION_BATTERY_TRENDS, DATE_TYPE_WEEK),
                self._app_period_section(APP_SECTION_BATTERY_TRENDS, DATE_TYPE_MONTH),
                self._app_period_section(APP_SECTION_BATTERY_TRENDS, DATE_TYPE_YEAR),
                self._app_period_section(APP_SECTION_PV_STAT, DATE_TYPE_DAY),
                self._app_period_section(APP_SECTION_PV_STAT, DATE_TYPE_WEEK),
                self._app_period_section(APP_SECTION_PV_STAT, DATE_TYPE_MONTH),
                self._app_period_section(APP_SECTION_PV_STAT, DATE_TYPE_YEAR),
                self._app_period_section(APP_SECTION_BATTERY_STAT, DATE_TYPE_DAY),
                self._app_period_section(APP_SECTION_BATTERY_STAT, DATE_TYPE_WEEK),
                self._app_period_section(APP_SECTION_BATTERY_STAT, DATE_TYPE_MONTH),
                self._app_period_section(APP_SECTION_BATTERY_STAT, DATE_TYPE_YEAR),
                self._app_period_section(APP_SECTION_HOME_STAT, DATE_TYPE_DAY),
                self._app_period_section(APP_SECTION_HOME_STAT, DATE_TYPE_WEEK),
                self._app_period_section(APP_SECTION_HOME_STAT, DATE_TYPE_MONTH),
                self._app_period_section(APP_SECTION_HOME_STAT, DATE_TYPE_YEAR),
            )
            for cache in self._slow_cache.values():
                for cache_key in cache_keys_to_clear:
                    cache.pop(cache_key, None)
        self._cached_date = today

        async def _get_with_ttl_for(
            cache: dict[str, tuple[float, Any]],
            cache_key: str,
            ttl_sec: int,
            fetcher: Callable[[], Awaitable[Any]],
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
            fetcher: Callable[[str], Awaitable[Any]],
            default: Any,
        ) -> Any:
            """System-scoped TTL cache wrapper."""
            per_system = self._slow_cache.setdefault(sys_id, {})
            return await _get_with_ttl_for(
                per_system,
                cache_key,
                ttl_sec,
                lambda: fetcher(sys_id),
                default,
            )

        async def _fetch_system(sys_id: str) -> dict[str, Any]:
            if sys_id in system_cache:
                return system_cache[sys_id]
            (
                statistic,
                alarm,
                pv_trends,
                pv_trends_week,
                pv_trends_month,
                pv_trends_year,
                home_trends,
                home_trends_week,
                home_trends_month,
                home_trends_year,
                battery_trends,
                battery_trends_week,
                battery_trends_month,
                battery_trends_year,
                price,
                price_sources,
                price_history_config,
            ) = await asyncio.gather(
                _get_with_ttl(
                    sys_id,
                    PAYLOAD_STATISTIC,
                    self._slow_metrics_interval_sec,
                    self.api.async_get_system_statistic,
                    {},
                ),
                _get_with_ttl(
                    sys_id,
                    PAYLOAD_ALARM,
                    self._slow_metrics_interval_sec,
                    self.api.async_get_alarm,
                    None,
                ),
                _get_with_ttl(
                    sys_id,
                    PAYLOAD_PV_TRENDS,
                    self._slow_metrics_interval_sec,
                    lambda sid: self.api.async_get_pv_trends(
                        sid,
                        **self._trend_query_kwargs(DATE_TYPE_DAY),
                    ),
                    {},
                ),
                _get_with_ttl(
                    sys_id,
                    self._app_period_section(APP_SECTION_PV_TRENDS, DATE_TYPE_WEEK),
                    self._slow_metrics_interval_sec,
                    lambda sid: self.api.async_get_pv_trends(
                        sid,
                        **self._trend_query_kwargs(DATE_TYPE_WEEK),
                    ),
                    {},
                ),
                _get_with_ttl(
                    sys_id,
                    self._app_period_section(APP_SECTION_PV_TRENDS, DATE_TYPE_MONTH),
                    self._slow_metrics_interval_sec,
                    lambda sid: self.api.async_get_pv_trends(
                        sid,
                        **self._trend_query_kwargs(DATE_TYPE_MONTH),
                    ),
                    {},
                ),
                _get_with_ttl(
                    sys_id,
                    self._app_period_section(APP_SECTION_PV_TRENDS, DATE_TYPE_YEAR),
                    self._slow_metrics_interval_sec,
                    lambda sid: self.api.async_get_pv_trends(
                        sid,
                        **self._trend_query_kwargs(DATE_TYPE_YEAR),
                    ),
                    {},
                ),
                _get_with_ttl(
                    sys_id,
                    PAYLOAD_HOME_TRENDS,
                    self._slow_metrics_interval_sec,
                    lambda sid: self.api.async_get_home_trends(
                        sid,
                        **self._trend_query_kwargs(DATE_TYPE_DAY),
                    ),
                    {},
                ),
                _get_with_ttl(
                    sys_id,
                    self._app_period_section(APP_SECTION_HOME_TRENDS, DATE_TYPE_WEEK),
                    self._slow_metrics_interval_sec,
                    lambda sid: self.api.async_get_home_trends(
                        sid,
                        **self._trend_query_kwargs(DATE_TYPE_WEEK),
                    ),
                    {},
                ),
                _get_with_ttl(
                    sys_id,
                    self._app_period_section(APP_SECTION_HOME_TRENDS, DATE_TYPE_MONTH),
                    self._slow_metrics_interval_sec,
                    lambda sid: self.api.async_get_home_trends(
                        sid,
                        **self._trend_query_kwargs(DATE_TYPE_MONTH),
                    ),
                    {},
                ),
                _get_with_ttl(
                    sys_id,
                    self._app_period_section(APP_SECTION_HOME_TRENDS, DATE_TYPE_YEAR),
                    self._slow_metrics_interval_sec,
                    lambda sid: self.api.async_get_home_trends(
                        sid,
                        **self._trend_query_kwargs(DATE_TYPE_YEAR),
                    ),
                    {},
                ),
                _get_with_ttl(
                    sys_id,
                    PAYLOAD_BATTERY_TRENDS,
                    self._slow_metrics_interval_sec,
                    lambda sid: self.api.async_get_battery_trends(
                        sid,
                        **self._trend_query_kwargs(DATE_TYPE_DAY),
                    ),
                    {},
                ),
                _get_with_ttl(
                    sys_id,
                    self._app_period_section(
                        APP_SECTION_BATTERY_TRENDS, DATE_TYPE_WEEK
                    ),
                    self._slow_metrics_interval_sec,
                    lambda sid: self.api.async_get_battery_trends(
                        sid,
                        **self._trend_query_kwargs(DATE_TYPE_WEEK),
                    ),
                    {},
                ),
                _get_with_ttl(
                    sys_id,
                    self._app_period_section(
                        APP_SECTION_BATTERY_TRENDS, DATE_TYPE_MONTH
                    ),
                    self._slow_metrics_interval_sec,
                    lambda sid: self.api.async_get_battery_trends(
                        sid,
                        **self._trend_query_kwargs(DATE_TYPE_MONTH),
                    ),
                    {},
                ),
                _get_with_ttl(
                    sys_id,
                    self._app_period_section(
                        APP_SECTION_BATTERY_TRENDS, DATE_TYPE_YEAR
                    ),
                    self._slow_metrics_interval_sec,
                    lambda sid: self.api.async_get_battery_trends(
                        sid,
                        **self._trend_query_kwargs(DATE_TYPE_YEAR),
                    ),
                    {},
                ),
                _get_with_ttl(
                    sys_id,
                    PAYLOAD_PRICE,
                    self._price_config_interval_sec,
                    self.api.async_get_power_price,
                    {},
                ),
                _get_with_ttl(
                    sys_id,
                    PAYLOAD_PRICE_SOURCES,
                    self._price_config_interval_sec,
                    self.api.async_get_price_sources,
                    [],
                ),
                _get_with_ttl(
                    sys_id,
                    PAYLOAD_PRICE_HISTORY_CONFIG,
                    self._price_config_interval_sec,
                    self.api.async_get_price_history_config,
                    {},
                ),
            )
            bundle: dict[str, Any] = {
                PAYLOAD_STATISTIC: statistic,
                PAYLOAD_ALARM: alarm,
                PAYLOAD_PV_TRENDS: pv_trends,
                self._app_period_section(
                    APP_SECTION_PV_TRENDS, DATE_TYPE_WEEK
                ): pv_trends_week,
                self._app_period_section(
                    APP_SECTION_PV_TRENDS, DATE_TYPE_MONTH
                ): pv_trends_month,
                self._app_period_section(
                    APP_SECTION_PV_TRENDS, DATE_TYPE_YEAR
                ): pv_trends_year,
                PAYLOAD_HOME_TRENDS: home_trends,
                self._app_period_section(
                    APP_SECTION_HOME_TRENDS, DATE_TYPE_WEEK
                ): home_trends_week,
                self._app_period_section(
                    APP_SECTION_HOME_TRENDS, DATE_TYPE_MONTH
                ): home_trends_month,
                self._app_period_section(
                    APP_SECTION_HOME_TRENDS, DATE_TYPE_YEAR
                ): home_trends_year,
                PAYLOAD_BATTERY_TRENDS: battery_trends,
                self._app_period_section(
                    APP_SECTION_BATTERY_TRENDS, DATE_TYPE_WEEK
                ): battery_trends_week,
                self._app_period_section(
                    APP_SECTION_BATTERY_TRENDS, DATE_TYPE_MONTH
                ): battery_trends_month,
                self._app_period_section(
                    APP_SECTION_BATTERY_TRENDS, DATE_TYPE_YEAR
                ): battery_trends_year,
                PAYLOAD_PRICE: price,
                PAYLOAD_PRICE_SOURCES: price_sources,
                PAYLOAD_PRICE_HISTORY_CONFIG: price_history_config,
            }
            month_history: dict[str, dict[int, dict[str, Any]]] = {}
            for prefix, stat_keys in self._SYSTEM_YEAR_BACKFILL_STAT_KEYS.items():
                if not self._needs_year_month_backfill(
                    bundle,
                    prefix,
                    stat_keys,
                    today=today,
                ):
                    continue
                current_month_section = self._app_period_section(
                    prefix,
                    DATE_TYPE_MONTH,
                )
                current_month_source = bundle.get(current_month_section)
                months: dict[int, dict[str, Any]] = {}
                if isinstance(current_month_source, dict):
                    months[today.month] = current_month_source
                if prefix == APP_SECTION_HOME_TRENDS:
                    previous_months = list(range(1, today.month))

                    async def _fetch_previous_home_month(
                        month: int,
                        section_prefix: str,
                    ) -> Any:
                        request_kwargs = app_month_request_kwargs(today.year, month)
                        return await _get_with_ttl(
                            sys_id,
                            f"{section_prefix}_{DATE_TYPE_MONTH}_{today.year}_{month:02d}",
                            self._price_config_interval_sec,
                            lambda sid: self.api.async_get_home_trends(
                                sid,
                                **request_kwargs,
                            ),
                            {},
                        )

                    sources = await asyncio.gather(
                        *(
                            _fetch_previous_home_month(month, prefix)
                            for month in previous_months
                        )
                    )
                    for month, source in zip(previous_months, sources, strict=False):
                        if isinstance(source, dict):
                            months[month] = source
                if months:
                    month_history[prefix] = months
            apply_year_month_backfill(bundle, month_history)
            system_cache[sys_id] = bundle
            return bundle

        async def _fetch_device_extras(
            dev_id: str,
            dev_sn: str | None,
            sys_id: str | None,
        ) -> dict[str, Any]:
            """Device-level slow metrics (deviceStatistic, OTA, location).

            deviceStatistic: changes on ~5 min boundary, like system stats.
            OTA + location: change practically never → hourly TTL.
            """
            per_dev_key = f"dev:{dev_id}"
            per_dev = self._slow_cache.setdefault(per_dev_key, {})

            task_names: list[str] = [PAYLOAD_DEVICE_STATISTIC, PAYLOAD_LOCATION]
            tasks = [
                _get_with_ttl_for(
                    per_dev,
                    PAYLOAD_DEVICE_STATISTIC,
                    self._slow_metrics_interval_sec,
                    lambda: self.api.async_get_device_statistic(dev_id),
                    {},
                ),
                _get_with_ttl_for(
                    per_dev,
                    PAYLOAD_LOCATION,
                    self._price_config_interval_sec,
                    lambda: self.api.async_get_location(dev_id),
                    {},
                ),
            ]

            for date_type in APP_PERIOD_DATE_TYPES:
                kwargs = self._trend_query_kwargs(date_type)
                pv_key = self._app_period_section(APP_SECTION_PV_STAT, date_type)
                battery_key = self._app_period_section(
                    APP_SECTION_BATTERY_STAT, date_type
                )
                home_key = self._app_period_section(APP_SECTION_HOME_STAT, date_type)
                if sys_id:
                    task_names.append(pv_key)
                    tasks.append(
                        _get_with_ttl_for(
                            per_dev,
                            pv_key,
                            self._slow_metrics_interval_sec,
                            lambda q=kwargs: self.api.async_get_device_pv_stat(
                                dev_id,
                                sys_id,
                                **q,
                            ),
                            {},
                        )
                    )
                task_names.append(battery_key)
                tasks.append(
                    _get_with_ttl_for(
                        per_dev,
                        battery_key,
                        self._slow_metrics_interval_sec,
                        lambda q=kwargs: self.api.async_get_device_battery_stat(
                            dev_id,
                            **q,
                        ),
                        {},
                    )
                )
                task_names.append(home_key)
                tasks.append(
                    _get_with_ttl_for(
                        per_dev,
                        home_key,
                        self._slow_metrics_interval_sec,
                        lambda q=kwargs: self.api.async_get_device_home_stat(
                            dev_id,
                            **q,
                        ),
                        {},
                    )
                )
            if dev_sn:
                # REST pack/list is slow and often returns null for SolarVault.
                # Live pack values are refreshed via MQTT subdevice queries.
                pack_interval_sec = self._slow_metrics_interval_sec
                task_names.append(PAYLOAD_OTA)
                tasks.append(
                    _get_with_ttl_for(
                        per_dev,
                        PAYLOAD_OTA,
                        self._price_config_interval_sec,
                        lambda: self.api.async_get_ota_info(dev_sn),
                        {},
                    )
                )
                task_names.append(PAYLOAD_BATTERY_PACKS)
                tasks.append(
                    _get_with_ttl_for(
                        per_dev,
                        PAYLOAD_BATTERY_PACKS,
                        pack_interval_sec,
                        lambda: self.api.async_get_battery_pack_list(dev_sn),
                        [],
                    )
                )
            values = await asyncio.gather(*tasks)
            out: dict[str, Any] = dict(zip(task_names, values, strict=False))
            out.setdefault(PAYLOAD_DEVICE_STATISTIC, {})
            out.setdefault(PAYLOAD_LOCATION, {})
            out.setdefault(PAYLOAD_OTA, {})
            out.setdefault(PAYLOAD_BATTERY_PACKS, [])

            packs = out.get(PAYLOAD_BATTERY_PACKS) or []
            if isinstance(packs, list) and packs:
                await self._async_enrich_battery_pack_ota(dev_id, packs, dev_sn)

            async def _fetch_device_month(
                prefix: str,
                month: int,
            ) -> dict[str, Any]:
                kwargs = app_month_request_kwargs(today.year, month)
                cache_key = f"{prefix}_{DATE_TYPE_MONTH}_{today.year}_{month:02d}"
                if prefix == APP_SECTION_PV_STAT:
                    if not sys_id:
                        return {}
                    return await _get_with_ttl_for(
                        per_dev,
                        cache_key,
                        self._price_config_interval_sec,
                        lambda q=kwargs: self.api.async_get_device_pv_stat(
                            dev_id,
                            sys_id,
                            **q,
                        ),
                        {},
                    )
                if prefix == APP_SECTION_BATTERY_STAT:
                    return await _get_with_ttl_for(
                        per_dev,
                        cache_key,
                        self._price_config_interval_sec,
                        lambda q=kwargs: self.api.async_get_device_battery_stat(
                            dev_id,
                            **q,
                        ),
                        {},
                    )
                if prefix == APP_SECTION_HOME_STAT:
                    return await _get_with_ttl_for(
                        per_dev,
                        cache_key,
                        self._price_config_interval_sec,
                        lambda q=kwargs: self.api.async_get_device_home_stat(
                            dev_id,
                            **q,
                        ),
                        {},
                    )
                return {}

            month_history: dict[str, dict[int, dict[str, Any]]] = {}
            for prefix, stat_keys in self._DEVICE_YEAR_BACKFILL_STAT_KEYS.items():
                if not self._needs_year_month_backfill(
                    out,
                    prefix,
                    stat_keys,
                    today=today,
                ):
                    continue
                current_month_section = self._app_period_section(
                    prefix,
                    DATE_TYPE_MONTH,
                )
                current_month_source = out.get(current_month_section)
                months: dict[int, dict[str, Any]] = {}
                if isinstance(current_month_source, dict):
                    months[today.month] = current_month_source
                previous_months = list(range(1, today.month))
                sources = await asyncio.gather(
                    *(_fetch_device_month(prefix, month) for month in previous_months)
                )
                for month, source in zip(previous_months, sources, strict=False):
                    if isinstance(source, dict):
                        months[month] = source
                if months:
                    month_history[prefix] = months
            apply_year_month_backfill(out, month_history)

            return out

        result: dict[str, dict[str, Any]] = {}
        invalid_device_ids: list[str] = []
        for dev_id, idx in self._device_index.items():
            try:
                payload = await self.api.async_get_device_property(dev_id)
            except JackeryAuthError as err:
                raise ConfigEntryAuthFailed(
                    "Jackery credentials were rejected during property refresh. "
                    "Re-authentication is required."
                ) from err
            except JackeryError as err:
                if "code=20000" in str(err):
                    invalid_device_ids.append(dev_id)
                _LOGGER.warning("property fetch failed for %s: %s", dev_id, err)
                if self.data and dev_id in self.data:
                    result[dev_id] = self.data[dev_id]
                continue

            # Pull SN from either the fresh property payload or the discovery
            # metadata — needed for the OTA endpoint (which keys on SN).
            dev_sn = (payload.get(PAYLOAD_DEVICE) or {}).get(FIELD_DEVICE_SN) or (
                idx.get(PAYLOAD_DEVICE_META) or {}
            ).get(FIELD_DEVICE_SN)
            sys_id = str(idx.get(FIELD_SYSTEM_ID)) if idx.get(FIELD_SYSTEM_ID) else None
            try:
                extras = await _fetch_device_extras(
                    dev_id,
                    dev_sn,
                    sys_id,
                )
            except JackeryAuthError as err:
                raise ConfigEntryAuthFailed(
                    "Jackery credentials were rejected while fetching extended device data. "
                    "Re-authentication is required."
                ) from err

            old_entry = {}
            if self.data:
                old_entry = self.data.get(dev_id) or {}
            http_props = self._sanitize_main_properties(
                payload.get(PAYLOAD_PROPERTIES) or {}
            )
            merged_props = self._merge_main_properties(
                old_entry.get(PAYLOAD_PROPERTIES) or {},
                http_props,
            )

            extra_packs = extras.get(PAYLOAD_BATTERY_PACKS) or []
            old_packs = old_entry.get(PAYLOAD_BATTERY_PACKS) or []
            if extra_packs:
                battery_packs = self._merge_battery_pack_lists(old_packs, extra_packs)
            elif isinstance(old_packs, list):
                battery_packs = old_packs
            else:
                battery_packs = []
            if battery_packs:
                await self._async_enrich_battery_pack_ota(
                    dev_id,
                    battery_packs,
                    dev_sn,
                )

            period_payloads = {
                self._app_period_section(prefix, date_type): extras.get(
                    self._app_period_section(prefix, date_type)
                )
                or {}
                for prefix in (
                    APP_SECTION_PV_STAT,
                    APP_SECTION_BATTERY_STAT,
                    APP_SECTION_HOME_STAT,
                )
                for date_type in APP_PERIOD_DATE_TYPES
            }
            entry: dict[str, Any] = {
                PAYLOAD_DEVICE: payload.get(PAYLOAD_DEVICE) or {},
                PAYLOAD_PROPERTIES: merged_props,
                PAYLOAD_HTTP_PROPERTIES: http_props,
                PAYLOAD_SYSTEM: idx.get(PAYLOAD_SYSTEM_META) or {},
                PAYLOAD_DISCOVERY: idx.get(PAYLOAD_DEVICE_META) or {},
                PAYLOAD_DEVICE_STATISTIC: extras.get(PAYLOAD_DEVICE_STATISTIC) or {},
                **period_payloads,
                PAYLOAD_OTA: extras.get(PAYLOAD_OTA) or {},
                PAYLOAD_LOCATION: extras.get(PAYLOAD_LOCATION) or {},
                PAYLOAD_BATTERY_PACKS: battery_packs,
            }
            for cached_key in PRESERVED_FAST_PAYLOAD_KEYS:
                if cached_key in old_entry:
                    entry[cached_key] = old_entry[cached_key]
            if sys_id:
                try:
                    sys_data = await _fetch_system(sys_id)
                except JackeryAuthError as err:
                    raise ConfigEntryAuthFailed(
                        "Jackery credentials were rejected while fetching system data. "
                        "Re-authentication is required."
                    ) from err
                entry.update(sys_data)
            override = self._price_overrides.get(dev_id)
            if override:
                override_ts, price_updates = override
                if time.monotonic() - override_ts < self._PRICE_OVERRIDE_TTL_SEC:
                    entry[PAYLOAD_PRICE] = self._merge_dict_values(
                        entry.get(PAYLOAD_PRICE) or {},
                        price_updates,
                    )
                else:
                    self._price_overrides.pop(dev_id, None)
            guard_statistic_totals_from_year(entry)
            quality_warnings = app_data_quality_warnings(entry, today=today)
            if quality_warnings:
                entry[PAYLOAD_DATA_QUALITY] = [
                    warning.as_dict() for warning in quality_warnings
                ]
            result[dev_id] = entry

        if invalid_device_ids and _retry_discovery_once:
            _LOGGER.info(
                "Jackery: dropping %d invalid device id(s) from discovery and retrying",
                len(invalid_device_ids),
            )
            for dev_id in invalid_device_ids:
                self._device_index.pop(dev_id, None)
            if not self._device_index:
                await self.async_discover()
            return await self._async_update_data(_retry_discovery_once=False)

        if self._mqtt is not None and (
            self.api.mqtt_fingerprint != self._mqtt_fingerprint
            or not self._mqtt.is_connected
        ):
            await self._async_ensure_mqtt()
        await self._async_update_data_quality_issue(result)
        await self._async_import_app_chart_statistics(result)
        self._schedule_mqtt_backfill_queries(result)
        # Drain queued device-registry removals from the stale-pack
        # cleanup. Fire-and-forget on the same task so a registry
        # hiccup does not break the data refresh.
        if self._pending_device_removals:
            try:
                await self.async_cleanup_pending_device_removals()
            except Exception as err:
                _LOGGER.debug("Jackery: device-registry cleanup deferred: %s", err)
        return result

    @property
    def mqtt_diagnostics(self) -> dict[str, Any]:
        """Return the MQTT client diagnostics block for the diagnostics export."""
        if self._mqtt is None:
            return {"enabled": False}
        diag = dict(self._mqtt.diagnostics)
        diag["enabled"] = True
        diag["credential_mac_id_source"] = self.api.mqtt_mac_id_source
        if self.api.mqtt_mac_id:
            diag["credential_mac_id_suffix"] = self.api.mqtt_mac_id[-6:]
        diag["slow_metrics_interval_seconds"] = self._slow_metrics_interval_sec
        diag["price_interval_seconds"] = self._price_config_interval_sec
        diag["subdevice_query_interval_seconds"] = self._subdevice_query_interval_sec
        diag["fixed_rate_polling_seconds"] = int(
            self._configured_update_interval.total_seconds()
        )
        diag["tls_certificate_verification"] = "enabled"
        diag["tls_insecure_warning"] = None
        diag["skipped_refresh_ticks"] = self._skipped_refresh_ticks
        diag["stale_battery_packs_dropped"] = self._stale_battery_packs_dropped
        return diag
