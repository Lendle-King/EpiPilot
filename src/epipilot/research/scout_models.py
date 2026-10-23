"""Bounded, source-anchored research scouting contracts.

Scout suggestions are *not* verified measurements. Explicit review may register
new hypotheses, but only v2 pre-registered experiments can support them.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from epipilot.core.models import utc_now


class ScoutRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ScoutStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ScoutCandidateStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class ScoutSource(ScoutRecord):
    """One exact line in a committed Git blob, never mutable working-tree text."""

    path: str = Field(min_length=3, max_length=240)
    line: int = Field(ge=1)
    exact_quote: str = Field(min_length=4, max_length=800)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")

    @field_validator("path")
    @classmethod
    def safe_relative_path(cls, value: str) -> str:
        path = PurePosixPath(value)
        if (
            path.is_absolute()
            or "\\" in value
            or ":" in value
            or ".." in path.parts
            or path.suffix.lower() not in {".md", ".txt", ".json", ".yaml", ".yml", ".csv"}
            or any(part.startswith(".") for part in path.parts)
            or any(part.lower() in {"secrets", "credentials", "private", "tokens"} for part in path.parts)
        ):
            raise ValueError("Scout source must be a safe nonprivate research-document path")
        return value


class ScoutCandidate(ScoutRecord):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    operation_id: str = Field(min_length=1)
    question: str = Field(min_length=8, max_length=500)
    hypothesis: str = Field(min_length=8, max_length=500)
    scope: str = Field(min_length=3, max_length=250)
    title: str = Field(min_length=6, max_length=160)
    rationale: str = Field(min_length=10, max_length=1000)
    acceptance: str = Field(min_length=8, max_length=600)
    source: ScoutSource
    kind: Literal["explore", "optimize", "verify"] = "explore"
    information_gain: float = Field(ge=0, le=1, allow_inf_nan=False)
    expected_improvement: float = Field(ge=0, le=1, allow_inf_nan=False)
    estimated_cost: float = Field(gt=0, allow_inf_nan=False)
    risk: float = Field(default=0, ge=0, le=1, allow_inf_nan=False)
    importance: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    decision_sensitivity: float = Field(default=0.5, ge=0, le=1, allow_inf_nan=False)
    status: ScoutCandidateStatus = ScoutCandidateStatus.PENDING
    review_rationale: str | None = None


class ScoutOperation(ScoutRecord):
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    git_revision: str = Field(pattern=r"^[a-f0-9]{40}$")
    epoch: str = Field(pattern=r"^[a-f0-9]{64}$")
    based_on_version: int = Field(ge=0)
    reserved_cost: float = Field(gt=0, allow_inf_nan=False)
    actual_cost: float = Field(default=0, ge=0, allow_inf_nan=False)
    max_candidates: int = Field(default=5, ge=1, le=5)
    max_sources: int = Field(default=3, ge=1, le=3)
    status: ScoutStatus = ScoutStatus.RUNNING
    candidate_ids: list[str] = Field(default_factory=list)
    summary: str = ""
    created_at: datetime = Field(default_factory=utc_now)


class ScoutSubmission(ScoutRecord):
    operation_id: str = Field(min_length=1)
    actual_cost: float = Field(ge=0, allow_inf_nan=False)
    status: Literal["completed", "failed"]
    summary: str = Field(min_length=8, max_length=1500)
    candidates: list[ScoutCandidate] = Field(default_factory=list, max_length=5)

    @model_validator(mode="after")
    def no_unreviewed_claim_promotion(self) -> ScoutSubmission:
        if self.status == "failed" and self.candidates:
            raise ValueError("failed Scout runs cannot publish candidates")
        if any(
            candidate.operation_id != self.operation_id
            or candidate.status is not ScoutCandidateStatus.PENDING
            for candidate in self.candidates
        ):
            raise ValueError("Scout candidates must be pending and belong to the operation")
        return self
