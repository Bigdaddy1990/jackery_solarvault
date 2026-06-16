"""Source-level MQTT protocol contract checks."""

import ast
import asyncio
from pathlib import Path

from custom_components.jackery_solarvault.const import (
    ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
    FIELD_ACTION_ID,
    FIELD_BAT_OUT_PW,
    FIELD_BAT_SOC,
    FIELD_BODY,
    FIELD_CMD,
    FIELD_MESSAGE_TYPE,
    FIELD_SOC,
    FIELD_THIRD_PARTY_MQTT_ENABLE,
    FIELD_THIRD_PARTY_MQTT_IP,
    FIELD_THIRD_PARTY_MQTT_PASSWORD,
    FIELD_THIRD_PARTY_MQTT_PORT,
    FIELD_THIRD_PARTY_MQTT_USERNAME,
    FIELD_WNAME,
    MQTT_CMD_QUERY_DEVICE_PROPERTY,
    MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG,
    MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG,
    PAYLOAD_MQTT_LAST,
    PAYLOAD_PROPERTIES,
    PAYLOAD_THIRD_PARTY_MQTT_CONFIG,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)

ROOT = Path(__file__).resolve().parents[2]
COORDINATOR_PATH = ROOT / "custom_components" / "jackery_solarvault" / "coordinator.py"
MQTT_PUSH_PATH = (
    ROOT / "custom_components" / "jackery_solarvault" / "client" / "mqtt_push.py"
)
CONST_PATH = ROOT / "custom_components" / "jackery_solarvault" / "const.py"
API_PATH = ROOT / "custom_components" / "jackery_solarvault" / "client" / "api.py"
SENSOR_PATH = ROOT / "custom_components" / "jackery_solarvault" / "sensor.py"


def _read(path: Path) -> str:
    """Read the text contents of a file, decoded as UTF-8.

    Parameters:
        path (Path): Path to the file to read.

    Returns:
        str: The file's contents decoded using UTF-8.
    """
    return path.read_text(encoding="utf-8")


def _function_source(path: Path, name: str) -> str:
    """Return the exact source text for a top-level function or async function defined.

    in a Python file.

    Parameters:
        path (Path): Path to the Python source file to read.
        name (str): Name of the function or async function to extract.

    Returns:
        str: The source code lines comprising the named function, including its
        signature and body.

    Raises:
        AssertionError: If the named function is not found in the given file.
    """
    source = _read(path)
    tree = ast.parse(source)
    lines = source.splitlines()
    for node in ast.walk(tree):
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == name
        ):
            assert node.end_lineno is not None
            return "\n".join(lines[node.lineno - 1 : node.end_lineno])
    msg = f"{name} not found in {path}"
    raise AssertionError(msg)


