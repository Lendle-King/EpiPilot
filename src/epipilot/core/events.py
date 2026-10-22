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
    RESEARCH_POLICY_CONFIGURED = "research_policy_configured"
    SCOUT_STARTED = "scout_started"
    SCOUT_COMPLETED = "scout_completed"
    DECISION_FORECAST_RECORDED = "decision_forecast_recorded"
    DECISION_OUTCOME_RECORDED = "decision_outcome_recorded"
    MANAGED_WORK = "managed_work"
    RESEARCH_FRONTIER_INITIALIZED = "research_frontier_initialized"
    RESEARCH_FRONTIER_DISCOVERED = "research_frontier_discovered"
    RESEARCH_QUESTION_RESOLVED = "research_question_resolved"

    RESEARCH_NODE_ADDED = "research_node_added"
    RESEARCH_CONFIGURED = "research_configured"
    RESEARCH_GOAL_REVISED = "research_goal_revised"
    RESEARCH_BUDGET_EXTENDED = "research_budget_extended"
    PROPOSAL_ADDED = "proposal_added"
    EXPERIMENT_RESERVED = "experiment_reserved"
    EXPERIMENT_OBSERVED = "experiment_observed"
    EXPERIMENT_REVIEWED = "experiment_reviewed"
    BUDGET_SETTLED = "budget_settled"

    REQUIREMENT_ADDED = "requirement_added"
    DECISION_MADE = "decision_made"
    UNKNOWN_REGISTERED = "unknown_registered"
    HYPOTHESIS_CREATED = "hypothesis_created"
    EVIDENCE_RECORDED = "evidence_recorded"
    TASK_CREATED = "task_created"
    TASK_STARTED = "task_started"
    TASK_STATUS_CHANGED = "task_status_changed"
    CONTEXT_COMPILED = "context_compiled"
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
