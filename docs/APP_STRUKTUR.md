
Hier ist eine inhaltlich vollständige, aber neu formulierte Markdown‑Zusammenfassung der neuen Protokoll‑Artefakte (geeignet z.B. als `PROTOCOL.md` oder ähnliches).[^1][^2][^3][^4][^5][^6][^7][^8][^9]

***

# Jackery SolarVault Protokoll \& Datenmodell – Konsolidierte Spezifikation

Dieses Dokument fasst alle Protokoll‑, Datenmodell‑ und Smali‑Analyse‑Artefakte zur Jackery‑SolarVault‑Plattform zusammen. Es dient als Referenz für die Home‑Assistant‑Integration.

Quellen:

- Commands \& Katalog: `hbxn_commands-2.html`, `jackery_command_catalog_v2-6.html`.[^8][^1]
- Java/Smali‑Modelle: `hbxn_model_fields.html`, `jackery_http_model_fields_v2-7.html`.[^2][^3]
- HTTP‑API: `jackery_http_api_endpoints_v2-8.html`.[^4]
- Smali/HA‑Analyse: `jackery_smali_home_assistant_report.html`, `jackery_smali_home_assistant_report_v2-9.html`.[^5][^9]
- Entity‑Kandidaten \& HA‑Extraktion: `jackery_entity_field_candidates_v2-5.html`, `jackery_ha_extraction_v2-4.html`.[^6][^7]

***

## 1. Cloud‑HTTP‑API – Struktur und Verhalten

### 1.1 Basis‑Setup

- Basis‑URL: `https://iot.jackeryapp.com/`, API‑Prefix `v1/`.[^5]
- Requests sind überwiegend `application/x-www-form-urlencoded` oder `application/json`, mit klaren Parametern pro Endpunkt.[^4]
- Authentifizierung erfolgt per Login‑Endpunkt, der eine Token‑Antwort (u.a. `token`, `userId`, `regionCode`) liefert. Dieses Token wird in weiteren Aufrufen als Header‑Feld gesetzt.[^3][^4]


### 1.2 Auth‑ und Konto‑Endpunkte

Im Auth‑ und Account‑Bereich gibt es typische Funktionen:[^3][^4]

- `auth/login`: Form‑Request mit AES‑verschlüsseltem JSON (Login‑Daten) und separat RSA‑verschlüsseltem AES‑Schlüssel.
- `auth/regist`, `auth/resetPassword`: Registrierung und Passwort‑Reset mit E‑Mail/Telefon und Bestätigungscode.
- `auth/jwt`, `auth/refreshToken`: Token‑Generierung und ‑Verlängerung.
- `user/info`, `user/update`: Nutzerprofil lesen/ändern (Name, Region, Sprach‑/Währungspräferenz).
- Support/FAQ: Endpunkte für häufige Fragen und Feedback.


### 1.3 Geräteverwaltung

Die Geräteverwaltung umfasst:[^3][^4]

- System‑ und Gerätelisten:
    - `home/UserSystemListApi`: Liste der Home‑Systeme eines Nutzers inklusive `systemId`, `systemSn`, `timezone`, `currency`, `gridStandard` und eingebundenen Geräten.[^3]
    - `UserSystemListApi$Device`: Modellinformationen (`devModel`, `productModel`, `deviceSn`, `onlineState`, `subType`, `typeName`).[^3]
- Gerätedetails:
    - `DeviceDetailApi`: Gerätedetail inklusive `deviceCode`, `deviceName`, `deviceSn`, Modell‑IDs sowie Verknüpfung zu Systemen und Herstellercodes.[^3]
- Binden/Entbinden:
    - Endpunkte zum Hinzufügen und Entfernen von Geräten, Prüfen, ob eine Seriennummer schon registriert ist, sowie zum Setzen von Anzeigenamen und Icons.[^4]


### 1.4 Status‑ und Statistik‑Endpunkte

Für Home‑Systeme stehen umfangreiche Trend‑ und Statistik‑APIs bereit:[^4][^3]

- Home‑Statistiken (`home/statistic/HomeStatApi`):
    - Felder wie `totalInGridEnergy`, `totalOutGridEnergy`, `unit` und Zeitreihen `x`, `y`, `y1`, `y2` für verschiedene Perioden (Tag/Woche/Monat/Jahr).[^3]
