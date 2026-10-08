# Home Assistant schema typing compatibility

The supported minimum remains Home Assistant **2026.9.4**, derived from
`pytest-homeassistant-custom-component` **0.13.367**. This compatibility change
does not modify `.HA_VERSION`, dependency constraints or HACS metadata.

## Exact boundaries

| Contract | Last old release | First new release |
| --- | --- | --- |
| Form and service annotations: `vol.Schema` to `probatio.Schema` | HA 2026.9.4 / pytest plugin 0.13.367 | HA 2026.10.0b0 / pytest plugin 0.13.368 |
| Binary sensor enum definition moves to `binary_sensor.const` | HA 2026.9.4 | HA 2026.10.0b0 |
| Binary sensor device class stops accepting `str` | HA 2022.12.9 | HA 2023.1.0b0; first stable release 2023.1.0 |

The binary sensor errors in the newer stack concern an implicit re-export, not
a newly stricter enum contract. Both tested stacks already require enum members.
The historical pytest-plugin transition for that older contract was 0.12.36
(HA 2022.12.8) to 0.12.37 (HA 2023.1.0b1).

Home Assistant has installed Probatio's Voluptuous runtime alias since
2026.9.0b0. The 2026.9.4 annotations still refer to Voluptuous, so changing
integration imports alone produces nominal-type errors on the minimum stack.

## Compatibility implementation

`schema_compat.Schema` derives the constructor from HA's public
`config_validation.PLATFORM_SCHEMA`. The installed HA annotations give both
checkers the appropriate concrete class. Form schemas use that constructor.
Service schemas are reconstructed at registration from the original definition,
preserving `required` and `extra`; the original markers, defaults and validators
remain in the definition. Construction happens once per registered service.
`Schema` is a constructor value, not an annotation alias.

HA still intentionally exports `BinarySensorDeviceClass` from the public
`binary_sensor` module after relocating its definition. The Mypy override enables
implicit re-exports only for that upstream module. It changes neither integration
typing strictness nor the enum's annotations. The schema/enum implementation adds
no typing ignores, casts, package-wide alias stubs or runtime version branches.

Python 3.14.2 runtime introspection also needs the coordinator's annotation
types to exist outside `TYPE_CHECKING`. Its concrete imports now remain available
to `inspect.signature`, `annotationlib` VALUE evaluation and `get_type_hints`.
Only Ruff's import-relocation rules are exempted for this module; both type
checkers retain their existing rules. `BleFrameObservation` lives alongside
`BleBinaryFrame` in the lightweight BLE codec module and remains re-exported by
the transport module, preserving class identity without eagerly importing the
optional BLE transport.

The local MQTT client's signatures use the same concrete-runtime import policy.
Device-registry ownership checks use `config_entry_id`, available in both pinned
stacks, instead of the deprecated `config_entries` property exposed by the newer
stack's full tests. No supported-version or dependency-baseline changes are needed.

## Validation

On Python 3.14.2 with HA 2026.9.4/plugin 0.13.367 and
HA 2026.10.0b0/plugin 0.13.368:

- Mypy 2.4.0: all 42 integration source files pass. Local commands use
  `--num-workers 0 --no-native-parser` to avoid the container's unavailable parent
  PID during native-parser worker discovery; this changes no typing rules.
- Pyrefly 1.3.2: integration and complete configured project pass with zero errors;
  existing project suppressions remain active; this change adds none.
- 769 affected runtime tests pass on each stack, including 20 schema/enum
  compatibility cases, six coordinator annotation regressions, three local MQTT
  annotation regressions and 62 existing device-registry ownership cases. The
  coordinator cases
  reproduced failures before the fix and verify concrete constructor types,
  every owned method/property, Mock specs, autospec arity, the lazy BLE transport
  import in a fresh interpreter and observation re-export identity.
- Negative probes for a dictionary passed as a form schema, a string returned as
  an enum and a string used as a description's device class each fail under both
  checkers on both stacks. The actual type contracts remain enforced.
- Both stacks use the workflow's exact Python 3.14.2 minimum.
- `pip check`, focused Ruff lint/format and `git diff --check` pass.

The compatibility CI matrix resolves each exact pytest-plugin stack independently.
Only its HA, coverage and plugin requirement rows replace the baseline test pins;
all remaining test requirements retain the repository's source values. The normal
minimum-stack checks and plugin-derived baseline checks remain in place.

An additional exploratory full-suite run on Python 3.14.2 encounters an upstream
Recorder test-fixture issue: the plugin's autospec evaluates HA's
`recorder.migration._find_schema_errors` annotation for its TYPE_CHECKING-only
`Recorder` import. The same failure reproduces on unchanged PR head `1fc48956`.
The alternate-stack full suite also deliberately disagrees with the checked-in
minimum-stack baseline assertion. Neither result is treated as a passing full
suite or suppressed; the compatibility workflow runs the expanded affected-test
selection, and regular CI runs the complete minimum-stack suite on current
Python 3.14.

## Primary sources

- [Probatio runtime migration and alias guarantee](https://developers.home-assistant.io/blog/2026/09/30/probatio-validation-engine/)
- [HA 2026.9.4 flow annotations](https://github.com/home-assistant/core/blob/2026.9.4/homeassistant/data_entry_flow.py)
- [HA 2026.10.0b0 flow annotations](https://github.com/home-assistant/core/blob/2026.10.0b0/homeassistant/data_entry_flow.py)
- [Schema annotation migration commit](https://github.com/home-assistant/core/commit/06ac207c22a8359c3d10f90eac593e60bf9ab78d)
- [Plugin 0.13.367 exact HA pin](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component/blob/0.13.367/requirements_test.txt)
- [Plugin 0.13.368 exact HA pin](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component/blob/0.13.368/requirements_test.txt)
- [New binary sensor public re-export](https://github.com/home-assistant/core/blob/2026.10.0b0/homeassistant/components/binary_sensor/__init__.py)
- [Historical removal of string device classes](https://github.com/home-assistant/core/commit/cb69364ad2f36b68fa5404400b88948e8bdd7173)

GitHub returned HTTP 410 ("This issue was deleted") for issue #469 during this
investigation on 2026-10-07. Its original thread cannot receive an update; the PR
and this document retain the diagnosis and validation evidence.
