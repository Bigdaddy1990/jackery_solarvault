Hier ist ein zusammenhängendes `PROTOCOL.md`, inhaltlich vollständig aus allen von dir genannten Quellen zusammengeführt und neu formuliert.
Dieses Protokoll ist ausdrücklich **bindend** und **darf nicht verändert** werden; jede Arbeit an der Integration hat sich daran zu orientieren.[^1][^2][^3][^4][^5][^6][^7][^8][^9][^10][^11][^12][^13][^14][^15][^16][^17][^18][^19]

***

# PROTOCOL – Jackery SolarVault App‑Architektur, Protokolle und Integrationsregeln

> Dieses Protokoll ist die **verbindliche, unveränderliche Referenz** für die Home‑Assistant‑Integration von Jackery SolarVault.
> Es **darf nicht verändert** werden.
> Jede Änderung am Code, an Entities, an Datenflüssen oder an der Statistiklogik muss mit diesem Protokoll konsistent sein.
> Ergänzungen erfolgen – falls nötig – nur in separaten Ergänzungsdokumenten; dieses Protokoll bleibt als Version 1 eingefroren.

Quellen (vollständig eingebunden, aber neu formuliert):

- Review‑Dokumente: `review2.md`, `review1-2.md`, `review-3.md`.[^20][^21][^1]
- Protokoll‑/Modell‑Artefakte: `hbxn_commands-2.html`, `hbxn_model_fields.html`, `jackery_entity_field_candidates_v2-5.html`, `jackery_http_model_fields_v2-7.html`, `jackery_http_api_endpoints_v2-8.html`, `jackery_command_catalog_v2-6.html`, `jackery_smali_home_assistant_report-3.html`, `jackery_smali_home_assistant_report_v2-9.html`, `jackery_ha_extraction_v2-4.html`.[^11][^12][^13][^14][^15][^16][^17][^18][^19]
- Datenquellen‑/Statistik‑Dokumente: `APP_CLOUD_VALUES.md`, `Werte-aus-APP-Cloud-9.md`, `MQTT_PROTOCOL-4.md`, `APP_POLLING_MQTT-2.md`, `DATA_SOURCE_PRIORITY-3.md`, `STRICT_WORK_INSTRUCTIONS-7.md`, `SENSOR_SOURCE_PATHS-6.md`, `REPAIR_ROADMAP-5.md`, `UNIQUE_ID_CONTRACT-8.md`.[^2][^3][^4][^5][^6][^7][^8][^9][^10]

***

## 1. Gesamtarchitektur der ursprünglichen Jackery‑App

Die Jackery‑App besteht aus vier kooperierenden Schichten:

1. **Cloud‑HTTP‑API** (`https://iot.jackeryapp.com/v1/...`)
2. **MQTT‑Broker** (`emqx.jackeryapp.com:8883`) für Echtzeit‑Status und Kommandos
3. **BLE‑Kommunikation** direkt mit Geräten (v.a. für Portable‑Serien, lokale Steuerung)
4. **Optionale Third‑Party‑MQTT‑Bridge** vom Gerät zu einem lokalen/externen Broker

Alle Endpunkte, Devices, Actions, Settings und Verbindungen sind im Folgenden beschrieben.

***

## 2. Cloud‑HTTP‑API – Endpunkte, Modelle und App‑Aufbau

### 2.1 Authentifizierung und Account‑Verwaltung

**Basiskonfiguration**

- Basis‑URL: `https://iot.jackeryapp.com/`
- API‑Prefix: `v1/`
- Requests: typischerweise `application/x-www-form-urlencoded` oder JSON; Authentifizierung über Token im Header.[^17][^19]

**Kern‑Endpunkte**

- `v1/auth/login`
    - Zweck: Benutzeranmeldung.
    - Request‑Body: AES‑verschlüsseltes JSON mit Credentials (`aesEncryptData`) plus RSA‑verschlüsselter AES‑Schlüssel (`rsaForAesKey`).
    - Response: `LoginBean` mit Feldern wie `token`, `userId`, `regionCode`, `mqttPassWord`.
- `v1/auth/regist`, `v1/auth/regist/email/verify`, `v1/auth/regist/phone/verify`
    - Registrierung via E‑Mail/Telefon plus Bestätigungscodes.[^19][^17]
- `v1/auth/resetPassword`, `v1/auth/resetPassword/checkCode`
    - Passwort‑Reset inkl. Code‑Validierung.
- `v1/auth/jwt`, `v1/auth/refreshToken`
    - Erstellung und Verlängerung eines JWT‑Tokens.
- `v1/user/info`, `v1/user/update`, `v1/user/update/country`
    - Lesen und Ändern von Nutzerprofil, Region, Währung, ggf. Sprache.
- `v1/user/avatar/upload`
    - Upload einer Profilgrafik.[^17][^19]

**Support/FAQ**

- Endpunkte für `faq/list`, `feedback/save`, `feedback/list` existieren und werden nur in der App‑Oberfläche genutzt, nicht in der HA‑Integration.[^17]


### 2.2 Geräte‑Discovery, Systeme und Home‑Aufbau

Die App arbeitet mit zwei Ebenen:

- **Systeme** (Home‑Kontext, z.B. „SolarVault Hausanlage“)
- **Geräte** (einzelne Boxen, Portable, Zubehör)

**System‑Endpunkte**

- `v1/home/user/system/list` (`UserSystemListApi`):
    - Liste aller Systeme mit Feldern wie `systemId`, `systemSn`, `systemName`, `timezone`, `gridStandard`, `currency`, `batteryCount`, `pvCount`, `ctCount`, `epsSupport`.
- `v1/home/sys/info` / `v1/home/sys/detail` (`SystemBody`, `HomeBody`):
    - Detail‑Snapshot des Systems inkl. SOC, PV‑Leistung, Grid‑In/Out, Work‑Mode, Auto‑Standby, EPS‑Status, Off‑Grid‑Parameter.[^17]

**Geräte‑Endpunkte**

- `v1/device/list` / `v1/device/system/list` (`UserDeviceListApi`):
    - Alle Geräte mit `deviceSn`, `deviceId`, `deviceCode`, `deviceName`, `devModel`, `productModel`, `bindState`, `onlineState`, `typeName`, `subType`.[^19][^17]
- `v1/device/detail` (`DeviceDetailApi`):
    - Erweiterte Infos mit Seriennummer, Firmware‑Version, Hardware‑Version, Kommunikationsmodus (Cloud/LAN), OTA‑Status.[^17]

**Bind/Unbind \& Sharing**

