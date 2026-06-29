"""Tests for QUERY_BUTTON_DESCRIPTIONS including the portable/Explorer powerstation buttons.

This test file covers:
- Total count of QUERY_BUTTON_DESCRIPTIONS (SolarVault + portable buttons)
- Structural integrity of all entries (keys, icons, action callables, message_type)
- Portable-specific button entries exist and have correct attributes
- No portable button has a dev_type (they address the main device)
- All keys are unique
- All icons use the mdi: prefix
- Portable button actions call async_send_portable_command on the coordinator
"""  # noqa: E501

from unittest.mock import AsyncMock, MagicMock

from custom_components.jackery_solarvault.button import (
    QUERY_BUTTON_DESCRIPTIONS,
    JackeryQueryButton,
    JackeryQueryButtonDescription,
)

# The expected total count: 14 SolarVault + 14 portable = 28 entries
_EXPECTED_TOTAL_COUNT = 28

# Portable button keys (added in current PR scope)
_PORTABLE_KEYS = frozenset({
    "portable_restart",
    "portable_power_off",
    "portable_power_pack_blink",
    "portable_refresh_device_info",
    "portable_refresh_wifi_list",
    "portable_refresh_battery_packs",
    "portable_refresh_electricity_count",
    "portable_sync_time_zone",
    "portable_sync_mqtt_info",
    "portable_refresh_wifi_config",
    "portable_get_charge_plan",
    "portable_current_charge_plan",
    "portable_get_peaks_troughs",
    "portable_refresh_sub_ct",
})

# SolarVault (non-portable) button keys
_SOLARVAULT_KEYS = frozenset({
    "refresh_system_info",
    "refresh_device_info",
    "refresh_wifi_list",
    "refresh_time_zone",
    "sync_time_zone",
    "sync_cloud_mqtt_info",
    "refresh_device_ota_version",
    "refresh_third_party_mqtt_config",
    "refresh_wifi_config",
    "refresh_battery_packs",
    "refresh_smart_meter",
    "refresh_meter_heads",
    "refresh_smart_plugs",
    "refresh_subdevice_combo",
})


# ---------------------------------------------------------------------------
# Structural integrity tests
# ---------------------------------------------------------------------------


class TestQueryButtonDescriptionsStructure:
    """Structural tests for QUERY_BUTTON_DESCRIPTIONS."""

    def test_is_a_tuple(self) -> None:  # noqa: PLR6301
        """QUERY_BUTTON_DESCRIPTIONS must be a tuple."""
        assert isinstance(QUERY_BUTTON_DESCRIPTIONS, tuple)

    def test_total_count_includes_portable_buttons(self) -> None:  # noqa: PLR6301
        """Total count must include both SolarVault and portable buttons."""
        assert len(QUERY_BUTTON_DESCRIPTIONS) == _EXPECTED_TOTAL_COUNT, (
            f"Expected {_EXPECTED_TOTAL_COUNT} descriptions, got {len(QUERY_BUTTON_DESCRIPTIONS)}. "  # noqa: E501
            f"Keys present: {[d.key for d in QUERY_BUTTON_DESCRIPTIONS]}"
        )

    def test_all_entries_are_query_button_description_instances(self) -> None:  # noqa: PLR6301
        """Every entry must be a JackeryQueryButtonDescription instance."""
        for desc in QUERY_BUTTON_DESCRIPTIONS:
            assert isinstance(desc, JackeryQueryButtonDescription), (
                f"Entry {desc!r} is not a JackeryQueryButtonDescription"
            )

    def test_all_keys_are_unique(self) -> None:  # noqa: PLR6301
        """Every description key must be unique."""
        keys = [desc.key for desc in QUERY_BUTTON_DESCRIPTIONS]
        assert len(keys) == len(set(keys)), (
            f"Duplicate keys found: {[k for k in keys if keys.count(k) > 1]}"
        )

    def test_all_icons_use_mdi_prefix(self) -> None:  # noqa: PLR6301
        """Every description icon must start with 'mdi:'."""
        for desc in QUERY_BUTTON_DESCRIPTIONS:
            assert desc.icon.startswith("mdi:"), (
                f"Description '{desc.key}' has icon '{desc.icon}' which does not start with 'mdi:'"  # noqa: E501
            )

    def test_all_actions_are_callable(self) -> None:  # noqa: PLR6301
        """Every description action must be callable."""
        for desc in QUERY_BUTTON_DESCRIPTIONS:
            assert callable(desc.action), (
                f"Description '{desc.key}' has non-callable action: {desc.action!r}"
            )

    def test_all_message_types_are_strings(self) -> None:  # noqa: PLR6301
        """Every description message_type must be a non-empty string."""
        for desc in QUERY_BUTTON_DESCRIPTIONS:
            assert isinstance(desc.message_type, str) and desc.message_type, (  # noqa: PT018
                f"Description '{desc.key}' has invalid message_type: {desc.message_type!r}"  # noqa: E501
            )

    def test_all_action_ids_are_positive_integers(self) -> None:  # noqa: PLR6301
        """Every description action_id must be a positive integer."""
        for desc in QUERY_BUTTON_DESCRIPTIONS:
            assert isinstance(desc.action_id, int) and desc.action_id > 0, (  # noqa: PT018
                f"Description '{desc.key}' has invalid action_id: {desc.action_id!r}"
            )

    def test_all_cmds_are_non_negative_integers(self) -> None:  # noqa: PLR6301
        """Every description cmd must be a non-negative integer."""
        for desc in QUERY_BUTTON_DESCRIPTIONS:
            assert isinstance(desc.cmd, int) and desc.cmd >= 0, (  # noqa: PT018
                f"Description '{desc.key}' has invalid cmd: {desc.cmd!r}"
            )

    def test_translation_key_matches_key_for_all_entries(self) -> None:  # noqa: PLR6301
        """Each description's translation_key must match its key."""
        for desc in QUERY_BUTTON_DESCRIPTIONS:
            assert desc.translation_key == desc.key, (
                f"Description '{desc.key}' has mismatched translation_key: '{desc.translation_key}'"  # noqa: E501
            )


