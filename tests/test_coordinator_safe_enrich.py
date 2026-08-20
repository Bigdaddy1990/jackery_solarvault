"""Tests for _safe_enrich and related background task helpers in coordinator.py."""

import logging
from types import SimpleNamespace
from typing import Any

import pytest

from custom_components.jackery_solarvault.client.api import (
    JackeryAuthError,
    JackeryError,
)
from custom_components.jackery_solarvault.coordinator import _safe_enrich


class TestSafeEnrich:
    """Test _safe_enrich helper function."""

    def _bare_entry(self) -> Any:  # noqa: PLR6301
        entry = SimpleNamespace()
        entry.options = {}
        entry.data = {}
        return entry

    @pytest.mark.asyncio
    async def test_safe_enrich_success(self) -> None:
        """Test _safe_enrich runs enrichment function successfully."""
        entry = self._bare_entry()
        enrich_called = False

        async def enrich_fn(dev_id: str, entry: Any, stale_ok: bool) -> None:  # noqa: RUF029
            nonlocal enrich_called
            enrich_called = True

        await _safe_enrich("device-1", entry, enrich_fn, stale_ok=True)
        assert enrich_called is True

    @pytest.mark.asyncio
    async def test_safe_enrich_auth_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test _safe_enrich handles JackeryAuthError."""
        entry = self._bare_entry()
        caplog.set_level(logging.DEBUG)

        async def enrich_fn(dev_id: str, entry: Any, stale_ok: bool) -> None:  # noqa: RUF029
            raise JackeryAuthError("auth failed")

        await _safe_enrich("device-1", entry, enrich_fn, stale_ok=True)
        # Should log at DEBUG level and not raise
        assert "Background enrichment enrich_fn was auth-rejected" in caplog.text

    @pytest.mark.asyncio
    async def test_safe_enrich_timeout_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test _safe_enrich handles TimeoutError."""
        entry = self._bare_entry()
        caplog.set_level(logging.DEBUG)

        async def enrich_fn(dev_id: str, entry: Any, stale_ok: bool) -> None:  # noqa: RUF029
            raise TimeoutError("timeout")

        await _safe_enrich("device-1", entry, enrich_fn, stale_ok=True)
        assert "Background enrichment enrich_fn failed" in caplog.text

    @pytest.mark.asyncio
    async def test_safe_enrich_jackery_error(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Test _safe_enrich handles JackeryError."""
        entry = self._bare_entry()
        caplog.set_level(logging.DEBUG)

        async def enrich_fn(dev_id: str, entry: Any, stale_ok: bool) -> None:  # noqa: RUF029
            raise JackeryError("api error")

        await _safe_enrich("device-1", entry, enrich_fn, stale_ok=True)
        assert "Background enrichment enrich_fn failed" in caplog.text

    @pytest.mark.asyncio
    async def test_safe_enrich_other_exception_raises(self) -> None:
        """Test _safe_enrich raises for unexpected exceptions."""
        entry = self._bare_entry()

        async def enrich_fn(dev_id: str, entry: Any, stale_ok: bool) -> None:  # noqa: RUF029
            raise ValueError("unexpected")

        with pytest.raises(ValueError, match="unexpected"):
            await _safe_enrich("device-1", entry, enrich_fn, stale_ok=True)


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
