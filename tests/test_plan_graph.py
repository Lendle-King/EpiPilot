from __future__ import annotations

import pytest

from epipilot.core.models import Task, TaskStatus, new_evidence_id, new_task_id
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
        linked_evidence=(new_evidence_id(),),
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


def test_runtime_state_projection_does_not_create_fake_plan_version() -> None:
    task = Task(id=new_task_id(), objective="Profile rollout", status=TaskStatus.READY)
    graph = PlanGraph(version=3, tasks=(task,), dependencies=(), basis=_basis())
    running = Task(id=task.id, objective=task.objective, status=TaskStatus.RUNNING)

    projected = graph.with_task_state(running)

    assert projected.version == 3
    assert projected.basis == graph.basis
    assert projected.task(task.id).status is TaskStatus.RUNNING


def test_objective_change_requires_structural_replan() -> None:
    task = Task(id=new_task_id(), objective="Original", status=TaskStatus.READY)
    graph = PlanGraph(version=1, tasks=(task,), dependencies=(), basis=_basis())
    changed = Task(id=task.id, objective="Different", status=TaskStatus.READY)

    with pytest.raises(ValueError, match="structural replanning"):
        graph.with_task_state(changed)


def test_structural_replan_increments_topology_version() -> None:
    task = Task(id=new_task_id(), objective="Original", status=TaskStatus.READY)
    graph = PlanGraph(version=1, tasks=(task,), dependencies=(), basis=_basis())
    new_task = Task(id=new_task_id(), objective="New diagnostic", status=TaskStatus.READY)
    evidence_basis = (PlanBasis(kind=PlanBasisKind.EVIDENCE, reference_id="EV-7"),)

    replanned = graph.replan(
        tasks=(task, new_task),
        dependencies=(),
        basis=evidence_basis,
    )

    assert replanned.version == 2
    assert replanned.basis == evidence_basis


def test_direct_passed_task_without_evidence_is_rejected() -> None:
    with pytest.raises(ValueError, match="completion evidence"):
        Task(
            id=new_task_id(),
            objective="Pretend to be complete",
            status=TaskStatus.PASSED,
        )
