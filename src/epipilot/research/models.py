"""Typed, intentionally small research domain contracts.

Research claims are defeasible; even a supported Claim is NOT a canonical Fact.
Executors may submit observations but cannot independently certify their truth.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from epipilot.core.models import utc_now


def research_id(prefix: str) -> str:
    """Generate a short opaque research identifier."""
    return f"{prefix}-{uuid4().hex[:12]}"


class Record(BaseModel):
    """Strict, serializable plugin record."""

    model_config = ConfigDict(extra="forbid")


class ClaimStatus(StrEnum):
    HYPOTHESIS = "hypothesis"
    SUPPORTED = "supported"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"
    SUPERSEDED = "superseded"


class ProposalKind(StrEnum):
    EXPLORE = "explore"
    OPTIMIZE = "optimize"
    VERIFY = "verify"


class ResearchGoal(Record):
    statement: str = Field(min_length=1)
    success_metrics: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    budget: float = Field(gt=0)
    user_approved: bool = True


class Unknown(Record):
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    importance: float = Field(default=0.5, ge=0, le=1)
    decision_sensitivity: float = Field(default=0.5, ge=0, le=1)
    status: str = Field(default="open", pattern=r"^(open|resolved)$")
    resolution_evidence_ids: list[str] = Field(default_factory=list)


class Claim(Record):
    id: str = Field(min_length=1)
    statement: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    status: ClaimStatus = ClaimStatus.HYPOTHESIS
    assumptions: list[str] = Field(default_factory=list)
    supporting_evidence_ids: list[str] = Field(default_factory=list)
    contradicting_evidence_ids: list[str] = Field(default_factory=list)
    superseded_by: str | None = None


class Proposal(Record):
    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    kind: ProposalKind
    rationale: str = Field(min_length=1)
    acceptance: str = Field(min_length=1)
    unknown_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    depends_on: list[str] = Field(default_factory=list)
    information_gain: float = Field(ge=0, le=1)
    expected_improvement: float = Field(ge=0, le=1)
    unlock_value: float = Field(ge=0, le=1)
    novelty: float = Field(ge=0, le=1)
    estimated_cost: float = Field(gt=0)
    risk: float = Field(ge=0, le=1)
    status: str = Field(
        default="proposed", pattern=r"^(proposed|awaiting_verification|completed|blocked)$"
    )

    @model_validator(mode="after")
    def no_self_dependency(self) -> Proposal:
        if self.id in self.depends_on:
            raise ValueError("proposal cannot depend on itself")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("duplicate proposal dependencies")
        return self


class Experiment(Record):
    id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    outcome: str = Field(pattern=r"^(improved|no_gain|inconclusive|infrastructure_failure)$")
    actual_cost: float = Field(ge=0)
    git_revision: str = Field(min_length=1)
    config_ref: str = Field(min_length=1)
    dataset_ref: str = Field(min_length=1)
    seeds: list[int] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    artifact_refs: list[str] = Field(default_factory=list)
    run_command: str = ""
    evidence_id: str = Field(min_length=1)
    created_at: datetime = Field(default_factory=utc_now)


class EvidenceRecord(Record):
    id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    artifact_refs: list[str] = Field(default_factory=list)
    independently_verified: bool = False
    check_argv: list[str] = Field(default_factory=list)
    check_output_sha256: str | None = None
    verified_at: datetime | None = None


class OnboardingSnapshot(Record):
    git_revision: str = Field(min_length=1)
    project_root: str = Field(min_length=1)
    source_counts: dict[str, int] = Field(default_factory=dict)
    relevant_files: list[str] = Field(default_factory=list)
    recorded_at: datetime = Field(default_factory=utc_now)


class ResearchState(Record):
    goal: ResearchGoal | None = None
    unknowns: dict[str, Unknown] = Field(default_factory=dict)
    claims: dict[str, Claim] = Field(default_factory=dict)
    proposals: dict[str, Proposal] = Field(default_factory=dict)
    experiments: dict[str, Experiment] = Field(default_factory=dict)
    evidence: dict[str, EvidenceRecord] = Field(default_factory=dict)
    onboarding: list[OnboardingSnapshot] = Field(default_factory=list)

    @property
    def spent(self) -> float:
        return sum(experiment.actual_cost for experiment in self.experiments.values())

    @property
    def remaining(self) -> float:
        return 0.0 if self.goal is None else max(0.0, self.goal.budget - self.spent)


class ResearchEvent(Record):
    sequence: int = Field(ge=1)
    kind: str = Field(min_length=1)
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=utc_now)
    schema_version: int = Field(default=1, ge=1, le=1)
