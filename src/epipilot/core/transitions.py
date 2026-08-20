"""Explicit task-state transitions and invariant checks."""

from __future__ import annotations

from dataclasses import replace

from epipilot.core.models import Evidence, EvidenceKind, Task, TaskStatus


class InvalidTaskTransition(ValueError):
    """Raised when a requested task transition violates the domain contract."""


_ALLOWED_TRANSITIONS: dict[TaskStatus, frozenset[TaskStatus]] = {
    TaskStatus.PROPOSED: frozenset(
        {
            TaskStatus.READY,
            TaskStatus.CANCELLED,
            TaskStatus.SUPERSEDED,
            TaskStatus.INVALIDATED,
        }
    ),
    TaskStatus.READY: frozenset(
        {
            TaskStatus.RUNNING,
            TaskStatus.BLOCKED,
            TaskStatus.CANCELLED,
            TaskStatus.SUPERSEDED,
            TaskStatus.INVALIDATED,
        }
    ),
    TaskStatus.RUNNING: frozenset(
        {
            TaskStatus.AGENT_REPORTED_DONE,
            TaskStatus.BLOCKED,
            TaskStatus.FAILED,
            TaskStatus.CANCELLED,
            TaskStatus.INVALIDATED,
        }
    ),
    TaskStatus.AGENT_REPORTED_DONE: frozenset(
        {
            TaskStatus.VERIFYING,
            TaskStatus.FAILED,
            TaskStatus.INVALIDATED,
        }
    ),
    TaskStatus.VERIFYING: frozenset(
        {
            TaskStatus.PASSED,
            TaskStatus.FAILED,
            TaskStatus.BLOCKED,
            TaskStatus.INVALIDATED,
        }
    ),
    TaskStatus.BLOCKED: frozenset(
        {
            TaskStatus.READY,
            TaskStatus.CANCELLED,
            TaskStatus.SUPERSEDED,
            TaskStatus.INVALIDATED,
        }
    ),
    TaskStatus.PASSED: frozenset(),
    TaskStatus.FAILED: frozenset(),
    TaskStatus.SUPERSEDED: frozenset(),
    TaskStatus.CANCELLED: frozenset(),
    TaskStatus.INVALIDATED: frozenset(),
}


def transition_task(
    task: Task,
    target: TaskStatus,
    *,
    evidence: Evidence | None = None,
) -> Task:
    """Return a new task in ``target`` state after validating invariants.

    Passing a task is deliberately stricter than other transitions: an executor
    report is never sufficient evidence for `PASSED`.
    """
    if target not in _ALLOWED_TRANSITIONS[task.status]:
        raise InvalidTaskTransition(f"illegal task transition: {task.status} -> {target}")

    linked_evidence = task.linked_evidence

    if target is TaskStatus.PASSED:
        if evidence is None:
            raise InvalidTaskTransition("PASSED requires independent verification evidence")
        if evidence.kind is EvidenceKind.EXECUTOR_REPORT or not evidence.independently_verified:
            raise InvalidTaskTransition(
                "executor self-report cannot independently prove task completion"
            )
        linked_evidence = (*linked_evidence, evidence.id)

    return replace(task, status=target, linked_evidence=linked_evidence)
