"""Pure reducer from typed project events to immutable canonical state."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from typing import TypeVar

from epipilot.core.events import EventType, ProjectEvent
from epipilot.core.models import (
    Evidence,
    EvidenceId,
    EvidenceKind,
    HypothesisId,
    Provenance,
    RequirementId,
    Task,
    TaskId,
    TaskStatus,
)
from epipilot.core.transitions import transition_task
from epipilot.epistemics.models import Hypothesis, Unknown, UnknownId
from epipilot.events.codec import decode_event_payload
from epipilot.events.payloads import (
    ContextCompiledPayload,
    DecisionMadePayload,
    EventPayload,
    EvidenceRecordedPayload,
    ExecutorObservationRecordedPayload,
    HypothesisCreatedPayload,
    PlanVersionCreatedPayload,
    RequirementAddedPayload,
    TaskCreatedPayload,
    TaskStartedPayload,
    TaskStatusChangedPayload,
    TaskSupersededPayload,
    UnknownRegisteredPayload,
    VerificationFailedPayload,
    VerificationPassedPayload,
)
from epipilot.planning.graph import PlanBasis, PlanBasisKind, PlanGraph, TaskDependency
from epipilot.requirements.models import Decision, DecisionId, Requirement
from epipilot.state.errors import (
    AggregateMismatch,
    DuplicateEntity,
    InvalidEventOrder,
    MissingEntity,
    StateReplayError,
)
from epipilot.state.project import (
    ContextRecord,
    EvidenceLink,
    ProjectState,
    SessionState,
    VerificationRecord,
)

PayloadT = TypeVar("PayloadT", bound=EventPayload)


def reduce_event(state: ProjectState, event: ProjectEvent) -> ProjectState:
    """Apply exactly one event without I/O, failing closed on any invariant violation."""
    if event.aggregate_id != state.project_id:
        raise AggregateMismatch(
            f"event aggregate {event.aggregate_id!r} does not match project {state.project_id!r}"
        )
    payload = decode_event_payload(event)

    try:
        updated = _reduce_payload(state, event.type, payload)
    except ValueError as exc:
        if isinstance(exc, StateReplayError):
            raise
        raise InvalidEventOrder(f"invalid {event.type.value} event ordering or state") from exc

    return replace(updated, event_version=state.event_version + 1)


def _reduce_payload(
    state: ProjectState,
    event_type: EventType,
    payload: EventPayload,
) -> ProjectState:
    if event_type is EventType.REQUIREMENT_ADDED:
        requirement_payload = _expect(payload, RequirementAddedPayload)
        requirement_id = RequirementId(requirement_payload.requirement_id)
        _ensure_new(requirement_id, (entry.id for entry in state.requirements), "requirement")
        requirement = Requirement(
            id=requirement_id,
            kind=requirement_payload.kind,
            statement=requirement_payload.statement,
            provenance=Provenance(
                source=requirement_payload.provenance_source,
                scope=requirement_payload.provenance_scope,
                created_at=requirement_payload.provenance_created_at,
            ),
        )
        return replace(state, requirements=(*state.requirements, requirement))

    if event_type is EventType.DECISION_MADE:
        decision_payload = _expect(payload, DecisionMadePayload)
        decision_id = DecisionId(decision_payload.decision_id)
        _ensure_new(decision_id, (entry.id for entry in state.decisions), "decision")
        decision = Decision(
            id=decision_id,
            question=decision_payload.question,
            choice=decision_payload.choice,
            authority=decision_payload.authority,
            rationale=decision_payload.rationale,
            basis_refs=decision_payload.basis_refs,
            reversible=decision_payload.reversible,
        )
        return replace(state, decisions=(*state.decisions, decision))

    if event_type is EventType.UNKNOWN_REGISTERED:
        unknown_payload = _expect(payload, UnknownRegisteredPayload)
        unknown_id = UnknownId(unknown_payload.unknown_id)
        _ensure_new(unknown_id, (entry.id for entry in state.unknowns), "unknown")
        for task_uuid in unknown_payload.blocking_tasks:
            _require_task(state, TaskId(task_uuid))
        unknown = Unknown(
            id=unknown_id,
            question=unknown_payload.question,
            impact=unknown_payload.impact,
            resolution_mode=unknown_payload.resolution_mode,
            blocking_tasks=tuple(TaskId(task_id) for task_id in unknown_payload.blocking_tasks),
            value_of_information=unknown_payload.value_of_information,
            decision_sensitivity=unknown_payload.decision_sensitivity,
        )
        return replace(state, unknowns=(*state.unknowns, unknown))

    if event_type is EventType.HYPOTHESIS_CREATED:
        hypothesis_payload = _expect(payload, HypothesisCreatedPayload)
        hypothesis_id = HypothesisId(hypothesis_payload.hypothesis_id)
        _ensure_new(hypothesis_id, (entry.id for entry in state.hypotheses), "hypothesis")
        for evidence_uuid in (
            *hypothesis_payload.supporting_evidence,
            *hypothesis_payload.contradicting_evidence,
        ):
            _require_evidence(state, EvidenceId(evidence_uuid))
        hypothesis = Hypothesis(
            id=hypothesis_id,
            statement=hypothesis_payload.statement,
            status=hypothesis_payload.status,
            confidence=hypothesis_payload.confidence,
            predictions=hypothesis_payload.predictions,
            falsification_conditions=hypothesis_payload.falsification_conditions,
            supporting_evidence=tuple(
                EvidenceId(value) for value in hypothesis_payload.supporting_evidence
            ),
            contradicting_evidence=tuple(
                EvidenceId(value) for value in hypothesis_payload.contradicting_evidence
            ),
            superseded_by=(
                HypothesisId(hypothesis_payload.superseded_by)
                if hypothesis_payload.superseded_by is not None
                else None
            ),
        )
        return replace(state, hypotheses=(*state.hypotheses, hypothesis))

    if event_type is EventType.EVIDENCE_RECORDED:
        evidence_payload = _expect(payload, EvidenceRecordedPayload)
        evidence_id = EvidenceId(evidence_payload.evidence_id)
        _ensure_new(evidence_id, (entry.id for entry in state.evidence), "evidence")
        evidence = Evidence(
            id=evidence_id,
            kind=evidence_payload.kind,
            summary=evidence_payload.summary,
            provenance=Provenance(
                source=evidence_payload.provenance_source,
                scope=evidence_payload.provenance_scope,
                created_at=evidence_payload.provenance_created_at,
            ),
            independently_verified=evidence_payload.independently_verified,
        )
        links = state.evidence_links
        if evidence_payload.task_id is not None:
            task_id = TaskId(evidence_payload.task_id)
            _require_task(state, task_id)
            links = (*links, EvidenceLink(evidence_id=evidence_id, task_id=task_id))
        return replace(state, evidence=(*state.evidence, evidence), evidence_links=links)

    if event_type is EventType.TASK_CREATED:
        task_payload = _expect(payload, TaskCreatedPayload)
        task_id = TaskId(task_payload.task_id)
        _ensure_new(task_id, (entry.id for entry in state.tasks), "task")
        task = Task(id=task_id, objective=task_payload.objective)
        return replace(state, tasks=(*state.tasks, task))

    if event_type is EventType.TASK_STARTED:
        start_payload = _expect(payload, TaskStartedPayload)
        task_id = TaskId(start_payload.task_id)
        task = _require_task(state, task_id)
        if task.status is not TaskStatus.READY:
            raise InvalidEventOrder("a task session can start only from READY")
        if any(session.session_id == start_payload.session_id for session in state.sessions):
            raise DuplicateEntity(f"session {start_payload.session_id!r} already exists")
        session = SessionState(task_id=task_id, session_id=start_payload.session_id)
        return replace(state, sessions=(*state.sessions, session))

    if event_type is EventType.TASK_STATUS_CHANGED:
        status_payload = _expect(payload, TaskStatusChangedPayload)
        task_id = TaskId(status_payload.task_id)
        task = _require_task(state, task_id)

        if status_payload.status is TaskStatus.RUNNING and not any(
            session.task_id == task_id for session in state.sessions
        ):
            raise InvalidEventOrder("RUNNING requires a previously started executor session")

        completion_evidence: Evidence | None = None
        if status_payload.status is TaskStatus.PASSED:
            if status_payload.completion_evidence_id is None:
                raise InvalidEventOrder("PASSED requires completion evidence")
            evidence_id = EvidenceId(status_payload.completion_evidence_id)
            completion_evidence = _require_evidence(state, evidence_id)
            if not any(
                record.task_id == task_id and record.passed and record.evidence_id == evidence_id
                for record in state.verifications
            ):
                raise InvalidEventOrder(
                    "PASSED requires a prior matching verification-passed event"
                )

        updated_task = transition_task(
            task,
            status_payload.status,
            evidence=completion_evidence,
        )
        return _replace_task(state, updated_task)

    if event_type is EventType.CONTEXT_COMPILED:
        context_payload = _expect(payload, ContextCompiledPayload)
        task_id = TaskId(context_payload.task_id)
        _require_task(state, task_id)
        if any(record.context_id == context_payload.context_id for record in state.contexts):
            raise DuplicateEntity(f"context {context_payload.context_id!r} already exists")
        context_record = ContextRecord(
            task_id=task_id,
            context_id=context_payload.context_id,
            included_item_ids=context_payload.included_item_ids,
            excluded_item_ids=context_payload.excluded_item_ids,
            token_cost=context_payload.token_cost,
            token_budget=context_payload.token_budget,
            compiler_version=context_payload.compiler_version,
        )
        return replace(state, contexts=(*state.contexts, context_record))

    if event_type is EventType.EXECUTOR_OBSERVATION_RECORDED:
        observation_payload = _expect(payload, ExecutorObservationRecordedPayload)
        task_id = TaskId(observation_payload.task_id)
        _require_task(state, task_id)
        session = _require_session(state, task_id, observation_payload.session_id)
        updated_session = replace(
            session,
            last_executor_state=observation_payload.state,
            changed_file_count=observation_payload.changed_file_count,
            artifact_refs=observation_payload.artifact_refs,
        )
        sessions = tuple(
            updated_session if current.session_id == observation_payload.session_id else current
            for current in state.sessions
        )
        artifact_refs = tuple(
            dict.fromkeys((*state.artifact_refs, *observation_payload.artifact_refs))
        )
        return replace(state, sessions=sessions, artifact_refs=artifact_refs)

    if event_type is EventType.VERIFICATION_PASSED:
        verification_payload = _expect(payload, VerificationPassedPayload)
        task_id = TaskId(verification_payload.task_id)
        task = _require_task(state, task_id)
        if task.status is not TaskStatus.VERIFYING:
            raise InvalidEventOrder("verification result requires task status VERIFYING")
        evidence = _require_evidence(state, EvidenceId(verification_payload.evidence_id))
        if evidence.kind is EvidenceKind.EXECUTOR_REPORT or not evidence.independently_verified:
            raise InvalidEventOrder("verification-passed evidence must be independent")
        verification_record = VerificationRecord(
            task_id=task_id,
            passed=True,
            evidence_id=evidence.id,
        )
        return replace(
            state,
            verifications=(*state.verifications, verification_record),
        )

    if event_type is EventType.VERIFICATION_FAILED:
        failure_payload = _expect(payload, VerificationFailedPayload)
        task_id = TaskId(failure_payload.task_id)
        task = _require_task(state, task_id)
        if task.status is not TaskStatus.VERIFYING:
            raise InvalidEventOrder("verification result requires task status VERIFYING")
        failure_record = VerificationRecord(
            task_id=task_id,
            passed=False,
            reason=failure_payload.reason,
        )
        return replace(state, verifications=(*state.verifications, failure_record))

    if event_type is EventType.TASK_SUPERSEDED:
        supersede_payload = _expect(payload, TaskSupersededPayload)
        task_id = TaskId(supersede_payload.task_id)
        task = _require_task(state, task_id)
        for replacement_uuid in supersede_payload.replacement_task_ids:
            replacement_id = TaskId(replacement_uuid)
            if replacement_id == task_id:
                raise InvalidEventOrder("a task cannot supersede itself")
            _require_task(state, replacement_id)
        _validate_untyped_basis_refs(state, supersede_payload.basis_refs)
        return _replace_task(state, transition_task(task, TaskStatus.SUPERSEDED))

    if event_type is EventType.PLAN_VERSION_CREATED:
        plan_payload = _expect(payload, PlanVersionCreatedPayload)
        expected_version = 1 if not state.plans else state.plans[-1].version + 1
        if plan_payload.version != expected_version:
            raise InvalidEventOrder(
                f"plan version must advance monotonically to {expected_version}"
            )
        tasks = tuple(_require_task(state, TaskId(task_id)) for task_id in plan_payload.task_ids)
        dependencies = tuple(
            TaskDependency(
                predecessor=TaskId(dependency.predecessor),
                successor=TaskId(dependency.successor),
            )
            for dependency in plan_payload.dependencies
        )
        basis = tuple(
            PlanBasis(kind=entry.kind, reference_id=entry.reference_id)
            for entry in plan_payload.basis
        )
        _validate_plan_basis(state, basis)
        plan = PlanGraph(
            version=plan_payload.version,
            tasks=tasks,
            dependencies=dependencies,
            basis=basis,
        )
        return replace(state, plans=(*state.plans, plan))

    raise InvalidEventOrder(f"no reducer registered for event type {event_type.value}")


def _expect(payload: EventPayload, expected: type[PayloadT]) -> PayloadT:
    if not isinstance(payload, expected):
        raise InvalidEventOrder(
            f"payload registry returned {type(payload).__name__}, expected {expected.__name__}"
        )
    return payload


def _ensure_new(identifier: object, existing: Iterable[object], kind: str) -> None:
    if identifier in existing:
        raise DuplicateEntity(f"{kind} {identifier!s} already exists")


def _require_task(state: ProjectState, task_id: TaskId) -> Task:
    try:
        return state.task(task_id)
    except KeyError as exc:
        raise MissingEntity(f"task {task_id!s} does not exist") from exc


def _require_evidence(state: ProjectState, evidence_id: EvidenceId) -> Evidence:
    try:
        return state.evidence_item(evidence_id)
    except KeyError as exc:
        raise MissingEntity(f"evidence {evidence_id!s} does not exist") from exc


def _require_session(state: ProjectState, task_id: TaskId, session_id: str) -> SessionState:
    for session in state.sessions:
        if session.task_id == task_id and session.session_id == session_id:
            return session
    raise MissingEntity(f"session {session_id!r} for task {task_id!s} does not exist")


def _replace_task(state: ProjectState, updated: Task) -> ProjectState:
    tasks = tuple(updated if task.id == updated.id else task for task in state.tasks)
    plans = state.plans
    if plans and any(task.id == updated.id for task in plans[-1].tasks):
        plans = (*plans[:-1], plans[-1].with_task_state(updated))
    return replace(state, tasks=tasks, plans=plans)


def _validate_plan_basis(state: ProjectState, basis: tuple[PlanBasis, ...]) -> None:
    requirement_ids = {str(item.id) for item in state.requirements}
    decision_ids = {str(item.id) for item in state.decisions}
    evidence_ids = {str(item.id) for item in state.evidence}
    for item in basis:
        if item.kind is PlanBasisKind.REQUIREMENT and item.reference_id not in requirement_ids:
            raise MissingEntity(f"plan basis requirement {item.reference_id!r} does not exist")
        if item.kind is PlanBasisKind.DECISION and item.reference_id not in decision_ids:
            raise MissingEntity(f"plan basis decision {item.reference_id!r} does not exist")
        if item.kind is PlanBasisKind.EVIDENCE and item.reference_id not in evidence_ids:
            raise MissingEntity(f"plan basis evidence {item.reference_id!r} does not exist")


def _validate_untyped_basis_refs(state: ProjectState, refs: tuple[str, ...]) -> None:
    known = {str(item.id) for item in state.requirements}
    known.update(str(item.id) for item in state.decisions)
    known.update(str(item.id) for item in state.evidence)
    for reference in refs:
        if reference not in known:
            raise MissingEntity(f"canonical basis reference {reference!r} does not exist")
