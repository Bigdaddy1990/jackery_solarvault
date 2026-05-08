"""Repair flows for Jackery SolarVault."""

import logging
from typing import Any

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
import voluptuous as vol

from .const import DOMAIN, REPAIR_ISSUE_APP_DATA_INCONSISTENCY
from .coordinator import JackerySolarVaultCoordinator

_LOGGER = logging.getLogger(__name__)


class AppDataInconsistencyRepairFlow(RepairsFlow):
    """Confirmation-only fix flow for the app/cloud data inconsistency issue.

    The issue cannot be repaired by the integration: the contradiction lives
    in Jackery's cloud reporting, not in HA state. The fix flow forces a
    refresh so transient inconsistencies clear themselves and the user is
    kept informed about the underlying source of truth.
    """

    def __init__(
        self, entry_id: str | None, description_placeholders: dict[str, str]
    ) -> None:
        """Initialize the repair flow for one config entry."""
        self._entry_id = entry_id
        self._description_placeholders = description_placeholders

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Route the initial repair step to the confirmation form."""
        return await self.async_step_confirm()

    async def async_step_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> data_entry_flow.FlowResult:
        """Show the confirmation form and refresh cloud data after submit."""
        if user_input is not None:
            await self._async_force_refresh()
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders=self._description_placeholders,
        )

    async def _async_force_refresh(self) -> None:
        coordinator = self._coordinator()
        if coordinator is None:
            return
        try:
            await coordinator.async_request_refresh()
        except Exception as err:
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


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Return the matching repair flow for an issue raised by this integration."""
    if issue_id.endswith(f"_{REPAIR_ISSUE_APP_DATA_INCONSISTENCY}"):
        issue_data = data or {}
        entry_id = issue_data.get("entry_id")
        description_placeholders = {
            "count": str(issue_data.get("count", "unknown")),
            "metric": str(issue_data.get("metric", "unknown")),
            "examples": str(issue_data.get("examples", "unknown")),
        }
        return AppDataInconsistencyRepairFlow(entry_id, description_placeholders)
    raise data_entry_flow.UnknownFlow(
        f"No repair flow registered for issue '{issue_id}' under domain '{DOMAIN}'"
    )