def test_mqtt_setter_commands_match_app_protocol() -> None:
    """Setter actionIds must match the Jackery app smali HomeCmdAction.smali.

    Smali authority:
      3022 = CONTROL_AC_OFF_GRID_SWITCH (EPS toggle, body ``swEps``)
      3023 = CONTROL_STANDBY            (standby mode, body ``standby``)
      3024 = SUB_CONTROL_SOCKET_SWITCH  (cmd=111 ControlSubDevice)
      3025 = SUB_CONTROL_SOCKET_PRI_ENABLE (cmd=111 ControlSubDevice)
      3026 = SUB_SET_CT_SCHEDULE_PHASE  (cmd=111 ControlSubDevice)
      3028 = SET_CHARGE_DISCHARGE_LINE  (both SOC limits in one frame)
    Earlier packet captures had 3022/3023 and the SOC actionIds wrong; the
    smali is the source of truth.
    """
    eps = _function_source(COORDINATOR_PATH, "async_set_eps")
    assert "action_id=ACTION_ID_EPS_ENABLED" in eps
    assert "FIELD_SW_EPS" in eps
    const_source = _read(CONST_PATH)
    assert "ACTION_ID_EPS_ENABLED: Final = 3022" in const_source
    assert "ACTION_ID_STANDBY: Final = 3023" in const_source

    # 3038 maxOutPw routes via DevicePropertyChange (cmd=107).
    max_output = _function_source(COORDINATOR_PATH, "async_set_max_output_power")
    assert "message_type=MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE" in max_output
    assert "cmd=MQTT_CMD_DEVICE_PROPERTY_CHANGE" in max_output

    # 3028 carries BOTH SOC limits in one frame — verified against smali.
    soc_limits = _function_source(COORDINATOR_PATH, "async_set_soc_limits")
    assert "ACTION_ID_SOC_LIMITS" in soc_limits
    assert "ACTION_ID_SOC_CHARGE_LIMIT" not in soc_limits
    assert "ACTION_ID_SOC_DISCHARGE_LIMIT" not in soc_limits
    assert "FIELD_SOC_CHG_LIMIT" in soc_limits
    assert "FIELD_SOC_DISCHG_LIMIT" in soc_limits
    assert "ACTION_ID_SOC_LIMITS: Final = 3028" in const_source

    # 3026 SUB_SET_CT_SCHEDULE_PHASE — body verified 2026-05-14.
    ct_phase = _function_source(COORDINATOR_PATH, "async_set_ct_phase")
    assert "MQTT_MESSAGE_CONTROL_SUB_DEVICE" in ct_phase
    assert "ACTION_ID_CT_PHASE" in ct_phase
    assert "MQTT_CMD_CONTROL_SUB_DEVICE" in ct_phase
    assert "SUBDEVICE_DEV_TYPE_CT" in ct_phase
    assert "FIELD_SCHE_PHASE" in ct_phase

    query_combine = _function_source(COORDINATOR_PATH, "async_query_system_info")
    assert "ACTION_ID_QUERY_COMBINE_DATA" in query_combine
    assert "cmd=MQTT_CMD_QUERY_COMBINE_DATA" in query_combine

    query_device = _function_source(COORDINATOR_PATH, "async_query_device_info")
    assert "MQTT_MESSAGE_QUERY_DEVICE_PROPERTY" in query_device
    assert "ACTION_ID_QUERY_DEVICE_PROPERTY" in query_device
    assert "cmd=MQTT_CMD_QUERY_DEVICE_PROPERTY" in query_device

    query_backfill = _function_source(
        COORDINATOR_PATH,
        "_async_query_system_info_for_missing",
    )
    assert "async_query_device_info" in query_backfill


def test_third_party_mqtt_bridge_setter_uses_smali_protocol() -> None:
    """Experimental 3046/3047 setter+query must match HomeCmdAction.smali."""
    const_source = _read(CONST_PATH)
    handler = _function_source(COORDINATOR_PATH, "_async_handle_mqtt_message")
    # ActionIds / cmd values straight from HomeCmdAction.smali.
    assert "ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG: Final = 3046" in const_source
    assert "ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG: Final = (" in const_source
    assert "3047  # cmd=114 QueryThirdPartMQTTConfig" in const_source
    assert (
        'PAYLOAD_THIRD_PARTY_MQTT_CONFIG: Final = "third_party_mqtt_config"'
        in const_source
    )
    assert (
        'MQTT_MESSAGE_THIRD_PARTY_MQTT_CONFIG: Final = "ThirdPartMQTTConfig"'
        in const_source
    )
    assert (
        'MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG: Final = "QueryThirdPartMQTTConfig"'
        in const_source
    )
    assert "MQTT_CMD_THIRD_PARTY_MQTT_CONFIG: Final = 113" in const_source
    assert "MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG: Final = 114" in const_source

    # Body keys per ThirdPartyMqttBody.smali.
    for line in (
        'FIELD_THIRD_PARTY_MQTT_ENABLE: Final = "enable"',
        'FIELD_THIRD_PARTY_MQTT_IP: Final = "ip"',
        'FIELD_THIRD_PARTY_MQTT_PORT: Final = "port"',
        'FIELD_THIRD_PARTY_MQTT_USERNAME: Final = "userName"',
        'FIELD_THIRD_PARTY_MQTT_PASSWORD: Final = "password"',
        'FIELD_THIRD_PARTY_MQTT_TOKEN: Final = "token"',
    ):
        assert line in const_source

    setter = _function_source(COORDINATOR_PATH, "async_set_third_party_mqtt_config")
    assert "MQTT_MESSAGE_THIRD_PARTY_MQTT_CONFIG" in setter
    assert "ACTION_ID_SET_THIRD_PARTY_MQTT_CONFIG" in setter
    assert "MQTT_CMD_THIRD_PARTY_MQTT_CONFIG" in setter
    for field in (
        "FIELD_THIRD_PARTY_MQTT_ENABLE",
        "FIELD_THIRD_PARTY_MQTT_IP",
        "FIELD_THIRD_PARTY_MQTT_PORT",
        "FIELD_THIRD_PARTY_MQTT_USERNAME",
        "FIELD_THIRD_PARTY_MQTT_PASSWORD",
        "FIELD_THIRD_PARTY_MQTT_TOKEN",
    ):
        assert field in setter

    query = _function_source(COORDINATOR_PATH, "async_query_third_party_mqtt_config")
    assert "MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG" in query
    assert "ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG" in query
    assert "MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG" in query
    assert "is_third_party_mqtt_config =" in handler
    assert "updated[PAYLOAD_THIRD_PARTY_MQTT_CONFIG]" in handler
    assert "_THIRD_PARTY_MQTT_CONFIG_KEYS" in _read(COORDINATOR_PATH)


