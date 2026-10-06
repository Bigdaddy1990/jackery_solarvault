"""Repair flows must not dismiss an unresolved issue or a failed refresh.

Exercise real flow/registry behavior while controlling the HA refresh boundary.
"""

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import AsyncMock

import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.jackery_solarvault.const import (
    DOMAIN,
    REPAIR_ISSUE_DEVICE_NOT_ACTIVATED,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from custom_components.jackery_solarvault.repairs import (
    DeviceNotActivatedRepairFlow,
    async_create_fix_flow,
)
from homeassistant.data_entry_flow import FlowResultType, UnknownFlow
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers import issue_registry as ir

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

_FLOW_CLASSES: tuple[type[DeviceNotActivatedRepairFlow], ...] = (
    DeviceNotActivatedRepairFlow,
)


def _bare_coordinator() -> tuple[JackerySolarVaultCoordinator, AsyncMock]:
    """Build a coordinator double whose refresh call is fully controllable."""
    coordinator = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    refresh = AsyncMock()
    cast("Any", coordinator).async_request_refresh = refresh
    cast("Any", coordinator).async_refresh = refresh
    coordinator.last_update_success = True
    return coordinator, refresh


def _entry_with_coordinator(
    hass: HomeAssistant,
    coordinator: JackerySolarVaultCoordinator,
) -> MockConfigEntry:
    """Register a config entry in hass with runtime_data wired to coordinator."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    entry.runtime_data = coordinator
    return entry


# --- async_step_init / async_step_confirm routing --------------------------


@pytest.mark.parametrize("flow_cls", _FLOW_CLASSES)
async def test_init_step_routes_to_the_confirm_form(
    hass: HomeAssistant,
    flow_cls: type[DeviceNotActivatedRepairFlow],
) -> None:
    """The init step is a pass-through that immediately shows confirmation."""
    flow = flow_cls(None, {"key": "value"}, "test-issue")
    flow.hass = hass

    result = await flow.async_step_init()

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"] == {"key": "value"}


@pytest.mark.parametrize("flow_cls", _FLOW_CLASSES)
async def test_confirm_step_without_input_shows_the_form(
    hass: HomeAssistant,
    flow_cls: type[DeviceNotActivatedRepairFlow],
) -> None:
    """Calling confirm with no submission redisplays the confirmation form."""
    flow = flow_cls(None, {"key": "value"}, "test-issue")
    flow.hass = hass

    result = await flow.async_step_confirm()

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "confirm"
    assert result["description_placeholders"] == {"key": "value"}


# --- confirm submission: refresh + completion -------------------------------


@pytest.mark.parametrize("flow_cls", _FLOW_CLASSES)
async def test_confirm_submission_refreshes_the_coordinator_and_completes(
    hass: HomeAssistant,
    flow_cls: type[DeviceNotActivatedRepairFlow],
) -> None:
    """Submitting the form refreshes cloud data and finishes the flow."""
    coordinator, refresh = _bare_coordinator()
    entry = _entry_with_coordinator(hass, coordinator)
    flow = flow_cls(entry.entry_id, {}, "test-issue")
    flow.hass = hass

    result = await flow.async_step_confirm({})

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {}
    refresh.assert_awaited_once()


@pytest.mark.parametrize("flow_cls", _FLOW_CLASSES)
async def test_confirm_submission_keeps_issue_open_after_refresh_error(
    hass: HomeAssistant,
    flow_cls: type[DeviceNotActivatedRepairFlow],
) -> None:
    """A failed refresh remains retryable and must not pretend to repair the issue."""
    coordinator, refresh = _bare_coordinator()
    refresh.side_effect = TimeoutError("cloud stalled")
    entry = _entry_with_coordinator(hass, coordinator)
    flow = flow_cls(entry.entry_id, {}, "test-issue")
    flow.hass = hass

    result = await flow.async_step_confirm({})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "refresh_failed"}


@pytest.mark.parametrize("flow_cls", _FLOW_CLASSES)
async def test_confirm_submission_propagates_auth_failure(
    hass: HomeAssistant,
    flow_cls: type[DeviceNotActivatedRepairFlow],
) -> None:
    """Auth failures are not background-task noise; they must propagate.

    Unlike a transient cloud error, ``ConfigEntryAuthFailed`` means the
    integration needs reauth, so the fix flow must not swallow it.
    """
    coordinator, refresh = _bare_coordinator()
    refresh.side_effect = ConfigEntryAuthFailed("expired")
    entry = _entry_with_coordinator(hass, coordinator)
    flow = flow_cls(entry.entry_id, {}, "test-issue")
    flow.hass = hass

    with pytest.raises(ConfigEntryAuthFailed):
        await flow.async_step_confirm({})


# --- confirm submission: coordinator lookup skips refresh cleanly ----------


@pytest.mark.parametrize("flow_cls", _FLOW_CLASSES)
async def test_confirm_submission_without_entry_id_stays_open(
    hass: HomeAssistant,
    flow_cls: type[DeviceNotActivatedRepairFlow],
) -> None:
    """An unavailable integration cannot claim that a cloud repair succeeded."""
    flow = flow_cls(None, {}, "test-issue")
    flow.hass = hass

    result = await flow.async_step_confirm({})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "integration_unavailable"}


@pytest.mark.parametrize("flow_cls", _FLOW_CLASSES)
async def test_confirm_submission_with_unknown_entry_id_stays_open(
    hass: HomeAssistant,
    flow_cls: type[DeviceNotActivatedRepairFlow],
) -> None:
    """An unavailable integration cannot claim that a cloud repair succeeded."""
    flow = flow_cls("stale-entry-id", {}, "test-issue")
    flow.hass = hass

    result = await flow.async_step_confirm({})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "integration_unavailable"}


@pytest.mark.parametrize("flow_cls", _FLOW_CLASSES)
async def test_confirm_submission_with_unset_up_entry_stays_open(
    hass: HomeAssistant,
    flow_cls: type[DeviceNotActivatedRepairFlow],
) -> None:
    """An unavailable integration cannot claim that a cloud repair succeeded."""
    entry = MockConfigEntry(domain=DOMAIN)
    entry.add_to_hass(hass)
    flow = flow_cls(entry.entry_id, {}, "test-issue")
    flow.hass = hass

    result = await flow.async_step_confirm({})

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "integration_unavailable"}


# --- async_create_fix_flow dispatch -----------------------------------------


async def test_dispatches_device_not_activated_issues(
    hass: HomeAssistant,
) -> None:
    """An issue id ending in the device-not-activated suffix builds that flow."""
    issue_id = f"entry-1_dev-1_{REPAIR_ISSUE_DEVICE_NOT_ACTIVATED}"
    data = {"entry_id": "entry-1", "device_id": "dev-1"}

    flow = await async_create_fix_flow(hass, issue_id, data)

    assert isinstance(flow, DeviceNotActivatedRepairFlow)
    flow.hass = hass
    result = await flow.async_step_init()
    assert result["description_placeholders"] == {"device_id": "dev-1"}


@pytest.mark.parametrize(
    ["suffix", "expected_flow_cls", "expected_placeholders"],
    [
        [
            REPAIR_ISSUE_DEVICE_NOT_ACTIVATED,
            DeviceNotActivatedRepairFlow,
            {"device_id": "unknown"},
        ],
    ],
)
async def test_dispatch_defaults_missing_issue_data_to_unknown(
    hass: HomeAssistant,
    suffix: str,
    expected_flow_cls: type[DeviceNotActivatedRepairFlow],
    expected_placeholders: dict[str, str],
) -> None:
    """Missing ``data`` (None) still builds a flow with 'unknown' placeholders."""
    issue_id = f"entry-1_{suffix}"

    flow = await async_create_fix_flow(hass, issue_id, None)

    assert isinstance(flow, expected_flow_cls)
    flow.hass = hass
    result = await flow.async_step_init()
    assert result["description_placeholders"] == expected_placeholders


async def test_dispatch_raises_for_an_unregistered_issue_id(
    hass: HomeAssistant,
) -> None:
    """An issue id matching neither known suffix raises UnknownFlow."""
    with pytest.raises(UnknownFlow, match=f"unmapped_issue.*{DOMAIN}"):
        await async_create_fix_flow(hass, "unmapped_issue", {})


async def test_failed_update_flag_keeps_repair_open(hass: HomeAssistant) -> None:
    """HA can consume an update exception and only mark the coordinator failed."""
    coordinator, _refresh = _bare_coordinator()
    coordinator.last_update_success = False
    entry = _entry_with_coordinator(hass, coordinator)
    flow = DeviceNotActivatedRepairFlow(entry.entry_id, {}, "test-issue")
    flow.hass = hass
    result = await flow.async_step_confirm({})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "refresh_failed"}


async def test_successful_refresh_with_unresolved_issue_stays_open(
    hass: HomeAssistant,
) -> None:
    """A successful HTTP request alone does not resolve the activation flag."""
    coordinator, _refresh = _bare_coordinator()
    entry = _entry_with_coordinator(hass, coordinator)
    issue_id = f"{entry.entry_id}_device_{REPAIR_ISSUE_DEVICE_NOT_ACTIVATED}"
    ir.async_create_issue(
        hass,
        DOMAIN,
        issue_id,
        is_fixable=True,
        severity=ir.IssueSeverity.WARNING,
        translation_key=REPAIR_ISSUE_DEVICE_NOT_ACTIVATED,
    )
    flow = await async_create_fix_flow(
        hass, issue_id, {"entry_id": entry.entry_id, "device_id": "device"}
    )
    assert isinstance(flow, DeviceNotActivatedRepairFlow)
    flow.hass = hass
    result = await flow.async_step_confirm({})
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "issue_still_present"}
    assert ir.async_get(hass).async_get_issue(DOMAIN, issue_id) is not None
