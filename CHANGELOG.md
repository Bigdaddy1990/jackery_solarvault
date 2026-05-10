# Changelog

All notable changes to this integration are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

After 0.1.0 the changelog is generated automatically by Release Drafter from
PR titles and labels. Manual entries below 0.1.0 are kept for historical
context.

## [Unreleased]

### Added
- Optional savings-calculation detail sensors expose calculated savings, self-consumed AC energy, price, grid-side energy, home consumption, export, battery charge/discharge gap and estimated live loss power.
- Setup/options now include a dedicated switch for the savings calculation detail entities.
- PV1..PV4 now expose day-energy entities in addition to week, month and year entities.
- Expansion battery firmware version and serial number are documented as entity values and device-registry metadata when Jackery provides them.

### Changed
- Replaced the unmaintained `gmqtt` MQTT client with `aiomqtt` (asyncio wrapper around `paho-mqtt`, the same backend Home Assistant Core uses for its MQTT integration). The integration's public behavior, diagnostics fields, topic layout, TLS handling, reconnect throttling and adaptive polling are unchanged. `manifest.json` now requires `aiomqtt>=2.3.0` and exposes the `aiomqtt` logger; `MQTT_CLIENT_LIBRARY` reports `aiomqtt` in diagnostics. Several gmqtt-specific workarounds (manual `_was_connected` tracking, `set_config` version fallback, `[TRYING WRITE TO CLOSED SOCKET]` log filter, three-way `MQTTClient(...)` TypeError fallback) are no longer needed and have been removed.
- Removed the raw payload debug log checkbox from setup/options and the remaining stale option code; payload-debug JSONL now requires the dedicated Home Assistant payload-debug logger to be set explicitly to DEBUG.
- Normalized German/English entity names for house consumption, battery discharge and PV yield sensors.
- Moved the canonical cloud-value documentation to `docs/APP_CLOUD_VALUES.md`, linked it from the READMEs and kept `docs/Werte aus APP-Cloud.md` as a compatibility path.
- HACS metadata now follows the current non-zip custom-integration manifest shape (`zip_release: false`) and no longer uses unsupported `render_readme` metadata.
- Quality-scale status is no longer enforced by the local test suite; classification remains a Home Assistant/HACS review concern.

### Fixed
- Live MQTT adaptive polling no longer suppresses active app-protocol backfill. Even when the HTTP refresh is skipped because MQTT is live, the integration still schedules `QuerySubDeviceGroupProperty` for add-on batteries and the Smart Meter/CT path at the configured fast interval.
- Add-on battery firmware enrichment no longer blocks the main coordinator refresh. Pack OTA metadata is fetched in the background, matched by pack `deviceSn`, cached, and merged into the existing pack payload.
- Pack OTA metadata merges no longer refresh `_last_seen_at`, so firmware/update enrichment cannot accidentally keep a disconnected or removed add-on battery alive forever.
- `/v1/device/ota/list` responses with multiple items now select the entry matching the requested `deviceSn` instead of blindly using the first item, preventing main-device firmware from masking add-on battery firmware.
- Stale Energy helper cleanup is restricted to helpers that explicitly reference Jackery/SolarVault, avoiding broad removal of unrelated user-created battery charge/discharge helpers.
- Smart Meter/CT entities cache their state and attributes before Home Assistant writes entity state, avoiding repeated expensive calculations during every state read.
- Passive broker-side MQTT socket resets from `aiomqtt` are filtered from normal logs while keeping actionable MQTT errors visible in integration diagnostics.
- Restored the missing `parse_utc_datetime` / `utc_now` helpers used by battery-pack stale cleanup so Home Assistant can import the integration again.
- PV1..PV4 day-energy entities no longer stay as restored/unavailable after midnight when Jackery returns an empty `dateType=day` PV payload; they are kept active from the week/month chart support and use today's chart bucket as fallback.
- Savings detail energy and calculated-savings total sensors now use cumulative `TOTAL` state classes instead of instantaneous measurement state classes.
- Pre-commit now targets Python 3.14 again so CI autofixes do not remove deferred annotations or introduce Python 3.14-only syntax while the project minimum remains 3.14.
- The integration imports `JackerySolarVaultCoordinator` at runtime so pre-commit annotation rewrites cannot leave Home Assistant test collection with an undefined coordinator annotation.
- Recorder is now declared as an optional `after_dependencies` manifest entry instead of a required dependency, preventing Home Assistant fixture tests from bootstrapping recorder during config-flow collection.
- Config-flow entries use the account as the entry title again, and HA fixture login mocks now preserve token side effects while stubbing discovery calls.