# ---------------------------------------------------------------------------
# SolarVault-specific keys must all be present
# ---------------------------------------------------------------------------


class TestSolarVaultKeys:
    """Tests for the 14 SolarVault (non-portable) button descriptions."""

    def test_all_solarvault_keys_present(self) -> None:  # noqa: PLR6301
        """All 14 expected SolarVault keys must be in QUERY_BUTTON_DESCRIPTIONS."""
        actual_keys = {desc.key for desc in QUERY_BUTTON_DESCRIPTIONS}
        missing = _SOLARVAULT_KEYS - actual_keys
        assert not missing, f"Missing SolarVault keys: {missing}"

    def test_subdevice_keys_have_dev_type(self) -> None:  # noqa: PLR6301
        """SolarVault subdevice-query keys must have a non-None dev_type."""
        subdevice_keys = {
            "refresh_battery_packs",
            "refresh_smart_meter",
            "refresh_meter_heads",
            "refresh_smart_plugs",
            "refresh_subdevice_combo",
        }
        for desc in QUERY_BUTTON_DESCRIPTIONS:
            if desc.key in subdevice_keys:
                assert desc.dev_type is not None, (
                    f"SolarVault subdevice key '{desc.key}' must have dev_type set"
                )

    def test_non_subdevice_solarvault_keys_have_no_dev_type(self) -> None:  # noqa: PLR6301
        """Non-subdevice SolarVault keys must have dev_type=None."""
        non_subdevice_sv_keys = _SOLARVAULT_KEYS - {
            "refresh_battery_packs",
            "refresh_smart_meter",
            "refresh_meter_heads",
            "refresh_smart_plugs",
            "refresh_subdevice_combo",
        }
        for desc in QUERY_BUTTON_DESCRIPTIONS:
            if desc.key in non_subdevice_sv_keys:
                assert desc.dev_type is None, (
                    f"Non-subdevice SolarVault key '{desc.key}' must have dev_type=None, "  # noqa: E501
                    f"got {desc.dev_type}"
                )


# ---------------------------------------------------------------------------
# Portable-specific keys must all be present and correctly configured
# ---------------------------------------------------------------------------


