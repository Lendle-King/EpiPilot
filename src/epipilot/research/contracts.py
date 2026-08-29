"""Typed contracts for evidence-driven research orchestration."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from epipilot.core.models import HypothesisId, TaskId
from epipilot.epistemics.models import UnknownId


class ResearchDirectiveKind(StrEnum):
    """High-level action exposed to an interactive research frontend."""

    ASK_USER = "ask_user"
    INVESTIGATE = "investigate"
    USE_SAFE_DEFAULT = "use_safe_default"
    EXECUTE = "execute"
    SYNTHESIZE = "synthesize"


@dataclass(frozen=True, slots=True)
class ExperimentPrediction:
    """Observable prediction tied to one preregistered hypothesis."""

    hypothesis_id: HypothesisId
    expected_observation: str
    falsification_condition: str

    def __post_init__(self) -> None:
        if not self.expected_observation.strip():
            raise ValueError("expected observation must not be empty")
        if not self.falsification_condition.strip():
            raise ValueError("falsification condition must not be empty")


@dataclass(frozen=True, slots=True)
class ExperimentContract:
    """Bounded minimum-discriminative experiment contract."""

    unknown_id: UnknownId
    objective: str
    hypothesis_ids: tuple[HypothesisId, ...]
    controlled_variables: tuple[str, ...]
    measurements: tuple[str, ...]
    predictions: tuple[ExperimentPrediction, ...]
    decision_rule: str
    budget: str
    resource_claims: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        for name, value in (
            ("objective", self.objective),
            ("decision_rule", self.decision_rule),
            ("budget", self.budget),
        ):
            if not value.strip():
                raise ValueError(f"experiment {name} must not be empty")
        if not self.hypothesis_ids:
            raise ValueError("experiment requires at least one hypothesis")
        if len(set(self.hypothesis_ids)) != len(self.hypothesis_ids):
            raise ValueError("experiment hypothesis ids must be unique")
        if not self.measurements:
            raise ValueError("experiment requires at least one measurement")
        if any(not item.strip() for item in self.controlled_variables):
            raise ValueError("controlled variables must not contain empty values")
        if any(not item.strip() for item in self.measurements):
            raise ValueError("measurements must not contain empty values")
        if any(not item.strip() for item in self.resource_claims):
            raise ValueError("resource claims must not contain empty values")

        prediction_ids = tuple(prediction.hypothesis_id for prediction in self.predictions)
        if set(prediction_ids) != set(self.hypothesis_ids):
            raise ValueError("experiment predictions must cover every hypothesis exactly once")
        if len(prediction_ids) != len(set(prediction_ids)):
            raise ValueError("experiment predictions must not duplicate a hypothesis")


@dataclass(frozen=True, slots=True)
class ResearchDirective:
    """Deterministic next-step recommendation derived from canonical state."""

    kind: ResearchDirectiveKind
    reason: str
    canonical_event_version: int
    questions: tuple[str, ...] = ()
    unknown_id: UnknownId | None = None
    task_id: TaskId | None = None

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("research directive reason must not be empty")
        if self.canonical_event_version < 0:
            raise ValueError("canonical event version must not be negative")
        if any(not item.strip() for item in self.questions):
            raise ValueError("directive questions must not contain empty values")
        if self.kind is ResearchDirectiveKind.ASK_USER and not self.questions:
            raise ValueError("ASK_USER directive requires at least one question")
        if (
            self.kind
            in {
                ResearchDirectiveKind.INVESTIGATE,
                ResearchDirectiveKind.USE_SAFE_DEFAULT,
            }
            and self.unknown_id is None
        ):
            raise ValueError(f"{self.kind.value} directive requires an unknown id")
        if self.kind is ResearchDirectiveKind.EXECUTE and self.task_id is None:
            raise ValueError("EXECUTE directive requires a task id")
