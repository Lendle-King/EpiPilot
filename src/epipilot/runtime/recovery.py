"""Crash-safe project resume and external executor reconciliation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Protocol

from epipilot.checkpoint.codec import create_checkpoint, load_checkpoint
from epipilot.checkpoint.errors import CheckpointError, CheckpointStateInvalid
from epipilot.checkpoint.models import Checkpoint
from epipilot.checkpoint.store import CheckpointStore
from epipilot.core.models import TaskId, TaskStatus
from epipilot.runtime.event_store import EventStore
from epipilot.state.errors import StateReplayError
from epipilot.state.project import ProjectState, SessionState
from epipilot.state.replay import replay_from_state, replay_project


class ExternalSessionState(StrEnum):
    """Observed liveness of a previously recorded executor session."""

    ACTIVE = "active"
    MISSING = "missing"
    TERMINATED = "terminated"
    UNKNOWN = "unknown"


class RecoveryDisposition(StrEnum):
    """Safe next action after logical/external state reconciliation."""

    REATTACH_SESSION = "reattach_session"
    RESUME_VERIFICATION = "resume_verification"
    RECOVERY_REQUIRED = "recovery_required"


class SessionRecoveryProbe(Protocol):
    """Read-only boundary for checking external executor reality."""

    async def inspect_session(self, session_id: str) -> ExternalSessionState:
        """Return current liveness without starting or mutating an executor session."""
        ...


@dataclass(frozen=True, slots=True)
class RecoveryDecision:
    """One explicit recovery condition for interrupted authoritative work."""

    task_id: TaskId
    recorded_status: TaskStatus
    session_id: str | None
    external_session_state: ExternalSessionState | None
    disposition: RecoveryDisposition
    reason: str


@dataclass(frozen=True, slots=True)
class ResumeResult:
    """Reconstructed state plus non-destructive recovery decisions."""

    state: ProjectState
    checkpoint_used: bool
    checkpoint_discard_reason: str | None
    recovery_decisions: tuple[RecoveryDecision, ...]


@dataclass(slots=True)
class ProjectRecoveryService:
    """Capture event-derived checkpoints and resume without duplicating execution."""

    event_store: EventStore
    checkpoint_store: CheckpointStore
    session_probe: SessionRecoveryProbe

    def capture_checkpoint(self, project_id: str) -> Checkpoint:
        """Persist a snapshot derived only from the canonical event stream."""
        events = self.event_store.load(project_id)
        state = replay_project(project_id, events)
        checkpoint = create_checkpoint(state)
        self.checkpoint_store.save(checkpoint)
        return checkpoint

    async def resume(self, project_id: str) -> ResumeResult:
        """Recover from checkpoint+tail or full replay, then reconcile interrupted work."""
        events = self.event_store.load(project_id)
        checkpoint_discard_reason: str | None = None

        try:
            checkpoint = self.checkpoint_store.latest(project_id)
        except CheckpointError as exc:
            self.checkpoint_store.discard_latest(project_id)
            checkpoint = None
            checkpoint_discard_reason = str(exc)

        checkpoint_used = False
        if checkpoint is None:
            state = replay_project(project_id, events)
        else:
            try:
                if checkpoint.last_event_version > len(events):
                    raise CheckpointStateInvalid(
                        "checkpoint event version is ahead of the canonical event stream"
                    )
                checkpoint_state = load_checkpoint(checkpoint)
                tail = events[checkpoint.last_event_version :]
                state = replay_from_state(checkpoint_state, tail)
                checkpoint_used = True
            except (CheckpointError, StateReplayError) as exc:
                self.checkpoint_store.discard_latest(project_id)
                checkpoint_discard_reason = str(exc)
                state = replay_project(project_id, events)

        decisions = await _reconcile_interrupted_work(state, self.session_probe)
        return ResumeResult(
            state=state,
            checkpoint_used=checkpoint_used,
            checkpoint_discard_reason=checkpoint_discard_reason,
            recovery_decisions=decisions,
        )


async def _reconcile_interrupted_work(
    state: ProjectState,
    probe: SessionRecoveryProbe,
) -> tuple[RecoveryDecision, ...]:
    decisions: list[RecoveryDecision] = []

    for task in state.tasks:
        if task.status is TaskStatus.RUNNING:
            session = _latest_session(state, task.id)
            if session is None:
                decisions.append(
                    RecoveryDecision(
                        task_id=task.id,
                        recorded_status=task.status,
                        session_id=None,
                        external_session_state=ExternalSessionState.UNKNOWN,
                        disposition=RecoveryDisposition.RECOVERY_REQUIRED,
                        reason="RUNNING task has no recorded executor session",
                    )
                )
                continue

            external_state = await probe.inspect_session(session.session_id)
            if external_state is ExternalSessionState.ACTIVE:
                disposition = RecoveryDisposition.REATTACH_SESSION
                reason = "recorded RUNNING session is still active"
            else:
                disposition = RecoveryDisposition.RECOVERY_REQUIRED
                reason = (
                    "recorded RUNNING session is not safely attachable; "
                    "do not restart the task automatically"
                )
            decisions.append(
                RecoveryDecision(
                    task_id=task.id,
                    recorded_status=task.status,
                    session_id=session.session_id,
                    external_session_state=external_state,
                    disposition=disposition,
                    reason=reason,
                )
            )
            continue

        if task.status in {TaskStatus.AGENT_REPORTED_DONE, TaskStatus.VERIFYING}:
            session = _latest_session(state, task.id)
            decisions.append(
                RecoveryDecision(
                    task_id=task.id,
                    recorded_status=task.status,
                    session_id=session.session_id if session is not None else None,
                    external_session_state=None,
                    disposition=RecoveryDisposition.RESUME_VERIFICATION,
                    reason="executor work must not be repeated; continue independent verification",
                )
            )

    return tuple(decisions)


def _latest_session(state: ProjectState, task_id: TaskId) -> SessionState | None:
    for session in reversed(state.sessions):
        if session.task_id == task_id:
            return session
    return None
