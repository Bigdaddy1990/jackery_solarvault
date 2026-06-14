# Reference Coverage Matrix

Tracks how much of the upstream Jackery protocol is implemented in the
Home Assistant integration. Primary source of truth: `source-of-truth/` Smali data from the Jackery App. `docs/jackery_complete_reference.json` is derived evidence and must be regenerated/validated from that source before use.

**Last updated:** 2026-06-14

## Summary

| Oberfläche               | Referenz | Implementiert | %      | Status |
|--------------------------|----------|---------------|--------|--------|
| HTTP-Endpoints           | 112      | 105           | 94 %   | ok (3 mobile-app-only intentionally skipped) |
| MQTT-Msg-Types (home)    | 16       | 16            | 100 %  | ok |
| MQTT-Msg-Types (portable)| n/a      | n/a           | n/a    | nicht anwendbar (MQTT = home only) |
| Commands (home)          | 47       | 47            | 100 %  | ok (all ACTION_ID constants defined) |
| Commands (portable)      | 51       | 51            | 100 %  | ok (all ACTION_ID_PORTABLE_* constants defined) |
| Portable entities        | ~119     | 119           | 100 %  | ok (76 sensors + 15 buttons + 11 switches + 10 numbers + 7 selects) |
| Device-Modelle           | runtime  | runtime       | n/a    | ok (`/v1/device/system/list`) |
| Accessories              | 14       | 14            | 100 %  | ok |
| Shelly Cloud2Cloud       | 7        | 7             | 100 %  | ok |
| Crypto Layer A (auth)    | 1        | 1             | 100 %  | **deviation** (hardcoded `b"1234567890123456"`) |
| Crypto Layer B (signing) | 1        | 1             | 100 %  | ok |
| Crypto Layer C (MQTT)    | 1        | 1             | 100 %  | ok (AES-128-CBC/PKCS7, key=bluetoothKey) |
| Services (HA)            | 7        | 7             | 100 %  | 7/7 in `strings.json` registriert |
| Test-Files               | 45       | 45            | 100 %  | tracked in git |

## HTTP-Endpoints (106/118)

### Implemented (106)

**Auth (11):**
- `auth/login` — AES-128-ECB + RSA-1024 hybrid login
- `auth/register` — new account registration
- `auth/loginOut` — session logout
- `auth/verificationCode` — send email/SMS verification code
- `auth/check_verification` — verify email/SMS code
- `auth/modifyPassword` — reset password
- `auth/modifyInfo` — update user profile
- `auth/headimg` — upload profile image
- `auth/cancel` — cancel account
- `auth/updateRegisterId` — update push registration ID
- `user/info` — get user profile

**Device Management (19):**
- `device/system/list` — all systems + devices
- `device/property` — single device properties
- `device/system/name` — rename system
- `device/location` — device location (GET + PUT)
- `device/battery/pack/list` — battery pack list
- `device/ota/list` — OTA firmware list
- `device/ota/bluetooth` — BLE OTA link
- `device/ota/version/list` — BLE OTA versions
- `device/ota/update` — start OTA update
- `device/chargeReport` — charge report
- `device/bind/list` — legacy bind list
- `device/bind` — bind device
- `device/unbind` — unbind device
- `device/bind/nickname` — set device nickname
- `device/accept_bind` — accept shared device
- `device/bind/shared` — list shared devices
- `device/bind/share/list` — list share managers
- `device/bind/remove` — remove shared access
- `device/bind/removeAll` — remove all shared access
- `device/system/exist` — check system bound
- `device/system` — create/configure system
- `device/system/deviceName` — modify device name
- `device/property/pv` — modify PV name
- `device/property/power3` — power3 properties

