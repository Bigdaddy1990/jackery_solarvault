# Jackery 2.1.1 RE — Teil 2: Protokoll, Datenmodell & Befehlstabellen

> Ergänzung zur Hauptdoku (`Jackery_2.1.1_RE_Documentation.md`). Alles hier ist **statisch aus den DEX** (classes4) extrahiert — Wire-Format, vollständiges Telemetrie-Schema und die kompletten Befehls-Enums. Stand: vollständige Bytecode-Analyse.

## 1. MQTT-Nachrichten-Envelope ✅

Jede Nachricht (Command **und** Shadow/Telemetrie) verwendet dieses JSON (aus `HomeControlFormat`/`BoxControlFormat`/`PortableControlFormat`):

```json
{"deviceSn":"<sn>","id":<int>,"version":<int>,"messageType":"<MsgType>","actionId":<int>,"timestamp":<ms>,"body":<obj>}
```

- `messageType` = der String aus den Befehlstabellen unten (z. B. `DevicePropertyChange`, `QueryDeviceProperty`, `ControlCombine`).
- `body` = der eigentliche Befehl, Format `{"cmd":<int>, …}` (siehe §4) — bei Telemetrie der State (siehe §5).
- `actionId`/`id` = laufende IDs; `timestamp` = ms.
- Topic: Command → `hb/app/<sn>/command`, Telemetrie ← `hb/app/<sn>/device` (Envelope dort als `{"body":{…}}`).

### BLE-Frame-Format (Nordic) 🔶
Für den lokalen BLE-Pfad werden Hex-Frames genutzt:
- Home/System: `DFED0001%s%s%s%s0001%s%s`
- Portable: `DFEC00%s%s%s%s`, `DFEC80%s%s%s%s%s%s` (paginiert), `DFED0001…`
- Konstanten: `BLE_SEND_DATA_FORMAT_HEX`, `..._HEX_2_0`, `..._PAGINATION_HEX`, `MQTT_SEND_DATA_FORMAT_JSON`.
Die `bleMsgType`-Codes (Spalte unten) gehen in diese Frames.

---

## 2. Befehls-Architektur ✅

Es gibt **zwei Gerätefamilien** mit eigenen Befehls-Enums:
- **Home/System** (Heim-Energiesysteme, DIY/HomePower) → `HomeCmdAction`
- **Portable** (klassische Explorer-Powerstations) → `cmd.portable.b`

Jeder Befehl trägt drei Identifikatoren:
| Feld | Bedeutung |
|------|-----------|
| `msgId` | interne Enum-ID (Home: 3001–3047, Portable: 1–53) |
| `bleMsgType` | Befehlscode im **BLE**-Frame (0 = via Default/Property-Change) |
| `mqttMsgType` | `messageType`-String im **MQTT**-Envelope |

Bei reinen Steuerbefehlen (CONTROL_*) ist `mqttMsgType` defaultet → mit hoher Wahrscheinlichkeit **`DevicePropertyChange`**, und die eigentliche Aktion steckt im `body.cmd` (🔶 Default-Wert beim Connect bestätigen).

---

## 3. Befehlstabellen ✅

### 3.1 HOME / SYSTEM (`HomeCmdAction`, 47 Befehle)

