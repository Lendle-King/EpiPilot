from __future__ import annotations

import pytest

from epipilot.core.models import Task, TaskStatus, new_task_id
from epipilot.planning.graph import PlanBasis, PlanBasisKind, PlanGraph
from epipilot.scheduler.policy import SchedulingSignals, rank_runnable_tasks


def _plan(*tasks: Task) -> PlanGraph:
    return PlanGraph(
        version=1,
        tasks=tasks,
        dependencies=(),
        basis=(PlanBasis(PlanBasisKind.REQUIREMENT, "REQ-1"),),
    )


def test_scheduler_prioritizes_information_value_per_cost_and_risk() -> None:
    implementation = Task(
        id=new_task_id(),
        objective="Implement full optimization",
        status=TaskStatus.READY,
    )
    diagnostic = Task(
        id=new_task_id(),
        objective="Run discriminative profiling experiment",
        status=TaskStatus.READY,
    )
    plan = _plan(implementation, diagnostic)

    ranked = rank_runnable_tasks(
        plan,
        (
            SchedulingSignals(
                task_id=implementation.id,
                impact=1.0,
                information_gain=0.4,
                cost=4.0,
                risk=2.0,
            ),
            SchedulingSignals(
                task_id=diagnostic.id,
                impact=0.8,
                information_gain=1.0,
                cost=1.0,
                risk=1.0,
            ),
        ),
    )

    assert ranked[0].task == diagnostic


def test_scheduler_refuses_to_invent_missing_signals() -> None:
    task = Task(id=new_task_id(), objective="Run experiment", status=TaskStatus.READY)

    with pytest.raises(ValueError, match="explicit scheduling signals"):
        rank_runnable_tasks(_plan(task), ())
