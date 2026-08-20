from __future__ import annotations

from collections import deque

import pytest

from epipilot.core.events import EventType
from epipilot.core.models import (
    Evidence,
    EvidenceKind,
    Provenance,
    Task,
    TaskStatus,
    new_evidence_id,
    new_task_id,
)
from epipilot.executors.base import ExecutorObservation, ExecutorState
from epipilot.runtime.engine import TaskRuntime
from epipilot.runtime.event_store import InMemoryEventStore
from epipilot.verification.pipeline import CheckResult, VerificationRequest, VerifierPipeline


class FakeExecutor:
    def __init__(self, observations: tuple[ExecutorObservation, ...]) -> None:
        self.observations = deque(observations)
        self.terminated = False
        self.interrupted = False

    async def start_task(self, task: Task, context: str) -> str:
        del task, context
        return "session-1"

    async def inspect(self, session_id: str) -> ExecutorObservation:
        del session_id
        return self.observations.popleft()

    async def interrupt(self, session_id: str, reason: str) -> None:
        del session_id, reason
        self.interrupted = True

    async def terminate(self, session_id: str) -> None:
        del session_id
        self.terminated = True


class StubCheck:
    def __init__(self, result: CheckResult) -> None:
        self.result = result

    async def run(self, request: VerificationRequest) -> CheckResult:
        del request
        return self.result


def _check_result(kind: EvidenceKind, *, independent: bool, passed: bool = True) -> CheckResult:
    evidence = Evidence(
        id=new_evidence_id(),
        kind=kind,
        summary="evidence",
        provenance=Provenance(source="test", scope="project/test"),
        independently_verified=independent,
    )
    return CheckResult(name="check", passed=passed, evidence=evidence)


@pytest.mark.asyncio
async def test_runtime_requires_independent_verification_before_passed() -> None:
    executor = FakeExecutor(
        (
            ExecutorObservation(state=ExecutorState.RUNNING, summary="working"),
            ExecutorObservation(
                state=ExecutorState.REPORTED_DONE,
                summary="done",
                artifacts=("artifact-1",),
            ),
        )
    )
    store = InMemoryEventStore()
    check = _check_result(EvidenceKind.DETERMINISTIC_CHECK, independent=True)
    runtime = TaskRuntime(
        project_id="project-1",
        executor=executor,
        verifier=VerifierPipeline(checks=(StubCheck(check),)),
        event_store=store,
    )
    task = Task(id=new_task_id(), objective="Implement feature", status=TaskStatus.READY)

    result = await runtime.run(task, "authoritative context")

    assert result.task.status is TaskStatus.PASSED
    assert result.task.linked_evidence == (check.evidence.id,)
    assert executor.terminated
    event_types = tuple(event.type for event in store.load("project-1"))
    assert EventType.VERIFICATION_PASSED in event_types
    assert EventType.EVIDENCE_RECORDED in event_types


@pytest.mark.asyncio
async def test_executor_self_report_only_causes_verification_failure() -> None:
    executor = FakeExecutor(
        (ExecutorObservation(state=ExecutorState.REPORTED_DONE, summary="trust me"),)
    )
    store = InMemoryEventStore()
    self_report = _check_result(EvidenceKind.EXECUTOR_REPORT, independent=False)
    runtime = TaskRuntime(
        project_id="project-1",
        executor=executor,
        verifier=VerifierPipeline(checks=(StubCheck(self_report),)),
        event_store=store,
    )
    task = Task(id=new_task_id(), objective="Implement feature", status=TaskStatus.READY)

    result = await runtime.run(task, "context")

    assert result.task.status is TaskStatus.FAILED
    assert not result.task.linked_evidence
    assert executor.terminated


@pytest.mark.asyncio
async def test_supervision_cap_blocks_instead_of_blindly_polling_forever() -> None:
    executor = FakeExecutor(
        (ExecutorObservation(state=ExecutorState.RUNNING, summary="still working"),)
    )
    store = InMemoryEventStore()
    check = _check_result(EvidenceKind.DETERMINISTIC_CHECK, independent=True)
    runtime = TaskRuntime(
        project_id="project-1",
        executor=executor,
        verifier=VerifierPipeline(checks=(StubCheck(check),)),
        event_store=store,
    )
    task = Task(id=new_task_id(), objective="Implement feature", status=TaskStatus.READY)

    result = await runtime.run(task, "context", max_observations=1)

    assert result.task.status is TaskStatus.BLOCKED
    assert executor.interrupted
    assert executor.terminated