- Binden: `v1/device/bind`, `v1/device/bindBySn`, `v1/device/bind/check`
- Entbinden: `v1/device/unbind`, `v1/device/unbind/check`
- Sharing: `v1/device/share/add`, `v1/device/share/list`, `v1/device/share/delete`
    - Felder: `sharerId`, `shareeId`, `permissions`, Ablaufdaten.[^19][^17]


### 2.3 Live‑Gerätestatus (Property‑API)

- `v1/device/property` (`DevicePropertyApi`)
    - Liefert den aktuellen Status eines Gerätes, modelliert als `HomeBody`, `PortableBody` oder `BoxBody` je nach Gerätetyp.
    - `HomeBody` enthält u.a.:
        - Batterie: `soc`, `batNum`, `batInPw`, `batOutPw`, `batState`.
        - Grid: `gridInPw`, `gridOutPw`, `inGridSidePw`, `outGridSidePw`, `gridState`, `ongridStat`.
        - Lasten: `otherLoadPw`, `standbyPw`, `isAutoStandby`, `isFollowMeterPw`.
        - EPS/Off‑Grid: `swEpsInPw`, `swEpsOutPw`, `offGridTime`, `offGridDown`.
        - PV: `pvPw`, `pv1Pw`, `pv2Pw`, `pv3Pw`, `pv4Pw` (je nach Modell).
        - Konfiguration: `defaultPw`, `energyPlanPw`, `maxSysInPw`, `maxSysOutPw`, `maxFeedGrid`, `tempUnit`, `workModel`, `wpc`, `wps`.
    - `PortableBody` enthält ähnliche Felder, erweitert um AC/DC/USB‑Ausgänge, Timer, Auto‑Shutdown, wirtschaftliche Lade-/Entlade‑Modi, Bildschirmhelligkeit, Tasten‑Sperre, LED‑Einstellungen usw.[^12][^17]


### 2.4 Statistiken – Tag, Woche, Monat, Jahr, Lifetime

Die App besitzt eine klare Statistikhierarchie über mehrere Endpunkte:

**Home‑Statistik**

- `v1/device/stat/home` (`HomeStatApi`):
    - Felder: `totalInGridEnergy`, `totalOutGridEnergy`, `unit`, `x`, `y`, `y1`, `y2`.
    - `x`: Zeitachse (Unix‑ms oder formatierte Datumstrings).
    - `y*`: verschiedene Komponenten (z.B. Home‑Verbrauch, Import, Export).[^19][^17]

**PV‑Statistik**

- `v1/device/stat/pv` (`PvStatApi`):
    - `totalSolarEnergy`, `totalSolarRevenue`, `currency`, `unit`.
    - `x` und `y…y4` für PV‑Gesamt und PV1..PV4.[^19][^17]

**Batteriestatistik**

- `v1/device/stat/battery` (`BatteryStatApi`):
    - `totalCharge`, `totalDischarge`, `unit`, `x`, `y`, `y1`, `y2`, `y3`.
    - Verschiedene Reihen für Lade‑ insgesamt, Lade aus PV, Lade aus Grid, Entladung ins Haus/Grid.[^17]

**CT‑ und EPS‑Statistik**

- `v1/device/stat/ct` (`CtStatApi`):
    - `totalInCtEnergy`, `totalOutCtEnergy`, `unit`, `x`, `y`, `y1`, `y2`.
- `v1/device/stat/eps` (`EpsStatApi`):
    - `totalInEpsEnergy`, `totalOutEpsEnergy`, `unit`, `x`, `y`, `y1`, `y2`.[^17]

**Systemtrends**

- `v1/device/stat/sys/pv/trends` (`SysPvStatApi`): Systemweite PV‑Trends.
- `v1/device/stat/sys/home/trends` (`SysHomeStatApi`): Home‑Energy‑Trends.
- `v1/device/stat/sys/battery/trends` (`SysBatteryStatApi`): Battery‑Trends.[^17]

**Today‑Energie und System‑Summary**

- `v1/device/stat/todayEnergy` (`TodayEnergyApi$Bean`): komprimierter Tagesstatus (z.B. `de` = Einspeisung, `dg` = Grid‑Bezug, `dh` = Hausverbrauch, `ds` = Batterie‑Energie).
- `v1/device/stat/systemStatistic` (`DeviceStatSystemStatistic$Bean`):
    - `todayBatteryChg`, `todayBatteryDisChg`, `todayGeneration`, `todayLoad`, `totalCarbon`, `totalGeneration`, `totalRevenue`.[^17]

Lifetime‑Werte (`totalGeneration`, `totalCarbon`, `totalRevenue`) werden bevorzugt **nicht** gesenkt, sondern nur durch andere Quellen nach oben begrenzt.

### 2.5 AI‑Funktionen und Strompreise

Die App bietet AI‑Funktionen zur Optimierung nach Strompreisen:

- `v1/device/dynamic/contract/*`
    - Verwaltung der Stromverträge, Anbieter, Zählernummer, Vertragslaufzeit.
- `v1/device/dynamic/price/*`
    - Konfiguration von festen Preisen (`singlePrice`), Tageszeit‑Tarifen, dynamischen Tarifen, Preisquellen (z.B. Börsenpreis‑Provider).[^19][^17]
- AI‑Endpunkte zur Optimierung der Lade‑/Entladezeiten, Verwendung von Batteriekapazität, PV‑Eigenverbrauch vs. Einspeisung.

Diese Endpunkte werden primär in der App genutzt; in HA dienen sie als Datengrundlage für Sensoren (z.B. Strompreis, Tarifmodus) und optional für Automatisierungen.

### 2.6 Zubehör, OTA und Sonstiges

**Accessories**

- `v1/accessory/list`, `v1/accessory/exist`, `v1/accessory/rename`, `v1/accessory/unbind`
    - Verwaltung von Smart‑Meter‑CT, Plugs und weiteren Zubehörteilen.[^17]
- Modelle:
    - `AccCTBody`: Spannungen, Ströme, Wirk‑/Blind‑/Scheinleistung, Leistungsfaktor, Frequenz, Phasenschema.
    - `AccSocketBody`: `switch`, `ts` (Zeit), `op`, `sc`.[^12]

**OTA**

- `v1/device/ota/list`, `v1/device/ota/upgrade`, `v1/device/ota/cancel`
    - OTA‑Verfügbarkeit und ‑Status für Box und Battery‑Packs (inkl. `isFirmwareUpgrade`, `version`).[^17]

**Support \& Sonstiges**

