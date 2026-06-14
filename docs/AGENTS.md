
```markdown
# AGENTS.md – Jackery SolarVault Integration
*Guidelines for Contributors and Agents*

> **Note (2026-06-06):** The Quality-Gate section (§5 below) — ruff / mypy /
> hassfest / pre-commit — is **intentionally deferred** from the current
> re-init + Streams A/B/C review pass and will be tackled in a follow-up
> plan. Do not interpret the absence of a clean `ruff check` / `mypy --strict`
> result in the reinit commit as a regression. The deferred items are
> tracked in `docs/BUG_LIST.md` §9.

---

# jackery_solarvault Contributor Guide

These instructions describe how to work on the `custom_components/jackery_solarvault`
Home Assistant integration. They consolidate the authoring rules from the
Gemini and Claude style guides with the upstream Home Assistant requirements so
contributors consistently deliver Platinum-quality changes.

## Environment setup

Python 3.14+ PEP758

1. Create a virtual environment and install the test tooling:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements_test.txt --upgrade
   pip install -r requirements.txt --upgrade
   ```
2. Install the integration in editable mode if you need to exercise packaging
   hooks: `pip install -e .`. The `pyproject.toml` file configures the
   setuptools build backend and enables branch coverage reporting for
   `custom_components/jackery_solarvault`.
3. Export `PYTHONPATH=$(pwd)` or invoke commands via `python -m …` so the local
   `scripts/` and `pytest_*` packages resolve correctly.

## Core workflow

Run every command before begin a new task:

```bash
ruff . check                           # Run Ruff lint (includes UP, E, F, ...)
ruff . format                          # Apply repository formatting rules
# repeat till all ruff check errors are fixed
python -m scripts.enforce_test_requirements  # Confirm tests declare third-party deps
python -m scripts.*...  #running local hass-tests, enforces, fixtures and sync-files
mypy custom_components/jackery_solarvault    # Ensure static typing stays strict
python -m scripts.hassfest \
  --integration-path custom_components/jackery_solarvault  # Validate manifest & strings
python -m scripts.sync_contributor_guides           # Refresh assistant copies AGENTS/copilot-...
pytest-homeassistant-custom-component -q            # Execute the homeassistant async pytest suite
python -m scripts.enforce_coverage_gates --coverage-xml coverage.xml  # Enforce total + critical module coverage

```

### Test triage protocol (required)

When the test pipeline becomes unstable or too slow, apply this escalation path
before broad refactors:

1. **If one test blocks for more than 30 minutes**
   - Reduce scope to focused unit coverage.
   - Mock API interactions more aggressively.
   - Validate the affected branch behavior directly instead of running only
     end-to-end flows.
2. **If tests are flaky**
   - Freeze time controls in the test harness.
   - Make asynchronous tasks deterministic.
3. **If coverage stalls**
   - Re-read the branch coverage report.
   - Fix only the top three coverage gaps first; avoid side quests until those
     gaps are closed.

### Coverage package execution protocol (required)

For each coverage package, contributors must execute the work in this exact
sequence:

1. Select **5-10 target branches** from documentation-driven branch lists
   before writing or editing tests.
2. Modify **exactly one module** per package to keep review scope narrow and
   traceable.
3. Write behavior-oriented tests that verify business outcomes; avoid assertions
   on private helper call order.
4. Mock only integration boundaries (external APIs, IO, Home Assistant service
   boundaries), never internal business logic.
5. Ship a regression test immediately whenever an edge case bug is fixed.
6. Close the package as soon as the minimum target is reached; do not pursue
   perfection work in the same ticket.

### Repository actions orchestration (required)

All repository quality workflows follow the same sequencing rule: run checks first, and only when a run is both push-triggered and bot-authored may CI apply fixes, commit, and re-run the full gate. Pull requests remain strict failing checks with no write-back.

This policy applies to `.github/workflows/ci.yml`,
`.github/workflows/python-modernization.yml`,
`.github/workflows/reusable-python-tests.yml`, and the manual
`.github/workflows/ruff-baseline.yml` fixer flow.

