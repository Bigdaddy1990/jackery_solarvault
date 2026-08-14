"""Repair flows for Jackery SolarVault."""

import logging
from typing import Any, cast

import voluptuous as vol

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

from .const import DOMAIN, REPAIR_ISSUE_DEVICE_NOT_ACTIVATED
from .coordinator import BACKGROUND_TASK_ERRORS, JackerySolarVaultCoordinator

_LOGGER = logging.getLogger(__name__)


class DeviceNotActivatedRepairFlow(RepairsFlow):
    """Confirmation-only fix flow for the cloud activation-flag issue.

    The device reports activated=0 in the cloud system. Treat this as a
    cloud-side data-quality flag, not proof that the device is unpaired
    locally. The fix flow forces a refresh so the integration can re-check
    whether Jackery still returns the inconsistent flag.
    """

    def __init__(
        self,
        entry_id: str | None,
        description_placeholders: dict[str, str],
    ) -> None:
        """Initialize the repair flow for one config entry."""
        self._entry_id = entry_id
        self._description_placeholders = description_placeholders

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> data_entry_flow.FlowResult:
        """Route the initial repair step to the confirmation form."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> data_entry_flow.FlowResult:
        """Show the confirmation form and refresh cloud data after submit."""
        if user_input is not None:
            await self._async_force_refresh()
            return cast("data_entry_flow.FlowResult", self.async_create_entry(data={}))
        return cast(
            "data_entry_flow.FlowResult",
            self.async_show_form(
                step_id="confirm",
                data_schema=vol.Schema({}),
                description_placeholders=self._description_placeholders,
            ),
        )

    async def _async_force_refresh(self) -> None:
        coordinator = self._coordinator()
        if coordinator is None:
            return
        try:
            await coordinator.async_request_refresh()
        except ConfigEntryAuthFailed:
            raise
        except BACKGROUND_TASK_ERRORS as err:
            _LOGGER.debug("Force refresh from repair flow failed: %s", err)

    def _coordinator(self) -> JackerySolarVaultCoordinator | None:
        if not self._entry_id:
            return None
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return None
        coordinator = getattr(entry, "runtime_data", None)
        if isinstance(coordinator, JackerySolarVaultCoordinator):
            return coordinator
        return None


async def async_create_fix_flow(  # ruff: ignore[unused-async]  # HA requires an async fix hook.
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Return the matching repair flow for an issue raised by this integration."""
    if issue_id.endswith(f"_{REPAIR_ISSUE_DEVICE_NOT_ACTIVATED}"):
        issue_data = data or {}
        entry_id = issue_data.get("entry_id")
        device_id = issue_data.get("device_id", "unknown")
        description_placeholders = {
            "device_id": device_id,
        }
        return DeviceNotActivatedRepairFlow(entry_id, description_placeholders)
    msg = f"No repair flow registered for issue '{issue_id}' under domain '{DOMAIN}'"
    raise data_entry_flow.UnknownFlow(msg)
