# Smali-Analyse `com.zip` – Extraktion für eine Home-Assistant-Integration

## Kurzfazit

Die ZIP enthält **7,758 Smali-Dateien**, davon **320 Dateien unter `com/hbxn/*`**. Für eine Home-Assistant-Integration ist der relevante Teil nicht `com/google/*`, sondern vor allem:

- `com/hbxn/control/device/cmd/*` – Kommando-IDs, MQTT-/BLE-Nachrichtenformate
- `com/hbxn/control/device/bean/*` – Payload-/Zustandsmodelle
- `com/hbxn/control/nordic/scan/*` – BLE-Scan-Logik
- `com/hbxn/jackery/dto/*` – zusätzliche DTOs

Wichtig: Die ZIP enthält **keine vollständige APK-Struktur** und es fehlen externe/obfuskierte Hilfsklassen wie `Lbb/c;`, `Lfb/b;`, `Lsb/*`, `Lza/*`. Deshalb sind Transportauswahl, BLE-Verschlüsselung/Encoding und Advertisement-Parser nicht vollständig rekonstruierbar.

## Wichtigste verwertbare Erkenntnisse

### 1. Kein Cloud-API-Endpunkt in den bereitgestellten Smali-Dateien

Ich habe in `com/hbxn/*` nach HTTP-/WebSocket-/MQTT-URLs, Domains, Retrofit-/OkHttp-Basis-URLs und Host-Konstanten gesucht. Ergebnis:

- echte HTTP/HTTPS/MQTT/WSS-URLs: **0**
- verwertbare Domains/Hosts: **0**

Damit lässt sich aus dieser ZIP **keine Cloud-API-Basis-URL** und kein vollständiges Auth-/Cloud-Protokoll ableiten.

### 2. MQTT-Kommando-Hülle ist klar erkennbar

Alle drei Familien nutzen sinngemäß diese JSON-Hülle:

```json
{"deviceSn":"%s","id":%s,"version":%s,"messageType":"%s","actionId":%s,"timestamp":%s,"body":%s}
```

Ableitung aus `HomeControlFormat`, `PortableControlFormat`, `BoxControlFormat`:

- `deviceSn`: Geräte-Seriennummer
- `id`: Timestamp/Request-ID
- `version`: im Code mit `0` befüllt
- `messageType`: MQTT-Operation, z. B. `DevicePropertyChange`, `QueryCombineData`, `ControlCombine`
- `actionId`: Kommando-/Message-ID
- `timestamp`: gleicher Zeitwert wie `id`
- `body`: JSON-Payload, häufig mit Feld `cmd`

### 3. BLE-Kommandoformat ist teilweise rekonstruierbar

Home/Box verwenden als BLE-Frame-Grundformat:

```text
DFED0001%s%s%s%s0001%s%s
```

Portable verwendet zusätzlich:

```text
DFEC00%s%s%s%s
DFEC80%s%s%s%s%s%s
DFED0001%s%s%s%s0001%s%s
```

Die Payload wird offenbar in Hex-Chunks zerlegt. Die Chunkgröße wird aus MTU abgeleitet: `(MTU - 60) * 2`. Danach wird der Frame über `Lbb/c;->c(String)` weiterverarbeitet. Diese Klasse fehlt in der ZIP, daher ist **aktives BLE-Schreiben nicht vollständig implementierbar**, ohne die fehlenden Klassen oder einen Runtime-Mitschnitt.

### 4. Third-Party-MQTT ist der beste Integrationsansatz

Es existiert ein eigenes Modell `ThirdPartyMqttBody` mit diesen Feldern:

```text
enable, ip, port, userName, password, token
```

Dazu passende Home-Kommandos:

| Kommando | msgId | BLE | MQTT messageType |
| --- | ---: | ---: | --- |
| SET_THIRD_PARTY_MQTT_CONFIG | 3046 | 113 | ThirdPartMQTTConfig |
| GET_THIRD_PARTY_MQTT_CONFIG | 3047 | 114 | QueryThirdPartMQTTConfig |

Für Home Assistant bedeutet das: Der vielversprechendste Weg ist, das Gerät auf einen lokalen MQTT-Broker zu konfigurieren und die tatsächlich publizierten Topics/Payloads mitzuschneiden. Topic-Namen selbst sind in dieser ZIP nicht enthalten.

## Home-Kommandos