| Befehl | msgId | bleType | mqttMsgType |
|--------|------:|--------:|-------------|
| READ_DEVICE_INFO | 3011 | 106 | QueryDeviceProperty |
| READ_SYSTEM_INFO | 3019 | 120 | QueryCombineData |
| READ_SUB_DEVICE_BATTERY_PACK | 3014 | 110 | QuerySubDeviceGroupProperty |
| READ_SUB_DEVICE_CT | 3031 | 110 | QuerySubDeviceGroupProperty |
| READ_SUB_DEVICE_SOCKET | 3032 | 110 | QuerySubDeviceGroupProperty |
| READ_SUB_DEVICE_METER_HEAD | 3033 | 110 | QuerySubDeviceGroupProperty |
| READ_SUB_DEVICE_COMBO | 3037 | 110 | QuerySubDeviceGroupProperty |
| SUB_CONTROL_SOCKET_SWITCH | 3024 | 111 | ControlSubDevice |
| SUB_CONTROL_SOCKET_PRI_ENABLE | 3025 | 111 | ControlSubDevice |
| SUB_SET_CT_SCHEDULE_PHASE | 3026 | 111 | ControlSubDevice |
| BIND_SMART_PART | 3012 | 108 | BindSmartAccessory |
| UNBIND_SMART_PART | 3013 | 109 | RemoveSmartAccessory |
| SYSTEM_CONTROL_AUTO_STANDBY | 3021 | 121 | ControlCombine |
| SYSTEM_SET_WORK_MODEL | 3027 | 121 | ControlCombine |
| SYSTEM_SET_FEED_GRID_POWER | 3029 | 121 | ControlCombine |
| SYSTEM_CONTROL_OFF_GRID_AUTO_SHUTDOWN | 3039 | 121 | ControlCombine |
| SYSTEM_SET_OFF_GRID_SHUTDOWN_TIME | 3040 | 121 | ControlCombine |
| SYSTEM_SET_TEMP_UNIT | 3041 | 121 | ControlCombine |
| SYSTEM_SET_DEFAULT_LOAD_POWER | 3043 | 121 | ControlCombine |
| SYSTEM_SET_FOLLOW_METER | 3044 | 121 | ControlCombine |
| SYSTEM_STORM_EVENT_SWITCH | 3036 | 0 | ControlCombine |
| SYSTEM_SET_STORM_EVENT | 3034 | 0 | SendWeatherAlert |
| SYSTEM_DELETE_STORM_EVENT | 3035 | 0 | CancelWeatherAlert |
| SYSTEM_GET_STORM_EVENT | 3020 | 23 | QueryWeatherPlan |
| TIMER_TASK_ADD | 3015 | 112 | DownloadDeviceSchedule |
| TIMER_TASK_DELETE | 3016 | 112 | DownloadDeviceSchedule |
| TIMER_TASK_UPDATE | 3017 | 112 | DownloadDeviceSchedule |
| TIMER_TASK_READ | 3018 | 112 | DownloadDeviceSchedule |
| FAULT_ALARM_REPORT | 3042 | 122 | UploadDeviceAlert |
| GET_WIFI_CONFIG | 3045 | 124 | QueryWifiConfig |
| SET_THIRD_PARTY_MQTT_CONFIG | 3046 | 113 | ThirdPartMQTTConfig |
| GET_THIRD_PARTY_MQTT_CONFIG | 3047 | 114 | QueryThirdPartMQTTConfig |
| CONTROL_AC_OFF_GRID_SWITCH | 3022 | 0 | (DevicePropertyChange) |
| CONTROL_STANDBY | 3023 | 0 | (DevicePropertyChange) |
| CONTROL_REBOOT | 3030 | 0 | (DevicePropertyChange) |
| CONTROL_MAX_OUT_PW | 3038 | 0 | (DevicePropertyChange) |
| SET_CHARGE_DISCHARGE_LINE | 3028 | 0 | (DevicePropertyChange) |
| SYNC_GRID_STANDARD | 3010 | 105 | — |
| SYNC_MQTT_CONNECT_INFO | 3005 | 99 | — |
| READ_WIFI_LIST | 3001 | 1 | — |
| WRITE_WIFI_INFO | 3002 | 2 | — |
| SEND_TIME_ZONE / GET_TIME_ZONE | 3003/3004 | 3/22 | — |
| GET_DEVICE_OTA_VERSION | 3006 | 100 | — |
| NOTIFY_DEVICE_CAN_OTA | 3007 | 101 | — |
| NOTIFY_DEVICE_OTA_TOTAL_PAGE | 3008 | 102 | — |
| DEVICE_GET_OTA_PAGE_DATA | 3009 | 103 | — |

### 3.2 PORTABLE (`cmd.portable.b`, 51 Befehle)

Ausgänge & Gerät:
| Befehl | msgId | bleType |
|--------|------:|--------:|
| CONTROL_OUTPUT_DC | 10 | 0 |
| CONTROL_OUTPUT_DC_USB | 11 | 0 |
| CONTROL_OUTPUT_DC_CAR | 12 | 0 |
| CONTROL_OUTPUT_AC | 13 | 0 |
| CONTROL_OUTPUT_AC240 | 14 | 0 |
| CONTROL_OUTPUT_PRIORITY_SWITCH | 47 | 0 |
| CONTROL_LIGHT | 17 | 0 |
| CONTROL_SCREEN | 18 | 0 |
| CONTROL_POWER_PACK_BLINK | 39 | 98 |
| RESTART | 45 | 96 |
| POWER_OFF | 46 | 97 |