**Statistics (18):**
- `device/stat/systemStatistic` — system-level statistics
- `device/stat/deviceStatistic` — device-level energy flows
- `device/stat/smartSocketStatistic` — socket statistics
- `device/stat/sys/pv/trends` — PV trend data
- `device/stat/sys/home/trends` — home trend data
- `device/stat/sys/battery/trends` — battery trend data
- `device/stat/pv` — PV period stats
- `device/stat/battery` — battery period stats
- `device/stat/onGrid` — home/grid period stats
- `device/stat/ct` — CT period stats
- `device/stat/ct/statics` — portable CT phase totals
- `device/stat/eps` — EPS period stats
- `device/stat/meter` — smart meter panel totals
- `device/stat/socket` — socket period stats
- `device/stat/today` — today's energy
- `device/stat/symmetry` — charge/discharge symmetry
- `device/stat/cutoff` — power outage stats
- `device/stat/soc` — state of charge
- `device/stat/carbon` — carbon offset
- `device/stat/profit` — revenue/profit
- `device/stat` — box electricity stats
- `device/stat/getSmartSchedulePrediction` — AI smart schedule

**Energy Price (14):**
- `device/dynamic/powerPriceConfig` — price configuration
- `device/dynamic/priceCompany` — price source list
- `device/dynamic/historyConfig` — price history config
- `device/dynamic/saveSingleMode` — single mode save
- `device/dynamic/saveDynamicMode` — dynamic mode save
- `device/dynamic/loginUrl` — dynamic price platform login URL
- `device/dynamic/saveContractAuth` — save contract auth
- `device/dynamic/contractList` — contract list
- `device/dynamic/cancelContractAuth` — cancel contract auth
- `device/dynamic/dynamicPrice` — dynamic price config
- `device/dynamic/saveLocationId` — Flatpeak location ID
- `device/tou/saveTouPlan` — TOU schedule plan
- `device/currencies/deviceCurrency` — device currency
- `device/currencies/currencyList` — currency list
- `device/currencies/bindCurrency` — bind currency

**Accessories (8):**
- `device/accessories` — get accessories
- `device/accessories/exist` — check accessories exist
- `device/accessories/list` — list accessories
- `device/accessories/name` — set accessory name
- `device/accessories/exists` — check Jackery accessories
- `device/accessories/synchronizeSmartAccessoriesData` — sync data
- `device/property/subShadow` — sub-device shadow
- `device/property/systemShadow` — system shadow

**Smart Mode (3):**
- `device/smartMode/checkIfSet` — check smart mode
- `device/smartMode/getSmartMode` — get smart mode info
- `device/smartMode/startSmartMode` — start smart mode

**Push (4):**
- `api/push/notifyList` — list notifications
- `api/push/unreadCount` — unread count
- `api/push/configSet` — set push config
- `api/push/configGet` — get push config

**Shelly (7):**
- `device/shelly/devices` — Shelly devices
- `wss-cloud/device/shelly/device/realtime-power` — Shelly realtime power
- `wss-cloud/device/shelly/device/control` — Shelly control
- `wss-cloud/device/shelly/auth-url` — Shelly auth URL
- `wss-cloud/device/shelly/unbind/device` — unbind Shelly device
- `wss-cloud/device/shelly/unbind/account` — unbind Shelly account
- `device/shelly/binding/failures` — Shelly binding failures

**Misc (16):**
- `device/deviceMaxPowerRecord/saveRecord` — max power save
- `api/alarm` — alarms
- `api/alarm/detail` — alarm detail
- `app/version/getNewVersion` — app version check
- `app/banner/list` — banner list
- `api/file/feedback` — submit feedback
- `api/faqList` — FAQ list
- `api/faq/answer` — FAQ answers
- `api/agreeUpgrade` — privacy consent
- `api/isUpgradeRequired` — privacy update check
- `api/instruction` — product instruction
- `api/diy/zoneList` — country/zone list
- `api/diy/gcsList` — grid standard list
- `device/alert` — sync faults/alarms
- `device/offline/stat` — offline statistics

### Missing (3) — mobile-app-only, intentionally skipped

