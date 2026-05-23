## Summary

<!-- What changed, why, and what user-visible effect this has. -->

## Checklist

- [ ] I reproduced or verified the issue with code, logs, diagnostics, or payloads.
- [ ] I followed the latest user report over stale comments or documentation.
- [ ] I did not use documentation files as a test oracle.
- [ ] Tests check executable code behavior, not markdown/html wording.
- [ ] Statistic changes keep HTTP polling, MQTT push, BLE transport, app-stat imports, entity-stat imports and repair paths separate.
- [ ] Historical day repair can correct bad Recorder spike rows; live/current imports remain gap-only.
- [ ] `bun run lint` and the relevant `bun run test:unit` selection pass locally.
