"""Shared credential safety contract for Jackery SolarVault."""

import hashlib
import logging
import re
from typing import TYPE_CHECKING, Final

import voluptuous as vol

from ..const import REDACTED_VALUE, REDACT_KEYS
from ..util import jackery_dev_mode_enabled

if TYPE_CHECKING:
    from collections.abc import Mapping


_LOGGER = logging.getLogger(__name__)
MAX_TOKEN_LENGTH: Final = 512
MAX_USERNAME_LENGTH: Final = 128
MAX_PASSWORD_LENGTH: Final = 128
_SECRET_ASSIGNMENT: Final = re.compile(
    r"(?P<prefix>(?<!\w)(?:"
    + "|".join(re.escape(key) for key in sorted(REDACT_KEYS))
    + r""")["']?\s*[:=]\s*)(?:"""
    + re.escape(REDACTED_VALUE)
    + r"""|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|[^\r\n]+)""",
    re.IGNORECASE,
)
_BEARER_CREDENTIAL: Final = re.compile(r"(\bBearer\s+)[^\s'\"]+", re.IGNORECASE)


class CredentialValidationError(vol.Invalid):
    """Credential validation failure that never includes the credential value."""

    def __init__(self, field: str, reason: str) -> None:
        """Build a redacted validation error from field name and failure class."""
        super().__init__(f"{field} {reason}")


def credential_text(value: object, *, field: str, max_length: int) -> str:
    """Validate credential type and length without echoing its value."""
    if not isinstance(value, str):
        raise CredentialValidationError(field, "must be a string")
    if len(value) > max_length:
        raise CredentialValidationError(field, "exceeds the permitted length")
    return value


def credential_fingerprint(fields: Mapping[str, str]) -> str:
    """Hash credentials with explicit, length-delimited field names and values."""
    digest = hashlib.sha256()
    digest.update(b"jackery-credential-v1\0")
    for name in sorted(fields):
        value = fields[name]
        credential_text(value, field=name, max_length=MAX_TOKEN_LENGTH)
        for part in (name.encode(), value.encode()):
            digest.update(len(part).to_bytes(4, "big"))
            digest.update(part)
    return digest.hexdigest()


def redacted_error_text(message: str, *, entry: object | None = None) -> str:
    """Honor explicit raw debugging before applying the normal credential scrub."""
    if jackery_dev_mode_enabled(entry):
        return message
    message = _SECRET_ASSIGNMENT.sub(
        lambda match: f"{match.group("prefix")}{REDACTED_VALUE}", message
    )
    return _BEARER_CREDENTIAL.sub(
        lambda match: f"{match.group(1)}{REDACTED_VALUE}", message
    )


def redacted_error(error: object, *, entry: object | None = None) -> str:
    """Return useful error classification with redacted exception details.

    Preserve exception structure and the process or entry raw-debug choice.
    Outside explicit raw debugging, labelled credential values are scrubbed.
    """
    if isinstance(error, BaseException):
        msg = str(error)
        return f"{type(error).__name__}: {redacted_error_text(msg, entry=entry)}"
    return redacted_error_text(str(error), entry=entry)
