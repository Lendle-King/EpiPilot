"""Auditable, budget-aware two-step research heuristic (not a probabilistic MCTS)."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median

from epipilot.research.models import (
    ClaimStatus,
    Proposal,
    ProposalKind,
    ResearchState,
)


@dataclass(frozen=True, slots=True)
class RankedProposal:
    proposal: Proposal
    score: float
    phase: str
    rationale: str


def research_phase(state: ResearchState) -> str:
    """Depend on unresolved decision-relevant uncertainty, not an iteration counter."""
    open_unknowns = [x for x in state.unknowns.values() if x.status == "open"]
    if not open_unknowns:
        return "optimization"
    unresolved = sum(x.importance * x.decision_sensitivity for x in open_unknowns)
    checked = sum(x.measurement_validated for x in state.evidence.values())
    share = unresolved / (unresolved + checked + 0.1)
    if share > 0.55:
        return "exploration"
    if share < 0.25 and checked >= 3:
        return "optimization"
    return "mixed"


_WEIGHTS: dict[str, tuple[float, float, float, float]] = {
    "exploration": (0.52, 0.12, 0.16, 0.20),
    "mixed": (0.34, 0.34, 0.18, 0.14),
    "optimization": (0.18, 0.49, 0.14, 0.19),
}


def _calibrated_cost(proposal: Proposal, state: ResearchState) -> float:
    """Shrink provisional cost toward measured same-kind cost after two real samples."""
    samples = [
        exp.actual_cost
        for exp in state.experiments.values()
        if state.proposals[exp.proposal_id].kind is proposal.kind
        and exp.actual_cost > 0
        and state.evidence[exp.evidence_id].measurement_validated
    ]
    if len(samples) < 2:
        return proposal.estimated_cost
    return max(0.01, 0.7 * proposal.estimated_cost + 0.3 * median(samples))


def _immediate_score(proposal: Proposal, state: ResearchState, phase: str) -> float:
    ig, gain, unlock, novelty = _WEIGHTS[phase]
    value = (
        ig * proposal.information_gain
        + gain * proposal.expected_improvement
        + unlock * proposal.unlock_value
        + novelty * proposal.novelty
    )
    unresolved = any(state.unknowns[uid].status == "open" for uid in proposal.unknown_ids)
    verified_claim = any(
        state.claims[cid].status is ClaimStatus.SUPPORTED
        and any(
            evidence_id in state.evidence and state.evidence[evidence_id].measurement_validated
            for evidence_id in state.claims[cid].supporting_evidence_ids
        )
        for cid in proposal.claim_ids
    )
    if proposal.kind is ProposalKind.EXPLORE and not unresolved:
        value *= 0.7
    if proposal.kind is ProposalKind.OPTIMIZE and not verified_claim:
        value *= 0.65
    if proposal.kind is ProposalKind.VERIFY and any(
        e.measurement_validated for e in state.evidence.values()
    ):
        value *= 1.1
    return value / (0.5 + _calibrated_cost(proposal, state)) - 0.2 * proposal.risk


def _runnable_after(parent: Proposal, child: Proposal, state: ResearchState) -> bool:
    """A child must be affordable and have no other unresolved dependencies."""
    if child.status != "proposed" or parent.id not in child.depends_on:
        return False
    if parent.estimated_cost + child.estimated_cost > state.remaining + 1e-9:
        return False
    return all(
        dependency == parent.id or state.proposals[dependency].status == "completed"
        for dependency in child.depends_on
    )


def rank_proposals(state: ResearchState, limit: int = 5) -> list[RankedProposal]:
    """Rank directly feasible actions; look ahead only to a feasible immediate successor."""
    if limit < 1:
        raise ValueError("limit must be positive")
    phase = research_phase(state)
    eligible = [
        proposal
        for proposal in state.proposals.values()
        if proposal.status == "proposed"
        and proposal.estimated_cost <= state.remaining + 1e-9
        and all(state.proposals[parent].status == "completed" for parent in proposal.depends_on)
    ]
    ranked: list[RankedProposal] = []
    for proposal in eligible:
        immediate = _immediate_score(proposal, state, phase)
        follow_on = max(
            (
                _immediate_score(child, state, phase)
                for child in state.proposals.values()
                if _runnable_after(proposal, child, state)
            ),
            default=0.0,
        )
        score = immediate + 0.25 * max(0.0, follow_on)
        rationale = (
            f"{phase}: immediate={immediate:.4f}, "
            f"discounted_follow_on={0.25 * max(0.0, follow_on):.4f}; "
            f"estimated_cost={proposal.estimated_cost:.3f}, "
            f"empirical_cost={_calibrated_cost(proposal, state):.3f}, "
            f"risk={proposal.risk:.2f}"
        )
        ranked.append(RankedProposal(proposal, score, phase, rationale))
    ranked.sort(key=lambda item: (-item.score, item.proposal.id))
    return ranked[:limit]
