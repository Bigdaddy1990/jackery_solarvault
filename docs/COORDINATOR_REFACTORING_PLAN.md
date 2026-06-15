# Coordinator Refactoring Plan — Jackery SolarVault

> **Status:** PLANNING  
> **Date:** 2026-06-12 (v2 - rewritten against RE docs)  
> **Coordinator:** 9,467 lines (was 10,091) -> target ~3,000 lines  
> **Authoritative sources:** Jackery 2.1.1 RE documentation (`source-of-truth/Jackery_2.1.1_RE_Documentation.md`,
> `source-of-truth/Jackery_2.1.1_RE_Supplement.md`, `source-of-truth/Jackery_2.1.1_RE_Crypto_and_DTOs.md`,
> `source-of-truth/Jackery_2.1.1_DEX_Aufschluesselung.md`, `source-of-truth/Jackery_2.1.1_Stats_und_Trends.md`,
> `source-of-truth/jackery_complete_reference.json`, `source-of-truth/jackery_entity_field_candidates_v2.json`,
> `source-of-truth/hbxn_commands.html`, `source-of-truth/jackery_command_catalog_v2.html`,
> `source-of-truth/jackery_http_api_endpoints_v2.html`, `source-of-truth/jackery_http_model_fields_v2.html`,
> `source-of-truth/hbxn_model_fields.html`, `source-of-truth/jackery_ha_extraction_v2.json`)

---

## 1. Protocol Reference (from RE docs)

### 1.1 Transport Layers

| Layer | Transport | Role | Write | Read |
|-------|-----------|------|:-----:|:----:|
| **3** | HTTP (`fasthttp`) | PRIMARY — all commands + data | ✅ | ✅ |
| **4** | BLE (GATT) | FALLBACK — commands + live data | ✅ | ✅ |
| **5** | MQTT (Cloud + Local) | DATA SOURCE + CMD TRANSPORT | ✅ | ✅ |

### 1.2 HTTP API — 112 Endpoints (57% implemented)

| Category | Endpoints | Implemented | Module |
|----------|:---------:|:-----------:|--------|
| Auth/login | 3 | 3 | `client/_endpoints/auth.py` |
| Device/property | 8 | 8 | `client/_endpoints/device.py` |
| System list | 1 | 1 | `client/_endpoints/device.py` |
| PV statistics | 20 | 20 | `client/_endpoints/statistics.py` |
| Home statistics | 10 | 10 | `client/_endpoints/statistics.py` |
| Battery statistics | 20 | 20 | `client/_endpoints/statistics.py` |
| CT/Meter statistics | 20 | 20 | `client/_endpoints/statistics.py` |
| Smart mode | 3 | 3 | `client/_endpoints/smart_mode.py` |
| Energy price | 6 | 3 | `client/_endpoints/energy_price.py` |
| Accessories | 2 | 2 | `client/_endpoints/accessories.py` |
| Alarms | 1 | 1 | `client/_endpoints/misc.py` |
| Weather | 2 | 2 | `client/_endpoints/misc.py` |
| Push register | 2 | 2 | `client/_endpoints/push.py` |
| OTA | 3 | 0 | NOT YET IMPLEMENTED |
| **Total** | **112** | **64** | |

### 1.3 MQTT Message Types — 25 (home) + 28 defined

Key message types routed by `_async_handle_mqtt_message`:

