# Jackery SolarVault für Home Assistant

Sprachen:
[English](../README.md) · [Deutsch](./README.de.md) · [Français](./README.fr.md) · [Español](./README.es.md)

[![HACS Custom](https://img.shields.io/badge/HACS-Custom-orange.svg)](https://hacs.xyz)
[![Release](https://img.shields.io/github/v/release/Bigdaddy1990/jackery_solarvault)](https://github.com/Bigdaddy1990/jackery_solarvault/releases)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](../LICENSE)

Community-Integration für Jackery-SolarVault-Systeme, insbesondere SolarVault 3 Pro Max. Die Integration liest Livewerte, Energie-Statistiken und konfigurierbare Einstellungen aus der Jackery-Cloud und nutzt MQTT-Push für schnelle Live-Aktualisierungen und Steuerbefehle.

Diese Integration ist kein offizielles Jackery-Produkt und steht in keiner Verbindung zu Jackery Inc.

## Was die Integration bereitstellt

- Automatische System- und Geräteerkennung über den Jackery-Cloud-Account.
- Hauptgerät, Smart-Meter und Zusatzbatterien als getrennte Home-Assistant-Geräte.
- Live-Leistungssensoren für Batterie, PV gesamt, PV-Kanäle, Netzbezug/-einspeisung, EPS, Stack-Leistung und Smart-Meter-Phasen.
- Energiesensoren für Jackery-App-Perioden: Tag, Woche, Monat und Jahr.
- Konfigurierbare Entitäten für EPS, Standby, Limits, Ausgangsleistung, Smart-Meter-Folge, Unwetterwarnung, Temperatureinheit und Strompreis.
- Neustart-Button und Cloud-Services für Systemname und Unwetterwarnungen.
- Diagnosen für redigierte Rohdaten, MQTT-Status, Firmware, Systemgrenzen und Datenqualitätswarnungen.

## Voraussetzungen

- Home Assistant 2025.8.0 oder neuer.
- Python 3.14 oder neuer, bereitgestellt durch Home Assistant.
- Jackery-Cloud-Account.
- SolarVault online per WLAN oder Ethernet.
- HACS für die empfohlene Installation.

## Empfohlener Jackery-Account

Jackery erlaubt praktisch nur eine aktive Sitzung pro Account. Wenn die offizielle Jackery-App und Home Assistant denselben Account gleichzeitig nutzen, können Token und MQTT-Zugangsdaten rotieren. Das kann zu Token-Fehlern, MQTT-Authentifizierungsfehlern oder zeitweise veralteten Daten führen.

Empfohlen:

1. Zweiten Jackery-Account erstellen.
2. SolarVault in der Jackery-App mit diesem zweiten Account teilen.
3. Den zweiten Account ausschließlich für Home Assistant verwenden.

## Installation

### HACS

[![Open in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Bigdaddy1990&repository=jackery_solarvault&category=integration)

1. HACS öffnen.
2. Drei-Punkte-Menü öffnen.
3. `Benutzerdefinierte Repositories` auswählen.
4. `https://github.com/Bigdaddy1990/jackery_solarvault` als `Integration` hinzufügen.
5. Nach `Jackery SolarVault` suchen und installieren.
6. Home Assistant neu starten.
7. `Einstellungen > Geräte & Dienste > Integration hinzufügen` öffnen.
8. `Jackery SolarVault` auswählen.

### Manuell

1. ZIP von der [Releases-Seite](https://github.com/Bigdaddy1990/jackery_solarvault/releases) herunterladen.
2. `custom_components/jackery_solarvault` nach `<HA-config>/custom_components/` kopieren.
3. Home Assistant neu starten.
4. `Jackery SolarVault` unter `Einstellungen > Geräte & Dienste` hinzufügen.

## Einrichtung und Optionen

Der Einrichtungsdialog fragt nach:

- Jackery-Cloud-E-Mail-Adresse.
- Jackery-Cloud-Passwort.
- Ob berechnete Smart-Meter-Sensoren erstellt werden sollen.
- Ob berechnete Netto-Leistungssensoren erstellt werden sollen.
- Ob Detail-Sensoren zur Ersparnisberechnung erstellt werden sollen.

Geräte-ID, System-ID, MQTT-`macId` und Region werden aus Cloud- und MQTT-Daten abgeleitet. Sie werden nicht manuell eingetragen.

Die Optionen können später in den Integrationsoptionen geändert werden. Zugangsdaten können über Home Assistants Reconfigure- oder Reauth-Flow aktualisiert werden, ohne die Integration zu löschen.

## Geräte und Entitäten

### Hauptgerät

Typische Sensoren:

- Ladezustand.
- Batterie-Ladeleistung und Batterie-Entladeleistung.
- PV-Leistung gesamt und PV1 bis PV4.
- Netzbezug, Netzeinspeisung und Netto-Netzleistung.
- Netzseitige Eingangs- und Ausgangsleistung.
- EPS-Leistung.
- Stack-Lade- und Entladeleistung.
- Sonstige Lastleistung.
- Strompreis.
- App-Werte für Tag, Woche, Monat und Jahr.
- Anzahl aktiver Alarme.

Typische Steuerungen:

- EPS-Ausgang.
- Standby.
- Auto-Aus im Inselbetrieb und Auto-Aus-Zeit.
- Lade- und Entladelimits.
- Einspeiseleistungsgrenze.
- Maximale Ausgangsleistung.
- Standard-Ausgangsleistung.
- Smart-Meter folgen.
- Energieverbrauchsmodus.
- Strompreismodus und Einheitstarif.
- Temperatureinheit.
- Unwetterwarnung und Vorwarnzeit.
- Neustart.

### Zusatzbatterien

Zusatzbatterien werden als eigene Geräte angelegt, wenn Jackery Daten dafür liefert. Bis zu fünf Batterien werden unterstützt. Je nach Payload können verfügbar sein:

- Ladezustand.
- Zelltemperatur.
- Lade- und Entladeleistung.
- Firmware-Version.
- Seriennummer.
- Kommunikationsstatus als Attribute.

### Smart-Meter

Wenn ein Jackery-Smart-Meter verbunden ist, wird es als eigenes Gerät angelegt. Es kann Folgendes liefern:

- Gesamtleistung.
- Phase 1, Phase 2 und Phase 3.
- Rohattribute zur Diagnose.
- Berechnete Hausverbrauchssensoren, wenn die Option aktiviert ist.

## Services

Die Integration registriert diese Services unter `jackery_solarvault`:

| Service | Zweck |
|---|---|
| `jackery_solarvault.rename_system` | SolarVault-System in der Jackery-Cloud umbenennen |
| `jackery_solarvault.refresh_weather_plan` | Aktuellen Unwetter-Warnplan abrufen |
| `jackery_solarvault.delete_storm_alert` | Aktive Unwetterwarnung per Cloud-Befehl löschen |

Die Parameter werden in Home Assistant unter `Entwicklerwerkzeuge > Aktionen`
gewählt. `refresh_weather_plan` und `delete_storm_alert` zeigen einen
Geräte-Picker, der auf Jackery-Geräte gefiltert ist – wähle dort die
SolarVault-Haupteinheit. Automationen können alternativ direkt die rohe
numerische Jackery-`device_id` aus dem Diagnose-Export übergeben.
`rename_system` erwartet die numerische System-ID aus der Diagnose, weil ein
Jackery-System mehrere Home-Assistant-Geräte umfasst.

Bei zwei konfigurierten Jackery-Konten leitet die Integration jede Aktion
automatisch an den Cloud-Eintrag weiter, dem die übergebene System-/Geräte-ID
gehört.

## Energy Dashboard und Sensorbedeutung

Einige Jackery-Werte klingen ähnlich, bedeuten aber nicht dasselbe.

- Batterie-Entladeleistung zeigt, was die Batterie abgibt.
- Netto-Netzleistung ist Netzbezug minus Netzeinspeisung. Sie muss nicht der Batterie-Entladeleistung entsprechen, weil PV, Hauslast, Smart-Meter-Werte und interne Regelung dazwischenliegen.
- Stack-Eingang/-Ausgang beschreibt den Zusatzbatterie-Stack oder den Leistungsfluss zwischen Hauptgerät und Zusatzbatterien.
- Smart-Meter-Werte kommen vom verbundenen Meter und werden getrennt von Hauptgerätewerten behandelt.
- `Hausverbrauch aktuell` nutzt Jackerys Live-Hauslast (`otherLoadPw`), wenn vorhanden. Fehlt dieser Wert, nutzt die Integration Smart-Meter-Nettoleistung minus Jackery-Netzseite-Eingang plus Jackery-Netzseite-Ausgang.
- `Tägliche Netzeinspeisung (Jackery-Cloud)` ist das Jackery-Feld `todayLoad`. Es ist kein verlässlicher realer Hausverbrauch. Für Hausverbrauch sollten die berechneten Smart-Meter-/Hausverbrauchssensoren genutzt werden.
- `App-Gesamtersparnis` ist der rohe Jackery-App-Wert. Er kann wie PV-Ertrag wirken. `Berechnete Ersparnis` ist die lokale Schätzung aus selbst genutzter AC-Energie, netzseitigem Ein-/Ausgang, optionaler öffentlicher Einspeisung, Hausverbrauch und konfiguriertem Strompreis.

Für das Home-Assistant-Energy-Dashboard sollten echte kumulative Tages-/Gesamtwerte und die berechneten Hausverbrauchssensoren verwendet werden. Wochen-, Monats- und Jahres-Periodensensoren sind keine lebenslangen Zähler.

Details zur Ersparnisberechnung stehen in [`APP_CLOUD_VALUES.md`](APP_CLOUD_VALUES.md).

## Periodenregeln und Datenqualität

Die Integration verwendet dieselben lokalen Periodengrenzen wie die Jackery-App:

- Woche: Montag bis Sonntag.
- Monat: Kalendermonat.
- Jahr: Kalenderjahr.

Wichtiges Verhalten:

- Periodensensoren sind Periodensummen, keine lebenslangen Zähler.
- Wochenwerte werden nicht zur Reparatur von Monats-, Jahres- oder Gesamtwerten verwendet.
- Wenn Jackery einen aktuellen Monatswert als Jahres- oder Gesamtwert für Erzeugung/CO2 meldet, kann die Integration ihn mit expliziten Monatswerten desselben Endpoints und Kalenderjahres nach oben absichern.
- `App-Gesamtersparnis` bleibt der rohe Cloud-Wert. Die berechnete Ersparnis ist ein separater Wert.
- Am Monatsanfang kann der Wochenwert höher als der Monatswert sein, wenn die aktuelle Woche noch Tage aus dem Vormonat enthält. Das ist erwartet.
- Wenn Jackery widersprüchliche Daten liefert, die nicht sauber abgesichert werden können, erzeugt die Integration ein Home-Assistant-Repair-Issue und speichert Details im Diagnoseexport unter `data_quality`.

## Polling, MQTT und TLS

MQTT-Push ist der primäre Live-Pfad, sobald die Verbindung steht. HTTP-Polling bleibt Start-, Fallback- und Keep-alive-Pfad:

- Der schnelle HTTP-Grundtakt beträgt 30 Sekunden.
- Wenn MQTT live ist, werden schnelle HTTP-Ticks übersprungen und ein vollständiger HTTP-Refresh nur in einem langsameren Keep-alive-Takt ausgeführt.
- Langsame Cloud-Statistiken und Preis-/Konfigurationsdaten werden seltener abgefragt, weil die Jackery-Cloud diese Daten nicht sekündlich aktualisiert.

Die MQTT-TLS-Verbindung prüft Zertifikatskette und Hostname. Die Integration enthält `custom_components/jackery_solarvault/jackery_ca.crt` als Trust Anchor für `emqx.jackeryapp.com`, weil das Broker-Zertifikat nicht von einer öffentlichen CA signiert ist. Es gibt keinen automatischen unsicheren TLS-Fallback. Der TLS-Status ist im Diagnoseexport sichtbar.

Technische Details dazu stehen in [`STRICT_WORK_INSTRUCTIONS.md`](STRICT_WORK_INSTRUCTIONS.md).

## Diagnose und Fehleranalyse

Bei Authentifizierungs- oder MQTT-Problemen ist ein Diagnoseexport hilfreich:

`Einstellungen > Geräte & Dienste > Jackery SolarVault > Drei-Punkte-Menü > Diagnose herunterladen`

Sensible Felder werden redigiert. MQTT-Topic-Pfade werden als `hb/app/**REDACTED**/...` exportiert; die rohe Jackery-User-ID ist nicht enthalten. Der Diagnoseexport enthält außerdem Zähler für verworfene Payloads, MQTT-Zeitstempel und Datenqualitätswarnungen.

Normales Debug-Logging:

```yaml
logger:
  default: info
  logs:
    custom_components.jackery_solarvault: debug
```

Rohes HTTP-/MQTT-Payload-Debug-Logging ist getrennt und bewusst nur per Opt-in aktiv. Es schreibt `/config/jackery_solarvault_payload_debug.jsonl` nur, wenn dieser dedizierte Logger auf `debug` steht:

```yaml
logger:
  logs:
    custom_components.jackery_solarvault.payload_debug: debug
```

Die Payload-Debug-Datei wird gedrosselt und bei 2 MB nach `jackery_solarvault_payload_debug.jsonl.1` rotiert. Auf normalen Installationen existiert sie nicht.

Home-Assistant-Brand-Icons werden in `custom_components/jackery_solarvault/brand/` mitgeliefert; die Integration schreibt zur Laufzeit keine Brand-Dateien.

## Referenzdokumentation

- [`APP_CLOUD_VALUES.md`](APP_CLOUD_VALUES.md): Jackery-App-/Cloud-Werte und Ersparnisberechnung.
- [`DATA_SOURCE_PRIORITY.md`](DATA_SOURCE_PRIORITY.md): Priorität von MQTT, HTTP und App-Statistiken.
- [`MQTT_PROTOCOL.md`](MQTT_PROTOCOL.md): MQTT-Topics und Payload-Verträge.
- [`APP_POLLING_MQTT.md`](APP_POLLING_MQTT.md): HTTP- und MQTT-Polling-Details.

## Mitwirken

Bug-Reports und Feature-Requests bitte über [GitHub Issues](https://github.com/Bigdaddy1990/jackery_solarvault/issues) melden. Bei Authentifizierungs-, MQTT- oder Datenqualitätsproblemen möglichst einen Home-Assistant-Diagnoseexport beilegen. Sensible Felder werden automatisch redigiert, aber die Datei sollte vor öffentlichem Teilen trotzdem kurz geprüft werden.

## Lizenz

MIT-Lizenz. Siehe [LICENSE](../LICENSE).
