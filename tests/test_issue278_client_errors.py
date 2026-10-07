"""Exercise client recovery paths protected by tuple exception handlers."""

import asyncio
from datetime import date
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest

from custom_components.jackery_solarvault.client.daily_energy import daily_delta
from custom_components.jackery_solarvault.client.local_mqtt import (
    JackeryLocalMqttClient,
)
from custom_components.jackery_solarvault.client.mqtt_push import JackeryMqttPushClient

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@pytest.mark.parametrize("request_id", ["not-an-integer", "42.5"])
async def test_malformed_response_id_preserves_pending_getter(
    hass: HomeAssistant,
    request_id: str,
) -> None:
    """An invalid wire ID cannot consume a pending integer-ID response."""
    client = JackeryMqttPushClient(hass, message_callback=AsyncMock())
    waiter = asyncio.create_task(client._wait_for_response(42, 1.0))  # ruff: ignore[private-member-access]
    await asyncio.sleep(0)

    client._resolve_pending_response({"request_id": request_id})  # ruff: ignore[private-member-access]

    assert not waiter.done()
    assert client.responses_correlated == 0
    response = {"request_id": 42, "body": {"soc": 88}}
    client._resolve_pending_response(response)  # ruff: ignore[private-member-access]

    assert await asyncio.wait_for(waiter, timeout=1.0) == response
    assert client.responses_correlated == 1
    assert client._pending_responses == {}  # ruff: ignore[private-member-access]


@pytest.mark.parametrize("error_type", [TimeoutError, OSError])
async def test_local_delivery_transport_error_remains_owned_by_task(
    error_type: type[Exception],
) -> None:
    """Shielded transport failures remain available to the delivery boundary."""
    error = error_type("delivery transport failed")

    async def _deliver() -> None:
        await asyncio.sleep(0)
        raise error

    task = asyncio.create_task(_deliver())

    assert await JackeryLocalMqttClient._async_wait_delivery_task(task) is False  # ruff: ignore[private-member-access]
    assert task.done()
    assert not task.cancelled()
    with pytest.raises(error_type, match="delivery transport failed") as caught:
        task.result()
    assert caught.value is error


@pytest.mark.parametrize(
    "current_value",
    ["not-a-number", [], float("nan"), float("inf"), float("-inf")],
    ids=["text", "list", "nan", "positive-infinity", "negative-infinity"],
)
def test_daily_delta_rejects_unusable_current_counter(current_value: Any) -> None:
    """Malformed counters cannot produce a delta or alter the verified anchor."""
    today = date(2026, 10, 7)
    snapshot = {
        "day": today.isoformat(),
        "values": {"pvEgy": 1000},
        "full_day_metrics": ["pvEgy"],
    }

    assert daily_delta(snapshot, "pvEgy", current_value, today=today) is None
    assert daily_delta(snapshot, "pvEgy", 1250, today=today) == 250  # ruff: ignore[magic-value-comparison]
    assert snapshot["values"] == {"pvEgy": 1000}
