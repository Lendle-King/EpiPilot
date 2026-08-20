"""SQLite-backed append-only event store for local V0 persistence."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from uuid import UUID

from epipilot.core.events import EventId, EventType, ProjectEvent
from epipilot.runtime.event_store import DuplicateEvent, EventVersionConflict

_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_events (
    aggregate_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    event_id TEXT NOT NULL UNIQUE,
    event_type TEXT NOT NULL,
    payload BLOB NOT NULL,
    occurred_at TEXT NOT NULL,
    schema_version INTEGER NOT NULL,
    PRIMARY KEY (aggregate_id, version)
)
"""


@dataclass(slots=True)
class SqliteEventStore:
    """Durable local event store preserving the core append-only contract."""

    path: Path

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(_SCHEMA)

    def append(self, event: ProjectEvent, *, expected_version: int | None = None) -> int:
        with self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            duplicate = connection.execute(
                "SELECT 1 FROM project_events WHERE event_id = ?",
                (str(event.id),),
            ).fetchone()
            if duplicate is not None:
                raise DuplicateEvent(f"event {event.id} has already been committed")

            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM project_events WHERE aggregate_id = ?",
                (event.aggregate_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError("SQLite failed to return aggregate version")
            current_version = int(row[0])

            if expected_version is not None and expected_version != current_version:
                raise EventVersionConflict(
                    f"expected aggregate version {expected_version}, found {current_version}"
                )

            next_version = current_version + 1
            connection.execute(
                """
                INSERT INTO project_events (
                    aggregate_id,
                    version,
                    event_id,
                    event_type,
                    payload,
                    occurred_at,
                    schema_version
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    event.aggregate_id,
                    next_version,
                    str(event.id),
                    event.type.value,
                    event.payload,
                    event.occurred_at.isoformat(),
                    event.schema_version,
                ),
            )
            return next_version

    def load(self, aggregate_id: str) -> tuple[ProjectEvent, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                """
                SELECT event_id, event_type, payload, occurred_at, schema_version
                FROM project_events
                WHERE aggregate_id = ?
                ORDER BY version ASC
                """,
                (aggregate_id,),
            ).fetchall()

        return tuple(
            ProjectEvent(
                id=EventId(UUID(str(row[0]))),
                type=EventType(str(row[1])),
                aggregate_id=aggregate_id,
                payload=bytes(row[2]),
                occurred_at=datetime.fromisoformat(str(row[3])),
                schema_version=int(row[4]),
            )
            for row in rows
        )

    def version(self, aggregate_id: str) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT COALESCE(MAX(version), 0) FROM project_events WHERE aggregate_id = ?",
                (aggregate_id,),
            ).fetchone()
        if row is None:
            raise RuntimeError("SQLite failed to return aggregate version")
        return int(row[0])

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)
