# Smali-Analyse Jackery – Ergänzung `com3.zip`, `com5.zip`, `coil.zip`, `zendesk.zip`, `okhttp3.zip`

## Kurzfazit

Die neuen ZIPs ändern den Befund deutlich: In der ersten `com.zip` war keine Cloud-Basis-URL enthalten. In `com5.zip` ist sie vorhanden:

```text
https://iot.jackeryapp.com/
```

Dazu kommt der Request-Prefix:

```text
v1/
```

Damit ist jetzt nicht nur der lokale/Third-Party-MQTT-Weg interessant, sondern auch eine mögliche Cloud-API-Integration. Für eine robuste Home-Assistant-Integration bleibt aber **Third-Party-MQTT der sauberste Startpunkt**, weil dafür keine Login-Reimplementierung und keine BLE-Verschlüsselung nötig ist, sobald die App die MQTT-Zugangsdaten einmal ins Gerät geschrieben hat.

## Analysierte Dateien

| ZIP | Smali-Dateien | relevante `hbxn`-Smali | Nutzwert |
| --- | ---: | ---: | --- |
| `com.zip` | 7758 | 320 | Commands, MQTT/BLE-Formate, Gerätemodelle |
| `com3.zip` | 3361 | 84 | Controller, Header-Setup, MQTT/BLE-Aufrufpfade |
| `com5.zip` | 3395 | 2547 | Cloud-API, HTTP-Modelle, Statistiken, Shelly-Cloud2Cloud |
| `coil.zip` | 353 | 0 | Bild-Library, nicht integrationsrelevant |
| `zendesk.zip` | 1707 | 0 | Support/Zendesk, nicht integrationsrelevant |
| `okhttp3.zip` | 389 | 0 | Standard-HTTP-Library, nur indirekt relevant |

## Wichtigste verwertbare Daten für Home Assistant

### 1. Cloud-API

Gefunden in:

```text
com5/com/hbxn/jackery/http/model/RequestServer.smali
com5/com/hbxn/jackery/other/a.smali
```

Rekonstruierbar:

```text
Base URL: https://iot.jackeryapp.com/
Prefix:   v1/
```

Damit ergeben sich API-Pfade der Form:

```text
https://iot.jackeryapp.com/v1/<endpoint>
```

Die HTTP-Requests scheinen form-orientiert zu sein (`RequestServer.getType()` liefert `FORM`).

### 2. HTTP-Header

Aus `com3/com/hbxn/jackery/app/AppApplication.smali` rekonstruierbar:

| Header | Wert/Quelle |
| --- | --- |
| `Accept-Language` | Sprache aus `qd/l.a()` |
| `token` | Login-/Sessiontoken aus `qd/e.f()` |
| `platform` | `2` |
| `app_version` | `v2.0.1` |
| `app_version_code` | `87` |
| `sys_version` | `Android <release>,level <sdk>/<abis>` |
| `model` | Hersteller + Gerätemodell |
| `network` | `mobile` oder `wifi` |

Für HA heißt das: Cloud-API ist möglich, aber der Token-/Loginfluss muss sauber nachgebaut werden. Ohne erfolgreichen Login sind die meisten Geräte-/Statistik-Endpunkte vermutlich nicht nutzbar.

### 3. Login / Auth

Relevante Klassen:

```text
com5/com/hbxn/jackery/http/api/LoginApi.smali
com5/com/hbxn/jackery/http/api/LoginApi$LoginBean.smali
```

Endpoint:

```text
auth/login
```

`LoginApi` sendet nicht direkt Benutzername/Passwort, sondern:

```text
aesEncryptData
rsaForAesKey
```

`LoginBean` enthält vorher u. a.:

```text
account, password, phone, verificationCode, regionCode, loginType, registerAppId, macId
```

Rekonstruierter Ablauf aus Smali:

1. `LoginBean` wird zu JSON serialisiert.
2. JSON wird per AES verschlüsselt.
3. Der AES-Schlüssel wird per `RSA/ECB/PKCS1Padding` mit einem eingebetteten Public Key verschlüsselt.
4. Request an `auth/login` enthält `aesEncryptData` und `rsaForAesKey`.

