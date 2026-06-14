"""Shared non-broad exception groups for Jackery SolarVault."""

from json import JSONDecodeError

from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from .client import JackeryAuthError, JackeryError

ACTION_WRITE_ERRORS = (
    JackeryError,
    HomeAssistantError,
    TimeoutError,
    RuntimeError,
    ValueError,
    TypeError,
    KeyError,
)

BACKGROUND_TASK_ERRORS = (
    JackeryError,
    HomeAssistantError,
    TimeoutError,
    RuntimeError,
    ValueError,
    TypeError,
    KeyError,
)

PAYLOAD_PARSE_ERRORS = (
    UnicodeDecodeError,
    JSONDecodeError,
    ValueError,
    TypeError,
    KeyError,
)

STORAGE_ERRORS = (OSError, ValueError, TypeError, KeyError, RuntimeError)

AUTH_ERRORS = (ConfigEntryAuthFailed, JackeryAuthError)

__all__ = [
    "ACTION_WRITE_ERRORS",
    "AUTH_ERRORS",
    "BACKGROUND_TASK_ERRORS",
    "PAYLOAD_PARSE_ERRORS",
    "STORAGE_ERRORS",
]
