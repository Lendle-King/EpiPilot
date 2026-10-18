"""Cost-aware two-step heuristic over a typed, evidence-aware research DAG.

Scores are explicit, uncalibrated research heuristics, NOT probabilities or
claims of empirically established optimality. Agent-provided estimates require
rationales and can be recalibrated from measured outcomes later.
"""

from __future__ import annotations

from dataclasses import dataclass

from epipilot.research.models import Proposal, ProposalKind, ResearchState


@dataclass(frozen=True, slots=True)
class RankedProposal:
    proposal: Proposal
    score: float
    phase: str
    rationale: str


def research_phase(state: ResearchState) -> str:
    """Select exploration/optimization mix from measured progress, not round count."""
    if not state.unknowns:
        return "exploration"
    unresolved = sum(
        x.importance * x.decision_sensitivity
        for x in state.unknowns.values()
        if x.status == "open"
    )
    checked = sum(e.independently_verified for e in state.evidence.values())
    uncertainty_share = unresolved / (unresolved + checked + 0.1)
    if uncertainty_share > 0.55:
        return "exploration"
    if uncertainty_share < 0.25 and checked >= 3:
        return "optimization"
    return "mixed"


_WEIGHTS: dict[str, tuple[float, float, float, float]] = {
    "exploration": (0.52, 0.12, 0.16, 0.20),
    "mixed": (0.34, 0.34, 0.18, 0.14),
    "optimization": (0.18, 0.49, 0.14, 0.19),
}


def _immediate_score(proposal: Proposal, state: ResearchState, phase: str) -> float:
    ig, gain, unlock, novelty = _WEIGHTS[phase]
    value = (
        ig * proposal.information_gain
        + gain * proposal.expected_improvement
        + unlock * proposal.unlock_value
        + novelty * proposal.novelty
    )
    unresolved = any(
        state.unknowns[uid].status == "open" for uid in proposal.unknown_ids
    )
    verified_claim = any(
        state.claims[cid].supporting_evidence_ids for cid in proposal.claim_ids
    )
    if proposal.kind is ProposalKind.EXPLORE and not unresolved:
        value *= 0.7
    if proposal.kind is ProposalKind.OPTIMIZE and not verified_claim:
        value *= 0.65
    if proposal.kind is ProposalKind.VERIFY and state.evidence:
        value *= 1.1
    return value / (0.5 + proposal.estimated_cost) - 0.2 * proposal.risk


def rank_proposals(state: ResearchState, limit: int = 5) -> list[RankedProposal]:
    """Rank runnable research actions, accounting for budget and a one-step child."""
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
        children = [
            child for child in state.proposals.values()
            if proposal.id in child.depends_on and child.status == "proposed"
        ]
        # Limited-depth lookahead: exploratory actions can unlock valuable
        # follow-on optimizations. This is not full MCTS.
        follow_on = max(
            (_immediate_score(child, state, phase) for child in children),
            default=0.0,
        )
        score = immediate + 0.25 * max(0.0, follow_on)
        rationale = (
            f"{phase}: immediate={immediate:.4f}, "
            f"discounted_follow_on={0.25 * max(0.0, follow_on):.4f}; "
            f"estimated_cost={proposal.estimated_cost:.3f}, risk={proposal.risk:.2f}"
        )
        ranked.append(RankedProposal(proposal, score, phase, rationale))
    ranked.sort(key=lambda item: (-item.score, item.proposal.id))
    return ranked[:limit]
