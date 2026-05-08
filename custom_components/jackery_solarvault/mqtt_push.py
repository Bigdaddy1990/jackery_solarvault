"""Async MQTT push client for Jackery SolarVault cloud broker."""

import asyncio
from collections.abc import Awaitable, Callable
import contextlib
from datetime import UTC, datetime
import hashlib
import inspect
import json
import logging
from pathlib import Path
import ssl
from typing import Any

from gmqtt import Client as MQTTClient
from homeassistant.core import HomeAssistant

from .const import (
    FIELD_BODY,
    FIELD_DATA,
    MQTT_CLIENT_LIBRARY,
    MQTT_CONNACK_REASONS,
    MQTT_HOST,
    MQTT_KEEPALIVE_SEC,
    MQTT_PORT,
    MQTT_SILENT_THRESHOLD_SEC,
    MQTT_TOPIC_PREFIX,
    MQTT_TOPIC_SUFFIXES,
    REDACTED_VALUE,
)

_LOGGER = logging.getLogger(__name__)
_GMQTT_LOGGER = logging.getLogger(f"{__name__}.gmqtt")


class _GmqttConnectionNoiseFilter(logging.Filter):
    """Suppress gmqtt connection-refusal duplicates handled by this integration."""

    def filter(self, record: logging.LogRecord) -> bool:
        """Return False for expected gmqtt refusal noise."""
        message = record.getMessage()
        return not (
            message.startswith("[CONNACK] 0x")
            or message
            == "[DISCONNECTED] max number of failed connection attempts achieved"
        )


_GMQTT_LOGGER.addFilter(_GmqttConnectionNoiseFilter())