Grenze: Die vollständige Request-Ausführung liegt teilweise in nicht mitgelieferten/obfuskierten Hilfsklassen (`gj/*`, `kj/*`, `lc/*`). Der Login ist aber grundsätzlich rekonstruierbar.

### 4. Third-Party-MQTT

Das bleibt der wichtigste Integrationspfad.

UI-Klasse:

```text
com5/com/hbxn/jackery/ui/activity/home/MqttMsgActivity.smali
```

Konfigurationsmodell:

```text
com/hbxn/control/device/bean/home/ThirdPartyMqttBody.smali
```

Felder:

```text
enable, ip, port, userName, password, token
```

Die App zeigt dazu Felder:

```text
Host, Port, Username, Password, Token
```

Wenn keine MQTT-Konfiguration existiert, erzeugt die App für `token` offenbar eine zufällige Ziffernfolge. Aus der Schleife ergibt sich wahrscheinlich eine 9-stellige Zahl, weil die Kotlin-Range `0..8` genutzt wird.

#### GET Third-Party-MQTT Config

| Wert | Inhalt |
| --- | --- |
| Command | `GET_THIRD_PARTY_MQTT_CONFIG` |
| msgId/actionId | `3047` |
| BLE cmd | `114` |
| MQTT messageType | `QueryThirdPartMQTTConfig` |

MQTT-Envelope:

```json
{"deviceSn":"<DEVICE_SN>","id":<TIMESTAMP>,"version":0,"messageType":"QueryThirdPartMQTTConfig","actionId":3047,"timestamp":<TIMESTAMP>,"body":{"cmd":114}}
```

#### SET Third-Party-MQTT Config

| Wert | Inhalt |
| --- | --- |
| Command | `SET_THIRD_PARTY_MQTT_CONFIG` |
| msgId/actionId | `3046` |
| BLE cmd | `113` |
| MQTT messageType | `ThirdPartMQTTConfig` |

Body-Felder:

```json
{"ip":"<BROKER_IP>","port":1883,"userName":"...","password":"...","token":"...","enable":1,"cmd":113}
```

Wichtig: In `HomeDeviceController.g1(...)` werden `userName`, `password` und `token` vor dem Versand über `Lbb/c;->d(String)` verarbeitet. Diese Klasse fehlt weiterhin. Deshalb sollte man die Konfiguration zunächst **über die App** setzen und danach in Home Assistant nur mitschneiden/auswerten.

### 5. MQTT-Kommandohülle

Gefunden in `HomeControlFormat` und `PortableControlFormat`:

```json
{"deviceSn":"%s","id":%s,"version":%s,"messageType":"%s","actionId":%s,"timestamp":%s,"body":%s}
```

Aus `HomeControlFormat.j(...)`:

- `id` = aktueller Timestamp
- `timestamp` = gleicher Timestamp
- `version` = `0`
- `messageType` = `mqtt_message_type` aus Command-Enum
- `actionId` = `msg_id`
- `body` = JSON aus Body-Map

### 6. MQTT-Topic-Namen

Auch mit den zusätzlichen ZIPs wurden keine konkreten lokalen MQTT-Topic-Namen gefunden. Gefunden wurden nur Envelope, MessageTypes, Controller-Aufrufe und Third-Party-MQTT-Konfiguration.

Für HA ist daher der nächste praktische Schritt:

```text
Mosquitto in HA aktivieren → Jackery-App Third-Party-MQTT auf HA-Broker setzen → mosquitto_sub -v -t '#'
```

Danach lassen sich Topics und reale Payloads exakt erfassen.

## Relevante Cloud-Endpunkte

Vollständige CSV: `jackery_http_api_endpoints_v2.csv`

Auszug:

