# Strict work instructions

## Repair the foundation first

Before adding entities, polish, or visualizations, every contributor
verifies that the **raw HTTP/MQTT payload** is parsed correctly into the
documented coordinator data layout. The mental model is:

```
parser -> coordinator data -> entity native value
```

If a sensor shows the wrong number, the bug is in one of those three
layers; never patch over it at the entity layer with a cross-period
repair. Use the payload-debug JSONL log
(`custom_components.jackery_solarvault.payload_debug=debug`) to compare
the raw frame against the parsed result.

## Do not write tests that preserve broken behavior

A failing test must drive a code fix. Adapting the assertion to match
the wrong output is forbidden. When a real-world payload reveals a
parser bug, write a regression test that locks down the **correct**
parsed shape, then fix the parser.

## TLS / certificates

The integration ships a single trust anchor file
``custom_components/jackery_solarvault/jackery_ca.crt``. This is **not**
a fallback to insecure TLS; it is a documented, explicit trust override
for the specific MQTT broker ``emqx.jackeryapp.com``. The reasons,
recorded here so the choice does not drift again:

1. **CA bundling is intentional.** Jackery's MQTT broker presents a
   certificate chain anchored at a Jackery-internal CA, not a public CA
   in the system trust store. Without bundling the CA, every Home
   Assistant installation would fail TLS verification on the very first
   MQTT connect.
2. **``VERIFY_X509_STRICT`` is intentionally cleared on Python ≥3.10 /
   OpenSSL 3.x.** Jackery's broker certificate is missing the
   ``Authority Key Identifier`` extension that strict X.509 validation
   in modern OpenSSL enforces. Disabling only this single strict flag
   preserves chain verification, hostname verification
   (``check_hostname = True``) and signature verification
   (``verify_mode = CERT_REQUIRED``). The integration logs the cleared
   flag visibly on every connect and reports it in diagnostics as
   ``"tls_x509_strict_disabled": true``.
3. **No silent insecure fallback.** ``tls_insecure=True``,
   ``ssl.CERT_NONE`` and any "disabled after strict TLS failure"
   automatic downgrade pattern are forbidden and enforced by
   ``test_mqtt_tls_uses_verified_jackery_ca_without_insecure_fallback``
   in ``tests/test_code_quality.py``. TLS failures stay visible to the
   user as real exceptions.
4. **Diagnostics.** ``mqtt_push.diagnostics()`` exposes
   ``tls_custom_ca_loaded``, ``tls_certificate_source`` and
   ``tls_x509_strict_disabled`` so the user can inspect the actual TLS
   posture without enabling debug logging.

## Privacy / diagnostics

* The diagnostics export anonymises raw map keys (``device_id``,
  serials) and topic paths. ``hb/app/<userId>/`` becomes
  ``hb/app/**REDACTED**/``.
* Raw payloads are only written to
  ``jackery_solarvault_payload_debug.jsonl`` when the dedicated DEBUG
  logger is enabled by the user. The file is throttled
  (`PAYLOAD_DEBUG_THROTTLE_SEC`) and content-deduplicated.
