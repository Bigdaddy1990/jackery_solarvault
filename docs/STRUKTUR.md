
Hier ist die Zuordnung deines PROTOCOLs zu den konkreten Code‑Stellen in der Integration. Ich orientiere mich an den Abschnitten des PROTOCOLs und nenne jeweils Dateien, relevante Funktionen/Docstrings und ihre Rolle.[^1]

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

