# Jackery App 2.1.1 — Vollständige Reverse-Engineering-Dokumentation für Home Assistant

> **App:** `com.hbxn.jackery` v2.1.1 (versionCode 93) · min SDK 24 · target SDK 35
> **Quellen:** vollständige XAPK (6 DEX, alle dekompiliert via androguard), REST-Capture, Frida-Hooks
> **Zweck:** vollständige Referenz für eine eigene Home-Assistant-Integration (Cloud-MQTT + REST), inkl. Shelly-Cloud2Cloud

## Status-Legende
✅ verifiziert (Capture/Frida/Bytecode) · 🔶 aus Code abgeleitet, Laufzeit-Detail offen · ❗ benötigt/zu erfassen

---

## 1. Architektur

```
[Powerstation/System] ──WiFi/4G──┐
                                  ▼
                         [emqx.jackeryapp.com:8883  (MQTT/TLS)]  ◄──► App / HA
                                  ▲
[Shelly-Geräte] ─► [*.shelly.cloud] ─OAuth─► [iot.jackeryapp.com]  ◄──► App / HA
                                  ▲ REST (HTTPS)
                              App / HA
```

Drei Wege, ein Gerät in HA zu bringen (Details unten):
1. **Cloud-MQTT** (`emqx.jackeryapp.com`) — Echtzeit-Telemetrie + Steuerung. Primärweg.
2. **REST** (`iot.jackeryapp.com`) — Login, Geräteliste, Statistiken, Konfiguration, OTA, Shelly.
3. **Lokal BLE (Nordic)** — Provisionierung & evtl. lokale Steuerung (separater Strang, §10).

Bibliotheken: **HiveMQ** MQTT-Client ✅ · **BlankJ AndroidUtilCode** (Krypto/Device) ✅ · **EasyHttp** (REST, je Endpoint eine `*Api`-Klasse) ✅ · Umeng (Analytics), Firebase/Crashlytics, JPush/极光 (`Jg`), QWeather.

---

## 2. Cloud-Verbindungen (alle Hosts)

| Host | Zweck | Status |
|------|-------|--------|
| `iot.jackeryapp.com` | **REST-API** (Basis, vmtl. mit `/v1/`-Segment) | ✅ |
| `emqx.jackeryapp.com:8883` | **MQTT-Broker** (TLS) | ✅ |
| `home.shelly.cloud`, `my.shelly.cloud`, `shelly.cloud` | Shelly OAuth-Login + Account-Link | ✅ |
| `widget-page.qweather.net` | Wetter-Widget (QWeather) | ✅ |
| `ht-iotdemo.s3.ap-southeast-1.amazonaws.com` | AWS S3 (Assets/Demo, Region Singapur) | ✅ |
| `*.umeng.com`, `umengcloud.com` | Umeng-Analytics (chinesisch) | ✅ |
| `app-measurement.com`, `firebase*`, `crashlytics` | Google/Firebase | ✅ |
| `www.jackery.com` | Marketing/Links | ✅ |

> ⚠️ Telemetrie für die HA-Integration kommt aus **emqx** (MQTT) + **iot** (REST). Umeng/Firebase sind reines Tracking — in HA ignorieren.

---

## 3. Authentifizierung (VOLLSTÄNDIG aus Bytecode geklärt)

### 3.1 REST-Login
`POST {base}/auth/login` → Response:
```json
{ "token":"<JWT HS256>", "code":0, "msg":"...",
  "data":{ "userId":"<snowflake>", "username":"jky_xx…", "appUserName":"jky_xx…",
           "mqttPassWord":"<base64, 32 Byte>", "account":"<email>",
           "nickname":"…", "mobPhone":null, "avatar":null, "passwordFlag":false },
  "encryption":false }
```
- JWT-Payload: `{currentTime, iss:"hbxn", exp, userId, iat}` (~30 Tage). Bearer für alle REST-Calls.
- **`mqttPassWord` rotiert pro Login** ✅ → MQTT-Credentials immer frisch nach dem Login berechnen.

### 3.2 `User`-Klasse (classes5) — Getter-Mapping ✅
`com.hbxn.jackery.router.bean.User` (Felder + obfuskierte Getter):

