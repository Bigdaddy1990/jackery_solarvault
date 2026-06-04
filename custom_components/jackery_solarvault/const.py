"""Constants for the Jackery SolarVault integration."""

from typing import Final

DOMAIN: Final = "jackery_solarvault"
MANUFACTURER: Final = "Jackery"


# --- API ---------------------------------------------------------------------
BASE_URL: Final = "https://iot.jackeryapp.com"
LOGIN_PATH: Final = "/v1/auth/login"

# API timeouts. Login uses a longer budget than the polling requests because
# hybrid AES+RSA-login plus chained credential fetches takes noticeably longer
# than GET /v1/device/* reads (which are served from a cache on Jackery's side).
LOGIN_TIMEOUT_SEC: Final = 30

# --- Jackery Cloud MQTT -----------------------------------------------------
MQTT_HOST: Final = "emqx.jackeryapp.com"
MQTT_PORT: Final = 8883
MQTT_KEEPALIVE_SEC: Final = 60

# How long the integration tolerates silence on the MQTT subscription
# before flagging it as stale in diagnostics. Real Jackery devices send
# at least one heartbeat every ~30 s; 5 minutes of silence is a strong
# signal the broker subscription is broken even though the TCP socket
# is still open.
MQTT_SILENT_THRESHOLD_SEC: Final = 180

# How long a battery pack may report ``commState=0`` (offline) before the
# coordinator removes it from PAYLOAD_BATTERY_PACKS. The Jackery cloud
# never explicitly emits "pack-removed"; a pack that has been physically
# unplugged simply stops appearing in MQTT updates while sometimes being
# echoed on HTTP for a while. 30 days covers seasonal off-line periods
# (e.g. backup packs that hibernate over the summer) so a temporarily
# disconnected pack is not removed and re-added on every cycle, while
# permanently-removed packs are still cleaned up within a month.
BATTERY_PACK_STALE_THRESHOLD_SEC: Final = 30 * 24 * 3600

# SolarVault BLE setters can echo their notify ACK later than the old 5s
# window. Keep the default below HA's common 30s task/unload thresholds while
# avoiding false MQTT fallbacks during normal GATT latency.
DEFAULT_BLE_ACK_TIMEOUT_SEC: Final = 15.0

# Internal field name used by the coordinator to remember when a pack
# last reported as online. Not exposed as an entity attribute.
PACK_FIELD_LAST_SEEN_AT: Final = "_last_seen_at"

# When the REST token/session rotates frequently (for example because the
# mobile app logs in at the same time), rebuilding MQTT credentials on every
# poll can create reconnect churn. Throttle reconnect attempts a little.
MQTT_RECONNECT_THROTTLE_SEC: Final = 120
# Adaptive polling: when MQTT delivered an inbound message within the
# live threshold, we skip the coordinator HTTP refresh so HTTP only runs as a
# keep-alive every ``ADAPTIVE_KEEPALIVE_INTERVAL_SEC``. The integration remains
# cloud_polling because HTTP polling is the startup, fallback and keep-alive
# data path; MQTT push is an optional live enhancement.
MQTT_LIVE_THRESHOLD_SEC: Final = 30
ADAPTIVE_KEEPALIVE_INTERVAL_SEC: Final = 30
# Consecutive CONNACK auth rejections (rc=4/5/134/135) at this threshold are
# logged loudly by the MQTT client. They do not trigger HA reauth by themselves:
# the official Jackery app can rotate broker sessions while HTTP credentials
# remain valid, so the coordinator pauses MQTT and keeps HTTP polling alive.
MQTT_AUTH_FAILURE_TOLERANCE: Final = 3

# When the broker rejects credentials with CONNACK rc=4/5/134/135, the most
# likely cause is that the official Jackery app just logged in with the same
# ``<userId>@APP`` clientId and the broker rotated our credentials. Reconnect-
# storms in that situation only deepen the conflict, so the coordinator pauses
# MQTT entirely for ``MQTT_APP_CONFLICT_PAUSE_SEC`` after every rejection and
# falls back to HTTP polling. The probe-reconnect after the pause confirms
# whether the conflict cleared (app went offline / token settled). HA reauth is
# left to HTTP auth failures; MQTT-only rejection must not stop the integration.
MQTT_APP_CONFLICT_PAUSE_SEC: Final = 60

# Persisted MQTT-session-cache dict keys. Used by mqtt_session_cache.py to
# read/write the JSON row that survives reloads and pins down the macId the
# device was last authenticated with — required so reconnects do not flip the
# clientId between cycles.
MQTT_SESSION_MAC_ID: Final = "mac_id"
MQTT_SESSION_MAC_ID_SOURCE: Final = "mac_id_source"
MQTT_SESSION_SEED_B64: Final = "seed_b64"
MQTT_SESSION_USER_ID: Final = "user_id"

# Third-party MQTT bridge config (PROTOCOL.md §5). Surfaced in the
# options/reconfigure flow so the device can be told to publish telemetry to
# a local MQTT broker. The bridge is DISABLED by default and all credential
# fields default to empty — actual values must come from user input via the
# config flow, never from hard-coded constants (PII / security).
CONF_THIRD_PARTY_MQTT_ENABLE: Final = "third_party_mqtt_enable"
DEFAULT_THIRD_PARTY_MQTT_ENABLE: Final = False
CONF_THIRD_PARTY_MQTT_IP: Final = "third_party_mqtt_ip"
DEFAULT_THIRD_PARTY_MQTT_IP: Final = ""
CONF_THIRD_PARTY_MQTT_PORT: Final = "third_party_mqtt_port"
DEFAULT_THIRD_PARTY_MQTT_PORT: Final = 1883
CONF_THIRD_PARTY_MQTT_USERNAME: Final = "third_party_mqtt_username"
DEFAULT_THIRD_PARTY_MQTT_USERNAME: Final = ""
CONF_THIRD_PARTY_MQTT_PASSWORD: Final = "third_party_mqtt_password"
DEFAULT_THIRD_PARTY_MQTT_PASSWORD: Final = ""
CONF_THIRD_PARTY_MQTT_TOKEN: Final = "third_party_mqtt_token"
DEFAULT_THIRD_PARTY_MQTT_TOKEN: Final = ""
CONF_THIRD_PARTY_MQTT_TOPIC_FILTER: Final = "third_party_mqtt_topic_filter"
# Safe narrow default from app traces / user reports. Still configurable.
# Broad wildcards (for example ``#``) remain blocked separately.
DEFAULT_THIRD_PARTY_MQTT_TOPIC_FILTER: Final = "homeassistant"

# HTTP endpoint constants.
DEVICE_PROPERTY_PATH: Final = "/v1/device/property"  # ?deviceId=<id>
SYSTEM_LIST_PATH: Final = "/v1/device/system/list"  # system/list discovery endpoint
ALARM_PATH: Final = "/v1/api/alarm"  # ?systemId=<id>
SYSTEM_STATISTIC_PATH: Final = "/v1/device/stat/systemStatistic"  # ?systemId=<id>
PV_TRENDS_PATH: Final = (
    "/v1/device/stat/sys/pv/trends"  # ?systemId=<id>&beginDate&endDate&dateType
)
POWER_PRICE_PATH: Final = "/v1/device/dynamic/powerPriceConfig"  # ?systemId=<id>
PRICE_SOURCE_LIST_PATH: Final = "/v1/device/dynamic/priceCompany"  # ?systemId=<id>
PRICE_HISTORY_CONFIG_PATH: Final = "/v1/device/dynamic/historyConfig"  # ?systemId=<id>
SAVE_SINGLE_MODE_PATH: Final = (
    "/v1/device/dynamic/saveSingleMode"  # form: systemId,singlePrice,currency
)
SAVE_DYNAMIC_MODE_PATH: Final = "/v1/device/dynamic/saveDynamicMode"  # form: systemId,platformCompanyId,systemRegion

# Device/statistic endpoint group
DEVICE_STATISTIC_PATH: Final = "/v1/device/stat/deviceStatistic"  # ?deviceId=<id>
HOME_TRENDS_PATH: Final = "/v1/device/stat/sys/home/trends"  # ?systemId&...
BATTERY_TRENDS_PATH: Final = "/v1/device/stat/sys/battery/trends"  # ?systemId&...
DEVICE_PV_STAT_PATH: Final = "/v1/device/stat/pv"  # ?deviceId&systemId&...
DEVICE_BATTERY_STAT_PATH: Final = "/v1/device/stat/battery"  # ?deviceId&...
DEVICE_HOME_STAT_PATH: Final = "/v1/device/stat/onGrid"  # ?deviceId&...
DEVICE_CT_STAT_PATH: Final = "/v1/device/stat/ct"  # ?deviceId&...
DEVICE_EPS_STAT_PATH: Final = "/v1/device/stat/eps"  # ?deviceId&...
DEVICE_TODAY_ENERGY_PATH: Final = "/v1/device/stat/today"  # ?deviceSn=<...>
DEVICE_METER_STAT_PATH: Final = (
    "/v1/device/stat/meter"  # ?deviceId=<smart-meter subdevice>
)
DEVICE_SOCKET_STAT_PATH: Final = "/v1/device/stat/socket"  # ?deviceId&...
DEVICE_SOCKET_STATISTIC_PATH: Final = (
    "/v1/device/stat/smartSocketStatistic"  # ?smartSocketId=<socket accessory>
)
BATTERY_PACK_PATH: Final = "/v1/device/battery/pack/list"  # ?deviceSn=<sn>
OTA_LIST_PATH: Final = "/v1/device/ota/list"  # ?deviceSnList=<sn>
LOCATION_PATH: Final = "/v1/device/location"  # ?deviceId=<id>
SYSTEM_NAME_PATH: Final = "/v1/device/system/name"  # PUT {systemName,id}
SHELLY_DEVICES_PATH: Final = "/v1/device/shelly/devices"
SHELLY_REALTIME_POWER_PATH: Final = "/v1/wss-cloud/device/shelly/device/realtime-power"
SHELLY_CONTROL_PATH: Final = "/v1/wss-cloud/device/shelly/device/control"
# Experimental write endpoint observed in the app traffic.
# The max-power endpoint was captured but only failed responses (code 10600)
# have been seen so far. It might be a history log rather than the live setter.
MAX_POWER_SAVE_PATH: Final = "/v1/device/deviceMaxPowerRecord/saveRecord"

# Legacy endpoint used by Explorer portables — kept as fallback only
DEVICE_LIST_PATH: Final = "/v1/device/bind/list"

# Crypto material extracted from the Jackery app (iOS+Android both use these)
AES_KEY: Final = b"1234567890123456"
RSA_PUBLIC_KEY_B64: Final = (
    "MIGfMA0GCSqGSIb3DQEBAQUAA4GNADCBiQKBgQCVmzgJy/4XolxPnkfu32YtJqYG"
    "FLYqf9/rnVgURJED+8J9J3Pccd6+9L97/+7COZE5OkejsgOkqeLNC9C3r5mhpE4zk"
    "/HStss7Q8/5DqkGD1annQ+eoICo3oi0dITZ0Qll56Dowb8lXi6WHViVDdih/oeUwV"
    "JY89uJNtTWrz7t7QIDAQAB"
)
REGISTER_APP_ID: Final = "com.hbxn.jackery"

# --- Android app headers documented in PROTOCOL.md §2 --------------------
# NB: iOS headers (platform=1) returned empty device lists on a SolarVault-only
# account. Android headers (platform=2) return data on /v1/device/property.
APP_VERSION: Final = "v2.0.1"
APP_VERSION_CODE: Final = "87"
SYS_VERSION: Final = "Android 16,level 36/[arm64-v8a, armeabi-v7a, armeabi]"
USER_AGENT: Final = "okhttp/5.3.2"
DEVICE_MODEL_HEADER: Final = "samsung/SM-S918B"
PLATFORM_HEADER: Final = "2"

CODE_OK: Final = 0
CODE_TOKEN_EXPIRED: Final = 10402

