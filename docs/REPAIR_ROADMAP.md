# Repair roadmap

This is the **strict order** in which the repository can be brought from
the bug-quagmire of past releases to a stable, well-tested integration.
Skipping phases creates compounding regressions; do not jump ahead.

## Phase 0 — Do not add migrations

The first public package shipped with the unique-ID contract from
``docs/UNIQUE_ID_CONTRACT.md``. Do not add config-entry version
migration ladders; there is no legacy state to migrate from.

## Phase 1: Establish real Home Assistant test infrastructure

Local Windows runs use stubs; CI uses
`pytest-homeassistant-custom-component` against the published HA
release. The CI workflow `validate.yml` installs `requirements-test.txt`
and runs `pytest -q -p no:cacheprovider`.

## Phase 2 — Lock down parser shape

Every public payload section (HTTP `data` and MQTT `body`) gets a
shape-asserting test. Tests use real captured payloads, not synthetic
mock data.

## Phase 3 — Lock down entity contract

Unique IDs, attribute names, units, state classes are pinned via
`tests/test_stat_metadata.py`. No silent renames.

## Phase 4 — Period contract

Day/week/month/year period entities follow `docs/DATA_SOURCE_PRIORITY.md`.
Cross-period repair is forbidden; contradictory cloud data surfaces as a
HA Repair issue.

## Phase 5 — TLS / privacy

`docs/MQTT_PROTOCOL.md` and `tests/test_code_quality.py` enforce the
TLS-with-bundled-CA strategy and the redacted-diagnostics rule.

## Phase 6 — Performance

* Per-update sensor cache (`JackeryStatSensor._refresh_cache`).
* Statistic-import dedup (`_stat_import_last_sig`).
* Payload-debug throttle (`PAYLOAD_DEBUG_THROTTLE_SEC`).

## Phase 7 — Localisation

`strings.json` plus per-locale `translations/*.json`. Every string the
user can see is translated; no English fallbacks in DE/ES/FR.

## Phase 8 — Documentation last

Once the code is correct, mirror the contract docs in `docs/` and the
user-facing READMEs (`README.md`, `docs/README.de.md`,
`docs/README.es.md`, `docs/README.fr.md`). The order is intentional:
documentation first creates documentation drift; documentation last
matches the actual behaviour.
