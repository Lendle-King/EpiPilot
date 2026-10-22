"""Frozen Scout/decision contracts; forecasts and source notes never certify science."""

from __future__ import annotations

from enum import StrEnum
from math import isclose
from typing import Self
from uuid import UUID

from pydantic import AwareDatetime, Field, model_validator

from epipilot.research.frontier import FrontierValue
from epipilot.research.models import Cost, amounts


class BranchKind(StrEnum):
    SUPPORT = "support"
    REFUTE = "refute"
    INCONCLUSIVE = "inconclusive"
    FAILURE = "failure"


class DecisionOption(FrontierValue):
    key: str = Field(min_length=1)
    baseline_utility: float = Field(ge=0, le=1)
    proposal_id: str | None = None


class DecisionBranch(FrontierValue):
    outcome: BranchKind
    probability: float = Field(ge=0, le=1)
    utilities: tuple[float, ...] = Field(min_length=1, max_length=3)


class DecisionEstimate(FrontierValue):
    proposal_id: str = Field(min_length=1)
    predictor: str = Field(min_length=1)
    utility_version: str = Field(min_length=1)
    rationale: str = "Explicit heuristic prior; not calibrated EVSI"
    basis_refs: tuple[str, ...] = ()
    options: tuple[DecisionOption, ...] = Field(min_length=1, max_length=3)
    branches: tuple[DecisionBranch, ...] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def coherent(self) -> Self:
        if len({b.outcome for b in self.branches}) != 4 or len(
            {d.key for d in self.options}
        ) != len(self.options):
            raise ValueError("outcomes and option keys must be unique")
        if tuple(b.outcome for b in self.branches) != tuple(BranchKind):
            raise ValueError("branches must follow support/refute/inconclusive/failure order")
        if not isclose(sum(b.probability for b in self.branches), 1.0, abs_tol=1e-9):
            raise ValueError("probabilities must sum to one")
        for i, option in enumerate(self.options):
            if any(
                len(b.utilities) != len(self.options) or any(not 0 <= u <= 1 for u in b.utilities)
                for b in self.branches
            ):
                raise ValueError("utility dimensions/bounds differ from options")
            if not isclose(
                sum(b.probability * b.utilities[i] for b in self.branches),
                option.baseline_utility,
                abs_tol=1e-8,
            ):
                raise ValueError("conditional utilities must be consistent with prior utility")
        if not any(o.proposal_id is None for o in self.options):
            raise ValueError("include a non-executing stop/defer baseline option")
        return self


class ResearchPolicy(FrontierValue):
    id: str = Field(min_length=1)
    basis_ref: str = Field(min_length=1)
    utility_version: str = Field(default="goal-v1", min_length=1)
    information_weight: float = Field(default=1.0, ge=0)
    risk_weight: float = Field(default=0.2, ge=0)
    cost_scales: tuple[Cost, ...] = Field(min_length=1)
    cost_weights: tuple[Cost, ...] = ()
    scout_cost: tuple[Cost, ...] = Field(min_length=1)
    max_candidates: int = Field(default=5, ge=1, le=5)
    max_queries: int = Field(default=3, ge=0, le=3)
    max_sources: int = Field(default=3, ge=0, le=3)
    probability_min_samples: int = Field(default=20, ge=1)
    probability_prior_strength: float = Field(default=20.0, gt=0)
    external_review_interval: int = Field(default=10, ge=1)

    @model_validator(mode="after")
    def valid_costs(self) -> Self:
        scales, weights, scout = (
            amounts(self.cost_scales),
            amounts(self.cost_weights),
            amounts(self.scout_cost),
        )
        if (
            any(v <= 0 for v in scales.values())
            or not any(scout.values())
            or not set(scout) <= scales.keys()
        ):
            raise ValueError("explicit positive cost scales and Scout reservation required")
        if weights and weights.keys() != scales.keys():
            raise ValueError("weights must cover every cost scale")
        return self


class DecisionScore(FrontierValue):
    proposal_id: str
    value: float
    normalized_cost: float
    risk: float
    score: float
    probabilities: tuple[float, ...]
    raw_cost: tuple[Cost, ...]
    estimated_cost: tuple[Cost, ...]
    enabled_before: tuple[bool, ...]
    enabled_after: tuple[tuple[bool, ...], ...]


class DecisionForecast(FrontierValue):
    id: UUID
    policy_id: str
    based_on_version: int = Field(ge=0)
    git_revision: str = Field(pattern="^[0-9a-f]{40}$")
    at: AwareDatetime
    estimates: tuple[DecisionEstimate, ...] = Field(min_length=1)
    scores: tuple[DecisionScore, ...] = Field(min_length=1)
    selected_proposal: str
    selection_reason: str
    calibration_id: str
    registered_adapters: tuple[str, ...] = ()


