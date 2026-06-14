# Test Kategorien

Kategorie-Definitionen: `source-backed` (direkt aus `source-of-truth/`), `ha-contract` (HA-Lifecycle ohne Protokollwerte), `legacy-regression` (nützlich, nicht autoritativ), `agent-slop` (PR-/Coverage-Fülltest: entfernen oder durch Source-Contract ersetzen).

| Testdatei | Kategorie | Begründung |
|---|---|---|
| `test_api.py` | `source-backed` | Erwartung an Reverse-Engineering-Quelle bzw. Source-of-truth gekoppelt. |
| `test_api_pr_changes.py` | `legacy-regression` | Regressionsschutz ohne direkte Autorität aus source-of-truth. |
| `test_battery_pack_stability.py` | `legacy-regression` | Regressionsschutz ohne direkte Autorität aus source-of-truth. |
| `test_ble_cmd120.py` | `source-backed` | Erwartung an Reverse-Engineering-Quelle bzw. Source-of-truth gekoppelt. |
| `test_ble_frame.py` | `source-backed` | Erwartung an Reverse-Engineering-Quelle bzw. Source-of-truth gekoppelt. |
| `test_ble_pr_changes.py` | `legacy-regression` | Regressionsschutz ohne direkte Autorität aus source-of-truth. |
| `test_ble_transport.py` | `source-backed` | Erwartung an Reverse-Engineering-Quelle bzw. Source-of-truth gekoppelt. |
| `test_ble_update_coalescing.py` | `source-backed` | Erwartung an Reverse-Engineering-Quelle bzw. Source-of-truth gekoppelt. |
| `test_button_new_coverage.py` | `legacy-regression` | Regressionsschutz ohne direkte Autorität aus source-of-truth. |
| `test_client_api_price.py` | `source-backed` | Erwartung an Reverse-Engineering-Quelle bzw. Source-of-truth gekoppelt. |
| `test_code_quality.py` | `legacy-regression` | Regressionsschutz ohne direkte Autorität aus source-of-truth. |
| `test_common.py` | `legacy-regression` | Regressionsschutz ohne direkte Autorität aus source-of-truth. |
| `test_config_flow.py` | `ha-contract` | Home-Assistant Setup/Unload/Flow/Service-Vertrag. |
| `test_coordinator_backoff.py` | `legacy-regression` | Regressionsschutz ohne direkte Autorität aus source-of-truth. |
| `test_ct_stat_accessory_id.py` | `source-backed` | Erwartung an Reverse-Engineering-Quelle bzw. Source-of-truth gekoppelt. |
| `test_diagnostics_local_mqtt.py` | `ha-contract` | Home-Assistant Setup/Unload/Flow/Service-Vertrag. |
| `test_discovery.py` | `legacy-regression` | Regressionsschutz ohne direkte Autorität aus source-of-truth. |
| `test_entity.py` | `legacy-regression` | Regressionsschutz ohne direkte Autorität aus source-of-truth. |
| `test_h_regressions.py` | `legacy-regression` | Regressionsschutz ohne direkte Autorität aus source-of-truth. |
| `test_home_assistant_best_practices.py` | `ha-contract` | Home-Assistant Setup/Unload/Flow/Service-Vertrag. |
| `test_init_local_mqtt_guards.py` | `legacy-regression` | Regressionsschutz ohne direkte Autorität aus source-of-truth. |
| `test_init_pr_changes.py` | `legacy-regression` | Regressionsschutz ohne direkte Autorität aus source-of-truth. |
| `test_integration_lifecycle_contract.py` | `ha-contract` | Home-Assistant Setup/Unload/Flow/Service-Vertrag. |
| `test_local_mqtt.py` | `legacy-regression` | Regressionsschutz ohne direkte Autorität aus source-of-truth. |
| `test_mqtt_protocol_contract.py` | `source-backed` | Erwartung an Reverse-Engineering-Quelle bzw. Source-of-truth gekoppelt. |
| `test_mqtt_stability.py` | `legacy-regression` | Regressionsschutz ohne direkte Autorität aus source-of-truth. |
| `test_power_math.py` | `legacy-regression` | Regressionsschutz ohne direkte Autorität aus source-of-truth. |
| `test_pr_additional_coverage.py` | `agent-slop` | PR-/Coverage-getrieben; besonders kritisch geprüft, nicht autoritativ. |
| `test_pr_binary_sensor_descriptions.py` | `agent-slop` | PR-/Coverage-getrieben; besonders kritisch geprüft, nicht autoritativ. |
| `test_pr_binary_sensor_plug_key.py` | `agent-slop` | PR-/Coverage-getrieben; besonders kritisch geprüft, nicht autoritativ. |
| `test_pr_binary_sensor_setup.py` | `agent-slop` | PR-/Coverage-getrieben; besonders kritisch geprüft, nicht autoritativ. |
| `test_pr_client_init_and_api_counters.py` | `agent-slop` | PR-/Coverage-getrieben; besonders kritisch geprüft, nicht autoritativ. |
| `test_pr_encrypt_mqtt_body.py` | `agent-slop` | PR-/Coverage-getrieben; besonders kritisch geprüft, nicht autoritativ. |
| `test_pr_final_coverage.py` | `agent-slop` | PR-/Coverage-getrieben; besonders kritisch geprüft, nicht autoritativ. |
| `test_pr_init_new_helpers.py` | `agent-slop` | PR-/Coverage-getrieben; besonders kritisch geprüft, nicht autoritativ. |
| `test_pr_legacy_suffix_cleanup.py` | `agent-slop` | PR-/Coverage-getrieben; besonders kritisch geprüft, nicht autoritativ. |
| `test_pr_new_coverage.py` | `agent-slop` | PR-/Coverage-getrieben; besonders kritisch geprüft, nicht autoritativ. |
| `test_pr_portable_button_descriptions.py` | `agent-slop` | PR-/Coverage-getrieben; besonders kritisch geprüft, nicht autoritativ. |
| `test_pr_remaining_coverage.py` | `agent-slop` | PR-/Coverage-getrieben; besonders kritisch geprüft, nicht autoritativ. |
| `test_pr_startup_orchestration.py` | `agent-slop` | PR-/Coverage-getrieben; besonders kritisch geprüft, nicht autoritativ. |
| `test_pr_syntax_regression.py` | `agent-slop` | PR-/Coverage-getrieben; besonders kritisch geprüft, nicht autoritativ. |
| `test_pr_vendor_yaml_smoke.py` | `agent-slop` | PR-/Coverage-getrieben; besonders kritisch geprüft, nicht autoritativ. |
| `test_price_setters.py` | `legacy-regression` | Regressionsschutz ohne direkte Autorität aus source-of-truth. |
| `test_reauth_contract.py` | `ha-contract` | Home-Assistant Setup/Unload/Flow/Service-Vertrag. |
| `test_scripts_gate.py` | `legacy-regression` | Regressionsschutz ohne direkte Autorität aus source-of-truth. |
| `test_select.py` | `legacy-regression` | Regressionsschutz ohne direkte Autorität aus source-of-truth. |
| `test_services.py` | `ha-contract` | Home-Assistant Setup/Unload/Flow/Service-Vertrag. |
| `test_setup_entry_ha.py` | `ha-contract` | Home-Assistant Setup/Unload/Flow/Service-Vertrag. |
| `test_smart_meter.py` | `source-backed` | Erwartung an Reverse-Engineering-Quelle bzw. Source-of-truth gekoppelt. |
| `test_stat_metadata.py` | `source-backed` | Erwartung an Reverse-Engineering-Quelle bzw. Source-of-truth gekoppelt. |
| `test_translations.py` | `ha-contract` | Home-Assistant Setup/Unload/Flow/Service-Vertrag. |
| `test_unload_contract.py` | `ha-contract` | Home-Assistant Setup/Unload/Flow/Service-Vertrag. |
| `test_vendor_yaml.py` | `source-backed` | Erwartung an Reverse-Engineering-Quelle bzw. Source-of-truth gekoppelt. |
| `test_source_backed_contracts.py` | `source-backed` | Erwartung an Reverse-Engineering-Quelle bzw. Source-of-truth gekoppelt. |
