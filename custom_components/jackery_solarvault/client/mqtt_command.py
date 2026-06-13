"""MQTT command publisher for Jackery SolarVault.

Handles Layer C encryption, payload construction, publish-with-retry,
and credential refresh.  The coordinator calls these helpers instead of
building MQTT payloads directly.
"""

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from jackery_solarvault.const import (
    FIELD_ACTION_ID,
    FIELD_BODY,
    FIELD_DEVICE_SN,
    FIELD_MESSAGE_TYPE,
    FIELD_TIMESTAMP,
    FIELD_VERSION,
    MQTT_CREDENTIAL_USER_ID,
    MQTT_TOPIC_COMMAND,
    MQTT_TOPIC_PREFIX,
)

from .api import JackeryAuthError, JackeryError, encrypt_mqtt_body

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from .mqtt_push import JackeryMqttPushClient

_LOGGER = logging.getLogger(__name__)


def coerce_transport_cmd(cmd: Any) -> int:  # noqa: ANN401  # arbitrary cmd input
    """
    Coerce an arbitrary command value into an integer suitable for transport.
    
    Parameters:
        cmd (Any): Input command. Accepted forms:
            - int
            - float that is finite and has no fractional part
            - str containing a base-10 integer or a finite integral float (e.g., "107" or "107.0")
          The following are rejected: booleans, NaN/inf, empty strings, and values with a fractional component.
    
    Returns:
        int: The coerced integer command.
    
    Raises:
        ValueError: If the input cannot be converted to an integer.
    """
    import contextlib
    import math

    if isinstance(cmd, bool):
        raise ValueError("cmd must be an integer")  # noqa: TRY003, TRY004
    if isinstance(cmd, int):
        return cmd
    if isinstance(cmd, float):
        if not math.isfinite(cmd) or not cmd.is_integer():
            raise ValueError("cmd must be an integer")  # noqa: TRY003
        return int(cmd)
    if isinstance(cmd, str):
        text = cmd.strip()
        if not text:
            raise ValueError("cmd must be an integer")  # noqa: TRY003
        with contextlib.suppress(ValueError):
            return int(text, 10)
        with contextlib.suppress(ValueError):
            parsed = float(text)
            if math.isfinite(parsed) and parsed.is_integer():
                return int(parsed)
        raise ValueError("cmd must be an integer")  # noqa: TRY003
    try:
        return int(cmd)
    except (TypeError, ValueError) as err:
        raise ValueError("cmd must be an integer") from err  # noqa: TRY003


def command_body_for_transport(
    body_fields: dict[str, Any], *, cmd: object
) -> dict[str, Any]:
    """
    Create the command body dictionary used by MQTT and BLE transports.
    
    The provided `body_fields` are copied and returned with an added "cmd" entry only when `cmd` can be coerced to an integer greater than zero.
    
    Parameters:
        body_fields (dict[str, Any]): Base fields to include in the returned body.
        cmd (object): Value to coerce to an integer and include as "cmd" when greater than zero.
    
    Returns:
        dict[str, Any]: A dictionary containing the combined command body; includes "cmd" only if the coerced value is > 0.
    
    Raises:
        ValueError: If `cmd` cannot be coerced to a valid integer.
    """
    body: dict[str, Any] = dict(body_fields)
    cmd_value = coerce_transport_cmd(cmd)
    if cmd_value > 0:
        body["cmd"] = cmd_value
    return body


