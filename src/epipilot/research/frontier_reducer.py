"""Pure frontier projection and scientific resolution gates."""

from dataclasses import replace

from epipilot.core.models import EvidenceId
from epipilot.events.payloads import EventPayload
from epipilot.research.frontier import (
    FrontierDiscovered,
    FrontierInitialized,
    Quality,
    QuestionResolved,
    ResearchFrontier,
    Resolution,
)
from epipilot.research.models import ExecutionStatus, Outcome
from epipilot.research.reducer import canonical_basis
from epipilot.state.project import ProjectState


def reduce_frontier(state: ProjectState, payload: EventPayload) -> ProjectState:
    r = state.research
    if r is None:
        raise ValueError("research not configured")
    f = r.frontier
    if isinstance(payload, FrontierInitialized):
        if f is not None or payload.basis_ref not in canonical_basis(state):
            raise ValueError("frontier already exists or lacks canonical authority")
        return replace(
            state,
            research=r.model_copy(
                update={
                    "frontier": ResearchFrontier(
                        repository=payload.repository,
                        exploration_fraction=payload.exploration_fraction,
                    )
                }
            ),
        )
    if f is None:
        raise ValueError("agent frontier not enabled")
    if isinstance(payload, FrontierDiscovered):
        if payload.audit.id in {d.id for d in f.discoveries}:
            raise ValueError("duplicate discovery")
        if payload.audit.review_count != len(r.reviews):
            raise ValueError("discovery review epoch differs")
        for eid in payload.audit.source_evidence_ids:
            if not state.evidence_item(EvidenceId(eid)).independently_verified:
                raise ValueError("discovery source quote not verified")
        ids = {q.unknown_id for q in f.questions}
        keys = {q.topic_key for q in f.questions}
        for q in payload.questions:
            if q.minimum_quality < Quality.MEASUREMENT:
                raise ValueError("scientific unknown requires at least measurement quality")
            if q.unknown_id in ids or q.topic_key in keys:
                raise ValueError("duplicate unknown/topic; reopen rather than rename")
            if q.unknown_id not in {u.id for u in state.unknowns} or not set(q.hypothesis_ids) <= {
                h.id for h in state.hypotheses
            }:
                raise ValueError("frontier references missing canonical entity")
            ids.add(q.unknown_id)
            keys.add(q.topic_key)
        contract_ids = {c.proposal_id for c in f.contracts}
        signatures = {c.fingerprint() for c in f.contracts}
        questions = (*f.questions, *payload.questions)
        for c in payload.contracts:
            p = r.proposal(c.proposal_id)
            if c.proposal_id in contract_ids or c.fingerprint() in signatures:
                raise ValueError("duplicate experiment contract; changed strategy/inputs required")
            if p.input_revision != c.fingerprint():
                raise ValueError("experiment inputs and contract fingerprint differ")
            if not p.unknown_ids or any(
                uid not in {q.unknown_id for q in questions} for uid in p.unknown_ids
            ):
                raise ValueError("agent experiment must target registered scientific unknowns")
            if any(
                p.hypothesis_id not in q.hypothesis_ids or p.scope.value != q.evidence_scope
                for q in questions
                if q.unknown_id in p.unknown_ids
            ):
                raise ValueError("experiment scope/hypothesis unrelated to question")
            contract_ids.add(c.proposal_id)
            signatures.add(c.fingerprint())
        updated = f.model_copy(
            update={
                "questions": questions,
                "contracts": (*f.contracts, *payload.contracts),
                "discoveries": (*f.discoveries, payload.audit),
            }
        )
        return replace(state, research=r.model_copy(update={"frontier": updated}))
    if isinstance(payload, QuestionResolved):
        validate_resolution(state, payload.resolution)
        resolution = payload.resolution
        updated = f.model_copy(update={"resolutions": (*f.resolutions, resolution)})
        return replace(state, research=r.model_copy(update={"frontier": updated}))
    raise ValueError("unsupported research event")


def validate_resolution(state: ProjectState, resolution: Resolution) -> None:
    r = state.research
    if r is None or r.frontier is None:
        raise ValueError("agent frontier not enabled")
    f = r.frontier
    question = next((q for q in f.questions if q.unknown_id == resolution.unknown_id), None)
    review = next((v for v in r.reviews if v.evidence_id == resolution.evidence_id), None)
    if question is None or review is None:
        raise ValueError("resolution requires canonical question and experimental evidence")
    a = review.assessment
    p = r.proposal(a.proposal_id)
    o = r.observation(p.id)
    if (
        resolution.unknown_id not in p.unknown_ids
        or p.hypothesis_id not in question.hypothesis_ids
        or not a.verified
        or a.quality
        < max(
            question.minimum_quality,
            Quality.INDEPENDENT
            if question.evidence_scope in {"independent", "prospective"}
            else Quality.MEASUREMENT,
        )
        or a.scope.value != question.evidence_scope
        or o is None
        or o.execution is not ExecutionStatus.SUCCEEDED
        or o.samples < p.minimum_samples
        or a.reliability < p.minimum_reliability
        or a.outcome not in {Outcome.SUPPORT, Outcome.REFUTE}
        or (a.outcome is Outcome.REFUTE and not p.definitive_negative)
    ):
        raise ValueError("evidence is unrelated, inconclusive, failed or below scientific quality")
    if resolution.resolved == f.closed(question.unknown_id):
        raise ValueError("question already in requested resolution state")
    latest_resolution = next(
        (x for x in reversed(f.resolutions) if x.unknown_id == question.unknown_id), None
    )
    if latest_resolution is not None:
        previous_index = next(
            i for i, v in enumerate(r.reviews) if v.evidence_id == latest_resolution.evidence_id
        )
        current_index = next(
            i for i, v in enumerate(r.reviews) if v.evidence_id == resolution.evidence_id
        )
        if current_index <= previous_index:
            raise ValueError("resolution changes require new evidence after the last resolution")
    if resolution.resolved:
        from epipilot.epistemics.models import HypothesisStatus
        from epipilot.research.policy import prerequisite_supported

        expected = (
            HypothesisStatus.SUPPORTED if a.outcome is Outcome.SUPPORT else HypothesisStatus.REFUTED
        )
        hypothesis = next(h for h in state.hypotheses if h.id == p.hypothesis_id)
        node = next(n for n in r.nodes if n.hypothesis_id == p.hypothesis_id)
        if (
            hypothesis.status is not expected
            or not all(prerequisite_supported(state, hid) for hid in node.and_ids)
            or (node.or_ids and not any(prerequisite_supported(state, hid) for hid in node.or_ids))
        ):
            raise ValueError("resolution lacks currently valid hypothesis/prerequisites")
    if not resolution.resolved:
        previous = next(
            x for x in reversed(f.resolutions) if x.unknown_id == question.unknown_id and x.resolved
        )
        old = next(v for v in r.reviews if v.evidence_id == previous.evidence_id)
        if previous.evidence_id == resolution.evidence_id or old.assessment.outcome == a.outcome:
            raise ValueError("reopening requires new counterevidence")
