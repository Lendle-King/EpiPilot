"""Outcome-aware progression and budget-protected exploration; explicit heuristics."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from epipilot.epistemics.models import HypothesisStatus, UnknownImpact
from epipilot.research.budget import BudgetManager
from epipilot.research.frontier import Phase, Purpose, Question
from epipilot.research.models import ExperimentProposal, Outcome, Ranking, Rejected, amounts
from epipilot.research.policy import prerequisite_supported, rank_candidates
from epipilot.state.project import ProjectState


def question_weight(state: ProjectState, question: Question) -> float:
    unknown = next(u for u in state.unknowns if u.id == question.unknown_id)
    impact = {UnknownImpact.HIGH: 1.0, UnknownImpact.MEDIUM: 0.6, UnknownImpact.LOW: 0.3}[
        unknown.impact
    ]
    return impact * unknown.value_of_information * unknown.decision_sensitivity


def question_closed(state: ProjectState, question: Question) -> bool:
    r = state.research
    if r is None or r.frontier is None or not r.frontier.closed(question.unknown_id):
        return False
    resolution = next(
        x for x in reversed(r.frontier.resolutions) if x.unknown_id == question.unknown_id
    )
    review = next(v for v in r.reviews if v.evidence_id == resolution.evidence_id)
    p = r.proposal(review.assessment.proposal_id)
    expected = (
        HypothesisStatus.SUPPORTED
        if review.assessment.outcome is Outcome.SUPPORT
        else HypothesisStatus.REFUTED
    )
    h = next(h for h in state.hypotheses if h.id == p.hypothesis_id)
    node = next(n for n in r.nodes if n.hypothesis_id == h.id)
    return (
        h.status is expected
        and all(prerequisite_supported(state, x) for x in node.and_ids)
        and (not node.or_ids or any(prerequisite_supported(state, x) for x in node.or_ids))
    )


def research_phase(state: ProjectState) -> Phase:
    r = state.research
    if r is None or r.frontier is None or not r.frontier.questions:
        return Phase.EXPLORATION
    total = resolved = 0.0
    critical = False
    for q in r.frontier.questions:
        weight = question_weight(state, q)
        total += weight
        if question_closed(state, q):
            resolved += weight
        elif weight >= 0.6:
            critical = True
    ratio = resolved / total if total else 0.0
    if not critical and ratio >= 0.7:
        return Phase.OPTIMIZATION
    return Phase.MIXED if ratio >= 0.25 else Phase.EXPLORATION


WEIGHTS = {
    Phase.EXPLORATION: (0.52, 0.12, 0.16, 0.20),
    Phase.MIXED: (0.34, 0.34, 0.18, 0.14),
    Phase.OPTIMIZATION: (0.18, 0.49, 0.14, 0.19),
}


def agent_contract_reason(
    state: ProjectState,
    p: ExperimentProposal,
    registered: frozenset[str],
    *,
    assumed_supported: frozenset[UUID] = frozenset(),
) -> str | None:
    """Measurement eligibility independent of either legacy or decision-value scores."""
    r = state.research
    assert r is not None and r.frontier is not None
    c = next((c for c in r.frontier.contracts if c.proposal_id == p.id), None)
    if c is None or c.adapter not in registered:
        return "no registered input-bound runner/verifier contract"
    if {cost.unit for cost in p.estimated_cost} != {"wall_seconds"}:
        return "built-in runner meters wall_seconds; provide a domain cost-aware runner"
    if c.purpose is Purpose.EXPLORE and all(
        question_closed(state, q) for q in r.frontier.questions if q.unknown_id in p.unknown_ids
    ):
        return "question already resolved; require new variable/input or valid reopening"
    if c.purpose is Purpose.OPTIMIZE:
        node = next(n for n in r.nodes if n.hypothesis_id == p.hypothesis_id)
        if not any(
            hid in assumed_supported or prerequisite_supported(state, hid)
            for hid in (p.hypothesis_id, *node.parent_ids, *node.and_ids)
        ):
            return "optimization has no currently supported mechanism/baseline"
    return None


def rank_agent(
    state: ProjectState, registered: frozenset[str], at: datetime | None = None
) -> Ranking:
    if state.intelligence is not None:
        from epipilot.research.decision_policy import rank_decision_value

        return rank_decision_value(state, at or datetime.now(UTC), registered)
    ranking = rank_candidates(state, at or datetime.now(UTC))
    r = state.research
    if r is None or r.frontier is None:
        raise ValueError("agent frontier not configured")
    if ranking.stop_reason in {"goal_met", "in_flight_requires_resume"}:
        return ranking
    f = r.frontier
    phase = research_phase(state)
    weights = WEIGHTS[phase]
    manager = BudgetManager(r)
    ranked = []
    rejected = list(ranking.rejected)
    by_contract = {c.proposal_id: c for c in f.contracts}
    for item in ranking.ranked:
        c = by_contract.get(item.proposal_id)
        p = r.proposal(item.proposal_id)
        reason = agent_contract_reason(state, p, registered)
        if reason:
            rejected.append(Rejected(proposal_id=p.id, reason=reason))
            continue
        # Credit only jointly affordable children whose other prerequisites are valid.
        unlock = 0.0
        for child in r.proposals:
            node = next(n for n in r.nodes if n.hypothesis_id == child.hypothesis_id)
            if (
                p.hypothesis_id not in node.and_ids
                or not child.data_qualified
                or child.id not in by_contract
                or by_contract[child.id].adapter not in registered
                or r.observation(child.id) is not None
                or child.id in {s.proposal_id for s in r.settlements}
            ):
                continue
            if any(
                not prerequisite_supported(state, x) for x in node.and_ids if x != p.hypothesis_id
            ):
                continue
            try:
                combined = amounts(manager.reservation(p))
                for cost in manager.reservation(child):
                    combined[cost.unit] = combined.get(cost.unit, 0.0) + cost.amount
                if all(v <= manager.remaining[k] for k, v in combined.items()):
                    unlock = max(unlock, child.contribution * child.uncertainty)
            except ValueError:
                continue
        info, gain, unblock, novelty = weights
        score = (
            info * item.information
            + gain * item.contribution
            + unblock * unlock
            + novelty * p.uncertainty
            - p.risk * 0.2
        ) / (item.normalized_cost + 1e-9)
        if score <= 0:
            rejected.append(
                Rejected(proposal_id=p.id, reason="nonpositive estimated decision value")
            )
            continue
        ranked.append(
            item.model_copy(
                update={
                    "score": score,
                    "unlocking": unlock,
                    "explanation": (
                        f"{phase.value}: verified-unknown progression; "
                        "jointly affordable lookahead; heuristic, not EVSI"
                    ),
                }
            )
        )
    ranked.sort(key=lambda x: (-x.score, x.proposal_id))
    explore = [x for x in ranked if by_contract[x.proposal_id].purpose is Purpose.EXPLORE]
    if ranked and explore and any(not question_closed(state, q) for q in f.questions):
        exploration_spent = total_spent = 0.0
        for settlement in r.settlements:
            c = by_contract.get(settlement.proposal_id)
            if c is None:
                continue
            spent_value = (
                manager.normalized(settlement.actual_cost)
                if any(x.amount for x in settlement.actual_cost)
                else 0.0
            )
            total_spent += spent_value
            if c.purpose is Purpose.EXPLORE:
                exploration_spent += spent_value
        best_explore = explore[0]
        chosen = ranked[0]
        deficit = f.exploration_fraction * total_spent - exploration_spent > 1e-12
        left = manager.remaining.copy()
        for cost in manager.reservation(r.proposal(chosen.proposal_id)):
            left[cost.unit] -= cost.amount
        starves = any(
            cost.amount > left[cost.unit]
            for cost in manager.reservation(r.proposal(best_explore.proposal_id))
        )
        if by_contract[chosen.proposal_id].purpose is not Purpose.EXPLORE and (deficit or starves):
            forced = best_explore.model_copy(
                update={
                    "explanation": best_explore.explanation
                    + "; protect declared exploration budget",
                    "score": max(x.score for x in ranked) + 1.0,
                }
            )
            ranked = [forced, *[x for x in ranked if x.proposal_id != forced.proposal_id]]
    return ranking.model_copy(
        update={
            "ranked": tuple(ranked),
            "rejected": tuple(rejected),
            "stop_reason": None if ranked else ranking.stop_reason or "blocked_agent_frontier",
        }
    )
