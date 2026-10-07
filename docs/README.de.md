# Jackery SolarVault for Home Assistant

Languages:
[English](../README.md) · [Deutsch](./README.de.md) · [Français](./README.fr.md) · [Español](./README.es.md)

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Bigdaddy1990&repository=jackery_solarvault&category=integration)
[![Release](https://img.shields.io/github/v/release/Bigdaddy1990/jackery_solarvault)](https://github.com/Bigdaddy1990/jackery_solarvault/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../LICENSE)

Eine benutzerdefinierte [Home Assistant](https://www.home-assistant.io/) Integration, die deine Jackery SolarVault, HomePower und Explorer Powerstations direkt in dein Smart Home bringt.

**Dies ist die ultimative (Non-Plus-Ultra) Jackery-Integration für Home Assistant.** Sie vereint 100 % der offiziellen App-Funktionalität (Cloud API) mit der Geschwindigkeit und Zuverlässigkeit von **lokalem MQTT** und **Bluetooth (BLE)**.

---

## 🏆 Warum diese Integration die beste Wahl ist

Vielleicht hast du von anderen manuellen MQTT-Workarounds oder älteren Integrationen gehört. Hier ist der Grund, warum diese Integration die klar bessere Lösung ist:

1. **Kein manuelles Auslesen von Tokens:** Die Einrichtung nutzt deine Cloud-Zugangsdaten, um Geräte zu finden und Schlüssel sowie MQTT-Sitzungsmaterial aus der Cloud abzurufen. Du musst dafür keinen Netzwerkverkehr abhören oder JSON-Payloads manuell erstellen.
2. **Optionale lokale Steuerung:** Bei unterstützten Geräten können ein eingerichteter lokaler MQTT-Broker und aktiviertes **Bluetooth (BLE)** lokale Daten und Befehle übertragen. Diese zusätzlichen Wege ersetzen die Cloud-Anmeldung nicht und deaktivieren weder HTTP noch Cloud-MQTT. Ein rein lokaler Einrichtungsmodus ist in dieser Integration nicht vorhanden.
3. **100 % App-Funktionalität:** Im Gegensatz zu rein lokalen Bastellösungen, die nur den Akkustand auslesen können, bietet diese Integration *alles*, was auch die Jackery-App kann. Inklusive Time-of-Use-Ladeplänen, Shelly-Integration, Firmware-Checks und erweiterten Ladeeinstellungen.

---

## 🔋 Unterstützte Geräte

Diese Integration unterstützt eine Vielzahl von Jackery-Geräten, darunter:

- **Heim-Energiesysteme:** Jackery SolarVault und HomePower Serie.
- **Tragbare Powerstations (Explorer Serie):** E240, E557, E900, E1000, E1500V2, E1800, E2000, E3000, E7647, E7987.
- **Zubehör:** Akku-Packs, Smart Meter, Smart Plugs und verknüpfte Shelly-Cloud-Steckdosen.

---

## ✨ Features & Entitäten

Die Integration erstellt Dutzende Entitäten pro Gerät, um dir volle Sichtbarkeit und Kontrolle zu geben:

| Plattform | Beispiele |
|-----------|-----------|
| `sensor` | SOC, Eingangs-/Ausgangs-/PV-Leistung, Netzbezug, Temperaturen, verbleibende Laufzeit, kumulierte Energie-Statistiken |
| `binary_sensor` | Lade-Status, Online-Status und Fehlermeldungen |
| `number` | Ladeleistungs-Limits, Speicher-Limits, benutzerdefinierte Akku-Grenzen, AC-Ausgangs-Verzögerung |
| `select` | Arbeitsmodus, Lademodus, Batterie-Modus, Ausgangs-Priorität, USV-Modell, Strompreis-Modus |
| `switch` | EPS-Ausgang, AC/DC-Ausgänge, Energiesparen, Super Charge |
| `button` | Neustart, Power-Pack-Blinken |
| `text` | WLAN- und Diagnose-Kennungen |

### 🛠️ Erweiterte Services (Dienste)

Wir stellen außerdem über 60 benutzerdefinierte Services in Home Assistant zur Verfügung, die dir die volle Macht der Jackery-App in deinen Automatisierungen geben:
- **Geräte-Management:** `bind_device`, `unbind_device`, `get_share_qr_code` (geräteabhängig; die Sharing-Dienste bestätigen keine SolarVault-Sharing-Unterstützung)
- **Cloud-to-Cloud:** `get_shelly_auth_url`, `list_shelly_devices`
- **Energie-Planung:** `save_tou_plan`, `insert_electricity_strategy`, `bind_currency`
- **Statistiken:** `query_charge_report`, `query_soc_stat`, `query_profit_stat`

---

## 🛠️ Installation

### HACS (Empfohlen)

1. Öffne HACS.
2. Öffne das Drei-Punkte-Menü.
3. Wähle `Benutzerdefinierte Repositories`.
4. Füge `https://github.com/Bigdaddy1990/jackery_solarvault` als `Integration` hinzu.
5. Suche nach `Jackery SolarVault` und lade es herunter.
6. Starte Home Assistant neu.
7. Gehe zu `Einstellungen > Geräte & Dienste > Integration hinzufügen`.
8. Wähle `Jackery SolarVault`.

### Option 2: Manuell

1. Lade das neueste Release herunter.
2. Kopiere den Ordner `custom_components/jackery_solarvault` in dein Home Assistant `config/custom_components/` Verzeichnis.
3. Starte Home Assistant neu.

---

## ⚙️ Konfiguration

1. Gehe zu **Einstellungen → Geräte & Dienste**.
2. Klicke auf **Integration hinzufügen** und suche nach **Jackery SolarVault**.
3. Folge dem Einrichtungsassistenten und gib deine Jackery-Cloud-Zugangsdaten ein.

> [!WARNING]
> **SolarVault-Kontonutzung:** Laut Jackery-App-Handbuch können SolarVault-Systeme nicht geteilt werden; nur der Besitzer kann sie verwalten. Verwende für diese Integration den Jackery-Account, dem dein SolarVault gehört. Ein zweiter HA-Account mit einem geteilten SolarVault ist keine unterstützte Lösung.
> **App und Home Assistant gleichzeitig:** Eine erneute Anmeldung kann die Sitzung des anderen Clients ersetzen. Die Integration versucht, Verbindungen wiederherzustellen, garantiert aber keine stabile gleichzeitige Nutzung mit der mobilen App. Auch BLE, lokales MQTT und vorhandenes Bootstrap-Material deaktivieren die Cloud-Anmeldung nicht. Siehe [Kontonutzung und lokale Alternativen (englisch)](./account-sessions.md) für Quellen, Einschränkungen und die separate offizielle MQTT-Anbindung.

### Konfigurationsoptionen

- **E-Mail & Passwort:** Die Zugangsdaten des Jackery-Cloud-Accounts, dem dein SolarVault gehört.
- **Bluetooth (BLE):** Optional. Benötigt Bluetooth-Reichweite und einen gültigen Geräteschlüssel aus der Cloud-Einrichtung oder dem Cache.
- **Lokales MQTT:** Optional. Benötigt unterstützte Geräte-Firmware und einen eingerichteten lokalen Broker. Seine Zugangsdaten sind unabhängig vom Jackery-Cloud-Account; die Cloud-Anmeldung dieser Integration bleibt aktiv.

---

## 💡 Automatisierungen & Beispiele

**Benachrichtigung bei niedrigem Akkustand:**

```yaml
automation:
  - alias: "Jackery Akku Warnung"
    triggers:
      - trigger: numeric_state
        entity_id: sensor.jackery_solarvault_state_of_charge
        below: 20
    actions:
      - action: notify.notify
        data:
          message: "Dein Jackery Akku ist unter 20% gefallen!"
```

**Zeitbasierten Ladeplan (Time-of-Use) einstellen:**

```yaml
action: jackery_solarvault.save_tou_plan
data:
  device_id: "<deine_device_id>"
  body:
    tasks:
      - start: "04:00"
        end: "06:00"
        loops: "1111111"
        pw: 700
        sysSwitch: 1
```

---

## ❓ Fehlerbehebung (FAQ)

- **Die mobile App meldet mich ab oder die Integration verliert nach einer App-Anmeldung die Verbindung:**
  Ein Sitzungskonflikt ist möglich, wenn App und Integration denselben Besitzer-Account nutzen. Ein zweiter Account löst dies bei SolarVault nicht, weil das System nicht geteilt werden kann. Zum Wechseln zwischen den Clients deaktiviere die Integration vor der App-Anmeldung und beende die App-Sitzung, bevor du die Integration wieder aktivierst. Wiederholte Anmeldungen und Neustarts schaffen keine zuverlässige gleichzeitige Nutzung. Prüfe bei anderen Verbindungsfehlern auch Netzwerk, Broker und BLE-Reichweite. [Details und offizielle MQTT-Alternative (englisch)](./account-sessions.md).
- **Wo sind die Sensoren für die gesamte Lebensdauer-Energie?**
  Die bereitgestellten Sensoren für Woche, Monat und Jahr sind *Periodensummen* und werden automatisch zurückgesetzt. Für das Home Assistant Energie-Dashboard nutze bitte die kumulativen Energie-Sensoren, die von der Integration bereitgestellt werden.

---

## 📜 Lizenz

Dieses Projekt ist unter der MIT-Lizenz lizenziert - siehe die [LICENSE](../LICENSE) Datei für Details.

Normale Diagnoseexporte enthalten vollständige Geräte- und Systemkennungen, Seriennummern, Brokeradressen, MQTT-Benutzernamen und Client-IDs sowie MQTT-Topics. Passwörter, Tokens und kryptografische Schlüssel bleiben maskiert. Für Kennungen und Topics ist die Entwickler-Debugoption nicht erforderlich.
