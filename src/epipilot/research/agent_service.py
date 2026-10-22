"""Agent-native loop and revisable frontier knowledge over canonical CARTS."""

from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from uuid import UUID

from epipilot.core.events import EventType
from epipilot.engineering.models import PublicationDue, WorkLease
from epipilot.engineering.service import ManagedWork
from epipilot.research.agent_policy import (
    question_closed,
    question_weight,
    rank_agent,
    research_phase,
)
from epipilot.research.engine import ResearchEngine
from epipilot.research.frontier import (
    FrontierValue,
    MeasurementContract,
    Phase,
    QuestionResolved,
    Resolution,
)
from epipilot.research.frontier_reducer import validate_resolution
from epipilot.research.intelligence_models import ResearchPolicy, ScoutRequest
from epipilot.research.measurement import Computer, MeasurementAdapter
from epipilot.research.models import ExperimentProposal, Metric, Observation, Ranking, ResearchGoal
from epipilot.research.policy import goal_met
from epipilot.research.reducer import task_id
from epipilot.state.project import ProjectState


def pending_resolutions(state: ProjectState) -> tuple[Resolution, ...]:
    """Apply only relevant, independently measured, preregistered decisive findings."""
    r = state.research
    if r is None or r.frontier is None:
        return ()
    updates = []
    for q in r.frontier.questions:
        related = [
            v for v in r.reviews if q.unknown_id in r.proposal(v.assessment.proposal_id).unknown_ids
        ]
        if not related:
            continue
        latest = related[-1]
        current = state.research
        assert current is not None and current.frontier is not None
        closed = current.frontier.closed(q.unknown_id)
        if closed:
            prior = next(
                x
                for x in reversed(current.frontier.resolutions)
                if x.unknown_id == q.unknown_id and x.resolved
            )
            previous = next(v for v in r.reviews if v.evidence_id == prior.evidence_id)
            if (
                previous.evidence_id == latest.evidence_id
                or previous.assessment.outcome == latest.assessment.outcome
            ):
                continue
        resolution = Resolution(
            unknown_id=q.unknown_id,
            evidence_id=latest.evidence_id,
            resolved=not closed,
            rationale="New independent counterevidence reopens the question"
            if closed
            else "Relevant adequate measurement answers the preregistered question",
        )
        try:
            validate_resolution(state, resolution)
        except ValueError:
            # Expected eligibility rejection; storage and replay errors must propagate.
            continue
        updates.append(resolution)
    return tuple(updates)


def reconcile_questions(engine: ResearchEngine) -> None:
    for resolution in pending_resolutions(engine.state):
        engine.append(EventType.RESEARCH_QUESTION_RESOLVED, QuestionResolved(resolution=resolution))


class Action(StrEnum):
    DISCOVER = "discovery_required"
    EXECUTE = "ready_to_execute"
    RESUME = "verification_required"
    RECOVER = "unrecorded_run_requires_recovery"
    BLOCKED = "blocked"
    GOAL_MET = "goal_met"


class QuestionView(FrontierValue):
    id: UUID
    topic_key: str
    question: str
    facet: str
    closed: bool
    decision_weight: float
    hypothesis_ids: tuple[UUID, ...]
    alternatives: tuple[str, ...]
    evidence_scope: str
    minimum_quality: int


class EvidenceView(FrontierValue):
    id: UUID
    proposal_id: str
    outcome: str
    execution: str
    verified: bool
    reliability: float
    definitive_negative: bool
    scope: str
    quality: int
    samples: int
    metrics: tuple[Metric, ...]
    artifact: str


class BasisView(FrontierValue):
    id: str
    kind: str
    summary: str


