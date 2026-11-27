"""Complete aiomqtt 2.5.1 cleanup after every owned session exit."""

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager, suppress
from functools import partial
import logging
from typing import TYPE_CHECKING

from .credentials import redacted_error

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from types import TracebackType

    from aiomqtt import Client as MqttClient

_LOGGER = logging.getLogger(__name__)
# MQTT 3.1.1 SUBACK: granted QoS 0..2, anything from 0x80 up means the broker
# refused the subscription (MQTT-3.9.3-2).
_SUBACK_FAILURE_CODE = 0x80


def subscription_refusals(codes: object) -> list[str]:
    """Return the SUBACK entries where the broker refused the subscription.

    ``aiomqtt.Client.subscribe`` only raises when the *local* paho call fails;
    a broker that refuses the filter answers with a failure reason code in the
    SUBACK and the coroutine returns normally. Ignoring that return value made
    an ACL-denied subscription look completely healthy: ``connected`` and
    ``subscribed`` both true, publishes flowing, and not a single frame ever
    delivered — not even our own, which the broker would otherwise echo back
    into the subscribed tree.

    MQTT 3.1.1 reports granted QoS as plain ints where ``0x80`` means failure;
    MQTT 5 returns ``ReasonCode`` objects exposing ``is_failure``.
    """
    refused: list[str] = []
    entries = codes if isinstance(codes, (list, tuple)) else ()
    for code in entries:
        is_failure = getattr(code, "is_failure", None)
        failed = (
            bool(is_failure)
            if is_failure is not None
            else isinstance(code, int) and code >= _SUBACK_FAILURE_CODE
        )
        if failed:
            refused.append(str(code))
    return refused


async def _force_sdk_cleanup(client: MqttClient) -> None:
    """Finish the pinned SDK's skipped socket, lock and background-task cleanup."""
    paho = getattr(client, "_client", None)
    descriptor = -1
    if paho is not None:
        with suppress(AttributeError, OSError):
            transport = paho.socket()
            descriptor = transport.fileno() if transport is not None else -1
    loop = asyncio.get_running_loop()
    try:
        if descriptor >= 0:
            with suppress(OSError, ValueError, KeyError):
                loop.remove_reader(descriptor)
                loop.remove_writer(descriptor)
        if paho is not None:
            with suppress(AttributeError, OSError):
                paho._sock_close()  # ruff: ignore[private-member-access]
    finally:
        sdk_lock = getattr(client, "_lock", None)
        if sdk_lock is not None and sdk_lock.locked():
            with suppress(RuntimeError):
                sdk_lock.release()
        misc_task = getattr(client, "_misc_task", None)
        if misc_task is not None:
            misc_task.cancel()
            await asyncio.gather(misc_task, return_exceptions=True)


async def _exit_sdk_context(
    client: MqttClient,
    entry: object | None,
    exc_type: type[BaseException] | None,
    exc: BaseException | None,
    traceback: TracebackType | None,
) -> None:
    """Retain the original SDK error and restore caller cancellation after cleanup."""
    try:
        await client.__aexit__(exc_type, exc, traceback)
    except BaseException as error:
        log = (
            _LOGGER.debug
            if isinstance(error, asyncio.CancelledError)
            else _LOGGER.warning
        )
        log(
            "Jackery MQTT SDK context teardown failed: %s",
            redacted_error(error, entry=entry),
        )
        if isinstance(exc, asyncio.CancelledError):
            raise exc from error
        raise
    finally:
        # aiomqtt returns early when its disconnected future is settled, even
        # when a read/write callback error settled it without closing the socket.
        # Complete resource release on every exit before another session starts.
        await _cleanup_failed_session(client, entry)


async def _cleanup_failed_session(client: MqttClient, entry: object | None) -> None:
    """Keep cleanup failures visible without replacing the original SDK failure."""
    try:
        await _force_sdk_cleanup(client)
    except Exception as cleanup_error:  # ruff: ignore[blind-except]  # Preserve the original SDK failure after cleanup errors.
        _LOGGER.warning(
            "Jackery MQTT SDK forced cleanup failed: %s",
            redacted_error(cleanup_error, entry=entry),
        )


@asynccontextmanager
async def mqtt_client_session(
    client: MqttClient, *, entry: object | None = None
) -> AsyncGenerator[MqttClient]:
    """Own one SDK context and complete failed teardown before releasing its owner."""
    async with AsyncExitStack() as stack:
        opening = asyncio.create_task(
            client.__aenter__(),  # ruff: ignore[unnecessary-dunder-call]  # Own executor connection work before cancellation can release the session.
            name="jackery-mqtt-sdk-connect",
        )
        try:
            await asyncio.wait({opening})
            connected = opening.result()
        except BaseException:
            while not opening.done():
                try:
                    await asyncio.wait({opening})
                except asyncio.CancelledError:
                    continue
            if not opening.cancelled():
                opening.exception()
            await _cleanup_failed_session(client, entry)
            raise
        stack.push_async_exit(partial(_exit_sdk_context, client, entry))
        yield connected
