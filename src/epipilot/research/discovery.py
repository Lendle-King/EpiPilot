"""Read-only, evidence-linked hypothesis comparison and diagnostic design.

This is a decision aid, not a second claim database, experiment runner or EVSI
estimator. Predicted observations are unverified hypotheses until an actual
preregistered experiment is independently measured and reviewed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path
from typing import Self

from pydantic import Field, model_validator

from epipilot.research.models import (
    ClaimStatus,
    ExplorationStage,
    Record,
    ResearchState,
)


class DiagnosticProbe(Record):
    """Candidate intervention with an explicit prediction under each hypothesis."""

    id: str = Field(min_length=1)
    title: str = Field(min_length=1)
    protocol: str = Field(min_length=8)
    control: str = Field(min_length=8)
    measurement: str = Field(min_length=8)
    decision_rule: str = Field(min_length=8)
    estimated_cost: float = Field(gt=0, allow_inf_nan=False)
    risk: float = Field(ge=0, le=1, allow_inf_nan=False)
    predictions: dict[str, str] = Field(min_length=2)

    @model_validator(mode="after")
    def nonempty_predictions(self) -> Self:
        if any(not value.strip() for value in self.predictions.values()):
            raise ValueError("all predicted observations must be nonempty")
        return self


class HypothesisMatrix(Record):
    """A temporary, scoped comparison referencing canonical Unknown and Claim IDs."""

    unknown_id: str = Field(min_length=1)
    scope: str = Field(min_length=1)
    hypothesis_ids: list[str] = Field(min_length=2, max_length=12)
    probes: list[DiagnosticProbe] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def distinct_references(self) -> Self:
        if len(set(self.hypothesis_ids)) != len(self.hypothesis_ids):
            raise ValueError("hypothesis IDs must be distinct")
        if len({probe.id for probe in self.probes}) != len(self.probes):
            raise ValueError("diagnostic probe IDs must be distinct")
        for probe in self.probes:
            if set(probe.predictions) != set(self.hypothesis_ids):
                raise ValueError("every probe needs one prediction per hypothesis")
        return self


@dataclass(frozen=True, slots=True)
class RankedDiagnostic:
    probe: DiagnosticProbe
    distinct_pairs: int
    total_pairs: int
    importance: float
    feasible: bool
    budget_reason: str


def _safe_json_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    data: dict[str, object] = {}
    for key, value in pairs:
        if key in data:
            raise ValueError(f"duplicate diagnostic JSON property: {key}")
        data[key] = value
    return data


def _reject_constant(value: str) -> None:
    raise ValueError(f"invalid JSON numeric constant: {value}")


def load_hypothesis_matrix(root: Path, path: Path) -> HypothesisMatrix:
    """Load a bounded, local JSON draft without touching canonical research state."""
    resolved_root = root.resolve()
    resolved = (resolved_root / path).resolve()
    if not resolved.is_relative_to(resolved_root):
        raise ValueError("diagnostic draft must stay inside the research root")
    if resolved.stat().st_size > 256 * 1024:
        raise ValueError("diagnostic draft exceeds 256 KiB")
    raw = json.loads(
        resolved.read_text(encoding="utf-8"),
        object_pairs_hook=_safe_json_object,
        parse_constant=_reject_constant,
    )
    if not isinstance(raw, dict):
        raise ValueError("diagnostic draft must contain a JSON object")
    return HypothesisMatrix.model_validate(raw)


def discovery_frontier(state: ResearchState) -> dict[str, object]:
    """Project canonical uncertainty and reviewed evidence; infer no causal links."""
    unknowns = sorted(
        state.unknowns.values(),
        key=lambda u: (-u.importance * u.decision_sensitivity, u.id),
    )
    claims: list[dict[str, object]] = []
    for claim in sorted(state.claims.values(), key=lambda c: c.id):
        cited_ids = sorted(
            set(
                claim.supporting_evidence_ids
                + claim.contradicting_evidence_ids
                + claim.inconclusive_evidence_ids
            )
        )
        qualified_ids = [
            evidence_id
            for evidence_id in cited_ids
            if evidence_id in state.evidence
            and state.evidence[evidence_id].measurement_validated
        ]
        claims.append(
            {
                "id": claim.id,
                "statement": claim.statement,
                "scope": claim.scope,
                "status": claim.status.value,
                "assumptions": claim.assumptions,
                "qualified_evidence_ids": qualified_ids,
                "unqualified_evidence_ids": [
                    evidence_id for evidence_id in cited_ids if evidence_id not in qualified_ids
                ],
            }
        )
    return {
        "goal": state.goal.statement if state.goal is not None else None,
        "open_unknowns": [
            {
                "id": u.id,
                "question": u.question,
                "decision_importance": u.importance * u.decision_sensitivity,
                "reopening_evidence_ids": u.reopening_evidence_ids,
            }
            for u in unknowns
            if u.status == "open"
        ],
        "scoped_claims": claims,
        "status": "read_only_projection",
        "qualification": (
            "Co-listed claims are not automatically competing hypotheses. "
            "A comparison must explicitly link claims and predict discriminating observations. "
            "Claim status and evidence still come solely from canonical v2 replay."
        ),
    }


def rank_diagnostic_probes(
    state: ResearchState, matrix: HypothesisMatrix
) -> dict[str, object]:
    """Prioritize qualitative discrimination, never invent probability or information gain."""
    if state.goal is None:
        raise ValueError("diagnostic design requires an approved research goal")
    unknown = state.unknowns.get(matrix.unknown_id)
    if unknown is None or unknown.status != "open":
        raise ValueError("diagnostic design must reference an open canonical Unknown")
    for claim_id in matrix.hypothesis_ids:
        claim = state.claims.get(claim_id)
        if claim is None:
            raise ValueError(f"diagnostic design references unknown Claim: {claim_id}")
        if claim.scope != matrix.scope:
            raise ValueError(f"diagnostic scope disagrees with Claim: {claim_id}")
        if claim.status in {ClaimStatus.REFUTED, ClaimStatus.SUPERSEDED}:
            raise ValueError(f"diagnostic design references inactive Claim: {claim_id}")

    available = state.remaining
    budget_source = "project_remaining"
    if (
        state.exploration is not None
        and state.exploration.enabled
        and state.exploration.stage is ExplorationStage.EXPLORATION
    ):
        available = min(available, state.exploration_remaining)
        budget_source = "exploration_remaining"

    importance = unknown.importance * unknown.decision_sensitivity
    pairs = list(combinations(matrix.hypothesis_ids, 2))
    ranked: list[RankedDiagnostic] = []
    for probe in matrix.probes:
        distinct = sum(
            probe.predictions[left] != probe.predictions[right]
            for left, right in pairs
        )
        feasible = probe.estimated_cost <= available + 1e-9
        reason = "within_budget" if feasible else "over_budget"
        ranked.append(
            RankedDiagnostic(
                probe=probe,
                distinct_pairs=distinct,
                total_pairs=len(pairs),
                importance=importance,
                feasible=feasible,
                budget_reason=reason,
            )
        )
    ranked.sort(
        key=lambda row: (
            not row.feasible,
            -row.distinct_pairs,
            -row.importance,
            row.probe.estimated_cost,
            row.probe.risk,
            row.probe.id,
        )
    )
    return {
        "unknown_id": matrix.unknown_id,
        "scope": matrix.scope,
        "hypothesis_ids": matrix.hypothesis_ids,
        "available_budget": available,
        "budget_source": budget_source,
        "ranked_probes": [
            {
                "id": row.probe.id,
                "title": row.probe.title,
                "distinguishable_pairs": row.distinct_pairs,
                "total_pairs": row.total_pairs,
                "qualitative_separation": row.distinct_pairs / row.total_pairs,
                "decision_importance": row.importance,
                "estimated_cost": row.probe.estimated_cost,
                "risk": row.probe.risk,
                "feasible": row.feasible,
                "status": (
                    "over_budget"
                    if not row.feasible
                    else "non_discriminating"
                    if row.distinct_pairs == 0
                    else "draft_only"
                ),
                "budget_reason": row.budget_reason,
                "protocol": row.probe.protocol,
                "control": row.probe.control,
                "measurement": row.probe.measurement,
                "decision_rule": row.probe.decision_rule,
                "predictions": row.probe.predictions,
            }
            for row in ranked
        ],
        "qualification": (
            "Predictions are agent-authored, uncalibrated categorical hypotheses. "
            "Pair separation is NOT measured information gain or EVSI. "
            "Nothing was registered, executed, measured, or promoted to evidence. "
            "Register a Proposal and freeze an independent experiment contract before running."
        ),
    }
