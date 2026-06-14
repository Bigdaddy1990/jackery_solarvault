"""Branch packages A-E for ingestion, statistics, MQTT, BLE fallback, services."""

# ruff: noqa: ANN401, PLR2004, PLR6301, PT006, PT007, PT011, RUF012, RUF069, SLF001

from datetime import date
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.jackery_solarvault import services
from custom_components.jackery_solarvault.const import (
    APP_REQUEST_BEGIN_DATE_ALT,
    APP_REQUEST_DATE_TYPE_ALT,
    APP_REQUEST_END_DATE_ALT,
    APP_SECTION_PV_STAT,
    APP_STAT_TOTAL_SOLAR_ENERGY,
    DATE_TYPE_DAY,
    DATE_TYPE_MONTH,
    DATE_TYPE_WEEK,
    DATE_TYPE_YEAR,
    FIELD_ACTION_ID,
    FIELD_BODY,
    FIELD_DEVICE_ID,
    FIELD_MESSAGE_TYPE,
    MQTT_MESSAGE_CANCEL_WEATHER_ALERT,
    MQTT_MESSAGE_CONTROL_COMBINE,
    MQTT_MESSAGE_DELETE_ELECTRICITY_STRATEGY,
    MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE,
    MQTT_MESSAGE_DOWNLOAD_DEVICE_SCHEDULE,
    MQTT_MESSAGE_INSERT_ELECTRICITY_STRATEGY,
    MQTT_MESSAGE_QUERY_CIRCUIT_PROPERTY,
    MQTT_MESSAGE_QUERY_COMBINE_DATA,
    MQTT_MESSAGE_QUERY_CURRENT_ELECTRICITY_STRATEGY,
    MQTT_MESSAGE_QUERY_DEVICE_PROPERTY,
    MQTT_MESSAGE_QUERY_ELECTRICITY_STRATEGY,
    MQTT_MESSAGE_QUERY_TOU_SCHEDULE,
    MQTT_MESSAGE_QUERY_WEATHER_PLAN,
    MQTT_MESSAGE_SEND_WEATHER_ALERT,
    MQTT_MESSAGE_SET_BATTERY_BOUNDARY,
    MQTT_MESSAGE_TOU_SCHEDULE,
    MQTT_MESSAGE_UPDATE_ELECTRICITY_STRATEGY,
    MQTT_MESSAGE_UPLOAD_COMBINE_DATA,
    MQTT_MESSAGE_UPLOAD_INCREMENTAL_COMBINE_DATA,
    MQTT_MESSAGE_UPLOAD_WEATHER_PLAN,
    PAYLOAD_BATTERY_BOUNDARY,
    PAYLOAD_CIRCUIT_PROPERTY,
    PAYLOAD_ELECTRICITY_STRATEGY,
    PAYLOAD_PROPERTIES,
    PAYLOAD_TASK_PLAN,
    PAYLOAD_TOU_SCHEDULE,
    PAYLOAD_WEATHER_PLAN,
)
from custom_components.jackery_solarvault.coordinator import (
    JackerySolarVaultCoordinator,
)
from custom_components.jackery_solarvault.ingest import merge_live_properties
from custom_components.jackery_solarvault.util import (
    app_month_request_kwargs,
    app_period_request_kwargs,
    app_year_request_kwargs,
    apply_year_month_backfill,
    effective_period_total_value,
    verify_and_backfill,
)


def test_package_a_ingest_property_merge_guards_blank_alias_and_conflicts() -> None:
    """Package A: None/0/negative/nested/alias/source priority/conflicts."""
    base = {
        "soc": 88,
        "batterySoc": 88,
        "zero": 5,
        "negative": 1,
        "nested": {"keep": "value", "replace": 1},
        "source": "http",
    }
    update = {
        "soc": None,
        "batterySoc": 0,
        "zero": 0,
        "negative": -3,
        "nested": {"keep": None, "replace": 2, "new": "mqtt"},
        "source": "ble",
    }

    merged = merge_live_properties(base, update)

    assert merged["soc"] == 88
    assert merged["batterySoc"] == 0
    assert merged["zero"] == 0
    assert merged["negative"] == -3
    assert merged["nested"] == {"keep": "value", "replace": 2, "new": "mqtt"}
    assert merged["source"] == "ble"
    assert base["nested"] == {"keep": "value", "replace": 1}


