"""Epistemic domain models.

These models keep observations, hypotheses, evidence-backed facts, and unknowns
separate so tentative executor claims cannot silently become canonical truth.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import NewType
from uuid import UUID, uuid4

from epipilot.core.models import (
    EvidenceId,
    HypothesisId,
    Provenance,
    TaskId,
    utc_now,
)

UnknownId = NewType("UnknownId", UUID)
FactId = NewType("FactId", UUID)
ObservationId = NewType("ObservationId", UUID)


def new_hypothesis_id() -> HypothesisId:
    return HypothesisId(uuid4())


def new_unknown_id() -> UnknownId:
    return UnknownId(uuid4())


def new_fact_id() -> FactId:
    return FactId(uuid4())


def new_observation_id() -> ObservationId:
    return ObservationId(uuid4())


class HypothesisStatus(StrEnum):
    PROPOSED = "proposed"
    ACTIVE = "active"
    SUPPORTED = "supported"
    REFUTED = "refuted"
    INCONCLUSIVE = "inconclusive"
    SUPERSEDED = "superseded"


class UnknownImpact(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class UnknownStatus(StrEnum):
    """Lifecycle state for a decision-relevant unknown."""

    OPEN = "open"
    RESOLVED = "resolved"


class ResolutionMode(StrEnum):
    ASK_USER = "ask_user"
    EXPERIMENT = "experiment"
    INVESTIGATION = "investigation"
    SAFE_DEFAULT = "safe_default"


@dataclass(frozen=True, slots=True)
class Observation:
    """Non-authoritative statement observed from an executor or environment."""

    id: ObservationId
    statement: str
    provenance: Provenance

    def __post_init__(self) -> None:
        if not self.statement.strip():
            raise ValueError("observation statement must not be empty")


@dataclass(frozen=True, slots=True)
class Hypothesis:
    """Falsifiable project belief with explicit evidence links and confidence."""

    id: HypothesisId
    statement: str
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    confidence: float = 0.5
    predictions: tuple[str, ...] = ()
    falsification_conditions: tuple[str, ...] = ()
    supporting_evidence: tuple[EvidenceId, ...] = ()
    contradicting_evidence: tuple[EvidenceId, ...] = ()
    superseded_by: HypothesisId | None = None

    def __post_init__(self) -> None:
        if not self.statement.strip():
            raise ValueError("hypothesis statement must not be empty")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("hypothesis confidence must be within [0, 1]")
        if set(self.supporting_evidence) & set(self.contradicting_evidence):
            raise ValueError("the same evidence cannot both support and contradict a hypothesis")
        if self.status is not HypothesisStatus.PROPOSED and (
            not self.predictions or not self.falsification_conditions
        ):
            raise ValueError("active hypotheses require predictions and falsification conditions")
        if self.status is HypothesisStatus.SUPPORTED and not self.supporting_evidence:
            raise ValueError("supported hypothesis requires supporting evidence")
        if self.status is HypothesisStatus.REFUTED and not self.contradicting_evidence:
            raise ValueError("refuted hypothesis requires contradicting evidence")
        if self.status is HypothesisStatus.SUPERSEDED and self.superseded_by is None:
            raise ValueError("superseded hypothesis must reference its replacement")
        if self.status is not HypothesisStatus.SUPERSEDED and self.superseded_by is not None:
            raise ValueError("only a superseded hypothesis may reference a replacement")


@dataclass(frozen=True, slots=True)
class Unknown:
    """Explicit uncertainty that may block tasks or drive information gathering."""

    id: UnknownId
    question: str
    impact: UnknownImpact
    resolution_mode: ResolutionMode
    blocking_tasks: tuple[TaskId, ...] = ()
    value_of_information: float = 1.0
    decision_sensitivity: float = 1.0
    status: UnknownStatus = UnknownStatus.OPEN
    resolution_evidence: tuple[EvidenceId, ...] = ()
    resolution_decisions: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not self.question.strip():
            raise ValueError("unknown question must not be empty")
        for name, value in (
            ("value_of_information", self.value_of_information),
            ("decision_sensitivity", self.decision_sensitivity),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be within [0, 1]")
        if self.status is UnknownStatus.OPEN and (
            self.resolution_evidence or self.resolution_decisions
        ):
            raise ValueError("open unknown cannot already carry resolution basis")
        if self.status is UnknownStatus.RESOLVED and not (
            self.resolution_evidence or self.resolution_decisions
        ):
            raise ValueError("resolved unknown requires evidence or a canonical decision")
        if any(not item.strip() for item in self.resolution_decisions):
            raise ValueError("unknown resolution decision references must not be empty")


@dataclass(frozen=True, slots=True)
class Fact:
    """Evidence-backed canonical fact with explicit temporal validity."""

    id: FactId
    statement: str
    provenance: Provenance
    supporting_evidence: tuple[EvidenceId, ...]
    confidence: float = 1.0
    valid_from: datetime = field(default_factory=utc_now)
    valid_to: datetime | None = None
    superseded_by: FactId | None = None

    def __post_init__(self) -> None:
        if not self.statement.strip():
            raise ValueError("fact statement must not be empty")
        if not self.supporting_evidence:
            raise ValueError("a canonical fact requires supporting evidence")
        if not 0.0 < self.confidence <= 1.0:
            raise ValueError("fact confidence must be within (0, 1]")
        if self.valid_from.tzinfo is None:
            raise ValueError("fact validity timestamps must be timezone-aware")
        if self.valid_to is not None:
            if self.valid_to.tzinfo is None:
                raise ValueError("fact validity timestamps must be timezone-aware")
            if self.valid_to <= self.valid_from:
                raise ValueError("fact valid_to must be later than valid_from")
        if self.superseded_by is not None and self.valid_to is None:
            raise ValueError("superseded fact must close its validity interval")
