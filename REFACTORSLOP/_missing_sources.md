# Truly-missing symbol sources (from backups)

## __init__.py :: _async_authenticate
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_recovery', 'pre_reconcile'])
- backup lines: 165-195 (31 lines)

```python
async def _async_authenticate(  # noqa: RUF067, RUF100
    hass: HomeAssistant, entry: JackeryConfigEntry
) -> JackeryApi:
    """Authenticate to the Jackery cloud using credentials from the config entry and return an authenticated API client.

    Returns:
        JackeryApi: An authenticated API client ready for use.

    Raises:
        ConfigEntryAuthFailed: If the provided credentials are rejected (triggers re-auth flow).
        ConfigEntryNotReady: If the Jackery cloud cannot be reached (setup should be retried later).
    """  # noqa: E501, RUF100
    session = async_get_clientsession(hass)
    api = JackeryApi(
        session=session,
        account=str(entry.data.get(CONF_USERNAME, "")).strip(),
        password=str(entry.data.get(CONF_PASSWORD, "")).strip(),
        mqtt_mac_id=entry.data.get(CONF_MQTT_MAC_ID),
        region_code=entry.data.get(CONF_REGION_CODE),
    )
    try:
        await api.async_login()
    except JackeryAuthError as err:
        raise ConfigEntryAuthFailed(  # noqa: TRY003
            f"Jackery login rejected the credentials: {err}"
        ) from err
    except JackeryError as err:
        raise ConfigEntryNotReady(  # noqa: TRY003
            f"Cannot reach Jackery cloud right now: {err}"
        ) from err
    return api
```

## client/api.py :: JackeryApi._decode_response_json
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 257-259 (4 lines)

```python
@staticmethod
def _decode_response_json(body_bytes: bytes) -> Any:  # noqa: ANN401
    """Decode JSON from a response body that was read exactly once."""
    return json.loads(body_bytes)
```

## client/api.py :: JackeryApi._is_transient_http_status
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 267-269 (4 lines)

```python
@staticmethod
def _is_transient_http_status(status: int) -> bool:
    """Return True for server-side statuses that are safe to retry."""
    return 500 <= status < 600  # noqa: PLR2004
```

## client/api.py :: JackeryApi._request_json_with_retry
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 271-311 (41 lines)

```python
async def _request_json_with_retry(
    self,
    method: str,
    path: str,
    request: Callable[[], Awaitable[tuple[int, dict[str, Any]]]],
) -> tuple[int, dict[str, Any]]:
    """Run one JSON HTTP request with bounded transient retry/backoff."""
    for attempt in range(1, _HTTP_RETRY_ATTEMPTS + 1):
        try:
            status, data = await request()
        except (TimeoutError, aiohttp.ClientConnectionError) as err:
            if attempt >= _HTTP_RETRY_ATTEMPTS:
                raise
            delay = _HTTP_RETRY_BACKOFF_SEC[attempt - 1]
            _LOGGER.debug(
                "Jackery %s %s transient %s on attempt %d/%d; retrying in %.1fs",
                method,
                path,
                type(err).__name__,
                attempt,
                _HTTP_RETRY_ATTEMPTS,
                delay,
            )
            await asyncio.sleep(delay)
            continue
        if not self._is_transient_http_status(status):
            return status, data
        if attempt >= _HTTP_RETRY_ATTEMPTS:
            return status, data
        delay = _HTTP_RETRY_BACKOFF_SEC[attempt - 1]
        _LOGGER.debug(
            "Jackery %s %s HTTP %d on attempt %d/%d; retrying in %.1fs",
            method,
            path,
            status,
            attempt,
            _HTTP_RETRY_ATTEMPTS,
            delay,
        )
        await asyncio.sleep(delay)
    raise JackeryApiError(f"{method} {path} retry loop exhausted")  # noqa: TRY003
```

## client/api.py :: JackeryApi._truncated_response_text
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 262-264 (4 lines)

```python
@staticmethod
def _truncated_response_text(body_bytes: bytes) -> str:
    """Return bounded raw response text for diagnostics."""
    return body_bytes[:HTTP_RAW_TEXT_LIMIT].decode("utf-8", errors="replace")
```

## client/api.py :: JackeryApi.async_set_max_power
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_recovery', 'pre_reconcile'])
- backup lines: 1735-1760 (26 lines)

```python
async def async_set_max_power(self, device_id: str | int, max_power: int) -> bool:
    """Set the device's maximum allowed power using the experimental max-power endpoint.

    Validates that `max_power` is an integer greater than or equal to 0 before sending the request.

    Parameters:
        device_id (str | int): Device identifier (serial or numeric id) used by the backend.
        max_power (int): Desired maximum power in watts; must be an integer greater than or equal to 0.

    Returns:
        bool: `True` if the backend acknowledged success (truthy `FIELD_DATA`), `False` otherwise.

    Raises:
        JackeryApiError: If `max_power` is invalid or the API call fails.
    """  # noqa: E501, RUF100
    if not isinstance(max_power, int) or isinstance(max_power, bool) or max_power < 0:
        raise JackeryApiError("max_power must be a non-negative integer")  # noqa: TRY003
    data = await self._post_form(
        MAX_POWER_SAVE_PATH,
        {FIELD_MAX_POWER: max_power, FIELD_DEVICE_ID: str(device_id)},
    )
    return bool(data.get(FIELD_DATA))
```

## client/ble_transport.py :: JackeryBleListener._mark_property_query_started
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 287-300 (14 lines)

```python
def _mark_property_query_started(
    self,
    device_id: str,
    cmd: int,
    current_started_at: datetime | None,
) -> datetime | None:
    """Record the request timestamp for BLE property-query replies."""
    if cmd != 106 or current_started_at is not None:  # noqa: PLR2004
        return current_started_at
    started_at = datetime.now()
    self._pending_property_query_starts.setdefault(device_id, deque(maxlen=4)).append(
        started_at
    )
    return started_at
```