- `auth/generatedJwt` — JWT generation for mobile app push notifications
- `device/bind/qrcode` — QR code for device binding (HA uses bindKey from config flow)
- `device/bluetoothKey` — HTTP endpoint for bluetooth key (key is captured from MQTT discovery instead)

## MQTT Message Types (16/16 home)

All 16 home MQTT message types are handled in `coordinator._async_handle_mqtt_message`:

### Implemented (16)
- `DevicePropertyChange` — main property snapshots
- `QueryDeviceProperty` — device property queries
- `QueryCombineData` — system/config snapshots
- `UploadCombineData` — system/config push updates
- `UploadIncrementalCombineData` — incremental system updates
- `ControlCombine` — system control commands
- `QuerySubDeviceGroupProperty` — subdevice group queries
- `ControlSubDevice` — subdevice control
- `DownloadDeviceSchedule` — schedule/task plan updates
- `ThirdPartMQTTConfig` — third-party MQTT config push
- `QueryThirdPartMQTTConfig` — third-party MQTT config queries
- `QueryWeatherPlan` — weather plan queries
- `SendWeatherAlert` — weather alert push
- `CancelWeatherAlert` — weather alert cancellation
- `QueryWifiConfig` — WiFi config queries
- `QueryCircuitProperty` — circuit breaker/relay configuration
- `UploadDeviceAlert` — device fault/alarm alerts
- `BindSmartAccessory` — smart accessory binding
- `RemoveSmartAccessory` — smart accessory unbinding

## Commands (47/47 home)

All 47 home commands have `ACTION_ID_*` constants defined in `const.py`.

### Commands with dedicated writer methods
- `SYSTEM_SET_WORK_MODEL` (3027) — `async_set_work_model()`
- `SYSTEM_SET_FEED_GRID_POWER` (3029) — `async_set_max_feed_grid()`
- `CONTROL_MAX_OUT_PW` (3038) — `async_set_max_output_power()`
- `SYSTEM_CONTROL_AUTO_STANDBY` (3021) — `async_set_auto_standby()`
- `CONTROL_STANDBY` (3023) — `async_set_standby()`
- `CONTROL_AC_OFF_GRID_SWITCH` (3022) — `async_set_eps_enabled()`
- `SET_CHARGE_DISCHARGE_LINE` (3028) — `async_set_soc_limits()`
- `CONTROL_REBOOT` (3030) — `async_reboot_device()`
- `SYSTEM_SET_OFF_GRID_SHUTDOWN_TIME` (3040) — `async_set_off_grid_time()`
- `SYSTEM_CONTROL_OFF_GRID_AUTO_SHUTDOWN` (3039) — `async_set_off_grid_down()`
- `SYSTEM_SET_TEMP_UNIT` (3041) — `async_set_temp_unit()`
- `SYSTEM_SET_DEFAULT_LOAD_POWER` (3043) — `async_set_default_power()`
- `SYSTEM_SET_FOLLOW_METER` (3044) — `async_set_follow_meter()`
- `SYSTEM_SET_STORM_EVENT` (3034) — `async_set_storm_minutes()`
- `SYSTEM_DELETE_STORM_EVENT` (3035) — `async_delete_storm_alert()`
- `SYSTEM_STORM_EVENT_SWITCH` (3036) — `async_set_storm_warning()`
- `GET_WIFI_CONFIG` (3045) — `async_query_wifi_config()`
- `SET_THIRD_PARTY_MQTT_CONFIG` (3046) — `async_set_third_party_mqtt_config()`
- `GET_THIRD_PARTY_MQTT_CONFIG` (3047) — `async_query_third_party_mqtt_config()`
- `TIMER_TASK_READ` (3018) — `async_query_schedule_bucket()`
- `TIMER_TASK_ADD/DELETE/UPDATE` (3015-3017) — `async_send_schedule_frame()`
- `READ_WIFI_LIST` (3001) — `async_read_wifi_list()`
- `WRITE_WIFI_INFO` (3002) — used in config_flow WiFi setup
- `SEND_TIME_ZONE` (3003) — `async_send_time_zone()`
- `GET_TIME_ZONE` (3004) — `async_get_time_zone()`
- `GET_DEVICE_OTA_VERSION` (3006) — `async_query_ota_version()`
- `NOTIFY_DEVICE_CAN_OTA` (3007) — OTA flow
- `NOTIFY_DEVICE_OTA_TOTAL_PAGE` (3008) — OTA flow
- `DEVICE_GET_OTA_PAGE_DATA` (3009) — OTA flow
- `SYNC_GRID_STANDARD` (3010) — `async_sync_grid_standard()`
- `SYNC_MQTT_CONNECT_INFO` (3005) — MQTT connection setup
- `READ_DEVICE_INFO` (3011) — `async_query_device_property()`
- `READ_SYSTEM_INFO` (3019) — `async_query_combine_data()`
- `READ_SUB_DEVICE_BATTERY_PACK` (3014) — `async_query_battery_packs()`
- `READ_SUB_DEVICE_CT` (3031) — `async_query_smart_meter()`
- `READ_SUB_DEVICE_SOCKET` (3032) — `async_query_smart_plugs()`
- `READ_SUB_DEVICE_METER_HEAD` (3033) — `async_query_meter_heads()`
- `READ_SUB_DEVICE_COMBO` (3037) — `async_query_combo_subdevices()`
- `SUB_CONTROL_SOCKET_SWITCH` (3024) — smart plug switch control
- `SUB_CONTROL_SOCKET_PRI_ENABLE` (3025) — smart plug priority
- `SUB_SET_CT_SCHEDULE_PHASE` (3026) — CT phase configuration
- `SYSTEM_GET_STORM_EVENT` (3020) — `async_query_weather_plan()`
- `BIND_SMART_PART` (3012) — accessory binding flow
- `UNBIND_SMART_PART` (3013) — accessory unbinding flow
- `FAULT_ALARM_REPORT` (3042) — device-to-cloud alert (read-only, device-initiated)

