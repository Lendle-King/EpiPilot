from __future__ import annotations

from dataclasses import dataclass, field

import pytest

from epipilot.core.models import Task, TaskStatus, new_evidence_id, new_task_id
from epipilot.planning.graph import PlanBasis, PlanBasisKind, PlanGraph, TaskDependency
from epipilot.runtime.engine import TaskRunResult
from epipilot.runtime.project import ProjectRuntime
from epipilot.scheduler.policy import SchedulingSignals


@dataclass(slots=True)
class FakeContextProvider:
    built_for: list[str] = field(default_factory=list)

    async def build(self, task: Task) -> str:
        self.built_for.append(task.objective)
        return f"context for {task.objective}"


@dataclass(slots=True)
class FakeTaskRunner:
    outcomes: dict[str, TaskStatus]
    run_order: list[str] = field(default_factory=list)

    async def run(
        self,
        task: Task,
        context: str,
        *,
        max_observations: int = 100,
    ) -> TaskRunResult:
        del context, max_observations
        self.run_order.append(task.objective)
        status = self.outcomes.get(task.objective, TaskStatus.PASSED)
        evidence = (new_evidence_id(),) if status is TaskStatus.PASSED else ()
        updated = Task(
            id=task.id,
            objective=task.objective,
            status=status,
            linked_evidence=evidence,
        )
        return TaskRunResult(
            task=updated,
            session_id=f"session-{len(self.run_order)}",
            observation=None,
            verification=None,
        )


def _basis() -> tuple[PlanBasis, ...]:
    return (PlanBasis(PlanBasisKind.REQUIREMENT, "REQ-1"),)


def _signals(*tasks: Task) -> tuple[SchedulingSignals, ...]:
    return tuple(SchedulingSignals(task_id=task.id) for task in tasks)


@pytest.mark.asyncio
async def test_project_runtime_unlocks_successor_only_after_predecessor_passes() -> None:
    first = Task(
        id=new_task_id(),
        objective="Reproduce baseline",
        status=TaskStatus.READY,
    )
    second = Task(
        id=new_task_id(),
        objective="Profile bottleneck",
        status=TaskStatus.READY,
    )
    plan = PlanGraph(
        version=4,
        tasks=(first, second),
        dependencies=(TaskDependency(first.id, second.id),),
        basis=_basis(),
    )
    runner = FakeTaskRunner(outcomes={})
    contexts = FakeContextProvider()
    runtime = ProjectRuntime(
        task_runner=runner,
        context_provider=contexts,
        scheduling_signals=_signals(first, second),
    )

    result = await runtime.run(plan)

    assert runner.run_order == ["Reproduce baseline", "Profile bottleneck"]
    assert contexts.built_for == runner.run_order
    assert result.plan.task(first.id).status is TaskStatus.PASSED
    assert result.plan.task(second.id).status is TaskStatus.PASSED
    assert result.plan.version == 4


@pytest.mark.asyncio
async def test_failed_branch_does_not_unlock_dependent_but_independent_branch_continues() -> None:
    failing = Task(id=new_task_id(), objective="Risky approach", status=TaskStatus.READY)
    dependent = Task(id=new_task_id(), objective="Build on risky approach", status=TaskStatus.READY)
    independent = Task(
        id=new_task_id(), objective="Independent diagnostic", status=TaskStatus.READY
    )
    plan = PlanGraph(
        version=1,
        tasks=(failing, dependent, independent),
        dependencies=(TaskDependency(failing.id, dependent.id),),
        basis=_basis(),
    )
    runner = FakeTaskRunner(outcomes={"Risky approach": TaskStatus.FAILED})
    runtime = ProjectRuntime(
        task_runner=runner,
        context_provider=FakeContextProvider(),
        scheduling_signals=(
            SchedulingSignals(task_id=failing.id, urgency=1.0),
            SchedulingSignals(task_id=dependent.id),
            SchedulingSignals(task_id=independent.id, urgency=0.5),
        ),
    )

    result = await runtime.run(plan)

    assert runner.run_order == ["Risky approach", "Independent diagnostic"]
    assert result.plan.task(failing.id).status is TaskStatus.FAILED
    assert result.plan.task(dependent.id).status is TaskStatus.READY
    assert result.plan.task(independent.id).status is TaskStatus.PASSED


@pytest.mark.asyncio
async def test_project_runtime_respects_task_budget() -> None:
    first = Task(id=new_task_id(), objective="First", status=TaskStatus.READY)
    second = Task(id=new_task_id(), objective="Second", status=TaskStatus.READY)
    plan = PlanGraph(version=1, tasks=(first, second), dependencies=(), basis=_basis())
    runner = FakeTaskRunner(outcomes={})
    runtime = ProjectRuntime(
        task_runner=runner,
        context_provider=FakeContextProvider(),
        scheduling_signals=_signals(first, second),
    )

    result = await runtime.run(plan, max_tasks=1)

    assert len(result.task_results) == 1
    assert len(runner.run_order) == 1
