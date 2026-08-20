"""Deterministic task-priority policy for runnable plan nodes."""

from __future__ import annotations

from dataclasses import dataclass

from epipilot.core.models import Task, TaskId
from epipilot.planning.graph import PlanGraph


@dataclass(frozen=True, slots=True)
class SchedulingSignals:
    """Explicit scheduling factors for one task.

    Normalized benefit factors are in ``[0, 1]``. Cost and risk must be positive and can
    use any project-consistent scale.
    """

    task_id: TaskId
    impact: float = 1.0
    unblocking: float = 1.0
    information_gain: float = 1.0
    urgency: float = 1.0
    cost: float = 1.0
    risk: float = 1.0

    def __post_init__(self) -> None:
        for name, value in (
            ("impact", self.impact),
            ("unblocking", self.unblocking),
            ("information_gain", self.information_gain),
            ("urgency", self.urgency),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be within [0, 1]")
        if self.cost <= 0:
            raise ValueError("scheduling cost must be positive")
        if self.risk <= 0:
            raise ValueError("scheduling risk must be positive")

    @property
    def priority(self) -> float:
        """Return benefit per unit cost/risk according to the V0 heuristic."""
        benefit = self.impact * self.unblocking * self.information_gain * self.urgency
        return benefit / (self.cost * self.risk)


@dataclass(frozen=True, slots=True)
class ScheduledTask:
    """Runnable task paired with its auditable priority score."""

    task: Task
    signals: SchedulingSignals

    @property
    def priority(self) -> float:
        return self.signals.priority


def rank_runnable_tasks(
    plan: PlanGraph,
    signals: tuple[SchedulingSignals, ...],
) -> tuple[ScheduledTask, ...]:
    """Rank runnable tasks without inventing missing scheduling assumptions."""
    signal_by_id = {item.task_id: item for item in signals}
    if len(signal_by_id) != len(signals):
        raise ValueError("scheduling signals must contain unique task ids")

    plan_task_ids = {task.id for task in plan.tasks}
    unknown_signal_ids = set(signal_by_id) - plan_task_ids
    if unknown_signal_ids:
        raise ValueError("scheduling signals reference tasks outside the plan")

    runnable = plan.runnable_tasks()
    missing = [task.id for task in runnable if task.id not in signal_by_id]
    if missing:
        raise ValueError("every runnable task requires explicit scheduling signals")

    ranked = [ScheduledTask(task=task, signals=signal_by_id[task.id]) for task in runnable]
    ranked.sort(key=lambda item: (-item.priority, str(item.task.id)))
    return tuple(ranked)
