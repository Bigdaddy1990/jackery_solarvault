"""Pure coordinator domain helpers.

These helpers are intentionally free of Home Assistant coordinator state. They
encode source-backed wire/debug coercion semantics that the coordinator only
orchestrates.
"""

import json
from typing import Any

from custom_components.jackery_solarvault.util import first_nonblank_int, safe_int
from homeassistant.helpers.update_coordinator import UpdateFailed


def stable_payload_debug_signature(event: dict[str, Any]) -> str:
    """Return a content-only signature for payload-debug dedup."""
    payload = event.get("payload") or {}
    body = payload.get("body") if isinstance(payload, dict) else None
    if isinstance(body, dict):
        body_sig: Any = {
            key: value for key, value in body.items() if key != "messageId"
        }
    else:
        body_sig = body
    response = (
        event.get("response") if isinstance(event.get("response"), dict) else None
    )
    response_data = response.get("data") if response is not None else None
    return json.dumps(
        [
            event.get("kind"),
            event.get("topic") or event.get("path"),
            payload.get("messageType") if isinstance(payload, dict) else None,
            body_sig,
            event.get("body_type"),
            event.get("data_type"),
            event.get("response_data_type"),
            event.get("status"),
            response_data,
        ],
        sort_keys=True,
        default=str,
    )


def exception_debug_message(err: BaseException) -> str:
    """Return a useful debug message for exceptions with empty ``str(err)``."""
    return f"{type(err).__name__}: {err or '(no message)'}"


def control_int(value: Any, field_name: str) -> int:  # noqa: ANN401
    """Return a finite integer control value or raise a coordinator error."""
    parsed = None if isinstance(value, bool) else safe_int(value)
    if parsed is None:
        msg = f"Invalid {field_name}"
        raise UpdateFailed(msg)
    return parsed


def transport_cmd(value: Any) -> int:  # noqa: ANN401
    """Return a command integer for MQTT/BLE transport routing."""
    parsed = first_nonblank_int(value)
    if parsed is None:
        msg = "cmd must be an integer"
        raise ValueError(msg)
    return parsed