# --- Config / Options --------------------------------------------------------
CONF_MQTT_MAC_ID: Final = "mqtt_mac_id"
CONF_REGION_CODE: Final = "region_code"
CONF_CREATE_SMART_METER_DERIVED_SENSORS: Final = "create_smart_meter_derived_sensors"
CONF_CREATE_CALCULATED_POWER_SENSORS: Final = "create_calculated_power_sensors"
CONF_CREATE_SAVINGS_DETAIL_SENSORS: Final = "create_savings_detail_sensors"
# Experimental BLE transport.
# When enabled, the coordinator subscribes to GATT notify on each known
# SolarVault and surfaces the decoded/raw frames in diagnostics
# client/ble.py for the wire-format reference.
CONF_ENABLE_BLE_TRANSPORT: Final = "enable_ble_transport"
DEFAULT_ENABLE_BLE_TRANSPORT: Final = True
CONF_ENABLE_BLE_WRITES: Final = "enable_ble_writes"
# Default off and gated by JACKERY_DEV_MODE=1
# ("BLE-Schreibbefehle waren als normale UI-Option erreichbar"). The UI
# toggle was removed; this flag is consumed only by services/code paths
# that already check the dev-mode env var.
DEFAULT_ENABLE_BLE_WRITES: Final = True
CONF_ENABLE_UNREDACTED_DIAGNOSTICS: Final = "enable_unredacted_diagnostics"
# Default off — diagnostics with full credentials, serial numbers, MQTT
# topics and bluetoothKey are off by default for security. User must opt
# in explicitly for local troubleshooting.
DEFAULT_ENABLE_UNREDACTED_DIAGNOSTICS: Final = False

#: Magic prefix that every plaintext frame starts with.
BLE_FRAME_MAGIC: str = "DFED"
#: Protocol version following the magic. Constant in the app's
#: ``BLE_SEND_DATA_FORMAT_HEX = "DFED0001%s%s%s%s0001%s%s"``.
BLE_FRAME_VERSION: str = "0001"
#: Payload-type marker between the header block and the chunk length.
BLE_FRAME_PAYLOAD_MARKER: str = "0001"
#: Length in hex characters of every fixed-width 16-bit field.
_HEX16_WIDTH: int = 4
#: Key lengths (in bytes) accepted by the BLE crypto helpers.
#:
#: PROTOCOL.md §14 originally documented a fixed 32-byte AES-256 key, but the
#: live ``/v1/device/system/list`` capture from a SolarVault 3 Pro Max
#: returned a 16-byte key (``base64.b64decode("aHIyYzBoaDM2MTMzNjEzOA==")``
#: → ``hr2c0hh361336138``). The Jackery app's smali ``bb/a`` accepts either
#: width because ``Cipher.getInstance("AES/CBC/PKCS7Padding")`` selects
#: AES-128 or AES-256 from the key length implicitly. Both are listed here
#: so callers can pick the right one without hard-coding either.
BLE_AES_KEY_LEN_AES128: int = 16
BLE_AES_KEY_LEN_AES256: int = 32
#: Tuple of accepted key lengths, used for input validation.
BLE_AES_KEY_LENGTHS: tuple[int, ...] = (
    BLE_AES_KEY_LEN_AES128,
    BLE_AES_KEY_LEN_AES256,
)
# Backwards-compatible alias kept until call sites migrate; new code should
# branch on the actual key length the device returns.
BLE_AES_KEY_LEN: int = BLE_AES_KEY_LEN_AES128
#: AES-CBC IV length in bytes.
BLE_AES_IV_LEN: int = 16
#: GATT service UUID advertised by the SolarVault BLE radio.
BLE_SERVICE_UUID: str = "0000bdee-0000-1000-8000-00805f9b34fb"
#: Write-without-response characteristic (app -> device).
BLE_WRITE_CHAR_UUID: str = "0000ee01-0000-1000-8000-00805f9b34fb"
#: Notify characteristic (device -> app); needs CCCD ``0x2902`` enabled.
BLE_NOTIFY_CHAR_UUID: str = "0000ee02-0000-1000-8000-00805f9b34fb"
#: Bluetooth SIG company identifier under which the SolarVault advertises
#: its serial number in the manufacturer-data field.
BLE_MANUFACTURER_ID: int = 0x4802  # 18434 decimal — confirmed via live scan

# Optional fallback for home-energy when sys/home/trends is empty.
# Default stays False: device_home_stat is not the same metric family as
# home_trends and must never silently replace it.
CONF_ENABLE_DERIVED_HOME_ENERGY_FALLBACK: Final = "enable_derived_home_energy_fallback"
DEFAULT_ENABLE_DERIVED_HOME_ENERGY_FALLBACK: Final = False

# Per-period statistics import toggles surfaced in the options flow. Day buckets
# always import; week/month/year are opt-out. Defaults preserve the baseline
# behaviour of importing every documented period.
CONF_ENABLE_WEEK_STATISTICS: Final = "enable_week_statistics"
DEFAULT_ENABLE_WEEK_STATISTICS: Final = True
CONF_ENABLE_MONTH_STATISTICS: Final = "enable_month_statistics"
DEFAULT_ENABLE_MONTH_STATISTICS: Final = True
CONF_ENABLE_YEAR_STATISTICS: Final = "enable_year_statistics"
DEFAULT_ENABLE_YEAR_STATISTICS: Final = True
# Config-flow step, error and abort identifiers.
FLOW_STEP_USER: Final = "user"
FLOW_STEP_INIT: Final = "init"
FLOW_STEP_REAUTH_CONFIRM: Final = "reauth_confirm"
FLOW_STEP_RECONFIGURE: Final = "reconfigure"
FLOW_ERROR_BASE: Final = "base"
FLOW_ERROR_INVALID_AUTH: Final = "invalid_auth"
FLOW_ERROR_CANNOT_CONNECT: Final = "cannot_connect"
FLOW_ERROR_ACCOUNT_REQUIRED: Final = "account_required"
FLOW_ABORT_REAUTH_ENTRY_MISSING: Final = "reauth_entry_missing"
FLOW_ABORT_REAUTH_SUCCESSFUL: Final = "reauth_successful"
FLOW_ABORT_RECONFIGURE_ENTRY_MISSING: Final = "reconfigure_entry_missing"
FLOW_ABORT_RECONFIGURE_SUCCESSFUL: Final = "reconfigure_successful"
FLOW_ABORT_RECONFIGURE_ACCOUNT_MISMATCH: Final = "reconfigure_account_mismatch"

DEFAULT_SCAN_INTERVAL_SEC: Final = 30
DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS: Final = True
DEFAULT_CREATE_CALCULATED_POWER_SENSORS: Final = False
DEFAULT_CREATE_SAVINGS_DETAIL_SENSORS: Final = False

# Slow-metric refresh cadences (decoupled from the fast property polling).
# These values match the server-side update rhythm we observed in the
# captured traffic — polling faster yields no fresher data.
SLOW_METRICS_INTERVAL_SEC: Final = 60  # statistic + pv_trends + alarm
PRICE_CONFIG_INTERVAL_SEC: Final = 600  # power price barely ever changes
DEFAULT_STORM_WARNING_MINUTES: Final = 120
REQUEST_TIMEOUT_SEC: Final = 15

HTTP_METHOD_GET: Final = "GET"
HTTP_METHOD_POST: Final = "POST"
HTTP_METHOD_PUT: Final = "PUT"
HTTP_HEADER_CONTENT_TYPE: Final = "content-type"
HTTP_CONTENT_TYPE_FORM: Final = "application/x-www-form-urlencoded"
HTTP_CONTENT_TYPE_JSON: Final = "application/json; charset=utf-8"
HTTP_RAW_TEXT_LIMIT: Final = 10000

# Shared payload section names. Coordinator, diagnostics and entity platforms use
# the same normalized payload shape; keep the keys in one place so section names
PAYLOAD_DEVICE: Final = "device"
PAYLOAD_PROPERTIES: Final = "properties"
PAYLOAD_HTTP_PROPERTIES: Final = "http_properties"
PAYLOAD_DISCOVERY: Final = "discovery"
PAYLOAD_SYSTEM: Final = "system"
PAYLOAD_STATISTIC: Final = "statistic"
PAYLOAD_PRICE: Final = "price"
PAYLOAD_PV_TRENDS: Final = "pv_trends"
PAYLOAD_BATTERY_TRENDS: Final = "battery_trends"
PAYLOAD_HOME_TRENDS: Final = "home_trends"
PAYLOAD_ALARM: Final = "alarm"
PAYLOAD_DEVICE_STATISTIC: Final = "device_statistic"
PAYLOAD_LIFETIME_COUNTERS: Final = "lifetime_counters"
PAYLOAD_OTA: Final = "ota"
PAYLOAD_LOCATION: Final = "location"
PAYLOAD_WEATHER_PLAN: Final = "weather_plan"
PAYLOAD_TASK_PLAN: Final = "task_plan"
PAYLOAD_THIRD_PARTY_MQTT_CONFIG: Final = "third_party_mqtt_config"
PAYLOAD_WIFI_CONFIG: Final = "wifi_config"
PAYLOAD_WIFI_LIST: Final = "wifi_list"
PAYLOAD_TIMEZONE_CONFIG: Final = "timezone_config"
PAYLOAD_MQTT_CONNECT_INFO: Final = "mqtt_connect_info"
PAYLOAD_PRICE_SOURCES: Final = "price_sources"
PAYLOAD_BATTERY_PACKS: Final = "battery_packs"
PAYLOAD_CT_METER: Final = "ct_meter"
PAYLOAD_METER_HEADS: Final = "meter_heads"
# Smart-plug subdevice payload bucket. Populated from the ``plugs`` array of
# UploadSubDeviceGroupProperty (cmd=110, actionId=3032, devType=6 per the
# app's ``HomeSubDeviceType`` enum) so downstream entity layers can iterate
# the plug list with the same shape as battery packs.
PAYLOAD_SMART_PLUGS: Final = "smart_plugs"
PAYLOAD_NOTICE: Final = "notice"
PAYLOAD_MQTT_LAST: Final = "mqtt_last"
PAYLOAD_DATA_QUALITY: Final = "data_quality"

# Internal normalized payload/cache keys. These are not Jackery wire keys, but
# integration-owned keys used to keep coordinator payloads and diagnostics stable.
PAYLOAD_SYSTEM_META: Final = "system_meta"
PAYLOAD_DEVICE_META: Final = "device_meta"
PAYLOAD_PRICE_HISTORY_CONFIG: Final = "price_history_config"


# Common Jackery app/API field names used by several platforms. Names are kept
# verbatim because they are wire keys documented or observed in app traffic.
FIELD_ACTION_ID: Final = "actionId"
FIELD_ACTION: Final = "action"
FIELD_ALERT_ID: Final = "alertId"
FIELD_BODY: Final = "body"
FIELD_CONTROL_ALLOWED: Final = "controlAllowed"
FIELD_DATA: Final = "data"
FIELD_FUNCTION: Final = "function"
FIELD_ID: Final = "id"
FIELD_VERSION: Final = "version"
FIELD_MESSAGE_TYPE: Final = "messageType"
FIELD_TIMESTAMP: Final = "timestamp"
FIELD_BIND_ID: Final = "bindId"
FIELD_DEVICE_CODE: Final = "deviceCode"
FIELD_HOST: Final = "host"
FIELD_ICON: Final = "icon"
FIELD_ICON_PATH: Final = "iconPath"
FIELD_INTEGRATOR_ENABLED: Final = "integratorEnabled"
FIELD_POWER_BODY: Final = "powerBody"
FIELD_SWITCH: Final = "switch"
SHELLY_CONTROL_FUNCTION_SWITCH: Final = "switch"
SHELLY_CONTROL_ACTION_ON: Final = "on"
SHELLY_CONTROL_ACTION_OFF: Final = "off"
# AccSocketBody short-keys per docs/html/jackery_smali_home_assistant_report.html
# §"AccSocketBody". ``op`` is already an alias for ``outPw`` (coordinator
# merge), ``switch`` is exposed through switch/binary_sensor entities, and
# the remaining two are documented but not observed in this installer's
# current payload stream — they are exposed as diagnostic sensors so users
# whose firmware does emit them see the data, without requiring a code
# change later.
FIELD_SOCKET_SWITCH_CYCLE: Final = "sc"
FIELD_SOCKET_LAST_UPDATE_TS: Final = "ts"
FIELD_CMD: Final = "cmd"
FIELD_UPDATES: Final = "updates"
FIELD_SYSTEM_ID: Final = "systemId"
FIELD_DEVICE_ID: Final = "deviceId"
FIELD_DEV_ID: Final = "devId"
FIELD_SMART_SOCKET_ID: Final = "smartSocketId"
FIELD_DEVICE_NAME: Final = "deviceName"
FIELD_DEVICE_SN: Final = "deviceSn"
FIELD_DEV_SN: Final = "devSn"
FIELD_DEVICE_SECRET: Final = "deviceSecret"
FIELD_RANDOM_SALT: Final = "randomSalt"
FIELD_SYSTEM_SN: Final = "systemSn"
FIELD_SYSTEM_NAME: Final = "systemName"
FIELD_MODEL_NAME: Final = "modelName"
FIELD_DEV_MODEL: Final = "devModel"
FIELD_ONLINE_STATUS: Final = "onlineStatus"
FIELD_ONLINE_STATE: Final = "onlineState"
FIELD_WNAME: Final = "wname"
FIELD_MAC: Final = "mac"
FIELD_WIP: Final = "wip"
# Ethernet diagnostics. Present in the SolarVault HomeBody payload alongside
# ``mac`` / ``wip`` (the Wi-Fi pair). Not exposed as entities by default;
# available to diagnostics and to future entity descriptions that need them.
FIELD_EIP: Final = "eip"
FIELD_EMAC: Final = "emac"
FIELD_CURRENT_VERSION: Final = "currentVersion"
FIELD_TARGET_VERSION: Final = "targetVersion"
FIELD_UPDATE_CONTENT: Final = "updateContent"
FIELD_DEVICES: Final = "devices"
FIELD_ACCESSORIES: Final = "accessories"
FIELD_BIND_KEY: Final = "bindKey"
FIELD_IS_CLOUD: Final = "isCloud"
FIELD_DEV_TYPE: Final = "devType"
FIELD_DEVICE_TYPE: Final = "deviceType"
FIELD_MODEL_CODE: Final = "modelCode"
FIELD_SCAN_NAME: Final = "scanName"
FIELD_SUB_TYPE: Final = "subType"
FIELD_TYPE_NAME: Final = "typeName"
FIELD_PRODUCT_MODEL: Final = "productModel"
FIELD_ONLINE: Final = "online"