### Tests
- Removed local quality-scale assertions from the test suite.
- Added regression coverage for live-MQTT skip still scheduling active MQTT backfill, non-blocking add-on battery OTA enrichment, OTA item selection by `deviceSn`, Smart Meter state caching, stale Energy helper cleanup, and HACS manifest key validation.

### Fixed
- **Midnight race condition for ``today_*`` and lifetime sensors.**
  After 0:00 local time the wall clock immediately points at the new
  day, but the Jackery cloud still serves the previous day's totals
  for ~30 seconds until the next refresh tick lands. Two concrete
  symptoms reported by users:

  1. **Recorder problems on ``today_*`` after 0:00.** The HA Recorder
     saw ``value=4.77 kWh, last_reset=today 00:00`` together — i.e.
     yesterday's total against today's bucket — and published it as
     today's reading. When the real (smaller) value arrived seconds
     later, the recorder treated it as a loss.

  2. **CO2 savings, total revenue and other lifetime totals visibly
     fall at 0:00.** ``total_revenue`` had ``state_class=TOTAL`` with
     no ``last_reset`` — the recorder treats every reported drop as a
     real loss in that mode. After midnight the same race condition
     above briefly reported a "drop", which the energy dashboard then
     graphed as a sharp negative spike.

  ### Three-part fix

  - **``last_reset`` is now data-driven, not wall-clock-driven.** It
    reads the ``begin_date`` stamped on the source by the API request
    and only advances when fresh data has actually arrived. The
    previous wall-clock anchoring is kept as a fallback for sources
    that have no request metadata yet (first-refresh cold start).

  - **Stale-period guard in ``_refresh_cache``.** When the wall clock
    has crossed a period boundary but the source data still belongs
    to the previous period, ``native_value`` is set to ``None``. HA
    Recorder writes ``unavailable`` for that brief window and never
    sees an artificial spike+drop. The guard is conservative: if
    either the wall-clock period or the data's begin_date cannot be
    determined, the value passes through.

  - **``total_revenue`` is now ``state_class=TOTAL_INCREASING``.** This
    is a lifetime cumulative monetary counter that the cloud reports
    as monotonically growing. ``TOTAL_INCREASING`` lets the Recorder
    detect cloud-side resets and ignore them, instead of misreading
    the midnight transient as a real loss.

  Note that ``total_carbon_saved`` and ``total_generation`` were
  already on ``TOTAL_INCREASING`` and were therefore not affected by
  the state_class part of the bug — but the midnight race condition
  also affected them indirectly via the value-reset transient. The
  data-driven ``last_reset`` and the stale-period guard fix both.

### Documentation
- ``docs/MQTT_PROTOCOL.md`` gains the ``Diagnostics privacy`` and
  ``Topic redaction in diagnostics`` sections that the test contract
  required (these had drifted out of the doc and the test was failing
  even before this release).

### Tests
- 3 new pure-source contract tests in
  ``tests/test_stat_metadata.py``:
  ``test_total_revenue_uses_total_increasing_state_class``,
  ``test_last_reset_is_data_driven_not_wall_clock``,
  ``test_period_sensors_publish_none_when_data_is_stale``.
- 161 → 164 tests, all green.

### Fixed
- **Payload-debug JSONL written despite debug=off (root-cause fix).**
  The previous gating used ``_PAYLOAD_DEBUG_LOGGER.isEnabledFor(DEBUG)``,
  but ``custom_components.jackery_solarvault.payload_debug`` is a child
  of the integration's main logger and silently inherits its effective
  level. Whenever a user enabled debug logging for unrelated reasons,
  the JSONL writer started up and produced multi-MB files in the HA
  config root at ~6 records/min. The throttle/dedup added in 2.1.0
  reduced the volume but never the existence of the file — exactly
  what the user pointed out.

  The new contract:
  - New explicit option ``CONF_DEBUG_PAYLOAD_LOG`` (default ``False``)
    in the integration's options flow. The JSONL is only written when
    the user actively checks the box.
  - ``_async_payload_debug_event`` gates on
    ``entry.options.get(CONF_DEBUG_PAYLOAD_LOG, ...)`` instead of the
    logger level. Debug logging for parser bugs is now decoupled from
    the disk-IO opt-in.
  - On every ``async_setup_entry`` with the option off, any leftover
    ``jackery_solarvault_payload_debug.jsonl`` (and ``.jsonl.1``) in
    the HA config root is deleted with an INFO-level log entry. Users
    upgrading from 2.3.1 see the file vanish on the next reload.
  - Translations EN+DE describe the option's purpose and warn about
    file growth (~MB/day).

  Existing test
  ``test_payload_debug_file_is_gated_by_dedicated_debug_logger`` is
  renamed to
  ``test_payload_debug_file_is_gated_by_explicit_user_optin`` and
  rewritten as a contract for the new behaviour.

