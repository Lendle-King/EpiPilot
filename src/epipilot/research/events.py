"""Typed research events, stored in EpiPilot's existing canonical event stream."""

from epipilot.events.payloads import EventPayload
from epipilot.research.intelligence_models import ResearchPolicy
from epipilot.research.models import (
    Cost,
    ExperimentProposal,
    Observation,
    ResearchGoal,
    ResearchNode,
    Reservation,
    Review,
    Settlement,
)


class ResearchConfigured(EventPayload):
    goal: ResearchGoal
    nodes: tuple[ResearchNode, ...]


class ResearchBudgetExtended(EventPayload):
    basis_ref: str
    additions: tuple[Cost, ...]


class ResearchGoalRevised(EventPayload):
    previous_goal: ResearchGoal
    goal: ResearchGoal
    basis_ref: str
    policy: ResearchPolicy | None = None


class ProposalAdded(EventPayload):
    proposal: ExperimentProposal
    basis_ref: str


class ExperimentReserved(EventPayload):
    reservation: Reservation


class ExperimentObserved(EventPayload):
    observation: Observation


class ExperimentReviewed(EventPayload):
    review: Review


class BudgetSettled(EventPayload):
    settlement: Settlement


class ResearchNodeAdded(EventPayload):
    node: ResearchNode
    companion_nodes: tuple[ResearchNode, ...] = ()