| command | msg_id | ble_msg_type | mqtt_message_type |
| --- | --- | --- | --- |
| READ_WIFI_LIST | 3001 | 1 | DevicePropertyChange |
| WRITE_WIFI_INFO | 3002 | 2 | DevicePropertyChange |
| SEND_TIME_ZONE | 3003 | 3 | DevicePropertyChange |
| GET_TIME_ZONE | 3004 | 22 | DevicePropertyChange |
| SYNC_MQTT_CONNECT_INFO | 3005 | 99 | DevicePropertyChange |
| GET_DEVICE_OTA_VERSION | 3006 | 100 | DevicePropertyChange |
| NOTIFY_DEVICE_CAN_OTA | 3007 | 101 | DevicePropertyChange |
| NOTIFY_DEVICE_OTA_TOTAL_PAGE | 3008 | 102 | DevicePropertyChange |
| DEVICE_GET_OTA_PAGE_DATA | 3009 | 103 | DevicePropertyChange |
| SYNC_GRID_STANDARD | 3010 | 105 | DevicePropertyChange |
| READ_DEVICE_INFO | 3011 | 106 | QueryDeviceProperty |
| BIND_SMART_PART | 3012 | 108 | BindSmartAccessory |
| UNBIND_SMART_PART | 3013 | 109 | RemoveSmartAccessory |
| READ_SUB_DEVICE_BATTERY_PACK | 3014 | 110 | QuerySubDeviceGroupProperty |
| TIMER_TASK_ADD | 3015 | 112 | DownloadDeviceSchedule |
| TIMER_TASK_DELETE | 3016 | 112 | DownloadDeviceSchedule |
| TIMER_TASK_UPDATE | 3017 | 112 | DownloadDeviceSchedule |
| TIMER_TASK_READ | 3018 | 112 | DownloadDeviceSchedule |
| READ_SYSTEM_INFO | 3019 | 120 | QueryCombineData |
| SYSTEM_GET_STORM_EVENT | 3020 | 23 | QueryWeatherPlan |
| SYSTEM_CONTROL_AUTO_STANDBY | 3021 | 121 | ControlCombine |
| CONTROL_AC_OFF_GRID_SWITCH | 3022 | 107 | DevicePropertyChange |
| CONTROL_STANDBY | 3023 | 107 | DevicePropertyChange |
| SUB_CONTROL_SOCKET_SWITCH | 3024 | 111 | ControlSubDevice |
| SUB_CONTROL_SOCKET_PRI_ENABLE | 3025 | 111 | ControlSubDevice |
| SUB_SET_CT_SCHEDULE_PHASE | 3026 | 111 | ControlSubDevice |
| SYSTEM_SET_WORK_MODEL | 3027 | 121 | ControlCombine |
| SET_CHARGE_DISCHARGE_LINE | 3028 | 107 | DevicePropertyChange |
| SYSTEM_SET_FEED_GRID_POWER | 3029 | 121 | ControlCombine |
| CONTROL_REBOOT | 3030 | 107 | DevicePropertyChange |
| READ_SUB_DEVICE_CT | 3031 | 110 | QuerySubDeviceGroupProperty |
| READ_SUB_DEVICE_SOCKET | 3032 | 110 | QuerySubDeviceGroupProperty |
| READ_SUB_DEVICE_METER_HEAD | 3033 | 110 | QuerySubDeviceGroupProperty |
| SYSTEM_SET_STORM_EVENT | 3034 | 0 | SendWeatherAlert |
| SYSTEM_DELETE_STORM_EVENT | 3035 | 0 | CancelWeatherAlert |
| SYSTEM_STORM_EVENT_SWITCH | 3036 | 0 | ControlCombine |
| READ_SUB_DEVICE_COMBO | 3037 | 110 | QuerySubDeviceGroupProperty |
| CONTROL_MAX_OUT_PW | 3038 | 107 | DevicePropertyChange |
| SYSTEM_CONTROL_OFF_GRID_AUTO_SHUTDOWN | 3039 | 121 | ControlCombine |
| SYSTEM_SET_OFF_GRID_SHUTDOWN_TIME | 3040 | 121 | ControlCombine |
| SYSTEM_SET_TEMP_UNIT | 3041 | 121 | ControlCombine |
| FAULT_ALARM_REPORT | 3042 | 122 | UploadDeviceAlert |
| SYSTEM_SET_DEFAULT_LOAD_POWER | 3043 | 121 | ControlCombine |
| SYSTEM_SET_FOLLOW_METER | 3044 | 121 | ControlCombine |
| GET_WIFI_CONFIG | 3045 | 124 | QueryWifiConfig |
| SET_THIRD_PARTY_MQTT_CONFIG | 3046 | 113 | ThirdPartMQTTConfig |
| GET_THIRD_PARTY_MQTT_CONFIG | 3047 | 114 | QueryThirdPartMQTTConfig |

