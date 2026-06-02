"""Repair flows for Jackery SolarVault."""

import logging
from typing import Any

import voluptuous as vol

from homeassistant import data_entry_flow
from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed

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
        """
        Display a confirmation form to the user and, if the form is submitted, trigger a cloud-data refresh before finishing the repair flow.
        
        Parameters:
            user_input (dict[str, Any] | None): Submitted form data; `None` when rendering the form.
        
        Returns:
            FlowResult: An entry creation result when the user confirms (completes the repair), or a form result to render the confirmation step when `user_input` is `None`.
        """
        if user_input is not None:
            await self._async_force_refresh()
            return self.async_create_entry(data={})
        return self.async_show_form(
            step_id="confirm",
            data_schema=vol.Schema({}),
            description_placeholders=self._description_placeholders,
        )

    async def _async_force_refresh(self) -> None:
        """
        Request a refresh from the integration coordinator to clear transient cloud data inconsistencies.
        
        If no coordinator is available, this does nothing. Re-raises ConfigEntryAuthFailed to surface authentication errors; logs and suppresses any other exception at debug level.
         
        Raises:
            ConfigEntryAuthFailed: If the coordinator reports an authentication failure.
        """
        coordinator = self._coordinator()
        if coordinator is None:
            return
        try:
            await coordinator.async_request_refresh()
        except ConfigEntryAuthFailed:
            raise
        except Exception as err:
            _LOGGER.debug("Force refresh from repair flow failed: %s", err)

    def _coordinator(self) -> JackerySolarVaultCoordinator | None:
        """
        Retrieve the integration coordinator for the stored config entry ID.
        
        Returns:
            JackerySolarVaultCoordinator | None: The coordinator instance for the config entry, or `None` if the entry ID is not set, the config entry cannot be found, or its `runtime_data` is not a `JackerySolarVaultCoordinator`.
        """
        if not self._entry_id:
            return None
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if entry is None:
            return None
        coordinator = getattr(entry, "runtime_data", None)
        if isinstance(coordinator, JackerySolarVaultCoordinator):
            return coordinator
        return None


async def async_create_fix_flow(  # noqa: RUF029  # HA awaits this entry point
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """
    Selects the appropriate RepairsFlow implementation for the provided issue identifier.
    
    Parameters:
    	issue_id (str): Issue identifier provided by Home Assistant; used to determine which repair flow to create.
    	data (dict[str, Any] | None): Optional issue-specific data. When present, may include "entry_id" (config entry id) and placeholder values "count", "metric", and "examples" used to configure the repair flow's description.
    
    Returns:
    	RepairsFlow: A RepairsFlow instance configured to handle the specified issue.
    
    Raises:
    	data_entry_flow.UnknownFlow: If no repair flow is registered for the given issue_id under this integration's domain.
    """
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
