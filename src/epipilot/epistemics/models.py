"""Epistemic domain models.

These models keep observations, hypotheses, evidence-backed facts, and unknowns
separate so tentative executor claims cannot silently become canonical truth.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import NewType
from uuid import UUID, uuid4

from epipilot.core.models import EvidenceId, HypothesisId, Provenance

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


@dataclass(frozen=True, slots=True)
class Observation:
    id: ObservationId
    statement: str
    provenance: Provenance


@dataclass(frozen=True, slots=True)
class Hypothesis:
    id: HypothesisId
    statement: str
    status: HypothesisStatus = HypothesisStatus.PROPOSED
    supporting_evidence: tuple[EvidenceId, ...] = ()
    contradicting_evidence: tuple[EvidenceId, ...] = ()


@dataclass(frozen=True, slots=True)
class Unknown:
    id: UnknownId
    question: str
    impact: str
    blocking_tasks: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Fact:
    id: FactId
    statement: str
    provenance: Provenance
    supporting_evidence: tuple[EvidenceId, ...]
    superseded_by: FactId | None = None

    def __post_init__(self) -> None:
        if not self.supporting_evidence:
            raise ValueError("a canonical fact requires supporting evidence")