## client/ble_transport.py :: JackeryBleListener.async_ensure_connected
- source backup: `pre_reconcile` (also in ['pre_recovery', 'pre_reconcile'])
- backup lines: 253-290 (38 lines)

```python
async def async_ensure_connected(
    self,
    device_id: str,
    *,
    timeout_sec: float,
) -> bool:
    """Wait until a BLE client is available for ``device_id``.

    The listener reconnects in the background after disconnects. A
    user-triggered service call may land during that reconnect window;
    waiting here keeps the service action from failing immediately while
    preserving the fast MQTT fallback path for normal setters.
    """
    if device_id in self._clients:
        return True
    address = self._device_addresses.get(device_id) or self._ble_address_resolver(
        device_id
    )
    if address is None:
        return False
    self._device_addresses.setdefault(device_id, address)

    loop = asyncio.get_running_loop()
    deadline = loop.time() + max(0.0, timeout_sec)
    while not self._stop_event.is_set():
        if device_id in self._clients:
            return True
        task = self._connections.get(device_id)
        if task is None or task.done():
            self._connections[device_id] = self._hass.async_create_background_task(
                self._async_run_connection(device_id, address),
                name=f"jackery_ble_{device_id}",
            )
        remaining = deadline - loop.time()
        if remaining <= 0:
            break
        await asyncio.sleep(min(0.2, remaining))
    return device_id in self._clients
```

## client/local_mqtt.py :: JackeryLocalMqttClient._async_consume_session
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 206-244 (39 lines)

```python
async def _async_consume_session(
    self,
    client: MQTTClient,
    mqtt_error_cls: type[Exception],
) -> None:
    """Mark the session connected, subscribe, then dispatch incoming messages.

    Split out of :meth:`_async_run_session` so the surrounding ``try`` stays
    small. A subscribe failure records the error and returns, which unwinds
    the caller's ``async with`` (disconnecting) and runs its ``finally``.

    Parameters:
        client: The connected aiomqtt client.
        mqtt_error_cls: The lazily imported ``aiomqtt.MqttError`` class to
            catch subscribe failures (passed in to avoid re-importing).
    """
    self._client = client
    self._connected = True
    self._last_connect_at = self._utc_now_iso()
    self._last_error = None
    self._connected_event.set()
    _LOGGER.info(
        "Jackery local MQTT connected to %s:%s; subscribing %r",
        self._host,
        self._port,
        self._topic_filter,
    )
    try:
        await client.subscribe(self._topic_filter, qos=0)
    except mqtt_error_cls as err:
        self._last_error = f"subscribe failed: {err}"
        _LOGGER.warning(
            "Jackery local MQTT subscribe failed for %r: %s",
            self._topic_filter,
            err,
        )
        return
    async for message in client.messages:
        self._handle_message(str(message.topic), message.payload)
```

## client/third_party_mqtt_codec.py :: _split_iv_envelope
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 10-14 (5 lines)

```python
def _split_iv_envelope(envelope: bytes) -> tuple[bytes, bytes]:
    """Split ``iv || ciphertext`` and validate the envelope shape."""
    if len(envelope) <= BLE_AES_IV_LEN:
        raise ValueError("missing third-party MQTT IV envelope")  # noqa: TRY003
    return envelope[:BLE_AES_IV_LEN], envelope[BLE_AES_IV_LEN:]
```

## coordinator.py :: JackerySolarVaultCoordinator._async_retry_after_invalid_discovery_devices
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 6134-6167 (34 lines)

```python
async def _async_retry_after_invalid_discovery_devices(
    self,
    invalid_device_ids: list[str],
) -> dict[str, dict[str, Any]] | None:
    """Drop persistently invalid discovery IDs and publish retry data."""
    if not invalid_device_ids:
        return None
    # code=20000 can be transient (maintenance window, firmware update,
    # or a brief cloud hiccup). Only remove a device from _device_index
    # after two consecutive failures so a single bad response does not
    # permanently orphan the device for the HA session.
    newly_persistent = []
    for dev_id in invalid_device_ids:
        prior_count = self._invalid_device_id_counts.get(dev_id, 0) + 1
        self._invalid_device_id_counts[dev_id] = prior_count
        if prior_count >= 2:  # noqa: PLR2004
            newly_persistent.append(dev_id)
    if not newly_persistent:
        recovered = set(self._invalid_device_id_counts) - set(invalid_device_ids)
        for dev_id in recovered:
            self._invalid_device_id_counts.pop(dev_id, None)
        return None

    _LOGGER.info(
        "Jackery: dropping %d device id(s) from discovery after "
        "repeated code=20000 errors and retrying",
        len(newly_persistent),
    )
    for dev_id in newly_persistent:
        self._device_index.pop(dev_id, None)
        self._invalid_device_id_counts.pop(dev_id, None)
    if not self._device_index:
        await self.async_discover()
    return await self._async_update_data(_retry_discovery_once=False)
```

## coordinator.py :: JackerySolarVaultCoordinator._encode_third_party_mqtt_secrets
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 4143-4178 (36 lines)

```python
def _encode_third_party_mqtt_secrets(
    self, device_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """Return ``body`` with userName/password/token AES-encoded for the wire.

    The firmware expects these three secrets AES-CBC-PKCS7 encoded with the
    device ``bluetoothKey``; plaintext is rejected. Non-secret fields
    (enable/ip/port) are sent as-is. Falls back to the raw value with a
    warning when no usable 16-byte key is available, so the call degrades
    gracefully instead of raising.
    """
    secret_fields = (
        FIELD_THIRD_PARTY_MQTT_USERNAME,
        FIELD_THIRD_PARTY_MQTT_PASSWORD,
        FIELD_THIRD_PARTY_MQTT_TOKEN,
    )
    if not any(body.get(field) for field in secret_fields):
        return body
    from .client.ble import BLE_AES_IV_LEN  # noqa: PLC0415

    key = self.device_bluetooth_key(device_id)
    if key is None or len(key) != BLE_AES_IV_LEN:
        _LOGGER.warning(
            "Jackery third-party MQTT: no 16-byte bluetoothKey for %s; "
            "sending credentials unencrypted (firmware will likely reject)",
            device_id,
        )
        return body
    from .client.third_party_mqtt_codec import encode_third_party_mqtt_field  # noqa: I001, PLC0415

    encoded = dict(body)
    for field in secret_fields:
        value = encoded.get(field)
        if isinstance(value, str) and value:
            encoded[field] = encode_third_party_mqtt_field(value, key)
    return encoded
```