class DecisionOutcome(FrontierValue):
    forecast_id: UUID
    proposal_id: str
    evidence_id: UUID
    outcome: BranchKind
    actual_cost: tuple[Cost, ...]
    brier_score: float = Field(ge=0)
    model_implied_gain: float
    observed_gain: None = None
    unlocked_proposal_ids: tuple[str, ...] = ()
    qualification: str = (
        "Model-implied branch gain is not observed VoI; observed benefit unavailable"
    )


class CalibrationSample(FrontierValue):
    predictor: str
    utility_version: str
    group: str
    probabilities: tuple[float, ...]
    outcome: BranchKind
    forecast_id: UUID


class CandidateDisposition(StrEnum):
    ACCEPTED = "accepted"
    DEFERRED = "deferred"
    REJECTED = "rejected"
    DUPLICATE = "duplicate"


class OpportunityCandidate(FrontierValue):
    id: str = Field(min_length=1)
    topic_key: str = Field(min_length=1)
    question: str = Field(min_length=1)
    alternatives: tuple[str, ...] = Field(min_length=2)
    decision_effect: str = Field(min_length=1)
    source_refs: tuple[str, ...] = Field(min_length=1)
    proposal_ids: tuple[str, ...] = ()
    disposition: CandidateDisposition
    reason: str = Field(min_length=1)


class SourceSnapshot(FrontierValue):
    id: str = Field(min_length=1)
    url: str = Field(min_length=1)
    title: str = Field(min_length=1)
    artifact: str = Field(min_length=1)
    sha256: str = Field(pattern="^[0-9a-f]{64}$")
    accessed_at: AwareDatetime
    published_at: AwareDatetime | None = None
    access_level: str = Field(pattern="^(full_text|sections|abstract|lead|failed)$")
    locator: str = Field(min_length=1)
    line: int = Field(ge=1)
    quote: str = Field(min_length=1)


class ScoutRequest(FrontierValue):
    operation_id: str = Field(min_length=1)
    based_on_version: int = Field(ge=0)
    git_revision: str = Field(pattern="^[0-9a-f]{40}$")
    canonical_root: str
    epoch: str
    policy_id: str
    goal: str
    authority_refs: tuple[str, ...]
    question_ids: tuple[str, ...]
    evidence_ids: tuple[str, ...]
    proposal_ids: tuple[str, ...]
    reserved_cost: tuple[Cost, ...]
    remaining_budget: tuple[Cost, ...]
    max_queries: int
    max_sources: int
    max_candidates: int
    external_search_required: bool
    review_count: int = Field(ge=0)
    instructions: tuple[str, ...]


class ScoutResultStatus(StrEnum):
    ACCEPTED = "accepted"
    INVALID = "invalid"
    STALE = "stale"


class ResearchPolicyConfigured(FrontierValue):
    policy: ResearchPolicy


class ScoutStarted(FrontierValue):
    request: ScoutRequest


class ScoutCompleted(FrontierValue):
    submission_hash: str
    external_search_complete: bool = False
    operation_id: str
    request_version: int
    status: ScoutResultStatus
    reason: str
    actual_cost: tuple[Cost, ...]
    candidates: tuple[OpportunityCandidate, ...] = ()
    estimates: tuple[DecisionEstimate, ...] = ()
    sources: tuple[SourceSnapshot, ...] = ()
    queries: tuple[str, ...] = ()
    discovery_id: UUID | None = None


class DecisionForecastRecorded(FrontierValue):
    forecast: DecisionForecast


class DecisionOutcomeRecorded(FrontierValue):
    outcome: DecisionOutcome


class IntelligenceState(FrontierValue):
    policies: tuple[ResearchPolicy, ...] = ()
    starts: tuple[ScoutStarted, ...] = ()
    completions: tuple[ScoutCompleted, ...] = ()
    forecasts: tuple[DecisionForecast, ...] = ()
    outcomes: tuple[DecisionOutcome, ...] = ()

    @property
    def policy(self) -> ResearchPolicy:
        if not self.policies:
            raise ValueError("explicit research policy required")
        return self.policies[-1]

    @property
    def active(self) -> tuple[ScoutStarted, ...]:
        done = {r.operation_id for r in self.completions}
        return tuple(r for r in self.starts if r.request.operation_id not in done)

    @property
    def estimates(self) -> tuple[DecisionEstimate, ...]:
        for result in reversed(self.completions):
            request = next(
                s.request for s in self.starts if s.request.operation_id == result.operation_id
            )
            if result.status is ScoutResultStatus.ACCEPTED and request.policy_id == self.policy.id:
                return result.estimates
        return ()
