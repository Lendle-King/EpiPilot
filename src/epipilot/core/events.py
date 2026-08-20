"""Typed append-only events for reconstructing EpiPilot state."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import NewType
from uuid import UUID, uuid4

from epipilot.core.models import utc_now

EventId = NewType("EventId", UUID)


def new_event_id() -> EventId:
    return EventId(uuid4())


class EventType(StrEnum):
    REQUIREMENT_ADDED = "requirement_added"
    DECISION_MADE = "decision_made"
    UNKNOWN_REGISTERED = "unknown_registered"
    HYPOTHESIS_CREATED = "hypothesis_created"
    EVIDENCE_RECORDED = "evidence_recorded"
    TASK_CREATED = "task_created"
    TASK_STARTED = "task_started"
    EXECUTOR_OBSERVATION_RECORDED = "executor_observation_recorded"
    VERIFICATION_PASSED = "verification_passed"
    VERIFICATION_FAILED = "verification_failed"
    TASK_SUPERSEDED = "task_superseded"
    PLAN_VERSION_CREATED = "plan_version_created"


@dataclass(frozen=True, slots=True)
class ProjectEvent:
    """Canonical event envelope.

    `payload` is intentionally bytes at the core boundary. Schema-aware codecs live
    outside the domain layer and must version their payloads explicitly.
    """

    id: EventId
    type: EventType
    aggregate_id: str
    payload: bytes
    occurred_at: datetime = field(default_factory=utc_now)
    schema_version: int = 1

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is None:
            raise ValueError("event timestamps must be timezone-aware")
        if self.schema_version < 1:
            raise ValueError("event schema version must be positive")
        if not self.aggregate_id.strip():
            raise ValueError("aggregate_id must not be empty")