FIELD_REBOOT: Final = "reboot"
FIELD_SCHE_PHASE: Final = "schePhase"
FIELD_ETH_PORT: Final = "ethPort"
FIELD_SW_EPS: Final = "swEps"
FIELD_SW_EPS_STATE: Final = "swEpsState"
FIELD_AUTO_STANDBY: Final = "autoStandby"
FIELD_IS_AUTO_STANDBY: Final = "isAutoStandby"
FIELD_IS_FOLLOW_METER_PW: Final = "isFollowMeterPw"
FIELD_FOLLOW_METER: Final = "followMeter"
FIELD_OFF_GRID_DOWN: Final = "offGridDown"
FIELD_OFF_GRID_TIME: Final = "offGridTime"
FIELD_OFF_GRID_DOWN_TIME: Final = "offGridDownTime"
FIELD_OFF_GRID_AUTO_OFF_TIME: Final = "offGridAutoOffTime"
FIELD_WPS: Final = "wps"
FIELD_WPC: Final = "wpc"
FIELD_MINS_INTERVAL: Final = "minsInterval"
FIELD_STORM: Final = "storm"
FIELD_START_TS: Final = "startTs"
FIELD_END_TS: Final = "endTs"
FIELD_STATUS: Final = "status"
FIELD_MANUAL: Final = "manual"
FIELD_ACTION_TYPE: Final = "actionType"
FIELD_TASK_TYPE: Final = "taskType"
FIELD_TID: Final = "tid"
FIELD_WORK_MODEL: Final = "workModel"
FIELD_TEMP_UNIT: Final = "tempUnit"
FIELD_DYNAMIC_OR_SINGLE: Final = "dynamicOrSingle"
FIELD_PRICE_MODE: Final = "priceMode"
FIELD_SINGLE_PRICE: Final = "singlePrice"
FIELD_PLATFORM_COMPANY_ID: Final = "platformCompanyId"
FIELD_SYSTEM_REGION: Final = "systemRegion"
FIELD_COUNTRY: Final = "country"
FIELD_COUNTRY_CODE: Final = "countryCode"
FIELD_COMPANY_NAME: Final = "companyName"
FIELD_NAME: Final = "name"
FIELD_CID: Final = "cid"
FIELD_CURRENCY: Final = "currency"
FIELD_CODE: Final = "code"
FIELD_MSG: Final = "msg"
FIELD_TOKEN: Final = "token"
FIELD_RAW_TEXT: Final = "_raw_text"
FIELD_ACCOUNT: Final = "account"
FIELD_LOGIN_TYPE: Final = "loginType"
FIELD_MAC_ID: Final = "macId"
FIELD_PASSWORD: Final = "password"
FIELD_REGISTER_APP_ID: Final = "registerAppId"
FIELD_REGION_CODE: Final = "regionCode"
FIELD_DEVICE_SN_LIST: Final = "deviceSnList"
FIELD_BATTERY_PACKS: Final = "batteryPacks"
FIELD_BATTERY_PACK: Final = "batteryPack"
FIELD_BATTERY_PACK_LIST: Final = "batteryPackList"
FIELD_BATTERIES: Final = "batteries"
FIELD_PACK_LIST: Final = "packList"
# HomeSubBody sub-device arrays (verified against
# ``com.hbxn.control.device.bean.home.HomeSubBody$PlugBody/CollectorBody/CtBody``
# in the Jackery app smali). Each sub-array key in the MQTT
# ``UploadSubDeviceGroupProperty`` payload matches the inner-class field name.
FIELD_PLUGS: Final = "plugs"
FIELD_COLLECTORS: Final = "collectors"
FIELD_CTS: Final = "cts"
FIELD_SN: Final = "sn"
FIELD_MODEL: Final = "model"
FIELD_COMM_STATE: Final = "commState"
FIELD_COMM_MODE: Final = "commMode"
FIELD_UPDATE_STATUS: Final = "updateStatus"
FIELD_EC: Final = "ec"
FIELD_IT: Final = "it"
FIELD_OT: Final = "ot"
FIELD_MAX_POWER: Final = "maxPower"
FIELD_CURRENCY_CODE: Final = "currencyCode"
FIELD_SINGLE_CURRENCY: Final = "singleCurrency"
FIELD_SINGLE_CURRENCY_CODE: Final = "singleCurrencyCode"
FIELD_REGION: Final = "region"
FIELD_TIMEZONE: Final = "timezone"
FIELD_UO: Final = "uo"
FIELD_TS: Final = "ts"
FIELD_GRID_STANDARD: Final = "gridStandard"
FIELD_SAFETY: Final = "safety"
FIELD_UNBIND: Final = "unbind"
FIELD_LONGITUDE: Final = "longitude"
FIELD_LATITUDE: Final = "latitude"

FIELD_SOC: Final = "soc"
FIELD_PV_PW: Final = "pvPw"
FIELD_PV1: Final = "pv1"
FIELD_PV2: Final = "pv2"
FIELD_PV3: Final = "pv3"
FIELD_PV4: Final = "pv4"
FIELD_BAT_IN_PW: Final = "batInPw"
FIELD_BAT_OUT_PW: Final = "batOutPw"
FIELD_STACK_IN_PW: Final = "stackInPw"
FIELD_STACK_OUT_PW: Final = "stackOutPw"
FIELD_WSIG: Final = "wsig"
FIELD_CHARGE_PLAN_PW: Final = "chargePlanPw"
FIELD_ON_GRID_STAT: Final = "onGridStat"
FIELD_CT_STATE: Final = "ctState"
FIELD_GRID_STATE_ALT: Final = "gridState"
FIELD_GRID_STAT: Final = "gridStat"
FIELD_MAX_OUT_PW: Final = "maxOutPw"
FIELD_MAX_FEED_GRID: Final = "maxFeedGrid"
FIELD_MAX_GRID_STD_PW: Final = "maxGridStdPw"
FIELD_DEFAULT_PW: Final = "defaultPw"
FIELD_SOC_CHG_LIMIT: Final = "socChgLimit"
FIELD_SOC_CHARGE_LIMIT: Final = "socChargeLimit"
FIELD_SOC_DISCHG_LIMIT: Final = "socDischgLimit"
FIELD_SOC_DISCHARGE_LIMIT: Final = "socDischargeLimit"
FIELD_OTHER_LOAD_PW: Final = "otherLoadPw"
FIELD_ABILITY: Final = "ability"
FIELD_MAX_IOT_NUM: Final = "maxIotNum"
FIELD_MAX_INV_STD_PW: Final = "maxInvStdPw"
FIELD_BAT_NUM: Final = "batNum"
FIELD_BAT_STATE: Final = "batState"
FIELD_BAT_SOC: Final = "batSoc"
FIELD_CELL_TEMP: Final = "cellTemp"
FIELD_IN_EGY: Final = "inEgy"  # Pack lifetime charged energy in Wh (BLE cmd=120)
FIELD_IN_PW: Final = "inPw"
FIELD_OUT_EGY: Final = "outEgy"  # Pack lifetime discharged energy in Wh (BLE cmd=120)
FIELD_OUT_PW: Final = "outPw"
FIELD_SWITCH_STATE: Final = "switchSta"  # PlugSub current on/off state (0/1)
FIELD_SYS_SWITCH: Final = "sysSwitch"  # PlugSub desired on/off setter (0/1)
FIELD_SOCKET_PRIORITY: Final = "socketPri"  # PlugSub priority enable flag
FIELD_TODAY_ENERGY: Final = "todayEgy"
FIELD_TOTAL_ENERGY: Final = "totalEgy"
FIELD_USE_ENERGY: Final = "useEnergy"
FIELD_CHARGING_ENERGY: Final = "chargingEnergy"
FIELD_DISCHARGING_ENERGY: Final = "dischargingEnergy"
FIELD_RB: Final = "rb"
FIELD_IP: Final = "ip"
FIELD_OP: Final = "op"
FIELD_STANDBY_PW: Final = "standbyPw"
FIELD_SW_EPS_IN_PW: Final = "swEpsInPw"
FIELD_SW_EPS_OUT_PW: Final = "swEpsOutPw"
FIELD_STAT: Final = "stat"
FIELD_ONGRID_STAT: Final = "ongridStat"
FIELD_CT_STAT: Final = "ctStat"
FIELD_GRID_STATE: Final = "gridSate"
FIELD_ENERGY_PLAN_PW: Final = "energyPlanPw"
FIELD_MAX_SYS_OUT_PW: Final = "maxSysOutPw"
FIELD_MAX_SYS_IN_PW: Final = "maxSysInPw"
FIELD_FUNC_ENABLE: Final = "funcEnable"
FIELD_IS_FIRMWARE_UPGRADE: Final = "isFirmwareUpgrade"
FIELD_HOME_LOAD_PW: Final = "homeLoadPw"
FIELD_LOAD_PW: Final = "loadPw"
FIELD_IN_ONGRID_PW: Final = "inOngridPw"
FIELD_GRID_IN_PW: Final = "gridInPw"
FIELD_IN_GRID_SIDE_PW: Final = "inGridSidePw"
FIELD_OUT_ONGRID_PW: Final = "outOngridPw"
FIELD_GRID_OUT_PW: Final = "gridOutPw"
FIELD_OUT_GRID_SIDE_PW: Final = "outGridSidePw"

# MQTT credential keys returned by login and consumed by mqtt_push.py.
FIELD_USER_ID: Final = "userId"
FIELD_MQTT_PASSWORD: Final = "mqttPassWord"
MQTT_CREDENTIAL_CLIENT_ID: Final = "client_id"
MQTT_CREDENTIAL_USERNAME: Final = "username"
MQTT_CREDENTIAL_PASSWORD: Final = "password"
MQTT_CREDENTIAL_USER_ID: Final = "user_id"

