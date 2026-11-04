"""Descriptive, matched research-progress comparison from sealed-score receipts.

The score loader does not execute an agent, model, task, or candidate program.
A matching digest is useful provenance, not proof of an isolated evaluator.
"""

from __future__ import annotations

import json
import math
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Self

from pydantic import Field, model_validator

from epipilot.benchfirst.contracts import (
    SHA256,
    BenchmarkPlan,
    Record,
    normalized_quality,
    _no_duplicate_properties,
    _reject_constant,
)


class Checkpoint(Record):
    elapsed_seconds: float = Field(ge=0, allow_inf_nan=False)
    score: float = Field(allow_inf_nan=False)
    candidate_sha256: str = Field(pattern=SHA256)
    score_artifact_sha256: str = Field(pattern=SHA256)


class Receipt(Record):
    adapter_id: str
    task_id: str
    replicate: int = Field(ge=0)
    status: Literal["success", "failed", "timeout", "blocked"]
    model_attempts: int = Field(ge=0)
    dev_experiments: int = Field(ge=0)
    elapsed_seconds: float = Field(ge=0, allow_inf_nan=False)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    cached_input_tokens: int | None = Field(default=None, ge=0)
    billed_usd: float | None = Field(default=None, ge=0, allow_inf_nan=False)
    evaluator_sha256: str = Field(pattern=SHA256)
    sealed_scoring: bool = False
    checkpoints: list[Checkpoint] = Field(default_factory=list, max_length=100)
    failure_reason: str = ""
    log_sha256: str = Field(pattern=SHA256)

    @model_validator(mode="after")
    def feasible_timeline(self) -> Self:
        previous = -1.0
        for point in self.checkpoints:
            if point.elapsed_seconds < previous or point.elapsed_seconds > self.elapsed_seconds:
                raise ValueError("unordered or future checkpoint in receipt")
            previous = point.elapsed_seconds
        if self.status != "success" and not self.failure_reason:
            raise ValueError("terminal failure requires a reason")
        return self


def _load_receipts(path: Path) -> list[Receipt]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 4_194_304:
        raise ValueError("receipt ledger missing or too large")
    receipts: list[Receipt] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            raise ValueError("blank line in receipt ledger")
        # Reuse the same strict JSON decoder for each receipt; no JSONL partial recovery.
        # A line is bounded independently from the aggregate.
        if len(line.encode("utf-8")) > 1_048_576:
            raise ValueError("receipt line too large")
        # JSONL is not a separate trust boundary; reject NaN and duplicate keys.
        raw = json.loads(
            line,
            object_pairs_hook=_no_duplicate_properties,
            parse_constant=_reject_constant,
        )
        receipts.append(Receipt.model_validate(raw))
    if not receipts:
        raise ValueError("empty receipt ledger")
    return receipts


def progress_area(points: list[tuple[float, float]], budget: float) -> float:
    """Integrate best-so-far verified quality against the same elapsed-time cap."""
    elapsed = 0.0
    best = 0.0
    area = 0.0
    for time, quality in points:
        if not (0 <= elapsed <= time <= budget) or not math.isfinite(quality):
            raise ValueError("invalid research progress timeline")
        if not 0 <= quality <= 1:
            raise ValueError("normalized quality is outside [0,1]")
        area += (time - elapsed) * best
        best = max(best, quality)
        elapsed = time
    return (area + (budget - elapsed) * best) / budget


@dataclass(frozen=True, slots=True)
class _SummaryRow:
    area: float
    final_quality: float
    model_attempts: int
    dev_experiments: int
    elapsed_seconds: float
    billed_usd: float | None


