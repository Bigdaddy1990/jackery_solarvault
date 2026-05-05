"""Constants for the Jackery SolarVault integration."""

from __future__ import annotations

from datetime import timedelta
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
MQTT_SILENT_THRESHOLD_SEC: Final = 300

# How long a battery pack may report ``commState=0`` (offline) before the
# coordinator removes it from PAYLOAD_BATTERY_PACKS. The Jackery cloud
# never explicitly emits "pack-removed"; a pack that has been physically
# unplugged simply stops appearing in MQTT updates while sometimes being
# echoed on HTTP for a while. 7 days is conservative — daily power
# outages or WiFi blips do NOT trigger removal, but a permanently-removed
# pack is cleaned up within a week.
BATTERY_PACK_STALE_THRESHOLD_SEC: Final = 7 * 24 * 3600

# Internal field name used by the coordinator to remember when a pack
# last reported as online. Not exposed as an entity attribute.
PACK_FIELD_LAST_SEEN_AT: Final = "_last_seen_at"

# When the REST token/session rotates frequently (for example because the
# mobile app logs in at the same time), rebuilding MQTT credentials on every
# poll can create reconnect churn. Throttle reconnect attempts a little.
MQTT_RECONNECT_THROTTLE_SEC: Final = 90

# HTTP endpoint constants. Keep this list aligned with APP_POLLING_MQTT.md.
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

# Device/statistic endpoint group from APP_POLLING_MQTT.md.
DEVICE_STATISTIC_PATH: Final = "/v1/device/stat/deviceStatistic"  # ?deviceId=<id>
HOME_TRENDS_PATH: Final = "/v1/device/stat/sys/home/trends"  # ?systemId&...
BATTERY_TRENDS_PATH: Final = "/v1/device/stat/sys/battery/trends"  # ?systemId&...
DEVICE_PV_STAT_PATH: Final = "/v1/device/stat/pv"  # ?deviceId&systemId&...
DEVICE_BATTERY_STAT_PATH: Final = "/v1/device/stat/battery"  # ?deviceId&...
DEVICE_HOME_STAT_PATH: Final = "/v1/device/stat/onGrid"  # ?deviceId&...
DEVICE_CT_STAT_PATH: Final = "/v1/device/stat/ct"  # ?deviceId&...
DEVICE_METER_STAT_PATH: Final = (
    "/v1/device/stat/meter"  # ?deviceId=<smart-meter subdevice>
)
BATTERY_PACK_PATH: Final = "/v1/device/battery/pack/list"  # ?deviceSn=<sn>
OTA_LIST_PATH: Final = "/v1/device/ota/list"  # ?deviceSnList=<sn>
LOCATION_PATH: Final = "/v1/device/location"  # ?deviceId=<id>
SYSTEM_NAME_PATH: Final = "/v1/device/system/name"  # PUT {systemName,id}

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

# --- Android app headers documented in APP_POLLING_MQTT.md --------------------
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


# Config-flow step, error and abort identifiers.
FLOW_STEP_USER: Final = "user"
FLOW_STEP_INIT: Final = "init"
FLOW_STEP_REAUTH_CONFIRM: Final = "reauth_confirm"
FLOW_ERROR_BASE: Final = "base"
FLOW_ERROR_INVALID_AUTH: Final = "invalid_auth"
FLOW_ERROR_CANNOT_CONNECT: Final = "cannot_connect"
FLOW_ERROR_ACCOUNT_REQUIRED: Final = "account_required"
FLOW_ABORT_REAUTH_ENTRY_MISSING: Final = "reauth_entry_missing"
FLOW_ABORT_REAUTH_SUCCESSFUL: Final = "reauth_successful"

DEFAULT_SCAN_INTERVAL_SEC: Final = 30
MIN_SCAN_INTERVAL_SEC: Final = 15
DEFAULT_CREATE_SMART_METER_DERIVED_SENSORS: Final = True
DEFAULT_CREATE_CALCULATED_POWER_SENSORS: Final = False
UPDATE_INTERVAL: Final = timedelta(seconds=DEFAULT_SCAN_INTERVAL_SEC)