## coordinator.py :: JackerySolarVaultCoordinator._entity_source_priority
- source backup: `pre_reconcile` (also in ['pre_recovery', 'pre_reconcile'])
- backup lines: 9147-9149 (4 lines)

```python
@staticmethod
def _entity_source_priority(reset_period: str, date_type: str) -> int:
    """Return priority for duplicate buckets within the same period."""
    return 1 if reset_period == date_type else 0
```

## coordinator.py :: JackerySolarVaultCoordinator._find_list_for_key
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_recovery', 'pre_reconcile'])
- backup lines: 2214-2232 (20 lines)

```python
@staticmethod
def _find_list_for_key(
    obj: Any,  # noqa: ANN401, RUF100
    key: str,
) -> list[dict[str, Any]] | None:
    """Find a nested list of dicts under a key such as batteryPacks."""
    if isinstance(obj, dict):
        value = obj.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        for child in obj.values():
            found = JackerySolarVaultCoordinator._find_list_for_key(child, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = JackerySolarVaultCoordinator._find_list_for_key(item, key)
            if found is not None:
                return found
    return None
```

## coordinator.py :: JackerySolarVaultCoordinator._has_subdevice_accessory_or_bucket
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_recovery', 'pre_reconcile'])
- backup lines: 3058-3079 (23 lines)

```python
@classmethod
def _has_subdevice_accessory_or_bucket(
    cls,
    payload: dict[str, Any],
    *,
    dev_type: int,
    bucket: str,
) -> bool:
    """Return True when discovery or a cached bucket mentions a subdevice."""
    target_type = str(dev_type)
    system = payload.get(PAYLOAD_SYSTEM) or payload.get(PAYLOAD_SYSTEM_META) or {}
    accessories: Any = payload.get(FIELD_ACCESSORIES)
    if not isinstance(accessories, list) and isinstance(system, dict):
        accessories = system.get(FIELD_ACCESSORIES)
    if isinstance(accessories, list):
        for item in accessories:
            if not isinstance(item, dict):
                continue
            item_type = item.get(FIELD_DEV_TYPE) or item.get(FIELD_DEVICE_TYPE)
            if str(item_type) == target_type:
                return True
    items = payload.get(bucket)
    return isinstance(items, list) and any(isinstance(item, dict) for item in items)
```

## coordinator.py :: JackerySolarVaultCoordinator._is_battery_pack_lifetime_ble_payload
- source backup: `pre_reconcile` (also in ['pre_recovery', 'pre_reconcile'])
- backup lines: 9063-9070 (9 lines)

```python
@staticmethod
def _is_battery_pack_lifetime_ble_payload(body: dict[str, Any]) -> bool:
    """Return whether a BLE cmd=120 body carries pack lifetime counters."""
    if not body.get(FIELD_DEVICE_SN):
        return False
    if body.get(FIELD_IN_EGY) is None and body.get(FIELD_OUT_EGY) is None:
        return False
    dev_type = safe_int(body.get(FIELD_DEV_TYPE))
    return dev_type in (None, SUBDEVICE_DEV_TYPE_BATTERY_PACK)
```

## coordinator.py :: JackerySolarVaultCoordinator._is_derived_home_energy_candidate
- source backup: `pre_reconcile` (also in ['pre_recovery', 'pre_reconcile'])
- backup lines: 9461-9476 (17 lines)

```python
@staticmethod
def _is_derived_home_energy_candidate(
    *,
    metric_key: str,
    section_prefix: str,
    stat_key: str,
    candidate_prefix: str,
    candidate_stat_key: str,
) -> bool:
    """Return True when a candidate is the derived home-energy fallback."""
    return (
        metric_key == "home_energy"
        and section_prefix == APP_SECTION_HOME_TRENDS
        and stat_key == APP_STAT_TOTAL_HOME_ENERGY
        and candidate_prefix == APP_SECTION_HOME_STAT
        and candidate_stat_key == APP_STAT_TOTAL_OUT_GRID_ENERGY
    )
```

## coordinator.py :: JackerySolarVaultCoordinator._is_smart_meter_accessory
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_recovery', 'pre_reconcile'])
- backup lines: 2996-3014 (20 lines)

```python
@staticmethod
def _is_smart_meter_accessory(item: dict[str, Any]) -> bool:
    """Return True for the CT/Smart-Meter accessory entry used by the app."""
    if (
        str(item.get(FIELD_DEV_TYPE) or item.get(FIELD_DEVICE_TYPE) or "")
        == SUBDEVICE_TYPE_SMART_METER
    ):
        return True
    text = " ".join(
        str(item.get(key) or "")
        for key in (
            FIELD_SCAN_NAME,
            FIELD_TYPE_NAME,
            FIELD_DEVICE_NAME,
            FIELD_PRODUCT_MODEL,
        )
    ).lower()
    if "shelly" in text or "3em" in text or "meter" in text or "ct" in text:
        return True
    return str(item.get(FIELD_SUB_TYPE) or "") == SMART_METER_SUBTYPE
```

## coordinator.py :: JackerySolarVaultCoordinator._jackery_error_code
- source backup: `pre_reconcile` (also in ['pre_recovery', 'pre_reconcile'])
- backup lines: 4094-4102 (10 lines)

```python
@staticmethod
def _jackery_error_code(err: JackeryError) -> int | None:
    """Extract an API error code from a JackeryError message."""
    match = re.search(r"\bcode=(\d+)\b", str(err))
    if match is None:
        return None
    try:
        return int(match.group(1))
    except TypeError, ValueError:
        return None
```