Avoid duplicate workflow responsibilities. Coverage uploads are handled by CI
and reusable test workflows, and release packaging/changelog publication are
handled by `release.yml` as the single tag-release flow.

When possible, prefer the latest Home Assistant package in CI by leaving
`home-assistant-spec` empty unless a temporary pinned override is required via
repository variables.

### Python modernization CI path (required)

When touching typing upgrades, syntax migrations, or hook configuration, keep
`.github/workflows/python-modernization.yml` green. This is the single required
modernization gate and runs these commands in order:

```bash
pre-commit run --all-files
pre-commit run --hook-stage manual python-typing-update --all-files
python -m mypy custom_components/jackery_solarvault
```

The workflow is intentionally sequential: if the initial checks fail, it may
apply modernization fixes, commit, and re-run the same checks. Auto-commit is
restricted to push events whose branch head commit is bot-authored; pull
requests remain strict failing checks without write-back.

## Bot policy (strict)

All automated assistants, bots, and code generation tools **must** follow the
official Home Assistant Developer documentation for architecture, integration
structure, manifests, config/option flows, YAML configuration, testing,
internationalization, and review checklists. This is non-negotiable and applies
to every change, suggestion, or review. # check /docs/ha_custom_config_and_best_practices

Authoritative sources (non-exhaustive, must be consulted when relevant):

- https://developers.home-assistant.io/blog
- https://developers.home-assistant.io/
- https://developers.home-assistant.io/docs/architecture_components
- https://developers.home-assistant.io/docs/development_guidelines
- https://developers.home-assistant.io/docs/development_tips
- https://developers.home-assistant.io/docs/development_validation
- https://developers.home-assistant.io/docs/development_typing
- https://developers.home-assistant.io/docs/internationalization/custom_integration
- https://developers.home-assistant.io/docs/development_testing
- https://developers.home-assistant.io/docs/creating_integration_file_structure
- https://developers.home-assistant.io/docs/creating_integration_manifest
- https://developers.home-assistant.io/docs/config_entries_config_flow_handler
- https://developers.home-assistant.io/docs/config_entries_options_flow_handler
- https://developers.home-assistant.io/docs/configuration_yaml_index
- https://developers.home-assistant.io/docs/development_checklist
- https://developers.home-assistant.io/docs/creating_component_code_review
- https://developers.home-assistant.io/docs/creating_platform_code_review
- https://developers.home-assistant.io/docs/core/integration-quality-scale/rules
- https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/action-setup/
- https://developers.home-assistant.io/docs/core/integration-quality-scale/rules/common-modules
- https://developers.home-assistant.io/docs/core/integration-quality-scale/checklist
- https://www.home-assistant.io/docs/tools/check_config/
- https://developers.home-assistant.io/docs/documenting/yaml-style-guide
- https://developers.home-assistant.io/docs/documenting/create-page
- https://developers.home-assistant.io/docs/documenting/integration-docs-examples
- https://developers.home-assistant.io/docs/instance_url
- https://developers.home-assistant.io/docs/core/platform/significant_change
- https://developers.home-assistant.io/docs/core/platform/reproduce_state
- https://developers.home-assistant.io/docs/core/platform/repairs
- https://developers.home-assistant.io/docs/api/native-app-integration/setup
- https://developers.home-assistant.io/docs/api/native-app-integration/sending-data
- https://developers.home-assistant.io/docs/api/native-app-integration/sensors
- https://developers.home-assistant.io/docs/api/native-app-integration/notifications
- https://developers.home-assistant.io/docs/intent_conversation_api
- https://developers.home-assistant.io/docs/core/llm/
- https://developers.home-assistant.io/docs/intent_builtin
- https://developers.home-assistant.io/docs/device_automation_index
- https://developers.home-assistant.io/docs/device_automation_trigger
- https://developers.home-assistant.io/docs/device_automation_condition
- https://developers.home-assistant.io/docs/device_automation_action
- https://developers.home-assistant.io/docs/automations
- https://developers.home-assistant.io/docs/data_entry_flow_index
- https://developers.home-assistant.io/docs/config_entries_index
- https://developers.home-assistant.io/docs/

