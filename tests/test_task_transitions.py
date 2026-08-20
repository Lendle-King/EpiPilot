from uuid import uuid4

import pytest

from epipilot.core.models import (
    Evidence,
    EvidenceId,
    EvidenceKind,
    Provenance,
    Task,
    TaskStatus,
    new_task_id,
)
from epipilot.core.transitions import InvalidTaskTransition, transition_task


def _verifying_task() -> Task:
    return Task(
        id=new_task_id(),
        objective="Verify a benchmarked improvement",
        status=TaskStatus.VERIFYING,
    )


def test_task_cannot_pass_without_evidence() -> None:
    with pytest.raises(InvalidTaskTransition, match="requires independent verification evidence"):
        transition_task(_verifying_task(), TaskStatus.PASSED)


def test_executor_self_report_cannot_pass_task() -> None:
    report = Evidence(
        id=EvidenceId(uuid4()),
        kind=EvidenceKind.EXECUTOR_REPORT,
        summary="Executor says the change works",
        provenance=Provenance(source="executor/session-1", scope="task/T-1"),
        independently_verified=False,
    )

    with pytest.raises(InvalidTaskTransition, match="executor self-report"):
        transition_task(_verifying_task(), TaskStatus.PASSED, evidence=report)


def test_independent_verification_can_pass_task() -> None:
    evidence = Evidence(
        id=EvidenceId(uuid4()),
        kind=EvidenceKind.DETERMINISTIC_CHECK,
        summary="Acceptance test suite passed",
        provenance=Provenance(source="pytest/artifact-42", scope="task/T-1"),
        independently_verified=True,
    )

    result = transition_task(_verifying_task(), TaskStatus.PASSED, evidence=evidence)

    assert result.status is TaskStatus.PASSED
    assert result.linked_evidence == (evidence.id,)


def test_illegal_transition_is_rejected() -> None:
    task = Task(id=new_task_id(), objective="Do the work", status=TaskStatus.READY)

    with pytest.raises(InvalidTaskTransition, match="illegal task transition"):
        transition_task(task, TaskStatus.PASSED)
