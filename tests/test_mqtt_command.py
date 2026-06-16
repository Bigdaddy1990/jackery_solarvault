from custom_components.jackery_solarvault.client.mqtt_command import (  # noqa: D100
    build_smali_command_envelope,
    command_body_for_transport,
)


def test_command_builder_uses_smali_envelope_order_and_body_cmd() -> None:  # noqa: D103
    body = command_body_for_transport({"devType": 1}, cmd=110)

    assert build_smali_command_envelope(
        device_sn="SN123",
        message_type="QuerySubDeviceGroupProperty",
        action_id=3014,
        body=body,
        timestamp_ms=123456789,
    ) == {
        "deviceSn": "SN123",
        "id": 123456789,
        "version": 0,
        "messageType": "QuerySubDeviceGroupProperty",
        "actionId": 3014,
        "timestamp": 123456789,
        "body": {"devType": 1, "cmd": 110},
    }


def test_command_body_omits_zero_ble_msg_type_cmd() -> None:  # noqa: D103
    assert command_body_for_transport({"sw": 1}, cmd=0) == {"sw": 1}
