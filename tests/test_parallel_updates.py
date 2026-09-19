"""Write platforms must serialize concurrent control-write/update calls.

button/select/number/text all expose setters that push writes to the cloud
and to MQTT. Without ``PARALLEL_UPDATES = 1`` at module scope, HA's
``entity_platform`` runs those service calls unbounded, which can reorder
`DevicePropertyChange` commands and blow out the MQTT broker queue depth.
Mirrors ``test_switch_platform_serializes_writes`` in test_switch_entities.py.
"""

from custom_components.jackery_solarvault import (
    button as button_mod,
    number as number_mod,
    select as select_mod,
    text as text_mod,
)


def test_button_platform_serializes_writes() -> None:
    """Button is a write platform; PARALLEL_UPDATES must serialize writes."""
    assert button_mod.PARALLEL_UPDATES == 1


def test_select_platform_serializes_writes() -> None:
    """Select is a write platform; PARALLEL_UPDATES must serialize writes."""
    assert select_mod.PARALLEL_UPDATES == 1


def test_number_platform_serializes_writes() -> None:
    """Number is a write platform; PARALLEL_UPDATES must serialize writes."""
    assert number_mod.PARALLEL_UPDATES == 1


def test_text_platform_serializes_writes() -> None:
    """Text is a write platform; PARALLEL_UPDATES must serialize writes."""
    assert text_mod.PARALLEL_UPDATES == 1