class TestPortableButtonKeys:
    """Tests for the portable/Explorer powerstation button descriptions."""

    def test_all_portable_keys_present(self) -> None:  # noqa: PLR6301
        """All 14 expected portable keys must be in QUERY_BUTTON_DESCRIPTIONS."""
        actual_keys = {desc.key for desc in QUERY_BUTTON_DESCRIPTIONS}
        missing = _PORTABLE_KEYS - actual_keys
        assert not missing, f"Missing portable keys: {missing}"

    def test_portable_buttons_have_no_dev_type(self) -> None:  # noqa: PLR6301
        """All portable buttons must have dev_type=None (they address the main device)."""  # noqa: E501
        for desc in QUERY_BUTTON_DESCRIPTIONS:
            if desc.key in _PORTABLE_KEYS:
                assert desc.dev_type is None, (
                    f"Portable button '{desc.key}' must not have dev_type set, "
                    f"got {desc.dev_type}"
                )

    def test_portable_restart_has_correct_cmd(self) -> None:  # noqa: PLR6301
        """portable_restart must use cmd=96 (bleMsgType=96; msgId/action_id=45)."""
        desc = next(d for d in QUERY_BUTTON_DESCRIPTIONS if d.key == "portable_restart")
        assert desc.cmd == 96  # noqa: PLR2004
        assert desc.action_id == 45  # noqa: PLR2004

    def test_portable_power_off_has_correct_cmd(self) -> None:  # noqa: PLR6301
        """portable_power_off must use cmd=97 (bleMsgType=97; msgId/action_id=46)."""
        desc = next(
            d for d in QUERY_BUTTON_DESCRIPTIONS if d.key == "portable_power_off"
        )
        assert desc.cmd == 97  # noqa: PLR2004
        assert desc.action_id == 46  # noqa: PLR2004

    def test_portable_power_pack_blink_has_correct_cmd(self) -> None:  # noqa: PLR6301
        """portable_power_pack_blink must use cmd=98 (bleMsgType=98; msgId/action_id=39)."""  # noqa: E501
        desc = next(
            d for d in QUERY_BUTTON_DESCRIPTIONS if d.key == "portable_power_pack_blink"
        )
        assert desc.cmd == 98  # noqa: PLR2004
        assert desc.action_id == 39  # noqa: PLR2004

    def test_portable_refresh_device_info_has_correct_cmd(self) -> None:  # noqa: PLR6301
        """portable_refresh_device_info must use cmd=3 (bleMsgType=3; msgId/action_id=6)."""  # noqa: E501
        desc = next(
            d
            for d in QUERY_BUTTON_DESCRIPTIONS
            if d.key == "portable_refresh_device_info"
        )
        assert desc.cmd == 3  # noqa: PLR2004
        assert desc.action_id == 6  # noqa: PLR2004

    def test_portable_refresh_wifi_list_has_correct_cmd(self) -> None:  # noqa: PLR6301
        """portable_refresh_wifi_list must use cmd=1 (bleMsgType=1; msgId/action_id=5)."""  # noqa: E501
        desc = next(
            d
            for d in QUERY_BUTTON_DESCRIPTIONS
            if d.key == "portable_refresh_wifi_list"
        )
        assert desc.cmd == 1
        assert desc.action_id == 5  # noqa: PLR2004

    def test_portable_refresh_battery_packs_has_correct_cmd(self) -> None:  # noqa: PLR6301
        """portable_refresh_battery_packs must use cmd=6 (bleMsgType=6; msgId/action_id=8)."""  # noqa: E501
        desc = next(
            d
            for d in QUERY_BUTTON_DESCRIPTIONS
            if d.key == "portable_refresh_battery_packs"
        )
        assert desc.cmd == 6  # noqa: PLR2004
        assert desc.action_id == 8  # noqa: PLR2004

    def test_portable_refresh_electricity_count_has_correct_cmd(self) -> None:  # noqa: PLR6301
        """portable_refresh_electricity_count must use cmd=7 (bleMsgType=7; msgId/action_id=9)."""  # noqa: E501
        desc = next(
            d
            for d in QUERY_BUTTON_DESCRIPTIONS
            if d.key == "portable_refresh_electricity_count"
        )
        assert desc.cmd == 7  # noqa: PLR2004
        assert desc.action_id == 9  # noqa: PLR2004

    def test_portable_sync_time_zone_has_correct_cmd(self) -> None:  # noqa: PLR6301
        """portable_sync_time_zone must use cmd=8 (bleMsgType=8; msgId/action_id=25)."""
        desc = next(
            d for d in QUERY_BUTTON_DESCRIPTIONS if d.key == "portable_sync_time_zone"
        )
        assert desc.cmd == 8  # noqa: PLR2004
        assert desc.action_id == 25  # noqa: PLR2004

    def test_portable_sync_mqtt_info_has_correct_cmd(self) -> None:  # noqa: PLR6301
        """portable_sync_mqtt_info must use cmd=99 (bleMsgType=99; msgId/action_id=50)."""  # noqa: E501
        desc = next(
            d for d in QUERY_BUTTON_DESCRIPTIONS if d.key == "portable_sync_mqtt_info"
        )
        assert desc.cmd == 99  # noqa: PLR2004
        assert desc.action_id == 50  # noqa: PLR2004

    def test_portable_refresh_wifi_config_has_correct_cmd(self) -> None:  # noqa: PLR6301
        """portable_refresh_wifi_config must use cmd=124 (bleMsgType=124; msgId/action_id=52)."""  # noqa: E501
        desc = next(
            d
            for d in QUERY_BUTTON_DESCRIPTIONS
            if d.key == "portable_refresh_wifi_config"
        )
        assert desc.cmd == 124  # noqa: PLR2004
        assert desc.action_id == 52  # noqa: PLR2004

    def test_portable_get_charge_plan_has_correct_cmd(self) -> None:  # noqa: PLR6301
        """portable_get_charge_plan must use cmd=15 (bleMsgType=15; msgId/action_id=26)."""  # noqa: E501
        desc = next(
            d for d in QUERY_BUTTON_DESCRIPTIONS if d.key == "portable_get_charge_plan"
        )
        assert desc.cmd == 15  # noqa: PLR2004
        assert desc.action_id == 26  # noqa: PLR2004

    def test_portable_current_charge_plan_has_correct_cmd(self) -> None:  # noqa: PLR6301
        """portable_current_charge_plan must use cmd=21 (bleMsgType=21; msgId/action_id=30)."""  # noqa: E501
        desc = next(
            d
            for d in QUERY_BUTTON_DESCRIPTIONS
            if d.key == "portable_current_charge_plan"
        )
        assert desc.cmd == 21  # noqa: PLR2004
        assert desc.action_id == 30  # noqa: PLR2004

    def test_portable_get_peaks_troughs_has_correct_cmd(self) -> None:  # noqa: PLR6301
        """portable_get_peaks_troughs must use cmd=131 (bleMsgType=131; msgId/action_id=43)."""  # noqa: E501
        desc = next(
            d
            for d in QUERY_BUTTON_DESCRIPTIONS
            if d.key == "portable_get_peaks_troughs"
        )
        assert desc.cmd == 131  # noqa: PLR2004
        assert desc.action_id == 43  # noqa: PLR2004

    def test_portable_refresh_sub_ct_has_correct_cmd(self) -> None:  # noqa: PLR6301
        """portable_refresh_sub_ct must use cmd=110 (bleMsgType=110; msgId/action_id=51)."""  # noqa: E501
        desc = next(
            d for d in QUERY_BUTTON_DESCRIPTIONS if d.key == "portable_refresh_sub_ct"
        )
        assert desc.cmd == 110  # noqa: PLR2004
        assert desc.action_id == 51  # noqa: PLR2004

    def test_portable_keys_have_portable_prefix_in_key(self) -> None:  # noqa: PLR6301
        """All portable keys must start with 'portable_'."""
        for key in _PORTABLE_KEYS:
            assert key.startswith("portable_"), (
                f"Portable key '{key}' does not start with 'portable_'"
            )


