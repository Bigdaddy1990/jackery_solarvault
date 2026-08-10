"""Regression tests for Home Assistant-controlled Jackery logger inheritance."""

import logging

import pytest

from custom_components.jackery_solarvault.client import local_mqtt, mqtt_push

_INTEGRATION_LOGGER = "custom_components.jackery_solarvault"
_AIOMQTT_CHILD_LOGGERS = (
    local_mqtt._AIOMQTT_LOGGER,  # ruff: ignore[private-member-access]
    mqtt_push._AIOMQTT_LOGGER,  # ruff: ignore[private-member-access]
)


@pytest.mark.parametrize("logger", _AIOMQTT_CHILD_LOGGERS)
def test_aiomqtt_child_logger_inherits_home_assistant_level(
    logger: logging.Logger,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Integration debug must reach every per-client aiomqtt child logger."""
    assert logger.level == logging.NOTSET
    assert logger.propagate is True

    message = f"inherited-debug-{logger.name}"
    with caplog.at_level(logging.DEBUG, logger=_INTEGRATION_LOGGER):
        logger.debug(message)

    assert any(record.getMessage() == message for record in caplog.records)


def test_aiomqtt_library_logger_is_not_overridden_at_import() -> None:
    """Importing Jackery transports must not mutate the external library logger."""
    assert logging.getLogger("aiomqtt").level == logging.NOTSET


@pytest.mark.parametrize("logger", _AIOMQTT_CHILD_LOGGERS)
def test_aiomqtt_debug_stays_quiet_at_normal_parent_level(
    logger: logging.Logger,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """NOTSET inheritance still suppresses packet DEBUG outside HA debug mode."""
    message = f"normal-level-debug-{logger.name}"
    with caplog.at_level(logging.INFO, logger=_INTEGRATION_LOGGER):
        logger.debug(message)

    assert all(record.getMessage() != message for record in caplog.records)
