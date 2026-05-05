## Mandatory quality checklist

Do not merge or package this integration unless every applicable item is true.

- [ ] I followed `docs/STRICT_WORK_INSTRUCTIONS.md` in order.
- [ ] I traced affected data from raw HTTP/MQTT payload to parser, coordinator,
      entity state, attributes, and Home Assistant metadata.
- [ ] I removed speculative/over-engineered workaround code before adding new
      behavior.
- [ ] I did not add tests that preserve unverified or broken behavior.
- [ ] I did not add migrations, version ladders, or CT-period logic unless this
      PR is explicitly scoped to that and backed by fixtures.
- [ ] Config-flow and service schemas contain only Home Assistant serializable
      validators.
- [ ] No platform module has missing imports or unresolved globals.
- [ ] No async function performs direct blocking file I/O.
- [ ] Unit/source checks pass.
- [ ] Home Assistant fixture tests pass with `pytest-homeassistant-custom-component`.
- [ ] JSON validation and ZIP validation pass.
- [ ] The release ZIP contains no caches, debug logs, or generated files.