| Getter | Feld | | Getter | Feld |
|--------|------|---|--------|------|
| `a()` | account | | `e()` | nickname |
| `b()` | avatar | | `f()` | **userId** |
| `c()` | mobPhone | | `g()` | username |
| `d()` | **mqttPassWord** | | `h()` | passwordFlag (bool) |

Setter: `i()`=avatar, `j()`=nickname, `k()`=passwordFlag.
**Konvention der ganzen App:** Bean-Getter = `a,b,c,…` in Feld-Reihenfolge, Setter = ab `i()`. (Gilt für alle DTOs.)

### 3.3 MQTT-Credential-Algorithmus ✅ (aus `jc.e.f()` + BlankJ `e0/d0/c0`)

```
identifier = userId + "@APP"
username   = userId + "@" + deviceId          # deviceId = c0.o() (siehe 3.4)
key        = Base64.decode(mqttPassWord)       # d0.a() → 32 Byte
iv         = key[:16]                          # Arrays.copyOf(key,16)
password   = Base64.encode(                    # d0.d()
                AES/CBC/PKCS5Padding.encrypt(  # e0.p() → e0.w0(): SecretKeySpec(key,"AES")+IvParameterSpec(iv)
                    plaintext = username.getBytes(UTF-8),
                    key, iv ), NO_WRAP )
```
Vollständig durch Bytecode belegt — kein hardcodierter Key, reines AES-256-CBC. Plaintext ist der **username**-String. Ergebnis ist 64-Byte-Ciphertext → 88-Zeichen-Base64.

**Verifikation:** Mit einem *zusammengehörigen* Paar (mqttPassWord + MQTT-Passwort aus **derselben** Session) reproduzierbar. Die bisherigen Captures stammten aus verschiedenen Logins (mqttPassWord rotiert), daher kein Match — der `e0.q`-Frida-Hook liefert key/iv/data/output atomar zur finalen Bestätigung.

