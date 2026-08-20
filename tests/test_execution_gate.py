from __future__ import annotations

from epipilot.core.models import Provenance
from epipilot.requirements.frontier import DecisionImpact, DecisionOwner, DecisionQuestion
from epipilot.requirements.gate import ExecutionGateStatus, evaluate_execution_gate
from epipilot.requirements.models import (
    ProjectContract,
    Requirement,
    RequirementKind,
    new_requirement_id,
)


def _requirement(kind: RequirementKind, statement: str) -> Requirement:
    return Requirement(
        id=new_requirement_id(),
        kind=kind,
        statement=statement,
        provenance=Provenance(source="user", scope="project"),
    )


def _ready_contract() -> ProjectContract:
    return ProjectContract(
        project_id="p1",
        requirements=(
            _requirement(RequirementKind.GOAL, "Improve throughput"),
            _requirement(RequirementKind.SUCCESS_CRITERION, "Increase throughput by >=25%"),
        ),
    )


def test_incomplete_contract_blocks_autonomous_execution() -> None:
    contract = ProjectContract(
        project_id="p1",
        requirements=(_requirement(RequirementKind.GOAL, "Improve throughput"),),
    )

    gate = evaluate_execution_gate(contract)

    assert gate.status is ExecutionGateStatus.CONTRACT_INCOMPLETE
    assert not gate.ready


def test_user_owned_open_decision_blocks_execution() -> None:
    question = DecisionQuestion(
        question="May the public API change?",
        owner=DecisionOwner.USER,
        impact=DecisionImpact.HIGH,
    )

    gate = evaluate_execution_gate(_ready_contract(), (question,))

    assert gate.status is ExecutionGateStatus.USER_DECISION_REQUIRED
    assert gate.blocking_questions == (question,)


def test_system_empirical_unknown_does_not_interrupt_user() -> None:
    question = DecisionQuestion(
        question="Is rollout the primary bottleneck?",
        owner=DecisionOwner.SYSTEM,
        impact=DecisionImpact.HIGH,
        cheaply_testable=True,
    )

    gate = evaluate_execution_gate(_ready_contract(), (question,))

    assert gate.ready
    assert gate.status is ExecutionGateStatus.READY
