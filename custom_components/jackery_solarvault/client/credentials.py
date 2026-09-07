"""Shared credential safety contract for Jackery SolarVault."""

import hashlib
from typing import TYPE_CHECKING, Final

import voluptuous as vol

from ..const import REDACTED_VALUE

if TYPE_CHECKING:
    from collections.abc import Mapping

MAX_TOKEN_LENGTH: Final = 512
MAX_USERNAME_LENGTH: Final = 128
MAX_PASSWORD_LENGTH: Final = 128


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


def redacted_error(error: object) -> str:
    """Return useful error classification without retaining exception details."""
    if isinstance(error, BaseException):
        return f"{type(error).__name__}: {REDACTED_VALUE}"
    return str(error)