def test_third_party_mqtt_response_does_not_pollute_main_properties() -> None:
    """Third-party MQTT config responses belong in their own payload bucket."""

    async def _run() -> None:
        """Exercise the coordinator's third-party MQTT query handling and assert.

        correct bucketing of response payload.

        Creates a minimal JackerySolarVaultCoordinator instance with required stubs,
        injects a QueryThirdPartMQTTConfig MQTT message, and verifies the coordinator:
        - does not merge third-party MQTT fields into the main `properties` map,
        - stores the full third-party config under `third_party_mqtt_config`,
        - and that `_sanitize_main_properties` returns an empty dict for the provided
        body.

        Raises:
            AssertionError: If any of the expected storage or sanitization conditions
            are not met.
        """
        self = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
        self.data = {"dev": {PAYLOAD_PROPERTIES: {"soc": 40}}}
        self._device_index = {"dev": {}}
        self._property_overrides = {}
        captured: dict[str, object] = {}

        async def _debug_event(_event_or_factory: object) -> None:  # noqa: RUF029
            """Act as a no-op placeholder for emitting or producing debug events.

            Parameters:
                _event_or_factory (object): An event object or a zero-argument factory
                callable that would produce an event when the debug mechanism is
                active. This function currently ignores the argument and returns
                without side effects.
            """
            return

        def _push_partial_update(new_data: dict[str, object]) -> None:
            """Store a partial update payload into the test capture dictionary.

            Parameters:
                new_data (dict[str, object]): Partial update payload to capture under
                the key "data".
            """
            captured["data"] = new_data

        self._async_payload_debug_event = _debug_event
        self._push_partial_update = _push_partial_update
        self._schedule_battery_pack_ota_enrichment = lambda _device_id: None

        body = {
            FIELD_CMD: MQTT_CMD_QUERY_THIRD_PARTY_MQTT_CONFIG,
            FIELD_THIRD_PARTY_MQTT_ENABLE: 1,
            FIELD_THIRD_PARTY_MQTT_IP: "192.0.2.10",
            FIELD_THIRD_PARTY_MQTT_PORT: 1883,
            FIELD_THIRD_PARTY_MQTT_USERNAME: "user",
            FIELD_THIRD_PARTY_MQTT_PASSWORD: "secret",
        }
        await JackerySolarVaultCoordinator._async_handle_mqtt_message(  # noqa: SLF001
            self,
            "hb/app/user/device",
            {
                FIELD_ACTION_ID: ACTION_ID_QUERY_THIRD_PARTY_MQTT_CONFIG,
                FIELD_BODY: body,
                FIELD_MESSAGE_TYPE: MQTT_MESSAGE_QUERY_THIRD_PARTY_MQTT_CONFIG,
            },
        )

        data = captured["data"]
        assert isinstance(data, dict)
        entry = data["dev"]
        assert isinstance(entry, dict)
        assert FIELD_THIRD_PARTY_MQTT_IP not in entry[PAYLOAD_PROPERTIES]
        assert FIELD_THIRD_PARTY_MQTT_PORT not in entry[PAYLOAD_PROPERTIES]
        assert FIELD_THIRD_PARTY_MQTT_ENABLE not in entry[PAYLOAD_PROPERTIES]
        assert entry[PAYLOAD_THIRD_PARTY_MQTT_CONFIG] == body
        assert JackerySolarVaultCoordinator._sanitize_main_properties(body) == {}  # noqa: SLF001

    asyncio.run(_run())


