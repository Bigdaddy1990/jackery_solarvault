# CI dependency sources and supported test environments

Python 3.14.2 or newer is required by the minimum Home Assistant release;
CI installs the current Python 3.14 patch release.

- `custom_components/jackery_solarvault/manifest.json` owns integration runtime
  requirements. `requirements.txt` mirrors it through `scripts.sync_requirements`.
- `requirements-test.txt` owns test dependency version constraints.
  `scripts.sync_requirements.ALWAYS_TEST` owns only the package inventory; it
  must preserve intentional constraints and Dependabot updates.
- The `pyproject.toml` development group retains the same Home Assistant and
  pytest integration-plugin lower bounds.

The minimum Home Assistant version remains 2026.9.4. Its matching pytest plugin
is 0.13.367; older plugin releases cannot provide this supported baseline.
Coverage 7.10.6 or newer supplies the Python 3.14-compatible test baseline.
These lower bounds prevent pip from exploring obsolete plugin/Coverage builds
while resolving the current Home Assistant stack. They do not pin Home Assistant
to a stable release or prohibit a newer compatible pytest plugin.

Coverage compatibility: https://coverage.readthedocs.io/en/7.10.6/changes.html

`multidict` follows the Home Assistant/aiohttp constraints. The runtime minimum
for `segno` (1.6.6) remains in the manifest. Home Assistant supplies `cryptography`.
`bleak-retry-connector` is supplied by Home Assistant's `bluetooth` integration,
which is already listed in `after_dependencies`. Home Assistant installs the
requirements of these optional integrations even when they are not configured,
so SolarVault must not declare a separate connector version constraint.

Source: https://developers.home-assistant.io/docs/creating_integration_manifest/#after-dependencies

## Native Home Assistant typing

`homeassistant-stubs` is deprecated. Its final 2026.10.0 release contains no
stubs; the maintainer directs consumers to Home Assistant's native `py.typed`
information. It is therefore removed from the test inventory and development
group, rather than pinned to the older, less precise stubs.

Source: https://github.com/KapJI/homeassistant-stubs/blob/main/README.md

The integration constructs flow schemas with Home Assistant's installed schema
class. This keeps both runtime validation and type checking aligned with
Home Assistant's voluptuous-to-probatio transition. Service schema conversion
retains the original fields, `required` and `extra` settings. It does not relax
validation. Compatibility re-exports remain usable at runtime while enum type
imports use the owning module.

Run checks against both the minimum Home Assistant version and the environment
selected by the current test requirements. Do not infer current-version typing
success from an older stub-backed run. For coverage, the reusable CI workflow
compares the XML **line-rate** with 85%; the combined line/branch percentage is
a different metric.
