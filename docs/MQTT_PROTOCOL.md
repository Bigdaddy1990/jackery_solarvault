# Jackery SolarVault MQTT Protocol Reference

**Source**: PCAPNG capture `PCAPdroid_18_Apr__23_45_57.pcapng` via Frida SSL-pinning
bypass (19. April 2026).

## Connection

```
Host:      emqx.jackeryapp.com:8883
Protocol:  MQTT over TLS 1.2 (runtime uses aiomqtt default protocol version, MQTT 3.1.1)
Keep-Alive: 60s
QoS:       0 (At most once)
Clean Session: Yes
```

## Authentication

**Status: solved (runtime-verified via Frida).**

MQTT credentials are derived as follows:
- `clientId = "<userId>@APP"`
- `username = "<userId>@<androidId>"`
- `raw = base64_decode(login_response.data.mqttPassWord)` (32 bytes)
- `key = raw` (full 32 bytes)
- `iv = raw[:16]`
- `password = base64( AES-256-CBC-PKCS5( username_utf8, key, iv ) )`

| Field | Format | Example |
|---|---|---|
| Client ID | `<userId>@APP` (23 chars) | `2041425653828689920@APP` |
| Username | `<userId>@<androidId>` (53 chars in observed account) | `2041425653828689920@271c55f5731fa3d9ba1fe131e088946e0` |
| Password | 88 chars base64 | `MOid8oiQI9+SJx+8VGQqNP60+XZriSGcbCuXM6+w0QkcP7jU9AuPX0j6qLImNqxwrCuBwUDBgNk3POllIB8hHw==` |

**Decoded password length**: 88 chars base64 → 64 raw bytes (AES-CBC ciphertext).

## Topics

All topics live under `hb/app/<userId>/...` where `<userId>` is the numeric
Jackery user ID.

| Topic | Direction | Purpose |
|---|---|---|
| `hb/app/<userId>/device` | Inbound (subscribed) | Live telemetry from device |
| `hb/app/<userId>/alert` | Inbound | Alerts / warnings |
| `hb/app/<userId>/config` | Inbound | Config change notifications |
| `hb/app/<userId>/notice` | Inbound | General notices |
| `hb/app/<userId>/command` | Outbound (published) | Commands to device |

## Message envelope

Every PUBLISH payload is a JSON object with this envelope:

```json
{
  "deviceSn": "HR2C04000280HH3",
  "id": 1776548805134,
  "version": 0,
  "messageType": "...",
  "actionId": 3022,
  "timestamp": 1776548805134,
  "body": { ... }
}
```

`id` and `timestamp` are Unix-ms timestamps (effectively the request ID).
`actionId` identifies the specific operation within a `messageType`.

## Command messages (app → device via `command` topic)

### DevicePropertyChange (cmd=107) ⭐ Main setters
Simple property writes. `actionId` varies per property group.

| actionId | Fields | Purpose |
|---|---|---|
| 3023 | `swEps` (0/1) | EPS output switch |
| 3022 | `socChargeLimit`/`socChgLimit` (%) | Battery charge SOC limit |
| 3028 | `socDischargeLimit`/`socDischgLimit` (%) | Battery discharge SOC limit |
| 3030 | `reboot` (1) | Device restart |
| 3038 | `maxOutPw` (W) | Max output power |

Example:
```json
{"body": {"socChgLimit": 95, "socDischgLimit": 10, "cmd": 107}}
```

### ControlCombine (cmd=121)
Complex combined controls.

| actionId | Fields | Purpose |
|---|---|---|
| 3021 | `isAutoStandby` (0/1) | Auto standby mode |
| 3027 | `workModel` (2/4/7/8) | Work mode (self-consumption, time-based, etc.) |
| 3029 | `maxFeedGrid` (W) | ⭐ Max grid feed-in — THIS is the 800W setting |
| 3039 | `offGridDown` (0/1) | Off-grid shutdown |
| 3040 | `offGridTime` (min) | Off-grid timer |
| 3043 | `defaultPw` (W) | Default standby power |
| 3044 | `isFollowMeterPw` (0/1) | Smart meter following |

### Weather commands (`cmd` omitted)
For storm-related commands the app sends `cmd=0` and omits the `cmd` field
from `body`.

| messageType | actionId | Fields | Purpose |
|---|---|---|---|
| `ControlCombine` | 3036 | `wps` (0/1) | Storm warning switch |
| `SendWeatherAlert` | 3034 | `minsInterval` | Storm warning lead time |
| `CancelWeatherAlert` | 3035 | `alertId` | Delete active storm alert |

### ControlSubDevice (cmd=111, actionId=3026)
Control sub-devices (plugs, CTs).
```json
{"body": {"devType": 3, "deviceSn": "5c013b048e3c", "schePhase": 2, "cmd": 111}}
```

### DownloadDeviceSchedule (cmd=112)
Push schedule rules to device.

