# MQTT protocol — Jackery SolarVault

The integration speaks MQTT to ``emqx.jackeryapp.com:8883`` over TLS.
Connection parameters and message types are captured from the Jackery
mobile app and refreshed periodically via the same login endpoint
(`/v1/user/login`).

## Topics

* Subscribed: `hb/app/<userId>/device,alert,config,notice` — telemetry,
  alarms, configuration changes, notices.
* Published: `hb/cloud/<userId>/device` — control commands and
  configuration writes.

## Message types

Captured from real devices and recorded in the version-controlled fixture
suite:

* `UploadIncrementalCombineData` — incremental power/SOC telemetry.
* `UploadCombineData` — full snapshot, used on connect.
* `DevicePropertyChange` — single-property updates.
* `UploadSubDeviceIncrementalProperty` — CT phase frames.
* `UploadSubDeviceGroupProperty` — battery-pack and CT group state.
* `UploadWeatherPlan` — weather plan updates.

## Diagnostics privacy

The integration's diagnostics export and `mqtt_status` payload only emit
**redacted** topic paths and counters. The Jackery `<userId>` portion of
`hb/app/<userId>/...` topics is replaced with `**REDACTED**` so an
exported diagnostics ZIP does not leak the account identifier through
the topic string.

Concretely, the diagnostics show a topic such as
`hb/app/**REDACTED**/device` instead of the raw subscription path, plus
connection counters, last-message timestamps, last-publish timestamps and
**dropped-message counters** — never raw payloads, never serial numbers,
never coordinates, never the MQTT username/password.

Raw payloads are only written to `/config/jackery_solarvault_payload_debug.jsonl`
when `custom_components.jackery_solarvault.payload_debug` is set to `debug`,
and that file is opt-in for the user (see `DATA_SOURCE_PRIORITY.md`).

Per-channel records in `jackery_solarvault_payload_debug.jsonl` are
throttled to one record per `PAYLOAD_DEBUG_THROTTLE_SEC` (60 s default)
per `(kind, topic, messageType)` combination. The first occurrence of
each new combination is always written immediately; identical follow-ups
within the throttle window are skipped.

## TLS

The MQTT TLS context verifies the broker certificate chain actively. The
integration ships ``custom_components/jackery_solarvault/jackery_ca.crt``
as a documented trust anchor for ``emqx.jackeryapp.com`` because Jackery
does not sign the broker certificate with a public CA.

On Python ≥3.10 / OpenSSL 3.x the integration explicitly clears the
``VERIFY_X509_STRICT`` flag because the broker certificate is missing
the ``Authority Key Identifier`` extension that strict validation
enforces. ``check_hostname = True`` and ``verify_mode = CERT_REQUIRED``
remain active; chain, hostname and signature verification still happen.

There is no automatic fallback to ``tls_insecure`` or ``CERT_NONE`` —
TLS failures stay visible. Diagnostics fields ``tls_custom_ca_loaded``,
``tls_certificate_source``, ``tls_x509_strict_disabled`` and the
coordinator's ``diag["tls_certificate_verification"] = "enabled"`` make
the live posture inspectable.
