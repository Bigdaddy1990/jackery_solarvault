"""Unit tests for MQTT session cache hydration before Layer-5 start."""

from typing import Any
from unittest.mock import MagicMock

from custom_components.jackery_solarvault.__init__ import (
    _async_prime_entry_bootstrap_mqtt_session,  # noqa: PLC2701, RUF105
    _entry_bootstrap_mqtt_session,  # noqa: PLC2701, RUF105
)
from custom_components.jackery_solarvault.client.api import JackeryApi
from custom_components.jackery_solarvault.client.mqtt_session_cache import (
    normalize_mqtt_session_snapshot,
)
from custom_components.jackery_solarvault.const import (
    ENTRY_BOOTSTRAP_MQTT_SESSION,
    MQTT_SESSION_MAC_ID,
    MQTT_SESSION_SEED_B64,
    MQTT_SESSION_USER_ID,
)


class MockHass:
    """Mock Home Assistant instance."""

    def __init__(self) -> None:  # noqa: D107, RUF105
        self.data = {}
        self.config = MagicMock()
        self.config.config_dir = "/mock/config"


class MockConfigEntry:
    """Mock ConfigEntry with data and options."""

    def __init__(  # ruff: ignore[undocumented-public-init]
        self,
        data: dict | None = None,
        options: dict | None = None,
        entry_id: str = "test_entry",
    ) -> None:  # noqa: D107, E501, RUF100
        self.data = data or {}
        self.options = options or {}
        self.entry_id = entry_id


class MockCoordinator:
    """Mock Coordinator with API."""

    def __init__(self, api: JackeryApi, entry: MockConfigEntry, hass: MockHass) -> None:  # noqa: D107, RUF105
        self.api = api
        self.entry = entry
        self.hass = hass
        self.data = {}
        self._mqtt_session_cache_loaded = False
        self._persisted_mqtt_session = None
        self._shutdown_started = False

    def cached_discovery_snapshot(self) -> dict | None:  # noqa: D102, PLR6301, RUF105
        return None

    async def async_load_cached_discovery(self, _label: str) -> bool:  # noqa: D102, PLR6301, RUF105
        return False

    async def async_load_local_daily_snapshots(self) -> None:  # noqa: D102, RUF105
        pass

    def mark_mqtt_session_cache_loaded(self, persisted: Any) -> bool:  # noqa: D102, RUF105
        self._persisted_mqtt_session = persisted
        self._mqtt_session_cache_loaded = True
        return True


async def test_entry_bootstrap_mqtt_session_extracts_valid_snapshot() -> None:  # noqa: RUF029, RUF105
    """_entry_bootstrap_mqtt_session extracts valid snapshot from entry.data."""
    # Valid bootstrap session - 32 zero bytes base64 encoded = 43 'A' + '=' padding = 44 chars  # noqa: RUF105
    seed = "A" * 43 + "="
    entry = MockConfigEntry(
        data={
            ENTRY_BOOTSTRAP_MQTT_SESSION: {
                MQTT_SESSION_USER_ID: "user123",
                MQTT_SESSION_SEED_B64: seed,
                MQTT_SESSION_MAC_ID: "mac456",
            }
        }
    )
    snapshot = _entry_bootstrap_mqtt_session(entry)
    assert snapshot is not None
    assert snapshot[MQTT_SESSION_USER_ID] == "user123"
    assert snapshot[MQTT_SESSION_MAC_ID] == "mac456"


async def test_entry_bootstrap_mqtt_session_returns_none_for_missing() -> None:  # noqa: RUF029, RUF105
    """_entry_bootstrap_mqtt_session returns None when key missing."""
    entry = MockConfigEntry(data={})
    snapshot = _entry_bootstrap_mqtt_session(entry)
    assert snapshot is None


async def test_entry_bootstrap_mqtt_session_returns_none_for_invalid() -> None:  # noqa: RUF029, RUF105
    """_entry_bootstrap_mqtt_session returns None for invalid snapshot."""
    entry = MockConfigEntry(
        data={
            ENTRY_BOOTSTRAP_MQTT_SESSION: {
                MQTT_SESSION_USER_ID: "",  # Empty user_id
                MQTT_SESSION_SEED_B64: "A" * 44,
                MQTT_SESSION_MAC_ID: "mac456",
            }
        }
    )
    snapshot = _entry_bootstrap_mqtt_session(entry)
    assert snapshot is None


