from contextlib import contextmanager
from uuid import uuid4

import pytest
from test_budget_extension import configured_engine
from test_scout import ready

from epipilot.core.events import EventType
from epipilot.events.payloads import DecisionMadePayload
from epipilot.requirements.models import DecisionAuthority
from epipilot.research.events import ResearchBudgetExtended, ResearchGoalRevised
from epipilot.research.models import Cost, MetricConstraint
from epipilot.state.replay import replay_project


@contextmanager
def rejected(message):
    with pytest.raises(ValueError) as error:
        yield
    assert message in str(error.value.__cause__ or error.value)


def authorize(engine, authority=DecisionAuthority.USER):
    identifier = uuid4()
    engine.append(
        EventType.DECISION_MADE,
        DecisionMadePayload(
            decision_id=identifier,
            question="Revise performance target?",
            choice="CAGR >= 15%, absolute drawdown <= 15%",
            authority=authority,
            rationale="Synthetic explicit user request.",
        ),
    )
    return str(identifier)


def revised(engine):
    return engine.state.research.goal.model_copy(
        update={"constraints": (MetricConstraint(name="mdd_abs", maximum=0.15),)}
    )


def test_revision_preserves_history_costs_and_replays(tmp_path):
    engine = configured_engine(tmp_path)
    basis = authorize(engine)
    engine.append(
        EventType.RESEARCH_BUDGET_EXTENDED,
        ResearchBudgetExtended(basis_ref=basis, additions=(Cost(unit="seconds", amount=5),)),
    )
    engine.reserve_next()
    engine.cancel("first", (Cost(unit="seconds", amount=2),), "Measured synthetic work")
    previous = engine.state.research
    budget = engine.budget()
    prefix = engine.store.load("p")
    engine.revise_goal(revised(engine), basis)
    assert engine.state.research == previous.model_copy(update={"goal": revised(engine)})
    assert engine.budget() == budget == {"spent": {"seconds": 2}, "remaining": {"seconds": 13}}
    assert engine.store.load("p")[: len(prefix)] == prefix
    assert replay_project("p", engine.store.load("p")) == engine.state
    assert replay_project("p", prefix).research == previous


@pytest.mark.parametrize("authority", [DecisionAuthority.SYSTEM, None])
def test_revision_rejects_missing_or_non_user_authority(tmp_path, authority):
    engine = configured_engine(tmp_path)
    basis = authorize(engine, authority) if authority else str(uuid4())
    with rejected("user decision"):
        engine.revise_goal(revised(engine), basis)


def test_revision_rejects_stale_goal_budget_change_and_active_work(tmp_path):
    engine = configured_engine(tmp_path)
    basis = authorize(engine)
    original = engine.state.research.goal
    with rejected("budgets/resources"):
        engine.revise_goal(
            original.model_copy(update={"budgets": (Cost(unit="seconds", amount=99),)}), basis
        )
    engine.append(
        EventType.RESEARCH_BUDGET_EXTENDED,
        ResearchBudgetExtended(basis_ref=basis, additions=(Cost(unit="seconds", amount=5),)),
    )
    assert engine.reserve_next() is not None
    with rejected("active"):
        engine.revise_goal(revised(engine), basis)
    engine.cancel("first", (Cost(unit="seconds", amount=1),), "Synthetic recovery")
    engine.revise_goal(revised(engine), basis)
    with rejected("stale"):
        engine.append(
            EventType.RESEARCH_GOAL_REVISED,
            ResearchGoalRevised(previous_goal=original, goal=original, basis_ref=basis),
        )


def test_revision_rotates_decision_utility_atomically_and_rejects_active_scout(tmp_path):
    engine, _, _, scout = ready(tmp_path)
    basis = authorize(engine)
    old_policy = engine.state.intelligence.policy
    policy = old_policy.model_copy(
        update={"id": "goal-v2", "basis_ref": basis, "utility_version": "new-goal-v2"}
    )
    with rejected("new utility"):
        engine.revise_goal(revised(engine), basis)
    with rejected("new utility"):
        engine.revise_goal(revised(engine), basis, policy=old_policy)
    prefix = engine.store.load(engine.project_id)
    engine.revise_goal(revised(engine), basis, policy=policy)
    assert engine.state.intelligence.policies == (old_policy, policy)
    assert engine.state.intelligence.estimates == ()
    assert engine.store.load(engine.project_id)[: len(prefix)] == prefix
    assert replay_project(engine.project_id, engine.store.load(engine.project_id)) == engine.state
    scout.prepare("active")
    with rejected("active"):
        engine.revise_goal(
            engine.state.research.goal.model_copy(update={"statement": "changed"}),
            basis,
            policy=policy,
        )
