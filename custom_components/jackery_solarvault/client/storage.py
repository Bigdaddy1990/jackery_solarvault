"""Keep Jackery persistent state together and migrate legacy stores safely."""

from typing import TYPE_CHECKING, Any

from homeassistant.core import CoreState
from homeassistant.helpers.storage import Store

from ..const import DOMAIN
from ..util import get_store_lock

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


class JackeryStore[T: dict[str, Any]](Store[T]):
    """Use the integration folder while preserving older root-level data."""

    def __init__(self, hass: HomeAssistant, version: int, key: str) -> None:
        """Retain the old key only for a verified migration."""
        if key.startswith(f"{DOMAIN}/"):
            self._legacy_key = key.removeprefix(f"{DOMAIN}/")
            storage_key = key
        else:
            self._legacy_key = key
            storage_key = f"{DOMAIN}/{key}"
        super().__init__(hass, version, storage_key)

    async def async_load(self) -> T | None:
        """Load current state and relocate the legacy file after readback."""
        async with get_store_lock(self.hass, f"{self.key}.migration"):
            current: T | None = await super().async_load()
            if self._legacy_key == self.key:
                return current
            legacy = Store[T](self.hass, self.version, self._legacy_key)
            previous: T | None = await legacy.async_load()
            if previous is None:
                return current
            if self.hass.state is CoreState.stopping:
                return previous if current is None else current
            destination = (
                self
                if current is None
                else Store[T](self.hass, self.version, f"{self.key}.legacy")
            )
            if current is not None:
                archive_index = 0
                while (
                    archived := await destination.async_load()
                ) is not None and archived != previous:
                    archive_index += 1
                    destination = Store[T](
                        self.hass, self.version, f"{self.key}.legacy.{archive_index}"
                    )
            await destination.async_save(previous)
            verified = await Store[T](
                self.hass, self.version, destination.key
            ).async_load()
            if verified != previous:
                msg = f"Jackery storage migration could not verify {self._legacy_key}"
                raise OSError(msg)
            await legacy.async_remove()
            return previous if current is None else current
