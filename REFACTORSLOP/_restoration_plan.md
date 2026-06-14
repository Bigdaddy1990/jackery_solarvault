# Jackery SolarVault — Restoration Ledger (working artifact, NOT repo doc)

Generated from fresh AST loss audit + adversarial verification workflow (wf_bc7fc629-b20)
+ manual adjudication of disputed items. Date: 2026-06-14.

Source of truth for "what the app does": extracted data in `docs/jackery_*`, `docs/hbxn_*`,
`docs/ha_*`, and the RE markdown. NEVER tests, NEVER prose .md.

## Legend
- [ ] todo   [x] done   [~] in progress   [!] quarantined (needs analysis)

## GROUP A — Rejection metrics (observability; quality_scale.yaml expects it)  ✅ CORE DONE 2026-06-14
Backup: coordinator.py. Was restored in a4b888c, reverted in 6b79eb7 (baseline reset — collateral loss).
- [x] RejectionMetrics dataclass (+ increment, _seen dedup, last_rejection, as_dict) — coordinator.py module level
- [x] coordinator.__init__: self.rejection_metrics = RejectionMetrics() + self.api.auth_rejection_callback = self.record_http_auth_rejection
- [x] record_http_auth_rejection / record_payload_validation_rejection / record_schema_rejection / record_timestamp_skew_rejection
- [x] API callback hook restored in _http.py (auth_rejection_callback attr + invoked in _is_auth_failure_response, guarded) + api.py default
- [x] surface in diagnostics.py (async_redact_data) + DIAGNOSTICS_SCHEMA_VERSION in const.py
- [x] CALL SITES wired: http_auth/token_expiry (callback), mqtt_broker (pause-after-auth-failure), schema (data-quality warnings loop @ ~7671)
- [ ] DEFERRED call sites: timestamp_skew (battery-pack validation moved to STATELESS subdevices/detector.py — needs detector→coordinator reporting), payload_validation (no backup call site found). Methods exist + callable.
- VERIFIED: ruff clean on all 5 files, compileall OK.

## CRITICAL BUGFIX (pre-existing, blocked ALL loading)  ✅ DONE 2026-06-14
- [x] const.py raised `NameError: MqttError` on import → ENTIRE integration could not load. Cause: abandoned/broken
      local-MQTT-constant "centralization" block (lines ~39-80) referencing undefined MqttError / use-before-def
      MQTT_TOPIC_NOTICE / unimported Callable,Any,Awaitable. All 9 names were dead duplicates of working defs in
      client/local_mqtt.py + client/mqtt_push.py (nothing imported the const copies). REMOVED the block.
- [x] const.py had the ENTIRE BLE constants block duplicated (F811 x10, lines 446-484 = verbatim copy of 149-186,
      identical values). REMOVED the misplaced duplicate.
- VERIFIED: const.py standalone import OK; package-wide ruff F821 + F811 = clean; compileall OK.
- NOTE: pre-existing uncommitted WIP left the tree broken. Other modules import fine (F821 clean package-wide).

## GROUP B — HTTP transient retry (RESILIENT contract; _HTTP_RETRY_* consts exist but UNUSED)  ✅ DONE 2026-06-14
Backup: client/api.py::JackeryApi. Current _http.py only retries once on token expiry.
- [x] _is_transient_http_status (500<=s<600)
- [x] _request_json_with_retry (bounded attempts + backoff over _HTTP_RETRY_BACKOFF_SEC, catch TimeoutError/ClientConnectionError)
- [x] wire _get_json/_put_json/_post_form/_post_json through it (preserve token-expiry path) — both initial + post-relogin call sites
- VERIFIED: py_compile + compileall OK, ruff check clean. Constants no longer dead.

