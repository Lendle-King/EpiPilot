"""Append-only event-store contracts and a deterministic in-memory implementation."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol

from epipilot.core.events import EventId, ProjectEvent


class EventStoreError(RuntimeError):
    """Base error for event-store contract violations."""


class EventVersionConflict(EventStoreError):
    """Raised when optimistic concurrency detects a stale aggregate version."""


class DuplicateEvent(EventStoreError):
    """Raised when an event identifier is appended more than once."""


class EventStore(Protocol):
    """Minimal append-only persistence boundary used by the runtime."""

    def append(self, event: ProjectEvent, *, expected_version: int | None = None) -> int:
        """Append one event and return the resulting aggregate version."""
        ...

    def load(self, aggregate_id: str) -> tuple[ProjectEvent, ...]:
        """Return an immutable snapshot of an aggregate event stream."""
        ...

    def version(self, aggregate_id: str) -> int:
        """Return the number of committed events for an aggregate."""
        ...


@dataclass(slots=True)
class InMemoryEventStore:
    """Reference event store for tests and single-process development.

    Production persistence can implement :class:`EventStore` with PostgreSQL while
    preserving the same optimistic-concurrency and append-only semantics.
    """

    _streams: dict[str, list[ProjectEvent]] = field(default_factory=dict)
    _seen_event_ids: set[EventId] = field(default_factory=set)

    def append(self, event: ProjectEvent, *, expected_version: int | None = None) -> int:
        stream = self._streams.setdefault(event.aggregate_id, [])
        current_version = len(stream)

        if expected_version is not None and expected_version != current_version:
            raise EventVersionConflict(
                f"expected aggregate version {expected_version}, found {current_version}"
            )
        if event.id in self._seen_event_ids:
            raise DuplicateEvent(f"event {event.id} has already been committed")

        stream.append(event)
        self._seen_event_ids.add(event.id)
        return len(stream)

    def load(self, aggregate_id: str) -> tuple[ProjectEvent, ...]:
        return tuple(self._streams.get(aggregate_id, ()))

    def version(self, aggregate_id: str) -> int:
        return len(self._streams.get(aggregate_id, ()))