# Slow-metric refresh cadences (decoupled from the fast property polling).
# These values match the server-side update rhythm we observed in the
# captured traffic — polling faster yields no fresher data.
SLOW_METRICS_INTERVAL_SEC: Final = 300  # statistic + pv_trends + alarm
PRICE_CONFIG_INTERVAL_SEC: Final = 3600  # power price barely ever changes
DEFAULT_STORM_WARNING_MINUTES: Final = 120

REQUEST_TIMEOUT_SEC: Final = 15

HTTP_METHOD_GET: Final = "GET"
HTTP_METHOD_POST: Final = "POST"
HTTP_METHOD_PUT: Final = "PUT"
HTTP_HEADER_CONTENT_TYPE: Final = "content-type"
HTTP_CONTENT_TYPE_FORM: Final = "application/x-www-form-urlencoded"
HTTP_CONTENT_TYPE_JSON: Final = "application/json; charset=utf-8"
HTTP_RAW_TEXT_LIMIT: Final = 500

# Shared payload section names. Coordinator, diagnostics and entity platforms use
# the same normalized payload shape; keep the keys in one place so section names
# stay aligned with APP_POLLING_MQTT.md.
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
PAYLOAD_OTA: Final = "ota"
PAYLOAD_LOCATION: Final = "location"
PAYLOAD_WEATHER_PLAN: Final = "weather_plan"
PAYLOAD_TASK_PLAN: Final = "task_plan"
PAYLOAD_PRICE_SOURCES: Final = "price_sources"
PAYLOAD_BATTERY_PACKS: Final = "battery_packs"
PAYLOAD_CT_METER: Final = "ct_meter"
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
FIELD_BODY: Final = "body"
FIELD_DATA: Final = "data"
FIELD_ID: Final = "id"
FIELD_VERSION: Final = "version"
FIELD_MESSAGE_TYPE: Final = "messageType"
FIELD_TIMESTAMP: Final = "timestamp"
FIELD_CMD: Final = "cmd"
FIELD_UPDATES: Final = "updates"
FIELD_SYSTEM_ID: Final = "systemId"
FIELD_DEVICE_ID: Final = "deviceId"
FIELD_DEV_ID: Final = "devId"
FIELD_DEVICE_NAME: Final = "deviceName"
FIELD_DEVICE_SN: Final = "deviceSn"
FIELD_DEV_SN: Final = "devSn"
FIELD_DEVICE_SECRET: Final = "deviceSecret"
FIELD_RANDOM_SALT: Final = "randomSalt"
FIELD_SYSTEM_SN: Final = "systemSn"
FIELD_SYSTEM_NAME: Final = "systemName"
FIELD_SYSTEM_STATE: Final = "systemState"
FIELD_MODEL_NAME: Final = "modelName"
FIELD_DEV_MODEL: Final = "devModel"
FIELD_ONLINE_STATUS: Final = "onlineStatus"
FIELD_ONLINE_STATE: Final = "onlineState"
FIELD_WNAME: Final = "wname"
FIELD_MAC: Final = "mac"
FIELD_WIP: Final = "wip"
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
FIELD_GRID_STANDARD: Final = "gridStandard"
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
FIELD_IN_PW: Final = "inPw"
FIELD_OUT_PW: Final = "outPw"
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
FIELD_CT_TOTAL_PHASE_POWER: Final = "tPhasePw"
FIELD_CT_TOTAL_NEGATIVE_PHASE_POWER: Final = "tnPhasePw"
FIELD_CT_A_PHASE_POWER: Final = "aPhasePw"
FIELD_CT_B_PHASE_POWER: Final = "bPhasePw"
FIELD_CT_C_PHASE_POWER: Final = "cPhasePw"
FIELD_CT_A_NEGATIVE_PHASE_POWER: Final = "anPhasePw"
FIELD_CT_B_NEGATIVE_PHASE_POWER: Final = "bnPhasePw"
FIELD_CT_C_NEGATIVE_PHASE_POWER: Final = "cnPhasePw"
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
CT_ATTRIBUTE_FIELDS: Final = (
    FIELD_SCAN_NAME,
    FIELD_SN,
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
)