## GROUP C — Device control  ✅ DONE 2026-06-14 (ruff clean, compile OK)
- [x] coordinator.async_bind_smart_part (MQTT_MESSAGE_BIND_SMART_ACCESSORY / ACTION_ID_BIND_SMART_PART / cmd 108)
- [x] coordinator.async_unbind_smart_part (MQTT_MESSAGE_REMOVE_SMART_ACCESSORY / ACTION_ID_UNBIND_SMART_PART / cmd 109)
- [x] coordinator.device_supports_third_party_mqtt (+ 6 bind/unbind const imports)
- [x] client/_endpoints/device.py::async_set_max_power (FIELD_MAX_POWER + MAX_POWER_SAVE_PATH imports)
- NOTE: NO callers existed even in backup (latent API). Exposing via services = NEW functionality (perfektionieren, later).
  text.py:122 in backup called device_supports_third_party_mqtt; current text.py does NOT (uses other check).

## GROUP D — BLE resilience  ⏸️ DEFERRED (low value)
Package-wide F821 is clean → current BLE code does NOT reference any of the missing consts, meaning the
code that USED them was also removed. Restoring the consts alone = dead; restoring behavior (exp. backoff,
version-count warning) = re-implementing refinements on the EXPERIMENTAL/gated BLE transport. Revisit only if
BLE stability issues surface.

## GROUP E — Diagnostics capping  ⏸️ PARTIAL
- [x] DIAGNOSTICS_SCHEMA_VERSION (done in Group A, const.py)
- [ ] _cap_section / _SECTION_SIZE_CAP=4096: DEFERRED — backup wrapped ~15 sections at 4KB each, replacing
      large response snapshots with {truncated, size_bytes}. Trades diagnostic DATA for size; harmful while
      debugging a faulty integration. Revisit Phase 6 with a sensible (larger) cap if 32KB HA limit bites.

## CORRECTNESS VALIDATION vs extracted app data (2026-06-14)
- [x] COMMAND/ACTION-ID layer: const.py ACTION_ID_* / MQTT_CMD_* / MQTT_MESSAGE_* cross-checked against
      docs/jackery_command_catalog_v2.csv (smali). ~20 commands spot-checked → ALL MATCH (incl. restored
      bind 3012/108/BindSmartAccessory, unbind 3013/109/RemoveSmartAccessory). Independently confirms
      3022=EPS/AC-off-grid (not charge limit) and 3028=charge-discharge line. COMMAND LAYER = CORRECT.
- [x] PAYLOAD FIELD layer: scripts/_field_audit.py diffs const.py FIELD_* vs http_model_fields +
      hbxn_model_fields + entity_field_candidates. Result: 63 "suspects" are almost all legit
      (MQTT-envelope: actionId/cmd/data/msg; write-command bodies; stats chart axes x/y; class-name keys).
      Integration correctly reads even the app's MISSPELLED fields: FIELD_GRID_STATE="gridSate"(sic),
      FIELD_ONGRID_STAT="ongridStat", FIELD_OTHER_LOAD_PW="otherLoadPw". FIELD LAYER = CORRECT.
- CONCLUSION: data-mapping layers (commands + fields) are SOUND. The integration's faultiness is in
  LOGIC/RUNTIME behavior, not data mappings. Focus next on the known bug clusters + located logic bugs.
  Audit scripts: clause backups/_field_audit.py (rerun anytime).

## PHASE 2 — Centralize constants  ✅ ESSENTIALLY DONE (already centralized)
scripts/_dup_const_audit.py: 1132 module-level consts, only 9 appear in >1 module — ALL correct-by-design:
  - PARALLEL_UPDATES (HA per-platform convention: 0 read-only / 1 command), REDACT_KEYS (diagnostics aliases
    _STATIC_REDACT_KEYS), _STORAGE_KEY (each cache needs its own), _LOGGER/_AIOMQTT_LOGGER (per-module __name__).
  - Only 4 trivial private cache keys duplicated (_KEY_ENTRIES, _STORAGE_VERSION, _LOCAL_MQTT_RUNTIME_KEY,
    _BLOCKED_LOCAL_MQTT_TOPIC_FILTERS). Recommendation: LEAVE local — centralizing module-private storage keys
    couples unrelated modules for no benefit. Directive satisfied. (The removed broken const.py block was an
    OVER-centralization attempt — correct rule is shared→const.py, private→local.)