| class | path | request_fields |
| --- | --- | --- |
| AtsEleStatApi | device/stat/symmetry | beginDate,dateType,deviceSn,endDate,negative,positive |
| BoxEleStatApi | device/stat | beginDate,dateType,deviceSn,endDate,key |
| BoxPowerOutageStatApi | device/stat/cutoff | beginDate,deviceSn,endDate |
| DeviceDetailApi | device/property | deviceId |
| DeviceStatSocApi | device/stat/soc | deviceId |
| PowerReportApi | device/property/power3 | deviceSn,properties |
| SocialContributionsApi | device/stat/carbon | deviceSn |
| StatProfitApi | device/stat/profit | deviceId |
| TodayEnergyApi | device/stat/today | deviceSn |
| UserDeviceListApi | device/bind/list |  |
| home.DeviceAccessoriesApi | device/accessories | devices,id,parentDeviceId |
| home.DeviceAccessoriesExistApi | device/accessories/exist | devices |
| home.DeviceAccessoriesListApi | device/accessories/list | deviceId |
| home.DeviceAccessoriesNameApi | device/accessories/name | deviceName,id |
| home.DeviceJackeryAccessoriesExistApi | device/accessories/exists | deviceSnInfos |
| home.DiyDeviceSystemApi | device/system | bindKey,countryCode,deviceSn,gridStandard,guid,id,systemName,timezone |
| home.DiyDeviceSystemNameApi | device/system/name | id,systemName |
| home.DiyPropertySubShadowApi | device/property/subShadow | devType,deviceSn,subDeviceSn |
| home.DiyPropertySystemShadowApi | device/property/systemShadow | deviceSn,diySn |
| home.ModifyDevicePvNameApi | device/property/pv | deviceSn,index,name |
| home.ModifyDiyDeviceNameApi | device/system/deviceName | deviceName,id |
| home.SynchronizeSmartAccessoriesDataApi | device/accessories/synchronizeSmartAccessoriesData |  |
| home.SystemBindExistApi | device/system/exist | bindKey,deviceSn,guid |
| home.UserSystemListApi | device/system/list |  |
| home.ai.AiSmartScheduleApi | device/stat/getSmartSchedulePrediction | systemId |
| home.statistic.AccMeterStatApi | device/stat/meter | deviceId |
| home.statistic.AccSocketStatApi | device/stat/socket | beginDate,dateType,deviceId,endDate |
| home.statistic.BatteryStatApi | device/stat/battery | beginDate,dateType,deviceId,endDate |
| home.statistic.CtStatApi | device/stat/ct | beginDate,dateType,deviceId,endDate |
| home.statistic.DeviceStatDeviceStatistic | device/stat/deviceStatistic | deviceId |
| home.statistic.DeviceStatSocketStatistic | device/stat/smartSocketStatistic | smartSocketId |
| home.statistic.DeviceStatSystemStatistic | device/stat/systemStatistic | systemId |
| home.statistic.EpsStatApi | device/stat/eps | beginDate,dateType,deviceId,endDate |
| home.statistic.HomeStatApi | device/stat/onGrid | beginDate,dateType,deviceId,endDate |
| home.statistic.PvStatApi | device/stat/pv | beginDate,dateType,deviceId,endDate,systemId |
| home.statistic.SysBatteryStatApi | device/stat/sys/battery/trends | beginDate,dateType,endDate,systemId |
| home.statistic.SysHomeStatApi | device/stat/sys/home/trends | beginDate,dateType,endDate,systemId |
| home.statistic.SysPvStatApi | device/stat/sys/pv/trends | beginDate,dateType,endDate,systemId |
| shelly.ShellyAuthUrlApi | wss-cloud/device/shelly/auth-url |  |
| shelly.ShellyDeviceControlApi | wss-cloud/device/shelly/device/control | action,deviceId,function |
| shelly.ShellyRealDataApi | wss-cloud/device/shelly/device/realtime-power | deviceId |
| shelly.ShellyUnbindAccountApi | wss-cloud/device/shelly/unbind/account |  |
| shelly.ShellyUnbindDeviceApi | wss-cloud/device/shelly/unbind/device | bindingId,deviceId |

## Statistik-Endpunkte für HA Energy / Langzeitwerte

Besonders relevant:

| Endpoint | Request-Felder | Nutzbare HA-Daten |
| --- | --- | --- |
| `device/stat/today` | `deviceSn` | Tageswerte `de`, `dg`, `dh`, `ds` |
| `device/stat/onGrid` | `deviceId`, `dateType`, `beginDate`, `endDate` | Netzbezug / Einspeisung |
| `device/stat/pv` | `deviceId`, `systemId`, `dateType`, `beginDate`, `endDate` | PV-Erzeugung / PV-Ertrag |
| `device/stat/battery` | `deviceId`, `dateType`, `beginDate`, `endDate` | Batterie-Ladung/Entladung |
| `device/stat/ct` | `deviceId`, `dateType`, `beginDate`, `endDate` | CT-/Phasenwerte |
| `device/stat/eps` | `deviceId`, `dateType`, `beginDate`, `endDate` | EPS/offgrid In/Out |
| `device/stat/sys/pv/trends` | `systemId`, `dateType`, `beginDate`, `endDate` | Systemweite PV-Trends |
| `device/stat/sys/home/trends` | `systemId`, `dateType`, `beginDate`, `endDate` | Systemweite Hauslast-Trends |
| `device/stat/sys/battery/trends` | `systemId`, `dateType`, `beginDate`, `endDate` | Systemweite Batterietrends |

Für deine bisherige Jackery-Integration ist das wichtig: Die API bestätigt, dass `dateType`, `beginDate` und `endDate` zentrale Parameter sind. Monats-/Jahreswerte sollten deshalb weiterhin strikt mit echten Periodengrenzen abgefragt werden, nicht pauschal mit „heute bis heute“.

## Entity-Kandidaten aus Gerätemodellen

### System / Hauptgerät

Aus `SystemBody`:

```text
soc, batInPw, batOutPw, gridInPw, gridOutPw, inGridSidePw, outGridSidePw,
swEpsInPw, swEpsOutPw, maxSysInPw, maxSysOutPw, maxFeedGrid,
defaultPw, otherLoadPw, energyPlanPw, batNum, batState,
gridSate, ongridStat, ctStat, workModel, tempUnit,
isAutoStandby, standbyPw, isFollowMeterPw, offGridDown, offGridTime
```

HA-Sensoren daraus:

- Batterie-SOC
- Batterie Ladeleistung / Entladeleistung
- Netzbezug / Einspeisung
- EPS/offgrid Leistung
- System-Eingangs-/Ausgangslimit
- maximale Einspeiseleistung
- Default-/sonstige Last
- Arbeitsmodus / Temperatur-Einheit / Standby-Status

### PV

Aus `PV`:

```text
commState, name, pvPw
```

HA-Sensoren:

- PV-Leistung je Eingang/string
- PV-Kommunikationsstatus
- PV-Name

### CT / Phasen

Aus `CtSub`:

```text
aPhasePw, bPhasePw, cPhasePw, tPhasePw,
anPhasePw, bnPhasePw, cnPhasePw, tnPhasePw,
funForm, schePhase, wip
```

Aus `AccCTBody`:

```text
volt, volt1, volt2, volt3,
curr, curr1, curr2, curr3,
power, power1, power2, power3,
freq, fact, rep, ap
```

HA-Sensoren:

- Spannung je Phase
- Strom je Phase
- Wirkleistung je Phase
- Frequenz
- Leistungsfaktor
- Gesamt-/Phasenleistung

### Batteriepack/Subdevices

Aus `BatteryPackSub`:

```text
batSoc, cellTemp, inPw, outPw, isFirmwareUpgrade, version
```

Aus `BatteryPackBody`:

```text
deviceSn, ec, ip, isFirmwareUpgrade, it, op, ot, rb, version
```

HA-Sensoren:

- Pack-SOC
- Zelltemperatur
- Pack-Lade-/Entladeleistung
- Firmwarestatus
- Version

### Steckdosen / Smart Plugs

Aus `AccSocketBody`:

```text
op, sc, switch, ts
```

HA-Entities:

- Switch-Entity
- Betriebsstatus
- Zeitstempel / Statuscode

## Unterstützte Zubehör-/Drittanbieter-Geräte

In den Smali-Dateien sind Hinweise auf folgende Zubehör-/Meter-Typen enthalten:

```text
Shelly Pro EM-50
Shelly Pro 3EM
Shelly Pro 3EM63
Shelly Plug S
Shelly Plug S Gen3
EcoTracker
P1 Meter
HomeWizard
Tasmota
Homey Energy Dongle
```