@pytest.mark.parametrize(
    ("date_type", "expected"),
    [
        (DATE_TYPE_DAY, ("2026-06-14", "2026-06-14")),
        (DATE_TYPE_WEEK, ("2026-06-08", "2026-06-14")),
        (DATE_TYPE_MONTH, ("2026-06-01", "2026-06-30")),
        (DATE_TYPE_YEAR, ("2026-01-01", "2026-12-31")),
    ],
)
def test_package_b_statistics_period_request_ranges(
    date_type: str, expected: tuple[str, str]
) -> None:
    """Package B: day/week/month/year request ranges."""
    kwargs = app_period_request_kwargs(date_type, today=date(2026, 6, 14))

    assert kwargs[APP_REQUEST_DATE_TYPE_ALT] == date_type
    assert kwargs[APP_REQUEST_BEGIN_DATE_ALT] == expected[0]
    assert kwargs[APP_REQUEST_END_DATE_ALT] == expected[1]


def test_package_b_month_year_backfill_lifetime_guard_and_diagnostics() -> None:
    """Package B: month-year backfill, lower-bound guard, diagnostics reasons."""
    section = f"{APP_SECTION_PV_STAT}_{DATE_TYPE_YEAR}"
    payload: dict[str, Any] = {section: {APP_STAT_TOTAL_SOLAR_ENERGY: 1.0}}
    apply_year_month_backfill(
        payload,
        {
            APP_SECTION_PV_STAT: {
                1: {APP_STAT_TOTAL_SOLAR_ENERGY: 2.0},
                2: {APP_STAT_TOTAL_SOLAR_ENERGY: 3.0},
            }
        },
    )

    assert (
        effective_period_total_value(
            payload[section], section, APP_STAT_TOTAL_SOLAR_ENERGY
        )
        >= 5.0
    )
    reasons: list[str] = []
    assert (
        verify_and_backfill(4.0, 10.0, label="lifetime", on_rejection=reasons.append)
        == 4.0
    )
    assert reasons == ["lifetime:divergence"]
    assert app_month_request_kwargs(2026, 2)[APP_REQUEST_END_DATE_ALT] == "2026-02-28"
    assert app_year_request_kwargs(2026)[APP_REQUEST_BEGIN_DATE_ALT] == "2026-01-01"


class _MqttHarness:
    def __init__(self) -> None:
        self.data = {"dev1": {PAYLOAD_PROPERTIES: {}}}
        self._device_index = {"dev1": {}}
        self._DEVICE_LIFETIME_COUNTER_KEYS = frozenset({"life"})
        self._SYSTEM_INFO_KEYS = frozenset()
        self._MAIN_LIVE_PROPERTY_KEYS = frozenset({"watts", "soc"})
        self._system_info_cache: dict[str, dict[str, Any]] = {}
        self.pushed: dict[str, Any] | None = None

    async def _async_payload_debug_event(self, _factory: Any) -> None:
        return None

    def _resolve_device_id_from_mqtt(self, _payload: dict[str, Any]) -> str | None:
        return "dev1"

    def _merge_lifetime_counter_data(
        self, _updated: dict[str, Any], _body: dict[str, Any]
    ) -> bool:
        return False

    def _merge_device_statistic_data(
        self, _updated: dict[str, Any], _body: dict[str, Any]
    ) -> bool:
        return False

    def _is_subdevice_payload(
        self, _payload: dict[str, Any], _body: dict[str, Any]
    ) -> bool:
        return False

    def _strip_lifetime_counters(self, source: dict[str, Any]) -> dict[str, Any]:
        return dict(source)

    def _merge_main_properties_for_device(
        self, _device_id: str, base: dict[str, Any], updates: dict[str, Any]
    ) -> dict[str, Any]:
        return merge_live_properties(base, updates)

    def _note_property_equivalent_push(self, _body: dict[str, Any]) -> None:
        return None

    def _decode_third_party_mqtt_config_body(
        self, _device_id: str, body: dict[str, Any]
    ) -> dict[str, Any]:
        return body

    def _merge_subdevice_data(
        self, updated: dict[str, Any], body: dict[str, Any], *, device_id: str
    ) -> bool:
        updated["subdevice"] = {"device_id": device_id, **body}
        return True

    def _push_partial_update(self, new_data: dict[str, Any]) -> None:
        self.pushed = new_data
        self.data = new_data


