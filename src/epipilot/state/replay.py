"""Deterministic reconstruction of canonical project state from ordered events."""

from __future__ import annotations

from epipilot.core.events import EventId, ProjectEvent
from epipilot.state.errors import DuplicateAppliedEvent
from epipilot.state.project import ProjectState
from epipilot.state.reducer import reduce_event


def replay_project(
    project_id: str,
    events: tuple[ProjectEvent, ...],
) -> ProjectState:
    """Rebuild a project aggregate solely from its append-only event stream."""
    return replay_from_state(ProjectState(project_id=project_id), events)


def replay_from_state(
    state: ProjectState,
    events: tuple[ProjectEvent, ...],
) -> ProjectState:
    """Apply an ordered tail to a validated state snapshot.

    Checkpoint recovery uses this only after the checkpoint envelope, checksum,
    project identity, and event version have been validated.
    """
    current = state
    seen: set[EventId] = set()
    for event in events:
        if event.id in seen:
            raise DuplicateAppliedEvent(
                f"event {event.id} appears more than once in the replay tail"
            )
        seen.add(event.id)
        current = reduce_event(current, event)
    return current
