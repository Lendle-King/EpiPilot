"""Core domain models for EpiPilot.

This module intentionally contains no database, network, subprocess, or model-provider code.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import NewType
from uuid import UUID, uuid4

TaskId = NewType("TaskId", UUID)
EvidenceId = NewType("EvidenceId", UUID)
HypothesisId = NewType("HypothesisId", UUID)
RequirementId = NewType("RequirementId", UUID)


def utc_now() -> datetime:
    """Return a timezone-aware UTC timestamp."""
    return datetime.now(timezone.utc)


def new_task_id() -> TaskId:
    """Create a new opaque task identifier."""
    return TaskId(uuid4())


def new_evidence_id() -> EvidenceId:
    """Create a new opaque evidence identifier."""
    return EvidenceId(uuid4())


class TaskStatus(StrEnum):
    """Lifecycle states for a task."""

    PROPOSED = "proposed"
    READY = "ready"
    RUNNING = "running"
    AGENT_REPORTED_DONE = "agent_reported_done"
    VERIFYING = "verifying"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"
    SUPERSEDED = "superseded"
    CANCELLED = "cancelled"
    INVALIDATED = "invalidated"


class EvidenceKind(StrEnum):
    """Evidence strength classes used by verification policy."""

    EXECUTOR_REPORT = "executor_report"
    STATIC_INSPECTION = "static_inspection"
    SEMANTIC_VERIFICATION = "semantic_verification"
    RUNTIME_MEASUREMENT = "runtime_measurement"
    DETERMINISTIC_CHECK = "deterministic_check"


@dataclass(frozen=True, slots=True)
class Provenance:
    """Where a canonical claim or evidence item came from."""

    source: str
    scope: str
    created_at: datetime = field(default_factory=utc_now)

    def __post_init__(self) -> None:
        if self.created_at.tzinfo is None:
            raise ValueError("provenance timestamps must be timezone-aware")
        if not self.source.strip():
            raise ValueError("provenance source must not be empty")
        if not self.scope.strip():
            raise ValueError("provenance scope must not be empty")


@dataclass(frozen=True, slots=True)
class Evidence:
    """A validated observation that may support project reasoning."""

    id: EvidenceId
    kind: EvidenceKind
    summary: str
    provenance: Provenance
    independently_verified: bool

    def __post_init__(self) -> None:
        if not self.summary.strip():
            raise ValueError("evidence summary must not be empty")


@dataclass(frozen=True, slots=True)
class Task:
    """Canonical task state.

    Mutations are represented by producing a replacement instance through explicit
    transition functions rather than changing fields in place.
    """

    id: TaskId
    objective: str
    status: TaskStatus = TaskStatus.PROPOSED
    linked_evidence: tuple[EvidenceId, ...] = ()

    def __post_init__(self) -> None:
        if not self.objective.strip():
            raise ValueError("task objective must not be empty")
