# Jackery SolarVault App-Protokoll: HTTP- und MQTT-Polling

Stand: 2026-04-29. Quelle: Android-App `com.hbxn.jackery` v2.0.1
(`base.apk`/Smali), Frida-Captures und Home-Assistant-Diagnose.

## Kernergebnis

Die App nutzt zwei getrennte Datenwege:

- HTTP fuer Login, Discovery, Hauptgeraete-Properties, Statistiken, Standort,
  OTA/Firmware und Strompreis-Konfiguration.
- MQTT fuer Live-Pushdaten, schreibbare Einstellungen, Wetterplan,
  Arbeitsmodus/Combine-Status und Subdevices wie Zusatzbatterien und
  Smart-Meter/CT.

Zusatzbatterien sind in der App keine normalen HTTP-Devices. Die App fragt sie
per MQTT `QuerySubDeviceGroupProperty` als `BatteryPackSub` ab. Der HTTP-Pfad
`/v1/device/battery/pack/list` existiert, liefert bei diesem SolarVault-Setup
aber `data: null`. Deshalb muss die Integration Zusatzbatterien aktiv per MQTT
im festen schnellen Polling-Takt abfragen.

## HTTP-Pfade

| Pfad | Methode | Parameter/Body | Zweck in App/Integration | Polling |
| --- | --- | --- | --- | --- |
| `/v1/auth/login` | POST | verschluesselter Login-Body | REST-Token und MQTT-Seed `mqttPassWord` | bei Setup/Reauth/Tokenablauf |
| `/v1/device/system/list` | GET | - | Systeme, Hauptgeraete, Smart-Meter-Metadaten, Land/Region | Discovery, gecacht |
| `/v1/device/bind/list` | GET | - | Legacy-Fallback fuer Explorer-/Portable-Accounts ohne `system/list`-Treffer | nur Fallback |
| `/v1/device/property` | GET | `deviceId` | Hauptgeraete-Livewerte: SOC, PV, Batterie, Grid, Limits, Modi | schnell, fest 30 s |
| `/v1/api/alarm` | GET | `systemId` | Alarme/Fehler | langsam, Statistik-Takt |
| `/v1/device/stat/systemStatistic` | GET | `systemId` | Tages-/Gesamt-KPIs des Systems | langsam, ca. 5 min |
| `/v1/device/stat/deviceStatistic` | GET | `deviceId` | Geraete-KPIs | langsam, ca. 5 min |
| `/v1/device/stat/pv` | GET | `deviceId`, `systemId`, `dateType`, `beginDate`, `endDate` | App-nahe PV-Statistiken pro Geraet | langsam, bevorzugte Quelle fuer Woche/Monat/Jahr |
| `/v1/device/stat/battery` | GET | `deviceId`, `dateType`, `beginDate`, `endDate` | App-nahe Lade-/Entlade-Statistiken pro Geraet | langsam, bevorzugte Quelle fuer Woche/Monat/Jahr |
| `/v1/device/stat/onGrid` | GET | `deviceId`, `dateType`, `beginDate`, `endDate` | App-nahe Geraete-Netzseite Eingang/Ausgang pro Geraet. Das ist nicht automatisch oeffentlicher Netzbezug/Netzeinspeisung. | langsam, bevorzugte Quelle fuer Geraete-Netzseite Woche/Monat/Jahr |
| `/v1/device/stat/ct` | GET | `deviceId` = Smart-Meter-/CT-Zubehoer-ID, `dateType`, `beginDate`, `endDate` | App-nahe CT/Smart-Meter Bezug-/Einspeisung-Statistiken | langsam, bevorzugte Quelle fuer Smart-Meter-Statistiken Woche/Monat/Jahr |
| `/v1/device/stat/meter` | GET | `deviceId` = Smart-Meter-/CT-Zubehoer-ID | Smart-Meter-Panel-Summen der App | langsam, Diagnose/Backfill |
| `/v1/device/stat/sys/pv/trends` | GET | `systemId`, `dateType`, `beginDate`, `endDate` | PV-Statistiken Tag/Woche/Monat/Jahr | langsam, Tageswechsel invalidiert Cache |
| `/v1/device/stat/sys/home/trends` | GET | `systemId`, `dateType`, `beginDate`, `endDate` | Hausverbrauchs-Statistiken | langsam, Tageswechsel invalidiert Cache |
| `/v1/device/stat/sys/battery/trends` | GET | `systemId`, `dateType`, `beginDate`, `endDate` | Batterie-Statistiken | langsam, Tageswechsel invalidiert Cache |
| `/v1/device/dynamic/powerPriceConfig` | GET | `systemId` | Strompreis-/Tarif-Konfiguration | langsam, ca. 1 h |
| `/v1/device/dynamic/priceCompany` | GET | `systemId` | Anbieter fuer dynamischen Tarif | langsam |
| `/v1/device/dynamic/historyConfig` | GET | `systemId` | historische Tarif-Konfiguration | langsam |
| `/v1/device/dynamic/saveSingleMode` | POST | `systemId`, `singlePrice`, `currency` | Einheitstarif setzen | Service/Number |
| `/v1/device/dynamic/saveDynamicMode` | POST | `systemId`, `platformCompanyId`, `systemRegion` | dynamischen Tarif setzen | Service/Select |
| `/v1/device/battery/pack/list` | GET | `deviceSn` | App/API-Pfad fuer Akku-Packs, bei SolarVault hier `data:null` | Fallback, nicht Hauptquelle |
| `/v1/device/ota/list` | GET | `deviceSnList` | Firmware/OTA pro Hauptgeraet oder Zusatzakku-SN | langsam, ca. 1 h |
| `/v1/device/location` | GET | `deviceId` | Standort/Region | langsam |
| `/v1/device/system/name` | PUT | `systemName`, `id` | System umbenennen | Service/Text |
| `/v1/device/deviceMaxPowerRecord/saveRecord` | POST | `deviceId`, `maxPower` | in App gefunden, Live-Setter nicht sicher bestaetigt | experimentell |
| `/v1/auth/generatedJwt` | GET | - | App-/H5-JWT, nicht fuer SolarVault-Telemetrie relevant | nicht genutzt |

