from __future__ import annotations

import pytest

from epipilot.core.models import Task, TaskStatus, new_task_id
from epipilot.planning.graph import PlanBasis, PlanBasisKind, PlanGraph, TaskDependency


def _basis() -> tuple[PlanBasis, ...]:
    return (PlanBasis(kind=PlanBasisKind.REQUIREMENT, reference_id="REQ-1"),)


def test_plan_requires_traceable_basis() -> None:
    task = Task(id=new_task_id(), objective="Reproduce baseline")

    with pytest.raises(ValueError, match="traceable basis"):
        PlanGraph(version=1, tasks=(task,), dependencies=(), basis=())


def test_plan_rejects_cycles() -> None:
    first = Task(id=new_task_id(), objective="First")
    second = Task(id=new_task_id(), objective="Second")

    with pytest.raises(ValueError, match="acyclic"):
        PlanGraph(
            version=1,
            tasks=(first, second),
            dependencies=(
                TaskDependency(first.id, second.id),
                TaskDependency(second.id, first.id),
            ),
            basis=_basis(),
        )


def test_runnable_tasks_require_verified_predecessors() -> None:
    first = Task(
        id=new_task_id(),
        objective="Reproduce baseline",
        status=TaskStatus.PASSED,
    )
    second = Task(
        id=new_task_id(),
        objective="Profile rollout",
        status=TaskStatus.READY,
    )
    graph = PlanGraph(
        version=1,
        tasks=(first, second),
        dependencies=(TaskDependency(first.id, second.id),),
        basis=_basis(),
    )

    assert graph.runnable_tasks() == (second,)


def test_ready_task_is_not_runnable_until_dependency_passes() -> None:
    first = Task(
        id=new_task_id(),
        objective="Reproduce baseline",
        status=TaskStatus.RUNNING,
    )
    second = Task(
        id=new_task_id(),
        objective="Profile rollout",
        status=TaskStatus.READY,
    )
    graph = PlanGraph(
        version=1,
        tasks=(first, second),
        dependencies=(TaskDependency(first.id, second.id),),
        basis=_basis(),
    )

    assert graph.runnable_tasks() == ()