def test_smart_plug_subdevice_protocol_is_wired() -> None:
    """Smart-plug query and setter use the app-captured subdevice protocol."""
    const_source = _read(CONST_PATH)
    assert "ACTION_ID_SUBDEVICE_3032" in const_source
    assert "ACTION_ID_CONTROL_SOCKET_SWITCH" in const_source
    assert "ACTION_ID_CONTROL_SOCKET_PRIORITY" in const_source
    assert "SUBDEVICE_DEV_TYPE_SOCKET: Final = 6" in const_source

    query = _function_source(COORDINATOR_PATH, "async_query_smart_plugs")
    assert "ACTION_ID_SUBDEVICE_3032" in query
    assert "MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY" in query
    assert "SUBDEVICE_DEV_TYPE_SOCKET" in query

    setter = _function_source(COORDINATOR_PATH, "async_set_smart_plug_switch")
    assert "MQTT_MESSAGE_CONTROL_SUB_DEVICE" in setter
    assert "ACTION_ID_CONTROL_SOCKET_SWITCH" in setter
    assert "MQTT_CMD_CONTROL_SUB_DEVICE" in setter
    assert "FIELD_DEVICE_SN: plug_sn" in setter
    assert "FIELD_SYS_SWITCH: 1 if on else 0" in setter

    priority = _function_source(COORDINATOR_PATH, "async_set_smart_plug_priority")
    assert "MQTT_MESSAGE_CONTROL_SUB_DEVICE" in priority
    assert "ACTION_ID_CONTROL_SOCKET_PRIORITY" in priority
    assert "MQTT_CMD_CONTROL_SUB_DEVICE" in priority
    assert "FIELD_DEVICE_SN: plug_sn" in priority
    assert "FIELD_SOCKET_PRIORITY: 1 if enabled else 0" in priority


def test_smart_plug_statistics_are_read_only_app_paths() -> None:
    """Validate that smart-plug statistics are read-only and wired to the app's.

    read-only REST/API paths.

    Asserts that the integration defines the smart-plug statistic REST paths and JSON
    field constants, that the API exposes the endpoints using those constants and
    underlying `_get_json` / `_async_get_device_period_stat` helpers, and that the
    coordinator and sensors provide enrichment, stat ID mapping, and sensor
    descriptions for today/total energy with daily reset behavior.
    """
    const_source = _read(CONST_PATH)
    coordinator_source = _read(COORDINATOR_PATH)
    sensor_source = _read(SENSOR_PATH)

    assert "DEVICE_SOCKET_STATISTIC_PATH" in const_source
    assert '"/v1/device/stat/smartSocketStatistic"' in const_source
    assert 'DEVICE_SOCKET_STAT_PATH: Final = "/v1/device/stat/socket"' in const_source
    assert 'FIELD_SMART_SOCKET_ID: Final = "smartSocketId"' in const_source
    assert 'FIELD_TODAY_ENERGY: Final = "todayEgy"' in const_source
    assert 'FIELD_TOTAL_ENERGY: Final = "totalEgy"' in const_source

    panel = _function_source(API_PATH, "async_get_device_socket_statistic")
    assert "DEVICE_SOCKET_STATISTIC_PATH" in panel
    assert "FIELD_SMART_SOCKET_ID" in panel
    assert "_get_json" in panel

    chart = _function_source(API_PATH, "async_get_device_socket_stat")
    assert "DEVICE_SOCKET_STAT_PATH" in chart
    assert "_async_get_device_period_stat" in chart

    assert "_enrich_smart_plug_statistics" in coordinator_source
    assert "async_get_device_socket_statistic" in coordinator_source
    assert "_subdevice_stat_id" in coordinator_source
    assert "SUBDEVICE_DEV_TYPE_SOCKET" in coordinator_source
    assert "FIELD_TODAY_ENERGY" in coordinator_source
    assert "FIELD_TOTAL_ENERGY" in coordinator_source

    assert "smart_plug_today_energy" in sensor_source
    assert "smart_plug_total_energy" in sensor_source
    assert "SMART_PLUG_STATISTIC_FIELDS" in sensor_source
    assert "reset_period=DATE_TYPE_DAY" in sensor_source


