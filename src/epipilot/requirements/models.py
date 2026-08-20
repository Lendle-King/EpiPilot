"""Canonical project requirements and decision-ledger models."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import NewType
from uuid import UUID, uuid4

from epipilot.core.models import Provenance, RequirementId

DecisionId = NewType("DecisionId", UUID)


def new_requirement_id() -> RequirementId:
    return RequirementId(uuid4())


def new_decision_id() -> DecisionId:
    return DecisionId(uuid4())


class RequirementKind(StrEnum):
    """Kinds of user/project intent kept in the canonical contract."""

    GOAL = "goal"
    SUCCESS_CRITERION = "success_criterion"
    HARD_CONSTRAINT = "hard_constraint"
    SOFT_PREFERENCE = "soft_preference"
    BUDGET = "budget"
    FORBIDDEN_ACTION = "forbidden_action"


class DecisionAuthority(StrEnum):
    """Authority that made a canonical project decision."""

    USER = "user"
    SYSTEM = "system"


@dataclass(frozen=True, slots=True)
class Requirement:
    """One canonical project requirement with provenance."""

    id: RequirementId
    kind: RequirementKind
    statement: str
    provenance: Provenance

    def __post_init__(self) -> None:
        if not self.statement.strip():
            raise ValueError("requirement statement must not be empty")


@dataclass(frozen=True, slots=True)
class Decision:
    """Resolved choice with authority, rationale, and explicit basis references."""

    id: DecisionId
    question: str
    choice: str
    authority: DecisionAuthority
    rationale: str
    basis_refs: tuple[str, ...] = ()
    reversible: bool = True

    def __post_init__(self) -> None:
        if not self.question.strip():
            raise ValueError("decision question must not be empty")
        if not self.choice.strip():
            raise ValueError("decision choice must not be empty")
        if not self.rationale.strip():
            raise ValueError("decision rationale must not be empty")
        if any(not ref.strip() for ref in self.basis_refs):
            raise ValueError("decision basis references must not be empty")


@dataclass(frozen=True, slots=True)
class ProjectContract:
    """Canonical user-visible contract governing autonomous execution."""

    project_id: str
    requirements: tuple[Requirement, ...]
    decisions: tuple[Decision, ...] = ()

    def __post_init__(self) -> None:
        if not self.project_id.strip():
            raise ValueError("project id must not be empty")

        requirement_ids = {requirement.id for requirement in self.requirements}
        if len(requirement_ids) != len(self.requirements):
            raise ValueError("project requirements must have unique ids")

        decision_ids = {decision.id for decision in self.decisions}
        if len(decision_ids) != len(self.decisions):
            raise ValueError("project decisions must have unique ids")

        goals = [item for item in self.requirements if item.kind is RequirementKind.GOAL]
        if len(goals) != 1:
            raise ValueError("project contract requires exactly one canonical goal")

    @property
    def execution_ready(self) -> bool:
        """Return whether the contract has at least one explicit success criterion."""
        return any(item.kind is RequirementKind.SUCCESS_CRITERION for item in self.requirements)

    def hard_requirements(self) -> tuple[Requirement, ...]:
        """Return requirements that must be mandatory during context compilation."""
        hard_kinds = {
            RequirementKind.GOAL,
            RequirementKind.SUCCESS_CRITERION,
            RequirementKind.HARD_CONSTRAINT,
            RequirementKind.BUDGET,
            RequirementKind.FORBIDDEN_ACTION,
        }
        return tuple(item for item in self.requirements if item.kind in hard_kinds)
