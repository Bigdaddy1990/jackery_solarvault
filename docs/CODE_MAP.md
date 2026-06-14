<img src="https://r2cdn.perplexity.ai/pplx-full-logo-primary-dark%402x.png" style="height:64px;margin-right:32px"/>

# ja mach das bitte

Ich habe eine `CODE_MAP.md` erzeugt, die jeden PROTOCOL‑Abschnitt einer konkreten Stelle im Code und in den Tests zuordnet.

Die Tabelle enthält für:

- **§2 Cloud‑HTTP‑API**: `const.py`, `client/api.py`, relevante Coordinator‑Methoden, Mapping in `sensor.py`.
- **§3 MQTT‑Schicht**: `client/mqtt_push.py`, MQTT‑Handling im `coordinator.py`, Sensor‑Zuordnungen und die beiden MQTT‑Tests.
- **§4 BLE‑Schicht**: vollständiges Frame‑/Krypto‑Handling in `client/ble.py`, Transport in `ble_transport.py`, BLE‑Test.
- **§5 Third‑Party‑MQTT**: API‑Client, Coordinator‑Einbettung, Diagnostics.
- **§6 TLS \& CA**: TLS‑Setup in `mqtt_push.py`, Diagnostics‑Felder, Absicherung per MQTT‑Test.
- **§7 Jahreswerte \& Ersparnis**: Year‑Backfill‑/Ersparnis‑Logik im Coordinator, `local_daily_cache.py`, Sensoren plus Power/Stat‑Tests.
- **§8 Datenquellen‑Priorität**: Quellenhierarchie und Repairs im Coordinator, `repairs.py`, Stat‑/Power‑/Lifecycle‑Tests.
- **§9 Strikte Arbeitsanweisungen**: STRICT‑Doc, Test‑Runner‑Skripte und Code‑Quality‑/Protocol‑Tests.
- **§10 Sensor‑Quellenpfade**: zentrale Mapping‑Tabelle und Source‑Kommentare in `sensor.py`, korrespondierende Fetch‑Logik im Coordinator.
- **§11 Repair‑Roadmap**: REPAIR‑Doc, HA‑Testintegration und relevante Tests.
- **§12 Unique‑ID‑Vertrag**: `entity.py` als Basis, Plattformdateien für Suffixe und die Unique‑ID‑/Battery‑Stabilitäts‑Tests.

Du kannst die Datei direkt ins Repo legen; sie ist rein navigativ und ändert das PROTOCOL nicht.

