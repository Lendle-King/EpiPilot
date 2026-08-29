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
from epipilot.epistemics.models import (
    Hypothesis,
    HypothesisStatus,
    ResolutionMode,
    Unknown,
    UnknownId,
    UnknownStatus,
)
from epipilot.events.codec import decode_event_payload
from epipilot.events.payloads import (
    ContextCompiledPayload,
    DecisionMadePayload,
    EventPayload,
    EvidenceRecordedPayload,
    ExecutorObservationRecordedPayload,
    ExperimentConcludedPayload,
    ExperimentPreregisteredPayload,
    HypothesisCreatedPayload,
    HypothesisUpdatedPayload,
    PlanVersionCreatedPayload,
    RequirementAddedPayload,
    TaskCreatedPayload,
    TaskStartedPayload,
    TaskStatusChangedPayload,
    TaskSupersededPayload,
    UnknownRegisteredPayload,
    UnknownResolvedPayload,
    VerificationFailedPayload,
    VerificationPassedPayload,
)
from epipilot.planning.graph import PlanBasis, PlanBasisKind, PlanGraph, TaskDependency
from epipilot.requirements.models import (
    Decision,
    DecisionAuthority,
    DecisionId,
    Requirement,
)
from epipilot.research.contracts import (
    ExperimentContract,
    ExperimentId,
    ExperimentPrediction,
    ExperimentRecord,
    ExperimentStatus,
)
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
    state: ProjectState, event_type: EventType, payload: EventPayload
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
            blocking_tasks=tuple(TaskId(value) for value in unknown_payload.blocking_tasks),
            value_of_information=unknown_payload.value_of_information,
            decision_sensitivity=unknown_payload.decision_sensitivity,
        )
        return replace(state, unknowns=(*state.unknowns, unknown))

    if event_type is EventType.UNKNOWN_RESOLVED:
        resolved_payload = _expect(payload, UnknownResolvedPayload)
        unresolved_unknown = _require_unknown(state, UnknownId(resolved_payload.unknown_id))
        if unresolved_unknown.status is not UnknownStatus.OPEN:
            raise InvalidEventOrder("only an open unknown may be resolved")
        resolution_evidence_ids = tuple(
            EvidenceId(value) for value in resolved_payload.evidence_ids
        )
        resolution_decision_ids = tuple(
            DecisionId(value) for value in resolved_payload.decision_ids
        )
        resolution_decisions = tuple(
            _require_decision(state, value) for value in resolution_decision_ids
        )
        if unresolved_unknown.resolution_mode in {
            ResolutionMode.EXPERIMENT,
            ResolutionMode.INVESTIGATION,
        }:
            if not resolution_evidence_ids:
                raise InvalidEventOrder(
                    "technical unknown resolution requires independently verified evidence"
                )
            for evidence_id in resolution_evidence_ids:
                _require_independent_evidence(state, evidence_id)
        if unresolved_unknown.resolution_mode is ResolutionMode.ASK_USER and not any(
            decision.authority is DecisionAuthority.USER for decision in resolution_decisions
        ):
            raise InvalidEventOrder("user-owned unknown requires an explicit user decision")
        if unresolved_unknown.resolution_mode is ResolutionMode.SAFE_DEFAULT:
            safe_decisions = tuple(
                decision
                for decision in resolution_decisions
                if decision.authority is DecisionAuthority.USER
                or (decision.authority is DecisionAuthority.SYSTEM and decision.reversible)
            )
            if not safe_decisions:
                raise InvalidEventOrder(
                    "safe-default unknown requires a reversible system decision or user decision"
                )
        for evidence_id in resolution_evidence_ids:
            _require_evidence(state, evidence_id)
        resolved_unknown = replace(
            unresolved_unknown,
            status=UnknownStatus.RESOLVED,
            resolution_evidence=resolution_evidence_ids,
            resolution_decisions=tuple(str(value) for value in resolution_decision_ids),
        )
        return _replace_unknown(state, resolved_unknown)

    if event_type is EventType.HYPOTHESIS_CREATED:
        hypothesis_payload = _expect(payload, HypothesisCreatedPayload)
        hypothesis_id = HypothesisId(hypothesis_payload.hypothesis_id)
        _ensure_new(hypothesis_id, (entry.id for entry in state.hypotheses), "hypothesis")
        hypothesis_support = tuple(
            EvidenceId(value) for value in hypothesis_payload.supporting_evidence
        )
        hypothesis_contradictions = tuple(
            EvidenceId(value) for value in hypothesis_payload.contradicting_evidence
        )
        for evidence_id in (*hypothesis_support, *hypothesis_contradictions):
            _require_evidence(state, evidence_id)
        if hypothesis_payload.status is HypothesisStatus.SUPPORTED:
            for evidence_id in hypothesis_support:
                _require_independent_evidence(state, evidence_id)
        if hypothesis_payload.status is HypothesisStatus.REFUTED:
            for evidence_id in hypothesis_contradictions:
                _require_independent_evidence(state, evidence_id)
        hypothesis = Hypothesis(
            id=hypothesis_id,
            statement=hypothesis_payload.statement,
            status=hypothesis_payload.status,
            confidence=hypothesis_payload.confidence,
            predictions=hypothesis_payload.predictions,
            falsification_conditions=hypothesis_payload.falsification_conditions,
            supporting_evidence=hypothesis_support,
            contradicting_evidence=hypothesis_contradictions,
            superseded_by=(
                HypothesisId(hypothesis_payload.superseded_by)
                if hypothesis_payload.superseded_by
                else None
            ),
        )
        return replace(state, hypotheses=(*state.hypotheses, hypothesis))

    if event_type is EventType.HYPOTHESIS_UPDATED:
        update_payload = _expect(payload, HypothesisUpdatedPayload)
        updated_hypothesis_id = HypothesisId(update_payload.hypothesis_id)
        current_hypothesis = _require_hypothesis(state, updated_hypothesis_id)
        updated_support = tuple(
            EvidenceId(value) for value in update_payload.supporting_evidence
        )
        updated_contradictions = tuple(
            EvidenceId(value) for value in update_payload.contradicting_evidence
        )
        for evidence_id in (*updated_support, *updated_contradictions):
            _require_evidence(state, evidence_id)
        if update_payload.status is HypothesisStatus.SUPPORTED:
            for evidence_id in updated_support:
                _require_independent_evidence(state, evidence_id)
        if update_payload.status is HypothesisStatus.REFUTED:
            for evidence_id in updated_contradictions:
                _require_independent_evidence(state, evidence_id)
        replacement_hypothesis_id = (
            HypothesisId(update_payload.superseded_by) if update_payload.superseded_by else None
        )
        if replacement_hypothesis_id is not None:
            if replacement_hypothesis_id == updated_hypothesis_id:
                raise InvalidEventOrder("a hypothesis cannot supersede itself")
            _require_hypothesis(state, replacement_hypothesis_id)
        updated_hypothesis = replace(
            current_hypothesis,
            status=update_payload.status,
            confidence=update_payload.confidence,
            supporting_evidence=updated_support,
            contradicting_evidence=updated_contradictions,
            superseded_by=replacement_hypothesis_id,
        )
        return _replace_hypothesis(state, updated_hypothesis)

    if event_type is EventType.EXPERIMENT_PREREGISTERED:
        experiment_payload = _expect(payload, ExperimentPreregisteredPayload)
        experiment_id = ExperimentId(experiment_payload.experiment_id)
        _ensure_new(experiment_id, (entry.id for entry in state.experiments), "experiment")
        experiment_unknown = _require_unknown(state, UnknownId(experiment_payload.unknown_id))
        if experiment_unknown.status is not UnknownStatus.OPEN:
            raise InvalidEventOrder("experiment must target an open unknown")
        if experiment_unknown.resolution_mode not in {
            ResolutionMode.EXPERIMENT,
            ResolutionMode.INVESTIGATION,
        }:
            raise InvalidEventOrder("experiment must target a technical unknown")
        experiment_hypothesis_ids = tuple(
            HypothesisId(value) for value in experiment_payload.hypothesis_ids
        )
        experiment_hypotheses = tuple(
            _require_hypothesis(state, value) for value in experiment_hypothesis_ids
        )
        if any(
            hypothesis.status in {HypothesisStatus.REFUTED, HypothesisStatus.SUPERSEDED}
            for hypothesis in experiment_hypotheses
        ):
            raise InvalidEventOrder("experiment cannot target refuted or superseded hypotheses")
        experiment_predictions = tuple(
            ExperimentPrediction(
                hypothesis_id=HypothesisId(prediction.hypothesis_id),
                expected_observation=prediction.expected_observation,
                falsification_condition=prediction.falsification_condition,
            )
            for prediction in experiment_payload.predictions
        )
        experiment_contract = ExperimentContract(
            unknown_id=experiment_unknown.id,
            objective=experiment_payload.objective,
            hypothesis_ids=experiment_hypothesis_ids,
            controlled_variables=experiment_payload.controlled_variables,
            measurements=experiment_payload.measurements,
            predictions=experiment_predictions,
            decision_rule=experiment_payload.decision_rule,
            budget=experiment_payload.budget,
            resource_claims=experiment_payload.resource_claims,
        )
        experiment = ExperimentRecord(id=experiment_id, contract=experiment_contract)
        return replace(state, experiments=(*state.experiments, experiment))

    if event_type is EventType.EXPERIMENT_CONCLUDED:
        conclusion_payload = _expect(payload, ExperimentConcludedPayload)
        current_experiment = _require_experiment(
            state, ExperimentId(conclusion_payload.experiment_id)
        )
        if current_experiment.status is not ExperimentStatus.PREREGISTERED:
            raise InvalidEventOrder("only a preregistered experiment may be concluded")
        experiment_evidence_ids = tuple(
            EvidenceId(value) for value in conclusion_payload.evidence_ids
        )
        for evidence_id in experiment_evidence_ids:
            _require_independent_evidence(state, evidence_id)
        concluded_experiment = replace(
            current_experiment,
            status=conclusion_payload.status,
            evidence_ids=experiment_evidence_ids,
            conclusion=conclusion_payload.conclusion,
        )
        return _replace_experiment(state, concluded_experiment)

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
        evidence_links = state.evidence_links
        if evidence_payload.task_id is not None:
            linked_task_id = TaskId(evidence_payload.task_id)
            _require_task(state, linked_task_id)
            evidence_links = (
                *evidence_links,
                EvidenceLink(evidence_id=evidence_id, task_id=linked_task_id),
            )
        return replace(
            state,
            evidence=(*state.evidence, evidence),
            evidence_links=evidence_links,
        )

    if event_type is EventType.TASK_CREATED:
        task_payload = _expect(payload, TaskCreatedPayload)
        created_task_id = TaskId(task_payload.task_id)
        _ensure_new(created_task_id, (entry.id for entry in state.tasks), "task")
        created_task = Task(id=created_task_id, objective=task_payload.objective)
        return replace(state, tasks=(*state.tasks, created_task))

    if event_type is EventType.TASK_STARTED:
        start_payload = _expect(payload, TaskStartedPayload)
        started_task_id = TaskId(start_payload.task_id)
        started_task = _require_task(state, started_task_id)
        if started_task.status is not TaskStatus.READY:
            raise InvalidEventOrder("a task session can start only from READY")
        if any(session.session_id == start_payload.session_id for session in state.sessions):
            raise DuplicateEntity(f"session {start_payload.session_id!r} already exists")
        started_session = SessionState(
            task_id=started_task_id,
            session_id=start_payload.session_id,
        )
        return replace(state, sessions=(*state.sessions, started_session))

    if event_type is EventType.TASK_STATUS_CHANGED:
        status_payload = _expect(payload, TaskStatusChangedPayload)
        status_task_id = TaskId(status_payload.task_id)
        status_task = _require_task(state, status_task_id)
        if status_payload.status is TaskStatus.RUNNING and not any(
            session.task_id == status_task_id for session in state.sessions
        ):
            raise InvalidEventOrder("RUNNING requires a previously started executor session")
        completion_evidence: Evidence | None = None
        if status_payload.status is TaskStatus.PASSED:
            if status_payload.completion_evidence_id is None:
                raise InvalidEventOrder("PASSED requires completion evidence")
            completion_evidence_id = EvidenceId(status_payload.completion_evidence_id)
            completion_evidence = _require_evidence(state, completion_evidence_id)
            if not any(
                record.task_id == status_task_id
                and record.passed
                and record.evidence_id == completion_evidence_id
                for record in state.verifications
            ):
                raise InvalidEventOrder(
                    "PASSED requires a prior matching verification-passed event"
                )
        transitioned_task = transition_task(
            status_task,
            status_payload.status,
            evidence=completion_evidence,
        )
        return _replace_task(state, transitioned_task)

    if event_type is EventType.CONTEXT_COMPILED:
        context_payload = _expect(payload, ContextCompiledPayload)
        context_task_id = TaskId(context_payload.task_id)
        _require_task(state, context_task_id)
        if any(record.context_id == context_payload.context_id for record in state.contexts):
            raise DuplicateEntity(f"context {context_payload.context_id!r} already exists")
        context_record = ContextRecord(
            task_id=context_task_id,
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
        observed_task_id = TaskId(observation_payload.task_id)
        _require_task(state, observed_task_id)
        observed_session = _require_session(
            state,
            observed_task_id,
            observation_payload.session_id,
        )
        updated_session = replace(
            observed_session,
            last_executor_state=observation_payload.state,
            changed_file_count=observation_payload.changed_file_count,
            artifact_refs=observation_payload.artifact_refs,
        )
        updated_sessions = tuple(
            updated_session if current.session_id == observation_payload.session_id else current
            for current in state.sessions
        )
        updated_artifact_refs = tuple(
            dict.fromkeys((*state.artifact_refs, *observation_payload.artifact_refs))
        )
        return replace(
            state,
            sessions=updated_sessions,
            artifact_refs=updated_artifact_refs,
        )

    if event_type is EventType.VERIFICATION_PASSED:
        verification_payload = _expect(payload, VerificationPassedPayload)
        verified_task_id = TaskId(verification_payload.task_id)
        verified_task = _require_task(state, verified_task_id)
        if verified_task.status is not TaskStatus.VERIFYING:
            raise InvalidEventOrder("verification result requires task status VERIFYING")
        verified_evidence = _require_independent_evidence(
            state,
            EvidenceId(verification_payload.evidence_id),
        )
        verification_record = VerificationRecord(
            task_id=verified_task_id,
            passed=True,
            evidence_id=verified_evidence.id,
        )
        return replace(
            state,
            verifications=(*state.verifications, verification_record),
        )

    if event_type is EventType.VERIFICATION_FAILED:
        failure_payload = _expect(payload, VerificationFailedPayload)
        failed_task_id = TaskId(failure_payload.task_id)
        failed_task = _require_task(state, failed_task_id)
        if failed_task.status is not TaskStatus.VERIFYING:
            raise InvalidEventOrder("verification result requires task status VERIFYING")
        failure_record = VerificationRecord(
            task_id=failed_task_id,
            passed=False,
            reason=failure_payload.reason,
        )
        return replace(
            state,
            verifications=(*state.verifications, failure_record),
        )

    if event_type is EventType.TASK_SUPERSEDED:
        supersede_payload = _expect(payload, TaskSupersededPayload)
        superseded_task_id = TaskId(supersede_payload.task_id)
        superseded_task = _require_task(state, superseded_task_id)
        for replacement_uuid in supersede_payload.replacement_task_ids:
            replacement_task_id = TaskId(replacement_uuid)
            if replacement_task_id == superseded_task_id:
                raise InvalidEventOrder("a task cannot supersede itself")
            _require_task(state, replacement_task_id)
        _validate_untyped_basis_refs(state, supersede_payload.basis_refs)
        return _replace_task(
            state,
            transition_task(superseded_task, TaskStatus.SUPERSEDED),
        )

    if event_type is EventType.PLAN_VERSION_CREATED:
        plan_payload = _expect(payload, PlanVersionCreatedPayload)
        expected_plan_version = 1 if not state.plans else state.plans[-1].version + 1
        if plan_payload.version != expected_plan_version:
            raise InvalidEventOrder(
                f"plan version must advance monotonically to {expected_plan_version}"
            )
        plan_tasks = tuple(
            _require_task(state, TaskId(task_id)) for task_id in plan_payload.task_ids
        )
        plan_dependencies = tuple(
            TaskDependency(
                predecessor=TaskId(dependency.predecessor),
                successor=TaskId(dependency.successor),
            )
            for dependency in plan_payload.dependencies
        )
        plan_basis = tuple(
            PlanBasis(kind=entry.kind, reference_id=entry.reference_id)
            for entry in plan_payload.basis
        )
        _validate_plan_basis(state, plan_basis)
        plan = PlanGraph(
            version=plan_payload.version,
            tasks=plan_tasks,
            dependencies=plan_dependencies,
            basis=plan_basis,
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


def _require_unknown(state: ProjectState, unknown_id: UnknownId) -> Unknown:
    for unknown in state.unknowns:
        if unknown.id == unknown_id:
            return unknown
    raise MissingEntity(f"unknown {unknown_id!s} does not exist")


def _require_hypothesis(state: ProjectState, hypothesis_id: HypothesisId) -> Hypothesis:
    for hypothesis in state.hypotheses:
        if hypothesis.id == hypothesis_id:
            return hypothesis
    raise MissingEntity(f"hypothesis {hypothesis_id!s} does not exist")


def _require_experiment(state: ProjectState, experiment_id: ExperimentId) -> ExperimentRecord:
    try:
        return state.experiment(experiment_id)
    except KeyError as exc:
        raise MissingEntity(f"experiment {experiment_id!s} does not exist") from exc


def _require_decision(state: ProjectState, decision_id: DecisionId) -> Decision:
    for decision in state.decisions:
        if decision.id == decision_id:
            return decision
    raise MissingEntity(f"decision {decision_id!s} does not exist")


def _require_evidence(state: ProjectState, evidence_id: EvidenceId) -> Evidence:
    try:
        return state.evidence_item(evidence_id)
    except KeyError as exc:
        raise MissingEntity(f"evidence {evidence_id!s} does not exist") from exc


def _require_independent_evidence(state: ProjectState, evidence_id: EvidenceId) -> Evidence:
    evidence = _require_evidence(state, evidence_id)
    if evidence.kind is EvidenceKind.EXECUTOR_REPORT or not evidence.independently_verified:
        raise InvalidEventOrder("epistemic resolution requires independently verified evidence")
    return evidence


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


def _replace_unknown(state: ProjectState, updated: Unknown) -> ProjectState:
    unknowns = tuple(updated if item.id == updated.id else item for item in state.unknowns)
    return replace(state, unknowns=unknowns)


def _replace_hypothesis(state: ProjectState, updated: Hypothesis) -> ProjectState:
    hypotheses = tuple(updated if item.id == updated.id else item for item in state.hypotheses)
    return replace(state, hypotheses=hypotheses)


def _replace_experiment(state: ProjectState, updated: ExperimentRecord) -> ProjectState:
    experiments = tuple(updated if item.id == updated.id else item for item in state.experiments)
    return replace(state, experiments=experiments)


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