def test_meter_head_subdevice_protocol_is_wired() -> None:
    """Meter-head/collector query uses the app-captured subdevice protocol."""
    const_source = _read(CONST_PATH)
    coordinator_source = _read(COORDINATOR_PATH)
    sensor_source = _read(SENSOR_PATH)
    assert "ACTION_ID_SUBDEVICE_3033" in const_source
    assert "SUBDEVICE_DEV_TYPE_METER_HEAD: Final = 4" in const_source
    assert 'PAYLOAD_METER_HEADS: Final = "meter_heads"' in const_source
    assert "DEVICE_METER_STAT_PATH" in const_source
    assert '"/v1/device/stat/meter"' in const_source
    assert 'FIELD_CHARGING_ENERGY: Final = "chargingEnergy"' in const_source
    assert 'FIELD_DISCHARGING_ENERGY: Final = "dischargingEnergy"' in const_source

    query = _function_source(COORDINATOR_PATH, "async_query_meter_heads")
    assert "ACTION_ID_SUBDEVICE_3033" in query
    assert "MQTT_CMD_QUERY_SUBDEVICE_GROUP_PROPERTY" in query
    assert "SUBDEVICE_DEV_TYPE_METER_HEAD" in query

    panel = _function_source(API_PATH, "async_get_device_meter_stat")
    assert "DEVICE_METER_STAT_PATH" in panel
    assert "FIELD_DEVICE_ID" in panel
    assert "_get_json" in panel

    merge = _function_source(COORDINATOR_PATH, "_merge_subdevice_data")
    # The HomeSubBody.CollectorBody array key is ``collectors`` (verified
    # against the Jackery app smali); the integration looks it up via the
    # named ``FIELD_COLLECTORS`` constant.
    assert "FIELD_COLLECTORS" in merge
    assert 'FIELD_COLLECTORS: Final = "collectors"' in const_source
    assert "PAYLOAD_METER_HEADS" in merge
    assert "_merge_subdevice_lists_by_sn" in merge

    query_backfill = _function_source(
        COORDINATOR_PATH,
        "_async_query_subdevices_for_missing",
    )
    assert "_has_meter_head_accessory" in query_backfill
    assert "async_query_meter_heads" in query_backfill

    assert "_enrich_meter_head_statistics" in coordinator_source
    assert "async_get_device_meter_stat" in coordinator_source
    assert "_subdevice_stat_id" in coordinator_source
    assert "FIELD_CHARGING_ENERGY" in coordinator_source
    assert "FIELD_DISCHARGING_ENERGY" in coordinator_source

    assert "METER_HEAD_SENSOR_DESCRIPTIONS" in sensor_source
    assert "JackeryMeterHeadSensor" in sensor_source
    assert "meter_head_charging_energy" in sensor_source
    assert "meter_head_discharging_energy" in sensor_source
    assert "_attr_entity_registry_enabled_default = False" in sensor_source


def test_enum_only_subdevice_types_are_not_queried_speculatively() -> None:
    """Only app-confirmed SubDeviceGroupProperty actionIds are queried."""
    const_source = _read(CONST_PATH)

    for name in (
        "SUBDEVICE_DEV_TYPE_METER: Final = 5",
        "SUBDEVICE_DEV_TYPE_BREAKER: Final = 7",
        "SUBDEVICE_DEV_TYPE_SMOKE: Final = 8",
        "SUBDEVICE_DEV_TYPE_TEMP_HUMIDITY: Final = 9",
        "SUBDEVICE_DEV_TYPE_WATER_LEAK: Final = 10",
    ):
        assert name in const_source

    assert "SUBDEVICE_DEV_TYPES_WITH_QUERY_ACTION" in const_source
    assert "SUBDEVICE_DEV_TYPES_ENUM_ONLY" in const_source
    queryable = const_source.split("SUBDEVICE_DEV_TYPES_WITH_QUERY_ACTION", 1)[1].split(
        "SUBDEVICE_DEV_TYPES_ENUM_ONLY",
        1,
    )[0]
    enum_only = const_source.split("SUBDEVICE_DEV_TYPES_ENUM_ONLY", 1)[1].split(
        "# Sets used",
        1,
    )[0]
    for name in (
        "SUBDEVICE_DEV_TYPE_METER",
        "SUBDEVICE_DEV_TYPE_BREAKER",
        "SUBDEVICE_DEV_TYPE_SMOKE",
        "SUBDEVICE_DEV_TYPE_TEMP_HUMIDITY",
        "SUBDEVICE_DEV_TYPE_WATER_LEAK",
    ):
        assert f"{name}," not in queryable
        assert f"{name}," in enum_only

    assert (
        "MQTT_ACTION_IDS_SUBDEVICE: Final = frozenset({3014, 3031, 3032, 3033, 3037})"
        in const_source
    )


def test_mqtt_action_id_routing_uses_shared_integer_parser() -> None:
    """MQTT actionId routing should accept text IDs without raw int casts."""
    handler = _function_source(COORDINATOR_PATH, "_async_handle_mqtt_message")
    subdevice = _function_source(COORDINATOR_PATH, "_is_subdevice_payload")

    assert "action_id = first_nonblank_int(payload.get(FIELD_ACTION_ID))" in handler
    assert "cmd = first_nonblank_int(body.get(FIELD_CMD))" in handler
    assert "action_id = first_nonblank_int(payload.get(FIELD_ACTION_ID))" in subdevice
    assert "action_id = payload.get(FIELD_ACTION_ID)" not in handler
    assert "body.get(FIELD_CMD) ==" not in handler
    assert "int(action_id)" not in subdevice


