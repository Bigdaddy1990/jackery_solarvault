# Home Assistant Integration – Platinum Readiness Checklist

Diese Checkliste ist für Integrationsentwickler gedacht, die eine Home‑Assistant‑Integration auf **Platinum**‑Niveau bringen wollen. Sie basiert auf den offiziellen Home‑Assistant‑Developer‑Vorgaben, der Integration‑Quality‑Scale und gängigen Best Practices und ist für neue wie bestehende Integrationen nutzbar.

> Hinweis: Diese Checkliste ist ein Arbeitsdokument. Die verbindlichen Regeln stehen in den Home‑Assistant‑Developer‑Docs und in PROTOCOL/CLAUS für die jeweilige Integration.

---

## 1. Projektstruktur & Manifest

- [ ] Integration liegt unter `custom_components/<domain>/` (oder `homeassistant/components/<domain>/` für Core) mit:
      `__init__.py`, `manifest.json`, `config_flow.py`, Plattformdateien (`sensor.py`, `switch.py`, …), optional `coordinator.py`, `diagnostics.py`, `repairs.py`, `translations/`.
- [ ] `manifest.json` enthält:
  - [ ] `domain` (klein, stabil, eindeutig).
  - [ ] `name` (sprechender Name der Integration).
  - [ ] `version` (Pflicht für Custom‑Integrationen).
  - [ ] `documentation` (URL zur Doku).
  - [ ] `requirements` mit allen externen Python‑Abhängigkeiten.
  - [ ] `codeowners` mit GitHub‑Handles.
  - [ ] ggf. `integration_type`, `zeroconf`, `ssdp`, `dhcp`, `bluetooth` nur, wenn die Funktionalität tatsächlich implementiert ist.
- [ ] Es gibt **keine** YAML‑basierte Konfiguration für neue Integrationen; `configuration.yaml` wird nicht benötigt.

---

## 2. Config Entries & Config Flow

- [ ] Die Integration nutzt **Config Entries** als primären Konfigurationsmechanismus.
- [ ] `config_flow.py` implementiert eine `ConfigFlow`‑Klasse mit:
  - [ ] Erstkonfiguration (z.B. Host, API‑Key, Auth).
  - [ ] Sinnvollen Fehlermeldungen bei Auth‑ und Verbindungsfehlern.
  - [ ] Unterstützung für Reauth (z.B. abgelaufene Tokens).
- [ ] Für nachträgliche Optionen (z.B. Polling‑Intervall, erweiterte Features) existiert eine `OptionsFlow`.
- [ ] Der Config‑Entry‑Lebenszyklus ist vollständig:
  - [ ] `async_setup_entry(hass, entry)` – richtet alles für eine neue Instanz ein.
  - [ ] `async_unload_entry(hass, entry)` – entlädt Plattformen und Verbindungen sauber.
  - [ ] `async_migrate_entry(hass, entry)` – migriert Daten, wenn sich Strukturen ändern.
- [ ] `ConfigEntryNotReady` wird verwendet, wenn externe Dienste temporär nicht erreichbar sind, damit HA später automatisch neu versucht.
- [ ] Es werden keine Config‑Daten außerhalb der `ConfigEntry`‑Struktur persistiert (kein eigener Datei‑/YAML‑Store).

---

## 3. DataUpdateCoordinator & Datenzugriff

- [ ] Für jede relevante Datenquelle/ConfigEntry gibt es eine eigene `DataUpdateCoordinator`‑Instanz (z.B. pro Konto, pro Hub, pro Gerät).
- [ ] Coordinator‑Klassen leben in `coordinator.py` (oder einer klar dokumentierten Datei) und:
  - [ ] Erben von `DataUpdateCoordinator`.
  - [ ] Bekommen ein explizites `config_entry`‑Argument.
  - [ ] Implementieren `_async_update_data()` rein asynchron (oder mit `async_add_executor_job` für Blocking‑Code).
  - [ ] Verwenden sinnvolle Updateintervalle (kein Polling im Sekundenbereich ohne Not).
  - [ ] Mappen externe Daten in einen klaren internen Datenbaum (z.B. Dict/Dataclasses), den Entities lesen.
- [ ] Optionales `_async_setup` des Coordinators wird genutzt für einmalige, teure Setup‑Tasks (z.B. Discovery, Version‑Check), bevor das erste Update läuft.
- [ ] Entities greifen **nicht** direkt auf HTTP/Socket/Client zu, sondern nur über den Coordinator‑State.

