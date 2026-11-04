"""Frozen, provider-neutral contracts for the Benchmark-First research track.

No model calls or external framework execution occur in this package. The
contracts describe comparability; they do not attest evaluator isolation.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

SHA40 = r"^[a-f0-9]{40}$"
SHA256 = r"^[a-f0-9]{64}$"


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelSpec(Record):
    provider: str = Field(min_length=1)
    model_id: str = Field(min_length=1)
    reasoning_effort: str = Field(min_length=1)
    transport: str = Field(min_length=1)


class AdapterSpec(Record):
    id: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,48}$")
    family: str = Field(min_length=1)
    source_url: str = Field(pattern=r"^https://github.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
    checkout: str = Field(pattern=r"^[a-zA-Z0-9_./-]+$")
    source_sha: str = Field(pattern=SHA40)
    license_note: str = Field(min_length=1)
    model: ModelSpec | None = None
    adapter_qualified: bool = False

    @model_validator(mode="after")
    def safe_checkout(self) -> Self:
        path = Path(self.checkout)
        if path.is_absolute() or ".." in path.parts or self.checkout.startswith("."):
            raise ValueError("adapter checkout must be inside the experiment workspace")
        return self


class TaskSpec(Record):
    id: str = Field(pattern=r"^[a-zA-Z0-9_-]{2,64}$")
    family: str = Field(min_length=1)
    dataset_sha256: str = Field(pattern=SHA256)
    evaluator_sha256: str = Field(pattern=SHA256)
    direction: Literal["maximize", "minimize"]
    metric_name: str = Field(min_length=1)
    baseline: float = Field(allow_inf_nan=False)
    reference: float = Field(allow_inf_nan=False)
    baseline_provenance: str = Field(min_length=10)
    task_source_sha: str = Field(pattern=SHA40)

    @model_validator(mode="after")
    def nonzero_reference_gap(self) -> Self:
        gap = self.reference - self.baseline
        if abs(gap) < 1e-9:
            raise ValueError("baseline/reference performance gap is zero")
        if (self.direction == "maximize" and gap < 0) or (self.direction == "minimize" and gap > 0):
            raise ValueError("reference must improve upon the baseline")
        return self


class Budget(Record):
    max_model_attempts: int = Field(default=0, ge=0, le=200)
    max_dev_experiments: int = Field(default=0, ge=0, le=100)
    max_wall_seconds: float = Field(default=600, gt=0, le=86400, allow_inf_nan=False)


class BenchmarkPlan(Record):
    schema_version: Literal[1]
    name: str = Field(min_length=1)
    stage: Literal["s0", "s1", "s2", "s4"]
    track: Literal["matched", "native"]
    model: ModelSpec | None = None
    replicates: int = Field(default=1, ge=1, le=10)
    budget: Budget
    tasks: list[TaskSpec] = Field(min_length=1, max_length=40)
    adapters: list[AdapterSpec] = Field(min_length=2, max_length=12)
    immutable_protocol_note: str = Field(min_length=12)
    evaluation_independence: Literal["not_audited", "private_audited"] = "not_audited"
    approval_status: Literal["not_authorized", "authorized"] = "not_authorized"

    @model_validator(mode="after")
    def validate_comparison(self) -> Self:
        if len({item.id for item in self.tasks}) != len(self.tasks):
            raise ValueError("duplicate task ID")
        if len({item.id for item in self.adapters}) != len(self.adapters):
            raise ValueError("duplicate adapter ID")
        if self.track == "matched":
            if self.model is None:
                raise ValueError("matched track requires a common pinned model")
            if any(a.model is not None and a.model != self.model for a in self.adapters):
                raise ValueError("matched track cannot mix models or transports")
        elif any(a.model is None for a in self.adapters):
            raise ValueError("native track requires a model description per adapter")
        if self.stage == "s0" and (
            self.budget.max_model_attempts != 0 or self.budget.max_dev_experiments != 0
        ):
            raise ValueError("S0 is offline-only: budget must allow zero model/experiment calls")
        if (
            self.stage != "s0"
            and self.approval_status == "authorized"
            and (self.evaluation_independence != "private_audited")
        ):
            raise ValueError("paid experiment authorization requires private evaluation audit")
        return self


def _no_duplicate_properties(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON property: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError(f"nonfinite JSON number: {value}")


def read_strict_json(path: Path, *, max_bytes: int = 1_048_576) -> object:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > max_bytes:
        raise ValueError("missing, symlinked or oversized JSON file")
    return json.loads(
        path.read_text(encoding="utf-8"),
        object_pairs_hook=_no_duplicate_properties,
        parse_constant=_reject_constant,
    )


def read_plan(path: Path) -> BenchmarkPlan:
    raw = read_strict_json(path)
    if not isinstance(raw, dict):
        raise ValueError("benchmark manifest must contain a JSON object")
    return BenchmarkPlan.model_validate(raw)


def normalized_quality(task: TaskSpec, raw_score: float) -> float:
    if not math.isfinite(raw_score):
        raise ValueError("score must be finite")
    quality = (raw_score - task.baseline) / (task.reference - task.baseline)
    return max(0.0, min(1.0, quality))