## coordinator.py :: JackerySolarVaultCoordinator._looks_like_battery_pack
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_recovery', 'pre_reconcile'])
- backup lines: 2305-2321 (18 lines)

```python
@classmethod
def _looks_like_battery_pack(cls, item: Any) -> bool:  # noqa: ANN401, RUF100
    """Return True for add-on battery pack dicts, not CT/smart meters."""
    if not isinstance(item, dict):
        return False
    if any(key in item for key in cls._CT_METER_KEYS):
        return False
    if (
        str(item.get(FIELD_DEV_TYPE) or item.get(FIELD_DEVICE_TYPE) or "")
        in NON_BATTERY_SUBDEVICE_TYPES
    ):
        return False
    scan_name = str(item.get(FIELD_SCAN_NAME) or "").lower()
    if "shelly" in scan_name or "3em" in scan_name:
        return False
    if str(item.get(FIELD_SUB_TYPE) or "") == SMART_METER_SUBTYPE:
        return False
    return any(key in item for key in cls._BATTERY_PACK_HINT_KEYS)
```

## coordinator.py :: JackerySolarVaultCoordinator._monotonic_age_seconds
- source backup: `pre_reconcile` (also in ['pre_recovery', 'pre_reconcile'])
- backup lines: 1413-1417 (6 lines)

```python
@staticmethod
def _monotonic_age_seconds(timestamp: float, now_monotonic: float) -> float | None:
    """Return elapsed monotonic seconds for diagnostics/logging."""
    if timestamp == float("-inf"):
        return None
    return max(0.0, now_monotonic - timestamp)
```

## coordinator.py :: JackerySolarVaultCoordinator._mqtt_connect_failure_signature
- source backup: `pre_reconcile` (also in ['pre_recovery', 'pre_reconcile'])
- backup lines: 1204-1213 (11 lines)

```python
@staticmethod
def _mqtt_connect_failure_signature(message: object) -> str:
    """Normalize MQTT setup errors for deduplicated backoff logging."""
    text = str(message or "").strip() or "unknown"
    if "Missing Authority Key Identifier" in text:
        return "tls_missing_authority_key_identifier"
    if "CERTIFICATE_VERIFY_FAILED" in text:
        return "tls_certificate_verify_failed"
    if text.startswith("MQTT not connected yet"):
        return text[:160]
    return text[:160]
```

## coordinator.py :: JackerySolarVaultCoordinator._normalize_battery_pack_payload
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_recovery', 'pre_reconcile'])
- backup lines: 2273-2302 (31 lines)

```python
@classmethod
def _normalize_battery_pack_payload(
    cls,
    item: Any,  # noqa: ANN401, RUF100
) -> dict[str, Any]:
    """Flatten Jackery battery-pack payloads to BatteryPackSub fields.

    The Android app parses add-on battery updates from BatteryPackSub. In
    live MQTT frames the actual values can sit below an `updates` object,
    while the top level only carries deviceSn/inPw/outPw metadata. Flatten
    those shapes before merging so partial packets do not hide SOC/temp.
    """
    if not isinstance(item, dict):
        return {}
    normalized = dict(item)
    for nested_key in (FIELD_UPDATES, FIELD_BODY, PAYLOAD_PROPERTIES):
        nested = normalized.get(nested_key)
        if isinstance(nested, dict):
            normalized = merge_live_properties(normalized, nested)
    aliases = {
        FIELD_RB: FIELD_BAT_SOC,
        FIELD_IP: FIELD_IN_PW,
        FIELD_OP: FIELD_OUT_PW,
    }
    for source_key, target_key in aliases.items():
        if (
            normalized.get(target_key) is None
            and normalized.get(source_key) is not None
        ):
            normalized[target_key] = normalized[source_key]
    return normalized
```

## coordinator.py :: JackerySolarVaultCoordinator._normalize_shelly_cloud_payload
- source backup: `pre_reconcile` (also in ['pre_recovery', 'pre_reconcile'])
- backup lines: 3737-3762 (27 lines)

```python
@classmethod
def _normalize_shelly_cloud_payload(cls, source: Mapping[str, Any]) -> dict[str, Any]:
    """Flatten Shelly Cloud DeviceItem/RealData payloads into subdevice fields."""
    normalized = {key: value for key, value in source.items() if value is not None}
    power_body = normalized.get(FIELD_POWER_BODY)
    if isinstance(power_body, dict):
        normalized = cls._merge_dict_values(normalized, power_body)
    if FIELD_SWITCH in normalized:
        switch_state = normalized[FIELD_SWITCH]
        normalized.setdefault(FIELD_SWITCH_STATE, switch_state)
        normalized.setdefault(FIELD_SYS_SWITCH, switch_state)
    if FIELD_OP in normalized:
        normalized.setdefault(FIELD_OUT_PW, normalized[FIELD_OP])
    if FIELD_IP in normalized:
        normalized.setdefault(FIELD_IN_PW, normalized[FIELD_IP])
    if FIELD_ONLINE in normalized:
        normalized.setdefault(FIELD_ONLINE_STATUS, normalized[FIELD_ONLINE])
    scan_name = str(normalized.get(FIELD_SCAN_NAME) or "").lower()
    if scan_name and scan_name in SUBDEVICE_SCAN_NAME_DEV_TYPES:
        normalized[FIELD_SCAN_NAME] = scan_name
        normalized.setdefault(
            FIELD_DEV_TYPE,
            SUBDEVICE_SCAN_NAME_DEV_TYPES[scan_name],
        )
    return normalized
```

## coordinator.py :: JackerySolarVaultCoordinator._shelly_cloud_api_device_id
- source backup: `pre_reconcile` (also in ['pre_recovery', 'pre_reconcile'])
- backup lines: 3681-3705 (26 lines)

