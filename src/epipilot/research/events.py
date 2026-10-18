"""Typed research events, stored in EpiPilot's existing canonical event stream."""

from epipilot.events.payloads import EventPayload
from epipilot.research.models import (
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