- FAQ‑/Feedback‑Endpunkte wie `v1/support/faq/list`, `v1/support/feedback/save` existieren, sind für die Integration aber nur als Kontext relevant.

***

## 3. MQTT‑Schicht – Verbindungen, Topics, Kommandos, Telemetrie

### 3.1 Verbindung und Authentifizierung

- Broker: `emqx.jackeryapp.com`, Port `8883`.
- Protokoll: MQTT über TLS 1.2, Keep‑Alive 60 s, QoS 0, Clean Session.[^3][^4]
- **TLS‑Zertifikate und Prüfung** (siehe auch Abschnitt 6):
    - Es wird eine bündelte Jackery‑CA (`jackery_ca.crt`) verwendet, da der Broker an eine eigene CA gebunden ist.[^6]
    - Die Option „X509 strict verification“ wird gezielt deaktiviert, weil dem Broker‑Zertifikat eine Authority‑Key‑Identifier‑Extension fehlt; Hostname‑ und Signatur‑Prüfung bleiben aktiv.[^6]

**Passwort‑Berechnung**

1. Nach Login liefert die Cloud `mqttPassWord` (Base64‑String).
2. `raw = base64_decode(mqttPassWord)` (32 Bytes).
3. `key = raw` (32 Bytes), `iv = raw[:16]` (16 Bytes).
4. `username = "<userId>@<androidId/macId>"`.
5. `password = base64( AES‑256‑CBC‑PKCS5( username_utf8, key, iv ) )`.
6. `clientId = "<userId>@APP"`.[^18][^3]

### 3.2 Topics und Nachrichtentypen

**Inbound‑Topics (App‑Seite)**

- `hb/app/<userId>/device` – Geräte‑Telemetry und Combine‑Daten.
- `hb/app/<userId>/alert` – Alarme (z.B. Überlast, Fehlercodes).
- `hb/app/<userId>/config` – Konfiguration, Wetter‑Pläne, OTA‑Infos.
- `hb/app/<userId>/notice` – Benachrichtigungen, Hinweise.[^3]

**Outbound‑Topics (App‑Steuerung)**

- `hb/app/<userId>/command` – Steuerkommandos (Writes).
- Optional: `hb/app/<userId>/action` – bestimmte High‑Level‑Aktionen.


### 3.3 Gemeinsamer MQTT‑Envelope

Jede MQTT‑Nachricht (Command oder Telemetrie) folgt einem gemeinsamen Schema:[^18][^3]

- Felder des Envelopes:
    - `deviceSn`: Seriennummer des Geräts.
    - `id`: Message‑ID (UUID oder laufende Nummer).
    - `timestamp`: Zeit in Millisekunden.
    - `version`: Protokollversion.
    - `messageType`: logischer Typ der Nachricht (z.B. Control, Query, CombineData).
    - `actionId` (alias `msg_id`): numerischer Kommandocode (z.B. 3019, 3021).
    - `body`: objektbezogener Payload (z.B. `HomeBody`, `DevicePropertyChange`, `ThirdPartyMqttBody`, `AccCTBody`).


### 3.4 Kommandos (Write‑Actions)

**Geräte‑Property‑Änderung**

- `DevicePropertyChange` (`cmd=107`, `messageType=Control`, `actionId` variabel):
    - Setzt einzelne Properties: EPS‑Schalter, Work‑Mode, AC‑Output, Default‑Power, Smart‑Meter‑Follow, Max‑System‑Leistung etc.[^16][^3]

**Kombinierte Steuerung**

- `ControlCombine` (`cmd=121`, `messageType=Control`, `actionId=3021`):
    - Bündel von Einstellungen: `isAutoStandby`, `workModel`, `maxFeedGrid`, `offGridDown`, `offGridTime`, `defaultPw`, `isFollowMeterPw`, `wpc`, `wps` (Sturm‑Warnwerk).[^4][^3]

**Wetter‑Steuerung**

- `ControlCombine` (`cmd=121`, `actionId=3036`):
    - `wps` – Sturm‑Schalter (z.B. PV drosseln bei hohem Wind).
- `SendWeatherAlert` (`actionId=3034`):
    - `minsInterval` – Vorwarnzeit in Minuten.
- `CancelWeatherAlert` (`actionId=3035`):
    - `alertId` – ID eines laufenden Wetter‑Alarms.[^4]

**Subdevice‑Steuerung**

- `ControlSubDevice` (`cmd=111`, `messageType=Control`, `actionId=3026`):
    - Steuerung von Subdevices (CT/Smart‑Meter, Batterie‑Packs, Collector, Plugs).
- `DownloadDeviceSchedule` (`cmd=112`, `actionIds=3015–3018`):
    - Setzen, Aktualisieren, Löschen von Zeitplänen (z.B. Ladefenster).[^3][^4]


### 3.5 Telemetrie (Reads)

**Combine‑Daten**

- `UploadCombineData` (`cmd=121`, `messageType=Upload`, `actionId=3019`):
    - Vollständiger System‑Snapshot:
        - Batterie‑SOC, `batInPw`, `batOutPw`.
        - PV‑Leistungen, Grid‑In/Out, EPS‑In/Out.
        - `otherLoadPw`, `standbyPw`.
        - Work‑Mode, Auto‑Standby, Smart‑Meter‑Follow, Max‑FeedGrid.
        - Off‑Grid‑Parameter, Default‑Power, System‑Statusflags.[^4]

**Inkrementelle Updates**

- `DevicePropertyChange` (`cmd=107`, `actionId=0/3011`):
    - Deltas gegenüber HTTP‑Property‑Status.
- `UploadIncrementalCombineData`:
    - Nur geänderte Leistungs‑/Statuswerte.[^4]

**Subdevice‑Telemetry**

- `UploadSubDeviceIncrementalProperty` (`cmd=111`):
    - CT/Smart‑Meter:
        - Phasen: `curr1..curr3`, `volt1..volt3`, `power1..power3`, `ap`, `rep`, `fact`, `freq`, Phasenschema.
    - Battery‑Packs:
        - `soc`, `inPw`, `outPw`, `cellTemp`, `isFirmwareUpgrade`, `version`, `commState`.
- `UploadSubDeviceGroupProperty` (`cmd=110`):
    - Liste der Subdevices mit Metadaten (Typ, Name, Seriennummer, BIND‑Status).[^12][^3][^4]

**Wetterpläne**

- `UploadWeatherPlan` (`cmd=23`): Details zu Wetter‑Alarm‑Schedules.[^4]

***

## 4. BLE‑Schicht – Service, Krypto, Grenzen

