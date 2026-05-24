# jackery_solarvault Contributor Guide

These instructions describe how to work on the `custom_components/jackery_solarvault`
Home Assistant integration. They consolidate the authoring rules from the
Gemini and Claude style guides with the upstream Home Assistant requirements so
contributors consistently deliver Platinum-quality changes.

## Environment setup

1. Create a virtual environment and install the test tooling:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements_test.txt
   pip install -r requirements.txt
   ```
2. Install the integration in editable mode if you need to exercise packaging
   hooks: `pip install -e .`. The `pyproject.toml` file configures the
   setuptools build backend and enables branch coverage reporting for
   `custom_components/jackery_solarvault`.
3. Export `PYTHONPATH=$(pwd)` or invoke commands via `python -m …` so the local
   `scripts/` and `pytest_*` packages resolve correctly.

## Core workflow

Run every command before opening a pull request:

```bash
ruff format                          # Apply repository formatting rules
ruff check                           # Run Ruff lint (includes docstring gates)
pytest -q                            # Execute the async pytest suite with coverage
python -m scripts.enforce_test_requirements  # Confirm tests declare third-party deps
python -m scripts.enforce_coverage_gates --coverage-xml coverage.xml  # Enforce total + critical module coverage
mypy custom_components/jackery_solarvault    # Ensure static typing stays strict
python -m scripts.hassfest \
  --integration-path custom_components/jackery_solarvault  # Validate manifest & strings
python -m scripts.sync_contributor_guides           # Refresh assistant copies
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
to every change, suggestion, or review.

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
  third-party dependencies to `requirements_test.txt` so CI never regresses on
  missing packages.

* `python -m scripts.enforce_coverage_gates --coverage-xml coverage.xml` is the mandatory module-level coverage gate for critical runtime files (`coordinator.py`, `config_flow.py`, `services.py`, and `data_manager.py`).
* Coverage exclusions (`# pragma: no cover`) are allowed only for import/version fallbacks, defensive logging/cleanup paths, and `TYPE_CHECKING` branches—and every exclusion must include an inline reason.

## Integration architecture

- The integration lives in `custom_components/jackery_solarvault` and is installed via
  the UI only (`CONFIG_SCHEMA` is entry-only).
- Runtime state is stored on `ConfigEntry.runtime_data` through helpers such as
  `store_runtime_data` and the `jackery_solarvaultCoordinator`, so new features must hook
  into the coordinator rather than creating bespoke tasks.
- The manifest advertises discovery via DHCP, USB, HomeKit, and Zeroconf and
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

### Coordinators, managers, and services

- Use the existing managers (`FeedingManager`, `WalkManager`, etc.) to store
  per-dog logic; entities should subscribe to coordinator data instead of calling
  clients directly.
- Normalise door sensor overrides with `ensure_door_sensor_settings_config` so
  timeouts, durations, delays, and confirmation toggles remain clamped before
  mutating `DoorSensorConfig`. Trim and validate sensor entity IDs before
  persisting them. Settings-only updates should leave monitoring listeners
  untouched when the effective snapshot is unchanged, stored payloads must travel
  under `CONF_DOOR_SENSOR_SETTINGS`, and the normalised snapshot has to be
  persisted through `jackery_solarvaultDataManager.async_update_dog_data` so config-entry
  reloads retain the clamped values.
- Always pass the active config entry into new `DataUpdateCoordinator` instances
  and surface API errors via `UpdateFailed` or `ConfigEntryAuthFailed` as shown in
  `coordinator.py` and `exceptions.py`.
- Service handlers must live in `services.py`/`script_manager.py` and be
  validated in `services.yaml`. Keep the scheduler wiring in
  `async_setup_daily_reset_scheduler` intact when extending services.

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