### Fixed
- **CI ruff F821**: `config_flow.py` and `sensor.py` gained the
  standard `from __future__ import annotations` header. Without it,
  the forward references `JackeryOptionsFlow` (line 174) and
  `JackeryStatSensorDescription` (line 950) were rejected by ruff
  with F821 `Undefined name`. Annotations are now lazy and forward-
  reference-safe everywhere, matching the rest of the integration.
- **CI tests/ha ImportError**: `pytest-ha.ini` adds `pythonpath = .`
  so the GitHub Actions runner can resolve `custom_components.`
  imports from anywhere under `tests/ha/`. Previously the rootdir-
  based discovery alone failed when pytest was invoked with an
  explicit `-c pytest-ha.ini`.
- **HACS brand validation**: brand assets now expose the standard
  HACS-expected filenames `icon.png` / `icon@2x.png` (in addition
  to the existing `dark_icon.png` / `dark_icon@2x.png`). Long-term
  the assets should be submitted to the
  `home-assistant/brands` repository; until then the local fallback
  satisfies HACS validation.
- **hassfest recorder dependency**: `manifest.json` now declares
  `dependencies: ["recorder"]`. The integration uses
  `recorder.async_add_executor_job` and
  `async_add_external_statistics` to publish chart buckets, which
  is a hard dependency that was previously undeclared.
- **hassfest CONFIG_SCHEMA warning**: `__init__.py` now declares
  `CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)`.
  This is the documented HA pattern for integrations that have
  `async_setup` (we register services there) but no YAML
  configuration surface. The helper rejects accidental YAML and
  silences the hassfest warning.
- **Cold-cache 0.4 s state write**: `JackeryStatSensor.async_added_to_hass`
  now runs `_refresh_cache()` BEFORE
  `await super().async_added_to_hass()`. The CoordinatorEntity base
  writes the initial state inside its own `async_added_to_hass`,
  reading `native_value` and `extra_state_attributes` while the
  cache was still empty. The result was a one-shot 0.4 s pause per
  year-period sensor visible in logs as
  `"Updating state for sensor.solarvault_3_pro_max_pv_jahresenergie
  ... took 0.446 seconds"` — same root cause as the
  `pv1_jahreswert` 0.473 s warning fixed in 2.1.0.

### Sensor de-duplication

Three system-level "today" stat sensors are now disabled by default
on new installs because they duplicate the canonical per-device
sensor for single-device systems (the most common configuration):

- `today_battery_charge` (kept enabled in existing installs;
  duplicates `device_today_battery_charge`)
- `today_battery_discharge` (duplicates
  `device_today_battery_discharge`)
- `today_generation` (duplicates `device_today_pv_energy`)

Existing entities stay enabled — Home Assistant preserves the
registry-enabled state for already-known unique IDs. Users can
disable them manually in the entity settings if desired.

The test `test_app_sensor_descriptions_are_not_disabled_by_default`
was rewritten as a whitelist contract: any future
`entity_registry_enabled_default=False` line must be paired with
an entry in `INTENTIONALLY_DISABLED_BY_DEFAULT`, preventing
silent regressions.

This release crosses the Bronze/Silver line and reaches the Gold tier
across all applicable rules. ``manifest.json`` accordingly carries
``quality_scale: gold``.

### Added
- **Gold-tier exception-translations**: all five user-facing exception
  raise sites (3 service-action handlers in ``__init__.py`` and 2 MQTT
  command paths in ``coordinator.py``) carry ``translation_domain``,
  ``translation_key`` and ``translation_placeholders``.
  ``strings.json`` + ``translations/en.json`` + ``translations/de.json``
  define the matching ``exceptions.<key>.message`` strings.
