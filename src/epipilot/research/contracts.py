"""Typed contracts for evidence-driven research orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import NewType
from uuid import UUID, uuid4

from epipilot.core.models import EvidenceId, HypothesisId, TaskId
from epipilot.epistemics.models import UnknownId

ExperimentId = NewType("ExperimentId", UUID)


def new_experiment_id() -> ExperimentId:
    """Create a new opaque experiment identifier."""
    return ExperimentId(uuid4())


class ResearchDirectiveKind(StrEnum):
    """High-level action exposed to an interactive research frontend."""

    ASK_USER = "ask_user"
    INVESTIGATE = "investigate"
    RUN_EXPERIMENT = "run_experiment"
    USE_SAFE_DEFAULT = "use_safe_default"
    EXECUTE = "execute"
    SYNTHESIZE = "synthesize"


class ExperimentStatus(StrEnum):
    """Lifecycle states for one preregistered research experiment."""

    PREREGISTERED = "preregistered"
    CONCLUDED = "concluded"
    INCONCLUSIVE = "inconclusive"


class SynthesisDimension(StrEnum):
    """Mandatory dimensions of a goal-conditioned completion synthesis."""

    OUTCOME_AGAINST_GOAL = "outcome_against_goal"
    SUBJECT_NATURE = "subject_nature"
    HYPOTHESIS_LANDSCAPE = "hypothesis_landscape"
    CAUSAL_OR_STRUCTURAL_EXPLANATION = "causal_or_structural_explanation"
    DECISIVE_EVIDENCE = "decisive_evidence"
    UNCERTAINTY_AND_ALTERNATIVES = "uncertainty_and_alternatives"
    GENERALIZATION_BOUNDARIES = "generalization_boundaries"
    REOPEN_CONDITIONS = "reopen_conditions"


_REQUIRED_SYNTHESIS_DIMENSIONS = (
    SynthesisDimension.OUTCOME_AGAINST_GOAL,
    SynthesisDimension.SUBJECT_NATURE,
    SynthesisDimension.HYPOTHESIS_LANDSCAPE,
    SynthesisDimension.CAUSAL_OR_STRUCTURAL_EXPLANATION,
    SynthesisDimension.DECISIVE_EVIDENCE,
    SynthesisDimension.UNCERTAINTY_AND_ALTERNATIVES,
    SynthesisDimension.GENERALIZATION_BOUNDARIES,
    SynthesisDimension.REOPEN_CONDITIONS,
)

_GOAL_CONDITIONED_EXPLORATION_QUESTIONS = (
    "Under the current goal, what kind of problem, system, or phenomenon is this?",
    "Which structural properties, mechanisms, constraints, invariants, or tradeoffs matter to the goal?",
    "Which competing hypotheses explain the observed behavior, and what is each hypothesis status?",
    "Which alternative explanations remain plausible, and what evidence would change the conclusion?",
)


@dataclass(frozen=True, slots=True)
class GoalConditionedSynthesisContract:
    """Mandatory epistemic report contract attached to every completion synthesis.

    Completion is not merely a statement that the requested output exists. The report must also
    characterize the subject and its hypothesis landscape relative to the user's goal. The fixed
    dimensions cannot be weakened by a caller.
    """

    goal: str
    success_criteria: tuple[str, ...]
    required_dimensions: tuple[SynthesisDimension, ...] = field(
        default=_REQUIRED_SYNTHESIS_DIMENSIONS,
        init=False,
    )
    exploration_questions: tuple[str, ...] = field(
        default=_GOAL_CONDITIONED_EXPLORATION_QUESTIONS,
        init=False,
    )

    def __post_init__(self) -> None:
        if not self.goal.strip():
            raise ValueError("synthesis goal must not be empty")
        if not self.success_criteria:
            raise ValueError("synthesis requires at least one success criterion")
        if any(not item.strip() for item in self.success_criteria):
            raise ValueError("synthesis success criteria must not contain empty values")
        if len(set(self.success_criteria)) != len(self.success_criteria):
            raise ValueError("synthesis success criteria must be unique")


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
class ExperimentRecord:
    """Canonical projection of a preregistered experiment and its verified outcome."""

    id: ExperimentId
    contract: ExperimentContract
    status: ExperimentStatus = ExperimentStatus.PREREGISTERED
    evidence_ids: tuple[EvidenceId, ...] = ()
    conclusion: str | None = None

    def __post_init__(self) -> None:
        if self.status is ExperimentStatus.PREREGISTERED:
            if self.evidence_ids or self.conclusion is not None:
                raise ValueError("preregistered experiment cannot already carry an outcome")
            return

        if not self.evidence_ids:
            raise ValueError("concluded experiment requires independently verified evidence")
        if self.conclusion is None or not self.conclusion.strip():
            raise ValueError("concluded experiment requires a non-empty conclusion")


@dataclass(frozen=True, slots=True)
class ResearchDirective:
    """Deterministic next-step recommendation derived from canonical state."""

    kind: ResearchDirectiveKind
    reason: str
    canonical_event_version: int
    questions: tuple[str, ...] = ()
    unknown_id: UnknownId | None = None
    experiment_id: ExperimentId | None = None
    task_id: TaskId | None = None
    synthesis_contract: GoalConditionedSynthesisContract | None = None

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
        if self.kind is ResearchDirectiveKind.RUN_EXPERIMENT and self.experiment_id is None:
            raise ValueError("RUN_EXPERIMENT directive requires an experiment id")
        if self.kind is ResearchDirectiveKind.EXECUTE and self.task_id is None:
            raise ValueError("EXECUTE directive requires a task id")
        if self.kind is ResearchDirectiveKind.SYNTHESIZE and self.synthesis_contract is None:
            raise ValueError("SYNTHESIZE directive requires a goal-conditioned synthesis contract")
        if (
            self.kind is not ResearchDirectiveKind.SYNTHESIZE
            and self.synthesis_contract is not None
        ):
            raise ValueError("only SYNTHESIZE directives may carry a synthesis contract")
