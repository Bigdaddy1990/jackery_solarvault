# Jackery SolarVault — Home Assistant Integration

[![hacs_badge](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![version](https://img.shields.io/badge/version-1.3.0-blue.svg)]()
<iframe src="https://github.com/sponsors/Bigdaddy1990/button" title="Sponsor Bigdaddy1990" height="32" width="114" style="border: 0; border-radius: 6px;"></iframe>


HACS-Integration für den **Jackery SolarVault 3 Pro Max** (und verwandte Modelle der SolarVault-3-Serie). Kommuniziert mit der Jackery-Cloud via `iot.jackeryapp.com` und liefert ~30 Sensoren für Echtzeit- und Energie-Daten.

> ⚠️ **Status**: v1.0.0, erste funktionierende Version. Alle Endpoints wurden durch HTTPS-Traffic-Analyse der offiziellen Jackery Android-App (v2.0.1) verifiziert.

---

## Verifizierte API-Endpoints

Alle Pfade unter `https://iot.jackeryapp.com`:

| Endpoint | Parameter | Zweck |
|---|---|---|
| `POST /v1/auth/login` | form-urlencoded, AES+RSA | Login (JWT-Token) |
| `GET /v1/device/system/list` | — | System- & Geräte-Discovery |
| `GET /v1/device/property` | `deviceId` | Echtzeit-Properties |
| `GET /v1/device/stat/deviceStatistic` | `deviceId` | Lifetime-Energien |
| `GET /v1/api/alarm` | `systemId` | Fehler-/Alarm-Status |
| `GET /v1/device/stat/systemStatistic` | `systemId` | Tages-/Gesamt-KPIs |
| `GET /v1/device/stat/sys/pv/trends` | `systemId&beginDate&endDate&dateType` | PV-Historie |
| `GET /v1/device/stat/sys/home/trends` | `systemId&beginDate&endDate&dateType` | Home-Historie |
| `GET /v1/device/stat/sys/battery/trends` | `systemId&beginDate&endDate&dateType` | Batterie-Historie |
| `GET /v1/device/dynamic/powerPriceConfig` | `systemId` | Strompreis-Config |
| `GET /v1/device/ota/list` | `deviceSnList` | Firmware-Version |
| `GET /v1/device/location` | `deviceId` | GPS-Koordinaten |
| **`PUT /v1/device/system/name`** | JSON Body | **System umbenennen** (Write!) |

## Sensoren (≈ 55)

**Echtzeit-Leistung**: SOC, Zellentemperatur, Batterie-Lade-/Entladeleistung, **Batterie-Netto** (signiert), PV gesamt, PV-Kanäle 1–4 einzeln, Netzbezug, Netzeinspeisung, **Netz-Netto** (signiert), EPS-Ein/Aus, Stack-Ein/Aus

**Lifetime-Energie** 🆕 (aus `deviceStatistic`, perfekt für Energy-Dashboard): PV gesamt, Batterie-Ladung gesamt, Batterie-Entladung gesamt, Netzbezug gesamt, Netzeinspeisung gesamt, Netz→Batterie gesamt, PV→Batterie gesamt, Batterie→Netz gesamt

**Tages-/Gesamt-Energie**: Heute Verbrauch, Heute Batterie-Laden, Heute Batterie-Entladen, Heute PV-Ertrag, PV heute (Trends), Gesamt PV-Ertrag, Gesamt Ersparnis (€), Gesamt CO₂ eingespart (kg), Strompreis (€/kWh)

**Alarme & Zeitstempel**: Aktive Alarme (Count + Details), Zuletzt online, Zuletzt offline, Letzte Aktualisierung, Aktivierungsdatum

**System-Meta & Diagnose**: Netz-Standard, Ländercode, Zeitzone, WLAN-Signal, WLAN-Name, IP-Adresse, Ladelimit, Entladelimit, Max. Ausgangsleistung/Netzleistung/Wechselrichter, Batterieanzahl/-zustand, Auto-Standby, **Firmware-Version** 🆕, **GPS Lat/Lng** 🆕 (default disabled), Rohdaten-Dump

**Binärsensoren**: Online, EPS aktiviert, EPS aktiv, Ethernet verbunden

**Schreib-Entities** 🆕:
- **Text-Entity** "Systemname" — Gerätename direkt in HA bearbeiten
- **Service** `jackery_solarvault.rename_system` — für Automationen und Skripte

**⚠️ Experimentelle Schreib-Entities** (v1.3.0):
- **Number-Entity** "Max. Leistung (experimentell)" — Default disabled. Versucht, die Ausgangsleistung per `POST /v1/device/deviceMaxPowerRecord/saveRecord` zu setzen. Bisher wurde nur ein abgelehnter Call gesehen (Server-Code `10600`). Der Endpoint-Name ("saveRecord") deutet eher auf ein History-Log als auf den Live-Setter hin. Bei Fehlern wird die komplette Server-Antwort im HA-Log ausgegeben.
- **Service** `jackery_solarvault.set_max_power` — gleiches Feature, für Automationen.

## Installation

### Option A — HACS (empfohlen)

1. HACS → drei Punkte oben rechts → **Custom repositories**
2. URL des GitHub-Repos eintragen, Kategorie: **Integration**
3. Nach **"Jackery SolarVault"** suchen → Download
4. Home Assistant neu starten
5. Settings → Devices & Services → **Add Integration** → "Jackery SolarVault"

### Option B — Manuell

1. ZIP entpacken
2. Ordner `custom_components/jackery_solarvault/` nach `<HA-config>/custom_components/` kopieren
3. Home Assistant neu starten
4. Settings → Devices & Services → **Add Integration** → "Jackery SolarVault"

## Konfiguration

Beim Hinzufügen:

- **E-Mail** + **Passwort** des Jackery-Cloud-Accounts
- **Geräte-ID (optional)** — nur befüllen, falls Auto-Discovery via `/v1/device/system/list` scheitert. Werte aus HA-Diagnose im `raw_api.system_list_response.data[].devices[].deviceId`-Feld
- **System-ID (optional)** — analog, aus `raw_api.system_list_response.data[].id`

Bei korrekt eingerichtetem Account findet die Integration **SolarVault 3 Pro Max** automatisch.

## Single-Session-Limit

Jackery erlaubt nur eine aktive Sitzung pro Account. Empfohlener Workaround:
1. Zweites Jackery-Konto anlegen
2. Dein SolarVault per **QR-Code-Sharing** (Jackery-App) an das Zweitkonto freigeben
3. Das Zweitkonto in Home Assistant verwenden

Damit bleibt dein Haupt-App-Login unangetastet.

## Polling-Intervall

Standard: 30 Sekunden. In den Integration-Optionen zwischen 15–3600 s einstellbar. Achtung: Die Jackery-Cloud aktualisiert Daten serverseitig nur alle ~60 s, wenn die App nicht im Vordergrund ist — schnelleres Polling liefert oft keine neueren Werte.

## Energy-Dashboard

Diese Sensoren eignen sich direkt für das HA-Energy-Dashboard:
- **Netzbezug** → Netz-Import-Quelle
- **Netzeinspeisung** → Netz-Export-Quelle (Heimvergütung)
- **Heute PV-Ertrag** → Solarproduktion
- **Heute Batterieladung** / **Heute Batterieentladung** → Home-Battery-Storage

## Beispiel-Automation

```yaml
automation:
  - alias: "SolarVault: niedriger Ladestand"
    trigger:
      platform: numeric_state
      entity_id: sensor.solarvault_3_pro_max_battery
      below: 15
    action:
      service: notify.mobile_app
      data:
        title: "SolarVault niedrig"
        message: "Akku nur noch {{ states('sensor.solarvault_3_pro_max_battery') }} %"

  - alias: "SolarVault: Einspeisung loggen"
    trigger:
      platform: numeric_state
      entity_id: sensor.solarvault_3_pro_max_grid_export
      above: 100
      for: "00:02:00"
    action:
      service: logbook.log
      data:
        name: "SolarVault"
        message: "Einspeisung > 100 W für 2 min"
```

## Debug-Logging

```yaml
# configuration.yaml
logger:
  default: info
  logs:
    custom_components.jackery_solarvault: debug
```

## Voraussetzungen

- Home Assistant 2024.4.0 oder neuer
- Jackery-Cloud-Account
- SolarVault online (WLAN oder Ethernet)
- App-Familie: **`com.hbxn.jackery`** (Standard-Jackery-App, blau)

## Abhängigkeiten

Keine externen pip-Pakete — nutzt das HA-native `cryptography`-Modul.

## Quellen & Lizenz

- Reverse-Engineering der Auth-Schicht: <https://qiita.com/Hsky16/items/c163137265a87186ac39> (@Hsky16)
- Explorer-Integration als architektonische Referenz: <https://github.com/theak/jackery-homeassistant>
- SolarVault-Endpoint-Identifikation: Eigene HTTPS-Traffic-Analyse der Jackery-Android-App v2.0.1 (April 2026)

[![Ko-fi](https://ko-fi.com/img/githubbutton_sm.svg)](https://ko-fi.com/bigdaddy1990)

MIT-Lizenz — siehe `LICENSE`. Diese Integration ist eine Community-Entwicklung und steht in keinem offiziellen Verhältnis zu Jackery Inc.