* `pyproject.toml` pins Python 3.14, enforces branch coverage and strict lint
  gates, and enables `pytest` warnings-as-errors, strict markers, and HTML/XML
  coverage reports.
* `scripts/enforce_docstring_baseline.py` and
  `scripts/enforce_shared_session_guard.py` run in CI to block regressions; run
  them manually when touching diagnostics or guard metrics.
* `python -m scripts.sync_localization_flags` keeps
  `setup_flags_panel_*` translations aligned across locales; execute it after
  editing localization strings.
* `python -m scripts.enforce_test_requirements` ensures new tests add their
  third-party dependencies to `requirements-test.txt` so CI never regresses on
  missing packages.

* `python -m scripts.enforce_coverage_gates --coverage-xml coverage.xml` is the mandatory module-level coverage gate for critical runtime files (`coordinator.py`, `config_flow.py`, `services.py`, `ingest.py`).
* Coverage exclusions (`# pragma: no cover`) are allowed only for import/version fallbacks, defensive logging/cleanup paths, and `TYPE_CHECKING` branches—and every exclusion must include an inline reason.

## Integration architecture

- The integration lives in `custom_components/jackery_solarvault` and is installed via
  the UI only (`CONFIG_SCHEMA` is entry-only).
- Runtime state is stored on `ConfigEntry.runtime_data` through helpers such as
  `store_runtime_data` and the `jackery_solarvaultCoordinator`, so new features must hook
  into the coordinator rather than creating bespoke tasks.
- The manifest advertises discovery via Bluetooth, DHCP, MQTT, and Zeroconf and
  declares Platinum—keep the badge, manifest, diagnostics, and docs aligned
  whenever quality scale evidence changes.

## Development standards

### Python quality bar

- Target Python 3.14 syntax and typing everywhere; no untyped defs or implicit
  optionals are allowed because MyPy is configured to fail otherwise.
- Keep modules fully typed (`py.typed` is shipped) and add type aliases in
  `types.py` when expanding runtime models.
- Ruff supplies formatting and linting—respect 88 character lines, prefer
  f-strings, and keep imports sorted by section.
- Handle `ValueError` and `TypeError` explicitly in separate `except` blocks
  when coercing user/data payloads so logs and diagnostics can distinguish the
  failure mode; never use Python 2 `except ValueError, TypeError` syntax.
- Every coroutine interacting with Home Assistant must be async. Wrap blocking
  work with `asyncio.to_thread` for pure Python blocking calls, or
  `hass.async_add_executor_job` when the executor context or HA-managed
  thread pool is required.
- Use Home Assistant’s type aliases (`ConfigEntry`, `HomeAssistant`,
  `Platform`) and annotate return values so the shipped `py.typed` marker stays
  accurate.

### Config flows, options, and reauth

- `config_flow.py` implements user, discovery, reauth, and reconfigure steps.
  Add validation helpers alongside the mixins and reuse existing constants from
  `const.py` to keep schemas consistent.
- Do not allow users to rename entries during setup; titles are generated from
  the profile helpers. Always call `_abort_if_unique_id_configured` or the
  matching helpers before creating entries.
- Options flows should mirror config flow validation and store adjustments in
  `ConfigEntry.options`. Keep translation keys in `strings.json` and
  `translations/` synchronized.

### Platform guidance

- Extend existing entity platforms (`sensor.py`, `switch.py`, `button.py`, etc.)
  instead of creating new modules unless Home Assistant exposes a dedicated
  platform hook. Ensure `_attr_has_entity_name = True` and `device_info`
  metadata stay consistent across additions.
- When adding new entities, wire them through the runtime manager containers so
  coordinator payloads remain typed and diagnostics inherit guard telemetry.
  Update tests under `tests/components/jackery_solarvault/` to cover registration,
  diagnostics, and service interactions.
- Document any new services or options in `services.yaml`, `strings.json`, the
  README, and the diagnostics guide; run the docstring and shared-session guard
  scripts if you touch these areas.

### Testing principles (required)

- Keep each test focused on **one behavior**; avoid multi-purpose tests that
  validate unrelated outcomes at once.
