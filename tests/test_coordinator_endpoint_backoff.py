"""Tests for coordinator endpoint backoff methods.

These drive the endpoint backoff logic through its various state transitions
using realistic cloud error scenarios. All internal backoff logic runs for real;
only the time source and cloud error payloads are controlled.

The scenarios assert business outcomes (what backoff state results from which
error sequence), never call order:
"""

from time import monotonic
from unittest.mock import MagicMock

import pytest

from custom_components.jackery_solarvault.client.api import JackeryError
from custom_components.jackery_solarvault.coordinator import (
    _ENDPOINT_BACKOFF_DELAYS_SEC,  # noqa: PLC2701, RUF105
    _ENDPOINT_BACKOFF_ENERGY_DELAYS_SEC,  # noqa: PLC2701, RUF105
    _ENDPOINT_BACKOFF_ENERGY_KEY_PARTS,  # noqa: PLC2701, RUF105
    _ENDPOINT_BACKOFF_RATELIMIT_DELAYS_SEC,  # noqa: PLC2701, RUF105
    _ENDPOINT_BACKOFF_TIMEOUT_CODE,  # noqa: PLC2701, RUF105
    _SHELLY_REALTIME_BACKOFF_PREFIX,  # noqa: PLC2701, RUF105
    JackerySolarVaultCoordinator,
)
from tests._update_cycle_fixture import (  # ruff:ignore[banned-api]
    make_update_cycle_api,
    setup_update_cycle_coordinator,
)


async def _teardown(hass, entry_id) -> None:  # noqa: RUF105
    """Unload the entry and drain background tasks."""
    await hass.config_entries.async_unload(entry_id)
    await hass.async_block_till_done()


@pytest.fixture
async def coordinator(hass):  # noqa: RUF105
    """Yield a coordinator with mocked api for backoff tests."""
    api = make_update_cycle_api()
    coord, entry, _api = await setup_update_cycle_coordinator(
        hass, api=api, discover=True
    )
    yield coord
    await _teardown(hass, entry.entry_id)


@pytest.mark.asyncio
async def test_endpoint_backoff_active_returns_false_when_no_state(coordinator) -> None:  # noqa: RUF029, RUF105
    """An endpoint with no recorded state is not in backoff."""
    now = monotonic()
    assert coordinator._endpoint_backoff_active("test_key", now) is False  # noqa: RUF105, SLF001


@pytest.mark.asyncio
async def test_endpoint_backoff_active_returns_false_when_state_expired(  # noqa: RUF029, RUF105
    coordinator,  # noqa: RUF105
) -> None:
    """An endpoint with expired backoff returns False."""
    now = monotonic()
    coordinator._endpoint_backoff["test_key"] = {  # noqa: RUF105, SLF001
        "code": 10422,
        "level": 0,
        "until": now - 10,  # expired
    }
    assert coordinator._endpoint_backoff_active("test_key", now) is False  # noqa: RUF105, SLF001


@pytest.mark.asyncio
async def test_endpoint_backoff_active_returns_true_when_active(coordinator) -> None:  # noqa: RUF029, RUF105
    """An endpoint with active backoff returns True."""
    now = monotonic()
    coordinator._endpoint_backoff["test_key"] = {  # noqa: RUF105, SLF001
        "code": 10422,
        "level": 0,
        "until": now + 10,  # active
    }
    assert coordinator._endpoint_backoff_active("test_key", now) is True  # noqa: RUF105, SLF001


@pytest.mark.asyncio
async def test_endpoint_backoff_active_returns_false_for_energy_keys(  # noqa: RUF029, RUF105
    coordinator,  # noqa: RUF105
) -> None:
    """Energy/stat keys are never considered in backoff."""
    now = monotonic()
    for part in _ENDPOINT_BACKOFF_ENERGY_KEY_PARTS:
        coordinator._endpoint_backoff[f"{part}_key"] = {  # noqa: RUF105, SLF001
            "code": 10422,
            "level": 0,
            "until": now + 10,
        }
        assert coordinator._endpoint_backoff_active(f"{part}_key", now) is False  # noqa: RUF105, SLF001


@pytest.mark.asyncio
async def test_endpoint_backoff_is_energy_key(coordinator) -> None:  # noqa: RUF029, RUF105
    """Energy key detection matches all configured parts."""
    for part in _ENDPOINT_BACKOFF_ENERGY_KEY_PARTS:
        assert (
            JackerySolarVaultCoordinator._endpoint_backoff_is_energy_key(  # noqa: RUF105, SLF001
                f"test_{part}_suffix"
            )
            is True
        )
    assert (
        JackerySolarVaultCoordinator._endpoint_backoff_is_energy_key("other_key")  # noqa: RUF105, SLF001
        is False
    )


