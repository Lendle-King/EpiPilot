"""Typed research contracts. Experiment pre-registration is immutable after creation.

Execution success, measurement validity, and a provisional research verdict are
different claims. An agent-authored observation alone cannot establish any of them.
"""

from __future__ import annotations

import math
from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from epipilot.core.models import utc_now


def research_id(prefix: str) -> str:
    """Generate an opaque research identifier."""
    return f"{prefix}-{uuid4().hex[:12]}"


class Record(BaseModel):
    """Strict serializable record, shared by the public research CLI."""

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


class MetricDirection(StrEnum):
    AT_LEAST = "at_least"
    AT_MOST = "at_most"


class ContractStatus(StrEnum):
    PREREGISTERED = "preregistered"
    RUNNING = "running"
    AWAITING_VERIFICATION = "awaiting_verification"
    BLOCKED = "blocked"
    COMPLETED = "completed"
    ABORTED = "aborted"


class ResearchGoal(Record):
    statement: str = Field(min_length=1)
    success_metrics: list[str] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    budget: float = Field(gt=0, allow_inf_nan=False)
    user_approved: bool = True


class Unknown(Record):
    id: str = Field(min_length=1)
    question: str = Field(min_length=1)
    importance: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    decision_sensitivity: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
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
    information_gain: float = Field(ge=0, le=1, allow_inf_nan=False)
    expected_improvement: float = Field(ge=0, le=1, allow_inf_nan=False)
    unlock_value: float = Field(ge=0, le=1, allow_inf_nan=False)
    novelty: float = Field(ge=0, le=1, allow_inf_nan=False)
    estimated_cost: float = Field(gt=0, allow_inf_nan=False)
    risk: float = Field(ge=0, le=1, allow_inf_nan=False)
    status: str = Field(
        default="proposed",
        pattern=r"^(proposed|preregistered|running|awaiting_verification|completed|blocked|aborted)$",
    )

    @model_validator(mode="after")
    def no_self_dependency(self) -> Proposal:
        if self.id in self.depends_on:
            raise ValueError("proposal cannot depend on itself")
        if len(self.depends_on) != len(set(self.depends_on)):
            raise ValueError("duplicate proposal dependencies")
        return self


class MetricRule(Record):
    """A frozen, machine-evaluable threshold; NOT a causal-validity claim."""

    name: str = Field(min_length=1)
    direction: MetricDirection
    threshold: float = Field(allow_inf_nan=False)

    def passed(self, value: float) -> bool:
        return (
            value >= self.threshold
            if self.direction is MetricDirection.AT_LEAST
            else value <= self.threshold
        )


class ExperimentContract(Record):
    """Frozen before execution; a changed protocol must create a new contract."""

    id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    git_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    config_ref: str = Field(min_length=1)
    dataset_ref: str = Field(min_length=1)
    config_sha256: str | None = None
    dataset_sha256: str | None = None
    baseline_ref: str = Field(min_length=1)
    controls: list[str] = Field(min_length=1)
    decision_rule: str = Field(min_length=1)
    falsification_rule: str = Field(min_length=1)
    artifact_ref: str = Field(min_length=1)
    metric_rules: list[MetricRule] = Field(min_length=1)
    verifier_argv: list[str] = Field(min_length=1)
    max_cost: float = Field(gt=0, allow_inf_nan=False)
    status: ContractStatus = ContractStatus.PREREGISTERED
    aborted_cost: float = Field(default=0, ge=0, allow_inf_nan=False)
    retry_reasons: list[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("artifact_ref")
    @classmethod
    def safe_artifact_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            value.startswith("/")
            or "\\" in value
            or ":" in value
            or ".." in path.parts
            or str(path) in {"", "."}
            or path.suffix.lower() != ".json"
        ):
            raise ValueError("artifact_ref must be a safe repository-relative .json path")
        return value

    @model_validator(mode="after")
    def validate_contract(self) -> ExperimentContract:
        names = [rule.name for rule in self.metric_rules]
        if len(names) != len(set(names)):
            raise ValueError("metric rules must use distinct names")
        if not all(arg.strip() for arg in self.verifier_argv):
            raise ValueError("verifier arguments cannot be blank")
        if not all(item.strip() for item in self.controls):
            raise ValueError("controls cannot be blank")
        return self


class Experiment(Record):
    id: str = Field(min_length=1)
    proposal_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    outcome: str = Field(pattern=r"^(improved|no_gain|inconclusive|infrastructure_failure)$")
    actual_cost: float = Field(ge=0, allow_inf_nan=False)
    git_revision: str = Field(min_length=1)
    config_ref: str = Field(min_length=1)
    dataset_ref: str = Field(min_length=1)
    seeds: list[int] = Field(default_factory=list)
    metrics: dict[str, float] = Field(default_factory=dict)
    artifact_refs: list[str] = Field(default_factory=list)
    run_command: str = ""
    evidence_id: str = Field(min_length=1)
    contract_id: str | None = None
    created_at: datetime = Field(default_factory=utc_now)

    @field_validator("metrics")
    @classmethod
    def finite_metrics(cls, value: dict[str, float]) -> dict[str, float]:
        if not all(math.isfinite(item) for item in value.values()):
            raise ValueError("experiment metric values must be finite")
        return value


class EvidenceRecord(Record):
    id: str = Field(min_length=1)
    experiment_id: str = Field(min_length=1)
    summary: str = Field(min_length=1)
    artifact_refs: list[str] = Field(default_factory=list)
    independently_verified: bool = False
    measurement_validated: bool = False
    scientific_verdict: str | None = Field(
        default=None, pattern=r"^(supported|refuted|inconclusive)$"
    )
    validated_metrics: dict[str, float] = Field(default_factory=dict)
    artifact_sha256: str | None = None
    check_argv: list[str] = Field(default_factory=list)
    check_output_sha256: str | None = None
    failure_count: int = Field(default=0, ge=0)
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
    contracts: dict[str, ExperimentContract] = Field(default_factory=dict)
    experiments: dict[str, Experiment] = Field(default_factory=dict)
    evidence: dict[str, EvidenceRecord] = Field(default_factory=dict)
    onboarding: list[OnboardingSnapshot] = Field(default_factory=list)

    @property
    def spent(self) -> float:
        return sum(item.actual_cost for item in self.experiments.values()) + sum(
            item.aborted_cost for item in self.contracts.values()
        )

    @property
    def reserved(self) -> float:
        return sum(
            item.max_cost for item in self.contracts.values()
            if item.status is ContractStatus.RUNNING
        )

    @property
    def remaining(self) -> float:
        return (
            0.0 if self.goal is None
            else max(0.0, self.goal.budget - self.spent - self.reserved)
        )

    @property
    def overrun(self) -> float:
        return 0.0 if self.goal is None else max(0.0, self.spent - self.goal.budget)


class ResearchEvent(Record):
    sequence: int = Field(ge=1)
    kind: str = Field(min_length=1)
    payload: dict[str, Any]
    created_at: datetime = Field(default_factory=utc_now)
    schema_version: int = Field(default=2, ge=1, le=2)
