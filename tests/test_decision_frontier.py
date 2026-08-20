from __future__ import annotations

from epipilot.requirements.frontier import (
    DecisionAction,
    DecisionImpact,
    DecisionOwner,
    DecisionQuestion,
    decide_action,
)


def test_high_impact_user_decision_requires_user() -> None:
    question = DecisionQuestion(
        question="May the project change the public API?",
        owner=DecisionOwner.USER,
        impact=DecisionImpact.HIGH,
        cheaply_testable=True,
        reversible_default_available=True,
    )

    assert decide_action(question) is DecisionAction.ASK_USER


def test_system_owned_empirical_question_is_investigated() -> None:
    question = DecisionQuestion(
        question="Is rollout the primary bottleneck?",
        owner=DecisionOwner.SYSTEM,
        impact=DecisionImpact.HIGH,
        cheaply_testable=True,
    )

    assert decide_action(question) is DecisionAction.INVESTIGATE


def test_low_impact_reversible_choice_can_use_default() -> None:
    question = DecisionQuestion(
        question="Which temporary report filename should be used?",
        owner=DecisionOwner.SYSTEM,
        impact=DecisionImpact.LOW,
        reversible_default_available=True,
    )

    assert decide_action(question) is DecisionAction.USE_SAFE_DEFAULT