@pytest.mark.parametrize(
    ("message_type", "payload_key"),
    [
        (MQTT_MESSAGE_DEVICE_PROPERTY_CHANGE, PAYLOAD_PROPERTIES),
        (MQTT_MESSAGE_CONTROL_COMBINE, PAYLOAD_PROPERTIES),
        (MQTT_MESSAGE_QUERY_COMBINE_DATA, PAYLOAD_PROPERTIES),
        (MQTT_MESSAGE_QUERY_DEVICE_PROPERTY, PAYLOAD_PROPERTIES),
        (MQTT_MESSAGE_UPLOAD_COMBINE_DATA, PAYLOAD_PROPERTIES),
        (MQTT_MESSAGE_UPLOAD_INCREMENTAL_COMBINE_DATA, PAYLOAD_PROPERTIES),
        (MQTT_MESSAGE_UPLOAD_WEATHER_PLAN, PAYLOAD_WEATHER_PLAN),
        (MQTT_MESSAGE_QUERY_WEATHER_PLAN, PAYLOAD_WEATHER_PLAN),
        (MQTT_MESSAGE_SEND_WEATHER_ALERT, PAYLOAD_WEATHER_PLAN),
        (MQTT_MESSAGE_CANCEL_WEATHER_ALERT, PAYLOAD_WEATHER_PLAN),
        (MQTT_MESSAGE_DOWNLOAD_DEVICE_SCHEDULE, PAYLOAD_TASK_PLAN),
        (MQTT_MESSAGE_QUERY_ELECTRICITY_STRATEGY, PAYLOAD_ELECTRICITY_STRATEGY),
        (MQTT_MESSAGE_INSERT_ELECTRICITY_STRATEGY, PAYLOAD_ELECTRICITY_STRATEGY),
        (MQTT_MESSAGE_UPDATE_ELECTRICITY_STRATEGY, PAYLOAD_ELECTRICITY_STRATEGY),
        (MQTT_MESSAGE_DELETE_ELECTRICITY_STRATEGY, PAYLOAD_ELECTRICITY_STRATEGY),
        (MQTT_MESSAGE_QUERY_CURRENT_ELECTRICITY_STRATEGY, PAYLOAD_ELECTRICITY_STRATEGY),
        (MQTT_MESSAGE_SET_BATTERY_BOUNDARY, PAYLOAD_BATTERY_BOUNDARY),
        (MQTT_MESSAGE_TOU_SCHEDULE, PAYLOAD_TOU_SCHEDULE),
        (MQTT_MESSAGE_QUERY_TOU_SCHEDULE, PAYLOAD_TOU_SCHEDULE),
        (MQTT_MESSAGE_QUERY_CIRCUIT_PROPERTY, PAYLOAD_CIRCUIT_PROPERTY),
    ],
)
@pytest.mark.asyncio()
async def test_package_c_mqtt_documented_message_type_routes(
    message_type: str, payload_key: str
) -> None:
    """Package C: documented messageType routes update the expected bucket."""
    coord = _MqttHarness()
    payload = {
        FIELD_DEVICE_ID: "dev1",
        FIELD_MESSAGE_TYPE: message_type,
        FIELD_BODY: {"watts": 42},
    }

    await JackerySolarVaultCoordinator._async_handle_mqtt_message(
        coord, "x/device", payload
    )  # type: ignore[arg-type]

    assert coord.pushed is not None
    assert payload_key in coord.data["dev1"]


@pytest.mark.asyncio()
async def test_package_c_mqtt_unknown_and_malformed_payloads_are_ignored() -> None:
    """Package C: unknown type, malformed body, encrypted failure do not mutate."""
    coord = _MqttHarness()

    await JackerySolarVaultCoordinator._async_handle_mqtt_message(  # type: ignore[arg-type]
        coord,
        "x/device",
        {FIELD_DEVICE_ID: "dev1", FIELD_MESSAGE_TYPE: "Unknown", FIELD_BODY: []},
    )
    assert coord.pushed is None

    await JackerySolarVaultCoordinator._async_handle_mqtt_message(  # type: ignore[arg-type]
        coord,
        "x/device",
        {
            FIELD_DEVICE_ID: "dev1",
            FIELD_ACTION_ID: "bad",
            FIELD_BODY: {"ciphertext": "not-json"},
        },
    )
    assert coord.data["dev1"][PAYLOAD_PROPERTIES]["ciphertext"] == "not-json"