```python
@classmethod
def _shelly_cloud_api_device_id(cls, item: dict[str, Any]) -> str | None:
    """Return the native Shelly Cloud id used by realtime/control APIs."""
    scan_name = str(item.get(FIELD_SCAN_NAME) or "").lower()
    is_shelly = scan_name.startswith("shelly")
    if not (
        is_shelly
        or str(item.get(FIELD_IS_CLOUD)).lower() in {"1", "true"}
        or item.get(FIELD_HOST) is not None
        or item.get(FIELD_DEVICE_CODE) is not None
    ):
        return None

    direct_id = item.get(FIELD_DEVICE_ID)
    if is_shelly:
        # System-list accessories use a numeric Jackery accessory id in
        # deviceId, while Shelly Cloud realtime/control expects the native
        # Shelly device id (`5c...`). The app-linked boundDevices payload
        # exposes that id either as deviceId or, in system-list, deviceSn.
        if direct_id not in (None, "") and not str(direct_id).isdecimal():
            return str(direct_id)
        serial = cls._subdevice_serial(item)
        if serial:
            return serial

    return cls._subdevice_id(item)
```

## coordinator.py :: JackerySolarVaultCoordinator._should_skip_fast_property_fetch
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_recovery', 'pre_reconcile'])
- backup lines: 1119-1146 (28 lines)

```python
def _should_skip_fast_property_fetch(self) -> bool:
    """Return True when the fast ``/v1/device/property`` fetch is redundant.

    Per PROTOCOL.md §0 rule 2 + §2 the HTTP property endpoint (30 s
    cadence) is the only call we may suppress when MQTT is delivering
    state at < ``MQTT_LIVE_THRESHOLD_SEC`` cadence. Slow stat endpoints,
    trends, day-cache rollover and the Recorder statistic imports stay
    on their own slow cadence regardless of MQTT liveness — they are
    gated by ``SLOW_METRICS_INTERVAL_SEC`` in their own TTL caches.

    Within the ``ADAPTIVE_KEEPALIVE_INTERVAL_SEC`` window the property
    fetch is skipped; the rest of the refresh cycle continues so the
    slow path keeps producing fresh ``dateType=day`` payloads (and the
    Recorder backfill keeps running) even while MQTT is live.
    """
    if not self.data:
        return False
    if self._mqtt is None:
        return False
    if not self._mqtt.is_connected:
        return False
    elapsed = self._mqtt.seconds_since_last_message
    if elapsed is None or elapsed > MQTT_LIVE_THRESHOLD_SEC:
        return False
    since_last_refresh = time.monotonic() - self._last_http_refresh_completed_monotonic
    return since_last_refresh < ADAPTIVE_KEEPALIVE_INTERVAL_SEC
```

## coordinator.py :: JackerySolarVaultCoordinator._smart_meter_accessories
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_recovery', 'pre_reconcile'])
- backup lines: 3017-3031 (16 lines)

```python
@classmethod
def _smart_meter_accessories(cls, source: dict[str, Any]) -> list[dict[str, Any]]:
    """Return Smart-Meter accessory metadata from coordinator payload or index."""
    accessories: Any = source.get(FIELD_ACCESSORIES)
    if not isinstance(accessories, list):
        system = source.get(PAYLOAD_SYSTEM) or source.get(PAYLOAD_SYSTEM_META) or {}
        accessories = system.get(FIELD_ACCESSORIES) if isinstance(system, dict) else []
    if not isinstance(accessories, list):
        return []
    return [
        item
        for item in accessories
        if isinstance(item, dict) and cls._is_smart_meter_accessory(item)
    ]
```

## coordinator.py :: JackerySolarVaultCoordinator._subdevice_accessories
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_recovery', 'pre_reconcile'])
- backup lines: 3125-3145 (22 lines)

```python
@classmethod
def _subdevice_accessories(
    cls,
    payload: dict[str, Any],
    *,
    dev_type: int,
) -> list[dict[str, Any]]:
    """Return discovery accessories matching a HomeSubDeviceType value."""
    target_type = str(dev_type)
    system = payload.get(PAYLOAD_SYSTEM) or payload.get(PAYLOAD_SYSTEM_META) or {}
    accessories: Any = payload.get(FIELD_ACCESSORIES)
    if not isinstance(accessories, list) and isinstance(system, dict):
        accessories = system.get(FIELD_ACCESSORIES)
    if not isinstance(accessories, list):
        return []
    return [
        item
        for item in accessories
        if isinstance(item, dict)
        and str(item.get(FIELD_DEV_TYPE) or item.get(FIELD_DEVICE_TYPE) or "")
        == target_type
    ]
```

## coordinator.py :: JackerySolarVaultCoordinator._subdevice_dev_type
- source backup: `pre_reconcile` (also in ['pre_recovery', 'pre_reconcile'])
- backup lines: 3708-3715 (9 lines)

```python
@classmethod
def _subdevice_dev_type(cls, item: Mapping[str, Any]) -> int | None:
    """Return the documented subdevice devType, including Shelly scan names."""
    raw_type = item.get(FIELD_DEV_TYPE) or item.get(FIELD_DEVICE_TYPE)
    if raw_type not in (None, ""):
        with contextlib.suppress(TypeError, ValueError):
            return int(str(raw_type))
    scan_name = str(item.get(FIELD_SCAN_NAME) or "").lower()
    return SUBDEVICE_SCAN_NAME_DEV_TYPES.get(scan_name)
```

## coordinator.py :: JackerySolarVaultCoordinator._subdevice_id
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_recovery', 'pre_reconcile'])
- backup lines: 3117-3122 (7 lines)

```python
@staticmethod
def _subdevice_id(item: dict[str, Any]) -> str | None:
    """Return the cloud id field used by accessory HTTP statistic APIs."""
    dev_id = item.get(FIELD_DEVICE_ID) or item.get(FIELD_ID) or item.get(FIELD_DEV_ID)
    return str(dev_id) if dev_id else None
```

## coordinator.py :: JackerySolarVaultCoordinator._subdevice_serial
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_recovery', 'pre_reconcile'])
- backup lines: 3109-3114 (7 lines)