| messageType | actionId | Direction | Purpose |
|-------------|:--------:|:---------:|---------|
| `UploadCombineData` | 3002 | → app | Main device telemetry push (HomeBody 35 fields) |
| `DevicePropertyChange` | 3001 | ↔ | Property delta update (HomeBody subset) |
| `ControlCombine` | 3006 | ↔ | Combined control (SOC limits, EPS, grid) |
| `QueryCombineData` | 3026 | app→ | Request device telemetry snapshot |
| `UploadSubDeviceIncrementalProperty` | 3007 | → app | Battery pack / CT incremental push |
| `QuerySubDeviceInfo` | 3027 | app→ | Request subdevice info |
| `UploadSystemInfo` | 3003 | → app | System config (SystemBody 32 fields) |
| `QuerySystemInfo` | 3023 | app→ | Request system info |
| `UploadWeatherPlan` | 3004 | → app | Weather forecast plan |
| `QueryWeatherPlan` | 3024 | app→ | Request weather plan |
| `SendWeatherAlert` | 3008 | → app | Storm warning push |
| `CancelWeatherAlert` | 3013 | → app | Storm cancellation |
| `DeleteStormAlert` | 3036 | app→ | Remove storm alert |
| `DownloadDeviceSchedule` | 3005 | → app | Timer/schedule push |
| `ControlDeviceSchedule` | 3032 | app→ | Add/update/delete timer tasks |
| `QueryTOUSchedule` | 3034 | app→ | Query TOU schedule |
| `TOUSchedule` | 3035 | → app | TOU schedule data |
| `QueryElectricityStrategy` | 3039 | app→ | Query electricity strategy |
| `UploadElectricityStrategy` | 3040 | → app | Electricity strategy data |
| `InsertElectricityStrategy` | 3041 | app→ | Create electricity strategy |
| `UpdateElectricityStrategy` | 3042 | app→ | Update electricity strategy |
| `DeleteElectricityStrategy` | 3043 | app→ | Delete electricity strategy |
| `QueryCurrentElectricityStrategy` | 3044 | app→ | Query current strategy |
| `WorkModelChange` | 3011 | → app | Work model echo (selfUse/forceCharge/etc.) |
| `UploadFirmwareVersion` | 3010 | → app | Firmware version push |
| `UploadOTAStatus` | 3014 | → app | OTA update status |
| `ThirdPartMQTTConfig` | 3047 | ↔ | Third-party MQTT config set |
| `QueryThirdPartMQTTConfig` | 3048 | app→ | Query third-party MQTT config |

### 1.4 Commands — 47 home + 41 portable = 88

Home commands (cmd int → actionId):
- `107` = DevicePropertyChange → actionId 3001
- `108` = ControlWorkModel → actionId 3011
- `109` = ControlEPS → actionId 3012
- `110` = ControlReboot → actionId 3013
- `111` = ControlSubDevice → actionId 3007
- `112` = ControlDeleteStormAlert → actionId 3036
- `113` = ThirdPartMQTTConfig → actionId 3047
- `114` = QueryThirdPartMQTTConfig → actionId 3048
- `115` = ControlStandby → actionId 3015
- `116` = ControlAutoStandby → actionId 3016
- `121` = ControlCombine → actionId 3006
- `122` = QueryCombineData → actionId 3026
- `123` = ControlMaxOutPw → actionId 3038
- `124` = ControlSocLimit → actionId 3028
- `125` = ControlMaxFeedGrid → actionId 3029
- `130` = QueryTOUSchedule → actionId 3034
- `131` = ControlTOUSchedule → actionId 3035
- `3027` = QuerySubDeviceInfo
- `3023` = QuerySystemInfo
- `3024` = QueryWeatherPlan
- `3032` = ControlDeviceSchedule
- `3033` = ControlTimerTask
- `3039`–`3044` = ElectricityStrategy CRUD
- `3046` = SetThirdPartMQTTConfig

### 1.5 Crypto Layers

| Layer | Purpose | Algorithm | Key Source | Implementation |
|-------|---------|-----------|------------|----------------|
| **A** | Login auth | AES/ECB/PKCS5 + RSA/ECB/PKCS1 (1024-bit) | Hardcoded `b"1234567890123456"` | `client/_endpoints/auth.py` |
| **B** | MQTT auth | AES-256-CBC/PKCS5 | Base64(mqttPassWord), IV=key[:16] | `client/mqtt_push.py` |
| **C** | Payload encrypt | AES-128-CBC/PKCS7 | Base64(bluetoothKey), IV=Key | `client/_crypto.py` |

### 1.6 Key DTOs (from RE)

| DTO | Fields | Role |
|-----|:------:|------|
| **HomeBody** | 35 | Main device property payload (UploadCombineData, DevicePropertyChange) |
| **SystemBody** | 32 | System config (UploadSystemInfo) |
| **PortableBody** | 96 | Portable device full telemetry |
| **BoxBody** | 35 | Battery box telemetry |
| **BatteryPackSub** | 9 | Add-on battery pack |
| **AccCTBody** | 26 | CT/SmartMeter accessory |
| **ThirdPartyMqttBody** | 6 | Third-party MQTT config |
| **WeatherPlanBody** | 4 | Weather forecast |
| **TimerTaskBody** | 5 | Schedule/task payload |
| **ChargeDischargeBody** | 5 | SOC limits |
| **EnergyPriceBody** | 15+ | Energy price config |
| **SmartModeBody** | 10+ | Smart mode config |
| **PvSource** / **PvUsage** | 2 each | PV energy flow |
| **BatterySources** / **BatteryUsage** | 2 each | Battery energy flow |
| **HomeSource** | 2 | Home energy flow |
| **StatisticBody** | 30+ | Lifetime/today counters |