FIELD_TARGET_MODULE_VERSION: Final = "targetModuleVersion"
FIELD_UPGRADE_TYPE: Final = "upgradeType"
FIELD_POWER_PRICE_RESOURCE: Final = "powerPriceResource"
FIELD_LOGIN_ALLOWED: Final = "loginAllowed"

MAIN_PROPERTY_ALIAS_PAIRS: Final = (
    (FIELD_MAX_FEED_GRID, FIELD_MAX_GRID_STD_PW),
    (FIELD_SOC_CHARGE_LIMIT, FIELD_SOC_CHG_LIMIT),
    (FIELD_SOC_DISCHARGE_LIMIT, FIELD_SOC_DISCHG_LIMIT),
)

TASK_PLAN_BODY: Final = FIELD_BODY
TASK_PLAN_TASKS: Final = "tasks"
STORM_MINUTES_FIELDS: Final = (FIELD_WPC, FIELD_MINS_INTERVAL)
STORM_ENABLE_FIELDS: Final = (FIELD_WPS,)

# MQTT subdevice routing and mirroring keys documented in MQTT_PROTOCOL.md.
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
    "ap",
    "ap1",
    "ap2",
    "ap3",
    FIELD_CT_A_PHASE_POWER,
    FIELD_CT_B_PHASE_POWER,
    FIELD_CT_C_PHASE_POWER,
    FIELD_CT_TOTAL_PHASE_POWER,
    FIELD_CT_A_NEGATIVE_PHASE_POWER,
    FIELD_CT_B_NEGATIVE_PHASE_POWER,
    FIELD_CT_C_NEGATIVE_PHASE_POWER,
    FIELD_CT_TOTAL_NEGATIVE_PHASE_POWER,
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
    FIELD_RB,
    FIELD_IP,
    FIELD_OP,
    FIELD_VERSION,
    FIELD_IS_FIRMWARE_UPGRADE,
})


# Normalized metadata/series keys used by app-period helpers. Request metadata is
# attached by api.py so entity diagnostics can show the exact documented
# begin/end/dateType range from APP_POLLING_MQTT.md.
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

# Date-type values used by the documented app period endpoints.
DATE_TYPE_DAY: Final = "day"
DATE_TYPE_WEEK: Final = "week"
DATE_TYPE_MONTH: Final = "month"
DATE_TYPE_YEAR: Final = "year"
APP_PERIOD_DATE_TYPES: Final = (
    DATE_TYPE_DAY,
    DATE_TYPE_WEEK,
    DATE_TYPE_MONTH,
    DATE_TYPE_YEAR,
)
APP_CHART_DATE_TYPES: Final = (DATE_TYPE_WEEK, DATE_TYPE_MONTH, DATE_TYPE_YEAR)