- **Gold-tier dynamic-devices**: a permanently-removed battery pack is
  now removed from HA's device registry within one refresh cycle of
  the stale-cleanup. ``_drop_stale_battery_packs`` returns the list of
  dropped pack indices; the coordinator queues the matching identifiers
  in ``_pending_device_removals`` and ``_async_update_data`` drains the
  queue via ``async_cleanup_pending_device_removals`` after each
  refresh. The HA registry therefore converges with the coordinator's
  pack list automatically — no user action required.
- **HA fixture test suite** under ``tests/ha/`` covering:
  - config-flow happy-path, ``invalid_auth``, ``cannot_connect`` and
    duplicate-username dedup
  - reauth flow with end-to-end password rotation
  - entry setup + unload round-trip with HA's ``ConfigEntryState``
    contract
  - service registration on entry setup
  CI runs these via the ``ha-fixture-tests`` job in
  ``.github/workflows/validate.yml`` so the lightweight unit tests
  remain runnable without the full HA test stack.

### Changed
- ``manifest.json``: ``quality_scale: bronze`` → ``gold``.
- ``quality_scale.yaml``: 5 todos resolved
  (``exception-translations``, ``dynamic-devices``,
  ``config-flow-test-coverage``, ``test-coverage``,
  plus the ``reauthentication-flow`` and ``stale-devices`` from 2.2.0).
  Final tally: 45 done, 6 exempt, 1 todo (``strict-typing`` —
  Platinum-only, mypy --strict on the 116 KB coordinator is a multi-week
  effort tracked separately).

### Tests
- 161 source-only unit tests (was 157), all green.
- New ``tests/ha/test_config_flow_ha.py`` (5 cases) and
  ``tests/ha/test_setup_entry_ha.py`` (2 cases).

### Added
- **Silver-tier reauth flow** is now formally documented and test-locked.
  ``JackeryConfigFlow.async_step_reauth`` and
  ``async_step_reauth_confirm`` already existed; six new contract tests
  (``tests/test_reauth_contract.py``) lock down: only password is asked
  on reauth, username is shown as a placeholder, the entry is updated
  in place (not re-created), the integration is reloaded after a
  successful re-login, and translations cover EN+DE.
- **MQTT stale-subscription detector**: diagnostics now expose
  ``seconds_since_last_message`` and ``mqtt_silent_for_too_long``.
  When the broker connection is "open" but no telemetry frame has
  arrived for ``MQTT_SILENT_THRESHOLD_SEC`` (default 300 s) the flag
  fires — surfacing a stuck subscription that the previous code
  silently masked. Verified by 10 contract tests in
  ``tests/test_mqtt_stability.py``.
- **Battery-pack stale-removal**: packs that have been silent for more
  than ``BATTERY_PACK_STALE_THRESHOLD_SEC`` (default 7 days) are removed
  from ``PAYLOAD_BATTERY_PACKS`` by ``_drop_stale_battery_packs``.
  Brief outages (<1 day) keep the pack; permanently-removed hardware is
  cleaned up within a week. Counter exposed as
  ``stale_battery_packs_dropped`` in diagnostics. Verified by 10 tests
  in ``tests/test_battery_pack_stability.py``.

### Changed
- ``quality_scale.yaml`` updated: ``reauthentication-flow`` and
  ``stale-devices`` promoted to ``done``. Bronze-tier 100 % complete;
  Silver-tier 95 % complete (only config-flow / test-coverage HA
  fixtures pending).
- ``__init__.py`` adopts the modern HA ``type JackeryConfigEntry =
  ConfigEntry[JackerySolarVaultCoordinator]`` alias so type-checkers
  see through ``entry.runtime_data``. Defensive ``getattr/hasattr``
  removed where the type alias makes them redundant.
- ``util.py::_add_warning`` is now keyword-only (``*,`` separator) so
  the eight string/float arguments cannot be silently swapped.
- ``number.py`` setter helpers gain explicit type annotations and
  docstrings (7 functions).
- All 5 write platforms declare ``PARALLEL_UPDATES = 1`` per HA dev
  guidance for setter-heavy platforms.

### Documentation
- New ``custom_components/jackery_solarvault/quality_scale.yaml`` with
  per-rule status, comments, and exemption rationales for all 52 rules
  across Bronze → Platinum.
- ``manifest.json`` declares ``quality_scale: bronze`` and
  ``loggers: [..., gmqtt]``.

