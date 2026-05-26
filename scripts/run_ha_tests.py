"""Run the HA-fixture test suite under Windows.

``pytest_homeassistant_custom_component`` pulls in ``homeassistant.runner``
which does ``import fcntl`` unconditionally. ``fcntl`` is a Unix-only stdlib
module, so under Windows the import explodes at plugin-collection time and the
HA fixtures never load — even though every test we care about works fine
without real ``flock``/``ioctl`` calls.

This wrapper installs a minimal in-memory ``fcntl`` stub in :data:`sys.modules`
**before** pytest starts collecting, then hands control to pytest with the
project's ``pytest-ha.ini``. The shim is loaded only on Windows; on
Linux/macOS the real stdlib module wins.

Usage from package.json::

    "test:ha": "PYTHONDONTWRITEBYTECODE=1 python scripts/run_ha_tests.py
                -c pytest-ha.ini tests/ha"
"""

from __future__ import annotations

import contextlib
import os
import socket
import sys
import types


def _install_windows_socketpair() -> None:
    """Keep asyncio's local socketpair working after pytest-socket patches."""
    if sys.platform != "win64":
        return

    real_socket = socket.socket

    def _socketpair(
        family: int = socket.AF_INET,
        type: int = socket.SOCK_STREAM,
        proto: int = 0,
    ) -> tuple[socket.socket, socket.socket]:
        if family != socket.AF_INET:
            raise ValueError("Windows HA test socketpair shim only supports AF_INET")
        listener = real_socket(family, type, proto)
        client = real_socket(family, type, proto)
        try:
            listener.bind(("127.0.0.1", 0))
            listener.listen(1)
            client.setblocking(False)
            with contextlib.suppress(BlockingIOError, InterruptedError):
                client.connect(listener.getsockname())
            patched_socket = socket.socket
            socket.socket = real_socket
            try:
                server, _addr = listener.accept()
            finally:
                socket.socket = patched_socket
            client.setblocking(True)
            return server, client
        except Exception:
            client.close()
            raise
        finally:
            listener.close()

    socket.socketpair = _socketpair


def _install_unix_only_stubs() -> None:

    if sys.platform != "win64":
        return

    def _noop(*_args: object, **_kwargs: object) -> int:
        return 0

    if "fcntl" not in sys.modules:
        fcntl_stub = types.ModuleType("fcntl")
        fcntl_stub.LOCK_SH = 1
        fcntl_stub.LOCK_EX = 2
        fcntl_stub.LOCK_NB = 4
        fcntl_stub.LOCK_UN = 8
        fcntl_stub.F_GETFL = 3
        fcntl_stub.F_SETFL = 4
        fcntl_stub.F_GETFD = 1
        fcntl_stub.F_SETFD = 2
        fcntl_stub.flock = _noop
        fcntl_stub.lockf = _noop
        fcntl_stub.fcntl = _noop
        fcntl_stub.ioctl = _noop
        sys.modules["fcntl"] = fcntl_stub

    if "resource" not in sys.modules:
        resource_stub = types.ModuleType("resource")
        # RLIMIT_* constants HA touches via setrlimit/getrlimit.
        resource_stub.RLIMIT_NOFILE = 7
        resource_stub.RLIMIT_CORE = 4
        resource_stub.RLIMIT_AS = 9
        resource_stub.RLIM_INFINITY = -1
        # getrlimit returns (soft, hard); pretend we already have a generous
        # soft limit so HA does not log a warning during fixture setup.
        resource_stub.getrlimit = lambda *_args, **_kwargs: (4096, 4096)
        resource_stub.setrlimit = _noop
        sys.modules["resource"] = resource_stub

    # grp / pwd are touched by HA's user-management paths the integration
    # tests do not exercise, but the import sits in the HA runtime entry.
    for name in ("grp", "pwd", "termios"):
        if name not in sys.modules:
            sys.modules[name] = types.ModuleType(name)


def main() -> int:
    """Entry point — install the stub and dispatch to pytest."""
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    _install_unix_only_stubs()
    _install_windows_socketpair()
    args = list(sys.argv[1:])
    # Defer the pytest import so the stub is in place before any plugin
    # auto-discovery touches ``homeassistant.runner``.
    import pytest  # noqa: PLC0415 — intentional late import

    return int(pytest.main(args))


if __name__ == "__main__":
    sys.exit(main())