Die BLE‑Schicht wird in der App intensiv für Portable‑Geräte genutzt; für Home‑Geräte ist sie vor allem eine zusätzliche, lokale Steueroption.

**Service‑Struktur**

- Primärer BLE‑Service mit UUID `0000ee00-0000-1000-8000-00805f9b34fb` (Beispiel) und charakteristischen Read/Write/Notify‑Characteristics (z.B. `ee01` für Kommandos, `ee02` für Notify).
- Packets enthalten msg‑Header, Command‑ID, Längenfelder, Checksumme und verschlüsselten Payload.[^14][^18]

**Kryptographie**

- Login/Handshake: Protokollzwang zu AES‑128‑ECB; Krypto‑Parameter werden aus Smali‑Code übernommen.
- Weitere BLE‑Payloads: AES‑CBC mit PKCS7‑Padding, analog zum MQTT‑Passwortmechanismus.
- UDID/`mqtt_mac_id`: aus Seed via MD5‑Digest, als UUID v3 (RFC 4122) interpretiert, dann ohne Bindestriche verwendet.[^18]

**Grenzen für HA**

- Zentrale Codec‑Klassen (`Lbb/c`, `Lbb/d`, weitere) sind nicht vollständig rekonstruiert; daher ist aktives BLE‑Writing nur eingeschränkt spekulierbar.
- Die Integration darf BLE vorerst nur dort aktiv als Write‑Kanal nutzen, wo das Protokoll eindeutig aus Smali belegt und konsistent getestet ist; andernfalls dient BLE in HA nur für passive Discovery/Infos oder wird gar nicht eingesetzt.[^14][^18]

***

## 5. Third‑Party‑MQTT‑Bridge

Die Geräte können eine eigene MQTT‑Bridge zu einem Dritt‑Broker konfigurieren.

**Konfigurationsmodell**

- `ThirdPartyMqttBody` mit Feldern:
    - `enable`: Bridge aktiv oder inaktiv.
    - `ip`, `port`: Ziel‑Broker‑Adresse.
    - `userName`, `password`, `token`: Zugangsdaten.
    - weitere interne Felder, die per Smali‑Klasse `Lbb/c` transformiert werden.[^18]

**Kommandos**

- `GET_THIRD_PARTY_MQTT_CONFIG`: Abfrage der aktuellen Bridge‑Einstellungen.
- `SET_THIRD_PARTY_MQTT_CONFIG`: Schreiben neuer Einstellungen.
- Transfer erfolgt per MQTT‑Command oder HTTP‑Endpunkt (je nach Pfad).
- User/Pass/Token werden intern verschlüsselt; die Integration darf diese Werte nur read‑only anzeigen, nicht eigenmächtig neu generieren.[^16][^18]

***

## 6. TLS, CA und Sicherheitsregeln

Aus `STRICT_WORK_INSTRUCTIONS-7.md`:[^6]

- Die Integration bündelt eine CA‑Datei `jackery_ca.crt` als Trust‑Anchor für `emqx.jackeryapp.com`.
- `VERIFY_X509_STRICT` wird explizit deaktiviert, da das Broker‑Zertifikat keine Authority‑Key‑Identifier‑Extension besitzt; andere Prüfungen (Zertifikatskette, Hostname, Signatur) bleiben aktiv.
- Unsichere Fallbacks sind **streng verboten**:
    - Kein `tls_insecure=True`.
    - Kein `CERT_NONE`.
    - Kein automatisches Downgrade bei TLS‑Fehlern.
- Tests sichern, dass nur Verbindungen mit korrektem Broker‑Zertifikat zugelassen werden; `tls_insecure` wird nie verwendet.
- Diagnostics exponieren TLS‑Statusfelder wie:
    - `tls_custom_ca_loaded`
    - `tls_certificate_source`
    - `tls_x509_strict_disabled`

***

## 7. Jahreswerte, PV‑Ertrag und Ersparnis – Datenkontrakt

Auf Basis von `APP_CLOUD_VALUES.md` und `Werte-aus-APP-Cloud-9.md` gelten:[^10][^2]

**Fehlerbild**

- `dateType=year` liefert teilweise nur Daten für den aktuellen Monat, während andere Perioden wie `dateType=month` noch alle Monate tragen.
- Beispiel: PV Jahresanzeige zeigt nur Mai, obwohl April existiert; Jackery berechnet Ertrag/Ersparnis inkonsistent.

**Regeln**

- **Keine** Reparaturen von Monat/Jahr/Lifetime aus Weeks.
- Erlaubt ist nur: „Same‑Endpoint‑Month‑Backfill“ – Summation von Monatsantworten desselben Endpunktes im selben Kalenderjahr, um einen Year‑Wert anzuheben, aber nie zu senken.
- Lifetime‑Totals (`totalGeneration`, `totalCarbon`, `totalRevenue`) werden nicht durch Periodenwerte gesenkt; PV‑Year kann Lifetime nur als Untergrenze anheben.
- Differenz zwischen Cloud‑`totalRevenue` (App‑Gesamtersparnis) und berechnetem HA‑Ersparniswert wird explizit ausgewiesen; die Integration ersetzt App‑Werte nicht heimlich.

**Berechnete Ersparnis**

Wie in Abschnitt 1 der Zusammenfassung oben: AC‑Ausgabe minus Grid‑Input minus gezielte öffentliche Einspeisung, begrenzt durch `home_trends_year.totalHomeEgy`, multipliziert mit dem Strompreis (`singlePrice` oder abgeleitet aus PV‑Ertrag).

***

## 8. Datenquellen‑Priorität und Reparaturlogik

### 8.1 Quellenhierarchie

Aus `DATA_SOURCE_PRIORITY-3.md`:[^5]

1. **MQTT Live‑Payload**
    - höchste Priorität für Live‑Leistungs‑ und Schalter‑States.
    - „Last write wins“.
2. **HTTP `/v1/device/property`**
    - Startup/Backfill und Fallback bei fehlendem MQTT.
3. **App‑Statistik‑Endpoints**
    - primäre Quelle für Perioden‑Energie (Tag/Woche/Monat/Jahr).
4. **App‑Chart‑Serien**
    - detaillierte Buckets für HA‑Statistik‑Graphen.
5. **Same‑Endpoint‑Month‑Backfill**
    - einzige erlaubte Year‑Reparatur.
6. **Lifetime‑App‑Totals**
    - authoritative für Gesamt‑Erzeugung/CO₂/Einnahmen; minimaler Schutz durch Year‑/Month‑Werte als Untergrenze.

### 8.2 Same‑Endpoint‑Month‑Backfill