---

## 2. Current State

### 2.1 File Line Counts

| Module | Lines | Status |
|--------|------:|--------|
| `coordinator.py` | 10,091 | **MONOLITHIC — TO SPLIT** |
| `sensor.py` | 5,486 | large but functional |
| `util.py` | 2,705 | utility, keep as-is |
| `const.py` | 2,078 | constants (88 cmds, 25 msgTypes, field defs) |
| `client/_http.py` | 806 | HTTP base mixin ✅ |
| `client/api.py` | 93 | API facade ✅ |
| `client/_endpoints/*.py` | ~2,146 | 9 domain endpoint modules ✅ |
| `client/mqtt_push.py` | 709 | Cloud MQTT client ✅ |
| `client/mqtt_state.py` | 250 | MQTT connection manager ✅ |
| `client/mqtt_command.py` | 174 | MQTT command publish ✅ |
| `client/local_mqtt.py` | 571 | Third-party MQTT ✅ |
| `client/third_party_mqtt_codec.py` | 195 | MQTT codec ✅ |
| `client/ble.py` | 699 | BLE frame helpers ✅ |
| `client/ble_transport.py` | 908 | BLE GATT transport ✅ |
| `client/_crypto.py` | 82 | Crypto helpers ✅ |

### 2.2 Coordinator Section Map (Current)

| Lines | Section | Responsibility | RE doc mapping |
|-------|---------|----------------|----------------|
| 1–718 | **Imports + helpers** | 200+ imports, helper functions, `_raise_config_entry_auth_failed` | — |
| 719–1001 | **Module constants** | `_METRIC_SOURCE_FALLBACKS`, stat intervals, class-level frozensets | §2 Stats und Trends |
| 1002–1245 | **`__init__`** | All instance attrs, MQTT/BLE managers, caches, timers | — |
| 1246–1461 | **MQTT state management** | Thin wrappers around `MqttConnectionManager` | §3 MQTT Protocol |
| 1462–2606 | **BLE transport** | BLE commands, notifications, frame handling, GATT helpers | §4 BLE Protocol |
| 2607–2797 | **Property merging** | `_merge_dict_values`, `_sync_property_aliases`, payload cleanup | §1.6 HomeBody merge |
| 2798–4343 | **Subdevice management** | Battery packs, CT, SmartMeter, Socket, Plug detection + merge | §1.5 BatteryPackSub |
| 4344–5330 | **Background queries + commands** | `async_set_*` setters, `_async_publish_command`, command dispatch | §1.4 Commands (88) |
| 5331–6003 | **Third-party MQTT bridge** | `async_get/set_third_party_mqtt_config`, local MQTT bridge | §1.5 ThirdPartyMqttBody |
| 6004–7424 | **Statistics import** | PV/home/battery/CT/meter trends, year backfill, daily cache, `verify_and_backfill` | §2 Stats und Trends |
| 7425–8832 | **`_async_update_data`** | Main poll cycle, HTTP fetches, MQTT merge, optimistic patches | §1 HTTP endpoints |
| 8833–9060 | **Background slow refresh** | `_launch_background_slow_refresh`, TTL-based metric caching | — |
| 9061–9219 | **Diagnostics** | `mqtt_diagnostics`, `diagnostics_data`, `rejection_metrics` | — |
| 9220–10091 | **Restored 24.05 features** | Offline/local cmd=113, live MQTT keys, local MQTT listener, discovery cache | §5 Offline/Local |

---

## 3. Target Module Structure

Mirrors the existing `client/` + `_endpoints/` pattern already established:

