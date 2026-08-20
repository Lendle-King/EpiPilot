"""Immutable, traceable task-graph model for versioned planning."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from epipilot.core.models import Task, TaskId, TaskStatus


class PlanBasisKind(StrEnum):
    """Canonical authorities that may justify a plan mutation."""

    REQUIREMENT = "requirement"
    DECISION = "decision"
    EVIDENCE = "evidence"


@dataclass(frozen=True, slots=True)
class PlanBasis:
    """Traceable reason for creating or mutating a plan version."""

    kind: PlanBasisKind
    reference_id: str

    def __post_init__(self) -> None:
        if not self.reference_id.strip():
            raise ValueError("plan basis reference must not be empty")


@dataclass(frozen=True, slots=True)
class TaskDependency:
    """A strict predecessor -> successor dependency."""

    predecessor: TaskId
    successor: TaskId

    def __post_init__(self) -> None:
        if self.predecessor == self.successor:
            raise ValueError("a task cannot depend on itself")


@dataclass(frozen=True, slots=True)
class PlanGraph:
    """One immutable, auditable topology version plus its execution-state projection.

    Runtime task-state changes keep ``version`` unchanged. Only structural replanning
    creates a new topology version and therefore requires a fresh traceable basis.
    """

    version: int
    tasks: tuple[Task, ...]
    dependencies: tuple[TaskDependency, ...]
    basis: tuple[PlanBasis, ...]

    def __post_init__(self) -> None:
        if self.version < 1:
            raise ValueError("plan version must be positive")
        if not self.basis:
            raise ValueError("every plan version must have a traceable basis")

        task_ids = {task.id for task in self.tasks}
        if len(task_ids) != len(self.tasks):
            raise ValueError("task ids must be unique within a plan")

        for dependency in self.dependencies:
            if dependency.predecessor not in task_ids or dependency.successor not in task_ids:
                raise ValueError("task dependency references a task outside the plan")

        _assert_acyclic(task_ids, self.dependencies)

    def task(self, task_id: TaskId) -> Task:
        """Return one task or raise ``KeyError`` when it is not in this plan."""
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(task_id)

    def with_task_state(self, updated: Task) -> PlanGraph:
        """Project a runtime state update without pretending that replanning occurred."""
        original = self.task(updated.id)
        if original.objective != updated.objective:
            raise ValueError("task objective changes require structural replanning")

        tasks = tuple(updated if task.id == updated.id else task for task in self.tasks)
        return PlanGraph(
            version=self.version,
            tasks=tasks,
            dependencies=self.dependencies,
            basis=self.basis,
        )

    def replan(
        self,
        *,
        tasks: tuple[Task, ...],
        dependencies: tuple[TaskDependency, ...],
        basis: tuple[PlanBasis, ...],
    ) -> PlanGraph:
        """Create the next structural topology version from explicit canonical reasons."""
        return PlanGraph(
            version=self.version + 1,
            tasks=tasks,
            dependencies=dependencies,
            basis=basis,
        )

    def runnable_tasks(self) -> tuple[Task, ...]:
        """Return READY tasks whose predecessors have all passed verification."""
        predecessors: dict[TaskId, list[TaskId]] = {task.id: [] for task in self.tasks}
        for dependency in self.dependencies:
            predecessors[dependency.successor].append(dependency.predecessor)

        by_id = {task.id: task for task in self.tasks}
        runnable: list[Task] = []
        for task in self.tasks:
            if task.status is not TaskStatus.READY:
                continue
            if all(by_id[parent].status is TaskStatus.PASSED for parent in predecessors[task.id]):
                runnable.append(task)
        return tuple(runnable)


def _assert_acyclic(task_ids: set[TaskId], dependencies: tuple[TaskDependency, ...]) -> None:
    indegree: dict[TaskId, int] = dict.fromkeys(task_ids, 0)
    children: dict[TaskId, list[TaskId]] = {task_id: [] for task_id in task_ids}

    for dependency in dependencies:
        children[dependency.predecessor].append(dependency.successor)
        indegree[dependency.successor] += 1

    queue = [task_id for task_id, degree in indegree.items() if degree == 0]
    visited = 0
    while queue:
        current = queue.pop()
        visited += 1
        for child in children[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)

    if visited != len(task_ids):
        raise ValueError("task dependencies must form an acyclic graph")