class JackeryMqttPushClient:
    """Async-native MQTT client for Jackery cloud topics in MQTT_PROTOCOL.md."""

    def __init__(
        self,
        hass: HomeAssistant,
        message_callback: Callable[[str, dict[str, Any]], Awaitable[None]],
        connect_callback: Callable[[], Awaitable[None]] | None = None,
    ) -> None:
        """Initialise the entity from the coordinator and description."""
        self._hass = hass
        self._loop = hass.loop
        self._message_callback = message_callback
        self._connect_callback = connect_callback
        self._lock = asyncio.Lock()
        self._client: MQTTClient | None = None
        self._fingerprint: str | None = None
        self._topics: list[str] = []
        self._connected_event = asyncio.Event()
        self._connected = False
        self._messages_seen = 0
        self._messages_dropped = 0
        self._last_error: str | None = None
        self._last_message_error: str | None = None
        self._last_published_topic: str | None = None
        self._last_connect_at: str | None = None
        self._last_disconnect_at: str | None = None
        self._last_message_at: str | None = None
        self._last_publish_at: str | None = None
        self._connect_attempts = 0
        self._last_connect_failure_signature: str | None = None
        self._tls_custom_ca_loaded = False
        self._tls_certificate_source = "not_built"

    async def async_start(
        self,
        *,
        client_id: str,
        username: str,
        password: str,
        user_id: str,
    ) -> None:
        """Start MQTT connection or reconfigure it when credentials changed."""
        fingerprint = self._credential_fingerprint(client_id, username, password)
        async with self._lock:
            if self._client is not None and self._fingerprint == fingerprint:
                if self._connected:
                    return
                _LOGGER.info(
                    "Jackery MQTT: reconnecting async client with unchanged credentials"
                )

            await self._async_stop_locked()

            self._topics = [
                f"{MQTT_TOPIC_PREFIX}/{user_id}/{suffix}"
                for suffix in MQTT_TOPIC_SUFFIXES
            ]
            self._connected_event.clear()
            self._connected = False
            self._last_error = None
            self._last_connect_failure_signature = None

            client = self._build_client(client_id=client_id)
            client.set_auth_credentials(username, password)
            try:
                client.set_config({"reconnect_retries": 0})
            except Exception as err:
                # gmqtt versions differ on the set_config signature; older
                # releases reject the dict form. Log at debug so the
                # incompatibility surfaces if reconnect-retry behaviour
                # diverges from this integration's expectations.
                _LOGGER.debug(
                    "Jackery MQTT: gmqtt.set_config rejected reconnect_retries=0 "
                    "(library default will apply): %s",
                    err,
                )

            client.on_connect = self._on_connect
            client.on_disconnect = self._on_disconnect
            client.on_message = self._on_message

            ssl_context = await self._hass.async_add_executor_job(
                self._build_ssl_context_blocking
            )

            self._client = client
            self._fingerprint = fingerprint
            self._connect_attempts += 1
            _LOGGER.info(
                "Jackery MQTT: connecting to %s:%s with gmqtt (TLS source=%s)",
                MQTT_HOST,
                MQTT_PORT,
                self._tls_certificate_source,
            )
            try:
                await self._connect_client(client, ssl_context)
            except Exception as err:
                self._last_error = f"connect failed: {err}"
                self._connected = False
                self._connected_event.set()
                _LOGGER.debug("Jackery MQTT connect setup failed: %s", err)

    @staticmethod
    def _credential_fingerprint(client_id: str, username: str, password: str) -> str:
        """Return a stable non-secret signature for MQTT credential changes."""
        hasher = hashlib.sha256()
        for value in (client_id, username, password):
            encoded = value.encode()
            hasher.update(len(encoded).to_bytes(4, "big"))
            hasher.update(encoded)
        return hasher.hexdigest()

    async def async_stop(self) -> None:
        """Stop MQTT connection."""
        async with self._lock:
            await self._async_stop_locked()

    async def async_publish_json(
        self,
        topic: str,
        payload: dict[str, Any],
        *,
        qos: int = 0,
        retain: bool = False,
    ) -> None:
        """Publish JSON payload to an MQTT topic."""
        text = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
        async with self._lock:
            client = self._client
        if client is None:
            raise RuntimeError("MQTT client is not running")
        if not self._connected:
            await self._async_wait_connected(timeout_sec=12.0)
        try:
            client.publish(topic, text, qos=qos, retain=retain)
        except Exception as err:
            self._connected = False
            self._connected_event.clear()
            self._last_error = f"publish failed: {err}"
            raise RuntimeError(f"MQTT publish failed: {err}") from err
        self._last_published_topic = topic
        self._last_publish_at = self._utc_now_iso()

    async def async_wait_until_connected(self, timeout_sec: float = 15.0) -> None:
        """Public wait helper used by command paths that require a live link."""
        async with self._lock:
            client = self._client
        if client is None:
            raise RuntimeError("MQTT client is not running")
        await self._async_wait_connected(timeout_sec=timeout_sec)

    async def _async_wait_connected(self, timeout_sec: float) -> None:
        try:
            await asyncio.wait_for(self._connected_event.wait(), timeout=timeout_sec)
        except TimeoutError as err:
            if self._last_error:
                raise RuntimeError(
                    f"MQTT not connected yet ({self._last_error})"
                ) from err
            self._last_error = "publish timeout waiting for MQTT connect"
            raise RuntimeError("MQTT not connected yet") from err
        if not self._connected:
            raise RuntimeError(f"MQTT not connected yet ({self._last_error})")

    async def _async_stop_locked(self) -> None:
        client = self._client
        if client is None:
            return
        was_connected = self._connected
        self._client = None
        self._fingerprint = None
        self._topics = []
        self._connected = False
        self._connected_event.clear()
        if not was_connected:
            return
        with contextlib.suppress(Exception):
            result = client.disconnect()
            if inspect.isawaitable(result):
                await result

    @staticmethod
    def _build_client(*, client_id: str) -> MQTTClient:
        """Create a gmqtt client while staying compatible with older releases."""
        try:
            return MQTTClient(client_id, clean_session=True, logger=_GMQTT_LOGGER)
        except TypeError:
            try:
                return MQTTClient(client_id, logger=_GMQTT_LOGGER)
            except TypeError:
                return MQTTClient(client_id)

    @staticmethod
    async def _connect_client(client: MQTTClient, ssl_context: ssl.SSLContext) -> None:
        """Connect with gmqtt's default protocol version.

        Jackery's broker rejects forced MQTT 3.1.1 on some accounts with
        CONNACK rc=1. gmqtt defaults to the protocol version it supports best,
        while the coordinator handles throttled reconnect attempts.
        """
        await client.connect(
            MQTT_HOST,
            port=MQTT_PORT,
            ssl=ssl_context,
            keepalive=MQTT_KEEPALIVE_SEC,
        )

    def _build_ssl_context_blocking(self) -> ssl.SSLContext:
        """Build a verified TLS context with the Jackery MQTT CA trust anchor."""
        ctx = ssl.create_default_context()
        source_parts = ["system_default"]
        self._tls_custom_ca_loaded = False
        ca_path = Path(
            self._hass.config.path(
                "custom_components", "jackery_solarvault", "jackery_ca.crt"
            )
        )
        if ca_path.is_file():
            try:
                ctx.load_verify_locations(cafile=str(ca_path))
            except (OSError, ssl.SSLError) as err:
                _LOGGER.warning(
                    "Jackery MQTT CA file %s could not be loaded: %s", ca_path, err
                )
            else:
                self._tls_custom_ca_loaded = True
                source_parts.append(f"jackery_ca:{ca_path}")
        else:
            _LOGGER.warning("Jackery MQTT CA file missing at %s", ca_path)
        ctx.check_hostname = True
        ctx.verify_mode = ssl.CERT_REQUIRED
        # OpenSSL 3.x / Python 3.10+ ssl.create_default_context() enables
        # VERIFY_X509_STRICT by default. This enforces the presence of the
        # Authority Key Identifier (AKID) extension in every certificate in
        # the chain. Jackery's broker certificate does not include this
        # extension, causing CERTIFICATE_VERIFY_FAILED on affected Python
        # versions. Disabling only this strict flag preserves full chain
        # verification, hostname checking, and signature validation.
        if hasattr(ssl, "VERIFY_X509_STRICT"):
            ctx.verify_flags &= ~ssl.VERIFY_X509_STRICT
            source_parts.append("no_x509_strict")
            _LOGGER.debug(
                "Jackery MQTT TLS: VERIFY_X509_STRICT cleared "
                "(broker cert missing AKID; chain/hostname/signature still verified)"
            )
        self._tls_certificate_source = "+".join(source_parts)
        return ctx

    def _on_connect(self, _client: MQTTClient, *args: Any) -> None:
        """Gmqtt connect callback."""
        rc = self._extract_connect_rc(args)
        if rc != 0:
            self._connected = False
            reason = MQTT_CONNACK_REASONS.get(rc, "unknown")
            message = f"connect rc={rc} ({reason})"
            self._last_error = message
            self._connected_event.set()
            if message == self._last_connect_failure_signature:
                _LOGGER.debug("Jackery MQTT repeated connect failure: %s", message)
            else:
                self._last_connect_failure_signature = message
                if self._is_connect_auth_failure_rc(rc):
                    _LOGGER.warning("Jackery MQTT connect failed: %s", message)
                else:
                    _LOGGER.debug("Jackery MQTT connect failed: %s", message)
            return

        self._connected = True
        self._last_connect_at = self._utc_now_iso()
        self._connected_event.set()
        self._last_error = None
        self._last_connect_failure_signature = None
        _LOGGER.info(
            "Jackery MQTT connected; subscribing to %d topic(s) [TLS source=%s]",
            len(self._topics),
            self._tls_certificate_source,
        )
        for topic in self._topics:
            try:
                _client.subscribe(topic, qos=0)
            except Exception as err:
                _LOGGER.warning("Jackery MQTT subscribe failed for %s: %s", topic, err)
        if self._connect_callback is not None:
            self._schedule_coroutine(self._connect_callback(), "connect snapshot")

    @staticmethod
    def _extract_connect_rc(args: tuple[Any, ...]) -> int:
        for arg in args:
            if isinstance(arg, int):
                return int(arg)
        return 0

    @staticmethod
    def _is_connect_auth_failure_rc(rc: int) -> bool:
        """Return True for CONNACK codes that mean credentials are rejected."""
        return rc in (4, 5, 134, 135)

    def _on_disconnect(self, _client: MQTTClient, *args: Any) -> None:
        """Gmqtt disconnect callback."""
        self._connected = False
        self._last_disconnect_at = self._utc_now_iso()
        if self._is_connect_failure_error(self._last_error):
            # Preserve the actionable connect failure for callers waiting on
            # the initial broker check. Some brokers close immediately after
            # a rejected CONNACK, and gmqtt reports that as a clean disconnect.
            self._connected_event.set()
            return
        self._connected_event.clear()
        error = self._extract_disconnect_error(args)
        if error:
            self._last_error = f"disconnect: {error}"
            _LOGGER.debug("Jackery MQTT disconnected: %s", error)
        else:
            self._last_error = None
            _LOGGER.info("Jackery MQTT disconnected cleanly")

    @staticmethod
    def _extract_disconnect_error(args: tuple[Any, ...]) -> str | None:
        for arg in reversed(args):
            if isinstance(arg, BaseException):
                return str(arg)
        return None

    @staticmethod
    def _is_connect_failure_error(error: str | None) -> bool:
        """Return True when disconnect should not hide a failed connect."""
        return str(error or "").startswith(("connect rc=", "connect failed:"))

    def _on_message(
        self,
        _client: MQTTClient,
        topic: str,
        payload: bytes | bytearray | str,
        *_args: Any,
    ) -> None:
        try:
            if isinstance(payload, str):
                text = payload
            else:
                text = bytes(payload).decode("utf-8")
            data = json.loads(text)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError) as err:
            self._messages_dropped += 1
            self._last_message_error = f"invalid JSON payload: {err}"
            return
        if not isinstance(data, dict):
            self._messages_dropped += 1
            self._last_message_error = "non-object JSON payload"
            return
        # MQTT_PROTOCOL.md documents body-based routing; some broker variants
        # send the same structure as data. Normalize before coordinator routing.
        if not isinstance(data.get(FIELD_BODY), dict):
            alt_body = data.get(FIELD_DATA)
            if isinstance(alt_body, dict):
                data[FIELD_BODY] = alt_body

        self._messages_seen += 1
        self._last_message_at = self._utc_now_iso()
        self._last_message_error = None
        self._schedule_coroutine(self._message_callback(str(topic), data), "message")

    def _schedule_coroutine(self, coro: Awaitable[None], label: str) -> None:
        task = self._hass.async_create_task(coro, name=f"jackery_mqtt_{label}")

        def _log_task_result(done: asyncio.Task[None]) -> None:
            try:
                done.result()
            except asyncio.CancelledError:
                return
            except Exception as err:
                _LOGGER.error("Jackery MQTT %s handler failed: %s", label, err)

        task.add_done_callback(_log_task_result)

    @staticmethod
    def _utc_now_iso() -> str:
        """Return a compact UTC timestamp for diagnostics."""
        return datetime.now(UTC).isoformat()

    @staticmethod
    def _redact_topic(topic: str | None) -> str | None:
        """Redact the userId segment from Jackery MQTT topics."""
        if topic is None:
            return None
        parts = topic.split("/")
        if len(parts) >= 4 and "/".join(parts[:2]) == MQTT_TOPIC_PREFIX:
            parts[2] = REDACTED_VALUE
        return "/".join(parts)

    @property
    def diagnostics(self) -> dict[str, Any]:
        """Return a redacted snapshot of the MQTT client state for diagnostics."""
        return {
            "connected": self._connected,
            "started": self._client is not None,
            "messages_seen": self._messages_seen,
            "messages_dropped": self._messages_dropped,
            "topics": [self._redact_topic(topic) for topic in self._topics],
            "topic_count": len(self._topics),
            "last_error": self._last_error,
            "last_message_error": self._last_message_error,
            "last_published_topic": self._redact_topic(self._last_published_topic),
            "last_connect_at": self._last_connect_at,
            "last_disconnect_at": self._last_disconnect_at,
            "last_message_at": self._last_message_at,
            "last_publish_at": self._last_publish_at,
            "seconds_since_last_message": self._seconds_since_last_message(),
            "mqtt_silent_for_too_long": self._mqtt_silent_for_too_long(),
            "host": MQTT_HOST,
            "port": MQTT_PORT,
            "connect_attempts": self._connect_attempts,
            "tls_insecure": False,
            "tls_x509_strict_disabled": hasattr(ssl, "VERIFY_X509_STRICT"),
            "tls_custom_ca_loaded": self._tls_custom_ca_loaded,
            "tls_certificate_source": self._tls_certificate_source,
            "library": MQTT_CLIENT_LIBRARY,
        }

    def _seconds_since_last_message(self) -> float | None:
        """Return seconds elapsed since the last inbound MQTT frame, or None.

        ``None`` means we have not received a single message yet on this
        client lifetime — the broker connect may have succeeded but the
        topic subscriptions may not have been honoured. The
        ``mqtt_silent_for_too_long`` flag combines this with a threshold.
        """
        if self._last_message_at is None:
            return None
        try:
            then = datetime.fromisoformat(self._last_message_at)
        except ValueError:
            return None
        now = datetime.now(tz=then.tzinfo)
        return max(0.0, (now - then).total_seconds())

    @property
    def seconds_since_last_message(self) -> float | None:
        """Public read-only view of the last-message age helper.

        Coordinator-side adaptive polling reads this property to gate
        fast HTTP ticks while MQTT push is delivering fresh frames.
        """
        return self._seconds_since_last_message()

    def _mqtt_silent_for_too_long(self) -> bool:
        """Return True when the broker is "connected" but no message arrives.

        Real Jackery devices emit at least one heartbeat per ~30 s. A
        sustained silence of ``MQTT_SILENT_THRESHOLD_SEC`` (default 300 s)
        while the connection is still open is a strong signal the
        subscription is broken even though TCP is alive — surface it in
        diagnostics so the user can investigate without enabling DEBUG.
        """
        if not self._connected:
            return False
        elapsed = self._seconds_since_last_message()
        if elapsed is None:
            # Still waiting for the first frame after connect — only
            # flag if it has been silent longer than the threshold AND
            # the connect itself was that long ago.
            if self._last_connect_at is None:
                return False
            try:
                then = datetime.fromisoformat(self._last_connect_at)
            except ValueError:
                return False
            now = datetime.now(tz=then.tzinfo)
            return (now - then).total_seconds() > MQTT_SILENT_THRESHOLD_SEC
        return elapsed > MQTT_SILENT_THRESHOLD_SEC

    @property
    def is_started(self) -> bool:
        """Return True once the connect/start lifecycle has run at least once."""
        return self._client is not None

    @property
    def is_connected(self) -> bool:
        """Return True when the MQTT client has an active broker session."""
        return self._connected