# ---------------------------------------------------------------------------
# Action function behaviour
# ---------------------------------------------------------------------------


class TestPortableButtonActions:
    """Tests that portable button actions call async_send_portable_command."""

    async def test_portable_restart_calls_send_portable_command(self) -> None:  # noqa: PLR6301
        """_portable_restart action must call async_send_portable_command on the coordinator."""  # noqa: E501
        from custom_components.jackery_solarvault.button import (  # noqa: PLC0415
            _portable_restart,  # noqa: PLC2701
        )
        from custom_components.jackery_solarvault.const import (  # noqa: PLC0415
            ACTION_ID_PORTABLE_RESTART,
            FIELD_REBOOT,
        )

        coordinator = MagicMock()
        coordinator.async_send_portable_command = AsyncMock()

        await _portable_restart(coordinator, "device_id_123")

        coordinator.async_send_portable_command.assert_called_once_with(
            "device_id_123",
            action_id=ACTION_ID_PORTABLE_RESTART,
            cmd=96,
            body_fields={FIELD_REBOOT: 1},
        )

    async def test_portable_power_off_calls_send_portable_command(self) -> None:  # noqa: PLR6301
        """_portable_power_off action must call async_send_portable_command on the coordinator."""  # noqa: E501
        from custom_components.jackery_solarvault.button import (  # noqa: PLC0415
            _portable_power_off,  # noqa: PLC2701
        )
        from custom_components.jackery_solarvault.const import (  # noqa: PLC0415
            ACTION_ID_PORTABLE_POWER_OFF,
            FIELD_REBOOT,
        )

        coordinator = MagicMock()
        coordinator.async_send_portable_command = AsyncMock()

        await _portable_power_off(coordinator, "device_id_456")

        coordinator.async_send_portable_command.assert_called_once_with(
            "device_id_456",
            action_id=ACTION_ID_PORTABLE_POWER_OFF,
            cmd=97,
            body_fields={FIELD_REBOOT: 2},
        )

    async def test_portable_power_pack_blink_calls_send_portable_command(self) -> None:  # noqa: PLR6301
        """_portable_power_pack_blink action must call async_send_portable_command."""
        from custom_components.jackery_solarvault.button import (  # noqa: PLC0415
            _portable_power_pack_blink,  # noqa: PLC2701
        )
        from custom_components.jackery_solarvault.const import (  # noqa: PLC0415
            ACTION_ID_PORTABLE_POWER_PACK_BLINK,
        )

        coordinator = MagicMock()
        coordinator.async_send_portable_command = AsyncMock()

        await _portable_power_pack_blink(coordinator, "device_id_789")

        coordinator.async_send_portable_command.assert_called_once_with(
            "device_id_789",
            action_id=ACTION_ID_PORTABLE_POWER_PACK_BLINK,
            cmd=98,
            body_fields={},
        )

    async def test_portable_read_device_info_calls_send_portable_command(self) -> None:  # noqa: PLR6301
        """_portable_read_device_info action must call async_send_portable_command."""
        from custom_components.jackery_solarvault.button import (  # noqa: PLC0415
            _portable_read_device_info,  # noqa: PLC2701
        )
        from custom_components.jackery_solarvault.const import (  # noqa: PLC0415
            ACTION_ID_PORTABLE_READ_DEVICE_INFO,
        )

        coordinator = MagicMock()
        coordinator.async_send_portable_command = AsyncMock()

        await _portable_read_device_info(coordinator, "dev_abc")

        coordinator.async_send_portable_command.assert_called_once_with(
            "dev_abc",
            action_id=ACTION_ID_PORTABLE_READ_DEVICE_INFO,
            cmd=3,
            body_fields={},
        )

    async def test_portable_get_charge_plan_calls_send_portable_command(self) -> None:  # noqa: PLR6301
        """_portable_get_charge_plan action must call async_send_portable_command."""
        from custom_components.jackery_solarvault.button import (  # noqa: PLC0415
            _portable_get_charge_plan,  # noqa: PLC2701
        )
        from custom_components.jackery_solarvault.const import (  # noqa: PLC0415
            ACTION_ID_PORTABLE_GET_CHARGE_PLAN,
            MQTT_MESSAGE_QUERY_ELECTRICITY_STRATEGY,
        )

        coordinator = MagicMock()
        coordinator.async_send_portable_command = AsyncMock()

        await _portable_get_charge_plan(coordinator, "dev_xyz")

        coordinator.async_send_portable_command.assert_called_once_with(
            "dev_xyz",
            action_id=ACTION_ID_PORTABLE_GET_CHARGE_PLAN,
            cmd=15,
            body_fields={},
            message_type=MQTT_MESSAGE_QUERY_ELECTRICITY_STRATEGY,
        )

    async def test_portable_sync_mqtt_info_calls_send_portable_command(self) -> None:  # noqa: PLR6301
        """_portable_sync_mqtt_info action must call async_send_portable_command."""
        from custom_components.jackery_solarvault.button import (  # noqa: PLC0415
            _portable_sync_mqtt_info,  # noqa: PLC2701
        )
        from custom_components.jackery_solarvault.const import (  # noqa: PLC0415
            ACTION_ID_PORTABLE_SYNC_MQTT_INFO,
        )

        coordinator = MagicMock()
        coordinator.async_send_portable_command = AsyncMock()

        await _portable_sync_mqtt_info(coordinator, "dev_sync")

        coordinator.async_send_portable_command.assert_called_once_with(
            "dev_sync",
            action_id=ACTION_ID_PORTABLE_SYNC_MQTT_INFO,
            cmd=99,
            body_fields={},
        )