Periodenabfragen muessen wie in der App explizite Bereiche senden:
`day=heute..heute`, `week=Montag..Sonntag`, `month=Monatserster..Monatsletzter`,
`year=01.01..31.12`. `dateType=month/year` mit `beginDate=endDate=heute`
liefert auf einigen Accounts nur tag-aehnliche Teilsummen.

## MQTT-Verbindung

| Wert | App-Wert |
| --- | --- |
| Host | `emqx.jackeryapp.com` |
| Port | `8883` |
| TLS | ja |
| Client-ID | `<userId>@APP` |
| Username | `<userId>@<macId>` |
| Passwort | AES-CBC-Ableitung aus REST `mqttPassWord` und Username, aus App-Methode `jc.e.y()` rekonstruiert |

## MQTT-Topics

| Richtung | Topic | Zweck |
| --- | --- | --- |
| Subscribe | `hb/app/<userId>/device` | Livegeraete, CombineData, Subdevice-Antworten |
| Subscribe | `hb/app/<userId>/config` | Konfigurationsantworten |
| Subscribe | `hb/app/<userId>/notice` | Hinweise/Notices |
| Subscribe | `hb/app/<userId>/alert` | Alarme |
| Publish | `hb/app/<userId>/command` | Kommandos, Subdevice-Queries, Property-Changes |
| Publish | `hb/app/<userId>/action` | App-Actions in der offiziellen App (von dieser HA-Integration derzeit nicht genutzt) |

## MQTT-Polling/Queries

| Operation | `messageType` | `actionId` | `cmd` | Body | Quelle/Zweck |
| --- | --- | ---: | ---: | --- | --- |
| Combine/System abfragen | `QueryCombineData` | `3019` | `120` | `{}` | Arbeitsmodus, Grid/Status, Limits, EPS/Auto-Standby/Follow-Meter |
| Wetterplan abfragen | `QueryWeatherPlan` | `3020` | `23` | `{}` | Unwetterwarnung und Vorwarnzeit |
| Zusatzbatterien abfragen | `QuerySubDeviceGroupProperty` | `3014` | `110` | `{"devType":1}` | App-Modell `BatteryPackSub`; bis zu 5 Zusatzakkus |
| Smart-Meter/CT abfragen | `QuerySubDeviceGroupProperty` | `3031` | `110` | `{"devType":3}` | CT/Smart-Meter Phasenleistungen |
| Combo-Subdevice abfragen | `QuerySubDeviceGroupProperty` | `3037` | `110` | `{"devType":2}` | Kombinierte Subdevice-Daten |

Weitere in der Integration geroutete MQTT-`actionId`s:
- `3015`, `3016`, `3017`, `3018`: Schedule-/Taskplan-Payloads (`DownloadDeviceSchedule` und verwandte Antworten) werden als `task_plan` verarbeitet.
- `3033`: Subdevice-Payload (zusätzlich zu `3014`/`3031`/`3037`) wird wie andere Subdevice-Daten in die Smart-Meter-/Pack-Verarbeitung geroutet.

## MQTT-Write-Kommandos