- PV‑Statistik (`PvStatApi`):
    - `totalSolarEnergy`, `totalSolarRevenue`, `currency`, `unit` plus `x`, `y…y4` für verschiedene PV‑Komponenten.[^3]
- Batterie‑Statistik (`BatteryStatApi`):
    - Summen `totalCharge`, `totalDischarge` und Zeitreihen `x`, `y, y1, y2, y3` für verschiedene Lade‑/Entladeanteile.[^3]
- CT‑Statistik (`CtStatApi`) und EPS‑Statistik (`EpsStatApi`):
    - Energien für In/Out‑Grid (CT) bzw. In/Out‑EPS (Notstrom) plus Einheiten und Zeitstempel‑Achse.[^3]
- Systemweite Aggregationen:
    - `SysPvStatApi`, `SysHomeStatApi`, `SysBatteryStatApi` fassen PV‑, Home‑ und Batteriequellen zu zusammenfassenden Kennzahlen und Zeitreihen zusammen.[^3]
- Tages‑Energie:
    - `TodayEnergyApi$Bean`: `de`, `dg`, `dh`, `ds` als komprimierter Tages‑Energie‑Status (z.B. PV‑Erzeugung, Grid‑Bezug, Home‑Verbrauch, Batterie).[^3]

Diese Endpunkte bilden die Quelle für HA‑Energie‑ und Trend‑Sensoren.

***

## 2. Java/Smali‑Modelle – Home, Zubehör, Battery

### 2.1 SystemBody – Home‑System‑Modell

`home/SystemBody` repräsentiert den „Live‑Status“ des Gesamtsystems:[^2]

- Batterie:
    - `batInPw`, `batOutPw`: Lade‑/Entladeleistung.
    - `batNum`: Anzahl der Packs.
    - `soc`: Gesamt‑State‑of‑Charge.
    - `batState`: zusammengefasster Zustandscode.
- Grid/On‑Grid:
    - `gridInPw`, `gridOutPw`: Netzbezug/Netzeinspeisung.
    - `inGridSidePw`, `outGridSidePw`: weitere Grid‑Leistungswerte.
    - `maxFeedGrid`: maximale Einspeiseleistung.
    - `ongridStat`, `gridSate`: Zustandsflags.[^2]
- Lasten \& Standby:
    - `otherLoadPw`: sonstige Last.
    - `standbyPw`: Standby‑Leistungsaufnahme.
    - `isAutoStandby`, `isFollowMeterPw`: Auto‑Standby und „Zähler folgen“.[^2]
- EPS \& Off‑Grid:
    - `swEpsInPw`, `swEpsOutPw`: Notstrom‑/EPS‑Leistungen.
    - `offGridTime`, `offGridDown`: Off‑Grid‑Zeit/Abschaltung.[^2]
- Konfiguration:
    - `defaultPw`, `energyPlanPw`, `maxSysInPw`, `maxSysOutPw`.
    - `tempUnit`: Temperatureinheit (z.B. °C/°F).
    - `workModel`: Arbeitsmodus (Auto, Self‑Use, Feed‑in, etc.).
    - `wpc`, `wps`: weitere Power‑Regler.[^2]

Diese Felder sind direkte Kandidaten für HA‑Sensoren: SOC, Grid‑Power, Home‑Load, EPS‑Power, Arbeitmodus etc.

### 2.2 PV, CT, BatteryPack, Collector

- `home/PV`:
    - `commState`: Kommunikationszustand.
    - `name`: Anzeigename.
    - `pvPw`: PV‑Leistung.[^2]
- `home/CtSub`:
    - `aPhasePw, bPhasePw, cPhasePw`, `anPhasePw, bnPhasePw, cnPhasePw`, `tPhasePw, tnPhasePw`: Phasen‑Leistungen und Summen.
    - `schePhase`: Phasen‑Schema.
    - `funForm`, `wip`: weitere CT‑Metadaten.[^2]
- `home/BatteryPackSub`:
    - `batSoc`: Pack‑SOC.
    - `cellTemp`: Zelltemperatur.
    - `inPw`, `outPw`: Pack‑Leistungen.
    - `isFirmwareUpgrade`, `version`: OTA‑Status.[^2]
- `home/CollectorSub`:
    - `inPw`, `outPw`: Einspeise‑/Abgabeleistung an einem Collector.[^2]


### 2.3 Zubehör‑Modelle (Accessory)