| actionId | Purpose |
|---|---|
| 3015 | Schedule/task-plan response variant (app/integration route) |
| 3016 | Schedule/task-plan response variant (app/integration route) |
| 3017 | Set schedule entry |
| 3018 | Clear/manage schedule |

Schedule body example:
```json
{"actionType": 3, "taskType": 2, "mode": 2, "pw": 700,
 "sysSwitch": 1, "end": "06:00", "loops": "1111111",
 "start": "04:00", "tid": "1776477689", "cmd": 112}
```

### Query* (various cmds)
Pull data on demand. These are equivalent to the REST GET endpoints.

| messageType | actionId | cmd | Pulls |
|---|---|---|---|
| QueryCombineData | 3019 | 120 | UploadCombineData (full state) |
| QueryDeviceProperty | 3011 | 106 | DevicePropertyChange (properties) |
| QuerySubDeviceGroupProperty | 3014/3031/3037 | 110 | Sub-device groups |
| QueryWeatherPlan | 3020 | 23 | Weather-based plan |

## Telemetry messages (device → app via `device` topic)

### UploadCombineData (cmd=121, actionId=3019) ⭐ Full state snapshot
Pushed periodically. Contains everything needed for the HA sensors:
- `soc`, `pvPw`, `batInPw`, `batOutPw`, `gridInPw`, `gridOutPw`
- `workModel`, `isAutoStandby`, `isFollowMeterPw`
- `maxFeedGrid`, `maxSysOutPw`, `offGridTime`, `defaultPw`
- `pv1`, `pv2`, `pv3`, `pv4` (per-MPPT details)
- `stat`, `ongridStat`, `gridSate`, `batState`, `ctStat`
- `funcEnable`, `tempUnit`, `chargePlanPw`, `energyPlanPw`, `standbyPw`
- `wpc`, `wps` (weatherwarning?)

### DevicePropertyChange (cmd=107, actionId=0 or 3011) ⭐ Live deltas
Pushed on change. Matches the `/v1/device/property` REST response 1:1:
- `soc`, `batSoc`, `cellTemp`
- `pvPw`, `batInPw`, `batOutPw`, `inOngridPw`, `outOngridPw`
- `stackInPw`, `stackOutPw`, `swEpsInPw`, `swEpsOutPw`
- `swEps`, `swEpsState`, `batState`, `batNum`
- `socChgLimit`, `socDischgLimit`, `maxOutPw`, `maxGridStdPw`, `maxInvStdPw`
- `autoStandby`, `ability`, `maxIotNum`, `ethPort`
- `wname`, `wip`, `mac`, `wsig`
- `pv1`, `pv2`, `pv3`, `pv4`

### UploadIncrementalCombineData (cmd=121)
Incremental power-flow updates (likely sent on every small change).
Smaller payload than UploadCombineData.

### UploadSubDeviceIncrementalProperty (cmd=111)
Per-phase power data (3-phase systems). Fields:
- `devType`, `deviceSn` (sub-device identifier)
- `aPhasePw`, `bPhasePw`, `cPhasePw` (phase-in power, W)
- `anPhasePw`, `bnPhasePw`, `cnPhasePw` (phase-neutral power, W)
- `tPhasePw`, `tnPhasePw` (total phase power)
- `inPw`, `outPw`, `schePhase`, `subType`

### UploadSubDeviceGroupProperty (cmd=110)
Grouped sub-device inventory:
- `batteryPacks`, `collectors`, `cts`, `plugs` (arrays)
- `devType` (1/2/6 for different groups)
- actionId variants seen/routed by integration: `3014`, `3031`, `3033`, `3037`

### UploadWeatherPlan (cmd=23)
Weather-based storm plan:
```json
{"storm": [], "cmd": 23}
```

## HA integration contract

The integration must keep these MQTT rules aligned with this document and
`APP_POLLING_MQTT.md`:

1. **Credential derivation**: Use `userId`, REST `mqttPassWord`, and the
   integration `macId` from the same login session. The MQTT `clientId` is
   `<userId>@APP`, the username is `<userId>@<macId>`, and the password is
   derived with the app AES-CBC formula above.

2. **Library**: Use the async-native `aiomqtt` client (asyncio wrapper around `paho-mqtt`).

3. **Client flow**:
   - REST login → get `userId`, `mqttPassWord`
   - derive MQTT credentials from the same login session
   - connect to `emqx.jackeryapp.com:8883` over TLS
   - subscribe to `hb/app/<userId>/device,alert,config,notice`
   - on connect: publish app snapshot queries (`QueryCombineData`,
     `QueryWeatherPlan`, and `QuerySubDeviceGroupProperty`)
   - handle inbound messages by updating coordinator state
   - expose setters as HA entities and publish `DevicePropertyChange`,
     `ControlCombine`, `SendWeatherAlert`, or `CancelWeatherAlert` commands

4. **Benefit**: fast SOC/power updates, instant writes, and much lower HTTP
   load than pure polling.
