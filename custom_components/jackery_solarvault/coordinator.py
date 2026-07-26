"""DataUpdateCoordinator for Jackery SolarVault.

Transport Layer Architecture (MANDATORY):
  Layer 3 = HTTP / Cloud API  -> PRIMARY login, cache, crypto, setters, data
  Layer 5 = MQTT/BLE/local MQTT -> local live data + command transports

Button command flow:
  _async_publish_command_ble_first() -> BLE first, then MQTT fallback
  HTTP-backed setters stay on self.api.async_*(); app transport commands stay
  coordinator-owned and never gate the HTTP polling path.
"""

import asyncio  # ruff:ignore[unsorted-imports]
import base64
import binascii
from collections import deque
from collections.abc import Mapping
import contextlib
import copy
from dataclasses import dataclass, field as dataclass_field
from datetime import UTC, date, datetime, timedelta
from functools import wraps
import hashlib
import importlib
import json
from json import JSONDecodeError
import logging
import math
import re
import sys
import time
from typing import TYPE_CHECKING, Any, ClassVar, Final, NoReturn, cast

from homeassistant.components import mqtt
from homeassistant.components.recorder import get_instance
from homeassistant.components.recorder.db_schema import (
    Statistics,
    StatisticsMeta,
)
from homeassistant.components.recorder.models import (
    StatisticData,
    StatisticMeanType,
    StatisticMetaData,
)
from homeassistant.components.recorder.statistics import (
    async_add_external_statistics,
    statistics_during_period,
)
from homeassistant.const import UnitOfEnergy
from homeassistant.core import CoreState, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import (
    device_registry as dr,
    entity_registry as er,
    issue_registry as ir,
)
from homeassistant.helpers.event import async_track_time_interval
from homeassistant.helpers.recorder import session_scope
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util
from homeassistant.util.unit_conversion import EnergyConverter

from .client import JackeryApiError, JackeryAuthError, JackeryError, ble
from .client.api import encrypt_mqtt_body
from .client.discovery_cache import (
    async_load_discovery_cache,
    async_save_discovery_cache,
)
from .client.local_daily_cache import (
    async_load_daily_cache,
    async_save_daily_cache,
    daily_delta,
    local_daily_signature,
    refresh_snapshot,
)
from .client.local_mqtt import JackeryLocalMqttClient, payload_has_jackery_marker
from .client.mqtt_session_cache import (
    async_clear_mqtt_session,
    async_save_mqtt_session,
)
from .client.third_party_mqtt_codec import (
    decode_third_party_mqtt_config_body,
    encode_third_party_mqtt_field,
    stable_third_party_mqtt_token,
    third_party_mqtt_config_plaintext,
)
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
    ACTION_ID_PORTABLE_ADD_CHARGE_PLAN,
    ACTION_ID_PORTABLE_CUSTOM_USE_BATTERY,
    ACTION_ID_PORTABLE_DELETE_CHARGE_PLAN,
    ACTION_ID_PORTABLE_GET_CHARGE_PLAN,
    ACTION_ID_PORTABLE_GET_WIFI_CONFIG,
    ACTION_ID_PORTABLE_NOTIFY_CAN_OTA,
    ACTION_ID_PORTABLE_NOTIFY_OTA_TOTAL_PAGE,
    ACTION_ID_PORTABLE_OTA_PAGE_DATA,
    ACTION_ID_PORTABLE_OTA_VERSION,
    ACTION_ID_PORTABLE_SEND_TIME_ZONE,
    ACTION_ID_PORTABLE_UPDATE_CHARGE_PLAN,
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
    ACTION_ID_SYNC_GRID_STANDARD,
    ACTION_ID_SYNC_MQTT_CONNECT_INFO,
    ACTION_ID_TEMP_UNIT,
    ACTION_ID_TIMER_TASK_ADD,
    ACTION_ID_TIMER_TASK_DELETE,
    ACTION_ID_TIMER_TASK_READ,
    ACTION_ID_TIMER_TASK_UPDATE,
    ACTION_ID_UNBIND_SMART_PART,
    ACTION_ID_WORK_MODEL,
    APP_CHART_STAT_METRICS,
    APP_CHART_STAT_PERIODS,
    APP_DAY_CHART_BUCKET_LABEL,
    APP_DEVICE_STAT_BATTERY_CHARGE,
    APP_DEVICE_STAT_BATTERY_DISCHARGE,
    APP_DEVICE_STAT_BATTERY_TO_GRID,
    APP_DEVICE_STAT_EPS_INPUT,
    APP_DEVICE_STAT_EPS_OUTPUT,
    APP_DEVICE_STAT_ONGRID_INPUT,
    APP_DEVICE_STAT_ONGRID_OUTPUT,
    APP_DEVICE_STAT_ONGRID_TO_BATTERY,
    APP_DEVICE_STAT_PV_ENERGY,
    APP_DEVICE_STAT_PV_TO_BATTERY,
    APP_PERIOD_DATE_TYPES,
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_BATTERY_TRENDS,
    APP_SECTION_CT_STAT,
    APP_SECTION_EPS_STAT,
    APP_SECTION_HOME_STAT,
    APP_SECTION_HOME_TRENDS,
    APP_SECTION_PV_STAT,
    APP_SECTION_PV_TRENDS,
    APP_SECTION_SOCKET_STAT,
    APP_SECTION_SYMMETRY_STAT,
    APP_SECTION_TODAY_ENERGY,
    APP_STAT_PV1_ENERGY,
    APP_STAT_PV2_ENERGY,
    APP_STAT_PV3_ENERGY,
    APP_STAT_PV4_ENERGY,
    APP_STAT_TODAY_BATTERY_ENERGY,
    APP_STAT_TODAY_FEED_IN_ENERGY,
    APP_STAT_TODAY_GRID_IMPORT_ENERGY,
    APP_STAT_TODAY_HOME_LOAD_ENERGY,
    APP_STAT_TOTAL_CHARGE,
    APP_STAT_TOTAL_CT_INPUT_ENERGY,
    APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
    APP_STAT_TOTAL_DISCHARGE,
    APP_STAT_TOTAL_HOME_ENERGY,
    APP_STAT_TOTAL_IN_EPS_ENERGY,
    APP_STAT_TOTAL_IN_GRID_ENERGY,
    APP_STAT_TOTAL_OUT_EPS_ENERGY,
    APP_STAT_TOTAL_OUT_GRID_ENERGY,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    APP_STAT_UNIT,
    BACKGROUND_SLOW_REFRESH_TIMEOUT_SEC,
    BATTERY_PACK_HINT_KEYS,
    BATTERY_PACK_STALE_THRESHOLD_SEC,
    BLE_AES_KEY_LENGTHS,
    BLE_COMMAND_CONNECT_TIMEOUT_SEC,
    BLE_CONNECT_BACKOFF_INITIAL_SEC,
    BLE_CONNECT_BACKOFF_MAX_SEC,
    CONF_ENABLE_BLE_TRANSPORT,
    CONF_ENABLE_DERIVED_HOME_ENERGY_FALLBACK,
    CONF_ENABLE_MONTH_STATISTICS,
    CONF_ENABLE_WEEK_STATISTICS,
    CONF_ENABLE_YEAR_STATISTICS,
    CONF_LOCAL_MQTT_ENABLE,
    CONF_LOCAL_MQTT_HOST,
    CONF_LOCAL_MQTT_PASSWORD,
    CONF_LOCAL_MQTT_PORT,
    CONF_LOCAL_MQTT_USERNAME,
    CONF_THIRD_PARTY_MQTT_ENABLE,
    CONF_THIRD_PARTY_MQTT_IP,
    CONF_THIRD_PARTY_MQTT_PASSWORD,
    CONF_THIRD_PARTY_MQTT_PORT,
    CONF_THIRD_PARTY_MQTT_TOKEN,
    CONF_THIRD_PARTY_MQTT_USERNAME,
    COORDINATOR_UPDATE_TIMEOUT_SEC,
    CT_METER_KEYS,
    CUSTOM_USE_BATTERY_BC_OFFSET,
    DATA_QUALITY_KEY_LABEL,
    DATA_QUALITY_KEY_METRIC_KEY,
    DATA_QUALITY_KEY_REASON,
    DATA_QUALITY_KEY_REFERENCE_SECTION,
    DATA_QUALITY_KEY_SOURCE_SECTION,
    DATA_QUALITY_REPAIR_EXAMPLE_LIMIT,
    DATE_TYPE_DAY,
    DATE_TYPE_HOUR,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    DEFAULT_BLE_ACK_TIMEOUT_SEC,
    DEFAULT_ENABLE_BLE_TRANSPORT,
    DEFAULT_ENABLE_DERIVED_HOME_ENERGY_FALLBACK,
    DEFAULT_ENABLE_MONTH_STATISTICS,
    DEFAULT_ENABLE_WEEK_STATISTICS,
    DEFAULT_ENABLE_YEAR_STATISTICS,
    DEFAULT_LOCAL_MQTT_ENABLE,
    DEFAULT_LOCAL_MQTT_PORT,
    DEFAULT_THIRD_PARTY_MQTT_ENABLE,
    DEFAULT_THIRD_PARTY_MQTT_PORT,
    DIAGNOSTICS_SCHEMA_VERSION,
    DISCOVERY_SOURCE_LEGACY_BIND_LIST,
    DISCOVERY_SOURCE_SYSTEM_LIST,
    DOMAIN,
    EXTERNAL_STAT_BUCKET_DAY_HOURLY,
    FIELD_ACCESSORIES,
    FIELD_ACC_CT_BODY,
    FIELD_ACTION_ID,
    FIELD_ACTION_TYPE,
    FIELD_ALERT_ID,
    FIELD_AUTO_STANDBY,
    FIELD_BATTERIES,
    FIELD_BATTERY_PACK,
    FIELD_BATTERY_PACKS,
    FIELD_BATTERY_PACK_LIST,
    FIELD_BAT_IN_PW,
    FIELD_BAT_NUM,
    FIELD_BAT_OUT_PW,
    FIELD_BAT_SOC,
    FIELD_BIND_ID,
    FIELD_BIND_KEY,
    FIELD_BLUETOOTH_KEY,
    FIELD_BODY,
    FIELD_CELL_TEMP,
    FIELD_CHARGE_PLAN_PW,
    FIELD_CHARGING_ENERGY,
    FIELD_CID,
    FIELD_CIR,
    FIELD_CMD,
    FIELD_COLLECTORS,
    FIELD_COMPANY_NAME,
    FIELD_CONTROL_ALLOWED,
    FIELD_COUNTRY,
    FIELD_COUNTRY_CODE,
    FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_TOTAL_PHASE_ENERGY,
    FIELD_CURRENCY,
    FIELD_CURRENCY_CODE,
    FIELD_CURRENT_VERSION,
    FIELD_DATA,
    FIELD_DEFAULT_PW,
    FIELD_DEVICES,
    FIELD_DEVICE_CODE,
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
    FIELD_ENERGY_PLAN_PW,
    FIELD_GRID_IN_PW,
    FIELD_GRID_OUT_PW,
    FIELD_GRID_STANDARD,
    FIELD_HOST,
    FIELD_ICON,
    FIELD_ICON_PATH,
    FIELD_ID,
    FIELD_IDX,
    FIELD_INTEGRATOR_ENABLED,
    FIELD_IN_EGY,
    FIELD_IN_GRID_SIDE_PW,
    FIELD_IN_ONGRID_PW,
    FIELD_IN_PW,
    FIELD_IP,
    FIELD_IS_AUTO_STANDBY,
    FIELD_IS_CLOUD,
    FIELD_IS_FIRMWARE_UPGRADE,
    FIELD_IS_FOLLOW_METER_PW,
    FIELD_LATITUDE,
    FIELD_LOGIN_ALLOWED,
    FIELD_LONGITUDE,
    FIELD_MAX_FEED_GRID,
    FIELD_MAX_GRID_STD_PW,
    FIELD_MAX_OUT_PW,
    FIELD_MESSAGE_TYPE,
    FIELD_MINS_INTERVAL,
    FIELD_MODEL_CODE,
    FIELD_NAME,
    FIELD_OFF_GRID_DOWN,
    FIELD_OFF_GRID_TIME,
    FIELD_ONLINE,
    FIELD_ONLINE_STATUS,
    FIELD_OP,
    FIELD_OTHER_LOAD_PW,
    FIELD_OUT_EGY,
    FIELD_OUT_GRID_SIDE_PW,
    FIELD_OUT_ONGRID_PW,
    FIELD_OUT_PW,
    FIELD_PACK_LIST,
    FIELD_PLATFORM_COMPANY_ID,
    FIELD_PLUGS,
    FIELD_POWER_BODY,
    FIELD_POWER_PRICE_RESOURCE,
    FIELD_PRODUCT_MODEL,
    FIELD_PV1,
    FIELD_PV2,
    FIELD_PV3,
    FIELD_PV4,
    FIELD_PV_NAME,
    FIELD_PV_PW,
    FIELD_RB,
    FIELD_REBOOT,
    FIELD_SAFETY,
    FIELD_SCAN_NAME,
    FIELD_SCHE_PHASE,
    FIELD_SINGLE_CURRENCY,
    FIELD_SINGLE_CURRENCY_CODE,
    FIELD_SINGLE_PRICE,
    FIELD_SN,
    FIELD_SOC,
    FIELD_SOCKET_PRIORITY,
    FIELD_SOC_CHARGE_LIMIT,
    FIELD_SOC_CHG_LIMIT,
    FIELD_SOC_DISCHARGE_LIMIT,
    FIELD_SOC_DISCHG_LIMIT,
    FIELD_STACK_IN_PW,
    FIELD_STACK_OUT_PW,
    FIELD_STORM,
    FIELD_SUB_DEVICE,
    FIELD_SUB_TYPE,
    FIELD_SW,
    FIELD_SWITCH,
    FIELD_SWITCH_STATE,
    FIELD_SW_EPS,
    FIELD_SW_EPS_IN_PW,
    FIELD_SW_EPS_OUT_PW,
    FIELD_SW_EPS_STATE,
    FIELD_SYSTEM_ID,
    FIELD_SYSTEM_NAME,
    FIELD_SYSTEM_REGION,
    FIELD_SYS_SWITCH,
    FIELD_TARGET_MODULE_VERSION,
    FIELD_TARGET_VERSION,
    FIELD_TASK_TYPE,
    FIELD_TEMP_UNIT,
    FIELD_THIRD_PARTY_MQTT_ENABLE,
    FIELD_THIRD_PARTY_MQTT_IP,
    FIELD_THIRD_PARTY_MQTT_PASSWORD,
    FIELD_THIRD_PARTY_MQTT_PORT,
    FIELD_THIRD_PARTY_MQTT_TOKEN,
    FIELD_THIRD_PARTY_MQTT_USERNAME,
    FIELD_TIMESTAMP,
    FIELD_TIMEZONE,
    FIELD_TODAY_ENERGY,
    FIELD_TOTAL_ENERGY,
    FIELD_TS,
    FIELD_TYPE_NAME,
    FIELD_UNBIND,
    FIELD_UO,
    FIELD_UPDATES,
    FIELD_UPDATE_CONTENT,
    FIELD_UPDATE_STATUS,
    FIELD_UPGRADE_TYPE,
    FIELD_VERSION,
    FIELD_WNAME,
    FIELD_WORK_MODEL,
    FIELD_WPC,
    FIELD_WPS,
    LOCAL_DAILY_LIFETIME_METRICS,
    LOCAL_MQTT_RUNTIME_KEY,
    MAIN_PROPERTY_ALIAS_PAIRS,
    MQTT_ACTION_IDS_ALARM,
    MQTT_ACTION_IDS_COMBINE,
    MQTT_ACTION_IDS_DEVICE_PROPERTY,
    MQTT_ACTION_IDS_SCHEDULE,
    MQTT_ACTION_IDS_SUBDEVICE,
    MQTT_APP_CONFLICT_PAUSE_SEC,
    MQTT_AUTH_FAILURE_RCS,
    MQTT_AUTH_FAILURE_TOLERANCE,
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
    MQTT_CMD_SYNC_GRID_STANDARD,
    MQTT_CMD_SYNC_MQTT_CONNECT_INFO,
    MQTT_CMD_THIRD_PARTY_MQTT_CONFIG,
    MQTT_CMD_UNBIND_SMART_PART,
    MQTT_CMD_UPLOAD_DEVICE_ALERT,
    MQTT_CONNECT_BACKOFF_STEPS_SEC,
    MQTT_CREDENTIAL_CLIENT_ID,
    MQTT_CREDENTIAL_PASSWORD,
    MQTT_CREDENTIAL_USERNAME,
    MQTT_CREDENTIAL_USER_ID,
    MQTT_HOST,
    MQTT_LIVE_THRESHOLD_SEC,
    MQTT_MESSAGE_BIND_SMART_ACCESSORY,
    MQTT_MESSAGE_CANCEL_WEATHER_ALERT,
    MQTT_MESSAGE_CONTROL_COMBINE,
    MQTT_MESSAGE_CONTROL_SUB_DEVICE,
    MQTT_MESSAGE_DELETE_ELECTRICITY_STRATEGY,
    MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
    MQTT_MESSAGE_DOWNLOAD_DEVICE_SCHEDULE,
    MQTT_MESSAGE_INSERT_ELECTRICITY_STRATEGY,
    MQTT_MESSAGE_QUERY_CIRCUIT_PROPERTY,
    MQTT_MESSAGE_QUERY_COMBINE_DATA,
    MQTT_MESSAGE_QUERY_CURRENT_ELECTRICITY_STRATEGY,
    MQTT_MESSAGE_QUERY_DEVICE_PROPERTY,
    MQTT_MESSAGE_QUERY_ELECTRICITY_STRATEGY,
    MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
    MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG,
    MQTT_MESSAGE_QUERY_TOU_SCHEDULE,
    MQTT_MESSAGE_QUERY_WEATHER_PLAN,
    MQTT_MESSAGE_QUERY_WIFI_CONFIG,
    MQTT_MESSAGE_REMOVE_SMART_ACCESSORY,
    MQTT_MESSAGE_SEND_WEATHER_ALERT,
    MQTT_MESSAGE_SET_BATTERY_BOUNDARY,
    MQTT_MESSAGE_THIRD_PARTY_MQTT_CONFIG,
    MQTT_MESSAGE_TOU_SCHEDULE,
    MQTT_MESSAGE_UPDATE_ELECTRICITY_STRATEGY,
    MQTT_MESSAGE_UPLOAD_COMBINE_DATA,
    MQTT_MESSAGE_UPLOAD_DEVICE_ALERT,
    MQTT_MESSAGE_UPLOAD_INCREMENTAL_COMBINE_DATA,
    MQTT_MESSAGE_UPLOAD_WEATHER_PLAN,
    MQTT_PORT,
    MQTT_RECONNECT_THROTTLE_SEC,
    MQTT_SESSION_MAC_ID,
    MQTT_TOPIC_COMMAND,
    MQTT_TOPIC_PREFIX,
    MQTT_TOPIC_SUFFIXES,
    MQTT_TRANSIENT_BACKOFF_STEPS_SEC,
    NON_BATTERY_SUBDEVICE_TYPES,
    PACK_FIELD_LAST_SEEN_AT,
    PAYLOAD_ALARM,
    PAYLOAD_BATTERY_BOUNDARY,
    PAYLOAD_BATTERY_PACKS,
    PAYLOAD_BATTERY_TRENDS,
    PAYLOAD_CIRCUIT_PROPERTY,
    PAYLOAD_CT_METER,
    PAYLOAD_DATA_QUALITY,
    PAYLOAD_DEBUG_LOG_FILENAME,
    PAYLOAD_DEBUG_LOGGER_NAME,
    PAYLOAD_DEBUG_THROTTLE_SEC,
    PAYLOAD_DEVICE,
    PAYLOAD_DEVICE_META,
    PAYLOAD_DEVICE_STATISTIC,
    PAYLOAD_DISCOVERY,
    PAYLOAD_DISCOVERY_SOURCE,
    PAYLOAD_DYNAMIC_PRICE,
    PAYLOAD_ELECTRICITY_STRATEGY,
    PAYLOAD_HOME_TRENDS,
    PAYLOAD_HTTP_PROPERTIES,
    PAYLOAD_LOCAL_DAILY_ENERGY,
    PAYLOAD_LOCATION,
    PAYLOAD_METER_HEADS,
    PAYLOAD_MQTT_CONNECT_INFO,
    PAYLOAD_MQTT_LAST,
    PAYLOAD_NOTICE,
    PAYLOAD_OTA,
    PAYLOAD_PRICE,
    PAYLOAD_PRICE_HISTORY_CONFIG,
    PAYLOAD_PRICE_SOURCES,
    PAYLOAD_PROPERTIES,
    PAYLOAD_PV_TRENDS,
    PAYLOAD_SMART_MODE,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_SMART_SCHEDULE,
    PAYLOAD_STATISTIC,
    PAYLOAD_SUBDEVICES,
    PAYLOAD_SYSTEM,
    PAYLOAD_SYSTEM_META,
    PAYLOAD_TASK_PLAN,
    PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
    PAYLOAD_TIMEZONE_CONFIG,
    PAYLOAD_TOU_SCHEDULE,
    PAYLOAD_WEATHER_PLAN,
    PAYLOAD_WIFI_CONFIG,
    PAYLOAD_WIFI_LIST,
    POLL_WATCHDOG_CHECK_INTERVAL_SEC,
    POLL_WATCHDOG_MIN_STALL_SEC,
    POLL_WATCHDOG_STALL_FACTOR,
    PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID,
    PRESERVED_FAST_PAYLOAD_KEYS,
    PRICE_CONFIG_INTERVAL_SEC,
    REPAIR_ISSUE_APP_DATA_INCONSISTENCY,
    REPAIR_ISSUE_DEVICE_NOT_ACTIVATED,
    REPAIR_TRANSLATION_APP_DATA_INCONSISTENCY,
    REPAIR_TRANSLATION_DEVICE_NOT_ACTIVATED,
    SHELLY_CONTROL_ACTION_OFF,
    SHELLY_CONTROL_ACTION_ON,
    SHELLY_CONTROL_FUNCTION_SWITCH,
    SHELLY_REALTIME_FETCH_TIMEOUT_SEC,
    SLOW_METRICS_INTERVAL_SEC,
    SMART_METER_SUBTYPE,
    SUBDEVICE_DEV_TYPE_BATTERY_PACK,
    SUBDEVICE_DEV_TYPE_BREAKER,
    SUBDEVICE_DEV_TYPE_COMBO,
    SUBDEVICE_DEV_TYPE_CT,
    SUBDEVICE_DEV_TYPE_METER,
    SUBDEVICE_DEV_TYPE_METER_HEAD,
    SUBDEVICE_DEV_TYPE_SOCKET,
    SUBDEVICE_FIELD_LAST_SEEN_AT,
    SUBDEVICE_HINT_KEYS,
    SUBDEVICE_MAIN_MIRROR_KEYS,
    SUBDEVICE_ONLY_PROPERTY_KEYS,
    SUBDEVICE_SCAN_NAME_DEV_TYPES,
    SUBDEVICE_TYPE_SMART_METER,
    SUBDEVICE_TYPE_SMOKE,
    SUBDEVICE_TYPE_TEMP_HUMIDITY,
    SUBDEVICE_TYPE_WATER_LEAK,
    SYSTEM_INFO_CACHE_MAX_AGE_SEC,
    SYSTEM_INFO_KEYS,
    TIMER_TASK_ACTION_READ,
    TIMER_TASK_TYPE_CUSTOM_MODE,
    TIMER_TASK_TYPE_SMART_PLUG,
    TIMER_TASK_TYPE_TIME_ELEC,
    _STATISTICS_BACKFILL_STORE_KEY,
    _STATISTICS_BACKFILL_STORE_VERSION,
    _THIRD_PARTY_MQTT_CONFIG_KEYS,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Coroutine, Sequence
    from datetime import tzinfo

    from homeassistant.config_entries import ConfigEntry
    from homeassistant.core import HomeAssistant

    from .client.api import JackeryApi, MqttSessionSnapshot
    from .client.ble_transport import BleFrameObservation
    from .client.mqtt_push import JackeryMqttPushClient

from itertools import starmap
import operator

from .ingest import (
    TransportSource,
    gate_payload_section,
    gate_period_hierarchy_for_recorder,
    is_periodic_section,
)
from .util import (
    WHOLE_INT_TEXT_RE,
    app_chart_name_prefix,
    app_chart_period_meta,
    app_data_quality_warnings,
    app_month_request_kwargs,
    app_period_range,
    app_period_request_kwargs,
    app_year_request_kwargs,
    append_payload_debug_line,
    apply_year_month_backfill,
    chart_series_debug,
    circuit_id,
    config_entry_bool_option,
    config_entry_int_option,
    config_entry_str_option,
    day_power_energy_points,
    day_power_series_key,
    dev_mode_redactions_disabled,
    diagnostic_redactions_disabled,
    effective_period_total_value,
    external_trend_statistic_id,
    first_nonblank_int,
    format_data_quality_warning,
    guard_statistic_totals_from_year,
    historical_day_payload_from_sources,
    iter_calendar_months,
    iter_calendar_weeks,
    iter_calendar_years,
    normalized_data_quality_warnings,
    parse_statistics_backfill_date,
    safe_bool,
    safe_float,
    safe_int,
    stable_subdevice_key,
    stat_row_start,
    statistics_http_backfill_dates,
    sub_device_serial,
    trend_series_points,
    utc_now,
    verify_and_backfill,
    year_payload_appears_current_month_only,
)

_LOGGER = logging.getLogger(__name__)
#: Dedicated payload-debug channel. Restored from the 0.1.0 baseline: gating the
#: payload records on this logger's own level is what lets the maintainer switch
#: raw payload capture on through HA's normal logging controls and get ONLY the
#: payloads — no integration-wide DEBUG flood. The name constant survived in
#: const.py after the logger itself was dropped, leaving it unreferenced.
_PAYLOAD_DEBUG_LOGGER = logging.getLogger(PAYLOAD_DEBUG_LOGGER_NAME)
_BACKGROUND_TASK_STOP_TIMEOUT_SEC = 2.0


def _payload_debug_capture_enabled() -> bool:
    """Return whether raw-payload JSONL capture is currently active.

    Capture is controlled exclusively through Home Assistant's logging
    system (deep review 2026-07-25, HA logging best practices): the user
    raises the dedicated ``payload_debug`` logger to DEBUG via the
    ``logger:`` configuration. ``JACKERY_DEV_MODE`` no longer force-enables
    capture — coupling it to the env flag made the JSONL trace grow
    unbounded on every dev-mode install. Do NOT use ``isEnabledFor(DEBUG)``
    for the logger check: this child logger inherits DEBUG from the parent
    integration logger and would then write JSONL whenever ordinary HA
    debug logging is on. Requiring a concrete DEBUG level on this exact
    logger keeps the trace an explicit opt-in.
    """
    return _PAYLOAD_DEBUG_LOGGER.level == logging.DEBUG


#: PV channel property blocks addressed by 0-based index for per-input renames.
_PV_CHANNEL_FIELDS: tuple[str, ...] = (FIELD_PV1, FIELD_PV2, FIELD_PV3, FIELD_PV4)

# HomeCmdAction msgId/bleMsgType pairs from
# docs/source-of-truth/jackery_command_catalog_v2.csv. Portable commands use a
# different frame family and therefore never match this Home-only set.
_HOME_BLE_COMMAND_PAIRS: Final[frozenset[tuple[int, int]]] = frozenset({
    (3001, 1),
    (3002, 2),
    (3003, 3),
    (3004, 22),
    (3005, 99),
    (3006, 100),
    (3007, 101),
    (3008, 102),
    (3009, 103),
    (3010, 105),
    (3011, 106),
    (3012, 108),
    (3013, 109),
    (3014, 110),
    (3015, 112),
    (3016, 112),
    (3017, 112),
    (3018, 112),
    (3019, 120),
    (3020, 23),
    (3021, 121),
    (3022, 107),
    (3023, 107),
    (3024, 111),
    (3025, 111),
    (3026, 111),
    (3027, 121),
    (3028, 107),
    (3029, 121),
    (3030, 107),
    (3031, 110),
    (3032, 110),
    (3033, 110),
    (3034, 0),
    (3035, 0),
    (3036, 0),
    (3037, 110),
    (3038, 107),
    (3039, 121),
    (3040, 121),
    (3041, 121),
    (3042, 122),
    (3043, 121),
    (3044, 121),
    (3045, 124),
    (3046, 113),
    (3047, 114),
})

# Home Assistant schedules DataUpdateCoordinator intervals after a refresh
# returns and adds up to roughly half a second of stagger. Reserve 1.5 seconds
# from the configured 15-second start-to-start budget: 0.5 s for HA's stagger
# plus 1 s for cancellation and ordinary event-loop scheduling latency. Spend
# only the remaining time in the follow-up timer. A strictly positive minimum
# is required because DataUpdateCoordinator treats timedelta(0) as "disabled".
_POLL_CADENCE_SCHEDULER_MARGIN_SEC: Final = 1.5
_POLL_CADENCE_MIN_DELAY_SEC: Final = 0.05

# bleMsgType=0 marks Home actions without a BLE command type. The QUERY (read)
# commands are refresh polls that fire every coordinator cycle, and live
# evidence shows the device firmware never returns a command ACK for them over
# BLE (confirmed for 110 in the baseline; the same holds for the other reads).
# Routing them BLE-first with wait_for_ack burns a full ack timeout PER CYCLE
# before falling back to MQTT — which stalls the slow-metric refresh that feeds
# PV / sub-device sensors and violates the invariant that the poll path is
# never gated by BLE/MQTT. Send every query straight to MQTT instead; the end
# result is identical (they fell back to MQTT anyway) minus the per-cycle block.
#
# SET_THIRD_PARTY_MQTT_CONFIG (cmd 113) is excluded for a different reason: the
# third-party MQTT bridge config is an MQTT-layer command (mqtt_message_type
# "ThirdPartMQTTConfig" per docs/hbxn_commands.csv). BLE and MQTT are independent
# layers with their own per-layer commands, so this config must publish
# pure-MQTT and never reuse cmd 113 as a BLE bleMsgType. Its read-back twin
# (QUERY cmd 114) is already excluded below.
_BLE_FIRST_UNSUPPORTED_MSG_TYPES: Final[frozenset[int]] = frozenset({
    0,
    MQTT_CMD_QUERY_DEVICE_PROPERTY,
    MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
    MQTT_CMD_QUERY_COMBINE_DATA,
    MQTT_CMD_QUERY_WEATHER_PLAN,
    MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG,
    MQTT_CMD_THIRD_PARTY_MQTT_CONFIG,
})


#: Section prefixes whose chart ``y``/``y1``..``y6`` series are PV *generation*
#: curves (solar produced energy). Only PV stat/trends qualify: per the
#: source-of-truth chart glossary the battery/onGrid/eps/ct ``y`` series are
#: directional charge/discharge / in/out-grid magnitudes (out of scope), and the
#: symmetry section carries a documented negative ``n`` branch.
GENERATION_SECTION_PREFIXES: frozenset[str] = frozenset({
    APP_SECTION_PV_STAT,
    APP_SECTION_PV_TRENDS,
})

_DAY_TREND_SOURCE_BY_METRIC_KEY: Final[dict[str, tuple[str, str]]] = {
    # Home energy exists only as a system trend. PV and battery day curves
    # must use their device-stat endpoints: the system trend variants are
    # optional/sparser and previously won merely because they were non-empty,
    # producing tiny Recorder buckets while the full device curve was ignored.
    "home_energy": (PAYLOAD_HOME_TRENDS, APP_STAT_TOTAL_HOME_ENERGY),
}


def stable_payload_debug_signature(event: dict[str, Any]) -> str:
    """Return a content-only signature for payload-debug dedup."""
    payload = event.get("payload") or {}
    body = payload.get("body") if isinstance(payload, dict) else None
    if isinstance(body, dict):
        body_sig: Any = {
            key: value for key, value in body.items() if key != "messageId"
        }
    else:
        body_sig = body
    response = (
        event.get("response") if isinstance(event.get("response"), dict) else None
    )
    response_data = response.get("data") if response is not None else None
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


def exception_debug_message(err: BaseException) -> str:
    """Return a useful debug message for exceptions with empty ``str(err)``."""
    return f"{type(err).__name__}: {err or "(no message)"}"


def _slow_fetch_failure_log_level(
    err: JackeryError,
    *,
    suppressed: bool,
) -> int:
    """Return the HA log level for one cached slow-endpoint failure."""
    if suppressed or isinstance(err.__cause__, TimeoutError):
        return logging.DEBUG
    return logging.WARNING


def control_int(value: Any, field_name: str) -> int:  # ruff:ignore[any-type]
    """Return a finite integer control value or raise a coordinator error."""
    parsed = None if isinstance(value, bool) else safe_int(value)
    if parsed is None:
        msg = f"Invalid {field_name}"
        raise UpdateFailed(msg)
    return parsed


def transport_cmd(value: Any) -> int:  # ruff:ignore[any-type]
    """Return a command integer for MQTT/BLE transport routing."""
    parsed = first_nonblank_int(value)
    if parsed is None:
        msg = "cmd must be an integer"
        raise ValueError(msg)
    return parsed


def _load_mqtt_push_client() -> type[Any]:
    """Import the optional MQTT client module outside the event loop."""
    module = importlib.import_module(".client.mqtt_push", __package__)
    return cast("type[Any]", module.JackeryMqttPushClient)


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
_BLE_PARTIAL_UPDATE_COALESCE_SEC = 0.25
_LOCAL_MQTT_MAX_PENDING_MESSAGE_TASKS = 32
# 10422/10432: persistent parameter/bind failures. 10600: endpoint/feature not
# supported or not configured for this device (e.g. device/stat/symmetry and
# device/dynamic/v2/dynamicPrice for units without ATS / dynamic-price
# contracts).
# These are structurally persistent — the correct method is being called, the
# device simply does not serve that data — so re-issuing the request every cycle
# only spams the debug log with identical failures. Back them off on the long
# diagnostic ladder. Do not back off transient timeouts or generic "no data"
# responses: HTTP/API is the primary path and must retry on its normal cadence
# rather than being throttled by auxiliary endpoint noise. Energy/stat keys are
# still exempt from suppression via ``_endpoint_backoff_is_energy_key`` below.
_ENDPOINT_BACKOFF_CODES = frozenset({10422, 10432, 10600})
_ENDPOINT_BACKOFF_DELAYS_SEC: tuple[int, ...] = (300, 900, 3600, 21600)
# Timeouts carry no cloud error ``code=`` token, so the code-based backoff above
# never records them. A supplementary (Layer-5) endpoint that repeatedly TIMES
# OUT — e.g. Shelly Cloud realtime power for an unreachable device — would
# otherwise be re-fetched every cycle forever: the failed fetch never advances
# its cache timestamp, so the cache is "stale" again next cycle and the
# background refresh re-issues the request, hammering the third-party device.
# Give repeated timeouts their own escalating window, scoped strictly to the
# Shelly realtime key (``_SHELLY_REALTIME_BACKOFF_PREFIX``) so the primary HTTP
# path and every other enrichment cache keep their normal retry cadence. The
# sentinel code is negative so it never collides with a real cloud code.
_ENDPOINT_BACKOFF_TIMEOUT_CODE = -1
_ENDPOINT_BACKOFF_TIMEOUT_DELAYS_SEC: tuple[int, ...] = (60, 300, 900, 3600)
_SHELLY_REALTIME_BACKOFF_PREFIX = "shelly_realtime:"
# kWh/period endpoints feed the recorder and must keep flowing. Their backoff
# ladder is capped at two minutes so a transient cloud failure never escalates
# to the multi-hour window used for static diagnostics. The recorded window is
# tracking-only: ``_endpoint_backoff_active`` returns ``False`` for energy keys,
# so the next scheduled HTTP attempt is never actually suppressed.
_ENDPOINT_BACKOFF_ENERGY_DELAYS_SEC: tuple[int, ...] = (30, 60, 120)
# kWh/period endpoints feed the recorder and must keep flowing. They may return
# cache/default data after a failed request, but they must not be entered into an
# endpoint-backoff window that suppresses the next scheduled HTTP attempt.
# NOTE: ``symmetry_stat`` is intentionally NOT listed here. The ATS/symmetry
# endpoint does not feed a kWh recorder sensor for the shipped hardware and
# returns code=10600 (unsupported) every cycle, so it must be eligible for
# real backoff suppression rather than being re-fetched forever like the true
# energy/recorder endpoints below.
_ENDPOINT_BACKOFF_ENERGY_KEY_PARTS = (
    "battery_stat",
    "ct_stat",
    "eps_stat",
    "home_stat",
    "pv_stat",
    "today_energy",
)
# Endpoint-backoff key for the discovery-cadence smart-accessory sync. The
# synchronize endpoint is a genuine POST (SynchronizeSmartAccessoriesDataApi),
# but returns code=10600 (feature not provisioned) every discovery cycle for
# accounts without smart accessories, re-firing the same best-effort request
# forever. Routing it through the shared endpoint-backoff ladder suppresses the
# repeat attempts (and their debug spam) without touching the HTTP verb. The key
# deliberately avoids any ``_ENDPOINT_BACKOFF_ENERGY_KEY_PARTS`` substring so it
# is eligible for real suppression rather than tracking-only backoff.
_ACCESSORIES_SYNC_BACKOFF_KEY = "accessories_sync"


def _raise_config_entry_auth_failed(message: str, err: JackeryAuthError) -> NoReturn:
    """Raise HA reauth trigger for rejected Jackery credentials."""
    msg = f"{message}. Re-authentication is required."
    raise ConfigEntryAuthFailed(msg) from err


@dataclass(slots=True)
class BleConnectBackoff:
    """Exponential per-device spacing between BLE connect attempts."""

    initial_sec: float = BLE_CONNECT_BACKOFF_INITIAL_SEC
    max_sec: float = BLE_CONNECT_BACKOFF_MAX_SEC
    _delay_sec: float = dataclass_field(default=0.0, init=False)
    _not_before: float = dataclass_field(default=0.0, init=False)

    def seconds_until_allowed(self, now: float) -> float:
        """Return how long the caller must still wait before connecting."""
        return max(0.0, self._not_before - now)

    def record_failure(self, now: float) -> float:
        """Register a failed connect and open a new retry window."""
        if self._delay_sec <= 0:
            self._delay_sec = self.initial_sec
        else:
            self._delay_sec = min(self._delay_sec * 2, self.max_sec)
        self._not_before = now + self._delay_sec
        return self._delay_sec

    def record_success(self) -> None:
        """Reset the ladder after a successful connect."""
        self._delay_sec = 0.0
        self._not_before = 0.0


def is_mqtt_auth_failure(message: object) -> bool:
    """Determine whether the message indicates a broker-side MQTT credential.

    rejection.

    Parameters:
        message (object): An object convertible to text (e.g., an exception or log
        string) to be inspected for known broker rejection patterns.

    Returns:
        True if the text of `message` matches known broker credential-rejection
        indicators, False otherwise.
    """
    text = str(message or "").lower()
    if any(f"connect rc={rc}" in text for rc in MQTT_AUTH_FAILURE_RCS):
        return True
    # aiomqtt/paho surface CONNACK rejections as "[code:<rc>] ...". Only the
    # MQTT v5 class (128-135) is unambiguous there — low numbers collide with
    # paho client-side error codes (e.g. code 4 = MQTT_ERR_NO_CONN).
    if any(
        f"code:{rc}" in text or f"code {rc}" in text
        for rc in MQTT_AUTH_FAILURE_RCS
        if rc >= 128  # ruff:ignore[magic-value-comparison]
    ):
        return True
    return "bad user name or password" in text or "not authorized" in text


def is_transient_connect_failure(message: object) -> bool:
    """Detect whether an MQTT connection failure indicates a transient network.

    or server issue.

    Checks the message text for known transient indicators such as
    "server unavailable",
    "connection refused", "connection timed out", or the word "unknown".
    Auth/ban rejections (including MQTT v5 rc=133) are never transient —
    they are classified by :func:`is_mqtt_auth_failure` first.

    Returns:
        `true` if the message indicates a transient connect failure, `false` otherwise.
    """
    if is_mqtt_auth_failure(message):
        return False
    text = str(message or "").lower()
    return (
        "server unavailable" in text
        or "connection refused" in text
        or "connection timed out" in text
        or "unknown" in text
    )


def mqtt_connect_failure_signature(message: object) -> str:
    """Create a short, stable signature from an MQTT/setup error message for.

    backoff deduplication.

    Parameters:
        message (object): Error text or object convertible to string describing the
        connect/setup failure.

    Returns:
        signature (str): A normalized signature:
            - "tls_missing_authority_key_identifier" for messages containing "Missing
            Authority Key Identifier".
            - "tls_certificate_verify_failed" for messages containing
            "CERTIFICATE_VERIFY_FAILED".
            - The first 160 characters of the message for messages starting with "MQTT
            not connected yet" or any other message.
            - "unknown" if the message is empty or falsy.
    """
    text = str(message or "").strip() or "unknown"
    if "Missing Authority Key Identifier" in text:
        return "tls_missing_authority_key_identifier"
    if "CERTIFICATE_VERIFY_FAILED" in text:
        return "tls_certificate_verify_failed"
    if text.startswith("MQTT not connected yet"):
        return text[:160]
    return text[:160]


class MqttConnectionManager:
    """Cloud MQTT connection state: backoff, pause, throttle, auth.

    The coordinator creates one instance and delegates all MQTT
    connection lifecycle decisions here.  The manager never owns the
    MQTT client itself — it only tracks state and answers *"should we
    try to connect / reconnect?"* questions.
    """

    def __init__(self) -> None:
        """Initialize MQTT connection state tracker.

        Tracks connection fingerprint, reconnect/backoff timers and steps, auth-failure
        state, pause windows for app-conflict handling, and a flag for whether a
        generated MAC warning was logged.

        Attributes:
            fingerprint (tuple[str|None, str|None, str|None] | None): Last known
            connection fingerprint (client_id, host, session or similar).
            generated_mac_warning_logged (bool): Whether a generated MAC warning has
            been logged.
            last_connect_attempt (float): Monotonic timestamp of the last connect
            attempt.
            auth_failure_message (str | None): Legacy diagnostic slot. MQTT does not
            own reauthentication; HTTP/API remains the only auth authority.
            paused_until_monotonic (float): Monotonic timestamp until which reconnects
            are paused due to auth/app-conflict.
            app_conflict_pause_cycles (int): Number of auth-triggered pause cycles
            applied.
            backoff_until_monotonic (float): Monotonic timestamp until which reconnect
            attempts are backed off.
            backoff_step (int): Current index in the backoff sequence (-1 means
            cleared).
            backoff_signature (str | None): Normalized signature of the last failure
            used to deduplicate/backoff progression.
        """
        self.fingerprint: tuple[str | None, str | None, str | None] | None = None
        self.generated_mac_warning_logged = False
        self.last_connect_attempt: float = 0.0
        self.auth_failure_message: str | None = None
        self.paused_until_monotonic: float = 0.0
        self.app_conflict_pause_cycles: int = 0
        self.backoff_until_monotonic: float = 0.0
        self.backoff_step: int = -1
        self.backoff_signature: str | None = None

    # ------------------------------------------------------------------
    # Backoff helpers
    # ------------------------------------------------------------------

    def backoff_remaining(self) -> int:
        """Return the seconds remaining in the Cloud-MQTT reconnect backoff.

        window.

        Returns:
            int: Seconds remaining until backoff expires, or 0 if no backoff is active.
        """
        return max(0, int(self.backoff_until_monotonic - time.monotonic()))

    def note_connect_failure(self, message: object) -> None:
        """Enter or extend Cloud-MQTT reconnect backoff after a setup or.

        connect failure.

        Selects a transient or permanent backoff sequence based on the provided error
        message, advances the backoff step when the failure signature repeats (or
        resets it when the signature changes), sets the next backoff expiry (monotonic
        timestamp), and logs the resulting pause duration and failure signature.

        Parameters:
            message (object): Error or diagnostic text/object used to derive a
            normalized failure signature and to classify the failure as transient or
            permanent.
        """
        signature = mqtt_connect_failure_signature(message)
        transient = is_transient_connect_failure(message)
        backoff_steps = (
            MQTT_TRANSIENT_BACKOFF_STEPS_SEC
            if transient
            else MQTT_CONNECT_BACKOFF_STEPS_SEC
        )
        repeated = signature == self.backoff_signature
        if repeated:
            self.backoff_step = min(
                self.backoff_step + 1,
                len(backoff_steps) - 1,
            )
        else:
            self.backoff_signature = signature
            self.backoff_step = 0
        delay = backoff_steps[self.backoff_step]
        self.backoff_until_monotonic = time.monotonic() + delay
        # Announce a new failure signature once at INFO; repeats of the
        # same signature only grow the backoff and stay at DEBUG so a
        # broker outage cannot flood the HA log.
        _LOGGER.log(
            logging.DEBUG if repeated else logging.INFO,
            "Jackery MQTT paused for %ds after %s connect failure (%s); "
            "HTTP, BLE and local MQTT remain active",
            delay,
            "transient" if transient else "permanent",
            signature,
        )

    def clear_connect_backoff(self) -> None:
        """Clear Cloud-MQTT connect backoff after a successful broker session."""
        if self.backoff_signature is not None:
            _LOGGER.debug(
                "Jackery MQTT connect backoff recovered after %s",
                self.backoff_signature,
            )
        self.backoff_until_monotonic = 0.0
        self.backoff_step = -1
        self.backoff_signature = None

    def pause_after_auth_failure(
        self,
        message: object,
        *,
        streak: int | None = None,
    ) -> None:
        """Pause MQTT reconnects for a fixed app-conflict window after the.

        broker rejects credentials.

        This sets a pause window during which reconnect attempts are suppressed (HTTP
        polling is expected to remain active) and increments the internal app-conflict
        pause cycle counter. If a pause is already active, this call does nothing.

        Parameters:
            message (object): The broker rejection message or diagnostic text used for
            logging.
            streak (int | None): Consecutive authentication failure count, if known;
            used only for logging.
        """
        now = time.monotonic()
        if self.paused_until_monotonic > now:
            return
        self.app_conflict_pause_cycles += 1
        self.paused_until_monotonic = now + MQTT_APP_CONFLICT_PAUSE_SEC
        # First pause cycle of an incident is actionable (shared account /
        # ban) and logged at INFO; follow-up cycles of the same incident
        # are demoted to DEBUG to keep the reconnect loop quiet. The cycle
        # counter resets once a connection succeeds.
        _LOGGER.log(
            logging.INFO if self.app_conflict_pause_cycles == 1 else logging.DEBUG,
            "Jackery MQTT paused for %ds after broker credential rejection "
            "(streak %s, pause cycle %d: %s); HTTP polling remains active",
            MQTT_APP_CONFLICT_PAUSE_SEC,
            streak if streak is not None else "unknown",
            self.app_conflict_pause_cycles,
            message,
        )

    # ------------------------------------------------------------------
    # Reconnect decision helpers
    # ------------------------------------------------------------------

    def should_skip_reconnect(
        self,
        mqtt: JackeryMqttPushClient | None,
        current_fingerprint: tuple[str | None, str | None, str | None] | None,
        *,
        force: bool = False,
    ) -> bool:
        """Decides whether a reconnect attempt should be skipped.

        Performs checks for a matching connection fingerprint, an app-conflict pause
        window, active backoff, and a short reconnect throttle; a matching
        started-and-connected client or any active pause/backoff/throttle causes the
        reconnect to be skipped unless overridden.

        Parameters:
            mqtt: The MQTT client instance (or None) used to determine
            started/connected state.
            current_fingerprint: The fingerprint tuple for the currently available
            connection; compared to the manager's stored fingerprint to detect changes.
            force: If True, bypasses the fast-path fingerprint match and the throttle
            check.

        Returns:
            True if the coordinator should not attempt a reconnect now, False otherwise.
        """
        if mqtt is None:
            return True

        now = time.monotonic()

        # A manager fingerprint can lag behind a healthy client when the async
        # on-connect callback loses a race with lifecycle cleanup. Do not adopt
        # the API fingerprint from connectivity alone: the cached credentials
        # may already have rotated. Let ``mqtt.async_start()`` verify the actual
        # client session first, bypassing retry state only for this unknown case.
        verify_connected_session = (
            mqtt.is_started and mqtt.is_connected and self.fingerprint is None
        )

        # A healthy connection whose session fingerprint is already known can
        # safely clear stale retry state and short-circuit the reconnect path.
        if (
            mqtt.is_started
            and mqtt.is_connected
            and self.fingerprint == current_fingerprint
        ):
            self.record_connect_success(mqtt, current_fingerprint)
            if self.app_conflict_pause_cycles or self.paused_until_monotonic:
                self.app_conflict_pause_cycles = 0
                self.paused_until_monotonic = 0.0
            if not force:
                return True

        # App-conflict pause
        if (
            not force
            and not verify_connected_session
            and self.paused_until_monotonic > now
        ):
            return True

        # Backoff
        backoff = 0.0 if force or verify_connected_session else self.backoff_remaining()
        if backoff > 0:
            return True

        # Throttle
        fingerprint_changed = (
            self.fingerprint is not None and self.fingerprint != current_fingerprint
        )
        reconnect_needed = fingerprint_changed or not mqtt.is_connected
        return bool(
            not verify_connected_session
            and not force
            and reconnect_needed
            and now - self.last_connect_attempt < MQTT_RECONNECT_THROTTLE_SEC
        )

    def record_connect_attempt(self) -> None:
        """Record the monotonic timestamp of the most recent MQTT connection attempt.

        Updates the manager's last_connect_attempt to the current monotonic time.
        """
        self.last_connect_attempt = time.monotonic()

    def record_connect_success(
        self,
        mqtt: JackeryMqttPushClient | None,
        current_fingerprint: tuple[str | None, str | None, str | None] | None,
    ) -> None:
        """Record a successful MQTT connection, update its fingerprint, and.

        clearing any connect backoff.

        Parameters:
            mqtt (JackeryMqttPushClient | None): The MQTT client that succeeded; if
            None, no state is changed.
            current_fingerprint (tuple[str | None, str | None, str | None] | None):
            Fingerprint tuple to store as the last successful connection.
        """
        if mqtt is not None:
            self.fingerprint = current_fingerprint
            self.clear_connect_backoff()

    def handle_connect_error(
        self,
        mqtt: JackeryMqttPushClient | None,
        error: object,
    ) -> None:
        """Classify a connection error and trigger an auth pause or a.

        connect backoff.

        If the provided MQTT client exposes a stored "last_error" in its diagnostics,
        that value is preferred for classification. If the error (or last_error)
        indicates broker-side credential rejection, schedule an app-conflict pause via
        pause_after_auth_failure and pass the client's consecutive_auth_failures as the
        streak. Otherwise, record the failure for backoff using note_connect_failure.
        If `mqtt` is None, no action is taken.

        Parameters:
            mqtt: MQTT client whose diagnostics and consecutive_auth_failures are
            consulted; may be None.
            error: The error object or message to classify (used when diagnostics do
            not provide a last_error).
        """
        if mqtt is None:
            return
        last_error = mqtt.diagnostics.get("last_error")
        if is_mqtt_auth_failure(error) or is_mqtt_auth_failure(last_error):
            streak = mqtt.consecutive_auth_failures
            self.pause_after_auth_failure(last_error or error, streak=streak)
        else:
            self.note_connect_failure(last_error or error)

    def defer_background_auth_failure(
        self,
        mqtt: JackeryMqttPushClient | None,
        message: str,
    ) -> None:
        """Handle a background MQTT authentication failure without reauth.

        If the message indicates broker-side credential rejection, start an
        app-conflict pause window (using the client's consecutive auth-failure streak
        when available). Other background auth-looking failures are logged and ignored
        for reauth purposes: MQTT is supplemental and must not drive HA reauth.

        Parameters:
            mqtt (JackeryMqttPushClient | None): The MQTT client instance, or None if
            unavailable.
            message (str): The authentication failure message text to inspect and store.
        """
        if "MQTT broker rejected credentials" in message or is_mqtt_auth_failure(
            message,
        ):
            streak = mqtt.consecutive_auth_failures if mqtt else None
            self.pause_after_auth_failure(message, streak=streak)
            return
        self.auth_failure_message = None
        _LOGGER.debug(
            "Jackery MQTT background auth notice ignored for reauth; "
            "HTTP/API polling remains the auth authority: %s",
            message,
        )


try:  # pragma: no cover - SQLAlchemy ships with recorder; guard minimal envs.
    from sqlalchemy.exc import SQLAlchemyError

    _SQLALCHEMY_IMPORT_ERRORS: tuple[type[BaseException], ...] = (SQLAlchemyError,)
except ImportError:  # pragma: no cover
    _SQLALCHEMY_IMPORT_ERRORS = ()

ACTION_WRITE_ERRORS = (
    JackeryError,
    HomeAssistantError,
    TimeoutError,
    OSError,
    RuntimeError,
    ValueError,
    TypeError,
    KeyError,
)
BACKGROUND_TASK_ERRORS = ACTION_WRITE_ERRORS
_RECORDER_BASE_ERRORS: tuple[type[BaseException], ...] = (
    HomeAssistantError,
    ValueError,
    TypeError,
    KeyError,
    LookupError,
    OSError,
    RuntimeError,
)
RECORDER_IMPORT_ERRORS: tuple[type[BaseException], ...] = (
    *_RECORDER_BASE_ERRORS,
    *_SQLALCHEMY_IMPORT_ERRORS,
)
RECORDER_BACKGROUND_TASK_ERRORS: tuple[type[BaseException], ...] = (
    *BACKGROUND_TASK_ERRORS,
    *RECORDER_IMPORT_ERRORS,
)
PAYLOAD_PARSE_ERRORS = (
    UnicodeDecodeError,
    JSONDecodeError,
    ValueError,
    TypeError,
    KeyError,
)
STORAGE_ERRORS = (
    HomeAssistantError,
    OSError,
    ValueError,
    TypeError,
    KeyError,
    RuntimeError,
)
AUTH_ERRORS = (ConfigEntryAuthFailed, JackeryAuthError)

_DICT_LIST_ID_KEYS = frozenset({"devId", "deviceId", "id", "idx"})
_DICT_LIST_SERIAL_KEYS = frozenset({"devSn", "deviceSn", "sn"})


def merge_dict_values(base: dict[str, Any], updates: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge nested dictionaries while preserving old keys."""
    merged = dict(base)
    for key, value in updates.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = merge_dict_values(current, value)
        else:
            merged[key] = value
    return merged


def changed_dict_values(
    before: dict[str, Any],
    after: dict[str, Any],
) -> dict[str, Any]:
    """Return only values added or changed in ``after``.

    Nested dictionaries are diffed recursively. Lists and scalar values are
    treated atomically because live transport list payloads already carry their
    own identity-aware merge semantics.
    """
    changed: dict[str, Any] = {}
    for key, value in after.items():
        if key not in before:
            changed[key] = copy.deepcopy(value)
            continue
        previous = before[key]
        if isinstance(previous, dict) and isinstance(value, dict):
            nested = changed_dict_values(previous, value)
            if nested:
                changed[key] = nested
        elif previous != value:
            changed[key] = copy.deepcopy(value)
    return changed


def _is_blank_value(value: object) -> bool:
    """Return whether an update value is too sparse to replace a populated one."""
    if value is None:
        return True
    if isinstance(value, str) and not value.strip():
        return True
    return isinstance(value, (list, dict)) and not value


def _dict_list_identity_values(item: dict[str, Any]) -> frozenset[str]:
    """Return stable identity tokens for an incremental dict-list item."""
    identities: set[str] = set()
    for key in _DICT_LIST_SERIAL_KEYS:
        value = item.get(key)
        if not _is_blank_value(value):
            identities.add(f"serial:{value}")
    for key in _DICT_LIST_ID_KEYS:
        value = item.get(key)
        if not _is_blank_value(value):
            identities.add(f"{key}:{value}")
    return frozenset(identities)


def _clean_dict_list_update(update: dict[str, Any]) -> dict[str, Any]:
    """Drop blank values before appending a new sparse list item."""
    return {key: value for key, value in update.items() if not _is_blank_value(value)}


def _merge_identified_dict_lists(
    current: list[Any],
    updates: list[Any],
) -> list[dict[str, Any]] | None:
    """Merge sparse dict-list updates when every update carries stable identity."""
    if not all(isinstance(item, dict) for item in current):
        return None
    if not all(isinstance(item, dict) for item in updates):
        return None
    typed_updates = [item for item in updates if isinstance(item, dict)]
    update_identities = [_dict_list_identity_values(item) for item in typed_updates]
    if not update_identities or any(not identities for identities in update_identities):
        return None

    merged = [dict(item) for item in current if isinstance(item, dict)]
    for raw_update, identities in zip(typed_updates, update_identities, strict=True):
        target_idx = next(
            (
                idx
                for idx, item in enumerate(merged)
                if identities & _dict_list_identity_values(item)
            ),
            None,
        )
        if target_idx is None:
            cleaned = _clean_dict_list_update(raw_update)
            if cleaned:
                merged.append(cleaned)
            continue
        merged[target_idx] = merge_present_dict_values(
            merged[target_idx],
            raw_update,
        )
    return merged


def merge_present_dict_values(
    base: dict[str, Any],
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Merge sparse live payloads without blanking populated existing values."""
    merged = dict(base)
    for key, value in updates.items():
        current = merged.get(key)
        if _is_blank_value(value) and not _is_blank_value(current):
            continue
        if isinstance(current, dict) and isinstance(value, dict):
            merged[key] = merge_present_dict_values(current, value)
        elif isinstance(current, list) and isinstance(value, list):
            identified = _merge_identified_dict_lists(current, value)
            merged[key] = value if identified is None else identified
        else:
            merged[key] = value
    return merged


def merge_missing_dict_values(
    base: dict[str, Any],
    updates: Mapping[str, Any],
) -> dict[str, Any]:
    """Fill blank or absent fields without replacing populated live values."""
    merged = dict(base)
    for key, value in updates.items():
        current = merged.get(key)
        if isinstance(current, dict) and isinstance(value, Mapping):
            merged[key] = merge_missing_dict_values(current, value)
        elif (key not in merged or _is_blank_value(current)) and not _is_blank_value(
            value,
        ):
            merged[key] = copy.deepcopy(value)
    return merged


def sync_property_aliases(
    values: dict[str, Any],
    alias_pairs: tuple[tuple[str, str], ...] | list[tuple[str, str]],
) -> dict[str, Any]:
    """Mirror equivalent app property names after merge operations."""
    synced = dict(values)
    for left, right in alias_pairs:
        left_value = synced.get(left)
        right_value = synced.get(right)
        if left_value is None and right_value is not None:
            synced[left] = right_value
        elif right_value is None and left_value is not None:
            synced[right] = left_value
    return synced


def find_dict_with_any_key(obj: object, keys: frozenset[str]) -> dict[str, Any] | None:
    """Return the first nested dict containing any key from ``keys``."""
    if isinstance(obj, dict):
        if any(key in obj for key in keys):
            return obj
        for value in obj.values():
            found = find_dict_with_any_key(value, keys)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for value in obj:
            found = find_dict_with_any_key(value, keys)
            if found is not None:
                return found
    return None


def find_list_for_key(obj: object, key: str) -> list[Any] | None:
    """Find a nested list of dicts under a key such as batteryPacks."""
    if isinstance(obj, dict):
        value = obj.get(key)
        if isinstance(value, list):
            return value
        for nested in obj.values():
            found = find_list_for_key(nested, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for nested in obj:
            found = find_list_for_key(nested, key)
            if found is not None:
                return found
    return None


def normalize_live_property_payload(source: dict[str, Any]) -> dict[str, Any]:
    """Return a shallow copy of a live property payload.

    Lifetime energy counters are live payload data. They bypass ingest like the
    other live properties and are protected by the HTTP-first merge guard, so a
    supplemental MQTT/BLE/local-MQTT frame can only fill them when HTTP omitted
    the key.
    """
    return dict(source)


async def call(
    coordinator: JackerySolarVaultCoordinator,
    method: str,
    *args: object,
    **kwargs: object,
) -> object:
    """Call a characterized coordinator setter by name."""
    return await getattr(coordinator, method)(*args, **kwargs)


def normalized_company_id(value: object) -> int | None:
    """Return a provider ID when the app payload encodes a whole number."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    text = str(value).strip()
    if not text or not WHOLE_INT_TEXT_RE.fullmatch(text):
        return None
    try:
        return int(float(text))
    except ValueError as err:
        _LOGGER.debug("Discarding non-numeric provider ID %r: %s", text, err)
        return None


def normalized_region(value: object) -> str | None:
    """Return a normalized electricity-price region token."""
    if value is None:
        return None
    text = str(value).strip().upper()
    return text or None


def source_regions(source: dict[str, Any]) -> list[str]:
    """Extract and normalize region tokens from a price source."""
    raw = source.get(FIELD_SYSTEM_REGION) or source.get(FIELD_COUNTRY)
    if raw in {None, ""}:
        return []
    return [part.strip() for part in str(raw).split(",") if part.strip()]


def normalized_source_regions(source: dict[str, Any]) -> list[str]:
    """Return normalized region tokens for a price provider source."""
    regions: list[str] = []
    for region in source_regions(source):
        normalized = normalized_region(region)
        if normalized is not None and normalized not in regions:
            regions.append(normalized)
    return regions


def first_nonblank_source_name(source: dict[str, Any], *keys: str) -> str | None:
    """Return the first non-empty provider display-name field."""
    for key in keys:
        value = source.get(key)
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def valid_price_sources(sources: object) -> list[dict[str, Any]]:
    """Return price provider dictionaries with a company id and region."""
    if not isinstance(sources, list):
        return []
    valid: list[dict[str, Any]] = []
    for item in sources:
        if not isinstance(item, dict):
            continue
        company_id = normalized_company_id(item.get(FIELD_PLATFORM_COMPANY_ID))
        if company_id is None or not normalized_source_regions(item):
            continue
        valid.append(item)
    return valid


def is_alarm_message(
    msg_type: str | None,
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Determine whether an MQTT message represents an alarm or alert.

    Checks the message type, action ID, and the command field in `body` for known
    alarm/alert indicators.

    Returns:
        `true` if the message is an alarm or alert, `false` otherwise.
    """
    return (
        msg_type == MQTT_MESSAGE_UPLOAD_DEVICE_ALERT
        or action_id in MQTT_ACTION_IDS_ALARM
        or body.get(FIELD_CMD) == MQTT_CMD_UPLOAD_DEVICE_ALERT
    )


def is_third_party_mqtt_config_message(
    msg_type: str | None,
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Determine whether an MQTT message is a third-party MQTT configuration.

    operation.

    Parameters:
        msg_type (str | None): The message type to check.
        action_id (int | None): The numeric action identifier to check.
        body (dict[str, Any]): The message payload; the function checks
        `body.get(FIELD_CMD)` for command matching.

    Returns:
        True if the message type, action id, or `body[FIELD_CMD]` indicates a
        third-party MQTT config request or query, False otherwise.
    """
    return (
        msg_type
        in {
            MQTT_MESSAGE_THIRD_PARTY_MQTT_CONFIG,
            MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG,
        }
        or action_id
        in {
            ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG,
            ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
        }
        or body.get(FIELD_CMD)
        in {
            MQTT_CMD_THIRD_PARTY_MQTT_CONFIG,
            MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG,
        }
    )


def is_wifi_config_message(
    msg_type: str | None,
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Determine whether the MQTT message represents a WiFi configuration query.

    Returns:
        `true` if the message is a WiFi config query, `false` otherwise.
    """
    return (
        action_id in {ACTION_ID_QUERY_WIFI_CONFIG, ACTION_ID_PORTABLE_GET_WIFI_CONFIG}
        or msg_type == MQTT_MESSAGE_QUERY_WIFI_CONFIG
        or body.get(FIELD_CMD) == MQTT_CMD_QUERY_WIFI_CONFIG
    )


def is_wifi_list_message(
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Determine whether the MQTT message requests the WiFi list.

    Parameters:
        action_id (int | None): Action identifier from the message header; may match
        ACTION_ID_READ_WIFI_LIST.
        body (dict[str, Any]): Message payload; may contain a command under FIELD_CMD.

    Returns:
        true if the message requests a WiFi list, false otherwise.
    """
    return (
        action_id == ACTION_ID_READ_WIFI_LIST
        or body.get(FIELD_CMD) == MQTT_CMD_READ_WIFI_LIST
    )


def is_time_zone_config_message(
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Identify MQTT messages that request or provide the device time zone.

    Parameters:
        action_id (int | None): Message action identifier that may indicate a time zone
        get/send.
        body (dict[str, Any]): Message payload; `FIELD_CMD` may contain the command key.

    Returns:
        True if the message is a time zone get or send command, False otherwise.
    """
    return action_id in {
        ACTION_ID_GET_TIME_ZONE,
        ACTION_ID_SEND_TIME_ZONE,
    } or body.get(FIELD_CMD) in {MQTT_CMD_GET_TIME_ZONE, MQTT_CMD_SEND_TIME_ZONE}


def is_grid_standard_sync_message(
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Determine whether the MQTT message represents a grid standard synchronization.

    @returns
        `true` if the message represents a grid standard sync, `false` otherwise.
    """
    return (
        action_id == ACTION_ID_SYNC_GRID_STANDARD
        or body.get(FIELD_CMD) == MQTT_CMD_SYNC_GRID_STANDARD
    )


def is_mqtt_connect_info_message(
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Determine whether a message requests or synchronizes MQTT connection information.

    Parameters:
        action_id (int | None): Numeric action identifier from the message metadata;
        may be None.
        body (dict[str, Any]): Message body; the function checks the value under
        `FIELD_CMD`.

    Returns:
        bool: `True` if `action_id` equals `ACTION_ID_SYNC_MQTT_CONNECT_INFO` or
        `body.get(FIELD_CMD)` equals `MQTT_CMD_SYNC_MQTT_CONNECT_INFO`, `False`
        otherwise.
    """
    return (
        action_id == ACTION_ID_SYNC_MQTT_CONNECT_INFO
        or body.get(FIELD_CMD) == MQTT_CMD_SYNC_MQTT_CONNECT_INFO
    )


def is_device_ota_version_message(
    action_id: int | None,
    body: dict[str, Any],
) -> bool:
    """Determine whether the MQTT message requests the device OTA version.

    Parameters:
        action_id (int | None): Numeric action identifier from the MQTT message.
        body (dict[str, Any]): Message payload; may contain a command under FIELD_CMD.

    Returns:
        bool: `true` if the message is a device OTA version query, `false` otherwise.
    """
    return (
        action_id == ACTION_ID_GET_DEVICE_OTA_VERSION
        or body.get(FIELD_CMD) == MQTT_CMD_GET_DEVICE_OTA_VERSION
    )


def is_subdevice_payload(
    payload: dict[str, Any],
    body: dict[str, Any],
    subdevice_hint_keys: frozenset[str],
    battery_pack_hint_keys: frozenset[str],
    subdevice_dev_type_strings: frozenset[str],
) -> bool:
    """Identify MQTT accessory payloads mixed into the app device topic."""
    msg_type = str(payload.get(FIELD_MESSAGE_TYPE) or "")
    if "SubDevice" in msg_type:
        return True
    action_id = first_nonblank_int(payload.get(FIELD_ACTION_ID))
    if action_id is not None and action_id in MQTT_ACTION_IDS_SUBDEVICE:
        return True
    updates = body.get(FIELD_UPDATES)
    if isinstance(updates, dict) and any(
        key in updates for key in subdevice_hint_keys | battery_pack_hint_keys
    ):
        return True
    dev_type = body.get(FIELD_DEV_TYPE) or body.get(FIELD_DEVICE_TYPE)
    if dev_type is not None and str(dev_type) in subdevice_dev_type_strings:
        return True
    return any(key in body for key in subdevice_hint_keys)


def normalize_battery_pack_payload(item: object) -> dict[str, Any]:
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
            normalized = merge_dict_values(normalized, nested)
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


def looks_like_battery_pack(  # ruff:ignore[too-many-return-statements]
    item: object,
    ct_meter_keys: frozenset[str],
    battery_pack_hint_keys: frozenset[str],
) -> bool:
    """Return True for add-on battery pack dicts, not CT/smart meters."""
    if not isinstance(item, dict):
        return False
    if any(key in item for key in ct_meter_keys):
        return False
    if (
        str(item.get(FIELD_DEV_TYPE) or item.get(FIELD_DEVICE_TYPE) or "")
        in NON_BATTERY_SUBDEVICE_TYPES
    ):
        return False
    if str(item.get(FIELD_DEV_TYPE) or item.get(FIELD_DEVICE_TYPE) or "") == str(
        SUBDEVICE_DEV_TYPE_BATTERY_PACK
    ):
        return True
    scan_name = str(item.get(FIELD_SCAN_NAME) or "").lower()
    if "shelly" in scan_name or "3em" in scan_name:
        return False
    if str(item.get(FIELD_SUB_TYPE) or "") == SMART_METER_SUBTYPE:
        return False
    return any(key in item for key in battery_pack_hint_keys)


def battery_packs_from_source(
    source: object,
    ct_meter_keys: frozenset[str],
    battery_pack_hint_keys: frozenset[str],
) -> list[dict[str, Any]] | None:
    """Extract add-on battery pack payloads from known shapes."""
    for key in (
        FIELD_BATTERY_PACKS,
        FIELD_BATTERY_PACK,
        FIELD_BATTERY_PACK_LIST,
        FIELD_BATTERIES,
        FIELD_PACK_LIST,
    ):
        packs = find_list_for_key(source, key)
        if packs:
            normalized = [normalize_battery_pack_payload(item) for item in packs]
            filtered = [
                item
                for item in normalized
                if looks_like_battery_pack(item, ct_meter_keys, battery_pack_hint_keys)
            ]
            return filtered or normalized
    if isinstance(source, list):
        normalized = [normalize_battery_pack_payload(item) for item in source]
        packs = [
            item
            for item in normalized
            if looks_like_battery_pack(item, ct_meter_keys, battery_pack_hint_keys)
        ]
        return packs or None
    sub_devices = find_list_for_key(source, FIELD_SUB_DEVICE)
    if sub_devices:
        normalized = [normalize_battery_pack_payload(item) for item in sub_devices]
        packs = [
            item
            for item in normalized
            if looks_like_battery_pack(item, ct_meter_keys, battery_pack_hint_keys)
        ]
        if packs:
            return packs
    normalized_source = normalize_battery_pack_payload(source)
    if looks_like_battery_pack(
        normalized_source,
        ct_meter_keys,
        battery_pack_hint_keys,
    ):
        return [normalized_source]
    return None


def subdevice_serial(item: dict[str, Any]) -> str | None:
    """Return the stable serial field used by app subdevice payloads."""
    serial = item.get(FIELD_DEVICE_SN) or item.get(FIELD_DEV_SN) or item.get(FIELD_SN)
    if serial in {None, ""}:
        return None
    value = str(serial).strip()
    return value or None


def battery_pack_serial(item: Mapping[str, Any]) -> str | None:
    """Return a battery pack's own serial without using its parent device id."""
    identity = item.get(FIELD_DEVICE_SN) or item.get(FIELD_DEV_SN) or item.get(FIELD_SN)
    if identity in {None, ""}:
        return None
    value = str(identity).strip()
    return value or None


def sorted_battery_pack_payloads(items: object) -> list[dict[str, Any]]:
    """Sort identified packs by serial and keep unidentified arrival order."""
    if not isinstance(items, list):
        return []
    identified: list[tuple[str, dict[str, Any]]] = []
    unidentified: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        serial = battery_pack_serial(item)
        if serial is None:
            unidentified.append(item)
        else:
            identified.append((serial, item))
    identified.sort(key=operator.itemgetter(0))
    return [item for _serial, item in identified] + unidentified


def valid_discovery_list_response(response: object) -> bool:
    """Return whether legacy discovery returned non-empty identified entries."""
    if not isinstance(response, Mapping):
        return False
    payload = response.get(FIELD_DATA)
    return (
        isinstance(payload, list)
        and bool(payload)
        and all(valid_discovery_device_identity(item) for item in payload)
    )


def valid_discovery_device_identity(item: object) -> bool:
    """Return whether a discovery item has an identity later indexing can use."""
    if not isinstance(item, dict):
        return False
    for key in (
        FIELD_DEVICE_ID,
        FIELD_ID,
        FIELD_DEV_ID,
        FIELD_DEVICE_SN,
        FIELD_DEV_SN,
        FIELD_SN,
    ):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, int) and not isinstance(value, bool) and value != 0:
            return True
    return False


def valid_system_parent_identity(item: object) -> bool:
    """Return whether a system-list parent has the ID fields indexing consumes."""
    if not isinstance(item, dict):
        return False
    for key in (FIELD_DEVICE_ID, FIELD_ID):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, int) and not isinstance(value, bool) and value != 0:
            return True
    return False


def valid_system_discovery_identity(item: object) -> bool:
    """Return whether a system entry has the identity used by discovery."""
    if not isinstance(item, dict):
        return False
    for key in (FIELD_ID, FIELD_SYSTEM_ID):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            return True
        if isinstance(value, int) and not isinstance(value, bool) and value != 0:
            return True
    return False


def valid_system_discovery_entries(entries: object) -> bool:
    """Return whether non-empty systems contain explicit valid device lists."""
    if not isinstance(entries, list):
        return False
    return bool(entries) and all(
        isinstance(system, dict)
        and valid_system_discovery_identity(system)
        and isinstance((devices := system.get(FIELD_DEVICES)), list)
        and all(valid_discovery_device_identity(device) for device in devices)
        for system in entries
    )


def valid_system_discovery_response(response: object) -> bool:
    """Return whether system discovery is non-empty and removal-authoritative."""
    return isinstance(response, Mapping) and valid_system_discovery_entries(
        response.get(FIELD_DATA)
    )


def subdevice_id(item: dict[str, Any]) -> str | None:
    """Return the cloud id field used by accessory HTTP statistic APIs."""
    dev_id = item.get(FIELD_DEVICE_ID) or item.get(FIELD_ID) or item.get(FIELD_DEV_ID)
    return str(dev_id) if dev_id else None


def subdevice_identity_values(item: Mapping[str, Any]) -> set[str]:
    """Return matching identities used across system-list and Shelly APIs."""
    values: set[str] = set()
    for key in (
        FIELD_DEVICE_ID,
        FIELD_ID,
        FIELD_DEV_ID,
        FIELD_DEVICE_SN,
        FIELD_DEV_SN,
        FIELD_SN,
        FIELD_BIND_ID,
        FIELD_DEVICE_CODE,
    ):
        value = item.get(key)
        if value not in {None, ""}:
            values.add(str(value))
    return values


def subdevice_dev_type(
    item: Mapping[str, Any],
    *,
    rejection_callback: Callable[[str], None] | None = None,
) -> int | None:
    """Return the documented subdevice devType, including Shelly scan names."""
    raw_type = item.get(FIELD_DEV_TYPE) or item.get(FIELD_DEVICE_TYPE)
    if raw_type is not None and raw_type != "":  # ruff:ignore[compare-to-empty-string]
        try:
            return int(str(raw_type))
        except TypeError:
            if rejection_callback is not None:
                rejection_callback("subdevice_dev_type_type_error")
        except ValueError:
            if rejection_callback is not None:
                rejection_callback("subdevice_dev_type_value_error")
    scan_name = str(item.get(FIELD_SCAN_NAME) or "").lower()
    return SUBDEVICE_SCAN_NAME_DEV_TYPES.get(scan_name)


def is_smart_meter_accessory(item: dict[str, Any]) -> bool:
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


def smart_meter_accessories(source: dict[str, Any]) -> list[dict[str, Any]]:
    """Return Smart-Meter accessory metadata from coordinator payload or index."""
    accessories: Any = source.get(FIELD_ACCESSORIES)
    if not isinstance(accessories, list):
        system = source.get(PAYLOAD_SYSTEM) or source.get(PAYLOAD_SYSTEM_META) or {}
        accessories = system.get(FIELD_ACCESSORIES) if isinstance(system, dict) else []
    if not isinstance(accessories, list):
        return []
    return [
        item
        for item in accessories
        if isinstance(item, dict) and is_smart_meter_accessory(item)
    ]


def smart_meter_accessory_device_id(source: dict[str, Any]) -> str | None:
    """Return the app's subDeviceId for CT statistic endpoints."""
    for item in smart_meter_accessories(source):
        dev_id = (
            item.get(FIELD_DEVICE_ID) or item.get(FIELD_ID) or item.get(FIELD_DEV_ID)
        )
        if dev_id is not None:
            return str(dev_id)

    ct = source.get(PAYLOAD_CT_METER) or {}
    if isinstance(ct, dict):
        dev_id = ct.get(FIELD_DEVICE_ID) or ct.get(FIELD_ID) or ct.get(FIELD_DEV_ID)
        if dev_id is not None:
            return str(dev_id)
    return None


def has_smart_meter_accessory(payload: dict[str, Any]) -> bool:
    """Return True when discovery metadata contains a CT/smart meter accessory."""
    return bool(smart_meter_accessories(payload))


def has_subdevice_accessory_or_bucket(
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


def has_meter_head_accessory(payload: dict[str, Any]) -> bool:
    """Return True when discovery or a prior MQTT reply mentions a meter head."""
    if has_subdevice_accessory_or_bucket(
        payload,
        dev_type=SUBDEVICE_DEV_TYPE_METER_HEAD,
        bucket=PAYLOAD_METER_HEADS,
    ):
        return True
    return has_subdevice_accessory_or_bucket(
        payload,
        dev_type=SUBDEVICE_DEV_TYPE_METER,
        bucket=PAYLOAD_METER_HEADS,
    )


def has_smart_plug_accessory(payload: dict[str, Any]) -> bool:
    """Return True when discovery or a prior MQTT reply mentions a smart plug."""
    return has_subdevice_accessory_or_bucket(
        payload,
        dev_type=SUBDEVICE_DEV_TYPE_SOCKET,
        bucket=PAYLOAD_SMART_PLUGS,
    )


def has_breaker_accessory(payload: dict[str, Any]) -> bool:
    """Return True when discovery or a prior MQTT reply mentions a breaker."""
    return has_subdevice_accessory_or_bucket(
        payload,
        dev_type=SUBDEVICE_DEV_TYPE_BREAKER,
        bucket=PAYLOAD_CIRCUIT_PROPERTY,
    )


def has_sub_device_accessory(payload: dict[str, Any]) -> bool:
    """Return True when discovery or a prior MQTT reply mentions generic subdevices."""
    items = payload.get(PAYLOAD_SUBDEVICES)
    if isinstance(items, list) and any(isinstance(item, dict) for item in items):
        return True
    system = payload.get(PAYLOAD_SYSTEM) or payload.get(PAYLOAD_SYSTEM_META) or {}
    accessories: Any = payload.get(FIELD_ACCESSORIES)
    if not isinstance(accessories, list) and isinstance(system, dict):
        accessories = system.get(FIELD_ACCESSORIES)
    if isinstance(accessories, list):
        for item in accessories:
            if not isinstance(item, dict):
                continue
            item_type = str(
                item.get(FIELD_DEV_TYPE) or item.get(FIELD_DEVICE_TYPE) or ""
            )
            if item_type in {
                SUBDEVICE_TYPE_SMOKE,
                SUBDEVICE_TYPE_TEMP_HUMIDITY,
                SUBDEVICE_TYPE_WATER_LEAK,
            }:
                return True
    return False


def subdevice_accessories(
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


def subdevice_stat_id(
    payload: dict[str, Any],
    subdevice: dict[str, Any],
    *,
    dev_type: int,
) -> str | None:
    """Resolve the accessory id needed by app statistic endpoints."""
    direct_id = subdevice_id(subdevice)
    if direct_id:
        return direct_id
    serial = subdevice_serial(subdevice)
    candidates = subdevice_accessories(payload, dev_type=dev_type)
    if serial:
        for item in candidates:
            if subdevice_serial(item) == serial:
                return subdevice_id(item)
    if len(candidates) == 1:
        return subdevice_id(candidates[0])
    return None


def entry_subdevice_candidates(
    entry: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return known accessory dictionaries for one coordinator entry."""
    candidates: list[dict[str, Any]] = []
    system = entry.get(PAYLOAD_SYSTEM) or entry.get(PAYLOAD_SYSTEM_META) or {}
    accessories = system.get(FIELD_ACCESSORIES) if isinstance(system, dict) else []
    if isinstance(accessories, list):
        candidates.extend(item for item in accessories if isinstance(item, dict))
    ct = entry.get(PAYLOAD_CT_METER)
    if isinstance(ct, dict):
        candidates.append(ct)
    for bucket in (PAYLOAD_SMART_PLUGS, PAYLOAD_METER_HEADS):
        items = entry.get(bucket)
        if isinstance(items, list):
            candidates.extend(item for item in items if isinstance(item, dict))
    return candidates


def battery_packs_need_query(
    payload: dict[str, Any],
    *,
    rejection_callback: Callable[[str], None] | None = None,
) -> bool:
    """Return True when add-on packs exist or are expected."""
    raw_props = payload.get(PAYLOAD_PROPERTIES) or {}
    props = raw_props if isinstance(raw_props, dict) else {}
    raw_http_props = payload.get(PAYLOAD_HTTP_PROPERTIES) or {}
    if isinstance(raw_http_props, dict) and raw_http_props:
        props = merge_present_dict_values(props, raw_http_props)
    raw_expected = props.get(FIELD_BAT_NUM)
    if raw_expected is None or raw_expected == "":  # ruff:ignore[compare-to-empty-string]
        expected = 0
    elif isinstance(raw_expected, bool):
        # bool is an int subclass; a True/False batNum is a schema error,
        # not a pack count of 1/0.
        if rejection_callback is not None:
            rejection_callback("battery_pack_bat_num_type_error")
        expected = 0
    else:
        try:
            expected = max(0, int(float(raw_expected)))
        except TypeError:
            if rejection_callback is not None:
                rejection_callback("battery_pack_bat_num_type_error")
            expected = 0
        except ValueError, OverflowError:
            if rejection_callback is not None:
                rejection_callback("battery_pack_bat_num_value_error")
            expected = 0
    packs = payload.get(PAYLOAD_BATTERY_PACKS)
    if not isinstance(packs, list):
        return expected > 0
    if expected > 0:
        return True
    return bool(packs)


# ---------------------------------------------------------------------------
# MQTT envelope normalization
# ---------------------------------------------------------------------------


def normalize_local_mqtt_payload(
    payload: dict[str, Any],
) -> dict[str, Any]:
    """Wrap a body-only LAN MQTT payload in the cloud-MQTT envelope when.

    necessary.

    A payload is already envelope-shaped only when ``body`` or ``data`` is a
    dictionary.  Some third-party bridges add ``messageType``/``actionId`` to an
    otherwise body-only payload; treating those scalar metadata fields as proof
    of an envelope drops every top-level live value.

    Returns:
        dict: The original payload (if already envelope-like) or a new envelope
        containing the payload under FIELD_BODY with copied device identifier keys when
        present.
    """
    if isinstance(payload.get(FIELD_BODY), dict) or isinstance(
        payload.get(FIELD_DATA),
        dict,
    ):
        return dict(payload)
    envelope_keys = {
        FIELD_MESSAGE_TYPE,
        FIELD_ACTION_ID,
        FIELD_TIMESTAMP,
        FIELD_DEVICE_ID,
        FIELD_DEV_ID,
        FIELD_DEVICE_SN,
        FIELD_DEV_SN,
        FIELD_SN,
    }
    body = {
        key: value
        for key, value in payload.items()
        if key not in envelope_keys and key not in {FIELD_BODY, FIELD_DATA}
    }
    envelope: dict[str, Any] = {
        key: value
        for key, value in payload.items()
        if key in envelope_keys and value is not None
    }
    envelope[FIELD_BODY] = body
    return envelope


# ---------------------------------------------------------------------------
# Property sanitization and normalization
# ---------------------------------------------------------------------------

_MAIN_PROPERTY_ALIAS_PAIRS = (
    ("inPw", "inPower"),
    ("outPw", "outPower"),
    ("elecFreq", "frequency"),
    ("soc", "batterySoc"),
    ("batSoc", "soc"),
)
_MAIN_PROPERTY_EXCLUDE_KEYS = SUBDEVICE_ONLY_PROPERTY_KEYS | {
    FIELD_THIRD_PARTY_MQTT_ENABLE,
    FIELD_THIRD_PARTY_MQTT_IP,
    FIELD_THIRD_PARTY_MQTT_PORT,
    FIELD_THIRD_PARTY_MQTT_USERNAME,
    FIELD_THIRD_PARTY_MQTT_PASSWORD,
    FIELD_THIRD_PARTY_MQTT_TOKEN,
}
_PERIODIC_PROPERTY_SECTION_PREFIXES = frozenset({
    PAYLOAD_STATISTIC,
    PAYLOAD_DEVICE_STATISTIC,
    APP_SECTION_PV_STAT,
    APP_SECTION_HOME_STAT,
    APP_SECTION_BATTERY_STAT,
    APP_SECTION_CT_STAT,
    APP_SECTION_EPS_STAT,
    APP_SECTION_SOCKET_STAT,
    APP_SECTION_SYMMETRY_STAT,
    APP_SECTION_TODAY_ENERGY,
    APP_SECTION_PV_TRENDS,
    APP_SECTION_HOME_TRENDS,
    APP_SECTION_BATTERY_TRENDS,
})

_PV_CHANNEL_PROPERTY_KEYS: tuple[str, ...] = (
    FIELD_PV1,
    FIELD_PV2,
    FIELD_PV3,
    FIELD_PV4,
)


def _is_periodic_property_section_key(key: str) -> bool:
    """Return True for stats/trends sections that must not become properties."""
    return any(
        key == prefix or key.startswith(f"{prefix}_")
        for prefix in _PERIODIC_PROPERTY_SECTION_PREFIXES
    )


def sanitize_main_properties(props: dict[str, Any]) -> dict[str, Any]:
    """Remove accessory-only properties and normalize main-device PV fields.

    Accessory-only, third-party MQTT config, and stats/trends section keys are
    removed. Numeric PV channel scalars are normalized to PV dictionaries.

    Returns:
        dict[str, Any]: The cleaned and alias-normalized properties mapping.
    """
    clean = {
        key: value
        for key, value in dict(props).items()
        if key not in _MAIN_PROPERTY_EXCLUDE_KEYS
        and not _is_periodic_property_section_key(key)
    }
    for channel_key in _PV_CHANNEL_PROPERTY_KEYS:
        channel_value = clean.get(channel_key)
        if isinstance(channel_value, dict):
            continue
        if channel_value is None or isinstance(channel_value, bool):
            continue
        channel_power = safe_float(channel_value)
        if channel_power is None:
            continue
        clean[channel_key] = {FIELD_PV_PW: channel_value}

    return sync_property_aliases(clean, _MAIN_PROPERTY_ALIAS_PAIRS)


# ---------------------------------------------------------------------------
# Battery-pack list merging
# ---------------------------------------------------------------------------


def merge_battery_pack_lists(
    current: Any,  # loose prior-state list, duck-typed via `current or []`  # ruff:ignore[any-type]
    updates: list[dict[str, Any]],
    *,
    refresh_last_seen: bool = True,
) -> list[dict[str, Any]]:
    """Merge incremental battery-pack telemetry into an existing pack list while.

    preserving learned and static fields.

    Overlay non-null update fields onto existing dict items, matching by device SN
    (FIELD_DEVICE_SN / FIELD_DEV_SN / FIELD_SN). Position is used only when an update
    has no serial identity; an unknown serial is appended instead of overwriting a
    different identified pack. Non-dict and None entries from the prior list are
    ignored. The function updates touched packs' PACK_FIELD_LAST_SEEN_AT timestamp
    when the caller confirms a fresh transport response.

    Returns:
        Merged list of battery pack dictionaries.
    """
    merged: list[dict[str, Any]] = [
        dict(item) for item in current or [] if isinstance(item, dict)
    ]
    index_by_sn: dict[str, int] = {}
    for idx, item in enumerate(merged):
        if (sn := battery_pack_serial(item)) is not None:
            index_by_sn[sn] = idx

    touched_indices: set[int] = set()
    for update_idx, raw_update in enumerate(updates):
        update = {key: value for key, value in raw_update.items() if value is not None}
        for identity_key in (FIELD_DEVICE_SN, FIELD_DEV_SN, FIELD_SN):
            identity = update.get(identity_key)
            if not identity or not str(identity).strip():
                update.pop(identity_key, None)
        sn = battery_pack_serial(update)
        target_idx = index_by_sn.get(sn) if sn is not None else None
        if target_idx is None and sn is None and update_idx < len(merged):
            target_idx = update_idx

        if target_idx is None:
            merged.append(dict(update))
            target_idx = len(merged) - 1
            if sn is not None:
                index_by_sn[sn] = target_idx
        else:
            merged[target_idx] = merge_dict_values(merged[target_idx], update)
            if sn is not None:
                index_by_sn[sn] = target_idx
        touched_indices.add(target_idx)

    if refresh_last_seen:
        # Only an actual transport response is proof-of-life. Callers replaying
        # a TTL cache explicitly disable this stamp.
        now_iso = datetime.now(UTC).isoformat()
        for index in touched_indices:
            merged[index][PACK_FIELD_LAST_SEEN_AT] = now_iso

    return merged


# ---------------------------------------------------------------------------
# Subdevice list merging
# ---------------------------------------------------------------------------


def merge_subdevice_lists_by_sn(
    current: Any,  # loose prior-state list, duck-typed via `current or []`  # ruff:ignore[any-type]
    updates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge a list of subdevice telemetry entries with incoming updates, matching by.

    device serial number when available.

    This returns a new list of subdevice dicts produced by:
    - copying dict items from `current` (non-dict entries are ignored),
    - removing keys with `None` values from each update before applying,
    - attempting to match each update to an existing item by serial number (checked in
    order: `deviceSn`, `devSn`, `sn`),
    - using the update's positional index only when it has no serial identity,
    - appending the update as a new item when neither a serial match nor a positional
    fallback is available,
    - overlaying update keys onto the matched item (existing keys are preserved when
    not present in the update).

    Parameters:
        current: Prior list-like state (may be None); only dict items are considered
        and copied.
        updates: Sequence of update dicts to merge; update keys with value `None` are
        ignored.

    Returns:
        list[dict[str, Any]]: The merged list of subdevice dictionaries.
    """
    merged: list[dict[str, Any]] = [
        dict(item) for item in current or [] if isinstance(item, dict)
    ]
    index_by_sn: dict[str, int] = {}
    for idx, item in enumerate(merged):
        if (sn := subdevice_serial(item)) is not None:
            index_by_sn[sn] = idx

    for update_idx, raw_update in enumerate(updates):
        update = {key: value for key, value in raw_update.items() if value is not None}
        sn = subdevice_serial(update)
        target_idx = index_by_sn.get(sn) if sn is not None else None
        if target_idx is None and sn is None and update_idx < len(merged):
            target_idx = update_idx

        if target_idx is None:
            merged.append(dict(update))
            if sn is not None:
                index_by_sn[sn] = len(merged) - 1
        else:
            merged[target_idx] = merge_dict_values(merged[target_idx], update)
            if sn is not None:
                index_by_sn[sn] = target_idx
    return merged


def merge_subdevice_list_by_identity(
    current: Any,  # loose prior-state list, duck-typed via `current or []`  # ruff:ignore[any-type]
    update: dict[str, Any],
) -> list[dict[str, Any]]:
    """Merge Shelly Cloud accessory data by stable identity values and return an.

    updated list.

    Builds a working copy of `current` (ignoring non-dict entries), removes keys with
    `None` from `update`, and computes identity values via `subdevice_identity_values`.
    If any existing item's identity set intersects the update's identity set, overlays
    the update onto that first matching item (using `merge_dict_values`) and returns
    the merged list. If no match is found and the cleaned update has identity values,
    appends the cleaned update. Non-dict entries in `current` are ignored in the
    resulting list.

    Parameters:
        current (Any): Prior list-like state; dict items are copied and non-dict
        entries are ignored.
        update (dict[str, Any]): Incoming accessory data; keys with `None` are
        discarded before matching.

    Returns:
        list[dict[str, Any]]: New list of subdevice dicts with the update merged into a
        matching identity entry or appended when no match exists.
    """
    cleaned = {key: value for key, value in update.items() if value is not None}
    merged: list[dict[str, Any]] = [
        dict(item) for item in current or [] if isinstance(item, dict)
    ]
    update_ids = subdevice_identity_values(cleaned)
    for idx, item in enumerate(merged):
        if update_ids and update_ids & subdevice_identity_values(item):
            merged[idx] = merge_dict_values(item, cleaned)
            return merged
    if cleaned and update_ids:
        merged.append(cleaned)
    return merged


def merge_smart_plug_lists(
    current: Any,  # loose prior-state list, duck-typed via `current or []`  # ruff:ignore[any-type]
    updates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge incremental smart-plug telemetry entries using device serial numbers to.

    align updates with existing entries.

    Parameters:
        current (Any): Prior list-like smart-plug state; may be None and may contain
        non-dict items — only dictionary items are considered when merging.
        updates (list[dict[str, Any]]): List of update dictionaries; update entries
        have `None` values removed before being merged.

    Returns:
        list[dict[str, Any]]: Merged list of smart-plug dictionaries where updates are
        overlaid onto existing entries matched by device serial number
        (`deviceSn`/`devSn`/`sn`), or by position only when the update has no serial.
    """
    return merge_subdevice_lists_by_sn(current, updates)


def merge_circuits(
    current: Any,  # ruff:ignore[any-type]
    updates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge incremental circuit breaker telemetry using idx to align updates."""
    return _merge_subdevice_lists_by_fn(current, updates, circuit_id)


def merge_sub_devices(
    current: Any,  # ruff:ignore[any-type]
    updates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge generic sub-device telemetry using serial numbers to align updates."""
    return _merge_subdevice_lists_by_fn(current, updates, sub_device_serial)


def _merge_subdevice_lists_by_fn(
    current: Any,  # ruff:ignore[any-type]
    updates: list[dict[str, Any]],
    serial_fn: Any,  # ruff:ignore[any-type]
) -> list[dict[str, Any]]:
    """Merge a list of subdevice telemetry entries with incoming updates, matching by.

    identity extracted via serial_fn.
    """
    merged: list[dict[str, Any]] = [
        dict(item) for item in current or [] if isinstance(item, dict)
    ]
    index_by_sn: dict[str, int] = {}
    for idx, item in enumerate(merged):
        sn = serial_fn(item)
        if sn:
            index_by_sn[str(sn)] = idx

    for update_idx, raw_update in enumerate(updates):
        update = {key: value for key, value in raw_update.items() if value is not None}
        sn = serial_fn(update)
        target_idx = index_by_sn.get(str(sn)) if sn else None
        if target_idx is None and sn is None and update_idx < len(merged):
            target_idx = update_idx

        if target_idx is None:
            merged.append(dict(update))
            if sn:
                index_by_sn[str(sn)] = len(merged) - 1
        else:
            merged[target_idx] = merge_dict_values(merged[target_idx], update)
            if sn:
                index_by_sn[str(sn)] = target_idx
    return merged


# ---------------------------------------------------------------------------
# Stale battery-pack cleanup
# ---------------------------------------------------------------------------


def drop_stale_battery_packs(
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
    silent for the full threshold (default 30 days) before it is
    dropped, so daily WiFi blips or manual reboots do not trigger
    spurious removal.
    """
    if not packs:
        return packs, 0, []
    now = datetime.now(UTC)
    kept: list[dict[str, Any]] = []
    stale = 0
    dropped_indices: list[int] = []
    for index, pack in enumerate(packs):
        last_seen = pack.get(PACK_FIELD_LAST_SEEN_AT)
        if not isinstance(last_seen, str):
            # Establish a baseline even if no further packet ever arrives.
            stamped = dict(pack)
            stamped[PACK_FIELD_LAST_SEEN_AT] = now.isoformat()
            kept.append(stamped)
            continue
        try:
            seen_at = datetime.fromisoformat(last_seen)
            if seen_at.tzinfo is None:
                seen_at = seen_at.replace(tzinfo=UTC)
        except ValueError as err:
            # Corrupt timestamp; keep but rewrite so future passes
            # have a clean baseline.
            _LOGGER.debug(
                "Battery pack %s has unparseable last-seen timestamp %r, "
                "resetting baseline: %s",
                index,
                last_seen,
                err,
            )
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


# ---------------------------------------------------------------------------
# Stale non-battery subdevice cleanup (F-SW3-A2)
# ---------------------------------------------------------------------------


def stamp_subdevice_last_seen(
    merged: list[dict[str, Any]],
    touched_serials: set[str],
    serial_fn: Callable[[dict[str, Any]], str | None],
) -> list[dict[str, Any]]:
    """Refresh the last-seen timestamp for entries mentioned in this update.

    A partial MQTT/BLE/shadow update proves presence only for identities it
    contains. Absence from that partial update is not evidence of unbinding.
    """
    if not touched_serials:
        return merged
    now_iso = datetime.now(UTC).isoformat()
    for item in merged:
        sn = serial_fn(item)
        if sn and sn in touched_serials:
            item[SUBDEVICE_FIELD_LAST_SEEN_AT] = now_iso
    return merged


def drop_stale_subdevices(
    items: list[dict[str, Any]],
    *,
    confirmed_absent: Callable[[dict[str, Any]], bool],
    threshold_seconds: int = BATTERY_PACK_STALE_THRESHOLD_SEC,
) -> tuple[list[dict[str, Any]], int, list[int]]:
    """Remove accessories both stale and absent from authoritative discovery.

    Silence alone is insufficient because transport updates are partial.
    ``confirmed_absent`` must be based on a complete HTTP discovery result.
    Missing or invalid timestamps establish a clean baseline instead of
    authorizing removal.
    """
    if not items:
        return items, 0, []
    now = datetime.now(UTC)
    kept: list[dict[str, Any]] = []
    stale = 0
    dropped_indices: list[int] = []
    for index, item in enumerate(items):
        last_seen = item.get(SUBDEVICE_FIELD_LAST_SEEN_AT)
        if not isinstance(last_seen, str):
            # Establish a baseline even if an unbound accessory never emits again.
            stamped = dict(item)
            stamped[SUBDEVICE_FIELD_LAST_SEEN_AT] = now.isoformat()
            kept.append(stamped)
            continue
        try:
            seen_at = datetime.fromisoformat(last_seen)
            if seen_at.tzinfo is None:
                seen_at = seen_at.replace(tzinfo=UTC)
        except ValueError as err:
            # Corrupt timestamp; keep but rewrite so future passes
            # have a clean baseline.
            _LOGGER.debug(
                "Subdevice entry %s has unparseable last-seen timestamp %r, "
                "resetting baseline: %s",
                index,
                last_seen,
                err,
            )
            fixed = dict(item)
            fixed[SUBDEVICE_FIELD_LAST_SEEN_AT] = now.isoformat()
            kept.append(fixed)
            continue
        elapsed = (now - seen_at).total_seconds()
        if elapsed > threshold_seconds and confirmed_absent(item):
            stale += 1
            dropped_indices.append(index)
            continue
        kept.append(item)
    return kept, stale, dropped_indices


def resolve_device_id_from_payload(payload: dict[str, Any]) -> str | None:
    """Extract the parent device identifier from a payload slice.

    Searches top-level keys in order: "deviceId", "device_id", then "id". If none are
    present or valid, and the payload contains a "properties" dict, searches "deviceId"
    and "device_id" there. Accepts string or integer values and returns the value as a
    stripped string.

    Returns:
        device_id (str | None): The extracted device identifier as a stripped string if
        found, `None` otherwise.
    """
    for key in ("deviceId", "device_id", "id"):
        value = payload.get(key)
        if (
            isinstance(value, str | int)
            and not isinstance(value, bool)
            and str(value).strip()
        ):
            return str(value).strip()
    props = payload.get("properties")
    if isinstance(props, dict):
        for key in ("deviceId", "device_id"):
            value = props.get(key)
            if (
                isinstance(value, str | int)
                and not isinstance(value, bool)
                and str(value).strip()
            ):
                return str(value).strip()
    return None


# ---------------------------------------------------------------------------
# BLE lifetime counter merging
# ---------------------------------------------------------------------------


def merge_battery_pack_lifetime_from_ble(  # ruff:ignore[too-many-return-statements, too-many-branches]
    updated: dict[str, Any],
    body: dict[str, Any],
) -> bool:
    """Merge BLE lifetime counters into the matching battery-pack.

    entry.

    Updates the `updated["batteryPacks"]` list when a pack with a matching serial
    number (from the payload) has its `inEgy` or `outEgy` changed, or when no matching
    pack exists (a minimal pack is appended containing the counters and identifying
    fields). Does nothing and returns `False` if the payload lacks a device serial,
    `batteryPacks` is not a list, or neither `inEgy` nor `outEgy` are present.

    Returns:
        `True` if `updated["batteryPacks"]` was modified (existing pack fields changed
        or a new minimal pack appended), `False` otherwise.
    """
    sn = body.get(FIELD_DEVICE_SN)
    if not sn:
        return False
    sn_str = str(sn).strip()
    if not sn_str:
        return False
    packs_key = FIELD_BATTERY_PACKS
    packs = updated.get(packs_key)
    if not isinstance(packs, list):
        packs_key = PAYLOAD_BATTERY_PACKS
        packs = updated.get(packs_key)
    if not isinstance(packs, list):
        return False
    in_egy = body.get(FIELD_IN_EGY)
    out_egy = body.get(FIELD_OUT_EGY)
    if in_egy is None and out_egy is None:
        return False
    # Match by deviceSn. Pack lists are short (<=5 packs) so a
    # linear scan is fine.
    touched = False
    matched = False
    merged_packs: list[Any] = []
    for pack in packs:
        if not isinstance(pack, dict):
            merged_packs.append(pack)
            continue
        pack_sn_raw = (
            pack.get(FIELD_DEVICE_SN) or pack.get(FIELD_DEV_SN) or pack.get(FIELD_SN)
        )
        pack_sn = str(pack_sn_raw).strip() if pack_sn_raw is not None else None
        if pack_sn != sn_str:
            merged_packs.append(pack)
            continue
        matched = True
        new_pack = dict(pack)
        if in_egy is not None and new_pack.get(FIELD_IN_EGY) != in_egy:
            new_pack[FIELD_IN_EGY] = in_egy
            touched = True
        if out_egy is not None and new_pack.get(FIELD_OUT_EGY) != out_egy:
            new_pack[FIELD_OUT_EGY] = out_egy
            touched = True
        merged_packs.append(new_pack)
    if touched:
        updated[packs_key] = merged_packs
        return True
    if matched:
        return False

    # BLE can report lifetime counters for packs that are not yet
    # present in the MQTT/HTTP pack list. Create a minimal pack so
    # lifetime entities do not stay unrouted forever.
    minimal_pack: dict[str, Any] = {
        FIELD_DEVICE_SN: sn_str,
        FIELD_DEV_TYPE: body.get(FIELD_DEV_TYPE),
        FIELD_SUB_TYPE: body.get(FIELD_SUB_TYPE),
        PACK_FIELD_LAST_SEEN_AT: datetime.now(UTC).isoformat(),
    }
    if in_egy is not None:
        minimal_pack[FIELD_IN_EGY] = in_egy
    if out_egy is not None:
        minimal_pack[FIELD_OUT_EGY] = out_egy
    merged_packs.append(minimal_pack)
    updated[packs_key] = merged_packs
    return True


# ---------------------------------------------------------------------------
# OTA metadata merging
# ---------------------------------------------------------------------------


def merge_pack_ota(pack: dict[str, Any], ota: dict[str, Any]) -> None:
    """Merge OTA metadata into a battery pack dictionary in place.

    Copies the OTA version (from `currentVersion` or `version`) into both `version` and
    `currentVersion` on the pack. For each OTA key (isFirmwareUpgrade, targetVersion,
    targetModuleVersion, updateStatus, updateContent, upgradeType), if the key exists
    in `ota` and its value is not None, writes that key/value into `pack`.

    Parameters:
        pack (dict[str, Any]): Battery pack object to update in-place.
        ota (dict[str, Any]): OTA metadata source whose fields will be merged into
        `pack`.
    """
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


def merge_battery_pack_ota_lists(
    current: Any,  # loose prior-state list, duck-typed via `current or []`  # ruff:ignore[any-type]
    ota_updates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Merge OTA metadata into a battery-pack list by matching serial numbers.

    and return an updated list capped to five entries.

    The function copies up to the first five dict items from `current`, then overlays
    OTA-related fields from `ota_updates` onto matching packs. Matching prefers
    serial-number keys (`deviceSn`, `devSn`, `sn`) and uses position only when the
    update has no serial. Only OTA keys that are present in an update and
    not `None` are applied. The function does not modify last-seen timestamps and
    always returns at most five pack dicts.

    Parameters:
        current (Any): Prior pack list (may be None or a heterogeneous sequence); only
        dict items are considered.
        ota_updates (list[dict[str, Any]]): Sequence of OTA update dicts; each may
        include serial-number keys and OTA fields.

    Returns:
        list[dict[str, Any]]: Updated list of battery pack dicts (maximum length 5)
        with OTA fields merged where applicable.
    """
    merged: list[dict[str, Any]] = [
        dict(item) for item in current or [] if isinstance(item, dict)
    ][:5]
    index_by_sn: dict[str, int] = {}
    for idx, item in enumerate(merged):
        if (sn := battery_pack_serial(item)) is not None:
            index_by_sn[sn] = idx

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
        sn = battery_pack_serial(raw_update)
        target_idx = index_by_sn.get(sn) if sn is not None else None
        if target_idx is None and sn is None and update_idx < len(merged):
            target_idx = update_idx
        if target_idx is None:
            continue
        for key in ota_keys:
            if key in raw_update and raw_update.get(key) is not None:
                merged[target_idx][key] = raw_update.get(key)
    return merged[:5]


# ---------------------------------------------------------------------------
# Misc pure helpers
# ---------------------------------------------------------------------------


def app_period_section(prefix: str, date_type: str) -> str:
    """Build the normalized ``<prefix>_<date_type>`` app-period key."""
    return f"{prefix}_{date_type}"


def shelly_cloud_api_device_id(item: dict[str, Any]) -> str | None:
    """Determine the native Shelly Cloud device identifier for a device payload.

    Returns the Shelly-native device id used by Shelly Cloud realtime/control APIs when
    the provided item represents a Shelly Cloud-related payload (e.g., identified as
    Shelly, marked as cloud, or containing host/device code). Selection preference:
    - a non-empty, non-numeric deviceId when present,
    - otherwise a subdevice serial-based id if available,
    - otherwise a generic subdevice id.

    Returns:
        str: Device id suitable for Shelly Cloud APIs, or `None` if the item does not
        indicate a Shelly Cloud payload.
    """
    scan_name = str(item.get(FIELD_SCAN_NAME) or "").lower()
    is_shelly = scan_name.startswith("shelly")
    if not (
        is_shelly
        or str(item.get(FIELD_IS_CLOUD)).lower() in {"1", "true"}
        or item.get(FIELD_HOST) is not None
        or item.get(FIELD_DEVICE_CODE) is not None
    ):
        return None

    direct_id = item.get("deviceId")
    if is_shelly:
        # System-list accessories use a numeric Jackery accessory id in
        # deviceId, while Shelly Cloud realtime/control expects the native
        # Shelly device id (`5c...`). The app-linked boundDevices payload
        # exposes that id either as deviceId or, in system-list, deviceSn.
        if direct_id not in {None, ""} and not str(direct_id).isdecimal():
            return str(direct_id)
        serial = subdevice_serial(item)
        if serial:
            return serial

    return subdevice_id(item)


def normalize_shelly_cloud_payload(
    source: Mapping[str, Any],
) -> dict[str, Any]:
    """Normalize and flatten a Shelly Cloud DeviceItem or RealData payload into.

    subdevice fields.

    Creates a shallow copy of all keys from `source` whose values are not None, merges
    any dictionary found under FIELD_POWER_BODY into the top-level result, and sets
    several fallback fields when their canonical counterparts are present:
    - copies FIELD_SWITCH into FIELD_SWITCH_STATE and FIELD_SYS_SWITCH if those keys
    are missing,
    - copies FIELD_OP -> FIELD_OUT_PW, FIELD_IP -> FIELD_IN_PW, and FIELD_ONLINE ->
    FIELD_ONLINE_STATUS when missing.
    If FIELD_SCAN_NAME is present and (lowercased) matches an entry in
    SUBDEVICE_SCAN_NAME_DEV_TYPES, rewrites FIELD_SCAN_NAME to the lowercased value and
    sets FIELD_DEV_TYPE from the mapping when missing.

    Parameters:
        source (Mapping[str, Any]): Original Shelly Cloud payload (DeviceItem or
        RealData).

    Returns:
        dict[str, Any]: A normalized, flattened dictionary suitable for subdevice
        merging.
    """
    normalized = {key: value for key, value in source.items() if value is not None}
    power_body = normalized.get(FIELD_POWER_BODY)
    if isinstance(power_body, dict):
        normalized = merge_dict_values(normalized, power_body)
    if FIELD_SWITCH in normalized:
        switch_state = normalized[FIELD_SWITCH]
        normalized.setdefault(FIELD_SWITCH_STATE, switch_state)
        normalized.setdefault(FIELD_SYS_SWITCH, switch_state)
    if FIELD_OP in normalized:
        normalized.setdefault(FIELD_OUT_PW, normalized[FIELD_OP])
    # ``ip`` is overloaded by Jackery's portable PowerBody (input power) and
    # Shelly Cloud device metadata (IPv4/IPv6 address).  Never copy an address
    # string into ``inPw`` — that produced states such as
    # ``inPw: "192.168.2.76"`` in the CT payload.
    if FIELD_IP in normalized and safe_float(normalized[FIELD_IP]) is not None:
        normalized.setdefault(FIELD_IN_PW, normalized[FIELD_IP])
    if FIELD_ONLINE in normalized:
        normalized.setdefault(FIELD_ONLINE_STATUS, normalized[FIELD_ONLINE])
    scan_name = str(normalized.get(FIELD_SCAN_NAME) or "").lower()
    if scan_name and scan_name in SUBDEVICE_SCAN_NAME_DEV_TYPES:
        normalized[FIELD_SCAN_NAME] = scan_name
        normalized.setdefault(
            FIELD_DEV_TYPE,
            SUBDEVICE_SCAN_NAME_DEV_TYPES[scan_name],
        )
    return normalized


def shelly_cloud_device_matches_entry(
    entry: dict[str, Any],
    shelly_device: Mapping[str, Any],
) -> bool:
    """Determine whether the given Shelly Cloud device belongs to the provided entry.

    Checks for any overlap between the set of subdevice identity values derived from
    `shelly_device` and the identity sets of the entry's subdevice candidates.

    Returns:
        `True` if any subdevice identity intersects, `False` otherwise.
    """
    shelly_ids = subdevice_identity_values(shelly_device)
    if not shelly_ids:
        return False
    return any(
        shelly_ids & subdevice_identity_values(candidate)
        for candidate in entry_subdevice_candidates(entry)
    )


def merge_shelly_cloud_item(  # ruff:ignore[too-many-return-statements, too-many-branches]
    entry: dict[str, Any],
    source: Mapping[str, Any],
    *,
    rejection_callback: Callable[[str], None] | None = None,
    fill_only: bool = False,
) -> bool:
    """Merge a normalized Shelly Cloud device/realtime payload into the appropriate.

    buckets of an entry.

    Normalizes the provided payload, marks it as a cloud-sourced item when relevant
    keys are present, and attempts to merge the normalized subdevice into one of the
    entry's buckets (CT meter, smart plugs, or meter heads). If a specific device type
    is determined, the payload is merged into the corresponding bucket; otherwise an
    identity-based fallback attempts to merge into any matching bucket. The function
    mutates `entry` in place.

    Parameters:
        entry (dict[str, Any]): The entry to update; updated in place when a merge
        occurs.
        source (Mapping[str, Any]): The raw Shelly Cloud device or realtime payload to
        normalize and merge.

    Returns:
        bool: `True` if `entry` was modified by the merge, `False` otherwise.
    """
    normalized = normalize_shelly_cloud_payload(source)
    if any(
        key in source
        for key in (
            FIELD_CONTROL_ALLOWED,
            FIELD_DEVICE_CODE,
            FIELD_HOST,
            FIELD_ICON,
            FIELD_ICON_PATH,
            FIELD_INTEGRATOR_ENABLED,
            FIELD_POWER_BODY,
        )
    ):
        normalized.setdefault(FIELD_IS_CLOUD, True)
    item_ids = subdevice_identity_values(normalized)
    dev_type = subdevice_dev_type(
        normalized,
        rejection_callback=rejection_callback,
    )
    if dev_type == SUBDEVICE_DEV_TYPE_CT:
        current = entry.get(PAYLOAD_CT_METER)
        current_dict = current if isinstance(current, dict) else {}
        merged_ct = (
            merge_missing_dict_values(current_dict, normalized)
            if fill_only
            else merge_present_dict_values(current_dict, normalized)
        )
        if merged_ct != current_dict:
            entry[PAYLOAD_CT_METER] = merged_ct
            return True
        return False
    if dev_type == SUBDEVICE_DEV_TYPE_SOCKET:
        current = entry.get(PAYLOAD_SMART_PLUGS)
        merged_plugs = merge_subdevice_list_by_identity(current, normalized)
        if merged_plugs != current:
            entry[PAYLOAD_SMART_PLUGS] = merged_plugs
            return True
        return False
    if dev_type == SUBDEVICE_DEV_TYPE_METER_HEAD:
        current = entry.get(PAYLOAD_METER_HEADS)
        merged_meter_heads = merge_subdevice_list_by_identity(current, normalized)
        if merged_meter_heads != current:
            entry[PAYLOAD_METER_HEADS] = merged_meter_heads
            return True
        return False

    if not item_ids:
        return False
    ct = entry.get(PAYLOAD_CT_METER)
    if isinstance(ct, dict) and item_ids & subdevice_identity_values(ct):
        entry[PAYLOAD_CT_METER] = (
            merge_missing_dict_values(ct, normalized)
            if fill_only
            else merge_present_dict_values(ct, normalized)
        )
        return True
    has_serial = bool(subdevice_serial(normalized))
    for bucket in (PAYLOAD_SMART_PLUGS, PAYLOAD_METER_HEADS):
        items = entry.get(bucket)
        if not isinstance(items, list):
            continue
        if any(
            isinstance(item, dict) and item_ids & subdevice_identity_values(item)
            for item in items
        ):
            if has_serial:
                merger = (
                    merge_smart_plug_lists
                    if bucket == PAYLOAD_SMART_PLUGS
                    else merge_subdevice_lists_by_sn
                )
                entry[bucket] = merger(items, [normalized])
            else:
                entry[bucket] = merge_subdevice_list_by_identity(items, normalized)
            return True
    return False


def shelly_cloud_device_ids(entry: dict[str, Any]) -> list[str]:
    """Collects known Shelly Cloud device identifiers associated with an entry.

    Parameters:
        entry (dict[str, Any]): Entry dictionary containing stored device/subdevice
        descriptors.

    Returns:
        list[str]: Deduplicated list of Shelly Cloud device IDs discovered for the
        given entry.
    """
    ids: list[str] = []
    for candidate in entry_subdevice_candidates(entry):
        dev_id = shelly_cloud_api_device_id(candidate)
        if dev_id and dev_id not in ids:
            ids.append(dev_id)
    return ids


_app_period_section_fn = app_period_section
_drop_stale_battery_packs_fn = drop_stale_battery_packs
_merge_battery_pack_lifetime_from_ble_fn = merge_battery_pack_lifetime_from_ble
_merge_battery_pack_lists_fn = merge_battery_pack_lists
_merge_battery_pack_ota_lists_fn = merge_battery_pack_ota_lists
_merge_circuits_fn = merge_circuits
_merge_pack_ota_fn = merge_pack_ota
_merge_smart_plug_lists_fn = merge_smart_plug_lists
_merge_sub_devices_fn = merge_sub_devices
_merge_subdevice_lists_by_sn_fn = merge_subdevice_lists_by_sn
_normalize_local_mqtt_payload_fn = normalize_local_mqtt_payload
_resolve_device_id_from_payload_fn = resolve_device_id_from_payload
_sanitize_main_properties_fn = sanitize_main_properties
_merge_shelly_cloud_item_fn = merge_shelly_cloud_item
_shelly_cloud_device_ids_fn = shelly_cloud_device_ids
_shelly_cloud_device_matches_entry_fn = shelly_cloud_device_matches_entry


_METRIC_SOURCE_FALLBACKS: dict[str, tuple[tuple[str, str], ...]] = {
    # Intentionally empty today.
    #
    # Home-energy period/day curves are only equivalent when sourced from
    # home_trends (totalHomeEgy + y-series). device_home_stat represents a
    # different metric family (grid-side in/out) and must not be substituted
    # for home-energy chart imports, otherwise Recorder gets false spikes.
}
_STATISTICS_HTTP_BACKFILL_WINDOW_DAYS = 120
_STATISTICS_HTTP_STARTUP_BACKFILL_MIN_DAYS = 120
_STATISTICS_HTTP_BACKFILL_REQUEST_BUDGET = 4
_STATISTICS_HTTP_PERIOD_BACKFILL_REQUEST_BUDGET = 3
_STATISTICS_HTTP_BACKFILL_INTERVAL_SEC = SLOW_METRICS_INTERVAL_SEC
_STATISTICS_HTTP_BACKFILL_RETRY_SEC = SLOW_METRICS_INTERVAL_SEC
_STATISTICS_HTTP_EMPTY_MAX_ATTEMPTS = 2
_STATISTICS_HTTP_TRANSPORT_ERROR_MAX_ATTEMPTS = 3
_STATISTICS_HTTP_TRANSIENT_RETRY_SEC = SLOW_METRICS_INTERVAL_SEC
_STATISTICS_HTTP_RETRY_AFTER_EPOCH = "retry_after_epoch"
_STATISTICS_IMPORT_THROTTLE_SEC = 300
_STATISTICS_IMPORT_STATE_TOLERANCE = 1e-4
_LOCAL_MQTT_CONFIG_RETRY_DELAYS_SEC = (15.0, 60.0)
_TRANSPORT_SOURCE_PRIORITY: Final[dict[TransportSource, int]] = {
    TransportSource.HTTP: 0,
    TransportSource.CLOUD_MQTT: 1,
    TransportSource.BLE: 2,
    TransportSource.LOCAL_MQTT: 3,
}
_ACCESSORY_IDENTITY_FIELDS: Final[frozenset[str]] = frozenset({
    FIELD_DEVICE_SN,
    FIELD_DEV_SN,
    FIELD_SN,
    FIELD_DEVICE_ID,
    FIELD_ID,
    FIELD_DEV_ID,
    FIELD_DEV_TYPE,
    FIELD_DEVICE_TYPE,
    FIELD_IDX,
})


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


def _serialize_mqtt_messages_by_device(
    handler: Callable[..., Awaitable[str | None]],
) -> Callable[..., Awaitable[str | None]]:
    """Serialize one device's callbacks without blocking other devices."""

    @wraps(handler)
    async def _wrapped(
        self: JackerySolarVaultCoordinator,
        topic: str,
        payload: dict[str, Any],
        **kwargs: Any,  # ruff:ignore[any-type]
    ) -> str | None:
        device_id = self._resolve_device_id_from_mqtt(payload) if self.data else None
        lock_key = device_id or "__unresolved__"
        locks = getattr(self, "_mqtt_message_locks", None)
        if locks is None:
            locks = self._mqtt_message_locks = {}
        lock = locks.setdefault(lock_key, asyncio.Lock())
        async with lock:
            return await handler(self, topic, payload, **kwargs)

    return _wrapped


class JackerySolarVaultCoordinator(DataUpdateCoordinator[dict[str, dict[str, Any]]]):  # ruff:ignore[too-many-public-methods]
    """Polls all known Jackery devices."""

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

    def _merge_lifetime_counter_data(
        self,
        updated: dict[str, Any],
        source: object,
    ) -> bool:
        """Merge transport lifetime energy counters into their own bucket."""
        if not isinstance(source, dict):
            return False
        current = updated.get("lifetime_counters")
        current_dict = current if isinstance(current, dict) else {}
        merged = self._merge_dict_values(current_dict, source)
        if merged == current_dict:
            return False
        updated["lifetime_counters"] = merged
        return True

    _PRICE_OVERRIDE_TTL_SEC = 600
    _PROPERTY_OVERRIDE_TTL_SEC = 120

    _CT_METER_KEYS = CT_METER_KEYS
    _SUBDEVICE_HINT_KEYS = SUBDEVICE_HINT_KEYS
    _SUBDEVICE_ONLY_PROPERTY_KEYS = SUBDEVICE_ONLY_PROPERTY_KEYS
    _SUBDEVICE_MAIN_MIRROR_KEYS = SUBDEVICE_MAIN_MIRROR_KEYS
    _SUBDEVICE_DEV_TYPE_STRINGS = NON_BATTERY_SUBDEVICE_TYPES | {
        str(SUBDEVICE_DEV_TYPE_BATTERY_PACK),
    }
    _SYSTEM_INFO_KEYS = SYSTEM_INFO_KEYS
    _BATTERY_PACK_HINT_KEYS = BATTERY_PACK_HINT_KEYS
    _MAIN_PROPERTY_ALIAS_PAIRS = MAIN_PROPERTY_ALIAS_PAIRS
    _BATTERY_PACK_LIVE_KEYS = frozenset({FIELD_BAT_SOC, FIELD_CELL_TEMP})
    #: pv family tracked by the per-key stale marker (F7 2026-07-03):
    #: scalar PV power plus the nested pv1..pv4 MPPT objects.
    _PV_PROPERTY_KEYS = frozenset({
        FIELD_PV_PW,
        FIELD_PV1,
        FIELD_PV2,
        FIELD_PV3,
        FIELD_PV4,
    })
    _DEVICE_STATISTIC_LIVE_KEYS = frozenset({
        APP_DEVICE_STAT_PV_ENERGY,
        APP_DEVICE_STAT_BATTERY_CHARGE,
        APP_DEVICE_STAT_BATTERY_DISCHARGE,
        APP_DEVICE_STAT_ONGRID_INPUT,
        APP_DEVICE_STAT_ONGRID_OUTPUT,
        APP_DEVICE_STAT_BATTERY_TO_GRID,
        APP_DEVICE_STAT_PV_TO_BATTERY,
        APP_DEVICE_STAT_ONGRID_TO_BATTERY,
        APP_DEVICE_STAT_EPS_INPUT,
        APP_DEVICE_STAT_EPS_OUTPUT,
    })
    _MAIN_LIVE_PROPERTY_KEYS = frozenset({
        *LOCAL_DAILY_LIFETIME_METRICS,
        FIELD_SOC,
        FIELD_BAT_SOC,
        FIELD_CELL_TEMP,
        FIELD_CHARGE_PLAN_PW,
        FIELD_ENERGY_PLAN_PW,
        FIELD_PV_PW,
        FIELD_PV1,
        FIELD_PV2,
        FIELD_PV3,
        FIELD_PV4,
        FIELD_BAT_IN_PW,
        FIELD_BAT_OUT_PW,
        FIELD_STACK_IN_PW,
        FIELD_STACK_OUT_PW,
        FIELD_GRID_IN_PW,
        FIELD_GRID_OUT_PW,
        FIELD_IN_GRID_SIDE_PW,
        FIELD_OUT_GRID_SIDE_PW,
        FIELD_IN_ONGRID_PW,
        FIELD_OUT_ONGRID_PW,
        FIELD_OTHER_LOAD_PW,
        FIELD_SW_EPS_IN_PW,
        FIELD_SW_EPS_OUT_PW,
        FIELD_SW_EPS,
        FIELD_SW_EPS_STATE,
    })
    # devType -> coordinator live bucket holding that accessory's telemetry.
    # AGENTS.md HTTP-primary: the shadow fallback fills these buckets when the
    # supplemental Layer-5 push is absent, stale, or missing HTTP-only fields.
    _SHADOW_DEV_TYPE_BUCKETS: ClassVar[dict[int, str]] = {
        SUBDEVICE_DEV_TYPE_BATTERY_PACK: PAYLOAD_BATTERY_PACKS,
        SUBDEVICE_DEV_TYPE_COMBO: PAYLOAD_SUBDEVICES,
        SUBDEVICE_DEV_TYPE_CT: PAYLOAD_CT_METER,
        SUBDEVICE_DEV_TYPE_METER_HEAD: PAYLOAD_METER_HEADS,
        SUBDEVICE_DEV_TYPE_SOCKET: PAYLOAD_SMART_PLUGS,
    }
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
        APP_SECTION_CT_STAT: (
            APP_STAT_TOTAL_CT_INPUT_ENERGY,
            APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
        ),
        APP_SECTION_EPS_STAT: (
            APP_STAT_TOTAL_IN_EPS_ENERGY,
            APP_STAT_TOTAL_OUT_EPS_ENERGY,
        ),
    }
    _SYSTEM_YEAR_BACKFILL_STAT_KEYS: ClassVar[dict[str, tuple[str, ...]]] = {
        APP_SECTION_HOME_TRENDS: (APP_STAT_TOTAL_HOME_ENERGY,),
    }

    def __init__(  # ruff:ignore[too-many-statements]
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
            config_entry=entry,
            name=f"{DOMAIN} ({entry.title})",
            update_interval=update_interval,
        )
        self.api = api
        self.api.payload_debug_callback = self._schedule_payload_debug_event
        self.api.auth_rejection_callback = self.record_http_auth_rejection
        self.rejection_metrics = RejectionMetrics()
        self.entry = entry
        self._configured_update_interval = update_interval
        interval_sec = max(15, safe_int(update_interval.total_seconds()) or 15)
        # Fast property polling should follow the configured interval, but
        # server-side slow endpoints (stats/trends/price) should keep their
        # own cadence to avoid long update cycles.
        self._slow_metrics_interval_sec = max(SLOW_METRICS_INTERVAL_SEC, interval_sec)
        self._price_config_interval_sec = max(PRICE_CONFIG_INTERVAL_SEC, interval_sec)
        self._last_discovery_refresh_monotonic: float = float("-inf")
        self._pending_discovery_parent_removals: set[tuple[str, str]] = set()

        # Mapping deviceId -> {systemId, system_meta, device_meta}
        self._device_index: dict[str, dict[str, Any]] = {}

        # Slow-metric caches: per-systemId -> (last_fetch_monotonic, payload)
        # Entries stay valid for the configured polling interval.
        self._slow_cache: dict[str, dict[str, tuple[float, Any]]] = {}
        # Track the calendar day of the last refresh so we can invalidate
        # day-bounded metrics (statistic, pv_trends) at local midnight.
        self._cached_date: date | None = None
        self._mqtt: JackeryMqttPushClient | None = None
        # Cloud MQTT connection state: backoff, pause, auth, fingerprint.
        # Owned here so supplemental transport failures cannot pause HTTP.
        self._mqtt_mgr: MqttConnectionManager = MqttConnectionManager()
        self._last_weather_plan_query: dict[str, float] = {}
        self._weather_plan_query_interval_sec = 180
        self._last_system_info_query: dict[str, float] = {}
        self._system_info_query_interval_sec = 180
        self._last_subdevice_query: dict[str, float] = {}
        # HTTP property-shadow fallback (Layer 3) reuses the subdevice cadence:
        # it only fills accessory live buckets when MQTT (Layer 5) is absent or
        # stale, so it must not run more often than the user's polling interval.
        self._last_shadow_query: dict[str, float] = {}
        self._battery_pack_http_cache_seen: dict[str, float] = {}
        self._shelly_realtime_cache_seen: dict[tuple[str, str], float] = {}
        self._shelly_device_cache_seen = float("-inf")
        # App-side MQTT subdevices must follow the user's polling interval, not
        # the slow statistic cadence.
        self._subdevice_query_interval_sec = interval_sec
        self._price_overrides: dict[str, tuple[float, dict[str, Any]]] = {}
        self._property_overrides: dict[str, tuple[float, dict[str, Any]]] = {}
        self._statistics_import_task: asyncio.Task[None] | None = None
        self._statistics_import_ready = False
        self._battery_pack_ota_tasks: dict[str, asyncio.Task[None]] = {}
        # Experimental BLE transport (Phase 3a — gated by
        # CONF_ENABLE_BLE_TRANSPORT). Typed as ``Any`` so the coordinator
        # module imports cleanly on hosts without BlueZ / bleak.
        self._ble_listener: Any = None
        # Coalesce rapid BLE bursts into one coordinator update per device.
        self._ble_pending_updates: dict[str, dict[str, Any]] = {}
        self._ble_coalesce_tasks: dict[str, asyncio.Task[None]] = {}
        # Layer 3 HTTP is never paused by Layer 5 transports. These timestamps
        # are diagnostics only; they must not suppress the fast property fetch.
        self._last_http_refresh_completed_monotonic: float = float("-inf")
        self._last_http_device_refresh_monotonic: dict[str, float] = {}
        self._last_http_cycle_started_monotonic: float = float("-inf")
        self._last_http_cycle_completed_monotonic: float = float("-inf")
        self._last_poll_watchdog_request_monotonic: float = float("-inf")
        # Last time a push transport delivered fields equivalent to
        # /v1/device/property. Generic MQTT traffic (CT frames, config echoes,
        # HA recorder events on local MQTT) is tracked for diagnostics only.
        self._last_property_push_monotonic: float = float("-inf")
        # Per-field freshness prevents an older HTTP/Shelly cache snapshot from
        # reversing a newer MQTT/BLE value while also letting genuinely stale
        # push data expire.  Message locks preserve callback arrival order per
        # device without serializing unrelated devices or dropping frames.
        self._live_property_received_monotonic: dict[str, dict[str, float]] = {}
        self._live_ct_received_monotonic: dict[str, float] = {}
        # Per-field provenance prevents a later lower-priority snapshot from
        # reversing fresh local data. State stays internal: payloads remain the
        # protocol's complete dictionaries, with no HA bookkeeping injected.
        self._property_source_state: dict[
            str,
            dict[str, tuple[TransportSource, float]],
        ] = {}
        self._accessory_source_state: dict[
            tuple[str, str, str],
            dict[str, tuple[TransportSource, float]],
        ] = {}
        self._mqtt_message_locks: dict[str, asyncio.Lock] = {}
        # MqttMsgActivity generates a 9-digit fallback token once for the
        # form. Keep the integration-generated equivalent stable for this
        # config-entry session instead of rotating it on every write.
        self._generated_third_party_mqtt_token: str | None = None
        # Dedup cache for payload-debug records:
        # (kind, channel, message_type) -> (last_signature, last_emit_monotonic).
        # Bounded by the number of distinct topics the device publishes
        # (typically <10), so memory stays trivial.
        self._payload_debug_last_sig: dict[tuple[str, str, str], str] = {}
        self._payload_debug_last_emit_ts: dict[tuple[str, str, str], float] = {}
        # Queue (not a single slot) so a burst of HTTP payload-debug events is
        # never lost to overwrite before the background drain runs.
        self._payload_debug_pending_events: deque[
            dict[str, Any] | Callable[[], dict[str, Any]]
        ] = deque()
        # Counter for diagnostics: how many battery packs the cleanup has
        # removed for being silent past BATTERY_PACK_STALE_THRESHOLD_SEC.
        # Resets on integration reload.
        self._stale_battery_packs_dropped = 0
        # Identifiers for devices to remove from HA's device_registry on
        # the next async cleanup hook. Populated synchronously by
        # _merge_subdevice_data, drained asynchronously by
        # _async_cleanup_pending_device_removals.
        self._pending_device_removals: list[tuple[str, str]] = []
        self._pending_battery_pack_removal_serials: dict[tuple[str, str], str] = {}
        # Setup-local registry migration can freeze an index to either a trusted
        # serial or an explicit index fallback for this coordinator session.
        self._battery_pack_identity_overrides: dict[tuple[str, int], str | None] = {}
        self._battery_pack_topology_reload_required = False
        # Statistic-import cache: avoid re-publishing identical chart buckets
        # to HA recorder when the cloud snapshot did not change. Keyed by
        # statistic_id, value is the JSON signature of the last published
        # (starts, states) tuple.
        self._stat_import_last_sig: dict[str, str] = {}
        self._data_quality_issue_signature: str | None = None
        self._activation_issue_active: set[str] = set()
        # Throttle recorder-statistics import separately from HTTP polling so
        # the recorder is not invoked on every fast Layer 3 refresh. The first
        # import runs after platforms are set up so setup is not blocked by
        # historical week/month/year recovery.
        self._last_stat_import_monotonic: float = float("-inf")
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
            _STATISTICS_BACKFILL_STORE_DEVICES: {},
        }
        self._statistics_backfill_state_loaded = False
        # Endpoint+device+period scoped backoff for persistent cloud
        # parameter/bind failures (e.g. code=10422/10432). Keeps poll cycles
        # lean and avoids repeating known-failing calls every refresh.
        self._endpoint_backoff: dict[str, dict[str, Any]] = {}
        # Cloud MQTT connection setup/backoff — managed by _mqtt_mgr
        # --- restored attrs (24.05 offline/local features) ---
        self._discovery_source: str = "none"
        self._persisted_mqtt_session: MqttSessionSnapshot | None = (
            api.mqtt_session_snapshot()
        )
        self._mqtt_session_cache_loaded = False
        self._local_daily_snapshots: dict[str, dict[str, Any]] = {}
        self._persisted_local_daily_signature: str | None = None
        self._local_daily_cache_loaded = False
        self._mqtt_poll_task: asyncio.Task[None] | None = None
        self._shadow_fallback_task: asyncio.Task[None] | None = None
        self._local_mqtt_unsubs: list[Callable[[], None]] = []
        self._local_mqtt_message_tasks: set[asyncio.Task[None]] = set()
        self._background_tasks: dict[str, asyncio.Task[Any]] = {}
        self._active_http_update_tasks: set[asyncio.Task[Any]] = set()
        self._topology_reload_task: asyncio.Task[None] | None = None
        self._shutdown_started: bool = False
        self._base_shutdown_task: asyncio.Task[None] | None = None
        self._ble_connect_backoff: dict[str, BleConnectBackoff] = {}
        self._local_mqtt_backlog_dropped = 0
        self._local_mqtt_listener_messages_received: int = 0
        self._local_mqtt_listener_foreign_ignored: int = 0
        self._local_mqtt_last_message_monotonic: float = float("-inf")
        self._local_mqtt_last_device_message_monotonic: dict[str, float] = {}
        self._local_mqtt_config_retry_pending = False
        self._statistics_startup_sync_pending = True
        self._polling_diagnostics: dict[str, Any] = {
            "cache_hits": 0,
            "fetches": 0,
            "empty_fetches": 0,
            "failures": 0,
            "property_fetch_completed": False,
            "last_schedule_decision": "not_started",
            "overrun_active": False,
            "overrun_incident_count": 0,
            "last_cycle_elapsed_sec": 0.0,
            "current_overrun_sec": 0.0,
            "last_overrun_sec": 0.0,
            "max_overrun_sec": 0.0,
            "incident_max_overrun_sec": 0.0,
            "overrun_started_at": None,
            "last_overrun_at": None,
            "last_recovered_at": None,
            "last_recovery_duration_sec": None,
            "timeout_active": False,
            "timeout_incident_count": 0,
            "timeout_started_at": None,
            "last_timeout_at": None,
            "last_timeout_elapsed_sec": 0.0,
            "incident_max_timeout_elapsed_sec": 0.0,
            "last_timeout_recovered_at": None,
            "last_timeout_recovery_duration_sec": None,
        }
        self._polling_overrun_started_monotonic: float | None = None
        self._polling_timeout_started_monotonic: float | None = None
        self._statistics_import_diagnostics: dict[str, Any] = {
            "statistics_import_last_decision": "not_started",
            "last_current_entity_imported_rows": 0,
            "last_status": "not_started",
        }
        self._last_statistics_http_backfill_monotonic: float = float("-inf")
        # Cache for MQTT CombineData system-info fields so they survive
        # temporary MQTT disconnects.  HTTP /v1/device/property never
        # returns these keys (HomeBody vs SystemBody), so without this
        # cache the sensors would flip to Unknown every time MQTT drops.
        # Keyed by device_id, stores the last-known system-info subset of
        # PAYLOAD_PROPERTIES.
        self._system_info_cache: dict[str, dict[str, Any]] = {}
        # Background task for refreshing slow metric caches (pv_trends,
        # home_trends, battery_trends, statistic, price, alarm etc.)
        # without blocking the main coordinator update cycle.
        self._slow_metrics_bg_task: asyncio.Task[None] | None = None
        self._system_info_cache_monotonic: dict[str, float] = {}
        # Poll-cadence watchdog (P6): the scheduled interval tick and the
        # background-refresh chain both proved losable during a BLE
        # outage (152 s silent stall, 2026-07-03). This independent
        # time-tracked check forces a refresh when the cadence dies so
        # the cloud HTTP poll can never silently stop (AGENTS.md §1.2).
        self._poll_watchdog_unsub: Callable[[], None] | None = (
            async_track_time_interval(
                hass,
                self._async_poll_watchdog,
                timedelta(seconds=POLL_WATCHDOG_CHECK_INTERVAL_SEC),
            )
        )

    async def _async_poll_watchdog(self, _now: datetime) -> None:
        """Force a coordinator refresh when the poll cadence stalls silently."""
        if self._shutdown_started:
            return
        if self._active_http_update_tasks:
            # The per-cycle timeout owns in-flight stalls. Asking HA's debouncer
            # for another refresh here can queue a redundant post-cycle poll.
            return
        last_completed = self._last_http_cycle_completed_monotonic
        if last_completed == float("-inf"):
            # Startup: the first refresh has not completed yet; entry
            # setup / first-refresh error handling owns that phase.
            return
        age = time.monotonic() - last_completed
        threshold = max(
            POLL_WATCHDOG_STALL_FACTOR
            * self._configured_update_interval.total_seconds(),
            POLL_WATCHDOG_MIN_STALL_SEC,
        )
        if age <= threshold:
            return
        now_monotonic = time.monotonic()
        if now_monotonic - self._last_poll_watchdog_request_monotonic < threshold:
            return
        self._last_poll_watchdog_request_monotonic = now_monotonic
        _LOGGER.warning(
            "Jackery poll watchdog: no completed HTTP cycle for %.0fs "
            "(threshold %.0fs) — the scheduled cadence stalled; forcing a "
            "coordinator refresh now",
            age,
            threshold,
        )
        await self.async_request_refresh()

    def _schedule_payload_debug_event(
        self,
        event_or_factory: dict[str, Any] | Callable[[], dict[str, Any]],
    ) -> None:
        """Coalesce payload-file writes outside the primary HTTP request path."""
        if self._shutdown_started:
            return
        if not _payload_debug_capture_enabled():
            return
        self._payload_debug_pending_events.append(event_or_factory)
        self._schedule_background_once(
            "payload_debug_write",
            self._async_drain_payload_debug_events,
            name=f"{DOMAIN}_payload_debug_write",
        )

    async def _async_drain_payload_debug_events(self) -> None:
        """Write the newest queued payload event without delaying API callers."""
        while self._payload_debug_pending_events:
            event_or_factory = self._payload_debug_pending_events.popleft()
            await self._async_payload_debug_event(event_or_factory)

    async def _async_payload_debug_event(
        self,
        event_or_factory: dict[str, Any] | Callable[[], dict[str, Any]],
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
        sites avoid building the event when DEBUG is disabled -- the most
        important hot-path optimization on the per-MQTT-message path.
        """
        # Gate the JSONL file on the dedicated payload-debug DEBUG logger only
        # (see _payload_debug_capture_enabled). MQTT/BLE call this writer
        # directly (bypassing _schedule_payload_debug_event), so the gate must
        # live here too. Redaction of the written line is a separate concern
        # (diagnostic_redactions_disabled below).
        if not _payload_debug_capture_enabled():
            return
        if isinstance(event_or_factory, dict):
            event = dict(event_or_factory)
        else:
            event = event_or_factory()
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
        signature = stable_payload_debug_signature(event)
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
        # Bound the dedup cache to prevent unbounded memory growth. With
        # ~10 topics per device the cap is never hit in normal operation;
        # it only guards against pathological churn.
        if len(self._payload_debug_last_sig) >= 256:  # ruff:ignore[magic-value-comparison]
            oldest = next(iter(self._payload_debug_last_sig))
            self._payload_debug_last_sig.pop(oldest, None)
            self._payload_debug_last_emit_ts.pop(oldest, None)
        self._payload_debug_last_sig[cache_key] = signature
        self._payload_debug_last_emit_ts[cache_key] = now_mono
        event.setdefault("timestamp", dt_util.now().isoformat())
        event.setdefault("entry_id", self.entry.entry_id)
        path = self.hass.config.path(PAYLOAD_DEBUG_LOG_FILENAME)
        await self.hass.async_add_executor_job(
            append_payload_debug_line,
            path,
            event,
            diagnostic_redactions_disabled(self.entry),
        )

    def _reset_discovery_removal_confirmations(
        self,
        source: str | None = None,
    ) -> None:
        """Clear stale consecutive-missing confirmations after an incomplete cycle."""
        if source is None:
            self._pending_discovery_parent_removals.clear()
            return
        self._pending_discovery_parent_removals = {
            confirmation
            for confirmation in self._pending_discovery_parent_removals
            if confirmation[0] != source
        }

    @staticmethod
    def _discovery_source_for_record(record: dict[str, Any]) -> str:
        """Return the authoritative source that owns one cached parent record."""
        device_meta = record.get(PAYLOAD_DEVICE_META)
        if isinstance(device_meta, dict):
            source = device_meta.get(PAYLOAD_DISCOVERY_SOURCE)
            if source in {
                DISCOVERY_SOURCE_SYSTEM_LIST,
                DISCOVERY_SOURCE_LEGACY_BIND_LIST,
            }:
                return str(source)
        if record.get(FIELD_SYSTEM_ID):
            return DISCOVERY_SOURCE_SYSTEM_LIST
        return DISCOVERY_SOURCE_LEGACY_BIND_LIST

    def _reconcile_discovery_parent_removals(
        self,
        new_index: dict[str, dict[str, Any]],
        *,
        system_source_valid: bool,
        legacy_source_valid: bool,
    ) -> tuple[dict[str, dict[str, Any]], set[str], set[str]]:
        """Require two consecutive complete-source omissions before parent removal."""
        reconciled = dict(new_index)
        next_confirmations: set[tuple[str, str]] = set()
        first_omissions: set[str] = set()
        confirmed_removals: set[str] = set()

        for device_id, previous in self._device_index.items():
            if device_id in new_index:
                continue
            source = self._discovery_source_for_record(previous)
            if source == DISCOVERY_SOURCE_SYSTEM_LIST and not system_source_valid:
                reconciled[device_id] = copy.deepcopy(previous)
                continue
            if source == DISCOVERY_SOURCE_LEGACY_BIND_LIST and not legacy_source_valid:
                reconciled[device_id] = copy.deepcopy(previous)
                continue
            confirmation = (source, device_id)
            if confirmation in self._pending_discovery_parent_removals:
                confirmed_removals.add(device_id)
                continue
            reconciled[device_id] = copy.deepcopy(previous)
            next_confirmations.add(confirmation)
            first_omissions.add(device_id)

        self._pending_discovery_parent_removals = next_confirmations
        return reconciled, first_omissions, confirmed_removals

    async def async_discover(self) -> bool:  # ruff:ignore[complex-structure, too-many-branches, too-many-locals, too-many-statements]
        """Populate _device_index from config or /v1/device/system/list."""
        new_index: dict[str, dict[str, Any]] = {}
        discovered_at = datetime.now(UTC).isoformat()

        # Primary: confirmed system/list endpoint (SolarVault + friends)
        try:
            systems = await self.api.async_get_system_list()
        except JackeryAuthError as err:
            self._reset_discovery_removal_confirmations()
            msg = (
                "Jackery credentials were rejected during system discovery. "
                "Re-authentication is required."
            )
            raise ConfigEntryAuthFailed(
                msg,
            ) from err
        except JackeryError as err:
            self._reset_discovery_removal_confirmations()
            msg = f"system/list failed: {err}"
            raise UpdateFailed(msg) from err
        system_response = self.api.last_system_list_response
        system_source_valid = valid_system_discovery_response(system_response)
        system_entries = (
            system_response.get(FIELD_DATA)
            if isinstance(system_response, Mapping)
            else None
        )
        system_source_explicitly_empty = (
            isinstance(system_entries, list) and not system_entries and not systems
        )
        if not system_source_valid and not system_source_explicitly_empty:
            self._reset_discovery_removal_confirmations()
            msg = (
                "system/list returned a missing or malformed system/device-list payload"
            )
            raise UpdateFailed(msg)
        if system_source_explicitly_empty:
            self._reset_discovery_removal_confirmations(
                DISCOVERY_SOURCE_SYSTEM_LIST,
            )
        for sys_entry in systems:
            devices = sys_entry[FIELD_DEVICES]
            property_devices = [
                device
                for device in devices
                if self._is_property_device_candidate(device)
            ]
            if devices and (
                not property_devices
                or any(
                    not valid_system_parent_identity(device)
                    for device in property_devices
                )
            ):
                self._reset_discovery_removal_confirmations()
                msg = "system/list returned devices that cannot be indexed safely"
                raise UpdateFailed(msg)

        for sys_entry in systems:
            sys_id = sys_entry.get(FIELD_ID) or sys_entry.get(FIELD_SYSTEM_ID)
            devices = sys_entry[FIELD_DEVICES]
            accessories = [
                {**dict(dev), SUBDEVICE_FIELD_LAST_SEEN_AT: discovered_at}
                for dev in devices
                if isinstance(dev, dict) and not self._is_property_device_candidate(dev)
            ]
            system_meta = {k: v for k, v in sys_entry.items() if k != FIELD_DEVICES}
            # Preserve an explicit empty list: later stale cleanup may treat
            # absence as authoritative only when system/list actually supplied
            # a complete accessories membership list.
            system_meta[FIELD_ACCESSORIES] = accessories
            for dev in devices:
                if not isinstance(dev, dict):
                    continue
                if not self._is_property_device_candidate(dev):
                    continue
                dev_id = dev.get(FIELD_DEVICE_ID) or dev.get(FIELD_ID)
                if not dev_id:
                    continue
                device_meta = dict(dev)
                device_meta[PAYLOAD_DISCOVERY_SOURCE] = DISCOVERY_SOURCE_SYSTEM_LIST
                new_index[str(dev_id)] = {
                    FIELD_SYSTEM_ID: str(sys_id) if sys_id else None,
                    PAYLOAD_SYSTEM_META: system_meta,
                    PAYLOAD_DEVICE_META: device_meta,
                }

        # Legacy bind/list is supplementary for mixed accounts. Some accounts can
        # have Home systems from system/list and Explorer portables from bind/list.
        legacy_source_valid = False
        try:
            legacy = await self.api.async_list_devices_legacy()
        except JackeryAuthError as err:
            if new_index:
                self._reset_discovery_removal_confirmations(
                    DISCOVERY_SOURCE_LEGACY_BIND_LIST
                )
                _LOGGER.debug(
                    "Jackery: legacy portable discovery auth failed after "
                    "system/list succeeded; keeping system/list discovery",
                )
                legacy = []
            else:
                self._reset_discovery_removal_confirmations()
                msg = (
                    "Jackery credentials were rejected during legacy device discovery. "
                    "Re-authentication is required."
                )
                raise ConfigEntryAuthFailed(
                    msg,
                ) from err
        except JackeryError as err:
            if not new_index:
                self._reset_discovery_removal_confirmations()
                msg = f"legacy bind/list failed: {err}"
                raise UpdateFailed(msg) from err
            self._reset_discovery_removal_confirmations(
                DISCOVERY_SOURCE_LEGACY_BIND_LIST
            )
            _LOGGER.debug(
                "Jackery: legacy portable discovery failed after system/list "
                "succeeded; preserving prior legacy devices: %s",
                err,
            )
            legacy = []
        else:
            legacy_response = self.api.last_legacy_device_list_response
            legacy_source_valid = valid_discovery_list_response(legacy_response)
            legacy_entries = (
                legacy_response.get(FIELD_DATA)
                if isinstance(legacy_response, Mapping)
                else None
            )
            legacy_source_explicitly_empty = (
                isinstance(legacy_entries, list) and not legacy_entries and not legacy
            )
            if not legacy_source_valid:
                self._reset_discovery_removal_confirmations(
                    DISCOVERY_SOURCE_LEGACY_BIND_LIST
                )
                if legacy_source_explicitly_empty:
                    _LOGGER.debug(
                        "Jackery: legacy bind/list returned an explicit empty list; "
                        "not using it as parent-removal evidence",
                    )
                    legacy = []
                elif not new_index:
                    self._reset_discovery_removal_confirmations()
                    msg = (
                        "legacy bind/list returned a missing or malformed list payload"
                    )
                    raise UpdateFailed(msg)
                else:
                    _LOGGER.warning(
                        "Jackery: legacy bind/list returned a malformed payload; "
                        "preserving prior legacy devices",
                    )
                    legacy = []
        for dev in legacy:
            # Third-party accessories (e.g. Shelly) also appear in the legacy
            # bind/list with bindKey=0 and no Jackery model metadata. They must
            # not enter the Jackery /device/property loop — that endpoint keys
            # on a Jackery deviceId and rejects a Shelly native id with
            # code=10600. The system/list path already applies this same filter;
            # the legacy path historically omitted it, so a bound Shelly was
            # hammered every poll cycle. Shelly stays a Layer-5 enrichment via
            # the dedicated device/shelly/* + Shelly Cloud paths.
            if not self._is_property_device_candidate(dev):
                continue
            dev_id = (
                dev.get(FIELD_DEV_ID)
                or dev.get(FIELD_DEVICE_ID)
                or dev.get(FIELD_ID)
                or dev.get(FIELD_DEV_SN)
                or dev.get(FIELD_DEVICE_SN)
            )
            if not dev_id:
                continue
            device_id_key = str(dev_id)
            if device_id_key in new_index:
                continue
            device_meta = dict(dev)
            device_meta[PAYLOAD_DISCOVERY_SOURCE] = DISCOVERY_SOURCE_LEGACY_BIND_LIST
            new_index[device_id_key] = {
                FIELD_SYSTEM_ID: None,
                PAYLOAD_SYSTEM_META: {},
                PAYLOAD_DEVICE_META: device_meta,
            }

        fresh_device_ids = set(new_index)
        new_index, first_omissions, confirmed_removals = (
            self._reconcile_discovery_parent_removals(
                new_index,
                system_source_valid=system_source_valid,
                legacy_source_valid=legacy_source_valid,
            )
        )
        if first_omissions:
            _LOGGER.warning(
                "Jackery: authoritative discovery omitted %d parent device(s); "
                "retaining them until a second consecutive complete result",
                len(first_omissions),
            )
        if confirmed_removals:
            _LOGGER.warning(
                "Jackery: authoritative discovery confirmed removal of %d parent "
                "device(s) in two consecutive complete results",
                len(confirmed_removals),
            )
        self._device_index = new_index
        self._last_discovery_refresh_monotonic = time.monotonic()
        await self._async_save_discovery_cache()
        if self._device_index:
            self._schedule_background_once(
                "http_accessory_discovery",
                self._async_refresh_http_accessories,
                name=f"{DOMAIN}_http_accessory_discovery",
            )
        if fresh_device_ids:
            _LOGGER.info(
                "Jackery: discovered %d device(s) from system/list + legacy bind/list",
                len(fresh_device_ids),
            )
        elif not self._device_index:
            msg = (
                "Jackery: no devices found on either /v1/device/system/list "
                "or /v1/device/bind/list."
            )
            _LOGGER.error(msg)
        return True

    async def _async_enumerate_http_accessories(
        self,
        index: dict[str, dict[str, Any]],
    ) -> None:
        """Overlay HTTP-enumerated accessories onto each device's system metadata.

        This is the HTTP-primary discovery source for the ``accessories`` list read
        by the subdevice presence predicates, so subdevices are discovered even when
        MQTT never connects. It runs on the discovery cadence only and is therefore
        kept off the hot ``_async_update_data`` poll cycle.

        The call is fully best-effort: every cloud failure, including an
        authentication failure, is swallowed so it can never break discovery.
        Enumeration deliberately does not own reauthentication. ``async_discover``'s
        primary ``system/list`` block runs *before* this and already converts an
        auth failure to ``ConfigEntryAuthFailed`` (the only exception the setup and
        update paths handle), so a token that expires mid-discovery is caught by
        that primary path on the next cycle. ``JackeryAuthError`` is a subclass of
        ``JackeryError``, so the broad ``except JackeryError`` below absorbs it.

        Args:
            index: Freshly built device index mapping device id to its discovery
                record. Accessory entries are merged into each record's system
                metadata in place, keyed by ``deviceSn`` for idempotency.
        """
        if not index:
            return
        if not self._endpoint_backoff_active(
            _ACCESSORIES_SYNC_BACKOFF_KEY,
            time.monotonic(),
        ):
            try:
                await self.api.async_sync_smart_accessories()
            except JackeryError as err:
                # A persistent code=10600 opens/extends the backoff window so the
                # sync stops re-firing every discovery cycle; transient failures
                # do not match the backoff codes and keep retrying normally.
                self._endpoint_backoff_note_failure(
                    _ACCESSORIES_SYNC_BACKOFF_KEY,
                    err,
                )
                _LOGGER.debug("Jackery: accessory sync failed (best-effort): %s", err)
            else:
                self._endpoint_backoff_note_success(_ACCESSORIES_SYNC_BACKOFF_KEY)
        for dev_id, record in index.items():
            try:
                accessories = await self.api.async_get_accessories_list(dev_id)
            except JackeryError as err:
                _LOGGER.debug(
                    "Jackery: accessory enumeration failed for %s (best-effort): %s",
                    dev_id,
                    err,
                )
                continue
            self._overlay_http_accessories(record, accessories)

    async def _async_refresh_http_accessories(self) -> None:
        """Refresh HTTP accessory discovery outside the live-property hot path."""
        if self._shutdown_started or not self._device_index:
            return
        before = copy.deepcopy(self._device_index)
        refreshed = copy.deepcopy(before)
        await self._async_enumerate_http_accessories(refreshed)
        if operator.truth(self._shutdown_started):
            return

        next_index = dict(self._device_index)
        changed = False
        for device_id, before_record in before.items():
            refreshed_record = refreshed.get(device_id)
            current_record = self._device_index.get(device_id)
            if not isinstance(refreshed_record, dict) or not isinstance(
                current_record, dict
            ):
                continue
            before_system = before_record.get(PAYLOAD_SYSTEM_META) or {}
            refreshed_system = refreshed_record.get(PAYLOAD_SYSTEM_META) or {}
            if not isinstance(before_system, dict) or not isinstance(
                refreshed_system, dict
            ):
                continue
            before_accessories = before_system.get(FIELD_ACCESSORIES)
            refreshed_accessories = refreshed_system.get(FIELD_ACCESSORIES)
            if refreshed_accessories == before_accessories:
                continue

            current_system = dict(current_record.get(PAYLOAD_SYSTEM_META) or {})
            current_system[FIELD_ACCESSORIES] = copy.deepcopy(refreshed_accessories)
            next_record = dict(current_record)
            next_record[PAYLOAD_SYSTEM_META] = current_system
            next_index[device_id] = next_record
            changed = True

        if not changed:
            return
        self._device_index = next_index
        await self._async_save_discovery_cache()
        _LOGGER.debug("Jackery: refreshed HTTP accessory discovery in background")

    @staticmethod
    def _overlay_http_accessories(
        record: dict[str, Any],
        accessories: list[dict[str, Any]],
    ) -> None:
        """Merge HTTP accessory entries into a device record's system metadata.

        Entries are merged by ``deviceSn`` so an accessory already present from the
        ``system/list`` device array and the same accessory returned by
        ``accessories/list`` collapse to a single dict (idempotent — a duplicate
        serial is never appended). Entries without a serial are appended as-is.

        Only non-``None`` values from the HTTP item overwrite an existing entry, so
        a null field in the ``accessories/list`` payload (e.g. ``devType=None``)
        cannot blank a value ``system/list`` had populated. Blanking ``devType``
        would silently defeat the presence predicates, which compare
        ``str(devType) == "<n>"`` and would see ``"None"``.

        Args:
            record: Discovery record whose system metadata is updated in place.
            accessories: Accessory dicts from ``async_get_accessories_list``.
        """
        discovered_at = datetime.now(UTC).isoformat()
        valid = [
            {**item, SUBDEVICE_FIELD_LAST_SEEN_AT: discovered_at}
            for item in accessories
            if isinstance(item, dict)
        ]
        if not valid:
            return
        system_meta = record[PAYLOAD_SYSTEM_META]
        existing = system_meta.get(FIELD_ACCESSORIES)
        merged = list(existing) if isinstance(existing, list) else []
        for item in valid:
            merged = merge_subdevice_list_by_identity(merged, item)
        system_meta[FIELD_ACCESSORIES] = merged

    @callback
    def async_schedule_discovery_refresh(self) -> None:
        """Queue immediate HTTP discovery after a successful remote binding."""
        if self._shutdown_started:
            return
        self._last_discovery_refresh_monotonic = (
            time.monotonic() - self._slow_metrics_interval_sec
        )
        self.entry.async_create_background_task(
            self.hass,
            self.async_request_refresh(),
            name=f"{DOMAIN}_discovery_refresh_{self.entry.entry_id}",
            eager_start=False,
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
        self._last_discovery_refresh_monotonic = now
        try:
            discovery_applied = await self.async_discover()
        except ConfigEntryAuthFailed:
            raise
        except JackeryAuthError as err:
            _raise_config_entry_auth_failed(
                "Jackery credentials were rejected during device rediscovery",
                err,
            )
        except JackeryError as err:
            _LOGGER.debug("Jackery runtime discovery refresh failed: %s", err)
            return
        except UpdateFailed as err:
            _LOGGER.debug("Jackery runtime discovery refresh failed: %s", err)
            return
        if not discovery_applied:
            return
        current_device_ids = set(self._device_index)
        removed_device_ids = old_device_ids - current_device_ids
        if removed_device_ids:
            removed_registry_devices = self._unlink_removed_parent_devices(
                removed_device_ids
            )
            _LOGGER.info(
                "Jackery: runtime discovery removed %d parent device(s) and "
                "unlinked %d registry device(s): %s",
                len(removed_device_ids),
                removed_registry_devices,
                ", ".join(sorted(removed_device_ids)),
            )
        new_device_ids = current_device_ids - old_device_ids
        if new_device_ids:
            _LOGGER.info(
                "Jackery: runtime discovery added %d device(s): %s",
                len(new_device_ids),
                ", ".join(sorted(new_device_ids)),
            )
            if self._mqtt is not None and self._mqtt.is_connected:
                self.async_schedule_local_mqtt_device_config()

    def _unlink_removed_parent_devices(self, device_ids: set[str]) -> int:
        """Unlink removed parents and their descendants from this config entry."""
        registry = dr.async_get(self.hass)
        unlinked = 0
        for device_id in sorted(device_ids):
            parent = registry.async_get_device(identifiers={(DOMAIN, device_id)})
            if parent is not None:
                linked_device_ids = {parent.id}
                changed = True
                while changed:
                    changed = False
                    for device in registry.devices.values():
                        if (
                            device.id not in linked_device_ids
                            and device.via_device_id in linked_device_ids
                            and self.entry.entry_id in device.config_entries
                        ):
                            linked_device_ids.add(device.id)
                            changed = True
                self._remove_registry_entities_for_devices(linked_device_ids)
                ordered_device_ids = [
                    *sorted(linked_device_ids - {parent.id}),
                    parent.id,
                ]
                for registry_device_id in ordered_device_ids:
                    registry_device = registry.async_get(registry_device_id)
                    if (
                        registry_device is None
                        or self.entry.entry_id not in registry_device.config_entries
                    ):
                        continue
                    registry.async_update_device(
                        device_id=registry_device_id,
                        remove_config_entry_id=self.entry.entry_id,
                    )
                    unlinked += 1
            for cache_key in tuple(self._slow_cache):
                if cache_key == f"dev:{device_id}" or cache_key.startswith(
                    f"dev:{device_id}:"
                ):
                    self._slow_cache.pop(cache_key, None)
            self._last_system_info_query.pop(device_id, None)
            self._last_weather_plan_query.pop(device_id, None)
            self._last_subdevice_query.pop(device_id, None)
            self._last_shadow_query.pop(device_id, None)
            self._system_info_cache.pop(device_id, None)
            self._system_info_cache_monotonic.pop(device_id, None)
            self._battery_pack_http_cache_seen.pop(device_id, None)
            for seen_key in tuple(self._shelly_realtime_cache_seen):
                if seen_key[0] == device_id:
                    self._shelly_realtime_cache_seen.pop(seen_key, None)
        return unlinked

    def _remove_registry_entities_for_devices(self, device_ids: set[str]) -> int:
        """Remove this config entry's entities attached to removed devices."""
        registry = er.async_get(self.hass)
        removed = 0
        for entity in tuple(
            er.async_entries_for_config_entry(registry, self.entry.entry_id)
        ):
            if entity.device_id not in device_ids:
                continue
            registry.async_remove(entity.entity_id)
            removed += 1
        return removed

    @staticmethod
    def _is_property_device_candidate(dev: dict[str, Any]) -> bool:
        """Filter out accessory entries that do not support /device/property."""
        # Observed for third-party accessories (e.g., Shelly): bindKey=0 and
        # no Jackery model metadata. Those IDs return API code=20000.
        bind_key = dev.get(FIELD_BIND_KEY)
        if safe_bool(bind_key) is False:
            return False
        if safe_int(dev.get(FIELD_DEV_TYPE)) == 3 and safe_bool(  # ruff:ignore[magic-value-comparison]
            dev.get(FIELD_IS_CLOUD)
        ):
            return False
        return not (not dev.get(FIELD_MODEL_CODE) and not dev.get(FIELD_DEV_MODEL))

    # ------------------------------------------------------------------
    # MQTT state management — delegated to MqttConnectionManager
    # ------------------------------------------------------------------

    @staticmethod
    def _is_mqtt_auth_failure(message: object) -> bool:
        """Return True for broker-side MQTT credential rejection."""
        return is_mqtt_auth_failure(message)

    def _mqtt_connect_backoff_remaining(self) -> int:
        """Return remaining Cloud-MQTT connect backoff seconds."""
        return self._mqtt_mgr.backoff_remaining()

    def _pause_mqtt_after_auth_failure(
        self,
        message: object,
        *,
        streak: int | None = None,
    ) -> None:
        """Pause MQTT after a broker auth rejection while HTTP keeps polling."""
        self.rejection_metrics.increment("mqtt_broker_rejections", str(message))
        self._mqtt_mgr.pause_after_auth_failure(message, streak=streak)

    def record_http_auth_rejection(self, status: int, data: object) -> None:
        """Record HTTP/API authentication rejection metrics."""
        reason = f"http_{status}"
        if self.api._is_token_expired_response(status, data):  # ruff:ignore[private-member-access]
            self.rejection_metrics.increment("auth_token_expiry_rejections", reason)
            return
        self.rejection_metrics.increment("http_auth_rejections", reason)

    def record_schema_rejection(self, reason: str) -> None:
        """Record a schema/data-quality rejection."""
        self.rejection_metrics.increment("schema_rejections", reason)

    def _defer_background_auth_failure(self, err: ConfigEntryAuthFailed) -> None:
        """Route background auth failures through the next coordinator refresh."""
        self._mqtt_mgr.defer_background_auth_failure(self._mqtt, str(err))

    def _bump_polling_diag(self, key: str) -> None:
        """Increment a numeric HTTP polling diagnostic counter safely."""
        values = self._polling_diagnostics
        current = safe_int(values.get(key)) or 0
        values[key] = current + 1

    def _note_polling_timeout(self, started: float) -> None:
        """Record one bounded HTTP polling-timeout incident."""
        completed = time.monotonic()
        elapsed = max(0.0, completed - started)
        interval_sec = self._configured_update_interval.total_seconds()
        overrun_sec = max(0.0, elapsed - interval_sec)
        now_iso = dt_util.utcnow().isoformat()
        diagnostics = self._polling_diagnostics
        diagnostics["last_status"] = "timeout"
        diagnostics["last_cycle_elapsed_sec"] = round(elapsed, 3)
        diagnostics["current_overrun_sec"] = round(overrun_sec, 3)
        diagnostics["last_timeout_at"] = now_iso
        diagnostics["last_timeout_elapsed_sec"] = round(elapsed, 3)
        diagnostics["incident_max_timeout_elapsed_sec"] = round(
            max(
                float(diagnostics.get("incident_max_timeout_elapsed_sec", 0.0)),
                elapsed,
            ),
            3,
        )
        if overrun_sec > 0.0:
            diagnostics["last_overrun_sec"] = round(overrun_sec, 3)
            diagnostics["last_overrun_at"] = now_iso
            diagnostics["max_overrun_sec"] = round(
                max(float(diagnostics.get("max_overrun_sec", 0.0)), overrun_sec),
                3,
            )
        if bool(diagnostics.get("timeout_active")):
            return
        diagnostics["timeout_active"] = True
        diagnostics["timeout_incident_count"] = (
            safe_int(diagnostics.get("timeout_incident_count")) or 0
        ) + 1
        diagnostics["timeout_started_at"] = now_iso
        diagnostics["incident_max_timeout_elapsed_sec"] = round(elapsed, 3)
        self._polling_timeout_started_monotonic = started
        _LOGGER.warning(
            "Jackery HTTP polling timed out after %.2fs; HTTP will retry on the "
            "next coordinator cycle",
            elapsed,
        )

    def _recover_polling_timeout(self) -> None:
        """Close an active HTTP polling-timeout incident after a clean cycle."""
        diagnostics = self._polling_diagnostics
        if not bool(diagnostics.get("timeout_active")):
            return
        completed = time.monotonic()
        started = self._polling_timeout_started_monotonic
        duration = max(0.0, completed - started) if started is not None else 0.0
        diagnostics["timeout_active"] = False
        diagnostics["last_timeout_recovered_at"] = dt_util.utcnow().isoformat()
        diagnostics["last_timeout_recovery_duration_sec"] = round(duration, 3)
        _LOGGER.info(
            "Jackery HTTP polling recovered after %.2fs; longest timed-out cycle "
            "was %.2fs",
            duration,
            float(diagnostics.get("incident_max_timeout_elapsed_sec", 0.0)),
        )
        self._polling_timeout_started_monotonic = None

    async def async_start_mqtt(self) -> None:
        """Start (or reconfigure) MQTT push channel."""
        if self._mqtt is None:
            try:
                mqtt_client_cls = await self.hass.async_add_executor_job(
                    _load_mqtt_push_client,
                )
            except ModuleNotFoundError as err:
                if err.name != "aiomqtt":
                    raise
                _LOGGER.warning(
                    "Jackery MQTT push is unavailable because aiomqtt is not installed",
                )
                return

            # The deferred Layer-5 startup and a foreground command can reach
            # this point concurrently. Re-check after the executor await so
            # only one runtime is constructed and owned by the coordinator.
            if self._mqtt is None:
                self._mqtt = mqtt_client_cls(
                    self.hass,
                    self._async_handle_mqtt_message,
                    self._async_mqtt_connected,
                    disconnect_callback=self._async_handle_mqtt_disconnect,
                )
        try:
            # Layer 5 is supplemental. Initial MQTT startup must not wait for
            # broker CONNACK because that can stall the primary HTTP path.
            await self._async_ensure_mqtt(force=True, wait_connected=False)
        except ConfigEntryAuthFailed as err:
            # MQTT is a supplemental transport. A connect/startup failure must
            # not open reauth or stop the HTTP coordinator; the HTTP/API path is
            # the only auth authority.
            self._defer_background_auth_failure(err)
        except RuntimeError as err:
            _LOGGER.debug(
                "Jackery MQTT initial connect did not complete; "
                "HTTP polling remains active: %s",
                err,
            )
            return

    async def _async_mqtt_connected(self) -> None:
        """Request a full app-style MQTT snapshot after every broker connect."""
        if self._shutdown_started:
            return
        self._mqtt_mgr.record_connect_success(self._mqtt, self.api.mqtt_fingerprint)
        # cmd=113 is a cloud-MQTT-only command. Schedule its idempotent bridge
        # configuration immediately after every broker connect so a failed
        # startup attempt is retried on reconnect and never waits for the
        # independent local-MQTT/BLE startup tasks to finish.
        self.async_schedule_local_mqtt_device_config()
        try:
            await self._async_query_system_info_for_missing(
                force=True,
                ensure_mqtt=False,
            )
            await self._async_query_weather_plan_for_missing(
                force=True,
                ensure_mqtt=False,
            )
            await self._async_query_subdevices_for_missing(
                force=True,
                ensure_mqtt=False,
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
        if self._shutdown_started or self._mqtt is None:
            return
        last_error = self._mqtt.diagnostics.get("last_error")
        if last_error:
            self._mqtt_mgr.handle_connect_error(self._mqtt, last_error)
        stopping_states = {
            CoreState.stopping,
            CoreState.stopped,
        }
        final_write_state = getattr(CoreState, "final_write", None)
        if final_write_state is not None:
            stopping_states.add(final_write_state)
        if self.hass.state in stopping_states:
            return

        async def _reconnect_background() -> None:
            try:
                # Do NOT reset _last_mqtt_connect_attempt here — the throttle
                # prevents reconnect storms that crash ESP32 proxies.
                # Do NOT use wait_connected=True — the coordinator update
                # cycle already handles the normal MQTT health check.
                await self._async_ensure_mqtt(force=False, wait_connected=False)
            except ConfigEntryAuthFailed as err:
                self._defer_background_auth_failure(err)
            except JackeryAuthError as err:
                _LOGGER.debug(
                    "Jackery MQTT auto-reconnect hit a transport auth error; "
                    "HTTP polling remains authoritative: %s",
                    err,
                )
            except BACKGROUND_TASK_ERRORS as err:
                _LOGGER.debug(
                    "Jackery MQTT auto-reconnect after disconnect failed: %s",
                    err,
                )

        self._schedule_background_once(
            "mqtt_reconnect",
            _reconnect_background,
            name=f"{DOMAIN}_mqtt_reconnect",
        )

    @property
    def configured_update_interval(self) -> timedelta:
        """Return the integration's coordinator polling interval."""  # ruff:ignore[property-docstring-starts-with-verb]
        return self._configured_update_interval

    def _poll_cycle_timeout_seconds(self) -> float:
        """Return a cycle ceiling that preserves the configured poll cadence."""
        hard_timeout = COORDINATOR_UPDATE_TIMEOUT_SEC
        if not self.data:
            # Initial login/discovery has no previous state to preserve and may
            # legitimately need more than one regular poll interval.
            return hard_timeout
        cadence_timeout = max(
            _POLL_CADENCE_MIN_DELAY_SEC,
            self._configured_update_interval.total_seconds()
            - _POLL_CADENCE_SCHEDULER_MARGIN_SEC,
        )
        return min(hard_timeout, cadence_timeout)

    def _set_next_poll_delay(self, started: float, completed: float) -> None:
        """Use only the unused portion of the start-to-start polling budget."""
        elapsed = max(0.0, completed - started)
        delay = max(
            _POLL_CADENCE_MIN_DELAY_SEC,
            self._configured_update_interval.total_seconds()
            - elapsed
            - _POLL_CADENCE_SCHEDULER_MARGIN_SEC,
        )
        # homeassistant-stubs 2026.7.2 splits the property/setter pair in its
        # .pyi, so mypy misses the HA-public update_interval setter
        # (helpers/update_coordinator.py:246) and wrongly reports read-only.
        self.update_interval = timedelta(seconds=delay)  # type: ignore[misc]
        diagnostics = self._polling_diagnostics
        diagnostics["last_total_cycle_elapsed_sec"] = round(elapsed, 3)
        diagnostics["next_poll_delay_sec"] = round(delay, 3)

    def _schedule_background_once(
        self,
        key: str,
        factory: Callable[[], Coroutine[Any, Any, Any]],
        *,
        name: str,
    ) -> asyncio.Task[Any] | None:
        """Create one identity-safe coordinator task for a logical operation."""
        if self._shutdown_started:
            return None
        current = self._background_tasks.get(key)
        if current is not None and not current.done():
            return current
        task: asyncio.Task[Any] = self.hass.async_create_background_task(
            factory(),
            name=name,
            eager_start=False,
        )
        self._background_tasks[key] = task

        def _task_done(done: asyncio.Task[Any]) -> None:
            if self._background_tasks.get(key) is done:
                self._background_tasks.pop(key, None)
            try:
                done.result()
            except asyncio.CancelledError:
                return
            except ConfigEntryAuthFailed as err:
                self._defer_background_auth_failure(err)
            except Exception as err:  # ruff:ignore[blind-except]  # background boundary
                _LOGGER.debug("Jackery background task %s failed: %s", key, err)

        task.add_done_callback(_task_done)
        return task

    @callback
    def async_schedule_local_mqtt_device_config(self) -> asyncio.Task[Any] | None:
        """Schedule one config push and replay a connect that arrives mid-flight."""
        key = "local_mqtt_device_config"
        if self._shutdown_started:
            return None
        current = self._background_tasks.get(key)
        if current is not None and not current.done():
            # A new broker session supersedes the one the in-flight command
            # started on. Remember it and replay once after the current
            # idempotent push completes; never overlap cmd=113 writers.
            self._local_mqtt_config_retry_pending = True
            return current

        async def _apply_with_pending_replay() -> None:
            failure_retry = 0
            while not self._shutdown_started:
                self._local_mqtt_config_retry_pending = False
                success = await self.async_apply_local_mqtt_config_to_devices()
                if success is False:
                    if failure_retry >= len(_LOCAL_MQTT_CONFIG_RETRY_DELAYS_SEC):
                        return
                    await self._async_local_mqtt_config_retry_sleep(
                        _LOCAL_MQTT_CONFIG_RETRY_DELAYS_SEC[failure_retry],
                    )
                    failure_retry += 1
                    continue
                failure_retry = 0
                if not self._local_mqtt_config_retry_pending:
                    return

        return self._schedule_background_once(
            key,
            _apply_with_pending_replay,
            name=f"{DOMAIN}_local_mqtt_device_config",
        )

    @staticmethod
    async def _async_local_mqtt_config_retry_sleep(delay: float) -> None:
        """Sleep before a bounded cmd=113 retry without blocking HA."""
        await asyncio.sleep(delay)

    def _note_property_equivalent_push(
        self,
        device_id: str,
        body: Mapping[str, Any],
    ) -> None:
        """Remember per-field live-property arrival times and diagnostics."""
        now = time.monotonic()
        if any(key in body for key in self._MAIN_LIVE_PROPERTY_KEYS):
            self._last_property_push_monotonic = now
        received = getattr(self, "_live_property_received_monotonic", None)
        if received is None:
            received = self._live_property_received_monotonic = {}
        timestamps = received.setdefault(device_id, {})
        for key in self._MQTT_LIVE_MAIN_PROPERTY_KEYS:
            if key in body and body.get(key) is not None:
                timestamps[key] = now

    def _schedule_topology_reload(self) -> None:
        """Reload once after a rare dynamic topology reindex completes."""
        if self._shutdown_started:
            return
        current = self._topology_reload_task
        if current is not None and not current.done():
            return

        async def _reload() -> None:
            await asyncio.sleep(1)
            if not await self.hass.config_entries.async_reload(self.entry.entry_id):
                msg = "Home Assistant rejected the Jackery topology reload"
                raise RuntimeError(msg)
            self._battery_pack_topology_reload_required = False
            replacement = self.entry.runtime_data
            if isinstance(replacement, type(self)) and replacement is not self:
                for identifier in self._pending_device_removals:
                    if identifier not in replacement._pending_device_removals:  # ruff:ignore[private-member-access]
                        replacement._pending_device_removals.append(identifier)  # ruff:ignore[private-member-access]
                replacement._pending_battery_pack_removal_serials.update(  # ruff:ignore[private-member-access]
                    self._pending_battery_pack_removal_serials
                )
                self._pending_device_removals.clear()
                self._pending_battery_pack_removal_serials.clear()
                self._battery_pack_identity_overrides.clear()

        task = self.hass.async_create_background_task(
            _reload(),
            name=f"{DOMAIN}_topology_reload",
            eager_start=False,
        )
        self._topology_reload_task = task

        def _reload_done(done: asyncio.Task[None]) -> None:
            if self._topology_reload_task is done:
                self._topology_reload_task = None
            try:
                done.result()
            except asyncio.CancelledError:
                return
            except Exception as err:  # ruff:ignore[blind-except]
                _LOGGER.warning("Jackery topology reload failed: %s", err)

        task.add_done_callback(_reload_done)

    @property
    def has_pending_supplemental_transport_cleanup(self) -> bool:
        """Return whether a Layer-5 transport still needs independent cleanup."""  # ruff:ignore[property-docstring-starts-with-verb]
        return (
            self._mqtt is not None
            or self._ble_listener is not None
            or bool(self._supplemental_transport_tasks())
        )

    def _supplemental_transport_tasks(self) -> set[asyncio.Task[Any]]:
        """Return background work that must never fence primary HTTP startup."""
        return {
            task
            for task in (
                self._statistics_import_task,
                self._slow_metrics_bg_task,
                self._mqtt_poll_task,
                self._shadow_fallback_task,
                *self._battery_pack_ota_tasks.values(),
                *self._ble_coalesce_tasks.values(),
                *self._local_mqtt_message_tasks,
                *self._background_tasks.values(),
            )
            if task is not None
        }

    def _retain_pending_supplemental_tasks(
        self,
        pending_tasks: set[asyncio.Task[Any]],
    ) -> None:
        """Drop completed supplemental task references and retain retryable work."""
        if self._statistics_import_task not in pending_tasks:
            self._statistics_import_task = None
        if self._slow_metrics_bg_task not in pending_tasks:
            self._slow_metrics_bg_task = None
        if self._mqtt_poll_task not in pending_tasks:
            self._mqtt_poll_task = None
        if self._shadow_fallback_task not in pending_tasks:
            self._shadow_fallback_task = None
        self._battery_pack_ota_tasks = {
            key: task
            for key, task in self._battery_pack_ota_tasks.items()
            if task in pending_tasks
        }
        self._ble_coalesce_tasks = {
            key: task
            for key, task in self._ble_coalesce_tasks.items()
            if task in pending_tasks
        }
        self._local_mqtt_message_tasks.intersection_update(pending_tasks)
        self._background_tasks = {
            key: task
            for key, task in self._background_tasks.items()
            if task in pending_tasks
        }
        if not self._ble_coalesce_tasks:
            self._ble_pending_updates.clear()

    async def async_stop_supplemental_transports(self) -> None:  # ruff:ignore[too-many-branches]
        """Bound and retry Layer-5 cleanup independently from HTTP teardown."""
        stop_errors: list[str] = []
        tracked_tasks = self._supplemental_transport_tasks()
        current_task = asyncio.current_task()
        if current_task is not None:
            tracked_tasks.discard(current_task)
        for task in tracked_tasks:
            if not task.done():
                task.cancel()
        pending_tasks: set[asyncio.Task[Any]] = set()
        if tracked_tasks:
            done_tasks, pending_tasks = await asyncio.wait(
                tracked_tasks,
                timeout=_BACKGROUND_TASK_STOP_TIMEOUT_SEC,
            )
            for task in done_tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    task.result()
        self._retain_pending_supplemental_tasks(pending_tasks)
        if pending_tasks:
            stop_errors.append(
                f"{len(pending_tasks)} supplemental task(s) did not stop within "
                f"{_BACKGROUND_TASK_STOP_TIMEOUT_SEC:.1f}s"
            )

        transports: list[tuple[str, object, Awaitable[None]]] = []
        if self._mqtt is not None:
            transports.append(("MQTT", self._mqtt, self._mqtt.async_stop()))
        if self._ble_listener is not None:
            transports.append((
                "BLE",
                self._ble_listener,
                self._ble_listener.async_stop(),
            ))
        if transports:
            try:
                async with asyncio.timeout(_BACKGROUND_TASK_STOP_TIMEOUT_SEC):
                    results = await asyncio.gather(
                        *(stop for _label, _client, stop in transports),
                        return_exceptions=True,
                    )
            except TimeoutError:
                stop_errors.append(
                    "supplemental transport stop exceeded "
                    f"{_BACKGROUND_TASK_STOP_TIMEOUT_SEC:.1f}s"
                )
            else:
                for (label, client, _stop), result in zip(
                    transports,
                    results,
                    strict=True,
                ):
                    if isinstance(result, BaseException):
                        stop_errors.append(f"{label} stop failed: {result}")
                        continue
                    if label == "MQTT" and self._mqtt is client:
                        self._mqtt = None
                    elif label == "BLE" and self._ble_listener is client:
                        self._ble_listener = None
        if stop_errors:
            raise RuntimeError("; ".join(stop_errors))

    async def async_shutdown(self) -> None:  # ruff:ignore[too-many-branches]
        """Stop MQTT + BLE clients on integration unload."""
        self._shutdown_started = True
        if self._base_shutdown_task is None:
            self._base_shutdown_task = self.hass.async_create_task(
                super().async_shutdown(),
                name=f"{DOMAIN}_base_shutdown_{self.entry.entry_id}",
            )
        await asyncio.shield(self._base_shutdown_task)
        if self._poll_watchdog_unsub is not None:
            self._poll_watchdog_unsub()
            self._poll_watchdog_unsub = None
        for unsubscribe in self._local_mqtt_unsubs:
            with contextlib.suppress(Exception):
                unsubscribe()
        self._local_mqtt_unsubs.clear()

        supplemental_tasks = self._supplemental_transport_tasks()
        current_shutdown_task = asyncio.current_task()
        if current_shutdown_task is not None:
            supplemental_tasks.discard(current_shutdown_task)
        for task in supplemental_tasks:
            if not task.done():
                task.cancel()
        self._retain_pending_supplemental_tasks({
            task for task in supplemental_tasks if not task.done()
        })

        tracked_tasks = set(self._active_http_update_tasks)
        if (
            self._topology_reload_task is not None
            and self._topology_reload_task is not current_shutdown_task
        ):
            tracked_tasks.add(self._topology_reload_task)
        for task in tracked_tasks:
            if not task.done():
                task.cancel()
        pending_tasks: set[asyncio.Task[Any]] = set()
        if tracked_tasks:
            done_tasks, pending_tasks = await asyncio.wait(
                tracked_tasks,
                timeout=_BACKGROUND_TASK_STOP_TIMEOUT_SEC,
            )
            for task in done_tasks:
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    task.result()
        shutdown_errors: list[str] = []
        if pending_tasks:
            shutdown_errors.append(
                f"{len(pending_tasks)} background task(s) did not stop within "
                f"{_BACKGROUND_TASK_STOP_TIMEOUT_SEC:.1f}s"
            )

        self._active_http_update_tasks.intersection_update(pending_tasks)
        if (
            self._topology_reload_task is not current_shutdown_task
            and self._topology_reload_task not in pending_tasks
        ):
            self._topology_reload_task = None
        if shutdown_errors:
            raise RuntimeError("; ".join(shutdown_errors))

    # ------------------------------------------------------------------
    # BLE transport (experimental, Phase 3a)
    # ------------------------------------------------------------------

    def _ble_writes_enabled(self) -> bool:
        """Return whether BLE writes are allowed for this entry.

        Deliberately gated on the BLE transport option **alone**. An earlier
        revision required a second opt-in (``CONF_ENABLE_BLE_WRITES``, default off)
        on top of it, which meant the BLE setters never fired unless both switches
        were on. BLE writes are no longer experimental, so that second gate is gone
        and the option stays hidden: enabling the BLE transport enables the setters
        with it. Do not restore the second gate.
        """
        return config_entry_bool_option(
            self.entry,
            CONF_ENABLE_BLE_TRANSPORT,
            DEFAULT_ENABLE_BLE_TRANSPORT,
        )

    async def async_send_ble_command(  # ruff:ignore[too-many-arguments]
        self,
        device_id: str,
        *,
        cmd: int,
        body: dict[str, Any] | bytes,
        flags: int = 0,
        wait_for_ack: bool = False,
        ack_timeout_sec: float = DEFAULT_BLE_ACK_TIMEOUT_SEC,
        mtu_override: int | None = None,
        connect_timeout_sec: float = 0.0,
    ) -> bool:
        """Send a single command frame to the device over BLE.

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
        if (
            isinstance(flags, bool)
            or isinstance(cmd, bool)
            or (flags, cmd) not in _HOME_BLE_COMMAND_PAIRS
            or cmd in _BLE_FIRST_UNSUPPORTED_MSG_TYPES
        ):
            return False
        if not self._ble_writes_enabled() or self._ble_listener is None:
            return False
        if isinstance(body, dict):
            transport_body = self._command_body_for_transport(body, cmd=cmd)
            payload = json.dumps(
                transport_body,
                separators=(",", ":"),
                ensure_ascii=False,
            )
            body_bytes = payload.encode("utf-8")
        else:
            body_bytes = bytes(body)
        if (
            connect_timeout_sec > 0
            and not await self._ble_listener.async_ensure_connected(
                device_id,
                timeout_sec=connect_timeout_sec,
            )
        ):
            return False
        sent = await self._ble_listener.async_send_command(
            device_id,
            msg_id=flags,
            ble_msg_type=cmd,
            body=body_bytes,
            wait_for_ack=wait_for_ack,
            ack_timeout_sec=ack_timeout_sec,
            mtu_override=mtu_override,
        )
        return bool(sent)

    def ble_observations(self) -> dict[str, Any]:
        """Return a JSON-friendly snapshot of the BLE listener stats.

        Used by diagnostics + the optional BLE-status sensor. Returns an
        empty dict when BLE is disabled or the listener is not running so
        the integration stays usable on systems without Bluetooth.
        """
        ble_enabled = config_entry_bool_option(
            self.entry,
            CONF_ENABLE_BLE_TRANSPORT,
            DEFAULT_ENABLE_BLE_TRANSPORT,
        )
        ble_write_enabled = self._ble_writes_enabled()
        listener_running = self._ble_listener is not None

        listener_stats: dict[str, Any] = (
            dict(self._ble_listener.all_stats())
            if self._ble_listener is not None
            else {}
        )

        device_ids = set(self._device_index)
        device_ids.update(listener_stats)
        snapshot: dict[str, Any] = {}
        for device_id in sorted(device_ids):
            stats = listener_stats.get(device_id)
            last_frame = getattr(stats, "last_frame", None)
            last_connect_at = getattr(stats, "last_connect_at", None)
            last_disconnect_at = getattr(stats, "last_disconnect_at", None)
            last_ack_at = getattr(stats, "last_ack_at", None)
            entry: dict[str, Any] = {
                "enabled": ble_enabled,
                "write_enabled": ble_write_enabled,
                "running": listener_running,
                "advertisements_seen": int(getattr(stats, "advertisements_seen", 0)),
                "connect_attempts": int(getattr(stats, "connect_attempts", 0)),
                "connect_failures": int(getattr(stats, "connect_failures", 0)),
                "frames_received": int(getattr(stats, "frames_received", 0)),
                "frames_decoded": int(getattr(stats, "frames_decoded", 0)),
                "frames_decode_failed": int(getattr(stats, "frames_decode_failed", 0)),
                "multi_chunk_frames_buffered": int(
                    getattr(stats, "multi_chunk_frames_buffered", 0)
                ),
                "multi_chunk_messages_assembled": int(
                    getattr(stats, "multi_chunk_messages_assembled", 0)
                ),
                "multi_chunk_assemblies_dropped": int(
                    getattr(stats, "multi_chunk_assemblies_dropped", 0)
                ),
                "notify_frames_dropped": int(
                    getattr(stats, "notify_frames_dropped", 0)
                ),
                "acks_received": int(getattr(stats, "acks_received", 0)),
                "acks_timed_out": int(getattr(stats, "acks_timed_out", 0)),
                "last_error": getattr(stats, "last_error", None),
                "last_keep_alive_error": getattr(
                    stats,
                    "last_keep_alive_error",
                    None,
                ),
                "last_decode_error": getattr(stats, "last_decode_error", None),
                "last_sink_error": getattr(stats, "last_sink_error", None),
                "last_connect_at": (
                    last_connect_at.isoformat()
                    if isinstance(last_connect_at, datetime)
                    else None
                ),
                "last_disconnect_at": (
                    last_disconnect_at.isoformat()
                    if isinstance(last_disconnect_at, datetime)
                    else None
                ),
                "last_ack_at": (
                    last_ack_at.isoformat()
                    if isinstance(last_ack_at, datetime)
                    else None
                ),
                "mtu": (
                    self._ble_listener.mtu_for_device(device_id)
                    if self._ble_listener is not None
                    else None
                ),
                # Per-cmd unrouted counter so the maintainer sees what
                # BLE telemetry currently flows past without being
                # merged into coordinator.data. Cmd 120 (system /
                # per-device / CT lifetime) is the most common entry.
                "unrouted_frames_by_cmd": dict(
                    getattr(stats, "unrouted_frames_by_cmd", {}),
                ),
            }
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
                                "utf-8",
                                errors="replace",
                            ),
                            "trailer_hex": last_frame.parsed.trailer.hex(),
                        }
                        if last_frame.parsed is not None
                        else None
                    ),
                }
            snapshot[device_id] = entry
        return snapshot

    def http_api_observations(self) -> dict[str, Any]:
        """Return a JSON-friendly snapshot of the HTTP API + Cloud MQTT counters.

        Used by the HTTP API diagnostic sensor. Merges the API transport
        counters with the Cloud MQTT push-client diagnostics so the sensor
        gives a single view of the cloud path health.
        """
        api_snap = self.api.diagnostics_snapshot()
        mqtt_snap = self.mqtt_diagnostics_snapshot()
        return {
            "connected": mqtt_snap.get("connected", False),
            "requests_total": api_snap.get("requests_total", 0),
            "requests_failed": api_snap.get("requests_failed", 0),
            "timeouts_total": api_snap.get("timeouts_total", 0),
            "auth_retries": api_snap.get("auth_retries", 0),
            "mqtt_messages_seen": mqtt_snap.get("messages_seen", 0),
            "mqtt_messages_dropped": mqtt_snap.get("messages_dropped", 0),
            "mqtt_birth_publishes": mqtt_snap.get("birth_publishes", 0),
            "mqtt_birth_publish_failed": mqtt_snap.get("birth_publish_failed", 0),
            "mqtt_last_birth_at": mqtt_snap.get("last_birth_at"),
            "last_error": mqtt_snap.get("last_error"),
            "connect_attempts": mqtt_snap.get("connect_attempts", 0),
            "consecutive_auth_failures": mqtt_snap.get("consecutive_auth_failures", 0),
        }

    def cloud_mqtt_observations(self) -> dict[str, Any]:
        """Return a JSON-friendly snapshot of the Cloud MQTT push client.

        Used by the Cloud MQTT diagnostic sensor. Enriches the raw
        ``diagnostics_snapshot`` with coordinator-level MQTT context.
        """
        snap = self.mqtt_diagnostics_snapshot()
        return {
            "connected": snap.get("connected", False),
            "messages_seen": snap.get("messages_seen", 0),
            "messages_dropped": snap.get("messages_dropped", 0),
            "pending_message_tasks": snap.get("pending_message_tasks", 0),
            "max_pending_message_tasks": snap.get("max_pending_message_tasks", 0),
            "birth_publishes": snap.get("birth_publishes", 0),
            "birth_publish_failed": snap.get("birth_publish_failed", 0),
            "last_birth_at": snap.get("last_birth_at"),
            "last_connect_at": snap.get("last_connect_at"),
            "last_disconnect_at": snap.get("last_disconnect_at"),
            "last_message_at": snap.get("last_message_at"),
            "last_error": snap.get("last_error"),
            "connect_attempts": snap.get("connect_attempts", 0),
            "consecutive_auth_failures": snap.get("consecutive_auth_failures", 0),
            "topic_count": snap.get("topic_count", 0),
            "tls_custom_ca_loaded": snap.get("tls_custom_ca_loaded", False),
            "library": snap.get("library"),
        }

    def local_mqtt_observations(self) -> dict[str, Any]:
        """Return a JSON-friendly snapshot of the Local MQTT bridge.

        Used by the Local MQTT diagnostic sensor. Reports both receive
        paths: the direct :class:`JackeryLocalMqttClient` (Path A) and the
        HA-integration broker listener (Path B). ``messages_received`` sums
        both counters and ``enabled`` reflects either path, so the RX total
        stays visible even when only the listener is active and no direct
        client is registered.
        """
        bucket = self.hass.data.get(DOMAIN, {}).get(self.entry.entry_id)
        client = (
            bucket.get(LOCAL_MQTT_RUNTIME_KEY) if isinstance(bucket, dict) else None
        )
        snap = (
            client.diagnostics_snapshot(redact=False)
            if isinstance(client, JackeryLocalMqttClient)
            else {}
        )
        listener_active = bool(self._local_mqtt_unsubs)
        return {
            "enabled": bool(snap.get("enabled", False)) or listener_active,
            "connected": snap.get("connected", False),
            "messages_received": (
                snap.get("messages_received", 0)
                + self._local_mqtt_listener_messages_received
            ),
            "messages_dropped": snap.get("messages_dropped", 0),
            "messages_forwarded": snap.get("messages_forwarded", 0),
            "messages_ignored_foreign": (
                snap.get("messages_ignored_foreign", 0)
                + self._local_mqtt_listener_foreign_ignored
            ),
            "pending_sink_tasks": snap.get("pending_sink_tasks", 0),
            "max_pending_sink_tasks": snap.get("max_pending_sink_tasks", 0),
            "sink_backlog_dropped": snap.get("sink_backlog_dropped", 0),
            "last_connect_at": snap.get("last_connect_at"),
            "last_disconnect_at": snap.get("last_disconnect_at"),
            "last_message_at": snap.get("last_message_at"),
            "last_error": snap.get("last_error"),
            "connect_attempts": snap.get("connect_attempts", 0),
            "blocked_by_filter_count": snap.get("blocked_by_filter_count", 0),
            "payload_too_large_count": snap.get("payload_too_large_count", 0),
            "home_assistant_event_count": snap.get("home_assistant_event_count", 0),
            "pending_ha_local_mqtt_tasks": len(self._local_mqtt_message_tasks),
            "max_pending_ha_local_mqtt_tasks": _LOCAL_MQTT_MAX_PENDING_MESSAGE_TASKS,
            "backlog_dropped": self._local_mqtt_backlog_dropped,
            "routing_warning": snap.get("routing_warning"),
            "local_mqtt_active": self._local_mqtt_is_active(),
            "library": snap.get("library"),
        }

    def _local_mqtt_direct_client_connected(self) -> bool:
        """Return whether the direct local MQTT client has a broker session."""
        hass = getattr(self, "hass", None)
        entry = getattr(self, "entry", None)
        if hass is None or entry is None:
            return False
        bucket = hass.data.get(DOMAIN, {}).get(entry.entry_id)
        if not isinstance(bucket, dict):
            return False
        client = bucket.get(LOCAL_MQTT_RUNTIME_KEY)
        return isinstance(client, JackeryLocalMqttClient) and client.is_connected

    def _local_mqtt_is_active(self, now_monotonic: float | None = None) -> bool:
        """Return whether Local MQTT has delivered a recent telemetry frame.

        This is message-freshness telemetry only. It never controls HTTP,
        Cloud MQTT, or BLE lifecycle decisions.
        """
        now = time.monotonic() if now_monotonic is None else now_monotonic
        last_message = safe_float(
            getattr(self, "_local_mqtt_last_message_monotonic", float("-inf")),
        )
        if last_message is None:
            return False
        return now - last_message <= MQTT_LIVE_THRESHOLD_SEC

    def _ble_backoff_for_device(self, device_id: str) -> BleConnectBackoff:
        """Return the Coordinator-owned BLE connect backoff for one device."""
        backoff = self._ble_connect_backoff.get(device_id)
        if backoff is None:
            backoff = BleConnectBackoff()
            self._ble_connect_backoff[device_id] = backoff
        return backoff

    def _ble_connect_backoff_remaining(self, device_id: str, now: float) -> float:
        """Return remaining BLE connect backoff seconds for a device."""
        return self._ble_backoff_for_device(device_id).seconds_until_allowed(now)

    def _ble_note_connect_failure(self, device_id: str, now: float) -> float:
        """Record a BLE connect failure and return the next retry delay."""
        return self._ble_backoff_for_device(device_id).record_failure(now)

    def _ble_note_connect_success(self, device_id: str) -> None:
        """Clear BLE connect backoff after a successful device session."""
        self._ble_backoff_for_device(device_id).record_success()

    async def async_start_ble_transport(self) -> None:  # ruff:ignore[too-many-statements]
        """Start the optional BLE listener if the config-entry option is set.

        Safe to call repeatedly; only the first call attaches a listener.
        Failures are logged at WARNING and don't propagate — BLE is an
        opt-in diagnostic channel and must not break cloud setup.
        """
        if self._ble_listener is not None:
            return
        if not config_entry_bool_option(
            self.entry,
            CONF_ENABLE_BLE_TRANSPORT,
            DEFAULT_ENABLE_BLE_TRANSPORT,
        ):
            return

        async def _sink(device_id: str, observation: BleFrameObservation) -> bool:  # ruff:ignore[too-many-return-statements, too-many-branches]
            """Merge BLE-delivered JSON bodies into ``coordinator.data``.

            Mirrors the cmd-routing of ``_async_handle_mqtt_message`` so
            BLE-decoded telemetry uses the **same** merge helpers as MQTT
            payloads. Without this contract the live values stop updating
            whenever MQTT goes quiet: a 2-arg
            ``_merge_main_properties_for_device(device_id, payload)``
            call (the previous shape) raises ``TypeError`` in the sink's
            ``try/except`` and silently drops every decoded frame
            (observed 2026-05-16 17:41–17:44 production log).

            Routing per ``cmd`` (from :data:`.const.MQTT_CMD_*`):

            * ``107`` / ``121`` (DevicePropertyChange / CombineData):
              main-properties merge via
              :meth:`_merge_main_properties_for_device` (3 args:
              ``device_id, base, updates``).
            * ``111`` (UploadSubDeviceIncrementalProperty): subdevice
              merge via :meth:`_merge_subdevice_data`.
            * Other cmds (e.g. ``120`` lifetime stat snapshots) are
              logged but not merged — they need their own contract that
              is out of scope here.

            After a successful merge the updated bundle is published via
            :meth:`_push_partial_update` so the existing entity listeners
            pick it up.
            """  # ruff:ignore[ambiguous-unicode-character-docstring]
            if observation.parsed is None:
                return False
            body = observation.parsed.body
            if not body:
                return False
            try:
                payload = json.loads(body.decode("utf-8"))
            except PAYLOAD_PARSE_ERRORS as err:
                _LOGGER.debug(
                    "Jackery BLE %s: body is not JSON (cmd=%d, %d bytes): %s",
                    device_id,
                    observation.parsed.cmd,
                    len(body),
                    err,
                )
                return False
            if not isinstance(payload, dict):
                _LOGGER.debug(
                    "Jackery BLE %s: body decoded to %s, expected dict",
                    device_id,
                    type(payload).__name__,
                )
                return False
            # Strip the BLE-only framing key — ``cmd`` duplicates
            # ``observation.parsed.cmd`` and never appears in cloud HTTP
            # payloads. ``deviceSn`` and ``devType`` stay (subdevice
            # merge needs them).
            payload = {k: v for k, v in payload.items() if k != FIELD_CMD}
            if not payload:
                return False

            cmd = observation.parsed.cmd
            # Mirror the MQTT debug-emit pattern (see ``_async_handle_mqtt_message``)
            # so payload-debug consumers can see BLE traffic too. The lazy
            # factory keeps the ``chart_series_debug`` walk off the hot path
            # when the dedicated payload_debug logger is below DEBUG. The
            # ``topic`` field is synthesised as ``ble://<device>/cmd<n>`` so
            # the per-channel dedup throttle in
            # ``_async_payload_debug_event`` distinguishes each device/cmd
            # pair (otherwise every BLE frame would share one cache slot).
            await self._async_payload_debug_event(
                lambda: {
                    "kind": "ble",
                    "topic": f"ble://{device_id}/cmd{cmd}",
                    "device_id": device_id,
                    "cmd": cmd,
                    "body_size": len(body),
                    "payload": payload,
                    "payload_chart_series_debug": chart_series_debug(payload),
                },
            )
            current_device = self._ble_partial_update_base(device_id)
            if not isinstance(current_device, dict):
                # Coordinator hasn't populated this device yet (first
                # cloud refresh still pending). Drop the frame quietly;
                # the next BLE notify after discovery will land.
                return False
            updated = dict(current_device)
            touched = False

            if cmd in {MQTT_CMD_DEVICE_PROPERTY_CHANGE, MQTT_CMD_CONTROL_COMBINE}:
                property_payload = self._normalize_live_property_payload(payload)
                props = self._merge_main_properties_for_device(
                    device_id,
                    current_device.get(PAYLOAD_PROPERTIES) or {},
                    property_payload,
                    source=TransportSource.BLE,
                )
                updated[PAYLOAD_PROPERTIES] = props
                touched = bool(property_payload) or touched
            elif cmd == MQTT_CMD_CONTROL_SUB_DEVICE:
                touched = self._merge_subdevice_data(
                    updated,
                    payload,
                    device_id=device_id,
                    source_transport=TransportSource.BLE,
                )
            elif cmd == MQTT_CMD_QUERY_COMBINE_DATA:
                if self._is_subdevice_payload(payload, payload):
                    touched = (
                        self._merge_subdevice_data(
                            updated,
                            payload,
                            device_id=device_id,
                            source_transport=TransportSource.BLE,
                        )
                        or touched
                    )
                    if str(payload.get(FIELD_DEV_TYPE)) == str(
                        SUBDEVICE_DEV_TYPE_BATTERY_PACK,
                    ) and payload.get(FIELD_DEVICE_SN):
                        touched = (
                            self._merge_battery_pack_lifetime_from_ble(
                                updated,
                                payload,
                            )
                            or touched
                        )
                else:
                    property_payload = self._normalize_live_property_payload(payload)
                    props = self._merge_main_properties_for_device(
                        device_id,
                        current_device.get(PAYLOAD_PROPERTIES) or {},
                        property_payload,
                        source=TransportSource.BLE,
                    )
                    updated[PAYLOAD_PROPERTIES] = props
                    touched = bool(property_payload) or touched
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
                return False
            if updated == current_device:
                return False

            self._schedule_ble_partial_update(device_id, updated)
            return True

        try:
            ble_transport_module = await self.hass.async_add_executor_job(
                importlib.import_module,
                f"{__package__}.client.ble_transport",
            )
            listener_class = cast("Any", ble_transport_module).JackeryBleListener
        except (AttributeError, ImportError) as err:
            _LOGGER.warning(
                "Jackery BLE transport is unavailable; HTTP remains active: %s",
                err,
            )
            return

        listener = listener_class(
            self.hass,
            _sink,
            key_resolver=self.device_bluetooth_key,
            ble_address_resolver=self._ble_address_for_device,
            connect_backoff_remaining=self._ble_connect_backoff_remaining,
            connect_backoff_note_failure=self._ble_note_connect_failure,
            connect_backoff_note_success=self._ble_note_connect_success,
            keep_alive_msg_id=ACTION_ID_QUERY_DEVICE_PROPERTY,
            keep_alive_ble_msg_type=MQTT_CMD_QUERY_DEVICE_PROPERTY,
            serial_resolver=self.device_id_for_ble_serial,
        )
        try:
            await listener.async_start(list(self._device_index.keys()))
        except BACKGROUND_TASK_ERRORS as err:
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
            for device_id in self._device_index:
                key = self.device_bluetooth_key(device_id)
                if key is None:
                    _LOGGER.warning(
                        "Jackery DEV_MODE: device %s has no bluetoothKey captured yet",
                        device_id,
                    )
                    continue
                # Never write raw key material into the HA log, even in
                # DEV_MODE — HA logs get shared for support. The full key
                # remains available (DEV_MODE only) in the payload-debug
                # JSONL and the diagnostics export.
                _LOGGER.warning(
                    "Jackery DEV_MODE: device %s bluetoothKey captured "
                    "(sha256=%s, %d bytes); full key is in the payload-debug "
                    "JSONL / diagnostics export",
                    device_id,
                    hashlib.sha256(key).hexdigest()[:16],
                    len(key),
                )

    async def async_reconcile_ble_transport(self) -> None:
        """Restart only BLE after its option changes, leaving HTTP untouched."""
        if self._shutdown_started:
            return
        listener = self._ble_listener
        if listener is not None:
            try:
                async with asyncio.timeout(_BACKGROUND_TASK_STOP_TIMEOUT_SEC):
                    await listener.async_stop()
            except TimeoutError:
                _LOGGER.warning(
                    "Jackery BLE option reconfiguration exceeded %.1fs; "
                    "HTTP remains active",
                    _BACKGROUND_TASK_STOP_TIMEOUT_SEC,
                )
                return
            except Exception as err:  # ruff:ignore[blind-except]
                _LOGGER.warning(
                    "Jackery BLE option reconfiguration failed; HTTP remains "
                    "active: %s",
                    err,
                )
                return
            if self._ble_listener is listener:
                self._ble_listener = None
        await self.async_start_ble_transport()

    def _ble_address_for_device(self, device_id: str) -> str | None:
        """Best-effort lookup of the BLE MAC for a Jackery device id.

        BLE addresses are learnt at advertisement time (the listener stores
        ``serial → MAC`` in its own state). The coordinator does not cache
        the MAC because it can change between adapter resets; the listener's
        in-memory map is the source of truth.
        """
        if self._ble_listener is None:
            return None
        return cast("str | None", self._ble_listener.address_for_device_id(device_id))

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
                    or "",
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
        self,
        *,
        force: bool = False,
        wait_connected: bool = False,
    ) -> None:
        """Ensure MQTT is connected with credentials from current login session."""
        mqtt = self._mqtt
        if mqtt is None:
            return

        current_fp = self.api.mqtt_fingerprint
        if self._mqtt_mgr.should_skip_reconnect(mqtt, current_fp, force=force):
            return

        # Cache-only: MQTT is a data-transport layer and must NEVER trigger
        # login/reauth (owner invariant 2026-07-05). It consumes the session
        # the HTTP/API login cached; when none is present yet, back off and
        # let the HTTP path acquire it — do NOT escalate to reauth.
        creds = self.api.get_cached_mqtt_credentials()
        if creds is None:
            _LOGGER.debug(
                "Jackery MQTT: no cached credentials yet; deferring connect to "
                "the HTTP login path",
            )
            return

        if not self._mqtt_mgr.generated_mac_warning_logged and str(
            self.api.mqtt_mac_id_source,
        ).startswith("generated"):
            _LOGGER.debug(
                "Jackery MQTT uses internally generated macId (%s)",
                self.api.mqtt_mac_id_source,
            )
            self._mqtt_mgr.generated_mac_warning_logged = True

        fingerprint = self.api.mqtt_fingerprint
        if (
            self._mqtt_mgr.fingerprint is not None
            and fingerprint != self._mqtt_mgr.fingerprint
        ):
            _LOGGER.info("Jackery MQTT: credential session changed, reconnecting")

        # A concurrent unload/reload may replace the MQTT runtime while we
        # awaited credentials. Bail out quietly instead of touching a stale
        # handle that might already be stopped.
        if self._mqtt is not mqtt:
            return

        self._mqtt_mgr.record_connect_attempt()
        await mqtt.async_start(
            client_id=creds[MQTT_CREDENTIAL_CLIENT_ID],
            username=creds[MQTT_CREDENTIAL_USERNAME],
            password=creds[MQTT_CREDENTIAL_PASSWORD],
            user_id=creds[MQTT_CREDENTIAL_USER_ID],
            wait_connected=wait_connected,
        )
        if self._mqtt is not mqtt:
            return
        if not wait_connected:
            # ``async_start`` returns immediately when its active session has
            # exactly these credentials. Only that client-verified no-op may
            # repair manager state after a delayed/cancelled connect callback.
            if mqtt.is_connected:
                self._mqtt_mgr.record_connect_success(mqtt, fingerprint)
            return
        if wait_connected:
            try:
                await mqtt.async_wait_until_connected(timeout_sec=15.0)
            except RuntimeError as err:
                mqtt_last_error = mqtt.diagnostics.get("last_error")
                if self._is_mqtt_auth_failure(err) or self._is_mqtt_auth_failure(
                    mqtt_last_error,
                ):
                    streak = mqtt.consecutive_auth_failures
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
                self._mqtt_mgr.handle_connect_error(mqtt, mqtt_last_error or err)
                raise
        self._mqtt_mgr.record_connect_success(mqtt, fingerprint)

    @_serialize_mqtt_messages_by_device
    async def _async_handle_mqtt_message(  # ruff:ignore[complex-structure, too-many-branches, too-many-locals, too-many-statements]
        self,
        topic: str,
        payload: dict[str, Any],
        *,
        source: TransportSource = TransportSource.CLOUD_MQTT,
    ) -> str | None:
        """Merge inbound MQTT payloads into coordinator data and push update."""
        # Lazy-built event: when the dedicated payload_debug logger is not
        # explicitly at DEBUG level, the factory is never called — saving the
        # ``chart_series_debug`` walk on the per-MQTT-message hot path.
        await self._async_payload_debug_event(
            lambda: {
                "kind": source.value,
                "topic": topic,
                "payload": payload,
                "body_type": type(payload.get(FIELD_BODY)).__name__,
                "data_type": type(payload.get(FIELD_DATA)).__name__,
                "body_chart_series_debug": chart_series_debug(payload.get(FIELD_BODY)),
                "data_chart_series_debug": chart_series_debug(payload.get(FIELD_DATA)),
            },
        )
        if not self.data:
            return None

        device_id = self._resolve_device_id_from_mqtt(payload)
        if not device_id:
            return None
        if device_id not in self.data:
            return None

        current = self.data[device_id]
        updated = dict(current)
        touched = False
        body = payload.get(FIELD_BODY)
        if not isinstance(body, dict):
            alt_body = payload.get(FIELD_DATA)
            body = alt_body if isinstance(alt_body, dict) else {}
        msg_type = payload.get(FIELD_MESSAGE_TYPE)
        action_id = first_nonblank_int(payload.get(FIELD_ACTION_ID))
        cmd = first_nonblank_int(body.get(FIELD_CMD))
        classification_body = body
        if cmd is not None:
            classification_body = {**body, FIELD_CMD: cmd}
        is_subdevice = self._is_subdevice_payload(payload, classification_body)
        is_alarm = (
            is_alarm_message(msg_type, action_id, classification_body)
            or msg_type == MQTT_MESSAGE_UPLOAD_DEVICE_ALERT
            or cmd == MQTT_CMD_UPLOAD_DEVICE_ALERT
            or action_id in MQTT_ACTION_IDS_ALARM
        )
        is_third_party_mqtt_config = is_third_party_mqtt_config_message(
            msg_type,
            action_id,
            classification_body,
        )
        is_wifi_config = is_wifi_config_message(
            msg_type, action_id, classification_body
        )
        is_wifi_list = is_wifi_list_message(action_id, classification_body)
        is_time_zone_config = is_time_zone_config_message(
            action_id,
            classification_body,
        )
        is_grid_standard_sync = is_grid_standard_sync_message(
            action_id,
            classification_body,
        )
        is_mqtt_connect_info = is_mqtt_connect_info_message(
            action_id,
            classification_body,
        )
        is_device_ota_version = is_device_ota_version_message(
            action_id,
            classification_body,
        )
        if topic.endswith(("/device", "/config")):
            if body:
                if is_wifi_config:
                    updated[PAYLOAD_WIFI_CONFIG] = body
                    touched = True
                elif is_wifi_list:
                    updated[PAYLOAD_WIFI_LIST] = body
                    touched = True
                elif is_time_zone_config:
                    updated[PAYLOAD_TIMEZONE_CONFIG] = body
                    if body.get(FIELD_TIMEZONE) is not None:
                        system = dict(current.get(PAYLOAD_SYSTEM) or {})
                        system[FIELD_TIMEZONE] = body.get(FIELD_TIMEZONE)
                        updated[PAYLOAD_SYSTEM] = system
                        self._patch_device_index_system_meta(
                            device_id,
                            {FIELD_TIMEZONE: body.get(FIELD_TIMEZONE)},
                        )
                    touched = True
                elif is_grid_standard_sync:
                    value = body.get(FIELD_GRID_STANDARD)
                    if value is None:
                        value = body.get(FIELD_SAFETY)
                    if value is not None:
                        system = dict(current.get(PAYLOAD_SYSTEM) or {})
                        system[FIELD_GRID_STANDARD] = str(value)
                        updated[PAYLOAD_SYSTEM] = system
                        self._patch_device_index_system_meta(
                            device_id,
                            {FIELD_GRID_STANDARD: str(value)},
                        )
                    touched = True
                elif is_mqtt_connect_info:
                    updated[PAYLOAD_MQTT_CONNECT_INFO] = body
                    touched = True
                elif is_device_ota_version:
                    ota = dict(current.get(PAYLOAD_OTA) or {})
                    ota.update(body)
                    updated[PAYLOAD_OTA] = ota
                    touched = True
                elif is_third_party_mqtt_config:
                    updated[PAYLOAD_THIRD_PARTY_MQTT_CONFIG] = (
                        self._decode_third_party_mqtt_config_body(device_id, body)
                    )
                    touched = True
                elif is_subdevice:
                    touched = (
                        self._merge_subdevice_data(
                            updated,
                            body,
                            device_id=device_id,
                            source_transport=source,
                        )
                        or touched
                    )
                elif not is_alarm:
                    property_body = self._normalize_live_property_payload(body)
                    props = self._merge_main_properties_for_device(
                        device_id,
                        current.get(PAYLOAD_PROPERTIES) or {},
                        property_body,
                        source=source,
                    )
                    updated[PAYLOAD_PROPERTIES] = props
                    touched = bool(property_body) or touched

            # Keep known metadata in sync when the envelope includes it.
            if payload.get(FIELD_DEVICE_SN) and not is_subdevice:
                meta = dict(current.get(PAYLOAD_DEVICE) or {})
                if meta.get(FIELD_DEVICE_SN) != payload.get(FIELD_DEVICE_SN):
                    meta[FIELD_DEVICE_SN] = payload.get(FIELD_DEVICE_SN)
                    updated[PAYLOAD_DEVICE] = meta
                    touched = True

        elif topic.endswith("/alert"):
            updated[PAYLOAD_ALARM] = body or payload
            touched = True

        elif topic.endswith("/notice"):
            # Not entity-backed today; keep as diagnostic context.
            updated[PAYLOAD_NOTICE] = payload
            touched = True

        if is_alarm:
            updated[PAYLOAD_ALARM] = body or payload
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
            or cmd == MQTT_CMD_QUERY_WEATHER_PLAN
            or action_id in weather_action_ids
        ):
            updated[PAYLOAD_WEATHER_PLAN] = body or payload
            touched = True

        # User-configurable schedule payloads (custom mode / tariff mode /
        # smart-plug priority) are transported via DownloadDeviceSchedule.
        if (
            msg_type == MQTT_MESSAGE_DOWNLOAD_DEVICE_SCHEDULE
            or action_id in MQTT_ACTION_IDS_SCHEDULE
        ):
            updated[PAYLOAD_TASK_PLAN] = body or payload
            touched = True

        # Electricity strategy (charge/discharge plan) messages from portable
        # devices.  QueryElectricityStrategy, InsertElectricityStrategy,
        # UpdateElectricityStrategy, DeleteElectricityStrategy, and
        # QueryCurrentElectricityStrategy all carry plan data in the body.
        if msg_type in {
            MQTT_MESSAGE_QUERY_ELECTRICITY_STRATEGY,
            MQTT_MESSAGE_INSERT_ELECTRICITY_STRATEGY,
            MQTT_MESSAGE_UPDATE_ELECTRICITY_STRATEGY,
            MQTT_MESSAGE_DELETE_ELECTRICITY_STRATEGY,
            MQTT_MESSAGE_QUERY_CURRENT_ELECTRICITY_STRATEGY,
        }:
            updated[PAYLOAD_ELECTRICITY_STRATEGY] = body or payload
            touched = True

        # TOU (Time-of-Use) schedule messages: TOUSchedule (set) and
        # QueryTOUSchedule (get) carry peak/trough tariff schedules.
        if msg_type in {
            MQTT_MESSAGE_TOU_SCHEDULE,
            MQTT_MESSAGE_QUERY_TOU_SCHEDULE,
        }:
            updated[PAYLOAD_TOU_SCHEDULE] = body or payload
            touched = True

        # SetBatteryBoundry carries battery SOC charge/discharge limits.
        if msg_type == MQTT_MESSAGE_SET_BATTERY_BOUNDARY:
            updated[PAYLOAD_BATTERY_BOUNDARY] = body or payload
            touched = True

        # QueryCircuitProperty carries circuit breaker / relay configuration.
        if msg_type == MQTT_MESSAGE_QUERY_CIRCUIT_PROPERTY:
            updated[PAYLOAD_CIRCUIT_PROPERTY] = body or payload
            touched = True

        # Device-property snapshots are the MQTT equivalent of the
        # /v1/device/property HTTP endpoint. The app requests them with
        # READ_DEVICE_INFO (QueryDeviceProperty, actionId=3011, cmd=106).
        if (
            not is_subdevice  # ruff:ignore[too-many-boolean-expressions]
            and not (
                is_wifi_config
                or is_wifi_list
                or is_time_zone_config
                or is_grid_standard_sync
                or is_mqtt_connect_info
                or is_device_ota_version
                or is_third_party_mqtt_config
            )
            and (
                msg_type
                in {
                    MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
                    MQTT_MESSAGE_QUERY_DEVICE_PROPERTY,
                }
                or action_id in MQTT_ACTION_IDS_DEVICE_PROPERTY
                or cmd
                in {
                    MQTT_CMD_DEVICE_PROPERTY_CHANGE,
                    MQTT_CMD_QUERY_DEVICE_PROPERTY,
                }
            )
            and body
        ):
            property_body = self._normalize_live_property_payload(body)
            props = self._merge_main_properties_for_device(
                device_id,
                current.get(PAYLOAD_PROPERTIES) or {},
                property_body,
                source=source,
            )
            updated[PAYLOAD_PROPERTIES] = props
            touched = bool(property_body) or touched

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
                or cmd in {MQTT_CMD_QUERY_COMBINE_DATA, MQTT_CMD_CONTROL_COMBINE}
            )
            and body
        ):
            property_body = self._normalize_live_property_payload(body)
            props = self._merge_main_properties_for_device(
                device_id,
                current.get(PAYLOAD_PROPERTIES) or {},
                property_body,
                source=source,
            )
            updated[PAYLOAD_PROPERTIES] = props
            touched = bool(property_body) or touched
            # Persist the CombineData system-info fields so they survive
            # temporary MQTT disconnects.  The HTTP property endpoint
            # (HomeBody) never returns these keys (SystemBody only), so
            # without caching the sensors would flip to Unknown when
            # MQTT drops.
            cached: dict[str, Any] = {}
            for key in self._SYSTEM_INFO_KEYS:
                val = property_body.get(key)
                if val is not None:
                    cached[key] = val
            if cached:
                self._system_info_cache.setdefault(device_id, {}).update(cached)
                self._system_info_cache_monotonic[device_id] = time.monotonic()

        # Local third-party MQTT can publish the same app field names on a
        # plain user topic without Jackery's cloud envelope metadata. If the
        # body clearly contains main-device live properties, merge it through
        # the same sanitizer instead of dropping it because the topic does not
        # end in `/device`.
        if (
            not is_subdevice
            and not is_alarm
            and body
            and any(key in body for key in self._MAIN_LIVE_PROPERTY_KEYS)
        ):
            property_body = self._normalize_live_property_payload(body)
            props = self._merge_main_properties_for_device(
                device_id,
                current.get(PAYLOAD_PROPERTIES) or {},
                property_body,
                source=source,
            )
            updated[PAYLOAD_PROPERTIES] = props
            touched = bool(property_body) or touched

        # Sub-device status: battery packs and CT/smart meter values are
        # transported as QuerySubDeviceGroupProperty responses.
        if (
            is_subdevice
            or msg_type == MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY
            or action_id in MQTT_ACTION_IDS_SUBDEVICE
        ):
            subdevice_source = body or payload
            if isinstance(subdevice_source, dict):
                touched = (
                    self._merge_subdevice_data(
                        updated,
                        subdevice_source,
                        device_id=device_id,
                        source_transport=source,
                    )
                    or touched
                )

        if not touched:
            return None

        updated[PAYLOAD_MQTT_LAST] = {
            "topic": topic,
            FIELD_MESSAGE_TYPE: payload.get(FIELD_MESSAGE_TYPE),
            FIELD_ACTION_ID: payload.get(FIELD_ACTION_ID),
            FIELD_TIMESTAMP: payload.get(FIELD_TIMESTAMP),
            FIELD_DEVICE_SN: payload.get(FIELD_DEVICE_SN),
            "received_at_monotonic": time.monotonic(),
            "source": source.value,
        }

        new_data = dict(self.data)
        new_data[device_id] = updated
        self._push_partial_update(new_data)
        if updated.get(PAYLOAD_BATTERY_PACKS):
            self._schedule_battery_pack_ota_enrichment(device_id)
        return device_id

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
                        FIELD_DEVICE_SN,
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

    async def async_handle_local_mqtt_message(
        self,
        topic: str,
        payload: dict[str, Any],
    ) -> None:
        """Route local third-party MQTT JSON through the shared MQTT parser.

        The device-side third-party bridge can publish body-only JSON on the
        local broker instead of the cloud envelope. Wrap that body so the
        existing MQTT/BLE routing logic sees the same normalized shape.
        """
        accepted_device_id = await self._async_handle_mqtt_message(
            topic,
            self._normalize_local_mqtt_payload(payload),
            source=TransportSource.LOCAL_MQTT,
        )
        # Freshness is diagnostic state only; Local MQTT never controls the
        # lifecycle or scheduling of HTTP, Cloud MQTT, or BLE.
        now = time.monotonic()
        self._local_mqtt_last_message_monotonic = now
        if accepted_device_id:
            self._local_mqtt_last_device_message_monotonic[accepted_device_id] = now

    @classmethod
    def _normalize_local_mqtt_payload(
        cls,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        """Normalize body-only LAN MQTT payloads into the cloud-MQTT envelope."""
        return _normalize_local_mqtt_payload_fn(payload)

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

    def device_supports_third_party_mqtt(self, device_id: str) -> bool:
        """Return True if the device supports third-party MQTT configuration.

        True when the device has already sent a ThirdPartMQTTConfig payload
        (``PAYLOAD_THIRD_PARTY_MQTT_CONFIG`` present) or when
        ``device_supports_advanced`` is True from Home/System payload evidence.
        """
        payload = (self.data or {}).get(device_id, {})
        return (
            PAYLOAD_THIRD_PARTY_MQTT_CONFIG in payload
            or self.device_supports_advanced(device_id)
        )

    def device_supports_advanced(self, device_id: str) -> bool:
        """Return True if the device exposes advanced controls.

        Home-family devices expose the full setting catalog. Prefer stable
        system-list/property metadata over a transient ``maxOutPw`` value so
        disabling a push transport cannot remove buttons or switches. Portable
        legacy-bind devices remain excluded.

        Centralized so that every platform asks the same question the same
        way; previously this 1-liner was duplicated across button/select/
        sensor/switch.
        """
        payload = (self.data or {}).get(device_id, {})
        raw_props = payload.get(PAYLOAD_PROPERTIES) or {}
        props = raw_props if isinstance(raw_props, dict) else {}
        raw_http_props = payload.get(PAYLOAD_HTTP_PROPERTIES) or {}
        if isinstance(raw_http_props, dict) and raw_http_props:
            props = merge_present_dict_values(props, raw_http_props)
        if FIELD_MAX_OUT_PW in props:
            return True

        index = getattr(self, "_device_index", {}).get(device_id) or {}
        for system in (
            payload.get(PAYLOAD_SYSTEM),
            payload.get(PAYLOAD_SYSTEM_META),
            index.get(PAYLOAD_SYSTEM_META),
        ):
            if isinstance(system, dict) and system:
                return True

        metadata = [
            payload.get(PAYLOAD_DEVICE),
            payload.get(PAYLOAD_DISCOVERY),
            index.get(PAYLOAD_DEVICE_META),
        ]
        if any(
            isinstance(item, dict)
            and item.get(PAYLOAD_DISCOVERY_SOURCE) == DISCOVERY_SOURCE_LEGACY_BIND_LIST
            for item in metadata
        ):
            return False
        return any(
            isinstance(item, dict)
            and bool(item.get(FIELD_MODEL_CODE) or item.get(FIELD_DEV_MODEL))
            for item in metadata
        )

    # ------------------------------------------------------------------
    # Property merging & payload helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _property_value_present(value: object) -> bool:
        """Return whether a live property value should count as present."""
        if value is None:
            return False
        if isinstance(value, str) and not value.strip():
            return False
        return not (isinstance(value, (dict, list)) and not value)

    @classmethod
    def _merge_main_properties(
        cls,
        base: dict[str, Any],
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        """Sanitize, merge, and normalize main-device property payloads."""
        merged = merge_present_dict_values(
            cls._sanitize_main_properties(base),
            cls._sanitize_main_properties(updates),
        )
        return sync_property_aliases(merged, cls._MAIN_PROPERTY_ALIAS_PAIRS)

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

    def _transport_source_freshness_window(self) -> float:
        """Return how long a higher-priority transport owns one field."""
        interval = getattr(self, "_configured_update_interval", timedelta(seconds=15))
        return max(60.0, interval.total_seconds() * 2)

    def _source_field_update_allowed(
        self,
        state: dict[str, tuple[TransportSource, float]],
        field: str,
        source: TransportSource,
        *,
        now: float,
    ) -> bool:
        """Return whether ``source`` may replace the current value of ``field``."""
        previous = state.get(field)
        if previous is None:
            return True
        previous_source, received_at = previous
        if source == previous_source:
            return True
        if (
            _TRANSPORT_SOURCE_PRIORITY[source]
            >= _TRANSPORT_SOURCE_PRIORITY[previous_source]
        ):
            return True
        return now - received_at > self._transport_source_freshness_window()

    def _property_updates_for_source(
        self,
        device_id: str,
        updates: dict[str, Any],
        source: TransportSource,
    ) -> dict[str, Any]:
        """Filter and stamp main-property updates by transport precedence."""
        clean = self._sanitize_main_properties(updates)
        source_state = getattr(self, "_property_source_state", None)
        if source_state is None:
            source_state = self._property_source_state = {}
        device_state = source_state.setdefault(device_id, {})
        now = time.monotonic()
        accepted: dict[str, Any] = {}
        for field, value in clean.items():
            if not self._property_value_present(value):
                continue
            if not self._source_field_update_allowed(
                device_state,
                field,
                source,
                now=now,
            ):
                continue
            accepted[field] = value
            device_state[field] = (source, now)
        return accepted

    def _accessory_updates_for_source(
        self,
        device_id: str | None,
        bucket: str,
        identity: str,
        updates: dict[str, Any],
        source: TransportSource,
    ) -> dict[str, Any]:
        """Filter and stamp one accessory's fields by transport precedence."""
        if device_id is None:
            return dict(updates)
        source_state = getattr(self, "_accessory_source_state", None)
        if source_state is None:
            source_state = self._accessory_source_state = {}
        item_state = source_state.setdefault((device_id, bucket, identity), {})
        now = time.monotonic()
        accepted: dict[str, Any] = {}
        for field, value in updates.items():
            if field == SUBDEVICE_FIELD_LAST_SEEN_AT or field in (
                _ACCESSORY_IDENTITY_FIELDS
            ):
                accepted[field] = value
                continue
            if not self._property_value_present(value):
                continue
            if not self._source_field_update_allowed(
                item_state,
                field,
                source,
                now=now,
            ):
                continue
            accepted[field] = value
            item_state[field] = (source, now)
        return accepted

    def _merge_main_properties_for_device(
        self,
        device_id: str,
        base: dict[str, Any],
        updates: dict[str, Any],
        *,
        source: TransportSource = TransportSource.CLOUD_MQTT,
    ) -> dict[str, Any]:
        """Merge main properties with source priority and setter protection."""
        accepted = self._property_updates_for_source(device_id, updates, source)
        if source is not TransportSource.HTTP:
            self._note_property_equivalent_push(device_id, accepted)
        merged = self._merge_main_properties(base, accepted)
        overrides = self._active_property_overrides(device_id)
        if not overrides:
            return merged
        return self._merge_main_properties(merged, overrides)

    def _overlay_cached_system_info(
        self,
        device_id: str,
        props: dict[str, Any],
    ) -> dict[str, Any]:
        """Fill HTTP-missing SystemBody fields from the CombineData cache.

        Fill-only + TTL: the cache exists so SystemBody-only keys survive
        temporary MQTT disconnects. It must never overwrite a value a
        fresh source already delivered, and an expired cache stops
        filling instead of presenting hours-old state as current.
        """
        cached = self._system_info_cache.get(device_id)
        if not cached:
            return props
        stamped = self._system_info_cache_monotonic.get(device_id)
        if (
            stamped is None
            or time.monotonic() - stamped > SYSTEM_INFO_CACHE_MAX_AGE_SEC
        ):
            return props
        filled = dict(props)
        for key, value in cached.items():
            if not self._property_value_present(
                filled.get(key)
            ) and self._property_value_present(value):
                filled[key] = value
        return filled

    @staticmethod
    def _find_dict_with_any_key(
        obj: object,
        keys: frozenset[str],
    ) -> dict[str, Any] | None:
        """Find the first nested dict containing any of the requested keys."""
        return find_dict_with_any_key(obj, keys)

    def _reconcile_today_energy(self, entry: dict[str, Any]) -> None:
        """Cross-check today_energy against day-period stats (AGENTS.md §2.3).

        The compact ``/v1/device/stat/today`` endpoint returns ``de/dg/dh/ds``
        as daily KPIs.  At boundary times the cloud may return 0 for these
        while the dated ``dateType=day`` endpoints still carry real values.
        When that happens, use the dated endpoint value via
        :func:`verify_and_backfill` so the Energy Dashboard does not show
        misleading 0 values.

        Field mapping (today_energy → day-period stat):
        * ``de`` (feed-in) → ``APP_STAT_TOTAL_OUT_GRID_ENERGY`` from home_stat_day
        * ``dg`` (grid import) → ``APP_STAT_TOTAL_IN_GRID_ENERGY`` from home_stat_day
        * ``dh`` (home load) → ``APP_STAT_TOTAL_HOME_ENERGY`` from home_stat_day
        * ``ds`` (battery energy) → ``APP_STAT_TOTAL_CHARGE`` from battery_stat_day
        """
        today_energy = entry.get(APP_SECTION_TODAY_ENERGY)
        if not isinstance(today_energy, dict) or not today_energy:
            return

        home_stat_day = entry.get(
            self._app_period_section(APP_SECTION_HOME_STAT, DATE_TYPE_DAY),
        )
        battery_stat_day = entry.get(
            self._app_period_section(APP_SECTION_BATTERY_STAT, DATE_TYPE_DAY),
        )

        def _reconcile_field(
            field: str,
            alt_section: dict[str, Any] | None,
            alt_key: str,
        ) -> None:
            cloud_val = safe_float(today_energy.get(field))
            # ``None`` means the cloud omitted the field; a measured ``0.0`` is a
            # real reading and has to survive. Merging the two into one
            # "missing_or_zero" predicate dropped legitimate zeros (grid import
            # on a pure feed-in day), which surfaced the sensor as
            # ``unavailable`` instead of ``0``. A negative daily energy is never
            # plausible and counts as absent.
            if cloud_val is None or cloud_val < 0:
                today_energy.pop(field, None)
                return
            if not isinstance(alt_section, dict):
                return
            local_val = safe_float(alt_section.get(alt_key))
            if local_val is None or local_val < 0 or math.isclose(local_val, 0.0):
                # No usable comparison value, so there is nothing to backfill
                # from. Keep the cloud reading as it stands — including a
                # confirmed ``0.0``.
                return
            reconciled = verify_and_backfill(
                cloud_val,
                local_val,
                label=f"today_energy.{field}",
            )
            if reconciled is None:
                today_energy.pop(field, None)
                return
            if reconciled != cloud_val:
                today_energy[field] = reconciled

        _reconcile_field(
            APP_STAT_TODAY_FEED_IN_ENERGY,
            home_stat_day,
            APP_STAT_TOTAL_OUT_GRID_ENERGY,
        )
        _reconcile_field(
            APP_STAT_TODAY_GRID_IMPORT_ENERGY,
            home_stat_day,
            APP_STAT_TOTAL_IN_GRID_ENERGY,
        )
        _reconcile_field(
            APP_STAT_TODAY_HOME_LOAD_ENERGY,
            home_stat_day,
            APP_STAT_TOTAL_HOME_ENERGY,
        )
        _reconcile_field(
            APP_STAT_TODAY_BATTERY_ENERGY,
            battery_stat_day,
            APP_STAT_TOTAL_CHARGE,
        )

    def _has_activation_contradicting_payload(self, entry: dict[str, Any]) -> bool:
        """Return true when populated data contradicts cloud activated=0."""
        evidence_keys = (
            PAYLOAD_PROPERTIES,
            PAYLOAD_HTTP_PROPERTIES,
            PAYLOAD_DEVICE_STATISTIC,
            APP_SECTION_TODAY_ENERGY,
        )
        if any(
            isinstance(entry.get(key), dict) and entry[key] for key in evidence_keys
        ):
            return True
        for prefix in (
            APP_SECTION_PV_STAT,
            APP_SECTION_BATTERY_STAT,
            APP_SECTION_HOME_STAT,
            APP_SECTION_CT_STAT,
            APP_SECTION_EPS_STAT,
            APP_SECTION_PV_TRENDS,
            APP_SECTION_HOME_TRENDS,
            APP_SECTION_BATTERY_TRENDS,
        ):
            for date_type in APP_PERIOD_DATE_TYPES:
                section = self._app_period_section(prefix, date_type)
                section_payload = entry.get(section)
                if isinstance(section_payload, dict) and section_payload:
                    return True
        return False

    @classmethod
    def _sanitize_main_properties(cls, props: dict[str, Any]) -> dict[str, Any]:
        """Remove accessory-only fields from main device properties."""
        return _sanitize_main_properties_fn(props)

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
        action_id = first_nonblank_int(payload.get(FIELD_ACTION_ID))
        if action_id is not None:
            payload = {**payload, FIELD_ACTION_ID: action_id}
        return is_subdevice_payload(
            payload,
            body,
            cls._SUBDEVICE_HINT_KEYS,
            cls._BATTERY_PACK_HINT_KEYS,
            cls._SUBDEVICE_DEV_TYPE_STRINGS,
        )

    @classmethod
    def _battery_packs_from_source(cls, source: object) -> list[dict[str, Any]] | None:
        """Extract up to five add-on battery pack payloads from known shapes."""
        return battery_packs_from_source(
            source,
            cls._CT_METER_KEYS,
            cls._BATTERY_PACK_HINT_KEYS,
        )

    def _battery_packs_need_query(self, payload: dict[str, Any]) -> bool:
        """Return True when add-on packs exist or are expected.

        The Android app polls BatteryPackSub over MQTT. The HTTP
        battery-pack endpoint can return data:null for this product/account,
        so stopping the MQTT query after the first SOC value leaves addon
        batteries stale.
        """
        return battery_packs_need_query(
            payload,
            rejection_callback=self.record_schema_rejection,
        )

    def _merge_subdevice_data(  # ruff:ignore[complex-structure, too-many-branches, too-many-locals, too-many-statements]
        self,
        updated: dict[str, Any],
        source: dict[str, Any],
        *,
        device_id: str | None = None,
        source_transport: TransportSource = TransportSource.CLOUD_MQTT,
    ) -> bool:
        """Route accessory data to accessory sections instead of main props."""
        touched = False

        def _filter_items(
            bucket: str,
            items: list[dict[str, Any]],
            serial_fn: Callable[[dict[str, Any]], str | None],
        ) -> list[dict[str, Any]]:
            filtered: list[dict[str, Any]] = []
            for index, item in enumerate(items, start=1):
                identity = serial_fn(item) or f"index_{index}"
                accepted = self._accessory_updates_for_source(
                    device_id,
                    bucket,
                    identity,
                    item,
                    source_transport,
                )
                if accepted:
                    filtered.append(accepted)
            return filtered

        def _merge_battery_packs(packs: list[dict[str, Any]]) -> None:
            nonlocal device_id, touched
            packs = _filter_items(
                PAYLOAD_BATTERY_PACKS,
                packs,
                battery_pack_serial,
            )
            if not packs:
                return
            updated[PAYLOAD_BATTERY_PACKS] = self._merge_battery_pack_lists(
                updated.get(PAYLOAD_BATTERY_PACKS),
                packs,
            )
            removal_device_id = device_id or self._resolve_device_id_from_payload(
                updated
            )
            if device_id is None:
                device_id = removal_device_id
            self._prune_stale_battery_pack_bucket(
                updated,
                device_id=removal_device_id,
            )
            touched = True

        packs = self._battery_packs_from_source(source)
        if packs:
            _merge_battery_packs(packs)

        ct = self._find_dict_with_any_key(source, self._CT_METER_KEYS)
        if ct:
            ct = {
                **ct,
                SUBDEVICE_FIELD_LAST_SEEN_AT: datetime.now(UTC).isoformat(),
            }
            # Shelly Pro 3EM wraps volt/curr/freq/fact/ap/rep inside a nested
            # AccCTBody dict. Merge AccCTBody keys up so sensors that read
            # volt/curr/... find them.
            acc_ct = ct.get(FIELD_ACC_CT_BODY)
            if isinstance(acc_ct, dict):
                # Surface nested AccCTBody keys up without blanking already
                # populated CT values (AGENTS.md §2.3: no raw dict overwrites).
                ct = merge_present_dict_values(ct, acc_ct)
            ct = self._accessory_updates_for_source(
                device_id,
                PAYLOAD_CT_METER,
                "ct",
                ct,
                source_transport,
            )
            current_ct = updated.get(PAYLOAD_CT_METER)
            if isinstance(current_ct, dict):
                updated[PAYLOAD_CT_METER] = merge_present_dict_values(current_ct, ct)
            else:
                updated[PAYLOAD_CT_METER] = dict(ct)
            if (
                ct
                and source_transport is not TransportSource.HTTP
                and device_id is not None
            ):
                received = getattr(self, "_live_ct_received_monotonic", None)
                if received is None:
                    received = self._live_ct_received_monotonic = {}
                received[device_id] = time.monotonic()
            touched = True

        plugs = source.get(FIELD_PLUGS)
        if isinstance(plugs, list):
            plug_dicts = [item for item in plugs if isinstance(item, dict)]
            plug_dicts = _filter_items(
                PAYLOAD_SMART_PLUGS,
                plug_dicts,
                subdevice_serial,
            )
            if plug_dicts:
                self._merge_and_prune_subdevice_bucket(
                    updated,
                    PAYLOAD_SMART_PLUGS,
                    plug_dicts,
                    self._merge_smart_plug_lists,
                    subdevice_serial,
                    device_id=device_id,
                    key_prefix="smart_plug",
                )
                touched = True

        collectors = source.get(FIELD_COLLECTORS)
        if isinstance(collectors, list):
            collector_dicts = [item for item in collectors if isinstance(item, dict)]
            collector_dicts = _filter_items(
                PAYLOAD_METER_HEADS,
                collector_dicts,
                subdevice_serial,
            )
            if collector_dicts:
                self._merge_and_prune_subdevice_bucket(
                    updated,
                    PAYLOAD_METER_HEADS,
                    collector_dicts,
                    self._merge_subdevice_lists_by_sn,
                    subdevice_serial,
                    device_id=device_id,
                    key_prefix="meter_head",
                )
                touched = True

        circuits = source.get(FIELD_CIR)
        if isinstance(circuits, list):
            circuit_dicts = [item for item in circuits if isinstance(item, dict)]
            circuit_dicts = _filter_items(
                PAYLOAD_CIRCUIT_PROPERTY,
                circuit_dicts,
                circuit_id,
            )
            if circuit_dicts:
                self._merge_and_prune_subdevice_bucket(
                    updated,
                    PAYLOAD_CIRCUIT_PROPERTY,
                    circuit_dicts,
                    _merge_circuits_fn,
                    circuit_id,
                    device_id=device_id,
                    key_prefix="breaker",
                )
                touched = True

        sub_devices = source.get(FIELD_SUB_DEVICE)
        if isinstance(sub_devices, list):
            sub_device_dicts = [item for item in sub_devices if isinstance(item, dict)]
            if sub_device_dicts:
                battery_pack_dicts: list[dict[str, Any]] = []
                regular_sub_device_dicts: list[dict[str, Any]] = []
                for item in sub_device_dicts:
                    item_packs = self._battery_packs_from_source(item)
                    if item_packs:
                        battery_pack_dicts.extend(item_packs)
                    else:
                        regular_sub_device_dicts.append(item)
                if battery_pack_dicts:
                    _merge_battery_packs(battery_pack_dicts)
                if regular_sub_device_dicts:
                    regular_sub_device_dicts = _filter_items(
                        PAYLOAD_SUBDEVICES,
                        regular_sub_device_dicts,
                        sub_device_serial,
                    )
                if regular_sub_device_dicts:
                    self._merge_and_prune_subdevice_bucket(
                        updated,
                        PAYLOAD_SUBDEVICES,
                        regular_sub_device_dicts,
                        _merge_sub_devices_fn,
                        sub_device_serial,
                        device_id=device_id,
                        key_prefix="sub_device",
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
                    source=source_transport,
                )
            updated[PAYLOAD_PROPERTIES] = props
            touched = True

        return touched

    def _merge_and_prune_subdevice_bucket(  # ruff:ignore[too-many-arguments]
        self,
        updated: dict[str, Any],
        bucket: str,
        update_dicts: list[dict[str, Any]],
        merge_fn: Callable[[Any, list[dict[str, Any]]], list[dict[str, Any]]],
        serial_fn: Callable[[dict[str, Any]], str | None],
        *,
        device_id: str | None,
        key_prefix: str,
    ) -> None:
        """Merge one accessory bucket and remove confirmed stale ghosts.

        Transport updates are partial. They refresh only mentioned identities;
        destructive cleanup additionally requires absence from the complete
        HTTP system-list discovery stored on ``updated``.
        """
        merged = merge_fn(updated.get(bucket), update_dicts)
        touched_serials = {
            sn for item in update_dicts if (sn := serial_fn(item)) is not None
        }
        merged = stamp_subdevice_last_seen(merged, touched_serials, serial_fn)
        now_iso = datetime.now(UTC).isoformat()
        for index, update in enumerate(update_dicts):
            if serial_fn(update) is None and index < len(merged):
                merged[index][SUBDEVICE_FIELD_LAST_SEEN_AT] = now_iso
        updated[bucket] = merged
        self._prune_stale_subdevice_bucket(
            updated,
            bucket,
            serial_fn,
            device_id=device_id,
            key_prefix=key_prefix,
        )

    @staticmethod
    def _discovery_confirms_accessory_absent(
        entry: dict[str, Any],
        item: dict[str, Any],
    ) -> bool:
        """Confirm an accessory is absent from an explicit system-list result."""
        system = entry.get(PAYLOAD_SYSTEM) or entry.get(PAYLOAD_SYSTEM_META)
        if not isinstance(system, dict):
            return False
        accessories = system.get(FIELD_ACCESSORIES)
        if not isinstance(accessories, list):
            return False
        item_ids = subdevice_identity_values(item)
        if not item_ids:
            return False
        return not any(
            isinstance(accessory, dict)
            and bool(item_ids & subdevice_identity_values(accessory))
            for accessory in accessories
        )

    def _prune_stale_subdevice_bucket(
        self,
        updated: dict[str, Any],
        bucket: str,
        serial_fn: Callable[[dict[str, Any]], str | None],
        *,
        device_id: str | None,
        key_prefix: str,
    ) -> int:
        """Remove confirmed stale accessories and queue registry cleanup."""
        current = updated.get(bucket)
        if not isinstance(current, list):
            return 0
        merged = [item for item in current if isinstance(item, dict)]
        kept, stale_count, dropped_indices = drop_stale_subdevices(
            merged,
            confirmed_absent=lambda item: self._discovery_confirms_accessory_absent(
                updated,
                item,
            ),
        )
        if kept != current:
            updated[bucket] = kept
        if stale_count:
            parent_device_id = device_id or self._resolve_device_id_from_payload(
                updated
            )
            if parent_device_id is not None:
                for dropped_index in dropped_indices:
                    dropped = merged[dropped_index]
                    stable_key = stable_subdevice_key(
                        key_prefix,
                        serial_fn(dropped),
                        dropped_index + 1,
                    )
                    identifier = (DOMAIN, f"{parent_device_id}_{stable_key}")
                    if identifier not in self._pending_device_removals:
                        self._pending_device_removals.append(identifier)
            _LOGGER.info(
                "Jackery: dropped %d stale %s entrie(s) silent for >%d days",
                stale_count,
                bucket,
                BATTERY_PACK_STALE_THRESHOLD_SEC // 86400,
            )
        return stale_count

    def _prune_stale_battery_pack_bucket(
        self,
        updated: dict[str, Any],
        *,
        device_id: str | None,
    ) -> int:
        """Age out one pack bucket and queue the matching registry device."""
        current = updated.get(PAYLOAD_BATTERY_PACKS)
        if not isinstance(current, list):
            return 0
        ordered = sorted_battery_pack_payloads(current)
        kept, stale_count, dropped_indices = self._drop_stale_battery_packs(ordered)
        if kept != current:
            updated[PAYLOAD_BATTERY_PACKS] = kept
        if not stale_count:
            return 0
        self._stale_battery_packs_dropped += stale_count
        parent_device_id = device_id or self._resolve_device_id_from_payload(updated)
        if parent_device_id is not None:

            def _queue_pack(pack: dict[str, Any], pack_index: int) -> None:
                pack_number = pack_index + 1
                serial = self._battery_pack_identity_overrides.get(
                    (parent_device_id, pack_number),
                    battery_pack_serial(pack),
                )
                stable_key = stable_subdevice_key(
                    "battery_pack",
                    serial,
                    pack_number,
                )
                identifier = (
                    DOMAIN,
                    f"{parent_device_id}_{stable_key}",
                )
                if identifier not in self._pending_device_removals:
                    self._pending_device_removals.append(identifier)
                if serial is not None:
                    self._pending_battery_pack_removal_serials[identifier] = serial

            dropped_index_set = set(dropped_indices)
            for dropped_index in dropped_indices:
                _queue_pack(ordered[dropped_index], dropped_index)

            # Serial-identified survivors stay stable when an earlier pack
            # disappears. Session-frozen index identities remain positional,
            # so unlink only those shifted fallback devices.
            first_affected_index = min(dropped_indices)
            for affected_index in range(first_affected_index, len(ordered)):
                if affected_index in dropped_index_set:
                    continue
                affected_pack = ordered[affected_index]
                identity_serial = self._battery_pack_identity_overrides.get(
                    (parent_device_id, affected_index + 1),
                    battery_pack_serial(affected_pack),
                )
                if identity_serial is not None:
                    continue
                _queue_pack(affected_pack, affected_index)
            self._battery_pack_topology_reload_required = True
        _LOGGER.info(
            "Jackery: dropped %d stale battery pack(s) silent for >%d days",
            stale_count,
            BATTERY_PACK_STALE_THRESHOLD_SEC // 86400,
        )
        return stale_count

    def _prune_stale_battery_packs(
        self,
        data: dict[str, Any],
    ) -> None:
        """Run battery-pack cleanup every completed HTTP cycle."""
        for parent_device_id, entry in data.items():
            if isinstance(entry, dict):
                self._prune_stale_battery_pack_bucket(
                    entry,
                    device_id=parent_device_id,
                )

    def _prune_stale_subdevices(
        self,
        data: dict[str, Any],
    ) -> None:
        """Run stale accessory cleanup every HTTP cycle, even without new pushes."""
        bucket_specs = (
            (PAYLOAD_SMART_PLUGS, subdevice_serial, "smart_plug"),
            (PAYLOAD_METER_HEADS, subdevice_serial, "meter_head"),
            (PAYLOAD_CIRCUIT_PROPERTY, circuit_id, "breaker"),
            (PAYLOAD_SUBDEVICES, sub_device_serial, "sub_device"),
        )
        for parent_device_id, entry in data.items():
            if not isinstance(entry, dict):
                continue
            for bucket, serial_fn, key_prefix in bucket_specs:
                self._prune_stale_subdevice_bucket(
                    entry,
                    bucket,
                    serial_fn,
                    device_id=parent_device_id,
                    key_prefix=key_prefix,
                )
            ct_meter = entry.get(PAYLOAD_CT_METER)
            if not isinstance(ct_meter, dict):
                continue

            def _confirmed_absent(
                item: dict[str, Any],
                parent_entry: dict[str, Any] = entry,
            ) -> bool:
                """Return whether authoritative discovery omits this accessory."""
                return self._discovery_confirms_accessory_absent(
                    parent_entry,
                    item,
                )

            kept_ct, stale_count, _dropped = drop_stale_subdevices(
                [ct_meter],
                confirmed_absent=_confirmed_absent,
            )
            if kept_ct:
                entry[PAYLOAD_CT_METER] = kept_ct[0]
                continue
            if stale_count:
                entry.pop(PAYLOAD_CT_METER, None)
                identifier = (DOMAIN, f"{parent_device_id}_smart_meter")
                if identifier not in self._pending_device_removals:
                    self._pending_device_removals.append(identifier)
                _LOGGER.info(
                    "Jackery: dropped stale smart-meter data silent for >%d days",
                    BATTERY_PACK_STALE_THRESHOLD_SEC // 86400,
                )

    def _normalize_live_property_payload(  # ruff:ignore[no-self-use]
        self, source: dict[str, Any]
    ) -> dict[str, Any]:
        """Normalize live properties before merging into coordinator data."""
        return normalize_live_property_payload(source)

    @classmethod
    def _merge_battery_pack_lists(
        cls,
        current: Any,  # loose prior-state list, duck-typed via `current or []`  # ruff:ignore[any-type]
        updates: list[dict[str, Any]],
        *,
        refresh_last_seen: bool = True,
    ) -> list[dict[str, Any]]:
        """Merge incremental pack telemetry without dropping static fields.

        Jackery's MQTT sub-device packets often contain only inPw/outPw plus
        deviceSn. Replacing the full pack list with those packets removes
        fields learned from HTTP/OTA (version, SOC, temperature). Keep known
        fields and overlay the latest non-null telemetry by SN, falling back
        to list position.
        """
        return _merge_battery_pack_lists_fn(
            current,
            updates,
            refresh_last_seen=refresh_last_seen,
        )

    @classmethod
    def _merge_subdevice_lists_by_sn(
        cls,
        current: Any,  # loose prior-state list, duck-typed via `current or []`  # ruff:ignore[any-type]
        updates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge generic subdevice telemetry by ``deviceSn`` when available."""
        return _merge_subdevice_lists_by_sn_fn(current, updates)

    @classmethod
    def _merge_smart_plug_lists(
        cls,
        current: Any,  # loose prior-state list, duck-typed via `current or []`  # ruff:ignore[any-type]
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
        return _merge_smart_plug_lists_fn(current, updates)

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
        rationale and ``docs/PROTOCOL.md`` §1 for the rule that we never
        invent device state, only document silence.
        """
        return _drop_stale_battery_packs_fn(packs, threshold_seconds=threshold_seconds)

    @staticmethod
    def _resolve_device_id_from_payload(payload: dict[str, Any]) -> str | None:
        """Pick the parent device id from a coordinator payload slice.

        Used by the stale-pack cleanup to construct the ``device_registry``
        identifier. The coordinator data is keyed by ``device_id`` at the
        top level, but nested payload slices passed into the merge step
        do not carry that key. Best-effort fallback: read ``deviceId``,
        ``device_id`` or ``id`` from the merged props.
        """
        return _resolve_device_id_from_payload_fn(payload)

    @callback
    def set_battery_pack_identity_override(
        self,
        parent_device_id: str,
        pack_index: int,
        serial: str | None,
    ) -> None:
        """Freeze one pack's registry identity for this coordinator session."""
        normalized = str(serial).strip() if serial is not None else ""
        self._battery_pack_identity_overrides[parent_device_id, pack_index] = (
            normalized or None
        )

    def battery_pack_identity_serial(
        self,
        parent_device_id: str,
        pack_index: int,
    ) -> str | None:
        """Return the frozen or currently observed serial for one pack index."""
        identity = (parent_device_id, pack_index)
        if identity in self._battery_pack_identity_overrides:
            return self._battery_pack_identity_overrides[identity]
        serial = self.battery_pack_observed_serial(parent_device_id, pack_index)
        if serial is None:
            return None
        payload = (self.data or {}).get(parent_device_id)
        if not isinstance(payload, dict):
            return None
        ordered = sorted_battery_pack_payloads(payload.get(PAYLOAD_BATTERY_PACKS))
        serial_key = stable_subdevice_key("battery_pack", serial, pack_index)
        matching_serials = sum(
            stable_subdevice_key(
                "battery_pack",
                battery_pack_serial(pack),
                index,
            )
            == serial_key
            for index, pack in enumerate(ordered, start=1)
            if battery_pack_serial(pack) is not None
        )
        return serial if matching_serials == 1 else None

    def battery_pack_observed_serial(
        self,
        parent_device_id: str,
        pack_index: int,
    ) -> str | None:
        """Return the current payload serial without applying registry overrides."""
        if pack_index < 1:
            return None
        payload = (self.data or {}).get(parent_device_id)
        if not isinstance(payload, dict):
            return None
        packs = payload.get(PAYLOAD_BATTERY_PACKS)
        if not isinstance(packs, list):
            return None
        ordered = sorted_battery_pack_payloads(packs)
        try:
            return battery_pack_serial(ordered[pack_index - 1])
        except IndexError:
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

        registry = dr.async_get(self.hass)
        removed = 0
        # Snapshot the queue and clear it before iterating so a concurrent
        # merge cannot lose new entries appended during the await.
        pending = list(self._pending_device_removals)
        self._pending_device_removals.clear()
        deferred: list[tuple[str, str]] = []
        for identifier in pending:
            expected_serial = self._pending_battery_pack_removal_serials.pop(
                identifier,
                None,
            )
            try:  # ruff:ignore[too-many-statements-in-try-clause]
                device = None
                if expected_serial is not None:
                    _domain, unique_id = identifier
                    parent_device_id = unique_id.rsplit("_battery_pack_", 1)[0]
                    parent = registry.async_get_device(
                        identifiers={(DOMAIN, parent_device_id)}
                    )
                    if parent is not None:
                        device = next(
                            (
                                candidate
                                for candidate in registry.devices.values()
                                if candidate.via_device_id == parent.id
                                and candidate.serial_number == expected_serial
                                and self.entry.entry_id in candidate.config_entries
                            ),
                            None,
                        )
                else:
                    device = registry.async_get_device(identifiers={identifier})
                if device is not None:
                    self._remove_registry_entities_for_devices({device.id})
                    registry.async_update_device(
                        device_id=device.id,
                        remove_config_entry_id=self.entry.entry_id,
                    )
                    removed += 1
            except Exception as err:  # ruff:ignore[blind-except]  # isolate registry failures
                if expected_serial is not None:
                    self._pending_battery_pack_removal_serials[identifier] = (
                        expected_serial
                    )
                deferred.append(identifier)
                _LOGGER.debug(
                    "Jackery: registry cleanup for %s deferred independently: %s",
                    identifier[1],
                    err,
                )
                continue
            # Commit cache cleanup per successful identifier so a later failure
            # cannot discard work from the processed prefix.
            _domain, unique_id = identifier
            if "_battery_pack_" in unique_id:
                dev_id = unique_id.rsplit("_battery_pack_", 1)[0]
                self._slow_cache.pop(f"dev:{dev_id}", None)
                self._last_system_info_query.pop(dev_id, None)
                self._last_weather_plan_query.pop(dev_id, None)
                self._last_subdevice_query.pop(dev_id, None)
        for identifier in deferred:
            if identifier not in self._pending_device_removals:
                self._pending_device_removals.append(identifier)
        if removed:
            _LOGGER.info(
                "Jackery: unlinked %d stale accessory device(s) from this config entry",
                removed,
            )
        return removed

    async def _async_enrich_battery_pack_ota(
        self,
        device_id: str,
        packs: list[dict[str, Any]],
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
                _LOGGER.debug(
                    "Jackery battery-pack OTA metadata auth-rejected for %s; "
                    "using cached/default OTA metadata while live HTTP polling "
                    "remains authoritative: %s",
                    pack_sn,
                    exception_debug_message(res),
                )
                per_dev[cache_key] = (now, {})
                continue
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

    def _device_enrichment_cache_stale(self, device_id: str) -> bool:
        """Return True when a supplementary L5 enrichment cache went stale.

        Covers the Shelly Cloud realtime cache (TTL = fast poll interval) and
        the smart-plug / meter-head socket-statistic caches (TTL = slow-metric
        interval). The critical path serves these ``stale_ok=True``; this signal
        lets the background pass re-fetch them off the critical path so the
        stale value is not served indefinitely. Cold enrichment caches are
        seeded with expired empty values on the critical path, so the first real
        fetch also happens in the background.
        """
        now = time.monotonic()
        for suffix, ttl_sec in (
            (":shelly_cloud", self._configured_update_interval.total_seconds()),
            (":smart_plug", self._slow_metrics_interval_sec),
            (":meter_head", self._slow_metrics_interval_sec),
        ):
            per_dev = self._slow_cache.get(f"dev:{device_id}{suffix}")
            if not per_dev:
                continue
            if any(now - ts >= ttl_sec for ts, _ in per_dev.values()):
                return True
        return False

    def _schedule_battery_pack_ota_enrichment(self, device_id: str) -> None:
        """Refresh per-pack OTA metadata without blocking the poll cycle."""
        if self._shutdown_started:
            return
        if not self._battery_pack_ota_fetch_due(device_id):
            return
        task = self._battery_pack_ota_tasks.get(device_id)
        if task is not None and not task.done():
            return
        self._battery_pack_ota_tasks[device_id] = (
            self.hass.async_create_background_task(
                self._async_refresh_battery_pack_ota(device_id),
                name=f"{DOMAIN}_battery_pack_ota_{device_id}",
                eager_start=False,
            )
        )

    async def _async_refresh_battery_pack_ota(self, device_id: str) -> None:
        """Fetch per-pack OTA metadata and push a partial coordinator update."""
        try:  # ruff:ignore[too-many-statements-in-try-clause]
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
        except BACKGROUND_TASK_ERRORS as err:
            _LOGGER.debug("Jackery pack OTA background refresh failed: %s", err)
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
        return _merge_battery_pack_lifetime_from_ble_fn(updated, body)

    @staticmethod
    def _merge_pack_ota(pack: dict[str, Any], ota: dict[str, Any]) -> None:
        """Merge OTA metadata fields into a battery pack dict in-place."""
        _merge_pack_ota_fn(pack, ota)

    @staticmethod
    def _merge_battery_pack_ota_lists(
        current: Any,  # loose prior-state list, duck-typed via `current or []`  # ruff:ignore[any-type]
        ota_updates: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Merge static OTA fields into packs without touching last-seen state."""
        return _merge_battery_pack_ota_lists_fn(current, ota_updates)

    @classmethod
    def _smart_meter_accessory_device_id(cls, source: dict[str, Any]) -> str | None:
        """Return the app's subDeviceId for CT statistic endpoints."""
        return smart_meter_accessory_device_id(source)

    @classmethod
    def _has_smart_meter_accessory(cls, payload: dict[str, Any]) -> bool:
        """Return True when discovery metadata contains a CT/smart meter accessory."""
        return has_smart_meter_accessory(payload)

    @classmethod
    def _has_meter_head_accessory(cls, payload: dict[str, Any]) -> bool:
        """Return True when discovery or a prior MQTT reply mentions a meter head."""
        return has_meter_head_accessory(payload)

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
        return has_smart_plug_accessory(payload)

    @classmethod
    def _subdevice_identity_values(cls, item: Mapping[str, Any]) -> set[str]:
        """Return matching identities used across system-list and Shelly APIs."""
        return subdevice_identity_values(item)

    @classmethod
    def _entry_subdevice_candidates(
        cls,
        entry: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Return known accessory dictionaries for one coordinator entry."""
        return entry_subdevice_candidates(entry)

    @classmethod
    def _shelly_cloud_device_matches_entry(
        cls,
        entry: dict[str, Any],
        shelly_device: Mapping[str, Any],
    ) -> bool:
        """Return True when a Shelly Cloud device belongs to the entry."""
        return _shelly_cloud_device_matches_entry_fn(entry, shelly_device)

    def _merge_shelly_cloud_item(
        self,
        entry: dict[str, Any],
        source: Mapping[str, Any],
        *,
        fill_only: bool = False,
    ) -> bool:
        """Merge a Shelly Cloud device/realtime payload into CT or socket buckets."""
        return _merge_shelly_cloud_item_fn(
            entry,
            source,
            rejection_callback=self.record_schema_rejection,
            fill_only=fill_only,
        )

    @classmethod
    def _shelly_cloud_device_ids(cls, entry: dict[str, Any]) -> list[str]:
        """Return app Shelly Cloud device IDs known for this entry."""
        return _shelly_cloud_device_ids_fn(entry)

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
        return subdevice_stat_id(payload, subdevice, dev_type=dev_type)

    def _local_timezone(self) -> tzinfo:
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
        return _app_period_section_fn(prefix, date_type)

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
        props = merge_dict_values(props, clean_updates)
        entry[PAYLOAD_PROPERTIES] = props
        new_data[device_id] = entry
        self._push_partial_update(new_data)

    def _apply_local_system_patch(
        self,
        device_id: str,
        updates: dict[str, Any],
    ) -> None:
        """Mirror app system metadata writes into coordinator data."""
        if not updates or not self.data or device_id not in self.data:
            return
        self._patch_device_index_system_meta(device_id, updates)
        new_data = dict(self.data)
        entry = dict(new_data[device_id])
        system = dict(entry.get(PAYLOAD_SYSTEM) or {})
        system.update(updates)
        entry[PAYLOAD_SYSTEM] = system
        system_meta = dict(entry.get(PAYLOAD_SYSTEM_META) or {})
        if system_meta:
            system_meta.update(updates)
            entry[PAYLOAD_SYSTEM_META] = system_meta
        new_data[device_id] = entry
        self._push_partial_update(new_data)

    def _patch_device_index_system_meta(
        self,
        device_id: str,
        updates: dict[str, Any],
    ) -> None:
        """Persist accepted live system fields in the HTTP rebuild baseline."""
        index = self._device_index.get(device_id)
        if not isinstance(index, dict) or not updates:
            return
        system_meta = dict(index.get(PAYLOAD_SYSTEM_META) or {})
        system_meta.update(updates)
        index[PAYLOAD_SYSTEM_META] = system_meta

    def _apply_local_system_name_patch(self, system_id: str, new_name: str) -> None:
        """Mirror a system rename into every matching coordinator payload."""
        if not self.data:
            return
        updates: dict[str, dict[str, Any]] = {}
        for device_id, entry in self.data.items():
            matched = False
            next_entry = dict(entry)
            for section_name in (PAYLOAD_SYSTEM, PAYLOAD_SYSTEM_META):
                section = next_entry.get(section_name)
                if not isinstance(section, dict):
                    continue
                section_id = section.get(FIELD_ID) or section.get(FIELD_SYSTEM_ID)
                if str(section_id or "") != str(system_id):
                    continue
                next_section = dict(section)
                next_section[FIELD_SYSTEM_NAME] = new_name
                next_entry[section_name] = next_section
                matched = True
            if matched:
                updates[device_id] = next_entry
        if updates:
            new_data = dict(self.data)
            new_data.update(updates)
            self._push_partial_update(new_data)

    def _apply_local_device_name_patch(self, device_id: str, new_name: str) -> None:
        """Mirror a device rename into coordinator metadata and live payload."""
        if not self.data or device_id not in self.data:
            return
        index = self._device_index.get(device_id)
        if isinstance(index, dict):
            meta = dict(index.get(PAYLOAD_DEVICE_META) or {})
            if meta:
                meta[FIELD_DEVICE_NAME] = new_name
                index[PAYLOAD_DEVICE_META] = meta

        entry = dict(self.data[device_id])
        for section_name in (
            PAYLOAD_DEVICE,
            PAYLOAD_DISCOVERY,
            PAYLOAD_SYSTEM,
            PAYLOAD_SYSTEM_META,
        ):
            section = entry.get(section_name)
            if not isinstance(section, dict):
                continue
            next_section = dict(section)
            next_section[FIELD_DEVICE_NAME] = new_name
            entry[section_name] = next_section
        props = self._sanitize_main_properties(entry.get(PAYLOAD_PROPERTIES) or {})
        props[FIELD_WNAME] = new_name
        entry[PAYLOAD_PROPERTIES] = props
        new_data = dict(self.data)
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
        price = merge_present_dict_values(price, updates)
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
        weather = merge_present_dict_values(weather, updates)
        entry[PAYLOAD_WEATHER_PLAN] = weather
        new_data[device_id] = entry
        self._push_partial_update(new_data)

    def _schedule_ble_partial_update(
        self,
        device_id: str,
        updated_payload: dict[str, Any],
    ) -> None:
        """Coalesce rapid BLE updates for one device into one push."""
        if self._shutdown_started:
            return
        self._ble_pending_updates[device_id] = dict(updated_payload)
        task = self._ble_coalesce_tasks.get(device_id)
        if task is not None and not task.done():
            return
        self._ble_coalesce_tasks[device_id] = self.hass.async_create_background_task(
            self._async_flush_ble_partial_update(device_id),
            name=f"{DOMAIN}_ble_coalesce_{device_id}",
            eager_start=False,
        )

    def _ble_partial_update_base(self, device_id: str) -> dict[str, Any] | None:
        """Return the pending BLE snapshot before falling back to committed data."""
        pending = self._ble_pending_updates.get(device_id)
        if isinstance(pending, dict):
            return pending
        current = (self.data or {}).get(device_id)
        return current if isinstance(current, dict) else None

    async def _async_flush_ble_partial_update(self, device_id: str) -> None:
        """Flush the latest pending BLE payload for one device."""
        try:
            await asyncio.sleep(_BLE_PARTIAL_UPDATE_COALESCE_SEC)
            pending = self._ble_pending_updates.pop(device_id, None)
            if not isinstance(pending, dict):
                return
            current = (self.data or {}).get(device_id)
            if not isinstance(current, dict):
                return
            if current == pending:
                return
            new_data = dict(self.data or {})
            new_data[device_id] = pending
            self._push_partial_update(new_data)
        finally:
            task = self._ble_coalesce_tasks.get(device_id)
            if task is asyncio.current_task():
                self._ble_coalesce_tasks.pop(device_id, None)

    def _endpoint_backoff_active(self, key: str, now_monotonic: float) -> bool:
        """Return True when the endpoint key is currently in backoff."""
        if self._endpoint_backoff_is_energy_key(key):
            return False
        state = self._endpoint_backoff.get(key)
        if not isinstance(state, dict):
            return False
        until = safe_float(state.get("until")) or 0.0
        return until > now_monotonic

    def _endpoint_backoff_active_count(self, now_monotonic: float | None = None) -> int:
        """Return the number of slow HTTP endpoint keys currently in backoff."""
        now = time.monotonic() if now_monotonic is None else now_monotonic
        active_count = 0
        for key, state in self._endpoint_backoff.items():
            if self._endpoint_backoff_is_energy_key(key):
                continue
            until = safe_float(state.get("until")) or 0.0
            if until > now:
                active_count += 1
        return active_count

    @staticmethod
    def _endpoint_backoff_is_energy_key(key: str) -> bool:
        """Return True for stat/energy endpoint keys that must not be backed off."""
        return any(part in key for part in _ENDPOINT_BACKOFF_ENERGY_KEY_PARTS)

    @staticmethod
    def _endpoint_backoff_delays_for_key(key: str) -> tuple[int, ...]:
        """Return the retry ladder for an endpoint-backoff key.

        Energy/stat keys use a short ladder capped at two minutes so they retry
        regularly; every other key keeps the long escalating diagnostic ladder.
        """
        if JackerySolarVaultCoordinator._endpoint_backoff_is_energy_key(key):
            return _ENDPOINT_BACKOFF_ENERGY_DELAYS_SEC
        return _ENDPOINT_BACKOFF_DELAYS_SEC

    @staticmethod
    def _is_backoffable_timeout(backoff_key: str | None, err: JackeryError) -> bool:
        """Return True for a Shelly-realtime endpoint that timed out.

        Timeouts carry no cloud error code, so ``_endpoint_backoff_note_failure``
        never records them. Scope a timeout backoff strictly to the Shelly
        realtime enrichment key so a persistently unreachable third-party device
        stops being re-polled every cycle; the primary HTTP path and every other
        enrichment cache keep their normal retry cadence.
        """
        return (
            backoff_key is not None
            and backoff_key.startswith(_SHELLY_REALTIME_BACKOFF_PREFIX)
            and isinstance(err.__cause__, TimeoutError)
        )

    def _endpoint_backoff_note_failure(self, key: str, err: JackeryError) -> bool:
        """Record backoff state for known persistent cloud endpoint failures."""
        err_message = str(err)
        code_match = re.search(r"\bcode=(\d+)\b", err_message)
        code: int | None = None
        if code_match is not None:
            try:
                code = int(code_match.group(1))
            except TypeError, ValueError:
                code = None
        if code is None or code not in _ENDPOINT_BACKOFF_CODES:
            return False
        failure_code = code
        now_monotonic = time.monotonic()
        previous = self._endpoint_backoff.get(key)
        previous_level = -1
        if isinstance(previous, dict):
            previous_level_raw = safe_float(previous.get("level"))
            previous_level = (
                int(previous_level_raw) if previous_level_raw is not None else -1
            )
            previous_code_raw = safe_float(previous.get("code"))
            previous_code = (
                int(previous_code_raw) if previous_code_raw is not None else 0
            )
        else:
            previous_code = 0
        delays = self._endpoint_backoff_delays_for_key(key)
        if previous_code == failure_code and previous_level >= 0:
            level = min(previous_level + 1, len(delays) - 1)
        else:
            level = 0
        delay_sec = delays[level]
        self._endpoint_backoff[key] = {
            "code": failure_code,
            "level": level,
            "until": now_monotonic + delay_sec,
        }
        if previous is None:
            _LOGGER.debug(
                "Jackery endpoint backoff entered for %s (code=%d, delay=%ss)",
                key,
                failure_code,
                delay_sec,
            )
        return True

    def _endpoint_backoff_note_timeout(self, key: str) -> None:
        """Open or escalate a backoff window for a repeatedly timing-out key.

        Timeouts have no cloud code, so this tracks them with a dedicated ladder
        and a sentinel code, escalating like the code-based backoff. Recovery
        clears the window through the shared ``_endpoint_backoff_note_success``.
        """
        now_monotonic = time.monotonic()
        previous = self._endpoint_backoff.get(key)
        previous_level = -1
        if (
            isinstance(previous, dict)
            and int(safe_float(previous.get("code")) or 0)
            == _ENDPOINT_BACKOFF_TIMEOUT_CODE
        ):
            previous_level_raw = safe_float(previous.get("level"))
            previous_level = (
                int(previous_level_raw) if previous_level_raw is not None else -1
            )
        delays = _ENDPOINT_BACKOFF_TIMEOUT_DELAYS_SEC
        level = min(previous_level + 1, len(delays) - 1) if previous_level >= 0 else 0
        delay_sec = delays[level]
        self._endpoint_backoff[key] = {
            "code": _ENDPOINT_BACKOFF_TIMEOUT_CODE,
            "level": level,
            "until": now_monotonic + delay_sec,
        }
        if previous is None:
            _LOGGER.debug(
                "Jackery endpoint backoff entered for %s (timeout, delay=%ss)",
                key,
                delay_sec,
            )

    def _endpoint_backoff_note_success(self, key: str) -> None:
        """Clear endpoint backoff state after a successful fetch."""
        state = self._endpoint_backoff.pop(key, None)
        if isinstance(state, dict):
            code = int(safe_float(state.get("code")) or 0)
            _LOGGER.debug(
                "Jackery endpoint backoff recovered for %s (code=%d)",
                key,
                code,
            )

    def endpoint_backoff_diagnostics(self) -> dict[str, Any]:
        """Return active endpoint-backoff windows for diagnostics."""
        now_monotonic = time.monotonic()
        active: dict[str, dict[str, int]] = {}
        for key, state in self._endpoint_backoff.items():
            if self._endpoint_backoff_is_energy_key(key):
                continue
            until = safe_float(state.get("until")) or 0.0
            remaining_raw = until - now_monotonic
            if remaining_raw <= 0:
                continue
            remaining = int(remaining_raw)
            active[key] = {
                "code": int(safe_float(state.get("code")) or 0),
                "level": int(safe_float(state.get("level")) or 0),
                "remaining_seconds": remaining,
            }
        return {
            "active_count": len(active),
            "active": active,
            "delay_seconds": list(_ENDPOINT_BACKOFF_DELAYS_SEC),
        }

    def _merge_partial_device_update(
        self,
        device_id: str,
        current: dict[str, Any],
        incoming: dict[str, Any],
    ) -> dict[str, Any]:
        """Merge one partial update; live BLE/MQTT values win for live keys.

        AGENTS.md §1.2: local transports are preferred for live values.
        """
        merged = merge_present_dict_values(current, incoming)
        current_props = current.get(PAYLOAD_PROPERTIES)
        incoming_props = incoming.get(PAYLOAD_PROPERTIES)
        current_http_props = current.get(PAYLOAD_HTTP_PROPERTIES)
        incoming_http_props = incoming.get(PAYLOAD_HTTP_PROPERTIES)

        raw_http_props: dict[str, Any] = {}
        if isinstance(current_http_props, dict):
            raw_http_props = current_http_props
        if isinstance(incoming_http_props, dict):
            raw_http_props = merge_present_dict_values(
                raw_http_props,
                incoming_http_props,
            )
        if isinstance(current_http_props, dict) or isinstance(
            incoming_http_props,
            dict,
        ):
            merged[PAYLOAD_HTTP_PROPERTIES] = self._sanitize_main_properties(
                raw_http_props,
            )

        if not isinstance(current_props, dict) and not isinstance(
            incoming_props,
            dict,
        ):
            return merged

        guarded_props = self._sanitize_main_properties(
            current_props if isinstance(current_props, dict) else {},
        )
        clean_incoming_props = self._sanitize_main_properties(
            incoming_props if isinstance(incoming_props, dict) else {},
        )
        # Local (BLE/MQTT) live values are preferred for live keys (AGENTS.md
        # §1.2): merge incoming live props straight in — they win over the
        # current snapshot. The pristine HTTP payload stays in
        # PAYLOAD_HTTP_PROPERTIES (set above). Reverted 2026-07-18 — the old
        # fill-only strip subordinated live pushes to HTTP.
        supplemental_props = clean_incoming_props
        if supplemental_props:
            guarded_props = sync_property_aliases(
                merge_present_dict_values(guarded_props, supplemental_props),
                self._MAIN_PROPERTY_ALIAS_PAIRS,
            )
        overrides = self._active_property_overrides(device_id)
        if overrides:
            guarded_props = self._merge_main_properties(guarded_props, overrides)
        merged[PAYLOAD_PROPERTIES] = guarded_props
        return merged

    def _merge_concurrent_coordinator_updates(
        self,
        baseline: dict[str, dict[str, Any]],
        result: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Reapply pushes received while an HTTP refresh was awaiting I/O."""
        current_data: Mapping[str, object] = self.data or {}
        merged = dict(result)
        reapplied = 0
        for device_id, current in current_data.items():
            if not isinstance(current, dict):
                continue
            previous = baseline.get(device_id)
            delta = (
                changed_dict_values(previous, current)
                if isinstance(previous, dict)
                else copy.deepcopy(current)
            )
            if not delta:
                continue
            target = merged.get(device_id)
            merged[device_id] = self._merge_partial_device_update(
                device_id,
                target if isinstance(target, dict) else {},
                delta,
            )
            reapplied += 1
        if reapplied:
            _LOGGER.debug(
                "Jackery: reapplied concurrent live updates for %d device(s) "
                "after HTTP refresh",
                reapplied,
            )
        return merged

    def _push_partial_update(self, new_data: dict[str, dict[str, Any]]) -> None:
        """Push updated coordinator data through HA's coordinator mechanism."""
        if self._shutdown_started:
            return
        current_data = self.data or {}
        merged: dict[str, dict[str, Any]] = dict(current_data)
        for device_id, incoming in new_data.items():
            current = merged.get(device_id)
            merged[device_id] = self._merge_partial_device_update(
                device_id,
                current if isinstance(current, dict) else {},
                incoming,
            )

        if self.data == merged:
            return
        self.data = merged
        self.last_update_success = True
        self.last_update_exception = None
        if self._listeners:
            self.async_update_listeners()

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
        # Local-first (F6 2026-07-03): the query dispatch below is
        # BLE-first, so a live BLE transport is enough. Requiring a
        # connected CLOUD client here left every SystemBody sensor
        # Unknown whenever the broker rejected the session (rc=133 ban),
        # although BLE could answer — the fields have NO HTTP source
        # (DeviceDetailApi has no SystemBean variant; smali-verified).
        mqtt_ready = self._mqtt is not None and self._mqtt.is_connected
        if self._ble_listener is None and not mqtt_ready:
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
                        device_id,
                        ensure_mqtt=ensure_mqtt,
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
                        device_id,
                        ensure_mqtt=ensure_mqtt,
                    )
                except ConfigEntryAuthFailed:
                    raise
                except (TimeoutError, HomeAssistantError, JackeryError) as err:
                    _LOGGER.debug(
                        "Jackery system-info query failed for %s: %s",
                        device_id,
                        err,
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
            except ConfigEntryAuthFailed:
                raise
            except (TimeoutError, HomeAssistantError, JackeryError) as err:
                _LOGGER.debug(
                    "Jackery weather-plan query failed for %s: %s",
                    device_id,
                    err,
                )

    @staticmethod
    def _coerce_transport_cmd(cmd: Any) -> int:  # ruff:ignore[any-type]
        """Coerce transport cmd input to an integer.

        Accepts plain ints plus integral numeric strings (e.g. ``"107"``,
        ``"107.0"``). Rejects booleans, NaN/inf and non-integral values.
        """
        if isinstance(cmd, bool):
            msg = "cmd must be an integer"
            raise ValueError(msg)  # ruff:ignore[type-check-without-type-error]
        if isinstance(cmd, int):
            return cmd
        if isinstance(cmd, float):
            if not math.isfinite(cmd) or not cmd.is_integer():
                msg = "cmd must be an integer"
                raise ValueError(msg)
            return int(cmd)
        if isinstance(cmd, str):
            text = cmd.strip()
            if not text:
                msg = "cmd must be an integer"
                raise ValueError(msg)
            try:
                return int(text, 10)
            except ValueError as err:
                _LOGGER.debug(
                    "cmd %r is not a base-10 integer, trying float parse: %s",
                    text,
                    err,
                )
            try:
                parsed = float(text)
            except ValueError as err:
                _LOGGER.debug("cmd %r is not a parseable float: %s", text, err)
            else:
                if math.isfinite(parsed) and parsed.is_integer():
                    return int(parsed)
            msg = "cmd must be an integer"
            raise ValueError(msg)
        try:
            return int(cmd)
        except (TypeError, ValueError) as err:
            msg = "cmd must be an integer"
            raise ValueError(msg) from err

    @staticmethod
    def _command_body_for_transport(
        body_fields: dict[str, Any],
        *,
        cmd: object,
    ) -> dict[str, Any]:
        """Build the command body shared by MQTT and BLE command transports."""
        body: dict[str, Any] = dict(body_fields)
        cmd_value = JackerySolarVaultCoordinator._coerce_transport_cmd(cmd)
        if cmd_value > 0:
            body[FIELD_CMD] = cmd_value
        return body

    async def _async_publish_command_ble_first(  # ruff:ignore[too-many-arguments]
        self,
        device_id: str,
        *,
        message_type: str,
        action_id: int,
        cmd: int,
        body_fields: dict[str, Any],
        ble_extra_body_fields: dict[str, Any] | None = None,
        ensure_mqtt: bool = True,
    ) -> None:
        """Try the BLE write path before falling back to MQTT.

        BLE writes are issued with ``wait_for_ack=True`` so a silent
        firmware drop (e.g. unknown cmd, missing CRC trailer) raises a
        ``RuntimeError`` after a short window and the caller cleanly
        falls back to the cloud-MQTT pipeline. Setters in this router
        are idempotent, so the worst case of a duplicated write is
        another state-toggle write — preferable to silently swallowing
        the command.
        """
        cmd_value = self._coerce_transport_cmd(cmd)
        ble_error: Exception | None = None

        portable_ble_type = PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID.get(action_id)
        ble_supported = (
            not isinstance(action_id, bool)
            and (
                (action_id, cmd_value) in _HOME_BLE_COMMAND_PAIRS
                or portable_ble_type == cmd_value
            )
            and cmd_value not in _BLE_FIRST_UNSUPPORTED_MSG_TYPES
        )
        if ble_supported:
            try:
                ble_body_fields = body_fields
                if ble_extra_body_fields is not None:
                    ble_body_fields = {**body_fields, **ble_extra_body_fields}
                sent = await self.async_send_ble_command(
                    device_id,
                    cmd=cmd_value,
                    flags=action_id,
                    body=self._command_body_for_transport(
                        ble_body_fields,
                        cmd=cmd_value,
                    ),
                    wait_for_ack=True,
                    connect_timeout_sec=BLE_COMMAND_CONNECT_TIMEOUT_SEC,
                )
            except ACTION_WRITE_ERRORS as err:
                ble_error = err
                _LOGGER.debug(
                    "Jackery BLE command failed for %s actionId=%s cmd=%s: %s",
                    device_id,
                    action_id,
                    cmd_value,
                    err,
                )
            else:
                if sent:
                    return
                _LOGGER.debug(
                    "Jackery BLE command unavailable for %s actionId=%s cmd=%s",
                    device_id,
                    action_id,
                    cmd_value,
                )
        try:
            await self._async_publish_command(
                device_id,
                message_type=message_type,
                action_id=action_id,
                cmd=cmd_value,
                body_fields=body_fields,
                ensure_mqtt=ensure_mqtt,
            )
        except BACKGROUND_TASK_ERRORS as mqtt_err:
            if ble_error is not None:
                _LOGGER.warning(
                    "Jackery MQTT fallback also failed for %s actionId=%s cmd=%s: "
                    "BLE=%s MQTT=%s",
                    device_id,
                    action_id,
                    cmd_value,
                    ble_error,
                    mqtt_err,
                )
            raise

    async def _async_publish_command(  # ruff:ignore[too-many-branches, too-many-arguments, too-many-statements]
        self,
        device_id: str,
        *,
        message_type: str,
        action_id: int,
        cmd: int,
        body_fields: dict[str, Any],
        ensure_mqtt: bool = True,
    ) -> None:
        """Publish an MQTT command through the coordinator-owned command path."""
        if self._mqtt is None and ensure_mqtt:
            # A foreground entity command may run before the deferred Layer-5
            # startup completed (or after supplemental transport cleanup).
            # Build the cloud client on demand; _async_ensure_mqtt cannot do so
            # itself because it deliberately owns connection state only.
            await self.async_start_mqtt()
        if self._mqtt is None:
            msg = "MQTT client not initialized"
            raise HomeAssistantError(msg)

        device_sn = self._resolve_device_sn(device_id)
        if not device_sn:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="mqtt_missing_device_sn",
                translation_placeholders={"device_id": str(device_id)},
            )

        async def _ensure() -> None:
            if not ensure_mqtt:
                return
            await self._async_ensure_mqtt(
                force=not (self._mqtt is not None and self._mqtt.is_connected),
                wait_connected=True,
            )

        async def _stop_mqtt() -> None:
            if self._mqtt is not None:
                await self._mqtt.async_stop()

        # No auth handling here: the MQTT command path is transport-only and
        # never triggers login/reauth (owner invariant 2026-07-05). A missing
        # cached session surfaces as a plain HomeAssistantError from
        # this method; the HTTP/API login path owns credentials.
        await _ensure()
        creds = self.api.get_cached_mqtt_credentials()
        if creds is None:
            msg = (
                "Jackery MQTT credentials are not available yet; the HTTP login "
                "session has not been cached"
            )
            raise HomeAssistantError(msg)

        user_id = creds[MQTT_CREDENTIAL_USER_ID]
        topic = f"{MQTT_TOPIC_PREFIX}/{user_id}/{MQTT_TOPIC_COMMAND}"
        timestamp_ms = int(time.time() * 1000)
        body = self._command_body_for_transport(body_fields, cmd=cmd)
        bt_key = self.device_bluetooth_key(device_id)

        payload_body: str
        if bt_key is not None and len(bt_key) == 16:  # ruff:ignore[magic-value-comparison]
            try:
                payload_body = encrypt_mqtt_body(body, bt_key)
            except (ValueError, TypeError) as err:
                _LOGGER.warning(
                    "Jackery MQTT Layer C encrypt failed for %s, sending plaintext: %s",
                    device_id,
                    err,
                )
                payload_body = json.dumps(
                    body,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
        else:
            payload_body = json.dumps(body, separators=(",", ":"), ensure_ascii=False)

        payload = {
            FIELD_DEVICE_SN: device_sn,
            "id": timestamp_ms,
            FIELD_VERSION: 0,
            FIELD_MESSAGE_TYPE: message_type,
            FIELD_ACTION_ID: action_id,
            FIELD_TIMESTAMP: timestamp_ms,
            FIELD_BODY: payload_body,
        }

        last_err: Exception | None = None
        for attempt in range(2):
            try:  # ruff:ignore[too-many-statements-in-try-clause]
                mqtt = self._mqtt
                if mqtt is not None and not mqtt.is_connected:
                    await _ensure()
                    mqtt = self._mqtt
                if mqtt is None or not mqtt.is_connected:
                    msg = "MQTT client is not connected"
                    raise RuntimeError(msg)  # ruff:ignore[raise-within-try]
                await mqtt.async_publish_json(topic, payload, qos=0, retain=False)
                _LOGGER.debug(
                    "Jackery MQTT TX %s: messageType=%s actionId=%s cmd=%s",
                    device_id,
                    message_type,
                    action_id,
                    cmd,
                )
                break
            except RuntimeError as err:
                last_err = err
                if attempt == 0:
                    await _stop_mqtt()
                    continue
        else:
            mqtt = self._mqtt
            mqtt_last_error = mqtt.diagnostics.get("last_error") if mqtt else None
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="mqtt_command_failed",
                translation_placeholders={
                    "error": str(last_err) if last_err else "unknown",
                    "mqtt_last_error": (
                        str(mqtt_last_error) if mqtt_last_error else "n/a"
                    ),
                },
            ) from last_err

        # TX record in the payload-debug JSONL so outbound commands can be
        # correlated with device ACK/property frames; the JSONL otherwise
        # only carries RX events (B-button finding 2026-07-03).
        await self._async_payload_debug_event(
            lambda: {
                "kind": "mqtt_tx",
                "device_id": device_id,
                "payload": {
                    FIELD_DEVICE_SN: device_sn,
                    "messageType": message_type,
                    "actionId": action_id,
                    "cmd": cmd,
                },
            },
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

    async def async_set_system_name(self, system_id: str, new_name: str) -> None:
        """Rename a Jackery system through the primary HTTP API."""
        ok = await self.api.async_set_system_name(system_id, new_name)
        if not ok:
            msg = "server returned false"
            raise JackeryError(msg)
        self._apply_local_system_name_patch(system_id, new_name)
        await self.async_request_refresh()
        self._apply_local_system_name_patch(system_id, new_name)

    async def async_set_pv_name(self, device_id: str, index: int, name: str) -> None:
        """Rename a PV input on a specific device via HTTP and patch locally.

        The 0-based ``index`` selects the ``pv<n>`` properties block. The device
        is resolved from ``device_id`` (its own meta/discovery) rather than by
        scanning for the first Home that exposes the channel, so renames stay
        correct on multi-device accounts.
        """
        field = _PV_CHANNEL_FIELDS[index]
        device_sn = self._resolve_device_sn(device_id)
        if not device_sn:
            msg = "missing deviceSn"
            raise JackeryError(msg)
        await self.api.async_modify_pv_name(
            device_sn=device_sn,
            index=index,
            name=name,
        )
        self._apply_local_pv_name_patch(device_id, field, name)
        await self.async_request_refresh()
        self._apply_local_pv_name_patch(device_id, field, name)

    def _apply_local_pv_name_patch(
        self,
        device_id: str,
        field: str,
        name: str,
    ) -> None:
        """Mirror a PV-input rename into the device's live properties.

        The echo key is unconfirmed in source-of-truth, so the value is stored
        optimistically under ``FIELD_PV_NAME`` (live-verify assumption).
        """
        if not self.data or device_id not in self.data:
            return
        entry = dict(self.data[device_id])
        props = dict(entry.get(PAYLOAD_PROPERTIES) or {})
        channel = props.get(field)
        if not isinstance(channel, dict):
            return
        next_channel = dict(channel)
        next_channel[FIELD_PV_NAME] = name
        props[field] = next_channel
        entry[PAYLOAD_PROPERTIES] = props
        new_data = dict(self.data)
        new_data[device_id] = entry
        self._push_partial_update(new_data)

    async def async_refresh_subdevices(
        self,
        *,
        context_device_id: str | None = None,
    ) -> None:
        """Refresh cloud accessory discovery and then refresh coordinator data."""
        if context_device_id is not None:
            self._require_home_config_context(context_device_id, "refresh subdevices")
        await self.api.async_sync_smart_accessories()
        await self.async_request_refresh()

    async def async_set_device_nickname(
        self,
        device_id: str,
        nickname: str,
    ) -> None:
        """Set a Jackery device nickname through the primary HTTP API and refresh."""
        await self.api.async_set_device_nickname(device_id, nickname)
        self._apply_local_device_name_patch(device_id, nickname)
        await self.async_request_refresh()
        self._apply_local_device_name_patch(device_id, nickname)

    async def async_unbind_device(self, device_id: str) -> None:
        """Unbind a Jackery device through the primary HTTP API and refresh."""
        await self.api.async_unbind_device(device_id)
        await self.async_request_refresh()

    async def async_unbind_accessories(
        self,
        bind_ids: Sequence[str],
    ) -> dict[str, Any]:
        """Unbind smart accessories by bind id and refresh the device list.

        Calls ``/v1/device/accessories/unbind`` (POST, app 2.4.0).
        """
        result = await self.api.async_unbind_accessories(bind_ids=bind_ids)
        await self.async_request_refresh()
        return result

    async def async_set_ac_nickname(
        self,
        device_id: str,
        *,
        ac_port: int,
        name: str,
    ) -> None:
        """Rename one AC output port of a portable device and refresh.

        Calls ``/v1/device/property/updateAcNickName`` (POST, app 2.4.0).
        """
        device_sn = self._resolve_device_sn(device_id)
        if not device_sn:
            msg = "missing deviceSn"
            raise JackeryError(msg)
        await self.api.async_set_ac_nickname(
            device_sn=device_sn,
            ac_port=ac_port,
            name=name,
        )
        await self.async_request_refresh()

    async def async_report_device_timezone(
        self,
        device_id: str,
        *,
        zone_id: str,
        time_offset: int,
    ) -> dict[str, Any]:
        """Report a device's timezone to the Jackery cloud (app parity).

        Calls ``/v1/device/timezone`` (POST, app 2.4.0). Pure report — no
        payload refresh is required.
        """
        return await self.api.async_report_device_timezone(
            device_id=device_id,
            zone_id=zone_id,
            time_offset=time_offset,
        )

    async def async_bind_device(
        self,
        *,
        bind_key: int,
        dev_id: str,
        guid: str,
        timezone_offset: int,
    ) -> None:
        """Bind a Jackery device through the primary HTTP API and refresh."""
        await self.api.async_bind_device(
            bind_key=bind_key,
            dev_id=dev_id,
            guid=guid,
            timezone_offset=timezone_offset,
        )
        await self.async_request_refresh()

    async def async_get_share_qr_code(self) -> dict[str, Any]:
        """Return the account share QR-code envelope from the primary HTTP API."""
        return await self.api.async_get_qr_code()

    async def async_list_shared_devices(self) -> list[Any]:
        """Return devices shared with this Jackery account."""
        return await self.api.async_get_device_shared_list()

    async def async_list_shared_managers(
        self,
        *,
        bind_user_id: str,
        level: int,
    ) -> list[Any]:
        """Return managers for a Jackery shared-device binding."""
        return await self.api.async_get_device_shared_managers(
            bind_user_id=bind_user_id,
            level=level,
        )

    async def async_remove_shared_access(
        self,
        *,
        bind_user_id: str,
        device_id: str,
    ) -> None:
        """Remove one shared-access binding through the primary HTTP API."""
        await self.api.async_remove_shared_access(
            bind_user_id=bind_user_id,
            device_id=device_id,
        )
        await self.async_request_refresh()

    async def async_remove_all_shared_access(
        self,
        *,
        bind_user_id: str,
        level: int,
    ) -> None:
        """Remove all shared-access bindings for a user and level."""
        await self.api.async_remove_all_shared_access(
            bind_user_id=bind_user_id,
            level=level,
        )
        await self.async_request_refresh()

    async def async_accept_shared_device(
        self,
        *,
        dev_id: str,
        qr_code_id: str,
    ) -> None:
        """Accept a shared Jackery device through the primary HTTP API."""
        await self.api.async_accept_shared_device(
            dev_id=dev_id,
            qr_code_id=qr_code_id,
        )
        await self.async_request_refresh()

    async def async_get_shelly_auth_url(self) -> dict[str, Any]:
        """Return the Jackery-owned Shelly OAuth authorization payload."""
        return await self.api.async_get_shelly_auth_url()

    async def async_get_shelly_devices(self) -> list[dict[str, Any]]:
        """Return Shelly Cloud devices linked to this Jackery account."""
        return await self.api.async_get_shelly_devices()

    async def async_get_shelly_binding_failures(
        self,
        state: str = "",
    ) -> dict[str, Any]:
        """Return Shelly binding failures from the primary HTTP API."""
        return await self.api.async_get_shelly_binding_failures(state=state)

    async def async_unbind_shelly_device(
        self,
        *,
        binding_id: str,
        shelly_device_id: str,
    ) -> bool:
        """Unbind one Shelly device through the primary HTTP API."""
        accepted = await self.api.async_unbind_shelly_device(
            binding_id=binding_id,
            device_id=shelly_device_id,
        )
        if accepted:
            await self.async_request_refresh()
        return accepted

    async def async_unbind_shelly_account(self) -> bool:
        """Unbind the Shelly account through the primary HTTP API."""
        accepted = await self.api.async_unbind_shelly_account()
        if accepted:
            await self.async_request_refresh()
        return accepted

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
            msg = "Cannot set SOC limits without charge_limit or discharge_limit"
            raise UpdateFailed(
                msg,
            )
        current = ((self.data or {}).get(device_id, {}) or {}).get(
            PAYLOAD_PROPERTIES,
            {},
        ) or {}

        def _soc_limit(value: object) -> int | None:
            parsed = safe_int(value)
            if parsed is None or parsed < 0 or parsed > 100:  # ruff:ignore[magic-value-comparison]
                return None
            return parsed

        def _current_soc_limit(primary: str, legacy: str, default: int) -> int:
            for raw in (current.get(primary), current.get(legacy)):
                parsed = _soc_limit(raw)
                if parsed is not None:
                    return parsed
            return default

        chg = (
            _soc_limit(charge_limit)
            if charge_limit is not None
            else _current_soc_limit(FIELD_SOC_CHG_LIMIT, FIELD_SOC_CHARGE_LIMIT, 100)
        )
        dis = (
            _soc_limit(discharge_limit)
            if discharge_limit is not None
            else _current_soc_limit(
                FIELD_SOC_DISCHG_LIMIT,
                FIELD_SOC_DISCHARGE_LIMIT,
                0,
            )
        )
        if chg is None or dis is None:
            msg = "Invalid SOC limit"
            raise UpdateFailed(msg)
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
            device_id,
            {FIELD_MAX_FEED_GRID: value, FIELD_MAX_GRID_STD_PW: value},
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
            device_id,
            {FIELD_WPC: value, FIELD_MINS_INTERVAL: value},
        )
        self._apply_local_weather_plan_patch(
            device_id,
            {FIELD_WPC: value, FIELD_MINS_INTERVAL: value},
        )

    async def async_delete_storm_alert(self, device_id: str, alert_id: str) -> None:
        """Delete storm alert."""
        await self._async_publish_command(
            device_id,
            message_type=MQTT_MESSAGE_CANCEL_WEATHER_ALERT,
            action_id=ACTION_ID_DELETE_STORM_ALERT,
            cmd=MQTT_CMD_NONE,
            body_fields={FIELD_ALERT_ID: alert_id},
        )
        self._apply_local_storm_alert_delete_patch(device_id, alert_id)

    def _apply_local_storm_alert_delete_patch(
        self,
        device_id: str,
        alert_id: str,
    ) -> None:
        """Remove a storm alert locally after the app-style delete command."""
        if not self.data or device_id not in self.data:
            return
        payload = dict(self.data[device_id])
        weather_plan = dict(payload.get(PAYLOAD_WEATHER_PLAN) or {})
        storm = weather_plan.get(FIELD_STORM)
        if not isinstance(storm, list):
            return
        updated = [
            item
            for item in storm
            if not (
                isinstance(item, dict)
                and str(item.get(FIELD_ALERT_ID) or "") == str(alert_id)
            )
        ]
        if len(updated) == len(storm):
            return
        weather_plan[FIELD_STORM] = updated
        payload[PAYLOAD_WEATHER_PLAN] = weather_plan
        new_data = dict(self.data)
        new_data[device_id] = payload
        self._push_partial_update(new_data)

    async def async_update_storm_alert_location(
        self,
        device_id: str,
        latitude: float,
        longitude: float,
    ) -> None:
        """Update storm-alert GPS coordinates via HTTP and patch locally.

        Unlike the MQTT-published storm-alert delete, this is a direct HTTP PUT
        to ``device/location``; it mirrors the system-rename write/patch/refresh
        shape so dependent entities reflect the new location immediately.
        """
        await self.api.async_update_location(
            device_id=device_id,
            latitude=latitude,
            longitude=longitude,
        )
        self._apply_local_location_patch(device_id, latitude, longitude)
        await self.async_request_refresh()
        self._apply_local_location_patch(device_id, latitude, longitude)

    def _apply_local_location_patch(
        self,
        device_id: str,
        latitude: float,
        longitude: float,
    ) -> None:
        """Mirror a location update into the device's cached location block."""
        if not self.data or device_id not in self.data:
            return
        payload = dict(self.data[device_id])
        location = dict(payload.get(PAYLOAD_LOCATION) or {})
        location[FIELD_LATITUDE] = latitude
        location[FIELD_LONGITUDE] = longitude
        payload[PAYLOAD_LOCATION] = location
        new_data = dict(self.data)
        new_data[device_id] = payload
        self._push_partial_update(new_data)

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
        self._require_home_config_context(device_id, "set single tariff")
        price = safe_float(price_value)
        if price is None or not math.isfinite(price) or price < 0:
            msg = f"Cannot set single tariff for {device_id}: invalid singlePrice"
            raise HomeAssistantError(msg)
        system_id = self._resolve_system_id(device_id)
        if not system_id:
            msg = f"Cannot set single tariff for {device_id}: missing systemId"
            raise UpdateFailed(
                msg,
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
            success = await self.api.async_set_single_mode(
                system_id=system_id,
                single_price=price,
                currency=str(currency),
            )
        except JackeryAuthError as err:
            _raise_config_entry_auth_failed(
                "Jackery credentials were rejected while saving the single tariff",
                err,
            )
        if not success:
            msg = (
                f"Cannot set single tariff for {device_id}: API rejected single tariff"
            )
            raise HomeAssistantError(msg)
        self._invalidate_system_cache(system_id, PAYLOAD_PRICE)
        self._apply_local_price_patch(
            device_id,
            {
                FIELD_DYNAMIC_OR_SINGLE: 2,
                FIELD_SINGLE_PRICE: round(price, 4),
            },
        )

    async def async_set_price_mode_single(self, device_id: str) -> None:
        """Set price mode single."""
        self._require_home_config_context(device_id, "set single tariff mode")
        current = ((self.data or {}).get(device_id, {}) or {}).get(PAYLOAD_PRICE) or {}
        single_price = current.get(FIELD_SINGLE_PRICE)
        if single_price is None:
            system_id = self._resolve_system_id(device_id)
            if not system_id:
                msg = (
                    f"Cannot switch to single tariff for {device_id}: missing systemId"
                )
                raise HomeAssistantError(
                    msg,
                )
            try:
                latest = await self.api.async_get_power_price(system_id)
            except JackeryAuthError as err:
                _raise_config_entry_auth_failed(
                    "Jackery credentials were rejected"
                    " while reading the current tariff",
                    err,
                )
            except JackeryError as err:
                msg = f"Cannot switch to single tariff for {device_id}: {err}"
                raise HomeAssistantError(
                    msg,
                ) from err
            if isinstance(latest, dict):
                single_price = latest.get(FIELD_SINGLE_PRICE)
        if single_price is None:
            msg = f"Cannot switch to single tariff for {device_id}: missing singlePrice"
            raise HomeAssistantError(
                msg,
            )
        price = safe_float(single_price)
        if price is None or not math.isfinite(price):
            msg = f"Cannot switch to single tariff for {device_id}: invalid singlePrice"
            raise HomeAssistantError(msg)
        await self.async_set_single_price(device_id, price)

    @staticmethod
    def _valid_price_sources(sources: object) -> list[dict[str, Any]]:
        return valid_price_sources(sources)

    async def _async_price_sources_for_device(
        self,
        device_id: str,
    ) -> list[dict[str, Any]]:
        self._require_home_config_context(device_id, "read dynamic price sources")
        payload = (self.data or {}).get(device_id, {}) or {}
        sources = self._valid_price_sources(payload.get(PAYLOAD_PRICE_SOURCES))
        if sources:
            return sources

        system_id = self._resolve_system_id(device_id)
        if not system_id:
            return []
        try:
            sources = self._valid_price_sources(
                await self.api.async_get_price_sources(system_id),
            )
        except JackeryAuthError as err:
            _raise_config_entry_auth_failed(
                "Jackery credentials were rejected while reading price sources",
                err,
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
        self,
        device_id: str,
        source: dict[str, Any],
    ) -> str | None:
        regions = normalized_source_regions(source)
        if not regions:
            return None
        country = self._device_country_code(device_id)
        if country:
            for region in regions:
                if region == country:
                    return region
        return regions[0]

    def _find_matching_price_source(
        self,
        device_id: str,
        sources: list[dict[str, Any]],
        current: dict[str, Any],
    ) -> dict[str, Any] | None:
        company_id = normalized_company_id(current.get(FIELD_PLATFORM_COMPANY_ID))
        if company_id is None:
            return None
        region = normalized_region(current.get(FIELD_SYSTEM_REGION))
        country = self._device_country_code(device_id)
        matches = [
            source
            for source in sources
            if normalized_company_id(source.get(FIELD_PLATFORM_COMPANY_ID))
            == company_id
        ]
        if not matches:
            return None
        if region is not None:
            for source in matches:
                if region in normalized_source_regions(source):
                    return source
        if country:
            for source in matches:
                if country in normalized_source_regions(source):
                    return source
        return matches[0] if len(matches) == 1 else None

    async def async_set_price_source(
        self,
        device_id: str,
        source: dict[str, Any],
    ) -> None:
        """Select a dynamic-price provider via the app's saveDynamicMode API."""
        self._require_home_config_context(device_id, "set dynamic tariff provider")
        system_id = self._resolve_system_id(device_id)
        if not system_id:
            msg = f"Cannot set dynamic tariff for {device_id}: missing systemId"
            raise HomeAssistantError(
                msg,
            )

        region = self._source_region_for_device(device_id, source)
        company_id_int = normalized_company_id(source.get(FIELD_PLATFORM_COMPANY_ID))
        if company_id_int is None or not region:
            msg = (
                "Cannot set dynamic tariff: selected provider is missing "
                "platformCompanyId/country."
            )
            raise HomeAssistantError(
                msg,
            )

        try:
            success = await self.api.async_set_dynamic_mode(
                system_id=system_id,
                platform_company_id=company_id_int,
                system_region=region,
            )
        except JackeryAuthError as err:
            _raise_config_entry_auth_failed(
                "Jackery credentials were rejected while saving the dynamic tariff",
                err,
            )
        if not success:
            msg = (
                f"Cannot set dynamic tariff for {device_id}: "
                "API rejected dynamic tariff"
            )
            raise HomeAssistantError(msg)
        self._invalidate_system_cache(system_id, PAYLOAD_PRICE)
        self._apply_local_price_patch(
            device_id,
            {
                FIELD_DYNAMIC_OR_SINGLE: 1,
                FIELD_PLATFORM_COMPANY_ID: company_id_int,
                FIELD_SYSTEM_REGION: region,
                FIELD_COMPANY_NAME: first_nonblank_source_name(
                    source,
                    FIELD_COMPANY_NAME,
                    FIELD_NAME,
                ),
                FIELD_POWER_PRICE_RESOURCE: source.get(FIELD_CID),
                FIELD_LOGIN_ALLOWED: source.get(FIELD_LOGIN_ALLOWED),
            },
        )

    async def async_set_price_mode_dynamic(self, device_id: str) -> None:
        """Set price mode dynamic."""
        self._require_home_config_context(device_id, "set dynamic tariff mode")
        system_id = self._resolve_system_id(device_id)
        if not system_id:
            msg = f"Cannot set dynamic tariff for {device_id}: missing systemId"
            raise HomeAssistantError(
                msg,
            )
        current = ((self.data or {}).get(device_id, {}) or {}).get(PAYLOAD_PRICE) or {}
        company_id_int = normalized_company_id(current.get(FIELD_PLATFORM_COMPANY_ID))
        region = normalized_region(current.get(FIELD_SYSTEM_REGION))
        if company_id_int is None or region is None:
            sources = await self._async_price_sources_for_device(device_id)
            source = self._find_matching_price_source(device_id, sources, current)
            if source is not None:
                await self.async_set_price_source(device_id, source)
                return
            if len(sources) == 1:
                await self.async_set_price_source(device_id, sources[0])
                return
            msg = (
                "Dynamic tariff requires provider selection. Use the "
                "'Electricity price provider' select entity first."
            )
            raise HomeAssistantError(
                msg,
            )
        try:
            success = await self.api.async_set_dynamic_mode(
                system_id=system_id,
                platform_company_id=company_id_int,
                system_region=region,
            )
        except JackeryAuthError as err:
            _raise_config_entry_auth_failed(
                "Jackery credentials were rejected while saving the dynamic tariff",
                err,
            )
        if not success:
            msg = (
                f"Cannot set dynamic tariff for {device_id}: "
                "API rejected dynamic tariff"
            )
            raise HomeAssistantError(msg)
        self._invalidate_system_cache(system_id, PAYLOAD_PRICE)
        self._apply_local_price_patch(
            device_id,
            {
                FIELD_DYNAMIC_OR_SINGLE: 1,
                FIELD_PLATFORM_COMPANY_ID: company_id_int,
                FIELD_SYSTEM_REGION: region,
            },
        )

    async def async_query_system_info(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
    ) -> None:
        """Query system info."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_COMBINE_DATA,
            action_id=ACTION_ID_QUERY_COMBINE_DATA,
            cmd=MQTT_CMD_QUERY_COMBINE_DATA,
            body_fields={},
            ensure_mqtt=ensure_mqtt,
        )

    @classmethod
    def _http_shadow_candidates(
        cls,
        entry: dict[str, Any],
        dev_type: int,
    ) -> list[tuple[str, int]]:
        """Return known accessory serials safe for one documented subShadow read."""
        candidates = [
            accessory
            for accessory in cls._entry_accessories(entry)
            if subdevice_dev_type(accessory) == dev_type
        ]
        bucket = cls._SHADOW_DEV_TYPE_BUCKETS.get(dev_type)
        bucket_value = entry.get(bucket) if bucket is not None else None
        if isinstance(bucket_value, dict):
            candidates.append(bucket_value)
        elif isinstance(bucket_value, list):
            candidates.extend(
                item
                for item in bucket_value
                if isinstance(item, dict)
                and (
                    dev_type != SUBDEVICE_DEV_TYPE_COMBO
                    or subdevice_dev_type(item) == dev_type
                )
            )

        unique: dict[str, tuple[str, int]] = {}
        for candidate in candidates:
            serial = subdevice_serial(candidate)
            if serial is not None:
                unique.setdefault(serial, (serial, dev_type))
        return list(unique.values())

    @classmethod
    def _documented_http_shadow_response_recognized(
        cls,
        response: dict[str, Any],
        *,
        kind: str,
        dev_type: int | None,
    ) -> bool:
        """Return whether a shadow response matches its documented payload family."""
        recognized = False
        if kind == "system_shadow":
            recognized = any(
                cls._property_value_present(response.get(key))
                for key in cls._SYSTEM_INFO_KEYS | cls._SUBDEVICE_MAIN_MIRROR_KEYS
            )
        elif dev_type == SUBDEVICE_DEV_TYPE_BATTERY_PACK:
            recognized = bool(cls._battery_packs_from_source(response))
        elif dev_type == SUBDEVICE_DEV_TYPE_CT:
            recognized = (
                cls._find_dict_with_any_key(response, cls._CT_METER_KEYS) is not None
            )
        elif dev_type == SUBDEVICE_DEV_TYPE_METER_HEAD:
            collectors = response.get(FIELD_COLLECTORS)
            recognized = isinstance(collectors, list) and bool(collectors)
        elif dev_type == SUBDEVICE_DEV_TYPE_SOCKET:
            plugs = response.get(FIELD_PLUGS)
            recognized = isinstance(plugs, list) and bool(plugs)
        elif dev_type == SUBDEVICE_DEV_TYPE_COMBO:
            sub_devices = response.get(FIELD_SUB_DEVICE)
            recognized = isinstance(sub_devices, list) and bool(sub_devices)
        return recognized

    async def async_refresh_documented_http_read(  # ruff:ignore[complex-structure,too-many-branches,too-many-locals,too-many-statements]
        self,
        device_id: str,
        *,
        device_property: bool = False,
        system_shadow: bool = False,
        battery_packs: bool = False,
        subdevice_dev_type: int | None = None,
    ) -> bool:
        """Refresh one documented read surface over HTTP and merge it safely.

        This is the independent HTTP half of a manual refresh button. The button
        runs its BLE -> cloud-MQTT query concurrently. HTTP responses use the same
        source-aware merge sinks as normal polling, so fresh local/BLE/cloud
        fields retain precedence while HTTP-only or missing fields are filled.

        Returns:
            True when at least one response matched its documented HTTP payload
            family, even when source priority made the merge a state no-op.
        """
        entry = (self.data or {}).get(device_id)
        if not isinstance(entry, dict):
            return False

        parent_sn = self._shadow_parent_device_sn(entry)
        requests: list[tuple[str, int | None, str | None, Any]] = []
        if device_property:
            requests.append((
                "device_property",
                None,
                None,
                self.api.async_get_device_property(device_id),
            ))
        if system_shadow and parent_sn:
            system_id = self._shadow_system_id(entry)
            if system_id is not None:
                requests.append((
                    "system_shadow",
                    None,
                    system_id,
                    self.api.async_get_system_shadow(
                        device_sn=parent_sn,
                        diy_sn=system_id,
                    ),
                ))
        requested_shadow_types = {
            dev_type
            for dev_type in (
                SUBDEVICE_DEV_TYPE_BATTERY_PACK if battery_packs else None,
                subdevice_dev_type,
            )
            if dev_type is not None
        }
        if battery_packs and parent_sn:
            requests.append((
                "battery_pack_list",
                SUBDEVICE_DEV_TYPE_BATTERY_PACK,
                None,
                self.api.async_get_battery_pack_list(parent_sn),
            ))
        if parent_sn:
            for shadow_dev_type in requested_shadow_types:
                for sub_device_sn, _ in self._http_shadow_candidates(
                    entry,
                    shadow_dev_type,
                ):
                    requests.append((
                        "sub_shadow",
                        shadow_dev_type,
                        sub_device_sn,
                        self.api.async_get_sub_shadow(
                            dev_type=str(shadow_dev_type),
                            device_sn=parent_sn,
                            sub_device_sn=sub_device_sn,
                        ),
                    ))
        if not requests:
            return False

        results = await asyncio.gather(
            *(request[3] for request in requests),
            return_exceptions=True,
        )
        # A BLE/MQTT/local-MQTT frame may arrive while the HTTP requests are
        # awaiting I/O. Merge into that latest snapshot, not the pre-await
        # entry, so the manual refresh cannot replay stale live values.
        latest_entry = (self.data or {}).get(device_id)
        working = dict(latest_entry) if isinstance(latest_entry, dict) else dict(entry)
        usable = False
        response_succeeded = False
        for (kind, dev_type, identity, _request), result in zip(
            requests,
            results,
            strict=True,
        ):
            if isinstance(result, asyncio.CancelledError):
                raise result
            if isinstance(result, BaseException):
                _LOGGER.debug(
                    "Jackery documented HTTP read %s failed for %s/%s: %s",
                    kind,
                    device_id,
                    identity or dev_type or "main",
                    exception_debug_message(result),
                )
                continue

            if kind == "device_property":
                if not isinstance(result, dict) or not result:
                    continue
                raw_props = result.get(PAYLOAD_PROPERTIES)
                device_meta = result.get(PAYLOAD_DEVICE)
                http_props = (
                    self._sanitize_main_properties(raw_props)
                    if isinstance(raw_props, dict)
                    else {}
                )
                recognized = bool(http_props) or (
                    isinstance(device_meta, dict) and bool(device_meta)
                )
                if not recognized:
                    continue
                response_succeeded = True
                if isinstance(raw_props, dict):
                    working[PAYLOAD_HTTP_PROPERTIES] = http_props
                    guarded = self._http_properties_with_live_overrides(
                        working,
                        http_props,
                        device_id=device_id,
                    )
                    working[PAYLOAD_PROPERTIES] = (
                        self._merge_main_properties_for_device(
                            device_id,
                            working.get(PAYLOAD_PROPERTIES) or {},
                            guarded,
                            source=TransportSource.HTTP,
                        )
                    )
                if isinstance(device_meta, dict):
                    working[PAYLOAD_DEVICE] = merge_present_dict_values(
                        working.get(PAYLOAD_DEVICE) or {},
                        device_meta,
                    )
                self._last_http_device_refresh_monotonic[device_id] = time.monotonic()
                usable = True
                continue

            if kind == "battery_pack_list":
                if not isinstance(result, list) or not result:
                    continue
                response_succeeded = True
                if self._merge_subdevice_data(
                    working,
                    {FIELD_BATTERY_PACKS: result},
                    device_id=device_id,
                    source_transport=TransportSource.HTTP,
                ):
                    usable = True
                continue

            if not isinstance(result, dict) or not result:
                continue
            merged = self._merge_subdevice_data(
                working,
                result,
                device_id=device_id,
                source_transport=TransportSource.HTTP,
            )
            if kind == "system_shadow":
                merged = (
                    self._merge_system_info_fields(device_id, working, result) or merged
                )
            if merged or self._documented_http_shadow_response_recognized(
                result,
                kind=kind,
                dev_type=dev_type,
            ):
                response_succeeded = True
            if merged:
                usable = True

        if usable:
            self._push_partial_update({device_id: working})
        return response_succeeded

    async def async_query_device_info(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
    ) -> None:
        """Query the app's device-property snapshot over MQTT."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_DEVICE_PROPERTY,
            action_id=ACTION_ID_QUERY_DEVICE_PROPERTY,
            cmd=MQTT_CMD_QUERY_DEVICE_PROPERTY,
            body_fields={},
            ensure_mqtt=ensure_mqtt,
        )

    async def async_query_wifi_list(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
    ) -> None:
        """Query nearby Wi-Fi list (READ_WIFI_LIST, actionId 3001/cmd 1)."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_READ_WIFI_LIST,
            cmd=MQTT_CMD_READ_WIFI_LIST,
            body_fields={},
            ensure_mqtt=ensure_mqtt,
        )

    async def async_get_time_zone(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
    ) -> None:
        """Query device time-zone config (GET_TIME_ZONE, actionId 3004/cmd 22)."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_GET_TIME_ZONE,
            cmd=MQTT_CMD_GET_TIME_ZONE,
            body_fields={},
            ensure_mqtt=ensure_mqtt,
        )

    def _time_zone_command_body(
        self,
        timezone_name: str | None = None,
    ) -> tuple[str, dict[str, Any], int]:
        """Build the app time-zone command body shared by home and portable devices."""
        name = (timezone_name or self.hass.config.time_zone or "UTC").strip()
        timezone = dt_util.get_time_zone(name)
        if timezone is None:
            msg = f"Invalid time zone: {name}"
            raise HomeAssistantError(msg)
        now = dt_util.now(timezone)
        offset = now.utcoffset()
        utc_offset_seconds = int(offset.total_seconds()) if offset is not None else 0
        return (
            name,
            {
                FIELD_UO: utc_offset_seconds,
                FIELD_TIMEZONE: name,
            },
            int(now.timestamp()),
        )

    async def async_send_time_zone(
        self,
        device_id: str,
        *,
        timezone_name: str | None = None,
        ensure_mqtt: bool = True,
    ) -> None:
        """Sync the Home Assistant time zone via the app's SEND_TIME_ZONE body."""
        name, body, timestamp = self._time_zone_command_body(timezone_name)
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_SEND_TIME_ZONE,
            cmd=MQTT_CMD_SEND_TIME_ZONE,
            body_fields=body,
            ble_extra_body_fields={FIELD_TS: timestamp},
            ensure_mqtt=ensure_mqtt,
        )
        self._apply_local_system_patch(device_id, {FIELD_TIMEZONE: name})

    async def async_sync_grid_standard(
        self,
        device_id: str,
        safety: int,
        *,
        ensure_mqtt: bool = True,
    ) -> None:
        """Sync the app grid standard (SYNC_GRID_STANDARD, actionId 3010/cmd 105)."""
        value = int(safety)
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_SYNC_GRID_STANDARD,
            cmd=MQTT_CMD_SYNC_GRID_STANDARD,
            body_fields={FIELD_SAFETY: value, FIELD_UNBIND: 1},
            ensure_mqtt=ensure_mqtt,
        )
        self._apply_local_system_patch(device_id, {FIELD_GRID_STANDARD: str(value)})

    async def async_sync_mqtt_connect_info(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
    ) -> None:
        """Sync app cloud-MQTT broker endpoint to device (3005/cmd 99)."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_SYNC_MQTT_CONNECT_INFO,
            cmd=MQTT_CMD_SYNC_MQTT_CONNECT_INFO,
            body_fields={FIELD_HOST: MQTT_HOST, "port": MQTT_PORT},
            ensure_mqtt=ensure_mqtt,
        )

    async def async_query_device_ota_version(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
    ) -> None:
        """Query device OTA version with the command family matching the device."""
        action_id = (
            ACTION_ID_PORTABLE_OTA_VERSION
            if self._is_portable_device_id(device_id)
            else ACTION_ID_GET_DEVICE_OTA_VERSION
        )
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=action_id,
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID.get(
                action_id,
                MQTT_CMD_GET_DEVICE_OTA_VERSION,
            ),
            body_fields={},
            ensure_mqtt=ensure_mqtt,
        )

    async def async_notify_device_can_ota(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
    ) -> None:
        """Notify device that OTA update is available (NOTIFY_DEVICE_CAN_OTA, 3007/cmd.

        101).
        """
        action_id = (
            ACTION_ID_PORTABLE_NOTIFY_CAN_OTA
            if self._is_portable_device_id(device_id)
            else ACTION_ID_NOTIFY_DEVICE_CAN_OTA
        )
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=action_id,
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID.get(
                action_id,
                MQTT_CMD_NOTIFY_DEVICE_CAN_OTA,
            ),
            body_fields={},
            ensure_mqtt=ensure_mqtt,
        )

    async def async_notify_device_ota_total_page(
        self,
        device_id: str,
        *,
        total_pages: int,
        ensure_mqtt: bool = True,
    ) -> None:
        """Tell device the total OTA page count (NOTIFY_DEVICE_OTA_TOTAL_PAGE, 3008/cmd.

        102).
        """
        action_id = (
            ACTION_ID_PORTABLE_NOTIFY_OTA_TOTAL_PAGE
            if self._is_portable_device_id(device_id)
            else ACTION_ID_NOTIFY_DEVICE_OTA_TOTAL_PAGE
        )
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=action_id,
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID.get(
                action_id,
                MQTT_CMD_NOTIFY_DEVICE_OTA_TOTAL_PAGE,
            ),
            body_fields={"totalPages": total_pages},
            ensure_mqtt=ensure_mqtt,
        )

    async def async_device_get_ota_page_data(
        self,
        device_id: str,
        *,
        page_index: int,
        ensure_mqtt: bool = True,
    ) -> None:
        """Request OTA firmware page data from device (DEVICE_GET_OTA_PAGE_DATA,.

        3009/cmd 103).
        """
        action_id = (
            ACTION_ID_PORTABLE_OTA_PAGE_DATA
            if self._is_portable_device_id(device_id)
            else ACTION_ID_DEVICE_GET_OTA_PAGE_DATA
        )
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=action_id,
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID.get(
                action_id,
                MQTT_CMD_DEVICE_GET_OTA_PAGE_DATA,
            ),
            body_fields={"pageIndex": page_index},
            ensure_mqtt=ensure_mqtt,
        )

    def _is_portable_device_id(self, device_id: str) -> bool:
        """Return True when discovery marked the device as legacy portable."""
        payload = self.data.get(device_id) if isinstance(self.data, dict) else None
        if not isinstance(payload, dict):
            return False
        for section in (PAYLOAD_DEVICE, PAYLOAD_DISCOVERY, PAYLOAD_DEVICE_META):
            meta = payload.get(section)
            if (
                isinstance(meta, Mapping)
                and meta.get(PAYLOAD_DISCOVERY_SOURCE)
                == DISCOVERY_SOURCE_LEGACY_BIND_LIST
            ):
                return True
        return False

    async def async_query_weather_plan(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
    ) -> None:
        """Query weather plan."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_WEATHER_PLAN,
            action_id=ACTION_ID_QUERY_WEATHER_PLAN,
            cmd=MQTT_CMD_QUERY_WEATHER_PLAN,
            body_fields={},
            ensure_mqtt=ensure_mqtt,
        )

    async def async_query_wifi_config(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
    ) -> None:
        """Query the app Wi-Fi config with the matching command family."""
        action_id = (
            ACTION_ID_PORTABLE_GET_WIFI_CONFIG
            if self._is_portable_device_id(device_id)
            else ACTION_ID_QUERY_WIFI_CONFIG
        )
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_WIFI_CONFIG,
            action_id=action_id,
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID.get(
                action_id,
                MQTT_CMD_QUERY_WIFI_CONFIG,
            ),
            body_fields={},
            ensure_mqtt=ensure_mqtt,
        )

    # ------------------------------------------------------------------
    # Third-party MQTT bridge (actionId 3046/3047)
    # ------------------------------------------------------------------
    # Per ``HomeCmdAction.smali``: ``SET_THIRD_PARTY_MQTT_CONFIG``
    # (cmd=113 ``ThirdPartMQTTConfig``) and ``GET_THIRD_PARTY_MQTT_CONFIG``
    # (cmd=114 ``QueryThirdPartMQTTConfig``). Body schema from
    # ``ThirdPartyMqttBody.smali``:
    #
    #     {"enable":0|1, "ip":<str>, "port":<int>,
    #      "userName":<str>, "password":<str>, "token":<str>}
    #
    # These methods bypass the REST relay and publish the same app command
    # body to the device over the available write transport.
    #
    # MqttMsgActivity creates a fallback token by iterating range 0..8 and
    # appending Random.nextInt(10). HomeDeviceController.g1(...) then sends
    # ``userName``/``password``/``token`` through the bb/* codec before
    # publishing. For SolarVault home devices the concrete codec is AES/CBC
    # with the decoded bluetoothKey as key+IV and Base64 ciphertext output.

    def _stable_third_party_mqtt_token(self, token: str) -> tuple[str, bool]:
        """Return a valid app-style token and whether HA generated it."""
        result_token, use_generated, new_generated = stable_third_party_mqtt_token(
            token,
            self._generated_third_party_mqtt_token,
        )
        if new_generated is not None:
            self._generated_third_party_mqtt_token = new_generated
            options = dict(self.entry.options)
            if not str(options.get(CONF_THIRD_PARTY_MQTT_TOKEN) or "").strip():
                options[CONF_THIRD_PARTY_MQTT_TOKEN] = new_generated
                self.hass.config_entries.async_update_entry(
                    self.entry,
                    options=options,
                )
            _LOGGER.debug("Jackery: generated stable 9-digit third-party MQTT token")
        return result_token, use_generated

    def _decode_third_party_mqtt_config_body(
        self,
        device_id: str,
        body: dict[str, Any],
    ) -> dict[str, Any]:
        """Decode app-encoded ThirdPartMQTTConfig credential fields."""
        bluetooth_key = self.device_bluetooth_key(device_id)
        if bluetooth_key is None and not self._has_encoded_third_party_mqtt_field(
            body,
        ):
            return dict(body)
        return decode_third_party_mqtt_config_body(
            body,
            bluetooth_key,
        )

    @staticmethod
    def _has_encoded_third_party_mqtt_field(body: dict[str, Any]) -> bool:
        """Return True when credential fields look like app AES/Base64 ciphertext."""
        for key in _THIRD_PARTY_MQTT_CONFIG_KEYS:
            value = body.get(key)
            if not isinstance(value, str) or not value:
                continue
            try:
                decoded = base64.b64decode(value, validate=True)
            except binascii.Error, ValueError:
                continue
            if decoded and len(decoded) % 16 == 0:
                return True
        return False

    def third_party_mqtt_config_plaintext(self, device_id: str) -> dict[str, Any]:
        """Return plaintext third-party MQTT config for HA entities."""
        return third_party_mqtt_config_plaintext(
            dict(self.entry.options),
            self._generated_third_party_mqtt_token,
            ((self.data or {}).get(device_id, {}) or {}),
        )

    def _apply_local_third_party_mqtt_config_patch(
        self,
        device_id: str,
        config: dict[str, Any],
    ) -> None:
        """Mirror plaintext third-party MQTT settings into coordinator data."""
        if not self.data or device_id not in self.data:
            return
        payload = dict(self.data[device_id])
        payload[PAYLOAD_THIRD_PARTY_MQTT_CONFIG] = {
            **config,
            "_ha_plaintext": True,
        }
        new_data = dict(self.data)
        new_data[device_id] = payload
        self._push_partial_update(new_data)

    async def async_update_third_party_mqtt_config(
        self,
        device_id: str,
        updates: dict[str, Any],
    ) -> None:
        """Update one or more ThirdPartMQTTConfig fields via HA entities."""
        config = self.third_party_mqtt_config_plaintext(device_id)
        config.update(updates)
        enabled = safe_bool(config.get(FIELD_THIRD_PARTY_MQTT_ENABLE)) is True
        if enabled and not str(config.get(FIELD_THIRD_PARTY_MQTT_IP) or "").strip():
            msg = "Third-party MQTT host/IP is required"
            raise HomeAssistantError(msg)
        port = safe_int(
            config.get(FIELD_THIRD_PARTY_MQTT_PORT) or DEFAULT_THIRD_PARTY_MQTT_PORT,
        )
        await self.async_set_third_party_mqtt_config(
            device_id,
            enable=enabled,
            ip=str(config.get(FIELD_THIRD_PARTY_MQTT_IP) or "").strip(),
            port=port if port is not None else DEFAULT_THIRD_PARTY_MQTT_PORT,
            username=str(config.get(FIELD_THIRD_PARTY_MQTT_USERNAME) or ""),
            password=str(config.get(FIELD_THIRD_PARTY_MQTT_PASSWORD) or ""),
            token=str(config.get(FIELD_THIRD_PARTY_MQTT_TOKEN) or "").strip(),
        )

    async def async_set_third_party_mqtt_config(  # ruff:ignore[too-many-arguments]
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
        normalized_token, use_generated_token = self._stable_third_party_mqtt_token(
            token,
        )
        bluetooth_key = self.device_bluetooth_key(device_id)
        if bluetooth_key is None:
            msg = "Cannot set third-party MQTT config without device bluetoothKey"
            raise HomeAssistantError(
                msg,
            )
        try:
            encoded_username = encode_third_party_mqtt_field(
                str(username),
                bluetooth_key,
            )
            encoded_password = encode_third_party_mqtt_field(
                str(password),
                bluetooth_key,
            )
            encoded_token = encode_third_party_mqtt_field(
                normalized_token,
                bluetooth_key,
            )
        except ValueError as err:
            msg = f"Cannot encode third-party MQTT credentials: {err}"
            raise HomeAssistantError(
                msg,
            ) from err
        body: dict[str, Any] = {
            FIELD_THIRD_PARTY_MQTT_ENABLE: 1 if enable else 0,
            FIELD_THIRD_PARTY_MQTT_IP: str(ip),
            FIELD_THIRD_PARTY_MQTT_PORT: int(port),
            FIELD_THIRD_PARTY_MQTT_USERNAME: encoded_username,
            FIELD_THIRD_PARTY_MQTT_PASSWORD: encoded_password,
            FIELD_THIRD_PARTY_MQTT_TOKEN: encoded_token,
        }
        _LOGGER.info(
            "Jackery: publishing SET_THIRD_PARTY_MQTT_CONFIG (3046) to %s "
            "enable=%s target_configured=%s username_set=%s token_generated=%s",
            device_id,
            enable,
            bool(ip) and int(port) > 0,
            bool(username),
            use_generated_token,
        )
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_THIRD_PARTY_MQTT_CONFIG,
            action_id=ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG,
            cmd=MQTT_CMD_THIRD_PARTY_MQTT_CONFIG,
            body_fields=body,
        )
        self._apply_local_third_party_mqtt_config_patch(
            device_id,
            {
                FIELD_THIRD_PARTY_MQTT_ENABLE: 1 if enable else 0,
                FIELD_THIRD_PARTY_MQTT_IP: str(ip),
                FIELD_THIRD_PARTY_MQTT_PORT: int(port),
                FIELD_THIRD_PARTY_MQTT_USERNAME: str(username),
                FIELD_THIRD_PARTY_MQTT_PASSWORD: str(password),
                FIELD_THIRD_PARTY_MQTT_TOKEN: normalized_token,
            },
        )

    async def async_query_third_party_mqtt_config(self, device_id: str) -> None:
        """Read back the device's third-party MQTT bridge config (experimental).

        Publishes ``GET_THIRD_PARTY_MQTT_CONFIG`` (actionId 3047, cmd 114).
        The response — if any — arrives on the ``device`` topic and is
        captured in the redacted payload-debug log. Inspect
        ``jackery_solarvault_payload_debug.jsonl`` after calling.
        """
        _LOGGER.info(
            "Jackery: publishing GET_THIRD_PARTY_MQTT_CONFIG "
            "(3047) to %s; check payload_debug log for the response",
            device_id,
        )
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG,
            action_id=ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
            cmd=MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG,
            body_fields={},
        )

    async def async_send_device_schedule(
        self,
        device_id: str,
        *,
        action_id: int,
        body: dict[str, Any],
    ) -> None:
        """Publish a DownloadDeviceSchedule frame (cmd=112, actionId 3015-3018).

        Empirical schedule body from Frida-PCAP per
        ``docs/Markdown/MQTT_PROTOCOL.md`` §DownloadDeviceSchedule:
        ``{"actionType": int, "taskType": int, "mode": int, "pw": int,
        "sysSwitch": int, "end": "HH:MM", "loops": "1111111", "start":
        "HH:MM", "tid": "<task-id>", "cmd": 112}``. The body is forwarded
        verbatim so callers can match observed wire layouts without the
        integration locking in one interpretation; only ``cmd`` is
        injected (and overwrites any caller-supplied value) so the
        wire-protocol invariant cmd=112 holds.

        ``action_id`` must be one of ACTION_ID_TIMER_TASK_*
        (3015=add, 3016=delete, 3017=update, 3018=read). The caller is
        responsible for picking the right one; the actionType inside
        the body is independent of the action_id selector per the
        captured frame layout.
        """
        if action_id not in {
            ACTION_ID_TIMER_TASK_ADD,
            ACTION_ID_TIMER_TASK_DELETE,
            ACTION_ID_TIMER_TASK_UPDATE,
            ACTION_ID_TIMER_TASK_READ,
        }:
            msg = (
                "action_id must be one of 3015/3016/3017/3018 "
                "(TIMER_TASK_ADD/DELETE/UPDATE/READ); got "
                f"{action_id!r}"
            )
            raise ValueError(
                msg,
            )
        merged_body = dict(body)
        merged_body[FIELD_CMD] = MQTT_CMD_DOWNLOAD_DEVICE_SCHEDULE
        _LOGGER.debug(
            "Jackery: publishing DownloadDeviceSchedule "
            "(actionId=%s) to %s — body keys=%s",
            action_id,
            device_id,
            sorted(merged_body),
        )
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DOWNLOAD_DEVICE_SCHEDULE,
            action_id=action_id,
            cmd=MQTT_CMD_DOWNLOAD_DEVICE_SCHEDULE,
            body_fields=merged_body,
        )

    async def async_read_device_schedule(
        self,
        device_id: str,
        *,
        task_type: int,
        plug_sn: str = "",
    ) -> None:
        """Read an app schedule bucket via ``TIMER_TASK_READ``.

        Smali ``HomeDeviceController`` builds
        ``{"actionType":4,"taskType":<1|2|3>}`` and adds ``deviceSn`` for
        ``SMART_PLUG_TIMER``. ``cmd=112`` is injected by
        ``async_send_device_schedule``.
        """
        task_type_int = int(task_type)
        if task_type_int not in {
            TIMER_TASK_TYPE_SMART_PLUG,
            TIMER_TASK_TYPE_CUSTOM_MODE,
            TIMER_TASK_TYPE_TIME_ELEC,
        }:
            msg = f"Unsupported task_type {task_type!r}"
            raise ValueError(msg)
        body: dict[str, Any] = {
            FIELD_ACTION_TYPE: TIMER_TASK_ACTION_READ,
            FIELD_TASK_TYPE: task_type_int,
        }
        if task_type_int == TIMER_TASK_TYPE_SMART_PLUG:
            body[FIELD_DEVICE_SN] = str(plug_sn)
        await self.async_send_device_schedule(
            device_id,
            action_id=ACTION_ID_TIMER_TASK_READ,
            body=body,
        )

    async def async_query_battery_packs(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
    ) -> None:
        """Query battery packs (devType=1, ``READ_SUB_DEVICE_BATTERY_PACK``)."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
            action_id=ACTION_ID_SUBDEVICE_3014,
            cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
            body_fields={FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_BATTERY_PACK},
            ensure_mqtt=ensure_mqtt,
        )

    async def async_query_smart_meter(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
    ) -> None:
        """Query smart meter / CT (devType=3, ``READ_SUB_DEVICE_CT``)."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
            action_id=ACTION_ID_SUBDEVICE_3031,
            cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
            body_fields={FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_CT},
            ensure_mqtt=ensure_mqtt,
        )

    async def async_query_meter_heads(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
    ) -> None:
        """Query meter-head / collector subdevices (devType=4)."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
            action_id=ACTION_ID_SUBDEVICE_3033,
            cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
            body_fields={FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_METER_HEAD},
            ensure_mqtt=ensure_mqtt,
        )

    async def async_query_smart_plugs(
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
    ) -> None:
        """Query smart plug / socket subdevices.

        Mirrors the Jackery app's ``READ_SUB_DEVICE_SOCKET`` action:
        ``messageType=QuerySubDeviceGroupProperty`` with ``actionId=3032``,
        ``cmd=110`` and ``devType=6`` per ``HomeSubDeviceType.SOCKET``.
        The response arrives as ``UploadSubDeviceGroupProperty`` with a
        ``plugs`` array (see docs/PROTOCOL.md §2 and PROTOCOL.md §3).
        """
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
            action_id=ACTION_ID_SUBDEVICE_3032,
            cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
            body_fields={FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_SOCKET},
            ensure_mqtt=ensure_mqtt,
        )

    async def async_set_smart_plug_switch(
        self,
        device_id: str,
        *,
        plug_sn: str,
        on: bool,
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
        # next ``UploadSubDeviceGroupProperty`` frame confirms it.
        self._apply_local_smart_plug_switch_patch(device_id, plug_sn, on)

    async def async_insert_electricity_strategy(
        self,
        device_id: str,
        body: dict[str, Any],
    ) -> None:
        """Add a new electricity strategy plan."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_INSERT_ELECTRICITY_STRATEGY,
            action_id=ACTION_ID_PORTABLE_ADD_CHARGE_PLAN,
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[ACTION_ID_PORTABLE_ADD_CHARGE_PLAN],
            body_fields=body,
        )

    async def async_update_electricity_strategy(
        self,
        device_id: str,
        body: dict[str, Any],
    ) -> None:
        """Update an existing electricity strategy plan."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_UPDATE_ELECTRICITY_STRATEGY,
            action_id=ACTION_ID_PORTABLE_UPDATE_CHARGE_PLAN,
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[
                ACTION_ID_PORTABLE_UPDATE_CHARGE_PLAN
            ],
            body_fields=body,
        )

    async def async_delete_electricity_strategy(
        self,
        device_id: str,
        body: dict[str, Any],
    ) -> None:
        """Delete an electricity strategy plan."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DELETE_ELECTRICITY_STRATEGY,
            action_id=ACTION_ID_PORTABLE_DELETE_CHARGE_PLAN,
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[
                ACTION_ID_PORTABLE_DELETE_CHARGE_PLAN
            ],
            body_fields=body,
        )

    async def async_query_electricity_strategy(
        self,
        device_id: str,
    ) -> None:
        """Query all electricity strategy plans."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_ELECTRICITY_STRATEGY,
            action_id=ACTION_ID_PORTABLE_GET_CHARGE_PLAN,
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[ACTION_ID_PORTABLE_GET_CHARGE_PLAN],
            body_fields={},
        )

    async def async_set_breaker_switch(
        self,
        device_id: str,
        breaker_id: str,
        on: bool,
    ) -> None:
        """Toggle a circuit breaker on or off.

        Mirrors the app's breaker control logic: ``messageType=ControlSubDevice``,
        ``cmd=111`` with body ``{devType: 7, idx: <breaker_id>, sw: 0|1}``.
        """
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_CONTROL_SUB_DEVICE,
            action_id=ACTION_ID_CONTROL_SOCKET_SWITCH,
            cmd=MQTT_CMD_CONTROL_SUB_DEVICE,
            body_fields={
                FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_BREAKER,
                FIELD_IDX: int(breaker_id),
                FIELD_SW: 1 if on else 0,
            },
        )
        # Optimistic local update
        self._apply_local_breaker_switch_patch(device_id, breaker_id, on)

    async def async_set_shelly_cloud_switch(
        self,
        device_id: str,
        *,
        shelly_device_id: str,
        on: bool,
    ) -> None:
        """Toggle a Shelly Cloud socket exactly like ``ShellySocketPanelVM``.

        Smali wires ``function="switch"`` and ``action="on"|"off"`` to
        ``wss-cloud/device/shelly/device/control``. This path is separate
        from Jackery ``ControlSubDevice`` because Shelly Cloud sockets are
        cloud-to-cloud accessories, not local Jackery BLE sockets.
        """
        await self.api.async_control_shelly_device(
            shelly_device_id,
            action=SHELLY_CONTROL_ACTION_ON if on else SHELLY_CONTROL_ACTION_OFF,
            function=SHELLY_CONTROL_FUNCTION_SWITCH,
            control_allowed=True,
        )
        self._apply_local_smart_plug_switch_patch(device_id, shelly_device_id, on)

    async def async_set_smart_plug_priority(
        self,
        device_id: str,
        *,
        plug_sn: str,
        enabled: bool,
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
        self,
        device_id: str,
        plug_sn: str,
        on: bool,
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

    def _apply_local_breaker_switch_patch(
        self,
        device_id: str,
        breaker_id: str,
        on: bool,
    ) -> None:
        """Mirror the requested breaker state into ``circuit_property`` immediately."""
        if not self.data or device_id not in self.data:
            return
        payload = dict(self.data[device_id])
        circuits = payload.get(PAYLOAD_CIRCUIT_PROPERTY)
        if not isinstance(circuits, list):
            return
        target = 1 if on else 0
        updated_circuits: list[Any] = []
        touched = False
        for breaker in circuits:
            if isinstance(breaker, dict) and circuit_id(breaker) == breaker_id:
                next_breaker = dict(breaker)
                next_breaker[FIELD_SW] = target
                updated_circuits.append(next_breaker)
                touched = True
            else:
                updated_circuits.append(breaker)
        if touched:
            payload[PAYLOAD_CIRCUIT_PROPERTY] = updated_circuits
            new_data = dict(self.data)
            new_data[device_id] = payload
            self._push_partial_update(new_data)

    def _apply_local_smart_plug_patch(
        self,
        device_id: str,
        plug_sn: str,
        updates: dict[str, Any],
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
            plug_ids = self._subdevice_identity_values(plug)
            if str(plug_sn) in plug_ids:
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
        self,
        device_id: str,
        *,
        ensure_mqtt: bool = True,
    ) -> None:
        """Query combo subdevice (devType=2, ``READ_SUB_DEVICE_COMBO``)."""
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY,
            action_id=ACTION_ID_SUBDEVICE_3037,
            cmd=MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY,
            body_fields={FIELD_DEV_TYPE: SUBDEVICE_DEV_TYPE_COMBO},
            ensure_mqtt=ensure_mqtt,
        )

    async def async_reboot_device(self, device_id: str) -> None:
        """Reboot device."""
        await self._async_publish_command_ble_first(
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
        phase_int = safe_int(phase)
        if phase_int not in {1, 2, 3, 4}:
            msg = f"CT phase must be 1..4 (got {phase_int})"
            raise HomeAssistantError(msg)
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

    # --- Portable / Explorer powerstation commands ---------------------------
    # Portable devices use ``action_id=<portable_msg_id>`` (1-53) and
    # ``cmd=<ble_msg_type>`` with the same
    # ``messageType=DevicePropertyChange`` envelope as home commands but routed
    # through the ``PortableControlFormat`` on the broker.

    async def async_send_portable_command(
        self,
        device_id: str,
        *,
        action_id: int,
        cmd: int,
        body_fields: dict[str, Any],
        message_type: str = MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
    ) -> None:
        """Send an arbitrary portable command (msg_id 1-53) via BLE-first then MQTT.

        Portable commands use ``action_id=<msgId>`` and ``cmd=<bleMsgType>``
        from ``cmd.portable.b``.  Most portable commands transport as
        ``DevicePropertyChange`` (the ``message_type`` parameter allows overriding
        for strategy/plan commands).
        """
        await self._async_publish_command_ble_first(
            device_id,
            message_type=message_type,
            action_id=action_id,
            cmd=cmd,
            body_fields=body_fields,
        )

    async def async_send_portable_time_zone(
        self,
        device_id: str,
        *,
        timezone_name: str | None = None,
    ) -> None:
        """Sync the Home Assistant time zone via portable SEND_TIME_ZONE."""
        name, body, timestamp = self._time_zone_command_body(timezone_name)
        await self._async_publish_command_ble_first(
            device_id,
            message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
            action_id=ACTION_ID_PORTABLE_SEND_TIME_ZONE,
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[ACTION_ID_PORTABLE_SEND_TIME_ZONE],
            body_fields=body,
            ble_extra_body_fields={FIELD_TS: timestamp},
        )
        self._apply_local_system_patch(device_id, {FIELD_TIMEZONE: name})

    async def async_portable_toggle_output(
        self,
        device_id: str,
        *,
        action_id: int,
        field: str,
        enabled: bool,
    ) -> None:
        """Toggle a portable output (DC/DC-USB/DC-CAR/AC/AC240/light/screen).

        Sends ``{field: 1}`` to enable or ``{field: 0}`` to disable via
        ``DevicePropertyChange`` with ``cmd=<ble_msg_type>``.
        """
        await self.async_send_portable_command(
            device_id,
            action_id=action_id,
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[action_id],
            body_fields={field: 1 if enabled else 0},
        )
        self._apply_local_property_patch(device_id, {field: 1 if enabled else 0})

    async def async_portable_set_number(
        self,
        device_id: str,
        *,
        action_id: int,
        field: str,
        value: int,
    ) -> None:
        """Set a numeric value on a portable device (charge power, countdown, etc.)."""
        await self.async_send_portable_command(
            device_id,
            action_id=action_id,
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[action_id],
            body_fields={field: value},
        )
        self._apply_local_property_patch(device_id, {field: value})

    async def async_portable_set_custom_use_battery(
        self,
        device_id: str,
        *,
        discharge_limit: int | None = None,
        charge_limit: int | None = None,
    ) -> None:
        """Set the custom-use battery bounds on a portable device (msgId=33).

        The app's SetBatteryBoundry frame carries the lower (``dl``) and upper
        (``cl``) bounds plus a derived ``bc = cl - CUSTOM_USE_BATTERY_BC_OFFSET``
        back-off together, so a missing side is filled from the last-known
        coordinator state and all three fields ship in a single command.
        """
        if discharge_limit is None and charge_limit is None:
            msg = "Cannot set custom-use battery without a bound"
            raise UpdateFailed(msg)
        current = ((self.data or {}).get(device_id, {}) or {}).get(
            PAYLOAD_PROPERTIES,
            {},
        ) or {}

        def _bound(value: object, default: int) -> int:
            parsed = safe_int(value)
            if parsed is None or parsed < 0 or parsed > 100:  # ruff:ignore[magic-value-comparison]
                return default
            return parsed

        lower = (
            _bound(discharge_limit, 0)
            if discharge_limit is not None
            else _bound(current.get("dl"), 0)
        )
        upper = (
            _bound(charge_limit, 100)
            if charge_limit is not None
            else _bound(current.get("cl"), 100)
        )
        body = {
            "dl": lower,
            "cl": upper,
            "bc": upper - CUSTOM_USE_BATTERY_BC_OFFSET,
        }
        await self.async_send_portable_command(
            device_id,
            action_id=ACTION_ID_PORTABLE_CUSTOM_USE_BATTERY,
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[
                ACTION_ID_PORTABLE_CUSTOM_USE_BATTERY
            ],
            body_fields=body,
        )
        self._apply_local_property_patch(device_id, body)

    async def async_portable_set_select(
        self,
        device_id: str,
        *,
        action_id: int,
        field: str,
        value: int,
        local_patch: dict[str, Any] | None = None,
    ) -> None:
        """Set a select value on a portable device (charge mode, power mode, etc.)."""
        await self.async_send_portable_command(
            device_id,
            action_id=action_id,
            cmd=PORTABLE_BLE_MSG_TYPE_BY_ACTION_ID[action_id],
            body_fields={field: value},
        )
        self._apply_local_property_patch(
            device_id, local_patch if local_patch is not None else {field: value}
        )

    async def _async_query_subdevices_for_missing(  # ruff:ignore[too-many-branches, too-many-statements]
        self,
        *,
        force: bool = False,
        ensure_mqtt: bool = True,
        snapshot: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        """Query MQTT sub-device status for accessories that need backfill."""
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
            should_query_combo = (
                force
                or has_breaker_accessory(payload)
                or has_sub_device_accessory(payload)
            )
            if (
                not should_query_meter
                and not should_query_packs
                and not should_query_meter_heads
                and not should_query_plugs
                and not should_query_combo
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
                        device_id,
                        ensure_mqtt=ensure_mqtt,
                    )
                except ConfigEntryAuthFailed:
                    raise
                except (TimeoutError, HomeAssistantError, JackeryError) as err:
                    _LOGGER.debug(
                        "Jackery battery-pack query failed for %s: %s",
                        device_id,
                        err,
                    )
            if should_query_combo:
                try:
                    await self.async_query_subdevice_combo(
                        device_id,
                        ensure_mqtt=ensure_mqtt,
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
                        device_id,
                        ensure_mqtt=ensure_mqtt,
                    )
                except ConfigEntryAuthFailed:
                    raise
                except (TimeoutError, HomeAssistantError, JackeryError) as err:
                    _LOGGER.debug(
                        "Jackery smart-meter query failed for %s: %s",
                        device_id,
                        err,
                    )
            if should_query_meter_heads:
                try:
                    await self.async_query_meter_heads(
                        device_id,
                        ensure_mqtt=ensure_mqtt,
                    )
                except ConfigEntryAuthFailed:
                    raise
                except (TimeoutError, HomeAssistantError, JackeryError) as err:
                    _LOGGER.debug(
                        "Jackery meter-head query failed for %s: %s",
                        device_id,
                        err,
                    )
            if should_query_plugs:
                try:
                    await self.async_query_smart_plugs(
                        device_id,
                        ensure_mqtt=ensure_mqtt,
                    )
                except ConfigEntryAuthFailed:
                    raise
                except (TimeoutError, HomeAssistantError, JackeryError) as err:
                    _LOGGER.debug(
                        "Jackery smart-plug query failed for %s: %s",
                        device_id,
                        err,
                    )

    def _schedule_statistics_import(
        self,
        snapshot: dict[str, dict[str, Any]],
    ) -> None:
        """Queue recorder statistic imports without blocking setup or polling."""
        if self._shutdown_started or not self._statistics_import_ready:
            return
        if (
            self._statistics_import_task is not None
            and not self._statistics_import_task.done()
        ):
            return
        now_monotonic = time.monotonic()
        import_throttle_sec = (
            max(15.0, self._configured_update_interval.total_seconds())
            if self._statistics_startup_sync_pending
            else _STATISTICS_IMPORT_THROTTLE_SEC
        )
        if now_monotonic - self._last_stat_import_monotonic < import_throttle_sec:
            return
        self._last_stat_import_monotonic = now_monotonic

        periodic_snapshot: dict[str, dict[str, Any]] = {}
        for device_id, payload in snapshot.items():
            periodic_payload = {
                section_key: value
                for section_key, value in payload.items()
                if is_periodic_section(section_key)
                or section_key
                in {
                    PAYLOAD_DEVICE,
                    PAYLOAD_DISCOVERY,
                    PAYLOAD_SYSTEM,
                    PAYLOAD_SYSTEM_META,
                }
            }
            periodic_snapshot[device_id] = periodic_payload
        if not periodic_snapshot:
            return

        self._statistics_import_task = self.hass.async_create_background_task(
            self._async_statistics_import_job(periodic_snapshot),
            name=f"{DOMAIN}_statistics_import",
            eager_start=False,
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
        except RECORDER_BACKGROUND_TASK_ERRORS:
            # RECORDER_BACKGROUND_TASK_ERRORS = base task errors + recorder/DB
            # errors (incl. SQLAlchemyError) so a recorder/database failure can
            # never escape this background task and surface as an unhandled-task
            # crash (which reads as a hung setup to the user).
            _LOGGER.exception("Jackery recorder-statistics import failed")
        finally:
            if asyncio.current_task() is self._statistics_import_task:
                self._statistics_import_task = None

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
        return local_start

    @staticmethod
    def _stat_row_start(row: Mapping[str, Any]) -> float | None:
        """Return a statistics row start timestamp in seconds."""
        return stat_row_start(row)

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
            recorder = get_instance(self.hass)
        except BACKGROUND_TASK_ERRORS as err:
            _LOGGER.debug("Recorder instance unavailable: %s", err)
            return 0.0

        first_start_ts = starts[0].timestamp()

        def _load_offset() -> float:
            with session_scope(session=recorder.get_session()) as session:
                meta = (
                    session
                    .query(StatisticsMeta.id)
                    .filter(StatisticsMeta.statistic_id == statistic_id)
                    .first()
                )
                if meta is None:
                    return 0.0
                row = (
                    session
                    .query(Statistics.sum)
                    .filter(
                        Statistics.metadata_id == meta[0],
                        Statistics.start_ts < first_start_ts,
                        Statistics.sum.is_not(None),
                        Statistics.sum >= 0,
                    )
                    .order_by(Statistics.start_ts.desc())
                    .first()
                )
                if row is None:
                    return 0.0
                return round(safe_float(row[0]) or 0.0, 5)

        try:
            return cast(
                "float",
                await recorder.async_add_executor_job(_load_offset),
            )
        except BACKGROUND_TASK_ERRORS as err:
            _LOGGER.debug(
                "Could not read previous statistics for %s: %s",
                statistic_id,
                err,
            )
            return 0.0

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

        issue_suffix = f"_{REPAIR_ISSUE_APP_DATA_INCONSISTENCY}"
        issue_id = f"{self.entry.entry_id}{issue_suffix}"
        registry = ir.async_get(self.hass)
        for domain, existing_issue_id in tuple(registry.issues):
            if (
                domain == DOMAIN
                and existing_issue_id.endswith(issue_suffix)
                and existing_issue_id != issue_id
            ):
                ir.async_delete_issue(self.hass, DOMAIN, existing_issue_id)
        if not warnings:
            self._data_quality_issue_signature = None
            ir.async_delete_issue(self.hass, DOMAIN, issue_id)
            return

        first = warnings[0]
        examples = "; ".join(
            format_data_quality_warning(warning)
            for warning in warnings[:DATA_QUALITY_REPAIR_EXAMPLE_LIMIT]
        )
        # Dedup on the contradiction's identity (which periods disagree), NOT
        # the live kWh values. The warning dicts carry rounded source/reference
        # values that drift every poll as the device produces energy; keying on
        # the full payload made every drift look like a new contradiction and
        # re-raised the repair issue every cycle. Keying on
        # reason+metric+sections fires the issue once and lets it rest.
        signature = json.dumps(
            sorted(
                [
                    str(warning.get(DATA_QUALITY_KEY_REASON, "")),
                    str(warning.get(DATA_QUALITY_KEY_METRIC_KEY, "")),
                    str(warning.get(DATA_QUALITY_KEY_SOURCE_SECTION, "")),
                    str(warning.get(DATA_QUALITY_KEY_REFERENCE_SECTION, "")),
                ]
                for warning in warnings
            ),
        )
        if self._data_quality_issue_signature == signature:
            return
        ir.async_create_issue(
            self.hass,
            DOMAIN,
            issue_id,
            is_fixable=True,
            is_persistent=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key=REPAIR_TRANSLATION_APP_DATA_INCONSISTENCY,
            translation_placeholders={
                "count": str(len(warnings)),
                "metric": str(
                    first.get(DATA_QUALITY_KEY_LABEL)
                    or first.get(DATA_QUALITY_KEY_METRIC_KEY)
                    or "unknown",
                ),
                "examples": examples or "unknown",
            },
            data={
                "entry_id": self.entry.entry_id,
                "count": str(len(warnings)),
                "metric": str(
                    first.get(DATA_QUALITY_KEY_LABEL)
                    or first.get(DATA_QUALITY_KEY_METRIC_KEY)
                    or "unknown",
                ),
                "examples": examples or "unknown",
            },
        )
        self._data_quality_issue_signature = signature

    async def async_load_statistics_backfill_state(self) -> None:
        """Load persistent recorder-statistics repair state."""
        if self._statistics_backfill_state_loaded:
            return
        loaded = await self._statistics_backfill_store.async_load()
        if isinstance(loaded, dict):
            devices = loaded.get(_STATISTICS_BACKFILL_STORE_DEVICES)
            if isinstance(devices, dict):
                self._statistics_backfill_state = {
                    _STATISTICS_BACKFILL_STORE_DEVICES: devices,
                }
        self._statistics_backfill_state_loaded = True

    async def _async_save_statistics_backfill_state(self) -> None:
        """Persist recorder-statistics repair state."""
        await self._statistics_backfill_store.async_save(
            self._statistics_backfill_state,
        )

    async def _async_ensure_statistics_backfill_state_loaded(self) -> None:
        """Load persistent repair state on demand."""
        if not self._statistics_backfill_state_loaded:
            await self.async_load_statistics_backfill_state()

    @property
    def statistics_backfill_diagnostics(self) -> dict[str, Any]:
        """Return redaction-safe statistics repair diagnostics."""  # ruff:ignore[property-docstring-starts-with-verb]
        devices = self._statistics_backfill_state.get(
            _STATISTICS_BACKFILL_STORE_DEVICES,
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
        return parse_statistics_backfill_date(value)

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

    def _statistics_repair_from_date(  # ruff:ignore[too-many-return-statements]
        self,
        device_id: str,
        today: date,
    ) -> date | None:
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
        return iter_calendar_months(start_date, end_date)

    @staticmethod
    def _iter_calendar_weeks(start_date: date, end_date: date) -> list[date]:
        """Return Monday week starts intersecting an inclusive date range."""
        return iter_calendar_weeks(start_date, end_date)

    @staticmethod
    def _iter_calendar_years(start_date: date, end_date: date) -> list[int]:
        """Return calendar years intersecting an inclusive date range."""
        return iter_calendar_years(start_date, end_date)

    @staticmethod
    def _app_chart_period_meta(date_type: str) -> tuple[str, str] | None:
        """Return the external bucket id and label for an app chart period."""
        return app_chart_period_meta(date_type)

    @staticmethod
    def _app_chart_name_prefix(device_id: str, payload: dict[str, Any]) -> str:
        """Return a stable, user-readable app chart statistic name prefix."""
        return app_chart_name_prefix(device_id, payload)

    def _day_chart_source_candidates(
        self,
        section_prefix: str,
        stat_key: str,
        metric_key: str,
    ) -> list[tuple[str, str]]:
        """Return candidate payload sections for one day power-curve metric."""
        candidates: list[tuple[str, str]] = []
        trend_source = _DAY_TREND_SOURCE_BY_METRIC_KEY.get(metric_key)
        if trend_source is not None:
            candidates.append(trend_source)
        for candidate_prefix, candidate_stat_key in self._metric_source_candidates(
            section_prefix,
            stat_key,
            metric_key,
        ):
            candidates.append((
                f"{candidate_prefix}_{DATE_TYPE_DAY}",
                candidate_stat_key,
            ))

        deduped: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            deduped.append(candidate)
        return deduped

    def _day_chart_points_for_metric(  # ruff:ignore[too-many-arguments]
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
            source = gate_payload_section(
                TransportSource.HTTP,
                section,
                source,
            )
            if not source:
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

    async def _async_add_app_chart_statistics(  # ruff:ignore[too-many-branches, too-many-arguments, too-many-locals, too-many-statements]
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
        samples: list[tuple[datetime, float]] = []
        negatives_skipped = 0
        for point in points:
            state = safe_float(point.value)
            if state is None:
                continue
            if state < 0:
                # §2.2 rule-1: PV / generation / grid / battery energy buckets
                # are always >= 0, so a negative value is anomalous (corrupt
                # payload or upstream API fault). Reject + skip as before, but
                # count it so the rejection is no longer silent.
                negatives_skipped += 1
                continue
            samples.append((
                self._local_statistic_start(point.start_date),
                round(state, 5),
            ))
        if negatives_skipped:
            # Aggregate to one line per (metric, bucket) call — never per bucket.
            _LOGGER.warning(
                "Rejected %d negative value(s) for metric '%s' bucket '%s' "
                "(§2.2: energy buckets must be >= 0); skipped from import",
                negatives_skipped,
                metric_key,
                bucket,
            )
        if not samples:
            return True, 0
        starts = [start for start, _state in samples]
        states = [state for _start, state in samples]
        statistic_id = external_trend_statistic_id(
            DOMAIN,
            device_id,
            metric_key,
            bucket,
        )
        # ``_async_statistic_sum_offset`` reads only rows *before* this series,
        # so a correction to an earlier day changes it (the day-hourly
        # ``statistic_id`` has no date part and spans every day). Folding the
        # offset into the signature makes the idempotence guard re-evaluate a
        # trailing day whose raw states are unchanged but whose cumulative sum
        # is now stale, bounding the re-sum to the existing backfill window
        # instead of blindly short-circuiting it.
        offset = await self._async_statistic_sum_offset(
            statistic_id,
            starts,
            states,
        )
        series_signature = json.dumps(
            [
                [s.isoformat() if hasattr(s, "isoformat") else s for s in starts],
                states,
                offset,
            ],
            sort_keys=True,
            default=str,
        )
        if self._stat_import_last_sig.get(statistic_id) == series_signature:
            return True, 0
        # ``async_add_external_statistics`` UPSERTs per (metadata_id, start_ts)
        # in this HA version (recorder/statistics.py: ``_statistics_exists`` ->
        # ``_update_statistics`` writes both state and sum, else
        # ``_insert_statistics``; the session is wrapped in
        # ``filter_unique_constraint_integrity_error``). Re-submitting an
        # existing start therefore UPDATEs it rather than raising
        # ``IntegrityError``. We read both the existing ``state`` and ``sum`` so
        # we can find the first bucket that diverges from the recorder.
        existing_states: dict[float, float] = {}
        existing_sums: dict[float, float] = {}
        try:  # ruff:ignore[too-many-statements-in-try-clause]
            recorder = get_instance(self.hass)
            earliest = dt_util.as_utc(min(starts))
            latest = dt_util.as_utc(max(starts))
            existing = await recorder.async_add_executor_job(
                statistics_during_period,
                self.hass,
                earliest,
                latest + timedelta(seconds=1),
                {statistic_id},
                "hour",
                None,
                {"start", "state", "sum"},
            )
            for row in existing.get(statistic_id, []):
                row_start = self._stat_row_start(row)
                if row_start is None:
                    continue
                row_state = safe_float(row.get("state"))
                if row_state is not None:
                    existing_states[row_start] = row_state
                row_sum = safe_float(row.get("sum"))
                if row_sum is not None:
                    existing_sums[row_start] = row_sum
        except BACKGROUND_TASK_ERRORS as err:
            _LOGGER.debug("Jackery recorder existing-statistics lookup failed: %s", err)

        # Walk buckets front-to-back accumulating the running sum. Skip the
        # leading contiguous prefix whose stored state *and* sum already match
        # the recorder, then re-emit every bucket from the first divergence
        # onward - a new start, a changed state, or a stale sum. Re-emitting the
        # whole tail keeps the submitted sum sequence monotonic so HA never sees
        # a spurious counter reset, and applies Jackery's historical corrections
        # instead of silently dropping them.
        statistics: list[StatisticData] = []
        cumulative = offset
        imported_any = False
        diverged = False
        for start, state in zip(starts, states, strict=False):
            cumulative = round(cumulative + state, 5)
            if not diverged:
                start_ts = self._stat_row_start({"start": start})
                prior_state = (
                    existing_states.get(start_ts) if start_ts is not None else None
                )
                prior_sum = (
                    existing_sums.get(start_ts) if start_ts is not None else None
                )
                state_matches = (
                    prior_state is not None
                    and abs(prior_state - state) < _STATISTICS_IMPORT_STATE_TOLERANCE
                )
                sum_matches = (
                    prior_sum is not None
                    and abs(prior_sum - cumulative) < _STATISTICS_IMPORT_STATE_TOLERANCE
                )
                if state_matches and sum_matches:
                    continue
                diverged = True
            statistics.append(StatisticData(start=start, state=state, sum=cumulative))
            imported_any = True
        if not imported_any:
            self._stat_import_last_sig[statistic_id] = series_signature
            return True, 0
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
        except BACKGROUND_TASK_ERRORS as err:
            _LOGGER.warning(
                "Could not import %d app chart statistics for %s: %s",
                len(statistics),
                statistic_id,
                exception_debug_message(err),
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
        enabled_date_types = self._enabled_app_chart_date_types()
        for device_id, payload in snapshot.items():
            name_prefix = self._app_chart_name_prefix(device_id, payload)
            for section_prefix, stat_key, metric_key, label in APP_CHART_STAT_METRICS:
                for date_type, bucket, bucket_label in APP_CHART_STAT_PERIODS:
                    if date_type not in enabled_date_types:
                        continue
                    section = f"{section_prefix}_{date_type}"
                    source = payload.get(section)
                    if not isinstance(source, dict):
                        continue
                    source = gate_payload_section(
                        TransportSource.HTTP,
                        section,
                        source,
                    )
                    if not source:
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

    async def _async_fetch_historical_app_chart_source(  # ruff:ignore[too-many-return-statements, too-many-arguments]
        self,
        *,
        device_id: str,
        system_id: str | None,
        ct_device_id: str | None = None,
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
        if section_prefix == APP_SECTION_CT_STAT:
            return await self.api.async_get_device_ct_stat(
                ct_device_id or device_id,
                **kwargs,
            )
        if section_prefix == APP_SECTION_EPS_STAT:
            return await self.api.async_get_device_eps_stat(
                device_id,
                **kwargs,
            )
        return {}

    async def _collect_repair_buckets(
        self,
        *,
        device_id: str,
        system_id: str | None,
        ct_device_id: str | None,
        prefixes: tuple[str, ...],
        period_plan: tuple[tuple[str, list[date]], ...],
    ) -> tuple[
        dict[tuple[str, str, date], dict[str, Any]],
        dict[str, tuple[str, str]],
        int,
    ]:
        """Fetch and first-gate every historical repair bucket (collect pass).

        Each surviving bucket is keyed by ``(prefix, date_type, period_start)``
        so the containment check can pair a shorter period with the exact
        longer-period container that contains it, rather than only the single
        period of one outer iteration. The transport-neutral first gate
        (:func:`gate_payload_section`) is applied unchanged before a bucket is
        retained.

        Args:
            device_id: The Jackery device being repaired.
            system_id: The device's system id, if known.
            ct_device_id: The smart-meter accessory device id, if any.
            prefixes: The distinct section prefixes to fetch per period.
            period_plan: The ``(date_type, period_starts)`` backfill plan.

        Returns:
            A ``(collected, period_meta_by_type, fetch_failed_count)`` triple.
            ``collected`` maps ``(prefix, date_type, period_start)`` to the
            first-gated source; ``period_meta_by_type`` maps each date type to
            its ``(bucket, bucket_label)``; ``fetch_failed_count`` counts the
            per-section fetch failures.
        """
        collected: dict[tuple[str, str, date], dict[str, Any]] = {}
        period_meta_by_type: dict[str, tuple[str, str]] = {}
        fetch_failed_count = 0

        for date_type, period_starts in period_plan:
            period_meta = self._app_chart_period_meta(date_type)
            if period_meta is None:
                continue
            bucket, bucket_label = period_meta
            for period_start in period_starts:
                for section_prefix in prefixes:
                    try:
                        fetched_source = (
                            await self._async_fetch_historical_app_chart_source(
                                device_id=device_id,
                                system_id=system_id,
                                ct_device_id=ct_device_id,
                                section_prefix=section_prefix,
                                date_type=date_type,
                                period_start=period_start,
                            )
                        )
                    except JackeryAuthError as err:
                        fetch_failed_count += 1
                        _LOGGER.debug(
                            "Jackery statistics backfill auth-rejected for "
                            "%s %s %s; skipping this bucket while live HTTP "
                            "polling remains authoritative: %s",
                            device_id,
                            section_prefix,
                            period_start.isoformat(),
                            exception_debug_message(err),
                        )
                        continue
                    except JackeryError as err:
                        fetch_failed_count += 1
                        _LOGGER.debug(
                            "Jackery statistics backfill fetch failed for %s %s %s: %s",
                            device_id,
                            section_prefix,
                            period_start.isoformat(),
                            err,
                        )
                        continue
                    if fetched_source:
                        section = f"{section_prefix}_{date_type}"
                        gated_source = gate_payload_section(
                            TransportSource.HTTP,
                            section,
                            fetched_source,
                        )
                        if gated_source:
                            collected[section_prefix, date_type, period_start] = (
                                gated_source
                            )
                            period_meta_by_type[date_type] = (bucket, bucket_label)

                await asyncio.sleep(0)

        return collected, period_meta_by_type, fetch_failed_count

    @staticmethod
    def _repair_containment_violations(
        *,
        collected: dict[tuple[str, str, date], dict[str, Any]],
        payload: dict[str, Any],
        to_date: date,
    ) -> set[tuple[str, str, date]]:
        """Return collected buckets that break the §2.2 period hierarchy.

        Mirrors :meth:`_gate_snapshot_period_hierarchy` for the backfill path,
        but validates every shorter-period bucket against the longer-period
        container that *actually contains it*, identified from the bucket's own
        ``period_start`` (never ``to_date`` or the current snapshot). This is
        what makes historical and multi-year backfill correct: a 2024 month is
        compared to the 2024 year, not the 2026 snapshot.

        For each shorter period a minimal flat unit is assembled holding only
        that bucket and its containing longer-period bucket(s), and the
        detector is run with ``today`` anchored *inside* the shorter period so
        its ``week_inside_current_*`` guards resolve true for the bucket under
        test. A month-straddling week skips the month check (mirroring the
        detector's ``week_inside_current_month`` guard).

        Containers are resolved against ``collected`` first. When a container
        was not fetched, the current snapshot's same-period section is used as
        a fallback ceiling *only* when that container period is the current
        calendar period (matched via :func:`app_period_range` against
        ``to_date``); this restores the current-snapshot coverage of the live
        gate without ever comparing a historical bucket to a different
        (current) snapshot period. When the year container is still absent, the
        PV lifetime ``PAYLOAD_STATISTIC`` total is used as the year-level
        ceiling; for non-PV prefixes there is no authoritative ceiling, so the
        bucket is imported without a cross-period withhold rather than
        over-blocked. The exceeding shorter period is named by
        ``warning.reference_section`` and is withheld; the lifetime
        ``PAYLOAD_STATISTIC`` source itself is never withheld.

        Args:
            collected: Fetched, first-gated buckets keyed by
                ``(prefix, date_type, period_start)``.
            payload: The device snapshot, consulted for its current-period
                container sections and its lifetime ``PAYLOAD_STATISTIC`` total
                as a PV year-level ceiling.
            to_date: The repair window's end date, used to identify which
                container period is the current calendar period.

        Returns:
            The set of ``(prefix, date_type, period_start)`` keys to withhold.
        """
        statistic = payload.get(PAYLOAD_STATISTIC)
        withheld: set[tuple[str, str, date]] = set()

        def _container_keys(
            prefix: str,
            date_type: str,
            period_start: date,
        ) -> list[tuple[str, str, date]]:
            if date_type == DATE_TYPE_WEEK:
                week_begin, week_end = app_period_range(
                    DATE_TYPE_WEEK, today=period_start
                )
                keys: list[tuple[str, str, date]] = []
                straddles_month = (
                    week_begin.month != week_end.month
                    or week_begin.year != week_end.year
                )
                if not straddles_month:
                    keys.append((
                        prefix,
                        DATE_TYPE_MONTH,
                        date(week_begin.year, week_begin.month, 1),
                    ))
                if week_begin.year == week_end.year:
                    keys.append((prefix, DATE_TYPE_YEAR, date(week_begin.year, 1, 1)))
                return keys
            if date_type == DATE_TYPE_MONTH:
                return [(prefix, DATE_TYPE_YEAR, date(period_start.year, 1, 1))]
            return []

        def _container_source(
            container_key: tuple[str, str, date],
        ) -> dict[str, Any] | None:
            fetched = collected.get(container_key)
            if fetched is not None:
                return fetched
            container_prefix, container_type, container_start = container_key
            is_current_period = app_period_range(
                container_type, today=container_start
            ) == app_period_range(container_type, today=to_date)
            if not is_current_period:
                return None
            snapshot_source = payload.get(f"{container_prefix}_{container_type}")
            return snapshot_source if isinstance(snapshot_source, dict) else None

        for (prefix, date_type, period_start), source in collected.items():
            if date_type not in {DATE_TYPE_WEEK, DATE_TYPE_MONTH}:
                continue
            unit: dict[str, Any] = {f"{prefix}_{date_type}": source}
            year_key = (prefix, DATE_TYPE_YEAR, date(period_start.year, 1, 1))
            year_present = False
            for container_key in _container_keys(prefix, date_type, period_start):
                container_source = _container_source(container_key)
                if container_source is None:
                    continue
                _container_prefix, container_type, _container_start = container_key
                unit[f"{prefix}_{container_type}"] = container_source
                if container_key == year_key:
                    year_present = True
            if (
                not year_present
                and prefix == APP_SECTION_PV_STAT
                and isinstance(statistic, dict)
            ):
                unit[PAYLOAD_STATISTIC] = statistic

            warnings = app_data_quality_warnings(unit, today=period_start)
            for warning in warnings:
                reference_section = warning.reference_section
                if reference_section == PAYLOAD_STATISTIC:
                    continue
                violating_prefix, violating_type = reference_section.rsplit("_", 1)
                if (violating_prefix, violating_type) == (prefix, date_type):
                    # The detector knows both sides of the comparison; the
                    # caller's summary warning names only the dropped section.
                    # Without the two values a drop cannot be attributed from
                    # the log: a bucket exceeding a *correct* container is bad
                    # data, while the same bucket exceeding a container the
                    # cloud reports as 0 is a cloud self-contradiction — and
                    # the two demand opposite remedies.
                    _LOGGER.warning(
                        "Jackery containment violation %s on %s (%s): "
                        "container %s=%r vs bucket %s=%r [metric=%s, unit=%s]",
                        warning.reason,
                        reference_section,
                        period_start.isoformat(),
                        warning.source_section,
                        warning.source_value,
                        warning.reference_section,
                        warning.reference_value,
                        warning.metric_key,
                        source.get(APP_STAT_UNIT),
                    )
                    withheld.add((prefix, date_type, period_start))

        return withheld

    async def _import_collected_repair_buckets(  # ruff:ignore[too-many-arguments]
        self,
        *,
        device_id: str,
        name_prefix: str,
        collected: dict[tuple[str, str, date], dict[str, Any]],
        period_meta_by_type: dict[str, tuple[str, str]],
        withheld: set[tuple[str, str, date]],
        to_date: date,
    ) -> tuple[int, int]:
        """Import surviving collected buckets as external + entity statistics.

        Buckets in ``withheld`` are intentionally dropped: they are neither
        repaired nor failed. Entity batches emit one tuple per
        ``(date_type, period_start)`` group so distinct period starts of the
        same date type are not collapsed into a single overwriting dict.

        Args:
            device_id: The Jackery device being repaired.
            name_prefix: The user-readable statistic name prefix.
            collected: All fetched, first-gated buckets.
            period_meta_by_type: Each date type's ``(bucket, bucket_label)``.
            withheld: Buckets to drop for §2.2 containment violations.
            to_date: The repair window's end date (the local "today").

        Returns:
            A ``(repaired_buckets, failed_buckets)`` accounting pair.
        """
        repaired_buckets = 0
        failed_buckets = 0
        survivors = {
            key: source for key, source in collected.items() if key not in withheld
        }

        for (
            section_prefix,
            stat_key,
            metric_key,
            label,
        ) in APP_CHART_STAT_METRICS:
            for (prefix, date_type, _period_start), source in survivors.items():
                if prefix != section_prefix:
                    continue
                meta = period_meta_by_type.get(date_type)
                if meta is None:
                    continue
                bucket, bucket_label = meta
                section = f"{section_prefix}_{date_type}"
                points = trend_series_points(
                    source,
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
                    # An idempotent recorder upsert legitimately writes zero
                    # rows when every stored state and cumulative sum already
                    # matches. Count the verified source points as repaired so
                    # the persistent bootstrap marker is not left at 0/0 and
                    # retried forever.
                    repaired_buckets += bucket_count or len(points)
                else:
                    failed_buckets += 1

        # Entity-backed statistics (``sensor.xxx``) were removed here: HA's own
        # sensor recorder already compiles those ids from live states, so a
        # parallel ``async_import_statistics`` into the same id fought the
        # recorder and produced corrupt cumulative sums (negative day deltas,
        # ~51k spikes). The external ``jackery_solarvault:`` statistics written
        # by the loop above are the sole, authoritative source; the Energy
        # Dashboard consumes those.
        return repaired_buckets, failed_buckets

    async def _async_repair_missing_app_chart_statistics(  # ruff:ignore[too-many-locals]
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
        ct_device_id = self._smart_meter_accessory_device_id(
            payload,
        ) or self._smart_meter_accessory_device_id(index)
        prefixes = tuple(dict.fromkeys(metric[0] for metric in APP_CHART_STAT_METRICS))

        enabled_date_types = self._enabled_app_chart_date_types()
        period_plan_candidates: tuple[tuple[str, list[date]], ...] = (
            (DATE_TYPE_MONTH, self._iter_calendar_months(from_date, to_date)),
            (
                DATE_TYPE_YEAR,
                [
                    date(year, 1, 1)
                    for year in self._iter_calendar_years(from_date, to_date)
                ],
            ),
            (DATE_TYPE_WEEK, self._iter_calendar_weeks(from_date, to_date)),
        )
        period_plan = tuple(
            entry for entry in period_plan_candidates if entry[0] in enabled_date_types
        )
        # Month charts contain the per-day buckets needed by the Energy
        # Dashboard. Fetch and import month+year first, before the much larger
        # historical week plan. This makes useful history durable even if a
        # reload or transient outage interrupts the later phase.
        fast_plan = tuple(
            item for item in period_plan if item[0] in {DATE_TYPE_MONTH, DATE_TYPE_YEAR}
        )
        week_plan = tuple(item for item in period_plan if item[0] == DATE_TYPE_WEEK)
        phase_plans = tuple(plan for plan in (fast_plan, week_plan) if plan)

        all_collected: dict[tuple[str, str, date], dict[str, Any]] = {}
        all_period_meta: dict[str, tuple[str, str]] = {}
        all_withheld: set[tuple[str, str, date]] = set()
        repaired_buckets = 0
        failed_buckets = 0

        for phase_plan in phase_plans:
            (
                phase_collected,
                phase_period_meta,
                phase_fetch_failed,
            ) = await self._collect_repair_buckets(
                device_id=device_id,
                system_id=system_id,
                ct_device_id=ct_device_id,
                prefixes=prefixes,
                period_plan=phase_plan,
            )
            failed_buckets += phase_fetch_failed
            all_collected.update(phase_collected)
            all_period_meta.update(phase_period_meta)
            phase_withheld = self._repair_containment_violations(
                collected=all_collected,
                payload=payload,
                to_date=to_date,
            )
            all_withheld.update(phase_withheld)
            (
                phase_repaired,
                phase_import_failed,
            ) = await self._import_collected_repair_buckets(
                device_id=device_id,
                name_prefix=name_prefix,
                collected=phase_collected,
                period_meta_by_type=all_period_meta,
                withheld=phase_withheld,
                to_date=to_date,
            )
            repaired_buckets += phase_repaired
            failed_buckets += phase_import_failed

        if all_withheld:
            withheld_names = sorted(
                f"{prefix}_{date_type} ({period_start.isoformat()})"
                for prefix, date_type, period_start in all_withheld
            )
            _LOGGER.warning(
                "Withholding repaired app chart section(s) %s for %s from "
                "recorder: violates the AGENTS.md §2.2 period hierarchy "
                "(shorter period exceeds its longer-period container)",
                ", ".join(withheld_names),
                device_id,
            )

        return repaired_buckets, failed_buckets

    @staticmethod
    def _gate_snapshot_period_hierarchy(
        snapshot: dict[str, dict[str, Any]],
        *,
        today: date,
    ) -> dict[str, dict[str, Any]]:
        """Withhold hierarchy-violating period sections before recorder import.

        AGENTS.md §2.2 requires every period total to stay within its
        longer-period container (``daily <= weekly <= monthly <= yearly <=
        lifetime``). Contradictions are detected by
        :func:`app_data_quality_warnings`; the exceeding (shorter) period is
        named as the warning ``reference_section``. Drop exactly those sections
        per device via the transport-neutral ingest gate so only validated
        period data reaches the HA Recorder. The lifetime statistic total is
        already bounded by ``guard_statistic_totals_from_year`` upstream, so its
        ``PAYLOAD_STATISTIC`` source is never withheld here.
        """
        gated: dict[str, dict[str, Any]] = {}
        for device_id, payload in snapshot.items():
            gated[device_id] = (
                JackerySolarVaultCoordinator._gate_period_hierarchy_from_warnings(
                    payload,
                    app_data_quality_warnings(payload, today=today),
                )
            )
        return gated

    @staticmethod
    def _gate_period_hierarchy_from_warnings(
        entry: dict[str, Any],
        warnings: Sequence[object],
    ) -> dict[str, Any]:
        """Withhold only contradictory period sections from a live entry.

        The gate receives already-computed data-quality warnings and removes
        exactly the named period sections. Property, accessory, MQTT, BLE and
        other live payloads are copied through unchanged; Recorder validation
        must never throttle or discard live telemetry.
        """
        violating = frozenset(
            str(reference)
            for warning in warnings
            if (
                (reference := getattr(warning, "reference_section", None)) is not None
                and reference != PAYLOAD_STATISTIC
            )
        )
        return gate_period_hierarchy_for_recorder(entry, violating)

    async def _async_import_and_repair_app_chart_statistics(
        self,
        snapshot: dict[str, dict[str, Any]],
    ) -> None:
        """Import current app chart buckets, then repair missed history.

        Restored per deep review 2026-07-25 (HA Recorder & long-term
        statistics guidelines): without the per-device repair loop a cloud
        or HA outage across a calendar boundary left permanent gaps in the
        Recorder / Energy Dashboard. The current-bucket import stays the
        single bounded job; the repair loop re-fetches missed periods via
        the app's beginDate/endDate stat endpoints and re-imports them as
        external statistics. Entity (``sensor.``) statistics stay removed —
        the external ``jackery_solarvault:`` ids are authoritative.
        """
        if not snapshot:
            return
        await self._async_ensure_statistics_backfill_state_loaded()
        today = self._local_today()
        repair_ok: dict[str, bool] = {}
        repair_counts: dict[str, tuple[int, int]] = {}

        successful_devices = await self._async_import_current_app_chart_statistics_job(
            snapshot,
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
                    "Jackery statistics backfill for %s repaired %d bucket(s), "
                    "%d step(s) failed",
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
                await self._async_import_app_chart_statistics(snapshot),
            )
        changed = bool(repair_counts)
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

    # ------------------------------------------------------------------
    # Coordinator update cycle (merge of HTTP + MQTT + caches)
    # ------------------------------------------------------------------

    async def _async_update_data(self) -> dict[str, dict[str, Any]]:
        """Track the authoritative HTTP cycle so unload can cancel and drain it."""
        if self._shutdown_started:
            return dict(self.data or {})
        cycle_started = time.monotonic()
        previous_started = self._last_http_cycle_started_monotonic
        self._last_http_cycle_started_monotonic = cycle_started
        if previous_started != float("-inf"):
            start_gap = max(0.0, cycle_started - previous_started)
            self._polling_diagnostics["last_cycle_start_gap_sec"] = round(
                start_gap,
                3,
            )
            self._polling_diagnostics["max_cycle_start_gap_sec"] = round(
                max(
                    float(
                        self._polling_diagnostics.get(
                            "max_cycle_start_gap_sec",
                            0.0,
                        )
                    ),
                    start_gap,
                ),
                3,
            )
        current_task = asyncio.current_task()
        if current_task is not None:
            self._active_http_update_tasks.add(current_task)
        try:
            return await self._async_update_data_with_timeout()
        finally:
            self._set_next_poll_delay(cycle_started, time.monotonic())
            if current_task is not None:
                self._active_http_update_tasks.discard(current_task)

    async def _async_update_data_with_timeout(self) -> dict[str, dict[str, Any]]:
        """Poll with a hard timeout while preserving last-known live data."""
        cycle_started = time.monotonic()
        timeout_sec = self._poll_cycle_timeout_seconds()
        cycle_baseline = copy.deepcopy(self.data or {})
        try:
            async with asyncio.timeout(timeout_sec):
                result = await self._async_update_data_guarded()
        except ConfigEntryAuthFailed as err:
            self.entry.async_start_reauth(self.hass)
            if self.data:
                _LOGGER.debug(
                    "Jackery HTTP auth failed; keeping last coordinator data "
                    "available while reauth is open: %s",
                    err,
                )
                return dict(self.data)
            raise
        except TimeoutError as err:
            self._note_polling_timeout(cycle_started)
            msg = (
                "Jackery coordinator update timed out after "
                f"{timeout_sec:.2f}s; rescheduling next cycle"
            )
            if self.data:
                return dict(self.data)
            raise UpdateFailed(msg) from err
        else:
            self._recover_polling_timeout()
            return self._merge_concurrent_coordinator_updates(
                cycle_baseline,
                result,
            )

    async def _async_update_data_guarded(  # ruff:ignore[complex-structure, too-many-branches, too-many-locals, too-many-statements]
        self,
        _retry_discovery_once: bool = True,
    ) -> dict[str, dict[str, Any]]:
        """Run one HTTP-primary update and reconcile supplementary snapshots."""
        # A background transport/task may have recorded an auth failure. Do NOT
        # re-raise it as ConfigEntryAuthFailed here: that opens reauth, and HA
        # then STOPS this coordinator until the user re-authenticates — so a
        # background MQTT/BLE rejection (or a transient single-session 401 the
        # HTTP client already auto-relogins around) would pause HTTP polling
        # indefinitely. The owner forbids exactly this: "HTTP polling must never
        # stop; MQTT/BLE must never trigger reauth" (2026-07-05). Genuine auth
        # is owned solely by the authoritative HTTP property fetch below — if
        # the credentials are truly invalid, that request raises
        # ConfigEntryAuthFailed on its own. So clear the deferred notice and
        # keep polling.
        if self._mqtt_mgr.auth_failure_message is not None:
            message = self._mqtt_mgr.auth_failure_message
            self._mqtt_mgr.auth_failure_message = None
            _LOGGER.debug(
                "Jackery: cleared a deferred background auth notice without "
                "pausing HTTP polling (the HTTP fetch owns reauth): %s",
                message,
            )

        # The passive reconnect path (``_async_ensure_mqtt`` without
        # ``wait_connected=True``) does not observe the CONNACK outcome
        # directly. If the MQTT client recorded broker auth rejections, treat
        # that as an app-conflict pause and keep the HTTP poll alive.
        if self._mqtt is not None:
            streak = self._mqtt.consecutive_auth_failures
            if streak > 0 and not self._mqtt.is_connected:
                last_error = self._mqtt.diagnostics.get("last_error") or "unknown"
                self._pause_mqtt_after_auth_failure(last_error, streak=streak)
                if (
                    streak >= MQTT_AUTH_FAILURE_TOLERANCE
                    and self._persisted_mqtt_session is not None
                ):
                    self._schedule_background_once(
                        "mqtt_session_cache_invalidate",
                        lambda: self._async_invalidate_mqtt_session_cache(
                            f"broker rejected cached session after {streak} attempts",
                        ),
                        name=f"{DOMAIN}_mqtt_session_cache_invalidate",
                    )
        # Local-first SystemBody fill (F6): workModel/maxSysOutPw/... have
        # no HTTP source; the throttled BLE-first query keeps them alive
        # without a healthy cloud-MQTT session. Never awaited in the poll.
        self._schedule_background_once(
            "system_info_query",
            lambda: self._async_query_system_info_for_missing(ensure_mqtt=False),
            name=f"{DOMAIN}_system_info_query",
        )
        if not self._device_index:
            await self.async_discover()
            if not self._device_index:
                msg = "No Jackery devices found."
                raise UpdateFailed(msg)

        self._schedule_background_once(
            "runtime_discovery_refresh",
            self._async_refresh_discovery_if_due,
            name=f"{DOMAIN}_runtime_discovery_refresh",
        )

        started = time.monotonic()

        # Per-system calls honour their own refresh intervals. Inside a
        # single update cycle we call each endpoint at most once; across
        # cycles the cache only refreshes when its TTL expired.
        system_cache: dict[str, dict[str, Any]] = {}
        # Track system_ids whose slow-metric TTL expired during this
        # cycle so we can refresh them in a background task without
        # blocking the main coordinator update.
        systems_needing_refresh: set[str] = set()
        # Historical month requests required to repair a month-only year
        # payload must follow the same background-only rule. The foreground
        # cycle only reads/seeds their TTL entries.
        historical_month_refreshers: list[Callable[[], Awaitable[Any]]] = []
        # Track devices whose per-device slow-metric cache expired this cycle.
        # Each tuple carries the parameters ``_fetch_device_extras`` needs so
        # the background pass can re-fetch them non-stale, mirroring the
        # system-level refresh. Keyed by dev_id to de-duplicate.
        devices_needing_refresh: dict[
            str, tuple[str, str | None, str | None, str | None]
        ] = {}
        # Track devices whose supplementary L5 enrichment caches (Shelly Cloud
        # realtime, smart-plug / meter-head socket statistics) went stale this
        # cycle. They are served ``stale_ok=True`` on the critical path so a
        # cold or slow third-party fetch never gates the fast HTTP poll; the
        # background pass re-runs the enrichers ``stale_ok=False`` off the
        # critical path.
        devices_needing_enrichment_refresh: set[str] = set()

        # At the start of each cycle: if the local date rolled over, wipe
        # the day-bounded caches so we don't keep serving yesterday's
        # final values for up to self._slow_metrics_interval_sec.
        today = self._local_today()
        local_daily_allow_new_anchor = (
            self._cached_date is not None and self._cached_date != today
        )
        if local_daily_allow_new_anchor:
            cached_date = cast("date", self._cached_date)
            _LOGGER.debug(
                "Jackery: day rollover (%s -> %s), clearing day-bounded caches",
                self._cached_date,
                today,
            )
            cache_keys_to_clear = {
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
            }
            if cached_date.isocalendar()[:2] != today.isocalendar()[:2]:
                cache_keys_to_clear.update({
                    self._app_period_section(APP_SECTION_PV_TRENDS, DATE_TYPE_WEEK),
                    self._app_period_section(APP_SECTION_HOME_TRENDS, DATE_TYPE_WEEK),
                    self._app_period_section(
                        APP_SECTION_BATTERY_TRENDS,
                        DATE_TYPE_WEEK,
                    ),
                    self._app_period_section(APP_SECTION_PV_STAT, DATE_TYPE_WEEK),
                    self._app_period_section(APP_SECTION_BATTERY_STAT, DATE_TYPE_WEEK),
                    self._app_period_section(APP_SECTION_HOME_STAT, DATE_TYPE_WEEK),
                    self._app_period_section(APP_SECTION_CT_STAT, DATE_TYPE_WEEK),
                    self._app_period_section(APP_SECTION_EPS_STAT, DATE_TYPE_WEEK),
                })
            if (cached_date.year, cached_date.month) != (
                today.year,
                today.month,
            ):
                cache_keys_to_clear.update({
                    self._app_period_section(APP_SECTION_PV_TRENDS, DATE_TYPE_MONTH),
                    self._app_period_section(APP_SECTION_HOME_TRENDS, DATE_TYPE_MONTH),
                    self._app_period_section(
                        APP_SECTION_BATTERY_TRENDS,
                        DATE_TYPE_MONTH,
                    ),
                    self._app_period_section(APP_SECTION_PV_STAT, DATE_TYPE_MONTH),
                    self._app_period_section(APP_SECTION_BATTERY_STAT, DATE_TYPE_MONTH),
                    self._app_period_section(APP_SECTION_HOME_STAT, DATE_TYPE_MONTH),
                    self._app_period_section(APP_SECTION_CT_STAT, DATE_TYPE_MONTH),
                    self._app_period_section(APP_SECTION_EPS_STAT, DATE_TYPE_MONTH),
                })
            if cached_date.year != today.year:
                cache_keys_to_clear.update({
                    self._app_period_section(APP_SECTION_PV_TRENDS, DATE_TYPE_YEAR),
                    self._app_period_section(APP_SECTION_HOME_TRENDS, DATE_TYPE_YEAR),
                    self._app_period_section(
                        APP_SECTION_BATTERY_TRENDS,
                        DATE_TYPE_YEAR,
                    ),
                    self._app_period_section(APP_SECTION_PV_STAT, DATE_TYPE_YEAR),
                    self._app_period_section(APP_SECTION_BATTERY_STAT, DATE_TYPE_YEAR),
                    self._app_period_section(APP_SECTION_HOME_STAT, DATE_TYPE_YEAR),
                    self._app_period_section(APP_SECTION_CT_STAT, DATE_TYPE_YEAR),
                    self._app_period_section(APP_SECTION_EPS_STAT, DATE_TYPE_YEAR),
                })
            for cache in self._slow_cache.values():
                for cache_key in cache_keys_to_clear:
                    cache.pop(cache_key, None)
            # Stat-import dedup cache spans calendar days; stale signatures
            # from yesterday would prevent fresh buckets from being written to
            # the HA recorder after midnight.
            self._stat_import_last_sig.clear()
        self._cached_date = today

        async def _get_with_ttl_for(  # ruff:ignore[too-many-return-statements, too-many-branches, too-many-arguments]
            cache: dict[str, tuple[float, Any]],
            cache_key: str,
            ttl_sec: int,
            fetcher: Callable[[], Awaitable[Any]],
            default: Any,  # generic TTL cache over arbitrary payloads  # ruff:ignore[any-type]
            *,
            backoff_key: str | None = None,
            stale_ok: bool = False,
        ) -> Any:  # generic TTL cache over arbitrary payloads  # ruff:ignore[any-type]
            """Generic TTL cache helper operating on any dict."""
            now = time.monotonic()
            entry = cache.get(cache_key)
            if backoff_key and self._endpoint_backoff_active(backoff_key, now):
                if entry is not None:
                    return entry[1]
                return default
            if entry is not None:
                last_ts, last_value = entry
                # ``0.0`` is the explicit cold-cache sentinel seeded by the
                # foreground stale-ok path. It must stay stale even during the
                # first TTL window after HA/WSL boot, when monotonic time is
                # still smaller than ``ttl_sec``.
                if last_ts > 0.0 and now - last_ts < ttl_sec:
                    return last_value
                # TTL expired — return stale data when caller allows it.
                if stale_ok:
                    return last_value
            elif stale_ok:
                seeded_default = copy.deepcopy(default)
                cache[cache_key] = (0.0, seeded_default)
                return seeded_default
            try:
                value = await fetcher()
            except JackeryAuthError as err:
                if backoff_key:
                    self._endpoint_backoff_note_failure(backoff_key, err)
                _LOGGER.debug(
                    "%s fetch was auth-rejected; using cached/default value "
                    "while the primary HTTP property poll remains authoritative: %s",
                    cache_key,
                    exception_debug_message(err),
                )
                if entry is not None:
                    return entry[1]
                return default
            except JackeryError as err:
                if backoff_key and self._endpoint_backoff_note_failure(
                    backoff_key,
                    err,
                ):
                    if entry is not None:
                        return entry[1]
                    return default
                if backoff_key is not None and self._is_backoffable_timeout(
                    backoff_key,
                    err,
                ):
                    # A repeatedly-timing-out Shelly realtime endpoint carries no
                    # cloud code, so note_failure recorded nothing above. Open a
                    # timeout backoff window so the background refresh stops
                    # re-firing at the unreachable device every cycle.
                    self._endpoint_backoff_note_timeout(backoff_key)
                    suppressed = True
                else:
                    suppressed = False
                # The cached/default value is returned below so the integration
                # keeps working (HTTP stays authoritative). Once we have decided
                # to suppress an endpoint (it just entered a backoff window) the
                # per-cycle failure is expected and self-healing, so log it at
                # DEBUG to avoid spamming the user log. A failure on an endpoint
                # we are NOT backing off (e.g. a one-off smart-meter enrichment
                # timeout) stays at WARNING so genuinely-new problems stay visible.
                _LOGGER.log(
                    _slow_fetch_failure_log_level(err, suppressed=suppressed),
                    "%s fetch failed, using cached/default value: %s",
                    cache_key,
                    exception_debug_message(err),
                )
                if entry is not None:
                    return entry[1]
                return default
            cache[cache_key] = (now, value)
            if backoff_key:
                self._endpoint_backoff_note_success(backoff_key)
            return value

        async def _get_with_ttl(  # ruff:ignore[too-many-arguments]
            sys_id: str,
            cache_key: str,
            ttl_sec: int,
            fetcher: Callable[[str], Awaitable[Any]],
            default: Any,  # generic TTL cache over arbitrary payloads  # ruff:ignore[any-type]
            *,
            backoff_key: str | None = None,
            stale_ok: bool = False,
        ) -> Any:  # generic TTL cache over arbitrary payloads  # ruff:ignore[any-type]
            """System-scoped TTL cache wrapper."""
            per_system = self._slow_cache.setdefault(sys_id, {})
            return await _get_with_ttl_for(
                per_system,
                cache_key,
                ttl_sec,
                lambda: fetcher(sys_id),
                default,
                backoff_key=backoff_key,
                stale_ok=stale_ok,
            )

        async def _fetch_shelly_cloud_devices(
            *,
            stale_ok: bool = False,
        ) -> list[dict[str, Any]]:
            """Return app-linked Shelly Cloud devices from the documented API.

            Shelly Cloud is a third-party (L5-class) enrichment. On the fast
            L3 critical path it is served ``stale_ok=True`` so an expired or
            cold TTL never blocks the property cycle on a fresh Shelly
            round-trip; the background slow-refresh pass re-fetches it
            non-stale.
            """
            per_shelly = self._slow_cache.setdefault("shelly_cloud", {})
            if stale_ok and "devices" not in per_shelly:
                per_shelly["devices"] = (0.0, [])
                return []
            devices = await _get_with_ttl_for(
                per_shelly,
                "devices",
                self._price_config_interval_sec,
                self.api.async_get_shelly_devices,
                [],
                stale_ok=stale_ok,
            )
            if not isinstance(devices, list):
                return []
            return [item for item in devices if isinstance(item, dict)]

        async def _fetch_system(  # ruff:ignore[too-many-locals]
            sys_id: str,
            *,
            stale_ok: bool = False,
        ) -> dict[str, Any]:
            if sys_id in system_cache:
                return system_cache[sys_id]
            # Keep slow endpoint failures isolated per slot. A single
            # TimeoutError / aiohttp.ClientError / SSL drop must not abort the
            # whole update cycle and mark every Jackery entity unavailable.
            # Map failures to the same defaults already wired in the
            # ``_get_with_ttl(... default)`` calls below.
            slow_results = await asyncio.gather(
                _get_with_ttl(
                    sys_id,
                    PAYLOAD_STATISTIC,
                    self._slow_metrics_interval_sec,
                    self.api.async_get_system_statistic,
                    {},
                    stale_ok=stale_ok,
                ),
                _get_with_ttl(
                    sys_id,
                    PAYLOAD_ALARM,
                    self._slow_metrics_interval_sec,
                    self.api.async_get_alarm,
                    None,
                    stale_ok=stale_ok,
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
                    stale_ok=stale_ok,
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
                    stale_ok=stale_ok,
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
                    stale_ok=stale_ok,
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
                    stale_ok=stale_ok,
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
                    stale_ok=stale_ok,
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
                    stale_ok=stale_ok,
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
                    stale_ok=stale_ok,
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
                    stale_ok=stale_ok,
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
                    stale_ok=stale_ok,
                ),
                _get_with_ttl(
                    sys_id,
                    self._app_period_section(
                        APP_SECTION_BATTERY_TRENDS,
                        DATE_TYPE_WEEK,
                    ),
                    self._slow_metrics_interval_sec,
                    lambda sid: self.api.async_get_battery_trends(
                        sid,
                        **self._trend_query_kwargs(DATE_TYPE_WEEK),
                    ),
                    {},
                    stale_ok=stale_ok,
                ),
                _get_with_ttl(
                    sys_id,
                    self._app_period_section(
                        APP_SECTION_BATTERY_TRENDS,
                        DATE_TYPE_MONTH,
                    ),
                    self._slow_metrics_interval_sec,
                    lambda sid: self.api.async_get_battery_trends(
                        sid,
                        **self._trend_query_kwargs(DATE_TYPE_MONTH),
                    ),
                    {},
                    stale_ok=stale_ok,
                ),
                _get_with_ttl(
                    sys_id,
                    self._app_period_section(
                        APP_SECTION_BATTERY_TRENDS,
                        DATE_TYPE_YEAR,
                    ),
                    self._slow_metrics_interval_sec,
                    lambda sid: self.api.async_get_battery_trends(
                        sid,
                        **self._trend_query_kwargs(DATE_TYPE_YEAR),
                    ),
                    {},
                    stale_ok=stale_ok,
                ),
                _get_with_ttl(
                    sys_id,
                    PAYLOAD_DYNAMIC_PRICE,
                    self._price_config_interval_sec,
                    self.api.async_get_dynamic_price,
                    {},
                    backoff_key=f"dynamic_price:{sys_id}",
                    stale_ok=stale_ok,
                ),
                _get_with_ttl(
                    sys_id,
                    PAYLOAD_PRICE,
                    self._price_config_interval_sec,
                    self.api.async_get_power_price,
                    {},
                    stale_ok=stale_ok,
                ),
                _get_with_ttl(
                    sys_id,
                    PAYLOAD_PRICE_SOURCES,
                    self._price_config_interval_sec,
                    self.api.async_get_price_sources,
                    [],
                    stale_ok=stale_ok,
                ),
                _get_with_ttl(
                    sys_id,
                    PAYLOAD_PRICE_HISTORY_CONFIG,
                    self._price_config_interval_sec,
                    self.api.async_get_price_history_config,
                    {},
                    stale_ok=stale_ok,
                ),
                return_exceptions=True,
            )
            # Per-slot defaults match the empty values already passed into
            # the ``_get_with_ttl`` calls above. ``alarm`` is None and
            # ``price_sources`` is a list; everything else collapses to {}.
            slow_defaults: tuple[Any, ...] = (
                {},  # statistic
                None,  # alarm
                {},  # pv_trends
                {},  # pv_trends_week
                {},  # pv_trends_month
                {},  # pv_trends_year
                {},  # home_trends
                {},  # home_trends_week
                {},  # home_trends_month
                {},  # home_trends_year
                {},  # battery_trends
                {},  # battery_trends_week
                {},  # battery_trends_month
                {},  # battery_trends_year
                {},  # dynamic_price
                {},  # price
                [],  # price_sources
                {},  # price_history_config
            )
            slow_safe = tuple(
                default if isinstance(value, BaseException) else value
                for value, default in zip(slow_results, slow_defaults, strict=True)
            )
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
                dynamic_price,
                price,
                price_sources,
                price_history_config,
            ) = slow_safe
            bundle: dict[str, Any] = {
                PAYLOAD_STATISTIC: statistic,
                PAYLOAD_ALARM: alarm,
                PAYLOAD_PV_TRENDS: pv_trends,
                self._app_period_section(
                    APP_SECTION_PV_TRENDS,
                    DATE_TYPE_WEEK,
                ): pv_trends_week,
                self._app_period_section(
                    APP_SECTION_PV_TRENDS,
                    DATE_TYPE_MONTH,
                ): pv_trends_month,
                self._app_period_section(
                    APP_SECTION_PV_TRENDS,
                    DATE_TYPE_YEAR,
                ): pv_trends_year,
                PAYLOAD_HOME_TRENDS: home_trends,
                self._app_period_section(
                    APP_SECTION_HOME_TRENDS,
                    DATE_TYPE_WEEK,
                ): home_trends_week,
                self._app_period_section(
                    APP_SECTION_HOME_TRENDS,
                    DATE_TYPE_MONTH,
                ): home_trends_month,
                self._app_period_section(
                    APP_SECTION_HOME_TRENDS,
                    DATE_TYPE_YEAR,
                ): home_trends_year,
                PAYLOAD_BATTERY_TRENDS: battery_trends,
                self._app_period_section(
                    APP_SECTION_BATTERY_TRENDS,
                    DATE_TYPE_WEEK,
                ): battery_trends_week,
                self._app_period_section(
                    APP_SECTION_BATTERY_TRENDS,
                    DATE_TYPE_MONTH,
                ): battery_trends_month,
                self._app_period_section(
                    APP_SECTION_BATTERY_TRENDS,
                    DATE_TYPE_YEAR,
                ): battery_trends_year,
                PAYLOAD_DYNAMIC_PRICE: dynamic_price,
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

                    def _make_previous_home_month_refresher(
                        cache_key: str,
                        request_kwargs: dict[str, str],
                    ) -> Callable[[], Awaitable[Any]]:
                        async def _refresh() -> Any:  # ruff:ignore[any-type]
                            return await _get_with_ttl(
                                sys_id,
                                cache_key,
                                self._price_config_interval_sec,
                                lambda sid: self.api.async_get_home_trends(
                                    sid,
                                    **request_kwargs,
                                ),
                                {},
                                stale_ok=False,
                            )

                        return _refresh

                    async def _fetch_previous_home_month(
                        month: int,
                        section_prefix: str,
                    ) -> Any:  # forwards arbitrary cached payload  # ruff:ignore[any-type]
                        request_kwargs = app_month_request_kwargs(today.year, month)
                        cache_key = (
                            f"{section_prefix}_{DATE_TYPE_MONTH}_"
                            f"{today.year}_{month:02d}"
                        )
                        cache_entry = self._slow_cache.setdefault(sys_id, {}).get(
                            cache_key
                        )
                        cache_needs_refresh = (
                            cache_entry is None
                            or cache_entry[0] <= 0.0
                            or time.monotonic() - cache_entry[0]
                            >= self._price_config_interval_sec
                        )
                        if stale_ok and cache_needs_refresh:
                            historical_month_refreshers.append(
                                _make_previous_home_month_refresher(
                                    cache_key,
                                    request_kwargs,
                                )
                            )
                        return await _get_with_ttl(
                            sys_id,
                            cache_key,
                            self._price_config_interval_sec,
                            lambda sid: self.api.async_get_home_trends(
                                sid,
                                **request_kwargs,
                            ),
                            {},
                            stale_ok=stale_ok,
                        )

                    # A single 404 (e.g. the device was bought mid-year and
                    # earlier months legitimately do not exist) must not abort
                    # the whole year backfill. ``return_exceptions`` lets the
                    # ``isinstance(source, dict)`` filter below quietly skip
                    # BaseException entries.
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

        async def _fetch_device_extras(  # ruff:ignore[too-many-locals, too-many-statements]
            dev_id: str,
            dev_sn: str | None,
            sys_id: str | None,
            ct_dev_id: str | None,
            *,
            stale_ok: bool = False,
        ) -> dict[str, Any]:
            """Device-level slow metrics (deviceStatistic, OTA, location).

            deviceStatistic: changes on ~5 min boundary, like system stats.
            OTA + location: change practically never → hourly TTL.
            """
            # The CT-statistic endpoint is accessory-scoped; fall back to the
            # main id only when no Smart-Meter accessory is known (then the
            # endpoint returns empty either way).
            ct_stat_device_id = ct_dev_id or dev_id
            per_dev_key = f"dev:{dev_id}"
            per_dev = self._slow_cache.setdefault(per_dev_key, {})
            backoff_pv_key = f"{per_dev_key}:pv_stat"
            backoff_battery_key = f"{per_dev_key}:battery_stat"
            backoff_home_key = f"{per_dev_key}:home_stat"
            backoff_ct_key = f"{per_dev_key}:ct_stat"
            backoff_eps_key = f"{per_dev_key}:eps_stat"
            backoff_today_key = f"{per_dev_key}:today_energy"
            backoff_symmetry_key = f"{per_dev_key}:symmetry_stat"

            def _period_backoff_key(base_key: str, date_type: str) -> str:
                return f"{base_key}:{date_type}"

            def _month_backoff_key(base_key: str, month: int) -> str:
                return f"{base_key}:{DATE_TYPE_MONTH}:{today.year}-{month:02d}"

            task_names: list[str] = [PAYLOAD_DEVICE_STATISTIC, PAYLOAD_LOCATION]
            tasks = [
                _get_with_ttl_for(
                    per_dev,
                    PAYLOAD_DEVICE_STATISTIC,
                    self._slow_metrics_interval_sec,
                    lambda: self.api.async_get_device_statistic(dev_id),
                    {},
                    stale_ok=stale_ok,
                ),
                _get_with_ttl_for(
                    per_dev,
                    PAYLOAD_LOCATION,
                    self._price_config_interval_sec,
                    lambda: self.api.async_get_location(dev_id),
                    {},
                    stale_ok=stale_ok,
                ),
            ]

            for date_type in APP_PERIOD_DATE_TYPES:
                kwargs = self._trend_query_kwargs(date_type)
                pv_key = self._app_period_section(APP_SECTION_PV_STAT, date_type)
                battery_key = self._app_period_section(
                    APP_SECTION_BATTERY_STAT,
                    date_type,
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
                            cast(
                                "Callable[[], Awaitable[dict[str, Any]]]",
                                lambda q=kwargs, s=sys_id: (
                                    self.api.async_get_device_pv_stat(
                                        dev_id,
                                        s,
                                        **q,
                                    )
                                ),
                            ),
                            {},
                            backoff_key=_period_backoff_key(
                                backoff_pv_key,
                                date_type,
                            ),
                            stale_ok=stale_ok,
                        ),
                    )
                task_names.append(battery_key)  # ruff:ignore[repeated-append]
                tasks.append(  # ruff:ignore[repeated-append]
                    _get_with_ttl_for(
                        per_dev,
                        battery_key,
                        self._slow_metrics_interval_sec,
                        cast(
                            "Callable[[], Awaitable[dict[str, Any]]]",
                            lambda q=kwargs: self.api.async_get_device_battery_stat(
                                dev_id,
                                **q,
                            ),
                        ),
                        {},
                        backoff_key=_period_backoff_key(
                            backoff_battery_key,
                            date_type,
                        ),
                        stale_ok=stale_ok,
                    ),
                )
                task_names.append(home_key)
                tasks.append(
                    _get_with_ttl_for(
                        per_dev,
                        home_key,
                        self._slow_metrics_interval_sec,
                        cast(
                            "Callable[[], Awaitable[dict[str, Any]]]",
                            lambda q=kwargs: self.api.async_get_device_home_stat(
                                dev_id,
                                **q,
                            ),
                        ),
                        {},
                        backoff_key=_period_backoff_key(
                            backoff_home_key,
                            date_type,
                        ),
                        stale_ok=stale_ok,
                    ),
                )
                # /v1/device/stat/ct — CT/smart-meter period statistics
                # (CtStatApi). Device-scoped, per dateType. Cached on the
                # slow-metrics TTL so per-cycle fast refreshes are free.
                task_names.append(ct_key)
                tasks.append(
                    _get_with_ttl_for(
                        per_dev,
                        ct_key,
                        self._slow_metrics_interval_sec,
                        cast(
                            "Callable[[], Awaitable[dict[str, Any]]]",
                            lambda q=kwargs: self.api.async_get_device_ct_stat(
                                ct_stat_device_id,
                                **q,
                            ),
                        ),
                        {},
                        backoff_key=_period_backoff_key(
                            backoff_ct_key,
                            date_type,
                        ),
                        stale_ok=stale_ok,
                    ),
                )
                # /v1/device/stat/eps — EPS / off-grid in/out period
                # statistics (EpsStatApi). Same shape as ct_stat: device
                # id + dateType, slow-metrics TTL.
                task_names.append(eps_key)
                tasks.append(
                    _get_with_ttl_for(
                        per_dev,
                        eps_key,
                        self._slow_metrics_interval_sec,
                        cast(
                            "Callable[[], Awaitable[dict[str, Any]]]",
                            lambda q=kwargs: self.api.async_get_device_eps_stat(
                                dev_id,
                                **q,
                            ),
                        ),
                        {},
                        backoff_key=_period_backoff_key(
                            backoff_eps_key,
                            date_type,
                        ),
                        stale_ok=stale_ok,
                    ),
                )
                # /v1/device/stat/symmetry — ATS / symmetry statistics
                # (AtsEleStatApi). Device-scoped, per dateType.
                symmetry_key = self._app_period_section(
                    APP_SECTION_SYMMETRY_STAT,
                    date_type,
                )
                task_names.append(symmetry_key)
                tasks.append(
                    _get_with_ttl_for(
                        per_dev,
                        symmetry_key,
                        self._slow_metrics_interval_sec,
                        cast(
                            "Callable[[], Awaitable[dict[str, Any]]]",
                            lambda q=kwargs: self.api.async_get_symmetry_stat(
                                device_sn=self.data[dev_id].get(FIELD_DEVICE_SN)
                                or dev_id,
                                **q,
                            ),
                        ),
                        {},
                        backoff_key=_period_backoff_key(
                            backoff_symmetry_key,
                            date_type,
                        ),
                        stale_ok=stale_ok,
                    ),
                )
            if dev_sn:
                # REST pack/list is slow and often returns null for SolarVault.
                # Live pack values are refreshed via MQTT subdevice queries.
                pack_interval_sec = self._slow_metrics_interval_sec
                task_names.append(PAYLOAD_OTA)  # ruff:ignore[repeated-append]
                tasks.append(  # ruff:ignore[repeated-append]
                    _get_with_ttl_for(
                        per_dev,
                        PAYLOAD_OTA,
                        self._price_config_interval_sec,
                        lambda: self.api.async_get_ota_info(dev_sn),
                        {},
                        stale_ok=stale_ok,
                    ),
                )
                task_names.append(PAYLOAD_BATTERY_PACKS)
                tasks.append(
                    _get_with_ttl_for(
                        per_dev,
                        PAYLOAD_BATTERY_PACKS,
                        pack_interval_sec,
                        lambda: self.api.async_get_battery_pack_list(dev_sn),
                        [],
                        stale_ok=stale_ok,
                    ),
                )
                # /v1/device/stat/today — compact today KPIs
                # (TodayEnergyApi: de/dg/dh/ds). Keyed by deviceSn, no
                # period parameters. Slow-metrics TTL so the fast 30 s
                # refresh does not hammer the cloud.
                task_names.append(APP_SECTION_TODAY_ENERGY)
                tasks.append(
                    _get_with_ttl_for(
                        per_dev,
                        APP_SECTION_TODAY_ENERGY,
                        self._slow_metrics_interval_sec,
                        lambda: self.api.async_get_today_energy(dev_sn),
                        {},
                        backoff_key=backoff_today_key,
                        stale_ok=stale_ok,
                    ),
                )
            # Keep device-metric failures local. One HTTP 5xx, timeout, or
            # payload-parse error must not abort the whole zip and blank every
            # per-device entity; map exceptions back to the structural default
            # expected by downstream consumers.
            raw_values = await asyncio.gather(*tasks, return_exceptions=True)
            device_extras_defaults: dict[str, Any] = {
                PAYLOAD_DEVICE_STATISTIC: {},
                PAYLOAD_LOCATION: {},
                PAYLOAD_OTA: {},
                PAYLOAD_BATTERY_PACKS: [],
            }
            values = [
                v
                if not isinstance(v, BaseException)
                else device_extras_defaults.get(name, {})
                for name, v in zip(task_names, raw_values, strict=False)
            ]
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

            async def _fetch_device_month(  # ruff:ignore[too-many-return-statements]
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
                            cast(
                                "Callable[[], Awaitable[dict[str, Any]]]",
                                lambda q=kwargs, s=sys_id: (
                                    self.api.async_get_device_pv_stat(
                                        dev_id,
                                        s,
                                        **q,
                                    )
                                ),
                            ),
                            {},
                            backoff_key=_month_backoff_key(backoff_pv_key, month),
                        ),
                    )
                if prefix == APP_SECTION_BATTERY_STAT:
                    return cast(
                        "dict[str, Any]",
                        await _get_with_ttl_for(
                            per_dev,
                            cache_key,
                            self._price_config_interval_sec,
                            cast(
                                "Callable[[], Awaitable[dict[str, Any]]]",
                                lambda q=kwargs: self.api.async_get_device_battery_stat(
                                    dev_id,
                                    **q,
                                ),
                            ),
                            {},
                            backoff_key=_month_backoff_key(
                                backoff_battery_key,
                                month,
                            ),
                        ),
                    )
                if prefix == APP_SECTION_HOME_STAT:
                    return cast(
                        "dict[str, Any]",
                        await _get_with_ttl_for(
                            per_dev,
                            cache_key,
                            self._price_config_interval_sec,
                            cast(
                                "Callable[[], Awaitable[dict[str, Any]]]",
                                lambda q=kwargs: self.api.async_get_device_home_stat(
                                    dev_id,
                                    **q,
                                ),
                            ),
                            {},
                            backoff_key=_month_backoff_key(backoff_home_key, month),
                        ),
                    )
                if prefix == APP_SECTION_CT_STAT:
                    return cast(
                        "dict[str, Any]",
                        await _get_with_ttl_for(
                            per_dev,
                            cache_key,
                            self._price_config_interval_sec,
                            cast(
                                "Callable[[], Awaitable[dict[str, Any]]]",
                                lambda q=kwargs: self.api.async_get_device_ct_stat(
                                    ct_stat_device_id,
                                    **q,
                                ),
                            ),
                            {},
                            backoff_key=_month_backoff_key(backoff_ct_key, month),
                        ),
                    )
                if prefix == APP_SECTION_EPS_STAT:
                    return cast(
                        "dict[str, Any]",
                        await _get_with_ttl_for(
                            per_dev,
                            cache_key,
                            self._price_config_interval_sec,
                            cast(
                                "Callable[[], Awaitable[dict[str, Any]]]",
                                lambda q=kwargs: self.api.async_get_device_eps_stat(
                                    dev_id,
                                    **q,
                                ),
                            ),
                            {},
                            backoff_key=_month_backoff_key(backoff_eps_key, month),
                        ),
                    )
                return {}

            # The previous-month backfill issues up to ``today.month - 1``
            # serial cloud round-trips per prefix (x5 prefixes). On the fast
            # critical update path (``stale_ok``) that blocking work is exactly
            # the L5-independent overrun source Q4 targets, so it is deferred to
            # the non-blocking background slow-refresh pass (which runs with
            # ``stale_ok=False``). Same-cycle derived consistency is unaffected:
            # the year value keeps its already-cached backfilled total until the
            # background pass refreshes it.
            pending_month_history: dict[str, dict[int, dict[str, Any]]] = {}
            month_requests: list[tuple[str, int]] = []
            for prefix, stat_keys in self._DEVICE_YEAR_BACKFILL_STAT_KEYS.items():
                if stale_ok:
                    break
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
                pending_month_history[prefix] = months
                month_requests.extend(
                    (prefix, month) for month in range(1, today.month)
                )

            # Flatten all prefixes into one bounded queue. This avoids five
            # serial timeout waves without allowing an unbounded request burst.
            month_request_semaphore = asyncio.Semaphore(8)

            async def _fetch_bounded_device_month(
                prefix: str,
                month: int,
            ) -> dict[str, Any]:
                async with month_request_semaphore:
                    return await _fetch_device_month(prefix, month)

            # Same year-backfill robustness as the home-trends path: a single
            # 404/timeout for one early month must not abort the entire year.
            sources = await asyncio.gather(
                *starmap(_fetch_bounded_device_month, month_requests),
                return_exceptions=True,
            )
            for (prefix, month), source in zip(
                month_requests,
                sources,
                strict=False,
            ):
                if isinstance(source, dict):
                    pending_month_history[prefix][month] = source
            month_history = {
                prefix: months
                for prefix, months in pending_month_history.items()
                if months
            }
            apply_year_month_backfill(out, month_history)

            return out

        async def _enrich_smart_plug_statistics(
            dev_id: str,
            entry: dict[str, Any],
            *,
            stale_ok: bool = False,
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
                cache_key = f"smart_socket_statistic:{stat_id}"
                if stale_ok and cache_key not in per_dev:
                    per_dev[cache_key] = (0.0, {})
                    updated_plugs.append(updated_plug)
                    continue
                panel = await _get_with_ttl_for(
                    per_dev,
                    cache_key,
                    self._slow_metrics_interval_sec,
                    cast(
                        "Callable[[], Awaitable[dict[str, Any]]]",
                        lambda sid=stat_id: self.api.async_get_device_socket_statistic(
                            sid,
                        ),
                    ),
                    {},
                    stale_ok=stale_ok,
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
            *,
            stale_ok: bool = False,
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
                dev_type = safe_int(updated_meter_head.get(FIELD_DEV_TYPE))
                stat_id = self._subdevice_stat_id(
                    entry,
                    updated_meter_head,
                    dev_type=dev_type or SUBDEVICE_DEV_TYPE_METER_HEAD,
                )
                if stat_id is None:
                    updated_meter_heads.append(updated_meter_head)
                    continue
                cache_key = f"meter_head_stat:{stat_id}"
                if stale_ok and cache_key not in per_dev:
                    per_dev[cache_key] = (0.0, {})
                    updated_meter_heads.append(updated_meter_head)
                    continue
                panel = await _get_with_ttl_for(
                    per_dev,
                    cache_key,
                    self._slow_metrics_interval_sec,
                    cast(
                        "Callable[[], Awaitable[dict[str, Any]]]",
                        lambda sid=stat_id: self.api.async_get_device_meter_stat(sid),
                    ),
                    {},
                    stale_ok=stale_ok,
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

        async def _enrich_shelly_cloud_realtime(
            dev_id: str,
            entry: dict[str, Any],
            *,
            stale_ok: bool = False,
        ) -> None:
            """Merge Shelly Cloud realtime-power into existing accessory buckets.

            Shelly Cloud is a third-party (L5-class) endpoint whose realtime
            round-trip can be slow or time out. Its TTL equals the fast poll
            interval, so on the L3
            critical path it must be served ``stale_ok=True``: a cold or
            expired TTL returns the cached/default value immediately instead of
            blocking the whole property cycle on the slow Shelly fetch (the
            documented overrun: e.g. 52s > 15s). The background slow-refresh
            pass re-runs this enricher ``stale_ok=False`` to warm the cache
            off-path, then requests a coordinator refresh so the fresh value is
            merged. Same rule as the Shelly device-list fetch above and AGENTS
            3.3 (local / third-party failures must not block Cloud data).
            """
            shelly_ids = self._shelly_cloud_device_ids(entry)
            if not shelly_ids:
                return
            per_dev = self._slow_cache.setdefault(f"dev:{dev_id}:shelly_cloud", {})
            ttl_sec = max(1, int(self._configured_update_interval.total_seconds()))

            def _make_shelly_realtime_fetcher(
                shelly_id: str,
            ) -> Callable[[], Awaitable[dict[str, Any]]]:
                async def _fetch() -> dict[str, Any]:
                    try:
                        async with asyncio.timeout(SHELLY_REALTIME_FETCH_TIMEOUT_SEC):
                            return await self.api.async_get_shelly_realtime_power(
                                shelly_id,
                            )
                    except TimeoutError as err:
                        msg = (
                            "Shelly realtime-power fetch timed out after "
                            f"{SHELLY_REALTIME_FETCH_TIMEOUT_SEC}s for {shelly_id}"
                        )
                        raise JackeryApiError(msg) from err

                return _fetch

            for shelly_id in shelly_ids:
                shelly_id_str = str(shelly_id)
                cache_key = f"realtime:{shelly_id_str}"
                if stale_ok and cache_key not in per_dev:
                    per_dev[cache_key] = (0.0, {})
                    continue
                realtime = await _get_with_ttl_for(
                    per_dev,
                    cache_key,
                    ttl_sec,
                    _make_shelly_realtime_fetcher(shelly_id_str),
                    {},
                    backoff_key=f"{_SHELLY_REALTIME_BACKOFF_PREFIX}{shelly_id_str}",
                    stale_ok=stale_ok,
                )
                if isinstance(realtime, dict):
                    cached_realtime = per_dev.get(cache_key)
                    realtime_generation = (
                        cached_realtime[0] if cached_realtime is not None else 0.0
                    )
                    seen_key = (dev_id, shelly_id_str)
                    realtime_is_new = (
                        cached_realtime is not None
                        and realtime_generation > 0
                        and realtime_generation
                        > self._shelly_realtime_cache_seen.get(
                            seen_key,
                            float("-inf"),
                        )
                    )
                    realtime_source = (
                        {
                            **realtime,
                            SUBDEVICE_FIELD_LAST_SEEN_AT: datetime.now(UTC).isoformat(),
                        }
                        if realtime_is_new
                        else realtime
                    )
                    self._merge_shelly_cloud_item(
                        entry,
                        realtime_source,
                        fill_only=(
                            not realtime_is_new or self._live_ct_is_fresh(dev_id)
                        ),
                    )
                    if realtime_is_new:
                        self._shelly_realtime_cache_seen[seen_key] = realtime_generation

        result: dict[str, dict[str, Any]] = {}
        invalid_device_ids: list[str] = []
        property_fetch_completed = False
        # Shelly Cloud is a third-party (L5-class) enrichment. Fetch the
        # app-linked device list once per cycle with ``stale_ok=True`` so a
        # cold or expired TTL never blocks the L3 property loop on a fresh
        # Shelly round-trip. If the cached list went stale this cycle, flag it
        # so the background slow-refresh pass re-fetches it non-stale off the
        # critical path (same pattern as the system/device extras above).
        # Same-cycle consistency is preserved: every device reuses this one
        # cached list.
        shelly_cloud_devices = await _fetch_shelly_cloud_devices(stale_ok=True)
        shelly_cache = self._slow_cache.get("shelly_cloud", {})
        shelly_entry = shelly_cache.get("devices")
        shelly_cache_stale = shelly_entry is not None and (
            time.monotonic() - shelly_entry[0] >= self._price_config_interval_sec
        )
        shelly_cache_generation = shelly_entry[0] if shelly_entry is not None else 0.0
        shelly_cache_is_new = (
            shelly_entry is not None
            and shelly_cache_generation > 0
            and shelly_cache_generation > self._shelly_device_cache_seen
        )
        self._polling_diagnostics["last_schedule_decision"] = "started"
        self._polling_diagnostics["property_fetch_completed"] = False
        # Fetch every device's authoritative /device/property concurrently.
        # These fetches are mutually independent and the device returns its
        # whole payload at once, so awaiting them one-by-one in the loop was the
        # dominant cost of the poll cycle (the per-device extras and system
        # metrics inside the loop are already served ``stale_ok=True`` from
        # cache on the critical path, i.e. they do not issue HTTP here). Each
        # result — value or exception — is replayed into the unchanged
        # per-device error handling below via ``return_exceptions=True``.
        device_items = list(self._device_index.items())
        # Every not-activated repair issue that a *currently known* device
        # could own this cycle. Used below to scope the stale-issue cleanup
        # to devices that dropped out of ``device_items`` entirely, instead
        # of deleting any issue that merely isn't the device the per-device
        # loop happens to be on right now (F-SW2-7: with two unactivated
        # devices in the same cycle, that used to tear down the sibling's
        # still-valid issue every iteration).
        current_act_issue_ids = {
            f"{self.entry.entry_id}_{d}_{REPAIR_ISSUE_DEVICE_NOT_ACTIVATED}"
            for d, _ in device_items
        }
        property_results = await asyncio.gather(
            *(self.api.async_get_device_property(dev_id) for dev_id, _ in device_items),
            return_exceptions=True,
        )
        first_property_failure: tuple[str, JackeryError] | None = None
        for (dev_id, idx), property_result in zip(
            device_items, property_results, strict=True
        ):
            old_entry: dict[str, Any] = {}
            if self.data:
                old_entry = self.data.get(dev_id) or {}
            property_error: BaseException | None = None
            payload: dict[str, Any] = {}
            if isinstance(property_result, BaseException):
                property_error = property_result
            else:
                payload = property_result
            try:
                if property_error is not None:
                    raise property_error
                property_fetch_completed = True
                if payload:
                    self._last_http_device_refresh_monotonic[dev_id] = time.monotonic()
            except JackeryAuthError as err:
                _raise_config_entry_auth_failed(
                    "Jackery credentials were rejected during property refresh",
                    err,
                )
            except JackeryError as err:
                self._bump_polling_diag("failures")
                if "code=20000" in str(err):
                    # code=20000 means the cloud rejected this device id as
                    # invalid. Track it for the discovery drop/retry below and
                    # keep the original skip semantics unchanged.
                    invalid_device_ids.append(dev_id)
                    _LOGGER.warning("property fetch failed for %s: %s", dev_id, err)
                    if self.data and dev_id in self.data:
                        result[dev_id] = self.data[dev_id]
                    continue
                _LOGGER.warning("property fetch failed for %s: %s", dev_id, err)
                if self.data and dev_id in self.data:
                    # Preserve a known device byte-for-byte. A newly discovered
                    # device retries on the next HTTP cycle without invalidating
                    # already available devices from this entry.
                    result[dev_id] = self.data[dev_id]
                elif first_property_failure is None:
                    first_property_failure = (dev_id, err)
                continue
            else:
                self._bump_polling_diag("fetches")
                if not payload:
                    self._bump_polling_diag("empty_fetches")

            # Pull SN from either the fresh property payload or the discovery
            # metadata — needed for the OTA endpoint (which keys on SN).
            dev_sn = (payload.get(PAYLOAD_DEVICE) or {}).get(FIELD_DEVICE_SN) or (
                idx.get(PAYLOAD_DEVICE_META) or {}
            ).get(FIELD_DEVICE_SN)
            sys_id = str(idx.get(FIELD_SYSTEM_ID)) if idx.get(FIELD_SYSTEM_ID) else None
            # Resolve the CT/Smart-Meter accessory's own deviceId from the
            # discovery index so the /v1/device/stat/ct endpoint is queried
            # with the accessory id it expects (not the main device id).
            ct_dev_id = self._smart_meter_accessory_device_id(idx)
            try:
                # Fast critical path: never block the L3 property cycle on the
                # slow per-device stat/OTA/pack endpoints. Serve the cached
                # (possibly stale) extras and let the background slow-refresh
                # pass fetch fresh data, exactly like the system-level metrics
                # below. HTTP L3 stays fast and is never gated by L5.
                extras = await _fetch_device_extras(
                    dev_id,
                    dev_sn,
                    sys_id,
                    ct_dev_id,
                    stale_ok=True,
                )
            except JackeryAuthError as err:
                _LOGGER.debug(
                    "Jackery slow device extras were auth-rejected for %s; "
                    "using empty extras while the primary property fetch remains "
                    "authoritative: %s",
                    dev_id,
                    exception_debug_message(err),
                )
                extras = {
                    PAYLOAD_DEVICE_STATISTIC: {},
                    PAYLOAD_LOCATION: {},
                    PAYLOAD_OTA: {},
                    PAYLOAD_BATTERY_PACKS: [],
                }
            # Track devices whose per-device slow-metric cache went stale this
            # cycle so the background refresh re-fetches them off the critical
            # path (mirrors the system-level ``systems_needing_refresh`` logic).
            per_dev_cache = self._slow_cache.get(f"dev:{dev_id}", {})
            pack_cache_entry = per_dev_cache.get(PAYLOAD_BATTERY_PACKS)
            pack_cache_timestamp = (
                pack_cache_entry[0] if pack_cache_entry is not None else 0.0
            )
            pack_response_is_new = (
                pack_cache_timestamp > 0
                and pack_cache_timestamp
                > self._battery_pack_http_cache_seen.get(dev_id, float("-inf"))
            )
            if per_dev_cache:
                now_mono = time.monotonic()
                dev_cache_fresh = all(
                    ts > 0.0 and now_mono - ts < self._slow_metrics_interval_sec
                    for ts, _ in per_dev_cache.values()
                )
                if not dev_cache_fresh:
                    if sys_id:
                        systems_needing_refresh.add(sys_id)
                    devices_needing_refresh[dev_id] = (
                        dev_id,
                        dev_sn,
                        sys_id,
                        ct_dev_id,
                    )

            # Keep the pristine sanitized HTTP body separate from the
            # override result: PAYLOAD_HTTP_PROPERTIES is the diagnostic
            # "what did HTTP really say" surface and must not be
            # contaminated by live-override shadowing (A2, 2026-07-03).
            raw_http_props = payload.get(PAYLOAD_PROPERTIES) or {}
            if not isinstance(raw_http_props, dict):
                raw_http_props = {}
            http_props = self._sanitize_main_properties(raw_http_props)
            live_guarded_props = self._http_properties_with_live_overrides(
                old_entry,
                http_props,
                device_id=dev_id,
            )
            merged_props = self._merge_main_properties_for_device(
                dev_id,
                old_entry.get(PAYLOAD_PROPERTIES) or {},
                live_guarded_props,
                source=TransportSource.HTTP,
            )
            local_daily_properties = self._local_daily_counter_properties(
                merged_props,
                old_entry,
            )
            local_daily_energy = self._refresh_local_daily_for_device(
                dev_id,
                local_daily_properties,
                today=today,
                allow_new_anchor_delta=local_daily_allow_new_anchor,
            )

            extra_packs = extras.get(PAYLOAD_BATTERY_PACKS) or []
            old_packs = old_entry.get(PAYLOAD_BATTERY_PACKS) or []
            if extra_packs:
                battery_packs = self._merge_battery_pack_lists(
                    old_packs,
                    extra_packs,
                    refresh_last_seen=pack_response_is_new,
                )
            elif isinstance(old_packs, list):
                battery_packs = old_packs
            else:
                battery_packs = []
            if pack_response_is_new:
                self._battery_pack_http_cache_seen[dev_id] = pack_cache_timestamp
            if battery_packs:
                await self._async_enrich_battery_pack_ota(
                    dev_id,
                    battery_packs,
                    dev_sn,
                    fetch_missing=False,
                )
                self._schedule_battery_pack_ota_enrichment(dev_id)

            period_payloads = {
                # Live entity state shows the cloud's answer as-is, including
                # genuine zeros. Live values NEVER pass through ingest (owner
                # directive 2026-07-25): the gate — zero-drop, negative-series
                # scrub, hierarchy withholding — is exclusively a Recorder/
                # statistics-import safeguard applied on that path.
                section: dict(extras.get(section) or {})
                for prefix in (
                    APP_SECTION_PV_STAT,
                    APP_SECTION_BATTERY_STAT,
                    APP_SECTION_HOME_STAT,
                    APP_SECTION_CT_STAT,
                    APP_SECTION_EPS_STAT,
                    APP_SECTION_SYMMETRY_STAT,
                )
                for date_type in APP_PERIOD_DATE_TYPES
                for section in (self._app_period_section(prefix, date_type),)
            }
            entry: dict[str, Any] = {
                PAYLOAD_DEVICE: payload.get(PAYLOAD_DEVICE) or {},
                PAYLOAD_PROPERTIES: merged_props,
                PAYLOAD_HTTP_PROPERTIES: http_props,
                PAYLOAD_SYSTEM: idx.get(PAYLOAD_SYSTEM_META) or {},
                PAYLOAD_DISCOVERY: idx.get(PAYLOAD_DEVICE_META) or {},
                PAYLOAD_DEVICE_STATISTIC: gate_payload_section(
                    TransportSource.HTTP,
                    PAYLOAD_DEVICE_STATISTIC,
                    extras.get(PAYLOAD_DEVICE_STATISTIC) or {},
                    # systemStatistic today counters from the same cycle
                    # cross-confirm real day-zeros (AGENTS.md §2.2 rule 7).
                    confirmation_source=extras.get(PAYLOAD_STATISTIC) or {},
                ),
                **period_payloads,
                # ``device/stat/today`` must be reconciled against dated day
                # stats before zero-only values are withheld; otherwise the
                # generic ingest gate can erase the cloud body before
                # ``_reconcile_today_energy`` has a chance to backfill it.
                APP_SECTION_TODAY_ENERGY: dict(
                    extras.get(APP_SECTION_TODAY_ENERGY) or {}
                ),
                PAYLOAD_OTA: extras.get(PAYLOAD_OTA) or {},
                PAYLOAD_LOCATION: extras.get(PAYLOAD_LOCATION) or {},
                PAYLOAD_BATTERY_PACKS: battery_packs,
            }
            if local_daily_energy:
                entry[PAYLOAD_LOCAL_DAILY_ENERGY] = local_daily_energy
            for cached_key in PRESERVED_FAST_PAYLOAD_KEYS:
                if cached_key in old_entry:
                    entry[cached_key] = old_entry[cached_key]
            # Overlay cached MQTT CombineData system-info fields back onto
            # PAYLOAD_PROPERTIES.  The HTTP property endpoint (HomeBody)
            # never returns these keys (SystemBody only), so without this
            # step the sensors would flip to Unknown whenever MQTT is
            # temporarily disconnected.
            entry[PAYLOAD_PROPERTIES] = self._overlay_cached_system_info(
                dev_id,
                entry.get(PAYLOAD_PROPERTIES) or {},
            )
            self._reconcile_today_energy(entry)
            for accessory in self._entry_subdevice_candidates(entry):
                self._merge_shelly_cloud_item(entry, accessory)
            for shelly_device in shelly_cloud_devices:
                if self._shelly_cloud_device_matches_entry(entry, shelly_device):
                    shelly_source = (
                        {
                            **shelly_device,
                            SUBDEVICE_FIELD_LAST_SEEN_AT: datetime.now(UTC).isoformat(),
                        }
                        if shelly_cache_is_new
                        else shelly_device
                    )
                    self._merge_shelly_cloud_item(
                        entry,
                        shelly_source,
                        fill_only=(
                            not shelly_cache_is_new or self._live_ct_is_fresh(dev_id)
                        ),
                    )
            if sys_id:
                try:
                    sys_data = await _fetch_system(sys_id, stale_ok=True)
                except JackeryAuthError as err:
                    _LOGGER.debug(
                        "Jackery slow system data was auth-rejected for %s; "
                        "using empty system extras while the primary property "
                        "fetch remains authoritative: %s",
                        sys_id,
                        exception_debug_message(err),
                    )
                    sys_data = {}
                # When stale_ok was used, the slow-metric cache may have
                # returned data older than the TTL.  Track these systems
                # so we can refresh them in a non-blocking background task.
                per_sys_cache = self._slow_cache.get(sys_id, {})
                now_mono = time.monotonic()
                cache_is_fresh = all(
                    ts > 0.0 and now_mono - ts < self._slow_metrics_interval_sec
                    for ts, _ in per_sys_cache.values()
                )
                if not cache_is_fresh and sys_id not in systems_needing_refresh:
                    systems_needing_refresh.add(sys_id)
                # A cold/never-fetched slow-metrics TTL cache seeds alarm as
                # ``None`` (see ``_get_with_ttl_for``'s stale_ok cold-cache
                # branch), which is indistinguishable here from "HTTP hasn't
                # spoken yet". Without this guard that ``None`` unconditionally
                # overwrites an ``entry[PAYLOAD_ALARM]`` already restored from
                # an MQTT push by the preserve loop above (F-SW2-3). A genuine
                # HTTP alarm result (including an authoritative "no active
                # alarms" empty list) is never ``None`` and still applies.
                if sys_data.get(PAYLOAD_ALARM) is None and PAYLOAD_ALARM in entry:
                    sys_data = {
                        key: value
                        for key, value in sys_data.items()
                        if key != PAYLOAD_ALARM
                    }
                live_system_fields = {
                    key: (entry.get(PAYLOAD_SYSTEM) or {}).get(key)
                    for key in (FIELD_TIMEZONE, FIELD_GRID_STANDARD)
                    if (entry.get(PAYLOAD_SYSTEM) or {}).get(key) is not None
                }
                entry.update(sys_data)
                if live_system_fields:
                    system = dict(entry.get(PAYLOAD_SYSTEM) or {})
                    system.update(live_system_fields)
                    entry[PAYLOAD_SYSTEM] = system
            override = self._price_overrides.get(dev_id)
            if override:
                override_ts, price_updates = override
                if time.monotonic() - override_ts < self._PRICE_OVERRIDE_TTL_SEC:
                    entry[PAYLOAD_PRICE] = merge_dict_values(
                        entry.get(PAYLOAD_PRICE) or {},
                        price_updates,
                    )
                else:
                    self._price_overrides.pop(dev_id, None)
            # The device-list wire payload omits `currency`; it is carried only
            # in the price section. Mirror it onto the device meta so the field
            # is a processed value rather than a null in diagnostics/entities.
            device_meta = entry.get(PAYLOAD_DEVICE)
            price_section = entry.get(PAYLOAD_PRICE)
            if (
                isinstance(device_meta, dict)
                and isinstance(price_section, dict)
                and device_meta.get(FIELD_CURRENCY) is None
            ):
                currency = (
                    price_section.get(FIELD_SINGLE_CURRENCY)
                    or price_section.get(FIELD_CURRENCY)
                    or price_section.get(FIELD_SINGLE_CURRENCY_CODE)
                    or price_section.get(FIELD_CURRENCY_CODE)
                )
                if currency is not None:
                    entry[PAYLOAD_DEVICE] = {
                        **device_meta,
                        FIELD_CURRENCY: currency,
                    }
            # Supplementary third-party cloud enrichments (Shelly Cloud, smart
            # plugs, meter heads). Their tokens rotate independently of the
            # SolarVault session; a JackeryAuthError here (e.g. Shelly "Token
            # expires", code=10402) must NOT propagate out of the update — doing
            # so flips last_update_success to False, which makes EVERY entity
            # (all buttons included) report unavailable and HA then refuses
            # button.press. The primary property/system/discovery fetches above
            # remain the sole auth authority that triggers reauth.
            #
            # These are L5-class endpoints (Shelly realtime in particular is
            # slow and, on timeout, fails the single request with no retry — a
            # received-but-rejected response triggers at most one rate-limited
            # re-login retry, never a fixed 3x loop). On the L3 critical path
            # they MUST be served ``stale_ok=True`` so a slow third-party fetch
            # never gates the fast HTTP poll (the documented overrun was driven
            # by the Shelly realtime endpoint: ~52s > 15s every cycle). When any
            # enrichment cache for this device is stale, flag it for the
            # non-blocking background refresh below, which re-runs these
            # enrichers ``stale_ok=False`` off the critical path.
            for enrich in (
                _enrich_shelly_cloud_realtime,
                _enrich_smart_plug_statistics,
                _enrich_meter_head_statistics,
            ):
                try:
                    await enrich(dev_id, entry, stale_ok=True)
                except JackeryAuthError as err:
                    _LOGGER.debug(
                        "Supplementary cloud enrichment %s was auth-rejected for "
                        "%s (token may be rotating); skipping — primary fetch "
                        "stays the auth authority: %s",
                        enrich.__name__,
                        dev_id,
                        exception_debug_message(err),
                    )
                except (TimeoutError, JackeryError) as err:
                    _LOGGER.debug(
                        "Supplementary cloud enrichment %s failed for %s: %s",
                        enrich.__name__,
                        dev_id,
                        exception_debug_message(err),
                    )
            if self._device_enrichment_cache_stale(dev_id):
                devices_needing_enrichment_refresh.add(dev_id)
            # Single-pack systems emit no per-pack live telemetry frame; the
            # pack's SOC / charge / discharge / cell temperature equal the main
            # battery's values, which are already present in PAYLOAD_PROPERTIES.
            # Backfill the lone pack from those so its live sensors populate
            # instead of showing "unknown" (the lifetime/SN/firmware fields keep
            # their own subdevice values).
            packs = entry.get(PAYLOAD_BATTERY_PACKS)
            main_props = entry.get(PAYLOAD_PROPERTIES)
            if (
                isinstance(packs, list)
                and len(packs) == 1
                and isinstance(packs[0], dict)
                and isinstance(main_props, dict)
            ):
                pack = packs[0]
                for pack_field, prop_field in (
                    (FIELD_BAT_SOC, FIELD_BAT_SOC),
                    (FIELD_CELL_TEMP, FIELD_CELL_TEMP),
                    (FIELD_IN_PW, FIELD_BAT_IN_PW),
                    (FIELD_OUT_PW, FIELD_BAT_OUT_PW),
                ):
                    if (
                        pack.get(pack_field) is None
                        and main_props.get(prop_field) is not None
                    ):
                        pack[pack_field] = main_props[prop_field]
            previous_statistic = old_entry.get(PAYLOAD_STATISTIC)
            guard_statistic_totals_from_year(
                entry,
                previous_statistic=previous_statistic
                if isinstance(previous_statistic, dict)
                else None,
            )
            quality_warnings = app_data_quality_warnings(entry, today=today)
            if quality_warnings:
                for warning in quality_warnings:
                    self.record_schema_rejection(warning.reason)
                entry[PAYLOAD_DATA_QUALITY] = [
                    warning.as_dict() for warning in quality_warnings
                ]
            # Create or dismiss a repair issue based on the cloud-reported
            # activation flag. Treat activated=0 as a cloud data-quality signal,
            # not proof of local pairing state.
            device_data = entry.get(PAYLOAD_DEVICE) or {}
            act_issue_id = (
                f"{self.entry.entry_id}_{dev_id}_{REPAIR_ISSUE_DEVICE_NOT_ACTIVATED}"
            )
            for domain, existing_issue_id in tuple(ir.async_get(self.hass).issues):
                if (
                    domain == DOMAIN
                    and existing_issue_id.endswith(
                        f"_{REPAIR_ISSUE_DEVICE_NOT_ACTIVATED}",
                    )
                    and existing_issue_id not in current_act_issue_ids
                ):
                    ir.async_delete_issue(self.hass, DOMAIN, existing_issue_id)
                    self._activation_issue_active.discard(existing_issue_id)

            activated_flag_is_unconfirmed = device_data.get(
                "activated"
            ) == 0 and self._has_activation_contradicting_payload(entry)
            if device_data.get("activated") == 0 and not activated_flag_is_unconfirmed:
                if act_issue_id in self._activation_issue_active:
                    result[dev_id] = entry
                    continue
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    act_issue_id,
                    is_fixable=True,
                    severity=ir.IssueSeverity.WARNING,
                    translation_key=REPAIR_TRANSLATION_DEVICE_NOT_ACTIVATED,
                    translation_placeholders={"device_id": dev_id},
                    data={
                        "entry_id": self.entry.entry_id,
                        "device_id": dev_id,
                    },
                )
                self._activation_issue_active.add(act_issue_id)
            else:
                ir.async_delete_issue(self.hass, DOMAIN, act_issue_id)
                self._activation_issue_active.discard(act_issue_id)
            result[dev_id] = entry

        if shelly_cache_is_new:
            self._shelly_device_cache_seen = shelly_cache_generation

        if invalid_device_ids and _retry_discovery_once:
            _LOGGER.info(
                "Jackery: dropping %d invalid device id(s) from discovery and retrying",
                len(invalid_device_ids),
            )
            for dev_id in invalid_device_ids:
                self._device_index.pop(dev_id, None)
            if not self._device_index:
                await self.async_discover()
            return await self._async_update_data_guarded(_retry_discovery_once=False)

        if not result and first_property_failure is not None:
            failed_device_id, property_error = first_property_failure
            msg = f"property fetch failed for {failed_device_id}: {property_error}"
            raise UpdateFailed(msg) from property_error

        # MQTT reconnection is non-blocking: fire-and-forget so the
        # coordinator result (HTTP data) is returned immediately.  The
        # previous ``await self._async_ensure_mqtt()`` blocked the
        # critical update path when the broker was unreachable, causing
        # pv_trends and other slow HTTP endpoints to time out.
        if self._mqtt is not None and (
            self.api.mqtt_fingerprint != self._mqtt_mgr.fingerprint
            or not self._mqtt.is_connected
        ):
            self._schedule_background_once(
                "mqtt_ensure",
                self._async_ensure_mqtt,
                name=f"{DOMAIN}_mqtt_ensure",
            )
        await self._async_update_data_quality_issue(result)
        # Recorder statistic imports only run at the slow-metric cadence
        # (server-side chart updates also operate at ~5 min granularity)
        # so the recorder is not woken up on every fast HTTP refresh.
        self._schedule_statistics_import(result)
        self._schedule_mqtt_poll_queries(result)
        # HTTP property-shadow fallback (HTTP-primary): fills subdevice live
        # buckets when MQTT is absent/stale. Background-only — never awaited in
        # this hot update path. Runs regardless of MQTT connection state.
        self._schedule_shadow_fallback(result)
        # Drain queued device-registry removals from the stale-pack
        # cleanup. Fire-and-forget on the same task so a registry
        # hiccup does not break the data refresh.
        self._prune_stale_battery_packs(result)
        self._prune_stale_subdevices(result)
        if self._pending_device_removals:
            try:
                await self.async_cleanup_pending_device_removals()
            except BACKGROUND_TASK_ERRORS as err:
                _LOGGER.debug("Jackery: device-registry cleanup deferred: %s", err)
        if self._battery_pack_topology_reload_required and not any(
            "_battery_pack_" in unique_id
            for _domain, unique_id in self._pending_device_removals
        ):
            self._schedule_topology_reload()
        # Launch a non-blocking background refresh for systems whose
        # slow-metric caches were stale this cycle.  This avoids blocking
        # the main coordinator update with 17+ parallel HTTP requests
        # that may each take up to 15 s (pv_trends, home_trends, etc.).
        # The stale Shelly Cloud device list (served stale_ok above) is
        # refreshed off-path here too, so the L3 cycle is never slowed by the
        # third-party Shelly round-trip.
        if (
            systems_needing_refresh
            or devices_needing_refresh
            or shelly_cache_stale
            or devices_needing_enrichment_refresh
            or historical_month_refreshers
        ):

            def _make_device_refresher(
                descriptor: tuple[str, str | None, str | None, str | None],
            ) -> Callable[[], Awaitable[Any]]:
                async def _refresh() -> Any:  # forwards device extras  # ruff:ignore[any-type]
                    return await _fetch_device_extras(
                        descriptor[0],
                        descriptor[1],
                        descriptor[2],
                        descriptor[3],
                        stale_ok=False,
                    )

                return _refresh

            def _make_enrichment_refresher(
                enrich_dev_id: str,
            ) -> Callable[[], Awaitable[Any]]:
                # Re-run the supplementary L5 enrichers ``stale_ok=False`` off
                # the critical path, reusing the same nested closures (no logic
                # duplication), then merge the freshly enriched accessory
                # buckets back into ``self.data`` and push a partial update.
                # Auth / transient failures stay isolated so they never flip
                # ``last_update_success``.
                async def _refresh() -> Any:  # ruff:ignore[any-type]
                    if not self.data or enrich_dev_id not in self.data:
                        return None
                    enrich_entry = dict(self.data[enrich_dev_id])
                    before = copy.deepcopy(enrich_entry)
                    for enrich in (
                        _enrich_shelly_cloud_realtime,
                        _enrich_smart_plug_statistics,
                        _enrich_meter_head_statistics,
                    ):
                        try:
                            await enrich(enrich_dev_id, enrich_entry, stale_ok=False)
                        except JackeryAuthError as err:
                            _LOGGER.debug(
                                "Background enrichment %s auth-rejected for %s: %s",
                                enrich.__name__,
                                enrich_dev_id,
                                exception_debug_message(err),
                            )
                        except (TimeoutError, JackeryError) as err:
                            _LOGGER.debug(
                                "Background enrichment %s failed for %s: %s",
                                enrich.__name__,
                                enrich_dev_id,
                                exception_debug_message(err),
                            )
                    if enrich_entry == before or not self.data:
                        return None
                    if enrich_dev_id not in self.data:
                        return None
                    new_data = dict(self.data)
                    new_data[enrich_dev_id] = {
                        **new_data[enrich_dev_id],
                        **enrich_entry,
                    }
                    self._push_partial_update(new_data)
                    return None

                return _refresh

            device_refreshers = [
                _make_device_refresher(descriptor)
                for descriptor in devices_needing_refresh.values()
            ]
            device_refreshers.extend(
                _make_enrichment_refresher(enrich_dev_id)
                for enrich_dev_id in devices_needing_enrichment_refresh
            )
            device_refreshers.extend(historical_month_refreshers)
            if shelly_cache_stale:

                async def _refresh_shelly() -> Any:  # ruff:ignore[any-type]
                    return await _fetch_shelly_cloud_devices(stale_ok=False)

                device_refreshers.append(_refresh_shelly)

            self._launch_background_slow_refresh(
                systems_needing_refresh,
                _get_with_ttl,
                device_refreshers=device_refreshers,
            )
        completed = time.monotonic()
        self._last_http_cycle_completed_monotonic = completed
        if property_fetch_completed:
            self._last_http_refresh_completed_monotonic = completed
        self._polling_diagnostics["property_fetch_completed"] = property_fetch_completed
        self._polling_diagnostics["last_status"] = "success" if result else "empty"
        elapsed = completed - started
        interval_sec = self._configured_update_interval.total_seconds()
        overrun_sec = max(0.0, elapsed - interval_sec)
        overrun_active = overrun_sec > 10.0  # ruff:ignore[magic-value-comparison]
        previous_overrun_active = bool(self._polling_diagnostics["overrun_active"])
        now_iso = dt_util.utcnow().isoformat()
        self._polling_diagnostics["last_cycle_elapsed_sec"] = round(elapsed, 3)
        self._polling_diagnostics["current_overrun_sec"] = round(overrun_sec, 3)
        if overrun_active:
            self._polling_diagnostics["last_overrun_sec"] = round(overrun_sec, 3)
            self._polling_diagnostics["last_overrun_at"] = now_iso
            self._polling_diagnostics["max_overrun_sec"] = round(
                max(
                    float(self._polling_diagnostics["max_overrun_sec"]),
                    overrun_sec,
                ),
                3,
            )
            if previous_overrun_active:
                self._polling_diagnostics["incident_max_overrun_sec"] = round(
                    max(
                        float(
                            self._polling_diagnostics.get(
                                "incident_max_overrun_sec",
                                0.0,
                            )
                        ),
                        overrun_sec,
                    ),
                    3,
                )
            else:
                self._polling_diagnostics["overrun_incident_count"] = (
                    safe_int(self._polling_diagnostics["overrun_incident_count"]) or 0
                ) + 1
                self._polling_diagnostics["overrun_started_at"] = now_iso
                self._polling_diagnostics["incident_max_overrun_sec"] = round(
                    overrun_sec,
                    3,
                )
                self._polling_overrun_started_monotonic = completed
                _LOGGER.warning(
                    "Jackery polling cycle overran interval: %.2fs > %.2fs "
                    "(over by %.2fs)",
                    elapsed,
                    interval_sec,
                    overrun_sec,
                )
        elif previous_overrun_active:
            started_monotonic = self._polling_overrun_started_monotonic
            recovery_duration = (
                max(0.0, completed - started_monotonic)
                if started_monotonic is not None
                else 0.0
            )
            self._polling_diagnostics["last_recovered_at"] = now_iso
            self._polling_diagnostics["last_recovery_duration_sec"] = round(
                recovery_duration,
                3,
            )
            _LOGGER.info(
                "Jackery polling recovered after %.2fs; maximum interval overrun "
                "during the incident was %.2fs",
                recovery_duration,
                float(
                    self._polling_diagnostics.get(
                        "incident_max_overrun_sec",
                        0.0,
                    )
                ),
            )
            self._polling_overrun_started_monotonic = None
        self._polling_diagnostics["overrun_active"] = overrun_active
        # Persist MQTT session + daily snapshots in the background so
        # disk I/O never blocks the coordinator result return.
        self._schedule_background_once(
            "mqtt_persist",
            self._async_persist_mqtt_session_if_changed,
            name=f"{DOMAIN}_mqtt_persist",
        )
        self._schedule_background_once(
            "daily_persist",
            self._async_persist_local_daily_snapshots_if_changed,
            name=f"{DOMAIN}_daily_persist",
        )
        return result

    # ------------------------------------------------------------------
    # Background slow-metric refresh
    # ------------------------------------------------------------------

    def _launch_background_slow_refresh(
        self,
        system_ids: set[str],
        get_with_ttl: Callable[..., Any],
        *,
        device_refreshers: list[Callable[[], Awaitable[Any]]] | None = None,
    ) -> None:
        """Fire-and-forget background refresh for stale slow-metric caches.

        The main coordinator update uses ``stale_ok=True`` to avoid blocking
        on 17+ parallel cloud HTTP requests (pv_trends, home_trends, etc.)
        that may each take up to 15 s.  When the TTL has expired, this
        method launches a non-blocking background task that fetches fresh
        data and triggers a coordinator re-update so entities reflect the
        latest values without delaying the fast property poll.

        ``device_refreshers`` carries one zero-arg coroutine factory per
        device whose per-device slow cache (deviceStatistic, period stats,
        OTA, packs, today energy, year backfill) went stale this cycle. They
        run non-stale here so the deferred device extras still refresh off the
        critical path.
        """
        if self._shutdown_started:
            return
        # Keep any in-flight background refresh running. Cancelling it on every
        # fast poll can keep slow Stats/Trends caches permanently stale when a
        # refresh takes longer than one coordinator interval.
        if (
            self._slow_metrics_bg_task is not None
            and not self._slow_metrics_bg_task.done()
        ):
            return

        sys_ids = set(system_ids)
        dev_refreshers = list(device_refreshers or ())

        async def _background_refresh() -> None:
            """Fetch slow metrics for each system_id without stale_ok."""
            _LOGGER.debug(
                "Jackery: background slow-metric refresh for %d system(s) / "
                "%d device(s)",
                len(sys_ids),
                len(dev_refreshers),
            )
            started_monotonic = time.monotonic()

            device_refresh_semaphore = asyncio.Semaphore(2)

            async def _refresh_device(
                refresh_device: Callable[[], Awaitable[Any]],
            ) -> Any:  # ruff:ignore[any-type]
                async with device_refresh_semaphore:
                    return await refresh_device()

            async def _refresh_devices() -> None:
                results = await asyncio.gather(
                    *(_refresh_device(refresh) for refresh in dev_refreshers),
                    return_exceptions=True,
                )
                for result in results:
                    if isinstance(result, BaseException):
                        raise result

            try:  # ruff:ignore[too-many-statements-in-try-clause]
                async with asyncio.timeout(BACKGROUND_SLOW_REFRESH_TIMEOUT_SEC):
                    device_task = (
                        asyncio.create_task(_refresh_devices())
                        if dev_refreshers
                        else None
                    )
                    try:
                        for sid in sys_ids:
                            await asyncio.gather(
                                get_with_ttl(
                                    sid,
                                    PAYLOAD_STATISTIC,
                                    self._slow_metrics_interval_sec,
                                    self.api.async_get_system_statistic,
                                    {},
                                ),
                                get_with_ttl(
                                    sid,
                                    PAYLOAD_ALARM,
                                    self._slow_metrics_interval_sec,
                                    self.api.async_get_alarm,
                                    None,
                                ),
                                get_with_ttl(
                                    sid,
                                    PAYLOAD_PV_TRENDS,
                                    self._slow_metrics_interval_sec,
                                    lambda s: self.api.async_get_pv_trends(
                                        s,
                                        **self._trend_query_kwargs(DATE_TYPE_DAY),
                                    ),
                                    {},
                                ),
                                get_with_ttl(
                                    sid,
                                    self._app_period_section(
                                        APP_SECTION_PV_TRENDS,
                                        DATE_TYPE_WEEK,
                                    ),
                                    self._slow_metrics_interval_sec,
                                    lambda s: self.api.async_get_pv_trends(
                                        s,
                                        **self._trend_query_kwargs(DATE_TYPE_WEEK),
                                    ),
                                    {},
                                ),
                                get_with_ttl(
                                    sid,
                                    self._app_period_section(
                                        APP_SECTION_PV_TRENDS,
                                        DATE_TYPE_MONTH,
                                    ),
                                    self._slow_metrics_interval_sec,
                                    lambda s: self.api.async_get_pv_trends(
                                        s,
                                        **self._trend_query_kwargs(DATE_TYPE_MONTH),
                                    ),
                                    {},
                                ),
                                get_with_ttl(
                                    sid,
                                    self._app_period_section(
                                        APP_SECTION_PV_TRENDS,
                                        DATE_TYPE_YEAR,
                                    ),
                                    self._slow_metrics_interval_sec,
                                    lambda s: self.api.async_get_pv_trends(
                                        s,
                                        **self._trend_query_kwargs(DATE_TYPE_YEAR),
                                    ),
                                    {},
                                ),
                                get_with_ttl(
                                    sid,
                                    PAYLOAD_HOME_TRENDS,
                                    self._slow_metrics_interval_sec,
                                    lambda s: self.api.async_get_home_trends(
                                        s,
                                        **self._trend_query_kwargs(DATE_TYPE_DAY),
                                    ),
                                    {},
                                ),
                                get_with_ttl(
                                    sid,
                                    self._app_period_section(
                                        APP_SECTION_HOME_TRENDS,
                                        DATE_TYPE_WEEK,
                                    ),
                                    self._slow_metrics_interval_sec,
                                    lambda s: self.api.async_get_home_trends(
                                        s,
                                        **self._trend_query_kwargs(DATE_TYPE_WEEK),
                                    ),
                                    {},
                                ),
                                get_with_ttl(
                                    sid,
                                    self._app_period_section(
                                        APP_SECTION_HOME_TRENDS,
                                        DATE_TYPE_MONTH,
                                    ),
                                    self._slow_metrics_interval_sec,
                                    lambda s: self.api.async_get_home_trends(
                                        s,
                                        **self._trend_query_kwargs(DATE_TYPE_MONTH),
                                    ),
                                    {},
                                ),
                                get_with_ttl(
                                    sid,
                                    self._app_period_section(
                                        APP_SECTION_HOME_TRENDS,
                                        DATE_TYPE_YEAR,
                                    ),
                                    self._slow_metrics_interval_sec,
                                    lambda s: self.api.async_get_home_trends(
                                        s,
                                        **self._trend_query_kwargs(DATE_TYPE_YEAR),
                                    ),
                                    {},
                                ),
                                get_with_ttl(
                                    sid,
                                    PAYLOAD_BATTERY_TRENDS,
                                    self._slow_metrics_interval_sec,
                                    lambda s: self.api.async_get_battery_trends(
                                        s,
                                        **self._trend_query_kwargs(DATE_TYPE_DAY),
                                    ),
                                    {},
                                ),
                                get_with_ttl(
                                    sid,
                                    self._app_period_section(
                                        APP_SECTION_BATTERY_TRENDS,
                                        DATE_TYPE_WEEK,
                                    ),
                                    self._slow_metrics_interval_sec,
                                    lambda s: self.api.async_get_battery_trends(
                                        s,
                                        **self._trend_query_kwargs(DATE_TYPE_WEEK),
                                    ),
                                    {},
                                ),
                                get_with_ttl(
                                    sid,
                                    self._app_period_section(
                                        APP_SECTION_BATTERY_TRENDS,
                                        DATE_TYPE_MONTH,
                                    ),
                                    self._slow_metrics_interval_sec,
                                    lambda s: self.api.async_get_battery_trends(
                                        s,
                                        **self._trend_query_kwargs(DATE_TYPE_MONTH),
                                    ),
                                    {},
                                ),
                                get_with_ttl(
                                    sid,
                                    self._app_period_section(
                                        APP_SECTION_BATTERY_TRENDS,
                                        DATE_TYPE_YEAR,
                                    ),
                                    self._slow_metrics_interval_sec,
                                    lambda s: self.api.async_get_battery_trends(
                                        s,
                                        **self._trend_query_kwargs(DATE_TYPE_YEAR),
                                    ),
                                    {},
                                ),
                                get_with_ttl(
                                    sid,
                                    PAYLOAD_DYNAMIC_PRICE,
                                    self._price_config_interval_sec,
                                    self.api.async_get_dynamic_price,
                                    {},
                                    backoff_key=f"dynamic_price:{sid}",
                                ),
                                get_with_ttl(
                                    sid,
                                    PAYLOAD_PRICE,
                                    self._price_config_interval_sec,
                                    self.api.async_get_power_price,
                                    {},
                                ),
                                get_with_ttl(
                                    sid,
                                    PAYLOAD_PRICE_SOURCES,
                                    self._price_config_interval_sec,
                                    self.api.async_get_price_sources,
                                    [],
                                ),
                                get_with_ttl(
                                    sid,
                                    PAYLOAD_PRICE_HISTORY_CONFIG,
                                    self._price_config_interval_sec,
                                    self.api.async_get_price_history_config,
                                    {},
                                ),
                                return_exceptions=True,
                            )
                        if device_task is not None:
                            await device_task
                    finally:
                        if device_task is not None:
                            if not device_task.done():
                                device_task.cancel()
                            await asyncio.gather(device_task, return_exceptions=True)
            except TimeoutError:
                _LOGGER.warning(
                    "Jackery: background slow-metric refresh timed out after %.0fs; "
                    "aborting cycle (will retry next tick)",
                    BACKGROUND_SLOW_REFRESH_TIMEOUT_SEC,
                )
            except asyncio.CancelledError:
                # Re-raise so the cancel-on-restack guard (above) actually stops
                # the prior in-flight task; a swallowed CancelledError would let
                # it keep running and stack. async_shutdown awaits this task under
                # suppress(CancelledError), so the re-raise is absorbed there.
                _LOGGER.debug("Jackery: background slow-metric refresh cancelled")
                raise
            except BACKGROUND_TASK_ERRORS as err:
                _LOGGER.debug("Jackery: background slow-metric refresh failed: %s", err)
            else:
                # Notify HA that fresh data is available so entity states
                # are updated immediately rather than waiting for the next
                # scheduled coordinator tick. The duration log is the P6
                # stall instrumentation: it makes the end of every bg run
                # visible so a lost request_refresh is diagnosable.
                _LOGGER.debug(
                    "Jackery: background slow-metric refresh completed in "
                    "%.1fs; requesting coordinator refresh",
                    time.monotonic() - started_monotonic,
                )
                if not self._shutdown_started:
                    await self.async_request_refresh()

        self._slow_metrics_bg_task = self.hass.async_create_background_task(
            _background_refresh(),
            f"jackery_slow_metrics_bg_{id(self)}",
            eager_start=False,
        )

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Diagnostics
    # ------------------------------------------------------------------

    @property
    def mqtt_diagnostics(self) -> dict[str, Any]:
        """Return the MQTT client diagnostics block for the diagnostics export."""  # ruff:ignore[property-docstring-starts-with-verb]
        return self.mqtt_diagnostics_snapshot()

    def mqtt_diagnostics_snapshot(
        self,
        *,
        redact_topics: bool = True,
    ) -> dict[str, Any]:
        """Return the MQTT client diagnostics block for the diagnostics export."""
        if self._mqtt is None:
            return {"enabled": False}
        diag = dict(self._mqtt.diagnostics_snapshot(redact_topics=redact_topics))
        diag["enabled"] = True
        diag["credential_mac_id_source"] = self.api.mqtt_mac_id_source
        # Key comes from ``MQTT_SESSION_MAC_ID`` so the diagnostics export
        # redacts it via ``REDACT_KEYS``; JACKERY_DEV_MODE=1 shows it verbatim.
        diag[MQTT_SESSION_MAC_ID] = self.api.mqtt_mac_id
        diag["slow_metrics_interval_seconds"] = self._slow_metrics_interval_sec
        diag["price_interval_seconds"] = self._price_config_interval_sec
        diag["subdevice_query_interval_seconds"] = self._subdevice_query_interval_sec
        diag["coordinator_polling_seconds"] = safe_int(
            self._configured_update_interval.total_seconds(),
        )
        diag["tls_certificate_verification"] = "enabled"
        diag["tls_insecure_warning"] = None
        diag["stale_battery_packs_dropped"] = self._stale_battery_packs_dropped
        diag["app_conflict_pause_cycles"] = self._mqtt_mgr.app_conflict_pause_cycles
        now_mono = time.monotonic()
        push_ts = self._last_property_push_monotonic
        last_property_push_age: float | None = (
            None if push_ts == float("-inf") else max(0.0, now_mono - push_ts)
        )
        http_ts = self._last_http_refresh_completed_monotonic
        last_http_property_age: float | None = (
            None if http_ts == float("-inf") else max(0.0, now_mono - http_ts)
        )
        diag["last_property_push_age_seconds"] = (
            None if last_property_push_age is None else round(last_property_push_age, 3)
        )
        diag["last_http_property_age_seconds"] = (
            None if last_http_property_age is None else round(last_http_property_age, 3)
        )
        diag["property_push_live_threshold_seconds"] = MQTT_LIVE_THRESHOLD_SEC
        active_endpoint_backoff_count = self._endpoint_backoff_active_count(now_mono)
        diag["active_endpoint_backoff_count"] = active_endpoint_backoff_count
        diag["third_party_mqtt_generated_token_active"] = (
            self._generated_third_party_mqtt_token is not None
        )
        pause_remaining = safe_int(self._mqtt_mgr.paused_until_monotonic - now_mono)
        diag["app_conflict_pause_remaining_seconds"] = max(0, pause_remaining or 0)
        diag["connect_backoff_remaining_seconds"] = (
            self._mqtt_connect_backoff_remaining()
        )
        diag["connect_backoff_signature"] = self._mqtt_mgr.backoff_signature
        return diag

    def app_chart_import_diagnostics(self) -> dict[str, Any]:  # ruff:ignore[too-many-locals]
        """Return current app-chart import coverage for diagnostics.

        This makes the day-to-hourly backfill routing explicit: every metric in
        ``APP_CHART_STAT_METRICS`` is evaluated against its documented day
        source candidates, not just PV.
        """
        now = self._local_now()
        devices: dict[str, Any] = {}
        for index, device_id in enumerate(sorted((self.data or {}).keys()), start=1):
            payload = (self.data or {}).get(device_id) or {}
            metric_rows: dict[str, Any] = {}
            for section_prefix, stat_key, metric_key, label in APP_CHART_STAT_METRICS:
                candidate_rows: list[dict[str, Any]] = []
                point_count = 0
                hour_section = self._app_period_section(section_prefix, DATE_TYPE_HOUR)
                hour_endpoint = {
                    "section": hour_section,
                    "queried": False,
                    "disabled_reason": "unsupported_app_2_1_1_date_type",
                    "replacement": "day_curve_to_day_hourly_recorder_buckets",
                }
                for section, source_stat_key in self._day_chart_source_candidates(
                    section_prefix,
                    stat_key,
                    metric_key,
                ):
                    source = payload.get(section)
                    if not isinstance(source, dict):
                        candidate_rows.append({
                            "section": section,
                            "stat_key": source_stat_key,
                            "present": False,
                            "point_count": 0,
                            "source_mode": "missing",
                        })
                        continue
                    points = day_power_energy_points(
                        source,
                        section,
                        source_stat_key,
                        bucket_minutes=60,
                        today=now.date(),
                        now=now,
                    )
                    series_key = day_power_series_key(
                        source,
                        section,
                        source_stat_key,
                    )
                    series = source.get(series_key) if series_key is not None else None
                    numeric_samples: list[float] = []
                    if isinstance(series, list):
                        numeric_samples = [
                            sample
                            for raw in series
                            if (sample := safe_float(raw)) is not None
                        ]
                    scalar_total = effective_period_total_value(
                        source,
                        section,
                        source_stat_key,
                    )
                    scalar_total_present = scalar_total is not None
                    source_mode = "unavailable"
                    if points:
                        if any(abs(sample) > 0 for sample in numeric_samples):
                            source_mode = "chart_series"
                        elif scalar_total_present:
                            source_mode = "scalar_total"
                        else:
                            source_mode = "zero_fill"
                    candidate_rows.append({
                        "section": section,
                        "stat_key": source_stat_key,
                        "present": True,
                        "unit": str(source.get("unit") or ""),
                        "series_key": series_key or "",
                        "scalar_total": scalar_total if scalar_total_present else 0.0,
                        "scalar_total_present": scalar_total_present,
                        "source_mode": source_mode,
                        "point_count": len(points),
                    })
                    point_count = max(point_count, len(points))
                metric_rows[metric_key] = {
                    "label": label,
                    "day_hourly_point_count": point_count,
                    "native_hour_endpoint": hour_endpoint,
                    "candidates": candidate_rows,
                }
            devices[f"device_{index}"] = metric_rows
        return {
            "bucket": EXTERNAL_STAT_BUCKET_DAY_HOURLY,
            "bucket_label": APP_DAY_CHART_BUCKET_LABEL,
            "devices": devices,
        }

    # --- restored from 24.05 lineage (offline/local/cmd=113 features) ---
    _MQTT_LIVE_MAIN_PROPERTY_KEYS = frozenset({
        FIELD_BAT_IN_PW,
        FIELD_BAT_OUT_PW,
        FIELD_BAT_SOC,
        FIELD_CHARGE_PLAN_PW,
        FIELD_ENERGY_PLAN_PW,
        FIELD_GRID_IN_PW,
        FIELD_GRID_OUT_PW,
        FIELD_IN_GRID_SIDE_PW,
        FIELD_IN_ONGRID_PW,
        FIELD_OTHER_LOAD_PW,
        FIELD_OUT_GRID_SIDE_PW,
        FIELD_OUT_ONGRID_PW,
        FIELD_PV_PW,
        FIELD_SOC,
        FIELD_STACK_IN_PW,
        FIELD_STACK_OUT_PW,
        FIELD_SW_EPS,
        FIELD_SW_EPS_IN_PW,
        FIELD_SW_EPS_OUT_PW,
        FIELD_SW_EPS_STATE,
    })

    @staticmethod
    def _with_discovery_source_marker(
        record: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Return discovery record with an explicit system-list/legacy source."""
        normalized = dict(record)
        system_meta = dict(normalized.get(PAYLOAD_SYSTEM_META) or {})
        device_meta = dict(normalized.get(PAYLOAD_DEVICE_META) or {})
        if PAYLOAD_DISCOVERY_SOURCE not in device_meta:
            system_id = normalized.get(FIELD_SYSTEM_ID)
            device_meta[PAYLOAD_DISCOVERY_SOURCE] = (
                DISCOVERY_SOURCE_SYSTEM_LIST
                if system_meta or system_id
                else DISCOVERY_SOURCE_LEGACY_BIND_LIST
            )
        normalized[PAYLOAD_SYSTEM_META] = system_meta
        normalized[PAYLOAD_DEVICE_META] = device_meta
        return normalized

    async def async_load_cached_discovery(self, reason: str) -> bool:
        """Use cached discovery metadata when Jackery cloud is unavailable."""
        try:
            cached = await async_load_discovery_cache(self.hass, self.entry.entry_id)
        except STORAGE_ERRORS as err:
            _LOGGER.debug("Jackery discovery cache load failed: %s", err)
            return False
        if not cached:
            return False
        self._device_index = {
            str(device_id): self._with_discovery_source_marker(record)
            for device_id, record in cached.items()
            if isinstance(record, Mapping)
        }
        if not self._device_index:
            return False
        self._discovery_source = "cache"
        self._last_discovery_refresh_monotonic = time.monotonic()
        _LOGGER.warning(
            "Jackery cloud discovery unavailable (%s); using cached discovery "
            "for local BLE startup while HTTP login/cache remains the primary path",
            reason,
        )
        return True

    async def _async_save_discovery_cache(self) -> None:
        """Persist discovery metadata needed for BLE during cloud outages."""
        try:
            await async_save_discovery_cache(
                self.hass,
                self.entry.entry_id,
                self._device_index,
            )
        except STORAGE_ERRORS as err:
            _LOGGER.debug("Jackery discovery cache save failed: %s", err)

    async def async_reconcile_local_mqtt_listener(self) -> None:
        """Restart only the HA-MQTT listener after local options change."""
        if self._shutdown_started:
            return
        for unsubscribe in self._local_mqtt_unsubs:
            with contextlib.suppress(Exception):
                unsubscribe()
        self._local_mqtt_unsubs.clear()

        message_tasks = set(self._local_mqtt_message_tasks)
        current_task = asyncio.current_task()
        if current_task is not None:
            message_tasks.discard(current_task)
        for task in message_tasks:
            if not task.done():
                task.cancel()
        pending_tasks: set[asyncio.Task[None]] = set()
        if message_tasks:
            _done_tasks, pending_tasks = await asyncio.wait(
                message_tasks,
                timeout=_BACKGROUND_TASK_STOP_TIMEOUT_SEC,
            )
        self._local_mqtt_message_tasks.intersection_update(pending_tasks)
        if pending_tasks:
            _LOGGER.warning(
                "%d Jackery HA-MQTT message task(s) did not stop within %.1fs; "
                "HTTP remains active",
                len(pending_tasks),
                _BACKGROUND_TASK_STOP_TIMEOUT_SEC,
            )
        await self.async_start_local_mqtt_listener()

    def _decrypt_binary_local_mqtt_payload(
        self, raw_payload: bytes | bytearray
    ) -> dict[str, Any] | None:
        """Attempt binary AES-CBC decryption for ESP / firmware frames."""
        for dev_id in list(self._device_index.keys()):
            key = self.device_bluetooth_key(dev_id)
            if not key:
                continue
            try:
                parsed_binary = ble.decrypt_binary_notify(bytes(raw_payload), key)
                if parsed_binary and parsed_binary.body:
                    payload = json.loads(parsed_binary.body.decode("utf-8"))
                    if isinstance(payload, dict):
                        return payload
            except Exception:  # ruff:ignore[blind-except]
                continue
        return None

    async def async_start_local_mqtt_listener(self) -> None:  # ruff:ignore[too-many-statements]
        """Subscribe to the user's HA MQTT broker for local bridge payloads."""
        if self._shutdown_started:
            return
        options = getattr(self.entry, "options", {}) or {}
        data = getattr(self.entry, "data", {}) or {}
        if CONF_LOCAL_MQTT_ENABLE in options or CONF_LOCAL_MQTT_ENABLE in data:
            enabled = config_entry_bool_option(
                self.entry,
                CONF_LOCAL_MQTT_ENABLE,
                DEFAULT_LOCAL_MQTT_ENABLE,
            )
        else:
            enabled = config_entry_bool_option(
                self.entry,
                CONF_THIRD_PARTY_MQTT_ENABLE,
                DEFAULT_THIRD_PARTY_MQTT_ENABLE,
            )
        if not enabled:
            return
        if self._local_mqtt_unsubs:
            return
        topics = [f"{MQTT_TOPIC_PREFIX}/+/{suffix}" for suffix in MQTT_TOPIC_SUFFIXES]

        async def _handle_local_mqtt_message(message: Any) -> None:  # ruff:ignore[any-type]
            if self._shutdown_started:
                return
            raw_payload = message.payload
            payload: Any = None
            if isinstance(raw_payload, (bytes, bytearray)):
                try:
                    payload = json.loads(raw_payload.decode("utf-8"))
                except (UnicodeDecodeError, json.JSONDecodeError):
                    payload = self._decrypt_binary_local_mqtt_payload(raw_payload)
            elif isinstance(raw_payload, str):
                try:
                    payload = json.loads(raw_payload)
                except PAYLOAD_PARSE_ERRORS as err:
                    _LOGGER.debug(
                        "Jackery local MQTT payload on %s is not JSON: %s",
                        message.topic,
                        err,
                    )
                    return
            else:
                payload = raw_payload
            if not isinstance(payload, dict):
                _LOGGER.debug(
                    "Jackery local MQTT payload on %s is %s, expected object",
                    message.topic,
                    type(payload).__name__,
                )
                return
            await self.async_handle_local_mqtt_message(
                str(message.topic),
                payload,
            )

        def _queue_local_mqtt_message(message: Any) -> None:  # ruff:ignore[any-type]
            if self._shutdown_started:
                return
            if not payload_has_jackery_marker(message.payload):
                # Foreign broker traffic on a shared topic — no task, no JSON
                # parse (layer independence: never load the polling loop).
                self._local_mqtt_listener_foreign_ignored += 1
                return
            self._local_mqtt_listener_messages_received += 1
            task = self.hass.async_create_background_task(
                _handle_local_mqtt_message(message),
                name=f"{DOMAIN}_local_mqtt_message",
                eager_start=False,
            )
            self._local_mqtt_message_tasks.add(task)

            def _drop_local_mqtt_task(done: asyncio.Task[None]) -> None:
                self._local_mqtt_message_tasks.discard(done)
                try:
                    done.result()
                except asyncio.CancelledError:
                    return
                except Exception as err:  # ruff:ignore[blind-except]  # message-task boundary
                    _LOGGER.debug("Jackery HA-MQTT message task failed: %s", err)

            task.add_done_callback(_drop_local_mqtt_task)

        try:
            for topic in topics:
                unsubscribe = await mqtt.async_subscribe(
                    self.hass,
                    topic,
                    _queue_local_mqtt_message,
                    qos=0,
                    encoding="utf-8",
                )
                self._local_mqtt_unsubs.append(unsubscribe)
        except (HomeAssistantError, RuntimeError) as err:
            for unsubscribe in self._local_mqtt_unsubs:
                with contextlib.suppress(Exception):
                    unsubscribe()
            self._local_mqtt_unsubs.clear()
            _LOGGER.warning(
                "Jackery local MQTT listener could not subscribe; "
                "BLE/cloud MQTT/HTTP remain active: %s",
                err,
            )
            return
        _LOGGER.info(
            "Jackery local MQTT listener subscribed to %d topic(s)",
            len(self._local_mqtt_unsubs),
        )

    def is_device_locally_reachable(self, device_id: str) -> bool:
        """Return whether an already-loaded local transport sees the device.

        A recently accepted local-MQTT frame also proves local reachability for
        that device. Otherwise this uses the already-loaded HA-core helper
        ``bluetooth.async_address_present`` documented at
        https://developers.home-assistant.io/docs/core/bluetooth/api so that a
        Jackery-cloud outage (which sets ``onlineStatus`` / ``onlineState`` to 0
        on the device payloads) does not falsely mark entities as unavailable
        while the device is still broadcasting on BLE and the listener owns a
        live GATT session. This method never imports or starts the optional
        Bluetooth integration, so Bluetooth dependencies cannot block HTTP.

        For sub-devices (battery packs, CT meters) the parent device_id is
        used — battery packs and CT meters do not advertise on their own MAC,
        they live behind the SolarVault host's BLE radio. Sensor classes that
        wrap a sub-device already set ``self._device_id`` to the parent's
        Jackery device id, so this method does not need an extra mapping pass.
        """
        last_local_mqtt = self._local_mqtt_last_device_message_monotonic.get(device_id)
        if (
            last_local_mqtt is not None
            and time.monotonic() - last_local_mqtt <= MQTT_LIVE_THRESHOLD_SEC
        ):
            return True
        address = self._ble_address_for_device(device_id)
        if not address:
            return False
        bluetooth_module = sys.modules.get("homeassistant.components.bluetooth")
        async_address_present = getattr(
            bluetooth_module,
            "async_address_present",
            None,
        )
        if not callable(async_address_present):
            return False
        return bool(
            async_address_present(self.hass, address, connectable=True),
        )

    def is_device_reachable(self, device_id: str) -> bool:
        """Return whether any independent transport recently reached the device."""
        if self.is_device_locally_reachable(device_id):
            return True
        now = time.monotonic()
        freshness_window = max(
            60.0,
            self._configured_update_interval.total_seconds() * 2,
        )
        last_http = getattr(
            self,
            "_last_http_device_refresh_monotonic",
            {},
        ).get(device_id)
        if last_http is not None and now - last_http <= freshness_window:
            return True
        entry = (self.data or {}).get(device_id) or {}
        marker = entry.get(PAYLOAD_MQTT_LAST)
        if not isinstance(marker, dict):
            return False
        received_at = safe_float(marker.get("received_at_monotonic"))
        return received_at is not None and now - received_at <= freshness_window

    def mark_mqtt_session_cache_loaded(
        self,
        persisted: Mapping[str, str] | None,
    ) -> bool:
        """Record Layer-5 cache reconciliation and queue tracked persistence.

        Returns:
            True when this coordinator still owns the config entry and persistence
            was enabled; False after unload or runtime replacement.
        """
        if (
            self._shutdown_started
            or getattr(self.entry, "runtime_data", None) is not self
        ):
            return False
        self._persisted_mqtt_session = cast(
            "MqttSessionSnapshot | None",
            persisted,
        )
        self._mqtt_session_cache_loaded = True
        self._schedule_background_once(
            "mqtt_persist",
            self._async_persist_mqtt_session_if_changed,
            name=f"{DOMAIN}_mqtt_persist",
        )
        return True

    async def _async_persist_mqtt_session_if_changed(self) -> None:
        """Store the current MQTT session so cloud outages cannot block reconnects."""
        if (
            not self._mqtt_session_cache_loaded
            or self._shutdown_started
            or getattr(self.entry, "runtime_data", None) is not self
        ):
            return
        snapshot = self.api.mqtt_session_snapshot()
        if snapshot is None or snapshot == self._persisted_mqtt_session:
            return
        try:
            await async_save_mqtt_session(self.hass, self.entry.entry_id, **snapshot)
        except STORAGE_ERRORS as err:
            _LOGGER.debug("Jackery MQTT session cache save failed: %s", err)
            return
        self._persisted_mqtt_session = snapshot

    async def _async_invalidate_mqtt_session_cache(self, reason: str) -> None:
        """Drop the cached MQTT session after a confirmed broker rejection."""
        try:
            await async_clear_mqtt_session(self.hass, self.entry.entry_id)
        except BACKGROUND_TASK_ERRORS as err:
            _LOGGER.debug(
                "Jackery MQTT session cache clear failed (%s): %s",
                reason,
                err,
            )
            return
        self._persisted_mqtt_session = None
        _LOGGER.info("Jackery MQTT session cache cleared: %s", reason)

    async def async_load_local_daily_snapshots(self) -> None:
        """Restore midnight-anchor snapshots for the daily-energy deltas.

        Scheduled after the mandatory first HTTP refresh. Persistence remains
        disabled until this read succeeds so a cold-start refresh cannot erase
        the stored same-day anchors.
        """
        starting_signature = self._local_daily_signature(self._local_daily_snapshots)
        try:
            cached = await async_load_daily_cache(self.hass, self.entry.entry_id)
        except STORAGE_ERRORS as err:
            _LOGGER.debug("Jackery local daily cache load failed: %s", err)
            return
        if (
            self._shutdown_started
            or getattr(self.entry, "runtime_data", None) is not self
        ):
            return
        cached_snapshots = {
            str(device_id): dict(snapshot)
            for device_id, snapshot in cached.items()
            if isinstance(snapshot, dict)
        }
        cached_signature = self._local_daily_signature(cached_snapshots)
        if (
            self._local_daily_signature(self._local_daily_snapshots)
            != starting_signature
        ):
            reconciled = dict(cached_snapshots)
            for device_id, current_snapshot in self._local_daily_snapshots.items():
                current_day = current_snapshot.get("day")
                current_values = current_snapshot.get("values")
                if not isinstance(current_day, str) or not isinstance(
                    current_values,
                    dict,
                ):
                    continue
                try:
                    snapshot_date = date.fromisoformat(current_day)
                except ValueError:
                    continue
                normalized_values: dict[str, int | float | None] = {}
                for metric, value in current_values.items():
                    if not isinstance(metric, str) or value is None:
                        continue
                    try:
                        normalized_values[metric] = int(value)
                    except TypeError, ValueError:
                        continue
                normalized_device_id = str(device_id)
                reconciled[normalized_device_id] = refresh_snapshot(
                    reconciled.get(normalized_device_id),
                    today=snapshot_date,
                    current_values=normalized_values,
                )
            self._local_daily_snapshots = reconciled
        else:
            self._local_daily_snapshots = cached_snapshots
        self._persisted_local_daily_signature = cached_signature
        self._local_daily_cache_loaded = True
        self._schedule_background_once(
            "daily_persist",
            self._async_persist_local_daily_snapshots_if_changed,
            name=f"{DOMAIN}_daily_persist",
        )

    @staticmethod
    def _local_daily_signature(
        snapshots: Mapping[str, dict[str, Any]],
    ) -> str:
        """Return a stable string signature for the snapshot map."""
        return local_daily_signature(snapshots)

    @staticmethod
    def _local_daily_counter_properties(
        properties: Mapping[str, Any],
        payload: Mapping[str, Any],
    ) -> dict[str, Any]:
        """Return main properties supplemented by live CT lifetime counters."""
        combined = dict(properties)
        ct_meter = payload.get(PAYLOAD_CT_METER)
        if not isinstance(ct_meter, Mapping):
            return combined
        for metric in (
            FIELD_CT_TOTAL_PHASE_ENERGY,
            FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
        ):
            value = ct_meter.get(metric)
            if value is not None:
                combined[metric] = value
        return combined

    def _refresh_local_daily_for_device(
        self,
        device_id: str,
        properties: Mapping[str, Any],
        *,
        today: date,
        allow_new_anchor_delta: bool,
    ) -> dict[str, int]:
        """Update the midnight snapshot and return today's energy deltas.

        ``properties`` is the merged ``PAYLOAD_PROPERTIES`` dict produced by
        the regular update cycle. Returns a dict ``{metric_key: today_wh}``
        for the documented :data:`LOCAL_DAILY_LIFETIME_METRICS`. Missing
        counters (firmware variant without that field) are skipped. A new
        anchor is only trusted during an observed coordinator day rollover;
        cold-start anchors stay silent so a mid-day restart cannot create
        artificial 0 kWh daily values.
        """
        current_values: dict[str, int | float | None] = {
            metric: properties.get(metric) for metric in LOCAL_DAILY_LIFETIME_METRICS
        }
        previous_snapshot = self._local_daily_snapshots.get(device_id)
        previous_same_day = (
            isinstance(previous_snapshot, dict)
            and previous_snapshot.get("day") == today.isoformat()
        )
        if not previous_same_day and not allow_new_anchor_delta:
            return {}
        snapshot = refresh_snapshot(
            previous_snapshot,
            today=today,
            current_values=current_values,
        )
        self._local_daily_snapshots[device_id] = snapshot
        delta_snapshot = previous_snapshot if previous_same_day else snapshot
        deltas: dict[str, int] = {}
        for metric in LOCAL_DAILY_LIFETIME_METRICS:
            delta = daily_delta(
                delta_snapshot,
                metric,
                properties.get(metric),
                today=today,
            )
            if delta is None:
                continue
            deltas[metric] = delta
        return deltas

    def local_daily_energy_kwh(
        self,
        device_id: str,
        metric_key: str,
    ) -> float | None:
        """Return today's local energy delta for a device + metric, in kWh.

        ``coordinator.data[device_id][PAYLOAD_LOCAL_DAILY_ENERGY]`` stores
        the deltas in Wh as integers. Sensors that prefer the cloud
        ``/v1/device/stat/*?dateType=day`` total can fall back to this value
        when the cloud response is stale or missing. Returns ``None`` when
        the device has no snapshot yet, the metric is not tracked, or the
        coordinator has not run a successful refresh.
        """
        payload = (self.data or {}).get(device_id) or {}
        section = payload.get(PAYLOAD_LOCAL_DAILY_ENERGY)
        if not isinstance(section, dict):
            return None
        value = section.get(metric_key)
        if value is None:
            return None
        try:
            return round(float(value) / 1000.0, 5)
        except TypeError, ValueError:
            return None

    def cached_discovery_snapshot(self) -> dict[str, dict[str, Any]]:
        """Return a minimal coordinator payload from cached discovery metadata."""
        snapshot: dict[str, dict[str, Any]] = {}
        for device_id, idx in self._device_index.items():
            device_meta = idx.get(PAYLOAD_DEVICE_META) or {}
            system_meta = idx.get(PAYLOAD_SYSTEM_META) or {}
            snapshot[device_id] = {
                PAYLOAD_PROPERTIES: {},
                PAYLOAD_DEVICE: dict(device_meta),
                PAYLOAD_DISCOVERY: dict(device_meta),
                PAYLOAD_SYSTEM: dict(system_meta),
            }
        return snapshot

    async def _async_persist_local_daily_snapshots_if_changed(self) -> None:
        """Write the daily-cache file when at least one anchor row changed."""
        if (
            not self._local_daily_cache_loaded
            or self._shutdown_started
            or getattr(self.entry, "runtime_data", None) is not self
        ):
            return
        signature = self._local_daily_signature(self._local_daily_snapshots)
        if signature == self._persisted_local_daily_signature:
            return
        try:
            await async_save_daily_cache(
                self.hass,
                self.entry.entry_id,
                snapshots=self._local_daily_snapshots,
            )
        except STORAGE_ERRORS as err:
            _LOGGER.debug("Jackery local daily cache save failed: %s", err)
            return
        self._persisted_local_daily_signature = signature

    def _mqtt_live_properties_are_fresh(self, entry: dict[str, Any]) -> bool:
        """Return True when MQTT has live property data newer than HTTP cache."""
        marker = entry.get(PAYLOAD_MQTT_LAST)
        if not isinstance(marker, dict):
            return False
        received_at = safe_float(marker.get("received_at_monotonic"))
        if received_at is not None:
            freshness_window = max(
                60.0,
                self._configured_update_interval.total_seconds() * 2,
            )
            if time.monotonic() - received_at <= freshness_window:
                return True
        return False

    def _live_ct_is_fresh(self, device_id: str) -> bool:
        """Return whether a push transport recently updated this device's CT data."""
        received_times: dict[str, float] = getattr(
            self,
            "_live_ct_received_monotonic",
            {},
        )
        received_at = received_times.get(device_id)
        if received_at is None:
            return False
        freshness_window = max(
            60.0,
            self._configured_update_interval.total_seconds() * 2,
        )
        return time.monotonic() - received_at <= freshness_window

    def _http_properties_with_live_overrides(
        self,
        entry: dict[str, Any],
        http_props: dict[str, Any],
        *,
        device_id: str | None = None,
    ) -> dict[str, Any]:
        """Keep fresh MQTT/BLE live telemetry from being overwritten by stale HTTP.

        HTTP/API remains the unconditional primary fetch and its pristine
        response stays in ``PAYLOAD_HTTP_PROPERTIES``. When a live (MQTT/BLE)
        frame is fresh, the approved live keys override HTTP; keys HTTP omits
        (combine/CT fields) are left untouched (AGENTS.md §1.2: local is
        preferred for live values).
        """
        fallback_fresh = self._mqtt_live_properties_are_fresh(entry)
        per_key = (
            getattr(self, "_live_property_received_monotonic", {}).get(device_id, {})
            if device_id is not None
            else {}
        )
        if not fallback_fresh and not per_key:
            return http_props
        live_props = entry.get(PAYLOAD_PROPERTIES) or {}
        if not isinstance(live_props, dict):
            return http_props
        guarded = dict(http_props)
        freshness_window = max(
            60.0,
            self._configured_update_interval.total_seconds() * 2,
        )
        now = time.monotonic()
        for key in self._MQTT_LIVE_MAIN_PROPERTY_KEYS:
            received_at = per_key.get(key)
            key_is_fresh = (
                received_at is not None and now - received_at <= freshness_window
            )
            if (
                key in guarded
                and live_props.get(key) is not None
                and (key_is_fresh or (not per_key and fallback_fresh))
            ):
                guarded[key] = live_props[key]
        return guarded

    async def async_apply_local_mqtt_config_to_devices(self) -> bool:
        """Push the user's local-MQTT bridge config to every known device.

        Reads the config-entry options (``CONF_LOCAL_MQTT_ENABLE``, host, port,
        credentials) and, when enabled, sends ``SET_THIRD_PARTY_MQTT_CONFIG``
        (cmd=113) to each device in ``_device_index`` through Jackery Cloud
        MQTT. Idempotent: a device already pointing at the configured broker
        just re-receives the same body. The cloud-connect callback schedules
        this method after every reconnect, independently from local MQTT/BLE.

        If the option is disabled or the host is empty, the method is a no-op —
        existing device-side bridge config is left untouched so users do not
        lose a setup they put in via the Jackery app.
        """
        # Resolve each field as ``local_mqtt_*`` first, then the legacy
        # ``third_party_mqtt_*`` keys. This mirrors the config-flow's own
        # normalization in ``_current_local_mqtt_options`` — without the
        # fallback, a host stored under ``third_party_mqtt_ip`` reads back
        # empty here even though the bridge is enabled, so the push is wrongly
        # skipped ("enabled but no host configured"). Keep both in sync.
        # The user's explicit local-bridge toggle is authoritative. Only fall
        # back to the legacy app-synced ``third_party_mqtt_enable`` flag when
        # ``local_mqtt_enable`` was never set — otherwise a stale legacy ``True``
        # OR-overrides an explicit disable, so the bridge "cannot be turned off"
        # and keeps pushing (and warning) against the user's choice.
        options = getattr(self.entry, "options", {}) or {}
        data = getattr(self.entry, "data", {}) or {}
        if CONF_LOCAL_MQTT_ENABLE in options or CONF_LOCAL_MQTT_ENABLE in data:
            enabled = config_entry_bool_option(
                self.entry,
                CONF_LOCAL_MQTT_ENABLE,
                DEFAULT_LOCAL_MQTT_ENABLE,
            )
        else:
            enabled = config_entry_bool_option(
                self.entry,
                CONF_THIRD_PARTY_MQTT_ENABLE,
                False,
            )
        if not enabled:
            return True
        host = (
            config_entry_str_option(self.entry, CONF_LOCAL_MQTT_HOST, "").strip()
            or config_entry_str_option(self.entry, CONF_THIRD_PARTY_MQTT_IP, "").strip()
        )
        if not host:
            # Warn once per misconfiguration, not every push cycle — a missing
            # host is a static config state, so repeating the warning each
            # coordinator update is pure log noise.
            if not getattr(self, "_local_mqtt_no_host_warned", False):
                _LOGGER.warning(
                    "Jackery local MQTT bridge is enabled but no host is "
                    "configured; skipping device push. Set host in the Jackery "
                    "integration options.",
                )
                self._local_mqtt_no_host_warned = True
            return True
        self._local_mqtt_no_host_warned = False
        port = config_entry_int_option(
            self.entry,
            CONF_LOCAL_MQTT_PORT,
            0,
        ) or config_entry_int_option(
            self.entry,
            CONF_THIRD_PARTY_MQTT_PORT,
            DEFAULT_LOCAL_MQTT_PORT,
        )
        username = config_entry_str_option(
            self.entry,
            CONF_LOCAL_MQTT_USERNAME,
            "",
        ) or config_entry_str_option(self.entry, CONF_THIRD_PARTY_MQTT_USERNAME, "")
        password = config_entry_str_option(
            self.entry,
            CONF_LOCAL_MQTT_PASSWORD,
            "",
        ) or config_entry_str_option(self.entry, CONF_THIRD_PARTY_MQTT_PASSWORD, "")
        success = True
        config_errors = getattr(self, "_local_mqtt_config_last_errors", {})
        if not isinstance(config_errors, dict):
            config_errors = {}
        self._local_mqtt_config_last_errors = config_errors
        for device_id in list(self._device_index):
            try:
                await self.async_set_third_party_mqtt_config(
                    device_id,
                    enable=True,
                    ip=host,
                    port=port,
                    username=username,
                    password=password,
                )
                config_errors.pop(str(device_id), None)
            except BACKGROUND_TASK_ERRORS as err:
                success = False
                error = exception_debug_message(err)
                log = (
                    _LOGGER.warning
                    if config_errors.get(str(device_id)) != error
                    else _LOGGER.debug
                )
                config_errors[str(device_id)] = error
                log(
                    "Jackery local MQTT bridge: failed to push config to "
                    "device %s (%s); scheduling a bounded retry",
                    device_id,
                    error,
                )
        return success

    def _schedule_mqtt_poll_queries(self, snapshot: dict[str, dict[str, Any]]) -> None:
        """Queue MQTT query commands without blocking the HTTP poll result."""
        if self._shutdown_started or self._mqtt is None or not self._mqtt.is_connected:
            return
        if self._mqtt_poll_task is not None and not self._mqtt_poll_task.done():
            return
        self._mqtt_poll_task = self.hass.async_create_background_task(
            self._async_mqtt_poll_queries(dict(snapshot)),
            name=f"{DOMAIN}_mqtt_poll_queries",
            eager_start=False,
        )

    async def _async_mqtt_poll_queries(
        self,
        snapshot: dict[str, dict[str, Any]],
    ) -> None:
        """Refresh app-side MQTT-only data after the HTTP poll has completed."""
        try:
            await self._async_query_subdevices_for_missing(snapshot=snapshot)
            await self._async_query_system_info_for_missing(snapshot=snapshot)
            await self._async_query_weather_plan_for_missing(snapshot=snapshot)
        except ConfigEntryAuthFailed as err:
            self._defer_background_auth_failure(err)
        except BACKGROUND_TASK_ERRORS as err:
            _LOGGER.debug(
                "Jackery MQTT polling query failed: %s",
                exception_debug_message(err),
            )

    def _schedule_shadow_fallback(
        self,
        snapshot: dict[str, dict[str, Any]],
    ) -> None:
        """Queue the HTTP shadow fallback without blocking the HTTP poll result.

        Unlike :meth:`_schedule_mqtt_poll_queries`, this runs *regardless* of
        the MQTT connection state: the whole point of the fallback is to fill
        subdevice buckets when MQTT never connected (HTTP-primary). A single
        in-flight task handle prevents the background work from piling up.

        Args:
            snapshot: The freshly-built HTTP coordinator result to scan.
        """
        if self._shutdown_started:
            return
        if (
            self._shadow_fallback_task is not None
            and not self._shadow_fallback_task.done()
        ):
            return
        self._shadow_fallback_task = self.hass.async_create_background_task(
            self._async_shadow_fallback(dict(snapshot)),
            name=f"{DOMAIN}_shadow_fallback",
            eager_start=False,
        )

    async def _async_shadow_fallback(
        self,
        snapshot: dict[str, dict[str, Any]],
    ) -> None:
        """Run the shadow fallback, routing background auth failures safely."""
        try:
            await self._async_shadow_fallback_for_missing(snapshot)
        except ConfigEntryAuthFailed as err:
            self._defer_background_auth_failure(err)
        except BACKGROUND_TASK_ERRORS as err:
            _LOGGER.debug(
                "Jackery shadow fallback failed: %s",
                exception_debug_message(err),
            )

    @staticmethod
    def _entry_accessories(entry: dict[str, Any]) -> list[dict[str, Any]]:
        """Return enumerated accessories from ``system_meta``/``system``."""
        for section in (PAYLOAD_SYSTEM_META, PAYLOAD_SYSTEM):
            system = entry.get(section)
            if not isinstance(system, dict):
                continue
            accessories = system.get(FIELD_ACCESSORIES)
            if isinstance(accessories, list):
                return [item for item in accessories if isinstance(item, dict)]
        return []

    @staticmethod
    def _shadow_parent_device_sn(entry: dict[str, Any]) -> str | None:
        """Resolve the parent device serial used by the shadow endpoints."""
        for section in (PAYLOAD_DEVICE, PAYLOAD_DEVICE_META):
            source = entry.get(section)
            if isinstance(source, dict) and source.get(FIELD_DEVICE_SN):
                return str(source[FIELD_DEVICE_SN])
        return None

    @staticmethod
    def _shadow_system_id(entry: dict[str, Any]) -> str | None:
        """Resolve the DIY/system id used by the system-shadow endpoint."""
        for section in (PAYLOAD_SYSTEM_META, PAYLOAD_SYSTEM):
            source = entry.get(section)
            if not isinstance(source, dict):
                continue
            sys_id = source.get(FIELD_SYSTEM_ID) or source.get(FIELD_ID)
            if sys_id:
                return str(sys_id)
        return None

    @staticmethod
    def _shadow_device_numeric_id(entry: dict[str, Any]) -> str | None:
        """Resolve the numeric device id used by the TOU-plan endpoint."""
        for section in (PAYLOAD_DEVICE, PAYLOAD_DEVICE_META):
            source = entry.get(section)
            if not isinstance(source, dict):
                continue
            dev_id = source.get(FIELD_DEVICE_ID) or source.get(FIELD_ID)
            if dev_id:
                return str(dev_id)
        return None

    @classmethod
    def _shadow_has_home_config_context(cls, entry: dict[str, Any]) -> bool:
        """Return True when HTTP config fallback endpoints match this payload."""
        if cls._shadow_system_id(entry) is not None:
            return True
        props = entry.get(PAYLOAD_PROPERTIES) or {}
        if not isinstance(props, dict):
            return False
        return any(
            key in props
            for key in (
                FIELD_DEFAULT_PW,
                FIELD_IS_AUTO_STANDBY,
                FIELD_IS_FOLLOW_METER_PW,
                FIELD_MAX_OUT_PW,
                FIELD_SW_EPS,
                FIELD_TEMP_UNIT,
                FIELD_WORK_MODEL,
            )
        )

    def _require_home_config_context(self, device_id: str, action: str) -> None:
        """Raise when a Home/System config endpoint is requested for non-Home data."""
        payload = (self.data or {}).get(device_id) or {}
        if self._shadow_has_home_config_context(payload):
            return
        msg = f"{action} is only available for Home/System devices: {device_id}"
        raise HomeAssistantError(msg)

    async def _async_shadow_fallback_for_missing(
        self,
        snapshot: dict[str, dict[str, Any]],
    ) -> None:
        """Fill subdevice live buckets from HTTP shadows when MQTT can't deliver.

        MQTT (Layer 5) is supplemental and incomplete. This HTTP shadow path
        still runs on its own cadence when MQTT/local/BLE data exists, because
        Layer 5 must never suppress HTTP-only fields. The shadow is HTTP, so it
        must never write ``PAYLOAD_MQTT_LAST`` (that marker means a genuine MQTT
        frame arrived). Per-SN failures are swallowed best-effort so one
        accessory cannot abort the rest; auth failures are a ``JackeryError``
        subclass and are likewise swallowed here because the primary HTTP path
        owns re-authentication.

        Args:
            snapshot: The HTTP coordinator result to scan and backfill.
        """
        if not snapshot:
            return
        now = time.monotonic()
        new_data: dict[str, dict[str, Any]] | None = None
        for device_id, entry in snapshot.items():
            # Full-wire rule (owner, 2026-07-04): the HTTP shadow poll is a
            # primary, unconditional data path. Earlier revisions skipped it
            # while MQTT frames looked fresh and required enumerated
            # accessories even for the system-level shadow — that starved
            # every shadow-only field (CT electrical detail, meter
            # comm/funForm, SystemBody config) as soon as MQTT was live.
            # The per-device cadence is the only remaining limiter.
            last_query = self._last_shadow_query.get(device_id, 0.0)
            if (now - last_query) < self._subdevice_query_interval_sec:
                continue
            parent_sn = self._shadow_parent_device_sn(entry)
            if not parent_sn:
                continue
            self._last_shadow_query[device_id] = now
            working = dict(entry)
            touched = await self._async_apply_shadows_for_entry(
                device_id,
                working,
                self._entry_accessories(entry),
                parent_sn=parent_sn,
            )
            if touched:
                if new_data is None:
                    new_data = dict(snapshot)
                new_data[device_id] = working
        if new_data is not None:
            self._push_partial_update(new_data)

    async def _async_apply_shadows_for_entry(
        self,
        device_id: str,
        working: dict[str, Any],
        accessories: list[dict[str, Any]],
        *,
        parent_sn: str,
    ) -> bool:
        """Fetch + merge the system shadow and every accessory sub-shadow.

        Both shadows refresh at the per-device cadence even when a bucket
        already holds data: the merge sink combines SN-keyed lists and
        never blanks populated values, so a refresh can only update — the
        old present-in-bucket skip meant a single MQTT frame permanently
        blocked the HTTP-only fields of that accessory.
        """
        touched = False
        system_body = await self._async_fetch_system_shadow_body(
            device_id,
            parent_sn=parent_sn,
            system_id=self._shadow_system_id(working),
        )
        if system_body is not None:
            if self._merge_subdevice_data(
                working,
                system_body,
                device_id=device_id,
                source_transport=TransportSource.HTTP,
            ):
                touched = True
            if self._merge_system_info_fields(device_id, working, system_body):
                touched = True
        for accessory in accessories:
            dev_type = subdevice_dev_type(
                accessory,
                rejection_callback=self.record_schema_rejection,
            )
            sub_device_sn = subdevice_serial(accessory)
            if dev_type is None or sub_device_sn is None:
                continue
            if dev_type not in self._SHADOW_DEV_TYPE_BUCKETS:
                continue
            shadow_body = await self._async_fetch_sub_shadow_body(
                device_id,
                dev_type=dev_type,
                parent_sn=parent_sn,
                sub_device_sn=sub_device_sn,
            )
            # Each body is routed through the SN-keyed merge sink so the
            # sub-shadow and system-shadow lists combine by serial instead
            # of one clobbering the other (G-sub-1a bug-(b) guard).
            if shadow_body is not None and self._merge_subdevice_data(
                working,
                shadow_body,
                device_id=device_id,
                source_transport=TransportSource.HTTP,
            ):
                touched = True
        # HTTP-only config buckets whose endpoints were wired but never polled
        # (owner invariant 2026-07-05: everything must come over HTTP). Additive
        # — they only fill their own bucket and cannot affect the merges above.
        if await self._async_apply_smart_mode(device_id, working):
            touched = True
        if await self._async_apply_smart_schedule(device_id, working):
            touched = True
        if await self._async_apply_tou_plan(device_id, working):
            touched = True
        self._carry_forward_shadow_buckets(device_id, working)
        return touched

    def _carry_forward_shadow_buckets(
        self,
        device_id: str,
        working: dict[str, Any],
    ) -> None:
        """Preserve last-known HTTP shadow config buckets across poll cycles.

        The smart-mode, smart-schedule and TOU buckets are filled best-effort
        from HTTP endpoints that can transiently fail (e.g. ``dynamicPrice``
        returning ``code=10600``) or return empty. When a cycle does not refill
        a bucket, the diagnostic sensors would otherwise flicker to Unknown
        every cycle. Carrying the previous non-empty bucket forward keeps them
        stable, matching the stated invariant that a fill never blanks an
        existing bucket.
        """
        previous = (self.data or {}).get(device_id) or {}
        for bucket in (
            PAYLOAD_SMART_MODE,
            PAYLOAD_SMART_SCHEDULE,
            PAYLOAD_TOU_SCHEDULE,
        ):
            if working.get(bucket):
                continue
            prior = previous.get(bucket)
            if isinstance(prior, dict) and prior:
                working[bucket] = dict(prior)

    def _merge_system_info_fields(
        self,
        device_id: str,
        working: dict[str, Any],
        system_body: dict[str, Any],
    ) -> bool:
        """Mirror SystemBody config fields from the HTTP system shadow.

        ``_merge_subdevice_data`` only mirrors ``SUBDEVICE_MAIN_MIRROR_KEYS``
        into main properties, so the SystemBody-only fields the app reads from
        CombineData over MQTT (``stat``, ``ctStat``, ``gridSate``,
        ``ongridStat``, ``energyPlanPw``, ``maxSysOutPw``, ``maxSysInPw``,
        ``funcEnable``) were dropped on the HTTP path and stayed Unknown while
        MQTT was down. HTTP is the authoritative, always-on source (owner
        invariant 2026-07-05), so surface them into the same
        ``PAYLOAD_PROPERTIES`` the MQTT CombineData handler writes and cache
        them. Section-targeted (not a widened accessory allowlist) so a stray
        sub-device ``stat``/``gridSate`` cannot bleed into main properties.

        Returns:
            True when at least one SystemBody info field was merged.
        """
        system_info = {
            key: system_body[key]
            for key in self._SYSTEM_INFO_KEYS
            if system_body.get(key) is not None
        }
        if not system_info:
            return False
        working[PAYLOAD_PROPERTIES] = self._merge_main_properties_for_device(
            device_id,
            working.get(PAYLOAD_PROPERTIES) or {},
            system_info,
            source=TransportSource.HTTP,
        )
        self._system_info_cache.setdefault(device_id, {}).update(system_info)
        self._system_info_cache_monotonic[device_id] = time.monotonic()
        return True

    async def _async_apply_smart_mode(
        self,
        device_id: str,
        working: dict[str, Any],
    ) -> bool:
        """Fill the smart-mode bucket from the HTTP getSmartMode endpoint.

        HTTP is the authoritative source (owner invariant 2026-07-05); the
        ``getSmartMode`` endpoint had API + coordinator methods but was
        never polled, so the smart-mode diagnostic sensors stayed Unknown
        without cloud MQTT. Best-effort and purely additive: a missing system
        id or endpoint error is swallowed so it can never abort the shadow
        cycle, and the fill never blanks an existing bucket.

        Returns:
            True when the smart-mode bucket was updated.
        """
        if not self._shadow_has_home_config_context(working):
            return False
        system_id = self._shadow_system_id(working)
        if system_id is None:
            return False
        try:
            body = await self.api.async_get_smart_mode_info(system_id)
        except (TimeoutError, HomeAssistantError, JackeryError) as err:
            _LOGGER.debug(
                "Jackery smart-mode query failed for %s: %s",
                device_id,
                exception_debug_message(err),
            )
            return False
        if not isinstance(body, dict) or not body:
            return False
        current = working.get(PAYLOAD_SMART_MODE)
        working[PAYLOAD_SMART_MODE] = (
            {**current, **body} if isinstance(current, dict) else dict(body)
        )
        return True

    async def _async_apply_smart_schedule(
        self,
        device_id: str,
        working: dict[str, Any],
    ) -> bool:
        """Fill the AI smart-schedule prediction bucket from HTTP.

        The prediction endpoint is an HTTP app endpoint keyed by systemId. It
        is best-effort and additive, matching smart-mode/TOU handling: failures
        must never abort live property refreshes.

        Returns:
            True when the smart-schedule bucket was updated.
        """
        if not self._shadow_has_home_config_context(working):
            return False
        system_id = self._shadow_system_id(working)
        if system_id is None:
            return False
        try:
            body = await self.api.async_get_smart_schedule_prediction(
                system_id=system_id,
            )
        except (TimeoutError, HomeAssistantError, JackeryError) as err:
            _LOGGER.debug(
                "Jackery smart-schedule query failed for %s: %s",
                device_id,
                exception_debug_message(err),
            )
            return False
        if not isinstance(body, dict) or not body:
            return False
        current = working.get(PAYLOAD_SMART_SCHEDULE)
        working[PAYLOAD_SMART_SCHEDULE] = (
            {**current, **body} if isinstance(current, dict) else dict(body)
        )
        return True

    async def _async_apply_tou_plan(
        self,
        device_id: str,
        working: dict[str, Any],
    ) -> bool:
        """Fill the TOU-schedule bucket from the HTTP queryTouPlan endpoint.

        Same rationale as :meth:`_async_apply_smart_mode`: the ``queryTouPlan``
        endpoint was wired but never polled, so ``tou_plan_tasks`` was Unknown
        off MQTT. Additive and best-effort.

        Returns:
            True when the TOU-schedule bucket was updated.
        """
        if not self._shadow_has_home_config_context(working):
            return False
        numeric_device_id = self._shadow_device_numeric_id(working)
        if not numeric_device_id:
            return False
        try:
            body = await self.api.async_query_tou_plan(device_id=numeric_device_id)
        except (TimeoutError, HomeAssistantError, JackeryError) as err:
            _LOGGER.debug(
                "Jackery TOU-plan query failed for %s: %s",
                device_id,
                exception_debug_message(err),
            )
            return False
        if not isinstance(body, dict) or not body:
            return False
        current = working.get(PAYLOAD_TOU_SCHEDULE)
        working[PAYLOAD_TOU_SCHEDULE] = (
            {**current, **body} if isinstance(current, dict) else dict(body)
        )
        return True

    async def _async_fetch_sub_shadow_body(
        self,
        device_id: str,
        *,
        dev_type: int,
        parent_sn: str,
        sub_device_sn: str,
    ) -> dict[str, Any] | None:
        """Fetch one accessory's sub-shadow body, swallowing per-SN errors."""
        try:
            body = await self.api.async_get_sub_shadow(
                dev_type=str(dev_type),
                device_sn=parent_sn,
                sub_device_sn=sub_device_sn,
            )
        except (TimeoutError, HomeAssistantError, JackeryError) as err:
            _LOGGER.debug(
                "Jackery sub-shadow query failed for %s/%s: %s",
                device_id,
                sub_device_sn,
                exception_debug_message(err),
            )
            return None
        return body if isinstance(body, dict) and body else None

    async def _async_fetch_system_shadow_body(
        self,
        device_id: str,
        *,
        parent_sn: str,
        system_id: str | None,
    ) -> dict[str, Any] | None:
        """Fetch the system-level shadow body whenever a system id exists.

        Historically gated to COMBO accessories, which meant the SystemBody
        config keys carried by this endpoint (workModel, tempUnit,
        standbyPw, ...) never arrived over HTTP for systems without a COMBO
        entry — full-wire rule: poll it unconditionally per device.
        """
        if system_id is None:
            return None
        try:
            body = await self.api.async_get_system_shadow(
                device_sn=parent_sn,
                diy_sn=system_id,
            )
        except (TimeoutError, HomeAssistantError, JackeryError) as err:
            _LOGGER.debug(
                "Jackery system-shadow query failed for %s: %s",
                device_id,
                exception_debug_message(err),
            )
            return None
        return body if isinstance(body, dict) and body else None

    @staticmethod
    def _statistics_http_backfill_dates(
        today: date,
        *,
        window_days: int = _STATISTICS_HTTP_BACKFILL_WINDOW_DAYS,
        include_current_year: bool = False,
    ) -> list[date]:
        """Return completed local days covered by automatic HTTP backfill."""
        return statistics_http_backfill_dates(
            today,
            window_days=window_days,
            include_current_year=include_current_year,
        )

    def _system_id_from_payload(
        self,
        device_id: str,
        payload: dict[str, Any],
    ) -> str | None:
        """Resolve the system id needed by system trend endpoints."""
        device_index = getattr(self, "_device_index", {})
        for source in (
            payload.get(PAYLOAD_SYSTEM),
            payload.get(PAYLOAD_SYSTEM_META),
            device_index.get(device_id),
        ):
            if not isinstance(source, dict):
                continue
            sys_id = source.get(FIELD_ID) or source.get(FIELD_SYSTEM_ID)
            if sys_id is not None:
                return str(sys_id)
        return None

    @staticmethod
    def _historical_day_payload_from_sources(
        section_sources: dict[str, dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Convert section-source dicts into the normal day payload shape."""
        return historical_day_payload_from_sources(section_sources)

    def _historical_day_source_prefixes(
        self,
        device_id: str,
        payload: dict[str, Any],
    ) -> tuple[str, ...]:
        """Return the available historical sources in a stable queue order."""
        prefixes = [
            APP_SECTION_BATTERY_STAT,
            APP_SECTION_HOME_STAT,
            APP_SECTION_CT_STAT,
            APP_SECTION_EPS_STAT,
        ]
        if self._system_id_from_payload(device_id, payload) is not None:
            prefixes.extend((APP_SECTION_PV_STAT, APP_SECTION_HOME_TRENDS))
        return tuple(prefixes)

    async def _async_fetch_historical_day_chart_source(
        self,
        *,
        device_id: str,
        payload: dict[str, Any],
        target_day: date,
        section_prefix: str,
    ) -> tuple[str, dict[str, Any]]:
        """Fetch and gate exactly one historical source/day pair."""
        request_kwargs = app_period_request_kwargs(DATE_TYPE_DAY, today=target_day)
        system_id = self._system_id_from_payload(device_id, payload)
        device_index = getattr(self, "_device_index", {})
        index = device_index.get(device_id) or {}
        ct_device_id = self._smart_meter_accessory_device_id(
            payload,
        ) or self._smart_meter_accessory_device_id(index)

        if section_prefix == APP_SECTION_BATTERY_STAT:
            request = self.api.async_get_device_battery_stat(
                device_id,
                **request_kwargs,
            )
        elif section_prefix == APP_SECTION_HOME_STAT:
            request = self.api.async_get_device_home_stat(
                device_id,
                **request_kwargs,
            )
        elif section_prefix == APP_SECTION_CT_STAT:
            request = self.api.async_get_device_ct_stat(
                ct_device_id or device_id,
                **request_kwargs,
            )
        elif section_prefix == APP_SECTION_EPS_STAT:
            request = self.api.async_get_device_eps_stat(
                device_id,
                **request_kwargs,
            )
        elif section_prefix == APP_SECTION_PV_STAT and system_id is not None:
            request = self.api.async_get_device_pv_stat(
                device_id,
                system_id,
                **request_kwargs,
            )
        elif section_prefix == APP_SECTION_HOME_TRENDS and system_id is not None:
            request = self.api.async_get_home_trends(
                system_id,
                **request_kwargs,
            )
        else:
            return "unsupported", {}

        error_status: str | None = None
        result: object = {}
        try:
            result = await request
        except JackeryAuthError as err:
            # Supplementary historical requests are not the authentication
            # authority. The primary property poll owns reauthentication.
            _LOGGER.debug(
                "Jackery historical %s fetch for %s on %s was auth-rejected; "
                "live polling remains the auth authority: %s",
                section_prefix,
                device_id,
                target_day.isoformat(),
                exception_debug_message(err),
            )
            error_status = "auth_error"
        except (TimeoutError, HomeAssistantError, JackeryError) as err:
            _LOGGER.debug(
                "Jackery historical %s fetch for %s on %s failed: %s",
                section_prefix,
                device_id,
                target_day.isoformat(),
                exception_debug_message(err),
            )
            error_status = "transport_error"
        except Exception as err:  # ruff:ignore[blind-except]
            _LOGGER.debug(
                "Jackery historical %s fetch for %s on %s failed: %s",
                section_prefix,
                device_id,
                target_day.isoformat(),
                exception_debug_message(err),
            )
            error_status = "transport_error"

        section = (
            PAYLOAD_HOME_TRENDS
            if section_prefix == APP_SECTION_HOME_TRENDS
            else f"{section_prefix}_{DATE_TYPE_DAY}"
        )
        if error_status is None and isinstance(result, dict) and result:
            gated_result = gate_payload_section(
                TransportSource.HTTP,
                section,
                result,
            )
            if gated_result:
                return "fetched", gated_result

        return error_status or "empty_ambiguous", {}

    async def _async_fetch_historical_day_chart_sources(
        self,
        *,
        device_id: str,
        payload: dict[str, Any],
        target_day: date,
    ) -> dict[str, dict[str, Any]]:
        """Fetch one completed day's sources sequentially for compatibility."""
        section_sources: dict[str, dict[str, Any]] = {}
        for section_prefix in self._historical_day_source_prefixes(
            device_id,
            payload,
        ):
            status, result = await self._async_fetch_historical_day_chart_source(
                device_id=device_id,
                payload=payload,
                target_day=target_day,
                section_prefix=section_prefix,
            )
            if status == "fetched":
                section_sources[section_prefix] = result
        return section_sources

    async def _async_import_historical_day_chart_statistics_for_device(
        self,
        *,
        device_id: str,
        payload: dict[str, Any],
        section_sources: dict[str, dict[str, Any]],
    ) -> tuple[bool, int]:
        """Import historical day HTTP curves as external hourly statistics."""
        historical_payload = self._historical_day_payload_from_sources(section_sources)
        if not historical_payload:
            return False, 0
        name_prefix = self._app_chart_name_prefix(device_id, payload)
        now = self._local_now()
        imported_rows = 0
        success = True
        handled_points = False
        for section_prefix, stat_key, metric_key, label in APP_CHART_STAT_METRICS:
            points = self._day_chart_points_for_metric(
                historical_payload,
                section_prefix,
                stat_key,
                metric_key,
                bucket_minutes=60,
                now=now,
            )
            if not points:
                continue
            handled_points = True
            ok, bucket_count = await self._async_add_app_chart_statistics(
                device_id=device_id,
                name_prefix=name_prefix,
                metric_key=metric_key,
                label=label,
                bucket=EXTERNAL_STAT_BUCKET_DAY_HOURLY,
                bucket_label=APP_DAY_CHART_BUCKET_LABEL,
                points=points,
            )
            success = success and ok
            imported_rows += bucket_count
        return success and handled_points, imported_rows

    async def _async_http_backfill_recent_day_statistics(  # ruff:ignore[too-many-branches,too-many-locals,too-many-statements]
        self,
        snapshot: dict[str, dict[str, Any]],
        *,
        force: bool = False,
        window_days: int = _STATISTICS_HTTP_BACKFILL_WINDOW_DAYS,
        include_current_year: bool = False,
        request_budget: int = _STATISTICS_HTTP_BACKFILL_REQUEST_BUDGET,
    ) -> dict[str, Any]:
        """Advance the persistent, bounded HTTP day-backfill queue."""
        diag = self._statistics_import_diagnostics
        now_monotonic = time.monotonic()
        since_last = now_monotonic - self._last_statistics_http_backfill_monotonic
        if not force and since_last < _STATISTICS_HTTP_BACKFILL_INTERVAL_SEC:
            diag["last_http_backfill_status"] = "throttled"
            diag["next_http_backfill_allowed_in_seconds"] = round(
                _STATISTICS_HTTP_BACKFILL_INTERVAL_SEC - since_last,
                3,
            )
            return {
                "external_rows": 0,
                "entity_imported_rows": 0,
                "entity_failed_rows": 0,
                "source_days": 0,
                "successful_devices": 0,
                "requests": 0,
                "terminal_transitions": 0,
                "pending_sources": 0,
            }

        await self._async_ensure_statistics_backfill_state_loaded()
        today = self._local_today()
        now_epoch = time.time()
        target_days = self._statistics_http_backfill_dates(
            today,
            window_days=window_days,
            include_current_year=include_current_year,
        )
        target_day_keys = {target_day.isoformat() for target_day in target_days}
        external_rows = 0
        entity_imported_rows = 0
        entity_failed_rows = 0
        source_days = 0
        successful_devices: set[str] = set()
        terminal_transitions = 0
        requests = 0
        state_changed = False
        candidates: list[
            tuple[
                int,
                str,
                date,
                int,
                str,
                str,
                dict[str, Any],
                dict[str, Any],
            ]
        ] = []

        for device_id in sorted(snapshot, key=str):
            payload = snapshot[device_id]
            device_state = self._statistics_backfill_device_state(device_id)
            queue_state = device_state.setdefault("http_day_backfill", {})
            if not isinstance(queue_state, dict):
                queue_state = {}
                device_state["http_day_backfill"] = queue_state
            sources_state = queue_state.setdefault("sources", {})
            if not isinstance(sources_state, dict):
                sources_state = {}
                queue_state["sources"] = sources_state

            for section_prefix in self._historical_day_source_prefixes(
                device_id,
                payload,
            ):
                source_state = sources_state.setdefault(section_prefix, {})
                if not isinstance(source_state, dict):
                    source_state = {}
                    sources_state[section_prefix] = source_state
                days_state = source_state.setdefault("days", {})
                if not isinstance(days_state, dict):
                    days_state = {}
                    source_state["days"] = days_state

                for stale_day in set(days_state) - target_day_keys:
                    days_state.pop(stale_day, None)
                    state_changed = True

                for target_day in target_days:
                    target_key = target_day.isoformat()
                    day_state = days_state.setdefault(target_key, {})
                    if not isinstance(day_state, dict):
                        day_state = {}
                        days_state[target_key] = day_state
                    state_status = day_state.get("status")
                    if state_status in {"imported", "unavailable"}:
                        continue
                    retry_after = safe_float(
                        day_state.get(_STATISTICS_HTTP_RETRY_AFTER_EPOCH),
                    )
                    if (
                        state_status == "deferred"
                        and retry_after is not None
                        and retry_after > now_epoch
                    ):
                        continue
                    attempts = day_state.get("attempts", 0)
                    if not isinstance(attempts, int) or attempts < 0:
                        attempts = 0
                    last_attempt = day_state.get("last_attempt_at")
                    candidates.append((
                        1 if last_attempt is not None else 0,
                        str(last_attempt) if last_attempt is not None else "",
                        target_day,
                        attempts,
                        str(device_id),
                        section_prefix,
                        payload,
                        day_state,
                    ))

        candidates.sort(key=operator.itemgetter(slice(6)))
        for (
            _attempted,
            _last_attempt,
            target_day,
            _attempts,
            device_id,
            section_prefix,
            payload,
            day_state,
        ) in candidates[: max(0, request_budget)]:
            requests += 1
            status, source = await self._async_fetch_historical_day_chart_source(
                device_id=device_id,
                payload=payload,
                target_day=target_day,
                section_prefix=section_prefix,
            )
            attempts = day_state.get("attempts", 0)
            day_state["attempts"] = (
                attempts + 1 if isinstance(attempts, int) and attempts >= 0 else 1
            )
            day_state["last_attempt_at"] = utc_now().isoformat()
            day_state["status"] = status
            day_state.pop(_STATISTICS_HTTP_RETRY_AFTER_EPOCH, None)
            state_changed = True

            if status != "fetched":
                attempts_now = day_state["attempts"]
                if (
                    status == "empty_ambiguous"
                    and attempts_now >= _STATISTICS_HTTP_EMPTY_MAX_ATTEMPTS
                ):
                    day_state["status"] = "unavailable"
                    day_state["completed_at"] = utc_now().isoformat()
                    terminal_transitions += 1
                elif (
                    status in {"auth_error", "transport_error"}
                    and attempts_now >= _STATISTICS_HTTP_TRANSPORT_ERROR_MAX_ATTEMPTS
                ):
                    # A temporary internet/auth-service failure must end the
                    # rapid bootstrap pass without becoming permanent data
                    # loss. The normal five-minute statistics scheduler will
                    # retry this exact source/day after the cooldown.
                    day_state["status"] = "deferred"
                    day_state[_STATISTICS_HTTP_RETRY_AFTER_EPOCH] = (
                        time.time() + _STATISTICS_HTTP_TRANSIENT_RETRY_SEC
                    )
                    terminal_transitions += 1
                await asyncio.sleep(0)
                continue

            source_days += 1
            (
                ok,
                imported,
            ) = await self._async_import_historical_day_chart_statistics_for_device(
                device_id=device_id,
                payload=payload,
                section_sources={section_prefix: source},
            )
            external_rows += imported
            if ok:
                day_state["status"] = "imported"
                day_state["imported_rows"] = imported
                day_state["completed_at"] = utc_now().isoformat()
                successful_devices.add(device_id)
                terminal_transitions += 1
            else:
                # A non-empty HTTP envelope can still contain no usable series
                # for this metric/day. That is missing source data, not a
                # Recorder write failure. Mark it terminal for this source/day
                # so it cannot starve independent periods in an endless loop.
                day_state["status"] = "unavailable"
                day_state["completed_at"] = utc_now().isoformat()
                day_state.pop("imported_rows", None)
                terminal_transitions += 1
            # Entity-backed (``sensor.xxx``) day import remains removed: it
            # collides with HA's recorder on the same statistic_id.
            await asyncio.sleep(0)

        pending_sources = sum(
            1
            for candidate in candidates
            if candidate[-1].get("status")
            not in {"deferred", "imported", "unavailable"}
        )

        if state_changed:
            await self._async_save_statistics_backfill_state()

        backfill_had_source = source_days > 0 and bool(successful_devices)
        retry_after_sec = (
            _STATISTICS_HTTP_BACKFILL_INTERVAL_SEC
            if backfill_had_source
            else _STATISTICS_HTTP_BACKFILL_RETRY_SEC
        )
        self._last_statistics_http_backfill_monotonic = (
            now_monotonic - _STATISTICS_HTTP_BACKFILL_INTERVAL_SEC + retry_after_sec
        )
        result: dict[str, Any] = {
            "external_rows": external_rows,
            "entity_imported_rows": entity_imported_rows,
            "entity_failed_rows": entity_failed_rows,
            "source_days": source_days,
            "successful_devices": len(successful_devices),
            "requests": requests,
            "terminal_transitions": terminal_transitions,
            "pending_sources": pending_sources,
        }
        diag.update({
            "last_http_backfill_checked_at": utc_now().isoformat(),
            "last_http_backfill_forced": force,
            "last_http_backfill_window_days": window_days,
            "last_http_backfill_include_current_year": include_current_year,
            "last_http_backfill_day_count": len(target_days),
            "last_http_backfill_oldest_day": (
                target_days[0].isoformat() if target_days else None
            ),
            "last_http_backfill_newest_day": (
                target_days[-1].isoformat() if target_days else None
            ),
            "last_http_backfill_status": (
                "completed"
                if pending_sources == 0
                else ("progress" if terminal_transitions else "pending")
            ),
            "last_http_backfill_external_rows": external_rows,
            "last_http_backfill_entity_imported_rows": entity_imported_rows,
            "last_http_backfill_entity_failed_rows": entity_failed_rows,
            "last_http_backfill_source_days": source_days,
            "last_http_backfill_successful_device_count": len(successful_devices),
            "last_http_backfill_requests": requests,
            "last_http_backfill_terminal_transitions": terminal_transitions,
            "last_http_backfill_pending_sources": pending_sources,
            "next_http_backfill_allowed_in_seconds": retry_after_sec,
        })
        return result

    async def _async_http_backfill_period_statistics(  # ruff:ignore[too-many-branches, too-many-locals, too-many-statements]
        self,
        snapshot: dict[str, dict[str, Any]],
        *,
        request_budget: int = _STATISTICS_HTTP_PERIOD_BACKFILL_REQUEST_BUDGET,
    ) -> dict[str, int]:
        """Advance persistent week/month/year HTTP history independently.

        Each source/period bucket has its own terminal state. Month and year
        lead the queue so an empty weekly endpoint can never prevent the Energy
        Dashboard's daily/monthly history from becoming durable.
        """
        await self._async_ensure_statistics_backfill_state_loaded()
        today = self._local_today()
        now_epoch = time.time()
        from_date = date(today.year, 1, 1)
        enabled = self._enabled_app_chart_date_types()
        period_plan: tuple[tuple[str, list[date]], ...] = tuple(
            item
            for item in (
                (
                    DATE_TYPE_MONTH,
                    self._iter_calendar_months(from_date, today),
                ),
                (
                    DATE_TYPE_YEAR,
                    [
                        date(year, 1, 1)
                        for year in self._iter_calendar_years(from_date, today)
                    ],
                ),
                (
                    DATE_TYPE_WEEK,
                    self._iter_calendar_weeks(from_date, today),
                ),
            )
            if item[0] in enabled
        )
        period_priority = {
            DATE_TYPE_MONTH: 0,
            DATE_TYPE_YEAR: 1,
            DATE_TYPE_WEEK: 2,
        }
        prefixes = tuple(dict.fromkeys(metric[0] for metric in APP_CHART_STAT_METRICS))
        candidates: list[
            tuple[
                int,
                int,
                str,
                date,
                str,
                str,
                str,
                dict[str, Any],
                dict[str, Any],
            ]
        ] = []

        for device_id in sorted(snapshot, key=str):
            payload = snapshot[device_id]
            device_state = self._statistics_backfill_device_state(device_id)
            queue_state = device_state.setdefault("http_period_backfill", {})
            if not isinstance(queue_state, dict):
                queue_state = {}
                device_state["http_period_backfill"] = queue_state
            sources_state = queue_state.setdefault("sources", {})
            if not isinstance(sources_state, dict):
                sources_state = {}
                queue_state["sources"] = sources_state

            for section_prefix in prefixes:
                source_state = sources_state.setdefault(section_prefix, {})
                if not isinstance(source_state, dict):
                    source_state = {}
                    sources_state[section_prefix] = source_state
                for date_type, period_starts in period_plan:
                    type_state = source_state.setdefault(date_type, {})
                    if not isinstance(type_state, dict):
                        type_state = {}
                        source_state[date_type] = type_state
                    for period_start in period_starts:
                        period_key = period_start.isoformat()
                        bucket_state = type_state.setdefault(period_key, {})
                        if not isinstance(bucket_state, dict):
                            bucket_state = {}
                            type_state[period_key] = bucket_state
                        if bucket_state.get("status") in {
                            "imported",
                            "unavailable",
                        }:
                            continue
                        retry_after = safe_float(
                            bucket_state.get(_STATISTICS_HTTP_RETRY_AFTER_EPOCH),
                        )
                        if (
                            bucket_state.get("status") == "deferred"
                            and retry_after is not None
                            and retry_after > now_epoch
                        ):
                            continue
                        last_attempt = bucket_state.get("last_attempt_at")
                        candidates.append((
                            period_priority[date_type],
                            1 if last_attempt is not None else 0,
                            str(last_attempt or ""),
                            period_start,
                            str(device_id),
                            section_prefix,
                            date_type,
                            payload,
                            bucket_state,
                        ))

        candidates.sort(key=operator.itemgetter(slice(7)))
        requests = 0
        imported_sources = 0
        terminal_transitions = 0
        state_changed = False

        for (
            _priority,
            _attempted,
            _last_attempt,
            period_start,
            device_id,
            section_prefix,
            date_type,
            payload,
            bucket_state,
        ) in candidates[: max(0, request_budget)]:
            requests += 1
            status = "empty_ambiguous"
            fetched_source: dict[str, Any] = {}
            try:
                fetched = await self._async_fetch_historical_app_chart_source(
                    device_id=device_id,
                    system_id=self._system_id_from_payload(device_id, payload),
                    ct_device_id=(
                        self._smart_meter_accessory_device_id(payload)
                        or self._smart_meter_accessory_device_id(
                            getattr(self, "_device_index", {}).get(device_id) or {}
                        )
                    ),
                    section_prefix=section_prefix,
                    date_type=date_type,
                    period_start=period_start,
                )
            except JackeryAuthError as err:
                status = "auth_error"
                _LOGGER.debug(
                    "Jackery period backfill auth-rejected for %s %s %s: %s",
                    device_id,
                    section_prefix,
                    period_start.isoformat(),
                    exception_debug_message(err),
                )
            except (TimeoutError, HomeAssistantError, JackeryError) as err:
                status = "transport_error"
                _LOGGER.debug(
                    "Jackery period backfill failed for %s %s %s: %s",
                    device_id,
                    section_prefix,
                    period_start.isoformat(),
                    exception_debug_message(err),
                )
            except Exception as err:  # ruff:ignore[blind-except]
                status = "transport_error"
                _LOGGER.debug(
                    "Jackery period backfill failed for %s %s %s: %s",
                    device_id,
                    section_prefix,
                    period_start.isoformat(),
                    exception_debug_message(err),
                )
            else:
                if isinstance(fetched, dict) and fetched:
                    section = f"{section_prefix}_{date_type}"
                    fetched_source = gate_payload_section(
                        TransportSource.HTTP,
                        section,
                        fetched,
                    )
                    status = "fetched" if fetched_source else "empty_ambiguous"

            attempts = bucket_state.get("attempts", 0)
            attempts_now = (
                attempts + 1 if isinstance(attempts, int) and attempts >= 0 else 1
            )
            bucket_state.update({
                "attempts": attempts_now,
                "last_attempt_at": utc_now().isoformat(),
                "status": status,
            })
            bucket_state.pop(_STATISTICS_HTTP_RETRY_AFTER_EPOCH, None)
            state_changed = True

            if status == "fetched":
                collected = {
                    (section_prefix, date_type, period_start): fetched_source,
                }
                withheld = self._repair_containment_violations(
                    collected=collected,
                    payload=payload,
                    to_date=today,
                )
                period_meta = self._app_chart_period_meta(date_type)
                repaired, failed = await self._import_collected_repair_buckets(
                    device_id=device_id,
                    name_prefix=self._app_chart_name_prefix(device_id, payload),
                    collected=collected,
                    period_meta_by_type=(
                        {date_type: period_meta} if period_meta is not None else {}
                    ),
                    withheld=withheld,
                    to_date=today,
                )
                if repaired > 0 and failed == 0:
                    bucket_state.update({
                        "status": "imported",
                        "imported_rows": repaired,
                        "completed_at": utc_now().isoformat(),
                    })
                    imported_sources += 1
                    terminal_transitions += 1
                elif failed:
                    bucket_state["status"] = "recorder_error"
                else:
                    bucket_state["status"] = "empty_ambiguous"

            current_status = bucket_state.get("status")
            if (
                current_status == "empty_ambiguous"
                and attempts_now >= _STATISTICS_HTTP_EMPTY_MAX_ATTEMPTS
            ):
                bucket_state.update({
                    "status": "unavailable",
                    "completed_at": utc_now().isoformat(),
                })
                terminal_transitions += 1
            elif (
                current_status in {"auth_error", "transport_error", "recorder_error"}
                and attempts_now >= _STATISTICS_HTTP_TRANSPORT_ERROR_MAX_ATTEMPTS
            ):
                bucket_state.update({
                    "status": "deferred",
                    _STATISTICS_HTTP_RETRY_AFTER_EPOCH: (
                        time.time() + _STATISTICS_HTTP_TRANSIENT_RETRY_SEC
                    ),
                })
                terminal_transitions += 1
            await asyncio.sleep(0)

        pending_sources = sum(
            1
            for candidate in candidates
            if candidate[-1].get("status")
            not in {"deferred", "imported", "unavailable"}
        )
        if state_changed:
            await self._async_save_statistics_backfill_state()
        self._statistics_import_diagnostics.update({
            "last_period_backfill_requests": requests,
            "last_period_backfill_imported_sources": imported_sources,
            "last_period_backfill_terminal_transitions": terminal_transitions,
            "last_period_backfill_pending_sources": pending_sources,
        })
        return {
            "requests": requests,
            "imported_sources": imported_sources,
            "terminal_transitions": terminal_transitions,
            "pending_sources": pending_sources,
        }

    @property
    def polling_diagnostics(self) -> dict[str, Any]:
        """Return the latest HTTP polling/cache diagnostics."""  # ruff:ignore[property-docstring-starts-with-verb]
        return dict(self._polling_diagnostics)

    @property
    def statistics_import_diagnostics(self) -> dict[str, Any]:
        """Return the latest Recorder import diagnostics."""  # ruff:ignore[property-docstring-starts-with-verb]
        return dict(self._statistics_import_diagnostics)

    def _metric_source_candidates(
        self,
        section_prefix: str,
        stat_key: str,
        metric_key: str,
    ) -> list[tuple[str, str]]:
        """Return ordered source candidates for one metric."""
        candidates: list[tuple[str, str]] = [(section_prefix, stat_key)]
        candidates.extend(_METRIC_SOURCE_FALLBACKS.get(metric_key, ()))
        if metric_key == "home_energy" and self._derived_home_energy_fallback_enabled():
            candidates.append((APP_SECTION_HOME_STAT, APP_STAT_TOTAL_OUT_GRID_ENERGY))
        deduped: list[tuple[str, str]] = []
        seen: set[tuple[str, str]] = set()
        for candidate in candidates:
            if candidate in seen:
                continue
            seen.add(candidate)
            deduped.append(candidate)
        return deduped

    def _enabled_app_chart_date_types(self) -> set[str]:
        """Return the period date types the user has not opted out of.

        DAY-hourly external statistics carry the Energy-Dashboard's
        hour-by-hour breakdown and have no HA-vs-Cloud conflict — they
        stay always on. WEEK/MONTH/YEAR are opt-out via config-flow
        toggles (defaults: enabled). A disabled period skips both the
        current-snapshot import and the historical repair fetch for that
        date type, sparing cloud round-trips and Recorder writes.
        """
        enabled: set[str] = {DATE_TYPE_DAY}
        if config_entry_bool_option(
            self.entry,
            CONF_ENABLE_WEEK_STATISTICS,
            DEFAULT_ENABLE_WEEK_STATISTICS,
        ):
            enabled.add(DATE_TYPE_WEEK)
        if config_entry_bool_option(
            self.entry,
            CONF_ENABLE_MONTH_STATISTICS,
            DEFAULT_ENABLE_MONTH_STATISTICS,
        ):
            enabled.add(DATE_TYPE_MONTH)
        if config_entry_bool_option(
            self.entry,
            CONF_ENABLE_YEAR_STATISTICS,
            DEFAULT_ENABLE_YEAR_STATISTICS,
        ):
            enabled.add(DATE_TYPE_YEAR)
        return enabled

    def _derived_home_energy_fallback_enabled(self) -> bool:
        """Return whether derived home-energy fallback may be used."""
        return config_entry_bool_option(
            self.entry,
            CONF_ENABLE_DERIVED_HOME_ENERGY_FALLBACK,
            DEFAULT_ENABLE_DERIVED_HOME_ENERGY_FALLBACK,
        )

    async def _async_import_current_app_chart_statistics_job(
        self,
        snapshot: dict[str, dict[str, Any]],
    ) -> set[str]:
        """Advance independent period/day history, then import current buckets.

        Returns:
            Device ids whose external statistics imported successfully, so
            the repair wrapper can maintain the per-device backfill state.
        """
        if not snapshot:
            return set()

        snapshot = self._gate_snapshot_period_hierarchy(
            snapshot,
            today=self._local_today(),
        )
        startup_sync = self._statistics_startup_sync_pending
        period_backfill_result = await self._async_http_backfill_period_statistics(
            snapshot,
        )
        period_pending = period_backfill_result.get("pending_sources", 0)
        backfill_result = await self._async_http_backfill_recent_day_statistics(
            snapshot,
            force=startup_sync,
            window_days=(
                _STATISTICS_HTTP_STARTUP_BACKFILL_MIN_DAYS
                if startup_sync
                else _STATISTICS_HTTP_BACKFILL_WINDOW_DAYS
            ),
            include_current_year=startup_sync,
            request_budget=(
                1
                if startup_sync and period_pending > 0
                else _STATISTICS_HTTP_BACKFILL_REQUEST_BUDGET
            ),
        )
        day_pending = backfill_result.get("pending_sources", 0)
        if startup_sync and period_pending == 0 and day_pending == 0:
            self._statistics_startup_sync_pending = False
        successful_devices = await self._async_import_day_chart_statistics(snapshot)
        period_successful_devices = await self._async_import_app_chart_statistics(
            snapshot,
        )
        successful_devices.update(period_successful_devices)
        # Entity-backed (``sensor.xxx``) statistics import removed here too: it
        # collided with HA's own sensor recorder on the same statistic_id. The
        # external ``jackery_solarvault:`` statistics above are authoritative;
        # the entity-row diagnostics counters are retained at 0 for continuity.
        self._statistics_import_diagnostics.update({
            "last_import_device_count": len(snapshot),
            "last_external_successful_device_count": len(successful_devices),
            "last_current_entity_imported_rows": 0,
            "last_current_entity_failed_rows": 0,
            "last_current_period_replace_existing_hours": startup_sync,
            "startup_sync_pending": self._statistics_startup_sync_pending,
        })
        return successful_devices

    # ------------------------------------------------------------------
    # Smart Mode / AI Schedule
    # ------------------------------------------------------------------

    async def async_check_smart_mode(
        self,
        device_id: str,
        system_id: str,
    ) -> dict[str, Any]:
        """Check if smart mode is configured for a device/system.

        Calls ``/v1/device/smartMode/checkIfSet`` (POST).
        Returns ``SmartConditionData``.
        """
        self._require_home_config_context(device_id, "check smart mode")
        return await self.api.async_check_smart_mode_set(
            device_id=device_id,
            system_id=system_id,
        )

    async def async_get_smart_mode_info(self, system_id: str) -> dict[str, Any]:
        """Get smart mode configuration for a system.

        Calls ``/v1/device/smartMode/getSmartMode`` (GET).
        Returns ``SmartModeInfoData``.
        """
        return await self.api.async_get_smart_mode_info(system_id)

    async def async_get_smart_schedule_prediction(
        self,
        device_id: str,
    ) -> dict[str, Any]:
        """Get AI smart-schedule prediction for a configured device.

        Calls ``/v1/device/stat/getSmartSchedulePrediction`` (GET) using the
        systemId resolved from the coordinator payload.
        """
        payload = (self.data or {}).get(device_id) or {}
        if not self._shadow_has_home_config_context(payload):
            msg = f"smart schedule is not a Home/System feature for {device_id}"
            raise LookupError(msg)
        system_id = self._shadow_system_id(payload)
        if system_id is None:
            msg = f"missing systemId for {device_id}"
            raise LookupError(msg)
        return await self.api.async_get_smart_schedule_prediction(
            system_id=system_id,
        )

    async def async_get_aiems_energy_prediction(
        self,
        device_id: str,
    ) -> dict[str, Any]:
        """Get the AI-EMS energy prediction for a configured device.

        Calls ``/v1/api/aiems/report/energy/prediction`` (GET, app 2.4.0)
        using the systemId resolved from the coordinator payload.
        """
        payload = (self.data or {}).get(device_id) or {}
        if not self._shadow_has_home_config_context(payload):
            msg = f"AI-EMS prediction is not a Home/System feature for {device_id}"
            raise LookupError(msg)
        system_id = self._shadow_system_id(payload)
        if system_id is None:
            msg = f"missing systemId for {device_id}"
            raise LookupError(msg)
        return await self.api.async_get_aiems_energy_prediction(
            system_id=system_id,
        )

    async def async_start_smart_mode(self, system_id: str) -> None:
        """Start or enable smart mode for a system.

        Calls ``/v1/device/smartMode/startSmartMode`` (POST).
        """
        await self.api.async_start_smart_mode(system_id)

    # ------------------------------------------------------------------
    # TOU (Time-of-Use) Plan
    # ------------------------------------------------------------------

    async def async_query_tou_plan(self, device_id: str) -> dict[str, Any]:
        """Query the current TOU schedule plan for a device.

        Calls ``/v1/device/tou/queryTouPlan`` (GET).
        """
        return await self.api.async_query_tou_plan(device_id=device_id)

    async def async_save_tou_plan(
        self,
        device_id: str,
        tasks: list[dict[str, Any]],
    ) -> None:
        """Save a TOU schedule plan for a device.

        Calls ``/v1/device/tou/saveTouPlan`` (POST).
        """
        await self.api.async_save_tou_plan(device_id=device_id, tasks=tasks)
        await self.async_request_refresh()

    async def async_list_currencies(self) -> list[dict[str, Any]]:
        """List currencies supported by the Jackery HTTP API."""
        return await self.api.async_get_currency_list()

    async def async_get_device_currency(self, device_id: str) -> dict[str, Any]:
        """Get the currency payload for a configured device."""
        self._require_home_config_context(device_id, "get device currency")
        return await self.api.async_get_device_currency(device_id)

    async def async_bind_currency(self, device_id: str, currency: str) -> None:
        """Bind a currency to a configured device and refresh."""
        self._require_home_config_context(device_id, "bind device currency")
        system_id = self._resolve_system_id(device_id)
        if not system_id:
            msg = f"Cannot bind currency for {device_id}: missing systemId"
            raise UpdateFailed(msg)
        await self.api.async_bind_currency(
            currency=currency,
            device_id=device_id,
            system_id=system_id,
        )
        await self.async_request_refresh()

    async def async_list_accessories(self, device_id: str) -> list[dict[str, Any]]:
        """List smart accessories for a configured parent device."""
        self._require_home_config_context(device_id, "list accessories")
        return await self.api.async_get_accessories_list(device_id)

    async def async_check_accessories_exist(
        self,
        *,
        devices: str,
        context_device_id: str | None = None,
    ) -> dict[str, Any]:
        """Check whether account accessories exist for a device list payload."""
        if context_device_id is not None:
            self._require_home_config_context(context_device_id, "check accessories")
        return await self.api.async_check_accessories_exist(devices=devices)

    async def async_check_jackery_accessories_exist(
        self,
        *,
        device_sn_infos: str,
        context_device_id: str | None = None,
    ) -> dict[str, Any]:
        """Check Jackery accessory existence for serialized device SN info."""
        if context_device_id is not None:
            self._require_home_config_context(
                context_device_id, "check Jackery accessories"
            )
        return await self.api.async_check_jackery_accessories_exist(
            device_sn_infos=device_sn_infos,
        )

    async def async_set_accessory_name(
        self,
        *,
        accessory_id: str,
        nickname: str,
        context_device_id: str | None = None,
    ) -> None:
        """Rename a smart accessory through the Jackery HTTP API and refresh."""
        if context_device_id is not None:
            self._require_home_config_context(context_device_id, "rename accessory")
        await self.api.async_set_accessories_name(
            device_name=nickname,
            id=accessory_id,
        )
        await self.async_request_refresh()

    async def async_get_dynamic_price_login_url(
        self,
        device_id: str,
        platform_company_id: int,
    ) -> dict[str, Any]:
        """Return a dynamic-price provider login URL for a configured device."""
        self._require_home_config_context(device_id, "get dynamic price login URL")
        system_id = self._resolve_system_id(device_id)
        if not system_id:
            msg = (
                f"Cannot get dynamic price login URL for {device_id}: missing systemId"
            )
            raise UpdateFailed(msg)
        return await self.api.async_get_dynamic_price_login_url(
            platform_company_id=platform_company_id,
            system_id=system_id,
        )

    async def async_list_dynamic_price_contracts(
        self,
        *,
        customer_number: str,
        platform_company_id: int,
    ) -> list[dict[str, Any]]:
        """List dynamic-price contracts for a customer and platform."""
        return await self.api.async_get_contract_list(
            customer_number=customer_number,
            platform_company_id=platform_company_id,
        )

    async def async_save_dynamic_price_contract_auth(
        self,
        device_id: str,
        *,
        contract_id: str,
        custom_id: str,
        platform_company_id: int,
    ) -> dict[str, Any]:
        """Save dynamic-price contract authorization and refresh."""
        self._require_home_config_context(device_id, "save dynamic price contract auth")
        system_id = self._resolve_system_id(device_id)
        if not system_id:
            msg = (
                f"Cannot save dynamic price contract auth for {device_id}: "
                "missing systemId"
            )
            raise UpdateFailed(msg)
        result = await self.api.async_save_contract_auth(
            contract_id=contract_id,
            custom_id=custom_id,
            platform_company_id=platform_company_id,
            system_id=system_id,
        )
        await self.async_request_refresh()
        return result

    async def async_cancel_dynamic_price_contract_auth(
        self,
        device_id: str,
        platform_company_id: int,
    ) -> dict[str, Any]:
        """Cancel dynamic-price contract authorization and refresh."""
        self._require_home_config_context(
            device_id, "cancel dynamic price contract auth"
        )
        system_id = self._resolve_system_id(device_id)
        if not system_id:
            msg = (
                f"Cannot cancel dynamic price contract auth for {device_id}: "
                "missing systemId"
            )
            raise UpdateFailed(msg)
        result = await self.api.async_cancel_contract_auth(
            platform_company_id=platform_company_id,
            system_id=system_id,
        )
        await self.async_request_refresh()
        return result

    async def async_save_dynamic_price_location_id(
        self,
        *,
        connect_token: str,
    ) -> dict[str, Any]:
        """Save a Flatpeak dynamic-price location token and refresh."""
        result = await self.api.async_save_location_id(connect_token=connect_token)
        await self.async_request_refresh()
        return result

    async def async_query_socket_stat(
        self,
        target_device_id: str,
        *,
        date_type: str,
        begin_date: str,
        end_date: str,
        context_device_id: str | None = None,
    ) -> dict[str, Any]:
        """Query app socket chart statistics for an accessory device id."""
        if context_device_id is not None:
            self._require_home_config_context(
                context_device_id, "query socket statistics"
            )
        return await self.api.async_get_device_socket_stat(
            target_device_id,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_list_country_zones(
        self,
    ) -> list[dict[str, Any]]:
        """List country/zone entries used by DIY device setup."""
        return await self.api.async_get_zone_list()

    async def async_list_grid_standards(
        self,
        *,
        country: str,
    ) -> list[dict[str, Any]]:
        """List grid-connection standards for a country code."""
        return await self.api.async_get_gcs_list(country=country)

    async def async_check_system_bound(
        self,
        *,
        bind_key: str,
        device_sn: str,
        guid: str,
    ) -> dict[str, Any]:
        """Check whether a system is already bound for bind-key/SN/GUID."""
        return await self.api.async_check_system_bound(
            bind_key=bind_key,
            device_sn=device_sn,
            guid=guid,
        )

    async def async_get_device_bluetooth_key_payload(
        self,
        *,
        device_sn: str,
        guid: str,
    ) -> dict[str, Any]:
        """Fetch the HTTP-owned Bluetooth-key payload for pairing/bootstrap code.

        The returned payload is sensitive and intentionally not exposed as a HA
        service response. Layer-5 transports only consume cached/known keys; they
        must not call this themselves.
        """
        return await self.api.async_get_device_bluetooth_key(
            device_sn=device_sn,
            guid=guid,
        )

    async def async_save_device_max_power(
        self,
        device_id: str,
        max_power: int,
    ) -> None:
        """Save the documented max-power record for a device and refresh."""
        self._require_home_config_context(device_id, "save device max power")
        accepted = await self.api.async_set_max_power(device_id, max_power)
        if not accepted:
            msg = f"Cannot save max power for {device_id}: API rejected value"
            raise HomeAssistantError(msg)
        await self.async_request_refresh()

    async def async_get_alarm_detail(
        self,
        alarm_key: str,
        *,
        context_device_id: str | None = None,
    ) -> dict[str, Any]:
        """Return detailed alarm information for an alarm key."""
        if context_device_id is not None:
            self._require_home_config_context(context_device_id, "get alarm detail")
        return await self.api.async_get_alarm_detail(alarm_key=alarm_key)

    async def async_list_notifications(
        self,
        *,
        current_time: int,
        device_sn: str,
        page_no: int,
        page_size: int,
    ) -> list[dict[str, Any]]:
        """List Jackery push notifications for the selected account."""
        return await self.api.async_get_notify_list(
            current_time=current_time,
            device_sn=device_sn,
            page_no=page_no,
            page_size=page_size,
        )

    async def async_get_unread_count(self) -> dict[str, Any]:
        """Return unread notification counts for the selected account."""
        return await self.api.async_get_unread_count()

    async def async_get_push_config(self) -> dict[str, Any]:
        """Return push-notification configuration for the selected account."""
        return await self.api.async_get_push_config()

    async def async_set_push_config(self, set_value: int) -> dict[str, Any]:
        """Set push-notification configuration for the selected account."""
        return await self.api.async_set_push_config(set=set_value)

    async def async_sync_alerts(
        self,
        *,
        content: str,
        id: str,
        context_device_id: str | None = None,
    ) -> dict[str, Any]:
        """Sync faults and alarms through the Jackery HTTP API."""
        if context_device_id is not None:
            self._require_home_config_context(context_device_id, "sync alerts")
        result = await self.api.async_sync_alerts(content=content, id=id)
        await self.async_request_refresh()
        return result

    async def async_get_offline_statistics(
        self,
        *,
        context_device_id: str | None = None,
    ) -> dict[str, Any]:
        """Return offline-statistics payload for the selected account."""
        if context_device_id is not None:
            self._require_home_config_context(
                context_device_id, "get offline statistics"
            )
        return await self.api.async_get_offline_statistics()

    async def async_query_charge_report(
        self,
        *,
        device_sn: str,
        page_index: int,
    ) -> dict[str, Any]:
        """Return charge-report history for a device serial number."""
        return await self.api.async_get_charge_report(
            device_sn=device_sn,
            page_index=page_index,
        )

    async def async_query_cutoff_stat(
        self,
        *,
        device_sn: str,
        begin_date: str,
        end_date: str,
    ) -> dict[str, Any]:
        """Return cutoff/power-outage statistics for a device serial number."""
        return await self.api.async_get_cutoff_stat(
            device_sn=device_sn,
            begin_date=begin_date,
            end_date=end_date,
        )

    async def async_query_soc_stat(self, device_id: str) -> dict[str, Any]:
        """Return SOC statistics for a configured device id."""
        return await self.api.async_get_soc_stat(device_id=device_id)

    async def async_query_carbon_stat(self, device_sn: str) -> dict[str, Any]:
        """Return carbon-offset statistics for a device serial number."""
        return await self.api.async_get_carbon_stat(device_sn=device_sn)

    async def async_query_profit_stat(self, device_id: str) -> dict[str, Any]:
        """Return profit/revenue statistics for a configured device id."""
        return await self.api.async_get_profit_stat(device_id=device_id)

    async def async_query_box_stat(
        self,
        *,
        device_sn: str,
        date_type: str,
        begin_date: str,
        end_date: str,
        key: str,
    ) -> dict[str, Any]:
        """Return generic box electricity statistics for a device serial number."""
        return await self.api.async_get_box_stat(
            device_sn=device_sn,
            date_type=date_type,
            begin_date=begin_date,
            end_date=end_date,
            key=key,
        )

    async def async_check_app_version(
        self,
        *,
        type: str,
        version_name: str,
    ) -> dict[str, Any]:
        """Return Jackery app-version metadata for the selected account."""
        return await self.api.async_check_app_version(
            type=type,
            version_name=version_name,
        )

    async def async_list_banners(self) -> list[dict[str, Any]]:
        """Return Jackery app banner entries for the selected account."""
        return await self.api.async_get_banner_list()

    async def async_submit_feedback(
        self,
        *,
        contact_info: str,
        content: str,
        device_sn: str,
    ) -> dict[str, Any]:
        """Submit user feedback through the Jackery HTTP API."""
        return await self.api.async_submit_feedback(
            contact_info=contact_info,
            content=content,
            device_sn=device_sn,
        )

    async def async_list_faqs(self) -> list[dict[str, Any]]:
        """Return Jackery FAQ entries for the selected account."""
        return await self.api.async_get_faq_list()

    async def async_list_faq_answers(self) -> list[dict[str, Any]]:
        """Return Jackery FAQ answer entries for the selected account."""
        return await self.api.async_get_faq_answer()

    async def async_check_privacy_update(self) -> dict[str, Any]:
        """Return whether updated privacy consent is required."""
        return await self.api.async_check_privacy_update()

    async def async_agree_privacy_consent(
        self,
        *,
        pending_agree_version_ids: list[int],
    ) -> dict[str, Any]:
        """Record privacy consent agreement through the Jackery HTTP API."""
        return await self.api.async_agree_privacy_consent(
            pending_agree_version_ids=pending_agree_version_ids,
        )

    async def async_get_product_instruction(
        self,
        *,
        device_sn: str,
        type: str,
    ) -> dict[str, Any]:
        """Return product-instruction payload for a device serial number."""
        return await self.api.async_get_product_instruction(
            dev_sn=device_sn,
            type=type,
        )

    async def async_get_user_info(self) -> dict[str, Any]:
        """Return authenticated-user profile information for the selected account."""
        return await self.api.async_get_user_info()

    async def async_update_user_info(self, nick_name: str) -> None:
        """Update the account display nickname via the primary HTTP API.

        The nickname is account-scoped and never ingested into coordinator data,
        so there is no local patch to apply.
        """
        await self.api.async_update_user_info(nick_name=nick_name)


__all__ = ["JackerySolarVaultCoordinator", "RejectionMetrics"]