def compare_receipts(plan: BenchmarkPlan, ledger: Path) -> dict[str, object]:
    """Reject missing cells, budget mismatches and forged claims of evaluator version."""
    records = _load_receipts(ledger)
    expected = {
        (adapter.id, task.id, repeat)
        for adapter in plan.adapters
        for task in plan.tasks
        for repeat in range(plan.replicates)
    }
    found = [(r.adapter_id, r.task_id, r.replicate) for r in records]
    if len(found) != len(set(found)) or set(found) != expected:
        raise ValueError("incomplete, duplicated or unexpected matched research cells")
    if plan.stage == "s0" and any(r.model_attempts > 0 or r.dev_experiments > 0 for r in records):
        raise ValueError("offline S0 contains billable actions")
    tasks = {t.id: t for t in plan.tasks}
    per_arm: dict[str, list[_SummaryRow]] = defaultdict(list)
    paired_areas: dict[tuple[str, str, int], float] = {}
    unknown_billing = False
    for receipt in records:
        task = tasks[receipt.task_id]
        if receipt.evaluator_sha256 != task.evaluator_sha256:
            raise ValueError("scored with unexpected evaluator revision")
        if (
            receipt.model_attempts > plan.budget.max_model_attempts
            or receipt.dev_experiments > plan.budget.max_dev_experiments
            or receipt.elapsed_seconds > plan.budget.max_wall_seconds + 1e-9
        ):
            raise ValueError("research cell exceeded frozen budget")
        if receipt.checkpoints and not receipt.sealed_scoring:
            raise ValueError("unverified checkpoint cannot count as research progress")
        points = [
            (point.elapsed_seconds, normalized_quality(task, point.score))
            for point in receipt.checkpoints
        ]
        best = max((value for _, value in points), default=0.0)
        area = progress_area(points, plan.budget.max_wall_seconds)
        per_arm[receipt.adapter_id].append(
            _SummaryRow(
                area=area,
                final_quality=best,
                model_attempts=receipt.model_attempts,
                dev_experiments=receipt.dev_experiments,
                elapsed_seconds=receipt.elapsed_seconds,
                billed_usd=receipt.billed_usd,
            )
        )
        paired_areas[(receipt.adapter_id, receipt.task_id, receipt.replicate)] = area
        if receipt.billed_usd is None:
            unknown_billing = True
    summary: dict[str, object] = {}
    baseline_arm = "agent_only" if "agent_only" in per_arm else plan.adapters[0].id
    for adapter in plan.adapters:
        rows = per_arm[adapter.id]
        differences = [
            paired_areas[(adapter.id, task.id, rep)] - paired_areas[(baseline_arm, task.id, rep)]
            for task in plan.tasks
            for rep in range(plan.replicates)
        ]
        summary[adapter.id] = {
            "mean_progress_area": sum(row.area for row in rows) / len(rows),
            "mean_final_quality": sum(row.final_quality for row in rows) / len(rows),
            "total_model_attempts": sum(row.model_attempts for row in rows),
            "total_dev_experiments": sum(row.dev_experiments for row in rows),
            "total_elapsed_seconds": sum(row.elapsed_seconds for row in rows),
            "billed_usd": (
                None
                if any(row.billed_usd is None for row in rows)
                else sum(row.billed_usd for row in rows if row.billed_usd is not None)
            ),
            "mean_paired_progress_delta": sum(differences) / len(differences),
            "paired_wins": sum(delta > 1e-12 for delta in differences),
            "paired_losses": sum(delta < -1e-12 for delta in differences),
            "paired_ties": sum(abs(delta) <= 1e-12 for delta in differences),
        }
    return {
        "name": plan.name,
        "stage": plan.stage,
        "track": plan.track,
        "matched_task_count": len(plan.tasks),
        "replicates_per_task": plan.replicates,
        "cells": len(records),
        "per_adapter": summary,
        "paired_baseline": baseline_arm,
        "provider_billing_complete": not unknown_billing,
        "evaluation_independence_claim": plan.evaluation_independence,
        "status": "descriptive_only",
        "qualification": (
            "Matching identities, numeric budget ceilings and asserted evaluator SHA checked. "
            "No runnable agent adapter or isolated private evaluator is attested by this file. "
            "Do not claim a statistically significant winner without task-level inference and "
            "an external, auditable holdout. Missing provider charges prohibit dollar claims."
        ),
    }