## Portable Commands (51/51)

All 51 portable commands have `ACTION_ID_PORTABLE_*` constants defined in `const.py`.
Entity descriptions wired in `button.py`, `switch.py`, `number.py`, `select.py`.

### Buttons (15)
- `RESTART` (1) — `portable_restart`
- `POWER_OFF` (2) — `portable_power_off`
- `POWER_PACK_BLINK` (3) — `portable_power_pack_blink`
- `READ_DEVICE_INFO` (6) — `portable_read_device_info`
- `READ_WIFI_LIST` (5) — `portable_read_wifi_list`
- `GET_POWER_PACK_LIST` (8) — `portable_get_power_pack_list`
- `GET_ELECTRICITY_DATA_COUNT` (9) — `portable_get_electricity_data_count`
- `SEND_TIME_ZONE` (25) — `portable_send_time_zone`
- `SYNC_MQTT_CONNECT_INFO` (50) — `portable_sync_mqtt_info`
- `GET_WIFI_CONFIG` (52) — `portable_get_wifi_config`
- `GET_CHARGE_DISCHARGE_PLAN` (26) — `portable_get_charge_plan`
- `CURRENT_CHARGE_DISCHARGE_PLAN` (30) — `portable_current_charge_plan`
- `GET_PEAKS_TROUGHS` (43) — `portable_get_peaks_troughs`
- `READ_SUB_DEVICE_CT` (51) — `portable_read_sub_ct`
- `NOTIFY_DEVICE_CAN_OTA` (3) — `portable_power_pack_blink` (reused)

