"""Bounded host-driven opportunity discovery over existing canonical CARTS."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Protocol, TypedDict

from epipilot.core.events import EventType
from epipilot.research.budget import BudgetManager
from epipilot.research.discovery import DiscoveryBatch, discovery_entries, git
from epipilot.research.engine import ResearchEngine
from epipilot.research.frontier import FrontierValue
from epipilot.research.intelligence_models import (
    CandidateDisposition,
    DecisionEstimate,
    OpportunityCandidate,
    ScoutCompleted,
    ScoutRequest,
    ScoutResultStatus,
    ScoutStarted,
)
from epipilot.research.models import Cost, amounts
from epipilot.state.project import ProjectState


class CompletionIdentity(TypedDict):
    operation_id: str
    request_version: int
    actual_cost: tuple[Cost, ...]
    submission_hash: str


class ScoutSubmission(FrontierValue):
    operation_id: str
    batch: DiscoveryBatch
    actual_cost: tuple[Cost, ...]
    candidates: tuple[OpportunityCandidate, ...] = ()
    estimates: tuple[DecisionEstimate, ...] = ()
    queries: tuple[str, ...] = ()


class ScoutProvider(Protocol):
    def propose(self, request: ScoutRequest) -> ScoutSubmission: ...


def research_revision(state: ProjectState, root: Path) -> str:
    integrated = [
        e.work.integrated_commit for e in state.engineering if e.work and e.work.integrated_commit
    ]
    return integrated[-1] if integrated else git(root, "rev-parse", "HEAD")


def research_epoch(state: ProjectState, revision: str) -> str:
    r, i = state.research, state.intelligence
    if r is None or i is None:
        raise ValueError("configured research policy required")
    data = {
        "revision": revision,
        "policy": i.policy.id,
        "reviews": [str(v.evidence_id) for v in r.reviews],
        "decisions": [str(d.id) for d in state.decisions],
        "requirements": [str(v.id) for v in state.requirements],
    }
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()


class ScoutService:
    def __init__(self, engine: ResearchEngine, root: Path):
        self.engine, self.root = engine, root.resolve()

    def prepare(self, operation_id: str) -> ScoutRequest:
        state = self.engine.state
        r, intel = state.research, state.intelligence
        if r is None or r.frontier is None or intel is None:
            raise ValueError("explicit canonical frontier and research policy required")
        if str(self.root) != r.frontier.repository:
            raise ValueError("Scout canonical root mismatch")
        old = next(
            (s.request for s in intel.starts if s.request.operation_id == operation_id), None
        )
        if old is not None:
            return old
        policy = intel.policy
        manager = BudgetManager(r)
        revision = research_revision(state, self.root)
        # Initial discovery is local; later cumulative synthesis requires actual external reading.
        reviews = len(r.reviews)
        last_external_review = max(
            (
                next(
                    s.request.review_count
                    for s in intel.starts
                    if s.request.operation_id == c.operation_id
                )
                for c in intel.completions
                if c.external_search_complete
            ),
            default=0,
        )
        required = (
            reviews // policy.external_review_interval
            > last_external_review // policy.external_review_interval
        )
        request = ScoutRequest(
            operation_id=operation_id,
            based_on_version=state.event_version + 1,
            git_revision=revision,
            canonical_root=str(self.root),
            epoch=research_epoch(state, revision),
            policy_id=policy.id,
            goal=r.goal.statement,
            authority_refs=(policy.basis_ref,),
            question_ids=tuple(str(q.id) for q in state.unknowns),
            evidence_ids=tuple(str(v.evidence_id) for v in r.reviews[-12:]),
            proposal_ids=tuple(p.id for p in r.proposals),
            reserved_cost=policy.scout_cost,
            remaining_budget=tuple(
                Cost(unit=k, amount=max(0.0, v)) for k, v in manager.remaining.items()
            ),
            max_queries=policy.max_queries,
            max_sources=policy.max_sources,
            max_candidates=policy.max_candidates,
            external_search_required=required,
            review_count=reviews,
            instructions=(
                (
                    "Read pinned Git objects and canonical references; "
                    "claims remain document observations."
                ),
                (
                    "Inspect contradictions, objective gaps, new mechanisms "
                    "and changed failure strategies."
                ),
                (
                    "Return bounded candidates plus existing DiscoveryBatch "
                    "and coherent decision tables."
                ),
                "Use public generic external queries; never disclose private research text.",
                "Record actual costs, source access and failed leads; missing cost is not zero.",
                (
                    "No-new-finding results are valid when justified; "
                    "no scientific promotion from source quotes."
                ),
            ),
        )
        self.engine.append(EventType.SCOUT_STARTED, ScoutStarted(request=request))
        return request

    def submit(self, submission: ScoutSubmission) -> ScoutCompleted:
        state = self.engine.state
        intel = state.intelligence
        if intel is None:
            raise ValueError("research policy required")
        digest = hashlib.sha256(submission.model_dump_json().encode()).hexdigest()
        old = next(
            (s for s in intel.completions if s.operation_id == submission.operation_id), None
        )
        if old is not None:
            if old.submission_hash != digest:
                raise ValueError("completed submission content cannot change")
            return old
        request = next(
            (s.request for s in intel.active if s.request.operation_id == submission.operation_id),
            None,
        )
        if request is None:
            raise ValueError("prepare Scout request before submit")
        if set(amounts(submission.actual_cost)) != set(amounts(request.reserved_cost)):
            raise ValueError("actual cost for every reserved unit required; recover unknown usage")
        base: CompletionIdentity = {
            "operation_id": request.operation_id,
            "request_version": request.based_on_version,
            "actual_cost": submission.actual_cost,
            "submission_hash": digest,
        }
        stale = (
            state.event_version != request.based_on_version
            or research_revision(state, self.root) != request.git_revision
        )
        if stale:
            completion = ScoutCompleted(
                **base,
                status=ScoutResultStatus.STALE,
                reason="State/revision changed; re-inspect and submit a new epoch",
            )
            self.engine.append(EventType.SCOUT_COMPLETED, completion)
            return completion
        try:
            self._validate(request, submission)
            entries = discovery_entries(
                self.engine, self.root, submission.batch, allow_empty_refresh=True
            )
            external_done = bool(
                submission.queries
                and any(
                    s.access_level in {"full_text", "sections", "abstract"}
                    for s in submission.batch.external_sources
                )
            )
            completion = ScoutCompleted(
                **base,
                status=ScoutResultStatus.ACCEPTED,
                reason="Validated bounded discovery"
                if not request.external_search_required or external_done
                else "Local discovery accepted; required external component remains incomplete",
                candidates=submission.candidates,
                estimates=submission.estimates,
                sources=submission.batch.external_sources,
                queries=submission.queries,
                discovery_id=submission.batch.id,
                external_search_complete=external_done,
            )
            self.engine.append_batch((*entries, (EventType.SCOUT_COMPLETED, completion)))
            return completion
        except (ValueError, OSError, subprocess.CalledProcessError):
            # Preserve genuine overhead without admitting any part of an invalid batch.
            # Never persist raw external responses, model traces or sensitive exception text.
            completion = ScoutCompleted(
                **base,
                status=ScoutResultStatus.INVALID,
                reason="Source, bounds, decision table or canonical discovery validation failed",
            )
            self.engine.append(EventType.SCOUT_COMPLETED, completion)
            return completion

    def _validate(self, request: ScoutRequest, submission: ScoutSubmission) -> None:
        batch = submission.batch
        if (
            batch.based_on_version != request.based_on_version
            or batch.basis_ref not in request.authority_refs
        ):
            raise ValueError("Scout batch version/authority mismatch")
        if (
            max(
                len(submission.candidates),
                len(submission.estimates),
                len(batch.findings),
                len(batch.proposals),
            )
            > request.max_candidates
        ):
            raise ValueError("candidate bound exceeded")
        if (
            len(submission.queries) > request.max_queries
            or len(batch.sources) + len(batch.external_sources) > request.max_sources
        ):
            raise ValueError("source/query bound exceeded")
        source_ids = {f"git:{s.revision}:{s.path}:{s.line}" for s in batch.sources} | {
            s.id for s in batch.external_sources
        }
        accepted = [
            c for c in submission.candidates if c.disposition is CandidateDisposition.ACCEPTED
        ]
        if batch.findings and {c.topic_key for c in accepted} != {
            f.topic_key for f in batch.findings
        }:
            raise ValueError("accepted candidates must cover canonical findings")
        for c in accepted:
            if not set(c.source_refs) <= source_ids:
                raise ValueError("candidate references unavailable sources")
            f = next((f for f in batch.findings if f.topic_key == c.topic_key), None)
            if f and (
                c.question != f.unknown.question or set(c.alternatives) != set(f.alternatives)
            ):
                raise ValueError("candidate question differs from discovery")
        known = set(request.proposal_ids) | {p.proposal.id for p in batch.proposals}
        if any(e.proposal_id not in known for e in submission.estimates):
            raise ValueError("decision estimate proposal missing")
        if any(
            o.proposal_id is not None and o.proposal_id not in known
            for e in submission.estimates
            for o in e.options
        ):
            raise ValueError("downstream decision proposal missing")