Laden / Energie / Modi:
| Befehl | msgId | bleType |
|--------|------:|--------:|
| SETTING_CHARGE | 21 | 0 |
| SETTING_SUPER_CHARGE | 23 | 0 |
| SETTING_BATTERY | 22 | 0 |
| SET_CHARGE_POWER | 38 | 0 |
| ENERGY_STORAGE_CHARGE_LIMIT | 31 | 0 |
| SETTING_OUTPUT_PRIORITY | 48 | 0 |
| SETTING_OUTPUT_PRIORITY_SOC | 49 | 0 |
| SETTING_DISCHARGE_MEMORY | 53 | 0 |
| USE_POWER_MODE | 32 | 0 |
| CUSTOM_USE_BATTERY | 33 | 22 |
| SETTING_ENERGY_SAVING | 20 | 0 |
| SETTING_AUTO_SHUTDOWN_TIME | 19 | 0 |
| SETTING_UPS_MODEL | 24 | 0 |
| SETTING_BLUETOOTH_MODULE_SLEEP_TIME | 44 | 0 |

Countdowns / Timer / Pläne / Strompreis:
| Befehl | msgId | bleType |
|--------|------:|--------:|
| AC_OUTPUT_COUNTDOWN | 34 | 0 |
| AC_OUTPUT_MODE | 40 | 0 |
| AC_OUTPUT_DELAY_OPEN_TIME | 41 | 0 |
| DC_OUTPUT_COUNTDOWN | 35 | 0 |
| DC_USB_OUTPUT_COUNTDOWN | 36 | 0 |
| DC_CAR_OUTPUT_COUNTDOWN | 37 | 0 |
| GET_CHARGE_DISCHARGE_PLAN | 26 | 15 |
| ADD_CHARGE_DISCHARGE_PLAN | 27 | 16 |
| UPDATE_CHARGE_DISCHARGE_PLAN | 28 | 17 |
| DELETE_CHARGE_DISCHARGE_PLAN | 29 | 18 |
| CURRENT_CHARGE_DISCHARGE_PLAN | 30 | 21 |
| SET_PEAKS_TROUGHS / GET_PEAKS_TROUGHS | 42/43 | 130/131 |
| GET_ELECTRICITY_DATA_COUNT | 9 | 7 |

Info/OTA/WiFi/Sub: READ_DEVICE_INFO(6,3) · GET_POWER_PACK_LIST(8,6) · READ_WIFI_LIST(5,1) · WRITE_WIFI_INFO(7,2) · GET_WIFI_CONFIG(52,124) · SEND_TIME_ZONE(25,8) · SYNC_MQTT_CONNECT_INFO(50,99) · READ_SUB_DEVICE_CT(51,110) · OTA: GET_DEVICE_OTA_VERSION(2,100), NOTIFY_DEVICE_CAN_OTA(3,101), NOTIFY_DEVICE_OTA_TOTAL_PAGE(4,102), DEVICE_GET_OTA_PAGE_DATA(1,103).

---

## 4. Command-Body-Formate ✅ (aus `BoxControlFormat`)

Der `body` ist ein JSON mit `cmd`-Code + Parametern:

```
{"cmd":<n>}                                   einfacher Schaltbefehl
{"cmd":<n>,"en":<0|1>}                         enable/disable
{"cmd":<n>,"dt":<int>}                          delay-time / countdown
{"cmd":<n>,"ups":<int>}                         UPS-Wert
{"cmd":<n>,"rc":<int>}                          rate/charge
{"cmd":<n>,"pss":<int>}                         priority-soc-setting
{"cmd":<n>,"ps":<int>,"pst":<int>}              output-priority + ...
{"cmd":<n>,"autoDt":<int>}                      auto-standby-time
{"cmd":<n>,"selfDt":<int>}                      self-shutdown-time
{"cmd":<n>,"cdsDt":<int>}                       charge/discharge-time
{"cmd":<n>,"ddt":<int>}                         discharge-delay-time
{"cmd":26,"wps":<int>}                          work-power-setting
{"cmd":<n>,"cir":<json>}                        circuit-config (Array)
{"cmd":<n>,"idx":<i>,"sw":<0|1>}                circuit index switch
{"cmd":<n>,"idx":<i>,"nm":"<name>"}             circuit index rename
{"cmd":<n>,"host":"<ip>","port":<p>}            *** Custom-MQTT setzen ***
{"cmd":<n>,"pid":"<planId>"}                    plan-id (Strompreis)
{"cmd":<n>,"ts":<i>,"uo":<i>,"timezone":"<tz>"} time/zone-set
```
Strompreis-MessageTypes (BoxControlFormat): `InsertElectricityStrategy`, `UpdateElectricityStrategy`, `DeleteElectricityStrategy`, `QueryElectricityStrategy`, `QueryCurrentElectricityStrategy`. Weiter: `QueryCircuitProperty`, `QueryDeviceProperty`, `DevicePropertyChange`, `QueryWeatherPlan`, `SendWeatherAlert`, `CancelWeatherAlert`.