# External statistic buckets. The normal entities stay period totals; these
# bucket names identify HA-recorder series imported from the app chart arrays.
EXTERNAL_STAT_BUCKET_WEEK_DAILY: Final = "week_daily"
EXTERNAL_STAT_BUCKET_MONTH_DAILY: Final = "month_daily"
EXTERNAL_STAT_BUCKET_YEAR_MONTHLY: Final = "year_monthly"
APP_CHART_BUCKET_BY_DATE_TYPE: Final = {
    DATE_TYPE_WEEK: EXTERNAL_STAT_BUCKET_WEEK_DAILY,
    DATE_TYPE_MONTH: EXTERNAL_STAT_BUCKET_MONTH_DAILY,
    DATE_TYPE_YEAR: EXTERNAL_STAT_BUCKET_YEAR_MONTHLY,
}
APP_CHART_BUCKET_LABEL_BY_DATE_TYPE: Final = {
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
DATA_QUALITY_REPAIR_EXAMPLE_LIMIT: Final = 3
REPAIR_ISSUE_APP_DATA_INCONSISTENCY: Final = "app_data_inconsistency"
REPAIR_TRANSLATION_APP_DATA_INCONSISTENCY: Final = "app_data_inconsistency"

# Section prefixes and chart-series keys documented by APP_POLLING_MQTT.md.
APP_SECTION_PV_STAT: Final = "device_pv_stat"
APP_SECTION_HOME_STAT: Final = "device_home_stat"
APP_SECTION_BATTERY_STAT: Final = "device_battery_stat"
APP_SECTION_HOME_TRENDS: Final = "home_trends"
APP_SECTION_PV_TRENDS: Final = "pv_trends"
APP_SECTION_BATTERY_TRENDS: Final = "battery_trends"
APP_SECTION_CT_STAT: Final = "device_ct_stat"

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
APP_STAT_TOTAL_TREND_CHARGE_ENERGY: Final = "totalChgEgy"
APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY: Final = "totalDisChgEgy"
APP_STAT_TOTAL_HOME_ENERGY: Final = "totalHomeEgy"
APP_STAT_TODAY_LOAD: Final = "todayLoad"
APP_STAT_TODAY_BATTERY_CHARGE: Final = "todayBatteryChg"
APP_STAT_TODAY_BATTERY_DISCHARGE: Final = "todayBatteryDisChg"
APP_STAT_TODAY_GENERATION: Final = "todayGeneration"
APP_STAT_TOTAL_GENERATION: Final = "totalGeneration"
APP_STAT_TOTAL_REVENUE: Final = "totalRevenue"
APP_STAT_TOTAL_CARBON: Final = "totalCarbon"
APP_DEVICE_STAT_PV_ENERGY: Final = "pvEgy"
APP_DEVICE_STAT_BATTERY_CHARGE: Final = "batChgEgy"
APP_DEVICE_STAT_BATTERY_DISCHARGE: Final = "batDisChgEgy"
APP_DEVICE_STAT_ONGRID_INPUT: Final = "inOngridEgy"
APP_DEVICE_STAT_ONGRID_OUTPUT: Final = "outOngridEgy"
APP_DEVICE_STAT_BATTERY_TO_GRID: Final = "batOtGridEgy"
APP_DEVICE_STAT_PV_TO_BATTERY: Final = "pvOtBatEgy"
APP_DEVICE_STAT_ONGRID_TO_BATTERY: Final = "ongridOtBatEgy"

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
PRICE_MODE_TO_OPTION: Final = {1: "dynamic", 2: "single"}
UNKNOWN_OPTION_PREFIX: Final = "unknown_"

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
    "password",
    "username",
    CONF_MQTT_MAC_ID,
    CONF_REGION_CODE,
    FIELD_MAC_ID,
    FIELD_TOKEN,
    FIELD_MQTT_PASSWORD,
    FIELD_DEVICE_ID,
    FIELD_SYSTEM_ID,
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
    "mqttUser",
    "mqttUsername",
    "mqttClientId",
    "mqtt_user",
    "mqtt_username",
}

# MQTT client metadata and topic layout from MQTT_PROTOCOL.md.
MQTT_CLIENT_LIBRARY: Final = "gmqtt"
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
}

# Subdevice type markers observed in MQTT_PROTOCOL.md. devType=1 is a
# BatteryPackSub query target, devType=2 is a combined subdevice group, and
# devType=3 is the Smart-Meter/CT accessory group. subType=2 appears on the
# system/accessory metadata for the Shelly 3EM/CT path.
SUBDEVICE_TYPE_BATTERY_PACK: Final = "1"
SUBDEVICE_TYPE_COMBINE: Final = "2"
SUBDEVICE_TYPE_SMART_METER: Final = "3"
SMART_METER_SUBTYPE: Final = SUBDEVICE_TYPE_COMBINE
NON_BATTERY_SUBDEVICE_TYPES: Final = frozenset({
    SUBDEVICE_TYPE_COMBINE,
    SUBDEVICE_TYPE_SMART_METER,
})

# Payload sections that must survive a slow HTTP refresh when they were last
# updated via MQTT. These are integration payload keys, not MQTT message types.
PRESERVED_FAST_PAYLOAD_KEYS: Final = (
    PAYLOAD_CT_METER,
    PAYLOAD_WEATHER_PLAN,
    PAYLOAD_TASK_PLAN,
    PAYLOAD_NOTICE,
    PAYLOAD_MQTT_LAST,
)

