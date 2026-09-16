"""Shared MQTT utilities for Jackery SolarVault integration."""

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    pass


async def async_wait_mqtt_message_queue_idle(
    message_queue: list[Any],
    message_consumer_task: asyncio.Task | None,
    message_delivery_task: asyncio.Task | None,
    settle_consumer: callable,
    settle_delivery: callable,
    ensure_consumer: callable,
) -> None:
    """Wait until every accepted MQTT frame has completed serial delivery.
    
    This is a shared utility for waiting on MQTT message queue idle state.
    It handles the common pattern used by both local_mqtt and mqtt_push modules.
    
    Parameters:
        message_queue: The queue of pending messages
        message_consumer_task: The task consuming messages from the queue
        message_delivery_task: The task delivering messages to the broker
        settle_consumer: Callable to settle a completed consumer task
        settle_delivery: Callable to settle a completed delivery task
        ensure_consumer: Callable to ensure a consumer task is running
    """
    while True:
        consumer = message_consumer_task
        if consumer is not None and consumer.done():
            settle_consumer(consumer)
        delivery = message_delivery_task
        if delivery is not None and delivery.done():
            settle_delivery(delivery)
        if (
            not message_queue
            and message_delivery_task is None
            and message_consumer_task is None
        ):
            return
        ensure_consumer()
        consumer = message_consumer_task
        delivery = message_delivery_task
        current = asyncio.current_task()
        if consumer is current or delivery is current:
            return
        waiter = consumer or delivery
        if waiter is None:
            await asyncio.sleep(0)
            continue
        try:
            await asyncio.shield(waiter)
        except asyncio.CancelledError:
            if not waiter.cancelled():
                raise