---

## 5. Telemetrie-Datenmodell (vollständige Felder) ✅

Envelope: `XxxBean { body: XxxBody }`. Alle Felder unten direkt aus den Bean-Klassen.

### 5.1 PortableBody (Explorer-Powerstation, 96 Felder)
Die wichtigsten (Rest siehe Glossar §6):
- **Akku:** `bs` (SOC), `bt`, `bc`, `bls`, `isPackConnect`, `ability`
- **AC-Ausgang:** `acdt, acip, acmode, acohz` (Hz), `acov`/`acov1` (Spannung), `acps, acpsp, acpss`, `oac, oacPw, oac1Name, oac1Prio, oac1PrioSoc, oac2, oac2Name, oac2Prio, oac2PrioSoc, oacl1, oacl1Pw, oacl2, oacl2Pw, oact, oact1, oact2`
- **DC/USB-Ausgang:** `odc, odcPrio, odcPrioSoc, odcc, odcct, odct, odcu, odcut`, `usba1/2/3` (USB-A), `usbc1/2/3` (USB-C), `idc`
- **Eingang/Laden:** `iac, iacPw, ip, it, cip, cl, cs, csc, csl, cst, pc, pm, pmb`
- **Status/Modi:** `op, ot, en, dt, dl, ec, lm, lps, pal, outPrio, pss, rb, ss, sfc, sltb, ta, tmt, tp, tt, ups, wss, ast, box, bpc, dhg_recall`
- **WiFi:** `wip, wname, wsig`, `mac`
- **Sonstige:** `cds` (Charge/Discharge-Pläne, Array), `cep` (Plan), `f` (Fault-Array)

### 5.2 HomeBody (Heim-System, 35 Felder)
`batSoc` (SOC %), `batInPw`/`batOutPw` (Akku-Leistung), `batNum` (Anzahl Akkus), `cellTemp`, `pvPw` (PV-Leistung gesamt), `pv1`–`pv4` (je `PV{commState,name,pvPw}`), `maxOutPw`, `maxGridStdPw`, `maxInvStdPw`, `maxIotNum`, `inOngridPw`/`outOngridPw`, `ongridStat`, `socChgLimit`/`socDischgLimit`, `autoStandby`, `reboot`, `ability`, EPS: `swEps, swEpsInPw, swEpsOutPw, swEpsState`, Netzwerk: `mac, emac, eip, ethPort, wip, wname, wsig`, `f` (WifiBean-Array).

### 5.3 SystemBody (Energie-System, 32 Felder)
`soc`, `batInPw`/`batOutPw`/`batNum`/`batState`, `gridInPw`/`gridOutPw`/`gridSate`, `inGridSidePw`/`outGridSidePw`, `inOngridPw` (n/a), `ongridStat`, `ctStat`, `energyPlanPw`, `defaultPw`, `otherLoadPw`, `standbyPw`, `maxFeedGrid`, `maxSysInPw`/`maxSysOutPw`, `isFollowMeterPw`, `isAutoStandby`, `offGridDown`/`offGridTime`, `swEpsInPw`/`swEpsOutPw`, `tempUnit`, `workModel`, `funcEnable`, `stat`, `wpc`/`wps`.

### 5.4 box/Ac (AC-Einheit der Box, 14 Felder)
`acpsp, bi, bp` (Akku-Packs, Liste), `bs` (SOC), `ip` (Input-Power), `it` (Input-Temp?), `mc, op` (Output-Power), `ot` (Output-Temp?), `rb` (Restlaufzeit?), `sn, ss, trb`.

### 5.5 box/BoxBody (35 Felder)
`ac1`/`ac2` (je `Ac`), `cir` (Circuits, Array von `Circuit{idx,nm,pc,pr,sph,sph_pc,sw}`), `fz` (`Fault`), `cep` (`Plan`), `cds` (Pläne), `storm` (Array), `mac, wip, wname, wpc, wps, wsig`, Zeiten: `autoDt, cdsDt, ddt, dt, selfDt`, Status: `bls, de, dg, dh, ds, en, ip, op, ot, ps, pss, pst, rb, rc, ups`, `f` (Fault-Strings).

