"""Project execution gate derived from canonical requirements and open decisions."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from epipilot.requirements.frontier import DecisionAction, DecisionQuestion, decide_action
from epipilot.requirements.models import ProjectContract


class ExecutionGateStatus(StrEnum):
    """Whether autonomous project execution may begin or continue."""

    READY = "ready"
    CONTRACT_INCOMPLETE = "contract_incomplete"
    USER_DECISION_REQUIRED = "user_decision_required"


@dataclass(frozen=True, slots=True)
class ExecutionGate:
    """Deterministic gate result with user-facing blocking reasons."""

    status: ExecutionGateStatus
    blocking_questions: tuple[DecisionQuestion, ...] = ()

    @property
    def ready(self) -> bool:
        return self.status is ExecutionGateStatus.READY


def evaluate_execution_gate(
    contract: ProjectContract,
    open_questions: tuple[DecisionQuestion, ...] = (),
) -> ExecutionGate:
    """Permit autonomy only when success is defined and user decisions are resolved.

    System-owned empirical questions do not block execution; they should become
    investigations/experiments. User-owned questions selected by the Decision Frontier
    remain explicit interrupts and fail closed.
    """
    if not contract.execution_ready:
        return ExecutionGate(status=ExecutionGateStatus.CONTRACT_INCOMPLETE)

    blocking = tuple(
        question
        for question in open_questions
        if decide_action(question) is DecisionAction.ASK_USER
    )
    if blocking:
        return ExecutionGate(
            status=ExecutionGateStatus.USER_DECISION_REQUIRED,
            blocking_questions=blocking,
        )

    return ExecutionGate(status=ExecutionGateStatus.READY)
