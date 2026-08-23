"""Import-contract tests for client-owned transport helper modules."""

from custom_components.jackery_solarvault.client.credentials import credential_text
from custom_components.jackery_solarvault.client.mqtt_discovery import (
    JackeryMqttSensorPublisher,
)
from custom_components.jackery_solarvault.client.transport_supervisor import (
    TransportSupervisor,
    TransportSupervisorManager,
)


def test_transport_helpers_are_owned_by_client_package() -> None:
    """Transport and credential helpers expose their canonical client imports."""
    assert callable(credential_text)
    assert JackeryMqttSensorPublisher.__name__ == "JackeryMqttSensorPublisher"
    assert TransportSupervisor.__name__ == "TransportSupervisor"
    assert TransportSupervisorManager.__name__ == "TransportSupervisorManager"