```python
@staticmethod
def _subdevice_serial(item: dict[str, Any]) -> str | None:
    """Return the stable serial field used by app subdevice payloads."""
    serial = item.get(FIELD_DEVICE_SN) or item.get(FIELD_DEV_SN) or item.get(FIELD_SN)
    return str(serial) if serial else None
```

## coordinator.py :: JackerySolarVaultCoordinator._sync_property_aliases
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_recovery', 'pre_reconcile'])
- backup lines: 2140-2148 (10 lines)

```python
@classmethod
def _sync_property_aliases(cls, props: dict[str, Any]) -> dict[str, Any]:
    """Mirror equivalent app property names after merge operations."""
    normalized = dict(props)
    for left, right in cls._MAIN_PROPERTY_ALIAS_PAIRS:
        if normalized.get(left) is not None and normalized.get(right) is None:
            normalized[right] = normalized[left]
        if normalized.get(right) is not None and normalized.get(left) is None:
            normalized[left] = normalized[right]
    return normalized
```

## coordinator.py :: JackerySolarVaultCoordinator.async_bind_smart_part
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 4312-4320 (9 lines)

```python
async def async_bind_smart_part(self, device_id: str, accessory_sn: str) -> None:
    """Bind a smart accessory to the device (actionId 3012, cmd 108)."""
    await self._async_publish_command(
        device_id,
        message_type=MQTT_MESSAGE_BIND_SMART_ACCESSORY,
        action_id=ACTION_ID_BIND_SMART_PART,
        cmd=MQTT_CMD_BIND_SMART_PART,
        body_fields={"sn": accessory_sn},
    )
```

## coordinator.py :: JackerySolarVaultCoordinator.async_unbind_smart_part
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 4322-4330 (9 lines)

```python
async def async_unbind_smart_part(self, device_id: str, accessory_sn: str) -> None:
    """Unbind a smart accessory from the device (actionId 3013, cmd 109)."""
    await self._async_publish_command(
        device_id,
        message_type=MQTT_MESSAGE_REMOVE_SMART_ACCESSORY,
        action_id=ACTION_ID_UNBIND_SMART_PART,
        cmd=MQTT_CMD_UNBIND_SMART_PART,
        body_fields={"sn": accessory_sn},
    )
```

## coordinator.py :: JackerySolarVaultCoordinator.device_supports_third_party_mqtt
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 2120-2133 (14 lines)

```python
def device_supports_third_party_mqtt(self, device_id: str) -> bool:
    """Return True if the device supports third-party MQTT configuration.

    True when the device has already sent a ThirdPartMQTTConfig payload
    (``PAYLOAD_THIRD_PARTY_MQTT_CONFIG`` present) or when
    ``device_supports_advanced`` is True, since Pro Max / modelCode 3002
    hardware always exposes this feature regardless of whether the config
    payload has arrived yet.
    """
    payload = (self.data or {}).get(device_id, {})
    return PAYLOAD_THIRD_PARTY_MQTT_CONFIG in payload or self.device_supports_advanced(
        device_id
    )
```

## coordinator.py :: JackerySolarVaultCoordinator.record_http_auth_rejection
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 981-987 (7 lines)

```python
def record_http_auth_rejection(self, status: int, data: object) -> None:
    """Record HTTP/API authentication rejection metrics."""
    reason = f"http_{status}"
    if self.api._is_token_expired_response(status, data):  # noqa: SLF001
        self.rejection_metrics.increment("auth_token_expiry_rejections", reason)
        return
    self.rejection_metrics.increment("http_auth_rejections", reason)
```

## coordinator.py :: JackerySolarVaultCoordinator.record_payload_validation_rejection
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 989-991 (3 lines)

```python
def record_payload_validation_rejection(self, reason: str) -> None:
    """Record a payload validation rejection."""
    self.rejection_metrics.increment("payload_validation_rejections", reason)
```

## coordinator.py :: JackerySolarVaultCoordinator.record_schema_rejection
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 993-995 (3 lines)

```python
def record_schema_rejection(self, reason: str) -> None:
    """Record a schema/data-quality rejection."""
    self.rejection_metrics.increment("schema_rejections", reason)
```

## coordinator.py :: JackerySolarVaultCoordinator.record_timestamp_skew_rejection
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 997-999 (3 lines)

```python
def record_timestamp_skew_rejection(self, reason: str) -> None:
    """Record a timestamp validation rejection."""
    self.rejection_metrics.increment("timestamp_skew_rejections", reason)
```

## coordinator.py :: RejectionMetrics
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 361-399 (40 lines)

```python
@dataclass
class RejectionMetrics:
    """Runtime rejection counters exported through diagnostics."""

    http_auth_rejections: int = 0
    mqtt_broker_rejections: int = 0
    payload_validation_rejections: int = 0
    schema_rejections: int = 0
    timestamp_skew_rejections: int = 0
    auth_token_expiry_rejections: int = 0
    last_rejection: dict[str, str] | None = None
    _seen: set[tuple[str, str]] = dataclass_field(default_factory=set, repr=False)

    def increment(self, counter: str, reason: str) -> None:
        """Increment one counter and remember the latest rejection."""
        key = (counter, reason)
        if key in self._seen:
            return
        self._seen.add(key)
        setattr(self, counter, getattr(self, counter) + 1)
        self.last_rejection = {
            "counter": counter,
            "reason": reason,
            "at": dt_util.utcnow().isoformat(),
        }

    def as_dict(self) -> dict[str, Any]:
        """Return diagnostics payload for rejection counters."""
        return {
            "schema_version": DIAGNOSTICS_SCHEMA_VERSION,
            "counters": {
                "http_auth_rejections": self.http_auth_rejections,
                "mqtt_broker_rejections": self.mqtt_broker_rejections,
                "payload_validation_rejections": self.payload_validation_rejections,
                "schema_rejections": self.schema_rejections,
                "timestamp_skew_rejections": self.timestamp_skew_rejections,
                "auth_token_expiry_rejections": self.auth_token_expiry_rejections,
            },
            "last_rejection": self.last_rejection,
        }
```

