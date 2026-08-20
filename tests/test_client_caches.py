"""Tests for client discovery cache and mqtt session cache."""

import asyncio
import time
from types import ModuleType
from typing import Any

import pytest
from homeassistant.core import HomeAssistant

from custom_components.jackery_solarvault.client import (
    discovery_cache as discovery_cache_module,
)
from custom_components.jackery_solarvault.client import (
    mqtt_session_cache as mqtt_session_cache_module,
)
from custom_components.jackery_solarvault.client import (
    local_daily_cache as local_daily_cache_module,
)
from custom_components.jackery_solarvault.client.discovery_cache import (
    async_load_discovery_cache,
    async_save_discovery_cache,
)
from custom_components.jackery_solarvault.client.mqtt_session_cache import (
    async_clear_mqtt_session,
    async_load_mqtt_session,
    async_save_mqtt_session,
)
from custom_components.jackery_solarvault.client.local_daily_cache import (
    async_load_daily_cache,
    async_save_daily_cache,
)
from custom_components.jackery_solarvault.const import (
    MQTT_SESSION_MAC_ID,
    MQTT_SESSION_MAC_ID_SOURCE,
    MQTT_SESSION_SEED_B64,
    MQTT_SESSION_USER_ID,
)

_VALID_SEED_B64 = "eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHh4eHg="


@pytest.mark.asyncio
async def test_discovery_cache_flow(hass: HomeAssistant) -> None:
    """Test loading and saving discovery cache."""
    # Loading empty store
    cache = await async_load_discovery_cache(hass, "entry_123")
    assert cache == {}

    # Save discovery cache
    device_index = {"dev_1": {"model": "Explorer 2000 Pro", "serial": "12345"}}
    await async_save_discovery_cache(hass, "entry_123", device_index)

    # Load back
    loaded = await async_load_discovery_cache(hass, "entry_123")
    assert loaded == device_index

    # Load non-existent entry
    loaded_other = await async_load_discovery_cache(hass, "entry_other")
    assert loaded_other == {}


@pytest.mark.asyncio
async def test_discovery_cache_survives_runtime_lock_recreation(
    hass: HomeAssistant,
) -> None:
    """Only the transaction lock is runtime state; discovery data stays on disk."""
    device_index = {
        "dev_1": {
            "system_meta": {"bluetoothKey": "MDEyMzQ1Njc4OWFiY2RlZg=="},
        },
    }
    await async_save_discovery_cache(hass, "entry_restart", device_index)

    hass.data.pop(discovery_cache_module._LOCK_KEY, None)

    assert await async_load_discovery_cache(hass, "entry_restart") == device_index


@pytest.mark.asyncio
async def test_discovery_cache_serializes_different_entry_rows(
    hass: HomeAssistant,
) -> None:
    """Concurrent entries cannot overwrite each other in the shared Store file."""
    await asyncio.gather(
        async_save_discovery_cache(hass, "entry_a", {"a": {"model": "A"}}),
        async_save_discovery_cache(hass, "entry_b", {"b": {"model": "B"}}),
    )

    assert await async_load_discovery_cache(hass, "entry_a") == {
        "a": {"model": "A"},
    }
    assert await async_load_discovery_cache(hass, "entry_b") == {
        "b": {"model": "B"},
    }


class _BlockingStore:
    """Store double exposing whether a cancelled caller cancels persistence."""

    def __init__(self) -> None:
        self.data: dict[str, Any] | None = None
        self.save_started = asyncio.Event()
        self.release_save = asyncio.Event()

    async def async_load(self) -> dict[str, Any] | None:
        return self.data

    async def async_save(self, data: dict[str, Any]) -> None:
        self.save_started.set()
        await self.release_save.wait()
        self.data = data