# Smart-meter/CT wire keys. These are app payload names; centralizing them keeps
# CT-derived diagnostics and helper math aligned without changing CT behavior.
FIELD_CT_POWER: Final = "power"
FIELD_CT_POWER1: Final = "power1"
FIELD_CT_POWER2: Final = "power2"
FIELD_CT_POWER3: Final = "power3"
FIELD_CT_VOLT: Final = "volt"
FIELD_CT_VOLT1: Final = "volt1"
FIELD_CT_VOLT2: Final = "volt2"
FIELD_CT_VOLT3: Final = "volt3"
FIELD_CT_CURRENT: Final = "curr"
FIELD_CT_CURRENT1: Final = "curr1"
FIELD_CT_CURRENT2: Final = "curr2"
FIELD_CT_CURRENT3: Final = "curr3"
FIELD_CT_FREQUENCY: Final = "freq"
FIELD_CT_POWER_FACTOR: Final = "fact"
FIELD_CT_POWER_FACTOR1: Final = "fact1"
FIELD_CT_POWER_FACTOR2: Final = "fact2"
FIELD_CT_POWER_FACTOR3: Final = "fact3"
# AccCTBody apparent / reactive power per docs/html jackery_entity_field_candidates_v2:
# ``ap``/``ap1..3`` = apparent power (VA), ``rep``/``rep1..3`` = reactive
# power (var). Total + per-phase variants mirror the active-power layout.
FIELD_CT_APPARENT_POWER: Final = "ap"
FIELD_CT_APPARENT_POWER1: Final = "ap1"
FIELD_CT_APPARENT_POWER2: Final = "ap2"
FIELD_CT_APPARENT_POWER3: Final = "ap3"
FIELD_CT_REACTIVE_POWER: Final = "rep"
FIELD_CT_REACTIVE_POWER1: Final = "rep1"
FIELD_CT_REACTIVE_POWER2: Final = "rep2"
FIELD_CT_REACTIVE_POWER3: Final = "rep3"
# CtSub.funForm per docs/html/jackery_entity_field_candidates_v2.html:
# CT function-form / wiring-mode identifier (1-phase vs 3-phase config).
# Exposed as diagnostic — useful for troubleshooting an unexpected CT layout.
FIELD_ACC_CT_BODY: Final = "AccCTBody"
FIELD_CT_FUN_FORM: Final = "funForm"
FIELD_CT_SCHE_PHASE: Final = "schePhase"
FIELD_CT_TOTAL_PHASE_POWER: Final = "tPhasePw"
FIELD_CT_TOTAL_NEGATIVE_PHASE_POWER: Final = "tnPhasePw"
FIELD_CT_A_PHASE_POWER: Final = "aPhasePw"
FIELD_CT_B_PHASE_POWER: Final = "bPhasePw"
FIELD_CT_C_PHASE_POWER: Final = "cPhasePw"
FIELD_CT_A_NEGATIVE_PHASE_POWER: Final = "anPhasePw"
FIELD_CT_B_NEGATIVE_PHASE_POWER: Final = "bnPhasePw"
FIELD_CT_C_NEGATIVE_PHASE_POWER: Final = "cnPhasePw"
FIELD_CT_TOTAL_PHASE_ENERGY: Final = "tPhaseEgy"
FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY: Final = "tnPhaseEgy"
FIELD_CT_A_PHASE_ENERGY: Final = "aPhaseEgy"
FIELD_CT_B_PHASE_ENERGY: Final = "bPhaseEgy"
FIELD_CT_C_PHASE_ENERGY: Final = "cPhaseEgy"
FIELD_CT_A_NEGATIVE_PHASE_ENERGY: Final = "anPhaseEgy"
FIELD_CT_B_NEGATIVE_PHASE_ENERGY: Final = "bnPhaseEgy"
FIELD_CT_C_NEGATIVE_PHASE_ENERGY: Final = "cnPhaseEgy"
CT_PHASE_POWER_PAIRS: Final = (
    (FIELD_CT_A_PHASE_POWER, FIELD_CT_A_NEGATIVE_PHASE_POWER),
    (FIELD_CT_B_PHASE_POWER, FIELD_CT_B_NEGATIVE_PHASE_POWER),
    (FIELD_CT_C_PHASE_POWER, FIELD_CT_C_NEGATIVE_PHASE_POWER),
)
CT_TOTAL_POWER_PAIR: Final = (
    FIELD_CT_TOTAL_PHASE_POWER,
    FIELD_CT_TOTAL_NEGATIVE_PHASE_POWER,
)
CT_POSITIVE_PHASE_POWER_FIELDS: Final = (
    FIELD_CT_A_PHASE_POWER,
    FIELD_CT_B_PHASE_POWER,
    FIELD_CT_C_PHASE_POWER,
)
CT_NEGATIVE_PHASE_POWER_FIELDS: Final = (
    FIELD_CT_A_NEGATIVE_PHASE_POWER,
    FIELD_CT_B_NEGATIVE_PHASE_POWER,
    FIELD_CT_C_NEGATIVE_PHASE_POWER,
)
CT_PHASE_ENERGY_PAIRS: Final = (
    (FIELD_CT_A_PHASE_ENERGY, FIELD_CT_A_NEGATIVE_PHASE_ENERGY),
    (FIELD_CT_B_PHASE_ENERGY, FIELD_CT_B_NEGATIVE_PHASE_ENERGY),
    (FIELD_CT_C_PHASE_ENERGY, FIELD_CT_C_NEGATIVE_PHASE_ENERGY),
)
CT_TOTAL_ENERGY_PAIR: Final = (
    FIELD_CT_TOTAL_PHASE_ENERGY,
    FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
)
CT_ATTRIBUTE_FIELDS: Final = (
    FIELD_SCAN_NAME,
    FIELD_TYPE_NAME,
    FIELD_SUB_TYPE,
    FIELD_DEV_TYPE,
    FIELD_CT_VOLT,
    FIELD_CT_VOLT1,
    FIELD_CT_VOLT2,
    FIELD_CT_VOLT3,
    FIELD_CT_CURRENT,
    FIELD_CT_CURRENT1,
    FIELD_CT_CURRENT2,
    FIELD_CT_CURRENT3,
    FIELD_CT_FREQUENCY,
    FIELD_CT_POWER_FACTOR,
    FIELD_CT_A_PHASE_POWER,
    FIELD_CT_B_PHASE_POWER,
    FIELD_CT_C_PHASE_POWER,
    FIELD_CT_TOTAL_PHASE_POWER,
    FIELD_CT_A_NEGATIVE_PHASE_POWER,
    FIELD_CT_B_NEGATIVE_PHASE_POWER,
    FIELD_CT_C_NEGATIVE_PHASE_POWER,
    FIELD_CT_TOTAL_NEGATIVE_PHASE_POWER,
    FIELD_CT_A_PHASE_ENERGY,
    FIELD_CT_B_PHASE_ENERGY,
    FIELD_CT_C_PHASE_ENERGY,
    FIELD_CT_TOTAL_PHASE_ENERGY,
    FIELD_CT_A_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_B_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_C_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
)

FIELD_TARGET_MODULE_VERSION: Final = "targetModuleVersion"
FIELD_UPGRADE_TYPE: Final = "upgradeType"
FIELD_POWER_PRICE_RESOURCE: Final = "powerPriceResource"
FIELD_LOGIN_ALLOWED: Final = "loginAllowed"

MAIN_PROPERTY_ALIAS_PAIRS: Final = (
    (FIELD_SOC_CHARGE_LIMIT, FIELD_SOC_CHG_LIMIT),
    (FIELD_SOC_DISCHARGE_LIMIT, FIELD_SOC_DISCHG_LIMIT),
)

TASK_PLAN_BODY: Final = FIELD_BODY
TASK_PLAN_TASKS: Final = "tasks"
TIMER_TASK_ACTION_ADD: Final = 1
TIMER_TASK_ACTION_DELETE: Final = 2
TIMER_TASK_ACTION_UPDATE: Final = 3
TIMER_TASK_ACTION_READ: Final = 4
TIMER_TASK_TYPE_SMART_PLUG: Final = 1
TIMER_TASK_TYPE_CUSTOM_MODE: Final = 2
TIMER_TASK_TYPE_TIME_ELEC: Final = 3

# MQTT subdevice routing and mirroring keys documented in PROTOCOL.md §3.
CT_METER_KEYS: Final = frozenset({
    FIELD_CT_POWER,
    FIELD_CT_POWER1,
    FIELD_CT_POWER2,
    FIELD_CT_POWER3,
    FIELD_CT_VOLT,
    FIELD_CT_VOLT1,
    FIELD_CT_VOLT2,
    FIELD_CT_VOLT3,
    FIELD_CT_CURRENT,
    FIELD_CT_CURRENT1,
    FIELD_CT_CURRENT2,
    FIELD_CT_CURRENT3,
    FIELD_CT_FREQUENCY,
    FIELD_CT_POWER_FACTOR,
    FIELD_CT_POWER_FACTOR1,
    FIELD_CT_POWER_FACTOR2,
    FIELD_CT_POWER_FACTOR3,
    FIELD_CT_APPARENT_POWER,
    FIELD_CT_APPARENT_POWER1,
    FIELD_CT_APPARENT_POWER2,
    FIELD_CT_APPARENT_POWER3,
    FIELD_CT_REACTIVE_POWER,
    FIELD_CT_REACTIVE_POWER1,
    FIELD_CT_REACTIVE_POWER2,
    FIELD_CT_REACTIVE_POWER3,
    FIELD_CT_A_PHASE_POWER,
    FIELD_CT_B_PHASE_POWER,
    FIELD_CT_C_PHASE_POWER,
    FIELD_CT_TOTAL_PHASE_POWER,
    FIELD_CT_A_NEGATIVE_PHASE_POWER,
    FIELD_CT_B_NEGATIVE_PHASE_POWER,
    FIELD_CT_C_NEGATIVE_PHASE_POWER,
    FIELD_CT_TOTAL_NEGATIVE_PHASE_POWER,
    FIELD_CT_A_PHASE_ENERGY,
    FIELD_CT_B_PHASE_ENERGY,
    FIELD_CT_C_PHASE_ENERGY,
    FIELD_CT_TOTAL_PHASE_ENERGY,
    FIELD_CT_A_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_B_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_C_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
})
SUBDEVICE_HINT_KEYS: Final = frozenset({
    FIELD_SCAN_NAME,
    FIELD_SUB_TYPE,
    FIELD_DEV_TYPE,
    FIELD_CT_A_PHASE_POWER,
    FIELD_CT_B_PHASE_POWER,
    FIELD_CT_C_PHASE_POWER,
    FIELD_CT_TOTAL_PHASE_POWER,
    FIELD_CT_A_NEGATIVE_PHASE_POWER,
    FIELD_CT_B_NEGATIVE_PHASE_POWER,
    FIELD_CT_C_NEGATIVE_PHASE_POWER,
    FIELD_CT_TOTAL_NEGATIVE_PHASE_POWER,
    FIELD_CT_A_PHASE_ENERGY,
    FIELD_CT_B_PHASE_ENERGY,
    FIELD_CT_C_PHASE_ENERGY,
    FIELD_CT_TOTAL_PHASE_ENERGY,
    FIELD_CT_A_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_B_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_C_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
})
SUBDEVICE_ONLY_PROPERTY_KEYS: Final = frozenset({
    FIELD_SCAN_NAME,
    FIELD_SUB_TYPE,
    FIELD_DEV_TYPE,
    FIELD_DEVICE_SN,
    FIELD_CMD,
    "messageId",
    "funForm",
    "schePhase",
    FIELD_CT_FUN_FORM,
    FIELD_CT_SCHE_PHASE,
    FIELD_COMM_MODE,
    FIELD_COMM_STATE,
    FIELD_IN_PW,
    FIELD_OUT_PW,
    FIELD_CT_A_PHASE_POWER,
    FIELD_CT_B_PHASE_POWER,
    FIELD_CT_C_PHASE_POWER,
    FIELD_CT_TOTAL_PHASE_POWER,
    FIELD_CT_A_NEGATIVE_PHASE_POWER,
    FIELD_CT_B_NEGATIVE_PHASE_POWER,
    FIELD_CT_C_NEGATIVE_PHASE_POWER,
    FIELD_CT_TOTAL_NEGATIVE_PHASE_POWER,
    FIELD_CT_A_PHASE_ENERGY,
    FIELD_CT_B_PHASE_ENERGY,
    FIELD_CT_C_PHASE_ENERGY,
    FIELD_CT_TOTAL_PHASE_ENERGY,
    FIELD_CT_A_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_B_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_C_NEGATIVE_PHASE_ENERGY,
    FIELD_CT_TOTAL_NEGATIVE_PHASE_ENERGY,
})
SUBDEVICE_MAIN_MIRROR_KEYS: Final = frozenset({
    FIELD_IN_GRID_SIDE_PW,
    FIELD_OUT_GRID_SIDE_PW,
    FIELD_GRID_IN_PW,
    FIELD_GRID_OUT_PW,
    FIELD_IN_ONGRID_PW,
    FIELD_OUT_ONGRID_PW,
    FIELD_OTHER_LOAD_PW,
    FIELD_MAX_FEED_GRID,
    FIELD_MAX_GRID_STD_PW,
    FIELD_MAX_OUT_PW,
    FIELD_MAX_INV_STD_PW,
    FIELD_OFF_GRID_TIME,
    FIELD_OFF_GRID_DOWN,
    FIELD_TEMP_UNIT,
    FIELD_WORK_MODEL,
    FIELD_WPS,
    FIELD_WPC,
    FIELD_MINS_INTERVAL,
    FIELD_IS_AUTO_STANDBY,
    FIELD_AUTO_STANDBY,
    FIELD_IS_FOLLOW_METER_PW,
    FIELD_DEFAULT_PW,
    FIELD_STANDBY_PW,
    FIELD_SW_EPS,
    FIELD_SW_EPS_STATE,
    FIELD_SW_EPS_IN_PW,
    FIELD_SW_EPS_OUT_PW,
    FIELD_REBOOT,
    FIELD_ONLINE,
})
SYSTEM_INFO_KEYS: Final = frozenset({
    FIELD_STAT,
    FIELD_ONGRID_STAT,
    FIELD_CT_STAT,
    FIELD_GRID_STATE,
    FIELD_ENERGY_PLAN_PW,
    FIELD_MAX_SYS_OUT_PW,
    FIELD_MAX_SYS_IN_PW,
    FIELD_WPS,
    FIELD_WPC,
    FIELD_WORK_MODEL,
    FIELD_MAX_FEED_GRID,
    FIELD_FUNC_ENABLE,
    FIELD_STANDBY_PW,
    FIELD_OFF_GRID_DOWN,
    FIELD_OFF_GRID_TIME,
    FIELD_IS_AUTO_STANDBY,
    FIELD_TEMP_UNIT,
    FIELD_DEFAULT_PW,
    FIELD_IS_FOLLOW_METER_PW,
})
BATTERY_PACK_HINT_KEYS: Final = frozenset({
    FIELD_BAT_SOC,
    FIELD_CELL_TEMP,
    FIELD_IN_PW,
    FIELD_OUT_PW,
    FIELD_IN_EGY,
    FIELD_OUT_EGY,
    FIELD_RB,
    FIELD_IP,
    FIELD_OP,
    FIELD_VERSION,
    FIELD_CURRENT_VERSION,
    FIELD_UPDATE_STATUS,
    FIELD_IS_FIRMWARE_UPGRADE,
})