```
custom_components/jackery_solarvault/
├── coordinator.py              # ORCHESTRATOR ONLY (~3,000 lines)
│   ├── __init__                #   attrs, setup, teardown
│   ├── _async_update_data      #   main poll cycle (HTTP merge)
│   ├── _async_handle_mqtt      #   MQTT message routing (thin router)
│   └── diagnostics             #   diagnostics export
│
├── client/                     # TRANSPORT LAYER (already exists)
│   ├── __init__.py             #   JackeryApi re-export
│   ├── _http.py                #   HTTP base mixin (token mgmt, retry)
│   ├── _crypto.py              #   AES encrypt/decrypt (Layer C)
│   ├── api.py                  #   Facade: all HTTP endpoints
│   ├── _endpoints/             #   Domain-specific HTTP endpoints
│   │   ├── accessories.py      #     GET accessories (14 types)
│   │   ├── auth.py             #     POST login (Layer A: AES+RSA)
│   │   ├── device.py           #     GET device/property, system/list
│   │   ├── energy_price.py     #     GET/PUT energy price
│   │   ├── misc.py             #     GET alarms, weather, misc
│   │   ├── push.py             #     POST push register
│   │   ├── shelly.py           #     Shelly cloud-to-cloud
│   │   ├── smart_mode.py       #     GET/PUT smart mode
│   │   └── statistics.py       #     17 device + 5 system stat endpoints
│   ├── mqtt_push.py            #   Cloud MQTT (emqx.jackeryapp.com:8883)
│   ├── mqtt_state.py           #   MqttConnectionManager
│   ├── mqtt_command.py         #   MQTT command publish + retry
│   ├── local_mqtt.py           #   Third-party LAN MQTT subscriber
│   ├── third_party_mqtt_codec.py # MQTT codec (encode/decode)
│   ├── ble.py                  #   BLE frame build/parse (Layer C)
│   └── ble_transport.py        #   BLE GATT transport
│
├── handlers/                   # NEW: MQTT/BLE message handlers
│   ├── __init__.py
│   ├── live.py                 #   UploadCombineData (35-field HomeBody), DevicePropertyChange
│   ├── subdevice.py            #   BatteryPackSub (9 fields), AccCTBody (26 fields)
│   ├── weather.py              #   WeatherPlanBody (4 fields), storm alerts
│   ├── schedule.py             #   TimerTaskBody (5 fields), TOU schedules, tariff
│   ├── config_echo.py          #   WorkModelChange, EPS, standby, off-grid echoes
│   ├── ota.py                  #   OTA status, firmware version (UploadFirmwareVersion)
│   └── system_info.py          #   UploadSystemInfo (32-field SystemBody)
│
├── setters/                    # NEW: Command dispatch + optimistic patches
│   ├── __init__.py
│   ├── core.py                 #   _async_publish_command, _async_publish_command_ble_first
│   ├── device.py               #   async_set_work_model (cmd=108), async_set_standby (cmd=115/116), async_reboot (cmd=110)
│   ├── power.py                #   async_set_soc_limits (cmd=124), async_set_max_out (cmd=123), async_set_eps (cmd=109)
│   ├── schedule.py             #   async_add/update/delete_timer_task (cmd=3032/3033)
│   ├── price.py                #   async_set_energy_price, async_set_price_source
│   ├── mqtt_config.py          #   async_set_third_party_mqtt_config (cmd=113)
│   ├── storm.py                #   async_set_storm_alert (cmd=112), async_delete_storm_alert
│   └── grid.py                 #   async_set_max_feed_grid (cmd=125), grid power limits
│
├── stats/                      # NEW: Statistics import + backfill
│   ├── __init__.py
│   ├── importer.py             #   PV/home/battery/CT/meter stat import to HA Recorder
│   ├── backfill.py             #   Year month-backfill, daily cache coordination
│   ├── validators.py           #   verify_and_backfill, source hierarchy checks
│   ├── slow_refresh.py         #   _launch_background_slow_refresh, TTL caching
│   └── entity_stats.py         #   Entity-statistic offset/repair (hourly sum/state)
│
├── models/                     # NEW: Shared data models
│   ├── __init__.py
│   ├── types.py                #   TypedDicts for DTOs (already exists)
│   ├── payload.py              #   Payload shape constants (PAYLOAD_DEVICE, etc.)
│   └── property_merge.py       #   _merge_dict_values, _sync_property_aliases, cleanup
│
├── subdevices/                 # NEW: Subdevice detection + merge
│   ├── __init__.py
│   ├── detector.py             #   _is_subdevice_payload, _is_battery_pack_payload, devType checks
│   ├── battery_pack.py         #   BatteryPackSub merge, aging, stale cleanup
│   ├── ct.py                   #   AccCTBody CT/SmartMeter frame handling
│   └── socket.py               #   Socket/Plug detection (devType=subType enums)
│
├── local_bridge.py             # NEW: Third-party MQTT bridge logic (lines 5331–6003)
│   # ThirdPartyMqttBody: {enable, ip, port, userName, password, token}
│   # Cmd 113 (SET) / Cmd 114 (GET) — encrypted via Layer C
│
├── const.py                    # EXISTING — all 88 commands, 25 msgTypes, field defs
├── types.py                    # EXISTING — TypedDicts
├── util.py                     # EXISTING — shared helpers
├── entity.py                   # EXISTING — base entity
├── sensor.py                   # EXISTING — sensor platform
├── switch.py                   # EXISTING — switch platform
├── number.py                   # EXISTING — number platform
├── select.py                   # EXISTING — select platform
├── text.py                     # EXISTING — text platform
├── binary_sensor.py            # EXISTING — binary sensor platform
├── button.py                   # EXISTING — button platform
├── config_flow.py              # EXISTING — config/options flow
├── diagnostics.py              # EXISTING — diagnostics export
├── repairs.py                  # EXISTING — repair issues
├── services.py                 # EXISTING — HA services
├── ingest.py                   # EXISTING — data ingestion
├── discovery_cache.py          # EXISTING — device discovery cache
├── local_daily_cache.py        # EXISTING — daily stat cache
├── mqtt_session_cache.py       # EXISTING — MQTT session cache
└── __init__.py                 # EXISTING — setup/unload
```

