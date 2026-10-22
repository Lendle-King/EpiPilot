"""Agent-facing metadata referring to canonical entities, never a second state store."""

from __future__ import annotations

import hashlib
import json
from enum import IntEnum, StrEnum
from typing import Self
from uuid import UUID

from pydantic import ConfigDict, Field, model_validator

from epipilot.events.payloads import EventPayload


class FrontierValue(EventPayload):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, allow_inf_nan=False)


class Quality(IntEnum):
    DOCUMENT = 0
    INTEGRITY = 1
    MEASUREMENT = 2
    INDEPENDENT = 3


class Facet(StrEnum):
    PROPERTY = "property"
    VARIABLE = "variable"
    BOUNDARY = "boundary"
    BOTTLENECK = "bottleneck"
    METHOD = "method"


class Purpose(StrEnum):
    EXPLORE = "explore"
    OPTIMIZE = "optimize"
    VERIFY = "verify"


class Phase(StrEnum):
    EXPLORATION = "exploration"
    MIXED = "mixed"
    OPTIMIZATION = "optimization"


class Question(FrontierValue):
    unknown_id: UUID
    topic_key: str = Field(min_length=1)
    facet: Facet
    hypothesis_ids: tuple[UUID, ...] = Field(min_length=1)
    alternatives: tuple[str, ...] = ()
    minimum_quality: Quality = Quality.MEASUREMENT
    evidence_scope: str = Field(
        pattern="^(integrity|development|independent|prospective|synthetic)$"
    )


class InputFile(FrontierValue):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern="^[0-9a-f]{64}$")


class Rule(FrontierValue):
    metric: str
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def nonempty(self) -> Self:
        if self.minimum is None and self.maximum is None:
            raise ValueError("measurement rule requires an explicit bound")
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("inverted measurement bounds")
        return self


class MeasurementContract(FrontierValue):
    proposal_id: str
    purpose: Purpose
    adapter: str = Field(min_length=1)
    git_revision: str = Field(pattern="^[0-9a-f]{40}$")
    inputs: tuple[InputFile, ...] = Field(min_length=1)
    value_column: str = "scale_observed"
    date_column: str = "date"
    boundary_date: str = Field(default="2019-01-01", pattern="^\\d{4}-\\d{2}-\\d{2}$")
    cutoff_date: str | None = None
    rules: tuple[Rule, ...] = Field(min_length=1)
    prospective: bool = False
    data_source: str = ""

    @model_validator(mode="after")
    def validate_prospective(self) -> Self:
        if self.prospective and (
            self.cutoff_date is None or not self.data_source.strip() or not self.rules
        ):
            raise ValueError(
                "prospective contracts require cutoff_date, data_source, and explicit rules"
            )
        return self

    def fingerprint(self) -> str:
        excluded = {"proposal_id", "purpose"}
        if not self.prospective:
            excluded.add("prospective")
        if not self.data_source:
            excluded.add("data_source")
        data = self.model_dump(mode="json", exclude=excluded)
        # File spelling/order is not scientific novelty. Preserve content multiplicity;
        # validation rejects duplicate contents/identities inside a measurement.
        data["inputs"] = sorted(item.sha256 for item in self.inputs)
        return hashlib.sha256(
            json.dumps(data, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()


class DiscoveryAudit(FrontierValue):
    id: UUID
    based_on_version: int = Field(ge=0)
    review_count: int = Field(ge=0)
    summary: str = Field(min_length=1)
    source_evidence_ids: tuple[UUID, ...]


class Resolution(FrontierValue):
    unknown_id: UUID
    evidence_id: UUID
    resolved: bool
    rationale: str = Field(min_length=1)


class ResearchFrontier(FrontierValue):
    repository: str
    exploration_fraction: float = Field(default=0.2, gt=0, lt=1)
    questions: tuple[Question, ...] = ()
    contracts: tuple[MeasurementContract, ...] = ()
    discoveries: tuple[DiscoveryAudit, ...] = ()
    resolutions: tuple[Resolution, ...] = ()

    def closed(self, unknown_id: UUID) -> bool:
        return next(
            (r.resolved for r in reversed(self.resolutions) if r.unknown_id == unknown_id), False
        )

    def contract(self, proposal_id: str) -> MeasurementContract:
        return next(c for c in self.contracts if c.proposal_id == proposal_id)


class FrontierInitialized(EventPayload):
    repository: str
    basis_ref: str
    exploration_fraction: float = Field(default=0.2, gt=0, lt=1)


class FrontierDiscovered(EventPayload):
    audit: DiscoveryAudit
    questions: tuple[Question, ...]
    contracts: tuple[MeasurementContract, ...]


class QuestionResolved(EventPayload):
    resolution: Resolution