async def publish_mqtt_command(  # noqa: PLR0913
    *,
    mqtt: JackeryMqttPushClient,
    api: Any,  # noqa: ANN401  # JackeryApi — avoids circular import
    device_id: str,
    device_sn: str,
    bt_key: bytes | None,
    message_type: str,
    action_id: int,
    cmd: int,
    body_fields: dict[str, Any],
    ensure_mqtt_cb: Callable[[], Awaitable[None]],
    relogin_cb: Callable[[], Awaitable[None]],
    stop_mqtt_cb: Callable[[], Awaitable[None]],
) -> None:
    """Build, encrypt, and publish an MQTT command payload.

    Parameters:
        mqtt: The running MQTT push client.
        api: The JackeryApi instance (for credential refresh).
        device_id: HA device identifier.
        device_sn: Device serial number for the payload.
        bt_key: Per-device AES key for Layer C encryption (or None).
        message_type: MQTT message type string.
        action_id: Numeric action identifier.
        cmd: Numeric command code.
        body_fields: Command body fields.
        ensure_mqtt_cb: Callable to ensure MQTT is connected.
        relogin_cb: Callable to refresh HTTP credentials.
        stop_mqtt_cb: Callable to stop/restart the MQTT client.
    """
    await ensure_mqtt_cb()

    try:
        creds = await api.async_get_mqtt_credentials()
    except JackeryAuthError as err:
        _raise_config_entry_auth_failed(
            "Jackery credentials were rejected while preparing an MQTT command",
            err,
        )
    except JackeryError as err:
        from homeassistant.exceptions import HomeAssistantError

        raise HomeAssistantError(  # noqa: TRY003
            f"Could not build Jackery MQTT credentials: {err}"
        ) from err

    user_id = creds[MQTT_CREDENTIAL_USER_ID]
    topic = f"{MQTT_TOPIC_PREFIX}/{user_id}/{MQTT_TOPIC_COMMAND}"
    ts = int(time.time() * 1000)
    body: dict[str, Any] = command_body_for_transport(body_fields, cmd=cmd)

    # Layer C: encrypt body with bluetoothKey per PROTOCOL.md §14.
    payload_str: str
    if bt_key is not None and len(bt_key) == 16:  # noqa: PLR2004
        try:
            payload_str = encrypt_mqtt_body(body, bt_key)
        except (ValueError, TypeError) as err:
            _LOGGER.warning(
                "Jackery MQTT Layer C encrypt failed for %s, sending plaintext: %s",
                device_id,
                err,
            )
            payload_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False)
    else:
        payload_str = json.dumps(body, separators=(",", ":"), ensure_ascii=False)

    payload: dict[str, Any] = {
        "id": ts,
        FIELD_VERSION: 0,
        FIELD_MESSAGE_TYPE: message_type,
        FIELD_ACTION_ID: action_id,
        FIELD_TIMESTAMP: ts,
        FIELD_BODY: payload_str,
        FIELD_DEVICE_SN: device_sn,
    }

    last_err: Exception | None = None
    for attempt in range(2):
        try:
            if not mqtt.is_connected:
                await ensure_mqtt_cb()
            if mqtt is None or not mqtt.is_connected:
                raise RuntimeError("MQTT client is not connected")  # noqa: TRY003, TRY301
            await mqtt.async_publish_json(topic, payload, qos=0, retain=False)
            return  # noqa: TRY300
        except RuntimeError as err:
            last_err = err
            if attempt == 0:
                try:
                    await relogin_cb()
                except JackeryAuthError as relogin_err:
                    _raise_config_entry_auth_failed(
                        "Jackery credentials rejected while refreshing "
                        "MQTT command credentials",
                        relogin_err,
                    )
                except JackeryError as relogin_err:
                    _LOGGER.debug(
                        "Jackery re-login before MQTT command retry failed: %s",
                        relogin_err,
                    )
                await stop_mqtt_cb()
                continue

    mqtt_last_error = mqtt.diagnostics.get("last_error") if mqtt else None
    from homeassistant.exceptions import HomeAssistantError

    raise HomeAssistantError(
        translation_domain="jackery_solarvault",
        translation_key="mqtt_command_failed",
        translation_placeholders={
            "error": str(last_err) if last_err else "unknown",
            "mqtt_last_error": str(mqtt_last_error) if mqtt_last_error else "n/a",
        },
    ) from last_err


def _raise_config_entry_auth_failed(message: str, err: Exception) -> None:
    """
    Raise a ConfigEntryAuthFailed to indicate the config entry's credentials are invalid.
    
    Parameters:
        message (str): Human-readable error message to attach to the raised exception.
        err (Exception): Original exception to chain as the cause.
    
    Raises:
        ConfigEntryAuthFailed: Always raised with `message` and chained from `err`.
    """
    from homeassistant.exceptions import ConfigEntryAuthFailed

    raise ConfigEntryAuthFailed(message) from err