---

## 4. Migration Phases

### Phase 0: Key Namespace Unification ✅ DONE
**Goal:** Fix local MQTT not starting  
**Status:** COMPLETE — `__init__.py` reads both `CONF_LOCAL_MQTT_*` and `CONF_THIRD_PARTY_MQTT_*`  
**Validation:** Local MQTT starts when options are set

---

### Phase 1: Extract Property Merging & Payload Helpers ✅ DONE
**Source:** `coordinator.py` lines 2607-2797  
**Target:** `models/property_merge.py` (67 lines) + `models/__init__.py`  
**Status:** COMPLETE - pure functions extracted, coordinator methods are thin wrappers  
**Size:** 67 lines extracted, coordinator reduced by 624 lines  
**RE mapping:** HomeBody (35 fields) merge logic, property alias sync

**Methods to move:**
- `_merge_dict_values()` → `merge_dict_values()` (module-level)
- `_sync_property_aliases()` → `sync_property_aliases()`
- `_merge_main_properties()` → `merge_main_properties()`
- `_merge_main_properties_for_device()` → `merge_main_properties_for_device(coordinator, ...)`
- `_active_property_overrides()` → `active_property_overrides(coordinator, ...)`
- `_apply_local_property_patch()` → `apply_local_property_patch(coordinator, ...)`
- `_sanitize_main_properties()` → `sanitize_main_properties()`
- `_clean_property_payload()` → `clean_property_payload()`
- All `_*HINT_KEYS`, `_MAIN_PROPERTY_ALIAS_PAIRS` frozensets → `models/payload.py`

**Dependencies:** `const.py` field constants only  
**Risk:** LOW — pure functions, no side effects  
**Validation:** `ruff check`, `mypy`, existing tests pass

---

### Phase 2: Extract Subdevice Management (IN PROGRESS)
**Source:** `coordinator.py` lines 2762-4024  
**Target:** `subdevices/` package  
**Size:** ~1,260 lines (detection: 387 lines extracted, merge ops: pending)  
**RE mapping:** BatteryPackSub (9 fields), AccCTBody (26 fields), devType/subType enums from `source-of-truth/jackery_entity_field_candidates_v2.json`

**2a - Detection functions ✅ DONE:**
- `subdevices/detector.py` (387 lines): 20 pure functions for subdevice identification
- `is_subdevice_payload`, `normalize_battery_pack_payload`, `looks_like_battery_pack`, `battery_packs_from_source`
- `subdevice_serial`, `subdevice_id`, `subdevice_identity_values`, `subdevice_dev_type`
- `is_smart_meter_accessory`, `smart_meter_accessories`, `smart_meter_accessory_device_id`
- `has_smart_meter_accessory`, `has_meter_head_accessory`, `has_smart_plug_accessory`
- `subdevice_accessories`, `subdevice_stat_id`, `entry_subdevice_candidates`