def test_mqtt_handler_accepts_text_cmd_for_action_topic_routing() -> None:
    """MQTT command routing tolerates text cmd IDs from payloads."""

    async def _run() -> None:
        """Run a minimal coordinator test that sends an MQTT action message containing.

        a text-form command and verifies the device property update is applied.

        This helper constructs a minimal JackerySolarVaultCoordinator instance with
        stubbed debug and push callbacks, delivers an MQTT "action" payload whose
        `FIELD_CMD` is a text string (e.g., "{MQTT_CMD_QUERY_DEVICE_PROPERTY}.0") and a
        body containing a `new` property, and asserts the coordinator merges that `new`
        value into the device's `PAYLOAD_PROPERTIES` and that a partial update was
        pushed.
        """
        self = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
        self.data = {"dev": {PAYLOAD_PROPERTIES: {"old": 1}}}
        self._device_index = {"dev": {}}
        self._property_overrides = {}
        captured: dict[str, object] = {}

        async def _debug_event(_event_or_factory: object) -> None:  # noqa: RUF029
            """Act as a no-op placeholder for emitting or producing debug events.

            Parameters:
                _event_or_factory (object): An event object or a zero-argument factory
                callable that would produce an event when the debug mechanism is
                active. This function currently ignores the argument and returns
                without side effects.
            """
            return

        def _push_partial_update(new_data: dict[str, object]) -> None:
            """Store a partial update payload into the test capture dictionary.

            Parameters:
                new_data (dict[str, object]): Partial update payload to capture under
                the key "data".
            """
            captured["data"] = new_data

        self._async_payload_debug_event = _debug_event
        self._push_partial_update = _push_partial_update
        self._schedule_battery_pack_ota_enrichment = lambda _device_id: None

        await JackerySolarVaultCoordinator._async_handle_mqtt_message(  # noqa: SLF001
            self,
            "hb/app/user/action",
            {
                FIELD_BODY: {
                    FIELD_CMD: f"{MQTT_CMD_QUERY_DEVICE_PROPERTY}.0",
                    "new": 2,
                },
            },
        )

        data = captured["data"]
        assert isinstance(data, dict)
        assert data["dev"][PAYLOAD_PROPERTIES]["new"] == 2  # noqa: PLR2004

    asyncio.run(_run())


def test_subdevice_payload_accepts_text_action_id_and_rejects_bad_values() -> None:
    """Verify _is_subdevice_payload accepts numeric action IDs provided as strings and.

    rejects invalid non-numeric values.

    Asserts that string forms `"3032"` and `"3032.0"` are treated as valid subdevice
    action IDs, while `True` and `float('nan')` are rejected.
    """
    assert JackerySolarVaultCoordinator._is_subdevice_payload(  # noqa: SLF001
        {FIELD_ACTION_ID: "3032"},
        {},
    )
    assert JackerySolarVaultCoordinator._is_subdevice_payload(  # noqa: SLF001
        {FIELD_ACTION_ID: "3032.0"},
        {},
    )
    assert not JackerySolarVaultCoordinator._is_subdevice_payload(  # noqa: SLF001
        {FIELD_ACTION_ID: True},
        {},
    )
    assert not JackerySolarVaultCoordinator._is_subdevice_payload(  # noqa: SLF001
        {FIELD_ACTION_ID: float("nan")},
        {},
    )


def test_mqtt_payload_buckets_survive_http_refresh() -> None:
    """MQTT-only accessory buckets must not disappear on a slow HTTP refresh."""
    const_source = _read(CONST_PATH)
    preserved = const_source.split("PRESERVED_FAST_PAYLOAD_KEYS", 1)[1].split(
        ")",
        1,
    )[0]
    assert "PAYLOAD_CT_METER" in preserved
    assert "PAYLOAD_METER_HEADS" in preserved
    assert "PAYLOAD_SMART_PLUGS" in preserved