Zusätzlich enthält `com5.zip` Shelly-Cloud2Cloud-Endpunkte:

```text
wss-cloud/device/shelly/auth-url
wss-cloud/device/shelly/device/control
wss-cloud/device/shelly/device/realtime-power
wss-cloud/device/shelly/unbind/account
wss-cloud/device/shelly/unbind/device
device/shelly/devices
device/shelly/binding/failures
```

Für HA ist das doppelt interessant: Einige dieser Geräte können in HA direkt zuverlässiger eingebunden werden. Die Jackery-Werte können dann gegen HA-eigene Shelly/HomeWizard/Tasmota-Werte geprüft werden.

## BLE

Weiterhin gefunden:

```text
Service UUID: 0000bdee-0000-1000-8000-00805f9b34fb
```

Frame-Formate:

```text
DFED0001%s%s%s%s0001%s%s
DFEC00%s%s%s%s
DFEC80%s%s%s%s%s%s
```

Grenze:

```text
Lbb/c; fehlt weiterhin
Lbb/d; fehlt weiterhin
Lsb/* Hilfsklassen fehlen teilweise
Lfb/b; Transporttyp fehlt
```

`BaseDeviceController` erzeugt die Verschlüsselungs-/Codec-Instanz über:

```text
new Lbb/d -> a(Lcb/b; String) -> Lbb/c
```

Ohne diese Klassen ist aktives BLE-Schreiben weiterhin nicht sicher rekonstruierbar.

## Home-Assistant-Integrationsvorschlag

### Phase 1: MQTT-Mitschnitt

1. Mosquitto-Broker in HA aktivieren.
2. In der Jackery-App MQTT öffnen (`Host`, `Port`, `Username`, `Password`, `Token`).
3. Broker-IP, Port und Zugangsdaten eintragen.
4. Auf HA mitschneiden:

```bash
mosquitto_sub -h <HA_IP> -p 1883 -u <USER> -P '<PASS>' -v -t '#'
```

5. Payloads speichern und daraus Parser bauen.

### Phase 2: Passive HA-Integration

- MQTT discovery optional erzeugen
- Device registry pro `deviceSn` / `systemSn`
- Sensoren für `SystemBody`, PV, CT, Batteriepack, Steckdosen
- Keine aktiven Commands, bis Topic- und Ack-Verhalten sauber dokumentiert sind

### Phase 3: Cloud-API-Fallback

- Login mit AES/RSA nachbauen
- Token speichern/erneuern
- `device/bind/list` und `device/system/list` für Discovery verwenden
- Statistik-Endpunkte für Langzeit-/Energy-Dashboard verwenden
- Periodengrenzen strikt einhalten

### Phase 4: Aktive Steuerung

Nur nach echten MQTT-Mitschnitten:

- Control-Commands mit `actionId`, `messageType`, `cmd`
- Ack/Timeout-Verhalten prüfen
- Keine blind gesendeten Control-Payloads

## Offene Grenzen

| Punkt | Status |
| --- | --- |
| Lokale MQTT-Topicnamen | nicht in Smali gefunden |
| Reale MQTT-Telemetrie-Payloads | nicht ohne Mitschnitt vorhanden |
| BLE-Encoding/Verschlüsselung | nicht vollständig, `Lbb/c` fehlt |
| Cloud-Login vollständig ausführbar | grundsätzlich rekonstruierbar, aber Transport-/Repository-Helfer fehlen teilweise |
| CA-Zertifikat/MQTT-TLS | in diesen ZIPs kein direkt verwertbares Jackery-MQTT-CA-Zertifikat gefunden |
| OkHttp/Coil/Zendesk | keine Jackery-spezifische Integrationslogik |

## Artefakte

- `jackery_http_api_endpoints_v2.csv` – Cloud-Endpunkte und Request-Felder
- `jackery_http_model_fields_v2.csv` – HTTP-DTO-Felder
- `jackery_command_catalog_v2.csv` – Home/Portable Commands mit IDs
- `jackery_entity_field_candidates_v2.json` – Entity-Kandidaten aus Modellen
- `jackery_ha_extraction_v2.json` – maschinenlesbare Zusammenfassung
