"""Behavioural tests for portable/Explorer powerstation switch setters.

This test file covers the discharge-memory portable switch dispatch.

It verifies that the ``_set_portable_discharge_memory`` setter dispatches the
``SETTING_DISCHARGE_MEMORY`` portable command (msgId 53, field ``"dl"``) using
the named ``ACTION_ID_PORTABLE_DISCHARGE_MEMORY`` constant rather than a magic
literal, mirroring the dispatch-assertion pattern used for the portable buttons
in ``test_portable_button_outcomes.py``.
"""

from unittest.mock import AsyncMock, MagicMock

from custom_components.jackery_solarvault.const import (
    ACTION_ID_PORTABLE_DISCHARGE_MEMORY,
)
from custom_components.jackery_solarvault.switch import (
    SWITCH_DESCRIPTIONS,
    _set_portable_discharge_memory,  # noqa: PLC2701
)

# Source-of-truth wire value: b.java SETTING_DISCHARGE_MEMORY has msgId 53.
_DISCHARGE_MEMORY_MSG_ID = 53

# Source-of-truth payload field for discharge memory: PortableBody key "dl".
_DISCHARGE_MEMORY_FIELD = "dl"


class TestPortableDischargeMemoryConstant:
    """The named discharge-memory action id must match the source-of-truth."""

    def test_discharge_memory_action_id_is_53(self) -> None:  # noqa: PLR6301
        """ACTION_ID_PORTABLE_DISCHARGE_MEMORY must equal msgId 53 (b.java)."""
        assert ACTION_ID_PORTABLE_DISCHARGE_MEMORY == _DISCHARGE_MEMORY_MSG_ID


class TestPortableDischargeMemorySetter:
    """The discharge-memory setter must dispatch the named action id."""

    async def test_enable_dispatches_named_action_id_and_dl_field(self) -> None:  # noqa: PLR6301
        """Enabling must toggle field 'dl' with the named discharge-memory id."""
        coordinator = MagicMock()
        coordinator.async_portable_toggle_output = AsyncMock()

        await _set_portable_discharge_memory(coordinator, "dev_dl_on", value=True)

        coordinator.async_portable_toggle_output.assert_called_once_with(
            "dev_dl_on",
            action_id=ACTION_ID_PORTABLE_DISCHARGE_MEMORY,
            field=_DISCHARGE_MEMORY_FIELD,
            enabled=True,
        )

    async def test_disable_dispatches_named_action_id_and_dl_field(self) -> None:  # noqa: PLR6301
        """Disabling must toggle field 'dl' with the named discharge-memory id."""
        coordinator = MagicMock()
        coordinator.async_portable_toggle_output = AsyncMock()

        await _set_portable_discharge_memory(coordinator, "dev_dl_off", value=False)

        coordinator.async_portable_toggle_output.assert_called_once_with(
            "dev_dl_off",
            action_id=ACTION_ID_PORTABLE_DISCHARGE_MEMORY,
            field=_DISCHARGE_MEMORY_FIELD,
            enabled=False,
        )


class TestPortableDischargeMemoryDescription:
    """Regression: the discharge-memory switch description must stay wired."""

    def test_discharge_memory_switch_present_with_dl_source(self) -> None:  # noqa: PLR6301
        """The portable_discharge_memory switch must source field 'dl'."""
        desc = next(
            d for d in SWITCH_DESCRIPTIONS if d.key == "portable_discharge_memory"
        )
        assert _DISCHARGE_MEMORY_FIELD in desc.source_keys
