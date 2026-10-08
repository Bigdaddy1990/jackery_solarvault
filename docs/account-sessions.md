# SolarVault account sessions and local access

Verified against PR #476 commit `7abd5ce9b0541ac62f44724ca9fe7428e5e5bd2d`
on 8 October 2026, including its merged `main` revision. This is a source and code audit; simultaneous app/device
operation was not tested with a live SolarVault account.

## Account ownership and sharing

Use the Jackery account that added the SolarVault system. The
[Jackery App User Manual, EN V1.0](https://cdn.shopify.com/s/files/1/0733/5644/3969/files/Jackery_App_User_Manual_EN_V1.0_-20260422.pdf?v=1780553558),
section 7.2, printed page 39 (PDF page 42), limits sharing to portable power
stations and explicitly excludes SolarVault systems. A second account cannot
obtain SolarVault access through that sharing feature. Jackery's
[portable-power-station sharing FAQ](https://faq.jackery.com/hc/en-us/articles/52357750587419-How-can-I-share-my-portable-power-station-with-multiple-devices-using-the-Jackery-App)
does not establish SolarVault eligibility.

[Issue #412](https://github.com/Bigdaddy1990/jackery_solarvault/issues/412)
reports the manual's restriction.
[Issue #43](https://github.com/Bigdaddy1990/jackery_solarvault/issues/43)
reports Android app disconnections, and the
[maintainer's earlier reply](https://github.com/Bigdaddy1990/jackery_solarvault/issues/43#issuecomment-4441828520)
already withdraws the second-account workaround. Generic sharing services
and the “Accept a shared device” form expose API operations for eligible
devices; they do not override the SolarVault restriction.

## QR codes, account binding and sharing

These operations use the authenticated cloud HTTP API. They are separate
from MQTT bootstrap hydration, BLE device keys and the local-MQTT device
token. The API and coordinator wrappers do not check model eligibility;
their availability is not evidence that Jackery allows a SolarVault share.

| HA operation | Actual request and behavior | What it does not establish |
| --- | --- | --- |
| `get_share_qr_code` | Uses the selected device to choose a loaded account, then sends `GET /v1/device/bind/qrcode` without device parameters. Returns `qr_code_id` and `user_id`; attempts to display a PNG QR code in an HA persistent notification. | A device-specific permission, an independent login or support for SolarVault sharing. |
| `accept_shared_device` / “Accept a shared device” | Sends `POST /v1/device/accept_bind` with `devId` and `qrCodeId` through the configured account. The coordinator requests a refresh; the form also schedules HTTP discovery to surface any newly accessible device. | A camera scanner, QR-image import, local onboarding or an eligibility override. The form does not reload the integration. |
| `bind_device` | Sends `POST /v1/device/bind` with `bindKey`, `devId`, `guid` and `timezoneOffset`, then requests a refresh. The service requires an existing device to select the account and separately supplied target provisioning values. | Obtaining those provisioning values, provisioning Wi-Fi on a factory-fresh unit, sharing ownership or creating a second session. |
| `unbind_device` | Sends `POST /v1/device/unbind` with `deviceId`, then requests a refresh. | Preserving access while solving an app/HA session conflict. It removes the account binding. |

The QR renderer encodes only the raw `qrCodeId` using `segno`. Its scan
format is reconstructed from the app contract, not vendor-documented or
live-validated by this audit. `userId` and `devId` are not embedded in the
image. A rendering failure leaves the service response available. Do not
treat the QR image or a successful QR-code request as confirmation that
the complete sharing workflow will work for a given model or account.

The HA form takes the device ID and QR-code ID as text. There is no
implemented camera scan or image decoder. Existing tests check request
fields, QR rendering/notification behavior, error handling and refresh
scheduling with mocked cloud responses; they do not prove backend sharing
eligibility or app scan compatibility.

The service actions request an ordinary coordinator refresh; they do not
force immediate HTTP rediscovery or reload. Newly accessible devices can
therefore appear only after subsequent rediscovery. The form explicitly
schedules discovery. `list_shared_devices` returns both received and outgoing
sharing records (`receive` and `share`), not a receive-only discovery list.

For a new SolarVault, complete initial account and Wi-Fi setup in the Jackery
app, then let HA discover the device using that owning account. The bind
endpoint wrapper does not implement the app's BLE Wi-Fi provisioning
exchange or derive its `bindKey` and `guid`. Do not unbind a working system
to try to obtain a separate HA session.

Implementation references: [`services.py`](../custom_components/jackery_solarvault/services.py)
(`_render_share_qr_png_data_uri`, `_notify_share_qr_code`, bind/share handlers),
[`config_flow.py`](../custom_components/jackery_solarvault/config_flow.py)
(`async_step_accept_shared`),
[`coordinator.py`](../custom_components/jackery_solarvault/coordinator.py)
(bind/share forwarding and `async_schedule_discovery_refresh`), and
[`client/api.py`](../custom_components/jackery_solarvault/client/api.py)
(the corresponding HTTP wrappers).

## What the current integration supports

| Path | Supported behavior | Limit for simultaneous app use |
| --- | --- | --- |
| Same account, cloud HTTP and cloud MQTT | Login, discovery, polling, MQTT and recovery attempts | No verified independent second session; repeated logins can replace sessions. |
| Saved MQTT bootstrap and discovery cache | Restore cached identity/keys and start transports sooner after a restart | No saved HTTP session token; cloud authentication still runs. |
| Optional local MQTT | Receive device telemetry and send supported commands through the configured broker | Enabling it does not disable cloud HTTP or cloud MQTT in this integration. |
| Optional BLE | Local communication with a reachable device and a usable cached/discovered Bluetooth key | No separate account-free setup; simultaneous BLE connections with the app are unverified. |
| Second account with device sharing | Available only for models supported by Jackery's sharing feature | SolarVault is explicitly excluded by the app manual. |

There is no proven reliable simultaneous cloud-app/Home Assistant mode in
this integration. Recovery is best effort: MQTT reconnect backoff limits
repeated connection attempts, while HTTP authentication failures can trigger
a fresh login. A fresh login may replace the other client's session again.
Opening the app briefly is therefore not guaranteed to cause only a short
MQTT pause, and HTTP data is not guaranteed to remain available either.

### Code evidence

All paths below refer to the audited commit linked above:

- [`config_flow.py`](../custom_components/jackery_solarvault/config_flow.py):
  `_async_route_discovery_to_user`, `async_step_mqtt`, `async_step_bluetooth`,
  `async_step_dhcp` and `async_step_zeroconf` lead to `async_step_user`, which
  calls `async_login`. Discovery is not local-only onboarding.
- [`__init__.py`](../custom_components/jackery_solarvault/__init__.py):
  `_async_authenticate_api_layer` performs cloud login even after MQTT
  bootstrap hydration. `async_setup_entry` also schedules
  `_async_prepare_primary_http_from_cache` during cache-backed startup.
- [`client/api.py`](../custom_components/jackery_solarvault/client/api.py):
  `hydrate_mqtt_session` restores `user_id`, `seed_b64` and `mac_id`, not the
  HTTP token. `_relogin_and_retry_request` can perform a full login after a
  rejected HTTP session. `_derive_mqtt_credentials` generates the
  account-bound cloud client ID `userId@APP`; a different `mac_id` changes
  the username but does not produce a separate client ID.
  `async_get_generated_jwt` itself uses authenticated HTTP and is not called
  by onboarding or the session-recovery paths; it is not an implemented
  account-free login or concurrency mechanism.
- [`client/mqtt_push.py`](../custom_components/jackery_solarvault/client/mqtt_push.py):
  `_async_open_session` passes that client ID unchanged to the cloud broker.
  MQTT 3.1.1 [§3.1.4, MQTT-3.1.4-2](https://docs.oasis-open.org/mqtt/mqtt/v3.1.1/os/mqtt-v3.1.1-os.html)
  requires replacement of an existing connection with the same client ID.
  A collision is therefore a protocol-level risk, not proof of the exact
  behavior of every current Jackery app/backend version.
- [`coordinator.py`](../custom_components/jackery_solarvault/coordinator.py):
  `_pause_mqtt_after_auth_failure` invalidates the HTTP token and MQTT seed
  for later HTTP refresh. `device_bluetooth_key` uses `bluetoothKey` from device
  or system discovery metadata. Local MQTT activity does not opt out of the
  cloud connection lifecycle.

Reusing bootstrap data, extracting an app token or changing the cloud MQTT
client ID has not been established as a supported concurrency solution.
This correction does not change identifiers, authentication or retry behavior.

## Separate official local-MQTT option

For supported SolarVault3 Series devices and firmware, Jackery documents a
device-to-local-broker path in its
[official MQTT integration](https://github.com/Jackery-Official/jackery#2-prerequisites).
Its instructions require Home Assistant's MQTT integration, an accessible
broker and a SolarVault configured in the Jackery app under
**Device Details → Settings → MQTT** (app version later than 2.0.0).
The HA form uses the device serial number and the device token generated
on that MQTT page, rather than Jackery cloud email/password.

This is a separate integration, not a mode of `jackery_solarvault`. It avoids
adding an HA cloud-account login through the documented local setup. It is
an option when retaining app access matters and local MQTT features meet
your needs; simultaneous operation and feature parity with this integration
have not been validated by this audit. Cloud history, statistics and other
cloud services must not be assumed available through the local path.

To evaluate that path, disable this account-based integration first, follow
the vendor's broker/SN/token setup, then check local HA updates while using
the app. Keep broker passwords and device tokens private. Do not operate
competing HA command paths during the check.

## Troubleshooting

1. If disconnections coincide with phone logins, disable this integration
   before signing in to the app. Conversely, sign out of the app before
   enabling this integration again to check HA access on its own. Closing
   the app window alone does not establish that its session ended.
2. Check logs/diagnostics for HTTP 401 or authentication failures and MQTT
   authentication rejections/backoff. Also check network connectivity,
   broker credentials and device availability; not every disconnection is
   an account conflict. Avoid repeated reauthentication as a concurrency fix.
3. Do not unbind/rebind the SolarVault or create a second account to share
   it. Those actions do not solve the documented sharing restriction.
4. Local MQTT/BLE can provide local data where configured, but their presence
   here does not guarantee preservation of the app's cloud session. For a
   setup without an additional HA cloud login, assess the separate official
   local-MQTT option above.

Any future claim of reliable simultaneous access needs a supported backend
or device procedure and an end-to-end check covering HA restarts, app login,
session expiry and command routing. The current code audit establishes none.