## Portable-Kommandos

| command | msg_id | ble_msg_type | mqtt_message_type |
| --- | --- | --- | --- |
| DEVICE_GET_OTA_PAGE_DATA | 1 | 103 | DevicePropertyChange |
| GET_DEVICE_OTA_VERSION | 2 | 100 | DevicePropertyChange |
| NOTIFY_DEVICE_CAN_OTA | 3 | 101 | DevicePropertyChange |
| NOTIFY_DEVICE_OTA_TOTAL_PAGE | 4 | 102 | DevicePropertyChange |
| READ_WIFI_LIST | 5 | 1 | DevicePropertyChange |
| READ_DEVICE_INFO | 6 | 3 | QueryDeviceProperty |
| WRITE_WIFI_INFO | 7 | 2 | DevicePropertyChange |
| GET_POWER_PACK_LIST | 8 | 6 | DevicePropertyChange |
| GET_ELECTRICITY_DATA_COUNT | 9 | 7 | DevicePropertyChange |
| CONTROL_OUTPUT_DC | 10 | 107 | DevicePropertyChange |
| CONTROL_OUTPUT_DC_USB | 11 | 107 | DevicePropertyChange |
| CONTROL_OUTPUT_DC_CAR | 12 | 107 | DevicePropertyChange |
| CONTROL_OUTPUT_AC | 13 | 107 | DevicePropertyChange |
| CONTROL_OUTPUT_AC240 | 14 | 107 | DevicePropertyChange |
| CONTROL_LIGHT | 17 | 107 | DevicePropertyChange |
| CONTROL_SCREEN | 18 | 107 | DevicePropertyChange |
| SETTING_AUTO_SHUTDOWN_TIME | 19 | 107 | DevicePropertyChange |
| SETTING_ENERGY_SAVING | 20 | 107 | DevicePropertyChange |
| SETTING_CHARGE | 21 | 107 | DevicePropertyChange |
| SETTING_BATTERY | 22 | 107 | DevicePropertyChange |
| SETTING_SUPER_CHARGE | 23 | 107 | DevicePropertyChange |
| SETTING_UPS_MODEL | 24 | 107 | DevicePropertyChange |
| SEND_TIME_ZONE | 25 | 8 | DevicePropertyChange |
| GET_CHARGE_DISCHARGE_PLAN | 26 | 15 | QueryElectricityStrategy |
| ADD_CHARGE_DISCHARGE_PLAN | 27 | 16 | InsertElectricityStrategy |
| UPDATE_CHARGE_DISCHARGE_PLAN | 28 | 17 | UpdateElectricityStrategy |
| DELETE_CHARGE_DISCHARGE_PLAN | 29 | 18 | DeleteElectricityStrategy |
| CURRENT_CHARGE_DISCHARGE_PLAN | 30 | 21 | QueryCurrentElectricityStrategy |
| ENERGY_STORAGE_CHARGE_LIMIT | 31 | 107 | DevicePropertyChange |
| USE_POWER_MODE | 32 | 107 | DevicePropertyChange |
| CUSTOM_USE_BATTERY | 33 | 22 | SetBatteryBoundry |
| AC_OUTPUT_COUNTDOWN | 34 | 107 | DevicePropertyChange |
| DC_OUTPUT_COUNTDOWN | 35 | 107 | DevicePropertyChange |
| DC_USB_OUTPUT_COUNTDOWN | 36 | 107 | DevicePropertyChange |
| DC_CAR_OUTPUT_COUNTDOWN | 37 | 107 | DevicePropertyChange |
| SET_CHARGE_POWER | 38 | 107 | DevicePropertyChange |
| CONTROL_POWER_PACK_BLINK | 39 | 98 | ControlSubDevice |
| AC_OUTPUT_MODE | 40 | 107 | DevicePropertyChange |
| AC_OUTPUT_DELAY_OPEN_TIME | 41 | 107 | DevicePropertyChange |
| SET_PEAKS_TROUGHS | 42 | 130 | TOUSchedule |
| GET_PEAKS_TROUGHS | 43 | 131 | QueryTOUSchedule |

## BoxControlFormat: erkannte Body-Templates

