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
        """Show a confirmation form, and after the user submits, refresh integration data and complete the repair.

        Returns:
            FlowResult: Presents the confirmation form when `user_input` is None, or completes the repair by creating an empty repair entry after submission.
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
        """Request a data refresh from the integration coordinator associated with this repair flow.

        If no coordinator can be resolved for the stored config entry id, the function returns without action. Authentication errors during the refresh are propagated; all other exceptions are caught, logged at debug level, and suppressed.

        Raises:
            ConfigEntryAuthFailed: If the config entry authentication failed during refresh.
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
        """Retrieve the runtime coordinator for the associated config entry.

        Looks up the config entry identified by the stored entry ID and returns its `runtime_data` only if it is a `JackerySolarVaultCoordinator`.

        Returns:
            JackerySolarVaultCoordinator | None: The coordinator for the stored entry, or `None` if the entry is missing or its `runtime_data` is not a `JackerySolarVaultCoordinator`.
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


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, Any] | None,
) -> RepairsFlow:
    """Constructs the appropriate repair flow for a reported integration issue.

    Parameters:
        issue_id (str): Identifier of the reported issue; the function selects a flow when the ID ends with
            the app-data-inconsistency marker.
        data (dict[str, Any] | None): Optional issue payload. When creating an AppDataInconsistencyRepairFlow,
            expected keys include `entry_id` (config entry id), `count`, `metric`, and `examples`; missing keys
            are represented as the string "unknown" in the flow's description placeholders.

    Returns:
        RepairsFlow: A repairs flow instance tailored to the specified issue (e.g., AppDataInconsistencyRepairFlow).

    Raises:
        data_entry_flow.UnknownFlow: If no repair flow is registered for the given `issue_id` under the integration domain.
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
        f"No repair flow registered for issue '{issue_id}' under domain '{DOMAIN}'",
    )