# Normalized metadata/series keys used by app-period helpers. Request metadata is
# attached by api.py so entity diagnostics can show the exact documented
# begin/end/dateType range from PROTOCOL.md §2.
APP_REQUEST_META: Final = "_request"
APP_REQUEST_DATE_TYPE: Final = "dateType"
APP_REQUEST_DATE_TYPE_ALT: Final = "date_type"
APP_REQUEST_BEGIN_DATE: Final = "beginDate"
APP_REQUEST_BEGIN_DATE_ALT: Final = "begin_date"
APP_REQUEST_END_DATE: Final = "endDate"
APP_REQUEST_END_DATE_ALT: Final = "end_date"
APP_STAT_UNIT: Final = "unit"
APP_UNIT_KWH: Final = "kwh"
APP_CHART_LABELS: Final = "x"
APP_CHART_SERIES_Y: Final = "y"
APP_CHART_SERIES_Y1: Final = "y1"
APP_CHART_SERIES_Y2: Final = "y2"
APP_CHART_SERIES_Y3: Final = "y3"
APP_CHART_SERIES_Y4: Final = "y4"
APP_CHART_SERIES_Y5: Final = "y5"
APP_CHART_SERIES_Y6: Final = "y6"
APP_HOME_GRID_SERIES_KEYS: Final = (APP_CHART_SERIES_Y1, APP_CHART_SERIES_Y2)
# Date-type values used by the documented app period endpoints. App 2.1.1
# exposes day/week/month/year/total for stat calls; "hour" is not a REST
# dateType and day Recorder buckets are derived from the day curve.
# Date-type values used by the documented app period endpoints.
DATE_TYPE_HOUR: Final = "hour"
DATE_TYPE_DAY: Final = "day"
DATE_TYPE_WEEK: Final = "week"
DATE_TYPE_MONTH: Final = "month"
DATE_TYPE_YEAR: Final = "year"
APP_PERIOD_DATE_TYPES: Final = (
    DATE_TYPE_HOUR,
    DATE_TYPE_DAY,
    DATE_TYPE_WEEK,
    DATE_TYPE_MONTH,
    DATE_TYPE_YEAR,
)
APP_CHART_DATE_TYPES: Final = (DATE_TYPE_WEEK, DATE_TYPE_MONTH, DATE_TYPE_YEAR)

# External statistic buckets. The normal entities stay period totals; these
# bucket names identify HA-recorder series imported from the app chart arrays.
EXTERNAL_STAT_BUCKET_DAY_HOURLY: Final = "day_hourly"
EXTERNAL_STAT_BUCKET_WEEK_DAILY: Final = "week_daily"
EXTERNAL_STAT_BUCKET_MONTH_DAILY: Final = "month_daily"
EXTERNAL_STAT_BUCKET_YEAR_MONTHLY: Final = "year_monthly"
APP_DAY_CHART_BUCKET_LABEL: Final = "hourly app day buckets from 5-minute power curves"
APP_CHART_BUCKET_BY_DATE_TYPE: Final = {
    DATE_TYPE_DAY: EXTERNAL_STAT_BUCKET_DAY_HOURLY,
    DATE_TYPE_WEEK: EXTERNAL_STAT_BUCKET_WEEK_DAILY,
    DATE_TYPE_MONTH: EXTERNAL_STAT_BUCKET_MONTH_DAILY,
    DATE_TYPE_YEAR: EXTERNAL_STAT_BUCKET_YEAR_MONTHLY,
}
APP_CHART_BUCKET_LABEL_BY_DATE_TYPE: Final = {
    DATE_TYPE_DAY: "daily app chart buckets for the current day",
    DATE_TYPE_WEEK: "daily app chart buckets for the current week",
    DATE_TYPE_MONTH: "daily app chart buckets for the current month",
    DATE_TYPE_YEAR: "monthly app chart buckets for the current year",
}
APP_CHART_STAT_PERIODS: Final = tuple(
    (
        date_type,
        APP_CHART_BUCKET_BY_DATE_TYPE[date_type],
        APP_CHART_BUCKET_LABEL_BY_DATE_TYPE[date_type],
    )
    for date_type in APP_CHART_DATE_TYPES
)

# Repair/data-quality diagnostics. These do not change entity values; they only
# surface contradictions between documented app sources. Keep the dict keys
# centralized because coordinator, diagnostics and tests read the same payload.
DATA_QUALITY_LEVEL_WARNING: Final = "warning"
DATA_QUALITY_KEY_LEVEL: Final = "level"
DATA_QUALITY_KEY_REASON: Final = "reason"
DATA_QUALITY_KEY_METRIC_KEY: Final = "metric_key"
DATA_QUALITY_KEY_LABEL: Final = "label"
DATA_QUALITY_KEY_SOURCE_SECTION: Final = "source_section"
DATA_QUALITY_KEY_SOURCE_VALUE: Final = "source_value"
DATA_QUALITY_KEY_REFERENCE_SECTION: Final = "reference_section"
DATA_QUALITY_KEY_REFERENCE_VALUE: Final = "reference_value"
DATA_QUALITY_KEY_SOURCE_REQUEST: Final = "source_request"
DATA_QUALITY_KEY_REFERENCE_REQUEST: Final = "reference_request"
DATA_QUALITY_KEY_SOURCE_CHART_SERIES_KEY: Final = "source_chart_series_key"
DATA_QUALITY_KEY_REFERENCE_CHART_SERIES_KEY: Final = "reference_chart_series_key"
DATA_QUALITY_KEY_TOTAL_METHOD: Final = "total_method"
DATA_QUALITY_REASON_YEAR_LESS_THAN_MONTH: Final = "year_less_than_month"
DATA_QUALITY_REASON_YEAR_LESS_THAN_WEEK: Final = "year_less_than_week"
DATA_QUALITY_REASON_MONTH_LESS_THAN_WEEK: Final = "month_less_than_week"
DATA_QUALITY_REASON_LIFETIME_LESS_THAN_YEAR: Final = "lifetime_less_than_year"
DATA_QUALITY_REASON_WEEK_LESS_THAN_DAY: Final = "week_less_than_day"
DATA_QUALITY_REASON_ZERO_UNCONFIRMED: Final = "zero_value_not_confirmed_by_adjacent_period"
DATA_QUALITY_REPAIR_EXAMPLE_LIMIT: Final = 3
REPAIR_ISSUE_APP_DATA_INCONSISTENCY: Final = "app_data_inconsistency"
REPAIR_TRANSLATION_APP_DATA_INCONSISTENCY: Final = "app_data_inconsistency"

# Internal metadata attached to corrected app statistic payloads. The raw cloud
# values remain visible in diagnostics while entity states use the guarded
# values produced from documented month endpoints.
APP_YEAR_BACKFILL_META: Final = "_year_month_backfill"
APP_TOTAL_GUARD_META: Final = "_total_lower_bound_guard"
APP_SAVINGS_CALC_META: Final = "_savings_calculation"

# Section prefixes and chart-series keys documented by PROTOCOL.md §2.
APP_SECTION_PV_STAT: Final = "device_pv_stat"
APP_SECTION_HOME_STAT: Final = "device_home_stat"
APP_SECTION_BATTERY_STAT: Final = "device_battery_stat"
APP_SECTION_HOME_TRENDS: Final = "home_trends"
APP_SECTION_PV_TRENDS: Final = "pv_trends"
APP_SECTION_BATTERY_TRENDS: Final = "battery_trends"
APP_SECTION_CT_STAT: Final = "device_ct_stat"
APP_SECTION_EPS_STAT: Final = "device_eps_stat"
APP_SECTION_TODAY_ENERGY: Final = "device_today_energy"

APP_STAT_TOTAL_SOLAR_ENERGY: Final = "totalSolarEnergy"
APP_STAT_PV1_ENERGY: Final = "pv1Egy"
APP_STAT_PV2_ENERGY: Final = "pv2Egy"
APP_STAT_PV3_ENERGY: Final = "pv3Egy"
APP_STAT_PV4_ENERGY: Final = "pv4Egy"
APP_STAT_TOTAL_IN_GRID_ENERGY: Final = "totalInGridEnergy"
APP_STAT_TOTAL_OUT_GRID_ENERGY: Final = "totalOutGridEnergy"
APP_STAT_TOTAL_CHARGE: Final = "totalCharge"
APP_STAT_TOTAL_DISCHARGE: Final = "totalDischarge"
APP_STAT_TOTAL_CT_INPUT_ENERGY: Final = "totalInCtEnergy"
APP_STAT_TOTAL_CT_OUTPUT_ENERGY: Final = "totalOutCtEnergy"
# EpsStatApi$Bean per docs/html/jackery_http_model_fields_v2.html — EPS /
# off-grid in/out totals for a single dateType payload.
APP_STAT_TOTAL_IN_EPS_ENERGY: Final = "totalInEpsEnergy"
APP_STAT_TOTAL_OUT_EPS_ENERGY: Final = "totalOutEpsEnergy"
# TodayEnergyApi$Bean per PROTOCOL.md §2.4 — flat today KPI bean:
# ``de`` = feed-in (Einspeisung), ``dg`` = grid import (Bezug),
# ``dh`` = home load (Hausverbrauch), ``ds`` = battery energy
# (Batterie-Energie). All four are doubles in kWh.
APP_STAT_TODAY_FEED_IN_ENERGY: Final = "de"
APP_STAT_TODAY_GRID_IMPORT_ENERGY: Final = "dg"
APP_STAT_TODAY_HOME_LOAD_ENERGY: Final = "dh"
APP_STAT_TODAY_BATTERY_ENERGY: Final = "ds"
APP_STAT_TOTAL_TREND_CHARGE_ENERGY: Final = "totalChgEgy"
APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY: Final = "totalDisChgEgy"
APP_STAT_TOTAL_HOME_ENERGY: Final = "totalHomeEgy"
APP_STAT_TODAY_LOAD: Final = "todayLoad"
APP_STAT_TOTAL_GENERATION: Final = "totalGeneration"
APP_STAT_TOTAL_REVENUE: Final = "totalRevenue"
# PvStatApi$Bean per docs/html/jackery_http_model_fields_v2.html — separate
# from systemStatistic.totalRevenue (latter is the lifetime KPI).
# totalSolarRevenue is the periodic Jackery cloud "PV revenue" value tied
# to that period's PV energy and the configured tariff (singlePrice or
# dynamic). Currency arrives in PvStatApi$Bean.currency.
APP_STAT_TOTAL_SOLAR_REVENUE: Final = "totalSolarRevenue"
APP_STAT_TOTAL_CARBON: Final = "totalCarbon"
APP_DEVICE_STAT_PV_ENERGY: Final = "pvEgy"
APP_DEVICE_STAT_BATTERY_CHARGE: Final = "batChgEgy"
APP_DEVICE_STAT_BATTERY_DISCHARGE: Final = "batDisChgEgy"
APP_DEVICE_STAT_ONGRID_INPUT: Final = "inOngridEgy"
APP_DEVICE_STAT_ONGRID_OUTPUT: Final = "outOngridEgy"
APP_DEVICE_STAT_BATTERY_TO_GRID: Final = "batOtGridEgy"
APP_DEVICE_STAT_PV_TO_BATTERY: Final = "pvOtBatEgy"
APP_DEVICE_STAT_ONGRID_TO_BATTERY: Final = "ongridOtBatEgy"
APP_DEVICE_STAT_EPS_INPUT: Final = "inEpsEgy"
APP_DEVICE_STAT_EPS_OUTPUT: Final = "outEpsEgy"
APP_DEVICE_STAT_AC_TO_BATTERY: Final = "acOtBatEgy"
APP_DEVICE_STAT_AC_TO_ONGRID: Final = "acOtOngridEgy"
APP_DEVICE_STAT_BATTERY_TO_AC: Final = "batOtAcEgy"
APP_DEVICE_STAT_ONGRID_TO_AC_LOAD: Final = "ongridOtAcLoadEgy"
APP_DEVICE_STAT_PV_TO_AC: Final = "pvOtAcEgy"
APP_DEVICE_STAT_PV_TO_ONGRID: Final = "pvOtOngridEgy"

