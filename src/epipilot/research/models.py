"""Immutable research metadata; hypotheses, tasks and evidence stay canonical."""

from __future__ import annotations

from datetime import datetime
from enum import IntEnum, StrEnum
from math import isfinite
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, ConfigDict, Field, model_validator

from epipilot.core.contracts import ResourceClaim
from epipilot.events.payloads import EventPayload
from epipilot.research.frontier import Quality, ResearchFrontier


class Value(EventPayload):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class Fidelity(IntEnum):
    F0 = 0
    F1 = 1
    F2 = 2
    F3 = 3
    F4 = 4


class ExperimentKind(StrEnum):
    DATA_QUALIFICATION = "data_qualification"
    DIAGNOSTIC = "diagnostic"
    FALSIFICATION = "falsification"
    SIMULATION = "simulation"
    PILOT = "pilot"
    ABLATION = "ablation"
    FULL_EVALUATION = "full_evaluation"
    PROSPECTIVE_VALIDATION = "prospective_validation"


class Scope(StrEnum):
    DEVELOPMENT = "development"
    INDEPENDENT = "independent"
    PROSPECTIVE = "prospective"
    INTEGRITY = "integrity"
    SYNTHETIC = "synthetic"


class ExecutionStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    INVALID = "invalid"
    CANCELLED = "cancelled"


class Outcome(StrEnum):
    SUPPORT = "support"
    REFUTE = "refute"
    INCONCLUSIVE = "inconclusive"
    BLOCKED = "blocked"
    INVALID = "invalid"


class Strategy(StrEnum):
    FIFO = "fifo"
    EXISTING = "existing"
    CARTS = "carts"
    NO_INFORMATION = "no_information"
    NO_COST = "no_cost"
    NO_UNLOCK = "no_unlock"


class Cost(Value):
    unit: str = Field(min_length=1)
    amount: float = Field(ge=0)


def amounts(costs: tuple[Cost, ...]) -> dict[str, float]:
    if len({c.unit for c in costs}) != len(costs):
        raise ValueError("duplicate cost unit")
    return {c.unit: c.amount for c in costs}


class Metric(Value):
    name: str
    value: float


class MetricConstraint(Value):
    name: str
    minimum: float | None = None
    maximum: float | None = None

    def accepts(self, metrics: tuple[Metric, ...]) -> bool:
        value = next((m.value for m in metrics if m.name == self.name), None)
        return (
            value is not None
            and (self.minimum is None or value >= self.minimum)
            and (self.maximum is None or value <= self.maximum)
        )


class ResearchGoal(Value):
    statement: str = Field(min_length=1)
    budgets: tuple[Cost, ...] = Field(min_length=1)
    constraints: tuple[MetricConstraint, ...] = ()
    utility_definition: str = "trusted improvement per normalized resource"
    minimum_improvement: float = Field(default=0.0, ge=0)
    required_scope: Scope = Scope.INDEPENDENT
    minimum_samples: int = Field(default=1, ge=1)
    resources: tuple[ResourceClaim, ...] = ()

    @model_validator(mode="after")
    def check_budget(self) -> Self:
        validate_resources(self.resources)
        if any(v <= 0 for v in amounts(self.budgets).values()):
            raise ValueError("budget limits must be positive")
        return self


class ResearchNode(Value):
    """Relations and priors referencing a canonical Hypothesis, never its status."""

    hypothesis_id: UUID
    label: str
    basis: str = Field(min_length=1)
    mechanism: str = "qualification followed by falsifiable experiment"
    source_refs: tuple[str, ...] = ()
    imported_route_state: str = "unknown source observation"
    parent_ids: tuple[UUID, ...] = ()
    and_ids: tuple[UUID, ...] = ()
    or_ids: tuple[UUID, ...] = ()
    mutex_ids: tuple[UUID, ...] = ()
    contribution: float = Field(default=0.5, ge=0, le=1)
    uncertainty: float = Field(default=0.5, ge=0, le=1)


