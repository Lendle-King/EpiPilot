"""Minimal single-task orchestration loop for EpiPilot V0."""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass

from epipilot.core.events import EventType, ProjectEvent, new_event_id
from epipilot.core.models import Task, TaskStatus
from epipilot.core.transitions import transition_task
from epipilot.executors.base import CodingAgentExecutor, ExecutorObservation, ExecutorState
from epipilot.runtime.event_store import EventStore
from epipilot.verification.pipeline import VerificationOutcome, VerificationRequest, VerifierPipeline


@dataclass(frozen=True, slots=True)
class TaskRunResult:
    """Terminal or blocked result from one runtime task attempt."""

    task: Task
    session_id: str
    observation: ExecutorObservation | None
    verification: VerificationOutcome | None


@dataclass(slots=True)
class TaskRuntime:
    """Coordinate one coding-agent task from READY through independent verification."""

    project_id: str
    executor: CodingAgentExecutor
    verifier: VerifierPipeline
    event_store: EventStore
    poll_interval_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not self.project_id.strip():
            raise ValueError("project id must not be empty")
        if self.poll_interval_seconds < 0:
            raise ValueError("poll interval must not be negative")

    async def run(
        self,
        task: Task,
        context: str,
        *,
        max_observations: int = 100,
    ) -> TaskRunResult:
        """Execute a READY task until completion, failure, blocking, or supervision cap."""
        if task.status is not TaskStatus.READY:
            raise ValueError("runtime can start only READY tasks")
        if not context.strip():
            raise ValueError("executor context must not be empty")
        if max_observations < 1:
            raise ValueError("max observations must be positive")

        current = transition_task(task, TaskStatus.RUNNING)
        session_id = await self.executor.start_task(current, context)
        if not session_id.strip():
            raise ValueError("executor returned an empty session id")

        self._record(
            EventType.TASK_STARTED,
            {"task_id": str(task.id), "session_id": session_id},
        )
        self._record_status(task.id, TaskStatus.RUNNING)

        latest: ExecutorObservation | None = None
        for _ in range(max_observations):
            latest = await self.executor.inspect(session_id)
            self._record(
                EventType.EXECUTOR_OBSERVATION_RECORDED,
                {
                    "task_id": str(task.id),
                    "state": latest.state.value,
                    "changed_file_count": len(latest.changed_files),
                    "artifact_count": len(latest.artifacts),
                },
            )

            if latest.state is ExecutorState.RUNNING:
                await asyncio.sleep(self.poll_interval_seconds)
                continue

            if latest.state is ExecutorState.BLOCKED:
                current = transition_task(current, TaskStatus.BLOCKED)
                self._record_status(task.id, current.status)
                await self.executor.terminate(session_id)
                return TaskRunResult(current, session_id, latest, None)

            if latest.state is ExecutorState.FAILED:
                current = transition_task(current, TaskStatus.FAILED)
                self._record_status(task.id, current.status)
                await self.executor.terminate(session_id)
                return TaskRunResult(current, session_id, latest, None)

            if latest.state is ExecutorState.REPORTED_DONE:
                return await self._verify_reported_done(current, session_id, latest)

        await self.executor.interrupt(session_id, "supervision observation budget exhausted")
        current = transition_task(current, TaskStatus.BLOCKED)
        self._record_status(task.id, current.status)
        await self.executor.terminate(session_id)
        return TaskRunResult(current, session_id, latest, None)

    async def _verify_reported_done(
        self,
        task: Task,
        session_id: str,
        observation: ExecutorObservation,
    ) -> TaskRunResult:
        current = transition_task(task, TaskStatus.AGENT_REPORTED_DONE)
        self._record_status(task.id, current.status)
        current = transition_task(current, TaskStatus.VERIFYING)
        self._record_status(task.id, current.status)

        outcome = await self.verifier.verify(
            VerificationRequest(task=current, artifact_refs=observation.artifacts)
        )
        for evidence in outcome.evidence:
            self._record(
                EventType.EVIDENCE_RECORDED,
                {
                    "task_id": str(task.id),
                    "evidence_id": str(evidence.id),
                    "kind": evidence.kind.value,
                    "independently_verified": evidence.independently_verified,
                },
            )

        if outcome.passed:
            completion_evidence = outcome.completion_evidence()
            current = transition_task(
                current,
                TaskStatus.PASSED,
                evidence=completion_evidence,
            )
            self._record(
                EventType.VERIFICATION_PASSED,
                {
                    "task_id": str(task.id),
                    "evidence_id": str(completion_evidence.id),
                },
            )
        else:
            current = transition_task(current, TaskStatus.FAILED)
            self._record(
                EventType.VERIFICATION_FAILED,
                {"task_id": str(task.id)},
            )

        self._record_status(task.id, current.status)
        await self.executor.terminate(session_id)
        return TaskRunResult(current, session_id, observation, outcome)

    def _record_status(self, task_id: object, status: TaskStatus) -> None:
        self._record(
            EventType.TASK_STATUS_CHANGED,
            {"task_id": str(task_id), "status": status.value},
        )

    def _record(self, event_type: EventType, payload: dict[str, object]) -> None:
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        event = ProjectEvent(
            id=new_event_id(),
            type=event_type,
            aggregate_id=self.project_id,
            payload=encoded,
        )
        self.event_store.append(
            event,
            expected_version=self.event_store.version(self.project_id),
        )