@pytest.mark.asyncio
async def test_endpoint_backoff_delays_for_key_energy(coordinator) -> None:  # noqa: RUF029, RUF105
    """Energy keys use the short energy ladder."""
    delays = JackerySolarVaultCoordinator._endpoint_backoff_delays_for_key(  # noqa: RUF105, SLF001
        "battery_stat_key", 10000
    )
    assert delays == _ENDPOINT_BACKOFF_ENERGY_DELAYS_SEC


@pytest.mark.asyncio
async def test_endpoint_backoff_delays_for_key_ratelimit(coordinator) -> None:  # noqa: RUF029, RUF105
    """Rate-limited keys (10426) use the short rate-limit ladder."""
    delays = JackerySolarVaultCoordinator._endpoint_backoff_delays_for_key(  # noqa: RUF105, SLF001
        "some_key", 10426
    )
    assert delays == _ENDPOINT_BACKOFF_RATELIMIT_DELAYS_SEC


@pytest.mark.asyncio
async def test_endpoint_backoff_delays_for_key_default(coordinator) -> None:  # noqa: RUF029, RUF105
    """Other keys use the default long ladder."""
    delays = JackerySolarVaultCoordinator._endpoint_backoff_delays_for_key(  # noqa: RUF105, SLF001
        "other_key", 10001
    )
    assert delays == _ENDPOINT_BACKOFF_DELAYS_SEC


@pytest.mark.asyncio
async def test_endpoint_backoff_note_failure_records_backoff(coordinator) -> None:  # noqa: RUF029, RUF105
    """A matching error code enters backoff at level 0."""
    now = monotonic()
    err = JackeryError("test error code=10422")
    result = coordinator._endpoint_backoff_note_failure("test_key", err)  # noqa: RUF105, SLF001
    assert result is True
    state = coordinator._endpoint_backoff["test_key"]  # noqa: RUF105, SLF001
    assert state["code"] == 10422  # noqa: RUF105
    assert state["level"] == 0
    assert state["until"] > now


@pytest.mark.asyncio
async def test_endpoint_backoff_note_failure_escalates_level_on_repeat(  # noqa: RUF029, RUF105
    coordinator,  # noqa: RUF105
) -> None:
    """Repeated same error escalates backoff level."""
    err = JackeryError("test error code=10422")
    coordinator._endpoint_backoff_note_failure("test_key", err)  # noqa: RUF105, SLF001
    coordinator._endpoint_backoff_note_failure("test_key", err)  # noqa: RUF105, SLF001
    state = coordinator._endpoint_backoff["test_key"]  # noqa: RUF105, SLF001
    assert state["level"] == 1


@pytest.mark.asyncio
async def test_endpoint_backoff_note_failure_resets_level_on_different_code(  # noqa: RUF029, RUF105
    coordinator,  # noqa: RUF105
) -> None:
    """Different error code resets level to 0."""
    coordinator._endpoint_backoff_note_failure("test_key", JackeryError("code=10422"))  # noqa: RUF105, SLF001
    coordinator._endpoint_backoff_note_failure("test_key", JackeryError("code=10432"))  # noqa: RUF105, SLF001
    state = coordinator._endpoint_backoff["test_key"]  # noqa: RUF105, SLF001
    assert state["level"] == 0
    assert state["code"] == 10432  # noqa: RUF105


@pytest.mark.asyncio
async def test_endpoint_backoff_note_failure_ignores_unknown_codes(coordinator) -> None:  # noqa: RUF029, RUF105
    """Non-backoffable codes return False and don't record state."""
    err = JackeryError("test error code=99999")
    result = coordinator._endpoint_backoff_note_failure("test_key", err)  # noqa: RUF105, SLF001
    assert result is False
    assert "test_key" not in coordinator._endpoint_backoff  # noqa: RUF105, SLF001


@pytest.mark.asyncio
async def test_endpoint_backoff_note_failure_extracts_code_from_message(  # noqa: RUF029, RUF105
    coordinator,  # noqa: RUF105
) -> None:
    """Code is extracted from error message via regex."""
    err = JackeryError("Cloud error: code=10422 details")
    result = coordinator._endpoint_backoff_note_failure("test_key", err)  # noqa: RUF105, SLF001
    assert result is True
    state = coordinator._endpoint_backoff["test_key"]  # noqa: RUF105, SLF001
    assert state["code"] == 10422  # noqa: RUF105