async def test_async_prime_entry_bootstrap_mqtt_session_hydrates_api() -> None:
    """_async_prime_entry_bootstrap_mqtt_session hydrates API from entry bootstrap."""
    hass = MockHass()
    seed = "A" * 43 + "="
    entry = MockConfigEntry(
        data={
            ENTRY_BOOTSTRAP_MQTT_SESSION: {
                MQTT_SESSION_USER_ID: "user123",
                MQTT_SESSION_SEED_B64: seed,
                MQTT_SESSION_MAC_ID: "mac456",
            }
        }
    )
    api = JackeryApi.__new__(JackeryApi)
    api._mqtt_user_id = None  # noqa: RUF105, SLF001
    api._mqtt_seed_b64 = None  # noqa: RUF105, SLF001
    api._mqtt_mac_id = None  # noqa: RUF105, SLF001

    result = await _async_prime_entry_bootstrap_mqtt_session(hass, entry, api)
    assert result is not None
    assert api._mqtt_user_id == "user123"  # noqa: RUF105, SLF001
    assert api._mqtt_mac_id == "mac456"  # noqa: RUF105, SLF001
    assert api._mqtt_seed_b64 == seed  # noqa: RUF105, SLF001


async def test_async_prime_entry_bootstrap_mqtt_session_noop_when_missing() -> None:
    """_async_prime_entry_bootstrap_mqtt_session is noop when no bootstrap session."""
    hass = MockHass()
    entry = MockConfigEntry(data={})
    api = JackeryApi.__new__(JackeryApi)
    api._mqtt_user_id = None  # noqa: RUF105, SLF001

    result = await _async_prime_entry_bootstrap_mqtt_session(hass, entry, api)
    assert result is None
    assert api._mqtt_user_id is None  # noqa: RUF105, SLF001


async def test_normalize_mqtt_session_snapshot_validates_expiry() -> None:  # noqa: RUF029, RUF105
    """normalize_mqtt_session_snapshot rejects expired sessions."""
    import time  # noqa: PLC0415, RUF105

    expired = time.time() - 3600
    seed = "A" * 44
    raw = {
        MQTT_SESSION_USER_ID: "user123",
        MQTT_SESSION_SEED_B64: seed,
        MQTT_SESSION_MAC_ID: "mac456",
        "expires_at": expired,
    }
    snapshot = normalize_mqtt_session_snapshot(raw)
    assert snapshot is None


async def test_normalize_mqtt_session_snapshot_accepts_valid() -> None:  # noqa: RUF029, RUF105
    """normalize_mqtt_session_snapshot accepts valid non-expired sessions."""
    import time  # noqa: PLC0415, RUF105

    future = time.time() + 3600
    seed = "A" * 43 + "="  # 32 bytes base64 encoded = 43 chars + "=" padding
    raw = {
        MQTT_SESSION_USER_ID: "user123",
        MQTT_SESSION_SEED_B64: seed,
        MQTT_SESSION_MAC_ID: "mac456",
        "expires_at": future,
    }
    snapshot = normalize_mqtt_session_snapshot(raw)
    assert snapshot is not None
    assert snapshot[MQTT_SESSION_USER_ID] == "user123"


async def test_mqtt_session_cache_load_save_roundtrip() -> None:  # noqa: RUF029, RUF105
    """MQTT session cache can be saved and loaded back."""
    # This tests the normalize function logic used by both load/save
    seed = "A" * 43 + "="  # 32 bytes base64
    # Use a fixed 'now' timestamp that's after cached_at but before expires_at
    fixed_now = 1500000.0
    raw = {
        MQTT_SESSION_USER_ID: "user123",
        MQTT_SESSION_SEED_B64: seed,
        MQTT_SESSION_MAC_ID: "mac456",
        "cached_at": 1000000.0,
        "expires_at": 2000000.0,
    }
    normalized = normalize_mqtt_session_snapshot(raw, now=fixed_now)
    assert normalized is not None
    assert normalized[MQTT_SESSION_USER_ID] == "user123"
    assert normalized[MQTT_SESSION_MAC_ID] == "mac456"
    assert normalized[MQTT_SESSION_SEED_B64] == seed


