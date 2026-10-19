from uuid import uuid4

import pytest

from epipilot.core.events import EventType
from epipilot.events.payloads import DecisionMadePayload
from epipilot.requirements.models import DecisionAuthority
from epipilot.research.engine import ResearchEngine
from epipilot.research.events import ResearchBudgetExtended
from epipilot.research.models import Cost, ExperimentProposal, ResearchGoal, ResearchNode
from epipilot.runtime.event_store import InMemoryEventStore
from epipilot.state.replay import replay_project


def configured_engine(tmp_path):
    engine = ResearchEngine(InMemoryEventStore(), "p", tmp_path)
    hypothesis_id = uuid4()
    engine.initialize(
        ResearchGoal(statement="research", budgets=(Cost(unit="seconds", amount=10),)),
        (ResearchNode(hypothesis_id=hypothesis_id, label="h", basis="request"),),
        (
            ExperimentProposal(
                id="first",
                hypothesis_id=hypothesis_id,
                title="first",
                input_revision="frozen",
                estimated_cost=(Cost(unit="seconds", amount=10),),
            ),
        ),
    )
    return engine


def test_budget_extension_is_user_authorized_append_only_and_replayable(tmp_path):
    engine = configured_engine(tmp_path)
    decision_id = uuid4()
    engine.append(
        EventType.DECISION_MADE,
        DecisionMadePayload(
            decision_id=decision_id,
            question="Extend research budget?",
            choice="Add 120 seconds",
            authority=DecisionAuthority.USER,
            rationale="User selected the additional allowance.",
        ),
    )
    prefix = engine.store.load("p")
    engine.append(
        EventType.RESEARCH_BUDGET_EXTENDED,
        ResearchBudgetExtended(
            basis_ref=str(decision_id), additions=(Cost(unit="seconds", amount=120),)
        ),
    )

    assert engine.state.research.goal.budgets == (Cost(unit="seconds", amount=10),)
    assert engine.budget()["remaining"] == {"seconds": 130}
    assert engine.store.load("p")[: len(prefix)] == prefix
    assert replay_project("p", engine.store.load("p")) == engine.state


def test_budget_extension_rejects_non_user_authority_unknown_units_and_zero(tmp_path):
    engine = configured_engine(tmp_path)
    system_id = uuid4()
    engine.append(
        EventType.DECISION_MADE,
        DecisionMadePayload(
            decision_id=system_id,
            question="Extend?",
            choice="Add",
            authority=DecisionAuthority.SYSTEM,
            rationale="Synthetic test.",
        ),
    )
    with pytest.raises(ValueError):
        engine.append(
            EventType.RESEARCH_BUDGET_EXTENDED,
            ResearchBudgetExtended(
                basis_ref=str(system_id), additions=(Cost(unit="seconds", amount=1),)
            ),
        )

    user_id = uuid4()
    engine.append(
        EventType.DECISION_MADE,
        DecisionMadePayload(
            decision_id=user_id,
            question="Extend?",
            choice="Add",
            authority=DecisionAuthority.USER,
            rationale="Synthetic test.",
        ),
    )
    for addition in (Cost(unit="tokens", amount=1), Cost(unit="seconds", amount=0)):
        with pytest.raises(ValueError):
            engine.append(
                EventType.RESEARCH_BUDGET_EXTENDED,
                ResearchBudgetExtended(basis_ref=str(user_id), additions=(addition,)),
            )
