"""Keep local receive times outside the device's JSON wire fields."""

from typing import Any, Self


class ReceivedBytes(bytes):
    """Binary ingress with its local receipt retained through JSON fallback."""

    received_at_monotonic: float

    def __new__(cls, payload: bytes, received_at_monotonic: float) -> Self:  # ruff: ignore[undocumented-public-method]
        result = super().__new__(cls, payload)
        result.received_at_monotonic = received_at_monotonic
        return result


class ReceivedPayload(dict[str, Any]):  # ruff: ignore[subclass-builtin]
    """A config payload with the time it crossed the transport ingress."""

    def __init__(  # ruff: ignore[undocumented-public-init]
        self, payload: dict[str, Any], received_at_monotonic: float
    ) -> None:
        super().__init__(payload)
        self.received_at_monotonic = received_at_monotonic


def with_config_receipt(
    payload: dict[str, Any], received_at_monotonic: float
) -> dict[str, Any]:
    """Attach receipt metadata only to potential bridge readbacks."""
    body = payload.get("body")
    if not isinstance(body, dict):
        body = payload.get("data")
    if not isinstance(body, dict):
        body = payload
    if (
        payload.get("actionId") in {3047, "3047"}
        or payload.get("messageType") == "QueryThirdPartMQTTConfig"
        or payload.get("type") in {114, "114"}
        or body.get("cmd") in {114, "114"}
    ):
        return ReceivedPayload(payload, received_at_monotonic)
    return payload