# ---------------------------------------------------------------------------
# JackeryQueryButton integration with portable descriptions
# ---------------------------------------------------------------------------


class TestJackeryQueryButtonWithPortableDescriptions:
    """Verify that JackeryQueryButton works correctly with portable descriptions."""

    def _make_coordinator(self, device_id: str = "dev123") -> MagicMock:  # noqa: PLR6301
        coordinator = MagicMock()
        coordinator.data = {device_id: {}}
        coordinator.async_add_listener = MagicMock(return_value=MagicMock())
        return coordinator

    def test_portable_restart_button_has_correct_message_type(self) -> None:
        """JackeryQueryButton with portable_restart description must have DEVICE_PROPERTY_CHANGE message type."""  # noqa: E501
        from custom_components.jackery_solarvault.const import (  # noqa: PLC0415
            MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
        )

        desc = next(d for d in QUERY_BUTTON_DESCRIPTIONS if d.key == "portable_restart")
        coordinator = self._make_coordinator()
        btn = JackeryQueryButton(coordinator, "dev123", description=desc)
        attrs = btn.extra_state_attributes
        from custom_components.jackery_solarvault.const import FIELD_MESSAGE_TYPE  # noqa: I001, PLC0415

        assert attrs[FIELD_MESSAGE_TYPE] == MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE

    def test_portable_restart_button_has_no_dev_type_in_attrs(self) -> None:
        """JackeryQueryButton with portable description must NOT include devType in attrs."""  # noqa: E501
        from custom_components.jackery_solarvault.const import FIELD_DEV_TYPE  # noqa: I001, PLC0415

        desc = next(d for d in QUERY_BUTTON_DESCRIPTIONS if d.key == "portable_restart")
        coordinator = self._make_coordinator()
        btn = JackeryQueryButton(coordinator, "dev123", description=desc)
        attrs = btn.extra_state_attributes
        assert FIELD_DEV_TYPE not in attrs

    def test_portable_restart_button_cmd_is_in_attrs(self) -> None:
        """JackeryQueryButton with portable_restart must include cmd=96 (bleMsgType) in extra_state_attributes."""  # noqa: E501
        from custom_components.jackery_solarvault.const import FIELD_CMD  # noqa: I001, PLC0415

        desc = next(d for d in QUERY_BUTTON_DESCRIPTIONS if d.key == "portable_restart")
        coordinator = self._make_coordinator()
        btn = JackeryQueryButton(coordinator, "dev123", description=desc)
        attrs = btn.extra_state_attributes
        assert attrs[FIELD_CMD] == 96  # noqa: PLR2004

    def test_portable_unique_id_does_not_clash_with_solarvault(self) -> None:
        """Portable button unique IDs must not match any SolarVault button unique IDs."""  # noqa: E501
        coordinator = self._make_coordinator()
        solarvault_ids = set()
        portable_ids = set()
        for desc in QUERY_BUTTON_DESCRIPTIONS:
            btn = JackeryQueryButton(coordinator, "dev123", description=desc)
            uid = btn._attr_unique_id  # noqa: SLF001
            if desc.key in _SOLARVAULT_KEYS:
                solarvault_ids.add(uid)
            elif desc.key in _PORTABLE_KEYS:
                portable_ids.add(uid)
        assert not solarvault_ids.intersection(portable_ids), (
            "Some portable button unique IDs clash with SolarVault unique IDs"
        )


# ---------------------------------------------------------------------------
# Regression: portable buttons must not be accidentally missing
# ---------------------------------------------------------------------------


def test_portable_restart_in_descriptions() -> None:
    """Regression: portable_restart must exist in QUERY_BUTTON_DESCRIPTIONS."""
    keys = [d.key for d in QUERY_BUTTON_DESCRIPTIONS]
    assert "portable_restart" in keys


def test_portable_power_off_in_descriptions() -> None:
    """Regression: portable_power_off must exist in QUERY_BUTTON_DESCRIPTIONS."""
    keys = [d.key for d in QUERY_BUTTON_DESCRIPTIONS]
    assert "portable_power_off" in keys


def test_no_portable_button_has_subdevice_dev_type() -> None:
    """Boundary: no portable button should address a subdevice (they address the main device)."""  # noqa: E501
    for desc in QUERY_BUTTON_DESCRIPTIONS:
        if desc.key.startswith("portable_"):
            assert desc.dev_type is None, (
                f"Portable button '{desc.key}' must not set dev_type; got {desc.dev_type}"  # noqa: E501
            )