### 3.4 deviceId (`c0.o()` = BlankJ `getUniqueDeviceId`) 🔶
- `c0.o()` → `c0.q("",true)`: liest `KEY_UDID` aus SharedPreferences (Datei „Utils"); wenn vorhanden → Cache, sonst `c0.s()` generiert + persistiert.
- Format: `"2" + UUIDv3(androidId)` (MD5-basiert, ohne Bindestriche) bzw. `"9" + randomUUID` falls keine androidId. → 33 Zeichen, passt zur Beobachtung `271c55f5…`.
- **Für HA:** Eine **einmalig selbst erzeugte, feste deviceId** verwenden und konstant halten. Der Broker prüft mutmaßlich nur `password == AES(username)` (Self-Validation), daher ist die konkrete deviceId frei wählbar — beim ersten Connect-Test bestätigen. ❗

---

## 4. MQTT-Layer

| Parameter | Wert |
|-----------|------|
| Broker | `emqx.jackeryapp.com:8883` (TLS) ✅ |
| Client | HiveMQ (MQTT 3.1.1/5 – beim Connect bestätigen) 🔶 |
| Client-ID | `<userId>@APP` ✅ |
| Username | `<userId>@<deviceId>` ✅ |
| Password | siehe §3.3 ✅ |

### Topics ✅ — `%s` = **deviceSn** (Geräte-Seriennummer)
| Topic | Richtung (vermutet) | Inhalt |
|-------|---------------------|--------|
| `hb/app/<sn>/device` | Gerät→App | Geräte-/Telemetrie-Shadow |
| `hb/app/<sn>/command` | App→Gerät | Steuerbefehle (siehe §6) |
| `hb/app/<sn>/action` | App→Gerät | Aktionen/Tasks |
| `hb/app/<sn>/config` | bidirektional | Konfiguration (WiFi/MQTT/…) |
| `hb/app/<sn>/alert` | Gerät→App | Alarme/Faults |
| `hb/app/<sn>/notice` | Gerät→App | Hinweise/Events |

> Pub/Sub-Richtung & QoS final per `hb/app/<sn>/*`-Capture (mqtt-explorer auf entschlüsseltem Stream) oder Hook auf HiveMQ `publishes`/`subscribe` bestätigen. ❗

### Payload-Format 🔶
- Klasse `com.hbxn.control.device.cmd.home.HomeControlFormat`; Strings `Home EncryptParam` / `Home DecryptParam` direkt bei den AES-Strings → **Payloads sind wahrscheinlich ebenfalls AES-(de)kodiert** (gleiche `e0`-Util, sehr wahrscheinlich gleicher Key/Modus wie §3.3).
- Wrapper-Muster: `XxxBean { XxxBody { … } }` (Bean=Hülle, Body=Nutzlast). `MqttBean{ MqttBody{ online=… } }`.

---

## 5. REST-API — vollständige Endpunktliste (116 Klassen) ✅

Basis vermutlich `https://iot.jackeryapp.com/` (+ ggf. `/v1/`). Pfade wie via EasyHttp `getApi()` extrahiert (relativ).

### Auth / Account
| Api | Pfad |
|-----|------|
| LoginApi | `auth/login` |
| LoginOutApi | `auth/loginOut` |
| RegisterAccountApi | `auth/register` |
| JWTApi | `auth/generatedJwt` |
| GetVerificationCodeApi | `auth/verificationCode` |
| CheckVerificationCodeApi | `auth/check_verification` |
| ResetPasswordApi | `auth/modifyPassword` |
| UpdateUserInfoApi | `auth/modifyInfo` |
| UserHeadImgApi | `auth/headimg` |
| CancelAccountApi | `auth/cancel` |
| UpdateRegisterIdJgApi | `auth/updateRegisterId` (JPush) |
| UserInfoApi | `user/info` |

### Gerät — Bind/Verwaltung
| Api | Pfad |
|-----|------|
| DeviceBindApi | `device/bind` |
| DeviceUnBindApi | `device/unbind` |
| DeviceNickNameApi | `device/bind/nickname` |
| UserDeviceListApi | `device/bind/list` |
| QrCodeApi | `device/bind/qrcode` |
| DeviceShareBindApi | `device/accept_bind` |
| DeviceSharedListApi | `device/bind/shared` |
| DeviceSharedManagerListApi | `device/bind/share/list` |
| DeviceSharedRemoveApi | `device/bind/remove` |
| DeviceSharedRemoveAllApi | `device/bind/removeAll` |
| DeviceDetailApi | `device/property` |
| DeviceAcNickNameApi | `device/property/updateAcNickName` |
| PowerReportApi | `device/property/power3` |
| ReporeTimeZoneApi | `device/timezone` |
| StormAddressApi / StormUpdateAddressApi | `device/location` |

### Gerät — OTA
| Api | Pfad |
|-----|------|
| DeviceMqttOTASelectApi | `device/ota/list` |
| DeviceMqttOTAStartApi | `device/ota/update` |
| DeviceBleOTASelectApi | `device/ota/version/list` |
| DeviceBleOTALinkQuiryApi | `device/ota/bluetooth` |

### Statistik / Energie
| Api | Pfad | | Api | Pfad |
|-----|------|---|-----|------|
| BoxEleStatApi | `device/stat` | | TodayEnergyApi | `device/stat/today` |
| DeviceStatSocApi | `device/stat/soc` | | EleStorageStatApi | `device/stat/symmetry` |
| BoxPowerOutageStatApi | `device/stat/cutoff` | | StatProfitApi | `device/stat/profit` |
| SocialContributionsApi | `device/stat/carbon` | | PortableCtStatApi | `device/stat/ct/statics` |
| HomeStatApi | `device/stat/onGrid` | | EpsStatApi | `device/stat/eps` |
| PvStatApi | `device/stat/pv` | | BatteryStatApi | `device/stat/battery` |
| CtStatApi | `device/stat/ct` | | AccMeterStatApi | `device/stat/meter` |
| AccSocketStatApi | `device/stat/socket` | | DeviceChargeReportApi | `device/chargeReport` |
| DeviceStatDeviceStatistic | `device/stat/deviceStatistic` | | DeviceStatSystemStatistic | `device/stat/systemStatistic` |
| DeviceStatSocketStatistic | `device/stat/smartSocketStatistic` | | SysBatteryStatApi | `device/stat/sys/battery/trends` |
| SysHomeStatApi | `device/stat/sys/home/trends` | | SysPvStatApi | `device/stat/sys/pv/trends` |
| AiSmartScheduleApi | `device/stat/getSmartSchedulePrediction` | | | |

### DIY-System / Home (Mehrgeräte-Energiesysteme)
| Api | Pfad |
|-----|------|
| UserSystemListApi | `device/system/list` |
| DiyDeviceSystemApi | `device/system` |
| DiyDeviceSystemNameApi | `device/system/name` |
| ModifyDiyDeviceNameApi | `device/system/deviceName` |
| SystemBindExistApi | `device/system/exist` |
| DiyPropertySubShadowApi | `device/property/subShadow` |
| DiyPropertySystemShadowApi | `device/property/systemShadow` |
| ModifyDevicePvNameApi | `device/property/pv` |
| RecordDeviceMaxPowerApi | `device/deviceMaxPowerRecord/saveRecord` |
| SyncFaultsAndAlarmsDataApi | `device/alert` |
| SyncOfflineStatisticsDataApi | `device/offline/stat` |
| ParallelInStandardListApi | `api/diy/gcsList` |
| CountryZoneListApi | `api/diy/zoneList` |

### Zubehör (Accessories)
| Api | Pfad |
|-----|------|
| DeviceAccessoriesApi | `device/accessories` |
| DeviceAccessoriesListApi | `device/accessories/list` |
| DeviceAccessoriesExistApi | `device/accessories/exist` |
| DeviceJackeryAccessoriesExistApi | `device/accessories/exists` |
| DeviceAccessoriesBindApi | `device/accessories/bind` |
| DeviceAccessoriesUnbindApi | `device/accessories/unbind` |
| DeviceAccessoriesScannableApi | `device/accessories/scannable` |
| DeviceAccessoriesNameApi | `device/accessories/name` |
| SynchronizeSmartAccessoriesDataApi | `device/accessories/synchronizeSmartAccessoriesData` |
| DeviceBluetoothApi | `device/bluetoothKey` |
| BatteryPackApi | `device/battery/pack/list` |

### Strompreis (dynamisch, FlatPeak-Integration)
| Api | Pfad |
|-----|------|
| ElePriceDynamicApi | `device/dynamic/dynamicPrice` |
| ElePriceSourceListApi | `device/dynamic/priceCompany` |
| ElePriceSourceApi | `device/dynamic/saveDynamicMode` |
| EleSingleModeApi | `device/dynamic/saveSingleMode` |
| ElePriceSettingsApi | `device/dynamic/powerPriceConfig` |
| ElePriceHistoryConfigApi | `device/dynamic/historyConfig` |
| ElePriceAuthContractApi | `device/dynamic/saveContractAuth` |
| ElePriceAuthContractListApi | `device/dynamic/contractList` |
| ElePriceCancelAuthApi | `device/dynamic/cancelContractAuth` |
| FlatpeakAuthApi | `device/dynamic/saveLocationId` |
| DynamicPriceLoginUrlApi | `device/dynamic/loginUrl` |
| EleDeviceCurrencyApi | `device/currencies/deviceCurrency` |
| CurrencyListApi | `device/currencies/currencyList` |
| CurrencySettingsApi | `device/currencies/bindCurrency` |

### TOU (Time-of-Use) / Smart Mode
| Api | Pfad |
|-----|------|
| QueryTouPlanApi | `device/tou/queryTouPlan` |
| SaveTouPlanApi | `device/tou/saveTouPlan` |
| AiSmartConditionApi | `device/smartMode/checkIfSet` |
| AiSmartModeInfoApi | `device/smartMode/getSmartMode` |
| AiSmartModeStartApi | `device/smartMode/startSmartMode` |

### Sonstiges (App/Content/Push)
| Api | Pfad | | Api | Pfad |
|-----|------|---|-----|------|
| AppVersionApi | `app/version/getNewVersion` | | BannerApi | `app/banner/list` |
| MsgApi | `api/push/notifyList` | | UnreadMsgApi | `api/push/unreadCount` |
| PushSwitchConfigApi | `api/push/configSet` | | PushSwitchStatusApi | `api/push/configGet` |
| AlarmListApi | `api/alarm` | | AlarmDetailsApi | `api/alarm/detail` |
| HelpQuestionApi | `api/faqList` | | HelpQuestionAnswerApi | `api/faq/answer` |
| FeedBackApi | `api/file/feedback` | | ProductInstructionApi | `api/instruction` |
| PrivacyConsentApi | `api/agreeUpgrade` | | PrivacyNeedUpdateApi | `api/isUpgradeRequired` |

---

## 6. Steuerbefehle (MQTT `command`/`action`) ✅

Befehls-Konstanten aus dem Device-Control-Layer (`com.hbxn.control.device.cmd.*`). Sende-Format wird über `HomeControlFormat` gebaut (vmtl. JSON, evtl. AES-gewrappt).

### Ausgänge / Output
`CONTROL_OUTPUT_AC` · `CONTROL_OUTPUT_AC240` · `CONTROL_OUTPUT_DC` · `CONTROL_OUTPUT_DC_CAR` · `CONTROL_OUTPUT_DC_USB` · `CONTROL_OUTPUT_PRIORITY_SWITCH` · `CONTROL_AC_OFF_GRID_SWITCH` · `CONTROL_MAX_OUT_PW` · `AC_OUTPUT_MODE` · `AC_OUTPUT_COUNTDOWN` · `AC_OUTPUT_DELAY_OPEN_TIME` · `DC_OUTPUT_COUNTDOWN` · `DC_CAR_OUTPUT_COUNTDOWN` · `DC_USB_OUTPUT_COUNTDOWN`

### Gerät / System
`CONTROL_LIGHT` (+ `LIGHT_SETTINGS`, `DEFAULT_LIGHT_SETTINGS`) · `CONTROL_SCREEN` · `CONTROL_STANDBY` · `SYSTEM_CONTROL_AUTO_STANDBY` · `CONTROL_REBOOT` · `CONTROL_POWER_PACK_BLINK` · `USE_POWER_MODE` · `DEVICE_MODEL`

### Laden / Entladen / Energie
`AUTO_CHARGE` · `SETTING_CHARGE` · `SETTING_SUPER_CHARGE` · `SET_CHARGE_POWER` · `BATTERY_PRIORITY` · `DISCHARGE_MEMORY` (+ `SETTING_DISCHARGE_MEMORY`) · `ENERGY_STORAGE_CHARGE_LIMIT` · `SETTING_OUTPUT_PRIORITY` (+ `_SOC`) · `SET_CHARGE_DISCHARGE_LINE` · `CHARGE_PLAN` · `ADD/DELETE/UPDATE/GET/CURRENT_CHARGE_DISCHARGE_PLAN` · `BACKUP` · `SYSTEM_SET_FEED_GRID_POWER` · `SYNC_GRID_STANDARD` · `EV_CHARGE_OPTIONS`

### Smart / Timer / Tasks
`SMART_MODE` · `CUSTOM_MODE_TIMER` · `SMART_PLUG_TIMER` · `TIME_ELEC_TIMER` · `TIMER_TASK_ADD/DELETE/READ/UPDATE` · `SYSTEM_STORM_EVENT_SWITCH`

### Sub-Geräte / Zubehör
`SUB_CONTROL_SOCKET_SWITCH` · `SUB_CONTROL_SOCKET_PRI_ENABLE` · `SUB_SET_CT_SCHEDULE_PHASE` · `READ_SUB_DEVICE_SOCKET` · `BIND_SMART_PART` · `UNBIND_SMART_PART`

### WiFi/MQTT-Provisionierung & OTA & Sync
`WIFI` · `WRITE_WIFI_INFO` · `WRITE_WIFI_AND_MQTT_INFO` · `GET_WIFI_CONFIG` · `READ_WIFI_LIST` · `SYNC_MQTT_CONNECT_INFO` · `CUSTOM_MQTT` · `GET_DEVICE_OTA_VERSION` · `DEVICE_GET_OTA_PAGE_DATA` · `NOTIFY_DEVICE_CAN_OTA` · `NOTIFY_DEVICE_OTA_TOTAL_PAGE` · `CMD_SYNC` · `CMD_RST` · `CMD_RST_FULL`

> ⚠️ **`CUSTOM_MQTT` / `ThirdPartyMqtt`** (`ThirdPartyMqttBody{enable, ip, port, userName, password, token}`): erlaubt mutmaßlich, das Gerät auf einen **eigenen** MQTT-Broker zu konfigurieren → potenziell **cloud-freier** HA-Pfad. Hoch priorisiert verifizieren.

---

## 7. Telemetrie-Datenmodell

### 7.1 Geräte-Identität
`deviceSn`/`devSn` (Topic-Schlüssel) · `deviceName` · `model`/`modelClass`/`productModel` · `mac`/`emac`

### 7.2 Telemetrie-Felder (aus DTO/Shadow) 🔶
Bestätigte Feldnamen: `batSoc`/`soc`/`batterySOC` · `socChgLimit` · `socDischgLimit` · `pvPw` · `power` · `energyPlanPw` · `energyStorage` · `cellTemp` · `freq` · `offGridTime` · `volt1` · `switch` · `online` · `acpsp`
Komponenten-Bodies: `HomeBody`, `SystemBody`, `BoxBody`, `BatteryPackBody`/`BatteryPackSub`, `AccSocketBody`, `AccCTBody`, `PlugBody`, `CollectorBody`, `ComboDeviceBody`, `CtBody`, `PV`, `Ac`.
> Vollständiges Shadow-Schema am besten live aus `hb/app/<sn>/device` mitschneiden. ❗

### 7.3 Gerätemodelle (Explorer-Serie) ✅
`E240` · `E557` · `E900` · `E1000` · `E1500V2` · `E1800` · `E2000` · `E3000` · `E7647` · `E7987`
Gerätetypen: `DEVICE_TYPE_BOX` (Powerstation), `DEVICE_TYPE_PHONE`.

---

## 8. Zusatzgeräte / Zubehör (vollständig) ✅

| Konstante | Gerät |
|-----------|-------|
| `ACC_CT_906`, `ACC_CT_907`, `ACC_CT_2604` | Strom­wandler-Klemmen (CT, 3-phasig: `CT_A/B/C`) |
| `ACC_METER_892`, `ACC_METER_905`, `ACC_METER_910` | Energiezähler (`METER_HEAD`, `METER_PROBE`) |
| `ACC_SOCKET_904` | Smart-Socket/Steckdose |
| `SHELLY_PLUG_S` | Shelly Plus Plug S |
| `SHELLY_PLUG_SG3` | Shelly Plug S Gen3 |
| `SHELLY_PRO_3EM` | Shelly Pro 3EM (3-Phasen-Zähler) |
| `SHELLY_PRO_3EM63` | Shelly Pro 3EM-63 |
| `SHELLY_PRO_EM50` | Shelly Pro EM-50 |
| `COMBO` / `ComboDeviceBean` | Kombi-/Parallel-Gerät |
| `CollectorBean` | Daten-Collector/Gateway |
| BatteryPack | externe Akku-Packs |

Weitere Zubehör-Hinweise (UI-Strings): CT-Klemmen, **IR**-Blaster (`acc_add_ir_tips`), **Linky** (franz. Smart-Meter, `acc_add_linky_tips`).

---

## 9. Shelly Cloud2Cloud (vollständige Details) ✅

Jackery bindet Shelly-Geräte **nicht lokal**, sondern via **OAuth-Account-Link** an die Jackery-Cloud; Jackery proxyt Steuerung/Daten zu Shelly.

### Ablauf (Account-Link)
1. App ruft `wss-cloud/device/shelly/auth-url` → erhält Shelly-OAuth-URL (`*.shelly.cloud`).
2. WebView-Login bei Shelly; Redirect auf Callback-URL (Prefix in `mShellyCallbackUrlPrefix`; geparst via `parseShellyCallbackUrl`/`isShellyCallbackUrl`).
3. „SAVE result redirect" erkannt → Binding-Check → Geräte erscheinen.

### Endpunkte
| Api | Pfad |
|-----|------|
| ShellyAuthUrlApi | `wss-cloud/device/shelly/auth-url` |
| ShellyDevicesApi | `device/shelly/devices` |
| ShellyDeviceControlApi | `wss-cloud/device/shelly/device/control` |
| ShellyRealDataApi | `wss-cloud/device/shelly/device/realtime-power` |
| ShellyUnbindDeviceApi | `wss-cloud/device/shelly/unbind/device` |
| ShellyUnbindAccountApi | `wss-cloud/device/shelly/unbind/account` |
| ShellyBindingFailuresApi | `device/shelly/binding/failures` |

### UI / Logik
- `ThirdPartShellyDeviceActivity` — Geräteliste, Account binden/lösen (`unBindShellyAccount`, `clearShellyWebDataThenLoad`, `EXTRA_CLEAR_SHELLY_SESSION`).
- `ShellyCloudSocketPanelActivity` + `ShellySocketPanelVM` — Steckdosen: `getShellRealData`, `shellSwitchStatus`, `getSocketStatistic`, `unbindDevicesWithFlow`.
- `ShellyCloudMeterPanelActivity` + `ShellyMeterPanelVM` — Energiezähler (3EM/EM): `getShellRealData`, `getDeviceInfoFromNet`, `getSocketStatistic`.

### Für HA
Steckdosen (Plug S/Gen3): schaltbar über `…/device/control`, Echtzeit-Leistung über `…/realtime-power`. Zähler (Pro 3EM/EM-50): Leistung/Statistik lesbar. Alles geht über die **Jackery-Cloud** (Bearer-Token), nicht direkt zu Shelly. Alternativ kannst du diese Shelly-Geräte in HA natürlich auch **direkt** über die offizielle Shelly-Integration einbinden (lokal/Shelly-Cloud) — unabhängig von Jackery.

---

## 10. Lokaler BLE-Pfad (Nordic) 🔶
Paket `com.hbxn.control.nordic.scan.*` + BLE-Permissions (SCAN/CONNECT/ADVERTISE). Nordic-Chipsatz (nRF). Genutzt für Provisionierung (`WRITE_WIFI_INFO`, `WRITE_WIFI_AND_MQTT_INFO`, `READ_WIFI_LIST`, `GET_WIFI_CONFIG`) und evtl. lokale Steuerung. Separater Forschungsstrang; relevant für komplett cloud-freie Einbindung. ❗

---

## 11. Status-Matrix
| Baustein | Status |
|----------|--------|
| REST-Endpunkte (116) | ✅ |
| REST-Login + JWT | ✅ |
| MQTT Broker/Topics | ✅ |
| MQTT-Auth-Algorithmus | ✅ (Bytecode; End-to-End-Verifikation per `e0.q`-Hook offen) |
| deviceId-Formel | ✅ Formel / 🔶 ob für Auth nötig |
| Steuerbefehle | ✅ Konstanten / 🔶 exaktes JSON |
| Payload-Krypto | 🔶 (vmtl. AES wie Auth) |
| Telemetrie-Schema | 🔶 (Feldnamen ✅, vollständig per MQTT-Capture) |
| Shelly Cloud2Cloud | ✅ |
| Zubehörtypen | ✅ |
| BLE-Pfad | 🔶 |

---

## 12. Nächste Schritte
1. **`e0.q`-Frida-Hook** einmal mitschneiden → Auth End-to-End bestätigen (siehe separates Skript).
2. **`hb/app/<sn>/device` + `.../command` mitschneiden** → Shadow-Schema + Command-JSON.
3. **`CUSTOM_MQTT`/ThirdPartyMqtt** prüfen (cloud-freier Pfad).
4. **Python-Prototyp:** REST-Login → Credentials (jackery_auth.py) → TLS-Connect → `device`-Topic abonnieren → Werte gegen App vergleichen.
5. **HA-Integration:** Config-Flow (Login), Coordinator (MQTT-sub + REST-Stats), Entities (Sensoren SOC/PV/AC/Temp/Freq; Switches AC/DC/USB/Light/Sockets; Numbers Charge-Limits/Max-Out; Selects Output-Priority/Mode).
