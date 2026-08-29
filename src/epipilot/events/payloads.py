"""Typed payload contracts for replayable project events."""

from __future__ import annotations

from datetime import datetime
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from epipilot.core.models import EvidenceKind, TaskStatus
from epipilot.epistemics.models import HypothesisStatus, ResolutionMode, UnknownImpact
from epipilot.planning.graph import PlanBasisKind
from epipilot.requirements.models import DecisionAuthority, RequirementKind


class EventPayload(BaseModel):
    """Base class for strict, immutable event payload schemas."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class RequirementAddedPayload(EventPayload):
    requirement_id: UUID
    kind: RequirementKind
    statement: str = Field(min_length=1)
    provenance_source: str = Field(min_length=1)
    provenance_scope: str = Field(min_length=1)
    provenance_created_at: datetime


class DecisionMadePayload(EventPayload):
    decision_id: UUID
    question: str = Field(min_length=1)
    choice: str = Field(min_length=1)
    authority: DecisionAuthority
    rationale: str = Field(min_length=1)
    basis_refs: tuple[str, ...] = ()
    reversible: bool = True


class UnknownRegisteredPayload(EventPayload):
    unknown_id: UUID
    question: str = Field(min_length=1)
    impact: UnknownImpact
    resolution_mode: ResolutionMode
    blocking_tasks: tuple[UUID, ...] = ()
    value_of_information: float = Field(default=1.0, ge=0.0, le=1.0)
    decision_sensitivity: float = Field(default=1.0, ge=0.0, le=1.0)


class UnknownResolvedPayload(EventPayload):
    unknown_id: UUID
    evidence_ids: tuple[UUID, ...] = ()
    decision_ids: tuple[UUID, ...] = ()

    @model_validator(mode="after")
    def validate_resolution_basis(self) -> Self:
        if not self.evidence_ids and not self.decision_ids:
            raise ValueError("unknown resolution requires evidence or a canonical decision")
        return self


class HypothesisCreatedPayload(EventPayload):
    hypothesis_id: UUID
    statement: str = Field(min_length=1)
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    predictions: tuple[str, ...] = ()
    falsification_conditions: tuple[str, ...] = ()
    supporting_evidence: tuple[UUID, ...] = ()
    contradicting_evidence: tuple[UUID, ...] = ()
    superseded_by: UUID | None = None


class HypothesisUpdatedPayload(EventPayload):
    """Full cumulative evidence projection for one hypothesis transition."""

    hypothesis_id: UUID
    status: HypothesisStatus
    confidence: float = Field(ge=0.0, le=1.0)
    supporting_evidence: tuple[UUID, ...] = ()
    contradicting_evidence: tuple[UUID, ...] = ()
    superseded_by: UUID | None = None


class EvidenceRecordedPayload(EventPayload):
    evidence_id: UUID
    kind: EvidenceKind
    summary: str = Field(min_length=1)
    provenance_source: str = Field(min_length=1)
    provenance_scope: str = Field(min_length=1)
    provenance_created_at: datetime
    independently_verified: bool
    task_id: UUID | None = None


class TaskCreatedPayload(EventPayload):
    task_id: UUID
    objective: str = Field(min_length=1)


class TaskStartedPayload(EventPayload):
    task_id: UUID
    session_id: str = Field(min_length=1)


class TaskStatusChangedPayload(EventPayload):
    task_id: UUID
    status: TaskStatus
    completion_evidence_id: UUID | None = None

    @model_validator(mode="after")
    def validate_completion_evidence(self) -> Self:
        if self.status is TaskStatus.PASSED and self.completion_evidence_id is None:
            raise ValueError("PASSED status requires completion_evidence_id")
        if self.status is not TaskStatus.PASSED and self.completion_evidence_id is not None:
            raise ValueError("completion_evidence_id is valid only for PASSED status")
        return self


class ContextCompiledPayload(EventPayload):
    task_id: UUID
    context_id: str = Field(min_length=1)
    included_item_ids: tuple[str, ...] = ()
    excluded_item_ids: tuple[str, ...] = ()
    token_cost: int = Field(ge=0)
    token_budget: int = Field(gt=0)
    compiler_version: str = Field(min_length=1)


class ExecutorObservationRecordedPayload(EventPayload):
    task_id: UUID
    session_id: str = Field(min_length=1)
    state: str = Field(min_length=1)
    changed_file_count: int = Field(ge=0)
    artifact_refs: tuple[str, ...] = ()


class VerificationPassedPayload(EventPayload):
    task_id: UUID
    evidence_id: UUID


class VerificationFailedPayload(EventPayload):
    task_id: UUID
    reason: str | None = None


class TaskSupersededPayload(EventPayload):
    task_id: UUID
    replacement_task_ids: tuple[UUID, ...] = ()
    basis_refs: tuple[str, ...] = Field(min_length=1)


class PlanDependencyPayload(EventPayload):
    predecessor: UUID
    successor: UUID


class PlanBasisPayload(EventPayload):
    kind: PlanBasisKind
    reference_id: str = Field(min_length=1)


class PlanVersionCreatedPayload(EventPayload):
    version: int = Field(gt=0)
    task_ids: tuple[UUID, ...]
    dependencies: tuple[PlanDependencyPayload, ...] = ()
    basis: tuple[PlanBasisPayload, ...] = Field(min_length=1)