### Switches (11)
- `DC_OUTPUT` (15) — `portable_dc_output`
- `DC_USB_OUTPUT` (16) — `portable_dc_usb_output`
- `DC_CAR_OUTPUT` (17) — `portable_dc_car_output`
- `AC_OUTPUT` (12) — `portable_ac_output`
- `AC240_OUTPUT` (13) — `portable_ac240_output`
- `LIGHT` (14) — `portable_light`
- `SCREEN` (18) — `portable_screen`
- `SUPER_CHARGE` (39) — `portable_super_charge`
- `ENERGY_SAVING` (20) — `portable_energy_saving`
- `OUTPUT_PRIORITY_SWITCH` (48) — `portable_output_priority_switch`
- `DISCHARGE_MEMORY` (53) — `portable_discharge_memory`

### Numbers (10)
- `CHARGE_POWER` (38) — `portable_charge_power`
- `ENERGY_STORAGE_CHARGE_LIMIT` (31) — `portable_energy_storage_charge_limit`
- `AUTO_SHUTDOWN_TIME` (19) — `portable_auto_shutdown_time`
- `AC_OUTPUT_COUNTDOWN` (34) — `portable_ac_countdown`
- `DC_OUTPUT_COUNTDOWN` (35) — `portable_dc_countdown`
- `DC_USB_OUTPUT_COUNTDOWN` (36) — `portable_dc_usb_countdown`
- `DC_CAR_OUTPUT_COUNTDOWN` (37) — `portable_dc_car_countdown`
- `OUTPUT_PRIORITY_SOC` (49) — `portable_output_priority_soc`
- `BLUETOOTH_SLEEP` (44) — `portable_bluetooth_sleep`
- `OUTPUT_PRIORITY` (48) — `portable_output_priority`

### Selects (4)
- `UPS_MODEL` (24) — `portable_ups_model`
- `POWER_MODE` (32) — `portable_power_mode`
- `AC_OUTPUT_MODE` (40) — `portable_ac_output_mode`
- `OUTPUT_PRIORITY` (48) — `portable_output_priority`

### Sensors (38)
Implemented in `sensor.py` as `PORTABLE_SENSOR_DESCRIPTIONS`.

## Subdevice Types (10 defined, 5 have entities)

| Type | Value | Entity Support | Notes |
|------|-------|----------------|-------|
| UNKNOWN | 0 | No | Fallback |
| POWER_ON (BatteryPack) | 1 | Yes (sensor) | batSoc, cellTemp, inPw, outPw, commState, version |
| COMBO | 2 | Yes (button) | Query combo subdevices |
| CT | 3 | Yes (sensor, button) | aPhasePw, bPhasePw, cPhasePw, tPhasePw |
| METER_HEAD | 4 | Yes (sensor, button) | inPw, outPw, wip |
| METER | 5 | No | No field model in RE docs |
| SOCKET | 6 | Yes (switch, binary_sensor) | inPw, outPw, switchSta, socketPri |
| **BREAKER** | 7 | **No** | No field model in RE docs |
| **SMOKE** | 8 | **No** | No field model in RE docs |
| **TEMP_HUMIDITY** | 9 | **No** | No field model in RE docs |
| **WATER_LEAK** | 10 | **No** | No field model in RE docs |

**Gap:** BREAKER(7), SMOKE(8), TEMP_HUMIDITY(9), WATER_LEAK(10) are defined as
`SUBDEVICE_DEV_TYPE_*` constants in `const.py` but lack explicit field models in
the RE documentation (`hbxn_model_fields.csv`). These types need field definitions
from captured MQTT payloads or upstream RE analysis before entities can be added.

## HomeBody / SystemBody Field-to-Entity Matrix

Detailed mapping in `sensor.py` docstring (lines 25-70).

### Live Entities (Home/System)

