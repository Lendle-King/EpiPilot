"""Frozen, bounded Scout-style decision tables independent of a model provider.

A decision table estimates future *decision usefulness*, not verified science.
Its calibration requires validated EpiPilot v2 measurements and frozen priors.
"""

from __future__ import annotations

from enum import StrEnum
from math import isclose
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator


class DecisionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")


class OutcomeKind(StrEnum):
    IMPROVED = "improved"
    NO_GAIN = "no_gain"
    INCONCLUSIVE = "inconclusive"
    INFRASTRUCTURE_FAILURE = "infrastructure_failure"


class DecisionOption(DecisionRecord):
    key: str = Field(min_length=1)
    prior_utility: float = Field(ge=0, le=1, allow_inf_nan=False)
    proposal_id: str | None = None


class DecisionBranch(DecisionRecord):
    outcome: OutcomeKind
    probability: float = Field(ge=0, le=1, allow_inf_nan=False)
    utilities: tuple[float, ...] = Field(min_length=1, max_length=4)

    @model_validator(mode="after")
    def valid_utilities(self) -> Self:
        if any(not 0 <= value <= 1 for value in self.utilities):
            raise ValueError("conditional utilities must lie in [0,1]")
        return self


class DecisionEstimate(DecisionRecord):
    proposal_id: str = Field(min_length=1)
    predictor: str = Field(min_length=1)
    utility_version: str = Field(min_length=1)
    group: str = Field(min_length=1)
    rationale: str = Field(min_length=1)
    options: tuple[DecisionOption, ...] = Field(min_length=1, max_length=4)
    branches: tuple[DecisionBranch, ...] = Field(min_length=4, max_length=4)

    @model_validator(mode="after")
    def validate_joint_table(self) -> Self:
        if tuple(branch.outcome for branch in self.branches) != tuple(OutcomeKind):
            raise ValueError("decision outcomes must follow canonical enum order")
        if len({option.key for option in self.options}) != len(self.options):
            raise ValueError("option keys must be unique")
        if not any(option.proposal_id is None for option in self.options):
            raise ValueError("a non-executing stop/defer option is mandatory")
        if not isclose(sum(branch.probability for branch in self.branches), 1.0, abs_tol=1e-9):
            raise ValueError("decision probabilities must sum to one")
        for i, option in enumerate(self.options):
            if any(len(branch.utilities) != len(self.options) for branch in self.branches):
                raise ValueError("decision utility vector length disagrees with option count")
            expected = sum(branch.probability * branch.utilities[i] for branch in self.branches)
            if not isclose(expected, option.prior_utility, abs_tol=1e-8):
                raise ValueError("prior utilities must match the frozen joint outcome table")
        return self


class DecisionScore(DecisionRecord):
    proposal_id: str
    value_of_information: float
    normalized_cost: float
    risk_penalty: float
    net_value: float
    probabilities: tuple[float, ...]
    calibration_samples: int
    enabled_before: tuple[bool, ...]
    enabled_after: tuple[tuple[bool, ...], ...]
    qualification: str = (
        "Conditional-utility heuristic only; not observed causal value or scientific evidence"
    )


class DecisionForecast(DecisionRecord):
    contract_id: str
    git_revision: str
    recorded_at: AwareDatetime
    estimate: DecisionEstimate
    score: DecisionScore


class DecisionOutcome(DecisionRecord):
    contract_id: str
    evidence_id: str
    outcome: OutcomeKind
    actual_cost: float = Field(ge=0, allow_inf_nan=False)
    brier_score: float = Field(ge=0, allow_inf_nan=False)
    qualified: bool
    observed_value_of_information: None = None
    qualification: str = (
        "Forecast score is model-implied; observed causal value-of-information unavailable"
    )