- `accessory/AccCTBody`:
    - Ströme: `curr, curr1..curr3`.
    - Spannungen: `volt, volt1..volt3`.
    - Wirkleistung: `power, power1..power3`.
    - Blindleistung: `rep, rep1..rep3`.
    - Leistungsfaktor: `fact, fact1..fact3`.
    - Frequenz: `freq`.
    - Scheinleistung: `ap, ap1..ap3`.[^2]
- `accessory/AccSocketBody`:
    - `switch`: Schaltstatus.
    - `op`: Operation/Resultcodes.
    - `sc`: möglicherweise Szenen‑ oder Statuscode.
    - `ts`: Zeitstempel der letzten Aktion.[^2]

Diese Modelle bilden die Quelle für CT‑Meter‑Sensoren und Smart‑Socket‑Entities.

***

## 3. Entitäts‑Kandidaten für Home Assistant

Die Datei mit Entitäts‑Kandidaten enthält eine aggregierte Liste von (Klasse, Feldliste), die sich für HA‑Entitäten anbieten.[^6]

Beispiele:

- `home/SystemBody`: siehe oben – zentrale Home‑KPIs für Grid, Battery, Work‑Mode.
- `home/statistic/*Api$Bean`: Felder wie `totalSolarEnergy`, `totalHomeEgy`, `totalChgEgy`, `totalDisChgEgy`, `totalInGridEnergy`, `totalOutGridEnergy`, `unit`, `x`, `y…` für Trend‑ und Summen‑Sensoren.[^6]
- `DeviceStatSystemStatistic$Bean`:
    - `todayBatteryChg`, `todayBatteryDisChg`, `todayGeneration`, `todayLoad`, `totalCarbon`, `totalGeneration`, `totalRevenue` – perfekte Kandidaten für Tages‑ und Lifetime‑Energy‑Sensoren.[^6]
- `home/UserSystemListApi$Bean` und `Device`:
    - `bluetoothKey`, `bindKey`, `deviceId`, `systemName`, `timezone`, `gridStandard`, `productModel` – sinnvoll für Device‑Registry und Diagnostics.[^6]

Damit ist klar, welche Rohfelder du zu HA‑Sensoren und Geräteattributen erheben kannst.

***

## 4. Command‑Katalog (BLE / MQTT / HTTP)

### 4.1 Command‑Mapping

Die Command‑Tabellen dokumentieren pro Befehl:[^1][^8]

- `device_family` (home/portable),
- logischen Befehl (z.B. `READ_DEVICE_INFO`, `SET_THIRD_PARTY_MQTT_CONFIG`, `SET_MAX_FEED_GRID`),
- `msg_id` / `actionId` laut Smali‑Befehls‑Envelopes,
- `mqtt_message_type` (z.B. Device‑Control, Device‑Query, Alarm, Config, Notice),
- zugehörige Request‑Bodies (z.B. `SystemBody`, `ThirdPartyMqttBody`, `AccCTBody`).

Für jede Funktion (PV‑Leistung lesen, Off‑Grid‑Zeit setzen, Smart‑Plug einschalten, Third‑Party‑Broker konfigurieren) gibt es einen Command‑Eintrag mit passendem Body‑Modell.

### 4.2 Third‑Party‑MQTT‑Konfiguration

Ein besonders wichtiger Block sind die Third‑Party‑MQTT‑Kommandos:[^8][^5]

- Body‑Modell `ThirdPartyMqttBody` enthält:
    - `enable`: Bridge ein/aus,
    - `ip`, `port`: Broker‑Adresse,
    - `userName`, `password` und `token`: Authentifizierungsfelder,
    - interne Felder, die von einer Smali‑Klasse `Lbb/c` transformiert werden.[^5]
- Es gibt sowohl GET‑ als auch SET‑Kommandos, die über MQTT oder HTTP transportiert werden können, mit klar definierten Topics und Feldern.

Der Code sollte diese Konfiguration primär aus dem Gerät lesen und nicht eigenmächtig neu generieren, solange die genaue Verschlüsselung/Transformation der Credentials nicht vollständig verstanden ist.

***

## 5. Smali‑Analyse und Protokolldesign

Die Smali‑Reports verbinden die Java‑/Smali‑Struktur mit den Protokollen und bieten konzeptionelle Leitlinien.[^9][^5]

### 5.1 Login‑Flow und Krypto

