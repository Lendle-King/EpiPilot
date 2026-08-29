"""Immutable canonical project-state projection rebuilt from project events."""

from __future__ import annotations

from dataclasses import dataclass

from epipilot.core.models import Evidence, EvidenceId, Task, TaskId
from epipilot.epistemics.models import Fact, Hypothesis, Unknown
from epipilot.planning.graph import PlanGraph
from epipilot.requirements.models import Decision, Requirement
from epipilot.research.contracts import ExperimentId, ExperimentRecord


@dataclass(frozen=True, slots=True)
class SessionState:
    task_id: TaskId
    session_id: str
    last_executor_state: str | None = None
    changed_file_count: int = 0
    artifact_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ContextRecord:
    task_id: TaskId
    context_id: str
    included_item_ids: tuple[str, ...]
    excluded_item_ids: tuple[str, ...]
    token_cost: int
    token_budget: int
    compiler_version: str


@dataclass(frozen=True, slots=True)
class VerificationRecord:
    task_id: TaskId
    passed: bool
    evidence_id: EvidenceId | None = None
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class EvidenceLink:
    evidence_id: EvidenceId
    task_id: TaskId


@dataclass(frozen=True, slots=True)
class ProjectState:
    """Canonical aggregate reconstructed exclusively from an ordered event stream."""

    project_id: str
    requirements: tuple[Requirement, ...] = ()
    decisions: tuple[Decision, ...] = ()
    unknowns: tuple[Unknown, ...] = ()
    hypotheses: tuple[Hypothesis, ...] = ()
    experiments: tuple[ExperimentRecord, ...] = ()
    facts: tuple[Fact, ...] = ()
    evidence: tuple[Evidence, ...] = ()
    evidence_links: tuple[EvidenceLink, ...] = ()
    tasks: tuple[Task, ...] = ()
    plans: tuple[PlanGraph, ...] = ()
    sessions: tuple[SessionState, ...] = ()
    contexts: tuple[ContextRecord, ...] = ()
    verifications: tuple[VerificationRecord, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    event_version: int = 0

    def __post_init__(self) -> None:
        if not self.project_id.strip():
            raise ValueError("project_id must not be empty")
        if self.event_version < 0:
            raise ValueError("event_version must not be negative")

    def task(self, task_id: TaskId) -> Task:
        for task in self.tasks:
            if task.id == task_id:
                return task
        raise KeyError(task_id)

    def evidence_item(self, evidence_id: EvidenceId) -> Evidence:
        for item in self.evidence:
            if item.id == evidence_id:
                return item
        raise KeyError(evidence_id)

    def experiment(self, experiment_id: ExperimentId) -> ExperimentRecord:
        for experiment in self.experiments:
            if experiment.id == experiment_id:
                return experiment
        raise KeyError(experiment_id)

    @property
    def current_plan(self) -> PlanGraph | None:
        return self.plans[-1] if self.plans else None