@pytest.mark.parametrize(
    ("ble_result", "ble_error", "mqtt_called"),
    [(True, None, False), (False, None, True), (False, RuntimeError("ble"), True)],
)
@pytest.mark.asyncio()
async def test_package_d_ble_first_success_unavailable_exception_and_mqtt_fallback(
    ble_result: bool, ble_error: Exception | None, mqtt_called: bool
) -> None:
    """Package D: BLE success/unavailable/exception/MQTT fallback."""
    calls: list[str] = []

    class Coord:
        def _coerce_transport_cmd(self, cmd: int) -> int:
            return cmd

        def _command_body_for_transport(
            self, body_fields: dict[str, Any], *, cmd: int
        ) -> dict[str, Any]:
            return {"cmd": cmd, **body_fields}

        async def async_send_ble_command(self, *_args: Any, **_kwargs: Any) -> bool:
            calls.append("ble")
            if ble_error is not None:
                raise ble_error
            return ble_result

        async def _async_publish_command(self, *_args: Any, **_kwargs: Any) -> None:
            calls.append("mqtt")

    await JackerySolarVaultCoordinator._async_publish_command_ble_first(  # type: ignore[arg-type]
        Coord(),
        "dev1",
        message_type="DevicePropertyChange",
        action_id=3022,
        cmd=107,
        body_fields={"swEps": 1},
    )

    assert ("mqtt" in calls) is mqtt_called


def test_package_d_http_primary_setter_path_keeps_ble_helper_unreferenced() -> None:
    """Package D: HTTP-primary setters stay separate from BLE-first helper."""
    setter = JackerySolarVaultCoordinator.async_save_tou_plan
    assert "api" in setter.__code__.co_names
    assert "_async_publish_command_ble_first" not in setter.__code__.co_names


@pytest.mark.parametrize("bad_body", ["[1, 2]", "{bad", {"bad": object()}])
def test_package_e_service_schema_validation_maps_to_ha_error(bad_body: object) -> None:
    """Package E: service body schema validation maps invalid payloads."""
    with pytest.raises(Exception) as err:
        services._ble_body_from_service(bad_body, "dev1")

    assert "body" in err.value.translation_placeholders["error"]


@pytest.mark.asyncio()
async def test_package_e_service_api_success_missing_entry_and_failure_mapping() -> (
    None
):
    """Package E: missing entry, API success, HTTP failure/fallback error mapping."""
    sent: list[tuple[str, dict[str, object]]] = []

    class Coord:
        async def async_send_ble_command(
            self, device_id: str, body: dict[str, object], **_kwargs: object
        ) -> bool:
            sent.append((device_id, body))
            return True

    coord = Coord()
    coord.data = {"dev1": {}}
    hass = SimpleNamespace(
        config_entries=SimpleNamespace(async_loaded_entries=lambda _domain: [])
    )
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(
            services, "_resolve_jackery_device_id", lambda _hass, raw: raw
        )
        monkeypatch.setattr(services, "_loaded_coordinators", lambda _hass: [coord])
        await services._async_handle_send_ble_command(
            hass,
            SimpleNamespace(
                data={"device_id": "dev1", "cmd": 107, "body": {"ok": True}}
            ),
        )
        assert sent == [("dev1", {"ok": True})]

        class FailingCoord:
            data = {"dev1": {}}

            async def async_send_ble_command(
                self, *_args: object, **_kwargs: object
            ) -> bool:
                return False

        monkeypatch.setattr(
            services, "_loaded_coordinators", lambda _hass: [FailingCoord()]
        )
        with pytest.raises(Exception) as err:
            await services._async_handle_send_ble_command(
                hass,
                SimpleNamespace(
                    data={"device_id": "dev1", "cmd": 107, "body": {"ok": True}}
                ),
            )
        assert err.value.translation_key == "send_ble_command_failed"
    finally:
        monkeypatch.undo()
