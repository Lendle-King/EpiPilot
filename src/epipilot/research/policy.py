"""Explainable heuristic decision value, explicitly NOT a calibrated EVSI."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from epipilot.core.models import TaskId
from epipilot.epistemics.models import HypothesisStatus
from epipilot.research.budget import BudgetManager, ResourceScheduler
from epipilot.research.models import (
    ExperimentProposal,
    Fidelity,
    Outcome,
    Ranked,
    Ranking,
    Rejected,
    ResearchNode,
    ResearchState,
    Strategy,
)
from epipilot.scheduler.policy import SchedulingSignals
from epipilot.state.project import ProjectState


def validate_graph(nodes: tuple[ResearchNode, ...]) -> None:
    by_id = {n.hypothesis_id: n for n in nodes}
    if len(by_id) != len(nodes):
        raise ValueError("duplicate research node")
    visiting: set[UUID] = set()
    done: set[UUID] = set()

    def visit(identifier: UUID) -> None:
        if identifier in visiting:
            raise ValueError("research dependency cycle")
        if identifier in done:
            return
        if identifier not in by_id:
            raise ValueError("unknown dependency")
        visiting.add(identifier)
        node = by_id[identifier]
        for dep in (*node.parent_ids, *node.and_ids, *node.or_ids):
            visit(dep)
        if any(x not in by_id or x == identifier for x in node.mutex_ids):
            raise ValueError("invalid mutex reference")
        visiting.remove(identifier)
        done.add(identifier)

    for identifier in by_id:
        visit(identifier)


def _unlocking(research: ResearchState, identifier: UUID) -> float:
    seen = {identifier}
    frontier = {identifier}
    value, discount = 0.0, 0.5
    while frontier:
        children = [
            n
            for n in research.nodes
            if n.hypothesis_id not in seen and any(x in frontier for x in (*n.and_ids, *n.or_ids))
        ]
        value += sum(n.contribution * n.uncertainty * discount for n in children)
        frontier = {n.hypothesis_id for n in children}
        seen.update(frontier)
        discount *= 0.5
    return min(value, 1.0)


def prerequisite_supported(state: ProjectState, identifier: UUID) -> bool:
    """Support is contingent on all still-valid ancestors, including OR alternatives."""
    r = state.research
    assert r is not None
    statuses: dict[UUID, HypothesisStatus] = {h.id: h.status for h in state.hypotheses}
    nodes = {n.hypothesis_id: n for n in r.nodes}
    memo: dict[UUID, bool] = {}

    def supported(hid: UUID) -> bool:
        if hid in memo:
            return memo[hid]
        node = nodes[hid]
        result = statuses.get(hid) is HypothesisStatus.SUPPORTED
        result = result and all(supported(x) for x in node.and_ids)
        result = result and (not node.or_ids or any(supported(x) for x in node.or_ids))
        memo[hid] = result
        return result

    return supported(identifier)


def _infeasible(state: ProjectState, p: ExperimentProposal, at: datetime) -> str | None:
    r = state.research
    assert r is not None
    statuses: dict[UUID, HypothesisStatus] = {h.id: h.status for h in state.hypotheses}
    node = next(n for n in r.nodes if n.hypothesis_id == p.hypothesis_id)
    if p.id in {x.proposal_id for x in r.reservations}:
        return "already submitted; reuse recorded result or explicitly recover in-flight work"
    from epipilot.research.frontier import Purpose

    recheck = False
    if r.frontier is not None:
        contract = next((c for c in r.frontier.contracts if c.proposal_id == p.id), None)
        recheck = (
            contract is not None
            and contract.purpose is Purpose.VERIFY
            and any(
                not r.frontier.closed(uid)
                and any(x.unknown_id == uid for x in r.frontier.resolutions)
                for uid in p.unknown_ids
            )
        )
    if statuses[p.hypothesis_id] is HypothesisStatus.SUPERSEDED or (
        statuses[p.hypothesis_id] is HypothesisStatus.REFUTED and not recheck
    ):
        return "route refuted or superseded; new evidence required"
    if p.available_at and p.available_at > at:
        return "future input unavailable at decision time"
    if not p.data_qualified:
        return f"data unqualified: {p.data_reason}"
    if any(not prerequisite_supported(state, x) for x in node.and_ids):
        return "AND prerequisite not supported"
    if node.or_ids and not any(prerequisite_supported(state, x) for x in node.or_ids):
        return "OR prerequisites not supported"
    for other in r.nodes:
        if other.hypothesis_id in node.mutex_ids or node.hypothesis_id in other.mutex_ids:
            busy = any(
                r.proposal(a.proposal_id).hypothesis_id == other.hypothesis_id for a in r.active
            )
            if busy or statuses[other.hypothesis_id] is HypothesisStatus.SUPPORTED:
                return "mutually exclusive route active or supported"
    for identifier in p.predecessor_ids:
        review = r.review(identifier)
        previous = r.proposal(identifier)
        observed = r.observation(identifier)
        from epipilot.research.adapters import VerifiedFidelityPolicy

        if (
            not review
            or not prerequisite_supported(state, previous.hypothesis_id)
            or observed is None
            or not VerifiedFidelityPolicy().can_upgrade(
                previous, observed, review.assessment, p.fidelity
            )
        ):
            return (
                "fidelity prerequisite not independently supported with sufficient reliable samples"
            )
    if (
        p.fidelity > Fidelity.F0
        and p.predecessor_ids
        and any(r.proposal(x).fidelity >= p.fidelity for x in p.predecessor_ids)
    ):
        return "invalid fidelity escalation"
    if state.current_plan is not None:
        from epipilot.research.reducer import task_id

        canonical_task = task_id(state.project_id, p.id)
        if canonical_task not in {t.id for t in state.current_plan.runnable_tasks()}:
            return "canonical task graph not runnable"
    # Only explicit, comparable stages are gated; no universal low-fidelity pruning.
    active = tuple(r.proposal(a.proposal_id).resources for a in r.active)
    if not ResourceScheduler.available(p.resources, active, r.goal.resources):
        return "resource conflict or quota"
    try:
        BudgetManager(r).reservation(p)
    except ValueError as exc:
        return str(exc)
    return None


def goal_met(state: ProjectState) -> bool:
    r = state.research
    if r is None or not r.goal.constraints:
        return False
    return any(
        review.assessment.verified
        and review.assessment.qualified_for_promotion
        and review.assessment.outcome is Outcome.SUPPORT
        and review.assessment.scope == r.goal.required_scope
        and r.proposal(review.assessment.proposal_id).fidelity == Fidelity.F4
        and (o := r.observation(review.assessment.proposal_id)) is not None
        and o.samples >= r.goal.minimum_samples
        and all(c.accepts(o.metrics) for c in r.goal.constraints)
        and (
            r.goal.minimum_improvement == 0
            or any(
                m.name == "improvement" and m.value >= r.goal.minimum_improvement for m in o.metrics
            )
        )
        for review in r.reviews
    )


def rank_candidates(
    state: ProjectState, at: datetime, strategy: Strategy = Strategy.CARTS
) -> Ranking:
    if at.tzinfo is None:
        raise ValueError("decision time must be timezone-aware")
    r = state.research
    if r is None:
        raise ValueError("research not configured")
    validate_graph(r.nodes)
    ranked, rejected = [], []
    for index, p in enumerate(r.proposals):
        reason = _infeasible(state, p, at)
        if reason:
            rejected.append(Rejected(proposal_id=p.id, reason=reason))
            continue
        cost = BudgetManager(r).normalized(BudgetManager(r).estimate(p))
        info = p.information * p.uncertainty
        contribution = p.contribution * (1 - 0.5 * p.uncertainty)
        unlocking = _unlocking(r, p.hypothesis_id)
        exploration = 0.15 * p.uncertainty
        if strategy is Strategy.NO_INFORMATION:
            info = 0.0
        if strategy is Strategy.NO_UNLOCK:
            unlocking = 0.0
        denominator = 1.0 if strategy is Strategy.NO_COST else cost + 1e-9
        score = (info + contribution + unlocking + exploration - p.risk) / denominator
        if strategy is Strategy.FIFO:
            score = float(-index)
        elif strategy is Strategy.EXISTING:
            score = SchedulingSignals(
                task_id=TaskId(p.hypothesis_id),
                impact=p.contribution,
                unblocking=max(0.05, unlocking),
                information_gain=p.information,
                cost=cost,
                risk=1 + p.risk,
            ).priority
        if info + contribution + unlocking + exploration - p.risk <= 0:
            rejected.append(
                Rejected(proposal_id=p.id, reason="no positive estimated decision value")
            )
            continue
        ranked.append(
            Ranked(
                proposal_id=p.id,
                score=score,
                information=info,
                contribution=contribution,
                unlocking=unlocking,
                exploration=exploration,
                risk=p.risk,
                normalized_cost=cost,
                source=p.estimate_source,
                confidence=p.estimate_confidence,
                uncertainty=p.uncertainty,
                explanation="heuristic decision value; budget-share cost; "
                "independent verification and prerequisites required",
            )
        )
    # One-step budget lookahead: avoid starving a qualified higher-fidelity path.
    # This is a decision preference, not an oracle or a new feasibility restriction.
    if strategy in {Strategy.CARTS, Strategy.NO_INFORMATION, Strategy.NO_UNLOCK}:
        manager = BudgetManager(r)
        progression = []
        for item in ranked:
            p = r.proposal(item.proposal_id)
            node = next(n for n in r.nodes if n.hypothesis_id == p.hypothesis_id)
            if p.fidelity >= Fidelity.F2 and (node.and_ids or p.predecessor_ids):
                progression.append(item)
        endangered = []
        for stage in progression:
            stage_cost = manager.reservation(r.proposal(stage.proposal_id))
            for item in ranked:
                q = r.proposal(item.proposal_id)
                if q.fidelity >= Fidelity.F2:
                    continue
                after = manager.remaining.copy()
                for c in manager.reservation(q):
                    after[c.unit] -= c.amount
                if any(c.amount > after[c.unit] for c in stage_cost):
                    endangered.append(stage.proposal_id)
                    break
        if endangered:
            chosen = max((x for x in ranked if x.proposal_id in endangered), key=lambda x: x.score)
            peak = max(x.score for x in ranked) + 1.0
            ranked = [
                x.model_copy(
                    update={
                        "score": peak,
                        "explanation": x.explanation
                        + "; preserve budget for verified prerequisite follow-through",
                    }
                )
                if x.proposal_id == chosen.proposal_id
                else x
                for x in ranked
            ]
    ranked.sort(key=lambda x: (-x.score, x.proposal_id))
    stop = None
    if goal_met(state):
        stop = "goal_met"
        ranked = []
    elif r.active:
        stop = "in_flight_requires_resume"
        ranked = []  # serial execution; conservative across process restarts
    elif not ranked:
        stop = (
            "budget_exhausted"
            if any("budget" in x.reason for x in rejected)
            else "blocked_or_exhausted"
        )
    return Ranking(
        strategy=strategy, at=at, ranked=tuple(ranked), rejected=tuple(rejected), stop_reason=stop
    )