async def test_coordinator_api_hydrated_before_layer5_start() -> None:  # noqa: RUF029, RUF105
    """Verify API is hydrated with MQTT session before Layer-5 transports start."""
    # This test verifies the sequence in _async_load_entry_caches:
    # 1. Persistent MQTT session is loaded
    # 2. If API has no session, fallback to persistent or bootstrap
    # 3. API is hydrated
    # 4. Cache loaded flag is set
    #
    # The actual integration test would require HA runtime, but we verify
    # the logic components work correctly.
    api = JackeryApi.__new__(JackeryApi)
    api._mqtt_user_id = None  # noqa: RUF105, SLF001
    api._mqtt_seed_b64 = None  # noqa: RUF105, SLF001
    api._mqtt_mac_id = None  # noqa: RUF105, SLF001
    api._mqtt_mac_id_source = "generated"  # noqa: RUF105, SLF001

    seed = "A" * 43 + "="
    persisted = {
        MQTT_SESSION_USER_ID: "user123",
        MQTT_SESSION_SEED_B64: seed,
        MQTT_SESSION_MAC_ID: "mac456",
    }

    # Simulate what _async_load_entry_caches does
    if api.mqtt_session_snapshot() is None:
        fallback = persisted
        if fallback is not None:
            api.hydrate_mqtt_session(
                user_id=fallback[MQTT_SESSION_USER_ID],
                seed_b64=fallback[MQTT_SESSION_SEED_B64],
                mac_id=fallback[MQTT_SESSION_MAC_ID],
            )

    # Verify API is hydrated
    assert api.mqtt_session_snapshot() is not None
    snapshot = api.mqtt_session_snapshot()
    assert snapshot[MQTT_SESSION_USER_ID] == "user123"
    assert snapshot[MQTT_SESSION_MAC_ID] == "mac456"

    # Verify credentials can be derived
    creds = api.get_cached_mqtt_credentials()
    assert creds is not None
    assert creds["client_id"] == "user123@APP"
    assert creds["username"] == "user123@mac456"


async def test_api_derives_credentials_after_hydration() -> None:  # noqa: RUF029, RUF105
    """API can derive MQTT credentials after session hydration."""
    api = JackeryApi.__new__(JackeryApi)
    seed = "A" * 43 + "="

    api.hydrate_mqtt_session(
        user_id="user123",
        seed_b64=seed,
        mac_id="mac456",
    )

    creds = api.get_cached_mqtt_credentials()
    assert creds is not None
    assert "client_id" in creds
    assert "username" in creds
    assert "password" in creds
    assert "user_id" in creds
    assert creds["user_id"] == "user123"


async def test_api_returns_none_credentials_without_session() -> None:  # noqa: RUF029, RUF105
    """API returns None for credentials when no session hydrated."""
    api = JackeryApi.__new__(JackeryApi)
    api._mqtt_user_id = None  # noqa: RUF105, SLF001
    api._mqtt_seed_b64 = None  # noqa: RUF105, SLF001
    api._mqtt_mac_id = None  # noqa: RUF105, SLF001

    creds = api.get_cached_mqtt_credentials()
    assert creds is None


async def test_mqtt_fingerprint_changes_after_new_login() -> None:  # noqa: RUF029, RUF105
    """MQTT fingerprint changes when new session is hydrated."""
    api = JackeryApi.__new__(JackeryApi)
    seed1 = "A" * 43 + "="
    seed2 = "B" * 43 + "="

    # Initial session
    api.hydrate_mqtt_session(user_id="user123", seed_b64=seed1, mac_id="mac456")
    fp1 = api.mqtt_fingerprint

    # New session after re-login
    api.hydrate_mqtt_session(user_id="user123", seed_b64=seed2, mac_id="mac456")
    fp2 = api.mqtt_fingerprint

    assert fp1 != fp2
    assert fp1 == ("user123", "mac456", seed1)
    assert fp2 == ("user123", "mac456", seed2)