@pytest.mark.asyncio
@pytest.mark.parametrize("cache_kind", ["discovery", "mqtt", "daily"])
async def test_cache_mutation_survives_caller_cancellation(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    cache_kind: str,
) -> None:
    """Offline bootstrap data finishes writing when its caller is cancelled."""
    store = _BlockingStore()
    module: ModuleType
    if cache_kind == "discovery":
        module = discovery_cache_module
        save_coro = async_save_discovery_cache(
            hass,
            "entry_cancel",
            {"device": {"model": "SolarVault"}},
        )
    elif cache_kind == "mqtt":
        module = mqtt_session_cache_module
        save_coro = async_save_mqtt_session(
            hass,
            "entry_cancel",
            user_id="user",
            seed_b64=_VALID_SEED_B64,
            mac_id="AABBCCDDEEFF",
        )
    else:
        module = local_daily_cache_module
        save_coro = async_save_daily_cache(
            hass,
            "entry_cancel",
            snapshots={
                "device": {
                    "day": "2026-07-29",
                    "values": {"pvEgy": 123},
                },
            },
        )
    monkeypatch.setattr(module, "_store", lambda _hass: store)

    caller = hass.async_create_task(save_coro)
    await store.save_started.wait()
    caller.cancel()
    with pytest.raises(asyncio.CancelledError):
        await caller

    store.release_save.set()
    await hass.async_block_till_done()

    assert store.data is not None
    assert "entry_cancel" in store.data["entries"]


@pytest.mark.asyncio
async def test_mqtt_session_cache_flow(hass: HomeAssistant) -> None:
    """Test save, load, and clear MQTT session cache."""
    # Initially None
    sess = await async_load_mqtt_session(hass, "entry_mqtt")
    assert sess is None

    # Save session
    await async_save_mqtt_session(
        hass,
        "entry_mqtt",
        user_id="user_abc",
        seed_b64=_VALID_SEED_B64,
        mac_id="AA:BB:CC:DD:EE:FF",
        mac_id_source="cloud",
        cached_at=1700000000.0,
    )

    # Load session
    loaded = await async_load_mqtt_session(hass, "entry_mqtt")
    assert loaded is not None
    assert loaded[MQTT_SESSION_USER_ID] == "user_abc"
    assert loaded[MQTT_SESSION_SEED_B64] == _VALID_SEED_B64
    assert loaded[MQTT_SESSION_MAC_ID] == "AA:BB:CC:DD:EE:FF"
    assert loaded[MQTT_SESSION_MAC_ID_SOURCE] == "cloud"

    # Clear session
    await async_clear_mqtt_session(hass, "entry_mqtt")
    cleared = await async_load_mqtt_session(hass, "entry_mqtt")
    assert cleared is None

    # Clearing non-existent entry is safe
    await async_clear_mqtt_session(hass, "entry_non_existent")


@pytest.mark.asyncio
async def test_mqtt_session_cache_survives_runtime_lock_recreation(
    hass: HomeAssistant,
) -> None:
    """Only the transaction lock is runtime state; broker seed data stays on disk."""
    await async_save_mqtt_session(
        hass,
        "entry_restart",
        user_id="user_abc",
        seed_b64=_VALID_SEED_B64,
        mac_id="AA:BB:CC:DD:EE:FF",
        mac_id_source="http_login",
    )

    hass.data.pop(mqtt_session_cache_module._LOCK_KEY, None)

    loaded = await async_load_mqtt_session(hass, "entry_restart")
    assert loaded is not None
    assert loaded[MQTT_SESSION_SEED_B64] == _VALID_SEED_B64


@pytest.mark.parametrize(
    "seed_b64",
    [
        "not-base64!",
        "c2hvcnQ=",
    ],
)
@pytest.mark.asyncio
async def test_mqtt_session_cache_rejects_invalid_aes_seed(
    hass: HomeAssistant,
    seed_b64: str,
) -> None:
    """Offline startup never hydrates malformed or non-AES-256 seed material."""
    entry_id = f"invalid-{seed_b64}"
    await async_save_mqtt_session(
        hass,
        entry_id,
        user_id="user",
        seed_b64=seed_b64,
        mac_id="AABBCCDDEEFF",
    )

    assert await async_load_mqtt_session(hass, entry_id) is None


