from __future__ import annotations

from pathlib import Path

import pytest

from epipilot.core.events import EventType, ProjectEvent, new_event_id
from epipilot.runtime.event_store import DuplicateEvent, EventVersionConflict
from epipilot.runtime.sqlite_event_store import SqliteEventStore


def _event(aggregate_id: str, payload: bytes = b"{}") -> ProjectEvent:
    return ProjectEvent(
        id=new_event_id(),
        type=EventType.TASK_CREATED,
        aggregate_id=aggregate_id,
        payload=payload,
    )


def test_sqlite_event_store_survives_reopen(tmp_path: Path) -> None:
    path = tmp_path / "epipilot.db"
    first = _event("project-1", b"first")
    second = _event("project-1", b"second")
    store = SqliteEventStore(path)

    assert store.append(first, expected_version=0) == 1
    assert store.append(second, expected_version=1) == 2

    reopened = SqliteEventStore(path)
    assert reopened.load("project-1") == (first, second)
    assert reopened.version("project-1") == 2


def test_sqlite_event_store_rejects_stale_writer(tmp_path: Path) -> None:
    store = SqliteEventStore(tmp_path / "epipilot.db")
    store.append(_event("project-1"), expected_version=0)

    with pytest.raises(EventVersionConflict):
        store.append(_event("project-1"), expected_version=0)


def test_sqlite_event_store_rejects_duplicate_id_across_aggregates(tmp_path: Path) -> None:
    store = SqliteEventStore(tmp_path / "epipilot.db")
    event = _event("project-1")
    store.append(event)
    duplicate = ProjectEvent(
        id=event.id,
        type=event.type,
        aggregate_id="project-2",
        payload=event.payload,
        occurred_at=event.occurred_at,
        schema_version=event.schema_version,
    )

    with pytest.raises(DuplicateEvent):
        store.append(duplicate)