**2b - Coordinator wiring (PENDING):**
- Update coordinator to import from `subdevices/detector.py`
- Replace inline detection methods with delegating wrappers
- Keep merge methods in coordinator (depend on coordinator state)

**2c - Merge functions (DEFERRED):**
- Battery pack merge, stale cleanup, OTA enrichment, CT merge, Shelly merge
- These depend heavily on coordinator state (`_slow_cache`, `_pending_device_removals`, `_property_overrides`)
- Deferred to avoid high-risk extraction in the same PR

---

### Phase 3: Extract Statistics Import & Backfill
**Source:** `coordinator.py` lines 6004–7424, 8833–9060  
**Target:** `stats/` package  
**Size:** ~1,425 + 228 = ~1,653 lines  
**RE mapping:** 17 device stat endpoints + 5 system stat endpoints from `source-of-truth/Jackery_2.1.1_Stats_und_Trends.md`, StatisticBody (30+ fields)

**Files:**
- `stats/importer.py` (~600 lines): All `_async_import_*` stat methods (pv_trends, home_trends, battery_trends, device_stat, system_stat)
- `stats/backfill.py` (~400 lines): Year month-backfill, `_expanded_year_series`, daily cache coordination
- `stats/validators.py` (~200 lines): `verify_and_backfill`, source hierarchy checks, `_stat_row_start`
- `stats/slow_refresh.py` (~228 lines): `_launch_background_slow_refresh`, TTL caching
- `stats/entity_stats.py` (~200 lines): Entity-statistic offset/repair, `_async_entity_statistic_offsets`
- `stats/__init__.py`: Re-exports

**Dependencies:** `client/_endpoints/statistics.py`, `local_daily_cache.py`  
**Risk:** HIGH — Recorder integration, needs stat metadata tests  
**Validation:** `test_stat_metadata.py`, `test_power_math.py`, coverage gates

---

### Phase 4: Extract Command Dispatch (Setters)
**Source:** `coordinator.py` lines 4344–5330  
**Target:** `setters/` package  
**Size:** ~986 lines  
**RE mapping:** 47 home commands (cmd 107–3044), body templates from `source-of-truth/hbxn_commands.html` + `source-of-truth/jackery_command_catalog_v2.html`

**Files:**
- `setters/core.py` (~200 lines): `_async_publish_command`, `_async_publish_command_ble_first`, optimistic patching
- `setters/device.py` (~150 lines): `async_set_work_model` (cmd=108), `async_set_standby` (cmd=115), `async_set_auto_standby` (cmd=116), `async_reboot` (cmd=110)
- `setters/power.py` (~150 lines): `async_set_soc_limits` (cmd=124), `async_set_max_output_power` (cmd=123), `async_set_eps` (cmd=109)
- `setters/grid.py` (~100 lines): `async_set_max_feed_grid` (cmd=125), grid power limits
- `setters/schedule.py` (~150 lines): Timer task CRUD (cmd=3032/3033), TOU schedule (cmd=130/131)
- `setters/price.py` (~100 lines): `async_set_energy_price`, `async_set_price_source`, `async_set_price_mode_dynamic`
- `setters/mqtt_config.py` (~150 lines): `async_set_third_party_mqtt_config` (cmd=113), `async_update_third_party_mqtt_config`
- `setters/storm.py` (~86 lines): Storm alert set/delete (cmd=112)
- `setters/__init__.py`: Re-exports

**Dependencies:** `client/api.py`, `client/mqtt_command.py`, `client/ble_transport.py`  
**Risk:** MEDIUM — command dispatch affects all entities  
**Validation:** Button entity tests, service tests

---

### Phase 5: Extract MQTT/BLE Message Handlers
**Source:** `coordinator.py` lines 1462–2606 (BLE), `_async_handle_mqtt_message` body  
**Target:** `handlers/` package  
**Size:** ~1,144 (BLE) + ~800 (MQTT handlers) = ~1,944 lines  
**RE mapping:** 25 MQTT message types, BLE cmd routing (cmd=107,111,121,120)

