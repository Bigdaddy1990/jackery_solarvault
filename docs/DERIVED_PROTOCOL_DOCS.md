# Derived protocol documentation policy

`source-of-truth/` is the only authoritative location for Jackery protocol
captures, reverse-engineering outputs, endpoint matrices, command catalogs, DTO
field dumps, and protocol helper scripts.

`docs/` must only contain contributor guidance or human-readable summaries that
are derived from `source-of-truth/` and/or runtime code. If a derived summary
conflicts with `source-of-truth/`, update or remove the summary; never treat the
summary as protocol truth.

## Classified files

| Former `docs/` path | Classification | Action |
|---|---|---|
| `docs/Jackery_2.1.1_DEX_Aufschluesselung.md` | Protocol source artifact | Removed from `docs/`; use `source-of-truth/Jackery_2.1.1_DEX_Aufschluesselung.md`. |
| `docs/Jackery_2.1.1_RE_Crypto_and_DTOs.md` | Protocol source artifact | Removed from `docs/`; use `source-of-truth/Jackery_2.1.1_RE_Crypto_and_DTOs.md`. |
| `docs/Jackery_2.1.1_RE_Documentation.md` | Protocol source artifact | Removed from `docs/`; use `source-of-truth/Jackery_2.1.1_RE_Documentation.md`. |
| `docs/Jackery_2.1.1_RE_Supplement.md` | Protocol source artifact | Removed from `docs/`; use `source-of-truth/Jackery_2.1.1_RE_Supplement.md`. |
| `docs/Jackery_2.1.1_Stats_und_Trends.md` | Protocol source artifact | Removed from `docs/`; use `source-of-truth/Jackery_2.1.1_Stats_und_Trends.md`. |
| `docs/jackery_auth.py` | Protocol helper source artifact | Removed from `docs/`; use `source-of-truth/jackery_auth.py`. |
| `docs/jackery_command_catalog_v2.html` | Generated protocol source artifact | Removed from `docs/`; use `source-of-truth/jackery_command_catalog_v2.html`. |
| `docs/jackery_complete_reference.json` | Single technical source of truth | Authoritative JSON for endpoint, MQTT, command, and protocol counters. |
| `docs/jackery_entity_field_candidates_v2.html` | Generated protocol source artifact | Removed from `docs/`; use `source-of-truth/jackery_entity_field_candidates_v2.html`. |
| `docs/jackery_entity_field_candidates_v2.json` | Generated protocol source artifact | Removed from `docs/`; use `source-of-truth/jackery_entity_field_candidates_v2.json`. |
| `docs/jackery_ha_extraction_v2.html` | Generated protocol source artifact | Removed from `docs/`; use `source-of-truth/jackery_ha_extraction_v2.html`. |
| `docs/jackery_ha_extraction_v2.json` | Generated protocol source artifact | Removed from `docs/`; use `source-of-truth/jackery_ha_extraction_v2.json`. |
| `docs/jackery_http_api_endpoints_v2.html` | Generated protocol source artifact | Removed from `docs/`; use `source-of-truth/jackery_http_api_endpoints_v2.html`. |
| `docs/jackery_http_model_fields_v2.html` | Generated protocol source artifact | Removed from `docs/`; use `source-of-truth/jackery_http_model_fields_v2.html`. |
| `docs/jackery_smali_home_assistant_report.html` | Generated protocol source artifact | Removed from `docs/`; use `source-of-truth/jackery_smali_home_assistant_report.html`. |
| `docs/jackery_smali_home_assistant_report.md` | Generated protocol source artifact | Removed from `docs/`; use `source-of-truth/jackery_smali_home_assistant_report.md`. |
| `docs/jackery_smali_home_assistant_report_v2.html` | Generated protocol source artifact | Removed from `docs/`; use `source-of-truth/jackery_smali_home_assistant_report_v2.html`. |
| `docs/jackery_smali_home_assistant_report_v2.md` | Generated protocol source artifact | Removed from `docs/`; use `source-of-truth/jackery_smali_home_assistant_report_v2.md`. |
| `docs/REFERENCE_COVERAGE.md` | Derived implementation coverage summary | Kept in `docs/`; it must cite `source-of-truth/` for protocol facts. |
| `docs/WIRING_REFERENCE.md` | Derived code-to-protocol wiring summary | Kept in `docs/`; it must cite `source-of-truth/` for protocol facts. |
| `docs/REPAIR_ROADMAP.md` | Contributor planning document | Kept in `docs/`; it is not a protocol source. |

## Contributor documentation boundary

Contributor and agent instructions such as `docs/AGENTS.md` remain in `docs/`.
They define workflow, quality gates, and review expectations only. They must not
introduce or override Jackery protocol values; protocol values come from
`source-of-truth/` and derived summaries are regenerated or edited from there.