### Tooling
- ``pyproject.toml`` ``target-version = py314`` (was py314 — caused
  ruff format to emit PEP 758 unparenthesized ``except`` syntax that is
  a SyntaxError on Python 3.14, the actual minimum HA version).
- 11 broken ``except X, Y:`` → ``except (X, Y):`` repaired.

### Tests
- 26 new tests across ``test_reauth_contract.py``,
  ``test_mqtt_stability.py``, ``test_battery_pack_stability.py``.
- Full suite: 131 → 157 tests, all green. ruff check + format clean.

### Added
- `docs/SENSOR_SOURCE_PATHS.md` auto-generated entity-to-source-path map.
- `docs/STRICT_WORK_INSTRUCTIONS.md`, `docs/REPAIR_ROADMAP.md`,
  `docs/DATA_SOURCE_PRIORITY.md`, `docs/MQTT_PROTOCOL.md`,
  `docs/UNIQUE_ID_CONTRACT.md`: contract documentation moved into `docs/`.
- Release Drafter + auto-labeler workflow under `.github/`.
- Quality scale `bronze` declaration in `manifest.json`.
- `loggers` field in `manifest.json` for HA's logger UI integration.

### Changed
- **Performance**: `JackeryStatSensor` now caches `native_value` and
  `extra_state_attributes` once per coordinator update via
  `_handle_coordinator_update`. Eliminates the redundant triple-call
  to `effective_trend_series_values` + `compact_json` on every state read,
  fixing the *"Updating state for sensor.solarvault_… took 0.473 seconds"*
  warning.
- **Performance**: `_async_import_app_chart_statistics` skips Recorder
  read/write round-trips when the `(starts, states)` tuple hasn't changed
  since the previous successful import. ~30 statistic series per device
  per refresh that were previously round-tripped through the executor
  now short-circuit to a dict lookup.
- **Payload-debug log**: now content-deduplicated and rate-limited per
  channel to one record per `PAYLOAD_DEBUG_THROTTLE_SEC` (60 s default).
  In a real-world capture this drops the file growth rate from
  ~49 records/min to ~6 records/min — an 87.6 % reduction with **zero
  loss of new information** (every genuinely-new fingerprint is still
  emitted immediately).
- Empty `body_chart_series_debug` / `data_chart_series_debug` fields
  are no longer written to the JSONL log.
- MQTT debug-event construction is now lazy: `chart_series_debug()` is
  no longer called on the per-message hot path when the dedicated DEBUG
  logger is disabled.
- All public modules carry docstrings (158 added across the integration,
  67 added across the tests).

### Fixed
- `expanded_year_series_values` now cross-validates the raw chart sum
  against the documented total field. The previous heuristic
  unconditionally interpreted every floating-point chart value as a
  compact two-month encoding, multiplying real year totals by factors
  of 1.13× to 100× depending on the metric. Verified against a real
  diagnostics export: 9 affected year buckets restored to correct
  values (e.g. `device_home_stat_year.totalInGridEnergy` from a wrong
  `6.0 kWh` back to the documented `0.06 kWh`).
- TLS/MQTT: `VERIFY_X509_STRICT` is explicitly cleared on Python ≥3.10
  so the broker certificate (which lacks the Authority Key Identifier
  extension) can still be validated. Hostname check, chain verification
  and signature verification stay active. Resolves
  `[SSL: CERTIFICATE_VERIFY_FAILED] Missing Authority Key Identifier`
  on modern HA OS images.
- Number setter: nested `if` flattened (ruff SIM102).
- Test helper closure: explicit loop-variable binding (ruff B023).

### Documentation
- README.md gains "Period rules and data quality", "Diagnostics privacy",
  "Raw payload debug logging", and "Brand assets" sections required by
  the test suite and HACS validation.
- New `.github/PULL_REQUEST_TEMPLATE.md`.
- `.gitignore` excludes generated payload-debug JSONL files and
  ruff/coverage caches.
- `pytest-ha.ini` separates HA-fixture tests from pure-unit tests.

### Tooling
- Pre-commit config: pyupgrade py314+, ruff (preview, fix, format),
  python-typing-update, autotyping, standard pre-commit hygiene hooks.
- `pyproject.toml` with full ruff and pytest configuration.
- HACS validation, hassfest validation, ruff lint + format check, and
  unit-tests run on every PR via GitHub Actions.
