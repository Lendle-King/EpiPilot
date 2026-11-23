from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from epipilot.checkpoint.sqlite_store import SqliteCheckpointStore
from epipilot.checkpoint.store import InMemoryCheckpointStore
from epipilot.core.events import EventType, ProjectEvent
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
)
from epipilot.planning.graph import PlanBasisKind
from epipilot.requirements.models import RequirementKind
from epipilot.runtime.event_store import EventStore, InMemoryEventStore
from epipilot.runtime.recovery import (
    ExternalSessionState,
    ProjectRecoveryService,
    RecoveryDisposition,
)
from epipilot.runtime.sqlite_event_store import SqliteEventStore
from epipilot.state.replay import replay_project

PROJECT_ID = "project-recovery"
REQUIREMENT_ID = UUID("00000000-0000-0000-0000-000000000501")
TASK_ID = UUID("00000000-0000-0000-0000-000000000502")
EVIDENCE_ID = UUID("00000000-0000-0000-0000-000000000503")
CREATED_AT = datetime(2026, 8, 20, 8, 30, tzinfo=UTC)


@dataclass(slots=True)
class FakeSessionProbe:
    state: ExternalSessionState
    calls: list[str] = field(default_factory=list)

    async def inspect_session(self, session_id: str) -> ExternalSessionState:
        self.calls.append(session_id)
        return self.state


def _events() -> tuple[ProjectEvent, ...]:
    return (
        make_project_event(
            EventType.REQUIREMENT_ADDED,
            PROJECT_ID,
            RequirementAddedPayload(
                requirement_id=REQUIREMENT_ID,
                kind=RequirementKind.GOAL,
                statement="Resume safely after process interruption",
                provenance_source="test",
                provenance_scope=f"project/{PROJECT_ID}",
                provenance_created_at=CREATED_AT,
            ),
        ),
        make_project_event(
            EventType.TASK_CREATED,
            PROJECT_ID,
            TaskCreatedPayload(task_id=TASK_ID, objective="Run recoverable work"),
        ),
        make_project_event(
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
        ),
        make_project_event(
            EventType.EVIDENCE_RECORDED,
            PROJECT_ID,
            EvidenceRecordedPayload(
                evidence_id=EVIDENCE_ID,
                kind=EvidenceKind.STATIC_INSPECTION,
                summary="Recovery fixture evidence",
                provenance_source="test",
                provenance_scope=f"project/{PROJECT_ID}/task/{TASK_ID}",
                provenance_created_at=CREATED_AT,
                independently_verified=True,
                task_id=TASK_ID,
            ),
        ),
        make_project_event(
            EventType.TASK_STATUS_CHANGED,
            PROJECT_ID,
            TaskStatusChangedPayload(task_id=TASK_ID, status=TaskStatus.READY),
        ),
        make_project_event(
            EventType.TASK_STARTED,
            PROJECT_ID,
            TaskStartedPayload(task_id=TASK_ID, session_id="session-recovery-1"),
        ),
        make_project_event(
            EventType.TASK_STATUS_CHANGED,
            PROJECT_ID,
            TaskStatusChangedPayload(task_id=TASK_ID, status=TaskStatus.RUNNING),
        ),
        make_project_event(
            EventType.TASK_STATUS_CHANGED,
            PROJECT_ID,
            TaskStatusChangedPayload(task_id=TASK_ID, status=TaskStatus.AGENT_REPORTED_DONE),
        ),
        make_project_event(
            EventType.TASK_STATUS_CHANGED,
            PROJECT_ID,
            TaskStatusChangedPayload(task_id=TASK_ID, status=TaskStatus.VERIFYING),
        ),
    )


def _append(store: EventStore, events: tuple[ProjectEvent, ...]) -> None:
    for event in events:
        store.append(event, expected_version=store.version(PROJECT_ID))


@pytest.mark.asyncio
async def test_restart_from_checkpoint_plus_tail_preserves_state_and_provenance() -> None:
    events = _events()
    event_store = InMemoryEventStore()
    checkpoint_store = InMemoryCheckpointStore()
    probe = FakeSessionProbe(ExternalSessionState.ACTIVE)
    service = ProjectRecoveryService(event_store, checkpoint_store, probe)
    _append(event_store, events[:5])
    checkpoint = service.capture_checkpoint(PROJECT_ID)
    _append(event_store, events[5:7])

    result = await service.resume(PROJECT_ID)
    expected = replay_project(PROJECT_ID, events[:7])

    assert result.checkpoint_used
    assert checkpoint.last_event_version == 5
    assert result.state == expected
    assert result.state.evidence[0].id == EVIDENCE_ID
    assert result.state.current_plan is not None
    assert result.state.current_plan.basis[0].reference_id == str(REQUIREMENT_ID)
    assert result.recovery_decisions[0].disposition is RecoveryDisposition.REATTACH_SESSION