| Sensor Key | HTTP Source | MQTT Source | Entity |
|------------|-------------|-------------|--------|
| soc | /v1/device/property | UploadCombineData/DevicePropertyChange | sensor |
| bat_soc | /v1/device/property | DevicePropertyChange | sensor |
| cell_temperature | /v1/device/property | DevicePropertyChange | sensor |
| battery_charge_power | /v1/device/property | UploadCombineData | sensor |
| battery_discharge_power | /v1/device/property | UploadCombineData | sensor |
| pv_power_total | /v1/device/property | UploadCombineData | sensor |
| pv1..pv4_power | /v1/device/property | DevicePropertyChange | sensor |
| grid_in_power | /v1/device/property | UploadCombineData | sensor |
| grid_out_power | /v1/device/property | UploadCombineData | sensor |
| eps_in_power / eps_out_power | /v1/device/property | DevicePropertyChange | sensor |
| stack_in_power / stack_out | /v1/device/property | DevicePropertyChange | sensor |

### Period/Energy Entities

| Sensor Key | HTTP Endpoint | Chart Series |
|------------|---------------|--------------|
| pv_energy_* | /v1/device/stat/pv | totalSolarEnergy |
| pv1..pv4_energy_* | /v1/device/stat/pv | pvNEgy |
| battery_charge_energy_* | /v1/device/stat/battery | totalCharge |
| battery_discharge_energy_* | /v1/device/stat/battery | totalDischarge |
| device_ongrid_input_* | /v1/device/stat/onGrid | totalInGridEnergy |
| device_ongrid_output_* | /v1/device/stat/onGrid | totalOutGridEnergy |
| home_energy_* | /v1/device/stat/sys/home/trends | totalHomeEgy |

### Missing HomeBody/SystemBody Fields (no entity)

- `ability` — feature bitmask (parsed by `device_supports_advanced`)
- `autoStandby` — auto-off timer (exposed via switch entity)
- `batNum` — battery count (exposed via sensor)
- `emac`, `eip`, `ethPort` — Ethernet diagnostic fields
- `f` — fault/wifi array (exposed via binary_sensor)
- `maxGridStdPw`, `maxInvStdPw`, `maxIotNum` — device capability limits
- `ongridStat` — grid connection status
- `reboot` — reboot status (exposed via button)
- `swEpsState` — EPS state (exposed via binary_sensor)
- `wip`, `wname`, `wsig` — WiFi diagnostic fields

## MQTT Message-Type / Body-Template Coverage

### Message Types (28 defined, 28 handled)

All 28 `MQTT_MESSAGE_*` constants are defined in `const.py:1682-1716` and
handled in the coordinator's `_async_process_mqtt_message` method.

| messageType | Status | Entity Exposure |
|-------------|--------|-----------------|
| DevicePropertyChange | handled | PAYLOAD_PROPERTIES (live sensors) |
| ControlCombine | handled | PAYLOAD_PROPERTIES (system config) |
| QueryCombineData | handled | PAYLOAD_PROPERTIES (system config) |
| QueryDeviceProperty | handled | PAYLOAD_PROPERTIES (live sensors) |
| UploadCombineData | handled | PAYLOAD_PROPERTIES (live sensors) |
| UploadIncrementalCombineData | handled | PAYLOAD_PROPERTIES (live sensors) |
| UploadWeatherPlan | handled | PAYLOAD_WEATHER_PLAN (weather buttons/sensors) |
| BindSmartAccessory | handled | accessory discovery flow |
| RemoveSmartAccessory | handled | accessory removal flow |
| QueryWeatherPlan | handled | PAYLOAD_WEATHER_PLAN (weather buttons/sensors) |
| SendWeatherAlert | handled | PAYLOAD_WEATHER_PLAN (weather buttons/sensors) |
| CancelWeatherAlert | handled | PAYLOAD_WEATHER_PLAN (weather buttons/sensors) |
| UploadDeviceAlert | handled | PAYLOAD_DEVICE_ALERT (diagnostics) |
| DownloadDeviceSchedule | handled | PAYLOAD_TASK_PLAN (schedule sensors) |
| QuerySubDeviceGroupProperty | handled | PAYLOAD_SUBDEVICE_GROUP (subdevice sensors) |
| ControlSubDevice | handled | subdevice switch/button entities |
| ThirdPartMQTTConfig | handled | PAYLOAD_THIRD_PARTY_MQTT_CONFIG (config) |
| QueryThirdPartMQTTConfig | handled | PAYLOAD_THIRD_PARTY_MQTT_CONFIG (config) |
| QueryWifiConfig | handled | PAYLOAD_WIFI_CONFIG (diagnostics) |
| QueryElectricityStrategy | handled | PAYLOAD_ELECTRICITY_STRATEGY (charge plan) |
| InsertElectricityStrategy | handled | PAYLOAD_ELECTRICITY_STRATEGY (charge plan) |
| UpdateElectricityStrategy | handled | PAYLOAD_ELECTRICITY_STRATEGY (charge plan) |
| DeleteElectricityStrategy | handled | PAYLOAD_ELECTRICITY_STRATEGY (charge plan) |
| QueryCurrentElectricityStrategy | handled | PAYLOAD_ELECTRICITY_STRATEGY (charge plan) |
| SetBatteryBoundry | handled | PAYLOAD_BATTERY_BOUNDARY (battery limits) |
| TOUSchedule | handled | PAYLOAD_TOU_SCHEDULE (TOU plan) |
| QueryTOUSchedule | handled | PAYLOAD_TOU_SCHEDULE (TOU plan) |
| QueryCircuitProperty | handled | PAYLOAD_CIRCUIT_PROPERTY (circuit config) |

