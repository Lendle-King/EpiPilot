from datetime import UTC, datetime
from uuid import UUID

import pytest

from epipilot.core.events import EventType
from epipilot.core.models import EvidenceKind, TaskStatus
from epipilot.events.codec import make_project_event
from epipilot.events.payloads import (
    EvidenceRecordedPayload,
    PlanBasisPayload,
    PlanVersionCreatedPayload,
    RequirementAddedPayload,
    TaskCreatedPayload,
    TaskStartedPayload,
    TaskStatusChangedPayload,
    VerificationPassedPayload,
)
from epipilot.planning.graph import PlanBasisKind
from epipilot.requirements.models import RequirementKind
from epipilot.state.errors import DuplicateAppliedEvent, InvalidEventOrder, MissingEntity
from epipilot.state.project import ProjectState
from epipilot.state.reducer import reduce_event
from epipilot.state.replay import replay_project

PROJECT_ID = "project-replay"
REQUIREMENT_ID = UUID("00000000-0000-0000-0000-000000000301")
TASK_ID = UUID("00000000-0000-0000-0000-000000000302")
EVIDENCE_ID = UUID("00000000-0000-0000-0000-000000000303")
CREATED_AT = datetime(2026, 8, 20, 6, 0, tzinfo=UTC)


def _valid_stream():
    requirement = make_project_event(
        EventType.REQUIREMENT_ADDED,
        PROJECT_ID,
        RequirementAddedPayload(
            requirement_id=REQUIREMENT_ID,
            kind=RequirementKind.GOAL,
            statement="Build deterministic project-state replay",
            provenance_source="user",
            provenance_scope=f"project/{PROJECT_ID}",
            provenance_created_at=CREATED_AT,
        ),
    )
    task = make_project_event(
        EventType.TASK_CREATED,
        PROJECT_ID,
        TaskCreatedPayload(task_id=TASK_ID, objective="Implement state replay"),
    )
    plan = make_project_event(
        EventType.PLAN_VERSION_CREATED,
        PROJECT_ID,
        PlanVersionCreatedPayload(
            version=1,
            task_ids=(TASK_ID,),
            basis=(
                PlanBasisPayload(
                    kind=PlanBasisKind.REQUIREMENT,
                    reference_id=str(REQUIREMENT_ID),
                ),
            ),
        ),
    )
    ready = make_project_event(
        EventType.TASK_STATUS_CHANGED,
        PROJECT_ID,
        TaskStatusChangedPayload(task_id=TASK_ID, status=TaskStatus.READY),
    )
    started = make_project_event(
        EventType.TASK_STARTED,
        PROJECT_ID,
        TaskStartedPayload(task_id=TASK_ID, session_id="session-1"),
    )
    running = make_project_event(
        EventType.TASK_STATUS_CHANGED,
        PROJECT_ID,
        TaskStatusChangedPayload(task_id=TASK_ID, status=TaskStatus.RUNNING),
    )
    reported_done = make_project_event(
        EventType.TASK_STATUS_CHANGED,
        PROJECT_ID,
        TaskStatusChangedPayload(task_id=TASK_ID, status=TaskStatus.AGENT_REPORTED_DONE),
    )
    verifying = make_project_event(
        EventType.TASK_STATUS_CHANGED,
        PROJECT_ID,
        TaskStatusChangedPayload(task_id=TASK_ID, status=TaskStatus.VERIFYING),
    )
    evidence = make_project_event(
        EventType.EVIDENCE_RECORDED,
        PROJECT_ID,
        EvidenceRecordedPayload(
            evidence_id=EVIDENCE_ID,
            kind=EvidenceKind.DETERMINISTIC_CHECK,
            summary="Replay contract test passed",
            provenance_source="pytest",
            provenance_scope=f"project/{PROJECT_ID}/task/{TASK_ID}",
            provenance_created_at=CREATED_AT,
            independently_verified=True,
            task_id=TASK_ID,
        ),
    )
    verification = make_project_event(
        EventType.VERIFICATION_PASSED,
        PROJECT_ID,
        VerificationPassedPayload(task_id=TASK_ID, evidence_id=EVIDENCE_ID),
    )
    passed = make_project_event(
        EventType.TASK_STATUS_CHANGED,
        PROJECT_ID,
        TaskStatusChangedPayload(
            task_id=TASK_ID,
            status=TaskStatus.PASSED,
            completion_evidence_id=EVIDENCE_ID,
        ),
    )
    return (
        requirement,
        task,
        plan,
        ready,
        started,
        running,
        reported_done,
        verifying,
        evidence,
        verification,
        passed,
    )


def test_replay_is_deterministic() -> None:
    events = _valid_stream()

    first = replay_project(PROJECT_ID, events)
    second = replay_project(PROJECT_ID, events)

    assert first == second


def test_replay_reconstructs_live_state() -> None:
    events = _valid_stream()
    live = ProjectState(project_id=PROJECT_ID)
    for event in events:
        live = reduce_event(live, event)

    replayed = replay_project(PROJECT_ID, events)

    assert replayed == live
    assert replayed.event_version == len(events)
    assert replayed.tasks[0].status is TaskStatus.PASSED
    assert replayed.tasks[0].linked_evidence
    assert replayed.current_plan is not None
    assert replayed.current_plan.tasks[0].status is TaskStatus.PASSED


def test_illegal_transition_fails_replay() -> None:
    events = _valid_stream()
    illegal_running = make_project_event(
        EventType.TASK_STATUS_CHANGED,
        PROJECT_ID,
        TaskStatusChangedPayload(task_id=TASK_ID, status=TaskStatus.RUNNING),
    )

    with pytest.raises(InvalidEventOrder):
        replay_project(PROJECT_ID, (events[0], events[1], illegal_running))


def test_duplicate_event_is_rejected() -> None:
    first = _valid_stream()[0]

    with pytest.raises(DuplicateAppliedEvent):
        replay_project(PROJECT_ID, (first, first))


def test_event_order_is_significant_or_rejected() -> None:
    requirement, task, plan, *_ = _valid_stream()

    with pytest.raises(MissingEntity):
        replay_project(PROJECT_ID, (requirement, plan, task))


def test_passed_requires_prior_matching_verification() -> None:
    events = _valid_stream()
    without_verification = (*events[:9], events[10])

    with pytest.raises(InvalidEventOrder):
        replay_project(PROJECT_ID, without_verification)
