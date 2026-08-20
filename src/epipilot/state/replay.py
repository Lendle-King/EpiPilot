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
    state = ProjectState(project_id=project_id)
    seen: set[EventId] = set()
    for event in events:
        if event.id in seen:
            raise DuplicateAppliedEvent(f"event {event.id} appears more than once in the stream")
        seen.add(event.id)
        state = reduce_event(state, event)
    return state