## coordinator.py :: RejectionMetrics.increment
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 373-384 (12 lines)

```python
def increment(self, counter: str, reason: str) -> None:
    """Increment one counter and remember the latest rejection."""
    key = (counter, reason)
    if key in self._seen:
        return
    self._seen.add(key)
    setattr(self, counter, getattr(self, counter) + 1)
    self.last_rejection = {
        "counter": counter,
        "reason": reason,
        "at": dt_util.utcnow().isoformat(),
    }
```

## coordinator.py :: _generate_third_party_mqtt_token
- source backup: `pre_reconcile` (also in ['pre_recovery', 'pre_reconcile'])
- backup lines: 644-646 (3 lines)

```python
def _generate_third_party_mqtt_token() -> str:
    """Generate a 9-digit numeric token matching app fallback behavior."""
    return "".join(str(secrets.randbelow(10)) for _ in range(9))
```

## diagnostics.py :: _cap_section
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 39-52 (14 lines)

```python
def _cap_section(value: Any) -> Any:  # noqa: ANN401
    """Cap a diagnostics section at _SECTION_SIZE_CAP bytes of JSON.

    Sections that exceed the cap are replaced with a sentinel so the overall
    export stays well under Home Assistant's implicit ~32 KB truncation limit.
    """
    try:
        encoded = json.dumps(value, default=str)
    except Exception:  # noqa: BLE001
        return {"truncated": True, "size_bytes": -1}
    size = len(encoded.encode())
    if size > _SECTION_SIZE_CAP:
        return {"truncated": True, "size_bytes": size}
    return value
```

## discovery_cache.py :: _entry_lock
- source backup: `pre_transfer` (also in ['pre_transfer'])
- backup lines: 20-22 (3 lines)

```python
def _entry_lock(entry_id: str) -> asyncio.Lock:
    """Return the in-process lock for one config-entry cache row."""
    return _ENTRY_LOCKS.setdefault(entry_id, asyncio.Lock())
```

## local_daily_cache.py :: _entry_lock
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 44-46 (3 lines)

```python
def _entry_lock(entry_id: str) -> asyncio.Lock:
    """Return the in-process lock for one config-entry cache row."""
    return _ENTRY_LOCKS.setdefault(entry_id, asyncio.Lock())
```

## services.py :: _device_id_field
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 333-348 (16 lines)

```python
def _device_id_field(call: ServiceCall, translation_key: str) -> str:
    raw = call.data.get(SERVICE_FIELD_DEVICE_ID)
    if not isinstance(raw, str):
        raise _service_validation_error(
            translation_key,
            device_id="",
            error="device_id must be text",
        )
    device_id = raw.strip()
    if not device_id:
        raise _service_validation_error(
            translation_key,
            device_id="",
            error="device_id must not be empty",
        )
    return device_id
```

## services.py :: _entry_for_coordinator
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 245-252 (8 lines)

```python
def _entry_for_coordinator(
    hass: HomeAssistant, coordinator: JackerySolarVaultCoordinator
) -> ConfigEntry | None:
    """Locate the loaded config entry that owns a coordinator."""
    for loaded_entry in hass.config_entries.async_loaded_entries(DOMAIN):
        if getattr(loaded_entry, "runtime_data", None) is coordinator:
            return loaded_entry
    return None
```

## services.py :: _optional_text
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 351-357 (7 lines)

```python
def _optional_text(call: ServiceCall, field: str, label: str) -> str:
    raw = call.data.get(field, "")
    if raw is None:
        return ""
    if not isinstance(raw, str):
        raise ValueError(f"{label} must be text")  # noqa: TRY003, TRY004
    return raw
```

## services.py :: _text_field
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])
- backup lines: 300-330 (31 lines)

```python
def _text_field(  # noqa: PLR0913
    call: ServiceCall,
    field: str,
    *,
    translation_key: str,
    placeholder_key: str,
    max_length: int | None = None,
    numeric: bool = False,
) -> str:
    raw = call.data.get(field)
    if not isinstance(raw, str):
        value = ""
        error = f"{field} must be text"
    else:
        value = raw.strip()
        if not value:
            error = f"{field} must not be empty"
        elif max_length is not None and len(value) > max_length:
            error = f"{field} must be at most {max_length} characters"
        elif numeric and not value.isdigit():
            error = f"{field} must be numeric"
        else:
            return value
    raise ServiceValidationError(
        translation_domain=DOMAIN,
        translation_key=translation_key,
        translation_placeholders={
            placeholder_key: value,
            "error": error,
        },
    )
```


# Truly-missing constants

## client/ble.py :: _unrecognised_version_count
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])

```python
_unrecognised_version_count: list[int] = [0]
```

## client/ble_transport.py :: _LOST_LINK_BACKOFF_SEC
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])

```python
_LOST_LINK_BACKOFF_SEC: float = 8.0
```

## client/ble_transport.py :: _MAX_BACKOFF_SEC
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])

```python
_MAX_BACKOFF_SEC: float = 300.0
```

## client/ble_transport.py :: _MAX_CONNECTION_RETRIES
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])

```python
_MAX_CONNECTION_RETRIES: int = 50
```

## config_flow.py :: DhcpServiceInfo
- source backup: `pre_reconcile` (also in ['pre_reconcile'])

```python
DhcpServiceInfo = Any
```

## config_flow.py :: MqttServiceInfo
- source backup: `pre_reconcile` (also in ['pre_reconcile'])

```python
MqttServiceInfo = Any
```

## config_flow.py :: ZeroconfServiceInfo
- source backup: `pre_reconcile` (also in ['pre_reconcile'])

```python
ZeroconfServiceInfo = Any
```

## const.py :: ACTION_ID_SOC_CHARGE_LIMIT
- source backup: `pre_reconcile` (also in ['pre_recovery', 'pre_reconcile'])

