# Integration quality scale self-assessment

The [integration quality scale](https://www.home-assistant.io/docs/quality_scale/) officially applies only to core integrations, so this repository does not declare `quality_scale` in `manifest.json`. This is an informal self-assessment so users and contributors know where the integration stands.

**Overall: roughly Bronze, with most Silver rules and some Gold rules met.**

## Bronze
| Rule | Status | Notes |
|---|---|---|
| config-flow | ✅ | Phone → SMS code, with `data_description`. |
| config-flow-test-coverage | ✅ | `tests/test_config_flow.py`: success, every error path with recovery, duplicate, reauth, wrong account. |
| unique-config-entry | ✅ | Unique ID = cloud user id. |
| entity-unique-id | ✅ | `<device_id>_<key>`. |
| has-entity-name | ✅ | |
| runtime-data | ✅ | `entry.runtime_data` holds the coordinator. |
| test-before-setup | ✅ | `async_config_entry_first_refresh`; auth → reauth, other errors → retry. |
| appropriate-polling | ✅ | 30 s; cloud refreshes power ~1/min. |
| common-modules | ✅ | `coordinator.py`, `entity.py`. |
| dependency-transparency | ⚠️ | API client lives in the integration (`api.py`), not in a PyPI library. Acceptable for a custom integration. |
| docs-* (actions, high-level description, installation, removal) | ✅/⚠️ | README covers description, installation and setup. Removal: delete the integration entry, then remove via HACS. No custom actions. |
| entity-event-setup, action-setup | n/a | No custom events/actions. |
| brands | ✅ | Local `brand/icon.png`. |

## Silver
| Rule | Status |
|---|---|
| reauthentication-flow | ✅ (required: 30-day token) |
| entity-unavailable / log-when-unavailable | ✅ (coordinator handles logging; `is_online` drives availability) |
| config-entry-unloading | ✅ |
| action-exceptions | ✅ translated `HomeAssistantError` on switch failure |
| parallel-updates | ✅ |
| integration-owner | ✅ `@sobelman` |
| test-coverage | ⚠️ main paths covered; coverage not measured in CI |
| docs-configuration-parameters, docs-installation-parameters | ✅ |

## Gold (partial)
| Rule | Status |
|---|---|
| diagnostics | ✅ redacted |
| devices | ✅ model, firmware, serial |
| entity-translations, exception-translations | ✅ en + he |
| entity-category, entity-disabled-by-default, entity-device-class | ✅ |
| dynamic-devices / stale-devices | ❌ new devices need a reload; removed devices are not cleaned up |
| reconfiguration-flow | ❌ (remove + re-add to change account) |
| discovery | n/a (cloud only) |
| docs-troubleshooting, docs-supported-devices, docs-known-limitations | ✅ |
| repair-issues | ❌ (reauth notification is used instead) |
