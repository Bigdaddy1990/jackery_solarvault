<!--
Thanks for contributing! Before submitting, please confirm the items
below. The repository follows the strict, narrow review style described
in `docs/STRICT_WORK_INSTRUCTIONS.md`.
-->

## Summary

<!-- One paragraph: what changed, why, and what user-visible effect this has. -->

## Checklist

- [ ] I have read `docs/STRICT_WORK_INSTRUCTIONS.md` and the relevant
      contract files in `docs/`.
- [ ] My change does not silently repair one period with another period
      (`docs/DATA_SOURCE_PRIORITY.md`).
- [ ] Unique IDs follow `docs/UNIQUE_ID_CONTRACT.md`.
- [ ] No raw payload, serial number, MQTT password or `<userId>` is
      written to logs or diagnostics on a normal install
      (`docs/MQTT_PROTOCOL.md`).
- [ ] Tests are added/updated to lock down the **correct** behaviour.
      I have not adapted asserts to match wrong output.
- [ ] `pytest tests/` passes locally and in CI.