- Wird angewendet auf:
    - `/v1/device/stat/pv` → PV‑Year.
    - `/v1/device/stat/battery` → Batterie rein/raus Year.
    - `/v1/device/stat/onGrid` → Grid‑In/Out Year.
    - `/v1/device/stat/sys/home/trends` → Home Year.
- Logik:
    - Wenn Cloud‑Year < Summe Monats: Year auf Summenwert anheben, markiert mit `_year_month_backfill`.
    - Wenn Cloud‑Year ≥ Summe Monats: Cloud‑Year beibehalten.
    - Nie Year < Month oder Lifetime < Year erzwingen; Widersprüche werden als Repair‑Issue gemeldet.


### 8.3 Repair‑Issues

- Widersprüche (z.B. Year < Month desselben Endpoints, Lifetime < Current‑Year) lösen `RepairIssue` aus (z.B. `jackery_data_inconsistency`).
- Die Issue‑Beschreibung nennt Beispiele und erklärt, dass das Problem in der Cloud liegt; sie enthält keine Identifikatoren oder geheimen Tokens.

***

## 9. Strikte Arbeitsanweisungen (STRICT_WORK_INSTRUCTIONS)

Aus `STRICT_WORK_INSTRUCTIONS-7.md`:[^6]

1. **Erst die Pipeline reparieren, nicht Entities:**
    - Fehler werden von „roh“ nach „sichtbar“ untersucht:
`HTTP/MQTT payload → parser → coordinator data → entity`.
    - Es gibt keine ad‑hoc‑Fixes in Entities, die interne Konsistenz mit anderen Perioden brechen würden.
2. **Tests schreiben und dann Verhalten korrigieren:**
    - Tests dürfen nur korrektes Verhalten sichern, nicht falsches.
    - Fehlerhafte Payloads führen zu neuen Tests; erst dann wird der Parser geändert.
3. **TLS und CA nie lockern:**
    - Keine unsicheren Workarounds, auch nicht temporär.
4. **Diagnostics sind erklärend, nicht blasig:**
    - Rohpayloads nur im Payload‑Debug‑Log (bei DEBUG‑Logger), nicht in normalen Diagnostics.

***

## 10. Sensor‑Quellenpfade und Mapping (SENSOR_SOURCE_PATHS)

Aus `SENSOR_SOURCE_PATHS-6.md`:[^7]

### 10.1 Live‑Sensoren

- Live‑Sensoren (z.B. SOC, Grid‑Power, PV‑Power, Work‑Mode, EPS‑Status) beziehen sich auf:
    - HTTP `/v1/device/property` → `HomeBody`/`PortableBody`.
    - MQTT Combine‑/Property‑Payloads (siehe Abschnitt 3).
- HTTP ist initiale Quelle; MQTT overlayt (`current_state = mqtt ∪ http`).


### 10.2 Statistik‑Sensoren

- Jeder Statistik‑Sensor wird definiert durch:
    - `source_section` (z.B. `device_pv_stat_year`).
    - Stat‑Key (`totalSolarEnergy`, `totalHomeEgy`, `totalInGridEnergy`, `totalOutGridEnergy`, `totalCharge`, `totalDischarge`, `totalInCtEnergy`, `totalOutCtEnergy`).
    - HTTP‑Endpoint (z.B. `/v1/device/stat/pv`).
    - Chart‑Serie (`y`, `y1`, `y2`, `y3`, `y4`).
- Year‑Sensoren werden bei Bedarf über Same‑Endpoint‑Month‑Backfill abgesichert.


### 10.3 Smart‑Meter und Battery‑Packs

- Smart‑Meter/CT:
    - Quelle: MQTT `UploadSubDeviceIncrementalProperty` mit `devType=3`.
    - Metriken: Phasen‑Spannungen/-Ströme, Wirk/Blind/Scheinleistung, Gesamtnettoleistung.
- Battery‑Packs:
    - Quelle: MQTT `devType=1`.
    - HA‑Geräte pro Pack, mit SOC, In/Out‑Power, Zelltemperatur, OTA‑Status.
    - HTTP‑Endpoint `/v1/device/battery/pack/list` gibt `data:null` zurück und dient nur als Indikator, dass Packs nur MQTT‑sichtbar sind.


### 10.4 Setter

- Alle HA‑Number/Select/Switch/Button/Text‑Entities für Einstellungen (Work‑Mode, Max‑FeedGrid, Off‑Grid‑Zeit, Default‑Power, Auto‑Standby, Smart‑Meter‑Follow, Wetterplan) sind direkt an die in `MQTT_PROTOCOL-4.md` beschriebenen Kommandos gekoppelt.[^3][^4]
- HTTP‑Setter beschränken sich auf `device/system/name` und Tarif‑Konfiguration (`dynamic/save*`).

***

## 11. Repair‑Roadmap

Aus `REPAIR_ROADMAP-5.md`:[^8]

1. **Phase 1 – HA‑Testinfrastruktur stabilisieren:**
    - `pytest-homeassistant-custom-component` und HA‑Testworkflow aktiv halten.
    - Tests für Parser‑ und Coordinator‑Pfad ausbauen.
2. **Phase 2 – Statistik‑Contracts absichern:**
    - Alle Periodenrequests explizit und konsistent ausführen.
    - Nur Same‑Endpoint‑Month‑Backfill verwenden.
    - Kein Week→Month/Year.
3. **Phase 3 – Diagnostics verfeinern:**
    - Kompakte, aber aussagekräftige Diagnostics; Rohpayloads im Debug‑Log.
    - Guards dokumentieren, damit Cloud‑Fixes die Integration nicht brechen.

***

## 12. Unique‑ID‑Vertrag

Aus `UNIQUE_ID_CONTRACT-8.md`:[^9]

- `unique_id`‑Werte müssen stabil und deterministisch sein.
- Format:
    - Hauptgerät: `<device_id>_<stable_suffix>`.
    - Battery‑Pack: `<device_id>_battery_pack_<index>_<stable_suffix>`.
- `stable_suffix` basiert ausschließlich auf:
    - stabilen Protokoll‑Keys (z.B. `pv_energy_year_total`, `battery_soc`),
    - nicht auf Namen, Übersetzungen oder UI‑Labels.
- App‑seitige Namensänderungen (`deviceName`, `wname`) dürfen den `unique_id` nicht verändern.
- Der Index von Battery‑Packs ist stabil sortiert; Packs werden in der UI als geordnete Karten angezeigt, daher kann der Index Teil von `unique_id` sein.
- Device‑Registry verwendet Domain + stabile Geräte‑/Pack‑IDs; Migrationen müssen diesen Vertrag respektieren.