@pytest.mark.asyncio
async def test_endpoint_backoff_note_failure_handles_non_numeric_code(  # noqa: RUF029, RUF105
    coordinator,  # noqa: RUF105
) -> None:
    """Non-numeric code in message is handled gracefully."""
    err = JackeryError("Cloud error: code=abc")
    result = coordinator._endpoint_backoff_note_failure("test_key", err)  # noqa: RUF105, SLF001
    assert result is False


@pytest.mark.asyncio
async def test_endpoint_backoff_note_failure_ignores_empty_code_match(  # noqa: RUF029, RUF105
    coordinator,  # noqa: RUF105
) -> None:
    """Error without code pattern is ignored."""
    err = JackeryError("Cloud error: no code here")
    result = coordinator._endpoint_backoff_note_failure("test_key", err)  # noqa: RUF105, SLF001
    assert result is False


@pytest.mark.asyncio
async def test_endpoint_backoff_note_timeout_opens_window(coordinator) -> None:  # noqa: RUF029, RUF105
    """Timeout opens backoff window with timeout sentinel code."""
    now = monotonic()
    coordinator._endpoint_backoff_note_timeout("test_key")  # noqa: RUF105, SLF001
    state = coordinator._endpoint_backoff["test_key"]  # noqa: RUF105, SLF001
    assert state["code"] == _ENDPOINT_BACKOFF_TIMEOUT_CODE
    assert state["level"] == 0
    assert state["until"] > now


@pytest.mark.asyncio
async def test_endpoint_backoff_note_timeout_escalates(coordinator) -> None:  # noqa: RUF029, RUF105
    """Repeated timeout escalates level."""
    coordinator._endpoint_backoff_note_timeout("test_key")  # noqa: RUF105, SLF001
    coordinator._endpoint_backoff_note_timeout("test_key")  # noqa: RUF105, SLF001
    state = coordinator._endpoint_backoff["test_key"]  # noqa: RUF105, SLF001
    assert state["level"] == 1


@pytest.mark.asyncio
async def test_endpoint_backoff_note_success_clears_state(coordinator) -> None:  # noqa: RUF029, RUF105
    """Success clears backoff state for the key."""
    err = JackeryError("code=10000")
    coordinator._endpoint_backoff_note_failure("test_key", err)  # noqa: RUF105, SLF001
    coordinator._endpoint_backoff_note_success("test_key")  # noqa: RUF105, SLF001
    assert "test_key" not in coordinator._endpoint_backoff  # noqa: RUF105, SLF001


@pytest.mark.asyncio
async def test_endpoint_backoff_note_success_idempotent(coordinator) -> None:  # noqa: RUF029, RUF105
    """Success on non-existent key is no-op."""
    coordinator._endpoint_backoff_note_success("nonexistent")  # noqa: RUF105, SLF001
    assert "nonexistent" not in coordinator._endpoint_backoff  # noqa: RUF105, SLF001


@pytest.mark.asyncio
async def test_endpoint_backoff_note_success_clears_timeout_backoff(  # noqa: RUF029, RUF105
    coordinator,  # noqa: RUF105
) -> None:
    """Success clears timeout-based backoff."""
    coordinator._endpoint_backoff_note_timeout("test_key")  # noqa: RUF105, SLF001
    coordinator._endpoint_backoff_note_success("test_key")  # noqa: RUF105, SLF001
    assert "test_key" not in coordinator._endpoint_backoff  # noqa: RUF105, SLF001


@pytest.mark.asyncio
async def test_endpoint_backoff_active_count(coordinator) -> None:  # noqa: RUF029, RUF105
    """Count of active backoff keys."""
    now = monotonic()
    coordinator._endpoint_backoff["active1"] = {  # noqa: RUF105, SLF001
        "code": 10000,
        "level": 0,
        "until": now + 10,
    }
    coordinator._endpoint_backoff["active2"] = {  # noqa: RUF105, SLF001
        "code": 10000,
        "level": 0,
        "until": now + 10,
    }
    coordinator._endpoint_backoff["expired"] = {  # noqa: RUF105, SLF001
        "code": 10000,
        "level": 0,
        "until": now - 10,
    }
    # Energy keys don't count
    for part in _ENDPOINT_BACKOFF_ENERGY_KEY_PARTS:
        coordinator._endpoint_backoff[f"{part}_key"] = {  # noqa: RUF105, SLF001
            "code": 10000,
            "level": 0,
            "until": now + 10,
        }
    assert coordinator._endpoint_backoff_active_count(now) == 2  # noqa: RUF105, SLF001