**Files:**
- `handlers/live.py` (~400 lines): `UploadCombineData` (HomeBody 35 fields), `DevicePropertyChange` merge
- `handlers/subdevice.py` (~300 lines): `UploadSubDeviceIncrementalProperty` (BatteryPackSub), `QuerySubDeviceInfo`
- `handlers/system_info.py` (~200 lines): `UploadSystemInfo` (SystemBody 32 fields)
- `handlers/weather.py` (~100 lines): Weather alerts, storm warnings, `UploadWeatherPlan`
- `handlers/schedule.py` (~200 lines): `DownloadDeviceSchedule`, `TOUSchedule`, tariff, `ControlDeviceSchedule`
- `handlers/config_echo.py` (~200 lines): `WorkModelChange`, EPS, standby, off-grid echoes
- `handlers/ota.py` (~100 lines): `UploadOTAStatus`, `UploadFirmwareVersion`
- `handlers/__init__.py`: Re-exports

**Dependencies:** `models/property_merge.py`, `subdevices/`  
**Risk:** HIGH — MQTT merge is the hot path  
**Validation:** `test_mqtt_protocol_contract.py`, `test_mqtt_stability.py`

---

### Phase 6: Extract Local Bridge
**Source:** `coordinator.py` lines 5331–6003  
**Target:** `local_bridge.py`  
**Size:** ~672 lines  
**RE mapping:** ThirdPartyMqttBody {enable, ip, port, userName, password, token}, cmd=113/114, Layer C encrypted, local MQTT subscribe

**Responsibilities:**
- Third-party MQTT config get/set (cmd 113/114)
- Local MQTT start/stop (`async_start_local_mqtt_listener`)
- Bridge lifecycle management
- `decode_third_party_mqtt_config_body` (Layer C decryption)
- `stable_third_party_mqtt_token` (token generation/validation)

**Dependencies:** `client/local_mqtt.py`, `client/third_party_mqtt_codec.py`, `client/_crypto.py`  
**Risk:** LOW — isolated feature  
**Validation:** Third-party MQTT tests

---

### Phase 7: Slim Down Coordinator
**After Phases 1–6, coordinator.py should contain:**
1. `__init__` (~300 lines): attrs, setup, teardown
2. `_async_update_data` (~1,200 lines): Main poll cycle (HTTP fetches → merge → emit)
3. `_async_handle_mqtt_message` (~200 lines): Thin router to handlers/
4. `diagnostics` (~200 lines): Export
5. Module constants (~100 lines)

**Target:** ~3,000 lines  
**Remaining:** Thin orchestrator that wires everything together

---

## 5. Validation Gate (Per Phase)

Per AGENTS.md §5 and §7:

```bash
ruff check .
ruff format --check .
python -m mypy custom_components/jackery_solarvault
python -m scripts.hassfest --integration-path custom_components/jackery_solarvault
pytest-homeassistant-custom-component -q
python -m scripts.enforce_coverage_gates --coverage-xml coverage.xml
```

**Per-phase requirements:**
- [ ] All existing tests pass
- [ ] New module has 100% branch coverage for moved code
- [ ] No new `# pragma: no cover` without justification
- [ ] Regression test for every bug fix
- [ ] `ruff check` clean
- [ ] `mypy --strict` clean
- [ ] `hassfest` passes

---

## 6. Dependency Graph

```
Phase 0 ✅ ─────────────────────────────────────────────┐
                                                        │
Phase 1: models/property_merge.py                       │
    │                                                   │
    ├── Phase 2: subdevices/                            │
    │       │                                           │
    │       └── Phase 5: handlers/                      │
    │               │                                   │
    │               └── Phase 7: coordinator slim       │
    │                                                   │
    ├── Phase 3: stats/                                 │
    │       │                                           │
    │       └── Phase 7: coordinator slim               │
    │                                                   │
    ├── Phase 4: setters/                               │
    │       │                                           │
    │       └── Phase 7: coordinator slim               │
    │                                                   │
    └── Phase 6: local_bridge.py                        │
            │                                           │
            └── Phase 7: coordinator slim ──────────────┘
```

**Parallelizable:** Phases 2, 3, 4, 6 can run in parallel after Phase 1  
**Sequential:** Phase 5 depends on Phase 2; Phase 7 depends on all

---

## 7. RE-Doc Cross-Reference

