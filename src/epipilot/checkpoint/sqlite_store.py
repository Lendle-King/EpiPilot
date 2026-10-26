"""SQLite-backed durable checkpoint snapshots."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from epipilot.checkpoint.errors import CheckpointStoreError
from epipilot.checkpoint.models import Checkpoint

_SCHEMA = """
CREATE TABLE IF NOT EXISTS project_checkpoints (
    project_id TEXT NOT NULL,
    last_event_version INTEGER NOT NULL,
    serialized_project_state BLOB NOT NULL,
    schema_version INTEGER NOT NULL,
    checksum TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (project_id, last_event_version)
)
"""


@dataclass(slots=True)
class SqliteCheckpointStore:
    """Durable local checkpoint store; event history remains authoritative."""

    path: Path

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(_SCHEMA)

    def save(self, checkpoint: Checkpoint) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO project_checkpoints (
                    project_id,
                    last_event_version,
                    serialized_project_state,
                    schema_version,
                    checksum,
                    created_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.project_id,
                    checkpoint.last_event_version,
                    checkpoint.serialized_project_state,
                    checkpoint.schema_version,
                    checkpoint.checksum,
                    checkpoint.created_at.isoformat(),
                ),
            )

    def latest(self, project_id: str) -> Checkpoint | None:
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT
                    project_id,
                    last_event_version,
                    serialized_project_state,
                    schema_version,
                    checksum,
                    created_at
                FROM project_checkpoints
                WHERE project_id = ?
                ORDER BY last_event_version DESC
                LIMIT 1
                """,
                (project_id,),
            ).fetchone()

        if row is None:
            return None

        try:
            return Checkpoint(
                project_id=str(row[0]),
                last_event_version=int(row[1]),
                serialized_project_state=bytes(row[2]),
                schema_version=int(row[3]),
                checksum=str(row[4]),
                created_at=datetime.fromisoformat(str(row[5])),
            )
        except (TypeError, ValueError) as exc:
            raise CheckpointStoreError("stored checkpoint envelope is malformed") from exc

    def discard_latest(self, project_id: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                DELETE FROM project_checkpoints
                WHERE project_id = ?
                  AND last_event_version = (
                      SELECT MAX(last_event_version)
                      FROM project_checkpoints
                      WHERE project_id = ?
                  )
                """,
                (project_id, project_id),
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)