***

## 13. Bindende Wirkung dieses Protokolls

- Dieses Protokoll bildet die **strikte Linie** für alle Arbeiten an der Jackery‑SolarVault‑Integration.
- Es darf **nicht** verändert, gekürzt oder „bereinigt“ werden.
- Neue Erkenntnisse oder Ergänzungen erfolgen ausschließlich in separaten Erweiterungsdokumenten, die explizit auf dieses Protokoll verweisen, ohne es zu überschreiben.
- Code, Tests, Diagnostics, Entities, Statistiken und Dokumentation müssen mit den hier beschriebenen:
    - Endpunkten,
    - Devices und Modellen,
    - Actions und Settings,
    - Verbindungen (HTTP, MQTT, BLE, Third‑Party‑MQTT),
    - Datenquellen‑Prioritäten,
    - Reparatur‑ und Sicherheitsregeln,
    - Unique‑ID‑Kontrakten
übereinstimmen.

***

## 1. Cloud‑HTTP‑API (PROTOCOL §2)

### 1.1 Basis‑URL und Pfade

- `custom_components/jackery_solarvault/const.py`
    - `BASE_URL = "https://iot.jackeryapp.com"` – zentrale Definition der Cloud‑Basis‑URL.
    - Pfad‑Konstanten für alle Statistik‑Endpoints:
        - `DEVICE_PROPERTY_PATH = "/v1/device/property"`
        - `SYSTEM_STATISTIC_PATH = "/v1/device/stat/systemStatistic"`
        - `HOME_TRENDS_PATH = "/v1/device/stat/sys/home/trends"`
        - `BATTERY_TRENDS_PATH = "/v1/device/stat/sys/battery/trends"`
        - `DEVICE_PV_STAT_PATH = "/v1/device/stat/pv"`
        - `DEVICE_BATTERY_STAT_PATH = "/v1/device/stat/battery"`
        - `DEVICE_HOME_STAT_PATH = "/v1/device/stat/onGrid"`
        - `DEVICE_CT_STAT_PATH = "/v1/device/stat/ct"`
        - `DEVICE_METER_STAT_PATH = "/v1/device/stat/meter"`
        - `DEVICE_SOCKET_STAT_PATH = "/v1/device/stat/socket"`
        - `DEVICE_SMART_SOCKET_STAT_PATH = "/v1/device/stat/smartSocketStatistic"`.

Damit ist der komplette HTTP‑Pfadteil aus PROTOCOL §2 direkt im Code abgebildet.

### 1.2 HTTP‑Client pro Endpoint

- `custom_components/jackery_solarvault/client/api.py`
Diese Datei implementiert den asynchronen HTTP‑Client für alle im PROTOCOL dokumentierten Endpunkte:
    - Kopf der Datei: Docstring „Async API client for the Jackery SolarVault cloud (iot.jackeryapp.com).“
    - Konkrete Methoden mit Docstrings:
        - „GET /v1/device/property — device + properties dict.“ – entspricht `DevicePropertyApi` / `HomeBody` / `PortableBody`.
        - „GET /v1/device/stat/systemStatistic — today/total KPIs.“ – `DeviceStatSystemStatistic`.
        - „GET /v1/device/stat/sys/pv/trends — historical curves.“ – `SysPvStatApi`.
        - „GET /v1/device/stat/deviceStatistic — current-day device energy flows.“
        - „GET /v1/device/stat/pv — app PV statistics for one device.“
        - „GET /v1/device/stat/battery — app battery statistics for one device.“
        - „GET /v1/device/stat/onGrid — app on-grid/home statistics.“
        - „GET /v1/device/stat/ct — app CT/smart-meter statistics.“
        - „GET /v1/device/stat/meter — app Smart-Meter panel totals.“
        - „GET /v1/device/stat/smartSocketStatistic — socket panel totals.“
        - „GET /v1/device/stat/socket — app socket chart statistics.“
        - „GET /v1/device/stat/sys/home/trends — home consumption breakdown.“
        - „GET /v1/device/stat/sys/battery/trends — battery charge/discharge history.“

Hier ist der gesamte Endpunkt‑Katalog aus PROTOCOL §2.4–2.6 konkret implementiert.

### 1.3 Verwendung im Coordinator

- `custom_components/jackery_solarvault/coordinator.py`
    - Nutzt den API‑Client, um:
        - schnelle Polls auf `/v1/device/property` zu machen (Docstring „fast `/v1/device/property` fetch“, Kommentar „per PROTOCOL /v1/device/property“).
        - Statistiken aus den `/v1/device/stat/*`‑Pfaden für Year/Month/Week/Daily‑Buckets und `systemStatistic`/`home_trends` zu holen.
    - Enthält Funktionen, die explizit auf PROTOCOL verweisen (z.B. Kommentar „PROTOCOL.md §2: /v1/device/stat/pv per-channel totals“ in der Nähe der Statistik‑Importlogik).


### 1.4 Mapping auf HA‑Sensoren

- `custom_components/jackery_solarvault/sensor.py`
    - Die Kopf‑Mapping‑Tabelle enthält direkt die PROTOCOL‑Zuordnung:
        - `soc` ← `/v1/device/property -> soc` und MQTT‑`UploadCombineData`/`DevicePropertyChange` `soc`.
        - `battery_charge_power` ← `/v1/device/property -> batInPw`, MQTT‑`UploadCombineData batInPw`.
        - `grid_in_power` ← `/v1/device/property -> inOngridPw`, MQTT‑`UploadCombineData gridInPw / inOngridPw`.
        - `pv_energy_*` ← `/v1/device/stat/pv (device_pv_stat_*) -> y (totalSolarEnergy)`.
        - `battery_charge_energy_*` ← `/v1/device/stat/battery (device_battery_stat_*) -> y1 (totalCharge)`.
        - `device_ongrid_input_*` ← `/v1/device/stat/onGrid (device_home_stat_*) -> y1 (totalInGridEnergy)`.
        - `home_energy_*` ← `/v1/device/stat/sys/home/trends (home_trends_*) -> y (totalHomeEgy)`.
    - Spätere Funktionen im Datei‑Tail haben Kommentare wie „PROTOCOL.md §2“ mit direkten Verweisen auf die entsprechenden HTTP‑Models aus den Smali‑Docs.

Damit sind alle HTTP‑Werte aus PROTOCOL §2 in konkrete Entitäten gemappt.

***

## 2. MQTT‑Schicht (PROTOCOL §3)

### 2.1 MQTT‑Client und TLS