---

## 4. Async‑I/O, Performance & Ressourcen

- [ ] Alle I/O‑Operationen erfolgen asynchron (mittels `async`/`await`) oder laufen in einem Executor:
  - [ ] HTTP‑Calls verwenden `aiohttp`/`async_get_clientsession(hass)`.
  - [ ] Blocking‑Bibliotheken laufen über `hass.async_add_executor_job`.
- [ ] Es gibt **keine** direkten `time.sleep`‑Aufrufe im Event‑Loop.
- [ ] Polling‑Intervalle sind konservativ (z.B. 10–60 Sekunden für Status; Minutenbereich für Statistiken).
- [ ] Es finden keine aggressiven Parallel‑Requests ohne Limit statt (z.B. koordinierte `asyncio.gather`, Rate‑Limit in Coordinator).
- [ ] Langlaufende Aufgaben werden in Tasks/Koroutinen ausgelagert, die sauber beendet werden können.

---

## 5. Entities, Devices & Verfügbarkeit

- [ ] Alle Entitäten erben von passenden Basisklassen (`SensorEntity`, `SwitchEntity`, `NumberEntity`, …) und folgen deren Contract.
- [ ] `unique_id` ist für jede Entität gesetzt, stabil und deterministisch:
  - [ ] Basierend auf stabilen IDs (z.B. Geräte‑ID, Kanal‑Index), nicht auf Namen oder Übersetzungen.
  - [ ] Wird nie nach Veröffentlichung geändert.
- [ ] Device‑Registry ist korrekt gepflegt:
  - [ ] Jedes physische/logische Gerät hat einen Device‑Eintrag.
  - [ ] Entities sind ihren Devices zugeordnet.
- [ ] `available` spiegelt die tatsächliche Erreichbarkeit des Geräts/Services wider:
  - [ ] Bei Netzwerk-/API‑Fehlern gehen Entities auf unavailable.
  - [ ] Bei Wiederverbindung werden sie automatisch wieder verfügbar.
- [ ] Entitätskategorien (`entity_category`) werden verwendet, wo sinnvoll:
  - [ ] `diagnostic` für rein diagnostische Sensoren.
  - [ ] `config` für Einstellungs‑Entities.
- [ ] States und Attribute sind konsistent und gut benannt (keine kryptischen Keys, klare Units).

---

## 6. Fehlerbehandlung, Logging & Wiederverbindung

- [ ] Fehler werden differenziert behandelt:
  - [ ] Auth‑Fehler, Rate‑Limits, Netzwerkfehler, Protokollfehler.
  - [ ] Verwendung von `ConfigEntryNotReady` beim Setup, `UpdateFailed` im Coordinator.
- [ ] Logging:
  - [ ] `logging.getLogger(__name__)` wird verwendet.
  - [ ] `DEBUG` für Payloads und tiefe Details, `INFO` für Lifecycle (Setup/Unload), `WARNING`/`ERROR` nur für echte Probleme.
  - [ ] Es gibt keine Sensiblen Daten (Passwörter, Tokens) im Log.
- [ ] Wiederverbindung:
  - [ ] Netzwerk/Verbindungsfehler führen zu Retry‑Logik ohne Event‑Loop zu blockieren.
  - [ ] Es gibt keine unkontrollierten, eng getakteten Retry‑Schleifen.

---

## 7. Sicherheit, TLS & Privacy

- [ ] Alle Verbindungen, die TLS unterstützen, nutzen es.
- [ ] Zertifikatsprüfung ist aktiviert; Ausnahmen sind begründet und klar dokumentiert (z.B. eigene CA‑Chain).
- [ ] Es gibt keine Optionen oder Codepfade, die Zertifikatsprüfung vollständig abschalten (`CERT_NONE`, `verify=False`, unsichere Flags).
- [ ] Secrets (Tokens, API‑Keys, Passwörter) werden:
  - [ ] Niemals geloggt.
  - [ ] Nur dort in Klarform verwendet, wo nötig.
  - [ ] In Diagnostics nur redaktiert angezeigt.
- [ ] Diagnostics‑Payload enthält keine personenbezogenen Daten oder geheimen Identifikatoren in Klarform.

---

## 8. Diagnostics & Repairs

- [ ] `diagnostics.py` existiert (für Core‑Integrationen empfohlen, für komplexe Custom‑Integrationen sinnvoll) und liefert:
  - [ ] Zusammenfassungen der Konfiguration (redaktiert).
  - [ ] Wichtige interne Zustände (Flags, Stati) und Datenqualitäts‑Information.