```text
{"cmd":%d}
{"cmd":%d,"cdsDt":%d}
{"cmd":%d,"selfDt":%d}
{"cmd":%d,"ts":%d,"uo":%d,"timezone":"%s"}
{"cmd":%d,"uo":%d,"timezone":"%s"}
{"cmd":%d,"cir":%s}
{"cmd":26,"wps":%d}
{"cmd":%d,"pss":%d}
{"cmd":%d,%s}
{"cmd":%d,"en":%d}
{"cmd":%s,"s":"%s","p":"%s"}
{"cmd":%d,"host":"%s","port":%s}
{"cmd":%d,"autoDt":%d}
{"cmd":%d,"ddt":%d}
{"cmd":%d,"rc":%d}
{"cmd":%d,"idx":%d,"nm":"%s"}
{"cmd":%d,"idx":%d,"sw":%d}
{"cmd":%d,"ups":%d}
{"cmd":%d,"pid":"%s"}
{"alertId":"%s"}
{"cmd":%d,"ps":%d,"pst":%d}
{"cmd":%d,"dt":%d}
{"cmd":%s,"sn":"%s","f":"%s"}
{"cmd":%s,"sn":"%s","f":"%s","p":%d,"s":%d}
{"minsInterval":%d}
```

Erkannte Box-`messageType`-Strings:

```text
DevicePropertyChange
CancelWeatherAlert
SendWeatherAlert
QueryWeatherPlan
QueryCurrentElectricityStrategy
DeleteElectricityStrategy
UpdateElectricityStrategy
InsertElectricityStrategy
QueryElectricityStrategy
QueryDeviceProperty
QueryCircuitProperty
```

## BLE-Scan-Daten

`com/hbxn/control/nordic/scan/h.smali` verweist auf `NordicBleScanManager.kt` und nutzt die Nordic-Scanner-Bibliothek.

- Service UUID: `0000bdee-0000-1000-8000-00805f9b34fb`
- Scan-Timeout: `15000 ms`
- weiterer Zeitwert/Intervall: `5000 ms`
- erkannte Scan-Events: `SCAN_STARTED`, `SCAN_STOPPED`, `SCAN_TIMEOUT`, `SCAN_FAILED`
- Scan-Ergebnis wird nur akzeptiert, wenn `BluetoothDevice.getName()` nicht leer ist
- Advertisement-/Device-Typ-Parser liegt in fehlenden Klassen `Lsb/*` und `Lza/*`

## Subdevice-/Zubehör-Erkennung

| enum | scanName | devType | deviceType | Hinweis |
| --- | --- | --- | --- | --- |
| UNKNOWN | UNKNOWN | 0 | UNKNOWN | unbekannt/Filter |
| SHELLY_PRO_EM50 | shellyproem50 | 3 | CT | Shelly Pro EM-50 als CT/Meter-Zubehör |
| SHELLY_PRO_3EM | shellypro3em | 3 | CT | Shelly Pro 3EM |
| SHELLY_PRO_3EM63 | shellypro3em63 | 3 | CT | Shelly Pro 3EM-63 |
| ECO_TRACKER | ecotracker | 4 | METER_HEAD | EcoTracker P1/R1 |
| P1_METER | p1meter | 4 | METER_HEAD | P1-Meter |
| HOMEY_ENERGY_DONGLE | homey_energy_dongle | 4 | METER_HEAD | Homey Energy Dongle |
| SHELLY_PLUG_S | shellyplusplugs | 6 | SOCKET | Shelly Plus Plug S |
| SHELLY_PLUG_SG3 | shellyplugsg3 | 6 | SOCKET | Shelly Plug S Gen3 |
| HTO892A | HTO892A | 4 | METER_HEAD | Jackery/Partner-Zubehörkennung |
| HTO904A | HTO904A | 6 | SOCKET | Jackery/Partner-Zubehörkennung |
| HTO905A | HTO905A | 4 | METER_HEAD | Jackery/Partner-Zubehörkennung |
| HTO906A | HTO906A | 3 | CT | Jackery/Partner-Zubehörkennung |
| HTO907A | HTO907A | 3 | CT | Jackery/Partner-Zubehörkennung |
| HTO910A | HTO910A | 4 | METER_HEAD | Jackery/Partner-Zubehörkennung |

Weitere Enums:

