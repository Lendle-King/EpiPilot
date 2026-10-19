"""Research projection and canonical hypothesis updates, purely from typed events."""

from dataclasses import replace
from uuid import NAMESPACE_URL, uuid5

from epipilot.core.models import EvidenceId, TaskId
from epipilot.epistemics.models import HypothesisStatus
from epipilot.events.payloads import EventPayload
from epipilot.research.budget import BudgetManager
from epipilot.research.events import (
    BudgetSettled,
    ExperimentObserved,
    ExperimentReserved,
    ExperimentReviewed,
    ProposalAdded,
    ResearchBudgetExtended,
    ResearchConfigured,
    ResearchNodeAdded,
)
from epipilot.research.frontier import Quality
from epipilot.research.models import (
    ExecutionStatus,
    Fidelity,
    Observation,
    Outcome,
    ResearchState,
    amounts,
)
from epipilot.research.policy import validate_graph
from epipilot.state.project import ProjectState


def task_id(project_id: str, proposal_id: str) -> TaskId:
    return TaskId(uuid5(NAMESPACE_URL, f"epipilot:{project_id}:experiment:{proposal_id}"))


def reduce_research(state: ProjectState, payload: EventPayload) -> ProjectState:
    o: Observation | None
    r = state.research
    if isinstance(payload, ResearchConfigured):
        if r is not None:
            raise ValueError("research already configured")
        validate_graph(payload.nodes)
        if not {n.hypothesis_id for n in payload.nodes} <= {h.id for h in state.hypotheses}:
            raise ValueError("research nodes require canonical hypotheses")
        if not all(n.basis in canonical_basis(state) for n in payload.nodes):
            raise ValueError("research nodes require canonical provenance")
        return replace(state, research=ResearchState(goal=payload.goal, nodes=payload.nodes))
    if r is None:
        raise ValueError("research not configured")
    if isinstance(payload, ResearchBudgetExtended):
        decision = next(
            (item for item in state.decisions if str(item.id) == payload.basis_ref), None
        )
        if decision is None or decision.authority.value != "user":
            raise ValueError("budget extension requires a canonical user decision")
        if not payload.additions or not set(amounts(payload.additions)) <= set(
            amounts(r.goal.budgets)
        ):
            raise ValueError("budget extension must add an existing budget unit")
        if any(cost.amount <= 0 for cost in payload.additions):
            raise ValueError("budget extension amounts must be positive")
        return replace(
            state,
            research=r.model_copy(
                update={"budget_additions": (*r.budget_additions, *payload.additions)}
            ),
        )
    if isinstance(payload, ResearchNodeAdded):
        nodes = (payload.node, *payload.companion_nodes)
        if any(
            node.hypothesis_id not in {h.id for h in state.hypotheses}
            or node.basis not in canonical_basis(state)
            for node in nodes
        ):
            raise ValueError("new research nodes require canonical hypotheses and basis")
        validate_graph((*r.nodes, *nodes))
        return replace(state, research=r.model_copy(update={"nodes": (*r.nodes, *nodes)}))
    if isinstance(payload, ProposalAdded):
        p = payload.proposal
        if p.id in {x.id for x in r.proposals}:
            raise ValueError("duplicate proposal id; no unchanged retries")
        if p.hypothesis_id not in {n.hypothesis_id for n in r.nodes}:
            raise ValueError("proposal hypothesis unknown")
        if not set(p.unknown_ids) <= {u.id for u in state.unknowns}:
            raise ValueError("proposal unknown references unknown")
        if payload.basis_ref not in canonical_basis(state):
            raise ValueError("proposal requires canonical basis")
        if any(x not in {old.id for old in r.proposals} for x in p.predecessor_ids):
            raise ValueError("fidelity predecessor must already exist")
        if any(
            old.hypothesis_id == p.hypothesis_id
            and old.input_revision == p.input_revision
            and old.kind == p.kind
            and old.fidelity == p.fidelity
            for old in r.proposals
        ):
            raise ValueError("unchanged experiment already proposed; reuse result")
        state.task(task_id(state.project_id, p.id))
        return replace(state, research=r.model_copy(update={"proposals": (*r.proposals, p)}))
    if isinstance(payload, ExperimentReserved):
        entry = payload.reservation
        from epipilot.research.policy import rank_candidates

        ranking = rank_candidates(state, entry.at)
        if entry.proposal_id not in {x.proposal_id for x in ranking.ranked}:
            raise ValueError("cannot reserve infeasible or submitted experiment")
        expected = BudgetManager(r).reservation(r.proposal(entry.proposal_id))
        if expected != entry.costs:
            raise ValueError("reservation differs from conservative cost estimate")
        if not entry.score_explanation:
            raise ValueError("reservation requires decision explanation")
        return replace(
            state, research=r.model_copy(update={"reservations": (*r.reservations, entry)})
        )
    if isinstance(payload, ExperimentObserved):
        o = payload.observation
        if o.proposal_id not in {a.proposal_id for a in r.active} or r.observation(o.proposal_id):
            raise ValueError("observation requires an active reservation and unique run")
        p = r.proposal(o.proposal_id)
        reserved = next(x for x in r.active if x.proposal_id == o.proposal_id)
        if o.recorded_at < reserved.at or (p.available_at and o.recorded_at < p.available_at):
            raise ValueError("observation predates reservation or available input")
        if set(amounts(o.actual_cost)) != set(amounts(p.estimated_cost)):
            raise ValueError("actual costs must measure every reserved unit")
        return replace(state, research=r.model_copy(update={"observations": (*r.observations, o)}))
    if isinstance(payload, ExperimentReviewed):
        review = payload.review
        a = review.assessment
        o = r.observation(a.proposal_id)
        if o is None or r.review(a.proposal_id):
            raise ValueError("review needs unique persisted observation")
        evidence = state.evidence_item(EvidenceId(review.evidence_id))
        tid = task_id(state.project_id, a.proposal_id)
        if not any(
            link.evidence_id == evidence.id and link.task_id == tid for link in state.evidence_links
        ):
            raise ValueError("review evidence must belong to experiment task")
        if a.verified != evidence.independently_verified:
            raise ValueError("assessment conflicts with canonical evidence verification")
        if a.verified and not any(
            v.task_id == tid and v.passed and v.evidence_id == evidence.id
            for v in state.verifications
        ):
            raise ValueError("review requires canonical independent verification")
        p = r.proposal(a.proposal_id)
        if a.scope != p.scope:
            raise ValueError("evidence scope cannot be upgraded")
        reliable = (
            a.verified and a.reliability >= p.minimum_reliability and o.samples >= p.minimum_samples
        )
        if r.frontier is not None:
            reliable = reliable and all(
                a.quality >= max(q.minimum_quality, Quality.MEASUREMENT)
                for q in r.frontier.questions
                if q.unknown_id in p.unknown_ids
            )
        if a.qualified_for_promotion and not (
            reliable
            and a.outcome is Outcome.SUPPORT
            and p.fidelity == Fidelity.F4
            and a.scope == r.goal.required_scope
            and p.data_qualified
            and o.execution is ExecutionStatus.SUCCEEDED
        ):
            raise ValueError("promotion requires qualified independent F4 evidence")
        status: HypothesisStatus | None = None
        if a.verified and a.outcome is Outcome.BLOCKED:
            status = HypothesisStatus.BLOCKED
        elif o.execution is ExecutionStatus.SUCCEEDED and a.verified:
            if a.outcome is Outcome.SUPPORT and reliable:
                status = HypothesisStatus.SUPPORTED
            elif a.outcome is Outcome.REFUTE and reliable and p.definitive_negative:
                status = HypothesisStatus.REFUTED
            else:
                status = HypothesisStatus.INCONCLUSIVE
        hypotheses = state.hypotheses
        if status is not None:
            hypotheses = tuple(
                replace(
                    h,
                    status=status,
                    supporting_evidence=(*h.supporting_evidence, evidence.id)
                    if status is HypothesisStatus.SUPPORTED
                    else h.supporting_evidence,
                    contradicting_evidence=(*h.contradicting_evidence, evidence.id)
                    if status is HypothesisStatus.REFUTED
                    else h.contradicting_evidence,
                )
                if h.id == p.hypothesis_id
                else h
                for h in hypotheses
            )
        return replace(
            state,
            hypotheses=hypotheses,
            research=r.model_copy(update={"reviews": (*r.reviews, review)}),
        )
    if isinstance(payload, BudgetSettled):
        s = payload.settlement
        o = r.observation(s.proposal_id)
        if o is None or r.review(s.proposal_id) is None:
            raise ValueError("settlement requires observation and review")
        if s.proposal_id in {x.proposal_id for x in r.settlements}:
            raise ValueError("experiment already settled")
        settled_review = r.review(s.proposal_id)
        assert settled_review is not None
        actual = amounts(o.actual_cost)
        for c in settled_review.verification_cost:
            if c.unit not in actual:
                raise ValueError("verification cost unit not budgeted")
            actual[c.unit] += c.amount
        if amounts(s.actual_cost) != actual:
            raise ValueError("settlement must equal measured costs (including overruns)")
        return replace(state, research=r.model_copy(update={"settlements": (*r.settlements, s)}))
    from epipilot.research.frontier_reducer import reduce_frontier

    return reduce_frontier(state, payload)


def canonical_basis(state: ProjectState) -> set[str]:
    return (
        {str(x.id) for x in state.decisions}
        | {str(e.id) for e in state.evidence}
        | {str(r.id) for r in state.requirements}
    )
