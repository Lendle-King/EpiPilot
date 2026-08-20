"""Project-level sequential DAG runner for the EpiPilot V0 runtime."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from epipilot.core.models import Task
from epipilot.planning.graph import PlanGraph
from epipilot.runtime.engine import TaskRunResult
from epipilot.scheduler.policy import SchedulingSignals, rank_runnable_tasks


class TaskRunner(Protocol):
    """Port implemented by the single-task runtime."""

    async def run(
        self,
        task: Task,
        context: str,
        *,
        max_observations: int = 100,
    ) -> TaskRunResult:
        """Run one task attempt."""
        ...


class TaskContextProvider(Protocol):
    """Compile or retrieve bounded context for one scheduled task."""

    async def build(self, task: Task) -> str:
        """Return the executor context for ``task``."""
        ...


@dataclass(frozen=True, slots=True)
class ProjectRunResult:
    """Execution-state projection after a bounded project run."""

    plan: PlanGraph
    task_results: tuple[TaskRunResult, ...]


@dataclass(slots=True)
class ProjectRuntime:
    """Repeatedly schedule runnable DAG nodes and run them through verification."""

    task_runner: TaskRunner
    context_provider: TaskContextProvider
    scheduling_signals: tuple[SchedulingSignals, ...]

    async def run(
        self,
        plan: PlanGraph,
        *,
        max_tasks: int | None = None,
        max_observations_per_task: int = 100,
    ) -> ProjectRunResult:
        if max_tasks is not None and max_tasks < 1:
            raise ValueError("max_tasks must be positive when provided")
        if max_observations_per_task < 1:
            raise ValueError("max observations per task must be positive")

        current = plan
        results: list[TaskRunResult] = []

        while max_tasks is None or len(results) < max_tasks:
            ranked = rank_runnable_tasks(current, self.scheduling_signals)
            if not ranked:
                break

            scheduled = ranked[0]
            context = await self.context_provider.build(scheduled.task)
            result = await self.task_runner.run(
                scheduled.task,
                context,
                max_observations=max_observations_per_task,
            )
            current = current.with_task_state(result.task)
            results.append(result)

        return ProjectRunResult(plan=current, task_results=tuple(results))
