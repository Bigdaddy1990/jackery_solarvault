"""Patch recorder related functions.

This file is originally from homeassistant/core and modified by pytest-homeassistant-custom-component.
"""  # noqa: E501

from contextlib import contextmanager
import sys

# Patch recorder util session scope
from homeassistant.helpers import recorder as recorder_helper

# Make sure homeassistant.components.recorder.util is not already imported
assert "homeassistant.components.recorder.util" not in sys.modules  # noqa: S101

real_session_scope = recorder_helper.session_scope


@contextmanager
def _session_scope_wrapper(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
    """Make session_scope patchable.

    This function will be imported by recorder modules.
    """
    with real_session_scope(*args, **kwargs) as ses:
        yield ses


recorder_helper.session_scope = _session_scope_wrapper
