"""Connect-attempt pacing for the Jackery BLE transport.

ESPHome Bluetooth proxies crash under rapid GATT connect/disconnect
cycles (8 stored ``esp32.crash`` reports were delivered 2026-07-03
22:33:41 local when the household proxy reconnected after a reboot).
:class:`BleConnectBackoff` enforces a per-device minimum spacing between
connect attempts that escalates exponentially on repeated failures:

* first failure → :data:`.const.BLE_CONNECT_BACKOFF_INITIAL_SEC` (30 s,
  identical to the previous fixed reconnect delay),
* each further failure doubles the delay,
* capped at :data:`.const.BLE_CONNECT_BACKOFF_MAX_SEC` (60 s),
* a successful connect resets the ladder.

The object is pure state + arithmetic (caller supplies ``now`` from the
event-loop monotonic clock) so it is unit-testable without hardware and
without an event loop.
"""

from dataclasses import dataclass, field

from ...const import (
    BLE_CONNECT_BACKOFF_INITIAL_SEC,
    BLE_CONNECT_BACKOFF_MAX_SEC,
)


@dataclass(slots=True)
class BleConnectBackoff:
    """Exponential per-device spacing between BLE connect attempts."""

    initial_sec: float = BLE_CONNECT_BACKOFF_INITIAL_SEC
    max_sec: float = BLE_CONNECT_BACKOFF_MAX_SEC
    _delay_sec: float = field(default=0.0, init=False)
    _not_before: float = field(default=0.0, init=False)

    def seconds_until_allowed(self, now: float) -> float:
        """Return how long the caller must still wait before connecting.

        Parameters:
            now (float): Current monotonic timestamp (``loop.time()``).

        Returns:
            float: Remaining wait in seconds; ``0.0`` when an attempt is
            allowed immediately.
        """
        return max(0.0, self._not_before - now)

    def record_failure(self, now: float) -> float:
        """Register a failed connect (or a lost link) and open a new window.

        The first failure applies :attr:`initial_sec`; every consecutive
        failure doubles the delay up to :attr:`max_sec`.

        Parameters:
            now (float): Current monotonic timestamp (``loop.time()``).

        Returns:
            float: The delay in seconds applied to the next attempt.
        """
        if self._delay_sec <= 0:
            self._delay_sec = self.initial_sec
        else:
            self._delay_sec = min(self._delay_sec * 2, self.max_sec)
        self._not_before = now + self._delay_sec
        return self._delay_sec

    def record_success(self) -> None:
        """Reset the ladder after a successful connect."""
        self._delay_sec = 0.0
        self._not_before = 0.0


__all__ = ["BleConnectBackoff"]