@pytest.mark.asyncio
async def test_is_backoffable_timeout_detects_shelly_timeout(coordinator) -> None:  # noqa: RUF029, RUF105
    """Shelly realtime timeout is detected as backoffable."""
    err = JackeryError("Shelly timeout")
    err.__cause__ = TimeoutError("Shelly timed out")
    assert (
        coordinator._is_backoffable_timeout(  # noqa: RUF105, SLF001
            f"{_SHELLY_REALTIME_BACKOFF_PREFIX}test", err
        )
        is True
    )


@pytest.mark.asyncio
async def test_is_backoffable_timeout_ignores_non_shelly_timeout(coordinator) -> None:  # noqa: RUF029, RUF105
    """Non-Shelly timeouts are not backoffable."""
    err = JackeryError("Other timeout")
    err.__cause__ = TimeoutError("Other timed out")
    assert coordinator._is_backoffable_timeout("other_key", err) is False  # noqa: RUF105, SLF001


@pytest.mark.asyncio
async def test_is_backoffable_timeout_ignores_non_timeout_cause(coordinator) -> None:  # noqa: RUF029, RUF105
    """Non-TimeoutError causes are not backoffable."""
    err = JackeryError("Shelly error")
    err.__cause__ = ValueError("not timeout")
    assert (
        coordinator._is_backoffable_timeout(  # noqa: RUF105, SLF001
            f"{_SHELLY_REALTIME_BACKOFF_PREFIX}test", err
        )
        is False
    )


@pytest.mark.asyncio
async def test_is_backoffable_timeout_ignores_none_cause(coordinator) -> None:  # noqa: RUF029, RUF105
    """None cause is not backoffable."""
    err = JackeryError("Shelly error")
    err.__cause__ = None
    assert (
        coordinator._is_backoffable_timeout(  # noqa: RUF105, SLF001
            f"{_SHELLY_REALTIME_BACKOFF_PREFIX}test", err
        )
        is False
    )


@pytest.mark.asyncio
async def test_is_backoffable_timeout_with_none_backoff_key(coordinator) -> None:  # noqa: RUF029, RUF105
    """None backoff key is not backoffable."""
    err = JackeryError("Shelly timeout")
    err.__cause__ = TimeoutError("Shelly timed out")
    assert coordinator._is_backoffable_timeout(None, err) is False  # noqa: RUF105, SLF001


@pytest.mark.asyncio
async def test_endpoint_backoff_note_failure_handles_type_error_in_code_extraction(  # noqa: RUF029, RUF105
    coordinator,  # noqa: RUF105
) -> None:
    """TypeError during code extraction is handled."""
    err = JackeryError("code=10422")
    import custom_components.jackery_solarvault.coordinator as coordinator_module  # noqa: PLC0415, RUF105

    original_search = coordinator_module.re.search
    try:
        # The actual code catches exceptions from int(), not from re.search()
        # So we mock the group() call to raise TypeError when int() is called
        mock_match = MagicMock()
        mock_match.group.side_effect = TypeError("test")
        coordinator_module.re.search = lambda *args, **kwargs: mock_match
        result = coordinator._endpoint_backoff_note_failure("test_key", err)  # noqa: RUF105, SLF001
        assert result is False
    finally:
        coordinator_module.re.search = original_search


@pytest.mark.asyncio
async def test_endpoint_backoff_note_failure_handles_value_error_in_code_extraction(  # noqa: RUF029, RUF105
    coordinator,  # noqa: RUF105
) -> None:
    """ValueError during code extraction is handled."""
    err = JackeryError("code=10422")
    import custom_components.jackery_solarvault.coordinator as coordinator_module  # noqa: PLC0415, RUF105

    original_search = coordinator_module.re.search
    try:
        mock_match = MagicMock()
        mock_match.group.side_effect = ValueError("test")
        coordinator_module.re.search = lambda *args, **kwargs: mock_match
        result = coordinator._endpoint_backoff_note_failure("test_key", err)  # noqa: RUF105, SLF001
        assert result is False
    finally:
        coordinator_module.re.search = original_search
