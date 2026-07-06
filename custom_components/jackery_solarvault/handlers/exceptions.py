"""Shared non-broad exception groups for Jackery SolarVault."""

from json import JSONDecodeError

from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from ..client import JackeryAuthError, JackeryError  # noqa: RUF100, TID252

try:  # pragma: no cover - SQLAlchemy ships with the recorder; guard for minimal envs
    from sqlalchemy.exc import SQLAlchemyError

    _SQLALCHEMY_IMPORT_ERRORS: tuple[type[BaseException], ...] = (SQLAlchemyError,)
except ImportError:  # pragma: no cover - import fallback when recorder is absent
    _SQLALCHEMY_IMPORT_ERRORS = ()

ACTION_WRITE_ERRORS = (
    JackeryError,
    HomeAssistantError,
    TimeoutError,
    OSError,
    RuntimeError,
    ValueError,
    TypeError,
    KeyError,
)

BACKGROUND_TASK_ERRORS = (
    JackeryError,
    HomeAssistantError,
    TimeoutError,
    OSError,
    RuntimeError,
    ValueError,
    TypeError,
    KeyError,
)

_RECORDER_BASE_ERRORS: tuple[type[BaseException], ...] = (
    HomeAssistantError,
    ValueError,
    TypeError,
    KeyError,
    LookupError,
    OSError,
    RuntimeError,
)
RECORDER_IMPORT_ERRORS: tuple[type[BaseException], ...] = (
    *_RECORDER_BASE_ERRORS,
    *_SQLALCHEMY_IMPORT_ERRORS,
)
# Synchronous failures from recorder statistics import calls.
#
# ``async_import_statistics`` validates ``statistic_id``/``source`` and may raise
# ``HomeAssistantError``/``ValueError`` before queuing. If the recorder surfaces
# a database error synchronously it derives from ``sqlalchemy.exc.SQLAlchemyError``
# (e.g. ``IntegrityError`` for a UNIQUE collision), which is not covered by
# ``BACKGROUND_TASK_ERRORS``. This tuple makes such failures catchable and
# visible instead of escaping the per-statistic loop into the background task.

RECORDER_BACKGROUND_TASK_ERRORS: tuple[type[BaseException], ...] = (
    *BACKGROUND_TASK_ERRORS,
    *RECORDER_IMPORT_ERRORS,
)
# Background statistics-import task errors: base task errors + recorder/DB errors.
#
# Pre-combined as a single annotated tuple so the ``except`` clause references
# one ``tuple[type[BaseException], ...]`` instead of an inline starred unpack
# (which mypy rejects as a non-exception-class expression in ``except``).

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
    "RECORDER_BACKGROUND_TASK_ERRORS",
    "RECORDER_IMPORT_ERRORS",
    "STORAGE_ERRORS",
]