def test_http_refresh_keeps_fresh_mqtt_live_soc_over_stale_http() -> None:
    """Stale HTTP property snapshots must not create SOC spikes."""

    class _Mqtt:
        def __init__(self, *, silent: bool) -> None:
            self.silent = silent

        def diagnostics_snapshot(self) -> dict[str, object]:
            """Collect a minimal diagnostics snapshot for MQTT connectivity.

            Returns:
                snapshot (dict[str, object]): Mapping with:
                    - "connected": `True` if the coordinator is currently connected to
                    MQTT.
                    - "mqtt_silent_for_too_long": boolean flag taken from `self.silent`
                    indicating whether MQTT has been silent for too long.
            """
            return {
                "connected": True,
                "mqtt_silent_for_too_long": self.silent,
            }

    self = JackerySolarVaultCoordinator.__new__(JackerySolarVaultCoordinator)
    entry = {
        PAYLOAD_MQTT_LAST: {"messageType": "UploadCombineData"},
        PAYLOAD_PROPERTIES: {
            FIELD_SOC: 49,
            FIELD_BAT_SOC: 51,
            FIELD_BAT_OUT_PW: 300,
            FIELD_WNAME: "old-wifi",
        },
    }
    http_props = {
        FIELD_SOC: 78,
        FIELD_BAT_SOC: 74,
        FIELD_BAT_OUT_PW: 163,
        FIELD_WNAME: "new-wifi",
    }

    self._mqtt = _Mqtt(silent=False)
    guarded = self._http_properties_with_live_overrides(entry, http_props)

    assert guarded[FIELD_SOC] == 49  # noqa: PLR2004
    assert guarded[FIELD_BAT_SOC] == 51  # noqa: PLR2004
    assert guarded[FIELD_BAT_OUT_PW] == 300  # noqa: PLR2004
    assert guarded[FIELD_WNAME] == "new-wifi"

    self._mqtt = _Mqtt(silent=True)
    unguarded = self._http_properties_with_live_overrides(entry, http_props)

    assert unguarded[FIELD_SOC] == 78  # noqa: PLR2004
    assert unguarded[FIELD_BAT_SOC] == 74  # noqa: PLR2004
    assert unguarded[FIELD_BAT_OUT_PW] == 163  # noqa: PLR2004


def test_fault_alarm_report_is_routed_as_alarm_payload() -> None:
    """UploadDeviceAlert actionId 3042 must not be merged into properties."""
    const_source = _read(CONST_PATH)
    assert (
        'MQTT_MESSAGE_UPLOAD_DEVICE_ALERT: Final = "UploadDeviceAlert"' in const_source
    )
    assert "MQTT_CMD_UPLOAD_DEVICE_ALERT: Final = 122" in const_source
    assert "ACTION_ID_FAULT_ALARM_REPORT: Final = 3042" in const_source
    assert "MQTT_ACTION_IDS_ALARM" in const_source

    handler = _function_source(COORDINATOR_PATH, "_async_handle_mqtt_message")
    assert "is_alarm =" in handler
    assert "MQTT_MESSAGE_UPLOAD_DEVICE_ALERT" in handler
    assert "MQTT_CMD_UPLOAD_DEVICE_ALERT" in handler
    assert "MQTT_ACTION_IDS_ALARM" in handler
    assert "elif not is_alarm:" in handler
    assert "updated[PAYLOAD_ALARM] = body if body else payload" in handler


def test_mqtt_uses_captured_qos_zero() -> None:
    """Verify MQTT publish and subscribe usage is configured to QoS 0.

    Asserts that the mqtt_push module declares a captured QoS of 0, that subscriptions
    use `qos=0`, and that the coordinator publishes JSON messages with `qos=0` and
    `retain=False`.
    """
    mqtt_source = _read(MQTT_PUSH_PATH)
    coordinator_source = _read(COORDINATOR_PATH)

    assert "qos: int = 0" in mqtt_source
    assert "subscribe(topic, qos=0)" in mqtt_source
    assert (
        "async_publish_json(topic, payload, qos=0, retain=False)" in coordinator_source
    )


def test_mqtt_payload_data_field_is_normalized_to_body() -> None:
    """Verify MQTT payloads using the 'data' field are normalized to the 'body' field.

    across the codebase.

    Asserts that the relevant constants for `data`/`body` and the ControlCombine
    message/cmd exist in const.py, and that mqtt_push and the coordinator normalize
    `FIELD_DATA` into `FIELD_BODY` by reading `data.get(FIELD_DATA)` and assigning it
    to `data[FIELD_BODY]` / `payload[FIELD_DATA]`.
    """
    mqtt_source = _read(MQTT_PUSH_PATH)
    coordinator_source = _read(COORDINATOR_PATH)
    const_source = _read(CONST_PATH)

    assert 'FIELD_DATA: Final = "data"' in const_source
    assert 'FIELD_BODY: Final = "body"' in const_source
    assert 'MQTT_MESSAGE_CONTROL_COMBINE: Final = "ControlCombine"' in const_source
    assert "MQTT_CMD_CONTROL_COMBINE: Final = 121" in const_source
    assert "alt_body = data.get(FIELD_DATA)" in mqtt_source
    assert "data[FIELD_BODY] = alt_body" in mqtt_source
    assert "alt_body = payload.get(FIELD_DATA)" in coordinator_source