@pytest.mark.asyncio
async def test_mqtt_session_cache_rejects_explicit_expiry(
    hass: HomeAssistant,
) -> None:
    """An App/server supplied expiry is honored without inventing a max age."""
    await async_save_mqtt_session(
        hass,
        "expired-session",
        user_id="user",
        seed_b64=_VALID_SEED_B64,
        mac_id="AABBCCDDEEFF",
        cached_at=time.time() - 3600,
        expires_at=time.time() - 1,
    )

    assert await async_load_mqtt_session(hass, "expired-session") is None


@pytest.mark.asyncio
@pytest.mark.parametrize("cached_at", [float("nan"), float("inf")])
async def test_mqtt_session_cache_rejects_invalid_cache_timestamp(
    hass: HomeAssistant,
    cached_at: float,
) -> None:
    """Non-finite cache metadata cannot bootstrap a broker session."""
    with pytest.raises(ValueError, match="cached_at"):
        await async_save_mqtt_session(
            hass,
            f"invalid-time-{cached_at}",
            user_id="user",
            seed_b64=_VALID_SEED_B64,
            mac_id="AABBCCDDEEFF",
            cached_at=cached_at,
        )

    assert await async_load_mqtt_session(hass, f"invalid-time-{cached_at}") is None


@pytest.mark.asyncio
async def test_legacy_mqtt_session_has_no_invented_max_age(
    hass: HomeAssistant,
) -> None:
    """A valid old cache remains usable when the App supplied no expiry."""
    await async_save_mqtt_session(
        hass,
        "legacy-session",
        user_id="user",
        seed_b64=_VALID_SEED_B64,
        mac_id="AABBCCDDEEFF",
        cached_at=1700000000.0,
    )

    assert await async_load_mqtt_session(hass, "legacy-session") is not None


@pytest.mark.asyncio
async def test_discovery_cache_rejects_blank_identity_and_empty_record(
    hass: HomeAssistant,
) -> None:
    """Only non-empty device identities with metadata can bootstrap platforms."""
    await async_save_discovery_cache(
        hass,
        "invalid-discovery",
        {
            "": {"model": "ignored"},
            "empty": {},
            "valid": {"model": "SolarVault"},
        },
    )

    assert await async_load_discovery_cache(hass, "invalid-discovery") == {
        "valid": {"model": "SolarVault"},
    }


@pytest.mark.asyncio
async def test_daily_cache_rejects_blank_devices_and_non_finite_counters(
    hass: HomeAssistant,
) -> None:
    """Malformed local counters cannot abort persistence or create ghost devices."""
    await async_save_daily_cache(
        hass,
        "daily-validation",
        snapshots={
            " ": {"day": "2026-08-20", "values": {"pvEgy": 10}},
            "device": {
                "day": "2026-08-20",
                "values": {
                    "valid": 12,
                    "boolean": True,
                    "infinite": float("inf"),
                },
            },
        },
    )

    assert await async_load_daily_cache(hass, "daily-validation") == {
        "device": {"day": "2026-08-20", "values": {"valid": 12}},
    }


def test_mqtt_session_normalization_trims_identifiers() -> None:
    """Whitespace around broker identities never reaches credential generation."""
    normalized = mqtt_session_cache_module.normalize_mqtt_session_snapshot({
        MQTT_SESSION_USER_ID: " user ",
        MQTT_SESSION_SEED_B64: _VALID_SEED_B64,
        MQTT_SESSION_MAC_ID: " AABBCCDDEEFF ",
        MQTT_SESSION_MAC_ID_SOURCE: " api ",
    })

    assert normalized is not None
    assert normalized[MQTT_SESSION_USER_ID] == "user"
    assert normalized[MQTT_SESSION_MAC_ID] == "AABBCCDDEEFF"
    assert normalized[MQTT_SESSION_MAC_ID_SOURCE] == "api"
