from __future__ import annotations

import pytest

from epipilot.core.models import Provenance
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


def test_project_contract_requires_exactly_one_goal() -> None:
    with pytest.raises(ValueError, match="exactly one"):
        ProjectContract(
            project_id="p1",
            requirements=(
                _requirement(RequirementKind.GOAL, "Goal A"),
                _requirement(RequirementKind.GOAL, "Goal B"),
            ),
        )


def test_project_contract_is_not_execution_ready_without_success_criterion() -> None:
    contract = ProjectContract(
        project_id="p1",
        requirements=(_requirement(RequirementKind.GOAL, "Improve throughput"),),
    )

    assert not contract.execution_ready


def test_hard_requirements_exclude_soft_preferences() -> None:
    goal = _requirement(RequirementKind.GOAL, "Improve throughput")
    success = _requirement(RequirementKind.SUCCESS_CRITERION, ">= 25% improvement")
    hard = _requirement(RequirementKind.HARD_CONSTRAINT, "Do not modify evaluator")
    soft = _requirement(RequirementKind.SOFT_PREFERENCE, "Prefer small diffs")
    contract = ProjectContract(
        project_id="p1",
        requirements=(goal, success, hard, soft),
    )

    assert contract.execution_ready
    assert contract.hard_requirements() == (goal, success, hard)
