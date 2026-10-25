"""Compare recorded research policies on matched cases under identical budget caps.

The independent evaluator must create the metric artifacts. Hash checks here
cannot prove evaluator correctness, absence of leakage, or causal improvements.
"""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class InvalidBenchmark(ValueError):
    """Benchmark rows are unpaired, invalid, stale, or otherwise incomparable."""


def _unique_fields(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    obj: dict[str, Any] = {}
    for key, value in pairs:
        if key in obj:
            raise InvalidBenchmark(f"duplicate JSON property: {key}")
        obj[key] = value
    return obj


def _read_json(path: Path) -> Any:
    if not path.is_file() or path.stat().st_size > 2 * 1024 * 1024:
        raise InvalidBenchmark("benchmark JSON is missing or oversized")
    try:
        return json.loads(
            path.read_text(encoding="utf-8"),
            object_pairs_hook=_unique_fields,
            parse_constant=lambda value: (_ for _ in ()).throw(
                InvalidBenchmark(f"nonfinite number: {value}")
            ),
        )
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise InvalidBenchmark("benchmark requires valid JSON") from exc


class _Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Case(_Record):
    case_id: str = Field(min_length=1)
    seed: int


class Run(_Record):
    policy: str = Field(min_length=1)
    case_id: str = Field(min_length=1)
    seed: int
    artifact_ref: str = Field(min_length=1)
    artifact_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    actual_cost: float = Field(ge=0, allow_inf_nan=False)
    human_interventions: int = Field(ge=0)
    repeated_experiments: int = Field(ge=0)
    unsupported_claims: int = Field(ge=0)


class Suite(_Record):
    schema_version: Literal[1]
    name: str = Field(min_length=1)
    dataset: str = Field(min_length=1)
    evaluator: str = Field(min_length=1)
    metric: str = Field(min_length=1)
    direction: Literal["maximize", "minimize"]
    budget_unit: str = Field(min_length=1)
    per_case_budget: float = Field(gt=0, allow_inf_nan=False)
    baseline: str = Field(min_length=1)
    policies: list[str] = Field(min_length=2)
    cases: list[Case] = Field(min_length=2)
    runs: list[Run] = Field(min_length=1)

    @model_validator(mode="after")
    def complete_matched_design(self) -> Self:
        if len(set(self.policies)) != len(self.policies) or self.baseline not in self.policies:
            raise ValueError("benchmark policies or baseline are invalid")
        cases = [(case.case_id, case.seed) for case in self.cases]
        if len(set(cases)) != len(cases):
            raise ValueError("duplicate case/seed")
        expected = {(p, name, seed) for p in self.policies for name, seed in cases}
        observed = [(r.policy, r.case_id, r.seed) for r in self.runs]
        if len(set(observed)) != len(observed) or set(observed) != expected:
            raise ValueError("all policies must evaluate every matched case exactly once")
        if any(r.actual_cost > self.per_case_budget + 1e-9 for r in self.runs):
            raise ValueError("recorded run exceeds shared per-case budget")
        return self


def _measured(root: Path, suite: Suite, run: Run) -> float:
    ref = Path(run.artifact_ref)
    if ref.is_absolute() or ".." in ref.parts:
        raise InvalidBenchmark("unsafe artifact reference")
    artifact = (root / ref).resolve()
    if not artifact.is_relative_to(root.resolve()):
        raise InvalidBenchmark("artifact escapes benchmark workspace")
    source = _read_json(artifact)
    if hashlib.sha256(artifact.read_bytes()).hexdigest() != run.artifact_sha256:
        raise InvalidBenchmark("artifact hash mismatch")
    if not isinstance(source, dict) or set(source) != {
        "case_id",
        "seed",
        "dataset",
        "evaluator",
        "metric",
        "value",
    }:
        raise InvalidBenchmark("unexpected independent evaluation record schema")
    expected = {
        "case_id": run.case_id,
        "seed": run.seed,
        "dataset": suite.dataset,
        "evaluator": suite.evaluator,
        "metric": suite.metric,
    }
    for key, value in expected.items():
        if type(source[key]) is not type(value) or source[key] != value:
            raise InvalidBenchmark(f"evaluator record disagrees on {key}")
    score = source["value"]
    if isinstance(score, bool) or not isinstance(score, (float, int)) or not math.isfinite(score):
        raise InvalidBenchmark("nonfinite or nonnumeric evaluation metric")
    return float(score)


def compare_suite(path: Path, *, min_cases: int = 2) -> dict[str, Any]:
    """Return descriptive matched-policy metrics; never make a significance claim."""
    if min_cases < 2:
        raise InvalidBenchmark("minimum matched case requirement must be >= 2")
    suite = Suite.model_validate(_read_json(path))
    if len(suite.cases) < min_cases:
        raise InvalidBenchmark("too few matched cases")
    root = path.resolve().parent
    runs = {(r.policy, r.case_id, r.seed): r for r in suite.runs}
    scores = {(r.policy, r.case_id, r.seed): _measured(root, suite, r) for r in suite.runs}
    case_keys = [(c.case_id, c.seed) for c in suite.cases]
    baseline = [scores[(suite.baseline, *key)] for key in case_keys]
    sign = 1 if suite.direction == "maximize" else -1
    report: dict[str, Any] = {}
    for policy in suite.policies:
        observations = [runs[(policy, *key)] for key in case_keys]
        values = [scores[(policy, *key)] for key in case_keys]
        differences = [sign * (a - b) for a, b in zip(values, baseline, strict=True)]
        report[policy] = {
            "mean_score": sum(values) / len(values),
            "mean_cost": sum(r.actual_cost for r in observations) / len(observations),
            "total_cost": sum(r.actual_cost for r in observations),
            "mean_paired_improvement": sum(differences) / len(differences),
            "paired_wins": sum(d > 1e-12 for d in differences),
            "paired_losses": sum(d < -1e-12 for d in differences),
            "paired_ties": sum(abs(d) <= 1e-12 for d in differences),
            "human_interventions": sum(r.human_interventions for r in observations),
            "repeated_experiments": sum(r.repeated_experiments for r in observations),
            "unsupported_claims": sum(r.unsupported_claims for r in observations),
        }
    return {
        "suite": suite.name,
        "dataset": suite.dataset,
        "evaluator": suite.evaluator,
        "metric": suite.metric,
        "direction": suite.direction,
        "baseline": suite.baseline,
        "matched_cases": len(case_keys),
        "per_case_budget": suite.per_case_budget,
        "budget_unit": suite.budget_unit,
        "policies": report,
        "qualification": (
            "Matched identities and artifact hashes checked; evaluator independence, "
            "cost authenticity, statistical confidence and causal gains not established."
        ),
    }
