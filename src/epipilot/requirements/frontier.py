"""Decision-frontier policy for deciding when EpiPilot must interrupt the user."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DecisionOwner(StrEnum):
    """Authority responsible for resolving an open decision."""

    USER = "user"
    SYSTEM = "system"


class DecisionImpact(StrEnum):
    """Impact of choosing incorrectly."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class DecisionAction(StrEnum):
    """Next action chosen by the deterministic decision-frontier policy."""

    ASK_USER = "ask_user"
    INVESTIGATE = "investigate"
    USE_SAFE_DEFAULT = "use_safe_default"


@dataclass(frozen=True, slots=True)
class DecisionQuestion:
    """An unresolved project decision with explicit authority and reversibility."""

    question: str
    owner: DecisionOwner
    impact: DecisionImpact
    safely_inferable: bool = False
    cheaply_testable: bool = False
    reversible_default_available: bool = False

    def __post_init__(self) -> None:
        if not self.question.strip():
            raise ValueError("decision question must not be empty")


def decide_action(question: DecisionQuestion) -> DecisionAction:
    """Choose whether to ask, investigate, or safely default.

    User-owned, high-impact choices are never silently defaulted. Technical/system-owned
    questions should normally be investigated rather than pushed to the user.
    """
    if (
        question.owner is DecisionOwner.USER
        and question.impact is DecisionImpact.HIGH
        and not question.safely_inferable
    ):
        return DecisionAction.ASK_USER

    if question.owner is DecisionOwner.SYSTEM and question.cheaply_testable:
        return DecisionAction.INVESTIGATE

    if question.reversible_default_available:
        return DecisionAction.USE_SAFE_DEFAULT

    if question.owner is DecisionOwner.USER:
        return DecisionAction.ASK_USER

    return DecisionAction.INVESTIGATE