- Login‑Requests enthalten ein AES‑verschlüsseltes JSON mit den Zugangsdaten und einen RSA‑verschlüsselten AES‑Schlüssel (`aesEncryptData`, `rsaForAesKey`).[^5]
- AES‑Modus:
    - Login verwendet zwingend AES‑128‑ECB („Protokollzwang“ per Smali).
    - Weitere Payloads (z.B. MQTT‑Passwort) nutzen AES‑CBC mit PKCS7‑Padding.[^5]
- UDID/`mqtt_mac_id`:
    - Wird aus einem Seed via MD5‑Digest erzeugt und als UUID‑v3 per RFC 4122 interpretiert, dann ohne Bindestriche in den MQTT‑User integriert.[^5]
    - Das erklärt den MD5‑Einsatz als Protokollvorgabe, nicht als integritätskritischen Designfehler.


### 5.2 MQTT‑Envelope

Smali‑Code zeigt ein generisches MQTT‑Format:[^8][^5]

- Umschlagfelder:
    - `deviceSn`, `id`, `timestamp`, `version`, `messageType`, `actionId` (bzw. `msg_id`).
- Body:
    - konkrete Kommandoklasse (z.B. `HomeBody`, `AccSocketBody`, `ThirdPartyMqttBody`).
- Topics:
    - App‑seitige Topics haben das Schema `hb/app/<userId>/(device|alert|config|notice)` und dienen als Basis für die HA‑MQTT‑Listener.[^5]

***

## 6. HA‑Extraktion und Integrationsleitlinien

Die `jackery_ha_extraction_v2`‑Artefakte enthalten bereits eine HA‑fokussierte Extraktion:[^7]

- HTTP‑Endpunkte werden nach „für HA sinnvoll“ markiert (z.B. nur Statistiken und System‑Info statt aller Support‑/Promo‑APIs).
- Modell‑Felder werden mit potenziellen HA‑Entity‑IDs verknüpft (z.B. `jackery_solarvault:<systemId>_pv_energy_day`, `..._battery_charge_total`).
- Zu jedem Bereich (HTTP, MQTT, BLE) wird skizziert, welche Datenquelle Primärquelle ist und welche nur als Fallback oder Diagnosekanal dient.

Die Smali‑Reports schlagen eine Strategie vor:

1. Cloud‑HTTP als stabile Grundlage für Discovery und Statistiken verwenden.
2. MQTT‑Push für schnellere Echtzeit‑Updates (Home‑Status, Alarme, Accessory‑States) ergänzen.
3. BLE zunächst lesend nutzen (where möglich), Schreibzugriffe erst nach vollständiger Reverse‑Engineering‑Absicherung der Codec‑Klassen.

***

## 7. Fazit

Mit diesen Dokumenten liegt im Wesentlichen eine vollständige Protokoll‑ und Datenmodell‑Spezifikation für Jackery SolarVault vor:

- Cloud‑API (Endpunkte, Request‑/Response‑Modelle, Statistiken),
- Java/Smali‑Modelle (Home, Battery, CT, Accessories, Statistic‑DTOs),
- Command‑Katalog (BLE/MQTT/HTTP, inklusive Third‑Party‑MQTT),
- Smali‑basierte Validierung kritischer Designentscheidungen (Krypto, Login, MQTT‑Envelope),
- HA‑spezifische Extraktion von sinnvollen Entities und Attributen.

Die Home‑Assistant‑Integration kann damit sehr gezielt:

- Entities und Attribute aus den Model‑Feldern ableiten,
- Statistiken konsistent importieren,
- Kommandos protokollgerecht abbilden,
- und künftige Erweiterungen (Third‑Party‑MQTT, AI‑Preisfunktionen, mehr Zubehör) auf einer soliden, dokumentierten Basis aufsetzen.

<div align="center">⁂</div>

[^1]: hbxn_commands-2.html

[^2]: hbxn_model_fields.html

[^3]: jackery_http_model_fields_v2-7.html

[^4]: jackery_http_api_endpoints_v2-8.html

[^5]: jackery_smali_home_assistant_report_v2-9.html

[^6]: jackery_entity_field_candidates_v2-5.html

[^7]: jackery_ha_extraction_v2-4.html

[^8]: jackery_command_catalog_v2-6.html

[^9]: jackery_smali_home_assistant_report-3.html