class ExperimentProposal(Value):
    id: str = Field(min_length=1)
    hypothesis_id: UUID
    title: str = Field(min_length=1)
    input_revision: str = Field(min_length=1)
    kind: ExperimentKind = ExperimentKind.DIAGNOSTIC
    fidelity: Fidelity = Fidelity.F0
    scope: Scope = Scope.INTEGRITY
    unknown_ids: tuple[UUID, ...] = ()
    estimated_cost: tuple[Cost, ...] = ()
    cost_uncertainty: float = Field(default=0.25, ge=0, le=5)
    resources: tuple[ResourceClaim, ...] = ()
    available_at: AwareDatetime | None = None
    data_qualified: bool = True
    data_reason: str = ""
    input_refs: tuple[str, ...] = ()
    predecessor_ids: tuple[str, ...] = ()
    minimum_samples: int = Field(default=1, ge=1)
    minimum_reliability: float = Field(default=0.8, ge=0, le=1)
    observation_rule: str = "independently recompute declared check"
    success_rule: str = "all preregistered checks pass"
    falsification_rule: str = "a definitive preregistered counterexample"
    upgrade_rule: str = "verified preceding stage, qualified inputs, new independent scope for F4"
    stop_rule: str = "no unchanged retry"
    verifier: str = "independent adapter"
    definitive_negative: bool = False
    information: float = Field(default=0.5, ge=0, le=1)
    contribution: float = Field(default=0.5, ge=0, le=1)
    uncertainty: float = Field(default=0.5, ge=0, le=1)
    risk: float = Field(default=0.1, ge=0, le=1)
    estimate_source: str = "explicit heuristic prior; uncalibrated"
    estimate_confidence: float = Field(default=0.3, ge=0, le=1)

    @model_validator(mode="after")
    def validate_cost(self) -> Self:
        validate_resources(self.resources)
        amounts(self.estimated_cost)
        return self


class Reservation(Value):
    proposal_id: str
    costs: tuple[Cost, ...]
    score_explanation: str
    at: AwareDatetime


class Observation(Value):
    proposal_id: str
    execution: ExecutionStatus
    metrics: tuple[Metric, ...] = ()
    samples: int = Field(default=0, ge=0)
    actual_cost: tuple[Cost, ...]
    artifact: str
    summary: str
    recorded_at: AwareDatetime

    @model_validator(mode="after")
    def validate_measurement(self) -> Self:
        amounts(self.actual_cost)
        if len({m.name for m in self.metrics}) != len(self.metrics):
            raise ValueError("duplicate metric name")
        return self


class Assessment(Value):
    proposal_id: str
    verified: bool
    outcome: Outcome
    reliability: float = Field(ge=0, le=1)
    scope: Scope
    qualified_for_promotion: bool = False
    quality: Quality = Quality.INTEGRITY
    reasons: tuple[str, ...] = Field(min_length=1)


class Review(Value):
    assessment: Assessment
    evidence_id: UUID
    verification_cost: tuple[Cost, ...] = ()


class Settlement(Value):
    proposal_id: str
    actual_cost: tuple[Cost, ...]


class ResearchState(Value):
    goal: ResearchGoal
    frontier: ResearchFrontier | None = None
    nodes: tuple[ResearchNode, ...] = ()
    proposals: tuple[ExperimentProposal, ...] = ()
    reservations: tuple[Reservation, ...] = ()
    observations: tuple[Observation, ...] = ()
    reviews: tuple[Review, ...] = ()
    settlements: tuple[Settlement, ...] = ()

    def proposal(self, identifier: str) -> ExperimentProposal:
        return next(p for p in self.proposals if p.id == identifier)

    def observation(self, identifier: str) -> Observation | None:
        return next((o for o in self.observations if o.proposal_id == identifier), None)

    def review(self, identifier: str) -> Review | None:
        return next((r for r in self.reviews if r.assessment.proposal_id == identifier), None)

    @property
    def active(self) -> tuple[Reservation, ...]:
        settled = {s.proposal_id for s in self.settlements}
        return tuple(r for r in self.reservations if r.proposal_id not in settled)


class Ranked(Value):
    proposal_id: str
    score: float
    information: float
    contribution: float
    unlocking: float
    exploration: float
    risk: float
    normalized_cost: float
    source: str
    confidence: float
    uncertainty: float
    explanation: str


class Rejected(Value):
    proposal_id: str
    reason: str


class Ranking(Value):
    strategy: Strategy
    at: datetime
    ranked: tuple[Ranked, ...]
    rejected: tuple[Rejected, ...]
    stop_reason: str | None = None


def validate_resources(resources: tuple[ResourceClaim, ...]) -> None:
    if len({(c.kind, c.key) for c in resources}) != len(resources):
        raise ValueError("duplicate resource kind/key")
    if any(not isfinite(c.quantity) or c.quantity <= 0 for c in resources):
        raise ValueError("resource quantities must be finite and positive")


class ResearchDefinition(Value):
    goal: ResearchGoal
    nodes: tuple[ResearchNode, ...]
    proposals: tuple[ExperimentProposal, ...]


class CostVector(Value):
    costs: tuple[Cost, ...]