def test_mqtt_topics_follow_documented_app_layout() -> None:
    """Guard the hb/app/<userId>/... topic layout."""
    const_source = _read(CONST_PATH)
    mqtt_source = _read(MQTT_PUSH_PATH)
    coordinator_source = _read(COORDINATOR_PATH)

    assert 'MQTT_TOPIC_PREFIX: Final = "hb/app"' in const_source
    for name, suffix in {
        "MQTT_TOPIC_DEVICE": "device",
        "MQTT_TOPIC_ALERT": "alert",
        "MQTT_TOPIC_CONFIG": "config",
        "MQTT_TOPIC_NOTICE": "notice",
        "MQTT_TOPIC_COMMAND": "command",
        "MQTT_TOPIC_ACTION": "action",
    }.items():
        assert f'{name}: Final = "{suffix}"' in const_source
    for name in (
        "MQTT_TOPIC_DEVICE",
        "MQTT_TOPIC_ALERT",
        "MQTT_TOPIC_CONFIG",
        "MQTT_TOPIC_NOTICE",
    ):
        assert name in const_source
    assert "MQTT_TOPIC_PREFIX" in mqtt_source
    assert "MQTT_TOPIC_SUFFIXES" in mqtt_source
    assert "MQTT_TOPIC_COMMAND" in coordinator_source


def test_mqtt_connect_requests_full_app_snapshot() -> None:
    """On reconnect the integration asks the app protocol for a fresh snapshot."""
    connected = _function_source(COORDINATOR_PATH, "_async_mqtt_connected")
    assert "_async_query_system_info_for_missing" in connected
    assert "_async_query_weather_plan_for_missing" in connected
    assert "_async_query_subdevices_for_missing" in connected
    assert "force=True" in connected
    assert "ensure_mqtt=False" in connected


def test_mqtt_credentials_are_derived_from_active_login_session() -> None:
    """The MQTT password must use the REST login userId/mqttPassWord/macId triple."""
    api_source = _read(
        ROOT / "custom_components" / "jackery_solarvault" / "client" / "api.py",
    )
    login = _function_source(
        ROOT / "custom_components" / "jackery_solarvault" / "client" / "api.py",
        "async_login",
    )
    credentials = _function_source(
        ROOT / "custom_components" / "jackery_solarvault" / "client" / "api.py",
        "async_get_mqtt_credentials",
    )

    assert "self._mqtt_user_id" in login
    assert "FIELD_USER_ID" in login
    assert "self._mqtt_seed_b64" in login
    assert "FIELD_MQTT_PASSWORD" in login
    assert "self._mqtt_mac_id = mac_id" in login
    assert "base64.b64decode(self._mqtt_seed_b64, validate=True)" in credentials
    assert "_aes_cbc_encrypt" in credentials
    assert "MQTT_CLIENT_ID_SUFFIX" in api_source
    assert "MQTT_USERNAME_SEPARATOR" in api_source


def test_setup_passes_configured_login_context_to_api() -> None:
    """Setup must honor stored app login context for cloud and MQTT parity."""
    init_source = _read(
        ROOT / "custom_components" / "jackery_solarvault" / "__init__.py",
    )
    assert "CONF_MQTT_MAC_ID" in init_source
    assert "CONF_REGION_CODE" in init_source
    assert "mqtt_mac_id=entry.data.get(CONF_MQTT_MAC_ID)" in init_source
    assert "region_code=entry.data.get(CONF_REGION_CODE)" in init_source


def test_write_retries_rebuild_auth_headers_after_relogin() -> None:
    """PUT/POST retry paths must use the refreshed token after re-login."""
    api_path = ROOT / "custom_components" / "jackery_solarvault" / "client" / "api.py"
    for name in ("_put_json", "_post_form"):
        source = _function_source(api_path, name)
        assert "def _request_headers()" in source
        assert "headers=_request_headers()" in source
        assert "self._token = None" in source
        assert "await self._ensure_token()" in source
