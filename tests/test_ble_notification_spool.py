"""Tests for the crash-safe BLE notification write-ahead spool."""

from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from custom_components.jackery_solarvault.client.ble_notification_spool import (
    BleNotificationSpool,
    BleSpoolRecord,
    BleSpoolStatus,
    _SqliteBleSpoolBackend,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _stage(
    spool: BleNotificationSpool,
    raw: bytes,
    *,
    notify_sequence: int,
) -> int:
    return spool.stage_notification(
        device_id="device-1",
        session_id="session-1",
        session_generation=1,
        notify_sequence=notify_sequence,
        received_at=datetime(2026, 8, 23, 10, 0, notify_sequence, tzinfo=UTC),
        raw=raw,
    ).sequence


@pytest.mark.asyncio
async def test_committed_rows_reopen_in_fifo_with_same_namespace_and_ids(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """A clean restart preserves order, namespace, IDs, and next sequence."""
    path = tmp_path / "ble-spool.sqlite3"
    first = BleNotificationSpool(hass, entry_id="entry-1", path=path)
    await first.async_open()
    first_namespace = first.namespace
    first_sequence = _stage(first, b"first", notify_sequence=1)
    second_sequence = _stage(first, b"second", notify_sequence=2)
    await first.async_flush_through(second_sequence)
    before_restart = await first.async_load_records()
    await first.async_close()

    replacement = BleNotificationSpool(hass, entry_id="entry-1", path=path)
    await replacement.async_open()
    after_restart = await replacement.async_load_records()
    third_sequence = _stage(replacement, b"third", notify_sequence=3)
    await replacement.async_flush_through(third_sequence)

    assert first_sequence == 1
    assert second_sequence == 2
    assert third_sequence == 3
    assert replacement.namespace == first_namespace
    assert [record.raw for record in before_restart] == [b"first", b"second"]
    assert [record.delivery_id for record in after_restart] == [
        f"{first_namespace}:n:1",
        f"{first_namespace}:n:2",
    ]
    await replacement.async_close()


@pytest.mark.asyncio
async def test_ambiguous_append_retry_is_idempotent_only_for_same_ticket(
    hass: HomeAssistant,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A commit whose executor result was lost cannot duplicate a callback."""
    path = tmp_path / "ambiguous.sqlite3"
    real_append = _SqliteBleSpoolBackend.append_batch
    calls = 0

    def _commit_then_raise_once(
        backend: _SqliteBleSpoolBackend,
        records: tuple[BleSpoolRecord, ...],
    ) -> Any:
        nonlocal calls
        calls += 1
        metrics = real_append(backend, records)
        if calls == 1:
            raise RuntimeError("executor result lost after commit")
        return metrics

    monkeypatch.setattr(
        _SqliteBleSpoolBackend,
        "append_batch",
        _commit_then_raise_once,
    )
    spool = BleNotificationSpool(hass, entry_id="entry-1", path=path)
    await spool.async_open()
    sequence = _stage(spool, b"only-once", notify_sequence=1)
    await spool.async_flush_through(sequence)
    records = await spool.async_load_records()

    assert calls == 2
    assert len(records) == 1
    assert records[0].raw == b"only-once"
    assert spool.metrics.pending_rows == 1
    await spool.async_close()


@pytest.mark.asyncio
async def test_precommit_append_failure_retries_the_same_ticket_without_loss(
    hass: HomeAssistant,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failure before commit retains the staged row and its stable identity."""
    path = tmp_path / "precommit-failure.sqlite3"
    real_append = _SqliteBleSpoolBackend.append_batch
    attempts: list[tuple[str, ...]] = []

    def _fail_before_first_commit(
        backend: _SqliteBleSpoolBackend,
        records: tuple[BleSpoolRecord, ...],
    ) -> Any:
        attempts.append(tuple(record.delivery_id for record in records))
        if len(attempts) == 1:
            raise OSError("temporary storage failure")
        return real_append(backend, records)

    monkeypatch.setattr(
        _SqliteBleSpoolBackend,
        "append_batch",
        _fail_before_first_commit,
    )
    spool = BleNotificationSpool(hass, entry_id="entry-1", path=path)
    await spool.async_open()
    sequence = _stage(spool, b"retained", notify_sequence=1)
    await spool.async_flush_through(sequence)
    records = await spool.async_load_records()

    assert len(attempts) == 2
    assert attempts[0] == attempts[1]
    assert [record.raw for record in records] == [b"retained"]
    await spool.async_close()


@pytest.mark.asyncio
async def test_task_factory_rejection_retains_staging_for_later_flush(
    hass: HomeAssistant,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lifecycle task-factory race must not raise through the BLE callback."""
    path = tmp_path / "task-factory-rejection.sqlite3"
    spool = BleNotificationSpool(hass, entry_id="entry-1", path=path)
    await spool.async_open()
    real_create = hass.async_create_background_task

    def _reject_task(*_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("task factory is closing")

    monkeypatch.setattr(hass, "async_create_background_task", _reject_task)
    ticket = spool.stage_notification(
        device_id="device-1",
        session_id="session-1",
        session_generation=1,
        notify_sequence=1,
        received_at=datetime(2026, 8, 23, 10, 0, 1, tzinfo=UTC),
        raw=b"still-owned",
    )

    assert ticket.sequence == 1
    assert spool.metrics.pending_rows == 1

    monkeypatch.setattr(hass, "async_create_background_task", real_create)
    await spool.async_flush_through(ticket.sequence)
    record = await spool.async_load_record(ticket.sequence)

    assert record is not None
    assert record.delivery_id == ticket.delivery_id
    assert record.raw == b"still-owned"
    await spool.async_close()


@pytest.mark.asyncio
async def test_confirm_deletes_only_named_rows_and_unconfirmed_rows_survive(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """Sink confirmation removes no neighboring or later record."""
    path = tmp_path / "confirm.sqlite3"
    spool = BleNotificationSpool(hass, entry_id="entry-1", path=path)
    await spool.async_open()
    first = _stage(spool, b"first", notify_sequence=1)
    second = _stage(spool, b"second", notify_sequence=2)
    third = _stage(spool, b"third", notify_sequence=3)
    await spool.async_flush_through(third)

    await spool.async_confirm((second,))
    records = await spool.async_load_records()

    assert first == 1
    assert [record.sequence for record in records] == [first, third]
    assert [record.raw for record in records] == [b"first", b"third"]
    assert spool.metrics.pending_rows == 2
    assert spool.metrics.pending_bytes == len(b"first") + len(b"third")
    await spool.async_close()


@pytest.mark.asyncio
async def test_immediate_confirm_waits_for_append_before_deleting(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """A confirmation racing the writer cannot become a silent no-op."""
    path = tmp_path / "confirm-race.sqlite3"
    spool = BleNotificationSpool(hass, entry_id="entry-1", path=path)
    await spool.async_open()
    sequence = _stage(spool, b"confirmed", notify_sequence=1)

    await spool.async_confirm((sequence,))

    assert await spool.async_load_records() == ()
    assert spool.metrics.pending_rows == 0
    await spool.async_close()


@pytest.mark.asyncio
async def test_immediate_status_update_waits_for_append_before_mutating(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """A durable disposition cannot be overwritten by the delayed append."""
    path = tmp_path / "status-race.sqlite3"
    spool = BleNotificationSpool(hass, entry_id="entry-1", path=path)
    await spool.async_open()
    sequence = _stage(spool, b"fragment", notify_sequence=1)

    await spool.async_mark_status(
        sequence,
        BleSpoolStatus.FRAGMENT,
        assembly_key="120:3022",
        chunk_index=1,
        chunk_count=2,
    )
    record = await spool.async_load_record(sequence)

    assert record is not None
    assert record.status is BleSpoolStatus.FRAGMENT
    assert record.assembly_key == "120:3022"
    assert record.chunk_index == 1
    assert record.chunk_count == 2
    await spool.async_close()


@pytest.mark.asyncio
async def test_load_record_returns_only_the_requested_durable_sequence(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """A session queue can hydrate one committed payload without loading the spool."""
    path = tmp_path / "load-one.sqlite3"
    spool = BleNotificationSpool(hass, entry_id="entry-1", path=path)
    await spool.async_open()
    first = _stage(spool, b"first", notify_sequence=1)
    second = _stage(spool, b"second", notify_sequence=2)
    await spool.async_flush_through(second)

    record = await spool.async_load_record(second)

    assert first == 1
    assert record is not None
    assert record.sequence == second
    assert record.raw == b"second"
    assert await spool.async_load_record(999) is None
    await spool.async_close()
