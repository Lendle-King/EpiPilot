"""Domain-independent typed adapter ports. No agent or ETF imports."""

from pathlib import Path
from typing import Protocol

from epipilot.research.models import (
    Assessment,
    Cost,
    ExperimentProposal,
    Fidelity,
    Observation,
    Outcome,
)
from epipilot.state.project import ProjectState


class ExperimentRunner(Protocol):
    def run(self, proposal: ExperimentProposal, output: Path) -> Observation: ...


class EvidenceVerifier(Protocol):
    def verify(self, proposal: ExperimentProposal, observation: Observation) -> Assessment: ...


class CostEstimator(Protocol):
    def estimate(self, proposal: ExperimentProposal) -> tuple[Cost, ...]: ...


class ResearchObjective(Protocol):
    def utility(self, observation: Observation) -> float: ...
    def met(self, state: ProjectState) -> bool: ...


class FidelityPolicy(Protocol):
    def can_upgrade(
        self,
        proposal: ExperimentProposal,
        observation: Observation,
        assessment: Assessment,
        target: Fidelity,
    ) -> bool: ...


class ResultInterpreter(Protocol):
    def interpret(self, proposal: ExperimentProposal, observation: Observation) -> Outcome: ...