- Use descriptive test names that communicate intent clearly, preferably in
  `given_when_then` form or an equivalently explicit purpose statement.
- Prefer `pytest.mark.parametrize` over copy/paste duplicates when exercising
  multiple input/output variants of the same behavior.
- Mock only true integration boundaries (network APIs, file I/O, Home Assistant
  service edges). Avoid over-mocking internal helpers or implementation details.
- Assert business outcomes and user-visible behavior, not transient
  implementation details such as private call ordering.
- Every bug fix must include a regression test added in the same change so the
  issue cannot silently return.

### Logging and diagnostics

- Initialise loggers with `_LOGGER = logging.getLogger(__name__)` and use lazy
  string formatting. Promote repeated failure information to repairs
  (`repairs.py`) and diagnostics exports (`diagnostics.py`).
- Diagnostics payloads must always include the `rejection_metrics` structure with
  zeroed defaults and `schema_version` so Platinum dashboards and docs can ingest
  the counters without bespoke scraping; update coordinator observability tests,
  docs, and front-end schema references together and revalidate the diagnostics
  panel once UI updates land.
- Mark entities with `_attr_has_entity_name = True` and populate `device_info`
  using identifiers from `const.py`. Align diagnostic sections with
  `docs/diagnostics.md` when telemetry changes.

## Documentation and release hygiene

## Documentation source boundary

- Contributor and agent workflow rules live in `docs/`.
- Jackery protocol captures, reverse-engineered DTOs, command catalogs, endpoint
  matrices, and protocol helper scripts live in `source-of-truth/`.
- `docs/` protocol pages are derived summaries only; if they conflict with
  `source-of-truth/`, update or remove the derived page.
- The classification for migrated protocol files is maintained in
  `docs/DERIVED_PROTOCOL_DOCS.md`.

- Update README, `info.md`, and `docs/` when workflows change. Each file must
  link to the relevant evidence (tests, modules, or scripts) so reviewers can
  verify Platinum claims.
- Keep `quality_scale.yaml` in sync with the manifest and Platinum badge so
  quality scale evidence stays current; update it alongside any architectural
  changes.
- Log new work in `CHANGELOG.md` and refresh brand assets once the Home
  Assistant brand repository accepts updates.
- After editing this guide, run `python -m scripts.sync_contributor_guides` so the
  Claude and Gemini mirrors stay in sync.


### Pull request review policy (required)

For every pull request, reviewers and bots must enforce this minimum quality bar:

1. Verify **branch-relevant coverage** so newly introduced branches and decision
   paths are covered by tests.
2. Require **traceable assertions** that validate user-visible outcomes or
   business invariants (not private implementation details).
3. Reject **fragile over-mocking** structures; mocks should be limited to true
   integration boundaries and keep behavior-focused tests stable.
4. For any new or changed logic, require a matching **regression test** in the
   same PR before merge.
5. Validate that **CI gates** (lint, typing, tests, coverage, and integration
   checks) fail and pass for the right reasons so merge protection is
   meaningful.
6. Keep review turnaround below **30 minutes per PR** when feasible by focusing
   on high-signal checks first and requesting targeted follow-ups for
   non-blocking refinements.

## Review checklist

- [ ] `ruff format`, `ruff check`, `mypy`, and `pytest -q` all pass locally.
- [ ] `scripts.hassfest` succeeds for `custom_components/jackery_solarvault`.
- [ ] Async flows, coordinators, and managers reuse shared helpers instead of
      introducing duplicate code.
- [ ] Config/Options flows validate input, prevent duplicates, and provide
      reauth/reconfigure paths.
- [ ] All user-facing strings live in `strings.json`/`translations/` and follow
      Home Assistant tone guidelines.
- [ ] New documentation includes citations to code/tests proving the behaviour.
- [ ] New logic includes regression tests; pull requests without tests for new behavior are not merged.
- [ ] Assertions in changed tests are traceable to user-visible behavior or business invariants.
- [ ] Test updates avoid fragile over-mocking and mock only true integration boundaries.
- [ ] CI gates are validated as meaningful for this PR (fail/pass behavior matches expectations).
- [ ] Review scope is triaged for a <30 minute reviewer pass when feasible.
- [ ] Any new `# pragma: no cover` usage is explicitly documented in the PR with file/line and a concrete justification.
- [ ] Device removal (`async_remove_config_entry_device`) and diagnostics remain
      covered by tests when behaviour changes.