### 5.6 Sub-Geräte (Home-System)
- **BatteryPackSub:** `batSoc, cellTemp, inPw, outPw, version, isFirmwareUpgrade`
- **CtSub** (Stromwandler): `aPhasePw, bPhasePw, cPhasePw, tPhasePw` (+ `an/bn/cn/tnPhasePw`), `funForm, schePhase, wip`
- **PlugSub** (Smart-Steckdose): `inPw, outPw, switchSta, sysSwitch, socketPri, wip`
- **CollectorSub** (Gateway): `inPw, outPw, wip`

### 5.7 Zubehör-Bodies
- **AccBaseBody:** `sn, mac, ip, ssid, name, type, rssi, version`
- **AccCTBody** (3-Phasen-CT, 26 Felder): je Phase `volt1/2/3, curr1/2/3, power1/2/3, ap1/2/3, fact1/2/3, rep1/2/3` + Summen `volt, curr, power, ap, fact, rep, freq`
- **AccSocketBody:** `switch` (on/off), `op` (Leistung), `sc, ts`
- **battery/BatteryPackBody:** `deviceSn, version, ec, ip, it, op, ot, rb, isFirmwareUpgrade`

---

## 6. Feld-Glossar (Abkürzungen) 🔶

Hohe Konfidenz: `soc/bs/batSoc`=Akku-SOC % · `pvPw`=PV-Leistung W · `cellTemp`=Zelltemp · `freq/acohz`=Frequenz Hz · `acov`=AC-Spannung · `op`=Output-Power · `ip`=Input-Power · `it`=Input-Temp · `ot`=Output-Temp · `rb`=Restlaufzeit · `en`=enabled · `sw`=switch · `maxOutPw`=max. Ausgangsleistung · `wip`=WiFi-IP · `wname`=SSID · `wsig`=WiFi-Signal · `emac`=Ethernet-MAC · `eip`=Ethernet-IP · `usba*/usbc*`=USB-A/-C-Ports · `oac*`=AC-Ausgangs-Settings · `odc*`=DC-Ausgangs-Settings.

Mittlere Konfidenz (per Capture verifizieren): `dt/ddt/autoDt/selfDt/cdsDt`=diverse Timer/Delays · `ups`=UPS-Status · `pss/ps/pst`=Output-Priority-Settings · `cl/dl`=Charge/Discharge-Limit · `ec`=Error-Code · `bls`=Battery-Low-State? · `ta`=Temp-Alarm? · `ability/funcEnable`=Feature-Bitmasken (long).

> Die exakten Einheiten/Skalierungen (W vs. 0,1 W, % vs. 0,1 %) am besten gegen die App-Anzeige bei einem `device`-Topic-Capture kalibrieren.

---

## 7. ThirdPartyMqtt / CUSTOM_MQTT (cloud-freier Pfad) ✅

`home/ThirdPartyMqttBody`: `{ enable:int, ip:String, port:int, userName:String, password:String, token:String }`

MessageTypes (HomeCmdAction): `SET_THIRD_PARTY_MQTT_CONFIG` (msgId 3046, ble 113, mqtt `ThirdPartMQTTConfig`) und `GET_THIRD_PARTY_MQTT_CONFIG` (3047, 114, `QueryThirdPartMQTTConfig`). Box-Variante: `{"cmd":<n>,"host":"<ip>","port":<p>}`.

→ Damit lässt sich das Gerät vermutlich auf einen **eigenen MQTT-Broker** (z. B. der HA-Mosquitto) umkonfigurieren. Wenn das Gerät die Verbindung dorthin tatsächlich aufbaut, ist das der **komplett cloud-freie** Integrationsweg. **Verifizieren** (Risiko: bei Fehlkonfiguration verliert die offizielle App die Verbindung — Rückweg über `SET_THIRD_PARTY_MQTT_CONFIG`/Reset einplanen).

---

## 8. Was damit jetzt möglich ist
- **HA-Sensoren** direkt aus §5 ableitbar (SOC, PV, AC/DC/USB-Leistungen, Temperaturen, Frequenz, Grid-Power, EPS).
- **HA-Switches/Numbers/Selects** aus §3 (Outputs, Light, Screen, Charge-Limit, Output-Priority, Max-Out-Power, Timer).
- **Command senden:** Envelope (§1) mit `messageType` (§3) + `body` (§4) auf `hb/app/<sn>/command`.
- **Offen (Capture):** exakte `body.cmd`-Codes für Portable-Steuerbefehle, Payload-(De)Krypto, Default-`mqttMsgType` der CONTROL_*-Befehle, Feld-Einheiten.