APP_CHART_METRIC_KEY_BY_SECTION_PREFIX: Final = {
    APP_SECTION_PV_STAT: {
        APP_STAT_TOTAL_SOLAR_ENERGY: "pv_energy",
        APP_STAT_PV1_ENERGY: "pv1_energy",
        APP_STAT_PV2_ENERGY: "pv2_energy",
        APP_STAT_PV3_ENERGY: "pv3_energy",
        APP_STAT_PV4_ENERGY: "pv4_energy",
    },
    APP_SECTION_HOME_STAT: {
        APP_STAT_TOTAL_IN_GRID_ENERGY: "device_ongrid_input_energy",
        APP_STAT_TOTAL_OUT_GRID_ENERGY: "device_ongrid_output_energy",
    },
    APP_SECTION_BATTERY_STAT: {
        APP_STAT_TOTAL_CHARGE: "battery_charge_energy",
        APP_STAT_TOTAL_DISCHARGE: "battery_discharge_energy",
    },
    APP_SECTION_CT_STAT: {
        APP_STAT_TOTAL_CT_INPUT_ENERGY: "ct_input_energy",
        APP_STAT_TOTAL_CT_OUTPUT_ENERGY: "ct_output_energy",
    },
    APP_SECTION_EPS_STAT: {
        APP_STAT_TOTAL_IN_EPS_ENERGY: "eps_input_energy",
        APP_STAT_TOTAL_OUT_EPS_ENERGY: "eps_output_energy",
    },
    APP_SECTION_HOME_TRENDS: {
        APP_STAT_TOTAL_HOME_ENERGY: "home_energy",
    },
}

APP_CHART_STAT_METRICS: Final = (
    (APP_SECTION_PV_STAT, APP_STAT_TOTAL_SOLAR_ENERGY, "pv_energy", "PV energy"),
    (APP_SECTION_PV_STAT, APP_STAT_PV1_ENERGY, "pv1_energy", "PV1 energy"),
    (APP_SECTION_PV_STAT, APP_STAT_PV2_ENERGY, "pv2_energy", "PV2 energy"),
    (APP_SECTION_PV_STAT, APP_STAT_PV3_ENERGY, "pv3_energy", "PV3 energy"),
    (APP_SECTION_PV_STAT, APP_STAT_PV4_ENERGY, "pv4_energy", "PV4 energy"),
    (
        APP_SECTION_HOME_STAT,
        APP_STAT_TOTAL_IN_GRID_ENERGY,
        "device_ongrid_input_energy",
        "Device grid-side input energy",
    ),
    (
        APP_SECTION_HOME_STAT,
        APP_STAT_TOTAL_OUT_GRID_ENERGY,
        "device_ongrid_output_energy",
        "Device grid-side output energy",
    ),
    (
        APP_SECTION_BATTERY_STAT,
        APP_STAT_TOTAL_CHARGE,
        "battery_charge_energy",
        "Battery charge energy",
    ),
    (
        APP_SECTION_BATTERY_STAT,
        APP_STAT_TOTAL_DISCHARGE,
        "battery_discharge_energy",
        "Battery discharge energy",
    ),
    (
        APP_SECTION_CT_STAT,
        APP_STAT_TOTAL_CT_INPUT_ENERGY,
        "ct_input_energy",
        "CT grid import energy",
    ),
    (
        APP_SECTION_CT_STAT,
        APP_STAT_TOTAL_CT_OUTPUT_ENERGY,
        "ct_output_energy",
        "CT grid export energy",
    ),
    (
        APP_SECTION_EPS_STAT,
        APP_STAT_TOTAL_IN_EPS_ENERGY,
        "eps_input_energy",
        "EPS input energy",
    ),
    (
        APP_SECTION_EPS_STAT,
        APP_STAT_TOTAL_OUT_EPS_ENERGY,
        "eps_output_energy",
        "EPS output energy",
    ),
    (
        APP_SECTION_HOME_TRENDS,
        APP_STAT_TOTAL_HOME_ENERGY,
        "home_energy",
        "Home energy",
    ),
)

# Select-option mappings from the documented app state machines. Option strings
# are translation keys; integer values are sent back to Jackery unchanged.
WORK_MODE_TO_OPTION: Final = {
    8: "ai_smart",
    2: "self_use",
    4: "custom",
    7: "tariff",
}
WORK_MODE_READ_ALIASES: Final = {5: "tariff"}
TEMP_UNIT_TO_OPTION: Final = {0: "celsius", 1: "fahrenheit"}
AUTO_OFF_HOURS: Final = (2, 8, 12, 24)
STORM_MINUTES_DEFAULT: Final = tuple(
    [hour * 60 for hour in range(1, 25)] + [2880, 4320]
)
# Storm-warning lead-times below this value are treated as firmware sentinels,
# not real user settings. The Jackery app dropdown starts at 60 minutes
# (STORM_MINUTES_DEFAULT minimum), so values like ``wpc=1`` / ``minsInterval=1``
# coming from an uninitialized weather plan must be dropped — otherwise the HA
# select adds an extra ``min_1`` option that has no translation entry.
STORM_MINUTES_MIN_VALID: Final = 60
PRICE_MODE_TO_OPTION: Final = {1: "dynamic", 2: "single"}

# Diagnostics redaction keys. Keep this set broad because app/MQTT payloads can
# include personal account data, device IDs, locations and tariff credentials.
REDACTED_VALUE: Final = "**REDACTED**"

# Optional diagnostic payload debug log. It is redacted and size-limited, but it
# still contains detailed raw cloud/MQTT value shapes for troubleshooting parser
# and mapping bugs.
PAYLOAD_DEBUG_LOG_FILENAME: Final = "jackery_solarvault_payload_debug.jsonl"
PAYLOAD_DEBUG_LOGGER_NAME: Final = f"custom_components.{DOMAIN}.payload_debug"
PAYLOAD_DEBUG_LOG_MAX_BYTES: Final = 2_000_000
PAYLOAD_DEBUG_LOG_BACKUP_SUFFIX: Final = ".1"
# Per-channel throttle window for payload-debug records. Stops the JSONL log
# from growing at the MQTT push-rate when a user enabled the dedicated DEBUG
# logger and forgot to disable it. Genuine first-of-its-kind records are still
# emitted immediately because the dedup check runs *before* the throttle.
PAYLOAD_DEBUG_THROTTLE_SEC: Final = 60
REDACT_KEYS: Final = {
    "p",
    "s",
    "password",
    "username",
    "ssid",
    "bssid",
    CONF_MQTT_MAC_ID,
    CONF_REGION_CODE,
    FIELD_MAC_ID,
    FIELD_TOKEN,
    FIELD_MQTT_PASSWORD,
    FIELD_DEVICE_ID,
    FIELD_SYSTEM_ID,
    FIELD_DEVICE_NAME,
    FIELD_SYSTEM_NAME,
    "mqttPassword",
    FIELD_DEVICE_SN,
    FIELD_DEV_SN,
    FIELD_SN,
    FIELD_SYSTEM_SN,
    FIELD_WNAME,
    FIELD_MAC,
    FIELD_WIP,
    "bluetoothKey",
    FIELD_DEVICE_SECRET,
    FIELD_RANDOM_SALT,
    "phone",
    "mobPhone",
    "email",
    "mail",
    FIELD_ACCOUNT,
    "accountName",
    "accountEmail",
    "bindEmail",
    "bindEmailAddress",
    "bindPhone",
    "avatar",
    "appUserName",
    "nickname",
    FIELD_USER_ID,
    MQTT_CREDENTIAL_CLIENT_ID,
    MQTT_CREDENTIAL_PASSWORD,
    MQTT_CREDENTIAL_USER_ID,
    MQTT_CREDENTIAL_USERNAME,
    "clientId",
    "platformToken",
    "platformApiParam",
    "contractAuth",
    "powerPriceResource",
    "address",
    "addressDetail",
    FIELD_TIMEZONE,
    FIELD_COUNTRY,
    FIELD_COUNTRY_CODE,
    FIELD_GRID_STANDARD,
    FIELD_LONGITUDE,
    FIELD_LATITUDE,
    FIELD_REGION,
    "base64_encoded",
    "body_preview",
    "mqttUser",
    "mqttUsername",
    "mqttClientId",
    "mqtt_user",
    "mqtt_username",
    "raw_bytes",
    "raw_hex",
    "trailer_hex",
}

# MQTT client metadata and topic layout from PROTOCOL.md §3.
MQTT_CLIENT_LIBRARY: Final = "aiomqtt"
MQTT_CLIENT_ID_SUFFIX: Final = "APP"
MQTT_USERNAME_SEPARATOR: Final = "@"
MQTT_MAC_ID_PREFIX: Final = "2"
MQTT_TOPIC_PREFIX: Final = "hb/app"
MQTT_TOPIC_DEVICE: Final = "device"
MQTT_TOPIC_ALERT: Final = "alert"
MQTT_TOPIC_CONFIG: Final = "config"
MQTT_TOPIC_NOTICE: Final = "notice"
MQTT_TOPIC_COMMAND: Final = "command"
# Documented app topic; integration does not publish here.
MQTT_TOPIC_ACTION: Final = "action"
MQTT_TOPIC_SUFFIXES: Final = (
    MQTT_TOPIC_DEVICE,
    MQTT_TOPIC_ALERT,
    MQTT_TOPIC_CONFIG,
    MQTT_TOPIC_NOTICE,
)
MQTT_CONNACK_REASONS: Final = {
    0: "Connection accepted",
    1: "unacceptable protocol version",
    2: "identifier rejected",
    3: "server unavailable",
    4: "bad user name or password",
    5: "not authorized",
    134: "bad user name or password",
    135: "not authorized",
    136: "server unavailable",
}

# Subdevice type markers. devType=1 is a
# BatteryPackSub query target. All other concrete devType values are excluded
# from battery-pack auto-detection so future grouped payloads cannot be
# mistaken for add-on batteries.
SUBDEVICE_TYPE_COMBINE: Final = "2"
SUBDEVICE_TYPE_SMART_METER: Final = "3"
SUBDEVICE_TYPE_METER_HEAD: Final = "4"
SUBDEVICE_TYPE_METER: Final = "5"
SUBDEVICE_TYPE_SOCKET: Final = "6"
SUBDEVICE_TYPE_BREAKER: Final = "7"
SUBDEVICE_TYPE_SMOKE: Final = "8"
SUBDEVICE_TYPE_TEMP_HUMIDITY: Final = "9"
SUBDEVICE_TYPE_WATER_LEAK: Final = "10"
SMART_METER_SUBTYPE: Final = SUBDEVICE_TYPE_COMBINE
NON_BATTERY_SUBDEVICE_TYPES: Final = frozenset({
    SUBDEVICE_TYPE_COMBINE,
    SUBDEVICE_TYPE_SMART_METER,
    SUBDEVICE_TYPE_METER_HEAD,
    SUBDEVICE_TYPE_METER,
    SUBDEVICE_TYPE_SOCKET,
    SUBDEVICE_TYPE_BREAKER,
    SUBDEVICE_TYPE_SMOKE,
    SUBDEVICE_TYPE_TEMP_HUMIDITY,
    SUBDEVICE_TYPE_WATER_LEAK,
})

# Payload sections that must survive a slow HTTP refresh when they were last
# updated via MQTT. These are integration payload keys, not MQTT message types.
PRESERVED_FAST_PAYLOAD_KEYS: Final = (
    PAYLOAD_ALARM,
    PAYLOAD_CT_METER,
    PAYLOAD_METER_HEADS,
    PAYLOAD_SMART_PLUGS,
    PAYLOAD_WEATHER_PLAN,
    PAYLOAD_TASK_PLAN,
    PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
    PAYLOAD_LIFETIME_COUNTERS,
    PAYLOAD_NOTICE,
    PAYLOAD_MQTT_LAST,
)