---

## **1. Core Principles**

### **1.0 Always strict follow Home Assistant Best Practices**  
## /docs/ha_custom_config_and_best_practices.json
## /source-of-truth/jackery_complete_reference.json

### **1.1 Data Integrity First**
- **Only verified data** may enter the Home Assistant Recorder.
- **No raw 0-values, `None`, or unchecked payloads** from any source (Cloud, MQTT, BLE).
- **Mutual validation** between all data sources (5-minute intervals ↔ daily/monthly/yearly/lifetime).

### **1.2 Authoritative Sources**
| Source      | Protocol  | Priority       | Write Access | Purpose                          |
|-------------|-----------|----------------|--------------|----------------------------------|
| **Cloud**   | fasthttp  | **Highest**    | ✅ Yes       | Live data, backfill, trends, statistics (fallback to BLE/local MQTT) |
| **MQTT**    | MQTT      | High           | ✅ Yes       | Live data, settings, backfill, trends, statistics (fallback to Cloud/BLE)  |
| **BLE**     | BLE       | High           | ✅ Yes       | Live data, settings, backfill, trends, statistics (fallback to Cloud/MQTT) |

> **Rule**: Cloud data (fasthttp) **must never be overwritten or paused, only no-connection paused and try reconnect every 60SEC **.
> Local data (BLE/MQTT) is prefered for live values.

### **1.3 Async-Only**
- **All code must be async** (Home Assistant requirement).
- Use `asyncio.to_thread` for blocking calls (e.g., BLE operations).
- **No synchronous I/O** (e.g., file operations, network requests).

---

## **2. Data Processing Workflow**
### **2.1 Hierarchy & Validation**
###  5-minute intervals<->Daily<->Weekly<->Monthly<->Yearly<->lifetime
```
5-Minute Intervals (Payloads)
       ↓ (Aggregate + Validate)
Daily Values (Cloud + Local)
       ↓ (Aggregate + Validate)
Daily/Weekly/Monthly/Yearly/Lifetime Values (Aggregate + Validate)
```

### **2.2 Validation Rules**
1. **5-minute intervals**:
   - Must be **≥ 0** and **not suddenly drop to 0**.
2. **Daily values**:
   - Must be **≤ weekly value**.
   - Must be **≥ 5-minute values** and **drops to 0 at midnight**.
3. **Weekly values**:
   - Must be **≤ monthly value**.
   - Must be **≥ daily value** and **drops to 0 at new week**.
3. **Monthly values**:
   - Must be **≤ yearly value**.
   - Must be **≥ weekly value** and **drops to 0 at new month**.
4. **Yearly values**:
   - Must be **≤ lifetime value**.
  - Must be **≥ monthly value** and **never has 0**.
6- **Lifetime values**:
   - Must be **≥ Yearly value**.
   - Must be **> 0** and **never has 0**.
7. **0-values**:
   - Ignored unless **confirmed by another source**.

### **2.3 Backfill Logic**
- **Missing data** (e.g., April values in year) is fetched from Cloud.
- **Inconsistent data** (e.g., Cloud=0, Local=500) triggers a warning and uses the **lower value**.
- **No silent overwrites**: All merges must use `merge_live_properties` (not `_merge_dict_values`).

#### **Example: `verify_and_backfill`**
```python
def verify_and_backfill(
    cloud_value: float | None, local_value: float | None
) -> float | None:
    """Validate and merge values from Cloud and local sources."""
    if cloud_value is None and local_value is None:
        return None  # No data available
    if cloud_value == 0 and local_value > 0:
        _LOGGER.warning("Cloud returned 0, using local value instead")
        return local_value  # Cloud failure, local is valid
    if abs(cloud_value - local_value) > 0.1 * cloud_value:
        _LOGGER.warning(
            f"Inconsistent values: Cloud={cloud_value}, Local={local_value}"
        )
        return min(cloud_value, local_value)  # Plausibility check
    return cloud_value  # Cloud is authoritative
```

