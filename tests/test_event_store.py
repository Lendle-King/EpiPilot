from __future__ import annotations

import pytest

from epipilot.core.events import EventType, ProjectEvent, new_event_id
from epipilot.runtime.event_store import (
    DuplicateEvent,
    EventVersionConflict,
    InMemoryEventStore,
)


def _event(aggregate_id: str, payload: bytes = b"{}") -> ProjectEvent:
    return ProjectEvent(
        id=new_event_id(),
        type=EventType.TASK_CREATED,
        aggregate_id=aggregate_id,
        payload=payload,
    )


def test_event_store_is_append_only_and_versioned() -> None:
    store = InMemoryEventStore()
    first = _event("project-1", b"first")
    second = _event("project-1", b"second")

    assert store.append(first, expected_version=0) == 1
    assert store.append(second, expected_version=1) == 2
    assert store.load("project-1") == (first, second)
    assert store.version("project-1") == 2


def test_event_store_rejects_stale_writer() -> None:
    store = InMemoryEventStore()
    store.append(_event("project-1"), expected_version=0)

    with pytest.raises(EventVersionConflict):
        store.append(_event("project-1"), expected_version=0)


def test_event_store_rejects_duplicate_event_id_globally() -> None:
    store = InMemoryEventStore()
    event = _event("project-1")
    store.append(event)

    with pytest.raises(DuplicateEvent):
        store.append(event)
