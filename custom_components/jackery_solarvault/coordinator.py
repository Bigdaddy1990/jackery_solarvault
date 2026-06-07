"""DataUpdateCoordinator for Jackery SolarVault."""

import asyncio
import binascii
import contextlib
from dataclasses import dataclass, field as dataclass_field
from datetime import date, datetime, timedelta
import importlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any, ClassVar, NoReturn, cast

from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .client import JackeryAuthError, JackeryError
from .const import (
    ACTION_ID_AUTO_STANDBY,
    ACTION_ID_BIND_SMART_PART,
    ACTION_ID_CONTROL_SOCKET_PRIORITY,
    ACTION_ID_CONTROL_SOCKET_SWITCH,
    ACTION_ID_CT_PHASE,
    ACTION_ID_DEFAULT_PW,
    ACTION_ID_DELETE_STORM_ALERT,
    ACTION_ID_DEVICE_GET_OTA_PAGE_DATA,
    ACTION_ID_EPS_ENABLED,
    ACTION_ID_FOLLOW_METER_PW,
    ACTION_ID_GET_DEVICE_OTA_VERSION,
    ACTION_ID_GET_TIME_ZONE,
    ACTION_ID_MAX_FEED_GRID,
    ACTION_ID_MAX_OUT_PW,
    ACTION_ID_NOTIFY_DEVICE_CAN_OTA,
    ACTION_ID_NOTIFY_DEVICE_OTA_TOTAL_PAGE,
    ACTION_ID_OFF_GRID_DOWN,
    ACTION_ID_OFF_GRID_TIME,
    ACTION_ID_QUERY_COMBINE_DATA,
    ACTION_ID_QUERY_DEVICE_PROPERTY,
    ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
    ACTION_ID_QUERY_WEATHER_PLAN,
    ACTION_ID_QUERY_WIFI_CONFIG,
    ACTION_ID_READ_WIFI_LIST,
    ACTION_ID_REBOOT_DEVICE,
    ACTION_ID_SEND_TIME_ZONE,
    ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG,
    ACTION_ID_SOC_LIMITS,
    ACTION_ID_STANDBY,
    ACTION_ID_STORM_MINUTES,
    ACTION_ID_STORM_WARNING,
    ACTION_ID_SUBDEVICE_3014,
    ACTION_ID_SUBDEVICE_3031,
    ACTION_ID_SUBDEVICE_3032,
    ACTION_ID_SUBDEVICE_3033,
    ACTION_ID_SUBDEVICE_3037,
    ACTION_ID_SYNC_MQTT_CONNECT_INFO,
    ACTION_ID_TEMP_UNIT,
    ACTION_ID_TIMER_TASK_READ,
    ACTION_ID_UNBIND_SMART_PART,
    ACTION_ID_WORK_MODEL,
    ADAPTIVE_KEEPALIVE_INTERVAL_SEC,
    APP_CHART_STAT_METRICS,
    APP_CHART_STAT_PERIODS,
    APP_DAY_CHART_BUCKET_LABEL,
    APP_PERIOD_DATE_TYPES,
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_BATTERY_TRENDS,
    APP_SECTION_CT_STAT,
    APP_SECTION_EPS_STAT,
    APP_SECTION_HOME_STAT,
    APP_SECTION_HOME_TRENDS,
    APP_SECTION_PV_STAT,
    APP_SECTION_PV_TRENDS,
    APP_SECTION_TODAY_ENERGY,
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
    APP_STAT_TOTAL_TREND_CHARGE_ENERGY,
    APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY,
    BATTERY_PACK_HINT_KEYS,
    BATTERY_PACK_STALE_THRESHOLD_SEC,
    CONF_ENABLE_BLE_TRANSPORT,
    CONF_ENABLE_BLE_WRITES,
    CT_METER_KEYS,
    DATA_QUALITY_KEY_LABEL,
    DATA_QUALITY_KEY_METRIC_KEY,
    DATA_QUALITY_REPAIR_EXAMPLE_LIMIT,
    DATE_TYPE_DAY,
    DATE_TYPE_HOUR,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    DEFAULT_ENABLE_BLE_TRANSPORT,
    DEFAULT_ENABLE_BLE_WRITES,
    DIAGNOSTICS_SCHEMA_VERSION,
    DOMAIN,
    EXTERNAL_STAT_BUCKET_DAY_HOURLY,
    FIELD_ACCESSORIES,
    FIELD_ACC_CT_BODY,
    FIELD_ACTION_ID,
    FIELD_AUTO_STANDBY,
    FIELD_BATTERIES,
    FIELD_BATTERY_PACK,
    FIELD_BATTERY_PACKS,
    FIELD_BATTERY_PACK_LIST,
    FIELD_BAT_NUM,
    FIELD_BAT_SOC,
    FIELD_BIND_KEY,
    FIELD_BLUETOOTH_KEY,
    FIELD_BODY,
    FIELD_CELL_TEMP,
    FIELD_CHARGING_ENERGY,
    FIELD_CID,
    FIELD_CMD,
    FIELD_COLLECTORS,
    FIELD_COMM_STATE,
    FIELD_COMPANY_NAME,
    FIELD_COUNTRY,
    FIELD_COUNTRY_CODE,
    FIELD_CURRENCY,
    FIELD_CURRENCY_CODE,
    FIELD_CURRENT_VERSION,
    FIELD_DATA,
    FIELD_DEFAULT_PW,
    FIELD_DEVICES,
    FIELD_DEVICE_ID,
    FIELD_DEVICE_NAME,
    FIELD_DEVICE_SN,
    FIELD_DEVICE_TYPE,
    FIELD_DEV_ID,
    FIELD_DEV_MODEL,
    FIELD_DEV_SN,
    FIELD_DEV_TYPE,
    FIELD_DISCHARGING_ENERGY,
    FIELD_DYNAMIC_OR_SINGLE,
    FIELD_ID,
    FIELD_IN_EGY,
    FIELD_IN_PW,
    FIELD_IP,
    FIELD_IS_AUTO_STANDBY,
    FIELD_IS_CLOUD,
    FIELD_IS_FIRMWARE_UPGRADE,
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
    FIELD_OUT_EGY,
    FIELD_OUT_PW,
    FIELD_PACK_LIST,
    FIELD_PLATFORM_COMPANY_ID,
    FIELD_PLUGS,
    FIELD_POWER_PRICE_RESOURCE,
    FIELD_PRODUCT_MODEL,
    FIELD_RB,
    FIELD_REBOOT,
    FIELD_SCAN_NAME,
    FIELD_SCHE_PHASE,
    FIELD_SINGLE_CURRENCY,
    FIELD_SINGLE_CURRENCY_CODE,
    FIELD_SINGLE_PRICE,
    FIELD_SN,
    FIELD_SOCKET_PRIORITY,
    FIELD_SOC_CHARGE_LIMIT,
    FIELD_SOC_CHG_LIMIT,
    FIELD_SOC_DISCHARGE_LIMIT,
    FIELD_SOC_DISCHG_LIMIT,
    FIELD_STATUS,
    FIELD_SUB_TYPE,
    FIELD_SWITCH_STATE,
    FIELD_SW_EPS,
    FIELD_SYSTEM_ID,
    FIELD_SYSTEM_REGION,
    FIELD_SYS_SWITCH,
    FIELD_TARGET_MODULE_VERSION,
    FIELD_TARGET_VERSION,
    FIELD_TEMP_UNIT,
    FIELD_THIRD_PARTY_MQTT_ENABLE,
    FIELD_THIRD_PARTY_MQTT_IP,
    FIELD_THIRD_PARTY_MQTT_PASSWORD,
    FIELD_THIRD_PARTY_MQTT_PORT,
    FIELD_THIRD_PARTY_MQTT_TOKEN,
    FIELD_THIRD_PARTY_MQTT_USERNAME,
    FIELD_TIMESTAMP,
    FIELD_TODAY_ENERGY,
    FIELD_TOTAL_ENERGY,
    FIELD_TYPE_NAME,
    FIELD_UPDATES,
    FIELD_UPDATE_CONTENT,
    FIELD_UPDATE_STATUS,
    FIELD_UPGRADE_TYPE,
    FIELD_VERSION,
    FIELD_WNAME,
    FIELD_WORK_MODEL,
    FIELD_WPC,
    FIELD_WPS,
    MAIN_PROPERTY_ALIAS_PAIRS,
    MQTT_ACTION_IDS_ALARM,
    MQTT_ACTION_IDS_COMBINE,
    MQTT_ACTION_IDS_DEVICE_PROPERTY,
    MQTT_ACTION_IDS_SCHEDULE,
    MQTT_ACTION_IDS_SUBDEVICE,
    MQTT_APP_CONFLICT_PAUSE_SEC,
    MQTT_CMD_BIND_SMART_PART,
    MQTT_CMD_CONTROL_COMBINE,
    MQTT_CMD_CONTROL_SUB_DEVICE,
    MQTT_CMD_DEVICE_GET_OTA_PAGE_DATA,
    MQTT_CMD_DEVICE_PROPERTY_CHANGE,
    MQTT_CMD_DOWNLOAD_DEVICE_SCHEDULE,
    MQTT_CMD_GET_DEVICE_OTA_VERSION,
    MQTT_CMD_GET_TIME_ZONE,
    MQTT_CMD_NONE,
    MQTT_CMD_NOTIFY_DEVICE_CAN_OTA,
    MQTT_CMD_NOTIFY_DEVICE_OTA_TOTAL_PAGE,
    MQTT_CMD_QUERY_COMBINE_DATA,
    MQTT_CMD_QUERY_DEVICE_PROPERTY,
    MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
    MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG,
    MQTT_CMD_QUERY_WEATHER_PLAN,
    MQTT_CMD_QUERY_WIFI_CONFIG,
    MQTT_CMD_READ_WIFI_LIST,
    MQTT_CMD_SEND_TIME_ZONE,
    MQTT_CMD_SYNC_MQTT_CONNECT_INFO,
    MQTT_CMD_THIRD_PARTY_MQTT_CONFIG,
    MQTT_CMD_UNBIND_SMART_PART,
    MQTT_CMD_UPLOAD_DEVICE_ALERT,
    MQTT_CREDENTIAL_CLIENT_ID,
    MQTT_CREDENTIAL_PASSWORD,
    MQTT_CREDENTIAL_USERNAME,
    MQTT_CREDENTIAL_USER_ID,
    MQTT_LIVE_THRESHOLD_SEC,
    MQTT_MESSAGE_BIND_SMART_ACCESSORY,
    MQTT_MESSAGE_CANCEL_WEATHER_ALERT,
    MQTT_MESSAGE_CONTROL_COMBINE,
    MQTT_MESSAGE_CONTROL_SUB_DEVICE,
    MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
    MQTT_MESSAGE_DOWNLOAD_DEVICE_SCHEDULE,
    MQTT_MESSAGE_QUERY_COMBINE_DATA,
    MQTT_MESSAGE_QUERY_DEVICE_PROPERTY,
    MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
    MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG,
    MQTT_MESSAGE_QUERY_WEATHER_PLAN,
    MQTT_MESSAGE_QUERY_WIFI_CONFIG,
    MQTT_MESSAGE_REMOVE_SMART_ACCESSORY,
    MQTT_MESSAGE_SEND_WEATHER_ALERT,
    MQTT_MESSAGE_THIRD_PARTY_MQTT_CONFIG,
    MQTT_MESSAGE_UPLOAD_COMBINE_DATA,
    MQTT_MESSAGE_UPLOAD_DEVICE_ALERT,
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
    PAYLOAD_DEBUG_LOGGER_NAME,
    PAYLOAD_DEBUG_LOG_FILENAME,
    PAYLOAD_DEBUG_THROTTLE_SEC,
    PAYLOAD_DEVICE,
    PAYLOAD_DEVICE_META,
    PAYLOAD_DEVICE_STATISTIC,
    PAYLOAD_DISCOVERY,
    PAYLOAD_HOME_TRENDS,
    PAYLOAD_HTTP_PROPERTIES,
    PAYLOAD_LOCATION,
    PAYLOAD_METER_HEADS,
    PAYLOAD_MQTT_LAST,
    PAYLOAD_NOTICE,
    PAYLOAD_OTA,
    PAYLOAD_PRICE,
    PAYLOAD_PRICE_HISTORY_CONFIG,
    PAYLOAD_PRICE_SOURCES,
    PAYLOAD_PROPERTIES,
    PAYLOAD_PV_TRENDS,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_STATISTIC,
    PAYLOAD_SYSTEM,
    PAYLOAD_SYSTEM_META,
    PAYLOAD_TASK_PLAN,
    PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
    PAYLOAD_WEATHER_PLAN,
    PRESERVED_FAST_PAYLOAD_KEYS,
    PRICE_CONFIG_INTERVAL_SEC,
    REPAIR_ISSUE_APP_DATA_INCONSISTENCY,
    REPAIR_TRANSLATION_APP_DATA_INCONSISTENCY,
    SLOW_METRICS_INTERVAL_SEC,
    SMART_METER_SUBTYPE,
    SUBDEVICE_DEV_TYPE_BATTERY_PACK,
    SUBDEVICE_DEV_TYPE_COMBO,
    SUBDEVICE_DEV_TYPE_CT,
    SUBDEVICE_DEV_TYPE_METER_HEAD,
    SUBDEVICE_DEV_TYPE_SOCKET,
    SUBDEVICE_HINT_KEYS,
    SUBDEVICE_MAIN_MIRROR_KEYS,
    SUBDEVICE_ONLY_PROPERTY_KEYS,
    SUBDEVICE_TYPE_SMART_METER,
    SYSTEM_INFO_KEYS,
)
from .ingest import merge_live_properties

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from homeassistant.components.recorder.models import StatisticData
    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .client import JackeryApi, JackeryMqttPushClient
    from .client.ble_transport import BleFrameObservation

import operator

from .util import (
    app_data_quality_warnings,
    app_month_request_kwargs,
    app_period_request_kwargs,
    app_year_request_kwargs,
    append_payload_debug_line,
    apply_year_month_backfill,
    chart_series_debug,
    config_entry_bool_option,
    day_power_energy_points,
    dev_mode_redactions_disabled,
    diagnostic_redactions_disabled,
    external_trend_statistic_id,
    format_data_quality_warning,
    guard_statistic_totals_from_year,
    normalized_data_quality_warnings,
    parse_utc_datetime,
    safe_float,
    trend_series_points,
    utc_now,
    year_payload_appears_current_month_only,
)

_LOGGER = logging.getLogger(__name__)
_PAYLOAD_DEBUG_LOGGER = logging.getLogger(PAYLOAD_DEBUG_LOGGER_NAME)


@dataclass
class RejectionMetrics:
    """Runtime rejection counters exported through diagnostics."""

    http_auth_rejections: int = 0
    mqtt_broker_rejections: int = 0
    payload_validation_rejections: int = 0
    schema_rejections: int = 0
    timestamp_skew_rejections: int = 0
    auth_token_expiry_rejections: int = 0
    last_rejection: dict[str, str] | None = None
    _seen: set[tuple[str, str]] = dataclass_field(default_factory=set, repr=False)

    def increment(self, counter: str, reason: str) -> None:
        """Increment one counter and remember the latest rejection."""
        key = (counter, reason)
        if key in self._seen:
            return
        self._seen.add(key)
        setattr(self, counter, getattr(self, counter) + 1)
        self.last_rejection = {
            "counter": counter,
            "reason": reason,
            "at": dt_util.utcnow().isoformat(),
        }

    def as_dict(self) -> dict[str, Any]:
        """Return diagnostics payload for rejection counters."""
        return {
            "schema_version": DIAGNOSTICS_SCHEMA_VERSION,
            "counters": {
                "http_auth_rejections": self.http_auth_rejections,
                "mqtt_broker_rejections": self.mqtt_broker_rejections,
                "payload_validation_rejections": self.payload_validation_rejections,
                "schema_rejections": self.schema_rejections,
                "timestamp_skew_rejections": self.timestamp_skew_rejections,
                "auth_token_expiry_rejections": self.auth_token_expiry_rejections,
            },
            "last_rejection": self.last_rejection,
        }


_STATISTICS_BACKFILL_STORE_VERSION = 1
_STATISTICS_BACKFILL_STORE_KEY = "statistics_backfill"


def _load_mqtt_push_client() -> Any:  # noqa: ANN401, RUF100
    """Import the optional MQTT client module outside the event loop."""
    module = importlib.import_module(".client.mqtt_push", __package__)
    return module.JackeryMqttPushClient


_STATISTICS_BACKFILL_STORE_DEVICES = "devices"
_STATISTICS_BACKFILL_LAST_SUCCESS = "last_successful_import_date"
_STATISTICS_BACKFILL_LAST_REPAIR = "last_repair_date"
_STATISTICS_BACKFILL_LAST_REPAIRED_BUCKETS = "last_repaired_bucket_count"
_STATISTICS_BACKFILL_LAST_FAILED_BUCKETS = "last_failed_bucket_count"
_STATISTICS_BACKFILL_LAST_ERROR = "last_error"
_STATISTICS_BACKFILL_EXTERNAL_REPAIR_VERSION = "external_statistics_repair_version"
_EXTERNAL_STATISTICS_REPAIR_VERSION = 1
_STATISTICS_BACKFILL_ENTITY_REPAIR_VERSION = "entity_statistics_repair_version"
_ENTITY_STATISTICS_REPAIR_VERSION = 3


_ENTITY_STATISTIC_KEY_BY_METRIC_PERIOD = {
    "pv_energy": {
        DATE_TYPE_DAY: "device_today_pv_energy",
        DATE_TYPE_WEEK: "pv_week_energy",
        DATE_TYPE_MONTH: "pv_month_energy",
        DATE_TYPE_YEAR: "pv_year_energy",
    },
    "pv1_energy": {
        DATE_TYPE_DAY: "device_pv1_day_energy",
        DATE_TYPE_WEEK: "device_pv1_week_energy",
        DATE_TYPE_MONTH: "device_pv1_month_energy",
        DATE_TYPE_YEAR: "device_pv1_year_energy",
    },
    "pv2_energy": {
        DATE_TYPE_DAY: "device_pv2_day_energy",
        DATE_TYPE_WEEK: "device_pv2_week_energy",
        DATE_TYPE_MONTH: "device_pv2_month_energy",
        DATE_TYPE_YEAR: "device_pv2_year_energy",
    },
    "pv3_energy": {
        DATE_TYPE_DAY: "device_pv3_day_energy",
        DATE_TYPE_WEEK: "device_pv3_week_energy",
        DATE_TYPE_MONTH: "device_pv3_month_energy",
        DATE_TYPE_YEAR: "device_pv3_year_energy",
    },
    "pv4_energy": {
        DATE_TYPE_DAY: "device_pv4_day_energy",
        DATE_TYPE_WEEK: "device_pv4_week_energy",
        DATE_TYPE_MONTH: "device_pv4_month_energy",
        DATE_TYPE_YEAR: "device_pv4_year_energy",
    },
    "device_ongrid_input_energy": {
        DATE_TYPE_DAY: "device_today_ongrid_input",
        DATE_TYPE_WEEK: "device_ongrid_input_week_energy",
        DATE_TYPE_MONTH: "device_ongrid_input_month_energy",
        DATE_TYPE_YEAR: "device_ongrid_input_year_energy",
    },
    "device_ongrid_output_energy": {
        DATE_TYPE_DAY: "device_today_ongrid_output",
        DATE_TYPE_WEEK: "device_ongrid_output_week_energy",
        DATE_TYPE_MONTH: "device_ongrid_output_month_energy",
        DATE_TYPE_YEAR: "device_ongrid_output_year_energy",
    },
    "battery_charge_energy": {
        DATE_TYPE_DAY: "device_today_battery_charge",
        DATE_TYPE_WEEK: "battery_charge_week_energy",
        DATE_TYPE_MONTH: "battery_charge_month_energy",
        DATE_TYPE_YEAR: "battery_charge_year_energy",
    },
    "battery_discharge_energy": {
        DATE_TYPE_DAY: "device_today_battery_discharge",
        DATE_TYPE_WEEK: "battery_discharge_week_energy",
        DATE_TYPE_MONTH: "battery_discharge_month_energy",
        DATE_TYPE_YEAR: "battery_discharge_year_energy",
    },
    "home_energy": {
        DATE_TYPE_DAY: "today_load",
        DATE_TYPE_WEEK: "home_week_energy",
        DATE_TYPE_MONTH: "home_month_energy",
        DATE_TYPE_YEAR: "home_year_energy",
    },
}

_DAY_TREND_SOURCE_BY_METRIC_KEY = {
    "pv_energy": (PAYLOAD_PV_TRENDS, APP_STAT_TOTAL_SOLAR_ENERGY),
    "battery_charge_energy": (
        PAYLOAD_BATTERY_TRENDS,
        APP_STAT_TOTAL_TREND_CHARGE_ENERGY,
    ),
    "battery_discharge_energy": (
        PAYLOAD_BATTERY_TRENDS,
        APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY,
    ),
    "home_energy": (PAYLOAD_HOME_TRENDS, APP_STAT_TOTAL_HOME_ENERGY),
}


def _stable_payload_debug_signature(event: dict[str, Any]) -> str:
    """Return a content-only signature for payload-debug dedup.

    Per-message identifiers (``id``, ``timestamp``, ``messageId``) and
    the optional ``entry_id`` annotation change for every record but
    do not represent new information about the device. They are
    excluded from the signature so a stream of identical telemetry
    payloads collapses into one log line per actually-changed value.
    """
    payload = event.get("payload") or {}
    body = payload.get(FIELD_BODY) if isinstance(payload, dict) else None
    if isinstance(body, dict):
        body_sig: Any = {k: v for k, v in body.items() if k != "messageId"}
    else:
        body_sig = body
    response = (
        event.get("response") if isinstance(event.get("response"), dict) else None
    )
    response_data = response.get(FIELD_DATA) if response is not None else None
    return json.dumps(
        [
            event.get("kind"),
            event.get("topic") or event.get("path"),
            payload.get(FIELD_MESSAGE_TYPE) if isinstance(payload, dict) else None,
            body_sig,
            event.get("body_type"),
            event.get("data_type"),
            event.get("response_data_type"),
            event.get(FIELD_STATUS),
            response_data,
        ],
        sort_keys=True,
        default=str,
    )


def _raise_config_entry_auth_failed(message: str, err: JackeryAuthError) -> NoReturn:
    """Raise HA reauth trigger for rejected Jackery credentials."""
    raise ConfigEntryAuthFailed(f"{message}. Re-authentication is required.") from err  # noqa: TRY003


class JackerySolarVaultCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):  # noqa: PLR0904
    """Polls all known Jackery devices.

    Architecture
    ------------
    The coordinator runs three parallel data paths and merges them into the
    single ``data`` dict that HA platforms consume:

    1. **HTTP polling** (``_async_update_data``) refreshes device properties
       on the configured interval and caches slow per-system metrics (alarms,
       statistics, weather plan, price config, period trends) behind TTL
       windows so we do not hammer the cloud faster than it updates.
    2. **MQTT push** (``_async_handle_mqtt_message``) merges UploadCombineData,
       DevicePropertyChange, weather and subdevice telemetry into the same
       per-device payload as the HTTP path. When MQTT is live the coordinator
       may skip only the fast HTTP property fetch
       (``_should_skip_fast_property_fetch``); slow HTTP statistics keep their
       own cadence.
    3. **Optimistic local patches** (``_apply_local_*``) apply user-driven
       writes (``async_set_*``) to the cached payload immediately so the UI
       reflects the change before the cloud confirms; a short TTL window
       protects them from a stale HTTP refresh that would otherwise overwrite
       them with the pre-write value.

    Battery packs are merged from MQTT subdevice frames and HTTP responses
    (``_merge_battery_pack_lists``), stamped with ``_last_seen_at``, and
    aged out via ``_drop_stale_battery_packs`` once the threshold expires.
    Dropped pack indices feed ``async_cleanup_pending_device_removals`` to
    keep HA's device registry in sync (Quality-Scale Gold dynamic-devices).

    Service-action and entity setters dispatch through ``_async_publish_command``
    which targets MQTT first and falls back to HTTP. All paths surface
    ``ConfigEntryAuthFailed`` on credential rejection so HA opens the reauth
    flow without removing the entry.

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
    """  # noqa: E501, RUF100

    _PRICE_OVERRIDE_TTL_SEC = 600
    _PROPERTY_OVERRIDE_TTL_SEC = 120

    _CT_METER_KEYS = CT_METER_KEYS
    _SUBDEVICE_HINT_KEYS = SUBDEVICE_HINT_KEYS
    _SUBDEVICE_ONLY_PROPERTY_KEYS = SUBDEVICE_ONLY_PROPERTY_KEYS
    _SUBDEVICE_MAIN_MIRROR_KEYS = SUBDEVICE_MAIN_MIRROR_KEYS
    _SYSTEM_INFO_KEYS = SYSTEM_INFO_KEYS
    _BATTERY_PACK_HINT_KEYS = BATTERY_PACK_HINT_KEYS
    _MAIN_PROPERTY_ALIAS_PAIRS = MAIN_PROPERTY_ALIAS_PAIRS
    _BATTERY_PACK_LIVE_KEYS = frozenset({FIELD_BAT_SOC, FIELD_CELL_TEMP})
    _DEVICE_YEAR_BACKFILL_STAT_KEYS: ClassVar[dict[str, tuple[str, ...]]] = {
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
    _SYSTEM_YEAR_BACKFILL_STAT_KEYS: ClassVar[dict[str, tuple[str, ...]]] = {
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
            update_interval=update_interval,
        )
        self.api = api
        self.api.payload_debug_callback = self._async_payload_debug_event
        self.api.auth_rejection_callback = self.record_http_auth_rejection
        self.entry = entry
        self.rejection_metrics = RejectionMetrics()
        self._configured_update_interval = update_interval
        interval_sec = max(15, int(update_interval.total_seconds()))
        # Fast property polling should follow the configured interval, but
        # server-side slow endpoints (stats/trends/price) should keep their
        # own cadence to avoid long update cycles.
        self._slow_metrics_interval_sec = max(SLOW_METRICS_INTERVAL_SEC, interval_sec)
        self._price_config_interval_sec = max(PRICE_CONFIG_INTERVAL_SEC, interval_sec)
        self._last_discovery_refresh_monotonic: float = float("-inf")

        # Mapping deviceId -> {systemId, system_meta, device_meta}
        self._device_index: dict[str, dict[str, Any]] = {}
        self._device_update_received_at: dict[str, datetime] = {}

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
        # Set when a background HTTP/auth path proves that the configured
        # Jackery credentials are genuinely invalid. MQTT-only broker
        # rejections are handled as app-conflict pauses instead; they must not
        # stop HTTP polling.
        self._mqtt_auth_failure_message: str | None = None
        # Monotonic deadline (``time.monotonic()`` clock) until which the
        # coordinator skips MQTT reconnect attempts because the broker most
        # likely rotated credentials behind a competing Jackery-app session.
        # See MQTT_APP_CONFLICT_PAUSE_SEC for the rationale. While paused the
        # integration runs on HTTP polling only; the next probe attempt happens
        # automatically once the deadline elapses.
        self._mqtt_paused_until_monotonic: float = 0.0
        # Counter for consecutive pause cycles that did not lead to a
        # successful broker handshake. This is diagnostic only: MQTT-only
        # rejections are not allowed to trigger reauth because the official
        # Jackery app can rotate broker sessions while HTTP polling remains
        # valid.
        self._mqtt_app_conflict_pause_cycles: int = 0
        self._last_weather_plan_query: dict[str, float] = {}
        self._weather_plan_query_interval_sec = 180
        self._last_system_info_query: dict[str, float] = {}
        self._system_info_query_interval_sec = 180
        self._last_subdevice_query: dict[str, float] = {}
        # App-side MQTT subdevices must follow the user's polling interval, not
        # the slow statistic cadence.
        self._subdevice_query_interval_sec = interval_sec
        self._price_overrides: dict[str, tuple[float, dict[str, Any]]] = {}
        self._property_overrides: dict[str, tuple[float, dict[str, Any]]] = {}
        # Local plaintext mirror of the third-party MQTT bridge config per
        # device. The device never echoes back plaintext credentials
        # (password/token), so the UI text/number entities read what the user
        # last entered from here rather than from the redacted data payload.
        self._third_party_mqtt_plaintext: dict[str, dict[str, Any]] = {}
        self._invalid_device_id_counts: dict[str, int] = {}
        self._mqtt_backfill_task: asyncio.Task[None] | None = None
        self._statistics_import_task: asyncio.Task[None] | None = None
        self._statistics_import_ready = False
        self._battery_pack_ota_tasks: dict[str, asyncio.Task[None]] = {}
        # Experimental BLE transport (Phase 3a — gated by
        # CONF_ENABLE_BLE_TRANSPORT). Typed as ``Any`` so the coordinator
        # module imports cleanly on hosts without BlueZ / bleak.
        self._ble_listener: Any = None
        self._skipped_refresh_ticks = 0
        # Adaptive polling: when MQTT push is live, fast HTTP refreshes are
        # short-circuited and a full HTTP refresh only runs every keep-alive
        # window. Initialise to ``-inf`` so the first coordinator refresh after
        # setup always runs and primes the interval bookkeeping.
        self._last_http_refresh_completed_monotonic: float = float("-inf")
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
        # Throttle recorder-statistics import to slow-metric cadence so the
        # recorder is not invoked on every fast HTTP refresh. The first
        # import runs after platforms are set up so setup is not blocked by
        # historical week/month/year recovery.
        self._last_stat_import_monotonic: float = float("-inf")
        # Throttle the once-per-cycle slow-poll INFO summary log so it fires
        # at most once per ``SLOW_METRICS_INTERVAL_SEC`` window, regardless of
        # how often the fast 30 s coordinator cycle runs. The actual network
        # fetches stay gated by their TTL caches in ``_get_with_ttl_for``.
        self._last_slow_poll_log_monotonic: float = float("-inf")
        # Persistent statistics repair state. It lets the integration notice a
        # successful cloud recovery after a HA/cloud outage and explicitly
        # reload month/year chart buckets that may have crossed an app period
        # boundary while polling was unavailable.
        self._statistics_backfill_store: Store[dict[str, Any]] = Store(
            hass,
            _STATISTICS_BACKFILL_STORE_VERSION,
            f"{DOMAIN}_{entry.entry_id}_{_STATISTICS_BACKFILL_STORE_KEY}",
        )
        self._statistics_backfill_state: dict[str, Any] = {
            _STATISTICS_BACKFILL_STORE_DEVICES: {}
        }
        self._statistics_backfill_state_loaded = False

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
            message_type = str(payload_obj.get(FIELD_MESSAGE_TYPE) or "")
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
        event.setdefault(FIELD_TIMESTAMP, dt_util.now().isoformat())
        event.setdefault("entry_id", self.entry.entry_id)
        path = self.hass.config.path(PAYLOAD_DEBUG_LOG_FILENAME)
        await self.hass.async_add_executor_job(
            append_payload_debug_line,
            path,
            event,
            diagnostic_redactions_disabled(self.entry),
        )

    async def async_discover(self) -> None:  # noqa: PLR0912
        """Populate _device_index from config or /v1/device/system/list."""
        new_index: dict[str, dict[str, Any]] = {}

        # Primary: confirmed system/list endpoint (SolarVault + friends)
        try:
            systems = await self.api.async_get_system_list()
        except JackeryAuthError as err:
            raise ConfigEntryAuthFailed(  # noqa: TRY003
                "Jackery credentials were rejected during system discovery. "
                "Re-authentication is required."
            ) from err
        except JackeryError as err:
            raise UpdateFailed(f"system/list failed: {err}") from err  # noqa: TRY003

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
                new_index[str(dev_id)] = {
                    FIELD_SYSTEM_ID: str(sys_id) if sys_id else None,
                    PAYLOAD_SYSTEM_META: system_meta,
                    PAYLOAD_DEVICE_META: dict(dev),
                }

        if new_index:
            self._device_index = new_index
            self._last_discovery_refresh_monotonic = time.monotonic()
            _LOGGER.info(
                "Jackery: discovered %d device(s) from /v1/device/system/list",
                len(new_index),
            )
            return

        # Fallback: legacy bind/list (Explorer portables)
        try:
            legacy = await self.api.async_list_devices_legacy()
        except JackeryAuthError as err:
            raise ConfigEntryAuthFailed(  # noqa: TRY003
                "Jackery credentials were rejected during legacy device discovery. "
                "Re-authentication is required."
            ) from err
        for dev in legacy:
            dev_id = (
                dev.get(FIELD_DEV_ID)
                or dev.get(FIELD_DEVICE_ID)
                or dev.get(FIELD_ID)
                or dev.get(FIELD_DEV_SN)
                or dev.get(FIELD_DEVICE_SN)
            )
            if dev_id:
                new_index[str(dev_id)] = {
                    FIELD_SYSTEM_ID: None,
                    PAYLOAD_SYSTEM_META: {},
                    PAYLOAD_DEVICE_META: dict(dev),
                }

        self._device_index = new_index
        self._last_discovery_refresh_monotonic = time.monotonic()
        if not new_index:
            _LOGGER.error(
                "Jackery: no devices found on either /v1/device/system/list "
                "or /v1/device/bind/list."
            )

    async def _async_refresh_discovery_if_due(self) -> None:
        """Refresh discovery metadata periodically for runtime device additions."""
        now = time.monotonic()
        if (
            now - self._last_discovery_refresh_monotonic
            < self._slow_metrics_interval_sec
        ):
            return
        old_device_ids = set(self._device_index)
        try:
            await self.async_discover()
            self._last_discovery_refresh_monotonic = now  # stamp AFTER success so a failed discover doesn't burn the retry window  # noqa: E501, RUF100
        except ConfigEntryAuthFailed:
            raise
        except JackeryAuthError as err:
            _raise_config_entry_auth_failed(
                "Jackery credentials were rejected during device rediscovery", err
            )
        except JackeryError:
            _LOGGER.exception("Jackery runtime discovery refresh failed")
            return
        except UpdateFailed:
            _LOGGER.exception("Jackery runtime discovery refresh failed")
            return
        new_device_ids = set(self._device_index) - old_device_ids
        if new_device_ids:
            _LOGGER.info(
                "Jackery: runtime discovery added %d device(s): %s",
                len(new_device_ids),
                ", ".join(sorted(new_device_ids)),
            )

    @staticmethod
    def _is_property_device_candidate(dev: dict[str, Any]) -> bool:
        """Filter out accessory entries that do not support /device/property."""
        # Observed for third-party accessories (e.g., Shelly): bindKey=0 and
        # no Jackery model metadata. Those IDs return API code=20000.
        bind_key = dev.get(FIELD_BIND_KEY)
        if bind_key in {0, "0"}:
            return False
        if dev.get(FIELD_DEV_TYPE) == 3 and bool(dev.get(FIELD_IS_CLOUD)):  # noqa: PLR2004
            return False
        return not (dev.get(FIELD_MODEL_CODE) is None and not dev.get(FIELD_DEV_MODEL))

    @staticmethod
    def _is_mqtt_auth_failure(message: object) -> bool:
        """Return True for broker-side MQTT credential rejection."""
        text = str(message or "").lower()
        return (
            "connect rc=4" in text
            or "connect rc=5" in text
            or "connect rc=134" in text
            or "connect rc=135" in text
            or "code 134" in text
            or "code 135" in text
            or "bad user name or password" in text
            or "not authorized" in text
        )

    def record_http_auth_rejection(self, status: int, data: object) -> None:
        """Record HTTP/API authentication rejection metrics."""
        reason = f"http_{status}"
        if self.api._is_token_expired_response(status, data):  # noqa: SLF001
            self.rejection_metrics.increment("auth_token_expiry_rejections", reason)
            return
        self.rejection_metrics.increment("http_auth_rejections", reason)

    def record_payload_validation_rejection(self, reason: str) -> None:
        """Record a payload validation rejection."""
        self.rejection_metrics.increment("payload_validation_rejections", reason)

    def record_schema_rejection(self, reason: str) -> None:
        """Record a schema/data-quality rejection."""
        self.rejection_metrics.increment("schema_rejections", reason)

    def record_timestamp_skew_rejection(self, reason: str) -> None:
        """Record a timestamp validation rejection."""
        self.rejection_metrics.increment("timestamp_skew_rejections", reason)

    def _pause_mqtt_after_auth_failure(
        self,
        message: object,
        *,
        streak: int | None = None,
    ) -> None:
        """Pause MQTT after a broker auth rejection while HTTP keeps polling."""
        now = time.monotonic()
        if self._mqtt_paused_until_monotonic > now:
            return
        self.rejection_metrics.increment("mqtt_broker_rejections", str(message))
        self._mqtt_app_conflict_pause_cycles += 1
        self._mqtt_paused_until_monotonic = now + MQTT_APP_CONFLICT_PAUSE_SEC
        _LOGGER.info(
            "Jackery MQTT paused for %ds after broker credential rejection "
            "(streak %s, pause cycle %d: %s); HTTP polling remains active",
            MQTT_APP_CONFLICT_PAUSE_SEC,
            streak if streak is not None else "unknown",
            self._mqtt_app_conflict_pause_cycles,
            message,
        )

    async def async_start_mqtt(self) -> None:
        """Start (or reconfigure) MQTT push channel."""
        if self._mqtt is None:
            try:
                mqtt_client_cls = await self.hass.async_add_executor_job(
                    _load_mqtt_push_client
                )
            except ModuleNotFoundError as err:
                if err.name != "aiomqtt":
                    raise
                _LOGGER.warning(
                    "Jackery MQTT push is unavailable because aiomqtt is not installed"
                )
                return

            self._mqtt = mqtt_client_cls(
                self.hass,
                self._async_handle_mqtt_message,
                self._async_mqtt_connected,
                disconnect_callback=self._async_handle_mqtt_disconnect,
            )
        try:
            await self._async_ensure_mqtt(force=True, wait_connected=True)
        except ConfigEntryAuthFailed:
            # Broker explicitly rejected the MQTT credentials. Surface this to
            # HA so the reauth UI opens; HTTP login may still have succeeded
            # but the user must update credentials regardless.
            raise
        except RuntimeError as err:
            _LOGGER.debug(
                "Jackery MQTT initial connect did not complete; "
                "HTTP polling remains active: %s",
                err,
            )
            return

    async def _async_mqtt_connected(self) -> None:
        """Request a full app-style MQTT snapshot after every broker connect."""
        try:
            await self._async_query_system_info_for_missing(
                force=True, ensure_mqtt=False
            )
            await self._async_query_weather_plan_for_missing(
                force=True, ensure_mqtt=False
            )
            await self._async_query_subdevices_for_missing(
                force=True, ensure_mqtt=False
            )
        except ConfigEntryAuthFailed as err:
            self._defer_background_auth_failure(err)

    async def _async_handle_mqtt_disconnect(self) -> None:
        """Recover from a server-side MQTT drop without flooding the log.

        Some Jackery broker disconnects (server-side TCP reset, Errno 104)
        cause aiomqtt's session task to exit with an MqttError. Recreating
        the client immediately on disconnect tears down the prior session
        cleanly and queues a fresh broker session, respecting
        ``MQTT_RECONNECT_THROTTLE_SEC`` so a flapping link cannot cause
        reconnect storms.
        """
        if self._mqtt is None:
            return
        # Reset the throttle window so the upcoming attempt actually runs.
        self._last_mqtt_connect_attempt = 0.0
        try:
            await self._async_ensure_mqtt(force=True, wait_connected=True)
        except ConfigEntryAuthFailed as err:
            self._defer_background_auth_failure(err)
        except JackeryAuthError:
            raise
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Jackery MQTT auto-reconnect after disconnect failed: %s", err
            )

    def _defer_background_auth_failure(self, err: ConfigEntryAuthFailed) -> None:
        """Route background auth failures through the next coordinator refresh."""
        message = str(err)
        if "MQTT broker rejected credentials" in message or self._is_mqtt_auth_failure(
            message
        ):
            streak = self._mqtt.consecutive_auth_failures if self._mqtt else None
            self._pause_mqtt_after_auth_failure(message, streak=streak)
            return
        self._mqtt_auth_failure_message = message
        _LOGGER.warning(
            "Jackery credentials rejected in a background task; "
            "Home Assistant reauth will be triggered on next refresh"
        )

    @property
    def configured_update_interval(self) -> timedelta:
        """Return the integration's coordinator polling interval."""
        return self._configured_update_interval

    def _should_skip_fast_property_fetch(self) -> bool:
        """Return True when the fast ``/v1/device/property`` fetch is redundant.

        Per PROTOCOL.md §0 rule 2 + §2 the HTTP property endpoint (30 s
        cadence) is the only call we may suppress when MQTT is delivering
        state at < ``MQTT_LIVE_THRESHOLD_SEC`` cadence. Slow stat endpoints,
        trends, day-cache rollover and the Recorder statistic imports stay
        on their own slow cadence regardless of MQTT liveness — they are
        gated by ``SLOW_METRICS_INTERVAL_SEC`` in their own TTL caches.

        Within the ``ADAPTIVE_KEEPALIVE_INTERVAL_SEC`` window the property
        fetch is skipped; the rest of the refresh cycle continues so the
        slow path keeps producing fresh ``dateType=day`` payloads (and the
        Recorder backfill keeps running) even while MQTT is live.
        """
        if not self.data:
            return False
        if self._mqtt is None:
            return False
        if not self._mqtt.is_connected:
            return False
        elapsed = self._mqtt.seconds_since_last_message
        if elapsed is None or elapsed > MQTT_LIVE_THRESHOLD_SEC:
            return False
        since_last_refresh = (
            time.monotonic() - self._last_http_refresh_completed_monotonic
        )
        return since_last_refresh < ADAPTIVE_KEEPALIVE_INTERVAL_SEC

    # Legacy alias kept so external callers/diagnostics that reference the
    # old name keep working. New code calls ``_should_skip_fast_property_fetch``.
    _should_skip_refresh_for_live_mqtt = _should_skip_fast_property_fetch

    async def async_shutdown(self) -> None:
        """Stop MQTT + BLE clients on integration unload."""
        for task in (
            self._mqtt_backfill_task,
            self._statistics_import_task,
            *self._battery_pack_ota_tasks.values(),
        ):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        self._mqtt_backfill_task = None
        self._statistics_import_task = None
        self._battery_pack_ota_tasks.clear()
        if self._mqtt is not None:
            await self._mqtt.async_stop()
            self._mqtt = None
        if self._ble_listener is not None:
            with contextlib.suppress(Exception):
                await self._ble_listener.async_stop()
            self._ble_listener = None

    # ------------------------------------------------------------------
    # BLE transport
    # ------------------------------------------------------------------

    def _ble_writes_enabled(self) -> bool:
        """Return whether experimental BLE writes are allowed for this entry."""
        return config_entry_bool_option(
            self.entry, CONF_ENABLE_BLE_TRANSPORT, DEFAULT_ENABLE_BLE_TRANSPORT
        ) and config_entry_bool_option(
            self.entry, CONF_ENABLE_BLE_WRITES, DEFAULT_ENABLE_BLE_WRITES
        )

    async def async_send_ble_command(  # noqa: PLR0913
        self,
        device_id: str,
        *,
        cmd: int,
        body: dict[str, Any] | bytes,
        flags: int = 0,
        wait_for_ack: bool = False,
        ack_timeout_sec: float = 5.0,
        ack_cmds: tuple[int, ...] | None = None,
        mtu_override: int | None = None,
    ) -> bool:
        """Send a single command frame to the device over BLE (experimental).

        Accepts the same ``cmd``/body shape as the MQTT setter pipeline:
        JSON-serialises ``body`` if it is a dict, otherwise uses it
        verbatim. Returns ``True`` if the GATT write completed (and, when
        ``wait_for_ack`` is set, the device echoed a decoded notify frame
        in time). Returns ``False`` only when the BLE listener is not
        connected to the device — callers fall back to MQTT in that case.

        Raises ``RuntimeError`` on ACK timeout when ``wait_for_ack`` is
        enabled; the BLE-first setter router catches that and falls back
        to MQTT. For SolarVault setters the duplicated write is
        idempotent.

        The trailer is currently sent as four NUL bytes — the firmware
        may or may not validate it; see :class:`.client.ble.BleBinaryFrame`.
        """
        if not self._ble_writes_enabled() or self._ble_listener is None:
            return False
        if isinstance(body, dict):
            payload = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
            body_bytes = payload.encode("utf-8")
        else:
            body_bytes = bytes(body)
        return bool(
            await self._ble_listener.async_send_command(
                device_id,
                cmd=cmd,
                body=body_bytes,
                flags=flags,
                wait_for_ack=wait_for_ack,
                ack_timeout_sec=ack_timeout_sec,
                ack_cmds=ack_cmds,
                mtu_override=mtu_override,
            )
        )

    def ble_observations(self) -> dict[str, Any]:
        """Return a JSON-friendly snapshot of the BLE listener stats.

        Used by diagnostics + the optional BLE-status sensor. Returns an
        empty dict when BLE is disabled or the listener is not running so
        the integration stays usable on systems without Bluetooth.
        """
        if self._ble_listener is None:
            return {}
        snapshot: dict[str, Any] = {}
        for device_id, stats in self._ble_listener.all_stats().items():
            entry: dict[str, Any] = {
                "advertisements_seen": stats.advertisements_seen,
                "connect_attempts": stats.connect_attempts,
                "connect_failures": stats.connect_failures,
                "frames_received": stats.frames_received,
                "frames_decoded": stats.frames_decoded,
                "frames_decode_failed": stats.frames_decode_failed,
                "acks_received": stats.acks_received,
                "acks_timed_out": stats.acks_timed_out,
                "writes_with_response_failed": stats.writes_with_response_failed,
                "last_error": stats.last_error,
                "last_connect_at": (
                    stats.last_connect_at.isoformat() if stats.last_connect_at else None
                ),
                "last_disconnect_at": (
                    stats.last_disconnect_at.isoformat()
                    if stats.last_disconnect_at
                    else None
                ),
                "last_ack_at": (
                    stats.last_ack_at.isoformat() if stats.last_ack_at else None
                ),
                "mtu": self._ble_listener.mtu_for_device(device_id),
                # Per-cmd unrouted counter so the maintainer sees what
                # BLE telemetry currently flows past without being
                # merged into coordinator.data. Cmd 120 (system /
                # per-device / CT lifetime) is the most common entry.
                "unrouted_frames_by_cmd": dict(stats.unrouted_frames_by_cmd),
            }
            last_frame = stats.last_frame
            if last_frame is not None:
                entry["last_frame"] = {
                    "received_at": last_frame.received_at.isoformat(),
                    "raw_hex": last_frame.raw_bytes.hex(),
                    "raw_len": len(last_frame.raw_bytes),
                    "decode_error": last_frame.decode_error,
                    "parsed": (
                        {
                            "frame_index": last_frame.parsed.frame_index,
                            "chunk_count": last_frame.parsed.chunk_count,
                            "flags": last_frame.parsed.flags,
                            "cmd": last_frame.parsed.cmd,
                            "body_len": len(last_frame.parsed.body),
                            "body_preview": last_frame.parsed.body[:240].decode(
                                "utf-8", errors="replace"
                            ),
                            "trailer_hex": last_frame.parsed.trailer.hex(),
                        }
                        if last_frame.parsed is not None
                        else None
                    ),
                }
            snapshot[device_id] = entry
        return snapshot

    async def async_start_ble_transport(self) -> None:  # noqa: PLR0915
        """Start the optional BLE listener if the config-entry option is set.

        Safe to call repeatedly; only the first call attaches a listener.
        Failures are logged at WARNING and don't propagate — BLE is an
        opt-in diagnostic channel and must not break cloud setup.
        """
        if self._ble_listener is not None:
            return
        if not config_entry_bool_option(
            self.entry, CONF_ENABLE_BLE_TRANSPORT, DEFAULT_ENABLE_BLE_TRANSPORT
        ):
            return
        try:
            from .client.ble_transport import JackeryBleListener  # noqa: PLC0415
        except ImportError as err:
            _LOGGER.warning(
                "Jackery BLE transport requested but module import failed: %s",
                err,
            )
            return

        async def _sink(  # noqa: PLR0911, PLR0912, RUF029
            device_id: str, observation: BleFrameObservation
        ) -> None:
            """Merge BLE-delivered JSON bodies into ``coordinator.data``.

            Mirrors the cmd-routing of ``_async_handle_mqtt_message`` so
            BLE-decoded telemetry uses the **same** merge helpers as MQTT
            payloads. Without this contract the live values stop updating
            whenever MQTT goes quiet: a 2-arg
            ``_merge_main_properties_for_device(device_id, payload)``
            call (the previous shape) raises ``TypeError`` in the sink's
            ``try/except`` and silently drops every decoded frame
            (observed 2026-05-16 17:41-17:44 production log).

            Routing per ``cmd`` (from :data:`.const.MQTT_CMD_*`):

            * ``107`` / ``121`` (DevicePropertyChange / CombineData):
              main-properties merge via
              :meth:`_merge_main_properties_for_device` (3 args:
              ``device_id, base, updates``).
            * ``111`` (UploadSubDeviceIncrementalProperty): subdevice
              merge via :meth:`_merge_subdevice_data`.
            * Other cmds (e.g. ``120`` lifetime stat snapshots) are
              logged but not merged - they need their own contract that
              is out of scope here.

            After a successful merge the updated bundle is published via
            :meth:`_push_partial_update` so the existing entity listeners
            pick it up.
            """
            if observation.parsed is None:
                return
            body = observation.parsed.body
            if not body:
                return
            try:
                payload = json.loads(body.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as err:
                _LOGGER.debug(
                    "Jackery BLE %s: body is not JSON (cmd=%d, %d bytes): %s",
                    device_id,
                    observation.parsed.cmd,
                    len(body),
                    err,
                )
                return
            if not isinstance(payload, dict):
                _LOGGER.debug(
                    "Jackery BLE %s: body decoded to %s, expected dict",
                    device_id,
                    type(payload).__name__,
                )
                return
            # Strip the BLE-only framing key — ``cmd`` duplicates
            # ``observation.parsed.cmd`` and never appears in cloud HTTP
            # payloads. ``deviceSn`` and ``devType`` stay (subdevice
            # merge needs them).
            payload = {k: v for k, v in payload.items() if k != FIELD_CMD}
            if not payload:
                return

            cmd = observation.parsed.cmd
            current = self.data or {}
            current_device = current.get(device_id)
            if not isinstance(current_device, dict):
                _LOGGER.debug(
                    "Jackery BLE %s: device not in coordinator data (discovery "
                    "pending?); dropping cmd=%d frame",
                    device_id,
                    cmd,
                )
                return
            source_started_at = observation.source_started_at
            last_update_at = self._device_update_received_at.get(device_id)
            if (
                source_started_at is not None
                and last_update_at is not None
                and source_started_at <= last_update_at
            ):
                _LOGGER.debug(
                    "Jackery BLE %s: dropping stale cmd=%d frame "
                    "(source_started_at=%s <= last_update_at=%s)",
                    device_id,
                    observation.parsed.cmd,
                    source_started_at.isoformat(),
                    last_update_at.isoformat(),
                )
                return
            updated = dict(current_device)
            touched = False

            if cmd in {MQTT_CMD_DEVICE_PROPERTY_CHANGE, MQTT_CMD_CONTROL_COMBINE}:
                props = self._merge_main_properties_for_device(
                    device_id,
                    current_device.get(PAYLOAD_PROPERTIES) or {},
                    payload,
                )
                updated[PAYLOAD_PROPERTIES] = props
                touched = True
            elif cmd == MQTT_CMD_CONTROL_SUB_DEVICE:
                touched = self._merge_subdevice_data(
                    updated, payload, device_id=device_id
                )
            elif (
                cmd == MQTT_CMD_QUERY_COMBINE_DATA
                and payload.get(FIELD_DEV_TYPE) == SUBDEVICE_DEV_TYPE_BATTERY_PACK
                and payload.get(FIELD_DEVICE_SN)
            ):
                # Battery-pack lifetime energy snapshot. The HTTP
                # ``/v1/device/battery/pack/list`` returns ``data: null``
                # for SolarVault, so BLE is the only source for per-pack
                # ``inEgy``/``outEgy`` lifetime counters. Merge them into
                # the matching pack entry in PAYLOAD_BATTERY_PACKS.
                touched = self._merge_battery_pack_lifetime_from_ble(updated, payload)
            elif cmd == MQTT_CMD_QUERY_COMBINE_DATA:
                # Non-pack QueryCombineData. Mirror the MQTT handler
                # (_async_handle_mqtt_message): a subdevice snapshot (CT /
                # smart meter / socket) goes through the subdevice merge,
                # while a main-device combine frame updates main properties.
                # Previously every such frame was dropped into the unrouted
                # counter, so live BLE telemetry was lost whenever MQTT went
                # quiet (observed as 11k+ unrouted cmd=120 frames).
                if self._is_subdevice_payload(payload, payload):
                    touched = self._merge_subdevice_data(
                        updated, payload, device_id=device_id
                    )
                else:
                    props = self._merge_main_properties_for_device(
                        device_id,
                        current_device.get(PAYLOAD_PROPERTIES) or {},
                        payload,
                    )
                    updated[PAYLOAD_PROPERTIES] = props
                    touched = True
            # Track unrouted frames in the listener stats so they
            # show up in diagnostics without spamming DEBUG once per
            # frame (cmd=120 system/per-device/CT variants arrive
            # multiple times per minute over BLE).
            elif self._ble_listener is not None:
                stats = self._ble_listener.stats_for(device_id)
                stats.unrouted_frames_by_cmd[cmd] = (
                    stats.unrouted_frames_by_cmd.get(cmd, 0) + 1
                )

            if not touched:
                return

            new_data = dict(current)
            new_data[device_id] = updated
            self._device_update_received_at[device_id] = observation.received_at
            self._push_partial_update(new_data)
            _LOGGER.debug(
                "Jackery BLE %s: merged %d field(s) from cmd=%d body",
                device_id,
                len(payload),
                cmd,
            )

        listener = JackeryBleListener(
            self.hass,
            _sink,
            key_resolver=self.device_bluetooth_key,
            ble_address_resolver=self._ble_address_for_device,
            serial_resolver=self.device_id_for_ble_serial,
        )
        try:
            await listener.async_start(list(self._device_index.keys()))
        except Exception as err:  # noqa: BLE001
            _LOGGER.warning("Jackery BLE listener failed to start: %s", err)
            return
        self._ble_listener = listener
        _LOGGER.info(
            "Jackery BLE listener attached for %d device(s)",
            len(self._device_index),
        )
        # Dev-mode convenience: when the developer env-var toggle is on,
        # log the per-device bluetoothKey so it can be reused to decrypt
        # sniffed BLE frames outside the integration. JACKERY_DEV_MODE=1
        # *also* disables redaction in the JSONL log and diagnostics
        # export; both surfaces are off by default.
        if dev_mode_redactions_disabled():
            import base64 as _base64  # noqa: PLC0415

            for device_id in self._device_index:
                key = self.device_bluetooth_key(device_id)
                if key is None:
                    _LOGGER.warning(
                        "Jackery DEV_MODE: device %s has no bluetoothKey captured yet",
                        device_id,
                    )
                    continue
                _LOGGER.warning(
                    "Jackery DEV_MODE: device %s bluetoothKey (base64) = %s",
                    device_id,
                    _base64.b64encode(key).decode("ascii"),
                )

    def _ble_address_for_device(self, device_id: str) -> str | None:
        """Best-effort lookup of the BLE MAC for a Jackery device id.

        BLE addresses are learnt at advertisement time (the listener stores
        ``serial → MAC`` in its own state). The coordinator does not cache
        the MAC because it can change between adapter resets; the listener's
        in-memory map is the source of truth.
        """
        if self._ble_listener is None:
            return None
        address = self._ble_listener.address_for_device_id(device_id)
        return str(address) if address is not None else None

    def device_id_for_ble_serial(self, ble_serial: str) -> str | None:
        """Map a BLE-broadcast serial to its Jackery device id.

        The HTTP ``/v1/device/system/list`` response uses a longer
        serial form than the BLE manufacturer-data field. Example from a
        SolarVault 3 Pro Max captured 2026-05-16:

            HTTP  deviceSn: ``HR2C04000280HH3``  (15 chars, ``H`` prefix)
            BLE   adv data: ``R2C04000280HH3``   (14 chars, no prefix)

        The mapping is therefore "BLE serial is a suffix of HTTP serial".
        We accept exact match too in case future firmware aligns them, and
        we case-fold both sides because Jackery is inconsistent.
        """
        if not ble_serial:
            return None
        target = ble_serial.strip().upper()
        for device_id, idx in self._device_index.items():
            device_meta = idx.get(PAYLOAD_DEVICE_META) or {}
            http_sn = (
                str(
                    device_meta.get(FIELD_DEVICE_SN)
                    or device_meta.get(FIELD_DEV_SN)
                    or ""
                )
                .strip()
                .upper()
            )
            if not http_sn:
                continue
            if http_sn == target or http_sn.endswith(target):
                return device_id
        return None

    def async_start_statistics_imports(self) -> None:
        """Allow recorder-statistics imports after sensor entities exist."""
        self._statistics_import_ready = True
        if self.data:
            self._schedule_statistics_import(self.data)

    async def _async_ensure_mqtt(
        self, *, force: bool = False, wait_connected: bool = False
    ) -> None:
        """Ensure MQTT is connected with credentials from current login session."""
        if self._mqtt is None:
            return

        # Fast path: current client is already configured for the current
        # session fingerprint, and no forced reconnect is requested. Clearing
        # the pause book-keeping here is safe — if we are connected, no
        # competing app session is winning the broker right now.
        current_fp = self.api.mqtt_fingerprint
        if (
            not force
            and self._mqtt.is_started
            and self._mqtt_fingerprint is not None
            and self._mqtt_fingerprint == current_fp
            and self._mqtt.is_connected
        ):
            if (
                self._mqtt_app_conflict_pause_cycles
                or self._mqtt_paused_until_monotonic
            ):
                self._mqtt_app_conflict_pause_cycles = 0
                self._mqtt_paused_until_monotonic = 0.0
            return

        now = time.monotonic()

        # App-conflict pause: when the broker last rejected credentials it is
        # almost always because the official Jackery app just claimed the
        # same ``<userId>@APP`` clientId. Reconnect storms only deepen the
        # conflict, so skip until the pause window elapses. ``force=True``
        # callers (e.g. setup) still respect the pause to avoid spamming a
        # broker we know is hostile.
        if self._mqtt_paused_until_monotonic > now:
            _LOGGER.debug(
                "Jackery MQTT: paused until app-conflict window clears "
                "(%.0fs remaining, cycle %d)",
                self._mqtt_paused_until_monotonic - now,
                self._mqtt_app_conflict_pause_cycles,
            )
            return

        # Avoid reconnect churn when another app session keeps rotating the
        # token/seed frequently.
        if (
            not force  # noqa: PLR0916
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
        except JackeryAuthError as err:
            _raise_config_entry_auth_failed(
                "Jackery credentials were rejected while preparing MQTT credentials",
                err,
            )
        except JackeryError:
            _LOGGER.exception("Jackery MQTT credential build failed")
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

        self._last_mqtt_connect_attempt = time.monotonic()
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
                    streak = self._mqtt.consecutive_auth_failures
                    # Likely a token/session race with the official Jackery app.
                    # MQTT pauses and the coordinator continues on HTTP. A
                    # genuine password/token problem will be detected by the
                    # HTTP auth paths; MQTT-only broker rejection must not stop
                    # polling or force users to reload the integration.
                    self._pause_mqtt_after_auth_failure(
                        mqtt_last_error or err,
                        streak=streak,
                    )
                    raise
                _LOGGER.debug(
                    "Jackery MQTT connect check did not complete "
                    "(TLS chain+hostname verified when the broker accepted TCP; "
                    "strict AKID check suppressed if supported): %s",
                    err,
                )
                raise
        self._mqtt_fingerprint = fingerprint

    async def async_handle_local_mqtt_message(
        self,
        topic: str,
        payload: dict[str, Any],
    ) -> None:
        """Route LAN MQTT JSON into the shared cloud-MQTT payload merge path.

        The local broker delivers the same UploadCombineData/device-property
        shapes as the cloud push, so it reuses the cloud router rather than
        duplicating the cmd-dispatch and merge logic.
        """
        await self._async_handle_mqtt_message(topic, payload)

    async def _async_handle_mqtt_message(  # noqa: C901, PLR0912, PLR0914, PLR0915
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
            _LOGGER.debug(
                "Jackery MQTT: could not resolve device_id from payload on %s; "
                "payload keys=%s",
                topic,
                list(payload.keys()),
            )
            return
        if device_id not in self.data:
            _LOGGER.debug(
                "Jackery MQTT: device_id %s not in coordinator data (discovery "
                "pending?); dropping message from %s",
                device_id,
                topic,
            )
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
        is_alarm = (
            msg_type == MQTT_MESSAGE_UPLOAD_DEVICE_ALERT
            or action_id in MQTT_ACTION_IDS_ALARM
            or body.get(FIELD_CMD) == MQTT_CMD_UPLOAD_DEVICE_ALERT
        )

        if topic.endswith(("/device", "/config")):
            if body:
                if is_subdevice:
                    touched = (
                        self._merge_subdevice_data(updated, body, device_id=device_id)
                        or touched
                    )
                elif not is_alarm:
                    props = self._merge_main_properties_for_device(
                        device_id,
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
            incoming_alarm = body or payload
            existing_alarm = updated.get(PAYLOAD_ALARM)
            if isinstance(existing_alarm, dict) and isinstance(incoming_alarm, dict):
                updated[PAYLOAD_ALARM] = {**existing_alarm, **incoming_alarm}
            else:
                updated[PAYLOAD_ALARM] = incoming_alarm
            touched = True

        elif topic.endswith("/notice"):
            # Not entity-backed today; keep as diagnostic context.
            updated[PAYLOAD_NOTICE] = payload
            touched = True

        if is_alarm:
            incoming_alarm = body or payload
            existing_alarm = updated.get(PAYLOAD_ALARM)
            if isinstance(existing_alarm, dict) and isinstance(incoming_alarm, dict):
                updated[PAYLOAD_ALARM] = {**existing_alarm, **incoming_alarm}
            else:
                updated[PAYLOAD_ALARM] = incoming_alarm
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
            in {
                MQTT_MESSAGE_UPLOAD_WEATHER_PLAN,
                MQTT_MESSAGE_QUERY_WEATHER_PLAN,
                MQTT_MESSAGE_SEND_WEATHER_ALERT,
                MQTT_MESSAGE_CANCEL_WEATHER_ALERT,
            }
            or body.get(FIELD_CMD) == MQTT_CMD_QUERY_WEATHER_PLAN
            or action_id in weather_action_ids
        ):
            incoming_wp = body or payload
            existing_wp = updated.get(PAYLOAD_WEATHER_PLAN)
            if isinstance(existing_wp, dict) and isinstance(incoming_wp, dict):
                updated[PAYLOAD_WEATHER_PLAN] = merge_live_properties(
                    existing_wp, incoming_wp
                )
            else:
                updated[PAYLOAD_WEATHER_PLAN] = incoming_wp
            touched = True

        # User-configurable schedule payloads (custom mode / tariff mode /
        # smart-plug priority) are transported via DownloadDeviceSchedule.
        if (
            msg_type == MQTT_MESSAGE_DOWNLOAD_DEVICE_SCHEDULE
            or action_id in MQTT_ACTION_IDS_SCHEDULE
        ):
            incoming_tp = body or payload
            existing_tp = updated.get(PAYLOAD_TASK_PLAN)
            if isinstance(existing_tp, dict) and isinstance(incoming_tp, dict):
                updated[PAYLOAD_TASK_PLAN] = merge_live_properties(
                    existing_tp, incoming_tp
                )
            else:
                updated[PAYLOAD_TASK_PLAN] = incoming_tp
            touched = True

        # Device-property snapshots are the MQTT equivalent of the
        # /v1/device/property HTTP endpoint. The app requests them with
        # READ_DEVICE_INFO (QueryDeviceProperty, actionId=3011, cmd=106).
        if (
            not is_subdevice
            and (
                msg_type
                in {
                    MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
                    MQTT_MESSAGE_QUERY_DEVICE_PROPERTY,
                }
                or action_id in MQTT_ACTION_IDS_DEVICE_PROPERTY
                or body.get(FIELD_CMD)
                in {
                    MQTT_CMD_DEVICE_PROPERTY_CHANGE,
                    MQTT_CMD_QUERY_DEVICE_PROPERTY,
                }
            )
            and body
        ):
            props = self._merge_main_properties_for_device(
                device_id,
                current.get(PAYLOAD_PROPERTIES) or {},
                body,
            )
            updated[PAYLOAD_PROPERTIES] = props
            touched = True

        # System/config snapshots (work mode, temp unit, standby/off-grid,
        # max system power, storm lead time) are transported via
        # QueryCombineData/UploadCombineData, not the HTTP property endpoint.
        if (
            not is_subdevice
            and (
                msg_type
                in {
                    MQTT_MESSAGE_QUERY_COMBINE_DATA,
                    MQTT_MESSAGE_UPLOAD_COMBINE_DATA,
                    MQTT_MESSAGE_UPLOAD_INCREMENTAL_COMBINE_DATA,
                    MQTT_MESSAGE_CONTROL_COMBINE,
                }
                or action_id in MQTT_ACTION_IDS_COMBINE
                or body.get(FIELD_CMD)
                in {MQTT_CMD_QUERY_COMBINE_DATA, MQTT_CMD_CONTROL_COMBINE}
            )
            and body
        ):
            props = self._merge_main_properties_for_device(
                device_id,
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
            source = body or payload
            if isinstance(source, dict):
                touched = (
                    self._merge_subdevice_data(updated, source, device_id=device_id)
                    or touched
                )

        # Third-party MQTT bridge config echoed back by the device
        # (QueryThirdPartMQTTConfig / ThirdPartMQTTConfig, actionId 3046/3047,
        # cmd 113/114). The device reports the applied ip/port/enable (it never
        # echoes the plaintext password/token). Surface it so the text/number
        # entities show the device's current config instead of blank fields.
        if (
            not is_subdevice
            and (
                msg_type
                in {
                    MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG,
                    MQTT_MESSAGE_THIRD_PARTY_MQTT_CONFIG,
                }
                or action_id
                in {
                    ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
                    ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG,
                }
                or body.get(FIELD_CMD)
                in {
                    MQTT_CMD_THIRD_PARTY_MQTT_CONFIG,
                    MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG,
                }
            )
            and isinstance(body, dict)
        ):
            reported = {
                key: value
                for key, value in body.items()
                if key != FIELD_CMD and value is not None
            }
            if reported:
                merged_cfg = {
                    **(current.get(PAYLOAD_THIRD_PARTY_MQTT_CONFIG) or {}),
                    **reported,
                }
                updated[PAYLOAD_THIRD_PARTY_MQTT_CONFIG] = merged_cfg
                # Mirror into the plaintext store the UI getter reads, but never
                # clobber a locally-entered secret with a device value it omits.
                self._third_party_mqtt_plaintext.setdefault(device_id, {}).update(
                    reported
                )
                touched = True

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
        if updated.get(PAYLOAD_BATTERY_PACKS):
            self._schedule_battery_pack_ota_enrichment(device_id)

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

    def device_bluetooth_key(self, device_id: str) -> bytes | None:
        """Return the per-device AES key used for BLE frame encryption.

        Source: ``bluetoothKey`` from the ``/v1/device/system/list``
        response. The HTTP response places the key at the **system**
        level (i.e. ``data[].bluetoothKey``), not in the per-device
        ``data[].devices[].bluetoothKey`` slot (which is ``null`` for
        the main SolarVault on every observed account). The lookup
        therefore checks the device-meta block first — in case a future
        firmware version migrates the key down — and falls back to the
        system-meta block, then to the live ``coordinator.data`` snapshot.

        Both AES-128 (16 bytes) and AES-256 (32 bytes) are accepted; the
        decoded byte length picks the cipher mode in
        :mod:`.client.ble`. Returns ``None`` when no usable key exists.
        """
        from .client.ble import BLE_AES_KEY_LENGTHS  # noqa: PLC0415

        idx = self._device_index.get(device_id) or {}
        device_meta = idx.get(PAYLOAD_DEVICE_META) or {}
        system_meta = idx.get(PAYLOAD_SYSTEM_META) or {}
        data_payload = (self.data or {}).get(device_id, {}) or {}
        live_system = data_payload.get(PAYLOAD_SYSTEM) or {}
        # Search order: per-device meta → system meta → live system data.
        # The first non-empty value wins so post-discovery updates can
        # supply the key if the initial discovery missed it.
        candidates = (
            device_meta.get(FIELD_BLUETOOTH_KEY),
            system_meta.get(FIELD_BLUETOOTH_KEY),
            live_system.get(FIELD_BLUETOOTH_KEY),
        )
        raw = next((c for c in candidates if c), None)
        if not raw:
            return None
        try:
            import base64  # noqa: PLC0415

            key = base64.b64decode(str(raw))
        except ValueError, binascii.Error:
            _LOGGER.debug("Jackery: bluetoothKey for %s is not valid base64", device_id)
            return None
        if len(key) not in BLE_AES_KEY_LENGTHS:
            _LOGGER.debug(
                "Jackery: bluetoothKey for %s decodes to %d bytes (expected one of %s)",
                device_id,
                len(key),
                BLE_AES_KEY_LENGTHS,
            )
            return None
        return key

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

    def device_supports_third_party_mqtt(self, device_id: str) -> bool:
        """Return True if the device supports third-party MQTT configuration.

        True when the device has already sent a ThirdPartMQTTConfig payload
        (``PAYLOAD_THIRD_PARTY_MQTT_CONFIG`` present) or when
        ``device_supports_advanced`` is True, since Pro Max / modelCode 3002
        hardware always exposes this feature regardless of whether the config
        payload has arrived yet.
        """
        payload = (self.data or {}).get(device_id, {})
        return (
            PAYLOAD_THIRD_PARTY_MQTT_CONFIG in payload
            or self.device_supports_advanced(device_id)
        )

    # ------------------------------------------------------------------
    # Property merging & payload helpers
    # ------------------------------------------------------------------

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
        """Sanitize, merge, and normalize main-device property payloads.

        Uses merge_live_properties so sparse MQTT/BLE push frames never blank
        populated values that the HTTP poll already delivered (ingest.py §rule 1).
        """
        merged = merge_live_properties(
            cls._sanitize_main_properties(base),
            cls._sanitize_main_properties(updates),
        )
        return cls._sync_property_aliases(merged)

    def _active_property_overrides(self, device_id: str) -> dict[str, Any]:
        """Return unexpired local writes that should beat stale snapshots."""
        override = self._property_overrides.get(device_id)
        if override is None:
            return {}
        override_ts, updates = override
        if time.monotonic() - override_ts >= self._PROPERTY_OVERRIDE_TTL_SEC:
            self._property_overrides.pop(device_id, None)
            return {}
        return dict(updates)

    def _merge_main_properties_for_device(
        self,
        device_id: str,
        base: dict[str, Any],
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge main properties while preserving recent local setter writes."""
        merged = self._merge_main_properties(base, updates)
        overrides = self._active_property_overrides(device_id)
        if not overrides:
            return merged
        return self._merge_main_properties(merged, overrides)

    @staticmethod
    def _find_dict_with_any_key(
        obj: Any,  # noqa: ANN401, RUF100
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
    def _find_list_for_key(
        obj: Any,  # noqa: ANN401, RUF100
        key: str,
    ) -> list[dict[str, Any]] | None:
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

    # ------------------------------------------------------------------
    # Subdevice & battery-pack management
    # ------------------------------------------------------------------

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
            if action_id is not None and int(action_id) in MQTT_ACTION_IDS_SUBDEVICE:
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
    def _normalize_battery_pack_payload(
        cls,
        item: Any,  # noqa: ANN401, RUF100
    ) -> dict[str, Any]:
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
                normalized = merge_live_properties(normalized, nested)
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
    def _looks_like_battery_pack(cls, item: Any) -> bool:  # noqa: ANN401, RUF100
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
    def _battery_packs_from_source(
        cls,
        source: Any,  # noqa: ANN401, RUF100
    ) -> list[dict[str, Any]] | None:
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

    def _merge_subdevice_data(  # noqa: PLR0912, PLR0914
        self,
        updated: dict[str, Any],
        source: dict[str, Any],
        *,
        device_id: str | None = None,
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
                removal_device_id = self._resolve_device_id_from_payload(updated)
                if removal_device_id:
                    if device_id is None:
                        device_id = removal_device_id
                    for pack_index in dropped_indices:
                        identifier = (
                            DOMAIN,
                            f"{device_id}_battery_pack_{pack_index}",
                        )
                        self._pending_device_removals.append(identifier)
            touched = True

        ct = self._find_dict_with_any_key(source, self._CT_METER_KEYS)
        if ct:
            # Shelly Pro 3EM wraps volt/curr/freq/fact/ap/rep inside a nested
            # AccCTBody dict. The outer dict already matched CT_METER_KEYS
            # (e.g. aPhasePw), so DFS stopped there and never descended.
            # Merge AccCTBody keys up so sensors that read volt/curr/... find them.
            acc_ct = ct.get(FIELD_ACC_CT_BODY)
            if isinstance(acc_ct, dict):
                ct = {**ct, **acc_ct}
            current_ct = updated.get(PAYLOAD_CT_METER)
            if isinstance(current_ct, dict):
                updated[PAYLOAD_CT_METER] = merge_live_properties(current_ct, ct)
            else:
                updated[PAYLOAD_CT_METER] = dict(ct)
            touched = True

        plugs = source.get(FIELD_PLUGS)
        if isinstance(plugs, list):
            plug_dicts = [item for item in plugs if isinstance(item, dict)]
            if plug_dicts:
                updated[PAYLOAD_SMART_PLUGS] = self._merge_smart_plug_lists(
                    updated.get(PAYLOAD_SMART_PLUGS),
                    plug_dicts,
                )
                touched = True

        collectors = source.get(FIELD_COLLECTORS)
        if isinstance(collectors, list):
            collector_dicts = [item for item in collectors if isinstance(item, dict)]
            if collector_dicts:
                updated[PAYLOAD_METER_HEADS] = self._merge_subdevice_lists_by_sn(
                    updated.get(PAYLOAD_METER_HEADS),
                    collector_dicts,
                )
                touched = True

        mirror = {
            key: value
            for key, value in source.items()
            if key in self._SUBDEVICE_MAIN_MIRROR_KEYS
        }
        if mirror:
            if device_id is None:
                props = self._merge_main_properties(
                    updated.get(PAYLOAD_PROPERTIES) or {},
                    mirror,
                )
            else:
                props = self._merge_main_properties_for_device(
                    device_id,
                    updated.get(PAYLOAD_PROPERTIES) or {},
                    mirror,
                )
            updated[PAYLOAD_PROPERTIES] = props
            touched = True

        return touched

    @classmethod
    def _merge_battery_pack_lists(
        cls,
        current: Any,  # noqa: ANN401, RUF100
        updates: list[Any],
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
        ]
        index_by_sn: dict[str, int] = {}
        for idx, item in enumerate(merged):
            sn = (
                item.get(FIELD_DEVICE_SN)
                or item.get(FIELD_DEV_SN)
                or item.get(FIELD_SN)
            )
            if sn:
                index_by_sn[str(sn)] = idx

        for update_idx, raw_update in enumerate(updates):
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
                merged[target_idx] = merge_live_properties(merged[target_idx], update)
                if sn:
                    index_by_sn[str(sn)] = target_idx

        # Tag each pack that is reporting as online with the current UTC
        # timestamp. Packs with commState=0 (offline) keep their previous
        # _last_seen_at so a brief outage does not look like a removal.
        # Packs that were never seen online (no commState) are also
        # tagged once on first discovery to give the stale-cleanup a
        # baseline.
        now_iso = utc_now().isoformat()
        for pack in merged:
            comm_state = str(pack.get(FIELD_COMM_STATE) or "")
            if comm_state == "1" or PACK_FIELD_LAST_SEEN_AT not in pack:
                pack[PACK_FIELD_LAST_SEEN_AT] = now_iso

        return merged

    @classmethod
    def _merge_subdevice_lists_by_sn(
        cls,
        current: Any,  # noqa: ANN401, RUF100
        updates: list[Any],
    ) -> list[dict[str, Any]]:
        """Merge generic subdevice telemetry by ``deviceSn`` when available."""
        merged: list[dict[str, Any]] = [
            dict(item) for item in current or [] if isinstance(item, dict)
        ]
        index_by_sn: dict[str, int] = {}
        for idx, item in enumerate(merged):
            sn = (
                item.get(FIELD_DEVICE_SN)
                or item.get(FIELD_DEV_SN)
                or item.get(FIELD_SN)
            )
            if sn:
                index_by_sn[str(sn)] = idx

        for update_idx, raw_update in enumerate(updates):
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
                merged[target_idx] = merge_live_properties(merged[target_idx], update)
                if sn:
                    index_by_sn[str(sn)] = target_idx
        return merged

    @classmethod
    def _merge_smart_plug_lists(
        cls,
        current: Any,  # noqa: ANN401, RUF100
        updates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge incremental smart-plug telemetry by ``deviceSn``.

        Mirrors the battery-pack merge contract but without the 5-pack cap
        and without stale-eviction (plug presence is driven by the system
        accessories list, not by silence). Plug payloads from
        ``UploadSubDeviceGroupProperty`` (cmd=110, actionId=3032) carry
        ``switchSta``, ``sysSwitch``, ``inPw``, ``outPw``, ``socketPri``,
        ``wip``, ``deviceSn`` and friends per PlugSub.smali. Keep older
        fields when an incremental packet only refreshes power values.
        """
        return cls._merge_subdevice_lists_by_sn(current, updates)

    def _drop_stale_battery_packs(
        self,
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
        rationale and ``docs/PROTOCOL.md`` §1 for the rule that we never
        invent device state, only document silence.
        """
        if not packs:
            return packs, 0, []
        now = utc_now()
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
                seen_at = parse_utc_datetime(last_seen)
            except ValueError:
                self.record_timestamp_skew_rejection("battery_pack_last_seen_at")
                # Corrupt timestamp; keep but rewrite so future passes
                # have a clean baseline.
                fixed = dict(pack)
                fixed[PACK_FIELD_LAST_SEEN_AT] = now.isoformat()
                kept.append(fixed)
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
        for key in (FIELD_DEVICE_ID, "device_id", FIELD_ID):
            value = payload.get(key)
            if isinstance(value, str | int) and str(value).strip():
                return str(value).strip()
        props = payload.get(PAYLOAD_PROPERTIES)
        if isinstance(props, dict):
            for key in (FIELD_DEVICE_ID, "device_id"):
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
        from homeassistant.helpers import device_registry as dr  # noqa: PLC0415

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

    async def _async_enrich_battery_pack_ota(  # noqa: PLR0912
        self,
        device_id: str,
        packs: list[Any],
        main_device_sn: str | None,
        *,
        fetch_missing: bool = True,
    ) -> bool:
        """Attach per-pack OTA metadata for packs learned through MQTT.

        Jackery exposes addon battery live data via MQTT BatteryPackSub, but
        firmware versions are read through /v1/device/ota/list by deviceSn.
        """
        if not packs:
            return False

        per_dev = self._slow_cache.setdefault(f"dev:{device_id}", {})
        now = time.monotonic()
        tasks: list[Any] = []
        task_meta: list[tuple[int, str, str]] = []
        changed = False

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
                    before = dict(packs[idx])
                    self._merge_pack_ota(packs[idx], cached_ota)
                    changed = changed or packs[idx] != before
                continue

            if not fetch_missing:
                continue
            tasks.append(self.api.async_get_ota_info(pack_sn))
            task_meta.append((idx, pack_sn, cache_key))

        if not tasks:
            return changed

        results = await asyncio.gather(*tasks, return_exceptions=True)
        for (idx, pack_sn, cache_key), res in zip(task_meta, results, strict=False):
            if isinstance(res, JackeryAuthError):
                _raise_config_entry_auth_failed(
                    "Jackery credentials were rejected while fetching battery pack OTA metadata",  # noqa: E501, RUF100
                    res,
                )
            if isinstance(res, Exception):
                _LOGGER.debug("Pack OTA fetch failed for %s: %s", pack_sn, res)
                per_dev[cache_key] = (now, {})
                continue
            if not isinstance(res, dict) or not res:
                per_dev[cache_key] = (now, {})
                continue
            per_dev[cache_key] = (now, res)
            before = dict(packs[idx])
            self._merge_pack_ota(packs[idx], res)
            changed = changed or packs[idx] != before
        return changed

    def _battery_pack_ota_fetch_due(self, device_id: str) -> bool:
        """Return True when at least one known pack serial needs OTA refresh."""
        payload = (self.data or {}).get(device_id) or {}
        packs = payload.get(PAYLOAD_BATTERY_PACKS)
        if not isinstance(packs, list):
            return False
        main_device_sn = self._resolve_device_sn(device_id)
        per_dev = self._slow_cache.setdefault(f"dev:{device_id}", {})
        now = time.monotonic()
        for pack in packs[:5]:
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
            cached = per_dev.get(f"pack_ota:{pack_sn}")
            if cached is None or now - cached[0] >= self._price_config_interval_sec:
                return True
        return False

    def _schedule_battery_pack_ota_enrichment(self, device_id: str) -> None:
        """Refresh per-pack OTA metadata without blocking the poll cycle."""
        if not self._battery_pack_ota_fetch_due(device_id):
            return
        task = self._battery_pack_ota_tasks.get(device_id)
        if task is not None and not task.done():
            return
        self._battery_pack_ota_tasks[device_id] = self.hass.async_create_task(
            self._async_refresh_battery_pack_ota(device_id),
            name=f"{DOMAIN}_battery_pack_ota_{device_id}",
        )

    async def _async_refresh_battery_pack_ota(self, device_id: str) -> None:
        """Fetch per-pack OTA metadata and push a partial coordinator update."""
        try:  # noqa: PLW0717
            payload = (self.data or {}).get(device_id) or {}
            packs = payload.get(PAYLOAD_BATTERY_PACKS)
            if not isinstance(packs, list) or not packs:
                return
            working_packs = [dict(pack) for pack in packs if isinstance(pack, dict)]
            if not working_packs:
                return
            changed = await self._async_enrich_battery_pack_ota(
                device_id,
                working_packs,
                self._resolve_device_sn(device_id),
                fetch_missing=True,
            )
            if not changed or not self.data or device_id not in self.data:
                return
            new_data = dict(self.data)
            entry = dict(new_data[device_id])
            entry[PAYLOAD_BATTERY_PACKS] = self._merge_battery_pack_ota_lists(
                entry.get(PAYLOAD_BATTERY_PACKS),
                working_packs,
            )
            new_data[device_id] = entry
            self._push_partial_update(new_data)
        except ConfigEntryAuthFailed as err:
            self._defer_background_auth_failure(err)
        except Exception:
            _LOGGER.exception("Jackery pack OTA background refresh failed")
        finally:
            current = self._battery_pack_ota_tasks.get(device_id)
            if current is asyncio.current_task():
                self._battery_pack_ota_tasks.pop(device_id, None)

    @staticmethod
    def _merge_battery_pack_lifetime_from_ble(
        updated: dict[str, Any],
        body: dict[str, Any],
    ) -> bool:
        """Merge BLE-sourced lifetime ``inEgy``/``outEgy`` into a battery pack.

        BLE ``cmd=120`` for ``devType=1`` carries lifetime cumulative
        energy counters per pack:

            {"cmd": 120, "deviceSn": "HQ2C01400955HP3",
             "devType": 1, "subType": 0,
             "outEgy": 5095, "inEgy": 5648}

        Values are in Wh-int (BLE wire format). HTTP
        ``/v1/device/battery/pack/list`` returns ``data: null`` for
        SolarVault, so BLE is the only source for these per-pack
        lifetime counters. We merge them into the existing pack entry
        identified by ``deviceSn``. Returns ``True`` when a matching
        pack was found and updated, ``False`` otherwise.

        We deliberately do NOT create a new pack entry from BLE alone:
        the pack list authority remains the MQTT
        ``UploadSubDeviceGroupProperty`` actionId=3014 stream, which
        also delivers live ``inPw``/``outPw``/``batSoc``. BLE merely
        enriches an already-known pack with otherwise-unreachable
        lifetime values.
        """
        sn = body.get(FIELD_DEVICE_SN)
        if not sn:
            return False
        packs = updated.get(PAYLOAD_BATTERY_PACKS)
        if not isinstance(packs, list):
            return False
        in_egy = body.get(FIELD_IN_EGY)
        out_egy = body.get(FIELD_OUT_EGY)
        if in_egy is None and out_egy is None:
            return False
        # Match by deviceSn. Pack lists are short (≤5 packs) so a
        # linear scan is fine.
        touched = False
        merged_packs: list[Any] = []
        for pack in packs:
            if not isinstance(pack, dict):
                merged_packs.append(pack)
                continue
            pack_sn = (
                pack.get(FIELD_DEVICE_SN)
                or pack.get(FIELD_DEV_SN)
                or pack.get(FIELD_SN)
            )
            if pack_sn != sn:
                merged_packs.append(pack)
                continue
            new_pack = dict(pack)
            if in_egy is not None and new_pack.get(FIELD_IN_EGY) != in_egy:
                new_pack[FIELD_IN_EGY] = in_egy
                touched = True
            if out_egy is not None and new_pack.get(FIELD_OUT_EGY) != out_egy:
                new_pack[FIELD_OUT_EGY] = out_egy
                touched = True
            merged_packs.append(new_pack)
        if touched:
            updated[PAYLOAD_BATTERY_PACKS] = merged_packs
        return touched

    @staticmethod
    def _merge_pack_ota(pack: dict[str, Any], ota: dict[str, Any]) -> None:
        current_version = ota.get(FIELD_CURRENT_VERSION) or ota.get(FIELD_VERSION)
        if current_version is not None:
            pack[FIELD_VERSION] = current_version
            pack[FIELD_CURRENT_VERSION] = current_version
        for key in (
            FIELD_IS_FIRMWARE_UPGRADE,
            FIELD_TARGET_VERSION,
            FIELD_TARGET_MODULE_VERSION,
            FIELD_UPDATE_STATUS,
            FIELD_UPDATE_CONTENT,
            FIELD_UPGRADE_TYPE,
        ):
            if key in ota and ota.get(key) is not None:
                pack[key] = ota.get(key)

    @staticmethod
    def _merge_battery_pack_ota_lists(
        current: Any,  # noqa: ANN401, RUF100
        ota_updates: list[Any],
    ) -> list[dict[str, Any]]:
        """Merge static OTA fields into packs without touching last-seen state."""
        merged: list[dict[str, Any]] = [
            dict(item) for item in current or [] if isinstance(item, dict)
        ]
        index_by_sn: dict[str, int] = {}
        for idx, item in enumerate(merged):
            sn = (
                item.get(FIELD_DEVICE_SN)
                or item.get(FIELD_DEV_SN)
                or item.get(FIELD_SN)
            )
            if sn:
                index_by_sn[str(sn)] = idx

        ota_keys = (
            FIELD_VERSION,
            FIELD_CURRENT_VERSION,
            FIELD_IS_FIRMWARE_UPGRADE,
            FIELD_TARGET_VERSION,
            FIELD_TARGET_MODULE_VERSION,
            FIELD_UPDATE_STATUS,
            FIELD_UPDATE_CONTENT,
            FIELD_UPGRADE_TYPE,
        )
        for update_idx, raw_update in enumerate(ota_updates[:5]):
            if not isinstance(raw_update, dict):
                continue
            sn = (
                raw_update.get(FIELD_DEVICE_SN)
                or raw_update.get(FIELD_DEV_SN)
                or raw_update.get(FIELD_SN)
            )
            target_idx = index_by_sn.get(str(sn)) if sn else None
            if target_idx is None and update_idx < len(merged):
                target_idx = update_idx
            if target_idx is None:
                continue
            for key in ota_keys:
                if key in raw_update and raw_update.get(key) is not None:
                    merged[target_idx][key] = raw_update.get(key)
        return merged

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

    @classmethod
    def _has_subdevice_accessory_or_bucket(
        cls,
        payload: dict[str, Any],
        *,
        dev_type: int,
        bucket: str,
    ) -> bool:
        """Return True when discovery or a cached bucket mentions a subdevice."""
        target_type = str(dev_type)
        system = payload.get(PAYLOAD_SYSTEM) or payload.get(PAYLOAD_SYSTEM_META) or {}
        accessories: Any = payload.get(FIELD_ACCESSORIES)
        if not isinstance(accessories, list) and isinstance(system, dict):
            accessories = system.get(FIELD_ACCESSORIES)
        if isinstance(accessories, list):
            for item in accessories:
                if not isinstance(item, dict):
                    continue
                item_type = item.get(FIELD_DEV_TYPE) or item.get(FIELD_DEVICE_TYPE)
                if str(item_type) == target_type:
                    return True
        items = payload.get(bucket)
        return isinstance(items, list) and any(isinstance(item, dict) for item in items)

    @classmethod
    def _has_meter_head_accessory(cls, payload: dict[str, Any]) -> bool:
        """Return True when discovery or a prior MQTT reply mentions a meter head."""
        return cls._has_subdevice_accessory_or_bucket(
            payload,
            dev_type=SUBDEVICE_DEV_TYPE_METER_HEAD,
            bucket=PAYLOAD_METER_HEADS,
        )

    @classmethod
    def _has_smart_plug_accessory(cls, payload: dict[str, Any]) -> bool:
        """Return True when discovery or a prior MQTT reply mentions a smart plug.

        Used by ``_async_query_subdevices_for_missing`` to gate the
        ``READ_SUB_DEVICE_SOCKET`` query so accounts without plugs do not
        emit a useless MQTT publish on every cycle. Sources scanned, in order:

        - ``accessories`` entries with ``devType == SUBDEVICE_DEV_TYPE_SOCKET``
          (the Jackery app's ``HomeSubDeviceType.SOCKET`` ordinal)
        - Cached ``smart_plugs`` payload bucket from a previous MQTT reply
        """
        return cls._has_subdevice_accessory_or_bucket(
            payload,
            dev_type=SUBDEVICE_DEV_TYPE_SOCKET,
            bucket=PAYLOAD_SMART_PLUGS,
        )

    @staticmethod
    def _subdevice_serial(item: dict[str, Any]) -> str | None:
        """Return the stable serial field used by app subdevice payloads."""
        serial = (
            item.get(FIELD_DEVICE_SN) or item.get(FIELD_DEV_SN) or item.get(FIELD_SN)
        )
        return str(serial) if serial else None

    @staticmethod
    def _subdevice_id(item: dict[str, Any]) -> str | None:
        """Return the cloud id field used by accessory HTTP statistic APIs."""
        dev_id = (
            item.get(FIELD_DEVICE_ID) or item.get(FIELD_ID) or item.get(FIELD_DEV_ID)
        )
        return str(dev_id) if dev_id else None

    @classmethod
    def _subdevice_accessories(
        cls,
        payload: dict[str, Any],
        *,
        dev_type: int,
    ) -> list[dict[str, Any]]:
        """Return discovery accessories matching a HomeSubDeviceType value."""
        target_type = str(dev_type)
        system = payload.get(PAYLOAD_SYSTEM) or payload.get(PAYLOAD_SYSTEM_META) or {}
        accessories: Any = payload.get(FIELD_ACCESSORIES)
        if not isinstance(accessories, list) and isinstance(system, dict):
            accessories = system.get(FIELD_ACCESSORIES)
        if not isinstance(accessories, list):
            return []
        return [
            item
            for item in accessories
            if isinstance(item, dict)
            and str(item.get(FIELD_DEV_TYPE) or item.get(FIELD_DEVICE_TYPE) or "")
            == target_type
        ]

    @classmethod
    def _subdevice_stat_id(
        cls,
        payload: dict[str, Any],
        subdevice: dict[str, Any],
        *,
        dev_type: int,
    ) -> str | None:
        """Resolve the accessory id needed by app statistic endpoints.

        MQTT subdevice bodies are keyed by serial number while app HTTP
        statistic endpoints use the accessory's cloud device id. Discovery
        usually carries both, so match by serial first and fall back to the
        single accessory of that type when there is no ambiguity.
        """
        direct_id = cls._subdevice_id(subdevice)
        if direct_id:
            return direct_id
        serial = cls._subdevice_serial(subdevice)
        candidates = cls._subdevice_accessories(payload, dev_type=dev_type)
        if serial:
            for item in candidates:
                if cls._subdevice_serial(item) == serial:
                    return cls._subdevice_id(item)
        if len(candidates) == 1:
            return cls._subdevice_id(candidates[0])
        return None

    def _local_timezone(self) -> Any:  # noqa: ANN401, RUF100
        """Return the Home Assistant local timezone for app-period math."""
        timezone = dt_util.get_time_zone(self.hass.config.time_zone)
        return timezone or dt_util.DEFAULT_TIME_ZONE

    def _local_now(self) -> datetime:
        """Return Home Assistant local wall-clock time."""
        return dt_util.now(self._local_timezone())

    def _local_today(self) -> date:
        """Return Home Assistant local date for app period requests."""
        return self._local_now().date()

    def _trend_query_kwargs(self, date_type: str) -> dict[str, str]:
        """Return Jackery-app style trend query kwargs.

        PROTOCOL.md §2 requires explicit app ranges:
        day=today, week=Monday..Sunday, month=first..last, year=Jan 1..Dec 31.
        Using today..today with ``dateType=month/year`` can return partial
        day-like totals on some accounts.
        """
        return app_period_request_kwargs(date_type, today=self._local_today())

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
        clean_updates = self._sanitize_main_properties(updates)
        active = self._active_property_overrides(device_id)
        active.update(clean_updates)
        self._property_overrides[device_id] = (time.monotonic(), active)
        new_data = dict(self.data)
        entry = dict(new_data[device_id])
        props = self._sanitize_main_properties(entry.get(PAYLOAD_PROPERTIES) or {})
        props = self._merge_main_properties(props, clean_updates)
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
        price = merge_live_properties(price, updates)
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
        weather = merge_live_properties(weather, updates)
        entry[PAYLOAD_WEATHER_PLAN] = weather
        new_data[device_id] = entry
        self._push_partial_update(new_data)

    def _push_partial_update(self, new_data: dict[str, dict[str, Any]]) -> None:
        """Push updated coordinator data through HA's coordinator mechanism.

        Merges ``new_data`` against the *current* ``self.data`` at push time
        rather than replacing it. This prevents concurrent MQTT/BLE/HTTP writers
        from silently discarding each other's changes: if writer A read the
        snapshot before writer B fired, B's fields survive because the merge
        here runs against the live state (not against A's stale read).

        Device-level entries absent from ``new_data`` are preserved unchanged.
        Per-field merge uses ``merge_live_properties`` so neither transport can
        blank a populated field with a None/empty value.
        """
        if not self.data:
            self.async_set_updated_data(new_data)
            return
        merged = dict(self.data)
        for device_id, incoming in new_data.items():
            current = merged.get(device_id)
            if isinstance(current, dict) and isinstance(incoming, dict):
                merged[device_id] = merge_live_properties(current, incoming)
            else:
                merged[device_id] = incoming
        self.async_set_updated_data(merged)

    # ------------------------------------------------------------------
    # Background queries & device commands
    # ------------------------------------------------------------------

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
            should_query_combine = force or not has_all
            should_query_device_info = force or not props
            if not should_query_combine and not should_query_device_info:
                continue
            last_query = self._last_system_info_query.get(device_id, 0.0)
            if not force and (now - last_query) < self._system_info_query_interval_sec:
                continue
            self._last_system_info_query[device_id] = now
            if should_query_device_info:
                try:
                    await self.async_query_device_info(
                        device_id, ensure_mqtt=ensure_mqtt
                    )
                except ConfigEntryAuthFailed:
                    raise
                except (TimeoutError, HomeAssistantError, JackeryError) as err:
                    _LOGGER.debug(
                        "Jackery device-info query failed for %s: %s",
                        device_id,
                        err,
                    )
            if should_query_combine:
                try:
                    await self.async_query_system_info(
                        device_id, ensure_mqtt=ensure_mqtt
                    )
                except ConfigEntryAuthFailed:
                    raise
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
            try:
                await self.async_query_weather_plan(device_id, ensure_mqtt=ensure_mqtt)
                self._last_weather_plan_query[device_id] = now  # stamp AFTER success
            except ConfigEntryAuthFailed:
                raise
            except (TimeoutError, HomeAssistantError, JackeryError) as err:
                _LOGGER.debug(
                    "Jackery weather-plan query failed for %s: %s", device_id, err
                )

    @staticmethod
    def _command_body_for_transport(
        body_fields: dict[str, Any], *, cmd: int
    ) -> dict[str, Any]:
        """Build the command body shared by MQTT and BLE command transports."""
        body: dict[str, Any] = dict(body_fields)
        # App formatter only injects `cmd` when bleMsgType > 0.
        # For actions like SendWeatherAlert/CancelWeatherAlert/Storm switch
        # (bleMsgType = 0), `cmd` is omitted.
        if int(cmd) > 0:
            body[FIELD_CMD] = int(cmd)
        return body

    async def _async_publish_command_ble_first(  # noqa: PLR0913
        self,
        device_id: str,
        *,
        message_type: str,
        action_id: int,
        cmd: int,
        body_fields: dict[str, Any],
        ensure_mqtt: bool = True,
    ) -> None:
        """Try the experimental BLE write path before falling back to MQTT.

        BLE writes are issued with ``wait_for_ack=True`` so a silent
        firmware drop (e.g. unknown cmd, missing CRC trailer) raises a
        ``RuntimeError`` after a short window and the caller cleanly
        falls back to the cloud-MQTT pipeline. Setters in this router
        are idempotent, so the worst case of a duplicated write is
        another state-toggle write — preferable to silently swallowing
        the command.
        """
        if int(cmd) > 0:
            try:
                sent = await self.async_send_ble_command(
                    device_id,
                    cmd=int(cmd),
                    body=self._command_body_for_transport(body_fields, cmd=cmd),
                    wait_for_ack=True,
                )
            except (RuntimeError, ValueError) as err:
                _LOGGER.warning(
                    "Jackery BLE command failed for %s actionId=%s cmd=%s; "
                    "falling back to MQTT: %s",
                    device_id,
                    action_id,
                    cmd,
                    err,
                )
            else:
                if sent:
                    return
                _LOGGER.debug(
                    "Jackery BLE command unavailable for %s actionId=%s cmd=%s; "
                    "falling back to MQTT",
                    device_id,
                    action_id,
                    cmd,
                )
        await self._async_publish_command(
            device_id,
            message_type=message_type,
            action_id=action_id,
            cmd=cmd,
            body_fields=body_fields,
            ensure_mqtt=ensure_mqtt,
        )

    async def _async_publish_command(  # noqa: PLR0912, PLR0913
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
            raise HomeAssistantError("MQTT client not initialized")  # noqa: TRY003

        try:
            creds = await self.api.async_get_mqtt_credentials()
        except JackeryAuthError as err:
            _raise_config_entry_auth_failed(
                "Jackery credentials were rejected while preparing an MQTT command", err
            )
        except JackeryError as err:
            raise HomeAssistantError(  # noqa: TRY003
                f"Could not build Jackery MQTT credentials: {err}"
            ) from err
        user_id = creds[MQTT_CREDENTIAL_USER_ID]
        topic = f"{MQTT_TOPIC_PREFIX}/{user_id}/{MQTT_TOPIC_COMMAND}"
        ts = int(time.time() * 1000)
        body = self._command_body_for_transport(body_fields, cmd=cmd)
        payload: dict[str, Any] = {
            FIELD_ID: ts,
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
            try:  # noqa: PLW0717
                if ensure_mqtt:
                    await self._async_ensure_mqtt(
                        force=not self._mqtt.is_connected,
                        wait_connected=True,
                    )
                elif self._mqtt is None or not self._mqtt.is_connected:
                    raise RuntimeError("MQTT client is not connected")  # noqa: TRY003, TRY301
                if self._mqtt is None:
                    raise RuntimeError("MQTT client is not running")  # noqa: TRY003, TRY301
                await self._mqtt.async_publish_json(topic, payload, qos=1, retain=False)
                return  # noqa: TRY300
            except RuntimeError as err:
                last_err = err
                if ensure_mqtt and attempt == 0:
                    # Recover from stale MQTT sessions (e.g. app login on the
                    # same account rotated mqttPassWord). A fresh REST login
                    # rebuilds credentials before we reconnect.
                    try:
                        await self.api.async_login()
                    except JackeryAuthError as relogin_err:
                        _raise_config_entry_auth_failed(
                            "Jackery credentials were rejected while refreshing MQTT command credentials",  # noqa: E501, RUF100
                            relogin_err,
                        )
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
        await self._async_publish_command_ble_first(
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
        """Set SOC limits.

        Verified against ``HomeCmdAction.smali``: the official app sends both
        limits in a single ``SET_CHARGE_DISCHARGE_LINE`` (actionId 3028)
        frame. Missing sides are filled from the last-known coordinator
        state so the frame always carries the full pair the device expects.
        """
        if charge_limit is None and discharge_limit is None:
            raise UpdateFailed(  # noqa: TRY003
                "Cannot set SOC limits without charge_limit or discharge_limit"
            )
        current = ((self.data or {}).get(device_id, {}) or {}).get(
            PAYLOAD_PROPERTIES, {}
        ) or {}
        chg = int(
            charge_limit
            if charge_limit is not None
            else current.get(
                FIELD_SOC_CHG_LIMIT, current.get(FIELD_SOC_CHARGE_LIMIT, 100)
            )
        )
        dis = int(
            discharge_limit
            if discharge_limit is not None
            else current.get(
                FIELD_SOC_DISCHG_LIMIT, current.get(FIELD_SOC_DISCHARGE_LIMIT, 0)
            )
        )
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_SOC_LIMITS,
            cmd=MQTT_CMD_DEVICE_PROPERTY_CHANGE,
            body_fields={
                FIELD_SOC_DISCHG_LIMIT: dis,
                FIELD_SOC_CHG_LIMIT: chg,
            },
        )
        self._apply_local_property_patch(
            device_id,
            {
                FIELD_SOC_CHARGE_LIMIT: chg,
                FIELD_SOC_CHG_LIMIT: chg,
                FIELD_SOC_DISCHARGE_LIMIT: dis,
                FIELD_SOC_DISCHG_LIMIT: dis,
            },
        )

    async def async_set_max_feed_grid(self, device_id: str, watts: int) -> None:
        """Set max feed grid."""
        value = int(watts)
        await self._async_publish_command_ble_first(
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
        """Set max output power.

        3038 routes via DevicePropertyChange (cmd 107), not ControlCombine —
        verified against official app via Frida capture 2026-05-14.
        """
        value = int(watts)
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_MAX_OUT_PW,
            cmd=MQTT_CMD_DEVICE_PROPERTY_CHANGE,
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
        await self._async_publish_command_ble_first(
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
        await self._async_publish_command_ble_first(
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
        await self._async_publish_command_ble_first(
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
        await self._async_publish_command_ble_first(
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
        await self._async_publish_command_ble_first(
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
        await self._async_publish_command_ble_first(
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
        await self._async_publish_command_ble_first(
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
        await self._async_publish_command_ble_first(
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
            raise UpdateFailed(  # noqa: TRY003
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
        try:
            await self.api.async_set_single_mode(
                system_id=system_id,
                single_price=float(price_value),
                currency=str(currency),
            )
        except JackeryAuthError as err:
            _raise_config_entry_auth_failed(
                "Jackery credentials were rejected while saving the single tariff", err
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
                raise HomeAssistantError(  # noqa: TRY003
                    f"Cannot switch to single tariff for {device_id}: missing systemId"
                )
            try:
                latest = await self.api.async_get_power_price(system_id)
            except JackeryAuthError as err:
                _raise_config_entry_auth_failed(
                    "Jackery credentials were rejected while reading the current tariff",  # noqa: E501, RUF100
                    err,
                )
            except JackeryError as err:
                raise HomeAssistantError(  # noqa: TRY003
                    f"Cannot switch to single tariff for {device_id}: {err}"
                ) from err
            if isinstance(latest, dict):
                self._apply_local_price_patch(device_id, latest)
                single_price = latest.get(FIELD_SINGLE_PRICE)
        if single_price is None:
            raise HomeAssistantError(  # noqa: TRY003
                f"Cannot switch to single tariff for {device_id}: missing singlePrice"
            )
        await self.async_set_single_price(device_id, float(single_price))

    @staticmethod
    def _valid_price_sources(
        sources: Any,  # noqa: ANN401, RUF100
    ) -> list[dict[str, Any]]:
        if not isinstance(sources, list):
            return []
        valid: list[dict[str, Any]] = []
        for item in sources:
            if not isinstance(item, dict):
                continue
            company_id = item.get(FIELD_PLATFORM_COMPANY_ID)
            region = item.get(FIELD_COUNTRY) or item.get(FIELD_SYSTEM_REGION)
            if company_id in {None, ""} or not region:
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
        except JackeryAuthError as err:
            _raise_config_entry_auth_failed(
                "Jackery credentials were rejected while reading price sources", err
            )
        except JackeryError:
            _LOGGER.exception("price source fetch failed for %s", device_id)
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
        if raw in {None, ""}:
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
            if raw not in {None, ""}:
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
        if company_id in {None, ""}:
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
        if region not in {None, ""}:
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
            raise HomeAssistantError(  # noqa: TRY003
                f"Cannot set dynamic tariff for {device_id}: missing systemId"
            )

        company_id = source.get(FIELD_PLATFORM_COMPANY_ID)
        region = self._source_region_for_device(device_id, source)
        if company_id is None or company_id == "" or not region:  # noqa: PLC1901
            raise HomeAssistantError(  # noqa: TRY003
                "Cannot set dynamic tariff: selected provider is missing "
                "platformCompanyId/country."
            )

        try:
            await self.api.async_set_dynamic_mode(
                system_id=system_id,
                platform_company_id=int(company_id),
                system_region=str(region),
            )
        except JackeryAuthError as err:
            _raise_config_entry_auth_failed(
                "Jackery credentials were rejected while saving the dynamic tariff", err
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
            raise HomeAssistantError(  # noqa: TRY003
                f"Cannot set dynamic tariff for {device_id}: missing systemId"
            )
        current = ((self.data or {}).get(device_id, {}) or {}).get(PAYLOAD_PRICE) or {}
        company_id = current.get(FIELD_PLATFORM_COMPANY_ID)
        region = current.get(FIELD_SYSTEM_REGION)
        if company_id is None or company_id == "" or not region:  # noqa: PLC1901
            sources = await self._async_price_sources_for_device(device_id)
            source = self._find_matching_price_source(device_id, sources, current)
            if source is not None:
                await self.async_set_price_source(device_id, source)
                return
            if len(sources) == 1:
                await self.async_set_price_source(device_id, sources[0])
                return
            raise HomeAssistantError(  # noqa: TRY003
                "Dynamic tariff requires provider selection. Use the "
                "'Electricity price provider' select entity first."
            )
        try:
            await self.api.async_set_dynamic_mode(
                system_id=system_id,
                platform_company_id=int(company_id),
                system_region=str(region),
            )
        except JackeryAuthError as err:
            _raise_config_entry_auth_failed(
                "Jackery credentials were rejected while saving the dynamic tariff", err
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

    async def async_query_device_info(
        self, device_id: str, *, ensure_mqtt: bool = True
    ) -> None:
        """Query the app's device-property snapshot over MQTT."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_DEVICE_PROPERTY,
            action_id=ACTION_ID_QUERY_DEVICE_PROPERTY,
            cmd=MQTT_CMD_QUERY_DEVICE_PROPERTY,
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

    # ------------------------------------------------------------------
    # Experimental: third-party MQTT bridge (actionId 3046/3047)
    # ------------------------------------------------------------------
    # Per ``HomeCmdAction.smali``: ``SET_THIRD_PARTY_MQTT_CONFIG``
    # (cmd=113 ``ThirdPartMQTTConfig``) and ``GET_THIRD_PARTY_MQTT_CONFIG``
    # (cmd=114 ``QueryThirdPartMQTTConfig``). Body schema from
    # ``ThirdPartyMqttBody.smali``:
    #
    #     {"enable":0|1, "ip":<str>, "port":<int>,
    #      "userName":<str>, "password":<str>, "token":<str>}
    #
    # PROTOCOL.md §15 marks this as server-side blocked (cloud REST relay
    # rejects it). These methods bypass the REST relay and publish the
    # frame directly to the device's MQTT ``command`` topic. The cloud's
    # broker has not been observed to inspect/filter publish payloads, so
    # the frame may still reach the device firmware — that is what these
    # helpers let you test.
    #
    # PROTOCOL.md §14 documents that ``userName``/``password`` are
    # normally AES-256-CBC-PKCS7 encrypted with the device's
    # ``bluetoothKey``. The integration now captures ``bluetoothKey``
    # (see ``device_bluetooth_key``) and encodes the three secret fields via
    # ``third_party_mqtt_codec`` (the ``bb/e.d`` implementation) before
    # publishing — plaintext credentials are rejected by the firmware.
    #
    # Encoding confirmed by docs/jackery_complete_reference.json: the bb/e
    # credential codec is AES-128-CBC/PKCS7 with key=IV=Base64.decode(
    # bluetoothKey), plain base64, NO IV prefix. (The random-IV/IV-prefixed
    # form is the separate BLE *frame* layer in client.ble, not this codec.)
    # Only userName/password/token are encoded; enable/ip/port stay plaintext.

    def _encode_third_party_mqtt_secrets(
        self, device_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        """Return ``body`` with userName/password/token AES-encoded for the wire.

        The firmware expects these three secrets AES-CBC-PKCS7 encoded with the
        device ``bluetoothKey``; plaintext is rejected. Non-secret fields
        (enable/ip/port) are sent as-is. Falls back to the raw value with a
        warning when no usable 16-byte key is available, so the call degrades
        gracefully instead of raising.
        """
        secret_fields = (
            FIELD_THIRD_PARTY_MQTT_USERNAME,
            FIELD_THIRD_PARTY_MQTT_PASSWORD,
            FIELD_THIRD_PARTY_MQTT_TOKEN,
        )
        if not any(body.get(field) for field in secret_fields):
            return body
        from .client.ble import BLE_AES_IV_LEN  # noqa: PLC0415

        key = self.device_bluetooth_key(device_id)
        if key is None or len(key) != BLE_AES_IV_LEN:
            _LOGGER.warning(
                "Jackery third-party MQTT: no 16-byte bluetoothKey for %s; "
                "sending credentials unencrypted (firmware will likely reject)",
                device_id,
            )
            return body
        from .client.third_party_mqtt_codec import encode_third_party_mqtt_field  # noqa: I001, PLC0415

        encoded = dict(body)
        for field in secret_fields:
            value = encoded.get(field)
            if isinstance(value, str) and value:
                encoded[field] = encode_third_party_mqtt_field(value, key)
        return encoded

    async def async_set_third_party_mqtt_config(  # noqa: PLR0913
        self,
        device_id: str,
        *,
        enable: bool,
        ip: str,
        port: int,
        username: str = "",
        password: str = "",
        token: str = "",
    ) -> None:
        """Configure the device's third-party MQTT bridge (experimental).

        Publishes ``SET_THIRD_PARTY_MQTT_CONFIG`` (actionId 3046, cmd 113)
        with a plaintext body. See PROTOCOL.md §15 for the open questions.
        """
        body: dict[str, Any] = {
            FIELD_THIRD_PARTY_MQTT_ENABLE: 1 if enable else 0,
            FIELD_THIRD_PARTY_MQTT_IP: str(ip),
            FIELD_THIRD_PARTY_MQTT_PORT: int(port),
            FIELD_THIRD_PARTY_MQTT_USERNAME: str(username),
            FIELD_THIRD_PARTY_MQTT_PASSWORD: str(password),
            FIELD_THIRD_PARTY_MQTT_TOKEN: str(token),
        }
        _LOGGER.warning(
            "Jackery: publishing experimental SET_THIRD_PARTY_MQTT_CONFIG "
            "(3046) to %s — enable=%s ip=%s:%s user=%r (plaintext credentials)",
            device_id,
            enable,
            ip,
            port,
            username,
        )
        # Mirror keeps the plaintext the user entered (UI display); the wire
        # gets the AES-encoded secrets the firmware expects.
        self._third_party_mqtt_plaintext.setdefault(device_id, {}).update(body)
        body = self._encode_third_party_mqtt_secrets(device_id, body)
        # MQTT-only: the device does not ACK this experimental cloud-config cmd
        # over BLE, so a BLE-first attempt just burns the 5 s ack timeout before
        # falling back. MQTT is the reliable transport for the bridge config.
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_THIRD_PARTY_MQTT_CONFIG,
            action_id=ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG,
            cmd=MQTT_CMD_THIRD_PARTY_MQTT_CONFIG,
            body_fields=body,
        )

    async def async_query_wifi_list(self, device_id: str) -> None:
        """Query WiFi list."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_READ_WIFI_LIST,
            cmd=MQTT_CMD_READ_WIFI_LIST,
            body_fields={},
        )

    async def async_get_time_zone(self, device_id: str) -> None:
        """Get time zone."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_GET_TIME_ZONE,
            cmd=MQTT_CMD_GET_TIME_ZONE,
            body_fields={},
        )

    async def async_send_time_zone(self, device_id: str) -> None:
        """Send time zone."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_SEND_TIME_ZONE,
            cmd=MQTT_CMD_SEND_TIME_ZONE,
            body_fields={},
        )

    async def async_sync_mqtt_connect_info(self, device_id: str) -> None:
        """Sync MQTT connect info."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_SYNC_MQTT_CONNECT_INFO,
            cmd=MQTT_CMD_SYNC_MQTT_CONNECT_INFO,
            body_fields={},
        )

    async def async_query_device_ota_version(self, device_id: str) -> None:
        """Query device OTA version."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_GET_DEVICE_OTA_VERSION,
            cmd=MQTT_CMD_GET_DEVICE_OTA_VERSION,
            body_fields={},
        )

    async def async_notify_device_can_ota(self, device_id: str) -> None:
        """Notify device that an OTA update is available (actionId 3007, cmd 101)."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_NOTIFY_DEVICE_CAN_OTA,
            cmd=MQTT_CMD_NOTIFY_DEVICE_CAN_OTA,
            body_fields={},
        )

    async def async_notify_device_ota_total_page(
        self, device_id: str, total_pages: int
    ) -> None:
        """Send total OTA page count to device (actionId 3008, cmd 102)."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_NOTIFY_DEVICE_OTA_TOTAL_PAGE,
            cmd=MQTT_CMD_NOTIFY_DEVICE_OTA_TOTAL_PAGE,
            body_fields={"totalPage": int(total_pages)},
        )

    async def async_device_get_ota_page_data(
        self, device_id: str, page_index: int
    ) -> None:
        """Request a specific OTA firmware page from the device (actionId 3009, cmd 103)."""  # noqa: E501
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_DEVICE_GET_OTA_PAGE_DATA,
            cmd=MQTT_CMD_DEVICE_GET_OTA_PAGE_DATA,
            body_fields={"pageIndex": int(page_index)},
        )

    async def async_bind_smart_part(self, device_id: str, accessory_sn: str) -> None:
        """Bind a smart accessory to the device (actionId 3012, cmd 108)."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_BIND_SMART_ACCESSORY,
            action_id=ACTION_ID_BIND_SMART_PART,
            cmd=MQTT_CMD_BIND_SMART_PART,
            body_fields={"sn": accessory_sn},
        )

    async def async_unbind_smart_part(self, device_id: str, accessory_sn: str) -> None:
        """Unbind a smart accessory from the device (actionId 3013, cmd 109)."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_REMOVE_SMART_ACCESSORY,
            action_id=ACTION_ID_UNBIND_SMART_PART,
            cmd=MQTT_CMD_UNBIND_SMART_PART,
            body_fields={"sn": accessory_sn},
        )

    async def async_query_wifi_config(self, device_id: str) -> None:
        """Query WiFi config."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_WIFI_CONFIG,
            action_id=ACTION_ID_QUERY_WIFI_CONFIG,
            cmd=MQTT_CMD_QUERY_WIFI_CONFIG,
            body_fields={},
        )

    async def async_send_device_schedule(
        self, device_id: str, action_id: int, body: dict[str, Any] | str
    ) -> None:
        """Send device schedule."""
        parsed_body: dict[str, Any] = (
            body if isinstance(body, dict) else json.loads(body)
        )
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_DOWNLOAD_DEVICE_SCHEDULE,
            action_id=action_id,
            cmd=MQTT_CMD_DOWNLOAD_DEVICE_SCHEDULE,
            body_fields=parsed_body,
        )

    async def async_read_device_schedule(
        self,
        device_id: str,
        task_type: int,
        req_type: int = 0,
        read_type: int = 0,
        smart_plug_sn: str | None = None,
    ) -> None:
        """Read device schedule."""
        body: dict[str, Any] = {
            "taskType": task_type,
            "reqType": req_type,
            "readType": read_type,
        }
        if smart_plug_sn:
            body[FIELD_DEVICE_SN] = smart_plug_sn
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_DOWNLOAD_DEVICE_SCHEDULE,
            action_id=ACTION_ID_TIMER_TASK_READ,
            cmd=MQTT_CMD_DOWNLOAD_DEVICE_SCHEDULE,
            body_fields=body,
        )

    def third_party_mqtt_config_plaintext(self, device_id: str) -> dict[str, Any]:
        """Return the device's third-party MQTT config for the UI entities.

        Merges the config the device reported over MQTT
        (``PAYLOAD_THIRD_PARTY_MQTT_CONFIG``) with the local plaintext mirror of
        values the user entered. The local mirror wins, because the device never
        echoes the plaintext password/token back — so a just-entered secret is
        not erased by a device report that omits it.
        """
        reported = ((self.data or {}).get(device_id, {}) or {}).get(
            PAYLOAD_THIRD_PARTY_MQTT_CONFIG
        )
        merged: dict[str, Any] = dict(reported) if isinstance(reported, dict) else {}
        merged.update(self._third_party_mqtt_plaintext.get(device_id, {}))
        return merged

    async def async_update_third_party_mqtt_config(
        self, device_id: str, body_fields: dict[str, Any]
    ) -> None:
        """Update third-party MQTT config with arbitrary fields."""
        self._third_party_mqtt_plaintext.setdefault(device_id, {}).update(body_fields)
        body_fields = self._encode_third_party_mqtt_secrets(device_id, body_fields)
        # MQTT-only — see async_set_third_party_mqtt_config (BLE never ACKs this).
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_THIRD_PARTY_MQTT_CONFIG,
            action_id=ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG,
            cmd=MQTT_CMD_THIRD_PARTY_MQTT_CONFIG,
            body_fields=body_fields,
        )

    async def async_query_third_party_mqtt_config(self, device_id: str) -> None:
        """Read back the device's third-party MQTT bridge config (experimental).

        Publishes ``GET_THIRD_PARTY_MQTT_CONFIG`` (actionId 3047, cmd 114).
        The response — if any — arrives on the ``device`` topic and is
        captured in the redacted payload-debug log. Inspect
        ``jackery_solarvault_payload_debug.jsonl`` after calling.
        """
        _LOGGER.warning(
            "Jackery: publishing experimental GET_THIRD_PARTY_MQTT_CONFIG "
            "(3047) to %s — check payload_debug log for the response",
            device_id,
        )
        # MQTT-only — the query cmd is not ACKed over BLE (5 s timeout waste).
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG,
            action_id=ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
            cmd=MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG,
            body_fields={},
        )

    async def async_query_battery_packs(
        self, device_id: str, *, ensure_mqtt: bool = True
    ) -> None:
        """Query battery packs (devType=1, ``READ_SUB_DEVICE_BATTERY_PACK``)."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
            action_id=ACTION_ID_SUBDEVICE_3014,
            cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
            body_fields={FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_BATTERY_PACK},
            ensure_mqtt=ensure_mqtt,
        )

    async def async_query_smart_meter(
        self, device_id: str, *, ensure_mqtt: bool = True
    ) -> None:
        """Query smart meter / CT (devType=3, ``READ_SUB_DEVICE_CT``)."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
            action_id=ACTION_ID_SUBDEVICE_3031,
            cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
            body_fields={FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_CT},
            ensure_mqtt=ensure_mqtt,
        )

    async def async_query_meter_heads(
        self, device_id: str, *, ensure_mqtt: bool = True
    ) -> None:
        """Query meter-head / collector subdevices (devType=4)."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
            action_id=ACTION_ID_SUBDEVICE_3033,
            cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
            body_fields={FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_METER_HEAD},
            ensure_mqtt=ensure_mqtt,
        )

    async def async_query_smart_plugs(
        self, device_id: str, *, ensure_mqtt: bool = True
    ) -> None:
        """Query smart plug / socket subdevices.

        Mirrors the Jackery app's ``READ_SUB_DEVICE_SOCKET`` action:
        ``messageType=QuerySubDeviceGroupProperty`` with ``actionId=3032``,
        ``cmd=110`` and ``devType=6`` per ``HomeSubDeviceType.SOCKET``.
        The response arrives as ``UploadSubDeviceGroupProperty`` with a
        ``plugs`` array (see docs/PROTOCOL.md §2 and PROTOCOL.md §3).
        """
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
            action_id=ACTION_ID_SUBDEVICE_3032,
            cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
            body_fields={FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET},
            ensure_mqtt=ensure_mqtt,
        )

    async def async_set_smart_plug_switch(
        self, device_id: str, *, plug_sn: str, on: bool
    ) -> None:
        """Toggle a smart plug on or off.

        Mirrors the Jackery app's ``SUB_CONTROL_SOCKET_SWITCH`` (verified
        against ``HomeCmdAction.smali``): ``messageType=ControlSubDevice``,
        ``cmd=111``, ``actionId=3024`` with body ``{devType: 6, deviceSn:
        <plug_sn>, sysSwitch: 0|1}``. The Jackery device echoes the new state
        back in the next ``UploadSubDeviceGroupProperty`` frame for
        ``plugs``.
        """
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_SUB_DEVICE,
            action_id=ACTION_ID_CONTROL_SOCKET_SWITCH,
            cmd=MQTT_CMD_CONTROL_SUB_DEVICE,
            body_fields={
                FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET,
                FIELD_DEVICE_SN: plug_sn,
                FIELD_SYS_SWITCH: 1 if on else 0,
            },
        )
        # Optimistic local update so the entity reflects the new state until
        # the next ``UploadSubDeviceGroupProperty`` frame confirms it.
        self._apply_local_smart_plug_switch_patch(device_id, plug_sn, on)

    async def async_set_smart_plug_priority(
        self, device_id: str, *, plug_sn: str, enabled: bool
    ) -> None:
        """Toggle smart-plug priority for load management.

        Mirrors the Jackery app's ``SUB_CONTROL_SOCKET_PRI_ENABLE``:
        ``messageType=ControlSubDevice``, ``cmd=111``, ``actionId=3025`` with
        body ``{devType: 6, deviceSn: <plug_sn>, socketPri: 0|1}``.
        """
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_SUB_DEVICE,
            action_id=ACTION_ID_CONTROL_SOCKET_PRIORITY,
            cmd=MQTT_CMD_CONTROL_SUB_DEVICE,
            body_fields={
                FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET,
                FIELD_DEVICE_SN: plug_sn,
                FIELD_SOCKET_PRIORITY: 1 if enabled else 0,
            },
        )
        self._apply_local_smart_plug_patch(
            device_id,
            plug_sn,
            {
                FIELD_SOCKET_PRIORITY: 1 if enabled else 0,
            },
        )

    def _apply_local_smart_plug_switch_patch(
        self, device_id: str, plug_sn: str, on: bool
    ) -> None:
        """Mirror the requested switch state into ``smart_plugs`` immediately."""
        target = 1 if on else 0
        self._apply_local_smart_plug_patch(
            device_id,
            plug_sn,
            {
                FIELD_SYS_SWITCH: target,
                FIELD_SWITCH_STATE: target,
            },
        )

    def _apply_local_smart_plug_patch(
        self, device_id: str, plug_sn: str, updates: dict[str, Any]
    ) -> None:
        """Mirror requested smart-plug fields into ``smart_plugs`` immediately."""
        if not self.data or device_id not in self.data:
            return
        payload = dict(self.data[device_id])
        plugs = payload.get(PAYLOAD_SMART_PLUGS)
        if not isinstance(plugs, list):
            return
        updated_plugs = []
        touched = False
        for plug in plugs:
            if not isinstance(plug, dict):
                updated_plugs.append(plug)
                continue
            sn = (
                plug.get(FIELD_DEVICE_SN)
                or plug.get(FIELD_DEV_SN)
                or plug.get(FIELD_SN)
            )
            if str(sn) == str(plug_sn):
                next_plug = dict(plug)
                next_plug.update(updates)
                updated_plugs.append(next_plug)
                touched = True
            else:
                updated_plugs.append(plug)
        if touched:
            payload[PAYLOAD_SMART_PLUGS] = updated_plugs
            new_data = dict(self.data)
            new_data[device_id] = payload
            self._push_partial_update(new_data)

    async def async_query_subdevice_combo(
        self, device_id: str, *, ensure_mqtt: bool = True
    ) -> None:
        """Query combo subdevice (devType=2, ``READ_SUB_DEVICE_COMBO``)."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
            action_id=ACTION_ID_SUBDEVICE_3037,
            cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
            body_fields={FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_COMBO},
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

    async def async_set_ct_phase(self, device_id: str, ct_sn: str, phase: int) -> None:
        """Assign a CT (current transformer) sub-device to a phase (1..4).

        Verified body shape from Frida capture (2026-05-14, app v2.1.1):
        ``{"devType":3,"deviceSn":"<ct-sn>","schePhase":<1..4>,"cmd":111}``.
        ``ct_sn`` is the CT's own MAC/serial (sub-device), not the SolarVault.
        """
        if not ct_sn:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="mqtt_missing_subdevice_sn",
                translation_placeholders={"device_id": str(device_id)},
            )
        phase_int = int(phase)
        if phase_int not in {1, 2, 3, 4}:
            raise HomeAssistantError(f"CT phase must be 1..4 (got {phase_int})")  # noqa: TRY003
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_SUB_DEVICE,
            action_id=ACTION_ID_CT_PHASE,
            cmd=MQTT_CMD_CONTROL_SUB_DEVICE,
            body_fields={
                FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_CT,
                FIELD_DEVICE_SN: ct_sn,
                FIELD_SCHE_PHASE: phase_int,
            },
        )

    async def _async_query_subdevices_for_missing(  # noqa: PLR0912, PLR0915
        self,
        *,
        force: bool = False,
        ensure_mqtt: bool = True,
        snapshot: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Query MQTT sub-device status for accessories that need backfill."""
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
            should_query_meter_heads = force or self._has_meter_head_accessory(payload)
            should_query_plugs = force or self._has_smart_plug_accessory(payload)
            if (
                not should_query_meter
                and not should_query_packs
                and not should_query_meter_heads
                and not should_query_plugs
            ):
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
                except ConfigEntryAuthFailed:
                    raise
                except (TimeoutError, HomeAssistantError, JackeryError) as err:
                    _LOGGER.debug(
                        "Jackery battery-pack query failed for %s: %s", device_id, err
                    )
                try:
                    await self.async_query_subdevice_combo(
                        device_id, ensure_mqtt=ensure_mqtt
                    )
                except ConfigEntryAuthFailed:
                    raise
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
                except ConfigEntryAuthFailed:
                    raise
                except (TimeoutError, HomeAssistantError, JackeryError) as err:
                    _LOGGER.debug(
                        "Jackery smart-meter query failed for %s: %s", device_id, err
                    )
            if should_query_meter_heads:
                try:
                    await self.async_query_meter_heads(
                        device_id, ensure_mqtt=ensure_mqtt
                    )
                except ConfigEntryAuthFailed:
                    raise
                except (TimeoutError, HomeAssistantError, JackeryError) as err:
                    _LOGGER.debug(
                        "Jackery meter-head query failed for %s: %s", device_id, err
                    )
            if should_query_plugs:
                try:
                    await self.async_query_smart_plugs(
                        device_id, ensure_mqtt=ensure_mqtt
                    )
                except ConfigEntryAuthFailed:
                    raise
                except (TimeoutError, HomeAssistantError, JackeryError) as err:
                    _LOGGER.debug(
                        "Jackery smart-plug query failed for %s: %s", device_id, err
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

    def _schedule_statistics_import(
        self,
        snapshot: dict[str, dict[str, Any]],
    ) -> None:
        """Queue recorder statistic imports without blocking setup or polling."""
        if not self._statistics_import_ready:
            return
        if (
            self._statistics_import_task is not None
            and not self._statistics_import_task.done()
        ):
            return
        now_monotonic = time.monotonic()
        if now_monotonic - self._last_stat_import_monotonic < SLOW_METRICS_INTERVAL_SEC:
            return
        self._last_stat_import_monotonic = now_monotonic
        self._statistics_import_task = self.hass.async_create_task(
            self._async_statistics_import_job(dict(snapshot)),
            name=f"{DOMAIN}_statistics_import",
        )

    async def _async_statistics_import_job(
        self,
        snapshot: dict[str, dict[str, Any]],
    ) -> None:
        """Run recorder statistic import/backfill in the background."""
        try:
            await self._async_import_and_repair_app_chart_statistics(snapshot)
        except asyncio.CancelledError:
            raise
        except ConfigEntryAuthFailed as err:
            self._defer_background_auth_failure(err)
        except Exception:
            _LOGGER.exception("Jackery recorder-statistics import failed")
        finally:
            if asyncio.current_task() is self._statistics_import_task:
                self._statistics_import_task = None

    async def _async_mqtt_backfill_queries(
        self, snapshot: dict[str, dict[str, Any]]
    ) -> None:
        """Refresh app-side MQTT-only data after the HTTP poll has completed."""
        try:
            await self._async_query_subdevices_for_missing(snapshot=snapshot)
            await self._async_query_system_info_for_missing(snapshot=snapshot)
            await self._async_query_weather_plan_for_missing(snapshot=snapshot)
        except ConfigEntryAuthFailed as err:
            self._defer_background_auth_failure(err)
        except Exception:
            _LOGGER.exception("Jackery MQTT backfill query failed")

    # ------------------------------------------------------------------
    # Statistics import & data-quality reporting
    # ------------------------------------------------------------------

    def _local_statistic_start(self, bucket_date: date | datetime) -> datetime:
        """Return a UTC timestamp for a local app-statistic bucket start."""
        timezone = self._local_timezone()
        if isinstance(bucket_date, datetime):
            if bucket_date.tzinfo is None:
                local_start = bucket_date.replace(tzinfo=timezone)
            else:
                local_start = bucket_date.astimezone(timezone)
        else:
            local_start = datetime.combine(
                bucket_date,
                datetime.min.time(),
                tzinfo=timezone,
            )
        return dt_util.as_utc(local_start)

    @staticmethod
    def _stat_row_start(row: Any) -> float | None:  # noqa: ANN401, RUF100
        """Return a statistics row start timestamp in seconds."""
        start = (
            row.get("start") if isinstance(row, dict) else getattr(row, "start", None)
        )
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
            from homeassistant.components.recorder import get_instance  # noqa: PLC0415
            from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
                statistics_during_period,
            )
        except ImportError, RuntimeError:
            _LOGGER.exception("Recorder statistics API unavailable")
            return 0.0

        try:
            recorder = get_instance(self.hass)
        except Exception:
            _LOGGER.exception("Recorder instance unavailable")
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
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Could not read previous statistics for %s: %s",
                statistic_id,
                err,
            )
            return 0.0

        rows = existing.get(statistic_id, []) if isinstance(existing, dict) else []
        previous: tuple[float, float] | None = None
        for row in rows:
            row_start = self._stat_row_start(row)
            row_sum = safe_float(
                row.get("sum") if isinstance(row, dict) else getattr(row, "sum", None)
            )
            if row_start is None or row_sum is None:
                continue
            if row_start >= starts[0].timestamp():
                continue
            if previous is None or row_start > previous[0]:
                previous = (row_start, row_sum)
        if previous is None:
            return 0.0
        return round(previous[1], 5)

    async def _async_entity_statistic_offsets(
        self,
        statistic_id: str,
        start: datetime,
        reset_start: datetime,
    ) -> tuple[float, float]:
        """Return previous ``sum`` and same-period ``state`` for an entity."""
        try:
            from homeassistant.components.recorder import get_instance  # noqa: PLC0415
            from homeassistant.components.recorder.db_schema import (  # noqa: PLC0415
                Statistics,
                StatisticsMeta,
            )
            from homeassistant.helpers.recorder import session_scope  # noqa: PLC0415
        except ImportError, RuntimeError:
            _LOGGER.exception("Recorder entity-statistic offset unavailable")
            return 0.0, 0.0

        try:
            recorder = get_instance(self.hass)
        except Exception:
            _LOGGER.exception("Recorder instance unavailable for entity offset")
            return 0.0, 0.0

        start_ts = start.timestamp()
        reset_ts = reset_start.timestamp()

        def _load_offsets() -> tuple[float, float]:
            with session_scope(session=recorder.get_session()) as session:
                meta = (
                    session
                    .query(StatisticsMeta.id)
                    .filter(StatisticsMeta.statistic_id == statistic_id)
                    .first()
                )
                if meta is None:
                    return 0.0, 0.0
                row = (
                    session
                    .query(
                        Statistics.sum,
                        Statistics.state,
                        Statistics.last_reset_ts,
                    )
                    .filter(
                        Statistics.metadata_id == meta[0],
                        Statistics.start_ts < start_ts,
                    )
                    .order_by(Statistics.start_ts.desc())
                    .first()
                )
                if row is None:
                    return 0.0, 0.0
                sum_offset = safe_float(row[0]) or 0.0
                state_offset = 0.0
                row_reset = safe_float(row[2])
                if row_reset is not None and abs(row_reset - reset_ts) < 1:
                    state_offset = safe_float(row[1]) or 0.0
                return round(sum_offset, 5), round(state_offset, 5)

        try:
            return await recorder.async_add_executor_job(_load_offsets)
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Could not read previous entity statistics for %s: %s",
                statistic_id,
                err,
            )
            return 0.0, 0.0

    async def _async_compiled_statistic_hour_starts(
        self,
        starts: list[datetime],
    ) -> set[int]:
        """Return HA statistic hours that Recorder has already compiled."""
        if not starts:
            return set()
        try:
            from homeassistant.components.recorder import get_instance  # noqa: I001, PLC0415
            from homeassistant.components.recorder.db_schema import StatisticsRuns  # noqa: PLC0415
            from homeassistant.helpers.recorder import session_scope  # noqa: PLC0415
        except ImportError, RuntimeError:
            _LOGGER.exception("Recorder run markers unavailable")
            return set()

        try:
            recorder = get_instance(self.hass)
        except Exception:
            _LOGGER.exception("Recorder instance unavailable for run markers")
            return set()

        range_start = min(starts)
        range_end = max(starts) + timedelta(hours=1)

        def _load_compiled_hours() -> set[int]:
            with session_scope(session=recorder.get_session()) as session:
                return {
                    round(item[0].timestamp()) - 55 * 60
                    for item in session
                    .query(StatisticsRuns.start)
                    .filter(
                        StatisticsRuns.start >= range_start,
                        StatisticsRuns.start < range_end + timedelta(hours=1),
                    )
                    .all()
                    if item[0] is not None and item[0].minute == 55  # noqa: PLR2004
                }

        try:
            return await recorder.async_add_executor_job(_load_compiled_hours)
        except Exception:
            _LOGGER.exception("Could not read Recorder run markers")
            return set()

    def _entity_statistic_ids_by_key(self, device_id: str) -> dict[str, str]:
        """Return current entity statistic IDs for app-chart repair keys."""
        try:
            from homeassistant.components.sensor import DOMAIN as SENSOR_DOMAIN  # noqa: I001, PLC0415
            from homeassistant.helpers import entity_registry as er  # noqa: PLC0415
        except ImportError, RuntimeError:
            _LOGGER.exception("Entity registry unavailable for entity repair")
            return {}

        registry = er.async_get(self.hass)
        keys: set[str] = set()
        for periods in _ENTITY_STATISTIC_KEY_BY_METRIC_PERIOD.values():
            keys.update(periods.values())
        entity_ids: dict[str, str] = {}
        for entity_key in keys:
            entity_id = registry.async_get_entity_id(
                SENSOR_DOMAIN,
                DOMAIN,
                f"{device_id}_{entity_key}",
            )
            if entity_id is not None:
                entity_ids[entity_key] = entity_id
        return entity_ids

    def _entity_reset_start(self, start: datetime, reset_period: str) -> datetime:
        """Return the UTC reset timestamp for one entity-statistic row."""
        timezone = self._local_timezone()
        local_start = start.astimezone(timezone)
        local_date = local_start.date()
        if reset_period == DATE_TYPE_WEEK:
            reset_date = local_date - timedelta(days=local_date.weekday())
        elif reset_period == DATE_TYPE_MONTH:
            reset_date = date(local_date.year, local_date.month, 1)
        elif reset_period == DATE_TYPE_YEAR:
            reset_date = date(local_date.year, 1, 1)
        else:
            reset_date = local_date
        return self._local_statistic_start(reset_date)

    @staticmethod
    def _entity_targets_for_app_points(
        metric_key: str,
        date_type: str,
    ) -> tuple[tuple[str, str, bool], ...]:
        """Return entity-key/reset/cumulative-state targets for app buckets."""
        periods = _ENTITY_STATISTIC_KEY_BY_METRIC_PERIOD.get(metric_key)
        if not periods:
            return ()
        if date_type == DATE_TYPE_DAY:
            key = periods.get(DATE_TYPE_DAY)
            return ((key, DATE_TYPE_DAY, True),) if key else ()
        if date_type == DATE_TYPE_WEEK:
            key = periods.get(DATE_TYPE_WEEK)
            return ((key, DATE_TYPE_WEEK, True),) if key else ()
        if date_type == DATE_TYPE_MONTH:
            targets: list[tuple[str, str, bool]] = []
            month_key = periods.get(DATE_TYPE_MONTH)
            year_key = periods.get(DATE_TYPE_YEAR)
            if month_key:
                targets.append((month_key, DATE_TYPE_MONTH, True))
            if year_key:
                targets.append((year_key, DATE_TYPE_YEAR, True))
            return tuple(targets)
        return ()

    def _completed_entity_app_points(  # noqa: PLR6301
        self,
        points: list[Any],
        *,
        date_type: str,
        reset_period: str,
        today: date,
    ) -> list[Any]:
        """Filter app points to completed buckets for entity-stat imports."""
        if date_type == DATE_TYPE_DAY:
            return points
        completed: list[Any] = []
        for point in points:
            start = point.start_date
            point_date = start.date() if isinstance(start, datetime) else start
            if not isinstance(point_date, date):
                continue
            if reset_period in {DATE_TYPE_DAY, DATE_TYPE_WEEK, DATE_TYPE_MONTH}:
                if point_date >= today:
                    continue
            elif reset_period == DATE_TYPE_YEAR and (
                point_date.year,
                point_date.month,
            ) >= (today.year, today.month):
                continue
            completed.append(point)
        return completed

    def _entity_statistics_from_contributions(
        self,
        contributions: list[tuple[datetime, float, str, bool]],
        *,
        compiled_hour_starts: set[int],
        sum_offset: float,
        state_offset: float,
    ) -> list[StatisticData]:
        """Build non-negative HA entity statistics from app bucket values."""
        statistics: list[StatisticData] = []
        cumulative_sum = max(0.0, sum_offset)
        current_reset: datetime | None = None
        running_state = 0.0
        for start, value, reset_period, cumulative_state in sorted(
            contributions,
            key=operator.itemgetter(0),
        ):
            hour_start = round(start.timestamp())
            if hour_start not in compiled_hour_starts:
                continue
            reset_start = self._entity_reset_start(start, reset_period)
            if current_reset is None or reset_start != current_reset:
                current_reset = reset_start
                running_state = state_offset if not statistics else 0.0
            bucket_value = max(0.0, value)
            if cumulative_state:
                running_state = round(running_state + bucket_value, 5)
                state = running_state
            else:
                state = round(bucket_value, 5)
            cumulative_sum = round(cumulative_sum + bucket_value, 5)
            statistics.append({
                "start": start,
                "state": state,
                "sum": max(0.0, cumulative_sum),
                "last_reset": reset_start,
            })
        return statistics

    async def _async_import_app_chart_entity_statistics_for_device(  # noqa: PLR0912, PLR0914, PLR0915
        self,
        *,
        device_id: str,
        payload: dict[str, Any],
        source_batches: list[tuple[str, dict[str, dict[str, Any]]]],
    ) -> tuple[int, int]:
        """Import app buckets into HA-owned entity statistics safely."""
        entity_ids = self._entity_statistic_ids_by_key(device_id)
        if not entity_ids:
            return 0, 0
        try:
            from homeassistant.components.recorder.models import (  # noqa: I001, PLC0415
                StatisticMeanType,
                StatisticMetaData,
            )
            from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
                async_import_statistics,
            )
            from homeassistant.const import UnitOfEnergy  # noqa: PLC0415
            from homeassistant.util.unit_conversion import EnergyConverter  # noqa: PLC0415
        except ImportError, RuntimeError:
            _LOGGER.exception("Recorder entity statistics import unavailable")
            return 0, 1

        today = self._local_today()
        now = self._local_now()
        contributions: dict[str, list[tuple[datetime, float, str, bool]]] = {}

        for date_type, section_sources in source_batches:
            for section_prefix, stat_key, metric_key, _label in APP_CHART_STAT_METRICS:
                if date_type == DATE_TYPE_DAY:
                    source = section_sources.get(section_prefix)
                    if isinstance(source, dict):
                        section = f"{section_prefix}_{date_type}"
                        points = day_power_energy_points(
                            source,
                            section,
                            stat_key,
                            bucket_minutes=60,
                            today=today,
                            now=now,
                        )
                    else:
                        points = self._day_chart_points_for_metric(
                            payload,
                            section_prefix,
                            stat_key,
                            metric_key,
                            bucket_minutes=60,
                            now=now,
                        )
                else:
                    source = section_sources.get(section_prefix)
                    if not isinstance(source, dict):
                        continue
                    section = f"{section_prefix}_{date_type}"
                    points = trend_series_points(
                        source,
                        section,
                        stat_key,
                        today=today,
                    )
                if not points:
                    continue
                for (
                    entity_key,
                    reset_period,
                    cumulative_state,
                ) in self._entity_targets_for_app_points(metric_key, date_type):
                    entity_id = entity_ids.get(entity_key)
                    if entity_id is None:
                        continue
                    completed_points = self._completed_entity_app_points(
                        points,
                        date_type=date_type,
                        reset_period=reset_period,
                        today=today,
                    )
                    for point in completed_points:
                        value = safe_float(point.value)
                        if value is None or value < 0:
                            continue
                        start = self._local_statistic_start(point.start_date)
                        contributions.setdefault(entity_id, []).append((
                            start,
                            value,
                            reset_period,
                            cumulative_state,
                        ))
        if not contributions:
            return 0, 0

        all_starts = [
            start
            for entity_contributions in contributions.values()
            for start, _value, _reset_period, _cumulative_state in entity_contributions
        ]
        compiled_hour_starts = await self._async_compiled_statistic_hour_starts(
            all_starts
        )
        if not compiled_hour_starts:
            return 0, 0

        imported_rows = 0
        failed_rows = 0
        for statistic_id, entity_contributions in sorted(contributions.items()):
            filtered = [
                item
                for item in sorted(entity_contributions, key=operator.itemgetter(0))
                if round(item[0].timestamp()) in compiled_hour_starts
            ]
            if not filtered:
                continue
            first_start = filtered[0][0]
            first_reset = self._entity_reset_start(first_start, filtered[0][2])
            sum_offset, state_offset = await self._async_entity_statistic_offsets(
                statistic_id,
                first_start,
                first_reset,
            )
            statistics = self._entity_statistics_from_contributions(
                filtered,
                compiled_hour_starts=compiled_hour_starts,
                sum_offset=sum_offset,
                state_offset=state_offset,
            )
            if not statistics:
                continue
            metadata = StatisticMetaData(
                mean_type=StatisticMeanType.NONE,
                has_sum=True,
                name=None,
                source="recorder",
                statistic_id=statistic_id,
                unit_class=EnergyConverter.UNIT_CLASS,
                unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR,
            )
            try:
                async_import_statistics(self.hass, metadata, statistics)
            except Exception as err:  # statistics import must not abort the whole cycle  # noqa: BLE001
                failed_rows += len(statistics)
                _LOGGER.debug(
                    "Could not import Jackery entity statistics for %s: %s",
                    statistic_id,
                    err,
                )
                continue
            imported_rows += len(statistics)
        return imported_rows, failed_rows

    def _current_app_chart_entity_source_batches(  # noqa: PLR6301
        self,
        payload: dict[str, Any],
    ) -> list[tuple[str, dict[str, dict[str, Any]]]]:
        """Return current-payload period sources safe for entity history import."""
        prefixes = tuple(dict.fromkeys(metric[0] for metric in APP_CHART_STAT_METRICS))
        source_batches: list[tuple[str, dict[str, dict[str, Any]]]] = []
        for date_type in (DATE_TYPE_WEEK, DATE_TYPE_MONTH):
            section_sources: dict[str, dict[str, Any]] = {}
            for section_prefix in prefixes:
                source = payload.get(f"{section_prefix}_{date_type}")
                if isinstance(source, dict):
                    section_sources[section_prefix] = source
            if section_sources:
                source_batches.append((date_type, section_sources))
        return source_batches

    async def _async_import_current_app_chart_entity_statistics(
        self,
        snapshot: dict[str, dict[str, Any]],
    ) -> dict[str, tuple[int, int]]:
        """Import completed current-payload app buckets into entity statistics."""
        results: dict[str, tuple[int, int]] = {}
        for device_id, payload in snapshot.items():
            source_batches = self._current_app_chart_entity_source_batches(payload)
            if not source_batches:
                continue
            (
                imported_rows,
                failed_rows,
            ) = await self._async_import_app_chart_entity_statistics_for_device(
                device_id=device_id,
                payload=payload,
                source_batches=source_batches,
            )
            if imported_rows or failed_rows:
                results[device_id] = (imported_rows, failed_rows)
                _LOGGER.debug(
                    "Jackery current-payload entity history import for %s "
                    "imported %d row(s), %d row(s) failed",
                    device_id,
                    imported_rows,
                    failed_rows,
                )
        return results

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
            from homeassistant.helpers import issue_registry as ir  # noqa: PLC0415
        except ImportError, RuntimeError:
            if warnings:
                examples = "; ".join(
                    format_data_quality_warning(warning)
                    for warning in warnings[:DATA_QUALITY_REPAIR_EXAMPLE_LIMIT]
                )
                _LOGGER.warning(
                    "Jackery app/cloud statistics are inconsistent; diagnostics contain %d warning(s): %s",  # noqa: E501, RUF100
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
            is_fixable=True,
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
            data={
                "entry_id": self.entry.entry_id,
                "count": str(len(warnings)),
                "metric": str(
                    first.get(DATA_QUALITY_KEY_LABEL)
                    or first.get(DATA_QUALITY_KEY_METRIC_KEY)
                    or "unknown"
                ),
                "examples": examples or "unknown",
            },
        )

    async def async_load_statistics_backfill_state(self) -> None:
        """Load persistent recorder-statistics repair state."""
        if self._statistics_backfill_state_loaded:
            return
        loaded = await self._statistics_backfill_store.async_load()
        if isinstance(loaded, dict):
            devices = loaded.get(_STATISTICS_BACKFILL_STORE_DEVICES)
            if isinstance(devices, dict):
                self._statistics_backfill_state = {
                    _STATISTICS_BACKFILL_STORE_DEVICES: devices
                }
        self._statistics_backfill_state_loaded = True

    async def _async_save_statistics_backfill_state(self) -> None:
        """Persist recorder-statistics repair state."""
        await self._statistics_backfill_store.async_save(
            self._statistics_backfill_state
        )

    async def _async_ensure_statistics_backfill_state_loaded(self) -> None:
        """Load persistent repair state on demand."""
        if not self._statistics_backfill_state_loaded:
            await self.async_load_statistics_backfill_state()

    @property
    def statistics_backfill_diagnostics(self) -> dict[str, Any]:
        """Return redaction-safe statistics repair diagnostics."""
        devices = self._statistics_backfill_state.get(
            _STATISTICS_BACKFILL_STORE_DEVICES
        )
        if not isinstance(devices, dict):
            devices = {}
        redacted_devices: dict[str, Any] = {}
        for index, device_id in enumerate(
            sorted(devices, key=str),
            start=1,
        ):
            state = devices.get(device_id)
            redacted_devices[f"device_{index}"] = (
                dict(state) if isinstance(state, dict) else {}
            )
        return {
            "loaded": self._statistics_backfill_state_loaded,
            "tracked_devices": len(redacted_devices),
            "devices": redacted_devices,
        }

    def _statistics_backfill_device_state(self, device_id: str) -> dict[str, Any]:
        """Return the mutable persistent repair state for one device."""
        devices = self._statistics_backfill_state.setdefault(
            _STATISTICS_BACKFILL_STORE_DEVICES,
            {},
        )
        if not isinstance(devices, dict):
            devices = {}
            self._statistics_backfill_state[_STATISTICS_BACKFILL_STORE_DEVICES] = (
                devices
            )
        state = devices.setdefault(str(device_id), {})
        if not isinstance(state, dict):
            state = {}
            devices[str(device_id)] = state
        return state

    @staticmethod
    def _parse_statistics_backfill_date(value: object) -> date | None:
        """Parse a persisted ISO date for statistics repair decisions."""
        if not isinstance(value, str):
            return None
        try:
            return date.fromisoformat(value[:10])
        except ValueError:
            return None

    @staticmethod
    def _statistics_current_year_recovery_needed(
        *,
        last_success: date,
        last_repair: date | None,
        failed_bucket_count: int,
        today: date,
    ) -> bool:
        """Return True when an old success marker may have skipped history.

        Older builds could persist ``last_successful_import_date`` from the
        current snapshot while the historical month/year repair never ran
        because a live MQTT window returned early. In that state the normal
        month-boundary branch would never revisit elapsed months of the same
        calendar year. Use ``last_repair_date`` as the recovery marker: once
        a repair has run in the same month as ``last_success``, the one-time
        current-year recovery is complete.
        """
        if today.month == 1:
            return False
        if last_success.year != today.year:
            return False
        if failed_bucket_count > 0:
            return last_repair is None or last_repair < today
        if last_repair is None:
            return True
        last_success_month = last_success.replace(day=1)
        return last_repair < last_success_month

    @staticmethod
    def _iter_calendar_months(start_date: date, end_date: date) -> list[date]:
        """Return first-of-month dates intersecting an inclusive date range.

        Static method (parallel to ``_iter_calendar_weeks`` below). The
        missing ``@staticmethod`` decorator caused
        ``self._iter_calendar_months(from_date, to_date)`` to pass three
        positional arguments to a two-arg function, breaking every
        ``async_import_statistics`` entity-stat repair attempt — the
        Recorder-side ``sensor.solarvault_3_pro_max_*`` entity statistic
        IDs that feed the Energy Dashboard's flow accounting. Observed
        2026-05-16 production log:

            Jackery recorder-statistics import failed:
            JackerySolarVaultCoordinator._iter_calendar_months() takes
            2 positional arguments but 3 were given
        """
        cursor = start_date.replace(day=1)
        end_month = end_date.replace(day=1)
        months: list[date] = []
        while cursor <= end_month:
            months.append(cursor)
            if cursor.month == 12:  # noqa: PLR2004
                cursor = cursor.replace(year=cursor.year + 1, month=1)
            else:
                cursor = cursor.replace(month=cursor.month + 1)
        return months

    @staticmethod
    def _iter_calendar_weeks(start_date: date, end_date: date) -> list[date]:
        """Return Monday week starts intersecting an inclusive date range."""
        cursor = start_date - timedelta(days=start_date.weekday())
        end_week = end_date - timedelta(days=end_date.weekday())
        weeks: list[date] = []
        while cursor <= end_week:
            weeks.append(cursor)
            cursor += timedelta(days=7)
        return weeks

    @staticmethod
    def _iter_calendar_years(start_date: date, end_date: date) -> list[int]:
        """Return calendar years intersecting an inclusive date range."""
        return list(range(start_date.year, end_date.year + 1))

    @staticmethod
    def _app_chart_period_meta(date_type: str) -> tuple[str, str] | None:
        """Return the external bucket id and label for an app chart period."""
        for period_date_type, bucket, bucket_label in APP_CHART_STAT_PERIODS:
            if period_date_type == date_type:
                return bucket, bucket_label
        return None

    @staticmethod
    def _app_chart_name_prefix(device_id: str, payload: dict[str, Any]) -> str:
        """Return a stable, user-readable app chart statistic name prefix."""
        return (
            (payload.get(PAYLOAD_SYSTEM) or {}).get(FIELD_DEVICE_NAME)
            or (payload.get(PAYLOAD_DISCOVERY) or {}).get(FIELD_DEVICE_NAME)
            or (payload.get(PAYLOAD_PROPERTIES) or {}).get(FIELD_WNAME)
            or f"Jackery {device_id}"
        )

    @staticmethod
    def _day_chart_source_candidates(
        section_prefix: str,
        stat_key: str,
        metric_key: str,
    ) -> list[tuple[str, str]]:
        """Return candidate payload sections for one day power-curve metric."""
        candidates: list[tuple[str, str]] = []
        trend_source = _DAY_TREND_SOURCE_BY_METRIC_KEY.get(metric_key)
        if trend_source is not None:
            candidates.append(trend_source)
        candidates.append((f"{section_prefix}_{DATE_TYPE_DAY}", stat_key))

        deduped: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            deduped.append(candidate)
        return deduped

    def _day_chart_points_for_metric(  # noqa: PLR0913
        self,
        payload: dict[str, Any],
        section_prefix: str,
        stat_key: str,
        metric_key: str,
        *,
        bucket_minutes: int,
        now: datetime,
    ) -> list[Any]:
        """Return converted day power-curve points for one metric."""
        for section, source_stat_key in self._day_chart_source_candidates(
            section_prefix,
            stat_key,
            metric_key,
        ):
            source = payload.get(section)
            if not isinstance(source, dict):
                continue
            points = day_power_energy_points(
                source,
                section,
                source_stat_key,
                bucket_minutes=bucket_minutes,
                today=now.date(),
                now=now,
            )
            if points:
                return points
        return []

    async def _async_add_app_chart_statistics(  # noqa: PLR0913
        self,
        *,
        device_id: str,
        name_prefix: str,
        metric_key: str,
        label: str,
        bucket: str,
        bucket_label: str,
        points: list[Any],
    ) -> tuple[bool, int]:
        """Add one external statistics series to HA recorder.

        Returns ``(ok, bucket_count)``. ``ok`` is true when the recorder import
        either succeeded or was skipped because the exact same bucket signature
        had already been published by this coordinator instance.
        """
        if not points:
            return True, 0
        try:
            from homeassistant.components.recorder.models import (  # noqa: I001, PLC0415
                StatisticData,
                StatisticMeanType,
                StatisticMetaData,
            )
            from homeassistant.components.recorder.statistics import (  # noqa: PLC0415
                async_add_external_statistics,
            )
            from homeassistant.const import UnitOfEnergy  # noqa: PLC0415
            from homeassistant.util.unit_conversion import EnergyConverter  # noqa: PLC0415
        except ImportError, RuntimeError:
            _LOGGER.exception("Recorder statistics import unavailable")
            return False, 0

        starts = [self._local_statistic_start(point.start_date) for point in points]
        states = [max(0.0, round(point.value, 5)) for point in points]
        if not starts or not states:
            return True, 0
        statistic_id = external_trend_statistic_id(
            DOMAIN,
            device_id,
            metric_key,
            bucket,
        )
        series_signature = json.dumps(
            [
                [s.isoformat() if hasattr(s, "isoformat") else s for s in starts],
                states,
            ],
            sort_keys=True,
            default=str,
        )
        if self._stat_import_last_sig.get(statistic_id) == series_signature:
            return True, 0
        offset = await self._async_statistic_sum_offset(
            statistic_id,
            starts,
            states,
        )
        statistics: list[StatisticData] = []
        cumulative = offset
        for start, state in zip(starts, states, strict=False):
            cumulative = round(cumulative + state, 5)
            statistics.append(StatisticData(start=start, state=state, sum=cumulative))
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
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug(
                "Could not import %d app chart statistics for %s: %s",
                len(statistics),
                statistic_id,
                err,
            )
            return False, 0
        self._stat_import_last_sig[statistic_id] = series_signature
        _LOGGER.debug(
            "Imported %d Jackery app chart statistic bucket(s) for %s",
            len(statistics),
            statistic_id,
        )
        return True, len(statistics)

    async def _async_import_day_chart_statistics(
        self,
        snapshot: dict[str, dict[str, Any]],
    ) -> set[str]:
        """Import app day power curves as hourly external statistics."""
        successful_devices: set[str] = set()
        if not snapshot:
            return successful_devices

        now = self._local_now()
        for device_id, payload in snapshot.items():
            name_prefix = self._app_chart_name_prefix(device_id, payload)
            for section_prefix, stat_key, metric_key, label in APP_CHART_STAT_METRICS:
                points = self._day_chart_points_for_metric(
                    payload,
                    section_prefix,
                    stat_key,
                    metric_key,
                    bucket_minutes=60,
                    now=now,
                )
                if not points:
                    continue
                ok, _bucket_count = await self._async_add_app_chart_statistics(
                    device_id=device_id,
                    name_prefix=name_prefix,
                    metric_key=metric_key,
                    label=label,
                    bucket=EXTERNAL_STAT_BUCKET_DAY_HOURLY,
                    bucket_label=APP_DAY_CHART_BUCKET_LABEL,
                    points=points,
                )
                if ok:
                    successful_devices.add(device_id)
        return successful_devices

    async def _async_import_app_chart_statistics(
        self,
        snapshot: dict[str, dict[str, Any]],
    ) -> set[str]:
        """Import Jackery app chart arrays as real HA external statistics.

        PROTOCOL.md §2 defines the source endpoints and period ranges.
        Normal week/month/year entities remain app period totals; the app chart
        arrays are imported separately as HA external statistics so recorder
        graphs receive real dated buckets instead of one flat total state.
        """
        successful_devices: set[str] = set()
        if not snapshot:
            return successful_devices

        today = self._local_today()
        for device_id, payload in snapshot.items():
            name_prefix = self._app_chart_name_prefix(device_id, payload)
            for section_prefix, stat_key, metric_key, label in APP_CHART_STAT_METRICS:
                # CT chart imports are intentionally excluded; see
                # PROTOCOL.md §2 Smart-Meter/CT rules.
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
                    ok, _bucket_count = await self._async_add_app_chart_statistics(
                        device_id=device_id,
                        name_prefix=name_prefix,
                        metric_key=metric_key,
                        label=label,
                        bucket=bucket,
                        bucket_label=bucket_label,
                        points=points,
                    )
                    if ok:
                        successful_devices.add(device_id)
        return successful_devices

    async def _async_fetch_historical_app_chart_source(  # noqa: PLR0911
        self,
        *,
        device_id: str,
        system_id: str | None,
        section_prefix: str,
        date_type: str,
        period_start: date,
    ) -> dict[str, Any]:
        """Fetch one explicit historical app chart source payload."""
        if date_type == DATE_TYPE_WEEK:
            kwargs = app_period_request_kwargs(DATE_TYPE_WEEK, today=period_start)
        elif date_type == DATE_TYPE_MONTH:
            kwargs = app_month_request_kwargs(period_start.year, period_start.month)
        elif date_type == DATE_TYPE_YEAR:
            kwargs = app_year_request_kwargs(period_start.year)
        else:
            return {}

        if section_prefix == APP_SECTION_PV_STAT:
            if not system_id:
                return {}
            return await self.api.async_get_device_pv_stat(
                device_id,
                system_id,
                **kwargs,
            )
        if section_prefix == APP_SECTION_BATTERY_STAT:
            return await self.api.async_get_device_battery_stat(
                device_id,
                **kwargs,
            )
        if section_prefix == APP_SECTION_HOME_STAT:
            return await self.api.async_get_device_home_stat(
                device_id,
                **kwargs,
            )
        if section_prefix == APP_SECTION_HOME_TRENDS:
            if not system_id:
                return {}
            return await self.api.async_get_home_trends(
                system_id,
                **kwargs,
            )
        if section_prefix == APP_SECTION_EPS_STAT:
            return await self.api.async_get_device_eps_stat(
                device_id,
                **kwargs,
            )
        return {}

    async def _async_repair_missing_app_chart_statistics(  # noqa: PLR0912, PLR0914
        self,
        device_id: str,
        payload: dict[str, Any],
        from_date: date,
        to_date: date,
    ) -> tuple[int, int]:
        """Backfill historical app chart statistic buckets after an outage.

        The normal coordinator snapshot only contains the app's current
        week/month/year periods. If HA or the Jackery cloud was unavailable
        over a calendar boundary, previous week/month/year buckets must be fetched
        explicitly before importing the current snapshot so cumulative sums stay
        monotonic and the long-term statistic graph has no avoidable gaps.
        """
        name_prefix = self._app_chart_name_prefix(device_id, payload)
        index = self._device_index.get(device_id) or {}
        system_id = (
            str(index.get(FIELD_SYSTEM_ID)) if index.get(FIELD_SYSTEM_ID) else None
        )
        prefixes = tuple(dict.fromkeys(metric[0] for metric in APP_CHART_STAT_METRICS))
        repaired_buckets = 0
        failed_buckets = 0
        entity_source_batches: list[tuple[str, dict[str, dict[str, Any]]]] = []

        period_plan: tuple[tuple[str, list[date]], ...] = (
            (DATE_TYPE_WEEK, self._iter_calendar_weeks(from_date, to_date)),
            (DATE_TYPE_MONTH, self._iter_calendar_months(from_date, to_date)),
            (
                DATE_TYPE_YEAR,
                [
                    date(year, 1, 1)
                    for year in self._iter_calendar_years(from_date, to_date)
                ],
            ),
        )

        for date_type, period_starts in period_plan:
            period_meta = self._app_chart_period_meta(date_type)
            if period_meta is None:
                continue
            bucket, bucket_label = period_meta
            for period_start in period_starts:
                section_sources: dict[str, dict[str, Any]] = {}
                for section_prefix in prefixes:
                    try:
                        fetched_source = (
                            await self._async_fetch_historical_app_chart_source(
                                device_id=device_id,
                                system_id=system_id,
                                section_prefix=section_prefix,
                                date_type=date_type,
                                period_start=period_start,
                            )
                        )
                    except JackeryAuthError:
                        raise
                    except JackeryError as err:
                        failed_buckets += 1
                        _LOGGER.debug(
                            "Jackery statistics backfill fetch failed for %s %s %s: %s",
                            device_id,
                            section_prefix,
                            period_start.isoformat(),
                            err,
                        )
                        continue
                    if fetched_source:
                        section_sources[section_prefix] = fetched_source

                if section_sources:
                    entity_source_batches.append((date_type, section_sources))

                for (
                    section_prefix,
                    stat_key,
                    metric_key,
                    label,
                ) in APP_CHART_STAT_METRICS:
                    section_source = section_sources.get(section_prefix)
                    if section_source is None:
                        continue
                    section = f"{section_prefix}_{date_type}"
                    points = trend_series_points(
                        section_source,
                        section,
                        stat_key,
                        today=to_date,
                    )
                    if not points:
                        continue
                    ok, bucket_count = await self._async_add_app_chart_statistics(
                        device_id=device_id,
                        name_prefix=name_prefix,
                        metric_key=metric_key,
                        label=label,
                        bucket=bucket,
                        bucket_label=bucket_label,
                        points=points,
                    )
                    if ok:
                        repaired_buckets += bucket_count
                    else:
                        failed_buckets += 1

        if entity_source_batches:
            (
                imported_entity_rows,
                failed_entity_rows,
            ) = await self._async_import_app_chart_entity_statistics_for_device(
                device_id=device_id,
                payload=payload,
                source_batches=entity_source_batches,
            )
            repaired_buckets += imported_entity_rows
            failed_buckets += failed_entity_rows
            if imported_entity_rows or failed_entity_rows:
                _LOGGER.debug(
                    "Jackery entity-statistics repair for %s imported %d row(s), "
                    "%d row(s) failed",
                    device_id,
                    imported_entity_rows,
                    failed_entity_rows,
                )

        return repaired_buckets, failed_buckets

    def _statistics_repair_from_date(self, device_id: str, today: date) -> date | None:  # noqa: PLR0911
        """Return the recovery start date for one device, if needed.

        On first run (``last_success`` not persisted yet) the method seeds
        the historical statistics from January 1 of the current calendar
        year. This matches the year-month backfill scope documented in
        ``docs/PROTOCOL.md §8`` and ensures HA Energy Dashboard /
        Recorder get past month/day buckets when the integration is added
        mid-year. In January the seed is skipped because there is no prior
        month inside the current calendar year, and the current snapshot
        already supplies the running January chart. If an older build already
        persisted a current-year success marker without a matching repair
        marker, the same January seed is used once to recover elapsed months.
        """
        state = self._statistics_backfill_device_state(device_id)
        last_success = self._parse_statistics_backfill_date(
            state.get(_STATISTICS_BACKFILL_LAST_SUCCESS)
        )
        if last_success is None:
            if today.month == 1:
                return None
            return today.replace(month=1, day=1)
        last_repair = self._parse_statistics_backfill_date(
            state.get(_STATISTICS_BACKFILL_LAST_REPAIR)
        )
        failed_bucket_count = int(
            safe_float(state.get(_STATISTICS_BACKFILL_LAST_FAILED_BUCKETS)) or 0
        )
        if (
            today.month != 1
            and state.get(_STATISTICS_BACKFILL_EXTERNAL_REPAIR_VERSION)
            != _EXTERNAL_STATISTICS_REPAIR_VERSION
        ):
            return today.replace(month=1, day=1)
        if (
            today.month != 1
            and state.get(_STATISTICS_BACKFILL_ENTITY_REPAIR_VERSION)
            != _ENTITY_STATISTICS_REPAIR_VERSION
        ):
            return today.replace(month=1, day=1)
        if self._statistics_current_year_recovery_needed(
            last_success=last_success,
            last_repair=last_repair,
            failed_bucket_count=failed_bucket_count,
            today=today,
        ):
            return today.replace(month=1, day=1)
        if last_success >= today:
            return None
        if (last_success.year, last_success.month) == (today.year, today.month):
            return None
        return last_success

    async def _async_import_and_repair_app_chart_statistics(
        self,
        snapshot: dict[str, dict[str, Any]],
    ) -> None:
        """Import current app chart buckets, then repair missed history."""
        if not snapshot:
            return
        await self._async_ensure_statistics_backfill_state_loaded()
        today = self._local_today()
        repair_ok: dict[str, bool] = {}
        repair_counts: dict[str, tuple[int, int]] = {}

        successful_devices = await self._async_import_app_chart_statistics(snapshot)
        successful_devices.update(
            await self._async_import_day_chart_statistics(snapshot)
        )
        current_entity_counts = (
            await self._async_import_current_app_chart_entity_statistics(snapshot)
        )

        for device_id, payload in snapshot.items():
            from_date = self._statistics_repair_from_date(device_id, today)
            if from_date is None:
                repair_ok[device_id] = True
                continue
            try:
                (
                    repaired,
                    failed,
                ) = await self._async_repair_missing_app_chart_statistics(
                    device_id,
                    payload,
                    from_date,
                    today,
                )
            except JackeryAuthError as err:
                _raise_config_entry_auth_failed(
                    "Jackery credentials were rejected during statistics backfill",
                    err,
                )
            repair_counts[device_id] = (repaired, failed)
            repair_ok[device_id] = failed == 0
            state = self._statistics_backfill_device_state(device_id)
            state[_STATISTICS_BACKFILL_LAST_REPAIR] = today.isoformat()
            state[_STATISTICS_BACKFILL_LAST_REPAIRED_BUCKETS] = repaired
            state[_STATISTICS_BACKFILL_LAST_FAILED_BUCKETS] = failed
            if failed == 0:
                state[_STATISTICS_BACKFILL_EXTERNAL_REPAIR_VERSION] = (
                    _EXTERNAL_STATISTICS_REPAIR_VERSION
                )
                state[_STATISTICS_BACKFILL_ENTITY_REPAIR_VERSION] = (
                    _ENTITY_STATISTICS_REPAIR_VERSION
                )
            if failed:
                state[_STATISTICS_BACKFILL_LAST_ERROR] = (
                    f"{failed} app chart backfill fetch/import step(s) failed"
                )
                _LOGGER.debug(
                    "Jackery statistics backfill for %s repaired %d bucket(s), %d step(s) failed",  # noqa: E501, RUF100
                    device_id,
                    repaired,
                    failed,
                )
            elif repaired:
                state.pop(_STATISTICS_BACKFILL_LAST_ERROR, None)
                _LOGGER.debug(
                    "Jackery statistics backfill for %s repaired %d bucket(s)",
                    device_id,
                    repaired,
                )

        if any(repaired for repaired, _failed in repair_counts.values()):
            successful_devices.update(
                await self._async_import_app_chart_statistics(snapshot)
            )
        changed = bool(repair_counts or current_entity_counts)
        for device_id in successful_devices:
            state = self._statistics_backfill_device_state(device_id)
            if not repair_ok.get(device_id, True):
                changed = True
                continue
            state[_STATISTICS_BACKFILL_LAST_SUCCESS] = today.isoformat()
            state[_STATISTICS_BACKFILL_LAST_FAILED_BUCKETS] = 0
            state.pop(_STATISTICS_BACKFILL_LAST_ERROR, None)
            changed = True

        if changed:
            await self._async_save_statistics_backfill_state()

    async def _async_retry_after_invalid_discovery_devices(
        self,
        invalid_device_ids: list[str],
    ) -> dict[str, dict[str, Any]] | None:
        """Drop persistently invalid discovery IDs and publish retry data."""
        if not invalid_device_ids:
            return None
        # code=20000 can be transient (maintenance window, firmware update,
        # or a brief cloud hiccup). Only remove a device from _device_index
        # after two consecutive failures so a single bad response does not
        # permanently orphan the device for the HA session.
        newly_persistent = []
        for dev_id in invalid_device_ids:
            prior_count = self._invalid_device_id_counts.get(dev_id, 0) + 1
            self._invalid_device_id_counts[dev_id] = prior_count
            if prior_count >= 2:  # noqa: PLR2004
                newly_persistent.append(dev_id)
        if not newly_persistent:
            recovered = set(self._invalid_device_id_counts) - set(invalid_device_ids)
            for dev_id in recovered:
                self._invalid_device_id_counts.pop(dev_id, None)
            return None

        _LOGGER.info(
            "Jackery: dropping %d device id(s) from discovery after "
            "repeated code=20000 errors and retrying",
            len(newly_persistent),
        )
        for dev_id in newly_persistent:
            self._device_index.pop(dev_id, None)
            self._invalid_device_id_counts.pop(dev_id, None)
        if not self._device_index:
            await self.async_discover()
        return await self._async_update_data(_retry_discovery_once=False)

    # ------------------------------------------------------------------
    # Coordinator update cycle (merge of HTTP + MQTT + caches)
    # ------------------------------------------------------------------

    async def _async_update_data(  # noqa: C901, PLR0912, PLR0914, PLR0915
        self, _retry_discovery_once: bool = True
    ) -> dict[str, dict[str, Any]]:
        # Background HTTP/auth tasks cannot raise into HA's setup flow. When
        # one proves the account credentials are invalid, it stashes the
        # message here so the next coordinator refresh opens reauth exactly
        # once. MQTT-only broker rejections are handled as app-conflict pauses
        # and must not stop HTTP polling.
        if self._mqtt_auth_failure_message is not None:
            message = self._mqtt_auth_failure_message
            self._mqtt_auth_failure_message = None
            raise ConfigEntryAuthFailed(message)

        # The passive reconnect path (``_async_ensure_mqtt`` without
        # ``wait_connected=True``) does not observe the CONNACK outcome
        # directly. If the MQTT client recorded broker auth rejections, treat
        # that as an app-conflict pause and keep the HTTP poll alive.
        if self._mqtt is not None:
            streak = self._mqtt.consecutive_auth_failures
            if streak > 0 and not self._mqtt.is_connected:
                last_error = self._mqtt.diagnostics.get("last_error") or "unknown"
                self._pause_mqtt_after_auth_failure(last_error, streak=streak)
        if not self._device_index:
            await self.async_discover()
            if not self._device_index:
                raise UpdateFailed("No Jackery devices found.")  # noqa: TRY003

        await self._async_refresh_discovery_if_due()

        skip_fast_property_fetch = self._should_skip_fast_property_fetch()
        if skip_fast_property_fetch:
            self._skipped_refresh_ticks += 1
            _LOGGER.debug(
                "Jackery: skipping fast /v1/device/property fetch because MQTT "
                "push is live (last property keep-alive %.0fs ago); slow HTTP "
                "statistics still refresh on their own cadence",
                time.monotonic() - self._last_http_refresh_completed_monotonic,
            )

        started = time.monotonic()

        # Once per slow-metrics window: log which HTTP statistics families
        # are about to be (re)fetched. Network calls themselves are still
        # TTL-gated by ``_get_with_ttl_for`` — this log just announces the
        # cadence boundary so operators can confirm cloud trends are being
        # pulled. We log per family (not per endpoint) to keep noise low.
        device_count = len(self._device_index)
        system_ids: set[str] = set()
        for idx_ in self._device_index.values():
            sys_id_ = idx_.get(FIELD_SYSTEM_ID)
            if sys_id_:
                system_ids.add(str(sys_id_))
        system_count = len(system_ids)
        if (
            started - self._last_slow_poll_log_monotonic
            >= self._slow_metrics_interval_sec
        ):
            self._last_slow_poll_log_monotonic = started
            _LOGGER.info(
                "Jackery: fetching system trends (pv/home/battery) stats for "
                "%d device(s) / %d system(s)",
                device_count,
                system_count,
            )
            _LOGGER.info(
                "Jackery: fetching system statistic stats for %d device(s) / "
                "%d system(s)",
                device_count,
                system_count,
            )
            _LOGGER.info(
                "Jackery: fetching device period (pv/battery/onGrid/ct) stats "
                "for %d device(s) / %d system(s)",
                device_count,
                system_count,
            )
            _LOGGER.info(
                "Jackery: fetching device statistic stats for %d device(s) / "
                "%d system(s)",
                device_count,
                system_count,
            )
            if not skip_fast_property_fetch:
                _LOGGER.info(
                    "Jackery: fetching device property stats for %d device(s)"
                    " / %d system(s)",
                    device_count,
                    system_count,
                )

        # Per-system calls honour their own refresh intervals. Inside a
        # single update cycle we call each endpoint at most once; across
        # cycles the cache only refreshes when its TTL expired.
        system_cache: dict[str, dict[str, Any]] = {}

        # At the start of each cycle: if the local date rolled over, wipe
        # the day-bounded caches so we don't keep serving yesterday's
        # final values for up to self._slow_metrics_interval_sec.
        today = self._local_today()
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
                self._app_period_section(APP_SECTION_CT_STAT, DATE_TYPE_DAY),
                self._app_period_section(APP_SECTION_CT_STAT, DATE_TYPE_WEEK),
                self._app_period_section(APP_SECTION_CT_STAT, DATE_TYPE_MONTH),
                self._app_period_section(APP_SECTION_CT_STAT, DATE_TYPE_YEAR),
                self._app_period_section(APP_SECTION_EPS_STAT, DATE_TYPE_DAY),
                self._app_period_section(APP_SECTION_EPS_STAT, DATE_TYPE_WEEK),
                self._app_period_section(APP_SECTION_EPS_STAT, DATE_TYPE_MONTH),
                self._app_period_section(APP_SECTION_EPS_STAT, DATE_TYPE_YEAR),
                APP_SECTION_TODAY_ENERGY,
            )
            for cache in self._slow_cache.values():
                for cache_key in cache_keys_to_clear:
                    cache.pop(cache_key, None)
        self._cached_date = today

        async def _get_with_ttl_for(
            cache: dict[str, tuple[float, Any]],
            cache_key: str,
            ttl_sec: int,
            fetcher: Callable[..., Awaitable[Any]],
            default: Any,  # noqa: ANN401, RUF100
        ) -> Any:  # noqa: ANN401, RUF100
            """Generic TTL cache helper operating on any dict."""
            now = time.monotonic()
            entry = cache.get(cache_key)
            if entry is not None:
                last_ts, last_value = entry
                if now - last_ts < ttl_sec:
                    return last_value
            try:
                value = await fetcher()
            except JackeryAuthError:
                # Credential rejection must propagate so HA can trigger reauth.
                raise
            except (
                Exception
            ):  # broad catch so one endpoint never kills sibling tasks in a gather
                # Transient request failures (JackeryApiError: timeouts, 5xx,
                # malformed payloads) fall back to the cached value or default
                # instead of failing the whole refresh / config-entry setup.
                _LOGGER.exception("%s failed", cache_key)
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
            default: Any,  # noqa: ANN401, RUF100
        ) -> Any:  # noqa: ANN401, RUF100
            """System-scoped TTL cache wrapper."""
            per_system = self._slow_cache.setdefault(sys_id, {})
            return await _get_with_ttl_for(
                per_system,
                cache_key,
                ttl_sec,
                lambda: fetcher(sys_id),
                default,
            )

        async def _fetch_system(sys_id: str) -> dict[str, Any]:  # noqa: PLR0914
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
                    ) -> Any:  # noqa: ANN401, RUF100
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
                        ),
                        return_exceptions=True,
                    )
                    months.update({
                        month: source
                        for month, source in zip(previous_months, sources, strict=False)
                        if isinstance(source, dict)
                    })
                if months:
                    month_history[prefix] = months
            apply_year_month_backfill(bundle, month_history)
            system_cache[sys_id] = bundle
            return bundle

        async def _fetch_device_extras(  # noqa: PLR0914, PLR0915
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
                # The device period-stat endpoints (/v1/device/stat/{pv,battery,
                # onGrid,eps}) only accept day/week/month/year. dateType=hour is
                # rejected with code=10422 "Ungültige Parameter", so requesting
                # it just spams failed calls and leaves empty *_stat_hour
                # sections that nothing consumes. Hourly resolution comes from
                # the trends endpoints instead.
                if date_type == DATE_TYPE_HOUR:
                    continue
                kwargs = self._trend_query_kwargs(date_type)
                pv_key = self._app_period_section(APP_SECTION_PV_STAT, date_type)
                battery_key = self._app_period_section(
                    APP_SECTION_BATTERY_STAT, date_type
                )
                home_key = self._app_period_section(APP_SECTION_HOME_STAT, date_type)
                ct_key = self._app_period_section(APP_SECTION_CT_STAT, date_type)
                eps_key = self._app_period_section(APP_SECTION_EPS_STAT, date_type)
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
                # /v1/device/stat/ct — CT/smart-meter period statistics
                # (CtStatApi). Device-scoped, per dateType. Cached on the
                # slow-metrics TTL so per-cycle fast refreshes are free.
                # /v1/device/stat/eps — EPS / off-grid in/out period
                # statistics (EpsStatApi). Same shape as ct_stat: device
                # id + dateType, slow-metrics TTL.
                task_names.extend((battery_key, home_key, ct_key, eps_key))
                tasks.extend((
                    _get_with_ttl_for(
                        per_dev,
                        battery_key,
                        self._slow_metrics_interval_sec,
                        lambda q=kwargs: self.api.async_get_device_battery_stat(
                            dev_id,
                            **q,
                        ),
                        {},
                    ),
                    _get_with_ttl_for(
                        per_dev,
                        home_key,
                        self._slow_metrics_interval_sec,
                        lambda q=kwargs: self.api.async_get_device_home_stat(
                            dev_id,
                            **q,
                        ),
                        {},
                    ),
                    _get_with_ttl_for(
                        per_dev,
                        ct_key,
                        self._slow_metrics_interval_sec,
                        lambda q=kwargs: self.api.async_get_device_ct_stat(
                            dev_id,
                            **q,
                        ),
                        {},
                    ),
                    _get_with_ttl_for(
                        per_dev,
                        eps_key,
                        self._slow_metrics_interval_sec,
                        lambda q=kwargs: self.api.async_get_device_eps_stat(
                            dev_id,
                            **q,
                        ),
                        {},
                    ),
                ))
            if dev_sn:
                # REST pack/list is slow and often returns null for SolarVault.
                # Live pack values are refreshed via MQTT subdevice queries.
                pack_interval_sec = self._slow_metrics_interval_sec
                # /v1/device/stat/today — compact today KPIs
                # (TodayEnergyApi: de/dg/dh/ds). Keyed by deviceSn, no
                # period parameters. Slow-metrics TTL so the fast 30 s
                # refresh does not hammer the cloud.
                task_names.extend((
                    PAYLOAD_OTA,
                    PAYLOAD_BATTERY_PACKS,
                    APP_SECTION_TODAY_ENERGY,
                ))
                tasks.extend((
                    _get_with_ttl_for(
                        per_dev,
                        PAYLOAD_OTA,
                        self._price_config_interval_sec,
                        lambda: self.api.async_get_ota_info(dev_sn),
                        {},
                    ),
                    _get_with_ttl_for(
                        per_dev,
                        PAYLOAD_BATTERY_PACKS,
                        pack_interval_sec,
                        lambda: self.api.async_get_battery_pack_list(dev_sn),
                        [],
                    ),
                    _get_with_ttl_for(
                        per_dev,
                        APP_SECTION_TODAY_ENERGY,
                        self._slow_metrics_interval_sec,
                        lambda: self.api.async_get_today_energy(dev_sn),
                        {},
                    ),
                ))
            raw_values = await asyncio.gather(*tasks, return_exceptions=True)
            values = [v if not isinstance(v, BaseException) else {} for v in raw_values]
            out: dict[str, Any] = dict(zip(task_names, values, strict=False))
            out.setdefault(PAYLOAD_DEVICE_STATISTIC, {})
            out.setdefault(PAYLOAD_LOCATION, {})
            out.setdefault(PAYLOAD_OTA, {})
            out.setdefault(PAYLOAD_BATTERY_PACKS, [])

            packs = out.get(PAYLOAD_BATTERY_PACKS) or []
            if isinstance(packs, list) and packs:
                await self._async_enrich_battery_pack_ota(
                    dev_id,
                    packs,
                    dev_sn,
                    fetch_missing=False,
                )

            async def _fetch_device_month(
                prefix: str,
                month: int,
            ) -> dict[str, Any]:
                kwargs = app_month_request_kwargs(today.year, month)
                cache_key = f"{prefix}_{DATE_TYPE_MONTH}_{today.year}_{month:02d}"
                if prefix == APP_SECTION_PV_STAT:
                    if not sys_id:
                        return {}
                    return cast(
                        "dict[str, Any]",
                        await _get_with_ttl_for(
                            per_dev,
                            cache_key,
                            self._price_config_interval_sec,
                            lambda q=kwargs: self.api.async_get_device_pv_stat(
                                dev_id,
                                sys_id,
                                **q,
                            ),
                            {},
                        ),
                    )
                if prefix == APP_SECTION_BATTERY_STAT:
                    return cast(
                        "dict[str, Any]",
                        await _get_with_ttl_for(
                            per_dev,
                            cache_key,
                            self._price_config_interval_sec,
                            lambda q=kwargs: self.api.async_get_device_battery_stat(
                                dev_id,
                                **q,
                            ),
                            {},
                        ),
                    )
                if prefix == APP_SECTION_HOME_STAT:
                    return cast(
                        "dict[str, Any]",
                        await _get_with_ttl_for(
                            per_dev,
                            cache_key,
                            self._price_config_interval_sec,
                            lambda q=kwargs: self.api.async_get_device_home_stat(
                                dev_id,
                                **q,
                            ),
                            {},
                        ),
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
                    *(_fetch_device_month(prefix, month) for month in previous_months),
                    return_exceptions=True,
                )
                months.update({
                    month: source
                    for month, source in zip(previous_months, sources, strict=False)
                    if isinstance(source, dict)
                })
                if months:
                    month_history[prefix] = months
            apply_year_month_backfill(out, month_history)

            return out

        async def _enrich_smart_plug_statistics(
            dev_id: str,
            entry: dict[str, Any],
        ) -> None:
            """Attach read-only app socket statistics to known smart plugs."""
            plugs = entry.get(PAYLOAD_SMART_PLUGS)
            if not isinstance(plugs, list) or not plugs:
                return
            per_dev = self._slow_cache.setdefault(f"dev:{dev_id}:smart_plug", {})
            changed = False
            updated_plugs: list[Any] = []
            for plug in plugs:
                if not isinstance(plug, dict):
                    updated_plugs.append(plug)
                    continue
                updated_plug = dict(plug)
                stat_id = self._subdevice_stat_id(
                    entry,
                    updated_plug,
                    dev_type=SUBDEVICE_DEV_TYPE_SOCKET,
                )
                if stat_id is None:
                    updated_plugs.append(updated_plug)
                    continue
                panel = await _get_with_ttl_for(
                    per_dev,
                    f"smart_socket_statistic:{stat_id}",
                    self._slow_metrics_interval_sec,
                    lambda sid=stat_id: self.api.async_get_device_socket_statistic(sid),
                    {},
                )
                if isinstance(panel, dict):
                    for key in (FIELD_TODAY_ENERGY, FIELD_TOTAL_ENERGY):
                        value = panel.get(key)
                        if value is not None and updated_plug.get(key) != value:
                            updated_plug[key] = value
                            changed = True
                updated_plugs.append(updated_plug)
            if changed:
                entry[PAYLOAD_SMART_PLUGS] = updated_plugs

        async def _enrich_meter_head_statistics(
            dev_id: str,
            entry: dict[str, Any],
        ) -> None:
            """Attach read-only app meter statistics to known meter heads."""
            meter_heads = entry.get(PAYLOAD_METER_HEADS)
            if not isinstance(meter_heads, list) or not meter_heads:
                return
            per_dev = self._slow_cache.setdefault(f"dev:{dev_id}:meter_head", {})
            changed = False
            updated_meter_heads: list[Any] = []
            for meter_head in meter_heads:
                if not isinstance(meter_head, dict):
                    updated_meter_heads.append(meter_head)
                    continue
                updated_meter_head = dict(meter_head)
                stat_id = self._subdevice_stat_id(
                    entry,
                    updated_meter_head,
                    dev_type=SUBDEVICE_DEV_TYPE_METER_HEAD,
                )
                if stat_id is None:
                    updated_meter_heads.append(updated_meter_head)
                    continue
                panel = await _get_with_ttl_for(
                    per_dev,
                    f"meter_head_stat:{stat_id}",
                    self._slow_metrics_interval_sec,
                    lambda sid=stat_id: self.api.async_get_device_meter_stat(sid),
                    {},
                )
                if isinstance(panel, dict):
                    for key in (FIELD_CHARGING_ENERGY, FIELD_DISCHARGING_ENERGY):
                        value = panel.get(key)
                        if value is not None and updated_meter_head.get(key) != value:
                            updated_meter_head[key] = value
                            changed = True
                updated_meter_heads.append(updated_meter_head)
            if changed:
                entry[PAYLOAD_METER_HEADS] = updated_meter_heads

        result: dict[str, dict[str, Any]] = {}
        invalid_device_ids: list[str] = []
        property_fetch_completed = False
        for dev_id, idx in self._device_index.items():
            old_entry: dict[str, Any] = {}
            if self.data:
                old_entry = self.data.get(dev_id) or {}
            used_cached_property = False
            if skip_fast_property_fetch and old_entry:
                used_cached_property = True
                payload = {
                    PAYLOAD_DEVICE: old_entry.get(PAYLOAD_DEVICE) or {},
                    PAYLOAD_PROPERTIES: old_entry.get(PAYLOAD_HTTP_PROPERTIES)
                    or old_entry.get(PAYLOAD_PROPERTIES)
                    or {},
                }
            else:
                try:
                    payload = await self.api.async_get_device_property(dev_id)
                    property_fetch_completed = True
                except JackeryAuthError as err:
                    _raise_config_entry_auth_failed(
                        "Jackery credentials were rejected during property refresh",
                        err,
                    )
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
                _raise_config_entry_auth_failed(
                    "Jackery credentials were rejected while fetching extended device data",  # noqa: E501, RUF100
                    err,
                )

            if used_cached_property:
                http_props = self._sanitize_main_properties(
                    old_entry.get(PAYLOAD_HTTP_PROPERTIES) or {}
                )
                merged_props = self._merge_main_properties_for_device(
                    dev_id,
                    old_entry.get(PAYLOAD_PROPERTIES) or {},
                    {},
                )
            else:
                http_props = self._sanitize_main_properties(
                    payload.get(PAYLOAD_PROPERTIES) or {}
                )
                merged_props = self._merge_main_properties_for_device(
                    dev_id,
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
                    fetch_missing=False,
                )
                self._schedule_battery_pack_ota_enrichment(dev_id)

            period_payloads = {
                self._app_period_section(prefix, date_type): extras.get(
                    self._app_period_section(prefix, date_type)
                )
                or {}
                for prefix in (
                    APP_SECTION_PV_STAT,
                    APP_SECTION_BATTERY_STAT,
                    APP_SECTION_HOME_STAT,
                    APP_SECTION_CT_STAT,
                    APP_SECTION_EPS_STAT,
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
                APP_SECTION_TODAY_ENERGY: extras.get(APP_SECTION_TODAY_ENERGY) or {},
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
                    _raise_config_entry_auth_failed(
                        "Jackery credentials were rejected while fetching system data",
                        err,
                    )
                entry.update(sys_data)
            override = self._price_overrides.get(dev_id)
            if override:
                override_ts, price_updates = override
                if time.monotonic() - override_ts < self._PRICE_OVERRIDE_TTL_SEC:
                    entry[PAYLOAD_PRICE] = merge_live_properties(
                        entry.get(PAYLOAD_PRICE) or {},
                        price_updates,
                    )
                else:
                    self._price_overrides.pop(dev_id, None)
            await _enrich_smart_plug_statistics(dev_id, entry)
            await _enrich_meter_head_statistics(dev_id, entry)
            previous_statistic = old_entry.get(PAYLOAD_STATISTIC)
            guard_statistic_totals_from_year(
                entry,
                previous_statistic=previous_statistic
                if isinstance(previous_statistic, dict)
                else None,
            )
            quality_warnings = app_data_quality_warnings(
                entry, today=today, now=self._local_now()
            )
            if quality_warnings:
                for warning in quality_warnings:
                    self.record_schema_rejection(warning.reason)
                entry[PAYLOAD_DATA_QUALITY] = [
                    warning.as_dict() for warning in quality_warnings
                ]
            result[dev_id] = entry
            self._device_update_received_at[dev_id] = datetime.now()

        if _retry_discovery_once:
            retry_result = await self._async_retry_after_invalid_discovery_devices(
                invalid_device_ids
            )
            if retry_result is not None:
                return retry_result

        if self._mqtt is not None and (
            self.api.mqtt_fingerprint != self._mqtt_fingerprint
            or not self._mqtt.is_connected
        ):
            await self._async_ensure_mqtt()
        await self._async_update_data_quality_issue(result)
        # Recorder statistic imports only run at the slow-metric cadence
        # (server-side chart updates also operate at ~5 min granularity)
        # so the recorder is not woken up on every fast HTTP refresh.
        self._schedule_statistics_import(result)
        self._schedule_mqtt_backfill_queries(result)
        # Drain queued device-registry removals from the stale-pack
        # cleanup. Fire-and-forget on the same task so a registry
        # hiccup does not break the data refresh.
        if self._pending_device_removals:
            try:
                await self.async_cleanup_pending_device_removals()
            except Exception:
                _LOGGER.exception("Jackery: device-registry cleanup deferred")
        completed = time.monotonic()
        if property_fetch_completed:
            self._last_http_refresh_completed_monotonic = completed
        elapsed = completed - started
        interval_sec = self._configured_update_interval.total_seconds()
        if elapsed > interval_sec:
            _LOGGER.debug(
                "Jackery polling cycle overran interval: %.2fs > %.2fs",
                elapsed,
                interval_sec,
            )
        return result

    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def mqtt_diagnostics(self) -> dict[str, Any]:
        """Return the MQTT client diagnostics block for the diagnostics export."""
        return self.mqtt_diagnostics_snapshot()

    def mqtt_diagnostics_snapshot(
        self, *, redact_topics: bool = True
    ) -> dict[str, Any]:
        """Return the MQTT client diagnostics block for the diagnostics export."""
        if self._mqtt is None:
            return {"enabled": False}
        diag = dict(self._mqtt.diagnostics_snapshot(redact_topics=redact_topics))
        diag["enabled"] = True
        diag["credential_mac_id_source"] = self.api.mqtt_mac_id_source
        diag["slow_metrics_interval_seconds"] = self._slow_metrics_interval_sec
        diag["price_interval_seconds"] = self._price_config_interval_sec
        diag["subdevice_query_interval_seconds"] = self._subdevice_query_interval_sec
        diag["coordinator_polling_seconds"] = int(
            self._configured_update_interval.total_seconds()
        )
        # Reflect the real TLS posture (see docs/STRICT_WORK_INSTRUCTIONS.md §2):
        # chain + hostname verification always on; VERIFY_X509_STRICT is cleared
        # because Jackery's broker cert lacks the Authority Key Identifier.
        if diag.get("tls_x509_strict_disabled"):
            diag["tls_certificate_verification"] = (
                "chain_hostname_enabled_x509_strict_disabled"
            )
        else:
            diag["tls_certificate_verification"] = "chain_hostname_enabled"
        diag["tls_insecure_warning"] = None
        diag["skipped_refresh_ticks"] = self._skipped_refresh_ticks
        diag["stale_battery_packs_dropped"] = self._stale_battery_packs_dropped
        diag["app_conflict_pause_cycles"] = self._mqtt_app_conflict_pause_cycles
        now_mono = time.monotonic()
        diag["app_conflict_pause_remaining_seconds"] = max(
            0, int(self._mqtt_paused_until_monotonic - now_mono)
        )
        return diag