---

## **3. Key Requirements**
### **3.1 Live Data Priority**
- **Live values must remain live** regardless of connection (Cloud/MQTT/BLE).
- **No buffering delays**: Data must be pushed to HA immediately.

### **3.2 Local MQTT & BLE**
- **MQTT**:
  - Must be **configurable** (enable/disable via UI, like the app).
  - Settings (e.g., broker URL) must persist across restarts.
- **BLE**:
  - **Receive data** as fallback to Cloud/MQTT.
  - **Write access** for commands (e.g., firmware updates).

### **3.3 Error Handling**
- **Cloud failures**:
  - Fall back to local data (BLE/MQTT) **without breaking live updates**.
- **Local failures**:
  - Log errors but **do not block Cloud data**.
- **Corrupted data**:
  - Discard and **request fresh data** from Cloud.

---

## **4. Development Guidelines**
### **4.1 Code Quality**
- **Strict typing**: Use `mypy` with `--strict`.
- **Linting**: `ruff` (configured in `pyproject.toml`).
- **Tests**:
  - **No single-source-of-truth**: Always cross-check `/docs`, `/logs`, and real files.
  - **Unit tests** will be auto-generated once the integration is stable.
- **Pre-commit hooks**:
  - Run `ruff`, `mypy`, and `pyupgrade` before commits.

### **4.2 Workflow**
1. **Before changes**:
   - Cross-check existing code for duplicates or conflicts.
2. **After changes**:
   - Verify all user requirements are met.
   - **No output to user** until validation is complete.
3. **Token efficiency**:
   - Keep outputs concise and actionable.

### **4.3 Documentation**
- **`const.py`**:
  - Add **source references** for fields/commands (e.g., `""`).
  - **Minimal comments**: Only explain `state-of-art` or reasons for changes.
- **Developer functions**:
  - Guard all test/debug features with `JACKERY_DEV_MODE`.

---

## **5. Quality Gates**
### **5.1 Mandatory Checks**
| Tool          | Command                          | Purpose                          |
|---------------|----------------------------------|----------------------------------|
| `ruff`        | `ruff check . / ruff format`     | Linting + formatting             |
| `mypy`        | `mypy custom_components/jackery_solarvault` | Static typing          |
| `pytest`      | `pytest -q`                     | Unit tests + coverage            |
| `hassfest`    | `python -m scripts.hassfest`    | Validate manifest/strings        |
| `pyupgrade`   | `pyupgrade --py314-plus`        | Python 3.14+ syntax upgrades     |

### **5.2 Coverage Requirements**
- **100% branch coverage** for critical modules:
  - `coordinator.py`
  - `config_flow.py`
  - `services.py`
- **No `# pragma: no cover`** without justification.

---

## **6. Example: Data Flow for `today_energy`**
```python
async def update_today_energy(
    coordinator: JackerySolarVaultCoordinator, entity_id: str
) -> float | None:
    """Update today's energy value with mutual validation."""
    # 1. Fetch authoritative Cloud data
    cloud_data = await coordinator.api.get_today_energy()
    # 2. Fetch local data (BLE/MQTT)
    local_data = coordinator.data.get(entity_id, None)
    # 3. Validate and merge
    validated_value = verify_and_backfill(cloud_data, local_data)
    if validated_value is None:
        _LOGGER.warning(f"No valid data for {entity_id}")
    return validated_value
```

---

## **7. Open Issues & Priorities**
### **7.1 ***Always fix ruff bugs first, then mypy***
### **7.2 Critical/Breaking* Bugs *directly after ruff/mypy, always need to be fixed*
### **7.3 High Impacts *non-breaking but high issues*
### **7.4 Medium *like Deprecated/wrong Values*
### **7.5 Low Priorities *like Syntax/codesmells. should mostly fixed by ruff/lint/pyupgrade* 

---

