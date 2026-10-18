"""Reusable pure implementations of objective, interpretation and fidelity ports."""

from dataclasses import dataclass

from epipilot.research.models import (
    Assessment,
    ExecutionStatus,
    ExperimentProposal,
    Fidelity,
    MetricConstraint,
    Observation,
    Outcome,
)
from epipilot.research.ports import FidelityPolicy
from epipilot.state.project import ProjectState


@dataclass(frozen=True)
class VerifiedFidelityPolicy:
    """Only explicit comparable stages use this gate; no automatic ASHA pruning."""

    def can_upgrade(
        self,
        proposal: ExperimentProposal,
        observation: Observation,
        assessment: Assessment,
        target: Fidelity,
    ) -> bool:
        return (
            target > proposal.fidelity
            and observation.proposal_id == proposal.id == assessment.proposal_id
            and observation.execution is ExecutionStatus.SUCCEEDED
            and observation.samples >= proposal.minimum_samples
            and assessment.verified
            and assessment.outcome is Outcome.SUPPORT
            and assessment.reliability >= proposal.minimum_reliability
            and assessment.scope == proposal.scope
        )


@dataclass(frozen=True)
class MetricObjective:
    metric: str
    maximize: bool = True

    def utility(self, observation: Observation) -> float:
        value = next(m.value for m in observation.metrics if m.name == self.metric)
        return value if self.maximize else -value

    def met(self, state: "ProjectState") -> bool:
        from epipilot.research.policy import goal_met

        return goal_met(state)


@dataclass(frozen=True)
class ThresholdInterpreter:
    """Observation interpretation is a proposal; independent verification is separate."""

    constraints: tuple[MetricConstraint, ...]

    def interpret(self, proposal: ExperimentProposal, observation: Observation) -> Outcome:
        if observation.execution is ExecutionStatus.BLOCKED:
            return Outcome.BLOCKED
        if observation.execution is not ExecutionStatus.SUCCEEDED:
            return Outcome.INVALID
        if observation.samples < proposal.minimum_samples or not self.constraints:
            return Outcome.INCONCLUSIVE
        if all(c.accepts(observation.metrics) for c in self.constraints):
            return Outcome.SUPPORT
        return Outcome.REFUTE if proposal.definitive_negative else Outcome.INCONCLUSIVE


DEFAULT_FIDELITY: FidelityPolicy = VerifiedFidelityPolicy()