- `custom_components/jackery_solarvault/client/mqtt_push.py`
    - Importiert `aiomqtt`, implementiert `JackeryMqttPushClient` (Name aus Tests).
    - Enthält Logzeile „Jackery MQTT: connecting to %s:%s with aiomqtt (TLS source=%s)“, die direkt dem PROTOCOL‑Teil zu MQTT‑Broker/Port entspricht.
    - TLS‑Konfiguration nutzt:
        - Host `emqx.jackeryapp.com`.
        - CA‑Quelle `jackery_ca.crt` und System‑Truststore (entspricht PROTOCOL‑Abschnitt 6).


### 2.2 Topics, Envelope, Command‑Routing

- Gleiches Modul (`mqtt_push.py`):
    - Kümmert sich um Verbindungsaufbau, Reconnect‑Logik und das Abonnieren von `hb/app/<userId>/device`, `/alert`, `/config`, `/notice` (wie im PROTOCOL beschrieben).
    - Übergibt eingehende Messages an den Coordinator (z.B. `_async_handle_mqtt_message`).
- `custom_components/jackery_solarvault/coordinator.py`
    - Kommentiert MQTT‑Pfad explizit:
        - „MQTT push merges UploadCombineData, DevicePropertyChange, weather and subdevice telemetry into the same state tree“.
    - Auswertung von `messageType`, `cmd`, `actionId`:
        - `107` / `121` für `DevicePropertyChange` / `CombineData`.
        - `111` für `UploadSubDeviceIncrementalProperty`.
        - `110` für `UploadSubDeviceGroupProperty`.
        - `QuerySubDeviceGroupProperty` / `ControlSubDevice` ActionIds (3025, 3026, 3032 etc.) – genau die, die im PROTOCOL dokumentiert sind.


### 2.3 Subdevices (CT, Battery‑Packs, Plugs)

- Ebenfalls `coordinator.py`:
    - CT/Smart‑Meter:
        - Kommentar zur „QuerySubDeviceGroupProperty responses transported as UploadSubDeviceGroupProperty; devType=3“ – deckt das im PROTOCOL beschriebene CT‑Telemetry ab.
    - Battery‑Packs:
        - Kommentare zu `HomeSubDeviceType.BATTERY_PACK` und den Discovery‑Pfaden für Packs (SOC, Power, OTA‑Status).
    - Plugs/Sockets:
        - `HomeSubDeviceType.SOCKET` mit Routing über `cmd=110`/`111` und die Telemetrie‑Frames, wie in MQTT‑Kapitel beschrieben.


### 2.4 Tests zum MQTT‑Protokoll

- `tests/test_mqtt_protocol_contract.py`, `tests/test_mqtt_stability.py`
    - Verifizieren, dass Topics, `messageType`/`cmd`/`actionId` und Fehlerhandling (inkl. `MqttCodeError`) den Spezifikationen aus `MQTT_PROTOCOL` und PROTOCOL folgen.

Damit ist PROTOCOL §3 direkt im MQTT‑Client, Coordinator und Tests verankert.

***

## 3. BLE‑Schicht (PROTOCOL §4)

### 3.1 Frameformat, UUIDs und Krypto

- `custom_components/jackery_solarvault/client/ble.py`
    - Docstring: „Jackery SolarVault BLE wire-format helpers. Pure-Python frame builder, parser and crypto for the Jackery app's BLE frames“.
    - Wichtigste Konstanten:
        - `BLE_FRAME_MAGIC = "DFED"`
        - `BLE_FRAME_VERSION = "0001"`
        - `BLE_FRAME_PAYLOAD_MARKER = "0001"`
        - `BLE_AES_KEY_LEN_AES128 = 16`, `BLE_AES_KEY_LEN_AES256 = 32`, `BLE_AES_KEY_LENGTHS = (16, 32)`
        - `BLE_AES_IV_LEN = 16`
        - `BLE_SERVICE_UUID = "0000bdee-0000-1000-8000-00805f9b34fb"`
        - `BLE_WRITE_CHAR_UUID = "0000ee01-0000-1000-8000-00805f9b34fb"`
        - `BLE_NOTIFY_CHAR_UUID = "0000ee02-0000-1000-8000-00805f9b34fb"`
        - `BLE_MANUFACTURER_ID = 0x4802` – Hersteller‑ID.
    - Krypto‑Hilfsfunktionen:
        - `aes_encrypt` / `aes_decrypt` mit `BLE_AES_IV_LEN`‑Checks.
        - Dekodierung/Encodierung von Frames (`BleNotifyFrame`, Frame‑Splitter für MTU‑Limit).


### 3.2 BLE‑Transport

- `custom_components/jackery_solarvault/client/ble_transport.py`
    - Implementiert GATT‑Scan, Verbindungsaufbau, Subscription auf `BLE_NOTIFY_CHAR_UUID` und Write auf `BLE_WRITE_CHAR_UUID`.
    - Wird vom Coordinator verwendet, wenn BLE als Transport aktiv ist.


### 3.3 Tests

- `tests/test_ble_frame.py`
    - Prüft, dass Frame‑Building/Parsing, Magic, Version, Payload‑Marker und AES‑Handling dem dokumentierten Protokoll entsprechen.

Damit sind alle BLE‑Aspekte aus PROTOCOL §4 im Code abgedeckt.

***

## 4. Third‑Party‑MQTT‑Bridge (PROTOCOL §5)

- `custom_components/jackery_solarvault/client/api.py`
    - Enthält Methoden für `GET_THIRD_PARTY_MQTT_CONFIG` und `SET_THIRD_PARTY_MQTT_CONFIG` auf Basis der Smali‑Analyse – Bodymodell `ThirdPartyMqttBody` mit `enable, ip, port, userName, password, token`.
- `custom_components/jackery_solarvault/coordinator.py`
    - Kapselt Third‑Party‑Config in einer Diagnostics‑Struktur, ohne Passwörter im Klartext zu exponieren.
- `custom_components/jackery_solarvault/diagnostics.py`
    - Zeigt Third‑Party‑MQTT‑Config (soweit vorhanden) mit Redaktionen an (z.B. `userName`/`password` gekürzt oder maskiert).

Diese Stellen implementieren die in PROTOCOL §5 beschriebene Third‑Party‑MQTT‑Brücke.

***

## 5. TLS, CA und Sicherheit (PROTOCOL §6)

- `custom_components/jackery_solarvault/client/mqtt_push.py`
    - Verwendet `jackery_ca.crt` als Custom‑CA in Kombination mit dem System‑Truststore.
    - Hat Kommentar/Logik zu `VERIFY_X509_STRICT` (Disabling des strikten Checks nur wegen fehlender AKID, nicht Deaktivierung aller Prüfungen).
