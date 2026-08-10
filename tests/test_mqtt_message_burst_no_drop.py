"""Regression: cloud-MQTT dispatches every inbound frame with no drop cap.

The previous ``_MAX_PENDING_MESSAGE_TASKS`` backlog cap silently dropped valid
inbound frames once 32 handler tasks were in flight, losing the less-frequent
``QueryCombineData`` (CT) and ``QuerySubDeviceGroupProperty`` (sub-battery)
replies so those fields stayed empty. A burst of inbound frames must now
dispatch every one; ``messages_dropped`` stays at 0.
"""

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

from custom_components.jackery_solarvault.client.mqtt_push import JackeryMqttPushClient

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

# Comfortably past the retired 32-task cap so the old code would have dropped
# every frame beyond the 32nd.
_BURST = 50


async def test_message_burst_past_old_cap_dispatches_every_frame(
    hass: HomeAssistant,
) -> None:
    """A synchronous burst of >40 valid frames dispatches all of them, drops none."""
    callback = AsyncMock()
    client = JackeryMqttPushClient(hass, message_callback=callback)

    # The dispatched handler tasks are created eager_start=False and only leave
    # the tracking set once they run, so a synchronous burst accumulates all of
    # them at once — exactly the condition the old cap dropped frames under.
    for index in range(_BURST):
        client._handle_message("device/property", json.dumps({"body": {"seq": index}}))  # ruff: ignore[private-member-access]

    snapshot = client.diagnostics_snapshot()
    assert snapshot["messages_seen"] == _BURST
    assert snapshot["messages_dropped"] == 0

    await hass.async_block_till_done()
    assert callback.await_count == _BURST
