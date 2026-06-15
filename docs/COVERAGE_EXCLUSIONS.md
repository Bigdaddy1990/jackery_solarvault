# Coverage exclusions

Current `custom_components/jackery_solarvault/` `# pragma: no cover` inventory:

| File | Line | Classification | Reason |
| --- | ---: | --- | --- |
| `custom_components/jackery_solarvault/client/ble.py` | 753 | Import/module namespace cleanup | Import-time `del os` keeps the entropy-source import out of the module namespace and is not a runtime branch. |

Reviewed 2026-06-14: former exclusions in `client/ble_transport.py` were testable defensive callback/error paths and now have unit coverage instead of coverage pragmas.