## **8. References**
- [Home Assistant Developer Docs](https://developers.home-assistant.io/)
- [Platinum Integration Requirements](https://developers.home-assistant.io/docs/core/integration_quality_scale/)
- [Async Best Practices](https://developers.home-assistant.io/docs/asyncio_working_with_async/)
```

---

## **9. Platinum Quality Scale Status**

Die Integration zielt auf den **Platinum**-Quality-Scale ab, wird aber bis zur
Aufnahme in `home-assistant/core` als `custom` veröffentlicht. Home Assistant
selbst vergibt das offizielle Scale-Badge erst nach Veröffentlichung im Core-Repo;
bis dahin wird der Status intern in `custom_components/jackery_solarvault/quality_scale.yaml`
getrackt. Aus diesem Grund steht im Manifest
`"quality_scale": "custom"` (`custom_components/jackery_solarvault/manifest.json`),
während `quality_scale.yaml` 39 Rules als `done` und 1 als `todo` führt
(`strict-typing`). Drei weitere Regeln sind explizit als `exempt`
markiert: `discovery`, `discovery-update-info`, `inject-websession`.

Die Integration erfüllt bzw. verfolgt konkret:

- Async-only Datenfluss (Cloud, MQTT, BLE)
- Mutual-Validation Cloud ↔ MQTT ↔ BLE
- Reauth / Reconfigure / Rediscovery-Pfade
- Diagnostics mit `rejection_metrics` + `schema_version`

Das Platinum-Badge wird in diesem Dokument **nicht** reklamiert; es ist das
architektonische Ziel.

## **10. Reference Coverage Status**

Diese Sektion dokumentiert, wie viel vom upstream Jackery-Protokoll in der
HA-Integration tatsächlich implementiert ist. Die vollständigen Matrizen mit
jeder einzelnen fehlenden Funktion, jedem nicht abgedeckten Endpoint und jeder
fehlenden msg_id liegen in `docs/REFERENCE_COVERAGE.md`. Hoch-Level-Übersicht:

| Oberfläche               | Referenz | Implementiert | %      | Status |
|--------------------------|----------|---------------|--------|--------|
| HTTP-Endpoints           | 112      | 64            | 57 %  | partial |
| MQTT-Msg-Types (home)    | 25       | 28            | 100 %  | ok (all constants defined + routed) |
| MQTT-Msg-Types (portable)| n/a      | n/a           | n/a    | nicht anwendbar (MQTT = home only) |
| Commands (home)          | 47       | 47            | 100 %  | ok |
| Commands (portable)      | 41       | 0             | 0 %    | intentional (SolarVault home only) |
| Device-Modelle           | runtime  | runtime       | n/a    | ok (`/v1/device/system/list`) |
| PortableBody entities    | ~77      | 38            | 49 %  | partial (AC/DC/USB/temp/power/config) |
| Accessories              | 14       | 14            | 100 %  | ok |
| Shelly Cloud2Cloud       | 7        | 7             | 100 %  | ok |
| Crypto Layer A (auth)    | 1        | 1             | 100 %  | ok (random AES-128 key per login, RSA-wrapped) |
| Crypto Layer B (signing) | 1        | 1             | 100 %  | ok |
| Crypto Layer C (MQTT)    | 1        | 1             | 100 %  | ok (AES-128-CBC/PKCS7, key=bluetoothKey) |
| Services (HA)            | 7        | 7             | 100 %  | 7/7 in `strings.json` registriert |
| Test-Files               | 45       | 45            | 100 %  | tracked in git |

**Bekannte Abweichungen vom Reference-Protokoll:**

- **Crypto Layer A:** Login erzeugt pro Anfrage einen frischen zufälligen
  AES-128-Key und überträgt ihn RSA-wrapped. Golden-vector Tests injizieren
  den AES-Key deterministisch, ohne echten Login.
- **Crypto Layer C:** MQTT-Bodies werden seit 2026-06-08 AES-128-CBC/PKCS7
  verschlüsselt (key=bluetoothKey, iv=key, Base64). Siehe `client/api.py:encrypt_mqtt_body`.

---


Die Datei enthält alle Anforderungen (Datenvalidierung, Cloud-Priorität, Async-Code, Quality Gates). Sie dient als **zentrale Anleitung für Entwickler und Agenten**.