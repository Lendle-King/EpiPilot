"""Read-only SQLite bridge to the existing canonical replay kernel."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import UUID

from epipilot.web.models import EventSummary

if TYPE_CHECKING:
    from epipilot.state.project import ProjectState


def load_stream(
    path: Path, project_id: str, *, max_events: int = 20_000,
) -> tuple[ProjectState, tuple[EventSummary, ...]]:
    if not path.is_file():
        raise ValueError("event database does not exist")
    if not project_id.strip() or max_events < 1:
        raise ValueError("explicit project id and positive event limit required")
    uri = path.resolve().as_uri() + "?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=2)) as connection:
        connection.execute("PRAGMA query_only=ON")
        rows = connection.execute(
            "SELECT version, event_id, event_type, payload, occurred_at, schema_version "
            "FROM project_events WHERE aggregate_id = ? ORDER BY version LIMIT ?",
            (project_id, max_events + 1),
        ).fetchall()
    if not rows:
        raise ValueError("project not found in event database")
    if len(rows) > max_events:
        raise ValueError("event stream exceeds workbench replay limit")
    if [row[0] for row in rows] != list(range(1, len(rows) + 1)):
        raise ValueError("event stream contains a version gap")
    if sum(len(row[3]) for row in rows) > 32 * 1024 * 1024:
        raise ValueError("event payload budget exceeded")

    from epipilot.core.events import EventId, EventType, ProjectEvent
    from epipilot.state.replay import replay_project

    events = tuple(ProjectEvent(
        id=EventId(UUID(row[1])), type=EventType(row[2]), aggregate_id=project_id,
        payload=bytes(row[3]), occurred_at=datetime.fromisoformat(row[4]), schema_version=row[5],
    ) for row in rows)
    state = replay_project(project_id, events)
    summaries = tuple(EventSummary(
        id=str(event.id), version=index, type=event.type.value,
        occurred_at=event.occurred_at.isoformat(),
    ) for index, event in enumerate(events, 1))
    return state, summaries[-50:]