### Body Templates (RE Protocol §4)

| Template | Description | Status |
|----------|-------------|--------|
| `{"cmd":<n>}` | Simple command (read/info) | implemented |
| `{"cmd":<n>,"en":<0\|1>}` | Enable/disable (AC, DC, USB, EPS, etc.) | implemented |
| `{"cmd":<n>,"dt":<int>}` | Countdown timer (AC/DC/USB/CAR) | implemented |
| `{"cmd":<n>,"ps":<int>}` | Priority settings (output priority, SOC limit) | implemented |
| `{"cmd":<n>,"cir":<json>}` | Circuit-config array (circuit breaker relays) | received, no entity |
| `{"cmd":<n>,"idx":<i>,"sw":<0\|1>}` | Circuit index switch (toggle relay) | received, no entity |
| `{"cmd":<n>,"idx":<i>,"nm":"<name>"}` | Circuit index rename (relay label) | received, no entity |
| `{"cmd":<n>,"mp":{...}}` | Modbus property (CT phase config) | handled via subdevice |
| `{"cmd":<n>,"ms":{...}}` | Modbus schedule (CT phase schedule) | handled via subdevice |
| `{"cmd":<n>,"ap":{...}}` | Accessory property (meter/socket) | handled via subdevice |

### Missing Body-Template Entities

- **Circuit-index entities** (cir/idx/sw/nm): MQTT payloads received and stored
  in `PAYLOAD_CIRCUIT_PROPERTY` but not exposed as HA entities. Needs circuit
  breaker switch entities and relay name sensors.
- **Smart Mode body templates** (SmartModeStart/SmartModeEnd): HTTP endpoints
  exist (`client/_endpoints/smart_mode.py`) + coordinator methods added
  (`async_check_smart_mode`, `async_get_smart_mode_info`, `async_start_smart_mode`).
  Sensor descriptions added for `smart_mode_active` and `smart_mode_time_difference`.
- **TOU Plan**: HTTP endpoint exists (`client/_endpoints/energy_price.py`) +
  coordinator methods added (`async_query_tou_plan`, `async_save_tou_plan`).
  Sensor description added for `tou_plan_tasks`.

## Crypto Layers

| Layer | Status | Details |
|-------|--------|---------|
| A (auth) | deviation | Hardcoded AES_KEY at const.py:278 (not per-login random) |
| B (signing) | ok | RSA-1024 PKCS#1 v1.5 for login AES key wrap |
| C (MQTT) | ok | AES-128-CBC/PKCS7, key=bluetoothKey, iv=key, Base64 |