- `HomeSubDeviceType`: `UNKNOWN=0`, `POWER_ON=1`, `COMBO=2`, `CT=3`, `METER_HEAD=4`, `METER=5`, `SOCKET=6`, `BREAKER=7`, `SMOKE=8`, `TEMP_HUMIDITY=9`, `WATER_LEAK=10`
- CT-Typen: `CT_A=1`, `CT_B=2`, `CT_C=3`, `CT_SUM=4`
- Meter-/Link-Typen: `ECOTRACKER_P1=1`, `ECOTRACKER_R1=2`, `ENERGY_DONGLE_V1=3`, `TASMOTA=4`, `HOMEWIZARD=5`, `AECC=6`
- Timer-Typen: `SMART_PLUG_TIMER=1`, `CUSTOM_MODE_TIMER=2`, `TIME_ELEC_TIMER=3`

## BindSmartAccessory-Payload

`HomeCmdAction$Companion$BindSmartBean` enthält:

```text
deviceSn, devType, subType, scanName, param, linkType, bindKey
```

Das gehört zu:

| Kommando | msgId | BLE | MQTT messageType |
| --- | ---: | ---: | --- |
| BIND_SMART_PART | 3012 | 108 | BindSmartAccessory |
| UNBIND_SMART_PART | 3013 | 109 | RemoveSmartAccessory |

## Zustands- und Payloadmodelle für HA-Entities

Die folgende Tabelle zeigt die wichtigsten Klassen/Felder. Die vollständige Feldliste liegt zusätzlich als CSV vor: `hbxn_model_fields.csv`.

| Klasse | Felder |
| --- | --- |
| HomeBody | ability, autoStandby, batInPw, batNum, batOutPw, batSoc, cellTemp, eip, emac, ethPort, f, inOngridPw, mac, maxGridStdPw, maxInvStdPw, maxIotNum, maxOutPw, ongridStat, outOngridPw, pv1, pv2, pv3, pv4, pvPw, reboot, socChgLimit, socDischgLimit, swEps, swEpsInPw, swEpsOutPw, swEpsState, wip, wname, wsig |
| SystemBody | batInPw, batNum, batOutPw, batState, ctStat, defaultPw, energyPlanPw, funcEnable, gridInPw, gridOutPw, gridSate, inGridSidePw, isAutoStandby, isFollowMeterPw, maxFeedGrid, maxSysInPw, maxSysOutPw, offGridDown, offGridTime, ongridStat, otherLoadPw, outGridSidePw, soc, standbyPw, stat, swEpsInPw, swEpsOutPw, tempUnit, workModel, wpc, wps |
| PV | commState, name, pvPw |
| BatteryPackSub | batSoc, cellTemp, inPw, isFirmwareUpgrade, outPw, version |
| PlugSub | inPw, outPw, socketPri, switchSta, sysSwitch, wip |
| CtSub | aPhasePw, anPhasePw, bPhasePw, bnPhasePw, cPhasePw, cnPhasePw, funForm, schePhase, tPhasePw, tnPhasePw, wip |
| CollectorSub | inPw, outPw, wip |
| ThirdPartyMqttBody | enable, ip, password, port, token, userName |
| BoxBody | ac1, ac2, autoDt, bls, cds, cdsDt, cep, cir, ddt, de, dg, dh, ds, dt, en, f, fz, ip, mac, op, ot, ps, pss, pst, rb, rc, selfDt, storm, ups, wip, wname, wpc, wps, wsig |
| Circuit | idx, nm, pc, pr, sph, sph_pc, sw |
| Ac | acpsp, bi, bp, bs, ip, it, mc, op, ot, rb, sn, ss, trb |
| Plan | et, lps, pid, st, sw, tt |
| PortableBody | accd, acdt, acip, acmode, acohz, acov, acov1, acps, acpsp, acpss, ast, bc, bls, box, bpc, bs, bt, cds, cep, cip, cl, cop, cs, csc, csl, cst, dl, dt, ec, en, f, iac, iacPw, idc, ip, ipalPw, isPackConnect, it, lm, lps, mac, oac, oac2, oacPw, oact, odc, odcc, odcct, odct, odcu, odcut, op, opalPw, ot, pal, pc, pm, pmb, pss, rb, sfc, sltb, ss, ta, tp, tt, ups, usba1, usba2, usba3, usbc1, usbc2, usbc3, wip, wname, wsig, wss |
| PeaksTroughs | crest, flat, follow, lowest, month, trough, weekend, work |
| PeaksTroughsTask | end, start, type |
| AccBaseBody | ip, mac, name, rssi, sn, ssid, type, version |
| AccCTBody | ap, ap1, ap2, ap3, curr, curr1, curr2, curr3, fact, fact1, fact2, fact3, freq, power, power1, power2, power3, rep, rep1, rep2, rep3, volt, volt1, volt2, volt3 |
| AccSocketBody | op, sc, switch, ts |
| HomeAlarmBody | alarmId, subDevice, sysAlertCount |
| SubAlarm | alertCount, devType, deviceSn |
| Storm | alertId, endTs, manual, startTs, status |