@pytest.mark.asyncio
async def test_corrupted_checkpoint_is_discarded_and_full_replay_used() -> None:
    events = _events()[:5]
    event_store = InMemoryEventStore()
    checkpoint_store = InMemoryCheckpointStore()
    service = ProjectRecoveryService(
        event_store,
        checkpoint_store,
        FakeSessionProbe(ExternalSessionState.UNKNOWN),
    )
    _append(event_store, events)
    checkpoint = service.capture_checkpoint(PROJECT_ID)
    checkpoint_store.save(
        replace(
            checkpoint,
            serialized_project_state=checkpoint.serialized_project_state + b" ",
        )
    )

    result = await service.resume(PROJECT_ID)

    assert not result.checkpoint_used
    assert result.checkpoint_discard_reason == "checkpoint checksum validation failed"
    assert result.state == replay_project(PROJECT_ID, events)
    assert checkpoint_store.latest(PROJECT_ID) is None


@pytest.mark.asyncio
async def test_stale_running_executor_requires_explicit_recovery() -> None:
    events = _events()[:7]
    event_store = InMemoryEventStore()
    _append(event_store, events)
    probe = FakeSessionProbe(ExternalSessionState.MISSING)
    service = ProjectRecoveryService(
        event_store,
        InMemoryCheckpointStore(),
        probe,
    )

    result = await service.resume(PROJECT_ID)

    assert len(result.recovery_decisions) == 1
    decision = result.recovery_decisions[0]
    assert decision.recorded_status is TaskStatus.RUNNING
    assert decision.external_session_state is ExternalSessionState.MISSING
    assert decision.disposition is RecoveryDisposition.RECOVERY_REQUIRED


@pytest.mark.asyncio
async def test_resume_is_idempotent_and_never_starts_duplicate_execution() -> None:
    events = _events()[:7]
    event_store = InMemoryEventStore()
    _append(event_store, events)
    probe = FakeSessionProbe(ExternalSessionState.ACTIVE)
    service = ProjectRecoveryService(
        event_store,
        InMemoryCheckpointStore(),
        probe,
    )
    version_before = event_store.version(PROJECT_ID)

    first = await service.resume(PROJECT_ID)
    second = await service.resume(PROJECT_ID)

    assert first.state == second.state
    assert first.recovery_decisions == second.recovery_decisions
    assert event_store.version(PROJECT_ID) == version_before
    assert probe.calls == ["session-recovery-1", "session-recovery-1"]


@pytest.mark.asyncio
async def test_reported_done_or_verifying_resumes_verification_not_executor() -> None:
    events = _events()[:9]
    event_store = InMemoryEventStore()
    _append(event_store, events)
    probe = FakeSessionProbe(ExternalSessionState.MISSING)
    service = ProjectRecoveryService(
        event_store,
        InMemoryCheckpointStore(),
        probe,
    )

    result = await service.resume(PROJECT_ID)

    assert len(result.recovery_decisions) == 1
    assert result.recovery_decisions[0].recorded_status is TaskStatus.VERIFYING
    assert result.recovery_decisions[0].disposition is RecoveryDisposition.RESUME_VERIFICATION
    assert probe.calls == []


@pytest.mark.asyncio
async def test_sqlite_restart_uses_durable_checkpoint_and_tail(tmp_path: Path) -> None:
    events = _events()
    event_path = tmp_path / "events.sqlite"
    checkpoint_path = tmp_path / "checkpoints.sqlite"
    event_store = SqliteEventStore(event_path)
    checkpoint_store = SqliteCheckpointStore(checkpoint_path)
    service = ProjectRecoveryService(
        event_store,
        checkpoint_store,
        FakeSessionProbe(ExternalSessionState.ACTIVE),
    )
    _append(event_store, events[:5])
    service.capture_checkpoint(PROJECT_ID)
    _append(event_store, events[5:7])

    restarted = ProjectRecoveryService(
        SqliteEventStore(event_path),
        SqliteCheckpointStore(checkpoint_path),
        FakeSessionProbe(ExternalSessionState.ACTIVE),
    )
    result = await restarted.resume(PROJECT_ID)

    assert result.checkpoint_used
    assert result.state == replay_project(PROJECT_ID, events[:7])
    assert result.recovery_decisions[0].disposition is RecoveryDisposition.REATTACH_SESSION