## PHASE 3 — Python 3.14+  ✅ ESSENTIALLY DONE
pyproject: requires-python ">=3.14", ruff target-version "py314". `ruff check --select UP,FA,RUF` on the whole
package = ZERO violations. Code already uses PEP 695/649 idioms (X|None, lazy annotations, tuple[T,...]).
No sweeping rewrite needed — only spot-fixes if the bug-hunt finds any.

## PHASE 4 — HA best practices  ⏳ partial
- get_instance imported from homeassistant.components.recorder (Pyright reportPrivateImportUsage) — VERIFY if
  HA actually re-exports it (HA core commonly imports it from components.recorder; may be a Pyright-stub nit, not real).
- config_flow async_step_dhcp/mqtt/zeroconf annotate discovery_info as `Any # noqa: ANN401` — replace with real
  homeassistant.helpers.service_info.{dhcp,mqtt,zeroconf} types (the deferred Dhcp/Mqtt/ZeroconfServiceInfo item).
- Else: integration already uses Store, DataUpdateCoordinator, entity_id contracts, repairs, quality_scale.yaml.

## PRE-EXISTING TYPE BUGS found via Pyright (log for bug-fix phase; verify against mypy.ini)
- coordinator.py async_save_mqtt_session(**snapshot): BENIGN — `# type: ignore[arg-type]` for **dict unpack, not a real bug.
- coordinator.py: `get_instance` imported from homeassistant.components.recorder (should be .helpers.recorder)
  — multiple sites (~545, 5147, 5207, 5277, 5891...). HA deprecation → Phase 4.
- coordinator.py ~7294/7314: async_get_device_pv_stat(system_id) called with `str | None`, expects `str | int`
  → can pass None → API error. Phase 5.
- coordinator.py ~8336: async_save_mqtt_session(cached_at=...) passed `str`, expects `float | None`. Phase 5.

## GROUP D — BLE resilience
- [ ] _mark_property_query_started (cmd!=106, _pending_property_query_starts deque maxlen=4)
- [ ] async_ensure_connected (verify call need first)
- [ ] _unrecognised_version_count = [0]  (ble.py)
- [ ] _MAX_CONNECTION_RETRIES = 50 (ble_transport.py)
- [ ] exponential backoff: _LOST_LINK_BACKOFF_SEC=8.0, _MAX_BACKOFF_SEC=300.0 (current only fixed _RECONNECT_BACKOFF_SEC=30.0 — degraded)

## GROUP E — Diagnostics capping
- [ ] _cap_section(value) + _SECTION_SIZE_CAP=4096 (diagnostics.py)
- [ ] DIAGNOSTICS_SCHEMA_VERSION: Final = 1 (diagnostics.py or const.py — coordinate with A)

## DROP — superseded / handle in later phase (NOT verbatim restore)
- config_flow Dhcp/Mqtt/ZeroconfServiceInfo = Any  -> Phase 4: import real HA service_info types
- client/third_party_mqtt_codec._split_iv_envelope -> superseded (key-as-IV); Phase 5 crypto cross-check
- util.ZERO_CONFIRM_MIN_LOCAL_HOUR -> feature gone; Phase 5 (midnight stats-reset bug)

## DO NOT RESTORE — verified correct removals
- 31 RELOCATED symbols/consts (subdevices/detector.py, models/shelly_cloud.py, models/property_merge.py,
  stats/__init__.py, client/mqtt_state.py, client/third_party_mqtt_codec.py, client/_http.py inlined)
- _ENTRY_LOCKS / _CACHE_LOCK (migrated to HA Store — best practice)
- ACTION_ID_SOC_CHARGE_LIMIT=3022 (BUG; smali proves 3022=EPS toggle; 3028 ACTION_ID_SOC_LIMITS carries both)
- _encode_third_party_mqtt_secrets (moved to client.third_party_mqtt_codec.encode_third_party_mqtt_field)
- _async_consume_session (inlined into _async_run_session)

## QUARANTINE — analyze before any action
- [!] _should_skip_fast_property_fetch — adaptive polling skip; state vars+consts still present (dead);
      interacts with commit b542352 "stop MQTT/BLE push from resetting HTTP polling timer". Analyze conflict.
- [!] _async_retry_after_invalid_discovery_devices — inlined; lost two-strike _invalid_device_id_counts state (minor)
