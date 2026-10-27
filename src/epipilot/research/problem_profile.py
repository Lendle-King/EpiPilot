"""Read-only problem characterization derived from v2 scoped claims and evidence."""

from __future__ import annotations

from epipilot.research.models import (
    ClaimStatus,
    ExplorationDimension,
    ProblemInsight,
    ResearchState,
)


def current_insight(state: ResearchState, insight: ProblemInsight) -> bool:
    """Guidance goes stale when counterevidence changes a supporting claim's status."""
    for claim_id, status in insight.claim_statuses.items():
        claim = state.claims.get(claim_id)
        if claim is None or claim.status is not status:
            return False
        reviewed = set(claim.supporting_evidence_ids + claim.contradicting_evidence_ids)
        if not reviewed.intersection(insight.evidence_ids):
            return False
    return all(
        evidence_id in state.evidence and state.evidence[evidence_id].measurement_validated
        for evidence_id in insight.evidence_ids
    )


def problem_profile(state: ResearchState) -> dict[str, object]:
    """Scope-aware projection. Human/agent guidance is not promoted to a fact."""
    dimensions: list[dict[str, object]] = []
    for dimension in ExplorationDimension:
        insights = [
            {
                "id": insight.id,
                "title": insight.title,
                "scope": insight.scope,
                "current": current_insight(state, insight),
                "recommendation": insight.recommendation,
                "avoidance": insight.avoidance,
                "rationale": insight.rationale,
                "claim_ids": sorted(insight.claim_statuses),
                "evidence_ids": insight.evidence_ids,
                "status": "advisory" if current_insight(state, insight) else "stale",
            }
            for insight in state.problem_insights.values()
            if insight.dimension is dimension
        ]
        if insights:
            dimensions.append({"dimension": dimension.value, "insights": insights})
    claims = [
        {
            "id": claim.id,
            "statement": claim.statement,
            "scope": claim.scope,
            "status": claim.status.value,
            "supporting_evidence_ids": claim.supporting_evidence_ids,
            "contradicting_evidence_ids": claim.contradicting_evidence_ids,
        }
        for claim in state.claims.values()
        if claim.status is not ClaimStatus.HYPOTHESIS
    ]
    unknowns = [
        {
            "id": unknown.id,
            "question": unknown.question,
            "status": unknown.status,
            "decision_importance": unknown.importance * unknown.decision_sensitivity,
        }
        for unknown in state.unknowns.values()
    ]
    return {
        "dimensions": dimensions,
        "scoped_claims": claims,
        "unknowns": unknowns,
        "qualification": (
            "Recommendations are traceable, scoped interpretations, NOT validated facts. "
            "Only frozen experiments with independently checked measurements support claims."
        ),
    }
