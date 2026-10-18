"""Serial research application service on canonical events, tasks and verification."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from time import perf_counter
from uuid import NAMESPACE_URL, UUID, uuid5

from epipilot.core.events import EventType
from epipilot.core.models import EvidenceKind, Task, TaskStatus
from epipilot.events.codec import make_project_event
from epipilot.events.payloads import (
    DecisionMadePayload,
    EventPayload,
    EvidenceRecordedPayload,
    HypothesisCreatedPayload,
    PlanBasisPayload,
    PlanDependencyPayload,
    PlanVersionCreatedPayload,
    TaskCreatedPayload,
    TaskStartedPayload,
    TaskStatusChangedPayload,
    VerificationFailedPayload,
    VerificationPassedPayload,
)
from epipilot.planning.graph import PlanBasisKind
from epipilot.requirements.models import DecisionAuthority
from epipilot.research.budget import BudgetManager
from epipilot.research.events import (
    BudgetSettled,
    ExperimentObserved,
    ExperimentReserved,
    ExperimentReviewed,
    ProposalAdded,
    ResearchConfigured,
    ResearchNodeAdded,
)
from epipilot.research.models import (
    Assessment,
    Cost,
    ExecutionStatus,
    ExperimentProposal,
    Observation,
    Outcome,
    Ranking,
    ResearchGoal,
    ResearchNode,
    Reservation,
    Review,
    Settlement,
    Strategy,
)
from epipilot.research.policy import rank_candidates, validate_graph
from epipilot.research.ports import EvidenceVerifier, ExperimentRunner
from epipilot.research.reducer import reduce_research, task_id
from epipilot.runtime.event_store import AtomicEventStore, EventStore
from epipilot.state.project import ProjectState
from epipilot.state.reducer import reduce_event
from epipilot.state.replay import replay_project


def stable_id(project: str, name: str) -> UUID:
    return uuid5(NAMESPACE_URL, f"epipilot:{project}:{name}")


class ResearchEngine:
    def __init__(self, store: EventStore, project_id: str, output: Path):
        self.store, self.project_id, self.output = store, project_id, output
        self._state = replay_project(project_id, store.load(project_id))

    @property
    def state(self) -> ProjectState:
        return self._state

    def append(self, kind: EventType, payload: EventPayload) -> None:
        event = make_project_event(kind, self.project_id, payload)
        updated = reduce_event(self._state, event)  # validate before durable append
        self.store.append(event, expected_version=self._state.event_version)
        self._state = updated

    def append_batch(self, entries: tuple[tuple[EventType, EventPayload], ...]) -> None:
        if not isinstance(self.store, AtomicEventStore):
            raise ValueError("discovery requires an atomic canonical EventStore")
        updated = self.state
        events = tuple(
            make_project_event(kind, self.project_id, payload) for kind, payload in entries
        )
        for event in events:
            updated = reduce_event(updated, event)
        self.store.append_many(events, expected_version=self.state.event_version)
        self._state = updated

    def initialize(
        self,
        goal: ResearchGoal,
        nodes: tuple[ResearchNode, ...],
        proposals: tuple[ExperimentProposal, ...],
    ) -> None:
        validate_graph(nodes)
        decision_id = stable_id(self.project_id, "carts-authority")
        if decision_id not in {x.id for x in self.state.decisions}:
            self.append(
                EventType.DECISION_MADE,
                DecisionMadePayload(
                    decision_id=decision_id,
                    question="How should research experiments be selected?",
                    choice="Cost-aware, independently verified research DAG",
                    authority=DecisionAuthority.USER,
                    rationale="User's CARTS specification; source metadata: "
                    + "; ".join(n.basis for n in nodes),
                ),
            )
        for node in nodes:
            if node.hypothesis_id not in {x.id for x in self.state.hypotheses}:
                self.append(
                    EventType.HYPOTHESIS_CREATED,
                    HypothesisCreatedPayload(
                        hypothesis_id=node.hypothesis_id,
                        statement=node.label,
                        predictions=(
                            "preregistered observation rule supports the stated mechanism",
                        ),
                        falsification_conditions=(
                            "preregistered definitive counterexample under qualified inputs",
                        ),
                    ),
                )
        if self.state.research is None:
            self.append(
                EventType.RESEARCH_CONFIGURED,
                ResearchConfigured(
                    goal=goal,
                    nodes=tuple(n.model_copy(update={"basis": str(decision_id)}) for n in nodes),
                ),
            )
        elif self.state.research.goal != goal or not {n.hypothesis_id for n in nodes} <= {
            n.hypothesis_id for n in self.state.research.nodes
        }:
            raise ValueError("cannot silently overwrite existing research configuration")
        for p in proposals:
            if self.state.research and p.id in {x.id for x in self.state.research.proposals}:
                if self.state.research.proposal(p.id) != p:
                    raise ValueError("cannot change existing proposal")
                continue
            self.propose(p, str(decision_id))
        self._ensure_plan(str(decision_id))

    def add_hypothesis(
        self,
        node: ResearchNode,
        predictions: tuple[str, ...],
        falsification_conditions: tuple[str, ...],
    ) -> None:
        from epipilot.research.reducer import canonical_basis

        if self.state.research is None or node.basis not in canonical_basis(self.state):
            raise ValueError("new hypothesis requires configured research and canonical basis")
        validate_graph((*self.state.research.nodes, node))
        if not predictions or not falsification_conditions:
            raise ValueError("research hypothesis must be falsifiable")
        if node.hypothesis_id not in {h.id for h in self.state.hypotheses}:
            self.append(
                EventType.HYPOTHESIS_CREATED,
                HypothesisCreatedPayload(
                    hypothesis_id=node.hypothesis_id,
                    statement=node.label,
                    predictions=predictions,
                    falsification_conditions=falsification_conditions,
                ),
            )
        self.append(EventType.RESEARCH_NODE_ADDED, ResearchNodeAdded(node=node))

    def propose(self, proposal: ExperimentProposal, basis_ref: str) -> None:
        tid = task_id(self.project_id, proposal.id)
        reduce_research(
            replace(self.state, tasks=(*self.state.tasks, Task(tid, proposal.title)))
            if tid not in {x.id for x in self.state.tasks}
            else self.state,
            ProposalAdded(proposal=proposal, basis_ref=basis_ref),
        )
        if tid not in {x.id for x in self.state.tasks}:
            self.append(
                EventType.TASK_CREATED, TaskCreatedPayload(task_id=tid, objective=proposal.title)
            )
        if self.state.task(tid).status is TaskStatus.PROPOSED:
            self.append(
                EventType.TASK_STATUS_CHANGED,
                TaskStatusChangedPayload(task_id=tid, status=TaskStatus.READY),
            )
        self.append(EventType.PROPOSAL_ADDED, ProposalAdded(proposal=proposal, basis_ref=basis_ref))
        self._ensure_plan(basis_ref)

    def _ensure_plan(self, basis_ref: str) -> None:
        previous = self.state.current_plan
        if previous and {t.id for t in self.state.tasks} <= {t.id for t in previous.tasks}:
            return
        kind = (
            PlanBasisKind.DECISION
            if basis_ref in {str(d.id) for d in self.state.decisions}
            else (
                PlanBasisKind.REQUIREMENT
                if basis_ref in {str(r.id) for r in self.state.requirements}
                else PlanBasisKind.EVIDENCE
            )
        )
        self.append(
            EventType.PLAN_VERSION_CREATED,
            PlanVersionCreatedPayload(
                version=len(self.state.plans) + 1,
                task_ids=tuple(t.id for t in self.state.tasks),
                dependencies=tuple(
                    PlanDependencyPayload(predecessor=d.predecessor, successor=d.successor)
                    for d in previous.dependencies
                )
                if previous
                else (),
                basis=(PlanBasisPayload(kind=kind, reference_id=basis_ref),),
            ),
        )

    def ranking(self, strategy: Strategy = Strategy.CARTS, at: datetime | None = None) -> Ranking:
        return rank_candidates(self.state, at or datetime.now(UTC), strategy)

    def budget(self) -> dict[str, dict[str, float]]:
        if self.state.research is None:
            raise ValueError("research not configured")
        manager = BudgetManager(self.state.research)
        return {"spent": manager.spent, "remaining": manager.remaining}

    def reserve_next(self, strategy: Strategy = Strategy.CARTS) -> ExperimentProposal | None:
        ranking = self.ranking(strategy)
        if not ranking.ranked:
            return None
        assert self.state.research is not None
        best = ranking.ranked[0]
        p = self.state.research.proposal(best.proposal_id)
        return self.reserve(p.id, best.model_dump_json(), ranking.at)

    def reserve(
        self, identifier: str, explanation: str, at: datetime | None = None
    ) -> ExperimentProposal:
        assert self.state.research is not None
        p = self.state.research.proposal(identifier)
        self.append(
            EventType.EXPERIMENT_RESERVED,
            ExperimentReserved(
                reservation=Reservation(
                    proposal_id=p.id,
                    costs=BudgetManager(self.state.research).reservation(p),
                    at=at or datetime.now(UTC),
                    score_explanation=explanation,
                )
            ),
        )
        return p

    def _start(self, p: ExperimentProposal) -> None:
        tid = task_id(self.project_id, p.id)
        if self.state.task(tid).status is TaskStatus.READY:
            if not any(s.task_id == tid for s in self.state.sessions):
                self.append(
                    EventType.TASK_STARTED,
                    TaskStartedPayload(task_id=tid, session_id=f"carts:{p.id}"),
                )
            self.append(
                EventType.TASK_STATUS_CHANGED,
                TaskStatusChangedPayload(task_id=tid, status=TaskStatus.RUNNING),
            )

    def observe(self, observation: Observation) -> None:
        assert self.state.research is not None
        self._start(self.state.research.proposal(observation.proposal_id))
        self.append(EventType.EXPERIMENT_OBSERVED, ExperimentObserved(observation=observation))

    def run_next(
        self,
        runner: ExperimentRunner,
        verifier: EvidenceVerifier,
        strategy: Strategy = Strategy.CARTS,
    ) -> Observation | None:
        p = self.reserve_next(strategy)
        if p is None:
            return None
        self._start(p)
        self.output.mkdir(parents=True, exist_ok=True)
        # IDs are not paths; use stable UUID names, never a shell or arbitrary path input.
        path = self.output / f"{task_id(self.project_id, p.id)}.json"
        if path.exists():
            raise ValueError("existing artifact needs explicit recovery; refusing overwrite")
        observation = runner.run(p, path)
        if observation.proposal_id != p.id:
            raise ValueError("runner returned another experiment's observation")
        self.observe(observation)
        self.resume(verifier)
        return observation

    def resume(self, verifier: EvidenceVerifier) -> None:
        r = self.state.research
        if r is None:
            raise ValueError("research not configured")
        for reservation in r.active:
            o = r.observation(reservation.proposal_id)
            if o is None:
                raise ValueError(
                    "unrecorded in-flight execution: recover observation or cancel "
                    "with actual costs"
                )
            p = r.proposal(o.proposal_id)
            review = r.review(p.id)
            if review is None:
                start = perf_counter()
                a = verifier.verify(p, o)
                verification_cost = (
                    (Cost(unit="wall_seconds", amount=perf_counter() - start),)
                    if any(c.unit == "wall_seconds" for c in o.actual_cost)
                    else ()
                )
                self._finish(p, o, a, verification_cost)
            self._settle(o)

    def _finish(
        self,
        p: ExperimentProposal,
        o: Observation,
        a: Assessment,
        verification_cost: tuple[Cost, ...] = (),
    ) -> None:
        if a.proposal_id != p.id:
            raise ValueError("verifier returned another experiment's assessment")
        self._start(p)
        projected = self.state
        entries: list[tuple[EventType, EventPayload]] = []

        def emit(kind: EventType, payload: EventPayload) -> None:
            nonlocal projected
            projected = reduce_event(projected, make_project_event(kind, self.project_id, payload))
            entries.append((kind, payload))

        tid = task_id(self.project_id, p.id)
        for previous, target in (
            (TaskStatus.RUNNING, TaskStatus.AGENT_REPORTED_DONE),
            (TaskStatus.AGENT_REPORTED_DONE, TaskStatus.VERIFYING),
        ):
            if projected.task(tid).status is previous:
                emit(
                    EventType.TASK_STATUS_CHANGED,
                    TaskStatusChangedPayload(task_id=tid, status=target),
                )
        eid = stable_id(self.project_id, f"evidence:{p.id}")
        if eid not in {e.id for e in projected.evidence}:
            emit(
                EventType.EVIDENCE_RECORDED,
                EvidenceRecordedPayload(
                    evidence_id=eid,
                    kind=EvidenceKind.DETERMINISTIC_CHECK
                    if a.verified
                    else EvidenceKind.EXECUTOR_REPORT,
                    summary="; ".join(a.reasons),
                    provenance_source=o.artifact or "no artifact",
                    provenance_scope=a.scope.value,
                    provenance_created_at=o.recorded_at,
                    independently_verified=a.verified,
                    task_id=tid,
                ),
            )
        if projected.task(tid).status is TaskStatus.VERIFYING:
            if not any(v.task_id == tid for v in projected.verifications):
                if a.verified:
                    emit(
                        EventType.VERIFICATION_PASSED,
                        VerificationPassedPayload(task_id=tid, evidence_id=eid),
                    )
                else:
                    emit(
                        EventType.VERIFICATION_FAILED,
                        VerificationFailedPayload(task_id=tid, reason="; ".join(a.reasons)),
                    )
            success = a.verified and o.execution is ExecutionStatus.SUCCEEDED
            target = (
                TaskStatus.PASSED
                if success
                else (
                    TaskStatus.BLOCKED
                    if o.execution is ExecutionStatus.BLOCKED
                    else TaskStatus.FAILED
                )
            )
            emit(
                EventType.TASK_STATUS_CHANGED,
                TaskStatusChangedPayload(
                    task_id=tid, status=target, completion_evidence_id=eid if success else None
                ),
            )
        emit(
            EventType.EXPERIMENT_REVIEWED,
            ExperimentReviewed(
                review=Review(assessment=a, evidence_id=eid, verification_cost=verification_cost)
            ),
        )

        self.append_batch(tuple(entries))

    def _settle(self, observation: Observation) -> None:
        from epipilot.research.models import amounts

        assert self.state.research is not None
        review = self.state.research.review(observation.proposal_id)
        assert review is not None
        actual = amounts(observation.actual_cost)
        for c in review.verification_cost:
            actual[c.unit] += c.amount
        self.append(
            EventType.BUDGET_SETTLED,
            BudgetSettled(
                settlement=Settlement(
                    proposal_id=observation.proposal_id,
                    actual_cost=tuple(Cost(unit=k, amount=v) for k, v in actual.items()),
                )
            ),
        )

    def cancel(self, identifier: str, actual_cost: tuple[Cost, ...], reason: str) -> None:
        r = self.state.research
        if (
            r is None
            or identifier not in {x.proposal_id for x in r.active}
            or r.observation(identifier)
        ):
            raise ValueError("cancellation requires an active unrecorded run")
        p = r.proposal(identifier)
        o = Observation(
            proposal_id=identifier,
            execution=ExecutionStatus.CANCELLED,
            actual_cost=actual_cost,
            artifact="operator cancellation",
            summary=reason,
            recorded_at=datetime.now(UTC),
        )
        self.observe(o)
        self._finish(
            p,
            o,
            Assessment(
                proposal_id=identifier,
                verified=False,
                outcome=Outcome.INVALID,
                reliability=0.0,
                scope=p.scope,
                reasons=(reason,),
            ),
        )
        self._settle(o)
