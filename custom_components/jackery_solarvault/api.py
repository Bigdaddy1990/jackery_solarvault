"""Root facade re-export for JackeryApi."""

from .client.api import JackeryApi, JackeryApiError, JackeryAuthError, JackeryError

__all__ = [
    "JackeryApi",
    "JackeryApiError",
    "JackeryAuthError",
    "JackeryError",
]