- [ ] `repairs.py` (oder vergleichbare Logik) ist vorhanden, wenn die Integration mit fragilen oder inkonsistenten Datenquellen arbeitet:
  - [ ] Erkennt widersprüchliche oder offensichtlich falsche Daten.
  - [ ] Meldet Issues über das HA‑Repair‑System (mit menschlich verständlicher Beschreibung).

---

## 9. Tests & CI

- [ ] Es existiert eine Test‑Suite mit `pytest` (idealerweise auf Basis von `pytest-homeassistant-custom-component` oder dem offiziellen HA‑Testframework für Core‑Integrationen).
- [ ] Tests decken ab:
  - [ ] Config‑Flow (inkl. Reauth, Fehlerszenarien).
  - [ ] `async_setup_entry` / `async_unload_entry` / `async_reload`.
  - [ ] Coordinator‑Update‑Pfad, inkl. Fehlerfälle.
  - [ ] Entity‑Erstellung und ‑State‑Updates.
  - [ ] Übersetzungs‑/Manifest‑Konsistenz (Domains, Plattformnamen, entity_category etc.).
- [ ] Es gibt Tests gegen reale oder aufgezeichnete Beispiel‑Payloads, um Parser‑ und Mapping‑Code abzusichern.
- [ ] CI (z.B. GitHub Actions) läuft mit Linting, Typprüfung und Testausführung.

---

## 10. Dokumentation & Translations

- [ ] README oder offizielle HA‑Dokuseite beschreibt:
  - [ ] Wie man die Integration installiert und einrichtet.
  - [ ] Welche Entitäten erzeugt werden und was sie bedeuten.
  - [ ] Benötigte API‑Keys/Anmeldedaten und wie man sie erhält.
  - [ ] Bekannte Limitierungen oder Besonderheiten.
- [ ] `translations/en.json` (und ggf. weitere Sprachdateien) enthalten:
  - [ ] Konfigurations‑Texte (Titel, Beschreibungen, Fehler für Config‑Flow).
  - [ ] Entitätsnamen/Attribute über lokalisierte Strings (keine hart kodierten deutschen/englischen Labels im Code).
- [ ] UI‑Texte sind widerspruchsfrei und folgen den HA‑Konventionen (kurz, klar, ohne technischen Jargon, wo nicht notwendig).

---

## 11. Quality Scale – Ziel Platinum

- [ ] Bronze‑Level erfüllt (Config Entries, dokumentiertes Manifest, grundlegende Tests, asynchrone Implementation).
- [ ] Silver/Gold‑Features umgesetzt:
  - [ ] OptionsFlow, Diagnostics, saubere Unload‑Pfad, gute Fehlermeldungen.
  - [ ] Mehrere Plattformen konsistent angebunden (z.B. Sensor, Switch, Number, Select).
- [ ] Platinum‑Features erfüllt:
  - [ ] Vollständige UI‑Konfiguration ohne YAML.
  - [ ] Ausgereifte Tests inkl. Edge‑Cases, Migrationspfade, Reauth‑Flows.
  - [ ] Vorbildliche Diagnosemöglichkeiten (Diagnostics, Repair‑Issues, Logging ohne Geheimnisse).
  - [ ] Konforme Nutzung aller aktuellen HA‑APIs (keine Deprecated‑/Legacy‑APIs, Coordinator mit `config_entry`, moderne Template‑/Entity‑Patterns).
  - [ ] UX, Dokumentation und Übersetzungen sind auf einem Niveau, das einer Core‑Integration entspricht.

---

## 12. Zukunftssicherheit

- [ ] Developer‑Blog und Architektur‑Diskussionen von Home Assistant werden regelmäßig auf Änderungen geprüft (z.B. neue Anforderungen an DataUpdateCoordinator, Änderungen in der Integration‑Quality‑Scale, Deprecations).
- [ ] Interne/instabile HA‑APIs werden möglichst gemieden; falls unvermeidbar, ist die Verwendung klar dokumentiert und defensiv implementiert.
- [ ] Neue HA‑Features (z.B. zusätzliche Entity‑Typen, Device‑Klassen, Entity‑Kategorien) werden sinnvoll adoptiert, wenn sie das Nutzererlebnis verbessern, ohne die Stabilität zu gefährden.
