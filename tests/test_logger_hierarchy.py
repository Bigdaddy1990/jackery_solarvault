"""Regression tests for Home Assistant-controlled Jackery logger inheritance."""

import logging

import pytest

from custom_components.jackery_solarvault.client import local_mqtt, mqtt_push

_INTEGRATION_LOGGER = "custom_components.jackery_solarvault"
_TRANSPORT_LOGGERS = (
    logging.getLogger(local_mqtt.__name__),
    logging.getLogger(mqtt_push.__name__),
)


@pytest.mark.parametrize("logger", _TRANSPORT_LOGGERS)
def test_transport_logger_inherits_home_assistant_level(
    logger: logging.Logger,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Integration debug must reach both local and cloud transport loggers."""
    assert logger.level == logging.NOTSET
    assert logger.propagate is True

    message = f"inherited-debug-{logger.name}"
    with caplog.at_level(logging.DEBUG, logger=_INTEGRATION_LOGGER):
        logger.debug(message)

    assert any(record.getMessage() == message for record in caplog.records)


@pytest.mark.parametrize("logger", _TRANSPORT_LOGGERS)
def test_transport_debug_stays_quiet_at_normal_parent_level(
    logger: logging.Logger,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """NOTSET inheritance suppresses transport DEBUG outside HA debug mode."""
    message = f"normal-level-debug-{logger.name}"
    with caplog.at_level(logging.INFO, logger=_INTEGRATION_LOGGER):
        logger.debug(message)

    assert all(record.getMessage() != message for record in caplog.records)