## Ableitung für Home Assistant

### Empfohlene Architektur

1. **MQTT-first:**
   - In HA/Mosquitto lokalen Broker bereitstellen.
   - Über `SET_THIRD_PARTY_MQTT_CONFIG` bzw. App-Funktion die Third-Party-MQTT-Daten setzen.
   - Danach MQTT-Traffic mitschneiden und Topic-/Payloadschema verifizieren.
   - Sensoren/Switches via `DataUpdateCoordinator` oder MQTT-Discovery abbilden.

2. **Passive BLE-Erkennung ergänzen:**
   - Scan auf `0000bdee-0000-1000-8000-00805f9b34fb`.
   - Advertisement-Daten mitschneiden.
   - Ohne fehlende Parserklassen erst nur Diagnostik/Discovery, keine sichere Steuerung.

3. **Cloud-API nicht priorisieren:**
   - Aus dieser ZIP sind keine Cloud-Endpunkte/Hostnamen ableitbar.
   - Für Cloud wären vollständige APK oder Runtime-Mitschnitt erforderlich.

4. **Subdevice-Brücke nutzen:**
   - Da Shelly, Tasmota, HomeWizard und EcoTracker im Code explizit als Zubehör/Meter auftauchen, kann HA diese Geräte direkt integrieren.
   - Für die Jackery-/Home-Seite sind vor allem Vergleichs- und Abgleichsensoren relevant: Grid-In/Out, PV, Battery-In/Out, SOC, CT-Phasen, Socket-Leistung.

### Mögliche HA-Entities aus den Feldern

- Batteriesensoren: `soc`, `batSoc`, `batInPw`, `batOutPw`, `batNum`, `cellTemp`, `version`
- PV-/Solar-Sensoren: `pvPw`, `pv1`–`pv4`, `PV.pvPw`, `PV.commState`
- Netzsensoren: `gridInPw`, `gridOutPw`, `inGridSidePw`, `outGridSidePw`, `ongridStat`, `gridSate`
- Last-/Output-Sensoren: `maxSysOutPw`, `maxOutPw`, `otherLoadPw`, `swEpsOutPw`, `outOngridPw`
- Schalter/Controls: `swEps`, `standbyPw`, `isAutoStandby`, `offGridDown`, `tempUnit`, `workModel`, `wpc`, `wps`
- Subdevices: CT-Phasen, Steckdosenstatus, Steckdosen-Priorität, Smart-Plug-Timer, Meter-Head-Status
- Diagnose: WLAN-IP/SSID/Signal (`wip`, `wname`, `wsig`), Ethernet-IP/MAC/Port (`eip`, `emac`, `ethPort`), Fehler/Alarme (`alarmId`, `alertCount`, `Fault`, `Storm`)

## Harte Grenzen dieser ZIP

- Keine vollständige APK: fehlende Pakete außerhalb `com/*` verhindern vollständiges Reversing.
- Keine Hostnamen/Cloud-Endpunkte gefunden.
- Keine Topics für Third-Party-MQTT gefunden.
- BLE-Schreibpfad ist ohne `Lbb/c;` nicht vollständig reproduzierbar.
- Advertisement-Parsing ist ohne `Lsb/*`/`Lza/*` nicht vollständig reproduzierbar.

## Nächste verwertbare Schritte

1. In der App prüfen, ob Third-Party-MQTT aktivierbar ist.
2. `GET_THIRD_PARTY_MQTT_CONFIG` auslösen bzw. App-Netzwerk/BLE-Verkehr mitschneiden.
3. Mosquitto in HA als Broker konfigurieren und alle Topics loggen.
4. MQTT-Payloads gegen die extrahierten Bean-Felder mappen.
5. Danach erst HA-Integration bauen: Config Flow → MQTT-Verbindung/Discovery → Sensor-/Switch-Mapping → Diagnose → Tests.

## Artefakte

- `hbxn_commands.csv`: Home- und Portable-Kommandos mit msgId/BLE/MQTT-MessageType.
- `hbxn_model_fields.csv`: alle extrahierten Modellfelder aus relevanten `hbxn`-Bean-/DTO-Klassen.