| Einstellung | `messageType`/Schema | `actionId` | `cmd` | Body-Feld |
| --- | --- | ---: | ---: | --- |
| Ladelimit | `DevicePropertyChange` | `3022` | `107` | `socChargeLimit` |
| Entladelimit | `DevicePropertyChange` | `3028` | `107` | `socDischargeLimit` |
| EPS | `DevicePropertyChange` | `3023` | `107` | `swEps` |
| Neustart | `DevicePropertyChange` | `3030` | `107` | `reboot` |
| Arbeits-/Energieverbrauchsmodus | `ControlCombine` | `3027` | `121` | `workModel` |
| Max. Einspeiseleistung | `ControlCombine` | `3029` | `121` | `maxFeedGrid` |
| Max. Ausgangsleistung | `ControlCombine` | `3038` | `121` | `maxOutPw` |
| Auto-Standby | `ControlCombine` | `3021` | `121` | `isAutoStandby`, `autoStandby` |
| Off-Grid Auto-Abschaltung | `ControlCombine` | `3039`/`3040` | `121` | `offGridDown`, `offGridTime` |
| Temperatureinheit | `ControlCombine` | `3041` | `121` | `tempUnit` |
| Standardausgangsleistung | `ControlCombine` | `3043` | `121` | `defaultPw` |
| Smart-Meter folgen | `ControlCombine` | `3044` | `121` | `isFollowMeterPw` |
| Unwetterwarnung schalten | `ControlCombine` | `3036` | `0` (kein `cmd`-Feld) | `wps` |
| Unwetter Vorwarnzeit setzen | `SendWeatherAlert` | `3034` | `0` (kein `cmd`-Feld) | `minsInterval` |
| Unwetter-Alarm loeschen | `CancelWeatherAlert` | `3035` | `0` (kein `cmd`-Feld) | `alertId` |

## Zusatzbatterie-Appmodell

Aus `HomeSubBody$BatteryPackBody`, `BatteryPackSub` und `SubBaseBean`:

| Feld | Bedeutung |
| --- | --- |
| `deviceSn` | Seriennummer des Zusatzakkus |
| `subType` | Subdevice-Typ |
| `commState` | Kommunikationsstatus |
| `scanName`, `deviceName` | App-Anzeigename |
| `commMode` | Kommunikationsmodus |
| `batSoc` | Zusatzakku-Ladestand |
| `inPw` | Zusatzakku-Ladeleistung |
| `outPw` | Zusatzakku-Entladeleistung |
| `cellTemp` | Zelltemperatur |
| `isFirmwareUpgrade` | Firmware-Upgrade verfuegbar/Status |
| `version` | Firmware-Version; bei Bedarf per `/v1/device/ota/list` ergaenzt |

## Integrationsregeln ab 1.8.2

- Hauptgeraetewerte bleiben aus `/v1/device/property` und werden nicht mit
  Zusatzakkuwerten ueberschrieben.
- Zusatzbatterien werden als eigene HA-Geraete unter dem Hauptgeraet gefuehrt.
- Zusatzbatterie-SOC/Leistung/Temperatur kommen primaer aus MQTT
  `QuerySubDeviceGroupProperty` mit `devType=1`.
- `batInPw`/`batOutPw` sind interne Batteriewerte; `stackInPw`/`stackOutPw`
  sind der komplette Batterie-Stack. Deshalb werden diese Werte getrennt als
  interne Batterie, Zusatzbatterie und Batterie-Stack bezeichnet.
- Zusatzbatterie-Firmware wird per OTA-Endpoint anhand der per MQTT bekannten
  Akku-Seriennummer ergaenzt.
- Zusatzbatterie-Seriennummer, Kommunikationsstatus und Update-Status werden
  als Diagnosewerte exponiert, wenn sie im MQTT-Subdevice-Payload vorhanden sind.
- Zusatzbatterie-Zelltemperatur wird nur angelegt, wenn der Payload wirklich
  ein per-Pack-`cellTemp` liefert; die Hauptgeraete-Zelltemperatur wird nicht
  auf Zusatzakkus kopiert.
- Smart-Meter-Werte kommen primaer aus MQTT `devType=3`.
- Der berechnete Momentan-Hausverbrauch nutzt `otherLoadPw`; falls dieser App-Wert fehlt: `Smart-Meter-Netto - Jackery-Netzseite-Eingang + Jackery-Netzseite-Ausgang`, damit ein auf einer Phase einspeisender SolarVault den Hausverbrauch nicht weg saldiert.
- MQTT-Subdevice-Polling folgt dem festen schnellen HA-Takt.
- App/MQTT/Combine-gestuetzte Sensoren werden beim Start nicht mehr aus der
  Entity-Registry entfernt, nur weil der erste Payload einzelne Keys noch nicht
  enthaelt.
- Statistiken/Trends und Preis-/OTA-Daten bleiben absichtlich langsamer
  gecacht, weil die Cloud sie nicht sekundenaktuell liefert.