# Service names and payload fields from services.yaml.
SERVICE_RENAME_SYSTEM: Final = "rename_system"
SERVICE_REFRESH_WEATHER_PLAN: Final = "refresh_weather_plan"
SERVICE_DELETE_STORM_ALERT: Final = "delete_storm_alert"
SERVICE_FIELD_SYSTEM_ID: Final = "system_id"
SERVICE_FIELD_NEW_NAME: Final = "new_name"
SERVICE_FIELD_DEVICE_ID: Final = "device_id"
SERVICE_FIELD_ALERT_ID: Final = "alert_id"
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
DUPLICATE_BINARY_SENSOR_SUFFIXES: Final = {"_eps_enabled"}
CT_PERIOD_SENSOR_SUFFIXES: Final = {
    "_smart_meter_import_week_energy",
    "_smart_meter_import_month_energy",
    "_smart_meter_import_year_energy",
    "_smart_meter_export_week_energy",
    "_smart_meter_export_month_energy",
    "_smart_meter_export_year_energy",
}
LEGACY_NUMBER_SENSOR_SUFFIXES: Final = {
    "_work_mode_set",
    "_temp_unit_set",
    "_off_grid_time_set",
    "_storm_warning_minutes_set",
    "_max_power_experimental",
}
LEGACY_LIFETIME_SENSOR_SUFFIXES: Final = {
    "_lifetime_pv_energy",
    "_lifetime_battery_charge",
    "_lifetime_battery_discharge",
    "_lifetime_grid_import",
    "_lifetime_grid_export",
    "_lifetime_ongrid_to_battery",
    "_lifetime_pv_to_battery",
    "_lifetime_battery_to_grid",
}
STALE_PERIOD_SENSOR_SUFFIXES: Final = {
    "_grid_import_week_energy",
    "_grid_import_month_energy",
    "_grid_import_year_energy",
    "_grid_export_week_energy",
    "_grid_export_month_energy",
    "_grid_export_year_energy",
}
LEGACY_AUTO_OFF_GRID_SELECT_SUFFIX: Final = "_auto_off_grid_mode"
LEGACY_PV_TODAY_SENSOR_SUFFIX: Final = "_pv_today_energy"
SYSTEM_PV_TODAY_SENSOR_SUFFIX: Final = "_system_pv_today_energy"
BATTERY_PACK_UID_MARKER: Final = "_battery_pack_"
BATTERY_PACK_CELL_TEMPERATURE_SUFFIX: Final = "_cell_temperature"
STALE_ENERGY_HELPER_PREFIX: Final = "sensor.energy_"
STALE_NET_POWER_SUFFIX: Final = "_net_power"
STALE_HELPER_VENDOR_TOKENS: Final = ("solarvault", "jackery")
STALE_HELPER_BATTERY_TOKENS: Final = ("battery", "batterie")
STALE_HELPER_CHARGE_TOKENS: Final = ("charge", "lade")
STALE_HELPER_DISCHARGE_TOKENS: Final = ("discharge", "entlade")
FORMER_DISABLED_APP_SENSOR_SUFFIXES: Final = {
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
}
NON_APP_DIAGNOSTIC_SENSOR_SUFFIXES: Final = {
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
}
REMOVED_SENSOR_SUFFIXES: Final = {
    "_grid_side_in_power",
    "_grid_side_out_power",
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
# Keep these names aligned with MQTT_PROTOCOL.md instead of repeating raw strings.
MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE: Final = "DevicePropertyChange"
MQTT_MESSAGE_CONTROL_COMBINE: Final = "ControlCombine"
MQTT_MESSAGE_QUERY_COMBINE_DATA: Final = "QueryCombineData"
MQTT_MESSAGE_UPLOAD_COMBINE_DATA: Final = "UploadCombineData"
MQTT_MESSAGE_UPLOAD_INCREMENTAL_COMBINE_DATA: Final = "UploadIncrementalCombineData"
MQTT_MESSAGE_UPLOAD_WEATHER_PLAN: Final = "UploadWeatherPlan"
MQTT_MESSAGE_QUERY_WEATHER_PLAN: Final = "QueryWeatherPlan"
MQTT_MESSAGE_SEND_WEATHER_ALERT: Final = "SendWeatherAlert"
MQTT_MESSAGE_CANCEL_WEATHER_ALERT: Final = "CancelWeatherAlert"
MQTT_MESSAGE_DOWNLOAD_DEVICE_SCHEDULE: Final = "DownloadDeviceSchedule"
MQTT_MESSAGE_QUERY_SUBDEVICE_GROUP_PROPERTY: Final = "QuerySubDeviceGroupProperty"

MQTT_CMD_NONE: Final = 0
MQTT_CMD_QUERY_WEATHER_PLAN: Final = 23
MQTT_CMD_DEVICE_PROPERTY_CHANGE: Final = 107
MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY: Final = 110
MQTT_CMD_QUERY_COMBINE_DATA: Final = 120
MQTT_CMD_CONTROL_COMBINE: Final = 121

# --- MQTT action IDs (Jackery cloud command/upload protocol) ----------------
# These IDs are sent in MQTT command payloads' `actionId` field. Keep them
# aligned with the MQTT command tables in MQTT_PROTOCOL.md and APP_POLLING_MQTT.md.

# Individual action IDs used in outbound commands:
ACTION_ID_SOC_CHARGE_LIMIT: Final = 3022  # cmd=107 DevicePropertyChange
ACTION_ID_SOC_DISCHARGE_LIMIT: Final = 3028  # cmd=107 DevicePropertyChange
ACTION_ID_MAX_FEED_GRID: Final = 3029  # cmd=121 ControlCombine (800W rule)
ACTION_ID_MAX_OUT_PW: Final = 3038  # cmd=121 ControlCombine
ACTION_ID_AUTO_STANDBY: Final = 3021  # cmd=121 ControlCombine
ACTION_ID_WORK_MODEL: Final = 3027  # cmd=121 ControlCombine
ACTION_ID_OFF_GRID_DOWN: Final = 3039  # cmd=121 ControlCombine
ACTION_ID_OFF_GRID_TIME: Final = 3040  # cmd=121 ControlCombine
ACTION_ID_TEMP_UNIT: Final = 3041  # cmd=121 ControlCombine
ACTION_ID_DEFAULT_PW: Final = 3043  # cmd=121 ControlCombine
ACTION_ID_FOLLOW_METER_PW: Final = 3044  # cmd=121 ControlCombine
ACTION_ID_QUERY_COMBINE_DATA: Final = 3019  # cmd=120 QueryCombineData
ACTION_ID_WPS_ENABLED: Final = ACTION_ID_QUERY_COMBINE_DATA
ACTION_ID_QUERY_WEATHER_PLAN: Final = 3020  # cmd=23 QueryWeatherPlan
ACTION_ID_EPS_ENABLED: Final = 3023  # cmd=107 DevicePropertyChange (EPS toggle)
ACTION_ID_STANDBY: Final = (
    ACTION_ID_EPS_ENABLED  # cmd=107 DevicePropertyChange (standby)
)
ACTION_ID_REBOOT_DEVICE: Final = 3030  # cmd=107 DevicePropertyChange
ACTION_ID_STORM_MINUTES: Final = 3034  # cmd=*** SendWeatherAlert
ACTION_ID_DELETE_STORM_ALERT: Final = 3035  # cmd=*** CancelWeatherAlert
ACTION_ID_STORM_WARNING: Final = 3036  # cmd=121 ControlCombine
ACTION_ID_SUBDEVICE_3014: Final = (
    3014  # cmd=110 QuerySubDeviceGroupProperty, battery packs
)
ACTION_ID_SUBDEVICE_3031: Final = (
    3031  # cmd=110 QuerySubDeviceGroupProperty, CT/smart meter
)
ACTION_ID_SUBDEVICE_3037: Final = (
    3037  # cmd=110 QuerySubDeviceGroupProperty, combined subdevices
)

# Sets used for MQTT message routing in coordinator._async_handle_mqtt_message:
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
MQTT_ACTION_IDS_SUBDEVICE: Final = frozenset({3014, 3031, 3033, 3037})