```python
ACTION_ID_SOC_CHARGE_LIMIT: Final = 3022  # cmd=107 DevicePropertyChange
```

## const.py :: ACTION_ID_SOC_DISCHARGE_LIMIT
- source backup: `pre_reconcile` (also in ['pre_recovery', 'pre_reconcile'])

```python
ACTION_ID_SOC_DISCHARGE_LIMIT: Final = 3028  # cmd=107 DevicePropertyChange
```

## const.py :: DIAGNOSTICS_SCHEMA_VERSION
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])

```python
DIAGNOSTICS_SCHEMA_VERSION: Final = 1
```

## coordinator.py :: _DAY_TREND_SOURCE_BY_METRIC_KEY
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_recovery', 'pre_reconcile'])

```python
_DAY_TREND_SOURCE_BY_METRIC_KEY = {
    "pv_energy": (PAYLOAD_PV_TRENDS, APP_STAT_TOTAL_SOLAR_ENERGY),
    "battery_charge_energy": (
        PAYLOAD_BATTERY_TRENDS,
        APP_STAT_TOTAL_TREND_CHARGE_ENERGY,
    ),
    "battery_discharge_energy": (
        PAYLOAD_BATTERY_TRENDS,
        APP_STAT_TOTAL_TREND_DISCHARGE_ENERGY,
    ),
    "home_energy": (PAYLOAD_HOME_TRENDS, APP_STAT_TOTAL_HOME_ENERGY),
}
```

## coordinator.py :: _ENTITY_STATISTIC_KEY_BY_METRIC_PERIOD
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_recovery', 'pre_reconcile'])

```python
_ENTITY_STATISTIC_KEY_BY_METRIC_PERIOD = {
    "pv_energy": {
        DATE_TYPE_DAY: "device_today_pv_energy",
        DATE_TYPE_WEEK: "pv_week_energy",
        DATE_TYPE_MONTH: "pv_month_energy",
        DATE_TYPE_YEAR: "pv_year_energy",
    },
    "pv1_energy": {
        DATE_TYPE_DAY: "device_pv1_day_energy",
        DATE_TYPE_WEEK: "device_pv1_week_energy",
        DATE_TYPE_MONTH: "device_pv1_month_energy",
        DATE_TYPE_YEAR: "device_pv1_year_energy",
    },
    "pv2_energy": {
        DATE_TYPE_DAY: "device_pv2_day_energy",
        DATE_TYPE_WEEK: "device_pv2_week_energy",
        DATE_TYPE_MONTH: "device_pv2_month_energy",
        DATE_TYPE_YEAR: "device_pv2_year_energy",
    },
    "pv3_energy": {
        DATE_TYPE_DAY: "device_pv3_day_energy",
        DATE_TYPE_WEEK: "device_pv3_week_energy",
        DATE_TYPE_MONTH: "device_pv3_month_energy",
        DATE_TYPE_YEAR: "device_pv3_year_energy",
    },
    "pv4_energy": {
        DATE_TYPE_DAY: "device_pv4_day_energy",
        DATE_TYPE_WEEK: "device_pv4_week_energy",
        DATE_TYPE_MONTH: "device_pv4_month_energy",
        DATE_TYPE_YEAR: "device_pv4_year_energy",
    },
    "device_ongrid_input_energy": {
        DATE_TYPE_DAY: "device_today_ongrid_input",
        DATE_TYPE_WEEK: "device_ongrid_input_week_energy",
        DATE_TYPE_MONTH: "device_ongrid_input_month_energy",
        DATE_TYPE_YEAR: "device_ongrid_input_year_energy",
    },
    "device_ongrid_output_energy": {
        DATE_TYPE_DAY: "device_today_ongrid_output",
        DATE_TYPE_WEEK: "device_ongrid_output_week_energy",
        DATE_TYPE_MONTH: "device_ongrid_output_month_energy",
        DATE_TYPE_YEAR: "device_ongrid_output_year_energy",
    },
    "battery_charge_energy": {
        DATE_TYPE_DAY: "device_today_battery_charge",
        DATE_TYPE_WEEK: "battery_charge_week_energy",
        DATE_TYPE_MONTH: "battery_charge_month_energy",
        DATE_TYPE_YEAR: "battery_charge_year_energy",
    },
    "battery_discharge_energy": {
        DATE_TYPE_DAY: "device_today_battery_discharge",
        DATE_TYPE_WEEK: "battery_discharge_week_energy",
        DATE_TYPE_MONTH: "battery_discharge_month_energy",
        DATE_TYPE_YEAR: "battery_discharge_year_energy",
    },
    "home_energy": {
        DATE_TYPE_DAY: "today_load",
        DATE_TYPE_WEEK: "home_week_energy",
        DATE_TYPE_MONTH: "home_month_energy",
        DATE_TYPE_YEAR: "home_year_energy",
    },
}
```

## coordinator.py :: _STATISTICS_HTTP_BACKFILL_WINDOW_DAYS
- source backup: `pre_reconcile` (also in ['pre_recovery', 'pre_reconcile'])

```python
_STATISTICS_HTTP_BACKFILL_WINDOW_DAYS = 7
```

## diagnostics.py :: _SECTION_SIZE_CAP
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])

```python
_SECTION_SIZE_CAP = 4096
```

## discovery_cache.py :: _ENTRY_LOCKS
- source backup: `pre_transfer` (also in ['pre_transfer'])

```python
_ENTRY_LOCKS: dict[str, asyncio.Lock] = {}
```

## local_daily_cache.py :: _ENTRY_LOCKS
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])

```python
_ENTRY_LOCKS: dict[str, asyncio.Lock] = {}
```

## mqtt_session_cache.py :: _CACHE_LOCK
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])

```python
_CACHE_LOCK = asyncio.Lock()
```

## util.py :: ZERO_CONFIRM_MIN_LOCAL_HOUR
- source backup: `pre_transfer` (also in ['pre_transfer', 'pre_reconcile'])

```python
ZERO_CONFIRM_MIN_LOCAL_HOUR: Final = 20
```