# Service names and payload fields from services.yaml.
SERVICE_RENAME_SYSTEM: Final = "rename_system"
SERVICE_REFRESH_WEATHER_PLAN: Final = "refresh_weather_plan"
SERVICE_DELETE_STORM_ALERT: Final = "delete_storm_alert"
# Experimental — see coordinator.async_set/async_query_third_party_mqtt_config.
SERVICE_SET_THIRD_PARTY_MQTT_CONFIG: Final = "set_third_party_mqtt_config"
SERVICE_QUERY_THIRD_PARTY_MQTT_CONFIG: Final = "query_third_party_mqtt_config"
SERVICE_SEND_BLE_COMMAND: Final = "send_ble_command"
SERVICE_SEND_DEVICE_SCHEDULE: Final = "send_device_schedule"
SERVICE_FIELD_ACTION_ID: Final = "action_id"
SERVICE_FIELD_SYSTEM_ID: Final = "system_id"
SERVICE_FIELD_NEW_NAME: Final = "new_name"
SERVICE_FIELD_DEVICE_ID: Final = "device_id"
SERVICE_FIELD_ALERT_ID: Final = "alert_id"
SERVICE_FIELD_CMD: Final = "cmd"
SERVICE_FIELD_FLAGS: Final = "flags"
SERVICE_FIELD_BODY: Final = "body"
SERVICE_FIELD_WAIT_FOR_ACK: Final = "wait_for_ack"
SERVICE_FIELD_ACK_TIMEOUT: Final = "ack_timeout"
SERVICE_FIELD_ENABLE: Final = "enable"
SERVICE_FIELD_IP: Final = "ip"
SERVICE_FIELD_PORT: Final = "port"
SERVICE_FIELD_USERNAME: Final = "username"
SERVICE_FIELD_PASSWORD: Final = "password"
SERVICE_FIELD_TOKEN: Final = "token"
SERVICE_NUMERIC_ID_PATTERN: Final = r"^\s*[0-9]+\s*$"
SERVICE_NON_EMPTY_TEXT_PATTERN: Final = r".*\S.*"

# Entity-registry cleanup suffixes for removed or option-controlled entities.
SMART_METER_DERIVED_SENSOR_SUFFIXES: Final = {
    "_smart_meter_net_import_power",
    "_smart_meter_net_export_power",
    "_smart_meter_gross_phase_import_power",
    "_smart_meter_gross_phase_export_power",
    "_smart_meter_gross_phase_flow_power",
    "_home_consumption_power",
}
CALCULATED_POWER_SENSOR_SUFFIXES: Final = {
    "_battery_net_power",
    "_battery_stack_net_power",
    "_grid_net_power",
}
SAVINGS_DETAIL_SENSOR_SUFFIXES: Final = {
    "_savings_calculated_total",
    "_savings_energy",
    "_savings_price",
    "_savings_battery_loss_year_energy",
    "_savings_conversion_loss_year_energy",
    "_savings_pv_residual_year_energy",
    "_conversion_loss_power",
}
DUPLICATE_BINARY_SENSOR_SUFFIXES: Final = {"_eps_enabled"}
CT_PERIOD_SENSOR_SUFFIXES: Final = {
    "_smart_meter_import_day_energy",
    "_smart_meter_import_week_energy",
    "_smart_meter_import_month_energy",
    "_smart_meter_import_year_energy",
    "_smart_meter_export_day_energy",
    "_smart_meter_export_week_energy",
    "_smart_meter_export_month_energy",
    "_smart_meter_export_year_energy",
}
# Documentation-only sensor suffix sets. These constants are intentionally
# kept (and exercised by source-only contract tests in tests/test_stat_metadata.py)
# so the historical sensor migrations remain discoverable when reviewing diffs.
# The sets are not used at runtime — they are deliberately dead code that
# documents which sensor surfaces have been removed in past releases.
LEGACY_PV_TODAY_SENSOR_SUFFIX: Final = "_pv_today_energy"
SYSTEM_PV_TODAY_SENSOR_SUFFIX: Final = "_system_pv_today_energy"
STALE_PERIOD_SENSOR_SUFFIXES: Final = frozenset({
    "_grid_import_day_energy",
    "_grid_import_week_energy",
    "_grid_import_month_energy",
    "_grid_import_year_energy",
    "_grid_export_day_energy",
    "_grid_export_week_energy",
    "_grid_export_month_energy",
    "_grid_export_year_energy",
})
STALE_ENERGY_HELPER_PREFIX: Final = "sensor.energy_"
STALE_NET_POWER_SUFFIX: Final = "_net_power"
STALE_HELPER_VENDOR_TOKENS: Final = ("solarvault", "jackery")
FORMER_DISABLED_APP_SENSOR_SUFFIXES: Final = frozenset({
    "_eps_in_power",
    "_eps_out_power",
    "_stack_in_power",
    "_stack_out_power",
    "_mac_address",
    "_eth_port",
    "_ability_bits",
    "_max_iot_num",
    "_eps_switch_state",
    "_reboot_flag",
    "_system_state",
    "_ongrid_state",
    "_ct_state",
    "_grid_state",
    "_max_system_output_power",
    "_max_system_input_power",
    "_standby_power",
    "_energy_plan_power",
    "_charge_plan_power",
    "_function_enable_flags",
})
NON_APP_DIAGNOSTIC_SENSOR_SUFFIXES: Final = frozenset({
    "_last_online",
    "_last_offline",
    "_last_update",
    "_activation_date",
    "_grid_standard",
    "_country_code",
    "_timezone",
    "_latitude",
    "_longitude",
    "_raw_properties",
    "_weather_plan",
    "_task_plan",
})
REMOVED_SENSOR_SUFFIXES: Final = {
    "_grid_side_in_power",
    "_grid_side_out_power",
    "_max_grid_power",
    "_today_battery_charge",
    "_today_battery_discharge",
    "_today_generation",
    "_savings_pv_year_energy",
    "_savings_device_grid_input_year_energy",
    "_savings_device_grid_output_year_energy",
    "_savings_device_grid_net_output_year_energy",
    "_savings_basis_ac_year_energy",
    "_savings_home_consumption_year_energy",
    "_savings_ct_public_export_year_energy",
    "_savings_battery_charge_year_energy",
    "_savings_battery_discharge_year_energy",
    "_savings_pv_not_savings_year_energy",
    "_savings_pv_surplus_loss_year_energy",
    "_smart_meter_import_today_energy",
    "_smart_meter_export_today_energy",
}

PLATFORMS: Final = [
    "sensor",
    "binary_sensor",
    "switch",
    "text",
    "number",
    "select",
    "button",
]

# --- MQTT message types and command IDs -------------------------------------
# The app protocol transports most writes as MQTT messageType/cmd/actionId triples.
MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE: Final = "DevicePropertyChange"
MQTT_MESSAGE_CONTROL_COMBINE: Final = "ControlCombine"
MQTT_MESSAGE_QUERY_COMBINE_DATA: Final = "QueryCombineData"
MQTT_MESSAGE_QUERY_DEVICE_PROPERTY: Final = "QueryDeviceProperty"
MQTT_MESSAGE_UPLOAD_COMBINE_DATA: Final = "UploadCombineData"
MQTT_MESSAGE_UPLOAD_INCREMENTAL_COMBINE_DATA: Final = "UploadIncrementalCombineData"
MQTT_MESSAGE_UPLOAD_WEATHER_PLAN: Final = "UploadWeatherPlan"
MQTT_MESSAGE_QUERY_WEATHER_PLAN: Final = "QueryWeatherPlan"
MQTT_MESSAGE_SEND_WEATHER_ALERT: Final = "SendWeatherAlert"
MQTT_MESSAGE_CANCEL_WEATHER_ALERT: Final = "CancelWeatherAlert"
MQTT_MESSAGE_UPLOAD_DEVICE_ALERT: Final = "UploadDeviceAlert"
MQTT_MESSAGE_DOWNLOAD_DEVICE_SCHEDULE: Final = "DownloadDeviceSchedule"
MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY: Final = "QuerySubDeviceGroupProperty"
MQTT_MESSAGE_CONTROL_SUB_DEVICE: Final = "ControlSubDevice"
# Third-party MQTT bridge config — actionId 3046/3047 per
MQTT_MESSAGE_THIRD_PARTY_MQTT_CONFIG: Final = "ThirdPartMQTTConfig"
MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG: Final = "QueryThirdPartMQTTConfig"
MQTT_MESSAGE_QUERY_WIFI_CONFIG: Final = "QueryWifiConfig"

MQTT_CMD_NONE: Final = 0
MQTT_CMD_READ_WIFI_LIST: Final = 1
MQTT_CMD_WRITE_WIFI_INFO: Final = 2
MQTT_CMD_SEND_TIME_ZONE: Final = 3
MQTT_CMD_QUERY_WEATHER_PLAN: Final = 23
MQTT_CMD_GET_TIME_ZONE: Final = 22
MQTT_CMD_SYNC_MQTT_CONNECT_INFO: Final = 99
MQTT_CMD_GET_DEVICE_OTA_VERSION: Final = 100
MQTT_CMD_QUERY_DEVICE_PROPERTY: Final = 106
MQTT_CMD_DEVICE_PROPERTY_CHANGE: Final = 107
MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY: Final = 110
MQTT_CMD_CONTROL_SUB_DEVICE: Final = 111
MQTT_CMD_QUERY_COMBINE_DATA: Final = 120
MQTT_CMD_CONTROL_COMBINE: Final = 121
MQTT_CMD_UPLOAD_DEVICE_ALERT: Final = 122
# Third-party MQTT bridge cmd values (HomeCmdAction.smali bleMsgType).
MQTT_CMD_DOWNLOAD_DEVICE_SCHEDULE: Final = 112
MQTT_CMD_THIRD_PARTY_MQTT_CONFIG: Final = 113
MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG: Final = 114
MQTT_CMD_QUERY_WIFI_CONFIG: Final = 124
MQTT_CMD_SYNC_GRID_STANDARD: Final = 105

# --- MQTT action IDs (Jackery cloud command/upload protocol) ----------------
# These IDs are sent in MQTT command payloads' `actionId` field. Keep them
# aligned with the MQTT command tables

# Individual action IDs used in outbound commands:
# 3028 DevicePropertyChange carries BOTH SOC limits (chg + dischg) in one
# frame. The integration must not send a separate frame for the charge limit
# alone — verified against the official app via Frida capture (2026-05-14).
# Verified against the Jackery app smali ``HomeCmdAction.smali``: a single
# actionId ``SET_CHARGE_DISCHARGE_LINE = 3028`` carries both SOC limits in
# one DevicePropertyChange frame (body has ``socChgLimit`` and
# ``socDischgLimit`` together).
ACTION_ID_SOC_LIMITS: Final = 3028  # cmd=107 DevicePropertyChange (chg + dischg)
ACTION_ID_MAX_FEED_GRID: Final = 3029  # cmd=121 ControlCombine (800W rule)
# 3038 is DevicePropertyChange (cmd=107), NOT ControlCombine
ACTION_ID_MAX_OUT_PW: Final = 3038  # cmd=107 DevicePropertyChange
ACTION_ID_AUTO_STANDBY: Final = 3021  # cmd=121 ControlCombine
ACTION_ID_WORK_MODEL: Final = 3027  # cmd=121 ControlCombine
ACTION_ID_OFF_GRID_DOWN: Final = 3039  # cmd=121 ControlCombine
ACTION_ID_OFF_GRID_TIME: Final = 3040  # cmd=121 ControlCombine
ACTION_ID_TEMP_UNIT: Final = 3041  # cmd=121 ControlCombine
ACTION_ID_DEFAULT_PW: Final = 3043  # cmd=121 ControlCombine
ACTION_ID_FOLLOW_METER_PW: Final = 3044  # cmd=121 ControlCombine
ACTION_ID_QUERY_DEVICE_PROPERTY: Final = 3011  # cmd=106 QueryDeviceProperty
ACTION_ID_QUERY_COMBINE_DATA: Final = 3019  # cmd=120 QueryCombineData
ACTION_ID_QUERY_WEATHER_PLAN: Final = 3020  # cmd=23 QueryWeatherPlan
# 3022 = EPS-Switch (swEps 0/1), 3023 = Standby mode (autoStandby 1/2).
# Earlier const swapped these IDs and aliased STANDBY to EPS
ACTION_ID_EPS_ENABLED: Final = 3022  # cmd=107 DevicePropertyChange (EPS toggle)
ACTION_ID_STANDBY: Final = 3023  # cmd=107 DevicePropertyChange (standby mode 1/2)
ACTION_ID_CT_PHASE: Final = 3026  # cmd=111 ControlSubDevice (CT phase 1..3, 4=combined)
ACTION_ID_REBOOT_DEVICE: Final = 3030  # cmd=107 DevicePropertyChange
ACTION_ID_STORM_MINUTES: Final = 3034  # cmd=*** SendWeatherAlert
ACTION_ID_DELETE_STORM_ALERT: Final = 3035  # cmd=*** CancelWeatherAlert
ACTION_ID_STORM_WARNING: Final = 3036  # cmd=121 ControlCombine
ACTION_ID_FAULT_ALARM_REPORT: Final = 3042  # cmd=122 UploadDeviceAlert
# Ceach ControlSubDevice intent has its own actionId — 3024 for the socket
# on/off toggle (``SUB_CONTROL_SOCKET_SWITCH``), 3025 for the priority
# toggle (``SUB_CONTROL_SOCKET_PRI_ENABLE``), 3026 for the CT phase setter
# (``SUB_SET_CT_SCHEDULE_PHASE``). They are not consolidated into a single
# actionId despite sharing cmd=111.
ACTION_ID_CONTROL_SOCKET_SWITCH: Final = (
    3024  # cmd=111 ControlSubDevice (Smart-Plug sysSwitch on/off)
)
ACTION_ID_CONTROL_SOCKET_PRIORITY: Final = (
    3025  # cmd=111 ControlSubDevice (socketPri toggle)
)
ACTION_ID_SUBDEVICE_3014: Final = (
    3014  # cmd=110 QuerySubDeviceGroupProperty, battery packs
)
ACTION_ID_SUBDEVICE_3031: Final = (
    3031  # cmd=110 QuerySubDeviceGroupProperty, CT/smart meter
)
ACTION_ID_SUBDEVICE_3032: Final = (
    3032  # cmd=110 QuerySubDeviceGroupProperty, smart plug / socket
)
ACTION_ID_SUBDEVICE_3033: Final = (
    3033  # cmd=110 QuerySubDeviceGroupProperty, meter head
)
ACTION_ID_SUBDEVICE_3037: Final = (
    3037  # cmd=110 QuerySubDeviceGroupProperty, combined subdevices
)
# Third-party MQTT bridge — actionId 3046/3047
ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG: Final = 3046  # cmd=113 ThirdPartMQTTConfig
ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG: Final = (
    3047  # cmd=114 QueryThirdPartMQTTConfig
)
ACTION_ID_QUERY_WIFI_CONFIG: Final = 3045  # cmd=124 QueryWifiConfig