class AgentPacket(FrontierValue):
    project_id: str
    authority: tuple[BasisView, ...]
    selected_proposal: ExperimentProposal | None
    selected_contract: MeasurementContract | None
    artifact_path: str | None
    event_version: int
    action: Action
    phase: Phase
    goal: ResearchGoal
    questions: tuple[QuestionView, ...]
    recent_evidence: tuple[EvidenceView, ...]
    omitted_questions: int
    ranking: Ranking
    proposal_id: str | None = None
    instructions: tuple[str, ...]
    canonical_root: str = ""
    engineering_workspaces: tuple[WorkLease, ...] = ()
    delivery_mode: str = "managed"
    delivery_configuration: str = "configuration-required"
    push_due: tuple[PublicationDue, ...] = ()
    research_policy: ResearchPolicy | None = None
    scout_request: ScoutRequest | None = None
    scout_due: bool = False


class AgentService:
    def __init__(
        self, engine: ResearchEngine, root: Path, computers: Mapping[str, Computer] | None = None
    ):
        self.engine, self.root = engine, root.resolve()
        self.adapter = MeasurementAdapter(engine, root, computers)

    def iterate(self) -> AgentPacket:
        state = self.engine.state
        r = state.research
        if r is None or r.frontier is None or Path(r.frontier.repository).resolve() != self.root:
            raise ValueError("enable the canonical research frontier for this root first")
        f = r.frontier
        ranking = rank_agent(state, frozenset(self.adapter.computers))
        proposal_id = None
        intel = state.intelligence
        scout_due = False
        scout_failed_epoch = False
        if intel is not None:
            from epipilot.research.scout import research_epoch, research_revision

            epoch = research_epoch(state, research_revision(state, self.root))
            requests = [s.request for s in intel.starts if s.request.epoch == epoch]
            scout_due = not requests
            scout_failed_epoch = bool(
                requests
                and not intel.active
                and not any(
                    c.operation_id == requests[-1].operation_id and c.status.value == "accepted"
                    for c in intel.completions
                )
            )
        if r.active:
            proposal_id = r.active[0].proposal_id
            action = Action.RESUME if r.observation(proposal_id) else Action.RECOVER
        elif updates := pending_resolutions(state):
            action = Action.RESUME
            proposal_id = next(
                v.assessment.proposal_id
                for v in r.reviews
                if v.evidence_id == updates[0].evidence_id
            )
        elif goal_met(state):
            action = Action.GOAL_MET
        elif scout_failed_epoch:
            action = Action.BLOCKED
        elif (
            scout_due
            or (intel is not None and intel.active)
            or not f.discoveries
            or f.discoveries[-1].review_count != len(r.reviews)
        ):
            action = Action.DISCOVER
        elif ranking.ranked:
            action = Action.EXECUTE
            proposal_id = ranking.ranked[0].proposal_id
        else:
            action = Action.BLOCKED
        questions = sorted(
            f.questions,
            key=lambda q: (question_closed(state, q), -question_weight(state, q), q.topic_key),
        )
        views = tuple(
            QuestionView(
                id=q.unknown_id,
                topic_key=q.topic_key,
                question=next(u.question for u in state.unknowns if u.id == q.unknown_id),
                facet=q.facet.value,
                closed=question_closed(state, q),
                decision_weight=question_weight(state, q),
                hypothesis_ids=q.hypothesis_ids,
                alternatives=q.alternatives,
                evidence_scope=q.evidence_scope,
                minimum_quality=int(q.minimum_quality),
            )
            for q in questions[:20]
        )
        evidence = []
        for review in r.reviews[-12:]:
            a = review.assessment
            o = r.observation(a.proposal_id)
            if o is not None:
                evidence.append(
                    EvidenceView(
                        id=review.evidence_id,
                        proposal_id=a.proposal_id,
                        outcome=a.outcome.value,
                        execution=o.execution.value,
                        verified=a.verified,
                        reliability=a.reliability,
                        definitive_negative=r.proposal(a.proposal_id).definitive_negative,
                        scope=a.scope.value,
                        quality=int(a.quality),
                        samples=o.samples,
                        metrics=o.metrics[:20],
                        artifact=o.artifact,
                    )
                )
        instructions: tuple[str, ...] = (
            "Read primary code and pinned sources; identify properties, variables, method "
            "boundaries and bottlenecks.",
            "Submit falsifiable alternatives and the cheapest discriminative experiment; "
            "all priors are heuristic.",
            "Use exact Git source quotes and this canonical event_version; no "
            "conversational state as truth.",
            "Execute only a registered measurement contract; source integrity alone "
            "cannot resolve scientific unknowns.",
            "After new evidence, refresh discovery; reopen on counterevidence; do not "
            "repeat unchanged failures.",
            "Stop on blocked inputs, unknown costs, budget exhaustion or verified goal; "
            "preserve frozen protocols and logs.",
        )
        if intel is not None:
            instructions = (
                *instructions,
                "Research policy is enabled: use scout prepare/submit for discovery. "
                "Recover the active scout_request before preparing another operation. "
                "State/revision conflicts require reinspection; unknown costs remain reserved. "
                "start/run freeze a decision forecast; verify/resume reconcile measured outcomes.",
            )
        return AgentPacket(
            project_id=state.project_id,
            authority=tuple(
                BasisView(id=str(d.id), kind="decision", summary=f"{d.question}: {d.choice}")
                for d in state.decisions[-20:]
            ),
            selected_proposal=r.proposal(proposal_id) if proposal_id else None,
            selected_contract=next((c for c in f.contracts if c.proposal_id == proposal_id), None),
            artifact_path=str(self.engine.output / f"{task_id(state.project_id, proposal_id)}.json")
            if proposal_id
            else None,
            event_version=state.event_version,
            action=action,
            phase=research_phase(state),
            goal=r.goal,
            questions=views,
            recent_evidence=tuple(evidence),
            omitted_questions=max(0, len(questions) - len(views)),
            ranking=ranking,
            proposal_id=proposal_id,
            research_policy=intel.policy if intel is not None else None,
            scout_request=intel.active[0].request if intel is not None and intel.active else None,
            scout_due=scout_due,
            canonical_root=str(self.root),
            delivery_configuration="configured"
            if any(e.policy for e in state.engineering)
            else "configuration-required",
            push_due=ManagedWork(self.engine.store, state.project_id, self.root).push_due()
            if any(e.policy for e in state.engineering)
            else (),
            engineering_workspaces=tuple(
                {
                    event.work.operation_id: event.work
                    for event in state.engineering
                    if event.work is not None
                }.values()
            ),
            instructions=(
                *instructions,
                "Before code/report edits, use work acquire; edit and run engineering commands "
                "in its absolute cwd. Keep --root canonical; reuse the lease within a stage. "
                "work complete independently commits, gates and integrates. Publish only "
                "an integrated engineering milestone with checkpoint/artifact refs.",
            ),
        )

    def start(self) -> str:
        packet = self.iterate()
        if packet.action is not Action.EXECUTE or packet.proposal_id is None:
            raise ValueError(f"cannot start: {packet.action.value}")
        p = self.engine.reserve(
            packet.proposal_id,
            packet.ranking.ranked[0].model_dump_json(),
            packet.ranking.at,
            registered=frozenset(self.adapter.computers),
        )
        self.engine._start(p)
        return p.id

    def verify(self) -> None:
        self.engine.resume(self.adapter)
        reconcile_questions(self.engine)
        from epipilot.research.decision_policy import reconcile_decision_outcomes

        reconcile_decision_outcomes(self.engine)

    def run_next(self) -> Observation | None:
        if self.iterate().action is not Action.EXECUTE:
            return None
        identifier = self.start()
        r = self.engine.state.research
        assert r is not None
        p = r.proposal(identifier)
        self.engine.output.mkdir(parents=True, exist_ok=True)
        path = self.engine.output / f"{task_id(self.engine.project_id, p.id)}.json"
        if path.exists():
            raise ValueError("existing artifact requires explicit recovery; refusing overwrite")
        observation = self.adapter.run(p, path)
        self.engine.observe(observation)
        self.verify()
        return observation