- `custom_components/jackery_solarvault/diagnostics.py`
    - Diagnostics‑Payload enthält Felder wie `tls_custom_ca_loaded` und `tls_certificate_source`.

Diese Code‑Stellen setzen die TLS‑, CA‑ und Sicherheitsregeln aus PROTOCOL §6 um.

***

## 6. Jahreswerte, PV‑Ertrag, Ersparnis (PROTOCOL §7)

- `custom_components/jackery_solarvault/local_daily_cache.py`
    - Kommentiert den Gebrauch der `dateType=day`‑Endpoints und deren Verhältnis zum HA‑Recorder.
- `custom_components/jackery_solarvault/coordinator.py`
    - Enthält Funktionen für:
        - Laden von `device_pv_stat_*`, `device_battery_stat_*`, `device_home_stat_*`, `home_trends_*`, `device_ct_stat_*`.
        - Zusammenführung von Monatswerten zu Jahreswerten (Same‑Endpoint‑Month‑Backfill).
        - Berechnung von `calculated_total` für die Ersparnis inklusive AC‑Ausgabe, CT‑Einspeisung, Strompreis.
- `custom_components/jackery_solarvault/sensor.py`
    - Entitäten wie „App‑Gesamtersparnis“ (`totalRevenue` aus `systemStatistic`) und „Berechnete Ersparnis“ (`_savings_calculation.calculated_total`).
    - Kommentiert die Formeldetails (Verwendung von Home‑Year‑Energie, ggf. CT‑Offload).

Die gesamte Formel‑ und Guard‑Logik aus PROTOCOL §7 spiegelt sich in diesen Dateien wider.

***

## 7. Datenquellen‑Priorität und Reparaturlogik (PROTOCOL §8)

- `custom_components/jackery_solarvault/coordinator.py`
    - Implementiert die Quellenhierarchie:
        - MQTT‑Live‑Payloads werden in `_async_handle_mqtt_message` in den Internal‑State geschrieben.
        - HTTP‑Property wird regelmäßig per `DEVICE_PROPERTY_PATH` abgefragt, wenn MQTT noch nicht gesprochen hat oder als Fallback.
        - Statistiken werden per `DEVICE_PV_STAT_PATH`, `DEVICE_BATTERY_STAT_PATH`, `DEVICE_HOME_STAT_PATH`, `HOME_TRENDS_PATH` etc. importiert.
    - Same‑Endpoint‑Month‑Backfill ist in den Helfern zur „expanded_year_series“ und in der Statistik‑Merge‑Logik implementiert (inkl. Guard‑Flags).
- `custom_components/jackery_solarvault/repairs.py`
    - Erzeugt Repair‑Issues, wenn Datenwidersprüche wie „Month > Year“ oder „Year > Lifetime“ nicht durch Backfill erklärt werden können.
- Tests:
    - `tests/test_stat_metadata.py`, `tests/test_power_math.py` prüfen Stat‑Contracts, Guards und energetische Konsistenz.

***

## 8. Strikte Arbeitsanweisungen (PROTOCOL §9)

- `docs/STRICT_WORK_INSTRUCTIONS-7.md` selbst enthält die menschliche Fassung; im Code umgesetzt durch:
    - `scripts/check_*` und `tests/test_home_assistant_best_practices.py`, `tests/test_code_quality.py` – stellen sicher, dass Parser/Coordinator‑Pfad respektiert wird und keine ad‑hoc‑Fixes in Entities landen.
    - `tests/test_mqtt_protocol_contract.py` und `tests/test_mqtt_stability.py` – sichern, dass kein unsicherer TLS‑Fallback oder „stiller“ Downgrade eingeführt wird.

***

## 9. Sensor‑Quellenpfade (PROTOCOL §10)

- `custom_components/jackery_solarvault/sensor.py`
    - Oberste Tabelle mit `SENSOR_SOURCE_PATHS`‑Mapping („soc / battery_charge_power / pv_energy_* / home_energy_* ...“).
    - Spätere Gruppen mit umfassenden Kommentaren:
        - Quellen für `device_pv_stat_*` (Serie `y`/`y1..y4`).
        - Quellen für `device_home_stat_*`, `home_trends_*`.
        - Quellen für CT‑/Smart‑Meter‑Panel‑Totals und Socket‑Panels (`/device/stat/meter`, `/device/stat/socket`, `/device/stat/smartSocketStatistic`).
- `custom_components/jackery_solarvault/coordinator.py`
    - Implementiert die dazugehörige Fetch‑Logik (HTTP + MQTT‑Merge) entsprechend dem Mapping.

***

## 10. Repair‑Roadmap (PROTOCOL §11)

- `REPAIR_ROADMAP-5.md` in `docs/` – beschreibt Phasen.
- Im Code abgebildet durch:
    - `scripts/run_ha_tests.py` / `pytest-ha.ini` – HA‑Testintegration.
    - `tests/test_integration_lifecycle_contract.py`, `tests/test_setup_entry_ha.py`, `tests/test_unload_contract.py` – stellen sicher, dass die Integration sauber init/unload kann, bevor Stat‑Kontrakte verschärft werden.
    - `tests/test_mqtt_protocol_contract.py`, `tests/test_stat_metadata.py` – Stufe 2 der Roadmap.

***

## 11. Unique‑ID‑Vertrag (PROTOCOL §12)

- `custom_components/jackery_solarvault/entity.py`
    - Basis‑Entitätsklasse für alle Plattformen (Sensor, Switch, Number, Select, Text, Binary Sensor, Button).
    - Konstruiert `unique_id` anhand von:
        - Jackery‑`device_id` oder internem eindeutigen Gerätenamen.
        - stabilem Suffix (z.B. `soc`, `battery_pack_0_soc`).
    - Stellt sicher, dass Labels/Namen nicht Teil des `unique_id` sind.
- Plattformdateien `sensor.py`, `switch.py`, `number.py`, `select.py`, `text.py`, `binary_sensor.py`, `button.py`

```
- Erben von der Basis‑Entität und definieren die jeweiligen Suffixe konsistent (z.B. `_battery_pack_<index>_<suffix>` für Packs).
```

- Tests:
    - `tests/test_entity.py`, `tests/test_battery_pack_stability.py` prüfen, dass `unique_id` stabil bleibt bei Namens‑/Label‑Änderungen und dass Battery‑Packs nicht ihre ID wechseln.

***