# Early app maintenance/read commands from HomeCmdAction.smali. These use the
# same DevicePropertyChange message type as live properties, so the coordinator
# routes their responses into dedicated buckets before the generic property merge.
ACTION_ID_READ_WIFI_LIST: Final = 3001  # cmd=1 DevicePropertyChange
ACTION_ID_WRITE_WIFI_INFO: Final = 3002  # cmd=2 DevicePropertyChange
ACTION_ID_SEND_TIME_ZONE: Final = 3003  # cmd=3 DevicePropertyChange
ACTION_ID_GET_TIME_ZONE: Final = 3004  # cmd=22 DevicePropertyChange
ACTION_ID_SYNC_MQTT_CONNECT_INFO: Final = 3005  # cmd=99 DevicePropertyChange
ACTION_ID_GET_DEVICE_OTA_VERSION: Final = 3006  # cmd=100 DevicePropertyChange
ACTION_ID_SYNC_GRID_STANDARD: Final = 3010  # cmd=105 DevicePropertyChange
ACTION_ID_TIMER_TASK_ADD: Final = 3015  # cmd=112 DownloadDeviceSchedule
ACTION_ID_TIMER_TASK_DELETE: Final = 3016  # cmd=112 DownloadDeviceSchedule
ACTION_ID_TIMER_TASK_UPDATE: Final = 3017  # cmd=112 DownloadDeviceSchedule
ACTION_ID_TIMER_TASK_READ: Final = 3018  # cmd=112 DownloadDeviceSchedule

# Third-party MQTT bridge body keys per ``ThirdPartyMqttBody.smali``.
FIELD_THIRD_PARTY_MQTT_ENABLE: Final = "enable"
FIELD_THIRD_PARTY_MQTT_IP: Final = "ip"
FIELD_THIRD_PARTY_MQTT_PORT: Final = "port"
FIELD_THIRD_PARTY_MQTT_USERNAME: Final = "userName"
FIELD_THIRD_PARTY_MQTT_PASSWORD: Final = "password"
FIELD_THIRD_PARTY_MQTT_TOKEN: Final = "token"

# Per-device BLE AES key — base64-encoded 32-byte value from the device entry
# in ``/v1/device/system/list``. Used to encrypt/decrypt the DFED-framed BLE
# packets (PROTOCOL.md §14, ``bb/c`` helper). Already in REDACT_KEYS.
FIELD_BLUETOOTH_KEY: Final = "bluetoothKey"

# Subdevice ``devType`` values from the Jackery app's ``HomeSubDeviceType``
# enum (one ordinal per type, 1..10). The Home Assistant integration uses the
# numeric value as the ``devType`` body field for QuerySubDeviceGroupProperty
# bodies and to route inbound UploadSubDeviceGroupProperty payloads.
SUBDEVICE_DEV_TYPE_BATTERY_PACK: Final = 1
SUBDEVICE_DEV_TYPE_COMBO: Final = 2
SUBDEVICE_DEV_TYPE_CT: Final = 3
SUBDEVICE_DEV_TYPE_METER_HEAD: Final = 4
SUBDEVICE_DEV_TYPE_METER: Final = 5
SUBDEVICE_DEV_TYPE_SOCKET: Final = 6
SUBDEVICE_DEV_TYPE_BREAKER: Final = 7
SUBDEVICE_DEV_TYPE_SMOKE: Final = 8
SUBDEVICE_DEV_TYPE_TEMP_HUMIDITY: Final = 9
SUBDEVICE_DEV_TYPE_WATER_LEAK: Final = 10

# The app enum defines devTypes 5 and 7..10, but the SolarVault MQTT model
# exposes no matching READ_SUB_DEVICE_* actionId or HomeSubBody array for them.
SUBDEVICE_DEV_TYPES_WITH_QUERY_ACTION: Final = frozenset({
    SUBDEVICE_DEV_TYPE_BATTERY_PACK,
    SUBDEVICE_DEV_TYPE_COMBO,
    SUBDEVICE_DEV_TYPE_CT,
    SUBDEVICE_DEV_TYPE_METER_HEAD,
    SUBDEVICE_DEV_TYPE_SOCKET,
})
SUBDEVICE_DEV_TYPES_ENUM_ONLY: Final = frozenset({
    SUBDEVICE_DEV_TYPE_METER,
    SUBDEVICE_DEV_TYPE_BREAKER,
    SUBDEVICE_DEV_TYPE_SMOKE,
    SUBDEVICE_DEV_TYPE_TEMP_HUMIDITY,
    SUBDEVICE_DEV_TYPE_WATER_LEAK,
})

# Subdevice ``scanName`` catalog from the Jackery app's accessory enum.
# Source: ``docs/html/jackery_smali_home_assistant_report.html`` section
# "Subdevice-/Zubehör-Erkennung" — the table the app's
# ``DeviceJackeryAccessoriesExistApi`` populates from BLE/mDNS scans.
# Keys are the wire ``scanName`` exactly as the firmware reports them
# (case-sensitive; HTO codes are uppercase, branded units are lowercase).
SUBDEVICE_SCAN_NAME_DEV_TYPES: Final[dict[str, int]] = {
    "shellyproem50": SUBDEVICE_DEV_TYPE_CT,
    "shellypro3em": SUBDEVICE_DEV_TYPE_CT,
    "shellypro3em63": SUBDEVICE_DEV_TYPE_CT,
    "ecotracker": SUBDEVICE_DEV_TYPE_METER_HEAD,
    "p1meter": SUBDEVICE_DEV_TYPE_METER_HEAD,
    "homey_energy_dongle": SUBDEVICE_DEV_TYPE_METER_HEAD,
    "shellyplusplugs": SUBDEVICE_DEV_TYPE_SOCKET,
    "shellyplugsg3": SUBDEVICE_DEV_TYPE_SOCKET,
    "HTO892A": SUBDEVICE_DEV_TYPE_METER_HEAD,
    "HTO904A": SUBDEVICE_DEV_TYPE_SOCKET,
    "HTO905A": SUBDEVICE_DEV_TYPE_METER_HEAD,
    "HTO906A": SUBDEVICE_DEV_TYPE_CT,
    "HTO907A": SUBDEVICE_DEV_TYPE_CT,
    "HTO910A": SUBDEVICE_DEV_TYPE_METER_HEAD,
}

# Human-readable labels mirroring the Jackery app's accessory enum. Used
# for device-info display and diagnostics; keep ordering aligned with
# SUBDEVICE_SCAN_NAME_DEV_TYPES so future audits can diff both maps.
SUBDEVICE_SCAN_NAME_LABELS: Final[dict[str, str]] = {
    "shellyproem50": "Shelly Pro EM-50",
    "shellypro3em": "Shelly Pro 3EM",
    "shellypro3em63": "Shelly Pro 3EM-63",
    "ecotracker": "EcoTracker P1/R1",
    "p1meter": "P1 Meter",
    "homey_energy_dongle": "Homey Energy Dongle",
    "shellyplusplugs": "Shelly Plus Plug S",
    "shellyplugsg3": "Shelly Plug S Gen3",
    "HTO892A": "Jackery HTO892A (Meter Head)",
    "HTO904A": "Jackery HTO904A (Socket)",
    "HTO905A": "Jackery HTO905A (Meter Head)",
    "HTO906A": "Jackery HTO906A (CT)",
    "HTO907A": "Jackery HTO907A (CT)",
    "HTO910A": "Jackery HTO910A (Meter Head)",
}

# Manufacturer names per accessory ``scanName``. Used for HA DeviceInfo so
# the UI shows the real brand instead of the raw wire identifier. Keys
# must stay aligned with SUBDEVICE_SCAN_NAME_DEV_TYPES; the value is what
# the corresponding stand-alone HA integration also reports (e.g. the
# Shelly integration uses "Shelly", HomeWizard uses "HomeWizard").
SUBDEVICE_SCAN_NAME_MANUFACTURERS: Final[dict[str, str]] = {
    "shellyproem50": "Shelly",
    "shellypro3em": "Shelly",
    "shellypro3em63": "Shelly",
    "ecotracker": "EcoTracker",
    "p1meter": "HomeWizard",
    "homey_energy_dongle": "Homey",
    "shellyplusplugs": "Shelly",
    "shellyplugsg3": "Shelly",
    "HTO892A": "Jackery",
    "HTO904A": "Jackery",
    "HTO905A": "Jackery",
    "HTO906A": "Jackery",
    "HTO907A": "Jackery",
    "HTO910A": "Jackery",
}

# Set of all known accessory ``scanName`` values.
SUBDEVICE_SCAN_NAMES: Final[frozenset[str]] = frozenset(
    SUBDEVICE_SCAN_NAME_DEV_TYPES.keys()
)

# ``DeviceJackeryAccessoriesExistApi$SCANTY`` enum values. The Jackery
# cloud uses this string to tell the app whether the accessory was
# discovered via BLE advertisement or via local mDNS. Both paths are
# documented and both should map to the same accessory catalog above.
SUBDEVICE_SCAN_TYPE_BLE: Final = "BLE"
SUBDEVICE_SCAN_TYPE_MDNS: Final = "MDNS"
SUBDEVICE_SCAN_TYPES: Final[frozenset[str]] = frozenset({
    SUBDEVICE_SCAN_TYPE_BLE,
    SUBDEVICE_SCAN_TYPE_MDNS,
})

# Sets used for MQTT message routing in coordinator._async_handle_mqtt_message:
MQTT_ACTION_IDS_DEVICE_PROPERTY: Final = frozenset({3011})
MQTT_ACTION_IDS_ALARM: Final = frozenset({3042})
MQTT_ACTION_IDS_SCHEDULE: Final = frozenset({3015, 3016, 3017, 3018})
MQTT_ACTION_IDS_COMBINE: Final = frozenset({
    3019,
    3021,
    3027,
    3029,
    3039,
    3040,
    3041,
    3043,
    3044,
})
# Inbound UploadSubDeviceGroupProperty action IDs the integration consumes.
# 3014=battery pack, 3031=CT, 3032=socket (smart plug), 3033=meter head,
# 3037=combo. See docs/PROTOCOL.md §2 "MQTT-Polling/Queries".
MQTT_ACTION_IDS_SUBDEVICE: Final = frozenset({3014, 3031, 3032, 3033, 3037})
