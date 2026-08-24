"""Synchronous SQLite backend for the BLE notification spool."""

from collections.abc import Sequence
from datetime import datetime
from pathlib import Path
import secrets
import sqlite3
from typing import cast

from .ble_notification_spool_models import (
    BleSpoolMetrics,
    BleSpoolRecord,
    BleSpoolStatus,
)

_SCHEMA_VERSION = 1


class _SqliteBleSpoolBackend:
    """Synchronous SQLite backend; callers run every method in an executor."""

    def __init__(self, path: Path, entry_id: str) -> None:
        self._path = path
        self._entry_id = entry_id

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    def initialize(self) -> tuple[str, int, BleSpoolMetrics]:
        """Create or validate schema and return namespace plus next sequence."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS spool_meta (
                    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                    schema_version INTEGER NOT NULL,
                    entry_id TEXT NOT NULL,
                    namespace TEXT NOT NULL,
                    next_sequence INTEGER NOT NULL,
                    pending_rows INTEGER NOT NULL DEFAULT 0,
                    pending_bytes INTEGER NOT NULL DEFAULT 0,
                    depth_high_watermark INTEGER NOT NULL DEFAULT 0,
                    bytes_high_watermark INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS notification (
                    sequence INTEGER PRIMARY KEY,
                    delivery_id TEXT NOT NULL UNIQUE,
                    device_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    session_generation INTEGER NOT NULL,
                    notify_sequence INTEGER NOT NULL,
                    received_at TEXT NOT NULL,
                    raw BLOB NOT NULL,
                    status INTEGER NOT NULL CHECK (status BETWEEN 0 AND 4),
                    assembly_key TEXT,
                    chunk_index INTEGER,
                    chunk_count INTEGER,
                    retry_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_us INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS notification_status_sequence
                ON notification(status, sequence);
                CREATE INDEX IF NOT EXISTS notification_assembly
                ON notification(device_id, session_id, assembly_key, chunk_index);
                """
            )
            row = connection.execute(
                "SELECT * FROM spool_meta WHERE singleton = 1"
            ).fetchone()
            if row is None:
                namespace = secrets.token_hex(16)
                connection.execute(
                    """
                    INSERT INTO spool_meta (
                        singleton, schema_version, entry_id, namespace,
                        next_sequence
                    ) VALUES (1, ?, ?, ?, 1)
                    """,
                    (_SCHEMA_VERSION, self._entry_id, namespace),
                )
            else:
                if int(row["schema_version"]) != _SCHEMA_VERSION:
                    msg = (
                        f"Unsupported Jackery BLE spool schema {row["schema_version"]}"
                    )
                    raise RuntimeError(msg)
                if str(row["entry_id"]) != self._entry_id:
                    raise RuntimeError("Jackery BLE spool belongs to another entry")

            aggregate = connection.execute(
                """
                SELECT COUNT(*) AS rows, COALESCE(SUM(length(raw)), 0) AS bytes,
                       COALESCE(MAX(sequence), 0) AS max_sequence
                FROM notification
                """
            ).fetchone()
            assert aggregate is not None
            pending_rows = int(aggregate["rows"])
            pending_bytes = int(aggregate["bytes"])
            next_sequence = int(aggregate["max_sequence"]) + 1
            connection.execute(
                """
                UPDATE spool_meta
                SET pending_rows = ?, pending_bytes = ?,
                    next_sequence = MAX(next_sequence, ?),
                    depth_high_watermark = MAX(depth_high_watermark, ?),
                    bytes_high_watermark = MAX(bytes_high_watermark, ?)
                WHERE singleton = 1
                """,
                (
                    pending_rows,
                    pending_bytes,
                    next_sequence,
                    pending_rows,
                    pending_bytes,
                ),
            )
            connection.execute(f"PRAGMA user_version = {_SCHEMA_VERSION}")
            meta = connection.execute(
                "SELECT * FROM spool_meta WHERE singleton = 1"
            ).fetchone()
            assert meta is not None
            return (
                str(meta["namespace"]),
                int(meta["next_sequence"]),
                self._metrics_from_row(meta),
            )

    @staticmethod
    def _metrics_from_row(row: sqlite3.Row) -> BleSpoolMetrics:
        return BleSpoolMetrics(
            pending_rows=int(row["pending_rows"]),
            pending_bytes=int(row["pending_bytes"]),
            depth_high_watermark=int(row["depth_high_watermark"]),
            bytes_high_watermark=int(row["bytes_high_watermark"]),
        )

    def _read_metrics(self, connection: sqlite3.Connection) -> BleSpoolMetrics:
        row = connection.execute(
            "SELECT * FROM spool_meta WHERE singleton = 1"
        ).fetchone()
        if row is None:
            raise RuntimeError("Jackery BLE spool metadata is missing")
        return self._metrics_from_row(row)

    def append_batch(self, records: Sequence[BleSpoolRecord]) -> BleSpoolMetrics:
        """Append a sequence-preserving batch idempotently in one FULL WAL commit."""
        if not records:
            with self._connect() as connection:
                return self._read_metrics(connection)
        with self._connect() as connection:
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("BEGIN IMMEDIATE")
            inserted_rows = 0
            inserted_bytes = 0
            for record in records:
                cursor = connection.execute(
                    """
                    INSERT OR IGNORE INTO notification (
                        sequence, delivery_id, device_id, session_id,
                        session_generation, notify_sequence, received_at, raw,
                        status, assembly_key, chunk_index, chunk_count
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        record.sequence,
                        record.delivery_id,
                        record.device_id,
                        record.session_id,
                        record.session_generation,
                        record.notify_sequence,
                        record.received_at.isoformat(),
                        record.raw,
                        int(record.status),
                        record.assembly_key,
                        record.chunk_index,
                        record.chunk_count,
                    ),
                )
                if cursor.rowcount:
                    inserted_rows += 1
                    inserted_bytes += len(record.raw)
                    continue
                existing = connection.execute(
                    "SELECT * FROM notification WHERE delivery_id = ?",
                    (record.delivery_id,),
                ).fetchone()
                if existing is None or not self._record_matches(existing, record):
                    raise RuntimeError(
                        f"Conflicting Jackery BLE spool record {record.delivery_id}"
                    )
            next_sequence = max(record.sequence for record in records) + 1
            connection.execute(
                """
                UPDATE spool_meta
                SET next_sequence = MAX(next_sequence, ?),
                    pending_rows = pending_rows + ?,
                    pending_bytes = pending_bytes + ?,
                    depth_high_watermark = MAX(
                        depth_high_watermark, pending_rows + ?
                    ),
                    bytes_high_watermark = MAX(
                        bytes_high_watermark, pending_bytes + ?
                    )
                WHERE singleton = 1
                """,
                (
                    next_sequence,
                    inserted_rows,
                    inserted_bytes,
                    inserted_rows,
                    inserted_bytes,
                ),
            )
            return self._read_metrics(connection)

    @staticmethod
    def _record_matches(row: sqlite3.Row, record: BleSpoolRecord) -> bool:
        return (
            int(row["sequence"]) == record.sequence
            and str(row["device_id"]) == record.device_id
            and str(row["session_id"]) == record.session_id
            and int(row["session_generation"]) == record.session_generation
            and int(row["notify_sequence"]) == record.notify_sequence
            and str(row["received_at"]) == record.received_at.isoformat()
            and bytes(row["raw"]) == record.raw
        )

    def load_records(self) -> tuple[BleSpoolRecord, ...]:
        """Return durable rows in global callback order."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM notification ORDER BY sequence"
            ).fetchall()
        return tuple(self._record_from_row(row) for row in rows)

    def load_record(self, sequence: int) -> BleSpoolRecord | None:
        """Return one durable row without materializing the entire journal."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM notification WHERE sequence = ?",
                (sequence,),
            ).fetchone()
        return None if row is None else self._record_from_row(row)

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> BleSpoolRecord:
        return BleSpoolRecord(
            sequence=int(row["sequence"]),
            delivery_id=str(row["delivery_id"]),
            device_id=str(row["device_id"]),
            session_id=str(row["session_id"]),
            session_generation=int(row["session_generation"]),
            notify_sequence=int(row["notify_sequence"]),
            received_at=datetime.fromisoformat(str(row["received_at"])),
            raw=bytes(row["raw"]),
            status=BleSpoolStatus(int(row["status"])),
            assembly_key=cast("str | None", row["assembly_key"]),
            chunk_index=cast("int | None", row["chunk_index"]),
            chunk_count=cast("int | None", row["chunk_count"]),
        )

    def delete_sequences(self, sequences: Sequence[int]) -> BleSpoolMetrics:
        """Delete exactly confirmed rows and update counters atomically."""
        if not sequences:
            with self._connect() as connection:
                return self._read_metrics(connection)
        placeholders = ",".join("?" for _ in sequences)
        with self._connect() as connection:
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("BEGIN IMMEDIATE")
            aggregate = connection.execute(
                f"""
                SELECT COUNT(*) AS rows, COALESCE(SUM(length(raw)), 0) AS bytes
                FROM notification WHERE sequence IN ({placeholders})
                """,  # ruff: ignore[hardcoded-sql-expression] -- generated placeholders
                tuple(sequences),
            ).fetchone()
            assert aggregate is not None
            connection.execute(
                f"DELETE FROM notification WHERE sequence IN ({placeholders})",  # ruff: ignore[hardcoded-sql-expression] -- generated placeholders
                tuple(sequences),
            )
            connection.execute(
                """
                UPDATE spool_meta
                SET pending_rows = MAX(0, pending_rows - ?),
                    pending_bytes = MAX(0, pending_bytes - ?)
                WHERE singleton = 1
                """,
                (int(aggregate["rows"]), int(aggregate["bytes"])),
            )
            return self._read_metrics(connection)

    def mark_status(
        self,
        sequence: int,
        status: BleSpoolStatus,
        assembly_key: str | None,
        chunk_index: int | None,
        chunk_count: int | None,
    ) -> bool:
        """Persist one non-confirmed processing disposition."""
        with self._connect() as connection:
            connection.execute("PRAGMA synchronous = FULL")
            cursor = connection.execute(
                """
                UPDATE notification
                SET status = ?, assembly_key = ?, chunk_index = ?, chunk_count = ?
                WHERE sequence = ?
                """,
                (int(status), assembly_key, chunk_index, chunk_count, sequence),
            )
            return bool(cursor.rowcount)

    def checkpoint(self) -> None:
        """Checkpoint committed WAL pages before orderly close."""
        with self._connect() as connection:
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")


__all__ = ["_SqliteBleSpoolBackend"]