| Coordinator Section | RE Doc Reference | Key Fields/DTOs |
|--------------------|------------------|-----------------|
| Property merging (2607–2797) | `source-of-truth/jackery_complete_reference.json` HomeBody | 35 fields: soc, batSoc, cellTemp, pvPw, batInPw, batOutPw, gridInPw, gridOutPw, ... |
| Subdevice mgmt (2798–4343) | `source-of-truth/jackery_complete_reference.json` BatteryPackSub | 9 fields: deviceSn, devType, subType, soc, cellTemp, inPw, outPw, commState, ... |
| Command dispatch (4344–5330) | `source-of-truth/hbxn_commands.html` + `source-of-truth/jackery_command_catalog_v2.html` | 88 cmds (47 home + 41 portable), body templates from BoxControlFormat |
| MQTT handlers (1462–2606) | `source-of-truth/jackery_complete_reference.json` msgTypes | 25 message types with actionId mapping |
| Statistics (6004–7424) | `source-of-truth/Jackery_2.1.1_Stats_und_Trends.md` | 17 device + 5 system stat endpoints, energy flow objects |
| Third-party bridge (5331–6003) | `source-of-truth/Jackery_2.1.1_RE_Supplement.md` §5 | ThirdPartyMqttBody, cmd=113/114, Layer C encrypted |
| HTTP endpoints (7425–8832) | `source-of-truth/jackery_http_api_endpoints_v2.html` | 112 endpoints, 64 implemented |
| BLE transport (1462–2606) | `source-of-truth/Jackery_2.1.1_RE_Documentation.md` §4 | BLE cmd routing, Layer C encryption |
| Crypto layers | `source-of-truth/Jackery_2.1.1_RE_Crypto_and_DTOs.md` | Layer A (login), B (MQTT auth), C (payload) |

---

## 8. Risk Mitigation

| Risk | Impact | Mitigation |
|------|--------|------------|
| MQTT merge breaks live data | **CRITICAL** | Keep `_push_partial_update` pattern; never call `async_set_updated_data` from MQTT |
| Statistics import breaks Recorder | **HIGH** | Keep `async_import_statistics` calls identical; regression test every bucket |
| Battery pack IDs change | **HIGH** | Freeze `unique_id` contract; `test_battery_pack_stability.py` |
| BLE commands fail silently | **MEDIUM** | BLE is FALLBACK; HTTP always primary; log at WARNING |
| HTTP timeout too aggressive | **MEDIUM** | `SLOW_ENDPOINT_TIMEOUT_SEC=30` for pv_trends; `REQUEST_TIMEOUT_SEC=10` for fast |
| Circular imports | **LOW** | Handlers/setters import from `client/` and `models/`, never from `coordinator` |
| Command body format mismatch | **HIGH** | MQTT body format ≠ HTTP body format; never merge code paths (per §1.2 rules) |
| Layer C key mismatch | **MEDIUM** | BLE key = `bluetoothKey`; MQTT payload uses same key; validate in `_crypto.py` |

---

## 9. Open Items

1. **Key namespace consolidation:** `CONF_THIRD_PARTY_MQTT_TOPIC_FILTER` is still used in options flow — decide whether to migrate to `CONF_LOCAL_MQTT_TOPIC` or keep dual-read
2. **`_async_update_data` is still 1,400+ lines** — needs internal decomposition into `_fetch_device_properties`, `_fetch_statistics`, `_merge_mqtt_payloads`
3. **Diagnostics section** — move to `diagnostics.py` or keep thin wrapper in coordinator
4. **`_raise_config_entry_auth_failed`** — keep in coordinator or move to `util.py`
5. **Translation keys** — validate `strings.json` consistency after refactoring
6. **OTA endpoints** (3 endpoints) — not yet implemented; add to `client/_endpoints/ota.py` when needed
7. **Portable commands** (41 cmds) — intentionally excluded (SolarVault home only); document in `const.py`

---

## 10. Session Recovery

If session is lost, resume by:
1. Read this file (`docs/COORDINATOR_REFACTORING_PLAN.md`)
2. Check which phases are marked DONE
3. Run `ruff check . && mypy custom_components/jackery_solarvault && pytest-homeassistant-custom-component -q` to verify current state
4. Continue with next pending phase

**Current session state (2026-06-12):**
- Phase 0: ✅ DONE
- Phase 1: ✅ DONE (models/property_merge.py: 67 lines)
- Phase 2: IN PROGRESS (subdevices/detector.py: 387 lines extracted)
- Phases 3-7: PENDING
