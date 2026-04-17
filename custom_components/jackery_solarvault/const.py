"""Constants for the Jackery SolarVault integration."""
from __future__ import annotations

from datetime import timedelta
from typing import Final

DOMAIN: Final = "jackery_solarvault"
MANUFACTURER: Final = "Jackery"

# --- API ---------------------------------------------------------------------
BASE_URL: Final = "https://iot.jackeryapp.com"
LOGIN_PATH: Final = "/v1/auth/login"

# Endpoints confirmed via HTTP traffic capture on the Android Jackery app v2.0.1
DEVICE_PROPERTY_PATH: Final = "/v1/device/property"       # ?deviceId=<id>
SYSTEM_LIST_PATH: Final = "/v1/device/system/list"        # 🎯 the one that actually works
ALARM_PATH: Final = "/v1/api/alarm"                       # ?systemId=<id>
SYSTEM_STATISTIC_PATH: Final = "/v1/device/stat/systemStatistic"   # ?systemId=<id>
PV_TRENDS_PATH: Final = "/v1/device/stat/sys/pv/trends"   # ?systemId=<id>&beginDate&endDate&dateType
POWER_PRICE_PATH: Final = "/v1/device/dynamic/powerPriceConfig"    # ?systemId=<id>

# Endpoints added in v1.2.0 (also verified via capture)
DEVICE_STATISTIC_PATH: Final = "/v1/device/stat/deviceStatistic"   # ?deviceId=<id>
HOME_TRENDS_PATH: Final = "/v1/device/stat/sys/home/trends"        # ?systemId&...
BATTERY_TRENDS_PATH: Final = "/v1/device/stat/sys/battery/trends"  # ?systemId&...
OTA_LIST_PATH: Final = "/v1/device/ota/list"                       # ?deviceSnList=<sn>
LOCATION_PATH: Final = "/v1/device/location"                       # ?deviceId=<id>
SYSTEM_NAME_PATH: Final = "/v1/device/system/name"                 # PUT {systemName,id}

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

# --- Android app headers (v2.0.1, April 2026) --------------------------------
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
CONF_DEVICE_ID: Final = "device_id"
CONF_SYSTEM_ID: Final = "system_id"
CONF_SCAN_INTERVAL: Final = "scan_interval"

DEFAULT_SCAN_INTERVAL_SEC: Final = 30
MIN_SCAN_INTERVAL_SEC: Final = 15
UPDATE_INTERVAL: Final = timedelta(seconds=DEFAULT_SCAN_INTERVAL_SEC)

# Slow-metric refresh cadences (decoupled from the fast property polling).
# These values match the server-side update rhythm we observed in the
# captured traffic — polling faster yields no fresher data.
SLOW_METRICS_INTERVAL_SEC: Final = 300   # statistic + pv_trends + alarm
PRICE_CONFIG_INTERVAL_SEC: Final = 3600  # power price barely ever changes

PLATFORMS: Final = ["sensor", "binary_sensor", "text"]
